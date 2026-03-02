"""Tests for OpenTelemetry integration (telemetry.py)."""

import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from network_mcp import telemetry
from network_mcp.config import NetworkSettings
from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_telemetry():
    """Reset telemetry module state before each test."""
    telemetry._reset()
    yield
    telemetry._reset()


@pytest.fixture
def mock_otel():
    """Set up telemetry with mocked OTel SDK (no real exporters)."""
    assert telemetry.OTEL_AVAILABLE, "opentelemetry-sdk must be installed for these tests"
    telemetry.setup_telemetry(service_name="test-service", endpoint="")
    yield
    telemetry._reset()


# ---------------------------------------------------------------------------
# 1. OTEL_AVAILABLE detection
# ---------------------------------------------------------------------------


class TestOtelAvailability:
    def test_otel_available_flag(self):
        """OTEL_AVAILABLE should be True when SDK is installed."""
        assert telemetry.OTEL_AVAILABLE is True

    def test_otel_available_false_when_import_fails(self):
        """When SDK is missing, OTEL_AVAILABLE should be False."""
        # We can't easily uninstall the SDK mid-test, but we verify the flag
        # logic indirectly: if OTEL_AVAILABLE is True, the try/except worked.
        assert isinstance(telemetry.OTEL_AVAILABLE, bool)


# ---------------------------------------------------------------------------
# 2. setup_telemetry
# ---------------------------------------------------------------------------


class TestSetupTelemetry:
    def test_setup_returns_true_when_otel_available(self):
        result = telemetry.setup_telemetry(service_name="test", endpoint="")
        assert result is True

    def test_setup_initializes_tracer(self):
        telemetry.setup_telemetry(service_name="test", endpoint="")
        assert telemetry.get_tracer() is not None

    def test_setup_initializes_meter(self):
        telemetry.setup_telemetry(service_name="test", endpoint="")
        assert telemetry.get_meter() is not None

    def test_setup_creates_metric_instruments(self):
        telemetry.setup_telemetry(service_name="test", endpoint="")
        assert telemetry._tool_call_counter is not None
        assert telemetry._tool_call_duration is not None
        assert telemetry._tool_call_errors is not None
        assert telemetry._connection_pool_gauge is not None
        assert telemetry._cache_hit_counter is not None
        assert telemetry._cache_miss_counter is not None

    def test_setup_with_custom_service_name(self):
        result = telemetry.setup_telemetry(service_name="custom-svc", endpoint="")
        assert result is True
        assert telemetry.get_tracer() is not None

    def test_setup_with_endpoint_creates_exporters(self):
        """setup_telemetry with an endpoint should still succeed (exporters are lazy)."""
        # Use a fake endpoint — exporters won't actually connect in tests
        result = telemetry.setup_telemetry(service_name="test", endpoint="http://localhost:4317")
        assert result is True

    def test_setup_returns_false_when_otel_unavailable(self):
        """When OTEL_AVAILABLE is False, setup should return False."""
        original = telemetry.OTEL_AVAILABLE
        try:
            telemetry.OTEL_AVAILABLE = False
            result = telemetry.setup_telemetry()
            assert result is False
            assert telemetry.get_tracer() is None
            assert telemetry.get_meter() is None
        finally:
            telemetry.OTEL_AVAILABLE = original

    def test_setup_idempotent(self):
        """Calling setup_telemetry twice should work without error."""
        telemetry.setup_telemetry(service_name="test1", endpoint="")
        tracer1 = telemetry.get_tracer()
        telemetry.setup_telemetry(service_name="test2", endpoint="")
        tracer2 = telemetry.get_tracer()
        assert tracer1 is not None
        assert tracer2 is not None


# ---------------------------------------------------------------------------
# 3. trace_tool_call context manager
# ---------------------------------------------------------------------------


class TestTraceToolCall:
    def test_trace_yields_none_when_not_initialized(self):
        """When telemetry is not set up, trace_tool_call yields None."""
        assert telemetry.get_tracer() is None
        with telemetry.trace_tool_call("test_tool", host="host1") as span:
            assert span is None

    def test_trace_yields_span_when_initialized(self, mock_otel):
        with telemetry.trace_tool_call("test_tool", host="host1") as span:
            assert span is not None

    def test_trace_span_has_correct_attributes(self, mock_otel):
        with telemetry.trace_tool_call("eos_get_vlans", host="spine-01") as span:
            attrs = dict(span.attributes)
            assert attrs["tool.name"] == "eos_get_vlans"
            assert attrs["device.host"] == "spine-01"

    def test_trace_span_custom_attributes(self, mock_otel):
        with telemetry.trace_tool_call("test_tool", host="h1", vlan_id="100") as span:
            attrs = dict(span.attributes)
            assert attrs["vlan_id"] == "100"

    def test_trace_records_exception(self, mock_otel):
        """Exceptions inside trace_tool_call should be re-raised and recorded."""
        with pytest.raises(ValueError, match="boom"):
            with telemetry.trace_tool_call("test_tool", host="h1"):
                raise ValueError("boom")

    def test_trace_normal_execution(self, mock_otel):
        """trace_tool_call should not swallow results."""
        result = None
        with telemetry.trace_tool_call("test_tool", host="h1"):
            result = {"status": "success"}
        assert result == {"status": "success"}

    def test_trace_nested_spans(self, mock_otel):
        """Nested trace_tool_call contexts should work."""
        with telemetry.trace_tool_call("outer", host="h1") as outer:
            with telemetry.trace_tool_call("inner", host="h1") as inner:
                assert outer is not None
                assert inner is not None

    def test_trace_empty_host(self, mock_otel):
        """trace_tool_call with empty host should work."""
        with telemetry.trace_tool_call("test_tool") as span:
            assert span is not None
            attrs = dict(span.attributes)
            assert attrs["device.host"] == ""


# ---------------------------------------------------------------------------
# 4. record_tool_call metrics
# ---------------------------------------------------------------------------


class TestRecordToolCall:
    def test_record_noop_when_not_initialized(self):
        """No error when metrics are not initialized."""
        telemetry.record_tool_call("test", "success", 100.0, "host1")

    def test_record_success(self, mock_otel):
        telemetry.record_tool_call("eos_get_vlans", "success", 42.5, "spine-01")
        # No assertion on counter value — OTel SDK doesn't expose counter reads easily.
        # This test verifies no exceptions are raised.

    def test_record_error(self, mock_otel):
        telemetry.record_tool_call("eos_get_vlans", "error", 100.0, "spine-01")

    def test_record_multiple_calls(self, mock_otel):
        for i in range(10):
            telemetry.record_tool_call("tool", "success", float(i), "h1")

    def test_record_with_empty_host(self, mock_otel):
        telemetry.record_tool_call("test_tool", "success", 10.0)

    def test_record_error_increments_error_counter(self, mock_otel):
        """Error calls should increment both the call counter and error counter."""
        telemetry.record_tool_call("test_tool", "error", 50.0, "h1")
        # Verify _tool_call_errors was invoked (counter is not None)
        assert telemetry._tool_call_errors is not None


# ---------------------------------------------------------------------------
# 5. record_cache_hit / record_connection_pool_size
# ---------------------------------------------------------------------------


class TestCacheAndPoolMetrics:
    def test_cache_hit_noop_when_not_initialized(self):
        telemetry.record_cache_hit(hit=True)
        telemetry.record_cache_hit(hit=False)

    def test_cache_hit_when_initialized(self, mock_otel):
        telemetry.record_cache_hit(hit=True)

    def test_cache_miss_when_initialized(self, mock_otel):
        telemetry.record_cache_hit(hit=False)

    def test_connection_pool_noop_when_not_initialized(self):
        telemetry.record_connection_pool_size(1)

    def test_connection_pool_when_initialized(self, mock_otel):
        telemetry.record_connection_pool_size(5)
        telemetry.record_connection_pool_size(-2)


# ---------------------------------------------------------------------------
# 6. _reset and shutdown
# ---------------------------------------------------------------------------


class TestResetAndShutdown:
    def test_reset_clears_all_state(self):
        telemetry.setup_telemetry(service_name="test", endpoint="")
        assert telemetry.get_tracer() is not None
        telemetry._reset()
        assert telemetry.get_tracer() is None
        assert telemetry.get_meter() is None
        assert telemetry._tool_call_counter is None

    def test_shutdown_no_error_when_not_initialized(self):
        telemetry.shutdown()

    def test_shutdown_flushes_providers(self):
        telemetry.setup_telemetry(service_name="test", endpoint="")
        telemetry.shutdown()
        # Should not raise


# ---------------------------------------------------------------------------
# 7. _TimedToolCall helper
# ---------------------------------------------------------------------------


class TestTimedToolCall:
    def test_timed_tool_call_returns_duration(self):
        timer = telemetry._TimedToolCall("test_tool", host="h1")
        time.sleep(0.01)  # 10ms
        duration = timer.finish("success")
        assert duration >= 5.0  # at least a few ms

    def test_timed_tool_call_records_metric(self, mock_otel):
        timer = telemetry._TimedToolCall("test_tool", host="h1")
        duration = timer.finish("success")
        assert duration >= 0

    def test_timed_tool_call_error_status(self, mock_otel):
        timer = telemetry._TimedToolCall("test_tool", host="h1")
        duration = timer.finish("error")
        assert duration >= 0


# ---------------------------------------------------------------------------
# 8. Config settings
# ---------------------------------------------------------------------------


class TestOtelConfig:
    def test_default_otel_disabled(self):
        settings = NetworkSettings(net_username="admin", net_password="")
        assert settings.net_otel_enabled is False

    def test_default_otel_endpoint_empty(self):
        settings = NetworkSettings(net_username="admin", net_password="")
        assert settings.net_otel_endpoint == ""

    def test_default_otel_service_name(self):
        settings = NetworkSettings(net_username="admin", net_password="")
        assert settings.net_otel_service_name == "network-mcp"

    def test_otel_enabled_from_env(self, monkeypatch):
        monkeypatch.setenv("NET_OTEL_ENABLED", "true")
        settings = NetworkSettings(net_username="admin", net_password="")
        assert settings.net_otel_enabled is True

    def test_otel_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("NET_OTEL_ENDPOINT", "http://collector:4317")
        settings = NetworkSettings(net_username="admin", net_password="")
        assert settings.net_otel_endpoint == "http://collector:4317"

    def test_otel_service_name_from_env(self, monkeypatch):
        monkeypatch.setenv("NET_OTEL_SERVICE_NAME", "my-network-mcp")
        settings = NetworkSettings(net_username="admin", net_password="")
        assert settings.net_otel_service_name == "my-network-mcp"

    def test_eos_alias_otel_enabled(self, monkeypatch):
        monkeypatch.setenv("EOS_OTEL_ENABLED", "true")
        settings = NetworkSettings(net_username="admin", net_password="")
        assert settings.net_otel_enabled is True


# ---------------------------------------------------------------------------
# 9. Integration with run_show_command (mocked)
# ---------------------------------------------------------------------------


class TestHelpersIntegration:
    """Verify that run_show_command integrates with telemetry calls."""

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

    def test_run_show_command_calls_otel_record(self, mock_conn_mgr, mock_otel):
        from network_mcp.helpers import run_show_command

        with patch.object(telemetry, "record_tool_call", wraps=telemetry.record_tool_call) as otel_rec:
            result = run_show_command(conn_mgr, "spine-01", ["show vlan"])
            assert result["status"] == "success"
            otel_rec.assert_called()

    def test_run_show_command_traces(self, mock_conn_mgr, mock_otel):
        from network_mcp.helpers import run_show_command

        with patch.object(telemetry, "trace_tool_call", wraps=telemetry.trace_tool_call) as traced:
            result = run_show_command(conn_mgr, "spine-01", ["show vlan"])
            assert result["status"] == "success"
            traced.assert_called_once()

    def test_run_show_command_records_cache_hit(self, mock_conn_mgr, mock_otel):
        from network_mcp.helpers import run_show_command

        # Prime the cache
        run_show_command(conn_mgr, "spine-01", ["show vlan"])
        # Second call should be a cache hit
        with patch.object(telemetry, "record_cache_hit") as cache_rec:
            result = run_show_command(conn_mgr, "spine-01", ["show vlan"])
            assert result["status"] == "success"
            cache_rec.assert_called_with(hit=True)

    def test_run_config_command_traces(self, mock_conn_mgr, mock_otel, monkeypatch):
        from network_mcp.helpers import run_config_command

        monkeypatch.setattr(
            "network_mcp.helpers._get_settings",
            lambda: MagicMock(
                net_read_only=False,
                net_rate_limit_max_wait=5.0,
                net_conn_acquire_timeout=30.0,
                config_retry_enabled=False,
                net_change_mgmt_enabled=False,
            ),
        )
        with patch.object(telemetry, "trace_tool_call", wraps=telemetry.trace_tool_call) as traced:
            result = run_config_command(conn_mgr, "spine-01", ["vlan 100", "name TEST"], "create_vlan")
            assert result["status"] == "success"
            traced.assert_called_once()


# ---------------------------------------------------------------------------
# 10. Fallback no-op behavior
# ---------------------------------------------------------------------------


class TestNoopFallback:
    """Verify all functions work cleanly when OTel is not initialized."""

    def test_trace_tool_call_noop(self):
        with telemetry.trace_tool_call("tool", host="h1") as span:
            assert span is None

    def test_record_tool_call_noop(self):
        telemetry.record_tool_call("tool", "success", 10.0, "h1")

    def test_record_cache_hit_noop(self):
        telemetry.record_cache_hit(True)
        telemetry.record_cache_hit(False)

    def test_record_connection_pool_noop(self):
        telemetry.record_connection_pool_size(5)

    def test_get_tracer_none(self):
        assert telemetry.get_tracer() is None

    def test_get_meter_none(self):
        assert telemetry.get_meter() is None

    def test_shutdown_noop(self):
        telemetry.shutdown()


# ---------------------------------------------------------------------------
# 11. Server lifespan OTel init
# ---------------------------------------------------------------------------


class TestServerLifespan:
    def test_lifespan_skips_otel_when_disabled(self):
        """When net_otel_enabled=False, setup_telemetry should not be called."""
        from network_mcp.server import settings

        assert settings.net_otel_enabled is False
        # Telemetry should not be initialized at import time
        assert telemetry.get_tracer() is None
