# DepthAPI

DepthAPI is a headless B2B Retrieval-Augmented Generation (RAG) and inference infrastructure API. It provides a robust backend for AI applications, focusing on production-grade reliability, query routing across multiple LLM providers, and high-performance hybrid retrieval.

## Applications and Use Cases

*   **B2B RAG Integration:** Plug-and-play backend for applications requiring semantic search and document synthesis over large custom datasets.
*   **AI Infrastructure Orchestration:** Centralized query routing and rate limiting across multiple model providers (Groq, Gemini, OpenAI) to manage costs and ensure high availability.
*   **Technical Documentation Retrieval:** Optimized for parsing, chunking, and retrieving highly technical content with high accuracy using hybrid search.
*   **Intelligent Query Routing:** Automatically directing simple queries to fast, low-cost models and complex reasoning tasks to larger, more capable models based on intent classification.

## Architecture

```text
+--------------+       +-------------------+       +-----------------------+
|              |       |                   |       |                       |
| API Client   +------>+ FastAPI Router    +------>+ Intent Classifier     |
|              |       |                   |       |                       |
+--------------+       +---------+---------+       +-----------+-----------+
                                 |                             |
                                 v                             v
                       +---------+---------+       +-----------+-----------+
                       |                   |       | Model Routing         |
                       | Hybrid RAG Engine |       | (Alias Chains)        |
                       |                   |       |                       |
                       +---------+---------+       +-----------+-----------+
                                 |                             |
                                 v                             v
                       +---------+---------+       +-----------+-----------+
                       |                   |       | Circuit Breaker &     |
                       | FAISS + BM25      |       | Fallback Orchestrator |
                       | (Local / pgvector)|       | (Redis)               |
                       +-------------------+       +-----------------------+
```

## Key Features

### 1. Multi-provider Fallback and Circuit Breakers
Implements a stateful circuit breaker using a Redis Lua script to manage rate limits and provider failures. The fallback orchestrator automatically reroutes traffic to alternate models in an alias chain upon detecting retryable errors.

### 2. Hybrid Search RAG Pipeline
Retrieval utilizes a multi-stage pipeline combining vector similarity (FAISS with inner product for cosine similarity) and keyword matching (BM25). Results are fused using Reciprocal Rank Fusion (RRF) and Maximal Marginal Relevance (MMR), followed by an optional cross-encoder reranking step. 

### 3. Intent-Based Model Escalation
Queries are scored on complexity, latency priority, reasoning, and explanation requirements. Based on these features, queries are routed to the most appropriate model chain:
*   Low-complexity/latency-sensitive: Groq (Llama 3.1 8B)
*   High-complexity/reasoning tasks: Gemini 1.5 Pro or equivalent

### 4. Privacy-Preserving Observability
Logging is handled via Structlog with JSON rendering. User identifiers are anonymized using a SHA-256 salted hash, and sensitive data (API keys, prompts) is automatically redacted before logging.

### 5. Local-First Development Path
Supports a complete local development environment without requiring cloud dependencies for the core API path:
*   Authentication via environment variables
*   Local Redis via Docker Compose
*   Filesystem-backed RAG (FAISS/BM25)

## Tech Stack

| Layer | Technologies |
| :--- | :--- |
| **Core Engine** | FastAPI (Python 3.11+), Pydantic v2, Structlog |
| **Routing & Logic** | Custom Intent Classifier, Provider Fallback Orchestrator |
| **RAG Retrieval** | FAISS, rank-bm25, SentenceTransformers |
| **Persistence (Cloud)** | Supabase (PostgreSQL), pgvector |
| **Observability/State** | Redis (Circuit Breaking, Rate Limiting) |

## Quick Start

### Local Development Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python scripts/setup_local_dev.py
docker compose up -d redis
uvicorn main:app --reload
```

This local path uses:
* `AUTH_PROVIDER_MODE=env` with `DEV_API_KEYS` for authentication.
* Local Redis from `docker compose` for cache/rate-limit storage.
* Filesystem-backed RAG data under `data/rag/`.

### Example Query
```bash
curl -X POST "http://localhost:8000/api/query" \
  -H "Authorization: Bearer sk-depth-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "System Architecture",
    "prompt_spec": {
      "depth": "technical",
      "task": "explain",
      "reasoning": "direct"
    },
    "mode": "technical",
    "use_trusted_corpus": true
  }'
```

## Datasets
The project utilizes an open-sourced dataset available at [Curated Dev Vertical Dataset](https://huggingface.co/datasets/sanjeevafk/depthapi_technical_corpus) on Hugging Face, containing technical documentation and system design references optimized for RAG retrieval.

## License
Apache License 2.0
