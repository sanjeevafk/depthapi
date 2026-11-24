# DepthAPI

DepthAPI is a local-first retrieval-augmented generation (RAG) engine. PostgreSQL with `pgvector` is the primary datastore for documents, chunks, embeddings, and full-text search, with Redis for transient query caching and token quotas, and Turso (libSQL) for downstream edge retrieval and caching.

---

## Capabilities

- **Document Ingestion (`POST /api/ingest`, `POST /api/ingest/file`):** Raw text/Markdown ingestion plus multipart file uploads (PDF, DOCX, XLSX, PPTX, CSV, HTML, Markdown) converted to Markdown via `markitdown`, with a 75 MB payload limit and content-hash idempotency.
- **Hybrid Retrieval:** Dense cosine similarity (`dense_search_v5`) with hybrid dense + lexical Reciprocal Rank Fusion (`hybrid_search_v5`, `hybrid_search_v6`) and optional concept-graph expansion (`hybrid_search_with_graph_v5`).
- **Negative Query Algebra:** Parses negation syntax (`NOT term`, `-term`, `without term`) and downweights matching candidates.
- **Cognitive Depth (Levels 1–5):**
  - **Depths 1–2:** Direct concept summaries from `knowledge_concepts`.
  - **Depths 3–4:** Dense-first semantic search with hybrid RRF fallback and intent-based 1-hop graph expansion.
  - **Depth 5:** Forced reranking (`BAAI/bge-reranker-base` when `sentence-transformers` is installed) and U-shaped Lost-in-the-Middle context ordering.
- **Explainability & Streaming:** `explain: true` returns retrieval scoring and chunk provenance; `POST /api/query/stream` streams Server-Sent Events (SSE).

---

## Quick Start

### 1. Start Infrastructure (PostgreSQL + pgvector & Redis)

```bash
docker compose up -d
```

Database migrations in `db/migrations/` (`001`–`006`) and the dev API key seed (`db/seed/001_dev_api_key.sql`) are mounted automatically.

### 2. Install & Start the API

```bash
pip install -e ".[dev]"
python -m uvicorn api.main:app --reload --port 8000
```

---

## API Endpoints

All endpoints except `/api/health` require `Authorization: Bearer <api-key>`. Default local dev key: `sk-depth-dev-local-0000000000000000`.

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | `GET` | Service liveness probe |
| `/api/ingest` | `POST` | Store raw markdown or text document and compute embeddings |
| `/api/ingest/file` | `POST` | Multipart file upload converted via `markitdown` |
| `/api/query` | `POST` | Hybrid retrieval with cognitive depth (1–5), negative algebra, and explainability |
| `/api/query/stream` | `POST` | Server-Sent Events (SSE) streaming completion |

### Usage Examples

#### Ingest Raw Text
```bash
curl -X POST http://localhost:8000/api/ingest \
  -H 'Authorization: Bearer sk-depth-dev-local-0000000000000000' \
  -H 'Content-Type: application/json' \
  -d '{
    "collection_name": "docs",
    "filename": "intro.md",
    "raw_text": "DepthAPI stores searchable knowledge locally."
  }'
```

#### Ingest Document Files (PDF, Office, Spreadsheets)
```bash
curl -X POST http://localhost:8000/api/ingest/file \
  -H 'Authorization: Bearer sk-depth-dev-local-0000000000000000' \
  -F 'file=@handbook.pdf' \
  -F 'collection_name=docs'
```

#### Query with Negative Algebra & Explainability
```bash
curl -X POST http://localhost:8000/api/query \
  -H 'Authorization: Bearer sk-depth-dev-local-0000000000000000' \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "FastAPI authentication NOT oauth2",
    "depth": 3,
    "explain": true
  }'
```

---

## Architecture & Deployment

```text
  Client / Agent ──(Bearer Auth)──▶ FastAPI (api.main:app)
                                       ├── Redis (query cache / token quotas)
                                       └── PostgreSQL + pgvector (primary store)
                                                │
                                                ▼ (sync)
                                           Turso / libSQL (edge retrieval & cache)
```

### Docker Build

```bash
docker build -f api/Dockerfile -t depthapi:latest .
```

### Edge Replication to Turso (libSQL)

```bash
export DATABASE_URL=postgresql://...
export TURSO_DATABASE_URL=libsql://...
export TURSO_AUTH_TOKEN=...
python scripts/turso/sync_platform.py --full
```

---

## Development Checks

```bash
pytest -q
ruff check api tests
python -m compileall -q api tests evaluation scripts
```
