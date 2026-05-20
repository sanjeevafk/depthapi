# DepthAPI: The Cognitive Synthesis Engine

> **From Surface-Level Answers to First-Principles Wisdom.**

DepthAPI is a high-performance, **headless B2B infrastructure** designed to deliver information at the precise cognitive depth required for any professional context. It transforms raw data into structured intelligence, ranging from ELI5 analogies to deep technical audits.

---

## Key Features

### 1. Canonical PromptSpec Request Model
DepthAPI composes prompts from an explicit `prompt_spec` object instead of hard-coding a fixed matrix of preset modes. The public API accepts these axes directly:

| Axis | Supported values |
| :--- | :--- |
| `depth` | `simple`, `accessible`, `technical`, `expert` |
| `task` | `explain`, `compare`, `brainstorm`, `analyze`, `summarize` |
| `reasoning` | `direct`, `socratic`, `debate`, `guided` |
| `style` | `normal`, `meme`, `concise`, `academic` |
| `capabilities` | `requires_search`, `requires_diagram`, `requires_context`, `requires_citations` |

This request model maps directly to the prompt engine and can also be inferred automatically by the intent classifier for streaming and fallback paths.

### 2. Dynamic Model Escalation
DepthAPI doesn't just call one model; it intelligently routes queries to the most cost-effective "brain" for the job:
*   **Speed Tier:** Low-complexity queries route to **Groq (Llama 3.1 8B)** for sub-second responses.
*   **Reasoning Tier:** Complex technical or expert queries escalate to **Gemini 1.5 Pro**, leveraging its 2M-token context window for massive document synthesis.
*   **Failover Logic:** Production-grade circuit breakers automatically reroute traffic between providers (Groq, Gemini, OpenAI, Cerebras) if a provider stumbles.

### 3. Production-Ready RAG
Optimized for massive corpuses (tested up to 250k+ chunks), our RAG infrastructure includes:
*   **Hybrid Search (v4):** Combines Vector similarity with BM25 keyword matching for surgical precision.
*   **Context Expansion:** Automatically fetches neighboring chunks (pre- and post-) to ensure the LLM understands the full paragraph, not just a fragment.
*   **Source Attribution:** Built-in metadata tracking for citations, page numbers, and source URLs.

### 4. Local-First Development Path
The repository now supports a local-first boot path for core API development:
*   **Env-backed API keys:** `AUTH_PROVIDER_MODE=env` with `DEV_API_KEYS` or `DEPTHAPI_API_KEYS`.
*   **Local Redis:** cache, idempotency, and rate limiting via `docker compose`.
*   **Filesystem RAG backend:** local ingestion and retrieval without requiring Supabase for the core path.
*   **Cloud path preserved:** Supabase-backed auth and ingestion still work when configured.

### 5. "Stealth Industrial" Design Philosophy
Engineered for developers, DepthAPI follows a high-density, no-fluff output style. Every response is structured to maximize information density while minimizing cognitive load.

---

## Tech Stack

| Layer | Technologies |
| :--- | :--- |
| **Core Engine** | FastAPI (Python 3.11+), Pydantic v2, Structlog |
| **Cognitive Layer** | OpenAI-compatible Routing, Custom Prompt Fragments |
| **Persistence** | Supabase (PostgreSQL), pgvector (Scale-ready RAG) |
| **Observability** | Upstash Redis (Caching, Rate Limiting, Idempotency) |
| **Auth** | SHA-256 Hashed API Keys, Plan-scoped Metadata |

---

## Quick Start

### Local-First Development
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
* No required Supabase or Upstash account just to boot the API.

`/api/query`, `/api/export`, `/api/ingest`, and collection management work in this mode once at least one LLM provider key is configured.

### Usage
```bash
curl -X POST "http://localhost:8000/api/query" \
  -H "Authorization: Bearer sk-depth-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Quantum Decoherence",
    "prompt_spec": {
      "depth": "technical",
      "task": "explain",
      "reasoning": "direct",
      "style": "normal",
      "capabilities": ["requires_citations"]
    },
    "mode": "technical",
    "use_trusted_corpus": true
  }'
```

### Ingest Documents (RAG)
```bash
curl -X POST "http://localhost:8000/api/ingest" \
  -H "Authorization: Bearer sk-depth-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "collection_name": "company-docs",
    "filename": "handbook.txt",
    "raw_text": "..."
  }'
```

### Collections
```bash
curl -X GET "http://localhost:8000/api/collections" \
  -H "Authorization: Bearer sk-depth-local-dev"
```

## Vertical-Agnostic, Expert-Specific
While the engine is vertical-agnostic, it is uniquely powerful for:
*   **Dev-Verticals:** Ingesting 100k+ pages of technical documentation.
*   **Legal/Compliance:** Cross-referencing complex regulatory books.
*   **Higher Education:** Transforming textbooks into personalized tutors.

## License
Apache License 2.0
