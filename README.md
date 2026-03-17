# KnowBear – Layered AI Knowledge Engine

**KnowBear** is an AI-powered tool that delivers explanations at **exactly the right depth** for any topic — from ELI5 (explain like I'm 5) to technical deep-dives, meme-style breakdowns, structured reasoning, and more.

It intelligently routes queries across multiple frontier models via a LiteLLM proxy, applies an ensemble judge in Learning mode, caches frequent requests, and offers clean exports — all wrapped in a minimalist, space-themed dark UI.

Live demo: https://knowbear.vercel.app

## ✨ Core Features

- **Layered explanation system** — switch between 5–7 distinct explanation styles  
  - ELI5 / ELI10 / ELI15  
  - Meme & analogy heavy
  - Structured academic style  
  - Technical deep-dive (math, proofs, code)  
  - First-principles reasoning  
- **Mode-aware routing (LiteLLM aliases)**  
  - Learning: two candidates + judge (`learning-candidate-1`, `learning-candidate-2`, judged by `judge`)  
  - Technical: `technical-primary` with fallbacks to `technical-fallback` then `default-fast`  
  - Socratic: `socratic`  
  - Default: `default-fast`  
- **Ultra-fast repeat queries** via Upstash Redis REST caching  
- **Export formats**: .txt, .md  
- **Pinned & trending topics** — discoverability without search  
- **Authentication & Pro tier** (optional, gated features)  
- **Dark-only, space/minimalist UI** with smooth Framer Motion animations  

## 🏗 Architecture Overview

```
KnowBear monorepo
├── api/                      # FastAPI backend ── serverless-ready
│   ├── main.py               # uvicorn entrypoint
│   ├── routers/              # FastAPI APIRouter modules
│   │   ├── query.py
│   │   ├── export.py
│   │   ├── pinned.py
│   │   └── health.py
│   ├── services/
│   │   ├── inference.py      # LiteLLM alias routing + streaming
│   │   ├── cache.py          # Redis abstraction
│   │   ├── auth.py           # Supabase / JWT verification
│   │   └── rate_limit.py     # per-user / global limits
│   └── schemas/              # Pydantic models
├── infra/litellm/            # LiteLLM proxy config + deployment assets
├── src/                      # React + Vite frontend
│   ├── components/           # atomic → molecule → organism
│   ├── pages/                # route-based pages
│   ├── hooks/                # useQuery, useModelRouter, etc.
│   ├── lib/                  # utils, constants, api client
│   └── styles/               # tailwind + global css
├── public/                   # static files, favicon, manifest
├── tests/                    # pytest (backend) + vitest (frontend) — expanding
├── .github/workflows/        # CI (lint, test, deploy preview)
├── vercel.json               # monorepo build config for Vercel
└── README.md
```

## 🤖 Model Routing (LiteLLM)

All model calls go through a LiteLLM proxy that exposes stable aliases. The backend only references aliases; actual provider models are configured in `infra/litellm/config.yaml`.

| Alias | Provider model | Purpose |
|------|----------------|---------|
| `learning-candidate-1` | `groq/llama-3.1-8b-instant` | Learning ensemble candidate |
| `learning-candidate-2` | `groq/openai/gpt-oss-20b` | Learning ensemble candidate |
| `judge` | `openrouter/z-ai/glm-4.5-air:free` | Judge for Learning mode |
| `technical-primary` | `gemini/gemini-2.5-pro` | Technical mode primary |
| `technical-fallback` | `openrouter/qwen/qwen3-coder:free` | Technical fallback |
| `socratic` | `groq/openai/gpt-oss-120b` | Socratic mode |
| `default-fast` | `groq/llama-3.1-8b-instant` | Default fast responses |

`technical-primary` falls back to `technical-fallback` and then `default-fast` via LiteLLM routing rules.

## 🚀 API Endpoints (public)

| Method | Path                | Description                                    | Auth? | Rate-limited? |
|--------|---------------------|------------------------------------------------|-------|---------------|
| GET    | `/api/health`       | Dependency status (`ok|degraded|down`)         | No    | No            |
| GET    | `/api/pinned`       | Curated & trending topics                      | No    | Light         |
| POST   | `/api/query`        | Main query endpoint — returns layered output   | Optional | Yes        |
| POST   | `/api/export`       | Convert result to file (txt/md)                | No    | Yes           |
| GET    | `/api/usage`        | Current user quota & usage (Pro users)         | Yes   | No            |

## Streaming Limits

- SSE heartbeat sent at least every 2s to keep connections alive.
- Streaming responses are capped at 25s and emit a graceful cutoff message on timeout.
- If streaming cannot start within the startup timeout, the backend falls back to non-streaming output.
- Partial responses are treated as final; retries are user-triggered and idempotent by message id.

## Degraded Mode (LiteLLM)

- On startup, the backend validates LiteLLM config (`LITELLM_BASE_URL` format and API key presence) and logs structured warning/error events.
- Missing LiteLLM config disables chat endpoints in degraded mode (`503`) while keeping the rest of the app available.
- Invalid LiteLLM credentials return structured errors (`invalid_api_key`) instead of silent failures.
- Frontend polls `/api/health` and shows a banner when chat is unavailable.

`/api/health` response shape:

```json
{
  "status": "ok|degraded|down",
  "litellm": { "status": "ok|degraded|down", "latency_ms": 0 },
  "rate_limit": { "status": "ok|degraded|down" },
  "db": { "status": "ok|degraded|down" }
}
```

## Monitoring and Telemetry (Sentry)

- Backend Sentry is enabled only when `SENTRY_DSN` is present and `SENTRY_ENABLED` is not `false`.
- Frontend Sentry is enabled only when `VITE_SENTRY_DSN` is present and `VITE_SENTRY_ENABLED` is not `false`.
- Sampling is enabled by default to reduce noise:
  - Backend: `SENTRY_TRACES_SAMPLE_RATE` and `SENTRY_PROFILES_SAMPLE_RATE`
  - Frontend: `VITE_SENTRY_TRACES_SAMPLE_RATE`
- PII and secrets are redacted before telemetry is emitted:
  - Emails, auth tokens, cookies, and headers are scrubbed.
  - Query strings are removed from request URLs.
- Telemetry hooks include `message_send`, `stream_start`, `stream_end`, and payment flow events.

## Payments and Pro Verification

- Upgrade CTA calls backend `POST /api/payments/create-checkout` and redirects to the provider checkout URL.
- Pro status is updated only by verified webhook events (`POST /api/payments/webhook/dodo` or legacy `/webhooks/dodo`).
- Webhook events require valid HMAC signature verification and are processed idempotently to ignore duplicates safely.
- Payment success grants Pro, while failed payments do not grant Pro; cancellation and renewal failure revoke Pro.
- `/success` only refreshes profile state and redirects to `/app`; it never grants Pro by redirect alone.

## 🛠 Tech Stack

| Layer         | Technologies                                                                 |
|---------------|------------------------------------------------------------------------------|
| Frontend      | React 18, TypeScript, Vite, Tailwind CSS, Framer Motion, Zustand, React Query |
| Backend       | FastAPI, Python 3.11+, Pydantic v2, Structlog, Upstash Redis REST             |
| AI Inference  | LiteLLM proxy + Groq + Gemini 2.5 + OpenRouter                               |
| Auth          | Supabase Auth (JWT + OAuth)                                                 |
| Cache / Queue | Redis (Upstash)                                                             |
| Deployment    | Vercel (frontend + serverless backend), Render / Railway (alternative)      |
| Testing       | pytest, vitest, Playwright (planned)                                        |
| License       | Apache License 2.0                                                          |

## Python Tooling

The backend uses the repository-root virtualenv at `.venv/`. Use the root scripts so local commands always resolve through `.venv/bin/python`:

```bash
npm run api:install
npm run api:dev
npm run api:test
```

## 🛤️ Development Journey

- **v0.x** — chaotic prototype, many deployment experiments (Vercel, Render, path hell, 500s)  
- **v1.0** — stable product with auth, payments (in progress), multi-model routing, Redis caching, clean exports  
- **v2.0** (current focus) — major refactor: better dependency injection, comprehensive test suite, OpenTelemetry tracing, more robust error handling, usage analytics

## Local Development

**Prerequisites**
- Node.js 18+ (for frontend)
- Python 3.11+ (for backend)
- pnpm (recommended package manager for frontend)

From the repository root:

### Backend (FastAPI)
```bash
python3 -m venv .venv
npm run api:install
cp .env.example .env
# Edit .env with your real keys:
#   LITELLM_BASE_URL=http://localhost:4000
#   LITELLM_VIRTUAL_KEY=... (or LITELLM_MASTER_KEY=...)
#   SUPABASE_URL=...
#   SUPABASE_ANON_KEY=...
#   UPSTASH_REDIS_REST_URL=...
#   etc.

npm run api:dev
```

Optional: run a local LiteLLM proxy (if you are not pointing at a hosted proxy):

```bash
litellm --config infra/litellm/config.yaml --port 4000
```

The proxy expects provider keys in its environment (for example `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`) plus `LITELLM_MASTER_KEY` if you want to secure the proxy.

Open http://localhost:8000/docs to see the Swagger UI.

### Frontend (React + Vite)

In a separate terminal, from repo root:

```bash
pnpm install
pnpm dev
```

Open http://localhost:5173 (it should proxy `/api` calls to the backend at http://localhost:8000/api — verify in `vite.config.ts` if needed).

### One-command dev (optional)

Run both frontend and backend with:

```bash
npm run dev:full
```

## Database Migrations (Supabase)

Before running any migrations, back up your Supabase database.

If this repo does not already have a `supabase/` folder, initialize it:

```bash
npx supabase init
```

Apply the v2 conversation schema migration:

```bash
npx supabase migration up
```

If you use the db-push workflow instead:

```bash
npx supabase db push
```

Run the v1 history → v2 conversations/messages data migration (dry-run by default):

```bash
.venv/bin/python scripts/migrate_v1_to_v2_history.py
```

To write data:

```bash
.venv/bin/python scripts/migrate_v1_to_v2_history.py --dry-run=false
```

Required environment variables for the migration script:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` (preferred for bypassing RLS) or `SUPABASE_ANON_KEY`



## Contributing

Contributions welcome — especially:

- Better judge/ensemble logic
- Additional explanation styles
- Frontend animations & UX polish
- Test coverage (both FE + BE)

Please open an issue first for larger changes.

## License

This project is licensed under the **Apache License 2.0**

