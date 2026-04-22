# DepthAPI — Product Requirements Document & Design Doc
> **Last Updated:** Clarifications on RAG infrastructure, local hosting, storage model, and depth levels added in Section 12.
**Working Title:** DepthAPI (previously KnowBear)  
**Status:** Draft v1.0 | For Approval  
**Author:** Antigravity (AI Pair Programmer)

---

## 0. The Honest Reality Check

Before anything else, here is a brutally honest framing of where we stand.

### What you have built (the asset)
- A **production-grade, multi-provider LLM routing engine** with Sentry telemetry, Redis-backed rate limiting at the token level, a circuit breaker written in Lua, and provider health tracking. This is genuinely non-trivial infrastructure.
- A **web-search augmented inference pipeline** (Tavily/Serper/Exa) with caching. Competitors charge $99/month for this with less fallback logic.
- A **Depth-Aware prompt system** (ELI5 → Technical) with Socratic mode. This is a *real* feature no raw LLM call gives you.
- A Supabase persistence layer with migrations, RLS, and RPCs already set up. The plumbing exists.

### What you have NOT built (the gap to PMF)
- Any reason for a developer to use this over calling Gemini directly.
- A vector store (RAG is documented but not implemented).
- API key authentication (replaced by Bearer JWT — wrong abstraction for B2B).
- Developer-facing documentation, SDK, or onboarding.
- A single paying customer or proof of demand.

### The Core Risk of this Pivot

> [!CAUTION]
> **"RAG as a Service" is an extremely crowded space.** LangChain, LlamaIndex, Cohere, Weaviate, and literally every cloud provider already sell this. If your only differentiator is "we do RAG," you will die. The unique claim has to be **Depth-Aware RAG** — the ability to retrieve the same knowledge and re-synthesize it at different cognitive depths for different audiences. This is the only moat worth building.

---

## 1. Product Vision

**DepthAPI** is a B2B inference API that takes any knowledge corpus (your own docs, PDFs, or DepthAPI's pre-indexed datasets) and answers any question about it at a **configurable cognitive depth** — from a child-friendly summary to a graduate-level technical analysis — using the optimal LLM for that depth, with automatic provider failover.

### The Elevator Pitch (1 sentence)
> *"Stripe for intelligent knowledge retrieval — one API call, configurable depth, automatic failover, your data or ours."*

### The Moat Analysis

| Moat | Strength | Reality Check |
|------|----------|---------------|
| **Depth-Aware synthesis** | ✅ **Strong** — No competitor offers this as a first-class API param | Only works if the prompt engineering is genuinely good. Currently ELI5 / Technical is barely differentiated. Needs real investment. |
| **Multi-provider failover** | ⚠️ **Weak alone** — LangChain/LlamaIndex do this too | Strong when combined with depth-aware routing (use Cerebras for speed on ELI5, Gemini Pro for depth on Technical). The *combination* is the moat. |
| **Pre-indexed datasets** (arXiv, Wikipedia) | ✅ **Medium** — reduces onboarding friction for devs | Only defensible if you keep them fresh and curated. Stale data is a liability not an asset. |
| **Cost efficiency (Gemini free tier + self-hosted pgvector)** | ✅ **Strong for you** | Not a customer-facing moat. It's a margin advantage that lets you undercut competitors on price. |
| **Bring Your Own Documents** | ⚠️ **Table stakes** | Every RAG product does this. You must do it to be competitive, not to win. |

---

## 2. Naming Decision

**KnowBear** is a consumer brand. Kill it for the B2B product.

Recommended names ranked:

| Name | Verdict | Reason |
|------|---------|--------|
| **DepthAPI** | ✅ Pick this | Says exactly what it does. Memorable. `.com` likely available. API-native. |
| CognitoCore | ❌ | Sounds like an AWS service or a 2008 startup. Confusing. |
| LayeredAI | ⚠️ | Describes the feature but not the developer tool. |
| NexusNode | ❌ | Means nothing. Generic. |

**Decision: DepthAPI.**

---

## 3. What Customers Actually Build With This

### Primary Customer: The Platform Builder
An EdTech company building a learning platform doesn't want to prompt-engineer GPT themselves. They call:
```bash
POST https://api.depthapi.dev/v1/query
{
  "q": "What is the Krebs Cycle?",
  "depth": "undergraduate",
  "collection": "biology-101",
  "format": "markdown"
}
```
They get back a cited, appropriately complex answer. They embed it in their LMS. They pay per token-bundle.

### Secondary Customer: The Enterprise Docs Team
A mid-size company wants their internal 3,000-page policy manual to become a "smart search" tool for employees at every level (new hire vs. senior counsel). They ingest once, query forever at different depths.

### What DepthAPI provides beyond the raw API
You *must* provide these or no one will build on you:

1. **API Key Dashboard** — minimal, functional. Shows usage, billing, key rotation.
2. **An ingestion CLI** (`depthapi ingest ./my-docs/`) — one-command local ingestion.
3. **A JavaScript SDK** (`npm install depthapi`) — one function call in React/Node.
4. **OpenAPI / Swagger docs** — auto-generated from FastAPI, but curated.
5. **A "Playground"** — a single web page to test queries against the API without code. (Essentially, this replaces the killed web app, but as a *developer tool*, not a consumer product.)

> [!IMPORTANT]
> The Playground is non-negotiable. Every successful API product (Stripe, Twilio, OpenAI) has a browser-based playground. It is the #1 conversion tool for developer sign-ups. **This is the only frontend you rebuild.**

---

## 4. System Architecture

```
Developer App
     │
     ▼
POST /v1/query  ──► [API Key Auth] ──► [Rate Limiter (existing)]
                                              │
                              ┌───────────────┼───────────────────┐
                              ▼               ▼                   ▼
                       [Vector Search]  [Web Search]       [Cache Check]
                       (pgvector/         (Tavily/             (Redis)
                        Supabase)         Serper/Exa)
                              │               │
                              └───────────────┘
                                      │
                                      ▼
                            [Context Builder]
                            (budget: RAG first,
                             web second, capped
                             at 2k tokens total)
                                      │
                                      ▼
                          [Depth-Aware Routing]
                         (ELI5 → Groq/Cerebras fast
                          Technical → Gemini Pro deep
                          Socratic → OpenRouter free)
                                      │
                                      ▼
                           [Provider Cascade]
                        (existing llm_client.py logic)
                                      │
                                      ▼
                             [Response + Citations]
                             {answer, depth, sources,
                              tokens_used, model_used}
```

### The RAG Layer (What Needs to Be Built)

```
POST /v1/ingest
     │
     ▼
[File/Text Input] ──► [Chunker (512 tok, 100 overlap)] ──► [Gemini Embedding]
                                                                    │
                                                                    ▼
                                                        [pgvector (Supabase)]
                                                        knowledge_chunks table
```

---

## 5. API Surface (v1)

### Authentication
Replace Supabase Bearer JWT with **API Key** auth.
```
Authorization: Bearer sk-depth-XXXXXXXXXXXX
```

### Endpoints

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `POST` | `/v1/query` | Core inference endpoint | API Key |
| `POST` | `/v1/ingest` | Upload documents/text | API Key |
| `GET` | `/v1/collections` | List ingested collections | API Key |
| `DELETE` | `/v1/collections/{id}` | Delete a collection | API Key |
| `GET` | `/v1/usage` | Token usage for current period | API Key |
| `GET` | `/v1/health` | Service health | Public |
| `POST` | `/internal/keys` | Generate API key (admin only) | Admin Key |

### Core Request/Response

**Request:**
```json
{
  "q": "Explain transformer attention mechanisms",
  "depth": "graduate",
  "collection_id": "ml-textbooks-v2",
  "stream": true,
  "format": "markdown",
  "top_k": 3
}
```

**`depth` parameter values:**
- `"child"` → ELI5
- `"teen"` → ELI12  
- `"undergraduate"` → ELI15/detailed
- `"graduate"` → Technical/rigorous
- `"socratic"` → Question-guided

**Response:**
```json
{
  "answer": "...",
  "depth": "graduate",
  "model_used": "gemini-2.5-pro",
  "sources": [
    {"title": "Attention is All You Need", "url": "...", "chunk_score": 0.91}
  ],
  "tokens_used": 1842,
  "cached": false,
  "latency_ms": 1240
}
```

---

## 6. Database Schema Changes

### New: `api_keys` table
```sql
CREATE TABLE api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash    TEXT NOT NULL UNIQUE,  -- bcrypt hash of sk-depth-XXX
    prefix      TEXT NOT NULL,         -- first 12 chars for display
    project_name TEXT NOT NULL,
    owner_email TEXT NOT NULL,
    plan        TEXT NOT NULL DEFAULT 'free',  -- 'free' | 'starter' | 'pro'
    monthly_token_budget BIGINT NOT NULL DEFAULT 100000,
    created_at  TIMESTAMPTZ DEFAULT now(),
    revoked_at  TIMESTAMPTZ
);
```

### New: `knowledge_collections` table
```sql
CREATE TABLE knowledge_collections (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id  UUID REFERENCES api_keys(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    doc_count   INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

### New: `knowledge_chunks` table
```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE knowledge_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id   UUID REFERENCES knowledge_collections(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,
    embedding       vector(768),          -- Gemini text-embedding-004 dimension
    metadata        JSONB DEFAULT '{}',   -- source, page, title, url
    chunk_index     INT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
```

---

## 7. Monetization

| Plan | Price | Monthly Token Budget | RAG Collections | Features |
|------|-------|---------------------|-----------------|---------|
| **Free** | $0 | 100K tokens | 1 (max 50 docs) | All depth levels, streaming |
| **Starter** | $29/mo | 2M tokens | 5 collections | Analytics dashboard |
| **Pro** | $99/mo | 10M tokens | Unlimited | Priority routing, SLA |
| **Enterprise** | Custom | Custom | Custom + pre-indexed datasets | SSO, dedicated infra |

**Why this works financially:**
- Your Gemini free-tier handles ~500K tokens/day. Free plan is literally free to serve.
- Starter plan: at $29, you need 3 customers to cover Upstash Redis costs. 10 customers = profitable.
- The Lua-based rate limiter in [rate_limit.py](file:///c:/Users/Sanjeev/Documents/KnowBear-main/api/services/rate_limit.py) already tracks `monthly_token_budget` scoped by identifier. Re-scoping to API key takes hours, not days.

---

## 8. Tradeoffs — The Hard Decisions

### Tradeoff 1: Kill the Web App Entirely vs. Keep a Playground
- **Kill entirely:** Faster to ship. Less maintenance.
- **Keep a Playground:** Non-negotiable for developer conversions. Every API company has one.
- **Decision: Kill `src/`. Build a single-page Playground as a static HTML file served directly from FastAPI. No React, no Vite, no Tailwind. Just vanilla JS that calls your own API.**

### Tradeoff 2: Self-Hosted pgvector vs. Dedicated Vector DB (Pinecone/Weaviate)
- **pgvector:** Already in your Supabase. Zero extra cost. Schema migrations already established. Max ~10M vectors on free/cheap tiers.
- **Pinecone:** $70/mo minimum. Faster at massive scale (10M+ vectors). Extra latency hop.
- **Decision: pgvector with HNSW index for now. Migration to Pinecone only if a customer hits 5M+ chunks.**

### Tradeoff 3: Gemini Embeddings vs. OpenAI Embeddings
- **Gemini `text-embedding-004`:** Free tier, 768 dimensions. Already in your provider stack.
- **OpenAI `text-embedding-3-small`:** $0.02/1M tokens. 1536 dimensions. Better benchmarks.
- **Decision: Gemini for now.** Your rate_limit.py already tracks Gemini token consumption. Adding embedding calls to the same budget is trivial. Switch to OpenAI if embedding quality is measurably poor.

### Tradeoff 4: Keeping Supabase Auth vs. Rolling Your Own API Keys
- **Supabase Auth (current):** Fine for consumer. Terrible for B2B API (JWT expiry, JWKS refresh, Supabase dependency).
- **Custom API Keys:** Simple. Bcrypt the key. Store the hash. Compare on every request. Add to Redis cache for speed.
- **Decision: Custom API keys. Rip out Supabase Auth from the API layer. Keep Supabase only for data storage (pgvector, usage logs).**

> [!WARNING]
> This is the single most breaking change in the entire pivot. Every existing route uses `verify_token_optional` from [auth.py](file:///c:/Users/Sanjeev/Documents/KnowBear-main/api/auth.py). Every single one needs to be updated. Budget 1-2 days for this alone.

---

## 9. What to Kill (The Great Pruning List)

| File/Directory | Reason for Deletion |
|----------------|-------------------|
| `src/` (entire React app) | Replaced by static Playground |
| `Dockerfile.frontend` | No longer needed |
| `Dockerfile.frontend.test` | No longer needed |
| `vercel.json` | No longer deploying frontend |
| `playwright.config.ts` | E2E tests were for UI |
| `tests/e2e/` | UI-specific tests |
| `api/routers/emails.py` | Consumer feature |
| `api/routers/shares.py` | Consumer viral loop, irrelevant for B2B |
| `api/routers/legal.py` | Replace with static `/v1/tos` doc |
| `api/routers/pinned.py` | Consumer feature |
| `api/routers/seo.py` | Consumer feature |
| `api/services/email_service.py` | Consumer feature |
| `api/services/email_templates.py` | Consumer feature |
| `api/services/share_manager.py` | Consumer feature |
| `api/services/analytics.py` | Replace with simpler API usage tracking |
| `package.json` (frontend deps) | NodeJS frontend tooling |
| `tailwind.config.js` | No Tailwind |
| `tsconfig.json` | No TypeScript frontend |

**WARNING — Do NOT delete:**
- `api/services/rate_limit.py` — The Lua scripts are gold. Re-scope to API keys.
- `api/services/llm_client.py` — The whole multi-provider cascade. Core asset.
- `api/services/inference.py` — The depth-aware routing engine. Core asset.
- `api/services/cache.py` — Redis caching. Core asset.
- `api/services/search.py` — Web search augmentation. Core asset.
- `supabase/migrations/` — Keep all existing migrations.

---

## 10. Phased Implementation Plan

### Phase 0: The Great Pruning (1 day)
- [ ] Delete all frontend directories and consumer-only routers listed above.
- [ ] Strip `package.json` to only keep backend dev scripts.
- [ ] Confirm `uvicorn main:app` still boots cleanly after deletions.
- [ ] Rename all internal Redis keys from `knowbear:*` to `depthapi:*`.

### Phase 1: Auth Rebuild (2 days)
- [ ] Create `api_keys` table migration.
- [ ] Write `verify_api_key` FastAPI dependency (hash comparison + Redis cache).
- [ ] Write `POST /internal/keys` admin endpoint (protected by env var `ADMIN_SECRET`).
- [ ] Update every router to use `verify_api_key` instead of `verify_token_optional`.
- [ ] Re-scope `rate_limit.py` from `user:{user_id}` to `key:{api_key_id}`.

### Phase 2: RAG Foundation (3 days)
- [ ] Write migration for `knowledge_collections` + `knowledge_chunks` tables with pgvector.
- [ ] Write `api/services/rag_service.py`:
  - `chunk_text(text, chunk_size=512, overlap=100)`
  - `embed_chunks(chunks)` → calls Gemini `text-embedding-004`
  - `search_chunks(query_embedding, collection_id, top_k=5, threshold=0.75)`
- [ ] Write `POST /v1/ingest` router.
- [ ] Write `GET/DELETE /v1/collections` routers.
- [ ] Inject RAG context into `inference.py`'s `_build_messages` before web search context.

### Phase 3: API Hardening + Developer Experience (2 days)
- [ ] Rebuild `POST /v1/query` with clean request/response schema (replace `QueryRequest`).
- [ ] Add `sources` field to all responses (from RAG chunks + web search).
- [ ] Build static Playground HTML page served at `/playground`.
- [ ] Generate and curate OpenAPI docs served at `/docs`.
- [ ] Write `depthapi` Python SDK (thin wrapper, ~200 lines).

### Phase 4: Monetization Wiring (1 day)
- [ ] Implement plan-based token budgets in `rate_limit.py` keyed by `api_key_id`.
- [ ] Add `GET /v1/usage` endpoint.
- [ ] Integrate DodoPayments (already wired in `payments.py`) for plan upgrades.

---

## 11. Open Questions — **RESOLVED**

| Question | Decision |
|----------|----------|
| Preserve existing KnowBear.app? | **Clean slate.** No backward compatibility. |
| Infrastructure budget? | **$0. Full local/self-hosted approach.** |
| Pre-indexed datasets Day 1? | **Yes — WikiDump, arXiv abstracts, select HF datasets are the core corpus.** |
| SDK language? | **Python first.** |

---

## 12. Clarifications & Updated Decisions

### 12.1 RAG Infrastructure — The Two-Corpus Architecture

The pre-indexed datasets (WikiDump, arXiv, HF) are a **fundamentally different problem** from customer-uploaded documents. They must be treated as separate storage tiers.

#### Tier 1 — Trusted Corpora (Pre-indexed, Self-Hosted)

These are your pre-built Knowledge Bases. They are yours. They don't belong to any customer's collection. They are the value you sell access to.

**The math:**
- WikiDump: ~80GB raw text → ~30M chunks → ~300GB with HNSW index (per your Self_Hosted_RAG_Guide.md)
- arXiv abstracts only: ~2GB → ~600K chunks → ~4GB indexed — **start here, not full PDFs**
- HF datasets: variable, select carefully. Target <5GB raw per dataset.
- **Total footprint (WikiDump + arXiv abstracts + 3 HF datasets):** ~350-380GB

**Infrastructure:** This absolutely cannot live on Supabase free tier (500MB limit).

**Decision: Self-host pgvector locally using Docker Compose on a dedicated machine.**

Minimum viable hardware (from your guide):
- 1TB NVMe SSD
- 32GB RAM (64GB strongly preferred for HNSW to live in memory)
- 8-core CPU
- Connectivity: Cloudflare Tunnel → public API endpoint OR Tailscale → internal only

**Ingestion pipeline:**
```
WikiDump XML / arXiv API / HF datasets
         │
         ▼
scripts/ingest_corpus.py
  - Parse raw source
  - Recursive character chunking (1000 tok, 100 overlap for trusted corpora)
  - Batch embed via Gemini text-embedding-004 (batches of 100)
  - Write to local pgvector: knowledge_chunks (corpus_type='trusted')
```

This is a one-time offline job. It will take hours, not minutes. Run it once, index it, never touch it again except for monthly refresh cron jobs.

#### Tier 2 — Customer Documents (BYOD, Supabase-Hosted)

Customer-uploaded PDFs/text for their private collections. These are small by comparison (typical: 50-500 documents per enterprise customer). This CAN live in Supabase cloud free tier for v1 (up to ~2M vectors before hitting limits).

**Schema split:**
```sql
-- Tier 1: Local self-hosted pgvector instance
CREATE TABLE trusted_corpus_chunks (
    id           UUID PRIMARY KEY,
    corpus_type  TEXT,  -- 'wikipedia', 'arxiv', 'hf_dataset_name'
    content      TEXT,
    embedding    vector(768),
    metadata     JSONB,
    chunk_index  INT
);

-- Tier 2: Supabase cloud (customer documents)
CREATE TABLE knowledge_chunks (
    id            UUID PRIMARY KEY,
    collection_id UUID REFERENCES knowledge_collections(id),
    content       TEXT,
    embedding     vector(768),
    metadata      JSONB,
    chunk_index   INT
);
```

**At query time:** Search BOTH databases, merge results ranked by cosine score, deduplicate, inject into context.

> [!IMPORTANT]
> The local pgvector instance is your competitive moat. Competitors can't serve Wikipedia + arXiv for free. You can, because it's sitting on your own hardware. This is the indie-hacker advantage.

---

### 12.2 Do Customers Need Their Own Storage?

**No. DepthAPI manages storage for customers.**

This is the right call for B2B. The developer experience should be:
```bash
# They send documents to YOU
curl -X POST https://api.depthapi.dev/v1/ingest \
  -H "Authorization: Bearer sk-depth-xxxx" \
  -F "file=@my_company_handbook.pdf" \
  -F "collection_name=company-docs"

# They query against their collection
curl -X POST https://api.depthapi.dev/v1/query \
  -H "Authorization: Bearer sk-depth-xxxx" \
  -d '{"q": "What is our PTO policy?", "depth": "simple", "collection_id": "company-docs"}'
```

They never touch a database. They never deploy infrastructure. That's the whole point.

**The storage model:**
- Free tier: 1 collection, max 50 documents, max 50MB
- Starter: 5 collections, max 500 documents, max 500MB — **this is where Supabase free tier maxes out**
- Pro: Unlimited collections → at this point you need paid Supabase OR local hosting for Tier 2 as well

**Supabase free tier limit:** 500MB database. At ~3KB/vector × 768 dims, that's ~160K chunks = roughly 400-600 average documents. Enough for 2-3 starter-tier customers **total** before you need to either upgrade or self-host Tier 2 as well.

> [!WARNING]
> This is the Achilles heel of the $0 budget. You get ~3 paying customers worth of storage on Supabase free. After that, you either charge them, or move Tier 2 to your local machine too. Plan for this from day 1 by designing the `DATABASE_URL` as a switchable env var, not hardcoded to Supabase.

---

### 12.3 Depth Levels — The Redesign

**The Problem with the Current Levels:**
- `ELI5`, `ELI10`, `ELI12`, `ELI15` are cosmetically different but produce nearly identical outputs from the LLM. No developer can intuit what "ELI10 vs ELI12" means. It's confusing.
- `Meme` is actually a strong differentiator — it makes DepthAPI memorable and shareable. Keep it.
- `Socratic` is a **mode**, not a depth (it changes the *interaction type*, not the knowledge level). It belongs as a separate `mode` param.

**Proposed Clean Depth System (5 levels):**

| `depth` value | What it means | LLM Routing | Old equivalent |
|---|---|---|---|
| `"simple"` | No jargon. Metaphors and analogies only. 5-year-old can follow. | Groq Llama (fast, cheap) | ELI5 |
| `"accessible"` | Plain language. Some domain terms defined inline. High-schooler can follow. | Gemini Flash (balanced) | ELI12-15 merged |
| `"technical"` | Domain terms assumed. Structured with code/math/diagrams. | Gemini Pro (deep) | Technical |
| `"expert"` | Peer-level depth. Assumes full domain mastery. Citations expected. | Gemini Pro + RAG enforced | (new) |
| `"meme"` | Explain it using internet culture, humor, and analogies. | Groq Llama (fast, fun) | Meme |

**The `mode` parameter (separate from depth):**

| `mode` value | What it means |
|---|---|
| `"answer"` | Default. Direct answer. |
| `"socratic"` | Guide via questions. No direct answer. |
| `"compare"` | Structured comparison of two concepts (detected from query). |

**Resulting API call example:**
```json
{
  "q": "What is a transformer neural network?",
  "depth": "accessible",
  "mode": "socratic",
  "collection_id": "ml-papers"
}
```

**Why `ELI10` and `ELI12` are being scrapped:**
There is no prompt engineering trick that produces a meaningfully different answer for "10-year-old" vs "12-year-old." The delta is noise. What actually matters is: *"Does this person know domain jargon?"* — which is a 3-state toggle: no (simple), some (accessible), yes (technical/expert). The fifth level `meme` is purely tonal, not depth-relative.

---

### 12.4 Updated `inference.py` Routing Map

With the new depth levels, the routing changes to:

```python
DEPTH_ROUTING = {
    "simple":     ["learn-groq-llama8b", "learn-gemini-flash"],
    "accessible": ["learn-gemini-flash", "learn-groq-llama8b"],
    "technical":  ["technical-gemini-pro", "technical-groq-llama8b"],
    "expert":     ["technical-gemini-pro", "technical-cerebras-glm"],
    "meme":       ["learn-groq-llama8b", "learn-gemini-flash"],  # fast + fun
}
```

The existing `inference_routing.py` alias system maps directly to this. Zero structural change needed — only the parameter names change and the `ELI10/ELI12/ELI15` → `simple/accessible` consolidation.

---

### 12.5 Updated Phase Plan (incorporating all clarifications)

**New Phase 0: Local Infrastructure Setup (Before any code)**
- [ ] Provision local machine with Docker Compose + pgvector
- [ ] Set up Cloudflare Tunnel for public API routing from local machine
- [ ] Configure Watchtower for auto-deploy from GitHub → Docker Hub → local server
- [ ] Write `scripts/ingest_corpus.py` — offline ingestion pipeline for Tier 1 corpora
- [ ] Run initial arXiv abstracts ingestion (start small, ~600K chunks, ~4GB)

**Updated Phase 1: Auth Rebuild** *(unchanged from above)*

**Updated Phase 2: Two-Tier RAG** *(extended)*
- [ ] Configure dual `DATABASE_URL` env vars: `LOCAL_PGVECTOR_URL` + `SUPABASE_URL`
- [ ] Write `rag_service.py` to route queries to both tiers and merge results
- [ ] Implement cosine threshold (0.75) with graceful empty-context degradation

**Updated Phase 3** *(unchanged)*

**Updated Phase 4** *(unchanged)*
