import argparse
import asyncio
import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Any

from analyze_results import analyze_and_report
from depthapi_client import DepthAPIClient
from generate_benchmark import generate_benchmark_dataset
from run_deepeval import evaluate_deepeval
from run_judge import CustomLLMJudge

RESULTS_DIR = Path("results")
RAW_DIR = RESULTS_DIR / "raw"
REPORTS_DIR = RESULTS_DIR / "reports"
CACHE_DIR = RESULTS_DIR / "cache"

GEN_CACHE_PATH = CACHE_DIR / "generation_cache.jsonl"
EVAL_CACHE_PATH = CACHE_DIR / "evaluation_cache.jsonl"

CHECKPOINT_PATH = RAW_DIR / "all_results_checkpoint.jsonl"
FINAL_RAW_PATH = RAW_DIR / "all_results.json"

PHASE_EVENTS_PATH = RAW_DIR / "phase_events.jsonl"
ERROR_EVENTS_PATH = RAW_DIR / "runtime_errors.jsonl"

MAX_RETRIES = 2
BACKOFF_BASE = 1.5

VALID_ERROR_TYPES = {
    "api_timeout",
    "http_error",
    "rate_limit",
    "context_overflow",
    "malformed_response",
    "evaluator_parse_failure",
    "invalid_benchmark_row",
    "missing_fields",
}


def ensure_dirs():
    for p in [RESULTS_DIR, RAW_DIR, REPORTS_DIR, CACHE_DIR]:
        p.mkdir(parents=True, exist_ok=True)


def stable_hash(payload: dict[str, Any]) -> str:
    text = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode()).hexdigest()


def append_jsonl(path: Path, item: dict[str, Any]):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def load_jsonl_map(path: Path, key_field: str):
    out = {}

    if not path.exists():
        return out

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            try:
                row = json.loads(line)
            except Exception:
                continue

            k = row.get(key_field)

            if isinstance(k, str):
                out[k] = row

    return out


def load_checkpoint():
    out = {}

    if not CHECKPOINT_PATH.exists():
        return out

    with CHECKPOINT_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            try:
                row = json.loads(line)
            except Exception:
                continue

            k = f"{row.get('system')}::{row.get('id')}"
            out[k] = row

    return out


def classify_error(error_text: str) -> str:
    t = (error_text or "").lower()

    if "timeout" in t or "timed out" in t:
        return "api_timeout"

    if "429" in t or "rate limit" in t:
        return "rate_limit"

    if "422" in t and "string should have at most" in t:
        return "context_overflow"

    if "http" in t or "client error" in t or "server error" in t:
        return "http_error"

    if "json" in t or "malformed" in t:
        return "malformed_response"

    if "parse" in t or "eval_failed" in t:
        return "evaluator_parse_failure"

    if "missing" in t:
        return "missing_fields"

    return "invalid_benchmark_row"


def log_error(
    sample_id: str,
    system: str,
    phase: str,
    error: str,
):
    append_jsonl(
        ERROR_EVENTS_PATH,
        {
            "sample_id": sample_id,
            "system": system,
            "phase": phase,
            "error_type": classify_error(error),
            "error": error,
            "timestamp": time.time(),
        },
    )


def log_phase_event(
    sample_id: str,
    system: str,
    phase: str,
    payload: dict[str, Any],
):
    append_jsonl(
        PHASE_EVENTS_PATH,
        {
            "sample_id": sample_id,
            "system": system,
            "phase": phase,
            "timestamp": time.time(),
            **payload,
        },
    )


def is_retryable(error: Exception) -> bool:
    if isinstance(error, asyncio.TimeoutError):
        return True

    t = str(error).lower()

    retryable_terms = [
        "timeout",
        "429",
        "rate limit",
        "connection",
        "server error",
        "temporarily unavailable",
    ]

    return any(term in t for term in retryable_terms)


def validate_generation_row(row: dict[str, Any]) -> bool:
    answer = (row.get("answer") or "").strip()

    contexts = row.get("contexts") or []

    if len(answer) < 30:
        return False

    if not isinstance(contexts, list):
        return False

    if len(contexts) < 1:
        return False

    context_texts = [
        str(c.get("text") or c.get("content") or "").strip()
        for c in contexts
        if isinstance(c, dict)
    ]
    if not any(context_texts):
        return False

    if not any(isinstance(c, dict) and (c.get("chunk_id") or c.get("doc_id")) for c in contexts):
        return False

    # Reject rows where duplicate retrievals dominate.
    ids = [
        str(c.get("chunk_id") or c.get("doc_id"))
        for c in contexts
        if isinstance(c, dict) and (c.get("chunk_id") or c.get("doc_id"))
    ]
    if ids:
        dup_ratio = 1.0 - (len(set(ids)) / len(ids))
        if dup_ratio > 0.6:
            return False

    # Soft requirement: metadata should exist, but do not hard-fail preflight on it.
    md = row.get("runtime_metadata")
    if md is not None and not isinstance(md, dict):
        return False

    # Basic truncation heuristic.
    if answer.endswith("..."):
        return False

    return True


def sanitize_query(raw_query: str, max_len: int = 200) -> str:
    q = str(raw_query or "").strip()
    q = re.sub(r"[\r\n\t]+", " ", q)
    q = re.sub(r"[`{}\[\]<>|\\]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    if len(q) > max_len:
        q = q[: max_len - 3].rstrip() + "..."
    return q


async def retry_async(fn, *args, timeout_s=45.0, **kwargs):
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            return await asyncio.wait_for(
                fn(*args, **kwargs),
                timeout=timeout_s,
            )

        except Exception as exc:
            last_exc = exc

            if attempt >= MAX_RETRIES:
                break

            if not is_retryable(exc):
                break

            backoff = BACKOFF_BASE ** attempt + random.uniform(0, 0.5)

            await asyncio.sleep(backoff)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Retry failed without capturing exception")


async def generate_one(
    item: dict[str, Any],
    system: str,
    client: Any,
):
    raw_query = str(item["query"])
    query = sanitize_query(raw_query, max_len=200)

    prompt_spec = item.get("prompt_spec")

    if system == "depthapi":
        res = await client.query(query, prompt_spec)
    else:
        res = await client.query(query)

    contexts = res.get("contexts") if isinstance(res.get("contexts"), list) else []

    row = {
        "id": item["id"],
        "system": system,
        "query": query,
        "query_original": raw_query,
        "prompt_spec": prompt_spec,
        "metadata": item.get("metadata", {}),
        "ground_truth": item.get("ground_truth"),
        "relevant_doc_ids": item.get("relevant_doc_ids"),
        "relevant_chunk_ids": item.get("relevant_chunk_ids"),
        "expected_doc_ids": item.get("expected_doc_ids"),
        "expected_chunk_ids": item.get("expected_chunk_ids"),
        "difficulty": item.get("difficulty"),
        "category": item.get("category"),
        "expected_citations": item.get("expected_citations"),
        "answer": res.get("answer", ""),
        "contexts": contexts,
        "citations": res.get("citations", []),
        "runtime_metadata": res.get("metadata", {}),
        "runtime_error": res.get("error"),
        "telemetry": {},
    }

    return row


async def phase_generation(
    dataset: list[dict[str, Any]],
    compare_baseline: bool,
    max_concurrency: int,
    timeout_s: float,
    resume: bool,
    skip_existing: bool,
    preflight_rows: dict[str, dict[str, Any]] | None = None,
):
    rows = {}

    generation_cache = load_jsonl_map(
        GEN_CACHE_PATH,
        "generation_key",
    )

    checkpoint = (
        load_checkpoint()
        if resume or skip_existing
        else {}
    )

    sem = asyncio.Semaphore(max_concurrency)

    async def process_system(system: str, client: Any):
        tasks = []

        for item in dataset:
            row_key = f"{system}::{item['id']}"

            if row_key in checkpoint:
                rows[row_key] = checkpoint[row_key]
                continue

            key_payload = {
                "system": system,
                "id": item["id"],
                "query": sanitize_query(str(item["query"]), max_len=200),
                "prompt_spec": item.get("prompt_spec"),
            }

            generation_key = stable_hash(key_payload)

            if skip_existing and generation_key in generation_cache:
                rows[row_key] = generation_cache[generation_key]["row"]
                continue

            async def worker(
                item=item,
                generation_key=generation_key,
            ):
                async with sem:
                    start = time.perf_counter()

                    try:
                        # For DepthAPI rows that passed preflight, reuse the same successful
                        # response to avoid second-call instability on identical queries.
                        if system == "depthapi" and preflight_rows and str(item["id"]) in preflight_rows:
                            row = dict(preflight_rows[str(item["id"])])
                        else:
                            row = await retry_async(
                                generate_one,
                                item,
                                system,
                                client,
                                timeout_s=timeout_s,
                            )

                    except Exception as exc:
                        row = {
                            "id": item["id"],
                            "system": system,
                            "query": item["query"],
                            "answer": "",
                            "contexts": [],
                            "runtime_error": str(exc),
                            "telemetry": {},
                        }

                    latency = round(
                        time.perf_counter() - start,
                        3,
                    )

                    row["telemetry"]["generation_latency_s"] = latency
                    row["generation_key"] = generation_key

                    if not validate_generation_row(row):
                        row["runtime_error"] = (
                            row.get("runtime_error")
                            or "invalid_generation_output"
                        )

                    if row.get("runtime_error"):
                        log_error(
                            str(row["id"]),
                            system,
                            "generation",
                            row["runtime_error"],
                        )
                        append_jsonl(
                            ERROR_EVENTS_PATH,
                            {
                                "sample_id": str(row.get("id")),
                                "system": system,
                                "phase": "generation_diagnostics",
                                "query": row.get("query"),
                                "query_original": row.get("query_original"),
                                "answer_length": len((row.get("answer") or "").strip()),
                                "context_count": len(row.get("contexts") or []),
                                "contexts": row.get("contexts") or [],
                                "citations": row.get("citations") or [],
                                "runtime_metadata": row.get("runtime_metadata") or {},
                                "error": row.get("runtime_error"),
                                "error_type": classify_error(str(row.get("runtime_error"))),
                                "provider": __import__("os").environ.get("EVALUATOR_PROVIDER", ""),
                                "model": __import__("os").environ.get("EVALUATOR_MODEL", ""),
                                "timestamp": time.time(),
                            },
                        )

                    append_jsonl(
                        GEN_CACHE_PATH,
                        {
                            "generation_key": generation_key,
                            "row": row,
                        },
                    )

                    append_jsonl(CHECKPOINT_PATH, row)

                    log_phase_event(
                        str(row["id"]),
                        system,
                        "generation",
                        {
                            "latency_s": latency,
                            "has_answer": bool(row.get("answer")),
                            "context_count": len(
                                row.get("contexts", [])
                            ),
                            "error": row.get("runtime_error"),
                        },
                    )

                    rows[f"{system}::{item['id']}"] = row

            tasks.append(worker())

        await asyncio.gather(*tasks)

    async with DepthAPIClient() as depth_client:
        await process_system("depthapi", depth_client)

    if compare_baseline:
        print("WARNING: legacy remote baseline was retired; skipping baseline (Baseline=nan).")

    return rows


async def phase_evaluation(
    rows: dict[str, dict[str, Any]],
    evals: list[str],
    max_concurrency: int,
    timeout_s: float,
    skip_existing: bool,
):
    ordered = [rows[k] for k in sorted(rows.keys())]

    sem = asyncio.Semaphore(max_concurrency)

    judge = (
        CustomLLMJudge()
        if "judge" in evals
        else None
    )

    eval_cache = load_jsonl_map(
        EVAL_CACHE_PATH,
        "eval_key",
    )

    completed = []

    async def evaluate_row(row):
        async with sem:
            start = time.perf_counter()

            out = dict(row)

            payload = {
                "query": row["query"],
                "answer": row["answer"],
                "contexts": row.get("contexts", []),
                "evals": sorted(evals),
                "evaluator_provider": __import__("os").environ.get("EVALUATOR_PROVIDER", ""),
                "evaluator_model": __import__("os").environ.get("EVALUATOR_MODEL", ""),
                "evaluator_version": "v1",
                "prompt_version": "v1",
            }

            eval_key = stable_hash(payload)

            if skip_existing and eval_key in eval_cache:
                cached = eval_cache[eval_key]["scores"]
                out.update(cached)
                return out

            retries = 0

            try:
                if judge:
                    out["judge"] = await retry_async(
                        judge.evaluate,
                        row["query"],
                        row["answer"],
                        row.get("contexts", []),
                        row.get("prompt_spec"),
                        sample_id=str(row["id"]),
                        timeout_s=timeout_s,
                    )

                if "deepeval" in evals:
                    out["deepeval"] = await asyncio.to_thread(
                        evaluate_deepeval,
                        row["query"],
                        row["answer"],
                        row.get("contexts", []),
                        str(row["id"]),
                    )

            except Exception as exc:
                retries += 1

                out.setdefault("telemetry", {})

                out["telemetry"]["eval_error"] = str(exc)
                out["telemetry"]["eval_error_type"] = classify_error(
                    str(exc)
                )

                log_error(
                    str(out["id"]),
                    out["system"],
                    "evaluation",
                    str(exc),
                )

            latency = round(
                time.perf_counter() - start,
                3,
            )

            out.setdefault("telemetry", {})

            out["telemetry"]["eval_latency_s"] = latency
            out["telemetry"]["eval_retries"] = retries

            out["telemetry"]["sample_runtime_s"] = round(
                out["telemetry"].get(
                    "generation_latency_s",
                    0,
                )
                + latency,
                3,
            )

            append_jsonl(
                EVAL_CACHE_PATH,
                {
                    "eval_key": eval_key,
                    "scores": {
                        k: out.get(k)
                        for k in [
                            "judge",
                            "deepeval",
                        ]
                        if k in out
                    },
                },
            )

            append_jsonl(CHECKPOINT_PATH, out)

            log_phase_event(
                str(out["id"]),
                out["system"],
                "evaluation",
                {
                    "latency_s": latency,
                    "retries": retries,
                    "error": out.get("telemetry", {}).get(
                        "eval_error"
                    ),
                },
            )

            return out

    tasks = [evaluate_row(row) for row in ordered]

    for coro in asyncio.as_completed(tasks):
        result = await coro

        completed.append(result)

        avg_runtime = sum(
            r.get("telemetry", {}).get(
                "sample_runtime_s",
                0,
            )
            for r in completed
        ) / len(completed)

        retry_rate = sum(
            r.get("telemetry", {}).get(
                "eval_retries",
                0,
            )
            for r in completed
        ) / len(completed)

        print(
            f"sample={len(completed)}/{len(tasks)} "
            f"avg_runtime={avg_runtime:.1f}s "
            f"retry_rate={retry_rate:.1%}"
        )

    return completed


def phase_reporting(results):
    with FINAL_RAW_PATH.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(results, f, indent=2)

    analyze_and_report(
        results,
        str(REPORTS_DIR),
    )
    from collections import Counter
    failures = Counter()
    eval_fail = Counter()
    timeouts = 0
    slowest = sorted(
        [
            (
                str(r.get("id")),
                str(r.get("system")),
                float((r.get("telemetry") or {}).get("sample_runtime_s", 0.0)),
                str((r.get("telemetry") or {}).get("error_type") or (r.get("telemetry") or {}).get("eval_error_type") or ""),
            )
            for r in results
        ],
        key=lambda x: x[2],
        reverse=True,
    )[:5]
    for r in results:
        tel = r.get("telemetry") or {}
        et = tel.get("error_type") or tel.get("eval_error_type")
        if et:
            failures[str(et)] += 1
            if str(et) == "api_timeout":
                timeouts += 1
        for ev in ["judge", "deepeval"]:
            v = r.get(ev) or {}
            if isinstance(v, dict) and v.get("error") == "EVAL_FAILED":
                eval_fail[ev] += 1
    summary = {
        "failure_counts_by_type": dict(failures),
        "timeout_frequency": timeouts,
        "evaluator_failure_distribution": dict(eval_fail),
        "slowest_samples": [{"sample_id": a, "system": b, "runtime_s": c, "error_type": d} for a, b, c, d in slowest],
    }
    (REPORTS_DIR / "failure_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    md = ["# Failure Analytics Summary", "", "## Failure Counts", ""]
    for k, v in summary["failure_counts_by_type"].items():
        md.append(f"- `{k}`: {v}")
    md.extend(["", "## Timeout Frequency", "", f"- `{timeouts}`", "", "## Evaluator Failure Distribution", ""])
    for k, v in summary["evaluator_failure_distribution"].items():
        md.append(f"- `{k}`: {v}")
    (REPORTS_DIR / "failure_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")


async def run_benchmark(
    size: int,
    evals: list[str],
    compare_baseline: bool,
    max_concurrency: int,
    timeout_s: float,
    resume: bool,
    skip_existing: bool,
    seed: int = 42,
):
    ensure_dirs()
    random.seed(seed)

    dataset_path = Path("benchmark_corpus.json")

    if dataset_path.exists():
        dataset = json.loads(
            dataset_path.read_text()
        )[:size]

    else:
        dataset = generate_benchmark_dataset(size)

    # Strict preflight to reject weak/invalid rows before spending eval quota.
    async with DepthAPIClient() as preflight:
        filtered = []
        preflight_rows: dict[str, dict[str, Any]] = {}
        seen_ids = {str(item.get("id")) for item in dataset}
        dataset_cursor = len(dataset)

        async def try_admit(item: dict[str, Any]) -> bool:
            raw_query = str(item["query"])
            query = sanitize_query(raw_query, max_len=200)
            try:
                preflight_timeout_s = max(25.0, min(timeout_s, 40.0))
                res = await retry_async(
                    preflight.query,
                    query,
                    item.get("prompt_spec"),
                    timeout_s=preflight_timeout_s,
                )
            except Exception as exc:
                err = str(exc).strip() or type(exc).__name__
                res = {
                    "error": f"preflight_exception: {err}",
                    "answer": "",
                    "contexts": [],
                    "citations": [],
                }
            row = {
                "id": item["id"],
                "system": "depthapi",
                "query": query,
                "query_original": raw_query,
                "prompt_spec": item.get("prompt_spec"),
                "metadata": item.get("metadata", {}),
                "ground_truth": item.get("ground_truth"),
                "relevant_doc_ids": item.get("relevant_doc_ids"),
                "relevant_chunk_ids": item.get("relevant_chunk_ids"),
                "expected_doc_ids": item.get("expected_doc_ids"),
                "expected_chunk_ids": item.get("expected_chunk_ids"),
                "difficulty": item.get("difficulty"),
                "category": item.get("category"),
                "expected_citations": item.get("expected_citations"),
                "answer": res.get("answer", ""),
                "contexts": res.get("contexts", []),
                "citations": res.get("citations", []),
                "runtime_error": res.get("error"),
                "runtime_metadata": res.get("metadata", {}),
                "telemetry": {},
            }
            if not row["runtime_error"] and validate_generation_row(row):
                preflight_rows[str(item["id"])] = row
                filtered.append(item)
                return True
            log_error(
                str(item.get("id")),
                "depthapi",
                "preflight",
                str(row.get("runtime_error") or "preflight_invalid_generation_output"),
            )
            log_phase_event(
                str(item.get("id")),
                "depthapi",
                "preflight",
                {
                    "admitted": False,
                    "query": query,
                    "answer_length": len((row.get("answer") or "").strip()),
                    "context_count": len(row.get("contexts") or []),
                    "error": row.get("runtime_error") or "invalid_generation_output",
                },
            )
            return False

        for item in dataset[:size]:
            await try_admit(item)

        # Fallback: resample from additional corpus rows until we reach target size.
        while len(filtered) < size and dataset_cursor < len(dataset):
            candidate = dataset[dataset_cursor]
            dataset_cursor += 1
            cid = str(candidate.get("id"))
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            await try_admit(candidate)

        # If benchmark_corpus.json is too small, generate additional rows and continue.
        if len(filtered) < size:
            needed = max(size * 3, size + 20)
            extra_dataset = generate_benchmark_dataset(needed)
            for candidate in extra_dataset:
                if len(filtered) >= size:
                    break
                cid = str(candidate.get("id"))
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                await try_admit(candidate)

        # Strict admission: if we cannot build a reliable set, fail early.
        min_required = max(2, int(size * 0.4))
        if len(filtered) < min_required:
            raise RuntimeError(
                f"preflight_rejected_too_many_rows: kept={len(filtered)} required={min_required} "
                f"(requested size={size}; survivorship-bias warning: only validate_generation_row passes were kept)"
            )
        if len(filtered) < size:
            print(
                f"WARNING: preflight shrank dataset {len(filtered)}/{size} "
                f"(min_required={min_required}); metrics are survivorship-biased."
            )
        dataset = filtered[:size]

    rows = await phase_generation(
        dataset=dataset,
        compare_baseline=compare_baseline,
        max_concurrency=max_concurrency,
        timeout_s=timeout_s,
        resume=resume,
        skip_existing=skip_existing,
        preflight_rows=preflight_rows,
    )

    results = await phase_evaluation(
        rows=rows,
        evals=evals,
        max_concurrency=max_concurrency,
        timeout_s=timeout_s,
        skip_existing=skip_existing,
    )

    phase_reporting(results)

    print("Benchmark complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument("--size", type=int, default=10)

    parser.add_argument("--evals", action="append", default=None)

    parser.add_argument(
        "--compare-baseline",
        action="store_true",
    )

    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=3,
    )

    parser.add_argument(
        "--timeout-s",
        type=float,
        default=45.0,
    )

    parser.add_argument(
        "--resume",
        action="store_true",
    )

    parser.add_argument(
        "--skip-existing",
        action="store_true",
    )

    parser.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()
    evals = args.evals if args.evals is not None else ["judge"]
    evals = [e.strip().lower() for e in evals if isinstance(e, str) and e.strip()]
    if not evals:
        evals = ["judge"]

    asyncio.run(
        run_benchmark(
            size=args.size,
            evals=evals,
            compare_baseline=args.compare_baseline,
            max_concurrency=args.max_concurrency,
            timeout_s=args.timeout_s,
            resume=args.resume,
            skip_existing=args.skip_existing,
            seed=args.seed,
        )
    )
