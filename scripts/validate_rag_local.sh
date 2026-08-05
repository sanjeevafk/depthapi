#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/3] Starting PostgreSQL/pgvector"
if docker compose version >/dev/null 2>&1; then
  docker compose up -d postgres
else
  echo "Docker Compose plugin unavailable; using docker run fallback"
  if ! docker container inspect depthapi-postgres >/dev/null 2>&1; then
    docker volume create depthapi_pg_data >/dev/null
    docker run -d --name depthapi-postgres \
      -e POSTGRES_DB=depthapi \
      -e POSTGRES_USER=depthapi \
      -e POSTGRES_PASSWORD=depthapi \
      -p 5432:5432 \
      -v depthapi_pg_data:/var/lib/postgresql/data \
      -v "$ROOT_DIR/db/migrations/001_schema.sql:/docker-entrypoint-initdb.d/001_schema.sql:ro" \
      -v "$ROOT_DIR/db/seed/001_dev_api_key.sql:/docker-entrypoint-initdb.d/002_dev_api_key.sql:ro" \
      pgvector/pgvector:pg17
  else
    docker start depthapi-postgres >/dev/null 2>&1 || true
  fi
fi

for _ in {1..30}; do
  if docker exec depthapi-postgres pg_isready -U depthapi -d depthapi >/dev/null 2>&1; then break; fi
  sleep 1
done

echo "[2/3] Checking schema and embedding coverage"
if docker compose version >/dev/null 2>&1; then
  DB_EXEC=(docker compose exec -T postgres)
else
  DB_EXEC=(docker exec -i depthapi-postgres)
fi
"${DB_EXEC[@]}" psql -U depthapi -d depthapi -v ON_ERROR_STOP=1 <<'SQL'
SELECT extname FROM pg_extension WHERE extname = 'vector';
SELECT get_embedding_dimension();
SELECT count(*)::int AS total_chunks,
       count(*) FILTER (WHERE embedding IS NULL)::int AS null_embeddings,
       count(*) FILTER (WHERE embedding IS NOT NULL)::int AS populated_embeddings
FROM knowledge_chunks;
SQL

echo "[3/3] Running API regression tests"
pytest -q
