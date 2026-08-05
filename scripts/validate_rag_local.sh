#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/3] Starting PostgreSQL/pgvector"
docker compose up -d postgres

echo "[2/3] Checking schema and embedding coverage"
docker compose exec -T postgres psql -U depthapi -d depthapi -v ON_ERROR_STOP=1 <<'SQL'
SELECT extname FROM pg_extension WHERE extname = 'vector';
SELECT get_embedding_dimension();
SELECT count(*)::int AS total_chunks,
       count(*) FILTER (WHERE embedding IS NULL)::int AS null_embeddings,
       count(*) FILTER (WHERE embedding IS NOT NULL)::int AS populated_embeddings
FROM knowledge_chunks;
SQL

echo "[3/3] Running API regression tests"
pytest -q
