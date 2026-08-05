# DepthAPI

> **⚠️ Work in progress** — DepthAPI is under active development and not yet production-ready. The API surface is intentionally minimal, and parts of the repository carry legacy or experimental code. 

Local-first RAG (retrieval-augmented generation) API backed by **PostgreSQL (pgvector)** and **Redis**. Ingest documents, query them with hybrid vector + lexical search, and receive LLM-synthesized answers with citations — all running locally with no mandatory external services.

## Features

- **Hybrid retrieval in SQL** — pgvector cosine similarity + full-text search combined in one Postgres function (`hybrid_search_v5`), with per-API-key tenant isolation.
- **Zero-dependency mode** — with no API keys configured, embeddings use a deterministic local fallback and the query endpoint returns retrieved source excerpts instead of LLM text.
- **Hash-only API keys** — credentials are stored as SHA-256 digests, never plaintext.
- **Offline ingestion pipeline** — plugin-based `Source → Parser → Middleware → Chunker → Sink` orchestrator with incremental/resume modes, source fingerprints, and a dead-letter queue.
- **Research-corpus pipeline** — crawl → chunk → dedup (minhash + fuzzy n-gram) → validate → benchmark → publish to Hugging Face.
- **Evaluation harness** — RAGAS, DeepEval, and judge-based prompt-spec evaluation against ground truth.

## Status

| Area | State |
|---|---|
| `POST /api/ingest`, `POST /api/query` | Functional; minimal by design |
| Real chunking on the ingest path | **Missing** — `/api/ingest` currently stores each document as a single chunk; the offline chunking pipeline is not wired in |
| Streaming (`/query/stream`) | Stub — buffers the full response, emits one SSE event |
| Offline ingestion / evaluation tooling | Most mature part of the repo; some scripts are broken leftovers (see audit) |
| Legacy Supabase/Turso code | Present but retired; tracked in [DEAD_CODE_AUDIT.md](./DEAD_CODE_AUDIT.md) |



## Quick start

Requires Docker (PostgreSQL + pgvector, Redis) and Python 3.11+.

```bash
cp .env.example .env   # adjust secrets if needed
make up                # start postgres + redis
make dev               # run the API on http://localhost:8000
```

The database schema is initialized from `db/migrations/001_schema.sql`; the development API key is seeded from `db/seed/001_dev_api_key.sql` (`sk-depth-dev-local-0000000000000000`).

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

With `OPENAI_API_KEY` configured, responses are synthesized by the configured model; otherwise the API returns retrieved source excerpts.

## API

| Endpoint | Description |
|---|---|
| `GET /api/health` | Liveness probe |
| `POST /api/ingest` | Store a document (with optional `collection_id`, `filename`, `source_url`, `metadata`) and index it for retrieval |
| `POST /api/query` | Hybrid retrieval + answer synthesis, returns `answer`, `contexts`, and `citations` |
| `POST /api/query/stream` | SSE variant (currently buffered, not true streaming) |

All endpoints except `/api/health` require `Authorization: Bearer <api-key>`.

## Project layout

```
api/                 FastAPI app: routers, services (inference, rag, security), pg adapter
db/migrations/       Postgres schema: tables, indexes, hybrid-search functions
db/seed/             Development API key
scripts/             Offline ingestion tooling, corpus pipeline, key generation
evaluation/          RAGAS / DeepEval / judge-based evaluation harness
demo/                Standalone interactive demo server
tests/               Unit, integration, and quality tests
```

Key services inside `api/`:

- `services/rag/embeddings.py` — OpenAI embeddings with deterministic local fallback (768-dim)
- `services/rag/knowledge_retrieval.py` — retrieval service wrapper around the Postgres RPCs
- `services/inference/inference.py` — LLM answer synthesis with truthful excerpt fallback
- `services/security/api_key_auth.py` — Bearer key → SHA-256 lookup
- `services/rag/pipeline/` — offline plugin ingestion orchestrator (used by `scripts/ingest_pipeline.py`)

## Development

```bash
pip install -e ".[dev]"        # or: pip install -r api/requirements-dev.txt
pytest                         # runs api/tests (see note below)
```

- **Tests:** live tests live in `api/tests/`; a broader suite lives in `tests/` (unit, integration, quality). Note: the default `pytest` configuration only collects `api/tests` — see the audit (Tier 4) for the config duplication.
- **Type checking:** `pyright` (config in `pyproject.toml`; the `strict` list is stale — see audit).
- **Evaluation:** `evaluation/` contains the offline QA harness (RAGAS, DeepEval, judge runs).

---

¹ **Work in progress:** DepthAPI is under active development and not yet production-ready. The runtime API surface is minimal (ingest + query), streaming currently buffers responses, the ingest path does not yet use the real chunking pipeline, and several offline scripts reference removed legacy (Supabase/Turso) code. Known dead code and recommended cleanup are tracked in [DEAD_CODE_AUDIT.md](./DEAD_CODE_AUDIT.md).
