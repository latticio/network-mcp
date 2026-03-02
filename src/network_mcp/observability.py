"""Server observability: metrics collection, health checks, and MCP logging utilities.

Provides MetricsCollector for tracking tool calls, cache stats, and rate limiter events.
Thread-safe counters using threading.Lock. Prometheus exposition format is optional —
only available when prometheus-client is installed.
"""

import logging
import re
import threading
import time

logger = logging.getLogger("network-mcp")

# Server start time for uptime tracking
_server_start_time = time.monotonic()

# Check for optional prometheus-client
try:
    import prometheus_client  # noqa: F401

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


# --- Prometheus label/metric name safety ---

_LABEL_MAX_LENGTH = 128
_METRIC_NAME_RE = re.compile(r"[^a-zA-Z0-9_:]")


def _escape_label_value(value: str) -> str:
    """Escape a Prometheus label value per the exposition format spec.

    Replaces backslashes, double-quotes, and newlines with their escaped forms.
    Truncates values longer than 128 characters.
    """
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("\n", "\\n")
    if len(value) > _LABEL_MAX_LENGTH:
        value = value[:_LABEL_MAX_LENGTH]
    return value


def _sanitize_metric_name(name: str) -> str:
    """Sanitize a Prometheus metric name.

    Replaces any character not in ``[a-zA-Z0-9_:]`` with ``_``.
    Prepends ``_`` if the name starts with a digit.
    """
    name = _METRIC_NAME_RE.sub("_", name)
    if name and name[0].isdigit():
        name = "_" + name
    return name


# --- Cardinality protection ---

_CARDINALITY_LIMIT = 100


class CardinalityTracker:
    """Tracks unique label combinations and enforces cardinality limits."""

    def __init__(self, limit: int = _CARDINALITY_LIMIT):
        self._limit = limit
        self._lock = threading.Lock()
        self._seen: dict[str, set[tuple]] = {}  # metric_name -> set of label tuples

    def check(self, metric_name: str, labels: dict[str, str]) -> bool:
        """Return True if this label combination is allowed (under limit)."""
        label_tuple = tuple(sorted(labels.items()))
        with self._lock:
            if metric_name not in self._seen:
                self._seen[metric_name] = set()
            seen = self._seen[metric_name]
            if label_tuple in seen:
                return True
            if len(seen) >= self._limit:
                return False  # cardinality limit reached
            seen.add(label_tuple)
            return True

    def reset(self) -> None:
        """Clear all tracked label combinations."""
        with self._lock:
            self._seen.clear()


_cardinality_tracker = CardinalityTracker()

# Histogram bucket boundaries (upper bounds in seconds)
_HISTOGRAM_BUCKETS = (0.1, 0.5, 1.0, 2.5, 5.0, 10.0)


class MetricsCollector:
    """Thread-safe metrics collector for server observability.

    Tracks tool call counts, durations, cache hits/misses, rate limiter events,
    error type breakdowns, and circuit breaker state transitions.
    All counters are protected by a single lock with small critical sections.
    """

    # Maximum duration samples per tool to prevent unbounded memory growth
    _MAX_DURATION_SAMPLES = 10_000

    # Maximum age (seconds) for per-device metrics before cleanup
    _METRIC_MAX_AGE = 3600  # 1 hour

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # tool_name -> {status -> count}
        self._tool_calls: dict[str, dict[str, int]] = {}
        # tool_name -> list of durations in seconds (windowed to _MAX_DURATION_SAMPLES)
        self._tool_durations: dict[str, list[float]] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        self._rate_limit_throttled = 0
        # Error type breakdown: error_type -> count
        self._error_type_counts: dict[str, int] = {}
        # Circuit breaker state transitions: "state" -> count
        self._circuit_breaker_transitions: dict[str, int] = {}
        # Per-device last-seen timestamps for TTL-based cleanup
        self._device_last_seen: dict[str, float] = {}
        # Per-device tool call counts: device -> {tool_name -> count}
        self._device_tool_calls: dict[str, dict[str, int]] = {}
        # Per-vendor tool call counts: vendor -> {status -> count}
        self._vendor_calls: dict[str, dict[str, int]] = {}
        # Circuit breaker per-device state snapshot: device -> int (0=healthy, 1=open, 2=half_open)
        self._circuit_breaker_device_states: dict[str, int] = {}
        # Connection pool gauge values (set before prometheus export)
        self._connection_pool_size: int = 0
        self._active_connections: int = 0
        # Per-tool latency histogram buckets: tool_name -> {bucket_le -> count}
        self._tool_histogram_buckets: dict[str, dict[str, int]] = {}
        # Per-tool histogram sum and count: tool_name -> {"sum": float, "count": int}
        self._tool_histogram_stats: dict[str, dict[str, float]] = {}

    def record_tool_call(
        self,
        tool_name: str,
        status: str,
        duration_ms: float,
        vendor: str = "eos",
        error_code: str | None = None,
    ) -> None:
        """Record a tool invocation with its status, duration, and vendor.

        Args:
            tool_name: Name of the tool that was called.
            status: Result status ("success", "error", "partial").
            duration_ms: Execution time in milliseconds.
            vendor: Vendor platform identifier (e.g. "eos", "cisco", "juniper").
            error_code: Optional structured error code from the error taxonomy.
        """
        # Cardinality check — overflow to a sentinel label if limit is reached
        if not _cardinality_tracker.check("net_tool_calls_total", {"tool": tool_name, "status": status}):
            tool_name = "_cardinality_overflow"

        duration_s = duration_ms / 1000.0
        with self._lock:
            if tool_name not in self._tool_calls:
                self._tool_calls[tool_name] = {}
            self._tool_calls[tool_name][status] = self._tool_calls[tool_name].get(status, 0) + 1

            if tool_name not in self._tool_durations:
                self._tool_durations[tool_name] = []
            durations = self._tool_durations[tool_name]
            durations.append(duration_s)
            # Prune to window size to prevent unbounded memory growth
            if len(durations) > self._MAX_DURATION_SAMPLES:
                self._tool_durations[tool_name] = durations[-self._MAX_DURATION_SAMPLES :]

            # Update histogram buckets
            if tool_name not in self._tool_histogram_buckets:
                self._tool_histogram_buckets[tool_name] = {str(b): 0 for b in _HISTOGRAM_BUCKETS}
                self._tool_histogram_buckets[tool_name]["+Inf"] = 0
                self._tool_histogram_stats[tool_name] = {"sum": 0.0, "count": 0}
            for bucket in _HISTOGRAM_BUCKETS:
                if duration_s <= bucket:
                    self._tool_histogram_buckets[tool_name][str(bucket)] += 1
            self._tool_histogram_buckets[tool_name]["+Inf"] += 1
            self._tool_histogram_stats[tool_name]["sum"] += duration_s
            self._tool_histogram_stats[tool_name]["count"] += 1

            # Track per-vendor counts
            if vendor not in self._vendor_calls:
                self._vendor_calls[vendor] = {}
            self._vendor_calls[vendor][status] = self._vendor_calls[vendor].get(status, 0) + 1

            # Track error code breakdown when provided
            if error_code is not None:
                self._error_type_counts[error_code] = self._error_type_counts.get(error_code, 0) + 1

    def record_tool_call_for_device(self, device: str, tool_name: str) -> None:
        """Record a tool call associated with a specific device (for TTL cleanup tracking).

        Args:
            device: Device hostname or IP.
            tool_name: Name of the tool.
        """
        with self._lock:
            self._device_last_seen[device] = time.monotonic()
            if device not in self._device_tool_calls:
                self._device_tool_calls[device] = {}
            self._device_tool_calls[device][tool_name] = self._device_tool_calls[device].get(tool_name, 0) + 1

    def record_cache_hit(self) -> None:
        """Record a cache hit event."""
        with self._lock:
            self._cache_hits += 1

    def record_cache_miss(self) -> None:
        """Record a cache miss event."""
        with self._lock:
            self._cache_misses += 1

    def record_rate_limit_throttle(self) -> None:
        """Record a rate limiter throttle event."""
        with self._lock:
            self._rate_limit_throttled += 1

    def record_error_type(self, error_type: str) -> None:
        """Record an error by type for breakdown tracking.

        Args:
            error_type: Error category (e.g. "ConnectionError", "CommandError",
                        "TimeoutError", "RBACDenied").
        """
        with self._lock:
            self._error_type_counts[error_type] = self._error_type_counts.get(error_type, 0) + 1

    def record_circuit_breaker_transition(self, state: str) -> None:
        """Record a circuit breaker state transition.

        Args:
            state: Target state (e.g. "opened", "closed", "half_open").
        """
        with self._lock:
            self._circuit_breaker_transitions[state] = self._circuit_breaker_transitions.get(state, 0) + 1

    def set_circuit_breaker_states(self, states: dict[str, int]) -> None:
        """Set per-device circuit breaker state values for Prometheus export.

        Args:
            states: Mapping of device -> state integer (0=healthy, 1=open, 2=half_open).
        """
        self._circuit_breaker_device_states = dict(states)

    def set_connection_pool_stats(self, pool_size: int, active: int) -> None:
        """Set connection pool gauge values for Prometheus export.

        Args:
            pool_size: Total pool capacity.
            active: Currently active connections.
        """
        self._connection_pool_size = pool_size
        self._active_connections = active

    def cleanup_stale_device_metrics(self, max_age: float | None = None) -> int:
        """Remove per-device metrics older than max_age seconds.

        Args:
            max_age: Maximum age in seconds. Defaults to _METRIC_MAX_AGE (1 hour).

        Returns:
            Number of stale devices pruned.
        """
        cutoff_age = max_age if max_age is not None else self._METRIC_MAX_AGE
        now = time.monotonic()
        pruned = 0
        with self._lock:
            stale_devices = [
                device for device, last_seen in self._device_last_seen.items() if (now - last_seen) > cutoff_age
            ]
            for device in stale_devices:
                del self._device_last_seen[device]
                self._device_tool_calls.pop(device, None)
                pruned += 1
        return pruned

    def get_metrics_json(self) -> dict:
        """Return all metrics as a JSON-serializable dict."""
        with self._lock:
            tool_calls = {}
            for tool_name, statuses in self._tool_calls.items():
                tool_calls[tool_name] = dict(statuses)

            tool_durations = {}
            for tool_name, durations in self._tool_durations.items():
                if durations:
                    sorted_d = sorted(durations)
                    count = len(sorted_d)
                    tool_durations[tool_name] = {
                        "count": count,
                        "sum_seconds": round(sum(sorted_d), 4),
                        "avg_seconds": round(sum(sorted_d) / count, 4),
                        "min_seconds": round(sorted_d[0], 4),
                        "max_seconds": round(sorted_d[-1], 4),
                        "p50_seconds": round(sorted_d[count // 2], 4),
                        "p99_seconds": round(sorted_d[min(int(count * 0.99), count - 1)], 4),
                    }

            vendor_calls = {vendor: dict(statuses) for vendor, statuses in self._vendor_calls.items()}

            return {
                "tool_calls_total": tool_calls,
                "tool_duration_seconds": tool_durations,
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "rate_limit_throttled": self._rate_limit_throttled,
                "error_types": dict(self._error_type_counts),
                "circuit_breaker_transitions": dict(self._circuit_breaker_transitions),
                "tracked_devices": len(self._device_last_seen),
                "vendor_calls": vendor_calls,
            }

    def get_latency_histogram(self, tool_name: str) -> dict:
        """Return latency histogram bucket counts for a specific tool.

        Args:
            tool_name: Name of the tool.

        Returns:
            Dict with bucket counts, sum, and count. Empty buckets if tool not tracked.
        """
        with self._lock:
            buckets = self._tool_histogram_buckets.get(tool_name, {})
            stats = self._tool_histogram_stats.get(tool_name, {"sum": 0.0, "count": 0})
            return {
                "bucket_le_0_1": buckets.get("0.1", 0),
                "bucket_le_0_5": buckets.get("0.5", 0),
                "bucket_le_1": buckets.get("1.0", 0),
                "bucket_le_2_5": buckets.get("2.5", 0),
                "bucket_le_5": buckets.get("5.0", 0),
                "bucket_le_10": buckets.get("10.0", 0),
                "bucket_le_inf": buckets.get("+Inf", 0),
                "sum": stats["sum"],
                "count": int(stats["count"]),
            }

    def get_metrics_prometheus(self) -> str:
        """Return metrics in Prometheus exposition format.

        Returns a manually generated Prometheus text format string following the
        OpenMetrics / Prometheus exposition format (text/plain; version=0.0.4).
        Works regardless of whether prometheus-client is installed.

        Emits the following metric families:
        - net_up (gauge)
        - net_server_info{version, read_only, demo_mode} (gauge)
        - network_mcp_tool_calls_total{tool, status}
        - network_mcp_vendor_calls_total{vendor, status}
        - network_mcp_tool_call_duration_seconds{tool, quantile} (summary)
        - net_tool_duration_seconds{tool} (histogram)
        - network_mcp_cache_hits_total / network_mcp_cache_misses_total
        - network_mcp_rate_limit_throttled_total
        - network_mcp_circuit_breaker_state{device} (gauge)
        - network_mcp_connection_pool_size (gauge)
        - network_mcp_active_connections (gauge)
        - network_mcp_errors_total{type}
        - network_mcp_uptime_seconds (gauge)
        """
        lines: list[str] = []
        _esc = _escape_label_value

        # --- Server up gauge ---
        lines.append("# HELP net_up Whether the network MCP server is up (1 = running).")
        lines.append("# TYPE net_up gauge")
        lines.append("net_up 1")

        # --- Server info gauge ---
        version = _esc(get_version() or "unknown")
        try:
            from network_mcp.config import NetworkSettings

            _s = NetworkSettings()
            read_only = str(_s.net_read_only).lower()
            demo_mode = str(_s.net_demo_mode).lower()
        except Exception:
            read_only = "unknown"
            demo_mode = "unknown"
        lines.append("# HELP net_server_info Server version and configuration metadata.")
        lines.append("# TYPE net_server_info gauge")
        lines.append(
            f'net_server_info{{version="{_esc(version)}",'
            f'read_only="{_esc(read_only)}",'
            f'demo_mode="{_esc(demo_mode)}"}} 1'
        )

        with self._lock:
            # --- Tool calls counter ---
            lines.append("# HELP network_mcp_tool_calls_total Total tool invocations by tool, status, and vendor")
            lines.append("# TYPE network_mcp_tool_calls_total counter")
            for tool_name, statuses in self._tool_calls.items():
                for status, count in statuses.items():
                    lines.append(
                        f'network_mcp_tool_calls_total{{tool="{_esc(tool_name)}",status="{_esc(status)}"}} {count}'
                    )

            # --- Per-vendor tool calls ---
            lines.append("# HELP network_mcp_vendor_calls_total Tool invocations by vendor and status")
            lines.append("# TYPE network_mcp_vendor_calls_total counter")
            for vendor, statuses in self._vendor_calls.items():
                for status, count in statuses.items():
                    lines.append(
                        f'network_mcp_vendor_calls_total{{vendor="{_esc(vendor)}",status="{_esc(status)}"}} {count}'
                    )

            # --- Tool duration summary with quantiles ---
            lines.append("# HELP network_mcp_tool_call_duration_seconds Tool call execution duration in seconds")
            lines.append("# TYPE network_mcp_tool_call_duration_seconds summary")
            for tool_name, durations in self._tool_durations.items():
                if durations:
                    sorted_d = sorted(durations)
                    count = len(sorted_d)
                    total = sum(sorted_d)
                    for q in (0.5, 0.9, 0.99):
                        idx = min(int(count * q), count - 1)
                        lines.append(
                            f'network_mcp_tool_call_duration_seconds{{tool="{_esc(tool_name)}",'
                            f'quantile="{q}"}} {sorted_d[idx]:.4f}'
                        )
                    lines.append(f'network_mcp_tool_call_duration_seconds_sum{{tool="{_esc(tool_name)}"}} {total:.4f}')
                    lines.append(f'network_mcp_tool_call_duration_seconds_count{{tool="{_esc(tool_name)}"}} {count}')

            # --- Per-tool latency histogram ---
            lines.append("# HELP net_tool_duration_seconds Tool call duration distribution.")
            lines.append("# TYPE net_tool_duration_seconds histogram")
            for tool_name in sorted(self._tool_histogram_buckets.keys()):
                buckets = self._tool_histogram_buckets[tool_name]
                stats = self._tool_histogram_stats[tool_name]
                for b in _HISTOGRAM_BUCKETS:
                    lines.append(
                        f'net_tool_duration_seconds_bucket{{tool="{_esc(tool_name)}",'
                        f'le="{b}"}} {buckets.get(str(b), 0)}'
                    )
                lines.append(
                    f'net_tool_duration_seconds_bucket{{tool="{_esc(tool_name)}",le="+Inf"}} {buckets.get("+Inf", 0)}'
                )
                lines.append(f'net_tool_duration_seconds_sum{{tool="{_esc(tool_name)}"}} {stats["sum"]:.4f}')
                lines.append(f'net_tool_duration_seconds_count{{tool="{_esc(tool_name)}"}} {int(stats["count"])}')

            # --- Cache counters ---
            lines.append("# HELP network_mcp_cache_hits_total Cache hit count")
            lines.append("# TYPE network_mcp_cache_hits_total counter")
            lines.append(f"network_mcp_cache_hits_total {self._cache_hits}")

            lines.append("# HELP network_mcp_cache_misses_total Cache miss count")
            lines.append("# TYPE network_mcp_cache_misses_total counter")
            lines.append(f"network_mcp_cache_misses_total {self._cache_misses}")

            # --- Rate limiter ---
            lines.append("# HELP network_mcp_rate_limit_throttled_total Rate limit rejections")
            lines.append("# TYPE network_mcp_rate_limit_throttled_total counter")
            lines.append(f"network_mcp_rate_limit_throttled_total {self._rate_limit_throttled}")

            # --- Error type breakdown ---
            lines.append("# HELP network_mcp_errors_total Errors by type")
            lines.append("# TYPE network_mcp_errors_total counter")
            for error_type, count in self._error_type_counts.items():
                lines.append(f'network_mcp_errors_total{{type="{_esc(error_type)}"}} {count}')

            # --- Circuit breaker transitions ---
            lines.append("# HELP network_mcp_circuit_breaker_transitions_total Circuit breaker state transitions")
            lines.append("# TYPE network_mcp_circuit_breaker_transitions_total counter")
            for state, count in self._circuit_breaker_transitions.items():
                lines.append(f'network_mcp_circuit_breaker_transitions_total{{state="{_esc(state)}"}} {count}')

        # --- Circuit breaker per-device state gauge (outside _lock — uses CB registry) ---
        lines.append(
            "# HELP network_mcp_circuit_breaker_state Per-device circuit breaker state (0=healthy, 1=open, 2=half_open)"
        )
        lines.append("# TYPE network_mcp_circuit_breaker_state gauge")
        for device, state_val in self._circuit_breaker_device_states.items():
            lines.append(f'network_mcp_circuit_breaker_state{{device="{_esc(device)}"}} {state_val}')

        # --- Connection pool gauges ---
        lines.append("# HELP network_mcp_connection_pool_size Total connection pool capacity")
        lines.append("# TYPE network_mcp_connection_pool_size gauge")
        lines.append(f"network_mcp_connection_pool_size {self._connection_pool_size}")

        lines.append("# HELP network_mcp_active_connections Currently active connections")
        lines.append("# TYPE network_mcp_active_connections gauge")
        lines.append(f"network_mcp_active_connections {self._active_connections}")

        # --- Uptime ---
        lines.append("# HELP network_mcp_uptime_seconds Server uptime in seconds")
        lines.append("# TYPE network_mcp_uptime_seconds gauge")
        lines.append(f"network_mcp_uptime_seconds {get_uptime_seconds()}")

        lines.append("")  # trailing newline
        return "\n".join(lines)

    def reset(self) -> None:
        """Reset all metrics. Useful for testing."""
        with self._lock:
            self._tool_calls.clear()
            self._tool_durations.clear()
            self._cache_hits = 0
            self._cache_misses = 0
            self._rate_limit_throttled = 0
            self._error_type_counts.clear()
            self._circuit_breaker_transitions.clear()
            self._device_last_seen.clear()
            self._device_tool_calls.clear()
            self._vendor_calls.clear()
            self._circuit_breaker_device_states.clear()
            self._connection_pool_size = 0
            self._active_connections = 0
            self._tool_histogram_buckets.clear()
            self._tool_histogram_stats.clear()
        _cardinality_tracker.reset()


def get_uptime_seconds() -> float:
    """Return server uptime in seconds since module load."""
    return round(time.monotonic() - _server_start_time, 1)


def get_server_health(
    conn_mgr,
    cache,
    circuit_breaker_registry,
    metrics_collector: MetricsCollector,
    module_status: dict | None = None,
) -> dict:
    """Build the server health response.

    Args:
        conn_mgr: ConnectionManager instance.
        cache: CommandCache instance.
        circuit_breaker_registry: CircuitBreakerRegistry instance.
        metrics_collector: MetricsCollector instance.
        module_status: Optional dict with "loaded_modules" and "failed_modules" keys.

    Returns:
        Health dict matching the V3 plan §14 format.
    """
    pool = conn_mgr.pool_stats()
    cache_stats = cache.stats()
    cb_stats = circuit_breaker_registry.stats()
    metrics = metrics_collector.get_metrics_json()

    open_circuits = cb_stats.get("open_circuits", [])
    failed_modules = module_status.get("failed_modules", {}) if module_status else {}
    status = "degraded" if open_circuits or failed_modules else "healthy"

    health: dict = {
        "status": status,
        "version": get_version(),
        "uptime_seconds": get_uptime_seconds(),
        "connections": {
            "active": pool.get("active", 0),
            "pooled": pool.get("pooled", 0),
            "max": pool.get("max", 0),
        },
        "circuit_breakers": {
            "enabled": cb_stats.get("enabled", False),
            "open": len(open_circuits),
            "devices": open_circuits,
        },
        "cache": {
            "enabled": cache_stats.get("enabled", True),
            "entries": cache_stats.get("entries", 0),
            "hit_rate": cache_stats.get("hit_rate", 0.0),
            "memory_mb": cache_stats.get("memory_mb", 0.0),
        },
        "rate_limiter": {
            "throttled_total": metrics.get("rate_limit_throttled", 0),
        },
        "vendor_calls": metrics.get("vendor_calls", {}),
    }

    if module_status is not None:
        health["modules"] = {
            "loaded": module_status.get("loaded_modules", []),
            "failed": failed_modules,
        }

    # SLO status (when SLI tracker is initialized)
    from network_mcp.slo import DEFAULT_SLOS, SLOEvaluator, get_sli_tracker

    tracker = get_sli_tracker()
    if tracker:
        evaluator = SLOEvaluator(DEFAULT_SLOS, tracker)
        health["slo_status"] = evaluator.evaluate_all()

    return health


def get_version() -> str:
    """Return the package version string.

    Single authoritative version resolver for the entire package.
    Uses importlib.metadata first (installed package), falls back to __version__.
    """
    try:
        from importlib.metadata import version

        return version("network-mcp")
    except Exception:
        from network_mcp import __version__

        return __version__


def mcp_log(level: str, message: str, logger_instance: logging.Logger | None = None) -> None:
    """Log a message using both Python logging and a format suitable for MCP notifications/message.

    This is a synchronous helper. For actual MCP ctx.log() calls in async tool
    functions, use `await ctx.log(level, message)` directly.

    For non-async code paths (helpers, connection management), this logs to stderr
    via the standard Python logger. The MCP protocol notifications/message is only
    available within async tool context.

    Args:
        level: Log level string ("debug", "info", "warning", "error").
        message: Log message.
        logger_instance: Optional logger to use. Defaults to the module logger.
    """
    log = logger_instance or logger
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    log.log(level_map.get(level.lower(), logging.INFO), message)


# Module-level singleton
metrics_collector = MetricsCollector()
