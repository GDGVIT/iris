#!/bin/bash
#
# dev.sh — Local development startup script
#
# Starts Redis (if not already running), a Celery worker, and the Flask
# development server in a single terminal. Press Ctrl+C to stop everything.
#
# Usage:
#   ./dev.sh
#
# Requires: redis-server, uv (https://docs.astral.sh/uv).
# Server runs at http://localhost:9020

echo "Starting Iris (development)"

export FLASK_ENV=development
export SECRET_KEY=${SECRET_KEY:-"dev-secret-key-not-secure"}
export REDIS_URL=${REDIS_URL:-"redis://localhost:6379"}

if ! redis-cli ping > /dev/null 2>&1; then
    echo "Redis not running — starting..."
    redis-server --daemonize yes
    sleep 1
    if ! redis-cli ping > /dev/null 2>&1; then
        echo "Failed to start Redis. Run: redis-server"
        exit 1
    fi
fi

echo "Redis OK"

cleanup() {
    echo ""
    echo "Stopping..."
    kill $CELERY_PID $WEB_PID 2>/dev/null || true
    wait $CELERY_PID $WEB_PID 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

uv run celery -A celery_worker.celery worker --loglevel=info &
CELERY_PID=$!

sleep 2

uv run python run.py &
WEB_PID=$!

sleep 1

echo "Server:  http://localhost:9020"
echo "Swagger: http://localhost:9020/api/docs"
echo "Press Ctrl+C to stop"

wait $CELERY_PID $WEB_PID
