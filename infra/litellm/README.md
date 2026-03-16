# LiteLLM Proxy (In-Repo)

This folder contains the LiteLLM proxy configuration used by the backend model aliases.

## Required Environment Variables

Set these for the proxy service:

- `GROQ_API_KEY`
- `GEMINI_API_KEY`
- `OPENROUTER_API_KEY`
- `LITELLM_MASTER_KEY`

## Render Deployment

`render.yaml` is a Docker service blueprint that runs:

```
litellm --config /app/infra/litellm/config.yaml --port $PORT
```

Ensure your Docker build copies this repository into `/app` so the config path resolves.

## Local Run

```
litellm --config infra/litellm/config.yaml --port 4000
```

Then check:

```
curl http://localhost:4000/health
```
