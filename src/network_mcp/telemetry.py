"""OpenTelemetry integration for distributed tracing and metrics.

Feature-flagged: Only active when ``opentelemetry-sdk`` is installed AND
``NET_OTEL_ENABLED=true``. Falls back to no-op when disabled.
"""

import logging
import time
from contextlib import contextmanager

logger = logging.getLogger("network-mcp")

# Try to import OTel — fall back to no-ops when not installed
try:
    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

# Module-level tracer and meter (initialized in setup_telemetry)
_tracer = None
_meter = None

# Metric instruments
_tool_call_counter = None
_tool_call_duration = None
_tool_call_errors = None
_connection_pool_gauge = None
_cache_hit_counter = None
_cache_miss_counter = None


def setup_telemetry(service_name: str = "network-mcp", endpoint: str = "") -> bool:
    """Initialize OTel providers, exporters, and metric instruments.

    Args:
        service_name: The service name reported to the OTel collector.
        endpoint: OTLP gRPC endpoint (e.g., ``http://localhost:4317``).
                  Empty string skips exporter setup (useful for testing).

    Returns:
        True if telemetry was successfully initialized, False otherwise.
    """
    global _tracer, _meter
    global _tool_call_counter, _tool_call_duration, _tool_call_errors
    global _connection_pool_gauge, _cache_hit_counter, _cache_miss_counter

    if not OTEL_AVAILABLE:
        logger.info("OpenTelemetry SDK not installed — telemetry disabled")
        return False

    resource = Resource.create({"service.name": service_name})

    # Set up TracerProvider
    tracer_provider = TracerProvider(resource=resource)
    if endpoint:
        span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)
    _tracer = trace.get_tracer("network-mcp")

    # Set up MeterProvider
    readers = []
    if endpoint:
        metric_exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
        readers.append(PeriodicExportingMetricReader(metric_exporter, export_interval_millis=30000))
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(meter_provider)
    _meter = metrics.get_meter("network-mcp")

    # Create metric instruments
    _tool_call_counter = _meter.create_counter(
        name="net.tool.calls",
        description="Total number of tool invocations",
        unit="1",
    )
    _tool_call_duration = _meter.create_histogram(
        name="net.tool.duration",
        description="Tool call execution duration in milliseconds",
        unit="ms",
    )
    _tool_call_errors = _meter.create_counter(
        name="net.tool.errors",
        description="Total number of tool call errors",
        unit="1",
    )
    _connection_pool_gauge = _meter.create_up_down_counter(
        name="net.connection_pool.size",
        description="Current connection pool size",
        unit="1",
    )
    _cache_hit_counter = _meter.create_counter(
        name="net.cache.hits",
        description="Cache hit count",
        unit="1",
    )
    _cache_miss_counter = _meter.create_counter(
        name="net.cache.misses",
        description="Cache miss count",
        unit="1",
    )

    logger.info("OpenTelemetry initialized (service=%s, endpoint=%s)", service_name, endpoint or "(none)")
    return True


@contextmanager
def trace_tool_call(tool_name: str, host: str = "", **attributes):
    """Context manager to trace a tool call with an OTel span.

    Yields the active span (or ``None`` when OTel is not enabled) so callers
    can optionally add custom attributes.

    Usage::

        with trace_tool_call("eos_get_vlans", host="spine-01") as span:
            result = run_show_command(...)
    """
    if _tracer is None:
        yield None
        return

    with _tracer.start_as_current_span(
        f"tool.{tool_name}",
        attributes={"tool.name": tool_name, "device.host": host, **attributes},
    ) as span:
        try:
            yield span
        except Exception as e:
            span.set_status(trace.StatusCode.ERROR, str(e))
            span.record_exception(e)
            raise


def record_tool_call(tool_name: str, status: str, duration_ms: float, host: str = "") -> None:
    """Record tool call metrics (counter + histogram).

    Args:
        tool_name: Name of the tool that was called.
        status: Result status (``"success"`` or ``"error"``).
        duration_ms: Execution time in milliseconds.
        host: Target device hostname (optional label).
    """
    attrs = {"tool": tool_name, "status": status, "host": host}
    if _tool_call_counter is not None:
        _tool_call_counter.add(1, attrs)
    if _tool_call_duration is not None:
        _tool_call_duration.record(duration_ms, attrs)
    if status == "error" and _tool_call_errors is not None:
        _tool_call_errors.add(1, attrs)


def record_cache_hit(hit: bool) -> None:
    """Record a cache hit or miss.

    Args:
        hit: True for cache hit, False for miss.
    """
    if hit and _cache_hit_counter is not None:
        _cache_hit_counter.add(1)
    elif not hit and _cache_miss_counter is not None:
        _cache_miss_counter.add(1)


def record_connection_pool_size(delta: int) -> None:
    """Update connection pool gauge by delta (positive = add, negative = remove).

    Args:
        delta: Change in pool size.
    """
    if _connection_pool_gauge is not None:
        _connection_pool_gauge.add(delta)


def get_tracer():
    """Return the module-level tracer, or None if not initialized."""
    return _tracer


def get_meter():
    """Return the module-level meter, or None if not initialized."""
    return _meter


def shutdown() -> None:
    """Shut down OTel providers, flushing pending spans and metrics."""
    if not OTEL_AVAILABLE:
        return
    try:
        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
    except Exception:
        logger.debug("Error shutting down tracer provider", exc_info=True)
    try:
        provider = metrics.get_meter_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
    except Exception:
        logger.debug("Error shutting down meter provider", exc_info=True)


def _reset() -> None:
    """Reset all module-level state. For testing only."""
    global _tracer, _meter
    global _tool_call_counter, _tool_call_duration, _tool_call_errors
    global _connection_pool_gauge, _cache_hit_counter, _cache_miss_counter

    _tracer = None
    _meter = None
    _tool_call_counter = None
    _tool_call_duration = None
    _tool_call_errors = None
    _connection_pool_gauge = None
    _cache_hit_counter = None
    _cache_miss_counter = None


class _TimedToolCall:
    """Helper to measure and record tool call duration with OTel tracing.

    Usage::

        timer = _TimedToolCall("run_show_command", host="spine-01")
        # ... do work ...
        timer.finish("success")
    """

    __slots__ = ("_tool_name", "_host", "_start")

    def __init__(self, tool_name: str, host: str = "") -> None:
        self._tool_name = tool_name
        self._host = host
        self._start = time.monotonic()

    def finish(self, status: str) -> float:
        """Record the tool call metric and return the duration in ms."""
        duration_ms = (time.monotonic() - self._start) * 1000
        record_tool_call(self._tool_name, status, duration_ms, self._host)
        return duration_ms
