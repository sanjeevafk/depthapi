# DepthAPI

Local-first RAG API backed by PostgreSQL with pgvector and Redis.

## Quick start

```bash
cp .env.example .env
make up
make dev
```

Ingest a document:

```bash
curl -X POST http://localhost:8000/api/ingest -H 'content-type: application/json' \
  -d '{"collection_name":"docs","filename":"intro.md","raw_text":"DepthAPI stores searchable knowledge locally."}'
```

Query it:

```bash
curl -X POST http://localhost:8000/api/query -H 'content-type: application/json' \
  -d '{"query":"Where is knowledge stored?"}'
```

The database schema is initialized from `db/migrations/001_schema.sql`; the development key seed is in `db/seed/001_dev_api_key.sql`.
