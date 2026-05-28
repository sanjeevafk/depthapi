"""Main runner for the PromptSpec eval suite.

Flow for each EvalCase:
  1. Build the system prompt from the spec via build_prompt_from_spec().
     - If expect_spec_error=True, assert that PromptSpecError is raised and mark PASS.
  2. Call the configured LLM provider with [system_prompt, user_query].
  3. Call judge_response() to score the raw LLM answer against the criteria.
  4. Collect all JudgeResults and pass them to the reporter.

Usage:
    # Run all cases (default provider from env):
    python -m evaluation.prompt_spec_eval.runner

    # Run only specific tags:
    python -m evaluation.prompt_spec_eval.runner --tags depth reasoning

    # Run a single case by ID:
    python -m evaluation.prompt_spec_eval.runner --case depth-expert-analyze

    # Use a different judge provider:
    EVALUATOR_PROVIDER=gemini python -m evaluation.prompt_spec_eval.runner

Environment variables (same as benchmark.py):
    EVALUATOR_PROVIDER   groq | openrouter | gemini  (default: groq)
    EVALUATOR_MODEL      model name
    GROQ_API_KEY / OPENROUTER_API_KEY / GEMINI_API_KEY
    EVAL_CALL_DELAY_SECONDS   pacing between judge calls (default: 1.5)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Allow running as a script from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.prompt_engine.builder import build_prompt_from_spec
from api.prompt_engine.models import PromptSpecError, RuntimeContext
from evaluation.eval_utils import call_evaluator_model, parse_json_with_repair
from evaluation.prompt_spec_eval.cases import ALL_CASES, EvalCase, load_benchmark_cases_from_file
from evaluation.prompt_spec_eval.judge import JudgeResult, judge_response, judge_response_with_fallback

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# LLM call for the "student" model (the model being evaluated)
# ---------------------------------------------------------------------------

def call_student_model(system_prompt: str, user_query: str) -> str:
    """Send [system, user] to the configured provider and return raw text.

    Reuses the same eval_utils infrastructure for consistent pacing.
    The student and judge can be different models (set STUDENT_MODEL env var).
    """
    student_model = os.environ.get("STUDENT_MODEL") or os.environ.get("EVALUATOR_MODEL")
    provider = (os.environ.get("EVALUATOR_PROVIDER", "groq") or "groq").strip().lower()

    import httpx

    if provider == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("Missing OPENROUTER_API_KEY for provider openrouter")
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    elif provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("Missing GEMINI_API_KEY for provider gemini")
        url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    elif provider == "groq":
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError("Missing GROQ_API_KEY for provider groq")
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    else:
        raise RuntimeError(f"Unsupported EVALUATOR_PROVIDER: {provider}")

    if not student_model:
        defaults = {
            "groq": "llama-3.1-8b-instant",
            "openrouter": "openai/gpt-4o-mini",
            "gemini": "gemini-2.0-flash",
        }
        student_model = defaults[provider]

    payload = {
        "model": student_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
        "temperature": float(os.environ.get("STUDENT_TEMPERATURE", "0.3")),
        "max_tokens": 800,
    }

    min_interval = float(os.environ.get("EVAL_CALL_DELAY_SECONDS", "1.5"))
    max_retries = int(os.environ.get("EVAL_HTTP_RETRIES", "4"))
    backoff_base = float(os.environ.get("EVAL_HTTP_BACKOFF_BASE", "2.0"))

    for attempt in range(max_retries):
        time.sleep(min_interval)
        resp = httpx.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code == 429:
            wait = min(60.0, backoff_base ** (attempt + 2))
            print(f"    [429] Backing off {wait:.0f}s …", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"] or ""
    raise RuntimeError("Student model request failed after retries")


# ---------------------------------------------------------------------------
# Per-case runner
# ---------------------------------------------------------------------------

def _merge_judge_results(
    case: EvalCase,
    primary: JudgeResult,
    secondary: JudgeResult,
) -> dict:
    name_to_secondary = {cs.name: cs for cs in secondary.criterion_scores}
    combined_scores = []
    for cs in primary.criterion_scores:
        other = name_to_secondary.get(cs.name)
        other_score = other.score if other else cs.score
        combined = (cs.score + other_score) / 2.0
        combined_scores.append(
            {
                "name": cs.name,
                "score": round(combined, 3),
                "reasoning": f"primary: {cs.reasoning} | secondary: {(other.reasoning if other else 'n/a')}",
                "passed": combined >= 0.6,
                "weight": cs.weight,
            }
        )

    total_weight = sum(cs["weight"] for cs in combined_scores) if combined_scores else 0.0
    overall = (
        sum(cs["score"] * cs["weight"] for cs in combined_scores) / total_weight
        if total_weight > 0
        else 0.0
    )
    critical_ok = True
    for cs in combined_scores:
        for crit in case.criteria:
            if crit.name == cs["name"] and crit.critical and cs["score"] < 0.6:
                critical_ok = False
                break
        if not critical_ok:
            break

    return {
        "overall_score": round(overall, 3),
        "passed": overall >= case.pass_threshold and critical_ok,
        "pre_filter_failures": list(set(primary.pre_filter_failures + secondary.pre_filter_failures)),
        "pass_threshold": case.pass_threshold,
        "total_weight": round(total_weight, 2),
        "criterion_scores": combined_scores,
        "error": primary.error or secondary.error,
    }


def run_case(case: EvalCase) -> dict:
    """Execute one EvalCase end-to-end. Returns a serialisable result dict."""
    start = time.time()
    result: dict = {
        "case_id": case.case_id,
        "tags": case.tags,
        "expect_spec_error": case.expect_spec_error,
        "pass_threshold": case.pass_threshold,
        "corpus_chunks": case.corpus_chunks,
        "metadata": case.metadata,
        "spec": {
            "topic": case.spec.topic,
            "depth": case.spec.depth,
            "task": case.spec.task,
            "reasoning": case.spec.reasoning,
            "style": case.spec.style,
            "capabilities": sorted(case.spec.capabilities),
        },
        "query": case.query,
        "system_prompt": None,
        "student_response": None,
        "judge_result": None,
        "passed": False,
        "error": None,
        "elapsed_s": 0.0,
    }

    # Step 1 – Build system prompt
    try:
        build = build_prompt_from_spec(case.spec, case.runtime)
        result["system_prompt"] = build.prompt
        result["trace"] = build.trace.to_dict()

        if case.expect_spec_error:
            result["error"] = "Expected PromptSpecError was NOT raised – spec was accepted."
            result["passed"] = False
            result["elapsed_s"] = round(time.time() - start, 2)
            return result

    except PromptSpecError as exc:
        if case.expect_spec_error:
            result["passed"] = True
            result["error"] = None
            result["student_response"] = f"[PromptSpecError correctly raised: {exc}]"
            result["elapsed_s"] = round(time.time() - start, 2)
            return result
        else:
            result["error"] = f"Unexpected PromptSpecError: {exc}"
            result["passed"] = False
            result["elapsed_s"] = round(time.time() - start, 2)
            return result

    # Step 2 – Call the student model
    try:
        response_text = call_student_model(build.prompt, case.query)
        result["student_response"] = response_text
    except Exception as exc:
        result["error"] = f"Student model call failed: {exc}"
        result["elapsed_s"] = round(time.time() - start, 2)
        return result

    # Step 3 – Judge the response with two judges
    primary_provider = (os.environ.get("EVALUATOR_PROVIDER", "groq") or "groq").strip().lower()
    primary_model = os.environ.get("EVALUATOR_MODEL")
    secondary_provider = (os.environ.get("SECONDARY_EVALUATOR_PROVIDER", "gemini") or "gemini").strip().lower()
    secondary_model = os.environ.get("SECONDARY_EVALUATOR_MODEL", "gemini-2.5-pro")
    fallback_provider = "groq"
    fallback_model = "llama-3.3-70b-versatile"

    primary = judge_response(case, build.prompt, response_text, provider=primary_provider, model=primary_model)
    secondary = judge_response_with_fallback(
        case,
        build.prompt,
        response_text,
        provider=secondary_provider,
        model=secondary_model,
        fallback_provider=fallback_provider,
        fallback_model=fallback_model,
    )

    combined = _merge_judge_results(case, primary, secondary)
    disagreement_delta = float(os.environ.get("JUDGE_DISAGREE_DELTA", "0.1"))
    judge_disagreement = (
        primary.passed != secondary.passed
        or abs(primary.overall_score - secondary.overall_score) >= disagreement_delta
    )

    result["judge_result"] = combined
    result["judge_results"] = {
        "primary": {
            "provider": primary.provider or primary_provider,
            "model": primary.model or primary_model,
            "overall_score": primary.overall_score,
            "passed": primary.passed,
            "error": primary.error,
        },
        "secondary": {
            "provider": secondary.provider or secondary_provider,
            "model": secondary.model or secondary_model,
            "overall_score": secondary.overall_score,
            "passed": secondary.passed,
            "error": secondary.error,
        },
        "disagreement": judge_disagreement,
    }
    result["passed"] = combined["passed"]
    if combined.get("error"):
        result["error"] = combined["error"]

    result["elapsed_s"] = round(time.time() - start, 2)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PromptSpec LLM eval suite")
    p.add_argument(
        "--tags",
        nargs="*",
        default=[],
        help="Only run cases that have ALL of these tags (e.g. --tags depth reasoning)",
    )
    p.add_argument(
        "--case",
        default=None,
        help="Run a single case by case_id",
    )
    p.add_argument(
        "--no-negative",
        action="store_true",
        help="Skip negative/incompatible cases",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Custom output JSON path (default: results/<timestamp>.json)",
    )
    p.add_argument(
        "--benchmark-file",
        default=None,
        help="Load benchmark cases from a JSON file",
    )
    return p.parse_args()


def select_cases(args: argparse.Namespace) -> list[EvalCase]:
    cases = ALL_CASES
    if args.benchmark_file:
        cases = load_benchmark_cases_from_file(args.benchmark_file)
    if args.case:
        cases = [c for c in cases if c.case_id == args.case]
        if not cases:
            print(f"ERROR: No case with id '{args.case}' found.", file=sys.stderr)
            sys.exit(1)
    if args.tags:
        cases = [c for c in cases if all(t in c.tags for t in args.tags)]
    if args.no_negative:
        cases = [c for c in cases if not c.expect_spec_error]
    return cases


def main() -> None:
    args = parse_args()
    cases = select_cases(args)

    if not cases:
        print("No cases matched the filters. Exiting.")
        sys.exit(0)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"prompt_spec_eval_{timestamp}.json"

    print(f"\n{'='*60}")
    print(f"  PromptSpec Eval Suite — {len(cases)} case(s)")
    print(f"  Provider : {os.environ.get('EVALUATOR_PROVIDER', 'groq')}")
    print(f"  Judge    : {os.environ.get('EVALUATOR_MODEL', '(provider default)')}")
    print(f"  Output   : {out_path}")
    print(f"{'='*60}\n")

    all_results = []
    passed = 0
    failed = 0
    errors = 0
    disagreements_log = RESULTS_DIR / f"judge_disagreements_{timestamp}.jsonl"
    failed_chunks_log = RESULTS_DIR / f"failed_corpus_chunks_{timestamp}.jsonl"

    for i, case in enumerate(cases, 1):
        status = "🔄"
        print(f"[{i:2d}/{len(cases)}] {status} {case.case_id}  ({', '.join(case.tags)})", end=" … ", flush=True)
        res = run_case(case)
        all_results.append(res)

        if res.get("judge_results", {}).get("disagreement"):
            disagreements_log.parent.mkdir(parents=True, exist_ok=True)
            with disagreements_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "case_id": res.get("case_id"),
                    "query": res.get("query"),
                    "primary": res.get("judge_results", {}).get("primary"),
                    "secondary": res.get("judge_results", {}).get("secondary"),
                    "overall": res.get("judge_result", {}).get("overall_score"),
                    "passed": res.get("passed"),
                }, ensure_ascii=True) + "\n")

        if not res.get("passed") and case.corpus_chunks:
            failed_chunks_log.parent.mkdir(parents=True, exist_ok=True)
            with failed_chunks_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "case_id": res.get("case_id"),
                    "query": res.get("query"),
                    "chunks": case.corpus_chunks,
                }, ensure_ascii=True) + "\n")

        if res.get("error") and not res["passed"]:
            errors += 1
            print(f"❌ ERROR  ({res['error'][:60]})")
        elif res["passed"]:
            passed += 1
            score = res.get("judge_result", {}).get("overall_score", "n/a") if res.get("judge_result") else "n/a"
            print(f"✅ PASS   (score={score}, {res['elapsed_s']}s)")
        else:
            failed += 1
            score = res.get("judge_result", {}).get("overall_score", "n/a") if res.get("judge_result") else "n/a"
            print(f"❌ FAIL   (score={score}, {res['elapsed_s']}s)")
            if res.get("judge_result", {}).get("criterion_scores"):
                for cs in res["judge_result"]["criterion_scores"]:
                    if not cs["passed"]:
                        print(f"         ↳ [{cs['name']}] {cs['score']:.2f} — {cs['reasoning']}")

    # Write results
    summary = {
        "run_timestamp": timestamp,
        "total": len(cases),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "pass_rate": round(passed / len(cases), 3) if cases else 0.0,
        "results": all_results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"  TOTAL  : {len(cases)}")
    print(f"  PASSED : {passed}  ({100 * passed // len(cases)}%)")
    print(f"  FAILED : {failed}")
    print(f"  ERRORS : {errors}")
    print(f"  Results: {out_path}")
    print(f"{'='*60}\n")

    sys.exit(0 if failed == 0 and errors == 0 else 1)


if __name__ == "__main__":
    main()
