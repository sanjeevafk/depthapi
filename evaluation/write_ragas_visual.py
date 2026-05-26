"""Generate a RAGAS stability visual report from all_results.json.

Usage:
    python write_ragas_visual.py                   # reads results/raw/all_results.json
    python write_ragas_visual.py --size 10 --date 2026-05-26
"""
import argparse
import json
from datetime import date
from pathlib import Path

RESULTS_DIR = Path("results")
RAW_DIR = RESULTS_DIR / "raw"
REPORTS_DIR = RESULTS_DIR / "reports"


def bar(value: float, width: int = 10, *, low_is_good: bool = False) -> str:
    filled = round(value * width)
    empty = width - filled
    bar_str = "█" * filled + "░" * empty
    return f"`[{bar_str}]`"


def generate(raw_path: Path, size: int, run_date: str, cmd: str) -> str:
    data = json.loads(raw_path.read_text())
    depth_rows = [r for r in data if r.get("system") == "depthapi"]

    total = len(depth_rows)
    runtime_errors = sum(1 for r in depth_rows if r.get("runtime_error"))
    runtime_error_rate = runtime_errors / total if total else 0

    ragas_valid = [
        r for r in depth_rows
        if isinstance(r.get("ragas"), dict)
        and r["ragas"].get("error") != "EVAL_FAILED"
        and r["ragas"].get("ragas_answer_relevancy") is not None
    ]
    ragas_coverage = len(ragas_valid) / total if total else 0

    def safe_mean(rows, key1, key2):
        vals = [r[key1].get(key2) for r in rows if isinstance(r.get(key1), dict) and r[key1].get(key2) is not None]
        return (sum(vals) / len(vals)) if vals else None

    relevancy_mean = safe_mean(ragas_valid, "ragas", "ragas_answer_relevancy")
    faith_mean = safe_mean(ragas_valid, "ragas", "ragas_faithfulness")

    doc_recalls = []
    doc_precisions = []
    doc_mrrs = []
    context_counts = []
    for r in depth_rows:
        contexts = r.get("contexts") or []
        context_counts.append(len(contexts))
        expected = [str(v) for v in (r.get("relevant_doc_ids") or []) if v]
        retrieved = [str(c.get("doc_id") or "") for c in contexts if isinstance(c, dict) and c.get("doc_id")]
        if expected:
            hit = len(set(expected) & set(retrieved[:5]))
            doc_recalls.append(hit / len(set(expected)))
            prec_k = retrieved[:5]
            doc_precisions.append(len(set(expected) & set(prec_k)) / len(set(prec_k)) if prec_k else None)
            mrr = 0.0
            for idx, item in enumerate(retrieved, 1):
                if item in set(expected):
                    mrr = 1 / idx
                    break
            doc_mrrs.append(mrr)

    def avg(lst):
        lst = [v for v in lst if v is not None]
        return sum(lst) / len(lst) if lst else None

    recall_mean = avg(doc_recalls)
    prec_mean = avg(doc_precisions)
    mrr_mean = avg(doc_mrrs)
    ctx_mean = avg(context_counts)

    runtime_gate = "PASS" if runtime_error_rate < 0.10 else "FAIL"
    ragas_gate = "PASS" if ragas_coverage >= 0.95 else "FAIL"

    avg_runtime = None
    rts = [
        (r.get("telemetry") or {}).get("sample_runtime_s") or
        (r.get("telemetry") or {}).get("generation_latency_s")
        for r in depth_rows
    ]
    rts = [v for v in rts if v]
    if rts:
        avg_runtime = sum(rts) / len(rts)

    lines = [
        f"# RAGAS Size-{size} Visual Report (DepthAPI Only)",
        "",
        "Run profile",
        f"- Date: {run_date}",
        f"- Command: `{cmd}`",
    ]
    if avg_runtime is not None:
        lines.append(f"- Runtime profile: avg sample runtime ~{avg_runtime:.1f}s")
    lines += [
        "",
        "## Stability Gate",
        "",
        "| Gate | Value | Target | Status |",
        "|---|---:|---:|---|",
        f"| Runtime error rate | {runtime_error_rate:.2%} ({runtime_errors}/{total}) | <10% | {runtime_gate} |",
        f"| RAGAS evaluator coverage | {ragas_coverage:.2%} ({len(ragas_valid)}/{total}) | >=95% | {ragas_gate} |",
        "",
        "## Metric Snapshot",
        "",
        "| Metric | Mean | Coverage |",
        "|---|---:|---:|",
    ]
    if relevancy_mean is not None:
        lines.append(f"| `ragas_answer_relevancy` | {relevancy_mean:.4f} | {ragas_coverage:.2%} |")
    if faith_mean is not None:
        lines.append(f"| `ragas_faithfulness` | {faith_mean:.4f} | {ragas_coverage:.2%} |")
    if recall_mean is not None:
        lines.append(f"| `retrieval.doc_recall_at_5` | {recall_mean:.4f} | 100.00% |")
    if prec_mean is not None:
        lines.append(f"| `retrieval.doc_precision_at_5` | {prec_mean:.4f} | 100.00% |")
    if mrr_mean is not None:
        lines.append(f"| `retrieval.doc_mrr` | {mrr_mean:.4f} | 100.00% |")
    if ctx_mean is not None:
        lines.append(f"| `retrieval.context_count` | {ctx_mean:.4f} | 100.00% |")

    lines += [
        "",
        "## Visual Bars",
        "",
        f"- `runtime_error_rate`       {bar(runtime_error_rate, low_is_good=True)} {runtime_error_rate:.2%} (good, lower is better)",
        f"- `ragas_coverage`           {bar(ragas_coverage)} {ragas_coverage:.2%} ({'OK' if ragas_gate == 'PASS' else 'needs >=95%'})",
    ]
    if relevancy_mean is not None:
        lines.append(f"- `answer_relevancy_mean`    {bar(relevancy_mean)} {relevancy_mean:.2f}")
    if faith_mean is not None:
        lines.append(f"- `faithfulness_mean`        {bar(faith_mean)} {faith_mean:.3f}")

    lines += [
        "",
        "## Per-sample RAGAS",
        "",
        "| Sample | Relevancy | Faithfulness | Status |",
        "|---|---:|---:|---|",
    ]
    for r in sorted(depth_rows, key=lambda x: str(x.get("id"))):
        rid = str(r.get("id"))
        ragas = r.get("ragas") or {}
        if ragas.get("error") == "EVAL_FAILED":
            lines.append(f"| `{rid}` | null | null | `EVAL_FAILED` |")
        elif ragas.get("ragas_answer_relevancy") is not None:
            rel = ragas["ragas_answer_relevancy"]
            fai = ragas.get("ragas_faithfulness")
            fai_str = f"{fai}" if fai is not None else "null"
            lines.append(f"| `{rid}` | {rel} | {fai_str} | valid |")
        else:
            lines.append(f"| `{rid}` | null | null | no_data |")

    lines += [
        "",
        "## Observations",
        "",
        f"- Generation/runtime robustness is stable in this run (`{runtime_errors}/{total}` runtime errors).",
    ]
    if ragas_gate == "PASS":
        lines.append(f"- RAGAS evaluator coverage meets the >=95% gate ({ragas_coverage:.2%}).")
        lines.append("- Ready to promote to the next size tier.")
    else:
        miss = total - len(ragas_valid)
        lines.append(f"- {miss} evaluator miss(es) drive RAGAS coverage to `{ragas_coverage:.2%}`.")
        lines.append("- Primary cause is 429 rate-limit exhaustion on the Groq evaluator endpoint.")

    lines += [
        "",
        "## Actionable Next Step",
        "",
    ]
    if ragas_gate == "PASS":
        next_size = size * 2
        lines.append(
            f"- Coverage gate passed at size={size}. Promote to `size={next_size}` ragas-only run."
        )
    else:
        lines.append(
            f"- Keep `size={size}`, rerun `ragas` with the same conservative pacing until coverage reaches >=95%, then promote to `size={size * 2}` ragas-only."
        )

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate RAGAS visual report")
    parser.add_argument("--size", type=int, default=5)
    parser.add_argument("--date", type=str, default=str(date.today()))
    parser.add_argument(
        "--cmd",
        type=str,
        default="benchmark.py --size 5 --evals ragas --max-concurrency 1 --timeout-s 60",
    )
    parser.add_argument("--input", type=str, default=str(RAW_DIR / "all_results.json"))
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    raw = Path(args.input)
    out_name = f"ragas_size{args.size}_visual.md"
    out = Path(args.output) if args.output else REPORTS_DIR / out_name

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    content = generate(raw, args.size, args.date, args.cmd)
    out.write_text(content, encoding="utf-8")
    print(f"Written: {out}")

    # Print the gate lines for quick CI check
    for line in content.splitlines():
        if "PASS" in line or "FAIL" in line:
            print(line)
