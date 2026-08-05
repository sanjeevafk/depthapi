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
explicitly migrated. The previous local Supabase Docker volume and corpus
backups must be retained until that migration is validated.

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

If Docker Compose is unavailable, start PostgreSQL with
`scripts/validate_rag_local.sh` or install the Docker Compose plugin.

Start the API:

```bash
python -m uvicorn api.main:app --reload
```

The database schema is initialized from
`db/migrations/001_schema.sql`; the development API key seed is in
`db/seed/001_dev_api_key.sql`. Set `DATABASE_URL` for a non-default PostgreSQL
connection and configure an embedding or language-model provider as needed.

## API examples

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
retrieval results before retiring the old corpus volume or backups.

## Development checks

```bash
pytest
python -m compileall -q api scripts evaluation
git diff --check
```
