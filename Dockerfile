FROM python:3.13-slim

# Pinned so a uv release can never change a build that the repo did not
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /uvx /bin/

WORKDIR /app

# Install Python dependencies (production only)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code (entrypoint.sh is executable in git)
COPY . .

# Make the venv's binaries available (gunicorn, celery, etc.)
ENV PATH="/app/.venv/bin:$PATH"

# SERVICE_TYPE=worker runs celery, anything else runs gunicorn
CMD ["/app/entrypoint.sh"]
