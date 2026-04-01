# KnowBear – Layered AI Knowledge Engine

KnowBear is an AI-powered product that delivers explanations at exactly the right depth for any topic, from ELI5 to technical deep dives. It routes requests through native `AsyncOpenAI` provider clients, enforces mode-specific behavior, caches repeat queries, and provides exportable responses in a focused UI.

- Live demo: https://knowbear.vercel.app (now-deprecated v1)
- Deprecated v1 repo: https://github.com/voidcommit-afk/knowbear-v1

## Status

- v2 is implemented and live in this repository, including technical-mode intent/depth handling, streaming reliability hardening, Pro/payment enforcement, and degraded-mode health behavior.
- v3 is the next track, focused on grounded retrieval and citation-backed responses.

## Core Features

- Layered explanation levels: ELI5, ELI10, ELI12, ELI15, Meme
- Dedicated modes: Learning, Technical, Socratic
- Technical mode v2 with intent detection (explain, compare, brainstorm), depth control, and optional diagram guidance
- Stable alias-based model routing through native provider fallback chains
- Production chat sidebar with full thread history, New Thread reset, Go to Home action, and auth-aware sign in/out controls
- Fresh-thread guarantees on reload and thread switch (stale-message race guarded with request-tokened loads)
- SSE streaming with heartbeat, start timeout, and graceful cutoffs
- Fast repeat queries via Redis caching
- Export formats: .txt, .md
- Authentication and Pro gating for premium modes
- Sentry telemetry with PII redaction and release tagging

## Architecture Overview

```
KnowBear monorepo
├── api/                      # FastAPI backend
│   ├── main.py               # uvicorn entrypoint
│   ├── routers/              # FastAPI APIRouter modules
│   │   ├── query.py
│   │   ├── messages.py
│   │   ├── export.py
│   │   ├── pinned.py
│   │   └── health.py
│   ├── services/
│   │   ├── inference.py      # model alias routing + streaming
│   │   ├── intent.py         # technical intent/depth/diagram detection
│   │   ├── cache.py          # Redis abstraction
│   │   ├── auth.py           # Supabase / JWT verification
│   │   └── rate_limit.py     # per-user / global limits
│   └── schemas/              # Pydantic models
├── src/                      # React + Vite frontend
│   ├── components/
│   ├── pages/
│   ├── hooks/
│   └── lib/
├── api/tests/                # backend pytest
├── tests/                    # frontend vitest + Playwright e2e
├── public/
├── vercel.json
└── README.md
```

## Model Routing (Native Providers)

All model calls go through `api/services/llm_client.py` using native `AsyncOpenAI` clients against:

- Groq (`llama-3.1-8b-instant`)
- Gemini (`gemini-2.5-flash`, `gemini-2.5-pro`)
- Cerebras (`zai-glm-4.7`)
- OpenRouter (`openrouter/free`) as optional fallback

Primary app-level MoE routing is defined in `api/services/inference.py` and alias-to-provider fallback mapping lives in `api/services/llm_client.py`.

### MoE Routing Matrix (Mode -> Alias Chain)

| Mode | Condition | Ranked Alias Chain (first -> fallback) |
|------|-----------|-----------------------------------------|
| Learning | Freshness query (`latest`, `today`, `recent`, etc.) | `learn-gemini-flash` -> `learn-groq-llama8b` -> `learn-openrouter-free` |
| Learning | Short/latency-biased query (`<8` tokens or high latency priority) | `learn-groq-llama8b` -> `learn-gemini-flash` -> `learn-openrouter-free` |
| Learning | Default | `learn-gemini-flash` -> `learn-groq-llama8b` -> `learn-openrouter-free` |
| Technical | Math-heavy, high complexity (`>=0.6`) | `technical-gemini-pro` -> (`technical-cerebras-glm` when Pro and complexity `>=0.8`) -> `technical-groq-llama8b` -> `technical-openrouter-free` |
| Technical | Math-heavy, low complexity (`<0.4`) | `technical-gemini-flash` -> `technical-groq-llama8b` -> `technical-openrouter-free` |
| Technical | Programming query or search-context assisted | `technical-gemini-pro` -> `technical-groq-llama8b` -> `technical-openrouter-free` (+ `technical-cerebras-glm` appended when Pro and complexity `>=0.8`) |
| Technical | Default | `technical-gemini-pro` -> (`technical-cerebras-glm` inserted at rank 2 when Pro and complexity `>=0.8`) -> `technical-groq-llama8b` -> `technical-openrouter-free` |
| Socratic | Default | `socratic-gemini-pro` -> `socratic-groq-llama8b` -> `socratic-openrouter-free` |
| Socratic | High-reasoning Pro path (reasoning + complexity `>=0.8`) | `socratic-cerebras-glm` -> `socratic-gemini-pro` -> `socratic-groq-llama8b` -> `socratic-openrouter-free` |

### Alias-to-Provider Matrix (Provider Model Fallback)

| Alias | Provider Fallback Order (provider:model) |
|------|-------------------------------------------|
| `learn-gemini-flash` | `gemini:gemini-2.5-flash` -> `groq:llama-3.1-8b-instant` -> `openrouter:openrouter/free` |
| `learn-groq-llama8b` | `groq:llama-3.1-8b-instant` -> `gemini:gemini-2.5-flash` -> `openrouter:openrouter/free` |
| `learn-openrouter-free` | `gemini:gemini-2.5-flash` -> `groq:llama-3.1-8b-instant` -> `openrouter:openrouter/free` |
| `technical-gemini-flash` | `gemini:gemini-2.5-flash` -> `groq:llama-3.1-8b-instant` -> `openrouter:openrouter/free` |
| `technical-gemini-pro` | `gemini:gemini-2.5-pro` -> `groq:llama-3.1-8b-instant` -> `openrouter:openrouter/free` |
| `technical-groq-llama8b` | `groq:llama-3.1-8b-instant` -> `gemini:gemini-2.5-pro` -> `openrouter:openrouter/free` |
| `technical-openrouter-free` | `gemini:gemini-2.5-pro` -> `groq:llama-3.1-8b-instant` -> `openrouter:openrouter/free` |
| `technical-cerebras-glm` | `cerebras:zai-glm-4.7` -> `gemini:gemini-2.5-pro` -> `groq:llama-3.1-8b-instant` |
| `socratic-gemini-pro` | `gemini:gemini-2.5-pro` -> `groq:llama-3.1-8b-instant` -> `openrouter:openrouter/free` |
| `socratic-groq-llama8b` | `groq:llama-3.1-8b-instant` -> `gemini:gemini-2.5-pro` -> `openrouter:openrouter/free` |
| `socratic-openrouter-free` | `gemini:gemini-2.5-pro` -> `groq:llama-3.1-8b-instant` -> `openrouter:openrouter/free` |
| `socratic-cerebras-glm` | `cerebras:zai-glm-4.7` -> `gemini:gemini-2.5-pro` -> `groq:llama-3.1-8b-instant` -> `openrouter:openrouter/free` |

## API Endpoints (public)

| Method | Path | Description | Auth? | Rate-limited? |
|--------|------|-------------|-------|---------------|
| GET | `/api/health` | Dependency status | No | No |
| GET | `/api/pinned` | Curated & trending topics | No | Light |
| POST | `/api/query` | Main query endpoint — returns layered output | Optional | Yes |
| POST | `/api/export` | Convert result to file (txt/md) | No | Yes |
| GET | `/api/usage` | Current user quota & usage (Pro users) | Yes | No |

## Streaming Behavior

- SSE heartbeat is emitted at least every 2 seconds.
- Stream duration is capped by `STREAM_MAX_SECONDS` (default 25s; longer in non-production).
- Stream start timeout is mode-sensitive (technical mode uses the maximum window; other modes use a tighter cap).
- If streaming cannot start in time, the backend falls back to non-streamed output.

## Frontend UX (Task 14)

- Sidebar now shows complete conversation history for the authenticated user (no 20-thread cap).
- Sidebar includes:
  - `New Thread` (immediate draft reset)
  - `Go to Home` (landing page redirect)
  - auth-aware `Sign in` / `Sign out` action in the account rail
- Thread loading behavior is now deterministic and race-safe:
  - selecting a thread clears visible messages immediately before loading the selected thread
  - out-of-order async loads are ignored using a per-request message-load token
  - reload/thread-switch paths always show the correct backend thread without stale bleed-through
- Sidebar collapse preference is hydrated after mount to keep client state and rendered UI in sync.

## Degraded Mode (Provider Routing)

- On startup, the backend validates provider API key config and logs structured warning/error events.
- Missing provider config disables chat endpoints in degraded mode (`503`) while keeping the rest of the app available.
- Invalid provider credentials return structured errors (`invalid_api_key`).
- Frontend polls `/api/health` and shows a banner when chat is unavailable.

`/api/health` response shape:

```json
{
  "status": "ok|degraded|down",
  "provider": {
    "status": "ok|degraded|down",
    "reachable": true,
    "key_valid": true
  },
  "rate_limit": { "status": "ok|degraded|down" },
  "db": { "status": "ok|degraded|down" }
}
```

## Monitoring and Telemetry (Sentry)

- Backend Sentry is enabled only when `SENTRY_DSN` is present and `SENTRY_ENABLED` is not `false`.
- Frontend Sentry is enabled only when `VITE_SENTRY_DSN` is present and `VITE_SENTRY_ENABLED` is not `false`.
- Release tagging is supported via `SENTRY_RELEASE` (backend) and `VITE_SENTRY_RELEASE` (frontend).
- Sampling is enabled by default to reduce noise.
- PII and secrets are redacted before telemetry is emitted (emails, auth tokens, cookies, headers, and query strings).
- Distributed tracing headers (`sentry-trace`, `baggage`) are propagated from frontend calls to backend and forwarded to provider requests.
- CI release automation lives in `.github/workflows/sentry-release.yml`.
- Alert bootstrap script: `scripts/setup_sentry_alerts.sh`.

## Payments and Pro Verification

- Upgrade CTA calls backend `POST /api/payments/create-checkout` and redirects to the provider checkout URL.
- Pro status is updated only by verified webhook events (`POST /api/payments/webhook/dodo` or legacy `/webhooks/dodo`).
- Webhook events require valid HMAC signature verification and are processed idempotently.
- Payment success grants Pro, while failed payments do not grant Pro; cancellation and renewal failure revoke Pro.
- `/success` only refreshes profile state and redirects to `/app`; it never grants Pro by redirect alone.

## Tech Stack

| Layer | Technologies |
|------|--------------|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, Framer Motion, Zustand, React Query |
| Backend | FastAPI, Python 3.11+, Pydantic v2, Structlog, Upstash Redis REST |
| AI Inference | Native AsyncOpenAI clients, Groq, Gemini, Cerebras, OpenRouter |
| Auth | Supabase Auth (JWT + OAuth) |
| Cache | Redis (Upstash) |
| Deployment | Vercel (frontend + serverless backend) |
| Testing | pytest, vitest, Playwright |
| License | Apache License 2.0 |

## Vercel Deployment Automation

GitHub Actions workflow: `.github/workflows/vercel-deploy.yml`

- Push to `main` deploys to **production**.
- Manual run (`workflow_dispatch`) supports **preview** or **production**.
- Deployment is blocked unless `/api/health` returns HTTP `200` and overall status is not `degraded`/`down`.

Required GitHub repository secrets:

- `VERCEL_TOKEN`
- `VERCEL_ORG_ID`
- `VERCEL_PROJECT_ID`

## Local Development

Prerequisites:
- Node.js 18+
- Python 3.11+

From the repository root:

### Backend (FastAPI)

```bash
python3 -m venv .venv
npm run api:install
cp .env.example .env

# Required environment variables:
# GROQ_API_KEY=...
# GEMINI_API_KEY=...
# OPENROUTER_API_KEY=...
# SUPABASE_URL=...
# SUPABASE_PUBLISHABLE_KEY=...
# SUPABASE_SECRET_KEY=...
# UPSTASH_REDIS_REST_URL=...

npm run api:dev
```

Open http://localhost:8000/docs to see the Swagger UI.

### Frontend (React + Vite)

```bash
npm install
npm run dev
```

Open http://localhost:5173 (it should proxy `/api` calls to the backend at http://localhost:8000/api).

### One-command dev

Run both frontend and backend with:

```bash
npm run dev:full
```

## Testing

```bash
npm run lint
npm run type-check
CI=1 npm run test
npm run test:smoke
npm run api:test
```

## Database Migrations (Supabase)

Before running migrations, back up your Supabase database.

Initialize if needed:

```bash
npx supabase init
```

Apply migrations:

```bash
npx supabase migration up
```

Run the v1 history to v2 conversations/messages migration (dry-run by default):

```bash
.venv/bin/python scripts/migrate_v1_to_v2_history.py
```

To write data:

```bash
.venv/bin/python scripts/migrate_v1_to_v2_history.py --dry-run=false
```

Required environment variables for the migration script:

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY` (preferred for bypassing RLS) or `SUPABASE_PUBLISHABLE_KEY`

## Contributing

Contributions welcome. Please open an issue first for larger changes.

Suggested areas:
- Additional explanation styles
- Improved test coverage
- UX and accessibility polish

## License

This project is licensed under the Apache License 2.0.
