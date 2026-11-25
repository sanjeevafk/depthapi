# DepthAPI

DepthAPI is a local-first retrieval-augmented generation (RAG) engine built on **FastAPI**, **PostgreSQL (`pgvector` + full-text search + concept graph)**, and **Redis**. Instead of running the same fixed `embed -> top-k -> prompt` pipeline on every request, it routes each query through an adaptive retrieval pipeline controlled by a **cognitive depth dial (`1–5`)**, intent-gated graph traversal, and confidence gating — all backed by a single PostgreSQL database.

---

## Why DepthAPI

- **One datastore, no separate vector or graph DB:** `halfvec` HNSW vector search, BM25/FTS lexical search, concept-graph edges, ingestion queues, and API key auth all live in PostgreSQL (`db/migrations/`).
- **Dense-first with weighted RRF fallback:** Strong semantic matches (`dense_search_v5`) are served directly without paying lexical/rerank overhead; weaker or ambiguous queries fall back to weighted Reciprocal Rank Fusion (`1.5` dense / `0.5` lexical via `hybrid_search*_v7`).
- **Intent-gated graph traversal:** Concept-graph hops are off by default to prevent topic drift on factual lookups, and engage automatically when relational, dependency, or lineage intent is detected (`api/services/rag/graph/router.py`).
- **Similarity-gated reranking:** At depths 3–4, `BAAI/bge-reranker-base` is skipped when dense cosine similarity is already at or above `rerank_skip_similarity` (`0.8`), avoiding unnecessary latency where vector search is already confident.
- **CRAG confidence gating & Lost-in-the-Middle ordering:** Refuses to synthesize when retrieval confidence is `insufficient`, and orders selected chunks into a U-shape (`[0, 2, 4, 3, 1]`) so the highest-scoring evidence sits at the start and end of the prompt.

---

## Query Pipeline

```mermaid
flowchart TD
    Q["POST /api/query (depth 1-5)"] --> N["Parse Negations (NOT, -term, without)"]
    N --> C{"Redis Cache Hit?"}
    C -- Yes --> OUT["Return Cached Response"]
    C -- No --> D{"Depth 1-2?"}
    D -- Yes --> K["Lookup knowledge_concepts (2-4 concepts)"]
    K -- Found --> CRAG["CRAG Confidence Gate"]
    K -- Empty --> DF
    D -- No --> DF{"Depth 3-5: Dense Search (graph_hops == 0)"}
    DF -- "Strong dense hit" --> NEG["Apply Negative Term Penalty"]
    DF -- "Weak / Graph Intent" --> HYB["Weighted RRF v7 (+ 1-hop/2-hop Graph)"]
    HYB --> NEG
    NEG --> RR{"Depth 5 OR (rerank & score < 0.8)?"}
    RR -- Yes --> CE["CrossEncoder Rerank (BAAI/bge-reranker-base)"]
    RR -- No --> CRAG
    CE --> CRAG
    CRAG -- "insufficient" --> REFUSE["Return Insufficient-Context Refusal"]
    CRAG -- "low / medium / high" --> LITM["U-Shaped Reordering + LLM Synthesis [N]"]
```

---

## Cognitive Depth Levels (`depth`)

| Depth | Retrieval Behavior | Reranking | Graph Traversal |
|---|---|---|---|
| **1–2** | Direct lookup on `knowledge_concepts` (2 or 4 concepts), falling back to hybrid search if empty | Skipped | Off |
| **3–4** | Dense-first (`dense_search_v5`), falling back to weighted RRF (`v7`) when dense hits are weak (`< 5` results or `< 0.5` similarity) | Runs only if top dense similarity `< 0.8` | Auto-detected from relational/lineage intent (or manual `graph_hops`) |
| **5** | Dense-first with weighted RRF (`v7`) fallback | Forced (`top_n=7`) | Auto-detected or manual `graph_hops` |

---

## Retrieval Benchmarks

Measured on Google Colab T4 GPU with `google/embeddinggemma-2` (`--max-seq-length 512 --fp16`), weighted RRF (`1.5` dense / `0.5` lexical), and `BAAI/bge-reranker-base` (top-25). Full tables and notes are in [docs/benchmarks/](docs/benchmarks/).

### SciFact (`5,183` docs, `300` queries)

| Strategy | 256d NDCG@10 | 768d NDCG@10 | 768d Recall@10 | 768d MRR@10 |
|---|---:|---:|---:|---:|
| `dense` | 0.8009 | **0.8166** | 0.9021 | 0.7959 |
| `lexical` | 0.5694 | 0.5694 | 0.6803 | 0.5381 |
| `hybrid_rrf` | 0.7459 | 0.7575 | **0.9206** | 0.7108 |
| `hybrid_rrf_rerank` | 0.7480 | 0.7601 | 0.8936 | 0.7229 |

### Multi-Hop HotpotQA (`5,000` docs, `200` queries, 256d)

| Strategy | NDCG@10 | Recall@10 | MRR@10 | p50 (ms) | p95 (ms) |
|---|---:|---:|---:|---:|---:|
| `dense` | 0.9469 | 0.9625 | 0.9772 | 66.45 | 104.26 |
| `lexical` | 0.8694 | 0.8850 | 0.9696 | 0.43 | 0.64 |
| `hybrid_rrf` | 0.9428 | 0.9675 | 0.9840 | 0.08 | 0.10 |
| `hybrid_graph_1hop` | 0.8975 | **0.9725** | 0.9325 | 0.56 | 2.02 |
| `hybrid_rrf_rerank` | **0.9559** | **0.9725** | **0.9912** | 399.08 | 617.50 |

- See [beir_small_embeddinggemma2_256d_baseline.md](docs/benchmarks/beir_small_embeddinggemma2_256d_baseline.md) for the 5-dataset 256d baseline (`scifact`, `nfcorpus`, `arguana`, `scidocs`, `fiqa`).
- See [beir_ablation_and_rerankers.md](docs/benchmarks/beir_ablation_and_rerankers.md) for 256d vs. 768d ablations, HotpotQA graph-hop results, and reranker comparisons.

---

## Capabilities

- **Document Ingestion (`POST /api/ingest`, `POST /api/ingest/file`):** Raw text/Markdown ingestion plus multipart file uploads (PDF, DOCX, XLSX, PPTX, CSV, HTML, Markdown) converted via `markitdown`, with a 75 MB payload limit, content-hash idempotency, and prose keyphrase concept-graph extraction.
- **Negative Query Algebra:** Parses `NOT term`, `-term`, `without term`, `except`, and `sans`, downweighting matching candidates while preserving native score scales for confidence gating.
- **Explainability & Streaming:** `explain: true` returns retrieval mode, graph hops, rerank status, confidence band, and chunk provenance; `POST /api/query/stream` streams Server-Sent Events (SSE).
- **LLM & Embedding Flexibility:** Works with OpenAI-compatible APIs or local inference servers (Ollama, vLLM, llama.cpp), plus local `sentence-transformers` embeddings with deterministic hash fallback.

---

## Quick Start

### 1. Start Infrastructure (PostgreSQL + pgvector & Redis)

```bash
docker compose up -d
```

`docker-compose.yml` mounts migrations `001`–`007` and the dev API key seed (`db/seed/001_dev_api_key.sql`) automatically.

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
| `/api/query` | `POST` | Adaptive retrieval with cognitive depth (1–5), negative algebra, and explainability |
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

## Development Checks

```bash
pytest -q
python -m compileall -q api tests evaluation scripts
```
