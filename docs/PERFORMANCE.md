# Performance Guide

This guide covers scaling limits, memory profiling, connection pool tuning, and deployment recommendations for the network-mcp server at various inventory sizes.

## Table of Contents

- [Threading Model](#threading-model)
- [Scaling Limits](#scaling-limits)
- [Memory Profiling](#memory-profiling)
- [Connection Pool Tuning](#connection-pool-tuning)
- [Rate Limiting Tuning](#rate-limiting-tuning)
- [Circuit Breaker Tuning](#circuit-breaker-tuning)
- [Retry Strategy](#retry-strategy)
- [Timeouts](#timeouts)
- [Command Cache](#command-cache)
- [Progressive Discovery](#progressive-discovery)
- [Output Size Limits](#output-size-limits)
- [Benchmark Results](#benchmark-results)
- [Large-Scale Deployment Tips](#large-scale-deployment-tips)
- [Horizontal Scaling](#horizontal-scaling)

---

## Threading Model

The network-mcp server operates differently depending on the transport mode:

### Stdio Transport (Default)

- **Single-threaded** event loop handling one client connection
- All tool calls are processed sequentially from the AI client
- Device commands use synchronous I/O (pyeapi, httpx, scrapli)
- Suitable for single-user local deployments (Claude Desktop, Cursor, VS Code)

### HTTP Transport (Streamable HTTP)

- **Multi-threaded** via FastMCP's ASGI server (uvicorn)
- Multiple concurrent client connections are supported
- Each request is handled in a thread pool worker
- Connection pool and rate limiter are shared across all threads (thread-safe)
- Suitable for multi-user, containerized, and production deployments

### Thread safety guarantees

- **ConnectionManager** is thread-safe via `threading.Lock` (`_nodes_lock`) — all pool mutations (create, evict, refcount) are atomic
- **Rate limiter** is per-device with its own lock — concurrent calls to different devices are not bottlenecked by each other
- **Circuit breaker** state is per-device — one device's failures don't block calls to other devices
- **Command cache** uses a thread-safe lock — concurrent cache reads/writes are serialized but fast (~1-3 µs)
- **Connection verification** runs outside the pool lock to avoid blocking all threads during network I/O

---

## Scaling Limits

Tested limits under controlled conditions (single server instance, HTTP transport):

| Metric | Tested Limit | Notes |
|--------|-------------|-------|
| Max concurrent devices | ~500 | Limited by connection pool memory and file descriptors |
| Max tool calls/sec (cached) | ~200/sec | Responses served from TTL cache, no device I/O |
| Max tool calls/sec (uncached) | ~20/sec | Bound by device response latency (~50-200ms per command) |
| Max concurrent MCP clients | ~50 | HTTP transport with uvicorn default workers |
| Max inventory size | 1,000+ devices | With connection pool auto-tuning (see below) |

### Bottlenecks

1. **Device response latency** is the dominant factor for uncached calls. Show commands typically take 50-200ms; config commands take 100-500ms.
2. **Connection establishment** is expensive (~500ms-2s per device). The connection pool amortizes this cost.
3. **Output serialization** can be slow for large responses (e.g., full running-config on 1000+ line configs). The `enforce_dict_output_limit()` helper caps output size.

---

## Memory Profiling

Estimated memory usage by inventory size:

| Inventory Size | Base Memory | Per-Device Overhead | Total Estimate |
|---------------|-------------|--------------------:|---------------:|
| 10 devices | ~50 MB | ~10 KB/device | ~50 MB |
| 100 devices | ~50 MB | ~10 KB/device | ~51 MB |
| 500 devices | ~50 MB | ~10 KB/device | ~55 MB |
| 1,000 devices | ~50 MB | ~10 KB/device | ~60 MB |

**Per-device memory breakdown (~10 KB):**

- Connection object and socket state: ~4 KB
- Device metadata (inventory entry): ~2 KB
- Rate limiter token bucket: ~1 KB
- Circuit breaker state: ~1 KB
- Per-device cache entries: ~2 KB (varies with cache usage)

**Additional memory consumers:**

- Command cache: up to `NET_CACHE_MAX_MB` (default 50 MB)
- Pre-change snapshots: ~5-50 KB per snapshot (stored in `NET_SNAPSHOT_DIR` on disk)
- Audit log buffer: ~1-5 MB depending on log rotation settings

### Profiling with `tracemalloc`

```python
# Add to server startup for memory profiling (development only)
import tracemalloc
tracemalloc.start()

# After some operation:
snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')
for stat in top_stats[:10]:
    print(stat, file=sys.stderr)
```

### Profiling with `memray`

```bash
# Install memray
uv pip install memray

# Run server under memray
memray run -o profile.bin $(which network-mcp) --transport streamable-http

# Generate flamegraph
memray flamegraph profile.bin -o profile.html
```

---

## Connection Pool Tuning

The `ConnectionManager` maintains a thread-safe pool of pyeapi/httpx/scrapli connections with FIFO eviction.

### Auto-tuning (default)

When `MAX_CONNECTIONS` is not set, the pool size is automatically calculated from the inventory:

```
max_connections = min(inventory_size * 2, 500)
```

This formula ensures each device can have up to 2 concurrent connections (one for show commands, one for config operations) while capping total connections at 500 to avoid file descriptor exhaustion. If no inventory is loaded, the default is 100.

To override auto-tuning, set the `MAX_CONNECTIONS` environment variable:

```bash
MAX_CONNECTIONS=250  # explicit pool size, disables auto-tuning
```

### Auto-tuning examples

| Inventory Size | Auto-tuned `max_connections` |
|---------------|-----------------------------:|
| 10 devices | 20 |
| 50 devices | 100 |
| 100 devices | 200 |
| 250 devices | 500 |
| 500+ devices | 500 (cap) |

### Pool statistics

The `pool_stats()` method returns detailed pool metrics for observability:

| Metric | Description |
|--------|-------------|
| `active` | Connections currently checked out (refcount > 0) |
| `idle` | Pooled connections not in active use |
| `pooled` | Total connections in the pool (active + idle) |
| `max` | Maximum pool size (auto-tuned or explicit) |
| `total_created` | Total connections created since startup |
| `total_checkouts` | Total `acquire()` calls since startup |
| `avg_checkout_ms` | Average time to acquire a connection (ms) |
| `per_device` | Per-device refcount breakdown |

### Related environment variables

| Variable | Default | Recommendation |
|----------|---------|---------------|
| `MAX_CONNECTIONS` | auto | Set explicitly only if auto-tuning doesn't fit your deployment |
| `TIMEOUT_CONNECT` | `10.0` | Increase to `15.0` for high-latency WAN links |
| `TIMEOUT_SHOW` | `30.0` | Increase to `60.0` for large config retrievals |
| `TIMEOUT_CONFIG` | `60.0` | Increase to `120.0` for complex config sessions |
| `NET_VERIFY_CONNECTIONS` | `false` | Enable for production to detect stale connections |

---

## Rate Limiting Tuning

The token-bucket rate limiter operates per-device to prevent overwhelming individual switches.

### Default settings

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_SHOW` | `5.0` | Show commands per second per device |
| `RATE_LIMIT_CONFIG` | `1.0` | Config commands per second per device |

Burst capacity equals the rate limit value (e.g., 5.0 = burst of 5). Increase `RATE_LIMIT_SHOW` for powerful platforms (7500R, 7800R). Keep `RATE_LIMIT_CONFIG` conservative (config operations are heavier).

### Production recommendations

| Deployment Size | `RATE_LIMIT_SHOW` | `RATE_LIMIT_CONFIG` | Rationale |
|----------------|-------------------|---------------------|-----------|
| Small (< 50 devices) | `5.0` (default) | `1.0` (default) | Default is fine |
| Medium (50-200 devices) | `3.0` | `0.5` | Reduce per-device load to avoid aggregate burst |
| Large (200-500 devices) | `2.0` | `0.5` | Protect control plane on heavily queried devices |
| Critical infrastructure | `1.0` | `0.2` | Conservative; prioritize device stability |

### Aggregate throughput

With `N` devices and `RATE_LIMIT_SHOW = R`, the theoretical max aggregate show commands/sec is `N * R`. For 100 devices at 3.0/sec, that is 300 commands/sec aggregate. In practice, device response latency is the binding constraint.

---

## Circuit Breaker Tuning

The per-device circuit breaker prevents cascading failures when devices become unreachable.

### Default settings

| Variable | Default | Description |
|----------|---------|-------------|
| `NET_CIRCUIT_BREAKER` | `false` | Disabled by default |
| `NET_CB_THRESHOLD` | `3` | Consecutive failures before opening |
| `NET_CB_COOLDOWN` | `60` | Seconds before half-open retry |

### Production recommendations

| Scenario | `NET_CB_THRESHOLD` | `NET_CB_COOLDOWN` | Rationale |
|---------|--------------------|--------------------|-----------|
| Stable network | `5` | `60` | Tolerate transient errors |
| Flaky WAN links | `3` | `120` | Fail fast, retry slowly |
| Maintenance windows | `2` | `300` | Quick detection, long backoff |
| Large fabric (500+) | `3` | `60` | Default; rely on health endpoint to surface open circuits |

Enable circuit breakers in production to prevent a single unreachable device from blocking tool calls with long timeouts:

```bash
NET_CIRCUIT_BREAKER=true
NET_CB_THRESHOLD=3
NET_CB_COOLDOWN=60
```

---

## Retry Strategy

Automatic retry with exponential backoff for transient errors:

- **Show commands:** 3 attempts, backoff 1s/2s/4s
- **Config commands:** 2 attempts, backoff 2s/5s
- Only retries transport errors (connection reset, timeout)

---

## Timeouts

| Setting | Default | Purpose |
|---------|---------|---------|
| `TIMEOUT_CONNECT` | `10.0s` | TCP connection timeout |
| `TIMEOUT_SHOW` | `30.0s` | Show command timeout |
| `TIMEOUT_CONFIG` | `60.0s` | Config command timeout |
| `GNMI_TIMEOUT` | `10s` | gNMI operation timeout |

**Tuning tips:**
- Increase `TIMEOUT_SHOW` for commands that return large datasets (e.g., `show tech-support`)
- Increase `GNMI_TIMEOUT` for subscribe operations over slow links

---

## Command Cache

TTL-based cache with LRU eviction reduces redundant device queries.

| Setting | Default | Purpose |
|---------|---------|---------|
| `NET_CACHE_ENABLED` | `true` | Enable/disable caching |
| `NET_CACHE_MAX_ENTRIES` | `500` | Maximum cached responses |
| `NET_CACHE_MAX_MB` | `50` | Memory limit in MB |

Commands have per-type TTLs (e.g., `show version` cached longer than `show interfaces counters`). Cache is automatically invalidated per-host when config changes are pushed.

**Tuning tips:**
- Increase `NET_CACHE_MAX_ENTRIES` for large inventories
- Decrease for environments with frequent config changes
- Disable for real-time monitoring workflows

---

## Progressive Discovery

`NET_PROGRESSIVE_DISCOVERY=true` loads only meta-tools at startup, deferring other tool modules until requested via `eos_load_tool_category()`.

**When to use:** When the full tool set (150+ tools) overwhelms the LLM's context window. Progressive discovery lets the model load only the tool categories it needs.

---

## Output Size Limits

Large responses are automatically truncated to prevent context window overflow. The `enforce_dict_output_limit()` function caps output size while preserving structure.

---

## Benchmark Results

Benchmarks measure MCP server abstraction overhead using mocked device connections (no real network I/O). All tests run with rate limiting disabled to isolate tool processing cost.

### Running benchmarks

```bash
# Run all benchmarks with timing output
uv run pytest tests/benchmarks/ -v --benchmark-enable

# Save results as baseline for future comparison
uv run pytest tests/benchmarks/ --benchmark-enable --benchmark-save=baseline

# Compare against saved baseline
uv run pytest tests/benchmarks/ --benchmark-enable --benchmark-compare

# Generate JSON report
uv run pytest tests/benchmarks/ --benchmark-enable --benchmark-json=benchmark_results.json
```

### Latest results

Measured on Python 3.14, pytest-benchmark 5.2.3, macOS (Apple Silicon). All times are median values with mocked device responses (no network latency).

#### Cache operations

| Benchmark | Median | Ops/sec | Description |
|-----------|-------:|--------:|-------------|
| Cache lookup (miss) | 360 ns | 2,754K | Empty cache lookup — minimal overhead |
| Cache hit | 1.75 µs | 558K | Warm cache return — no device call |
| Cache put + get cycle | 3.46 µs | 285K | Write + read cycle on cache data structure |
| Cache miss (full tool) | 73.4 µs | 11.3K | Tool call with cold cache (mock device query) |

#### Tool throughput (single device, mocked)

| Benchmark | Median | Ops/sec | Description |
|-----------|-------:|--------:|-------------|
| Direct pyeapi call | 4.58 µs | 156K | Baseline: mock node.run_commands() |
| MCP tool call (uncached) | 65.7 µs | 15.1K | Full stack: validation + rate limit + cache + audit |
| get_device_info | 65.7 µs | 12.5K | Simplest read-only tool |
| get_bgp_summary | 67.7 µs | 5.3K | Moderate complexity (VRF lookup, filtering) |
| get_interfaces_status | 69.9 µs | 11.0K | Moderate complexity (field filtering) |
| fabric_health_operation | 73.4 µs | 10.7K | Per-device health (4 show commands) |

#### Abstraction overhead

The MCP abstraction overhead is the difference between a direct mock call and a full tool call:

```
Abstraction overhead = 65.7 µs - 4.58 µs ≈ 61 µs per call
```

This ~61 µs overhead covers: host validation, cache check, circuit breaker check, rate limiter check, connection acquire, retry wrapper, credential sanitization, audit logging, and metrics collection. Well under the 5 ms target.

#### Rate limiter overhead

Rate limiter overhead is included in the abstraction overhead above. When benchmarked in isolation (disabled for throughput tests), the token-bucket check adds ~0.5–1 µs per request

---

## Large-Scale Deployment Tips

For deployments managing 500+ network devices:

### 1. Enable connection pool auto-tuning

Set `max_connections` to `min(inventory_size * 2, 500)`. Beyond 500 connections, use horizontal scaling (multiple server instances) instead.

### 2. Enable circuit breakers

```bash
NET_CIRCUIT_BREAKER=true
NET_CB_THRESHOLD=3
NET_CB_COOLDOWN=60
```

This prevents a few unreachable devices from degrading the entire server with timeout delays.

### 3. Tune cache aggressively

```bash
NET_CACHE_ENABLED=true
NET_CACHE_MAX_ENTRIES=1000
NET_CACHE_MAX_MB=100
```

Large inventories benefit from higher cache limits. Monitor cache hit rate via the `/health` endpoint or `net_server_metrics` tool.

### 4. Use progressive discovery

```bash
NET_PROGRESSIVE_DISCOVERY=true
```

Defers loading of non-essential tool modules until first use. Reduces startup time and initial memory footprint.

### 5. Segment by inventory

Run separate server instances per network segment (e.g., data center, region, environment). Each instance manages a subset of devices via its own `devices.yaml`. This improves fault isolation and allows independent scaling.

### 6. Monitor file descriptors

Each device connection consumes a file descriptor. Ensure the server process has sufficient limits:

```bash
# Check current limit
ulimit -n

# Increase for current session
ulimit -n 4096

# Persistent (systemd)
# Add LimitNOFILE=4096 to the [Service] section
```

### 7. Use HTTP transport with load balancing

For 500+ devices, deploy multiple server instances behind a load balancer. See [Horizontal Scaling](#horizontal-scaling) and [DEPLOYMENT.md](DEPLOYMENT.md) for details.

---

## Horizontal Scaling

For deployments that exceed single-instance capacity, run multiple network-mcp server instances behind a load balancer.

### Architecture

```
                        ┌─────────────────┐
MCP Clients ──────────> │  Load Balancer  │
                        │  (nginx/HAProxy)│
                        └────────┬────────┘
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
              ┌──────────┐ ┌──────────┐ ┌──────────┐
              │ MCP Srv 1│ │ MCP Srv 2│ │ MCP Srv 3│
              │ (DC-East)│ │ (DC-West)│ │ (Campus) │
              └──────────┘ └──────────┘ └──────────┘
                    │            │            │
                    ▼            ▼            ▼
              DC-East        DC-West       Campus
              Devices        Devices       Devices
```

### Requirements

- **HTTP transport only** -- stdio transport is single-client and cannot be load-balanced
- **Inventory partitioning** -- each instance manages a separate set of devices via its own `devices.yaml`
- **Stateless design** -- no shared state between instances; each has its own connection pool, cache, and circuit breakers
- **Sticky sessions** (optional) -- if MCP clients use long-lived SSE connections, configure session affinity on the load balancer

### nginx load balancer example

```nginx
upstream mcp_cluster {
    # Round-robin by default; use ip_hash for sticky sessions
    # ip_hash;
    server mcp-east:8000;
    server mcp-west:8000;
    server mcp-campus:8000;
}

server {
    listen 443 ssl;
    server_name mcp.example.com;

    location / {
        proxy_pass http://mcp_cluster;
        proxy_buffering off;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
    }

    location /health {
        proxy_pass http://mcp_cluster/health;
    }
}
```

### Memory budget planning

Use ~10 KB per device connection when planning memory allocation across instances:

| Total Devices | Instances | Devices/Instance | Memory/Instance |
|--------------|-----------|------------------|----------------:|
| 500 | 2 | 250 | ~55 MB |
| 1,000 | 3 | ~333 | ~55 MB |
| 2,000 | 5 | 400 | ~55 MB |
| 5,000 | 10 | 500 | ~55 MB |

Base memory (~50 MB) dominates at these scales. The primary reason to scale horizontally is connection pool limits and blast radius reduction, not memory.
