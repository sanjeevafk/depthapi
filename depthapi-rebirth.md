# depthapi-rebirth.md
> **Goal:** Strip Supabase platform, remove learn/socratic modes + prompt_engine, remove Dodo/B2C code. Transition to local-first Docker postgres+pgvector.
> **Precursor to:** `depthapi-pivot.md` (OKF knowledge engine)
> **Branch:** `git checkout -b rebirth`

---

## Done When
- [ ] `docker compose up` starts postgres + redis, app connects, `/api/health` returns 200
- [ ] `POST /api/query` returns a real RAG response with no mode param required
- [ ] `POST /api/ingest` stores a document into local postgres pgvector
- [ ] Zero refs to `supabase_url`, `SUPABASE_SECRET_KEY`, `LEARNING_MODE`, `SOCRATIC_MODE`, `dodo_customer_id` in `api/`
- [ ] `grep -r "mock_placeholder\|_mock_source_catalog\|build_mock_contexts" api/` returns empty

---

## Phase 1 — Snapshot & Branch

- [ ] T1.1: `git checkout -b rebirth` → Verify: `git branch` shows `* rebirth`
- [ ] T1.2: Run baseline: `pytest api/tests -x -q 2>&1 | tail -5` → Record pass/fail count as baseline

---

## Phase 2 — Delete Dead Directories & Files

**Rule:** Delete entirely. No edits. If listed here, it's gone.

- [ ] T2.1: Delete prompt engine and configs
  ```bash
  rm -rf api/prompt_engine api/prompt_configs
  ```
  → Verify: `ls api/` shows neither directory

- [ ] T2.2: Delete conversation service layer
  ```bash
  rm -rf api/services/conversation
  ```
  → Verify: `ls api/services/` has no `conversation/`

- [ ] T2.3: Delete dead routers
  ```bash
  rm -f api/routers/demo.py api/routers/export.py api/routers/messages.py
  ```
  → Verify: `ls api/routers/` shows only `query.py`, `ingest.py`, `__init__.py`

- [ ] T2.4: Delete B2C repositories and all repo-level migrations
  ```bash
  rm -f api/repositories/chat_repository.py api/repositories/history_repository.py
  rm -rf api/repositories/migrations
  ```
  → Verify: `ls api/repositories/` shows only `__init__.py`

- [ ] T2.5: Delete Supabase-specific messaging persistence files
  ```bash
  rm -f api/services/messaging/message_persistence.py \
        api/services/messaging/message_persistence_manager.py \
        api/services/messaging/stream_persistence.py \
        api/services/messaging/streaming_message_pipeline.py \
        api/services/messaging/message_workflow.py \
        api/services/messaging/stream_event_finalize.py
  ```
  → Verify: `ls api/services/messaging/ | wc -l` is ≤ 17

---

## Phase 3 — Remove Mock Data Fallbacks

**File:** `api/services/rag/rag_backend_router.py`

- [ ] T3.1: Delete functions `_mock_source_catalog`, `_mock_placeholder_paragraphs`, `_build_mock_contexts` entirely
  → Verify: `grep -n "mock" api/services/rag/rag_backend_router.py` returns empty

- [ ] T3.2: Replace every callsite of `_build_mock_contexts(...)` with:
  ```python
  logger.warning("retrieval_empty", query_mode=query_mode, backend=backend_name)
  return []
  ```
  → Verify: `python -c "from api.services.rag.rag_backend_router import get_rag_backend"` imports cleanly

---

## Phase 4 — Replace Supabase Adapter with asyncpg Pool

> ⚠️ **Highest-risk phase. Complete fully before Phase 5. Auth depends on this.**

- [ ] T4.1: Add `asyncpg>=0.29.0` to `api/requirements.txt` and `pyproject.toml [project.dependencies]`
  → Verify: `pip install asyncpg` succeeds (or already installed)

- [ ] T4.2: Create `api/adapters/pg_adapter.py` with:
  - Module-level `_pool: asyncpg.Pool | None = None` singleton
  - `async def init_pool(dsn: str) -> None` — creates pool at startup
  - `async def close_pool() -> None` — closes pool at shutdown
  - `def get_pool() -> asyncpg.Pool` — returns pool or raises RuntimeError if not initialised
  - `async def execute_rpc(fn_name: str, params: dict) -> list[dict]` — `SELECT * FROM fn(params)`
  - `async def fetch_one(table: str, where: dict) -> dict | None` — single row SELECT with WHERE
  → Verify: `python -c "from api.adapters.pg_adapter import init_pool, execute_rpc, fetch_one"` imports cleanly

- [ ] T4.3: Add `database_url: str` to `api/config.py` Settings. Default: `"postgresql://depthapi:depthapi@localhost:5432/depthapi"`
  Remove: `supabase_url`, `supabase_publishable_key`, `supabase_secret_key`, `local_pgvector_url`, `local_pgvector_secret_key`, `upstash_redis_rest_url`, `upstash_redis_rest_token`, `vercel_function_max_duration_seconds`
  → Verify: `python -c "from api.config import get_settings; print(get_settings().database_url)"`

- [ ] T4.4: Rewrite `api/auth.py` to export `get_pg_pool()` (wraps `pg_adapter.get_pool()`) instead of `get_supabase_admin()`
  → Verify: `python -c "from api.auth import get_pg_pool"` imports cleanly

- [ ] T4.5: Rewrite `_lookup_in_db()` in `api/services/security/api_key_auth.py`:
  - Remove `from api.auth import get_supabase_admin` import
  - Use `from api.adapters.pg_adapter import fetch_one`
  - Body: `row = await fetch_one("api_keys", {"key_hash": key_hash, "is_active": True})`; build `ApiKeyRecord` from row
  → Verify: `python -c "from api.services.security.api_key_auth import verify_api_key"` imports cleanly

- [ ] T4.6: Rewrite `api/services/rag/knowledge_retrieval.py`:
  - Remove `get_supabase_admin` + `SupabaseHTTPClient` imports
  - Delete `get_trusted_corpus_admin()` function
  - Replace `supabase.rpc("hybrid_search_v5", params).execute()` → `pg_adapter.execute_rpc("hybrid_search_v5", params)`
  - Replace `supabase.rpc("get_neighbor_chunks", params).execute()` → `pg_adapter.execute_rpc("get_neighbor_chunks", params)`
  → Verify: `python -c "from api.services.rag.knowledge_retrieval import RetrievalService"` imports cleanly

- [ ] T4.7: Delete `api/adapters/supabase_adapter.py`
  ```bash
  rm api/adapters/supabase_adapter.py
  ```
  → Verify: `grep -r "supabase_adapter" api/ --include="*.py"` returns empty

---

## Phase 5 — Flatten the Query Layer

- [ ] T5.1: Rewrite `api/routers/query.py`:

  **Delete:** `PromptSpecRequest` import, `mode` field, `_history_prompt_specs()`, `save_to_history()`, `upsert_history` RPC, multi-level `explanations: dict[str,str]` loop

  **New request/response models:**
  ```python
  class QueryRequest(BaseModel):
      query: str = Field(..., min_length=1, max_length=8000)
      collection_id: str | None = None
      use_trusted_corpus: bool = True
      bypass_cache: bool = False
      temperature: float = Field(default=0.7, ge=0.0, le=2.0)

  class QueryResponse(BaseModel):
      answer: str
      contexts: list[dict]
      citations: list[dict]
      cached: bool = False
      metadata: dict = {}
  ```

  **Keep:** Redis idempotency, RAG retrieval call, LLM generation, SSE streaming endpoint
  → Verify: `python -c "from api.routers.query import router"` imports cleanly

- [ ] T5.2: Trim `api/utils.py` — delete all mode/depth symbols:
  `LEARNING_MODE`, `SOCRATIC_MODE`, `TECHNICAL_MODE`, `MODE_ALIASES`, `CHAT_MODE_ALIASES`, `SUPPORTED_CHAT_MODES`, `DEFAULT_CHAT_MODE`, `normalize_mode()`, `_load_chat_modes()`, `CHAT_MODES`, `PROMPT_DEPTHS`, `PROMPT_LEVELS`, `canonical_prompt_depth()`, `normalize_prompt_level()`, `DEPTH_REQUEST_PATTERNS`, `requests_depth()`, `topic_cache_key()`
  Rename `sanitize_topic()` → `sanitize_query()`. Keep `sanitize_filename()`, `escape_for_prompt()`, `with_timeout()`
  → Verify: `python -c "from api.utils import sanitize_query, with_timeout"` imports cleanly

- [ ] T5.3: Trim `api/config.py` `StreamConfig` dataclass — remove `stream_max_seconds_learning`, and all `*_learning`, `*_socratic` variant fields. Unify to single `stream_max_seconds`
  → Verify: `python -c "from api.config import StreamConfig"` imports cleanly

- [ ] T5.4: Trim `api/services/inference/inference_routing.py` — delete `learning-primary` and `socratic-primary` alias chains; route everything to `technical-primary`
  → Verify: `python -c "from api.services.inference.inference_routing import route_model_aliases"` imports cleanly

- [ ] T5.5: Trim `api/services/inference/inference.py` — delete `generate_explanation()` multi-level fn and mode-branching; expose single `generate_response(query, contexts, temperature)` and `generate_stream_response(...)`
  → Verify: `python -c "from api.services.inference.inference import generate_response"` imports cleanly

---

## Phase 6 — Strip Supabase from Messaging Layer

- [ ] T6.1: In `api/services/messaging/stream_event_loop.py`:
  - Remove `stream_persistence` import
  - Remove all `persist_to_supabase(...)` fire-and-forget calls
  → Verify: `grep -n "supabase\|stream_persistence\|StreamPersist" api/services/messaging/stream_event_loop.py` returns empty

- [ ] T6.2: In `api/services/messaging/stream_helpers.py`:
  - Remove any `finalize_assistant_message()` call that writes to Supabase
  → Verify: `python -c "from api.services.messaging.stream_helpers import drain_stream_chunks"` imports cleanly

- [ ] T6.3: Final scan of messaging layer:
  ```bash
  grep -r "supabase" api/services/messaging/ --include="*.py"
  ```
  → Must return empty

---

## Phase 7 — New Database Infrastructure

- [ ] T7.1: Create directory structure: `mkdir -p db/migrations db/seed`

- [ ] T7.2: Create `db/migrations/001_schema.sql` — clean consolidated schema:
  - `CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;`
  - Tables: `api_keys`, `knowledge_collections`, `knowledge_documents`, `knowledge_chunks`, `knowledge_ingestion_queue`, `knowledge_query_logs` (partitioned)
  - Partition helper function: `create_query_log_partition(year int, month int)`
  - Indexes: HNSW on `knowledge_chunks.embedding`, dual GIN on `fts_tokens`+`fts_tokens_simple`, GIN on `metadata`
  - Functions: `hybrid_search_v5`, `hybrid_search_trusted_v5`, `get_neighbor_chunks`, `apply_chunk_embeddings`, `get_embedding_dimension`, `dequeue_ingestion_job`
  - Triggers: `update_chunk_fts_tokens()` auto-populating both tsvector columns
  - **Excluded:** `conversations`, `messages`, `history`, `user_usage`, `users` — all gone
  → Verify: `wc -l db/migrations/001_schema.sql` > 200

- [ ] T7.3: Create `db/seed/001_dev_api_key.sql` — insert dev API key `sk-depth-dev-local-0000000000000000` with `plan='enterprise'`, `monthly_token_budget=0`
  → Verify: file exists

- [ ] T7.4: Rewrite `docker-compose.yml`:
  ```yaml
  services:
    postgres:
      image: pgvector/pgvector:pg17
      environment:
        POSTGRES_DB: depthapi
        POSTGRES_USER: depthapi
        POSTGRES_PASSWORD: depthapi
      ports: ["5432:5432"]
      volumes:
        - pg_data:/var/lib/postgresql/data
        - ./db/migrations:/docker-entrypoint-initdb.d/migrations:ro
        - ./db/seed:/docker-entrypoint-initdb.d/seed:ro
      healthcheck:
        test: ["CMD-SHELL", "pg_isready -U depthapi"]
        interval: 5s
        timeout: 5s
        retries: 5
    redis:
      image: redis:7-alpine
      ports: ["6379:6379"]
      healthcheck:
        test: ["CMD", "redis-cli", "ping"]
        interval: 5s
        retries: 5
  volumes:
    pg_data:
  ```
  → Verify: `docker compose config` produces no errors

- [ ] T7.5: Archive old Supabase directory:
  ```bash
  mkdir -p archive
  mv supabase archive/supabase-platform
  ```
  → Verify: `ls archive/` shows `supabase-platform/`, `ls supabase 2>&1` shows "No such file or directory"

---

## Phase 8 — Wire Startup Lifecycle in `main.py`

- [ ] T8.1: In `api/main.py`:
  - Remove `from api.routers import demo, export` and their `include_router` calls
  - In `lifespan()` startup block: add `await init_pool(get_settings().database_url)`
  - In `lifespan()` shutdown block: add `await close_pool()`
  - Import: `from api.adapters.pg_adapter import init_pool, close_pool`
  → Verify: `python -c "from api.main import app"` imports cleanly

- [ ] T8.2: Confirm exactly 2 routers remain:
  ```bash
  grep "include_router" api/main.py
  ```
  → Must show only `query.router` and `ingest.router`

---

## Phase 9 — Clean Developer Surface

- [ ] T9.1: Rewrite `.env.example` (remove all SUPABASE_*, DODO_*, UPSTASH_*, add DATABASE_URL)
  → Verify: `grep -c "SUPABASE\|DODO\|UPSTASH\|VERCEL" .env.example` returns 0

- [ ] T9.2: Create `Makefile` with targets: `up`, `down`, `reset`, `shell`, `logs`, `dev`
  → Verify: `make --dry-run up` shows `docker compose up -d`

- [ ] T9.3: Update `README.md` quick-start — 3-step: copy .env, `make up`, `make dev`. Add minimal curl example for `/api/query`
  → Verify: `grep -c "Supabase\|learn mode\|socratic\|PromptSpec\|dodo" README.md` returns 0

---

## Phase 10 — Final Verification

- [ ] T10.1: `grep -r "supabase_url\|SUPABASE_SECRET\|get_supabase_admin\|supabase_adapter\|SupabaseHTTP" api/ --include="*.py"` → empty

- [ ] T10.2: `grep -r "LEARNING_MODE\|SOCRATIC_MODE\|dodo_customer\|user_usage\|prompt_engine\|PromptSpecRequest" api/ --include="*.py"` → empty

- [ ] T10.3: `grep -r "mock_placeholder\|_mock_source_catalog\|build_mock_contexts" api/ --include="*.py"` → empty

- [ ] T10.4: Start stack and health check:
  ```bash
  make up && sleep 8 && curl -s http://localhost:8000/api/health | python -m json.tool
  ```
  → `"status": "ok"` in response

- [ ] T10.5: Run tests (delete test files for deleted modules first):
  ```bash
  pytest api/tests -x -q 2>&1 | tail -10
  ```
  → Pass count ≥ adjusted baseline from T1.2

- [ ] T10.6: Commit:
  ```bash
  git add -A
  git commit -m "refactor: rebirth — strip Supabase, modes, B2C, mock data

  - Replace Supabase platform with Docker postgres+pgvector (asyncpg pool)
  - Remove learn/socratic modes, PromptSpec, prompt_engine, prompt_configs
  - Remove Dodo payments, user_usage, B2C conversation persistence
  - Delete mock data fallbacks (_mock_placeholder_paragraphs, _build_mock_contexts)
  - Flatten query router to single endpoint (no mode param)
  - Add db/migrations/001_schema.sql clean consolidated schema
  - Add docker-compose.yml with pgvector/pgvector:pg17 + redis:7-alpine
  - Archive supabase/ → archive/supabase-platform/

  Precursor to: depthapi-pivot.md (OKF knowledge engine)"
  ```

---

## Notes

- **Phase 4 must complete before Phase 5.** Auth depends on pg_adapter being wired first.
- **`ingest.py` `isinstance` anti-pattern is deferred.** It still works post-rebirth. Fix is Phase 1 of pivot plan.
- **Delete test files for deleted modules** before running T10.5 or you'll see import errors, not real failures.
- **`FilesystemRAGStore` survives.** Zero-config local dev still works without Docker. pgvector is now the production path.
- **`api/shared_types/prompt.py` and `PromptSpec`** — check if `ingest.py` or any surviving file still imports from `shared_types`. If not, delete `shared_types/prompt.py` in T2.x.
