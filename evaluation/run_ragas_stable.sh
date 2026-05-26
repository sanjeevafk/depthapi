#!/usr/bin/env bash
# run_ragas_stable.sh — Stabilization runner for RAGAS at size=5 and size=10.
#
# Usage:
#   GROQ_API_KEY=<key> bash run_ragas_stable.sh          # size=5 (default)
#   GROQ_API_KEY=<key> bash run_ragas_stable.sh --size 10
#
# Strategy:
#   - --resume:            reuse generation checkpoint (skip preflight + generation for cached rows)
#   - NO --skip-existing:  force fresh evaluation (bypasses stale EVAL_FAILED cache entries)
#   - --max-concurrency 1: serial eval to avoid concurrent 429 hammering on Groq
#   - --timeout-s 60:      extra headroom for DepthAPI on slower connections
#   - EVAL_CALL_DELAY_SECONDS=2.0: 2s pacing between evaluator HTTP calls (~30 req/min safe zone)

set -euo pipefail

SIZE="${1:-5}"
# Allow passing --size N
if [[ "$#" -ge 2 && "$1" == "--size" ]]; then
    SIZE="$2"
fi

if [[ -z "${GROQ_API_KEY:-}" ]]; then
    echo "ERROR: GROQ_API_KEY is not set. Export it before running:"
    echo "  export GROQ_API_KEY=<your_key>"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/evaluation"

export EVALUATOR_PROVIDER="groq"
export EVALUATOR_MODEL="llama-3.3-70b-versatile"
export EVAL_CALL_DELAY_SECONDS="2.0"
export EVAL_HTTP_RETRIES="4"
export EVAL_HTTP_MAX_BACKOFF_SECONDS="60"

echo "=== RAGAS Stabilization Run ==="
echo "  Size:     ${SIZE}"
echo "  Provider: ${EVALUATOR_PROVIDER} / ${EVALUATOR_MODEL}"
echo "  Pacing:   ${EVAL_CALL_DELAY_SECONDS}s between evaluator calls"
echo "  Retries:  ${EVAL_HTTP_RETRIES} (max backoff ${EVAL_HTTP_MAX_BACKOFF_SECONDS}s)"
echo ""

python3 benchmark.py \
    --size "${SIZE}" \
    --evals ragas \
    --max-concurrency 1 \
    --timeout-s 60 \
    --resume

echo ""
echo "=== Generating visual report ==="
python3 write_ragas_visual.py \
    --size "${SIZE}" \
    --cmd "benchmark.py --size ${SIZE} --evals ragas --max-concurrency 1 --timeout-s 60 --resume"

echo ""
echo "Done. Check: evaluation/results/reports/ragas_size${SIZE}_visual.md"
