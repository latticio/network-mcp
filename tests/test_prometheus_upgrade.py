"""Tests for Prometheus metrics upgrade: safe labels, cardinality, histograms, lifecycle tracking."""

import threading
import time
from unittest.mock import patch

from network_mcp.middleware import clear_request_context, get_request_duration, set_request_context
from network_mcp.observability import (
    CardinalityTracker,
    MetricsCollector,
    _cardinality_tracker,
    _escape_label_value,
    _sanitize_metric_name,
)

# --- _escape_label_value tests ---


class TestEscapeLabelValue:
    def test_backslash_escaped(self):
        assert _escape_label_value(r"foo\bar") == r"foo\\bar"

    def test_double_quote_escaped(self):
        assert _escape_label_value('foo"bar') == r"foo\"bar"

    def test_newline_escaped(self):
        assert _escape_label_value("foo\nbar") == r"foo\nbar"

    def test_combined_escaping(self):
        assert _escape_label_value('a\\b"c\nd') == r"a\\b\"c\nd"

    def test_truncation_at_128_chars(self):
        long_value = "x" * 200
        result = _escape_label_value(long_value)
        assert len(result) == 128

    def test_short_string_unchanged(self):
        assert _escape_label_value("simple") == "simple"

    def test_empty_string(self):
        assert _escape_label_value("") == ""


# --- _sanitize_metric_name tests ---


class TestSanitizeMetricName:
    def test_invalid_chars_replaced(self):
        assert _sanitize_metric_name("my-metric.name") == "my_metric_name"

    def test_leading_digit_prefixed(self):
        assert _sanitize_metric_name("42metric") == "_42metric"

    def test_valid_name_unchanged(self):
        assert _sanitize_metric_name("valid_metric:name") == "valid_metric:name"

    def test_spaces_and_special_chars(self):
        assert _sanitize_metric_name("metric name!@#") == "metric_name___"

    def test_empty_string(self):
        assert _sanitize_metric_name("") == ""


# --- CardinalityTracker tests ---


class TestCardinalityTracker:
    def test_under_limit_allowed(self):
        tracker = CardinalityTracker(limit=100)
        for i in range(100):
            assert tracker.check("metric", {"tool": f"tool_{i}"}) is True

    def test_over_limit_rejected(self):
        tracker = CardinalityTracker(limit=100)
        for i in range(100):
            tracker.check("metric", {"tool": f"tool_{i}"})
        # 101st unique combination should be rejected
        assert tracker.check("metric", {"tool": "tool_100"}) is False

    def test_existing_combination_still_passes(self):
        tracker = CardinalityTracker(limit=100)
        for i in range(100):
            tracker.check("metric", {"tool": f"tool_{i}"})
        # Existing combination should still pass even at limit
        assert tracker.check("metric", {"tool": "tool_0"}) is True

    def test_different_metrics_tracked_independently(self):
        tracker = CardinalityTracker(limit=5)
        for i in range(5):
            tracker.check("metric_a", {"label": f"val_{i}"})
        # metric_a is at limit, but metric_b should still accept
        assert tracker.check("metric_b", {"label": "val_0"}) is True

    def test_reset_clears_tracking(self):
        tracker = CardinalityTracker(limit=5)
        for i in range(5):
            tracker.check("metric", {"tool": f"tool_{i}"})
        assert tracker.check("metric", {"tool": "tool_new"}) is False
        tracker.reset()
        assert tracker.check("metric", {"tool": "tool_new"}) is True

    def test_thread_safety(self):
        """Run concurrent check() calls from 10 threads."""
        tracker = CardinalityTracker(limit=1000)
        errors: list[Exception] = []

        def worker(thread_id: int):
            try:
                for i in range(100):
                    tracker.check("metric", {"tool": f"t{thread_id}_{i}"})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # All 1000 unique combos should be tracked
        assert len(tracker._seen.get("metric", set())) == 1000


# --- net_up gauge test ---


class TestUpGauge:
    def test_net_up_in_prometheus_output(self):
        mc = MetricsCollector()
        output = mc.get_metrics_prometheus()
        assert "net_up 1" in output

    def test_net_up_has_help_and_type(self):
        mc = MetricsCollector()
        output = mc.get_metrics_prometheus()
        assert "# HELP net_up" in output
        assert "# TYPE net_up gauge" in output


# --- net_server_info gauge test ---


class TestServerInfoGauge:
    def test_server_info_has_version(self):
        mc = MetricsCollector()
        output = mc.get_metrics_prometheus()
        assert "net_server_info{" in output
        assert 'version="' in output

    def test_server_info_has_help_and_type(self):
        mc = MetricsCollector()
        output = mc.get_metrics_prometheus()
        assert "# HELP net_server_info" in output
        assert "# TYPE net_server_info gauge" in output


# --- Latency histogram tests ---


class TestLatencyHistogram:
    def test_histogram_bucket_counts(self):
        mc = MetricsCollector()
        # Record calls with known durations (in ms)
        # 50ms -> bucket 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, +Inf
        for _ in range(10):
            mc.record_tool_call("test_tool", "success", 50.0)
        # 200ms -> bucket 0.5, 1.0, 2.5, 5.0, 10.0, +Inf (NOT 0.1)
        for _ in range(20):
            mc.record_tool_call("test_tool", "success", 200.0)
        # 3000ms -> bucket 5.0, 10.0, +Inf (NOT 0.1, 0.5, 1.0, 2.5)
        for _ in range(5):
            mc.record_tool_call("test_tool", "success", 3000.0)

        hist = mc.get_latency_histogram("test_tool")
        assert hist["bucket_le_0_1"] == 10  # only the 50ms calls
        assert hist["bucket_le_0_5"] == 30  # 50ms + 200ms
        assert hist["bucket_le_1"] == 30  # same
        assert hist["bucket_le_2_5"] == 30  # same
        assert hist["bucket_le_5"] == 35  # all except nothing above 5s
        assert hist["bucket_le_10"] == 35  # same
        assert hist["bucket_le_inf"] == 35  # all calls
        assert hist["count"] == 35
        expected_sum = (10 * 0.05) + (20 * 0.2) + (5 * 3.0)
        assert abs(hist["sum"] - expected_sum) < 0.001

    def test_histogram_unknown_tool_returns_zeros(self):
        mc = MetricsCollector()
        hist = mc.get_latency_histogram("nonexistent")
        assert hist["bucket_le_inf"] == 0
        assert hist["count"] == 0
        assert hist["sum"] == 0.0

    def test_histogram_prometheus_format(self):
        mc = MetricsCollector()
        mc.record_tool_call("get_vlans", "success", 100.0)  # 100ms = 0.1s
        output = mc.get_metrics_prometheus()

        assert "# HELP net_tool_duration_seconds" in output
        assert "# TYPE net_tool_duration_seconds histogram" in output
        assert 'net_tool_duration_seconds_bucket{tool="get_vlans",le="0.1"} 1' in output
        assert 'net_tool_duration_seconds_bucket{tool="get_vlans",le="+Inf"} 1' in output
        assert 'net_tool_duration_seconds_sum{tool="get_vlans"}' in output
        assert 'net_tool_duration_seconds_count{tool="get_vlans"} 1' in output


# --- Cardinality overflow test ---


class TestCardinalityOverflow:
    def test_overflow_aggregated(self):
        mc = MetricsCollector()
        _cardinality_tracker.reset()

        # Record 150 unique tool names — only 100 should be tracked
        for i in range(150):
            mc.record_tool_call(f"tool_{i}", "success", 10.0)

        # Check that _cardinality_overflow was used for the excess
        assert "_cardinality_overflow" in mc._tool_calls
        overflow_count = sum(mc._tool_calls["_cardinality_overflow"].values())
        assert overflow_count == 50  # 150 - 100 = 50 overflow calls

        # Original 100 should still be tracked individually
        tracked = len([k for k in mc._tool_calls if k != "_cardinality_overflow"])
        assert tracked == 100


# --- Correlation ID on success responses ---


class TestCorrelationIdOnSuccess:
    def test_correlation_id_on_show_success(self, mock_node, mock_conn_mgr):
        from network_mcp.helpers import run_show_command
        from network_mcp.server import conn_mgr

        mock_node.run_show.return_value = [{"vlans": {}}]
        set_request_context(user="testuser")
        try:
            result = run_show_command(conn_mgr, "spine-01", ["show vlan"])
            assert result["status"] == "success"
            assert "correlation_id" in result
            assert "request_id" in result
        finally:
            clear_request_context()

    def test_correlation_id_on_config_success(self, mock_node, mock_conn_mgr):
        from network_mcp.helpers import run_config_command
        from network_mcp.server import conn_mgr

        mock_node.run_config.return_value = [{}]
        set_request_context(user="testuser")
        try:
            with patch("network_mcp.helpers._get_settings") as mock_settings:
                mock_settings.return_value.net_read_only = False
                mock_settings.return_value.config_retry_enabled = False
                mock_settings.return_value.net_change_mgmt_enabled = False
                mock_settings.return_value.net_rate_limit_max_wait = 5.0
                mock_settings.return_value.net_conn_acquire_timeout = 30.0
                result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "test_action")
                assert result["status"] == "success"
                assert "correlation_id" in result
                assert "request_id" in result
        finally:
            clear_request_context()


# --- get_request_duration test ---


class TestGetRequestDuration:
    def test_duration_positive_after_set(self):
        clear_request_context()
        set_request_context(user="testuser")
        time.sleep(0.01)
        duration = get_request_duration()
        assert duration is not None
        assert duration > 0
        clear_request_context()

    def test_duration_none_without_context(self):
        clear_request_context()
        assert get_request_duration() is None


# --- Prometheus output completeness test ---


class TestPrometheusOutputCompleteness:
    def test_all_metric_families_present(self):
        mc = MetricsCollector()
        # Record some data to populate metrics
        mc.record_tool_call("test_tool", "success", 100.0)
        mc.record_cache_hit()
        mc.record_cache_miss()
        mc.record_rate_limit_throttle()
        mc.record_error_type("ConnectionError")
        mc.record_circuit_breaker_transition("opened")
        mc.set_circuit_breaker_states({"spine-01": 0})
        mc.set_connection_pool_stats(pool_size=10, active=3)

        output = mc.get_metrics_prometheus()

        expected_families = [
            "net_up",
            "net_server_info",
            "network_mcp_tool_calls_total",
            "network_mcp_vendor_calls_total",
            "network_mcp_tool_call_duration_seconds",
            "net_tool_duration_seconds",
            "network_mcp_cache_hits_total",
            "network_mcp_cache_misses_total",
            "network_mcp_rate_limit_throttled_total",
            "network_mcp_errors_total",
            "network_mcp_circuit_breaker_transitions_total",
            "network_mcp_circuit_breaker_state",
            "network_mcp_connection_pool_size",
            "network_mcp_active_connections",
            "network_mcp_uptime_seconds",
        ]
        for family in expected_families:
            assert family in output, f"Missing metric family: {family}"


# --- Label escaping integration test ---


class TestLabelEscapingInOutput:
    def test_special_chars_in_tool_name_escaped(self):
        mc = MetricsCollector()
        _cardinality_tracker.reset()
        mc.record_tool_call('tool"with"quotes', "success", 50.0)
        output = mc.get_metrics_prometheus()
        assert r"tool\"with\"quotes" in output
        assert 'tool"with"quotes' not in output.split("# HELP")[2]  # not raw in metric lines

    def test_newline_in_error_type_escaped(self):
        mc = MetricsCollector()
        mc.record_error_type("Error\nType")
        output = mc.get_metrics_prometheus()
        assert r"Error\nType" in output
