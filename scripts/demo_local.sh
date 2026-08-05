#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage: scripts/demo_local.sh [--start-api]

Actions:
  1) Switches .env to local mode
  2) Ensures local PostgreSQL is running
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

echo "[2/4] Checking local PostgreSQL"
docker compose up -d postgres

echo "[3/4] Running local schema sanity checks"
docker compose exec -T postgres psql -U depthapi -d depthapi -c "select get_embedding_dimension();"
docker compose exec -T postgres psql -U depthapi -d depthapi -c "select count(*)::int as chunk_count from knowledge_chunks;"

echo "[4/4] Environment summary"
set -a
source .env
set +a
echo "DATABASE_URL=${DATABASE_URL:-unset}"
echo "RAG_BACKEND=${RAG_BACKEND:-unset}"

if [[ "$start_api" == "true" ]]; then
  echo "Starting API server..."
  exec python3 -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
fi

echo "Local demo environment is ready."
