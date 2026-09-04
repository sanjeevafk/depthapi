FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

# NOTE: build from repo root: docker build -f api/Dockerfile .
COPY . /tmp/src
RUN set -eux; \
    if [ -d /tmp/src/api ]; then \
        pip install --no-cache-dir -r /tmp/src/api/requirements.txt; \
        mkdir -p /app/api; \
        cp -a /tmp/src/api/. /app/api/; \
        rm -rf /app/api/tests /app/api/scripts; \
    elif [ -f /tmp/src/requirements.txt ]; then \
        pip install --no-cache-dir -r /tmp/src/requirements.txt; \
        mkdir -p /app/api; \
        cp -a /tmp/src/. /app/api/; \
    else \
        echo "No requirements.txt found in expected paths"; \
        exit 1; \
    fi; \
    rm -rf /tmp/src

RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
