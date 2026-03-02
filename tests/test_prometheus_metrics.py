"""Tests for Prometheus /metrics endpoint and exposition format."""

import re
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.middleware import clear_request_context, get_correlation_id, set_request_context
from network_mcp.observability import MetricsCollector, get_uptime_seconds, metrics_collector
from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Reset metrics before each test."""
    metrics_collector.reset()
    yield
    metrics_collector.reset()


@pytest.fixture
def collector():
    """Return a fresh MetricsCollector instance."""
    return MetricsCollector()


# ---------------------------------------------------------------------------
# 1. Prometheus exposition format — metric families present
# ---------------------------------------------------------------------------


class TestPrometheusMetricFamilies:
    """Verify all expected metric families appear in the Prometheus output."""

    def test_tool_calls_total_present(self, collector):
        collector.record_tool_call("eos_get_vlans", "success", 100.0)
        output = collector.get_metrics_prometheus()
        assert "# TYPE network_mcp_tool_calls_total counter" in output
        assert 'network_mcp_tool_calls_total{tool="eos_get_vlans",status="success"} 1' in output

    def test_tool_call_duration_seconds_present(self, collector):
        collector.record_tool_call("eos_get_vlans", "success", 500.0)
        output = collector.get_metrics_prometheus()
        assert "# TYPE network_mcp_tool_call_duration_seconds summary" in output
        assert 'network_mcp_tool_call_duration_seconds{tool="eos_get_vlans",quantile="0.5"}' in output
        assert 'network_mcp_tool_call_duration_seconds{tool="eos_get_vlans",quantile="0.99"}' in output
        assert 'network_mcp_tool_call_duration_seconds_sum{tool="eos_get_vlans"}' in output
        assert 'network_mcp_tool_call_duration_seconds_count{tool="eos_get_vlans"} 1' in output

    def test_cache_hits_total_present(self, collector):
        collector.record_cache_hit()
        output = collector.get_metrics_prometheus()
        assert "# TYPE network_mcp_cache_hits_total counter" in output
        assert "network_mcp_cache_hits_total 1" in output

    def test_cache_misses_total_present(self, collector):
        collector.record_cache_miss()
        output = collector.get_metrics_prometheus()
        assert "# TYPE network_mcp_cache_misses_total counter" in output
        assert "network_mcp_cache_misses_total 1" in output

    def test_rate_limit_throttled_total_present(self, collector):
        collector.record_rate_limit_throttle()
        output = collector.get_metrics_prometheus()
        assert "# TYPE network_mcp_rate_limit_throttled_total counter" in output
        assert "network_mcp_rate_limit_throttled_total 1" in output

    def test_errors_total_present(self, collector):
        collector.record_error_type("ConnectionError")
        output = collector.get_metrics_prometheus()
        assert "# TYPE network_mcp_errors_total counter" in output
        assert 'network_mcp_errors_total{type="ConnectionError"} 1' in output

    def test_circuit_breaker_state_present(self, collector):
        collector.set_circuit_breaker_states({"spine-01": 0, "leaf-01": 1})
        output = collector.get_metrics_prometheus()
        assert "# TYPE network_mcp_circuit_breaker_state gauge" in output
        assert 'network_mcp_circuit_breaker_state{device="spine-01"} 0' in output
        assert 'network_mcp_circuit_breaker_state{device="leaf-01"} 1' in output

    def test_connection_pool_size_present(self, collector):
        collector.set_connection_pool_stats(pool_size=100, active=5)
        output = collector.get_metrics_prometheus()
        assert "# TYPE network_mcp_connection_pool_size gauge" in output
        assert "network_mcp_connection_pool_size 100" in output

    def test_active_connections_present(self, collector):
        collector.set_connection_pool_stats(pool_size=100, active=5)
        output = collector.get_metrics_prometheus()
        assert "# TYPE network_mcp_active_connections gauge" in output
        assert "network_mcp_active_connections 5" in output

    def test_uptime_seconds_present(self, collector):
        output = collector.get_metrics_prometheus()
        assert "# TYPE network_mcp_uptime_seconds gauge" in output
        assert "network_mcp_uptime_seconds" in output

    def test_vendor_calls_total_present(self, collector):
        collector.record_tool_call("eos_get_vlans", "success", 100.0, vendor="eos")
        output = collector.get_metrics_prometheus()
        assert "# TYPE network_mcp_vendor_calls_total counter" in output
        assert 'network_mcp_vendor_calls_total{vendor="eos",status="success"} 1' in output


# ---------------------------------------------------------------------------
# 2. Prometheus format validity
# ---------------------------------------------------------------------------


class TestPrometheusFormatValidity:
    """Verify the output conforms to Prometheus exposition format rules."""

    def test_ends_with_newline(self, collector):
        output = collector.get_metrics_prometheus()
        assert output.endswith("\n")

    def test_help_before_type(self, collector):
        collector.record_tool_call("test", "success", 10.0)
        output = collector.get_metrics_prometheus()
        lines = output.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("# TYPE"):
                # The line before should be a HELP line for the same metric
                assert i > 0
                assert lines[i - 1].startswith("# HELP")

    def test_no_empty_metric_names(self, collector):
        collector.record_tool_call("test_tool", "success", 50.0)
        output = collector.get_metrics_prometheus()
        # No line should have metric name that is empty
        for line in output.strip().split("\n"):
            if line and not line.startswith("#"):
                # Should have format: metric_name{...} value or metric_name value
                assert re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*", line), f"Invalid metric line: {line}"

    def test_counter_values_are_numeric(self, collector):
        collector.record_tool_call("test", "success", 100.0)
        collector.record_cache_hit()
        collector.record_error_type("Timeout")
        output = collector.get_metrics_prometheus()
        for line in output.strip().split("\n"):
            if line and not line.startswith("#"):
                parts = line.rsplit(" ", 1)
                assert len(parts) == 2, f"Expected 'metric value' format: {line}"
                # Value should be parseable as a number
                try:
                    float(parts[1])
                except ValueError:
                    pytest.fail(f"Non-numeric value in line: {line}")

    def test_quantile_labels_are_valid(self, collector):
        for i in range(10):
            collector.record_tool_call("test", "success", float(i * 100))
        output = collector.get_metrics_prometheus()
        # Check quantile labels exist and have valid values
        quantile_lines = [line for line in output.split("\n") if "quantile=" in line]
        assert len(quantile_lines) >= 3  # 0.5, 0.9, 0.99
        for line in quantile_lines:
            match = re.search(r'quantile="([0-9.]+)"', line)
            assert match, f"Invalid quantile label: {line}"
            q = float(match.group(1))
            assert 0 <= q <= 1, f"Quantile out of range: {q}"


# ---------------------------------------------------------------------------
# 3. Multiple tool calls and vendors
# ---------------------------------------------------------------------------


class TestMultipleToolCalls:
    """Verify metrics accumulate correctly for multiple tools and vendors."""

    def test_multiple_tools_tracked(self, collector):
        collector.record_tool_call("eos_get_vlans", "success", 100.0)
        collector.record_tool_call("eos_get_interfaces", "success", 200.0)
        collector.record_tool_call("eos_get_vlans", "error", 50.0)
        output = collector.get_metrics_prometheus()
        assert 'tool="eos_get_vlans",status="success"} 1' in output
        assert 'tool="eos_get_vlans",status="error"} 1' in output
        assert 'tool="eos_get_interfaces",status="success"} 1' in output

    def test_multiple_vendors_tracked(self, collector):
        collector.record_tool_call("show_version", "success", 100.0, vendor="eos")
        collector.record_tool_call("show_version", "success", 100.0, vendor="iosxe")
        collector.record_tool_call("show_version", "error", 100.0, vendor="nxos")
        output = collector.get_metrics_prometheus()
        assert 'vendor="eos",status="success"} 1' in output
        assert 'vendor="iosxe",status="success"} 1' in output
        assert 'vendor="nxos",status="error"} 1' in output

    def test_duration_quantiles_accurate(self, collector):
        # Record 100 calls with known durations (0-99ms -> 0-0.099s)
        for i in range(100):
            collector.record_tool_call("test", "success", float(i))
        output = collector.get_metrics_prometheus()
        # P50 should be around 0.049-0.050s (50ms)
        match = re.search(r'quantile="0.5"} ([0-9.]+)', output)
        assert match
        p50 = float(match.group(1))
        assert 0.04 <= p50 <= 0.06, f"P50 = {p50}, expected ~0.05"


# ---------------------------------------------------------------------------
# 4. set_circuit_breaker_states
# ---------------------------------------------------------------------------


class TestCircuitBreakerStates:
    def test_empty_states(self, collector):
        collector.set_circuit_breaker_states({})
        output = collector.get_metrics_prometheus()
        assert "# TYPE network_mcp_circuit_breaker_state gauge" in output
        # No device lines
        state_lines = [line for line in output.split("\n") if line.startswith("network_mcp_circuit_breaker_state{")]
        assert len(state_lines) == 0

    def test_all_state_values(self, collector):
        collector.set_circuit_breaker_states({"a": 0, "b": 1, "c": 2})
        output = collector.get_metrics_prometheus()
        assert 'device="a"} 0' in output
        assert 'device="b"} 1' in output
        assert 'device="c"} 2' in output


# ---------------------------------------------------------------------------
# 5. set_connection_pool_stats
# ---------------------------------------------------------------------------


class TestConnectionPoolStats:
    def test_default_zero(self, collector):
        output = collector.get_metrics_prometheus()
        assert "network_mcp_connection_pool_size 0" in output
        assert "network_mcp_active_connections 0" in output

    def test_custom_values(self, collector):
        collector.set_connection_pool_stats(pool_size=200, active=42)
        output = collector.get_metrics_prometheus()
        assert "network_mcp_connection_pool_size 200" in output
        assert "network_mcp_active_connections 42" in output


# ---------------------------------------------------------------------------
# 6. Correlation ID
# ---------------------------------------------------------------------------


class TestCorrelationId:
    def test_get_correlation_id_generates_uuid(self):
        clear_request_context()
        cid = get_correlation_id()
        assert cid is not None
        assert len(cid) == 36  # UUID v4 format

    def test_get_correlation_id_returns_existing(self):
        request_id = set_request_context(user="test")
        cid = get_correlation_id()
        assert cid == request_id
        clear_request_context()

    def test_correlation_id_stable_within_request(self):
        clear_request_context()
        cid1 = get_correlation_id()
        cid2 = get_correlation_id()
        assert cid1 == cid2
        clear_request_context()

    def test_correlation_id_in_audit_log(self):
        """Verify audit log entries include correlation_id."""
        from network_mcp.audit import AuditLogger

        set_request_context(user="test-user")
        audit = AuditLogger(logger_name="test.audit.corr")
        entries = []

        # Capture the emitted audit_data
        def capture_emit(audit_data, message, **kwargs):
            entries.append(audit_data)

        audit._emit = capture_emit

        audit.log_tool_call(
            tool_name="eos_get_vlans",
            device="spine-01",
            parameters={"commands": ["show vlan"]},
            result_status="success",
            duration_ms=42.0,
        )

        assert len(entries) == 1
        assert "correlation_id" in entries[0]
        assert entries[0]["correlation_id"] == entries[0]["request_id"]
        clear_request_context()


# ---------------------------------------------------------------------------
# 7. Reset clears all new fields
# ---------------------------------------------------------------------------


class TestMetricsReset:
    def test_reset_clears_circuit_breaker_states(self, collector):
        collector.set_circuit_breaker_states({"spine-01": 1})
        collector.reset()
        assert collector._circuit_breaker_device_states == {}

    def test_reset_clears_pool_stats(self, collector):
        collector.set_connection_pool_stats(pool_size=100, active=10)
        collector.reset()
        assert collector._connection_pool_size == 0
        assert collector._active_connections == 0

    def test_reset_clears_all_counters(self, collector):
        collector.record_tool_call("test", "success", 100.0)
        collector.record_cache_hit()
        collector.record_cache_miss()
        collector.record_rate_limit_throttle()
        collector.record_error_type("Timeout")
        collector.reset()
        output = collector.get_metrics_prometheus()
        assert "network_mcp_cache_hits_total 0" in output
        assert "network_mcp_cache_misses_total 0" in output
        assert "network_mcp_rate_limit_throttled_total 0" in output


# ---------------------------------------------------------------------------
# 8. Integration: /metrics endpoint with mock HTTP transport
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    """Verify the /metrics HTTP endpoint returns valid Prometheus output."""

    @pytest.fixture
    def mock_node(self):
        node = MagicMock()
        node.run_show = MagicMock(return_value=[{"vlans": {}}])
        node.run_config = MagicMock(return_value=[])
        node.platform = "eos"
        node.is_connected = MagicMock(return_value=True)
        return node

    @pytest.fixture
    def mock_conn_mgr(self, mock_node):
        @contextmanager
        def _mock_acquire(host):
            yield mock_node

        with (
            patch.object(conn_mgr, "get_driver", return_value=mock_node),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        ):
            yield

    def test_metrics_endpoint_function_exists(self):
        """The metrics_endpoint function should be registered."""
        try:
            from starlette.testclient import TestClient  # noqa: F401

            # If starlette is available, the endpoint should be registered
            from network_mcp.server import metrics_endpoint  # noqa: F811

            assert callable(metrics_endpoint)
        except ImportError:
            pytest.skip("starlette not installed")

    def test_metrics_after_tool_calls(self, mock_conn_mgr):
        """After running tool calls, metrics should reflect them."""
        from network_mcp.helpers import run_show_command

        result = run_show_command(conn_mgr, "spine-01", ["show vlan"])
        assert result["status"] == "success"

        output = metrics_collector.get_metrics_prometheus()
        assert "network_mcp_tool_calls_total" in output
        assert 'status="success"' in output


# ---------------------------------------------------------------------------
# 9. Uptime metric
# ---------------------------------------------------------------------------


class TestUptimeMetric:
    def test_uptime_is_positive(self, collector):
        output = collector.get_metrics_prometheus()
        match = re.search(r"network_mcp_uptime_seconds ([0-9.]+)", output)
        assert match
        uptime = float(match.group(1))
        assert uptime >= 0

    def test_uptime_seconds_function(self):
        uptime = get_uptime_seconds()
        assert uptime >= 0
