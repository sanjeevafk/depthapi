# LiteLLM Proxy (In-Repo)

This folder contains the LiteLLM proxy configuration used by the backend model aliases.

## Required Environment Variables

Set these for the proxy service:

- `GROQ_API_KEY`
- `GEMINI_API_KEY`
- `OPENROUTER_API_KEY`
- `LITELLM_MASTER_KEY` - A secure random key used to authenticate requests to the proxy. Generate with `openssl rand -hex 32` or similar.
- Optional Sentry settings (when proxy runtime supports Sentry): `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`.

**Security Notes:**
- ⚠️ Never commit these secrets to version control
- For local development: Use a `.env` file (add to `.gitignore`) or export them in your shell
- For Render deployment: Set as environment variables in the Render dashboard under "Environment"
- Rotate the `LITELLM_MASTER_KEY` periodically and after any suspected exposure


## Local Run

```
litellm --config infra/litellm/config.yaml --port 4000
```

Then check:

```
curl http://localhost:4000/health
```
