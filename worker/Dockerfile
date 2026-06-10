# Self-hosted tool-execution worker image (Fargate). Build + push to ECR uplift-worker.
# Runs in-VPC; gets ONLY the environment key (never the org API key).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-api.txt ./
RUN pip install --no-cache-dir -r requirements-api.txt

COPY shared/ ./shared/
COPY agents/ ./agents/
COPY worker/ ./worker/
COPY ml/ ./ml/
# worker/worker.py run() lazy-imports api.control.greenlight + api.pg_clients (which in turn
# lazy-imports ingest.embed for SearchRag) — without these the worker crash-loops on startup
# with ModuleNotFoundError. Both are stdlib-only at module import; deps are in requirements-api.
COPY api/ ./api/
COPY ingest/ ./ingest/

RUN useradd --create-home --uid 10001 appuser
USER appuser

# No inbound port; the worker polls the queue outbound. UPLIFT_ENV_ID / UPLIFT_ENV_KEY from Secrets Manager.
CMD ["python", "-m", "worker"]
