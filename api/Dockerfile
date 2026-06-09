# Control-plane API image (FastAPI on Fargate). Build + push to ECR uplift-api.
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Runtime deps only (test/lint deps stay out of the image).
COPY requirements-api.txt ./
RUN pip install --no-cache-dir -r requirements-api.txt

# App code (the modules the API imports at runtime).
COPY shared/ ./shared/
COPY api/ ./api/
COPY agents/ ./agents/
COPY conv/ ./conv/
COPY signup/ ./signup/
COPY ml/ ./ml/
# db/ holds schema.sql + roles.sql, used by `python -m api.migrate`
COPY db/ ./db/

# Non-root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# Health: the ALB target group checks /healthz.
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "api.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
