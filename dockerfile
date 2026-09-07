FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .

RUN pip install --no-cache-dir --prefix=/install .

# No embedding model is baked into the image. Retrieval embeddings run on
# Gemini text-embedding-004 (see embedding_stategy.GoogleEmbedding), so there
# is nothing local to download. The optional offline fallback lives in the
# `offline` dependency group and is not installed here.

FROM python:3.11-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

COPY . .

RUN chmod +x /app/scripts/render_start.sh

# The application modules live at /app and are imported by bare name
# (`from brand_metrics import ...`). uvicorn finds them because it is invoked
# from /app, but `celery` is a console script in /usr/local/bin, so sys.path[0]
# is that directory and /app is absent. Celery still boots — it resolves
# `-A celery_task` itself — and then every task body that does a lazy import
# fails with ModuleNotFoundError. Setting PYTHONPATH makes imports resolve the
# same way regardless of which entry point started the process.
ENV PYTHONPATH=/app

# PORT is supplied by the host on most PaaS (Render, Fly, Cloud Run); default
# to 8000 for local runs and docker-compose.
ENV PORT=8000
EXPOSE 8000

# The FastAPI application object is `app` in main.py.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
