"""Tests for observability v2 enhancements: error type breakdown, circuit breaker transitions, TTL cleanup."""

import time

from network_mcp.observability import MetricsCollector


class TestErrorTypeBreakdown:
    def test_record_single_error_type(self):
        mc = MetricsCollector()
        mc.record_error_type("ConnectionError")
        metrics = mc.get_metrics_json()
        assert metrics["error_types"]["ConnectionError"] == 1

    def test_record_multiple_error_types(self):
        mc = MetricsCollector()
        mc.record_error_type("ConnectionError")
        mc.record_error_type("ConnectionError")
        mc.record_error_type("CommandError")
        mc.record_error_type("TimeoutError")
        mc.record_error_type("RBACDenied")

        metrics = mc.get_metrics_json()
        assert metrics["error_types"]["ConnectionError"] == 2
        assert metrics["error_types"]["CommandError"] == 1
        assert metrics["error_types"]["TimeoutError"] == 1
        assert metrics["error_types"]["RBACDenied"] == 1

    def test_error_types_empty_initially(self):
        mc = MetricsCollector()
        metrics = mc.get_metrics_json()
        assert metrics["error_types"] == {}

    def test_error_types_in_prometheus_format(self):
        mc = MetricsCollector()
        mc.record_error_type("ConnectionError")
        mc.record_error_type("TimeoutError")

        prom = mc.get_metrics_prometheus()
        assert "# HELP network_mcp_errors_total Errors by type" in prom
        assert "# TYPE network_mcp_errors_total counter" in prom
        assert 'network_mcp_errors_total{type="ConnectionError"} 1' in prom
        assert 'network_mcp_errors_total{type="TimeoutError"} 1' in prom

    def test_reset_clears_error_types(self):
        mc = MetricsCollector()
        mc.record_error_type("ConnectionError")
        mc.reset()
        metrics = mc.get_metrics_json()
        assert metrics["error_types"] == {}


class TestCircuitBreakerTransitions:
    def test_record_transition(self):
        mc = MetricsCollector()
        mc.record_circuit_breaker_transition("opened")
        mc.record_circuit_breaker_transition("opened")
        mc.record_circuit_breaker_transition("closed")

        metrics = mc.get_metrics_json()
        assert metrics["circuit_breaker_transitions"]["opened"] == 2
        assert metrics["circuit_breaker_transitions"]["closed"] == 1

    def test_transitions_empty_initially(self):
        mc = MetricsCollector()
        metrics = mc.get_metrics_json()
        assert metrics["circuit_breaker_transitions"] == {}

    def test_transitions_in_prometheus_format(self):
        mc = MetricsCollector()
        mc.record_circuit_breaker_transition("opened")
        mc.record_circuit_breaker_transition("half_open")

        prom = mc.get_metrics_prometheus()
        assert "# HELP network_mcp_circuit_breaker_transitions_total" in prom
        assert "# TYPE network_mcp_circuit_breaker_transitions_total counter" in prom
        assert 'network_mcp_circuit_breaker_transitions_total{state="opened"} 1' in prom
        assert 'network_mcp_circuit_breaker_transitions_total{state="half_open"} 1' in prom

    def test_reset_clears_transitions(self):
        mc = MetricsCollector()
        mc.record_circuit_breaker_transition("opened")
        mc.reset()
        metrics = mc.get_metrics_json()
        assert metrics["circuit_breaker_transitions"] == {}


class TestMetricCleanup:
    def test_cleanup_removes_stale_entries(self):
        mc = MetricsCollector()
        # Record device metric
        mc.record_tool_call_for_device("old-device", "show_version")

        # Artificially age the entry by manipulating _device_last_seen
        with mc._lock:
            mc._device_last_seen["old-device"] = time.monotonic() - 7200  # 2 hours ago

        pruned = mc.cleanup_stale_device_metrics(max_age=3600)  # 1 hour max age
        assert pruned == 1

        # Verify device metrics were cleaned up
        metrics = mc.get_metrics_json()
        assert metrics["tracked_devices"] == 0

    def test_cleanup_keeps_recent_entries(self):
        mc = MetricsCollector()
        mc.record_tool_call_for_device("active-device", "show_version")

        pruned = mc.cleanup_stale_device_metrics(max_age=3600)
        assert pruned == 0

        metrics = mc.get_metrics_json()
        assert metrics["tracked_devices"] == 1

    def test_cleanup_mixed_stale_and_recent(self):
        mc = MetricsCollector()
        mc.record_tool_call_for_device("active-device", "show_version")
        mc.record_tool_call_for_device("stale-device", "show_version")

        # Age only the stale device
        with mc._lock:
            mc._device_last_seen["stale-device"] = time.monotonic() - 7200

        pruned = mc.cleanup_stale_device_metrics(max_age=3600)
        assert pruned == 1
        assert mc.get_metrics_json()["tracked_devices"] == 1

    def test_cleanup_with_default_max_age(self):
        mc = MetricsCollector()
        mc.record_tool_call_for_device("device-1", "show_version")

        # Use default max_age (1 hour) — recent entry should not be pruned
        pruned = mc.cleanup_stale_device_metrics()
        assert pruned == 0

    def test_record_tool_call_for_device_tracks_counts(self):
        mc = MetricsCollector()
        mc.record_tool_call_for_device("spine-01", "show_version")
        mc.record_tool_call_for_device("spine-01", "show_version")
        mc.record_tool_call_for_device("spine-01", "show_interfaces")

        with mc._lock:
            assert mc._device_tool_calls["spine-01"]["show_version"] == 2
            assert mc._device_tool_calls["spine-01"]["show_interfaces"] == 1

    def test_reset_clears_device_metrics(self):
        mc = MetricsCollector()
        mc.record_tool_call_for_device("device-1", "show_version")
        mc.reset()
        metrics = mc.get_metrics_json()
        assert metrics["tracked_devices"] == 0


class TestMetricsJsonNewFields:
    def test_json_includes_new_fields(self):
        mc = MetricsCollector()
        metrics = mc.get_metrics_json()
        assert "error_types" in metrics
        assert "circuit_breaker_transitions" in metrics
        assert "tracked_devices" in metrics
