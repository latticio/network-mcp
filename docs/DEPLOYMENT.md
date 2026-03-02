# Deployment Guide

This guide covers how to deploy the network-mcp server for different environments — from local AI assistant integration to production multi-user deployments with Docker, Kubernetes, health monitoring, and reverse proxies.

## Table of Contents

- [Overview](#overview)
- [Stdio Transport (Default)](#stdio-transport-default)
- [HTTP Transport (Streamable HTTP)](#http-transport-streamable-http)
- [Docker Deployment](#docker-deployment)
- [Docker Compose](#docker-compose)
- [Kubernetes Deployment](#kubernetes-deployment)
- [Helm Chart](#helm-chart)
- [Systemd Service](#systemd-service)
- [Health Checks](#health-checks)
- [Environment Variable Reference](#environment-variable-reference)
- [Reverse Proxy (nginx)](#reverse-proxy-nginx)
- [Multiple Instances](#multiple-instances)
- [HA and Failover Patterns](#ha-and-failover-patterns)
- [Monitoring (Optional)](#monitoring-optional)
- [Resource Tuning Guide](#resource-tuning-guide)
- [Troubleshooting Guide](#troubleshooting-guide)

---

## Overview

network-mcp supports two transport modes:

| Transport | Protocol | Use Case |
|-----------|----------|----------|
| **stdio** (default) | JSON-RPC over stdin/stdout | Local AI assistants — Claude Desktop, Cursor, VS Code Copilot, Claude Code |
| **streamable-http** | JSON-RPC over HTTP (port 8000) | Remote/multi-user deployments, Docker, health monitoring, team shared servers |

**When to use stdio:** You are running the server locally on the same machine as your AI assistant. The client launches the server process and communicates over stdin/stdout. This is the simplest setup and works out of the box.

**When to use streamable-http:** You need the server accessible over the network — shared team access, running in a container, deployed on a remote host, or when you need the `/health` HTTP endpoint for monitoring.

---

## Stdio Transport (Default)

Stdio is the default transport. The MCP client (Claude Desktop, Cursor, etc.) spawns the server as a subprocess and exchanges JSON-RPC messages over stdin/stdout.

### How it works

```
AI Client (Claude Desktop)
    │  spawns process
    ▼
network-mcp (stdin/stdout)
    │  JSON-RPC 2.0 messages
    ▼
Network devices (via eAPI, RESTCONF, NETCONF, NX-API)
```

> **Critical:** Never use `print()` or write to stdout in server code. Stdout is the JSON-RPC transport — any stray output corrupts the protocol stream. All logging goes to stderr via Python's `logging` module.

### Claude Desktop

Config file: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows)

```json
{
  "mcpServers": {
    "network-mcp": {
      "command": "uvx",
      "args": ["network-mcp"],
      "env": {
        "NET_USERNAME": "admin",
        "NET_PASSWORD": "your-password",
        "NET_INVENTORY_FILE": "/path/to/devices.yaml"
      }
    }
  }
}
```

To use a locally installed version instead of `uvx`:

```json
{
  "mcpServers": {
    "network-mcp": {
      "command": "/path/to/venv/bin/network-mcp",
      "env": {
        "NET_USERNAME": "admin",
        "NET_PASSWORD": "your-password"
      }
    }
  }
}
```

### Cursor

Config file: `.cursor/mcp.json` in your project root (or global settings)

```json
{
  "mcpServers": {
    "network-mcp": {
      "command": "uvx",
      "args": ["network-mcp"],
      "env": {
        "NET_USERNAME": "admin",
        "NET_PASSWORD": "your-password",
        "NET_INVENTORY_FILE": "/path/to/devices.yaml"
      }
    }
  }
}
```

### VS Code Copilot

Config file: `.vscode/mcp.json` in your workspace

```json
{
  "mcpServers": {
    "network-mcp": {
      "command": "uvx",
      "args": ["network-mcp"],
      "env": {
        "NET_USERNAME": "admin",
        "NET_PASSWORD": "your-password"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add network-mcp -- uvx network-mcp
```

Or with environment variables:

```bash
claude mcp add network-mcp \
  -e NET_USERNAME=admin \
  -e NET_PASSWORD=your-password \
  -e NET_INVENTORY_FILE=/path/to/devices.yaml \
  -- uvx network-mcp
```

---

## HTTP Transport (Streamable HTTP)

The streamable-http transport runs the server as an HTTP service on port 8000 (FastMCP default). Multiple clients can connect simultaneously, and the `/health` endpoint becomes available for monitoring.

### Starting the server

```bash
# Using the installed entry point
network-mcp --transport streamable-http

# Using uv (from the project directory)
uv run network-mcp --transport streamable-http

# Using uvx (no install needed)
uvx network-mcp --transport streamable-http
```

The server listens on `http://0.0.0.0:8000` by default.

### When to use HTTP transport

- **Multi-user access** — multiple team members share one server instance
- **Remote deployment** — server runs on a different machine than the AI client
- **Docker / Kubernetes** — containerized deployments with health monitoring
- **Health checks** — the `/health` HTTP endpoint is only available with this transport
- **Load balancing** — run behind a reverse proxy with SSL termination

### Client configuration for HTTP transport

Clients connect to the HTTP endpoint URL instead of spawning a local process. Refer to your client's MCP documentation for HTTP transport configuration. The endpoint URL is:

```
http://<host>:8000/mcp
```

---

## Docker Deployment

The repository includes a production-ready `Dockerfile` with a multi-stage build.

### Using the included Dockerfile

The project's `Dockerfile` uses a two-stage build with uv for fast, reproducible installs:

- **Stage 1 (builder):** Installs dependencies into a virtual environment using `uv sync`
- **Stage 2 (runtime):** Copies the virtual environment into a slim Python image, runs as non-root user `mcp`

```bash
# Build the image
docker build -t network-mcp .

# Run with stdio transport (for piping to a local client)
docker run -it \
  -e NET_USERNAME=admin \
  -e NET_PASSWORD=secret \
  network-mcp

# Run with HTTP transport (for remote access)
docker run -d \
  -p 8000:8000 \
  -e NET_USERNAME=admin \
  -e NET_PASSWORD=secret \
  -e NET_INVENTORY_FILE=/app/config/devices.yaml \
  -v ./devices.yaml:/app/config/devices.yaml:ro \
  network-mcp --transport streamable-http
```

### Demo mode (no switches required)

```bash
docker run -d \
  -p 8000:8000 \
  -e NET_DEMO_MODE=true \
  network-mcp --transport streamable-http
```

### Optional dependencies in Docker

To include optional extras (gNMI, ANTA validation, Prometheus metrics), modify the `Dockerfile` builder stage to install them:

```dockerfile
# In the builder stage, after the base uv sync:
RUN uv sync --frozen --no-dev && \
    uv pip install pygnmi anta prometheus-client
```

Or if installing from PyPI instead of building from source:

```dockerfile
FROM python:3.13-slim
RUN pip install network-mcp[gnmi,anta,prometheus]
ENTRYPOINT ["network-mcp"]
```

### Persistent snapshots

If you use workflow tools that create pre-change snapshots, mount a volume for persistent storage:

```yaml
volumes:
  - ./devices.yaml:/app/config/devices.yaml:ro
  - net-snapshots:/app/snapshots
environment:
  - NET_SNAPSHOT_DIR=/app/snapshots
```

---

## Docker Compose

The repository includes a [`docker-compose.yml`](../docker-compose.yml) with a production-ready configuration including health checks and restart policies.

### Quick start

```bash
# Start the service
docker compose up -d

# Follow logs
docker compose logs -f

# Check health
docker compose ps
```

### Basic docker-compose.yml

```yaml
services:
  network-mcp:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      - NET_INVENTORY_FILE=/app/config/devices.yaml
    volumes:
      - ./devices.yaml:/app/config/devices.yaml:ro
    command: ["--transport", "streamable-http"]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    restart: unless-stopped
```

See the project root [`docker-compose.yml`](../docker-compose.yml) for the full configuration.

---

## Kubernetes Deployment

The repository includes Kubernetes manifests in the [`k8s/`](../k8s/) directory:

| File | Purpose |
|------|---------|
| [`k8s/deployment.yaml`](../k8s/deployment.yaml) | Deployment with health checks and resource limits |
| [`k8s/service.yaml`](../k8s/service.yaml) | ClusterIP Service on port 8000 |
| [`k8s/configmap.yaml`](../k8s/configmap.yaml) | Environment variable configuration |
| [`k8s/secret.yaml`](../k8s/secret.yaml) | Credentials (NET_PASSWORD, AUTH_SECRET_KEY) |
| [`k8s/ingress.yaml`](../k8s/ingress.yaml) | Ingress with TLS termination |

### Quick start

```bash
# Create namespace
kubectl create namespace network-mcp

# Apply manifests
kubectl apply -f k8s/ -n network-mcp

# Check status
kubectl get pods -n network-mcp
kubectl logs -f deployment/network-mcp -n network-mcp
```

### Production considerations

- Use Kubernetes Secrets or an external secret manager (Vault, AWS Secrets Manager) for credentials
- Set resource requests and limits appropriate to your inventory size (see [PERFORMANCE.md](PERFORMANCE.md))
- Configure horizontal pod autoscaling if needed (each pod manages its own device inventory)

---

## Helm Chart

For more flexible Kubernetes deployments, use the Helm chart in [`deploy/helm/network-mcp/`](../deploy/helm/network-mcp/):

```bash
# Install
helm install network-mcp deploy/helm/network-mcp/ \
  --namespace network-mcp \
  --create-namespace \
  --set config.netUsername=admin \
  --set secrets.netPassword=your-password

# Upgrade
helm upgrade network-mcp deploy/helm/network-mcp/ \
  --namespace network-mcp \
  -f custom-values.yaml

# Uninstall
helm uninstall network-mcp -n network-mcp
```

The chart includes templates for Deployment, Service, ConfigMap, Secret, Ingress, and NOTES.txt. See [`deploy/helm/network-mcp/values.yaml`](../deploy/helm/network-mcp/values.yaml) for all configurable values.

---

## Systemd Service

For direct host deployment without containers, use a systemd service unit.

### Service file

Create `/etc/systemd/system/network-mcp.service`:

```ini
[Unit]
Description=Network MCP Server
After=network.target

[Service]
Type=simple
User=network-mcp
ExecStart=/opt/network-mcp/venv/bin/network-mcp --transport streamable-http --port 8000
Restart=always
RestartSec=5
Environment=NET_READ_ONLY=true
Environment=LOG_LEVEL=INFO

[Install]
WantedBy=multi-user.target
```

### Setup

```bash
# Create service user
sudo useradd --system --shell /usr/sbin/nologin network-mcp

# Install the application
sudo mkdir -p /opt/network-mcp
sudo python -m venv /opt/network-mcp/venv
sudo /opt/network-mcp/venv/bin/pip install network-mcp

# Copy inventory and environment config
sudo cp devices.yaml /opt/network-mcp/
sudo cp .env /opt/network-mcp/

# Install and start the service
sudo cp network-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now network-mcp

# Check status
sudo systemctl status network-mcp
sudo journalctl -u network-mcp -f
```

### Environment file (optional)

Instead of inline `Environment=` directives, use an environment file:

```ini
# /etc/network-mcp/env
NET_USERNAME=admin
NET_PASSWORD=secret
NET_INVENTORY_FILE=/opt/network-mcp/devices.yaml
NET_READ_ONLY=true
NET_CIRCUIT_BREAKER=true
LOG_LEVEL=INFO
```

Reference it in the service file:

```ini
[Service]
EnvironmentFile=/etc/network-mcp/env
```

---

## Health Checks

### HTTP `/health` endpoint

When running with `--transport streamable-http`, the server exposes an HTTP health check endpoint at:

```
GET http://localhost:8000/health
```

This endpoint returns a JSON response with server status information:

```json
{
  "status": "healthy",
  "version": "4.0.0",
  "uptime_seconds": 3621.5,
  "connections": {
    "active": 2,
    "pooled": 5,
    "max": 100
  },
  "circuit_breakers": {
    "enabled": false,
    "open": 0,
    "devices": []
  },
  "cache": {
    "enabled": true,
    "entries": 42,
    "hit_rate": 0.73,
    "memory_mb": 1.2
  },
  "rate_limiter": {
    "throttled_total": 0
  }
}
```

**Response codes:**
- `200` — server is healthy
- `503` — server is degraded (one or more circuit breakers are open, indicating unreachable devices)

### Docker health check

The `Dockerfile` includes a `HEALTHCHECK` directive that uses httpx to poll the health endpoint. This works when running with HTTP transport. For stdio transport, override with `HEALTHCHECK NONE` in your docker-compose or orchestrator config.

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()" || exit 1
```

Or with `curl` if available in your image:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1
```

### MCP tool health check (all transports)

The `net_server_health` MCP tool provides the same health information on any transport (including stdio). AI assistants can call it directly:

```
"What's the health status of the MCP server?"
→ calls net_server_health()
```

This is useful when running over stdio where there is no HTTP endpoint to query.

---

## Environment Variable Reference

The server is configured entirely through environment variables. See [`.env.example`](../.env.example) for the complete list with defaults.

### Critical deployment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NET_USERNAME` | `admin` | Default username for device authentication |
| `NET_PASSWORD` | *(empty)* | Default password for device authentication |
| `NET_TRANSPORT` | `https` | Default device connection protocol (`https` or `http`) |
| `NET_INVENTORY_FILE` | *(none)* | Path to `devices.yaml` inventory file |
| `NET_READ_ONLY` | `true` | Block all write operations (recommended for production) |
| `NET_DEMO_MODE` | `false` | Use mock devices with realistic data (no real switches needed) |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Performance tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `TIMEOUT_CONNECT` | `10.0` | Connection timeout in seconds |
| `TIMEOUT_SHOW` | `30.0` | Show command timeout in seconds |
| `TIMEOUT_CONFIG` | `60.0` | Config command timeout in seconds |
| `RATE_LIMIT_SHOW` | `5.0` | Max show commands/sec per device |
| `RATE_LIMIT_CONFIG` | `1.0` | Max config commands/sec per device |
| `NET_CACHE_ENABLED` | `true` | Enable TTL-based command response cache |
| `NET_CACHE_MAX_ENTRIES` | `500` | Maximum cache entries |
| `NET_CACHE_MAX_MB` | `50` | Maximum cache memory in MB |

See [PERFORMANCE.md](PERFORMANCE.md) for detailed tuning guidance.

### Resilience

| Variable | Default | Description |
|----------|---------|-------------|
| `NET_CIRCUIT_BREAKER` | `false` | Enable per-device circuit breaker |
| `NET_CB_THRESHOLD` | `3` | Consecutive failures before opening circuit |
| `NET_CB_COOLDOWN` | `60` | Seconds before retrying after circuit opens |
| `NET_VERIFY_CONNECTIONS` | `false` | Validate device connectivity with a test command on first use |

### Secrets management

> **Never pass passwords as command-line arguments.** Command-line arguments are visible in process listings (`ps aux`). Always use environment variables or a `.env` file.

```bash
# Good: environment variable
NET_PASSWORD=secret network-mcp --transport streamable-http

# Good: .env file (loaded automatically by pydantic-settings)
echo "NET_PASSWORD=secret" >> .env
network-mcp --transport streamable-http

# Good: Docker env file
docker run --env-file .env network-mcp

# Bad: visible in process listing
network-mcp --password secret   # DON'T DO THIS (not even a real flag, but don't invent one)
```

For production deployments, consider using Docker secrets, Kubernetes secrets, or a vault solution (see [HashiCorp Vault integration](../src/network_mcp/)) to inject `NET_PASSWORD`, `AUTH_SECRET_KEY`, and `NET_CVP_TOKEN`.

---

## Reverse Proxy (nginx)

When deploying the HTTP transport in production, place the server behind a reverse proxy for SSL termination, access control, and logging.

### nginx configuration

```nginx
upstream mcp_backend {
    server 127.0.0.1:8000;
}

server {
    listen 443 ssl http2;
    server_name mcp.example.com;

    ssl_certificate     /etc/nginx/ssl/mcp.example.com.crt;
    ssl_certificate_key /etc/nginx/ssl/mcp.example.com.key;

    # Recommended SSL settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;

    location / {
        proxy_pass http://mcp_backend;
        proxy_http_version 1.1;

        # Required for SSE (Server-Sent Events) streaming
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Disable buffering for streaming responses
        proxy_buffering off;
        proxy_cache off;

        # Increase timeouts for long-running operations
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    # Health check endpoint (optional: restrict to internal networks)
    location /health {
        proxy_pass http://mcp_backend/health;
        # allow 10.0.0.0/8;
        # deny all;
    }
}
```

### Key proxy requirements

- **Disable buffering** (`proxy_buffering off`) — the MCP streamable-http transport uses SSE (Server-Sent Events) for streaming responses. Buffering breaks real-time message delivery.
- **Upgrade headers** — required for potential WebSocket upgrades in the MCP protocol.
- **Increase timeouts** — network operations (multi-device fabric checks, large config retrievals) can exceed default proxy timeouts. 300 seconds is a reasonable starting point.

### Docker Compose with nginx

```yaml
services:
  network-mcp:
    build: .
    command: ["--transport", "streamable-http"]
    env_file: .env
    volumes:
      - ./devices.yaml:/app/config/devices.yaml:ro
    expose:
      - "8000"
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports:
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
    depends_on:
      - network-mcp
    restart: unless-stopped
```

---

## Multiple Instances

You can run multiple server instances for different teams, environments, or device groups.

### Different inventory files

```bash
# Production network team
NET_INVENTORY_FILE=devices-prod.yaml \
NET_READ_ONLY=true \
  network-mcp --transport streamable-http
  # Serves on port 8000 (default)

# Lab environment (separate process)
NET_INVENTORY_FILE=devices-lab.yaml \
NET_READ_ONLY=false \
  network-mcp --transport streamable-http
  # Note: FastMCP binds to port 8000 by default;
  # use a reverse proxy to route traffic to different instances
```

### Docker Compose with multiple instances

```yaml
services:
  mcp-prod:
    build: .
    command: ["--transport", "streamable-http"]
    environment:
      - NET_INVENTORY_FILE=/app/config/devices.yaml
      - NET_READ_ONLY=true
      - NET_USERNAME=readonly-user
    env_file: .env.prod
    volumes:
      - ./devices-prod.yaml:/app/config/devices.yaml:ro
    expose:
      - "8000"

  mcp-lab:
    build: .
    command: ["--transport", "streamable-http"]
    environment:
      - NET_INVENTORY_FILE=/app/config/devices.yaml
      - NET_READ_ONLY=false
      - NET_USERNAME=admin
    env_file: .env.lab
    volumes:
      - ./devices-lab.yaml:/app/config/devices.yaml:ro
    expose:
      - "8000"

  nginx:
    image: nginx:alpine
    ports:
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
    depends_on:
      - mcp-prod
      - mcp-lab
```

Route traffic with nginx path-based routing:

```nginx
location /prod/ {
    proxy_pass http://mcp-prod:8000/;
    proxy_buffering off;
}

location /lab/ {
    proxy_pass http://mcp-lab:8000/;
    proxy_buffering off;
}
```

### Instance isolation guidelines

- Use **separate inventory files** to control which devices each instance can access
- Use **separate credentials** — a read-only service account for production, admin for lab
- Enable **`NET_READ_ONLY=true`** on production instances to prevent accidental changes
- Use **`NET_ENABLED_MODULES`** to limit which tool categories are available per instance (e.g., disable `security` and `config_mgmt` for junior engineers)

---

## HA and Failover Patterns

### Active-passive failover

For environments requiring high availability without load balancing:

```
              ┌──────────────────┐
              │   VIP / DNS      │
              │   Failover       │
              └────────┬─────────┘
                 ┌─────┴─────┐
                 ▼           ▼
           ┌──────────┐ ┌──────────┐
           │  Active   │ │ Standby  │
           │  MCP Srv  │ │ MCP Srv  │
           └──────────┘ └──────────┘
```

- Deploy two instances with identical configuration
- Use keepalived, Pacemaker, or cloud-native failover (AWS ALB, GCP ILB) for VIP management
- The standby instance starts with its own connection pool (connections are established on demand, so a cold standby has minimal overhead)
- Health check the active instance via `/health`; promote standby on failure

### Active-active with inventory partitioning

Each instance manages a non-overlapping set of devices:

```
              ┌─────────────────┐
              │  Load Balancer  │
              └────────┬────────┘
                 ┌─────┴─────┐
                 ▼           ▼
           ┌──────────┐ ┌──────────┐
           │  MCP Srv  │ │  MCP Srv  │
           │  East     │ │  West    │
           └──────────┘ └──────────┘
                │             │
                ▼             ▼
           East Devices  West Devices
```

- Each instance uses its own `devices.yaml` with a subset of devices
- Load balancer routes requests based on path prefix, header, or round-robin (with device-name-based routing at the application layer)
- No shared state between instances — each has independent connection pools, caches, and circuit breakers
- See [PERFORMANCE.md](PERFORMANCE.md#horizontal-scaling) for capacity planning

### Kubernetes-native HA

Use the Kubernetes Deployment with `replicas > 1` and a Service with `sessionAffinity: ClientIP`:

```yaml
# In k8s/deployment.yaml
spec:
  replicas: 2
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1
      maxSurge: 1
```

Each replica should manage a separate inventory partition, or use the same inventory with the understanding that device connections are not shared (each replica establishes its own connections).

---

## Monitoring (Optional)

### Prometheus metrics

The server can expose metrics in Prometheus exposition format. Install the optional dependency:

```bash
pip install network-mcp[prometheus]
# or
uv pip install prometheus-client
```

Enable metrics collection:

```bash
NET_METRICS=true network-mcp --transport streamable-http
```

### Available metrics

The `net_server_metrics` MCP tool returns detailed metrics including:

- **`net_tool_calls_total`** — tool invocation counts by tool name and status
- **`net_tool_duration_seconds`** — execution duration per tool (count, sum, avg, min, max, p50, p99)
- **`net_cache_hit_total`** / **`net_cache_miss_total`** — cache effectiveness
- **`net_rate_limit_throttled_total`** — rate limiter rejections

### Prometheus scrape configuration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'network-mcp'
    metrics_path: '/health'
    scrape_interval: 30s
    static_configs:
      - targets: ['mcp-server:8000']
```

> **Note:** The `/health` endpoint returns JSON, not Prometheus text format. For native Prometheus exposition format, use the `net_server_metrics` MCP tool with `format: "prometheus"`, or implement a custom `/metrics` endpoint. Grafana dashboard templates are planned for a future release.

### Health monitoring with external tools

Any HTTP monitoring tool can poll the `/health` endpoint:

```bash
# Simple cron-based check
curl -sf http://localhost:8000/health | jq '.status'

# Nagios/Icinga check
curl -sf -o /dev/null -w "%{http_code}" http://localhost:8000/health
# 200 = healthy, 503 = degraded
```

---

## Production Deployment Checklist

Use this checklist before going live with a production network-mcp deployment.

### Application Configuration

- [ ] Set `NET_READ_ONLY=true` for monitoring-only deployments
- [ ] Configure `NET_INVENTORY_FILE` with all managed devices
- [ ] Set `NET_DISTRIBUTED_BACKEND=redis` and `REDIS_URL` for multi-replica deployments
- [ ] Enable circuit breakers: `NET_CIRCUIT_BREAKER=true`
- [ ] Enable metrics: `NET_METRICS=true` and `PROMETHEUS_ENABLED=true`
- [ ] Configure auth: `AUTH_ENABLED=true` with `AUTH_ISSUER_URL` and `AUTH_SECRET_KEY`
- [ ] Enable RBAC: `NET_RBAC_ENABLED=true`
- [ ] Configure audit logging: verify `LOG_LEVEL=INFO` and audit log destination

### Resource Tuning

- [ ] Set resource limits appropriate to device count (see [PERFORMANCE.md](PERFORMANCE.md))
- [ ] For small inventories (< 50 devices): 256Mi request / 512Mi limit, 250m / 1000m CPU
- [ ] For large inventories (50-500 devices): 512Mi request / 1Gi limit, 500m / 2000m CPU
- [ ] For very large inventories (500+ devices): 1Gi request / 2Gi limit, 1000m / 4000m CPU

### Kubernetes / Infrastructure

- [ ] Verify K8s probes: liveness -> `/health/live`, readiness -> `/health/ready`
- [ ] Configure HPA with min 2, max based on device count
- [ ] Set up PodDisruptionBudget with `minAvailable: 1`
- [ ] Enable NetworkPolicy to restrict ingress to authorized clients
- [ ] Pin container image to a specific version tag (not `:latest`)
- [ ] Use Kubernetes Secrets or Vault for credentials (never hardcode in manifests)

### Resilience Validation

- [ ] Test failover: kill one replica, verify traffic shifts to remaining replicas
- [ ] Test Redis failover: stop Redis, verify graceful degradation to local state
- [ ] Verify circuit breaker behaviour: simulate unreachable device, confirm circuit opens
- [ ] Confirm rate limiting works across replicas (shared Redis counters)
- [ ] Run a rolling update and verify zero-downtime deployment

---

## Resource Tuning Guide

Use the tables below to select appropriate settings for your deployment size. All values are recommendations; adjust based on your specific workload and device types.

### Small Deployments (1-100 devices)

Default settings work well for small deployments. A single replica is sufficient.

| Setting | Recommended Value | Notes |
|---------|-------------------|-------|
| `RATE_LIMIT_SHOW` | `5.0` (default) | Adequate for interactive use |
| `RATE_LIMIT_CONFIG` | `1.0` (default) | Safe default for config changes |
| `NET_CACHE_ENABLED` | `true` (default) | Reduces repeated device queries |
| `NET_CACHE_MAX_ENTRIES` | `500` (default) | Plenty for small inventories |
| `NET_CIRCUIT_BREAKER` | `false` (default) | Optional; enable if devices are unreliable |
| `TIMEOUT_CONNECT` | `10.0` (default) | Standard timeout |
| `TIMEOUT_SHOW` | `30.0` (default) | Standard timeout |
| `MAX_CONNECTIONS` | Auto (default) | Auto-calculated from inventory size |
| `NET_PROGRESSIVE_DISCOVERY` | `false` (default) | Load all tools at startup |
| Replicas | 1 | Single instance sufficient |

### Medium Deployments (100-500 devices)

Enable reliability features and consider horizontal scaling. A Redis backend is recommended for multi-replica deployments.

| Setting | Recommended Value | Notes |
|---------|-------------------|-------|
| `RATE_LIMIT_SHOW` | `8.0-10.0` | Higher throughput for more devices |
| `RATE_LIMIT_CONFIG` | `2.0` | Slightly faster config operations |
| `NET_CACHE_ENABLED` | `true` | Essential for reducing device load |
| `NET_CACHE_MAX_ENTRIES` | `1000-2000` | More entries for larger inventories |
| `NET_CACHE_MAX_MB` | `100` | Increase memory allowance for cache |
| `NET_CIRCUIT_BREAKER` | `true` | Prevent cascading failures |
| `NET_CB_THRESHOLD` | `3` (default) | Open after 3 consecutive failures |
| `NET_CB_COOLDOWN` | `60` (default) | Retry after 60 seconds |
| `TIMEOUT_CONNECT` | `15.0` | Allow for slower WAN links |
| `TIMEOUT_SHOW` | `45.0` | Allow for complex show commands |
| `MAX_CONNECTIONS` | `200-300` | Explicit pool sizing |
| `KEEPALIVE_INTERVAL` | `30` | More aggressive keepalive for pool health |
| `NET_DISTRIBUTED_BACKEND` | `redis` | Required for multi-replica state sharing |
| Replicas | 2-3 | With Redis for shared state |

### Large Deployments (500-1000+ devices)

Enable all reliability features. Redis is required. Use HPA with custom metrics for auto-scaling.

| Setting | Recommended Value | Notes |
|---------|-------------------|-------|
| `RATE_LIMIT_SHOW` | `10.0-20.0` | High throughput needed |
| `RATE_LIMIT_CONFIG` | `2.0-5.0` | Higher for batch operations |
| `NET_CACHE_ENABLED` | `true` | Essential |
| `NET_CACHE_MAX_ENTRIES` | `5000` | Large cache for large inventories |
| `NET_CACHE_MAX_MB` | `200-500` | Scale with inventory size |
| `NET_CIRCUIT_BREAKER` | `true` | Essential for reliability |
| `NET_CB_THRESHOLD` | `5` | Slightly more tolerant before tripping |
| `NET_CB_COOLDOWN` | `120` | Longer cooldown to avoid flapping |
| `TIMEOUT_CONNECT` | `20.0` | Generous connect timeout |
| `TIMEOUT_SHOW` | `60.0` | Allow for slow/busy devices |
| `TIMEOUT_CONFIG` | `120.0` | Allow for large config pushes |
| `MAX_CONNECTIONS` | `500-1000` | Large pool for many devices |
| `KEEPALIVE_INTERVAL` | `30` | Aggressive keepalive |
| `NET_PROGRESSIVE_DISCOVERY` | `true` | Reduce startup time and memory |
| `NET_DISTRIBUTED_BACKEND` | `redis` | Required |
| `NET_METRICS` | `true` | Enable for HPA custom metrics |
| `NET_OTEL_ENABLED` | `true` | Full observability |
| Replicas | 3-5 minimum | With HPA auto-scaling |

### General Scaling Guidelines

- **Connection pool**: Each replica maintains its own pool. With inventory partitioning, set `MAX_CONNECTIONS` to cover your partition size plus headroom.
- **Cache sizing**: Rule of thumb: `NET_CACHE_MAX_ENTRIES` should be at least 2x the number of devices times the number of frequently used commands.
- **Progressive discovery**: For large deployments, enabling `NET_PROGRESSIVE_DISCOVERY=true` reduces startup time and initial memory usage by loading tool modules on demand.
- **Redis**: When running multiple replicas, use Redis (`NET_DISTRIBUTED_BACKEND=redis`) to share circuit breaker state, change management locks, and other distributed state.

---

## Troubleshooting Guide

Common operational issues and their resolutions.

### Circuit breaker keeps opening

**Symptoms:** Tools return "circuit breaker open" errors for specific devices.

**Diagnosis:**
1. Check the `/health` endpoint for devices listed under `circuit_breakers.devices`
2. Verify device reachability: `ping <device-ip>` from the server host
3. Check device eAPI/RESTCONF/NETCONF service status

**Resolution:**
- Verify the device is reachable and its management API is responding
- Increase `NET_CB_THRESHOLD` if transient errors are common (default: 3)
- Increase `NET_CB_COOLDOWN` if the device needs more recovery time (default: 60s)
- Check `TIMEOUT_CONNECT` and `TIMEOUT_SHOW` — timeouts count as failures
- If the device is known to be down for maintenance, the circuit breaker is working as intended

```bash
# Increase threshold and cooldown
NET_CB_THRESHOLD=5
NET_CB_COOLDOWN=120
```

### Rate limit too aggressive

**Symptoms:** Tools return rate limit errors or seem slow for specific devices.

**Diagnosis:**
1. Check the `/health` endpoint for `rate_limiter.throttled_total`
2. Review whether the rate limit matches your device capabilities

**Resolution:**
- Increase `RATE_LIMIT_SHOW` for faster show command throughput (default: 5.0/s)
- Increase `RATE_LIMIT_CONFIG` for faster config operations (default: 1.0/s)
- Rate limits are **per device** — a global increase affects all devices equally
- Modern devices (EOS 4.28+, NX-OS 10.x) can typically handle 10-20 show commands/second

```bash
# More permissive rate limits
RATE_LIMIT_SHOW=10.0
RATE_LIMIT_CONFIG=2.0
```

### Auth failures

**Symptoms:** HTTP 401 Unauthorized or 403 Forbidden responses on the HTTP transport.

**Diagnosis:**
1. Check that `AUTH_ENABLED=true` is set
2. Verify the JWT token is not expired
3. Verify the issuer URL matches the token's `iss` claim
4. Check RBAC scope requirements if 403

**Resolution:**
- Verify `AUTH_ISSUER_URL` matches your identity provider
- Verify `AUTH_SECRET_KEY` matches the signing key used to issue tokens
- Check token expiry (`exp` claim) — expired tokens are rejected
- If using RBAC, verify the token includes the required scopes (e.g., `network:read`)
- Check `AUTH_AUDIENCE` matches the token's `aud` claim (default: `network-mcp`)
- For JWKS-based validation, verify `AUTH_JWKS_URL` is reachable from the server

```bash
# Debug auth issues
LOG_LEVEL=DEBUG   # Logs token validation details to stderr
```

### Connection pool exhaustion

**Symptoms:** Connection timeouts, slow tool responses, "connection pool exhausted" errors.

**Diagnosis:**
1. Check the `/health` endpoint for `connections.active` vs `connections.max`
2. Look for connection leak patterns (active count grows but never shrinks)

**Resolution:**
- Increase `MAX_CONNECTIONS` to handle more concurrent device connections
- Enable `KEEPALIVE_INTERVAL` to detect and recycle stale connections (default: 60s)
- Reduce `KEEPALIVE_INTERVAL` to recycle connections more aggressively (minimum: 10s)
- If using multiple replicas, partition the inventory so each replica manages fewer devices
- Check for long-running commands that hold connections open

```bash
# Increase pool and tune keepalive
MAX_CONNECTIONS=200
KEEPALIVE_INTERVAL=30
```

### High latency

**Symptoms:** Tool responses take longer than expected.

**Diagnosis:**
1. Check if caching is enabled (`NET_CACHE_ENABLED=true`)
2. Check the `/health` endpoint for cache hit rate
3. Verify network path to devices (traceroute, latency)

**Resolution:**
- Enable caching if disabled: `NET_CACHE_ENABLED=true`
- Increase cache size if hit rate is low due to eviction: `NET_CACHE_MAX_ENTRIES=2000`
- Check network path between the server and devices — high latency multiplies with sequential commands
- Use `NET_PROGRESSIVE_DISCOVERY=true` to reduce startup time
- For fabric-wide operations, consider running the server closer to the devices (same data center)
- Increase `TIMEOUT_SHOW` if commands are timing out on busy devices

```bash
# Optimize for latency
NET_CACHE_ENABLED=true
NET_CACHE_MAX_ENTRIES=2000
NET_CACHE_MAX_MB=100
NET_PROGRESSIVE_DISCOVERY=true
```

### Memory usage growing

**Symptoms:** Server memory usage increases over time, eventually causing OOM kills.

**Diagnosis:**
1. Check cache memory usage via the `/health` endpoint (`cache.memory_mb`)
2. Monitor Python process RSS over time
3. Check for large tool outputs being cached

**Resolution:**
- Reduce cache size: `NET_CACHE_MAX_ENTRIES=500`, `NET_CACHE_MAX_MB=50`
- The cache uses LRU eviction — reducing max entries forces older entries out
- Reduce `MAX_CONNECTIONS` to limit per-connection memory overhead
- Use output size limits (enabled by default in `sanitizer.py`) to prevent oversized responses
- For Kubernetes deployments, set memory resource limits and let the OOM killer restart the pod

```bash
# Constrain memory usage
NET_CACHE_MAX_ENTRIES=500
NET_CACHE_MAX_MB=50
MAX_CONNECTIONS=100
```
