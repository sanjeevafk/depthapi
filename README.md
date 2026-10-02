# DepthAPI

DepthAPI is a high-performance, local-first retrieval-augmented generation (RAG) engine. PostgreSQL with pgvector is the authoritative datastore for documents, chunks, embeddings, and full-text search, accelerated by a compiled Rust native engine (`depth_engine`). Redis is used for transient query caching and rate-limiting.

Turso/libSQL serves as an optional downstream edge store for cached, augmented, and backup retrieval.

---

## Current Status & Capabilities

- **Unified Document Ingestion (`POST /api/ingest/file`):** Direct multipart ingestion powered by compiled Rust `anydoc` supporting PDF, DOCX, XLSX, PPTX, CSV, EPUB, and Markdown with offloaded threadpool parsing and 50 MB payload ceilings.
- **Compiled Rust Acceleration (`depth_engine`):** Sub-millisecond PyO3 native module providing SIMD-vectorized Reciprocal Rank Fusion (RRF), regex DFA query intent routing (<1 µs), CRAG confidence gating, and Lost-in-the-Middle U-shaped prompt ordering.
- **Mosaic Negative Query Algebra:** Automatically parses negation syntax (`NOT term`, `-term`, `without term`) and applies Mosaic soft penalties ($\lambda = 0.5$) via `fuse_rrf` to suppress contaminated candidates rather than boosting them.
- **OKF Cognitive Depth (Levels 1–5):**
  - **Depths 1–2:** Sub-200ms concept summaries from `knowledge_concepts` with ~95% token savings.
  - **Depths 3–4:** Dense-first semantic search with hybrid RRF fallback and intent-based 1-hop graph expansion.
  - **Depth 5:** Forced 2-hop concept graph traversal, cross-encoder neural reranking (`bge-reranker-base`), and context reordering.
- **Concurrent Query Execution:** Asynchronously evaluates Redis query cache, computes dense query embeddings, and acquires database connections in parallel via `asyncio.gather`.
- **Turbopuffer Rank-by-Attribute:** Migration 005 blends Robertson sigmoid recency decay and chunk quality scores into the first-stage lexical scorer before RRF fusion.
- **Explainability & Diagnostics:** Optional `explain: true` flag on `/api/query` exposes scoring modes, chunk provenance, graph hops, confidence assessments, and negative algebra terms.
- **Test Suite Status:** 150 automated tests passing in ~3.5s across unit, integration, and quality gates, plus 22 compiled Rust unittests in 0.05s.

---

## Quick Start

### 1. Start Infrastructure (PostgreSQL + pgvector & Redis)

Start PostgreSQL with pgvector (pg17) and Redis with all schema migrations (001–006) automatically applied:

```bash
docker compose up -d
```

Validate container readiness and embedding coverage:

```bash
scripts/validate_rag_local.sh
```

### 2. Install & Start the API

Install dependencies (with compiled Rust extension):

```bash
pip install -e ".[dev]"
```

Launch the FastAPI application:

```bash
python -m uvicorn api.main:app --reload --port 8000
```

The database schema is initialized from `db/migrations/` (001 through 006), with development API credentials seeded from `db/seed/001_dev_api_key.sql`.

---

## API Endpoints

All endpoints except `/api/health` require `Authorization: Bearer <api-key>`. A default development key is available: `sk-depth-dev-local-0000000000000000`.

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | `GET` | Service liveness probe |
| `/api/ingest` | `POST` | Store raw markdown or text document and queue for embedding |
| `/api/ingest/file` | `POST` | Multipart file upload (PDF, DOCX, XLSX, PPTX, CSV, EPUB, Markdown via anydoc) |
| `/api/query` | `POST` | Hybrid retrieval with cognitive depth (1–5), negative algebra, and explainability |
| `/api/query/stream` | `POST` | Buffered Server-Sent Events (SSE) streaming completion |

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

#### Query with Negative Query Algebra & Explainability
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

## Benchmarking & Evaluation

DepthAPI provides built-in benchmarking harnesses covering retrieval latency, BEIR accuracy, and Rust native acceleration.

### 1. Compiled Rust Engine Speedup Benchmark
Measures the latency reduction of native Rust `fuse_rrf`, `detect_graph_hops`, and `reorder_lost_in_the_middle` against pure-Python implementations:

```bash
python scripts/benchmark_rust_retrieval_speedup.py
```

### 2. BEIR Retrieval Benchmark
Evaluates standard information retrieval metrics (nDCG@10, Recall@10, MAP) across dense, lexical, and hybrid fusion modes:

```bash
python scripts/benchmark_beir.py --dataset scifact
```

### 3. Depth Engine Parsing & Chunking Throughput
Benchmarks document format parsing and chunking throughput across synthetic and realistic test corpora:

```bash
python scripts/benchmark_depth_engine.py
```

### 4. End-to-End RAG Benchmark
Runs the full evaluation pipeline across question sets with citation enforcement and confidence checks:

```bash
python evaluation/benchmark.py
```

---

## Deployment Architecture

```text
                           ┌────────────────────────┐
                           │      Client / Agent    │
                           └───────────┬────────────┘
                                       │ Bearer Auth
                                       ▼
                           ┌────────────────────────┐
                           │    FastAPI Gateway     │
                           │   (api.main:app)       │
                           └─────┬────────────┬─────┘
                Async Cache /     │            │  Compiled PyO3
             Rate Limit (Redis)   │            │  (depth_engine)
                                  ▼            ▼
                           ┌─────────────┐  ┌───────────────────────┐
                           │ PostgreSQL  │  │  Rust Native Engine   │
                           │  + pgvector │  │  - anydoc parser     │
                           │  (pg17)     │  │  - SIMD fuse_rrf     │
                           └─────┬───────┘  │  - Intent router      │
                                 │          │  - Lost-in-middle     │
                           Sync  │          └───────────────────────┘
                                 ▼
                           ┌─────────────┐
                           │ Turso Edge  │ (Optional Downstream Replica)
                           └─────────────┘
```

### Production Deployment Notes

1. **Docker Container Builds:** Multi-stage Dockerfile builds the compiled Rust library and packages the minimal Python runtime:
   ```bash
   docker build -f api/Dockerfile -t depthapi:latest .
   ```
2. **Database Migrations:** All production environments must apply `db/migrations/` sequentially:
   - `001_schema.sql` (Base tables, pgvector cosine index, FTS tokens, hybrid_search_v5)
   - `002_concepts_graph.sql` (Knowledge concepts & graph lineage edges)
   - `003_fixes.sql` (Index improvements & constraints)
   - `004_dense_search.sql` (Direct dense search RPC dense_search_v5)
   - `005_rank_by_attribute.sql` (Turbopuffer rank-by-attribute hybrid_search_v6)
   - `006_halfvec_hnsw.sql` (pgvector halfvec float16 HNSW index for ≥ 0.7.0)
3. **Edge Replication to Turso (libSQL):**
   ```bash
   export DATABASE_URL=postgresql://...
   export TURSO_DATABASE_URL=libsql://...
   export TURSO_AUTH_TOKEN=...
   python scripts/turso/sync_platform.py --full
   ```

---

## Development Checks

Run the complete local validation suite:

```bash
# Python & Rust test suites
pytest
cargo test --manifest-path crates/depth_engine/Cargo.toml

# Linting & bytecode verification
ruff check api tests
python -m compileall -q api tests evaluation scripts
```
