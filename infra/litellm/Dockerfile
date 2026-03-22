FROM python:3.12-slim

WORKDIR /app

# Keep the image small and reproducible.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install LiteLLM proxy extras (includes websockets and related deps).
RUN pip install --no-cache-dir "litellm[proxy]"

# Copy config into the image at the expected path.
COPY config.yaml /app/infra/litellm/config.yaml

# Railway injects $PORT. Use a shell so it expands.
CMD ["sh", "-c", "python -m litellm --config /app/infra/litellm/config.yaml --host 0.0.0.0 --port $PORT"]
