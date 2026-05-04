#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SUPABASE_BIN="tools/bin/supabase"

usage() {
  cat <<'EOF'
Usage: scripts/demo_local.sh [--start-api]

Actions:
  1) Switches .env to local mode
  2) Ensures local Supabase is running
  3) Prints local status, URLs, and key sanity checks
  4) Optionally starts API server

Options:
  --start-api    Start uvicorn API server after checks
EOF
}

start_api=false
if [[ "${1:-}" == "--start-api" ]]; then
  start_api=true
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
elif [[ -n "${1:-}" ]]; then
  usage
  exit 1
fi

echo "[1/4] Switching environment to local"
scripts/switch_env.sh local >/dev/null
scripts/switch_env.sh status

echo "[2/4] Checking local Supabase"
if ! "$SUPABASE_BIN" status >/tmp/depthapi-supabase-status.txt 2>&1; then
  echo "Local Supabase is not running. Starting..."
  "$SUPABASE_BIN" start >/tmp/depthapi-supabase-start.txt 2>&1 || {
    echo "Failed to start Supabase. See /tmp/depthapi-supabase-start.txt"
    exit 1
  }
fi
"$SUPABASE_BIN" status

echo "[3/4] Running local schema sanity checks"
"$SUPABASE_BIN" db query --local "select * from public.get_embedding_dimension();" -o table
"$SUPABASE_BIN" db query --local "select count(*)::int as chunk_count from public.knowledge_chunks;" -o table

echo "[4/4] Environment summary"
set -a
source .env
set +a
echo "SUPABASE_URL=${SUPABASE_URL:-unset}"
echo "RAG_BACKEND=${RAG_BACKEND:-unset}"
echo "LOCAL_PGVECTOR_URL=${LOCAL_PGVECTOR_URL:-unset}"

if [[ "$start_api" == "true" ]]; then
  echo "Starting API server..."
  exec python3 -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
fi

echo "Local demo environment is ready."
