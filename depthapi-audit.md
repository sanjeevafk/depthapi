# DepthAPI — Comprehensive Audit & Strategic Pivot Assessment
> **Date:** August 2, 2026 | **Auditor:** Antigravity (Orchestrator Mode)
> **Scope:** Documentation, Architecture, Supabase/DB, Strategic Direction, Gap Analysis, Roadmap

---

## EXECUTIVE SUMMARY

DepthAPI is a **FastAPI-based cognitive synthesis engine** built around dataset-specific developer knowledge retrieval. The codebase has reached genuine architectural competence — it is not a toy project. It has a working multi-provider LLM stack, a sophisticated streaming pipeline, a real hybrid-search (pgvector + BM25 via RRF) Supabase schema, and a reasonable API key auth system.

However, the project is fighting itself: the documentation describes six different visions simultaneously. The codebase has significant technical debt concentrated in routers and persistence. The database schema has multi-tenancy primitives but no true tenant isolation at the row level for RAG tables. And the path to a general-purpose RAG + OKF knowledge engine is obstructed by hardcoded dataset assumptions scattered across all layers.

**The pivot is advisable, but not free.** The foundations are strong enough to build on, but the wrong abstractions have calcified in enough places that "pivot" will require targeted surgical rewrites before new features are added.

---

## 1. DOCUMENTATION AUDIT

### 1.1 File-by-File Inventory

#### `local-docs/` Root

| File | Summary | Classification | Relevance |
|---|---|---|---|
| `DEPTHAPI_PRODUCT_ARCHITECTURE_OKF_HERMES.md` | Blueprint for OKF dual-indexing (concept graph + vector), cognitive depth levels 1–5, MCP server for Hermes/Claude integration | **Future/Ideas** | Directly aligned with the target pivot. Partially implemented (depth routing absent; OKF builder absent). |
| `architecture_overview.md` | Distributed retrieval architecture: Supabase (canonical) + Turso (edge cache/failover) | **Stale** | Turso integration is described but entirely unimplemented in code. No Turso adapter, no edge layer. |
| `depthapi_architecture.md` | Deep codebase tour with mermaid diagrams, layer-by-layer walkthrough of the actual implementation | **Active** | The most accurate and up-to-date architectural reference. Matches the real codebase closely. |
| `depthapi_use_cases.md` | Five commercial use cases: dev portals, EdTech, internal knowledge bases, legal compliance, fintech | **Active** | Useful marketing and product context. Aligns well with the OKF pivot vision. |
| `benchmark_evals_critique.md` | Critical review of `evaluation/` benchmark framework quality | **Active** | Technically accurate and actionable critique. |
| `dataset_corpus_evaluation.md` | Analysis of existing CS/programming book datasets for ingestion quality | **Archive** | Dataset-specific. No relevance after the general-purpose pivot. |
| `ENTERPRISE_RAG_PIPELINE_PLAN.md` | 60KB detailed declarative pipeline architecture spec | **Future/Ideas** | High-quality architectural thinking. Core ideas (YAML plugin pipelines, immutable Pydantic contracts) are directly reusable for generic ingestion. Should be stripped of dataset-specific examples and generalized. |
| `DepthAPI.pptx` | Slide deck | **Archive** | Snapshot artifact. Not actionable. |

#### `local-docs/docs/` (Troubleshooting & Operations)

| File | Summary | Classification |
|---|---|---|
| `COMPREHENSIVE_DIAGNOSIS_REPORT.md` | Debugging report for vector retrieval failures | **Archive** — problem likely resolved |
| `LOCAL_SUPABASE_SETUP.md` | Step-by-step guide for local Supabase instance | **Active** — operationally necessary |
| `MOCK_RAG_IMPLEMENTATION.md` | Documents mock RAG fallback behavior | **Archive** — mock layer superseded by real backend |
| `RESPONSE_FLOW_ANALYSIS.md` | Traces a full request through the SSE pipeline | **Stale** — partially superseded by `depthapi_architecture.md` |
| `RETRIEVAL_FIX_QUICK_REFERENCE.md` | Quick fix card for retrieval issues | **Archive** — historical debugging artifact |
| `RETRIEVAL_SYSTEM_STATUS.md` | Status report on retrieval fixes | **Archive** |
| `SUPABASE_FIX_SUMMARY.md` | Summary of Supabase connection fixes | **Archive** |
| `SUPABASE_RETRIEVAL_FAILURE.md` | Root cause analysis of retrieval failures | **Archive** |
| `VECTOR_RETRIEVAL_DIAGNOSTICS.md` | Detailed diagnostic log for vector search failures | **Archive** |

#### `local-docs/archive/` (Historical Planning)

| File | Summary | Classification |
|---|---|---|
| `CHUNK_QUALITY_EVALUATION.md` | 26KB analysis of 54K-chunk FAISS corpus quality | **Archive** — pre-pgvector era |
| `CIU_INGESTION.md` | Coding Interview University dataset ingestion plan | **Archive** — dataset-specific |
| `DEBATE_AI_USE_CASE.md` | AI debate system use case exploration | **Archive** — speculative feature |
| `EVALUATION_PACKAGE_INDEX.md` | Index of evaluation packages | **Archive** |
| `MASTER-reference.md` | Agent prompt reference from May 2026 sprint | **Stale** — tasks now partially complete |
| `MASTER-strategy.md` | Open-core strategy and moat analysis | **Future/Ideas** — still directionally relevant |
| `MULTI_VERTICAL_STRATEGY.md` | Plans for multiple industry verticals | **Future/Ideas** |
| `MVP_DEV_VERTICAL.md` | MVP plan for developer tooling vertical | **Stale** — partially implemented |
| `OPEA_DOCS_INGESTION.md` | OPEA documentation ingestion plan | **Archive** — dataset-specific |
| `PGVECTOR_EXECUTIVE_SUMMARY.md` | Decision brief for pgvector migration | **Archive** — migration is complete |
| `PGVECTOR_MIGRATION_SPEC.md` | Technical spec for FAISS→pgvector migration | **Archive** — migration is complete |
| `PLAN-depthapi-elite.md` | Elite expansion plan (B2B headless engine) | **Stale** — partially executed |
| `PLAN-tech-debt.md` | Tech debt refactor prerequisite list | **Active** — most tasks still outstanding |
| `PLAN.md` | General project plan | **Stale** — superseded |
| `RAG_implementation_plan.md` | Early RAG implementation plan | **Archive** — superseded by v5 hybrid search |
| `advanced_rag_architectures.md` | Survey of HyDE, ColBERT, RAPTOR techniques | **Future/Ideas** — useful reference |
| `broader_goal_tasks.md` | 8 high-fidelity retrieval quality goals | **Active** — still relevant checklist |
| `commands-reference.md` | CLI and command reference | **Stale** |
| `datasets_analysis.md` | Evaluation of CS/programming book datasets | **Archive** — dataset-specific |
| `fpb_langs_analysis.md` | Free programming books analysis | **Archive** — dataset-specific |
| `implementation_plan.md` | 25KB API surface, DB schema, depth levels spec | **Stale** — partially implemented |
| `pageindex_evaluation.md` | Evaluation of page-level indexing approaches | **Archive** |
| `project_evaluation.md` | Early project evaluation | **Archive** |
| `rag_design.md` | 23KB RAG design authority document | **Active** — still the most comprehensive retrieval design reference |
| `repo_strategy.md` | Repository strategy notes | **Archive** |
| `wordllama_hyde_rrf_evaluation.md` | Evaluation of HyDE + WordLlama + RRF | **Future/Ideas** — valuable retrieval research |
| `ciu_analysis.md` | Coding Interview University analysis | **Archive** |

#### `local-docs/plans/`

| File | Summary | Classification |
|---|---|---|
| `consolidated_goals_execution_plan.md` | 46KB master execution plan | **Stale** — too large, overlaps everything else |
| `corpus_pipeline_retrieval_reset_plan.md` | Reset plan for corpus pipeline | **Archive** |

---

### 1.2 Contradictions & Overlaps

| Contradiction | Documents Involved |
|---|---|
| **Turso edge layer** described as integral but entirely absent from code | `architecture_overview.md` vs actual codebase |
| **Embedding dimension**: 1536 referenced in old docs, 768 in current code | Multiple archive docs vs current migrations |
| **Auth model**: Supabase JWT vs API keys — docs describe both as current | `conversation_schema_baseline.sql` (JWT RLS) vs `create_api_keys.sql` |
| **Depth level naming**: "simple/accessible/technical/expert/meme" vs "learn/technical/socratic" | `MASTER-reference.md` vs `conversations` table CHECK constraint |
| **OKF Builder**: Described as a core component in `DEPTHAPI_PRODUCT_ARCHITECTURE_OKF_HERMES.md` | **Does not exist in code** |
| **FAISS**: Still referenced as current storage in many archive docs | Replaced by pgvector (migration complete) |
| **Three active "master" plans**: `MASTER-reference.md`, `consolidated_goals_execution_plan.md`, `implementation_plan.md` | All overlap, none is canonical |

---

### 1.3 Missing Documentation

- ❌ **No `CONTRIBUTING.md`** — no onboarding guide for new contributors
- ❌ **No API Reference** — `openapi.json` or equivalent not generated/documented
- ❌ **No Data Model diagram** — no ERD for the current Supabase schema
- ❌ **No runbook for Supabase migrations** — how to apply migrations in prod
- ❌ **No OKF specification** — the concept is described but never formally defined
- ❌ **No agent/MCP integration spec** — Hermes/Claude tool integration only sketched
- ❌ **No multi-tenancy design doc** — how tenant isolation actually works is undocumented
- ❌ **No deployment guide** — Docker/cloud deployment process undocumented

---

### 1.4 Recommended Documentation Structure

```
docs/
├── README.md                    # Project overview, quick start
├── ARCHITECTURE.md              # Canonical architecture (replaces depthapi_architecture.md)
├── DATA_MODEL.md                # ERD + table descriptions + RLS matrix
├── API_REFERENCE.md             # OpenAPI spec or generated reference
├── INGESTION.md                 # How document ingestion works
├── RETRIEVAL.md                 # Hybrid search, OKF, depth routing
├── MULTI_TENANCY.md             # Tenant isolation model
├── AUTH.md                      # API key system, RLS, security
├── DEPLOYMENT.md                # Docker, Supabase, environment setup
├── CONTRIBUTING.md              # Contribution guide
├── OKF_SPECIFICATION.md         # Open Knowledge Framework formal spec
│
├── adr/                         # Architecture Decision Records
│   ├── 001-pgvector-over-qdrant.md
│   ├── 002-api-keys-over-jwt.md
│   └── 003-filesystem-fallback.md
│
└── plans/
    ├── okf-pivot-roadmap.md     # THIS audit's roadmap
    └── tech-debt-backlog.md     # Consolidated from PLAN-tech-debt.md
```

---

### 1.5 Prioritized Documentation Cleanup Plan

| Priority | Action | Files |
|---|---|---|
| P0 | **Delete** — pure historical debugging noise | All 8 files in `docs/` subdirectory |
| P0 | **Archive** — move to `/archive/` or delete | `dataset_corpus_evaluation.md`, `PGVECTOR_EXECUTIVE_SUMMARY.md`, `PGVECTOR_MIGRATION_SPEC.md`, `CIU_INGESTION.md`, `OPEA_DOCS_INGESTION.md`, datasets/fpb analysis files, `corpus_pipeline_retrieval_reset_plan.md` |
| P1 | **Rewrite** — update to reflect current code | `architecture_overview.md` → expand to `ARCHITECTURE.md`; remove Turso references |
| P1 | **Merge** — consolidate planning documents | `implementation_plan.md` + `MASTER-reference.md` + `consolidated_goals_execution_plan.md` → single `plans/tech-debt-backlog.md` |
| P1 | **Create** | `DATA_MODEL.md`, `CONTRIBUTING.md`, `DEPLOYMENT.md` |
| P2 | **Generalize** | `ENTERPRISE_RAG_PIPELINE_PLAN.md` → strip dataset specifics, keep plugin pipeline architecture |
| P2 | **Promote** | `DEPTHAPI_PRODUCT_ARCHITECTURE_OKF_HERMES.md` → formalize as `OKF_SPECIFICATION.md` |

---

## 2. ARCHITECTURE AUDIT

### 2.1 What the Project Currently Is

DepthAPI is a **streaming inference API** with RAG augmentation. Its core value propositions are:

1. Multi-provider LLM fallback chain with circuit breaking
2. Hybrid pgvector + BM25 search via RRF
3. API-key-scoped multi-tenant knowledge collections
4. Streaming SSE with idempotency and response caching
5. Intent classification and mode-based prompt routing

It is **not** yet a knowledge engine, a knowledge graph, or a general-purpose document ingestion platform. It is an inference API that happens to do document retrieval.

---

### 2.2 Architectural Assumptions (Current)

| Assumption | Evidence | Problem for Pivot |
|---|---|---|
| Documents belong to a single "developer knowledge" domain | Mock source catalogs, hardcoded corpus URIs | Breaking — OKF needs domain-agnostic ingestion |
| One embedding model, fixed dimension (768, Gemini) | `config.py` hardcode + startup validation | Breaking — multi-source pipelines may need model routing |
| Three fixed interaction modes (learn/technical/socratic) | DB CHECK constraints, router logic, prompt configs | Constraining — OKF needs extensible context types |
| Retrieval is always flat-chunk-based | No hierarchical index, no OKF graph | Breaking — OKF requires concept graph layer |
| Auth is API-key-per-tenant at collection level | `knowledge_collections.api_key_id` | Mostly correct — RLS enforcement still incomplete |
| Turso provides an edge layer | Architecture docs | **False** — not implemented |
| Web search is a retrieval augmentation | `search.py` with Tavily/Serper/Exa | Correct and reusable |

---

### 2.3 Tightly Coupled to Dataset-Centric Design

The following components are hardcoded to specific dataset assumptions and will **resist the pivot**:

#### Hardcoded in Code
- **`rag_backend_router.py`** — `_mock_source_catalog()` contains hardcoded `depthapi://corpus/technical`, `depthapi://docs/api-reference`, `depthapi://kb/engineering` URIs. This is a silent lie embedded in operational fallback logic.
- **`inference_constants.py`** — Model aliases (`tech-gemini-pro`, etc.) implicitly assume a narrow query domain.
- **`utils.py`** — `LEARNING_MODE`, `TECHNICAL_MODE`, `SOCRATIC_MODE` constants propagated through 10+ files.
- **`prompts.py`** — System prompt hardcoded as a single global constant, not tenant or collection configurable.
- **`config.py`** — `embedding_provider`, `embedding_model`, `embedding_dimension` hardcoded; no runtime override mechanism.

#### Hardcoded in Schema
- `conversations.mode CHECK (mode IN ('learn', 'technical', 'socratic'))` — prevents extensible context modes
- `knowledge_documents.language_config CHECK (language_config IN ('english', 'french', ...))` — prevents non-text content types
- `knowledge_query_logs` — single April 2026 partition; no partition creation automation
- RRF k-weights in `hybrid_search_v5` — hardcoded to `'code'`, `'technical'`, `'conceptual'` string matching

---

### 2.4 Reusable Components (Preserve)

| Component | Quality | Reuse Value |
|---|---|---|
| `llm_client.py` — Multi-provider async client with circuit breaking | ⭐⭐⭐⭐⭐ | Core asset for any LLM-backed service |
| `StreamEventLoop` + streaming pipeline | ⭐⭐⭐⭐ | Well-engineered SSE delivery system |
| `supabase_adapter.py` — Lightweight httpx-based Supabase client | ⭐⭐⭐⭐ | Eliminates SDK dependency weight |
| `StreamConfig` frozen dataclass | ⭐⭐⭐⭐ | Good performance pattern, reusable |
| `hybrid_search_v5` SQL function | ⭐⭐⭐⭐⭐ | Production-grade RRF hybrid search |
| Redis circuit-breaker + `redis_safe.py` | ⭐⭐⭐⭐ | Clean resilience pattern |
| `knowledge_collections` / `knowledge_documents` / `knowledge_chunks` schema | ⭐⭐⭐⭐ | Good multi-tenant RAG foundation |
| Web search abstraction (`search.py`) | ⭐⭐⭐ | Useful for knowledge augmentation |
| API key auth + rate limiting | ⭐⭐⭐⭐ | Solid B2B auth foundation |
| `FilesystemRAGStore` (FAISS + BM25) | ⭐⭐⭐ | Good development fallback, not for production |

---

### 2.5 Technical Debt

| Debt | Severity | Location |
|---|---|---|
| **History persistence triplicated** — same logic in 3 places | 🔴 High | `query.py`, `chat_repository.py`, `history_repository.py` |
| **Fat routers** — `ingest.py` (~460 lines) and `query.py` do chunking, hashing, embedding, DB writes | 🔴 High | `api/routers/` |
| **`SupabaseHTTPClient` creates new `httpx.AsyncClient` per call** — TCP/TLS exhaustion under load | 🔴 High | `supabase_adapter.py` |
| **`isinstance` check in router** — `if not isinstance(backend, FilesystemRAGStore)` | 🔴 High | `ingest.py` |
| **Disk I/O in retrieval path** — JSONL trace writing during `retrieve()` calls | 🟡 Medium | `rag_backend_router.py` |
| **Streaming fragmented across 4 files** — `streaming.py`, `message_streaming.py`, `query_streaming.py`, `streaming_message_pipeline.py` | 🟡 Medium | `services/messaging/` |
| **`IAuthProvider` protocol defined but never implemented** — dead abstraction | 🟡 Medium | `shared_types/protocols.py` |
| **`pyproject.toml` and `requirements.txt` duplicated** — version drift risk | 🟡 Medium | Root |
| **Intent classification duplicated** — `conversation_intent.py` vs `intent.py` | 🟡 Medium | `services/conversation/` |
| **Technical mode logic split** — `technical_mode.py` vs `inference_technical.py` | 🟡 Medium | `services/inference/` |
| **Global mutable singletons** — `_fs_store`, `_STREAM_CONFIG` without async locks | 🟡 Medium | Multiple |
| **Mock fallbacks silently masquerade as real data** — `_mock_placeholder_paragraphs()` | 🔴 High | `rag_backend_router.py` |

---

### 2.6 Dead Code

- `IAuthProvider` protocol in `shared_types/protocols.py` — unused
- `conversation_intent.py` — duplicates `intent.py`
- `legacy_purge` migration drops B2C tables but `users` table still exists in repo-level migrations with Dodo payments schema
- `hybrid_search_v4` function still partially referenced in migration comments but replaced by v5
- `MASTER-strategy.md` describes an open-source split that never happened

---

### 2.7 Scalability / Maintainability Issues

1. **No ingestion worker** — ingestion queue table exists but no background worker service consuming it. Ingestion is synchronous in-request, blocking.
2. **Redis as sole rate-limit enforcement** — Redis failure opens rate limits entirely. No degraded enforcement fallback.
3. **`knowledge_query_logs` partitioning** — only one partition created (April 2026). Production queries after May 2026 will fail or land in default partition unless partitions are created manually.
4. **No VACUUM/ANALYZE automation** — HNSW indexes degrade without regular maintenance. No scheduled maintenance noted.
5. **Filesystem store is not production-grade** — FAISS + BM25 on local disk cannot scale horizontally and breaks in containerized environments.

---

## 3. SUPABASE ANALYSIS

### 3.1 Schema Inventory

```
Extensions: pgvector, pgcrypto
Auth: Supabase Auth (JWT), supplemented by custom API key table

Core Tables:
├── api_keys              — B2B auth, plans, rate limit config
├── conversations         — Chat sessions (supports both user_id and api_key_id)
├── messages              — Chat turn storage
├── history               — PromptSpec-keyed explanation history
├── knowledge_collections — Multi-tenant RAG namespace root
├── knowledge_documents   — Source documents per collection
├── knowledge_chunks      — Embedding + FTS retrieval units
├── knowledge_ingestion_queue — Async worker queue (consumed by nothing)
├── knowledge_query_logs  — Partitioned search telemetry
└── users                 — B2C billing bridge (Dodo payments)

Key Functions:
├── hybrid_search_v5()           — api-key-scoped hybrid RRF search
├── hybrid_search_trusted_v5()   — unscoped global corpus search
├── get_neighbor_chunks()        — context window expansion
├── dequeue_ingestion_job()      — atomic SKIP LOCKED job claim
├── apply_chunk_embeddings()     — bulk embedding upsert
├── get_embedding_dimension()    — dimension introspection RPC
├── upsert_history()             — merge prompt_specs JSONB
└── insert_message_bundle()      — atomic conversation write
```

---

### 3.2 What Should Remain

| Component | Reason |
|---|---|
| `api_keys` table + RLS policy | Solid B2B auth foundation |
| `knowledge_collections` / `documents` / `chunks` core structure | Well-designed multi-tenant RAG hierarchy |
| `hybrid_search_v5` function | Production-grade — sophisticated RRF implementation |
| `get_neighbor_chunks` | Good context window expansion primitive |
| `apply_chunk_embeddings` / `get_embedding_dimension` RPCs | Useful operational introspection |
| `dequeue_ingestion_job` | Correct pattern — needs a worker to consume it |
| HNSW index on embeddings | Correct indexing strategy |
| Dual TSVECTOR (english + simple) | Smart for mixed content (prose + code) |

---

### 3.3 What Should Be Redesigned

| Issue | Problem | Recommended Fix |
|---|---|---|
| **`conversations.mode CHECK`** constraint | Locks to 3 modes; blocks extensibility | Replace with soft enum (no CHECK) or enum table; validate in application layer |
| **RLS on RAG tables** | `knowledge_*` tables use no RLS — multi-tenancy enforced only by stored procedure `WHERE api_key_id` parameters | Add RLS policies on `knowledge_collections` scoped to `api_key_id` claim in JWT/service context |
| **`knowledge_query_logs` partitioning** | Only 1 partition (April 2026) — not automated | Add partition creation function + cron trigger |
| **Embedding dimension hardcoded to 768** | Cannot switch models without schema migration | Add `embedding_model` column to `knowledge_collections` — allow per-collection model configuration |
| **Single FTS language config** | All chunks in a document share one language config | Support per-chunk `language_config` or auto-detect |
| **`hybrid_search_trusted_v5`** | Bypasses all tenant isolation — searches ALL chunks | Add source namespace parameter; restrict to explicit trusted namespaces |
| **No `tenant_id` or `workspace_id`** | Multi-tenancy is API-key scoped only — no organizational hierarchy | Add `organizations` table with users-many-to-org, api-keys-per-org model |

---

### 3.4 What Can Be Removed

- `user_usage` table — purged in migration 3 but `users` table in repo-level migrations still has B2C billing columns. If pivoting fully to B2B, drop `users.dodo_customer_id`, `dodo_subscription_id`.
- `history` table — conversation history is already in `messages`; `history.prompt_specs` is a legacy B2C learning-app artifact
- `knowledge_query_logs_2026_04` stale partition — should be superseded by automated partition management

---

### 3.5 Migration Strategy

**Phase 1 — Additive (Zero disruption, 1–2 weeks)**
1. Add `organizations` table + FK from `api_keys.organization_id`
2. Add `embedding_model` + `embedding_dimension` columns to `knowledge_collections`
3. Add RLS to `knowledge_collections` (service_role bypass)
4. Create partition management function for `knowledge_query_logs`
5. Remove `mode` CHECK constraint from `conversations` (replace with soft validation)

**Phase 2 — Refactoring (Requires ingestion re-run, 2–4 weeks)**
1. Add `namespace` column to `knowledge_collections` for OKF concept graph isolation
2. Add `content_type` column to `knowledge_documents` (markdown, pdf, code, url, etc.)
3. Add `okf_concept_id` column to `knowledge_chunks` for OKF graph linking
4. Create `okf_concepts` table for knowledge graph nodes
5. Create `okf_edges` table for concept relationships

**Phase 3 — Breaking (Major version, 4–8 weeks)**
1. Remove `history` table if fully deprecated
2. Consolidate `hybrid_search_v5` + `hybrid_search_trusted_v5` into single parameterized function
3. Migrate conversation `mode` to extensible `context_type` enum table

---

## 4. STRATEGIC PIVOT ASSESSMENT

### 4.1 Target Vision: General-Purpose RAG + OKF Knowledge Engine

Evaluating how the existing architecture supports a **personal and enterprise knowledge platform** with OKF (Open Knowledge Framework) as a first-class organizational primitive.

| Capability | Current State | Gap | Severity |
|---|---|---|---|
| **Multi-source ingestion** | In-request only; no pipeline; dataset-specific scripts exist externally | No generic document pipeline; no connector architecture | 🔴 Major |
| **Generic document pipelines** | `ENTERPRISE_RAG_PIPELINE_PLAN.md` describes a complete design; none implemented | Plugin-based pipeline (YAML sources, middleware, chunkers) entirely absent | 🔴 Major |
| **Personal vs enterprise isolation** | API key scopes collections — rudimentary | No org-level hierarchy; no personal namespace concept | 🟡 Moderate |
| **Multi-tenancy** | `api_key_id` scoped collections; no RLS on RAG tables | No true DB-level tenant isolation; SQL bypass via trusted search | 🔴 Major |
| **Search architecture** | Excellent — dual-sparse + dense RRF, neighbor expansion, Jaccard diversity | No OKF concept graph layer; no depth-routing to graph vs vector | 🔴 Major |
| **Embeddings** | 768-dim Gemini text-embedding-004; single model hardcoded | No per-collection model routing; no multi-modal embedding | 🟡 Moderate |
| **Retrieval** | Hybrid pgvector + BM25 working well | No graph traversal; no hierarchical retrieval; no OKF depth routing | 🔴 Major |
| **Knowledge graph potential** | Zero — not implemented | Entire OKF concept graph layer missing | 🔴 Major |
| **Agent integration** | MCP server described in `DEPTHAPI_PRODUCT_ARCHITECTURE_OKF_HERMES.md` | Not implemented; FastAPI endpoints not MCP-wrapped | 🔴 Major |
| **Extensibility** | Plugin-based ingestion planned but absent; LLM providers are pluggable | RAG backends pluggable (filesystem/pgvector); embedding not extensible | 🟡 Moderate |
| **Security** | API key auth solid; SHA-256 hash; Redis cache; plan-tier enforcement | No org-level RBAC; no token-level permissions; no audit log | 🟡 Moderate |
| **Permissions** | Binary: has-key or not | No resource-level permissions; no read-only vs read-write distinction | 🟡 Moderate |
| **Long-term scalability** | pgvector with HNSW scales to ~10M chunks without reengineering | No partition automation; no connection pooling; no worker service | 🟡 Moderate |

---

### 4.2 Where Current Architecture Fights the Pivot

1. **Hardcoded modes poison the API contract.** The `learn/technical/socratic` split is baked into DB CHECK constraints, router logic, prompts, and config. A general knowledge engine needs context types that are domain-driven by the collection, not hardcoded globally.

2. **No ingestion abstraction.** Every document source requires custom code. `ENTERPRISE_RAG_PIPELINE_PLAN.md` was the right design — nothing from it was built. Building generic ingestion is the single largest missing capability.

3. **The trusted corpus bypass is architecturally dangerous.** `hybrid_search_trusted_v5` searches all chunks with no tenant scoping. In a multi-tenant knowledge engine, this is a data isolation violation waiting to happen.

4. **OKF is a concept without an implementation.** The `DEPTHAPI_PRODUCT_ARCHITECTURE_OKF_HERMES.md` document describes OKF correctly — concept graph + vector dual-index, depth-routing between them — but none of it exists. The "OKF" is currently a marketing term with no code representation.

5. **Conversation history is Redis-first, Supabase-second, with no retrieval guarantee.** For a personal knowledge engine, conversation context should be a first-class retrieval source, not an afterthought SSE persistence side-effect.

6. **The mock fallback is a liability.** `_mock_placeholder_paragraphs()` returns fake knowledge content when retrieval is empty. In a knowledge accuracy context, this is actively harmful. It should be removed entirely.

---

## 5. GAP ANALYSIS

### Current State vs Target Vision

```
CURRENT STATE                          TARGET VISION
─────────────────────────────────────  ─────────────────────────────────────
Dataset-specific ingestion scripts     Generic declarative ingestion pipeline
Single Gemini embedding model          Per-collection embedding model routing
Flat chunk retrieval only              OKF concept graph + vector dual-index
3 hardcoded interaction modes          Extensible context types per collection
API-key tenant isolation (no RLS)      Org-level RBAC + DB-level RLS isolation
No MCP server                          Full MCP server (Hermes/Claude native)
No knowledge graph                     OKF concept graph with traversal
No agent integration                   Agent-native with tool definitions
Synchronous in-request ingestion       Async worker pipeline with queue
No personal namespace concept          Personal + shared + enterprise namespaces
Mock data fallback in retrieval        Honest "no results" with reasoning
No partition automation                Automated query log partition management
```

---

### 5.1 Major Architectural Gaps

1. **Generic Document Ingestion Pipeline** — The most critical missing piece. No YAML-declarative source connectors, no middleware chain, no chunker abstraction.

2. **OKF Concept Graph Layer** — `okf_concepts` table, `okf_edges` table, OKF builder (LLM pass during ingestion), depth router (concept graph vs vector selection).

3. **Organizational Hierarchy** — `organizations` → `members` → `api_keys` → `collections`. Currently flat.

4. **MCP Server** — Not a wrapper around existing endpoints but a proper MCP protocol server with `depthapi_ingest`, `depthapi_query`, `depthapi_list_collections` tools.

5. **Ingestion Worker** — The `knowledge_ingestion_queue` table exists with the right schema but there is no consumer service.

6. **True Multi-Tenancy RLS** — Database-level row isolation for `knowledge_collections` and `knowledge_chunks`.

---

### 5.2 Missing Capabilities

- Document connector registry (HTTP, filesystem, GitHub, Notion, S3, Confluence)
- Per-collection LLM prompt template configuration
- Knowledge versioning and document update detection
- OKF concept graph traversal and depth-routing
- Namespace-level search scoping
- Personal knowledge spaces (user-owned collections isolated from enterprise)
- Audit logging for all knowledge access
- MCP server implementation
- Automated partition lifecycle management
- Real-time ingestion status webhooks

---

### 5.3 Features Worth Preserving

| Feature | Reason |
|---|---|
| Multi-provider LLM with circuit breaking | Production-grade; rare to build this well |
| Hybrid pgvector + BM25 + RRF | Excellent retrieval quality |
| SSE streaming with idempotency | Correct engineering for streaming AI |
| Redis-backed rate limiting + quota | Real B2B necessity |
| API key auth model | Right for headless B2B |
| `SupabaseHTTPClient` (after connection pooling fix) | Lightweight, avoids SDK overhead |
| `FilesystemRAGStore` as development fallback | Good for offline/local-first use |
| `knowledge_ingestion_queue` pattern | Correct async worker model |

---

### 5.4 Features to Remove

| Feature | Reason |
|---|---|
| `_mock_placeholder_paragraphs()` and `_mock_source_catalog()` | Silent data fabrication is dangerous in a knowledge system |
| `hybrid_search_trusted_v5` in its current form | Unscoped global search violates multi-tenancy |
| Direct database calls in routers | Couples HTTP layer to persistence |
| `history` table (long-term) | Superseded by proper conversation + collection architecture |
| `user_usage` table / Dodo payments columns | B2C artifacts; clean break needed |
| Disk I/O trace writing in retrieval path | Breaks serverless; should be structured logs |
| Duplicated streaming/intent/mode files | Consolidation overdue |

---

### 5.5 Risks During Migration

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Breaking existing API consumers during mode enum change | Medium | High | Version the API (`/v1` → `/v2`); keep old endpoints |
| RLS policy addition causes query plan regression | Medium | High | Test with `EXPLAIN ANALYZE` before applying |
| Embedding model change requires full re-ingestion | High | High | Run new model in parallel; dual-index during migration |
| Partition gap in `knowledge_query_logs` (post April 2026) | High | Medium | Add partition management immediately |
| Mock fallback removal breaks dev/demo environments | Medium | Low | Provide honest "empty retrieval" response instead |
| Ingestion worker consuming existing queue items | Low | Medium | Clear queue or version queue items before worker deploy |

---

## 6. ROADMAP

### Immediate (Next 1–2 Weeks)

**Priority: Stop the bleeding. Fix the debt that blocks everything else.**

1. **Fix `SupabaseHTTPClient` connection pooling** — shared `httpx.AsyncClient` singleton; highest production risk
2. **Remove mock data fallbacks** — `_mock_placeholder_paragraphs()` and `_mock_source_catalog()` replaced with honest empty results + logging
3. **Create `knowledge_query_logs` partition automation** — add `create_query_log_partition(month)` SQL function + cron
4. **Consolidate streaming files** — merge 4 streaming module variants into 2 (query vs messages)
5. **Consolidate history persistence** — single `HistoryRepository.upsert_history()` call site; deprecate duplicates in `query.py` and `ChatRepository`
6. **Fix `isinstance` check in `ingest.py`** — introduce `IngestionService.ingest_document()` abstraction
7. **Write `DATA_MODEL.md`** — ERD + current schema documentation; blocks all future DB work
8. **Strip mode CHECK constraint** — replace `CHECK(mode IN (...))` with application-layer validation; enables extensibility
9. **Write `DEPLOYMENT.md`** and `LOCAL_SUPABASE_SETUP.md` validation — ensure onboarding works

---

### Medium Term (4–8 Weeks)

**Priority: Build the foundations the pivot requires.**

1. **Generic Ingestion Pipeline** — Implement the plugin-based pipeline from `ENTERPRISE_RAG_PIPELINE_PLAN.md`:
   - `SourceConnector` interface (HTTP URL, local filesystem, GitHub, Notion)
   - Immutable Pydantic `Document` → `ParsedDocument` → `Chunk` contracts
   - Middleware chain (PII filter, TOC stripper, deduplication)
   - YAML-declarative source configuration

2. **Ingestion Worker Service** — Build the background worker that consumes `knowledge_ingestion_queue`
   - `dequeue_ingestion_job()` consumer loop
   - Status webhooks/callbacks
   - Retry + dead-letter handling

3. **Organization Hierarchy** — Add `organizations` → `api_keys` → `collections` model
   - `organizations` table with `created_at`, `plan`, `metadata`
   - `organization_members` with RBAC roles
   - RLS on `knowledge_collections` keyed to org membership

4. **OKF Schema Foundation** — Add to Supabase:
   - `okf_concepts` table (`id`, `collection_id`, `title`, `summary`, `level`, `metadata`)
   - `okf_edges` table (`source_id`, `target_id`, `relationship_type`)
   - `okf_concept_id` FK on `knowledge_chunks`

5. **Per-Collection Embedding Model** — Add `embedding_model` + `embedding_dimension` to `knowledge_collections`; update `embeddings.py` to route per-collection

6. **MCP Server Skeleton** — Implement `depthapi-mcp` server with:
   - `depthapi_ingest(source_uri, collection_id)` tool
   - `depthapi_query(query, collection_id, depth)` tool
   - `depthapi_list_collections()` tool

7. **Audit Log** — `knowledge_access_log` table for all retrieval events (tenant, query_hash, timestamp, result_count)

---

### Long Term (8–24 Weeks)

**Priority: Transform into a production-grade personal/enterprise knowledge platform.**

1. **OKF Builder** — LLM pass during ingestion that synthesizes `index.md` + concept files:
   - Async background pass after raw chunk ingestion completes
   - Generates `okf_concepts` records per document
   - Extracts relationships between concepts into `okf_edges`

2. **OKF Depth Router** — `depth_router.py`:
   - Depth 1–2: OKF concept graph only (concept summaries, no chunk retrieval)
   - Depth 3–4: Concept graph narrows search space → hybrid vector search within concept scope
   - Depth 5: Full graph traversal + multi-hop vector retrieval + reranking

3. **Knowledge Graph API** — REST + MCP endpoints:
   - `GET /api/v1/collections/{id}/graph` — concept graph as JSON/GraphML
   - `GET /api/v1/concepts/{id}` — concept detail with relationships
   - Graph traversal queries

4. **Personal Knowledge Spaces** — User-owned private collections:
   - Personal namespace scoped to `user_id` not `api_key_id`
   - Personal ↔ shared knowledge federation queries

5. **Multi-Modal Ingestion** — Beyond text:
   - PDF extraction with layout preservation
   - Image/diagram ingestion (vision embeddings)
   - Code repository ingestion with AST-aware chunking
   - Structured data (CSV, JSON) ingestion

6. **Agent Integration Framework** — Native tool use:
   - Full MCP server implementation
   - OpenAI function calling compatible tool definitions
   - Claude tool use integration
   - Webhook triggers for ingestion events

7. **Knowledge Observability Dashboard**:
   - Collection health metrics (chunk count, staleness, coverage)
   - Query analytics (top queries, zero-result rates, latency distribution)
   - Concept graph visualization

8. **Enterprise Features**:
   - SSO integration (SAML/OIDC via Supabase Auth)
   - Data residency configuration (regional Supabase instances)
   - Compliance audit export (SOC2-aligned access logs)
   - BYODB support (customer-owned pgvector instance)

---

## 7. FINAL VERDICT

### Project Maturity: **Early Production (Phase 2 of 5)**

The core LLM infrastructure (streaming, fallback, circuit-breaking, rate limiting) is genuinely production-grade. The RAG schema and hybrid search are well-engineered. The project has moved past prototype quality. However, it has not yet achieved the coherence of a platform — it is a collection of capable components without a clear runtime composition story.

---

### Is the Pivot Advisable?

**Yes, unambiguously.** The dataset-centric model was always a means, not an end. The real moat was always the retrieval quality and the streaming inference infrastructure. A general-purpose RAG + OKF engine is a significantly larger market and leverages exactly what was built well. The pivot does not require throwing away the codebase — it requires building the abstraction layers above the existing foundations.

The risk is not the pivot itself. The risk is **premature feature addition without debt resolution**. If new OKF features are built on top of the current fat routers, duplicated persistence, and mock-data fallbacks, the debt compounds rather than resolves.

---

### Biggest Architectural Mistakes

1. **Mock data in production retrieval path.** `_mock_placeholder_paragraphs()` and `_mock_source_catalog()` returning fabricated content silently. This is the most dangerous technical decision in the codebase — a knowledge engine that can lie about its sources without any signal.

2. **Dataset-specific assumptions baked into 4 layers simultaneously** (config, code, schema, documentation). Each hardcoded assumption will require a coordinated 4-layer change to remove.

3. **No ingestion abstraction whatsoever.** The `ENTERPRISE_RAG_PIPELINE_PLAN.md` was written and then completely ignored. The ingestion queue table was created but never consumed. This is the biggest missing capability gap.

4. **The `depthapi_architecture.md` describes a different system than the code.** Multiple documents confidently describe Turso, OKF builders, and depth routers that do not exist. This causes real confusion about what is built vs what is planned.

5. **Connection creation per request in `SupabaseHTTPClient`.** Every database call opens a new TLS connection. Under any meaningful load this will be the first performance cliff hit.

---

### Strongest Existing Foundations

1. **The hybrid search SQL (`hybrid_search_v5`)** — This is the best piece of engineering in the entire project. Dual-tsvector (english + simple) for prose and code, dynamic RRF k-weighting per query mode, correct use of `SKIP LOCKED` for the worker queue. This is production-grade PostgreSQL.

2. **The multi-provider LLM client with circuit breaking** — Rare to see this done correctly. Native `AsyncOpenAI` per provider, no LiteLLM wrapper, real circuit-breaker semantics, provider state tracking in Redis with in-memory fallback.

3. **The streaming pipeline** — The `StreamEventLoop` with idempotency, response caching, fallback generation, and clean SSE event protocol is well-engineered. The complexity is justified by the correctness guarantees it provides.

4. **The multi-tenant schema foundations** — `knowledge_collections → knowledge_documents → knowledge_chunks` with proper soft deletes, `api_key_id` scoping, and HNSW indexing. This is the right foundation to build OKF on top of.

---

### The Single Most Valuable Next Step

> **Remove the mock data fallbacks and write honest retrieval diagnostics.**

Before any new features are built, `_mock_placeholder_paragraphs()` and `_mock_source_catalog()` must be deleted. Replace them with structured log events and honest `"retrieval_empty": true` response metadata. Then, fix the `SupabaseHTTPClient` connection pooling.

These two changes cost less than 4 hours of engineering and will immediately improve production reliability, remove the data integrity risk, and establish the honest retrieval semantics that a knowledge engine requires as its foundational contract.

Everything else — OKF graphs, ingestion pipelines, MCP servers — must be built on top of a system that tells the truth about what it knows. Right now, it sometimes doesn't.

---

*End of Audit Report — DepthAPI Comprehensive Assessment, August 2026*
