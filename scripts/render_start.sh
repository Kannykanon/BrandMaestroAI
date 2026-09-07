#!/usr/bin/env sh
# Single-service entrypoint: one Celery worker plus the API in one container.
#
# docker-compose runs four workers, one per queue, so generation cannot starve
# feedback or RAG refresh and each scales independently. That shape needs a
# process per worker, and Render bills background workers separately from web
# services. For a demo deployment the queues still exist and routing is
# unchanged — they are simply all consumed by one worker in the same container
# as the API.
#
# This is a deployment-shape decision, not an architectural one. To restore
# independent scaling, drop this script and run
#   celery -A celery_task worker --queues <queue>
# as its own Render Background Worker per queue, pointed at the same Redis.

set -e

: "${PORT:=8000}"
: "${CELERY_CONCURRENCY:=2}"
: "${CELERY_QUEUES:=generation,feedback,retraining,rag_refresh}"

echo "[render_start] starting celery worker (queues=${CELERY_QUEUES} concurrency=${CELERY_CONCURRENCY})"
celery -A celery_task worker \
    --queues "${CELERY_QUEUES}" \
    --concurrency "${CELERY_CONCURRENCY}" \
    --loglevel info &
CELERY_PID=$!

# Stop both processes together, so a failed worker does not leave an API
# serving requests that will never be picked up off the queue.
term() {
    echo "[render_start] shutting down"
    kill -TERM "$CELERY_PID" 2>/dev/null || true
    kill -TERM "$UVICORN_PID" 2>/dev/null || true
    wait
}
trap term TERM INT

echo "[render_start] starting api on port ${PORT}"
uvicorn main:app --host 0.0.0.0 --port "${PORT}" &
UVICORN_PID=$!

# If either process dies, stop the other and exit, so the platform restarts the
# container rather than leaving a half-working service up — an API serving
# requests nothing will consume, or a worker with nothing feeding it.
#
# Polled with kill -0 rather than `wait -n`, which is a bash builtin: this
# image runs /bin/sh (dash), where `wait -n` fails with "Illegal option -n"
# and takes the container down on startup.
while true; do
    if ! kill -0 "$CELERY_PID" 2>/dev/null; then
        echo "[render_start] celery worker exited"
        break
    fi
    if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
        echo "[render_start] api exited"
        break
    fi
    sleep 5
done

term
exit 1
