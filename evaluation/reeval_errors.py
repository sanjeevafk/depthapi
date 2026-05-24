import json
import asyncio
from pathlib import Path

RESULTS_PATH = Path("results/raw/all_results.json")
OUT_PATH = Path("results/raw/all_results_reeval.json")

if not RESULTS_PATH.exists():
    print("Results file not found:", RESULTS_PATH)
    raise SystemExit(1)

with open(RESULTS_PATH, "r") as f:
    results = json.load(f)

from run_judge import CustomLLMJudge
from run_deepeval import evaluate_deepeval
from run_ragas import evaluate_ragas

judge = CustomLLMJudge()

async def run_judge_async(q, a, ctx, spec):
    return await judge.evaluate(q, a, ctx, prompt_spec=spec)

updated = 0
skipped = 0
errors = 0

for i, entry in enumerate(results):
    q = entry.get("query")
    a = entry.get("answer")
    ctx = entry.get("context") or entry.get("contexts") or []
    spec = entry.get("prompt_spec")

    # Detect problematic judge results: all zeros or reasoning contains 'error' or 'rate limit'
    judge = entry.get("judge")
    judge_bad = False
    if not judge or not isinstance(judge, dict):
        judge_bad = True
    else:
        if int(judge.get("depth_compliance", 0)) == 0 and int(judge.get("answer_quality", 0)) == 0 and int(judge.get("faithfulness", 0)) == 0:
            judge_bad = True
        reasoning = str(judge.get("reasoning", "")).lower()
        if "error" in reasoning or "rate limit" in reasoning or "invalid json" in reasoning:
            judge_bad = True

    # Detect deepeval/ragas missing or zeroed
    deepeval = entry.get("deepeval") or {}
    deepeval_bad = not deepeval or (deepeval.get("deepeval_relevancy", 0) == 0 and deepeval.get("deepeval_faithfulness", 0) == 0)
    ragas = entry.get("ragas") or {}
    ragas_bad = not ragas or (ragas.get("ragas_answer_relevancy", 0) == 0 and ragas.get("ragas_faithfulness", 0) == 0)

    if not (judge_bad or deepeval_bad or ragas_bad):
        skipped += 1
        continue

    # Try to re-evaluate problematic pieces
    try:
        if judge_bad:
            try:
                jres = asyncio.run(run_judge_async(q, a, ctx, spec))
                entry["judge"] = jres
            except Exception as e:
                entry.setdefault("_reeval_error", "")
                entry["_reeval_error"] += f"judge_retry_failed: {e};"

        if deepeval_bad:
            try:
                dres = evaluate_deepeval(q, a, ctx)
                entry["deepeval"] = dres
            except Exception as e:
                entry.setdefault("_reeval_error", "")
                entry["_reeval_error"] += f"deepeval_retry_failed: {e};"

        if ragas_bad:
            try:
                rres = evaluate_ragas(q, a, ctx)
                entry["ragas"] = rres
            except Exception as e:
                entry.setdefault("_reeval_error", "")
                entry["_reeval_error"] += f"ragas_retry_failed: {e};"

        # If judge now looks healthy, clear previous top-level error fields
        j = entry.get("judge")
        if isinstance(j, dict) and int(j.get("depth_compliance", 0)) > 0:
            entry.pop("error", None)

        updated += 1
    except Exception as e:
        entry.setdefault("_reeval_error", "")
        entry["_reeval_error"] += f"outer_failure: {e};"
        errors += 1

with open(OUT_PATH, "w") as f:
    json.dump(results, f, indent=2)

print(f"Re-eval complete. updated={updated}, skipped={skipped}, errors={errors}")
print("Wrote:", OUT_PATH)
