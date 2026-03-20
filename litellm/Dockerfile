FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir litellm

COPY ../infra/litellm/config.yaml /app/config.yaml

EXPOSE 4000

CMD ["litellm", "--config", "/app/config.yaml", "--host", "0.0.0.0", "--port", "4000"]
