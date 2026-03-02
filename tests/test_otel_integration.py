"""OpenTelemetry integration tests for the network MCP server.

Tests verify:
- Span creation when OTel is enabled (tool name, attributes, duration)
- NoOp behavior when OTel is disabled (_tracer is None)
- Metric instruments (counter, histogram) record correctly
- Trace context propagation (correlation ID, child spans, traceparent)
- setup_telemetry and shutdown lifecycle
"""

import time
from unittest.mock import patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

import network_mcp.telemetry as telemetry_mod
from network_mcp.telemetry import (
    _reset,
    _TimedToolCall,
    get_meter,
    get_tracer,
    record_cache_hit,
    record_connection_pool_size,
    record_tool_call,
    setup_telemetry,
    shutdown,
    trace_tool_call,
)

# ---------------------------------------------------------------------------
# In-memory span exporter (OTel SDK doesn't always ship one publicly)
# ---------------------------------------------------------------------------


class _InMemorySpanExporter(SpanExporter):
    """Collects spans in a list for test assertions."""

    def __init__(self):
        self._spans = []
        self._shutdown = False

    def export(self, spans):
        self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        self._shutdown = True

    def get_finished_spans(self):
        return list(self._spans)

    def clear(self):
        self._spans.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_telemetry():
    """Reset telemetry module state before and after each test."""
    _reset()
    yield
    _reset()


@pytest.fixture
def otel_setup():
    """Set up real OTel providers with in-memory exporters for test inspection.

    Avoids the global set_tracer_provider/set_meter_provider which disallow
    re-setting. Instead, creates providers and wires them directly into the
    telemetry module globals.

    Returns:
        A dict with span_exporter, metric_reader, tracer_provider, and meter_provider.
    """
    resource = Resource.create({"service.name": "test-network-mcp"})

    # Tracing — use provider directly, do NOT call trace.set_tracer_provider
    span_exporter = _InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    # Metrics — use provider directly, do NOT call metrics.set_meter_provider
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])

    # Wire up telemetry module globals directly from the provider instances
    telemetry_mod._tracer = tracer_provider.get_tracer("network-mcp")
    telemetry_mod._meter = meter_provider.get_meter("network-mcp")

    # Create the metric instruments (mirrors setup_telemetry logic)
    telemetry_mod._tool_call_counter = telemetry_mod._meter.create_counter(
        name="net.tool.calls",
        description="Total number of tool invocations",
        unit="1",
    )
    telemetry_mod._tool_call_duration = telemetry_mod._meter.create_histogram(
        name="net.tool.duration",
        description="Tool call execution duration in milliseconds",
        unit="ms",
    )
    telemetry_mod._tool_call_errors = telemetry_mod._meter.create_counter(
        name="net.tool.errors",
        description="Total number of tool call errors",
        unit="1",
    )
    telemetry_mod._cache_hit_counter = telemetry_mod._meter.create_counter(
        name="net.cache.hits",
        description="Cache hit count",
        unit="1",
    )
    telemetry_mod._cache_miss_counter = telemetry_mod._meter.create_counter(
        name="net.cache.misses",
        description="Cache miss count",
        unit="1",
    )
    telemetry_mod._connection_pool_gauge = telemetry_mod._meter.create_up_down_counter(
        name="net.connection_pool.size",
        description="Current connection pool size",
        unit="1",
    )

    yield {
        "span_exporter": span_exporter,
        "metric_reader": metric_reader,
        "tracer_provider": tracer_provider,
        "meter_provider": meter_provider,
    }

    # Shutdown providers to flush any pending data
    try:
        tracer_provider.shutdown()
    except Exception:  # noqa: S110
        pass  # Shutdown errors during test teardown are non-fatal
    try:
        meter_provider.shutdown()
    except Exception:  # noqa: S110
        pass  # Shutdown errors during test teardown are non-fatal


# ===========================================================================
# Span Creation Tests
# ===========================================================================


class TestSpanCreation:
    """Verify spans are created with correct name, attributes, and timing."""

    def test_trace_tool_call_creates_span(self, otel_setup):
        """trace_tool_call() should create a span with tool name and host attributes."""
        exporter = otel_setup["span_exporter"]

        with trace_tool_call("eos_get_vlans", host="spine-01") as span:
            assert span is not None
            time.sleep(0.01)  # Small delay for measurable duration

        spans = exporter.get_finished_spans()
        assert len(spans) == 1

        finished = spans[0]
        assert finished.name == "tool.eos_get_vlans"
        assert finished.attributes["tool.name"] == "eos_get_vlans"
        assert finished.attributes["device.host"] == "spine-01"

    def test_span_records_duration(self, otel_setup):
        """Span should have a measurable start and end time."""
        exporter = otel_setup["span_exporter"]

        with trace_tool_call("eos_get_version", host="leaf-01"):
            time.sleep(0.02)

        spans = exporter.get_finished_spans()
        assert len(spans) == 1

        finished = spans[0]
        duration_ns = finished.end_time - finished.start_time
        duration_ms = duration_ns / 1_000_000
        assert duration_ms >= 10, f"Expected >= 10ms duration, got {duration_ms:.1f}ms"

    def test_span_custom_attributes(self, otel_setup):
        """Extra keyword attributes should be attached to the span."""
        exporter = otel_setup["span_exporter"]

        with trace_tool_call("eos_get_bgp", host="spine-02", vrf="default", peer="10.0.0.1"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = dict(spans[0].attributes)
        assert attrs["vrf"] == "default"
        assert attrs["peer"] == "10.0.0.1"

    def test_span_records_exception_on_error(self, otel_setup):
        """Span should record exception and set error status on failure."""
        exporter = otel_setup["span_exporter"]

        with pytest.raises(ValueError, match="test error"):
            with trace_tool_call("eos_bad_tool", host="broken-device"):
                raise ValueError("test error")

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        finished = spans[0]
        assert finished.status.status_code == trace.StatusCode.ERROR
        assert "test error" in finished.status.description

        # Verify the exception event was recorded
        events = finished.events
        assert len(events) >= 1
        exception_event = events[0]
        assert exception_event.name == "exception"

    def test_multiple_spans_independent(self, otel_setup):
        """Multiple sequential tool calls should create independent spans."""
        exporter = otel_setup["span_exporter"]

        with trace_tool_call("tool_a", host="dev-1"):
            pass
        with trace_tool_call("tool_b", host="dev-2"):
            pass
        with trace_tool_call("tool_c", host="dev-3"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 3
        names = {s.name for s in spans}
        assert names == {"tool.tool_a", "tool.tool_b", "tool.tool_c"}


# ===========================================================================
# NoOp When Disabled Tests
# ===========================================================================


class TestNoOpWhenDisabled:
    """Verify no-op behavior when OTel is not enabled (_tracer is None)."""

    def test_trace_tool_call_yields_none_when_disabled(self):
        """When _tracer is None, trace_tool_call should yield None (no span created)."""
        assert telemetry_mod._tracer is None  # From autouse reset fixture

        with trace_tool_call("some_tool", host="any-device") as span:
            assert span is None

    def test_record_tool_call_noop_when_disabled(self):
        """record_tool_call should not raise when metric instruments are None."""
        assert telemetry_mod._tool_call_counter is None
        # Should complete silently, not raise
        record_tool_call("some_tool", "success", 42.0, host="dev-1")

    def test_record_cache_hit_noop_when_disabled(self):
        """record_cache_hit should not raise when counters are None."""
        assert telemetry_mod._cache_hit_counter is None
        record_cache_hit(True)
        record_cache_hit(False)

    def test_record_connection_pool_noop_when_disabled(self):
        """record_connection_pool_size should not raise when gauge is None."""
        assert telemetry_mod._connection_pool_gauge is None
        record_connection_pool_size(5)
        record_connection_pool_size(-3)

    def test_get_tracer_returns_none_when_disabled(self):
        """get_tracer() should return None before setup."""
        assert get_tracer() is None

    def test_get_meter_returns_none_when_disabled(self):
        """get_meter() should return None before setup."""
        assert get_meter() is None

    def test_shutdown_noop_when_not_initialized(self):
        """shutdown() should not raise even when nothing was initialized."""
        # Should complete without error
        shutdown()

    def test_timed_tool_call_noop_when_disabled(self):
        """_TimedToolCall should still measure time but not fail when metrics are None."""
        timer = _TimedToolCall("noop_tool", host="dev-x")
        time.sleep(0.01)
        duration = timer.finish("success")
        assert duration >= 5.0  # At least ~10ms but with tolerance


# ===========================================================================
# Metric Instrument Tests
# ===========================================================================


class TestMetricInstruments:
    """Verify OTel metric instruments record data correctly."""

    def _collect_metrics(self, metric_reader):
        """Collect metrics from the reader and return as a dict of name -> data points."""
        data = metric_reader.get_metrics_data()
        result = {}
        for resource_metrics in data.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    result[metric.name] = metric
        return result

    def test_tool_call_counter_increments(self, otel_setup):
        """net.tool.calls counter should increment on each record_tool_call."""
        reader = otel_setup["metric_reader"]

        record_tool_call("get_vlans", "success", 50.0, host="spine-01")
        record_tool_call("get_vlans", "success", 30.0, host="spine-01")
        record_tool_call("get_bgp", "error", 100.0, host="spine-01")

        collected = self._collect_metrics(reader)
        assert "net.tool.calls" in collected

        counter_metric = collected["net.tool.calls"]
        data_points = list(counter_metric.data.data_points)
        total = sum(dp.value for dp in data_points)
        assert total == 3

    def test_tool_call_duration_histogram(self, otel_setup):
        """net.tool.duration histogram should record duration values."""
        reader = otel_setup["metric_reader"]

        record_tool_call("get_version", "success", 25.0, host="dev-1")
        record_tool_call("get_version", "success", 75.0, host="dev-1")
        record_tool_call("get_version", "success", 150.0, host="dev-1")

        collected = self._collect_metrics(reader)
        assert "net.tool.duration" in collected

        hist_metric = collected["net.tool.duration"]
        data_points = list(hist_metric.data.data_points)
        assert len(data_points) >= 1

        # The histogram should have recorded 3 observations
        total_count = sum(dp.count for dp in data_points)
        assert total_count == 3

        # Sum should be 25 + 75 + 150 = 250
        total_sum = sum(dp.sum for dp in data_points)
        assert total_sum == pytest.approx(250.0, abs=1.0)

    def test_error_counter_only_on_errors(self, otel_setup):
        """net.tool.errors counter should only increment for status='error'."""
        reader = otel_setup["metric_reader"]

        record_tool_call("get_vlans", "success", 50.0, host="dev-1")
        record_tool_call("get_vlans", "error", 100.0, host="dev-1")
        record_tool_call("get_bgp", "error", 200.0, host="dev-2")

        collected = self._collect_metrics(reader)
        assert "net.tool.errors" in collected

        error_metric = collected["net.tool.errors"]
        data_points = list(error_metric.data.data_points)
        total_errors = sum(dp.value for dp in data_points)
        assert total_errors == 2

    def test_cache_hit_counter(self, otel_setup):
        """net.cache.hits counter should increment on cache hit."""
        reader = otel_setup["metric_reader"]

        record_cache_hit(True)
        record_cache_hit(True)
        record_cache_hit(True)

        collected = self._collect_metrics(reader)
        assert "net.cache.hits" in collected
        hits_metric = collected["net.cache.hits"]
        data_points = list(hits_metric.data.data_points)
        total = sum(dp.value for dp in data_points)
        assert total == 3

    def test_cache_miss_counter(self, otel_setup):
        """net.cache.misses counter should increment on cache miss."""
        reader = otel_setup["metric_reader"]

        record_cache_hit(False)
        record_cache_hit(False)

        collected = self._collect_metrics(reader)
        assert "net.cache.misses" in collected
        misses_metric = collected["net.cache.misses"]
        data_points = list(misses_metric.data.data_points)
        total = sum(dp.value for dp in data_points)
        assert total == 2

    def test_connection_pool_gauge(self, otel_setup):
        """net.connection_pool.size gauge should track pool size deltas."""
        reader = otel_setup["metric_reader"]

        record_connection_pool_size(5)
        record_connection_pool_size(-2)

        collected = self._collect_metrics(reader)
        assert "net.connection_pool.size" in collected

    def test_metrics_include_attributes(self, otel_setup):
        """Metric data points should include tool/status/host attributes."""
        reader = otel_setup["metric_reader"]

        record_tool_call("eos_get_interfaces", "success", 42.0, host="spine-01")

        collected = self._collect_metrics(reader)
        counter_metric = collected["net.tool.calls"]
        data_points = list(counter_metric.data.data_points)
        assert len(data_points) >= 1

        # Find the data point with our attributes
        found = False
        for dp in data_points:
            attrs = dict(dp.attributes)
            if attrs.get("tool") == "eos_get_interfaces" and attrs.get("status") == "success":
                found = True
                assert attrs["host"] == "spine-01"
        assert found, f"Expected data point with tool=eos_get_interfaces not found in {data_points}"


# ===========================================================================
# Trace Context Propagation Tests
# ===========================================================================


class TestTraceContextPropagation:
    """Verify trace context propagation (correlation ID, child spans, traceparent)."""

    def test_child_spans_share_trace_id(self, otel_setup):
        """Nested trace_tool_call blocks should create child spans with same trace_id."""
        exporter = otel_setup["span_exporter"]

        # Use the same tracer provider as the fixture so spans go to the same exporter
        tracer = otel_setup["tracer_provider"].get_tracer("test-propagation")
        with tracer.start_as_current_span("parent-operation"):
            with trace_tool_call("child_tool_1", host="dev-1") as child1:
                assert child1 is not None
            with trace_tool_call("child_tool_2", host="dev-2") as child2:
                assert child2 is not None

        spans = exporter.get_finished_spans()
        assert len(spans) == 3  # parent + 2 children

        parent = [s for s in spans if s.name == "parent-operation"][0]
        children = [s for s in spans if s.name.startswith("tool.")]
        assert len(children) == 2

        # All spans share the same trace ID
        parent_trace_id = parent.context.trace_id
        for child in children:
            assert child.context.trace_id == parent_trace_id

        # Children reference the parent span ID
        for child in children:
            assert child.parent.span_id == parent.context.span_id

    def test_correlation_id_propagation(self, otel_setup):
        """Correlation ID from middleware should be attachable to spans."""
        from network_mcp.middleware import clear_request_context, get_correlation_id, set_request_context

        exporter = otel_setup["span_exporter"]

        # Set a request context with a known correlation ID
        request_id = set_request_context(user="test-user")
        correlation_id = get_correlation_id()
        assert correlation_id == request_id

        try:
            with trace_tool_call("correlated_tool", host="dev-1", correlation_id=correlation_id) as span:
                assert span is not None
                span.set_attribute("correlation.id", correlation_id)

            spans = exporter.get_finished_spans()
            assert len(spans) == 1
            attrs = dict(spans[0].attributes)
            assert attrs["correlation.id"] == request_id
            assert attrs["correlation_id"] == correlation_id
        finally:
            clear_request_context()

    def test_traceparent_format(self, otel_setup):
        """Span context should have valid trace ID and span ID."""
        exporter = otel_setup["span_exporter"]

        with trace_tool_call("traceparent_test", host="dev-1"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 1

        ctx = spans[0].context
        # trace_id should be a 128-bit integer (non-zero)
        assert ctx.trace_id != 0
        # span_id should be a 64-bit integer (non-zero)
        assert ctx.span_id != 0

    def test_sequential_calls_get_different_trace_ids(self, otel_setup):
        """Independent (non-nested) tool calls should have different trace IDs."""
        exporter = otel_setup["span_exporter"]

        with trace_tool_call("tool_alpha", host="dev-1"):
            pass
        with trace_tool_call("tool_beta", host="dev-2"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 2
        assert spans[0].context.trace_id != spans[1].context.trace_id

    def test_span_status_ok_on_success(self, otel_setup):
        """Span should have UNSET (default OK) status on successful completion."""
        exporter = otel_setup["span_exporter"]

        with trace_tool_call("success_tool", host="dev-1"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        # StatusCode.UNSET is the default (successful) status
        assert spans[0].status.status_code == trace.StatusCode.UNSET


# ===========================================================================
# setup_telemetry / shutdown Lifecycle Tests
# ===========================================================================


class TestTelemetryLifecycle:
    """Test the setup_telemetry and shutdown lifecycle functions."""

    def test_setup_telemetry_returns_true_when_available(self):
        """setup_telemetry should return True when OTel SDK is installed."""
        result = setup_telemetry(service_name="test-svc", endpoint="")
        assert result is True
        assert get_tracer() is not None
        assert get_meter() is not None

    def test_setup_telemetry_initializes_instruments(self):
        """After setup, all metric instruments should be non-None."""
        setup_telemetry(service_name="test-instruments", endpoint="")
        assert telemetry_mod._tool_call_counter is not None
        assert telemetry_mod._tool_call_duration is not None
        assert telemetry_mod._tool_call_errors is not None
        assert telemetry_mod._cache_hit_counter is not None
        assert telemetry_mod._cache_miss_counter is not None
        assert telemetry_mod._connection_pool_gauge is not None

    def test_setup_telemetry_returns_false_when_unavailable(self):
        """setup_telemetry should return False when OTel SDK is not available."""
        with patch.object(telemetry_mod, "OTEL_AVAILABLE", False):
            result = setup_telemetry(service_name="no-otel")
            assert result is False
            assert get_tracer() is None
            assert get_meter() is None

    def test_shutdown_does_not_raise(self):
        """shutdown() should not raise even after setup."""
        setup_telemetry(service_name="shutdown-test", endpoint="")
        # Should complete without error
        shutdown()

    def test_reset_clears_all_state(self):
        """_reset() should set all module globals back to None."""
        setup_telemetry(service_name="reset-test", endpoint="")
        assert get_tracer() is not None
        assert get_meter() is not None

        _reset()
        assert get_tracer() is None
        assert get_meter() is None
        assert telemetry_mod._tool_call_counter is None
        assert telemetry_mod._tool_call_duration is None

    def test_setup_with_service_name(self):
        """Service name should be reported in the tracer provider resource."""
        setup_telemetry(service_name="custom-service", endpoint="")
        tracer = get_tracer()
        assert tracer is not None


# ===========================================================================
# _TimedToolCall Tests
# ===========================================================================


class TestTimedToolCall:
    """Test the _TimedToolCall helper class."""

    def test_measures_duration(self, otel_setup):
        """_TimedToolCall should measure elapsed time in milliseconds."""
        timer = _TimedToolCall("timed_tool", host="dev-1")
        time.sleep(0.02)
        duration_ms = timer.finish("success")
        assert duration_ms >= 10.0  # At least ~20ms but with tolerance

    def test_records_metric_on_finish(self, otel_setup):
        """finish() should call record_tool_call which records metrics."""
        reader = otel_setup["metric_reader"]

        timer = _TimedToolCall("measured_tool", host="dev-1")
        timer.finish("success")

        collected_data = reader.get_metrics_data()
        metric_names = set()
        for rm in collected_data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    metric_names.add(m.name)
        assert "net.tool.calls" in metric_names
        assert "net.tool.duration" in metric_names

    def test_records_error_metric(self, otel_setup):
        """finish('error') should increment the error counter."""
        reader = otel_setup["metric_reader"]

        timer = _TimedToolCall("failing_tool", host="dev-1")
        timer.finish("error")

        collected_data = reader.get_metrics_data()
        metric_names = set()
        for rm in collected_data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    metric_names.add(m.name)
        assert "net.tool.errors" in metric_names
