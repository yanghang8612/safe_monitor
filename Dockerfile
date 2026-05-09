# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# Install uv (to world-readable location so non-root USER app can invoke it)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
 && cp /root/.local/bin/uv /usr/local/bin/uv \
 && cp /root/.local/bin/uvx /usr/local/bin/uvx
ENV PATH="/usr/local/bin:$PATH"

WORKDIR /app

# Copy lockfile + manifest; install into system site-packages for smaller image
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

# Copy source
COPY src ./src
COPY scripts ./scripts
COPY config.yaml ./config.yaml

# Create non-root user
RUN useradd -m -u 10001 app \
 && mkdir -p /app/data \
 && chown -R app:app /app
USER app

ENV PYTHONPATH=/app/src

CMD ["uv", "run", "python", "-m", "safe_monitor.main"]
