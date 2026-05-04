#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SUPABASE_BIN="tools/bin/supabase"
BACKFILL_BIN=".venv-ingest/bin/python"
BACKFILL_SCRIPT="scripts/ingest_corpus/backfill_chunk_embeddings_supabase.py"
REPORT_DIR="data/rag/reports"

BATCH_SIZE="${BATCH_SIZE:-100}"
MAX_BATCHES="${MAX_BATCHES:-20}"

usage() {
  cat <<EOF
Usage: scripts/validate_rag_local.sh [--no-backfill]

Env knobs:
  BATCH_SIZE   default: ${BATCH_SIZE}
  MAX_BATCHES  default: ${MAX_BATCHES}

Notes:
  - Uses local Supabase DB (--local)
  - Uses current .env for embedding provider key
  - Generates report under ${REPORT_DIR}
EOF
}

run_backfill=true
if [[ "${1:-}" == "--no-backfill" ]]; then
  run_backfill=false
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
elif [[ -n "${1:-}" ]]; then
  usage
  exit 1
fi

mkdir -p "$REPORT_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="${REPORT_DIR}/local_rag_validation_${TS}.json"

echo "[1/5] Switching to local env"
scripts/switch_env.sh local >/dev/null

echo "[2/5] Ensuring local Supabase is up"
if ! "$SUPABASE_BIN" status >/tmp/depthapi-supabase-status.txt 2>&1; then
  "$SUPABASE_BIN" start >/tmp/depthapi-supabase-start.txt 2>&1 || {
    echo "Failed to start Supabase. See /tmp/depthapi-supabase-start.txt"
    exit 1
  }
fi

echo "[3/5] Gathering pre-backfill metrics"
PRE_COUNTS="$("$SUPABASE_BIN" db query --local "select count(*)::int as total_chunks, count(*) filter (where embedding is null and deleted_at is null)::int as null_embeddings, count(*) filter (where embedding is not null and deleted_at is null)::int as populated_embeddings from public.knowledge_chunks;" -o json)"
DIMENSION_JSON="$("$SUPABASE_BIN" db query --local "select * from public.get_embedding_dimension();" -o json)"

BACKFILL_OUTPUT=""
if [[ "$run_backfill" == "true" ]]; then
  echo "[4/5] Running backfill window (batch_size=${BATCH_SIZE}, max_batches=${MAX_BATCHES})"
  set -a
  source .env
  if [[ -f .env.cloud ]]; then
    # preserve embedding keys if local env doesn't carry them
    source .env.cloud
  fi
  set +a

  if [[ ! -x "$BACKFILL_BIN" ]]; then
    echo "Missing ${BACKFILL_BIN}. Create/install .venv-ingest first."
    exit 1
  fi
  BACKFILL_OUTPUT="$("$BACKFILL_BIN" "$BACKFILL_SCRIPT" --batch-size "$BATCH_SIZE" --max-batches "$MAX_BATCHES" 2>&1 || true)"
fi

echo "[5/5] Gathering post-backfill metrics and writing report"
POST_COUNTS="$("$SUPABASE_BIN" db query --local "select count(*)::int as total_chunks, count(*) filter (where embedding is null and deleted_at is null)::int as null_embeddings, count(*) filter (where embedding is not null and deleted_at is null)::int as populated_embeddings from public.knowledge_chunks;" -o json)"

PRE_FILE="$(mktemp)"
DIM_FILE="$(mktemp)"
POST_FILE="$(mktemp)"
BACKFILL_FILE="$(mktemp)"
printf "%s" "$PRE_COUNTS" > "$PRE_FILE"
printf "%s" "$DIMENSION_JSON" > "$DIM_FILE"
printf "%s" "$POST_COUNTS" > "$POST_FILE"
printf "%s" "$BACKFILL_OUTPUT" > "$BACKFILL_FILE"

python3 - <<PY
import json
from pathlib import Path

report = {
  "timestamp_utc": "${TS}",
  "mode": "local",
  "batch_size": int("${BATCH_SIZE}"),
  "max_batches": int("${MAX_BATCHES}"),
  "pre_counts": json.loads(Path("${PRE_FILE}").read_text(encoding="utf-8")),
  "dimension": json.loads(Path("${DIM_FILE}").read_text(encoding="utf-8")),
  "backfill_output": Path("${BACKFILL_FILE}").read_text(encoding="utf-8").strip(),
  "post_counts": json.loads(Path("${POST_FILE}").read_text(encoding="utf-8")),
}
Path("${REPORT_FILE}").write_text(json.dumps(report, indent=2), encoding="utf-8")
print("${REPORT_FILE}")
PY

rm -f "$PRE_FILE" "$DIM_FILE" "$POST_FILE" "$BACKFILL_FILE"

echo "Validation report written: ${REPORT_FILE}"
