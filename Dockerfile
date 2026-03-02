# syntax=docker/dockerfile:1

# ============================================================
# Dockerfile — network-mcp
# Multi-stage build: builder (deps) + runtime (slim)
#
# Transport modes:
#   stdio (default):          docker run -it network-mcp
#   streamable-http:          docker run -p 8000:8000 network-mcp --transport streamable-http
#
# Environment variables (NET_* prefix; EOS_* also works for backward compat):
#   NET_USERNAME              Default device username (default: admin)
#   NET_PASSWORD              Default device password
#   NET_TRANSPORT             eAPI transport: https or http (default: https)
#   NET_INVENTORY_FILE        Path to devices.yaml (default: /app/config/devices.yaml)
#   NET_READ_ONLY             Block write operations (default: true)
#   NET_DEMO_MODE             Enable demo mode with mock data (default: false)
#   NET_DISABLED_MODULES      Comma-separated modules to disable
#   NET_CACHE_ENABLED         Enable command cache (default: true)
#   NET_SNAPSHOT_TTL          Pre-change snapshot TTL in seconds (default: 3600)
#   NET_SNAPSHOT_DIR          Directory for persistent snapshot storage
#   RATE_LIMIT_SHOW           Max show commands/sec per device (default: 5.0)
#   RATE_LIMIT_CONFIG         Max config commands/sec per device (default: 1.0)
#   LOG_LEVEL                 Logging level (default: INFO)
#   AUTH_ENABLED              Enable OAuth 2.1 auth for HTTP mode (default: false)
#   AUTH_SECRET_KEY           JWT signing/verification key
#   AUTH_ISSUER_URL           Expected JWT issuer URL
#   GNMI_PORT                 gNMI port (default: 6030)
#   GNMI_INSECURE             Skip TLS for gNMI (default: false)
# ============================================================

# Stage 1: Builder
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install project
COPY src/ src/
RUN uv sync --frozen --no-dev

# Stage 2: Runtime
FROM python:3.14-slim AS runtime

LABEL org.opencontainers.image.title="network-mcp"
LABEL org.opencontainers.image.description="Multi-vendor Network MCP Platform for AI-powered network automation"
LABEL org.opencontainers.image.source="https://github.com/latticio/network-mcp"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# Create non-root user
RUN groupadd --gid 1000 mcp && \
    useradd --uid 1000 --gid mcp --shell /bin/bash --create-home mcp

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy source
COPY --from=builder /app/src /app/src

# Config volume for devices.yaml and .env
VOLUME /app/config

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV NET_INVENTORY_FILE=/app/config/devices.yaml

USER mcp

# Health check for HTTP transport mode.
# When using stdio transport, override with HEALTHCHECK NONE in docker-compose or orchestrator config.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()" || exit 1

STOPSIGNAL SIGTERM

# Default: stdio transport (for Claude Desktop / local usage)
# Override with: --transport streamable-http
ENTRYPOINT ["network-mcp"]
