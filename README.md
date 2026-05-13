# DepthAPI: The Cognitive Synthesis Engine

> **From Surface-Level Answers to First-Principles Wisdom.**

DepthAPI is a high-performance, **headless B2B infrastructure** designed to deliver information at the precise cognitive depth required for any professional context. It transforms raw data into structured intelligence, ranging from ELI5 analogies to deep technical audits.

---

## Key Features

### 1. The 5x3 Depth-Mode Matrix
Unlike generic LLM wrappers, DepthAPI uses a proprietary orchestration layer to deliver 15 distinct interaction "lenses":

| Depth | Answer Mode | Socratic Mode | Compare Mode |
| :--- | :--- | :--- | :--- |
| **Simple** | Plain-language analogies | Guided discovery | Surface-level trade-offs |
| **Accessible** | Real-world examples | Conceptual scaffolding | Structural differences |
| **Technical** | Mechanism + Rationale | Logic-based questioning | API/Schema comparison |
| **Expert** | First Principles + Math | Peer-level inquiry | Formal notation audits |
| **Meme** | Internet culture / Slang | Irony-based learning | Cultural "vibe" check |

### 2. Dynamic Model Escalation (The "Moat")
DepthAPI doesn't just call one model; it intelligently routes queries to the most cost-effective "brain" for the job:
*   **Speed Tier:** Low-complexity queries route to **Groq (Llama 3.1 8B)** for sub-second responses.
*   **Reasoning Tier:** Complex technical or expert queries escalate to **Gemini 1.5 Pro**, leveraging its 2M-token context window for massive document synthesis.
*   **Failover Logic:** Production-grade circuit breakers automatically reroute traffic between providers (Groq, Gemini, OpenAI, Cerebras) if a provider stumbles.

### 3. Production-Ready RAG (Hybrid Intelligence)
Optimized for massive corpuses (tested up to 250k+ chunks), our RAG infrastructure includes:
*   **Hybrid Search (v4):** Combines Vector similarity with BM25 keyword matching for surgical precision.
*   **Context Expansion:** Automatically fetches neighboring chunks (pre- and post-) to ensure the LLM understands the full paragraph, not just a fragment.
*   **Source Attribution:** Built-in metadata tracking for citations, page numbers, and source URLs.

### 4. "Stealth Industrial" Design Philosophy
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

### Installation
```bash
git clone https://github.com/your-repo/depthapi
python -m venv .venv
source .venv/bin/activate
pip install -r api/requirements.txt
```

### Usage
```bash
curl -X POST "https://api.depthapi.com/api/query" \
  -H "Authorization: Bearer sk-depth-..." \
     -H "Content-Type: application/json" \
     -d '{
       "topic": "Quantum Decoherence",
    "levels": ["expert"],
    "mode": "technical",
    "use_trusted_corpus": true
     }'

### Ingest Documents (RAG)
```bash
curl -X POST "https://api.depthapi.com/api/ingest" \
  -H "Authorization: Bearer sk-depth-..." \
  -H "Content-Type: application/json" \
  -d '{
    "collection_name": "company-docs",
    "filename": "handbook.txt",
    "raw_text": "..."
  }'
```

### Collections
```bash
curl -X GET "https://api.depthapi.com/api/collections" \
  -H "Authorization: Bearer sk-depth-..."
```
```

---

## Vertical-Agnostic, Expert-Specific
While the engine is vertical-agnostic, it is uniquely powerful for:
*   **Dev-Verticals:** Ingesting 100k+ pages of technical documentation.
*   **Legal/Compliance:** Cross-referencing complex regulatory books.
*   **Higher Education:** Transforming textbooks into personalized tutors.

## License
Apache License 2.0
