# DepthAPI — RAG Architecture, Storage Strategy & Execution Plan

---

## Part 1: High-Quality RAG Design

Naive RAG (chunk → embed → cosine search → inject) produces mediocre results. It will hallucinate on edge cases, retrieve irrelevant chunks, and fail silently on multi-hop questions. This design avoids those failure modes systematically.

---

### 1.1 The Three Quality Killers in RAG (and how we fix them)

| Problem | Symptom | Fix |
|---|---|---|
| **Bad chunks** | Truncated concepts, half-sentences | Document-type-aware chunking |
| **Naive retrieval** | Semantically close but factually wrong results | Hybrid search + reranking |
| **Context misuse** | LLM ignores retrieved context or hallucinates despite it | Structured prompt injection + citation enforcement |

---

### 1.2 Chunking Strategy (Document-Type Aware)

One chunk size does NOT fit all. Different document types have different semantic structures.

#### For Trusted Corpora (WikiDump, arXiv, HF datasets)

```
WikiDump → Hierarchical: Section-level (parent) + Paragraph-level (child)
                          - Parent: ~1500 tokens (section context)
                          - Child:  ~400 tokens (specific facts)
                          - Overlap: 100 tokens on child chunks

arXiv → Abstract-only for v1. Single chunk per abstract (~300 tokens).
        Full paper parsing is a v2 problem.

HF Datasets → Dataset-specific. Text classification datasets: per-example.
              Long-form QA: paragraph-level. Evaluate case by case.
```

**Why Hierarchical for Wikipedia?**
Wikipedia articles have a `Section → Subsection → Paragraph` structure. Retrieving just the paragraph loses context (you don't know *which article* or *which section* it came from). The hierarchical approach stores both — the child chunk is retrieved, but the parent chunk's text is injected into the prompt for context. This is the "Parent-Child" retrieval pattern and it dramatically reduces hallucination on Wikipedia-sourced answers.

#### For Customer Documents (BYOD — Tier 2)

Customer documents are unpredictable. The chunking strategy must detect and adapt:

```python
# Pseudocode — document type routing in ingest pipeline
def detect_document_type(content: str, filename: str) -> str:
    if filename.endswith(('.py', '.ts', '.js', '.go', '.rs')):
        return 'code'
    if filename.endswith('.md'):
        return 'markdown'
    if has_table_structure(content):
        return 'structured'
    return 'prose'

CHUNKING_CONFIG = {
    'code': {
        'strategy': 'ast_aware',       # split on function/class boundaries
        'chunk_size': 300,             # code is dense; smaller chunks
        'overlap': 50,
    },
    'markdown': {
        'strategy': 'header_aware',    # split on ## and ### boundaries
        'chunk_size': 500,
        'overlap': 100,
    },
    'structured': {                    # tables, CSVs
        'strategy': 'row_batch',       # batch rows into chunks
        'chunk_size': 400,
        'overlap': 0,                  # no overlap for tabular data
    },
    'prose': {
        'strategy': 'recursive_character',
        'chunk_size': 512,
        'overlap': 100,
    },
}
```

> [!IMPORTANT]
> **Codebases are a special case.** A company's codebase is NOT prose. Embedding raw source code with a language-agnostic embedding model produces poor retrieval. For v1, we support code as a first-class document type with ast-aware chunking. The embedding model (Gemini text-embedding-004) handles code reasonably well — but the chunking must respect function/class boundaries, not character counts.

---

### 1.3 Embedding Strategy

**Primary: Gemini `text-embedding-004`**
- 768 dimensions
- Free tier: 1,500 requests/minute, batches of 100
- Handles multilingual + code adequately
- Zero cost aligns with $0 budget constraint

**Batching logic to stay within free tier during ingestion:**
```python
# Respect Gemini free tier: 1500 RPM = 25 RPS
# Conservative: 10 RPS with exponential backoff
BATCH_SIZE = 100       # chunks per API call
REQUESTS_PER_SECOND = 8  # safe rate
BACKOFF_BASE = 2.0

async def embed_in_batches(chunks: list[str]) -> list[list[float]]:
    embeddings = []
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i:i + BATCH_SIZE]
        result = await gemini_embed(batch)
        embeddings.extend(result)
        await asyncio.sleep(1.0 / REQUESTS_PER_SECOND)
    return embeddings
```

**For the Wikipedia ingestion specifically (30M chunks at 100/batch = 300K API calls):**
At 8 batches/second → ~10 hours of continuous ingestion for WikiDump.
Start with arXiv abstracts (~6K API calls → ~12 minutes). Validate quality first.

---

### 1.4 Retrieval Strategy — Hybrid Search

**Naive cosine similarity alone is not enough.** A query like "what does transformer mean?" will retrieve chunks about electrical transformers *and* neural network transformers with similar cosine scores. Hybrid search fixes this.

#### Dense Retrieval (Semantic)
Standard pgvector cosine similarity. Already planned.
```sql
SELECT id, content, metadata,
       1 - (embedding <=> query_embedding) AS score
FROM knowledge_chunks
WHERE collection_id = $1
ORDER BY embedding <=> query_embedding
LIMIT 20;  -- retrieve more than top_k for reranking pass
```

#### Sparse Retrieval (Keyword/BM25)
Add a `tsvector` column to `knowledge_chunks` for PostgreSQL full-text search:
```sql
ALTER TABLE knowledge_chunks ADD COLUMN fts tsvector
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

CREATE INDEX ON knowledge_chunks USING GIN (fts);
```

```sql
-- BM25-style retrieval
SELECT id, content,
       ts_rank(fts, plainto_tsquery('english', $query)) AS bm25_score
FROM knowledge_chunks
WHERE fts @@ plainto_tsquery('english', $query)
LIMIT 20;
```

#### Fusion (Reciprocal Rank Fusion)
Merge dense and sparse results without needing scores to be on the same scale:
```python
def reciprocal_rank_fusion(
    dense_results: list[dict],
    sparse_results: list[dict],
    k: int = 60,  # standard RRF constant
) -> list[dict]:
    scores: dict[str, float] = {}
    for rank, result in enumerate(dense_results):
        scores[result['id']] = scores.get(result['id'], 0) + 1 / (k + rank + 1)
    for rank, result in enumerate(sparse_results):
        scores[result['id']] = scores.get(result['id'], 0) + 1 / (k + rank + 1)
    
    all_chunks = {r['id']: r for r in dense_results + sparse_results}
    return sorted(all_chunks.values(), key=lambda x: scores[x['id']], reverse=True)
```

This is cheap (pure Python, no extra API call) and reliably outperforms either method alone.

---

### 1.5 Query Expansion — HyDE (Hypothetical Document Embeddings)

**The problem:** User queries are short and noisy. "transformer attention" doesn't embed similarly to a 500-token Wikipedia paragraph describing attention, even though they mean the same thing.

**HyDE fixes this:** Generate a *hypothetical* answer to the query, embed *that* instead of the raw query, and use the result for vector search. The hypothesis doesn't need to be factually correct — it just needs to be stylistically similar to the target chunks.

```python
async def hyde_embed(query: str, depth: str) -> list[float]:
    # Generate a short hypothetical document (50-100 tokens max)
    hypothesis = await llm_call(
        prompt=f"Write a 2-sentence factual statement that would answer: {query}",
        model="learn-groq-llama8b",  # use fast model, not accurate
        max_tokens=80,
    )
    # Embed the hypothesis, not the raw query
    return await gemini_embed(hypothesis)
```

**Cost:** One extra fast LLM call per query (Groq Llama8b, essentially free). The quality improvement on technical queries is significant.

**When to use HyDE:** Only when `depth` is `technical` or `expert`. For `simple` and `meme`, raw query embedding is sufficient.

---

### 1.6 Reranking (Post-Retrieval Quality Gate)

After hybrid search returns 20 candidates, we need the actual top 3-5. A cross-encoder reranker reads both the query AND each chunk together (unlike bi-encoders which embed separately) — giving much higher precision.

**Options:**
| Reranker | Cost | Quality | Latency |
|---|---|---|---|
| **Cohere Rerank API** | $1/1K requests | ⭐⭐⭐⭐⭐ | +100ms |
| **`cross-encoder/ms-marco-MiniLM-L-6-v2`** (self-hosted) | $0 (CPU) | ⭐⭐⭐⭐ | +200-400ms |
| **Simple score threshold** (no reranker) | $0 | ⭐⭐ | 0ms |

**Decision for v1 (purely $0 constraint):** Score threshold at 0.75 cosine similarity, plus RRF fusion. Skip the cross-encoder for now. Add Cohere Rerank as an option in v2 for Pro tier queries.

---

### 1.7 Context Assembly — The "Lost in the Middle" Problem

Research shows LLMs perform best on content at the *beginning and end* of the context window, and worst on content in the *middle*. If you have 5 retrieved chunks, don't inject them sequentially — put the most relevant first and last.

```python
def assemble_context(chunks: list[dict], budget_tokens: int = 2000) -> str:
    # Sort by relevance score
    sorted_chunks = sorted(chunks, key=lambda x: x['score'], reverse=True)
    
    # Place most relevant at start, second most relevant at end
    # This is the "lost in the middle" mitigation
    if len(sorted_chunks) >= 2:
        ordered = [sorted_chunks[0]] + sorted_chunks[2:] + [sorted_chunks[1]]
    else:
        ordered = sorted_chunks
    
    context_parts = []
    token_count = 0
    for chunk in ordered:
        chunk_tokens = estimate_tokens(chunk['content'])
        if token_count + chunk_tokens > budget_tokens:
            break
        context_parts.append(
            f"[Source: {chunk['metadata'].get('title', 'Unknown')}]\n{chunk['content']}"
        )
        token_count += chunk_tokens
    
    return "\n\n---\n\n".join(context_parts)
```

**Token budget allocation per depth level:**
```python
CONTEXT_BUDGET = {
    "simple":     {"rag": 800,  "web": 400},  # short context, simple LLM
    "accessible": {"rag": 1200, "web": 600},
    "technical":  {"rag": 1800, "web": 800},
    "expert":     {"rag": 2500, "web": 500},  # max RAG, minimal web
    "meme":       {"rag": 400,  "web": 200},  # minimal context, tone-driven
}
```

---

### 1.8 Anti-Hallucination Prompt Structure

The system prompt must instruct the model to behave correctly when context is provided:

```
SYSTEM:
You are a knowledge synthesis engine. You will be given:
1. RETRIEVED CONTEXT — verified knowledge from trusted sources
2. USER QUERY — the question to answer
3. DEPTH LEVEL — how to calibrate the response complexity

Rules:
- If RETRIEVED CONTEXT is provided, base your answer primarily on it.
- Do NOT invent facts not present in the context.
- If the context doesn't answer the question, say so explicitly before adding your own knowledge.
- Always include a SOURCES section at the end listing which sources you used.
- Do not make up source titles or URLs.

DEPTH: {depth}
RETRIEVED CONTEXT:
{context}

USER QUERY: {query}
```

---

### 1.9 RAG Quality Evaluation (RAGAS Framework)

Before shipping, you need to know if your RAG is actually good. The RAGAS framework provides four metrics:

| Metric | Measures | How |
|---|---|---|
| **Faithfulness** | Does the answer stick to retrieved context? | LLM-as-judge |
| **Answer Relevancy** | Does the answer address the question? | Embedding similarity |
| **Context Precision** | Are retrieved chunks actually useful? | LLM-as-judge |
| **Context Recall** | Were the right chunks retrieved? | Ground truth comparison |

```bash
pip install ragas
```

Build a small **golden dataset** of 50-100 Q&A pairs from your trusted corpora with known answers. Run RAGAS against it before launch. Target: Faithfulness > 0.85, Context Precision > 0.75.

---

## Part 2: Database Storage for Enterprise Customers

### 2.1 The Reality of Large-Scale Customer Documents

A company with a large codebase or document library isn't a "50 documents" problem. Consider:
- A mid-size SaaS company: ~10,000 source files → ~50GB of code
- A legal firm: ~100,000 contracts → ~30GB of PDFs
- A healthcare provider: ~500,000 clinical notes → ~200GB

**You cannot host this on a home server + Supabase free tier.** You need a tiered storage strategy.

---

### 2.2 The Three-Tier Storage Model

```
┌─────────────────────────────────────────────────────┐
│ TIER 0: Trusted Corpora (your data, self-hosted)    │
│ WikiDump + arXiv + HF datasets                       │
│ ~350GB on local machine, shared across all users     │
│ No customer data. Your revenue asset.                │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ TIER 1: Managed Small (your infra, Supabase cloud)  │
│ Free plan: <50MB, Starter: <500MB                    │
│ You manage it. Customers don't see it.               │
│ Revenue model: per-document or per-query pricing     │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ TIER 2: Bring Your Own DB (BYODB) — Enterprise       │
│ Customer provides: connection string to their DB     │
│ (Postgres/pgvector, Qdrant, Weaviate, Pinecone)      │
│ DepthAPI reads/writes embeddings to THEIR storage    │
│ DepthAPI is purely the inference + retrieval layer   │
└─────────────────────────────────────────────────────┘
```

### 2.3 BYODB — The Enterprise Onboarding Flow

For large customers, DepthAPI becomes **stateless on storage**. The customer owns their vector DB. DepthAPI is the smart query engine on top of it.

```bash
# Enterprise customer setup (one-time)
curl -X POST https://api.depthapi.dev/v1/connections \
  -H "Authorization: Bearer sk-depth-enterprise-xxxx" \
  -d '{
    "type": "pgvector",
    "connection_string": "postgresql://user:pass@their-rds.amazonaws.com:5432/docs",
    "collection_table": "company_knowledge_chunks"
  }'

# Response: {"connection_id": "conn_abc123", "status": "verified"}

# Now they ingest (DepthAPI writes to THEIR DB)
curl -X POST https://api.depthapi.dev/v1/ingest \
  -H "Authorization: Bearer sk-depth-enterprise-xxxx" \
  -d '{"connection_id": "conn_abc123", "source": "s3://their-bucket/docs/"}'

# Query uses their DB transparently
curl -X POST https://api.depthapi.dev/v1/query \
  -H "Authorization: Bearer sk-depth-enterprise-xxxx" \
  -d '{"q": "...", "depth": "technical", "connection_id": "conn_abc123"}'
```

**Supported BYODB backends (in priority order for v1 → v2):**
| Backend | v1 | v2 | Notes |
|---|---|---|---|
| PostgreSQL + pgvector | ✅ | ✅ | Native. Same schema as Tier 1. |
| Qdrant | ❌ | ✅ | Popular, open-source, easy to self-host |
| Pinecone | ❌ | ✅ | Most common enterprise vector DB |
| Weaviate | ❌ | ✅ | GraphQL-native, strong enterprise adoption |

**Why this model is brilliant for a $0 budget product:**
- DepthAPI never stores a single byte of enterprise data.
- DepthAPI has zero infrastructure cost for large customers.
- DepthAPI charges for **compute** (embeddings calls, LLM inference) not storage.
- This is *exactly* how managed AI services (Azure OpenAI, Vertex AI) work at scale.

### 2.4 Pricing Realignment for BYODB

The monetization model must reflect this:

| Plan | Storage | Model | Price |
|---|---|---|---|
| **Free** | DepthAPI-managed (50MB) | Per-query (100K tokens/mo) | $0 |
| **Starter** | DepthAPI-managed (500MB) | Per-query (2M tokens/mo) | $29/mo |
| **Pro** | DepthAPI-managed (5GB, local Tier 2) | Per-query (10M tokens/mo) | $99/mo |
| **Enterprise** | BYODB (their infra, unlimited) | Per-query (custom) + connection fee | $500+/mo |

The **connection fee** for BYODB ($100-200/mo) covers the engineering cost of maintaining the adapter layer. The per-query fee covers LLM inference. The customer's AWS/GCP bill covers storage. Everyone wins.

---

## Part 3: Execution Timeline

### 3.1 Honest Time Estimates (Solo Developer, Full-Time Focus)

| Phase | Work | Honest Days |
|---|---|---|
| **Phase 0** | Local infra setup + Cloudflare Tunnel + CI/CD (Watchtower) + arXiv ingestion script | 4-5 days |
| **Phase 1** | Auth rebuild (API keys), re-scope rate limiter, update all routes | 3-4 days |
| **Phase 2** | RAG foundation: chunking + embedding + hybrid search + two-tier query | 6-8 days |
| **Phase 3** | API hardening (v1 schema) + Python SDK + Playground HTML + OpenAPI docs | 4-5 days |
| **Phase 4** | Monetization wiring + usage endpoint + DodoPayments plan tiers | 2-3 days |

**Total: 19-25 working days**

At full-time (8h/day): **~4 weeks to a shippable v1**  
At part-time (3-4h/day): **~8-10 weeks**

> [!WARNING]
> The arXiv + Wikipedia ingestion is NOT part of the development timeline. It runs in the background on the local machine, independently. The ingestion script takes 1-2 days to write, but the actual job runs for 10-18 hours unattended. Plan this as a background overnight job.

### 3.2 The Critical Path

These are the tasks that block everything else. Cannot parallelise:
```
Phase 0 infra up → Phase 1 auth rebuild → Phase 2 RAG core → Phase 3 API surface
```

These CAN be done in parallel with the above:
- Python SDK (can be written against a mock backend)
- Playground HTML (static, can be built against the existing API)
- Wikipedia ingestion (runs on hardware, not in code editor)
- RAGAS golden dataset construction (data work, not coding)

---

## Part 4: The Promotional Demo Web App

### 4.1 What It Is (and Is Not)

This is **NOT** a rebuilt version of the KnowBear web app. It is a **single stateless HTML page** that exists purely to demonstrate the core value proposition to potential B2B customers.

It requires no login, no persistence, no database. It calls DepthAPI with a **rate-limited demo API key** (max 20 requests/day per IP) and shows the output.

### 4.2 Core Demo Features (What Goes In)

| Feature | Why Include | Implementation |
|---|---|---|
| **Depth Switcher** | The #1 differentiator. Users see the same question answered 5 ways instantly. | Tabs: Simple / Accessible / Technical / Expert / Meme |
| **Live Query Input** | Let users type their own questions | Text input → POST /v1/query |
| **Real-time Streaming** | Shows the API is fast. Impresses demo viewers. | SSE stream, token-by-token display |
| **Source Citations** | Proves it's not hallucinating | Collapsible "Sources" section |
| **"Try the API" CTA** | Converts visitors to signups | Links to docs + key signup |
| **Code snippet toggle** | Shows the developer the actual curl/Python call behind the demo | Syntax-highlighted code drawer |

### 4.3 What Stays OUT of the Demo App

| Feature | Why Exclude |
|---|---|
| Document upload / ingestion | Too complex for a demo. Trusted corpora is enough. |
| Conversation history | Stateless is simpler. No Supabase needed. |
| User accounts | Zero auth complexity. Rate limit by IP only. |
| Mode switcher (Socratic/Compare) | Depth switcher is the hero. Don't dilute. |
| Mermaid diagram rendering | Nice to have, adds complexity. v2. |

### 4.4 Technical Spec

```
Stack:       Vanilla HTML + vanilla JS (no framework, no bundler)
Hosting:     Served as static file from FastAPI at GET /demo
Size target: <50KB total (HTML + inline CSS + inline JS)
Auth:        Demo API key (rate-limited env var: DEMO_API_KEY)
Rate limit:  20 requests / IP / day (enforce in existing rate_limit.py)
Analytics:   Plausible.io script tag (free, privacy-friendly)
```

**The page layout:**
```
┌─────────────────────────────────────────┐
│  DepthAPI  [Live Demo]   [Get API Key →] │
├─────────────────────────────────────────┤
│                                          │
│  Ask anything about science, tech,       │
│  history, or code:                       │
│                                          │
│  [_________________________________] [→] │
│                                          │
│  Depth: [Simple][Accessible][Technical]  │
│         [Expert][Meme]                   │
│                                          │
├─────────────────────────────────────────┤
│  Answer streams here...                  │
│                                          │
│  ▼ Sources (3)                           │
│    • Wikipedia: Transformer model        │
│    • arXiv: Attention Is All You Need    │
├─────────────────────────────────────────┤
│  The API call behind this:               │
│  [Python] [curl] [JavaScript]            │
│  ┌──────────────────────────────┐        │
│  │ import depthapi               │        │
│  │ client = depthapi.Client(key) │        │
│  │ resp = client.query(...)      │        │
│  └──────────────────────────────┘        │
│                                          │
│  [→ Read the Docs] [→ Get an API Key]    │
└─────────────────────────────────────────┘
```

### 4.5 When to Build the Demo App

**Phase 3, after the Python SDK.** In that order:
1. The underlying `/v1/query` API must be clean and stable.
2. The demo app simply calls that API.
3. The code snippet toggle literally copies from the Python SDK README.

The demo app is 2-3 days of work, done in parallel with the Python SDK in Phase 3.

---

## Summary of All Decisions

| Question | Answer |
|---|---|
| RAG chunking | Hierarchical for Wikipedia, doc-type-aware for customer files, AST-aware for code |
| Retrieval | Hybrid: dense (pgvector) + sparse (PostgreSQL FTS) fused via RRF |
| Query expansion | HyDE on `technical` + `expert` depth, raw embedding for others |
| Reranking | Score threshold + RRF for v1. Cohere Rerank for v2 Pro tier. |
| Context assembly | Lost-in-the-middle mitigation: best chunk first, second-best chunk last |
| Anti-hallucination | Structured system prompt with explicit source citation requirement |
| Quality gates | RAGAS golden dataset: 50-100 Q&A pairs, target Faithfulness >0.85 |
| Large customer storage | BYODB model: customer provides pgvector/Qdrant/Pinecone connection string |
| Enterprise pricing | Connection fee (~$100-200/mo) + per-query billing. DepthAPI = compute layer only. |
| Execution timeline | 4 weeks full-time, 8-10 weeks part-time |
| Promotional demo app | Single static HTML page, depth switcher, streaming, source citations, code snippets |
