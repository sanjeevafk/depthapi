"""Create a local-first development env file and bootstrap local data dirs."""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _default_env_path() -> Path:
    return REPO_ROOT / ".env.local"


def _render_env(dev_api_key: str) -> str:
    return f"""# Local-first DepthAPI development
ENVIRONMENT=development
AUTH_PROVIDER_MODE=env
DEV_API_KEYS={dev_api_key}

# Local Redis via docker compose
REDIS_URL=redis://localhost:6379/0
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=

# Configure at least one provider key to generate answers
GROQ_API_KEY=
GEMINI_API_KEY=
OPENROUTER_API_KEY=
OPENAI_API_KEY=
CEREBRAS_API_KEY=

# Safe local defaults
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
LOG_USER_HASH_SALT=depthapi-local-dev-salt
SENTRY_ENABLED=false
SENTRY_DSN=

# Optional cloud features remain disabled in local-first mode
SUPABASE_URL=
SUPABASE_PUBLISHABLE_KEY=
SUPABASE_SECRET_KEY=
DODO_API_KEY=
DODO_WEBHOOK_SECRET=
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap local-first DepthAPI development settings.")
    parser.add_argument("--output", type=Path, default=None, help="Env file to create. Defaults to .env or .env.local-first.")
    parser.add_argument("--force", action="store_true", help="Overwrite the target file if it exists.")
    args = parser.parse_args()

    output_path = args.output or _default_env_path()
    if not output_path.is_absolute():
        output_path = (REPO_ROOT / output_path).resolve()

    if output_path.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing file: {output_path}. Re-run with --force.")

    dev_api_key = f"sk-depth-local-{secrets.token_hex(8)}"
    output_path.write_text(_render_env(dev_api_key), encoding="utf-8")

    for rel_path in ("data/rag", "data/rag/trusted"):
        (REPO_ROOT / rel_path).mkdir(parents=True, exist_ok=True)

    print(f"Wrote local-first env file: {output_path}")
    print(f"Generated dev API key: {dev_api_key}")
    print("Next steps:")
    print("  1. Start Redis with `docker compose up -d redis`")
    print("  2. Start the API with `uvicorn main:app --reload`")
    print("  3. Call /api/query with `Authorization: Bearer <generated key>`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
