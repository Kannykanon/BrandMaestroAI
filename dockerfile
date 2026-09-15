FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .

# Generous timeout and retries: the dependency set includes several large
# wheels, and a single slow read from PyPI otherwise fails the whole build.
RUN pip install --no-cache-dir --prefix=/install --timeout 120 --retries 10 .

FROM python:3.11-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

COPY . .

# The application modules live at /app and are imported by bare name
# (`from brand_metrics import ...`). uvicorn finds them because it is invoked
# from /app, but `celery` is a console script in /usr/local/bin, so sys.path[0]
# is that directory and /app is absent. Celery still boots — it resolves
# `-A celery_task` itself — and then every task body that does a lazy import
# fails with ModuleNotFoundError. Setting PYTHONPATH makes imports resolve the
# same way regardless of which entry point started the process.
ENV PYTHONPATH=/app

# Where the local embedding model (EMBEDDING_PROVIDER=local) is downloaded on
# first use. docker-compose mounts a volume here so it survives redeploys.
ENV FASTEMBED_CACHE_PATH=/app/.cache/fastembed

# YouTube Automation: where the Kokoro voice model is downloaded on first use,
# and where files are kept when no storage bucket is configured. docker-compose
# mounts volumes at both so they survive redeploys.
ENV YT_KOKORO_MODEL_DIR=/app/.cache/kokoro
ENV YT_LOCAL_STORAGE_PATH=/app/.yt_storage

# PORT is supplied by the host on most PaaS (Render, Fly, Cloud Run); default
# to 8000 for local runs and docker-compose.
ENV PORT=8000
EXPOSE 8000

# The FastAPI application object is `app` in main.py.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]

# YouTube Automation render worker: the app plus ffmpeg (with libass for burned-in
# captions) and the DejaVu font the captions use. Built only when asked for
# (docker compose's worker_render sets target: render), so the API and marketing
# workers do not carry ffmpeg.
FROM runtime AS render
RUN apt-get update && apt-get install -y --no-install-recommends     ffmpeg     fonts-dejavu-core     && rm -rf /var/lib/apt/lists/*

# The default image. It must stay the last stage: `docker build .` and every
# compose service without a target build the last stage.
FROM runtime AS app
