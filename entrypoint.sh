#!/bin/sh
#
# entrypoint.sh — the container's CMD (see Dockerfile)
#
# One image runs both services; SERVICE_TYPE picks the role:
#
# SERVICE_TYPE=worker  → Celery worker
# SERVICE_TYPE=<unset> → Gunicorn web server (default)
#
# The web server uses threaded workers because GET /tasks/stream/<id> holds a
# connection open for the length of a search; a sync worker would be pinned to
# one viewer. --timeout bounds a stuck worker, not a long request, under gthread.
#
# Port is read from PORT env var (default: 8080, Railway sets this automatically).

set -e

if [ "$SERVICE_TYPE" = "worker" ]; then
    echo "Starting Celery worker..."
    exec celery -A celery_worker.celery worker \
        --loglevel=info \
        --max-tasks-per-child=3 \
        --concurrency=2
else
    echo "Starting Gunicorn on port ${PORT:-8080}..."
    exec gunicorn --bind "0.0.0.0:${PORT:-8080}" \
        --workers 2 \
        --worker-class gthread \
        --threads 16 \
        --timeout 120 \
        run:app
fi
