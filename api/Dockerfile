FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

# NOTE: build from repo root: docker build -f api/Dockerfile .
COPY api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -rf /tmp/requirements.txt

COPY api /app/api
RUN rm -rf /app/api/tests /app/api/scripts

RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
