#!/bin/sh
#
# entrypoint.sh — Docker container entrypoint
#
# Switches between web and worker modes based on the SERVICE_TYPE env var.
# Used by the Dockerfile and railway.toml for Railway deployments.
#
# SERVICE_TYPE=worker  → Celery worker
# SERVICE_TYPE=<unset> → Gunicorn web server (default)
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
    exec gunicorn --bind "0.0.0.0:${PORT:-8080}" --workers 2 --timeout 120 run:app
fi
