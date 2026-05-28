"""Visual report generator for PromptSpec eval results.

Reads a results JSON produced by runner.py and prints a rich terminal report
plus writes a Markdown summary.

Usage:
    python -m evaluation.prompt_spec_eval.report results/prompt_spec_eval_<timestamp>.json
    python -m evaluation.prompt_spec_eval.report --latest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _latest_result() -> Path:
    files = sorted(RESULTS_DIR.glob("prompt_spec_eval_*.json"))
    if not files:
        print("No results found in", RESULTS_DIR, file=sys.stderr)
        sys.exit(1)
    return files[-1]


def _score_bar(score: float, width: int = 20) -> str:
    filled = round(score * width)
    color = "\033[92m" if score >= 0.75 else "\033[93m" if score >= 0.5 else "\033[91m"
    reset = "\033[0m"
    bar = "█" * filled + "░" * (width - filled)
    return f"{color}{bar}{reset} {score:.2f}"


def _axis_stats(results: list[dict]) -> dict[str, dict[str, list[tuple[float, bool]]]]:
    stats: dict[str, dict[str, list[tuple[float, bool]]]] = {
        "depth": {},
        "task": {},
        "reasoning": {},
        "style": {},
        "capabilities": {},
    }
    for r in results:
        jr = r.get("judge_result")
        if not jr:
            continue
        score = jr.get("overall_score", 0.0)
        passed = bool(r.get("passed"))
        spec = r.get("spec", {})
        for axis in ("depth", "task", "reasoning", "style"):
            val = spec.get(axis, "unknown")
            stats[axis].setdefault(val, []).append((score, passed))
        caps = spec.get("capabilities", []) or ["none"]
        for cap in caps:
            stats["capabilities"].setdefault(cap, []).append((score, passed))
    return stats


def _top_failure_suggestions(results: list[dict]) -> list[str]:
    counts: dict[str, int] = {}
    for r in results:
        jr = r.get("judge_result")
        if not jr:
            continue
        for cs in jr.get("criterion_scores", []):
            if not cs.get("passed"):
                name = cs.get("name", "unknown")
                counts[name] = counts.get(name, 0) + 1

    suggestion_map = {
        "depth_appropriateness": "Tighten depth control to match target audience level.",
        "technical_correctness_level": "Improve factual correctness checks or add verification steps.",
        "explanation_granularity": "Adjust detail level to avoid being too shallow or too deep.",
        "task_adherence": "Make task intent explicit in the system prompt and enforce structure.",
        "comparison_balance": "Ensure both sides receive balanced coverage before recommending.",
        "idea_diversity": "Increase variety of ideas by covering multiple levers.",
        "risk_coverage": "Expand analysis to cover more risk dimensions (correctness, perf, cost).",
        "brevity_vs_completeness": "Compress responses while retaining all key points.",
        "socratic_questioning_quality": "Ask more targeted, diagnostic questions instead of vague prompts.",
        "debate_balance": "Strengthen the weaker side with concrete arguments.",
        "guided_step_progression": "Make steps explicit and ordered; add decision checkpoints.",
        "conciseness_efficiency": "Reduce word count without dropping essential content.",
        "academic_rigor": "Use formal register and precise terminology consistently.",
        "informal_relatable_tone": "Lean into the casual tone while keeping the explanation accurate.",
        "search_integration_quality": "Integrate key facts from search context directly into the answer.",
        "citation_presence": "Require explicit citations for sourced claims.",
        "includes_mermaid_block": "Always include a Mermaid diagram block when requested.",
        "diagram_type_match": "Match the exact requested Mermaid diagram type.",
    }

    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    suggestions = []
    for name, count in ranked[:5]:
        suggestion = suggestion_map.get(name, f"Improve adherence to criterion '{name}'.")
        suggestions.append(f"{suggestion} (failures: {count})")
    return suggestions


def print_report(data: dict) -> None:
    results = data["results"]
    print(f"\n{'='*70}")
    print(f"  PromptSpec Eval Report  |  Run: {data['run_timestamp']}")
    print(f"  Pass rate: {data['passed']}/{data['total']} ({100*data['pass_rate']:.0f}%)  "
          f"  Errors: {data['errors']}")
    print(f"{'='*70}\n")

    # Group by tag
    tag_groups: dict[str, list[dict]] = {}
    for r in results:
        primary_tag = r["tags"][0] if r["tags"] else "other"
        tag_groups.setdefault(primary_tag, []).append(r)

    for group, group_results in tag_groups.items():
        group_pass = sum(1 for r in group_results if r["passed"])
        print(f"  ── {group.upper()} ({group_pass}/{len(group_results)} passed) ──")
        for r in group_results:
            icon = "✅" if r["passed"] else "❌"
            jr = r.get("judge_result") or {}
            overall = jr.get("overall_score") if jr else None
            threshold = jr.get("pass_threshold") or r.get("pass_threshold")
            score_str = _score_bar(overall) if overall is not None else "  [error/skip]       "
            print(f"  {icon}  {r['case_id']:<40}  {score_str}  (thr={threshold})")

            if r.get("error"):
                print(f"       ⚠  {r['error'][:80]}")

            if jr.get("criterion_scores"):
                for cs in jr["criterion_scores"]:
                    icon2 = "✓" if cs["passed"] else "✗"
                    weight = cs.get("weight", 1.0)
                    print(
                        f"       {icon2} [{cs['name']}] {cs['score']:.2f} (w={weight})  "
                        f"{cs['reasoning'][:65]}"
                    )

            if jr.get("pre_filter_failures"):
                for pf in jr["pre_filter_failures"]:
                    print(f"       🚫 pre-filter: {pf}")
        print()

    print(f"{'='*70}\n")


def write_markdown(data: dict, out_path: Path) -> None:
    results = data["results"]
    lines = [
        f"# PromptSpec Eval Report",
        f"",
        f"**Run:** `{data['run_timestamp']}`  ",
        f"**Pass rate:** {data['passed']}/{data['total']} ({100*data['pass_rate']:.0f}%)  ",
        f"**Errors:** {data['errors']}",
        f"",
        f"---",
        f"",
        f"## Summary",
        f"",
        f"| Case ID | Axes tested | Overall Score | Pass Threshold | Passed |",
        f"|---------|-------------|:-------------:|:--------------:|:------:|",
    ]
    for r in results:
        spec = r.get("spec", {})
        caps = spec.get("capabilities", [])
        caps_str = ", ".join(caps) if caps else "none"
        axes = (
            f"depth={spec.get('depth')}, task={spec.get('task')}, "
            f"reasoning={spec.get('reasoning')}, style={spec.get('style')}, "
            f"capabilities={caps_str}"
        )
        jr = r.get("judge_result")
        score = f"{jr['overall_score']:.2f}" if jr else "n/a"
        threshold = f"{jr['pass_threshold']:.2f}" if jr else f"{r.get('pass_threshold', 0.0):.2f}"
        passed = "✅" if r["passed"] else "❌"
        lines.append(f"| `{r['case_id']}` | {axes} | {score} | {threshold} | {passed} |")

    lines += ["", "---", "", "## Detailed Results", ""]
    for r in results:
        jr = r.get("judge_result")
        if not jr or not jr.get("criterion_scores"):
            continue
        lines.append(f"### `{r['case_id']}`")
        lines.append(f"")
        spec = r.get("spec", {})
        caps = spec.get("capabilities", [])
        caps_str = ", ".join(caps) if caps else "none"
        lines.append(f"> **Query:** {r['query']}")
        lines.append(
            f"> **Axes:** depth={spec.get('depth')}, task={spec.get('task')}, "
            f"reasoning={spec.get('reasoning')}, style={spec.get('style')}, capabilities={caps_str}"
        )
        meta = r.get("metadata") or {}
        if meta.get("category"):
            lines.append(f"> **Category:** {meta.get('category')}")
        if meta.get("expected_behavior"):
            lines.append(f"> **Expected behavior:** {meta.get('expected_behavior')}")
        lines.append(f"> **Pass threshold:** {jr.get('pass_threshold', 0.0):.2f}  **Total weight:** {jr.get('total_weight', 0.0):.2f}")
        lines.append(f"")
        if r.get("student_response"):
            preview = r["student_response"][:300].replace("\n", " ")
            lines.append(f"> **Response preview:** {preview}…")
            lines.append(f"")
        lines.append(f"| Criterion | Weight | Score | Reasoning |")
        lines.append(f"|-----------|:------:|:-----:|-----------|")
        for cs in jr["criterion_scores"]:
            status = "✅" if cs["passed"] else "❌"
            weight = cs.get("weight", 1.0)
            lines.append(f"| {status} `{cs['name']}` | {weight:.2f} | {cs['score']:.2f} | {cs['reasoning']} |")
        if jr.get("pre_filter_failures"):
            for pf in jr["pre_filter_failures"]:
                lines.append(f"| 🚫 pre-filter | 0.00 | 0.00 | {pf} |")
        lines.append("")

    lines += ["", "---", "", "## Axis Performance Summary", ""]
    axis_stats = _axis_stats(results)
    lines.append("| Axis | Value | Avg Score | Pass Rate | N |")
    lines.append("|------|-------|:--------:|:---------:|:--:|")
    for axis, values in axis_stats.items():
        for value, rows in sorted(values.items()):
            scores = [s for s, _ in rows]
            passes = sum(1 for _, p in rows if p)
            avg = sum(scores) / len(scores) if scores else 0.0
            rate = passes / len(rows) if rows else 0.0
            lines.append(f"| {axis} | {value} | {avg:.2f} | {rate:.2f} | {len(rows)} |")

    suggestions = _top_failure_suggestions(results)
    lines += ["", "---", "", "## Actionable Improvement Suggestions", ""]
    if suggestions:
        for suggestion in suggestions:
            lines.append(f"- {suggestion}")
    else:
        lines.append("- No failing criteria detected in this run.")

    disagreements = [r for r in results if r.get("judge_results", {}).get("disagreement")]
    lines += ["", "---", "", "## Judge Disagreements", ""]
    if disagreements:
        lines.append("| Case ID | Primary | Secondary | Combined | Passed |")
        lines.append("|---------|---------|-----------|:--------:|:------:|")
        for r in disagreements:
            primary = r.get("judge_results", {}).get("primary", {})
            secondary = r.get("judge_results", {}).get("secondary", {})
            combined = r.get("judge_result", {}).get("overall_score", "n/a")
            lines.append(
                "| `{}` | {} ({}) | {} ({}) | {:.2f} | {} |".format(
                    r.get("case_id"),
                    primary.get("provider"),
                    primary.get("model"),
                    secondary.get("provider"),
                    secondary.get("model"),
                    float(combined) if isinstance(combined, (int, float)) else 0.0,
                    "✅" if r.get("passed") else "❌",
                )
            )
    else:
        lines.append("- No judge disagreements in this run.")

    failed_chunks = [r for r in results if not r.get("passed") and r.get("corpus_chunks")]
    lines += ["", "---", "", "## Failed Corpus Chunks", ""]
    if failed_chunks:
        lines.append("| Case ID | Chunk ID | Doc ID | Source |")
        lines.append("|---------|----------|--------|--------|")
        for r in failed_chunks:
            for chunk in r.get("corpus_chunks", []):
                lines.append(
                    "| `{}` | {} | {} | {} |".format(
                        r.get("case_id"),
                        chunk.get("chunk_id"),
                        chunk.get("doc_id"),
                        chunk.get("source"),
                    )
                )
    else:
        lines.append("- No failed corpus chunks in this run.")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nMarkdown report written to: {out_path}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("result_file", nargs="?", help="Path to results JSON")
    p.add_argument("--latest", action="store_true", help="Use the most recent results file")
    args = p.parse_args()

    if args.latest or not args.result_file:
        result_path = _latest_result()
    else:
        result_path = Path(args.result_file)

    if not result_path.exists():
        print(f"File not found: {result_path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(result_path.read_text(encoding="utf-8"))
    print_report(data)

    md_path = result_path.with_suffix(".md")
    write_markdown(data, md_path)


if __name__ == "__main__":
    main()
