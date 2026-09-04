FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

COPY . /tmp/src
RUN set -eux; \
    if [ -d /tmp/src/api ]; then \
        pip install --no-cache-dir -r /tmp/src/api/requirements.txt; \
        mkdir -p /app/api; \
        cp -a /tmp/src/api/. /app/api/; \
    elif [ -f /tmp/src/requirements.txt ]; then \
        pip install --no-cache-dir -r /tmp/src/requirements.txt; \
        mkdir -p /app/api; \
        cp -a /tmp/src/. /app/api/; \
    else \
        echo "No requirements.txt found in expected paths"; \
        exit 1; \
    fi; \
    rm -rf /tmp/src

EXPOSE 8000

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
