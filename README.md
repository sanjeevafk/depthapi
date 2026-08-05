# DepthAPI

DepthAPI is a local-first retrieval-augmented generation API. PostgreSQL with
pgvector is the authoritative datastore for documents, chunks, embeddings, and
full-text search. Redis is available for transient application caching.

Turso/libSQL is an optional downstream edge store for cached, augmented, and
backup retrieval. It is not a source of truth. Supabase is not required by the
application or ingestion pipeline.

## Current status

The API and local ingestion pipeline are operational. The PostgreSQL schema,
API-key authentication, ingestion and query routes, offline chunking pipeline,
and PostgreSQL-to-Turso replication path are implemented.

The current PostgreSQL development database is empty until the legacy corpus is
explicitly migrated. The previous local database volume and corpus backups
must be retained until that migration is validated.

The test suite currently passes 141 tests. Run the validation script to check
the local database and execute the suite:

```bash
scripts/validate_rag_local.sh
```

The script uses Docker Compose when available and falls back to `docker run`
for installations without the Compose plugin.

## Quick start

Start PostgreSQL and Redis:

```bash
docker compose up -d
```

If Docker Compose is unavailable, use `scripts/validate_rag_local.sh` or
install the Docker Compose plugin.

Start the API:

```bash
python -m uvicorn api.main:app --reload
```

The database schema is initialized from
`db/migrations/001_schema.sql`; the development API key seed is in
`db/seed/001_dev_api_key.sql`. Set `DATABASE_URL` for a non-default PostgreSQL
connection and configure an embedding or language-model provider as needed.

## API

| Endpoint | Description |
|---|---|
| `GET /api/health` | Liveness probe |
| `POST /api/ingest` | Store a document and queue it for retrieval |
| `POST /api/query` | Hybrid vector and lexical retrieval with answer synthesis |
| `POST /api/query/stream` | Buffered SSE response for compatibility |

All endpoints except `/api/health` require `Authorization: Bearer <api-key>`.

Ingest a document:

```bash
curl -X POST http://localhost:8000/api/ingest \
  -H 'Authorization: Bearer sk-depth-dev-local-0000000000000000' \
  -H 'content-type: application/json' \
  -d '{"collection_name":"docs","filename":"intro.md","raw_text":"DepthAPI stores searchable knowledge locally."}'
```

Query it:

```bash
curl -X POST http://localhost:8000/api/query \
  -H 'Authorization: Bearer sk-depth-dev-local-0000000000000000' \
  -H 'content-type: application/json' \
  -d '{"query":"Where is knowledge stored?"}'
```

## Corpus migration and edge replication

The research-corpus exporter reads PostgreSQL directly. After importing the
legacy corpus into PostgreSQL, replicate it to Turso with:

```bash
export DATABASE_URL=postgresql://...
export TURSO_DATABASE_URL=libsql://...
export TURSO_AUTH_TOKEN=...
python scripts/turso/sync_platform.py --full
```

Initialize the Turso schema with `scripts/turso/schema.sql` before the first
replication. Validate row counts, embedding dimensions, metadata, and sample
retrieval results before retiring the old database volume or backups.

## Project layout

```text
api/                 FastAPI application and PostgreSQL adapter
db/                  PostgreSQL schema and development seed data
scripts/             Offline ingestion, migration, validation, and replication
evaluation/          Offline evaluation harnesses
demo/                Standalone demo server
tests/               Unit, integration, and quality tests
```

## Development checks

```bash
pip install -e ".[dev]"
pytest
python -m compileall -q api scripts evaluation
git diff --check
```

## Code navigation graph

The optional `code-review-graph` tool maintains an ignored structural index in
`.code-review-graph/`. It can answer symbol, caller, flow, community, and risk
queries without loading the entire repository into an agent context.

Refresh it manually with:

```bash
scripts/refresh_code_review_graph.sh
```

The repository post-commit hook refreshes the graph after five or more commits
since its last build. Enable the managed hooks once per checkout with:

```bash
git config core.hooksPath .githooks
```
