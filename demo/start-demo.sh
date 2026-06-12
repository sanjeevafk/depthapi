#!/usr/bin/env bash
# DepthAPI — presentation startup script
# Usage: ./start-demo.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
UVICORN="$VENV/bin/uvicorn"
PYTHON="$VENV/bin/python"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║         DepthAPI — Demo Startup          ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 0. Free port 8000 if already occupied ───────────────────────────────────
if fuser 8000/tcp &>/dev/null 2>&1; then
  echo "⚠️  Port 8000 in use — killing stale process..."
  fuser -k 8000/tcp &>/dev/null 2>&1 || true
  sleep 1
  echo "✅ Port 8000 freed"
fi

# ── 1. Redis ────────────────────────────────────────────────────────────────
if redis-cli ping &>/dev/null; then
  echo "✅ Redis already running"
else
  echo "🔴 Starting Redis..."
  if command -v redis-server &>/dev/null; then
    redis-server --daemonize yes --logfile /tmp/redis-depthapi.log
    sleep 1
    redis-cli ping && echo "✅ Redis started"
  else
    echo "⚠️  redis-server not found — trying Docker fallback..."
    docker rm -f depthapi-redis 2>/dev/null || true
    docker run -d --rm --name depthapi-redis -p 6379:6379 redis:7-alpine \
      redis-server --appendonly yes
    sleep 2
    echo "✅ Redis (Docker) started"
  fi
fi

# ── 2. Environment ───────────────────────────────────────────────────────────
set -a
# Load .env.local first (takes precedence), then .env
[ -f "$SCRIPT_DIR/.env.local" ] && source "$SCRIPT_DIR/.env.local"
[ -f "$SCRIPT_DIR/.env" ] && source "$SCRIPT_DIR/.env"
set +a

# Override CORS to allow the demo file to talk to the API
export ALLOWED_ORIGINS="http://localhost:8000,http://127.0.0.1:8000"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export AUTH_PROVIDER_MODE="${AUTH_PROVIDER_MODE:-env}"
export DEV_API_KEYS="${DEV_API_KEYS:-sk-depth-local-dev}"
export ENVIRONMENT="development"

echo ""
echo "📡 LLM Keys loaded:"
[ -n "${GROQ_API_KEY:-}" ]       && echo "   ✅ Groq"          || echo "   ❌ Groq (missing)"
[ -n "${GEMINI_API_KEY:-}" ]     && echo "   ✅ Gemini"        || echo "   ❌ Gemini (missing)"
[ -n "${OPENROUTER_API_KEY:-}" ] && echo "   ✅ OpenRouter"    || echo "   ❌ OpenRouter (missing)"
[ -n "${CEREBRAS_API_KEY:-}" ]   && echo "   ✅ Cerebras"      || echo "   ❌ Cerebras (missing)"
echo ""

# Enable Mock RAG mode for presentations (bypasses DB and uses LLM/web search instead)
export MOCK_RAG="${MOCK_RAG:-1}"

# Allow longer streams for expert/technical depth responses during demos
export STREAM_MAX_SECONDS="${STREAM_MAX_SECONDS:-90}"
export TECHNICAL_STREAM_MAX_SECONDS="${TECHNICAL_STREAM_MAX_SECONDS:-120}"

# ── 3. API ───────────────────────────────────────────────────────────────────
echo "🚀 Starting DepthAPI on http://localhost:8000 ..."
echo "🌐 Open demo at: http://localhost:8000/demo"
echo "📊 Presentation deck: http://localhost:8000/presentation"
echo "📖 OpenAPI docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop."
echo ""

cd "$SCRIPT_DIR"
PYTHONPATH="$SCRIPT_DIR:$SCRIPT_DIR/api" \
  "$UVICORN" main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload \
  --log-level info
