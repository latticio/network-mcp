"""Tests for server observability: MetricsCollector, health/metrics tools, and MCP logging."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.observability import (
    PROMETHEUS_AVAILABLE,
    MetricsCollector,
    get_server_health,
    get_uptime_seconds,
    mcp_log,
    metrics_collector,
)  # noqa: I001

# --- MetricsCollector unit tests ---


class TestMetricsCollectorBasics:
    def test_record_tool_call_increments_counter(self):
        mc = MetricsCollector()
        mc.record_tool_call("run_show_command", "success", 150.0)
        mc.record_tool_call("run_show_command", "success", 200.0)
        mc.record_tool_call("run_show_command", "error", 50.0)

        metrics = mc.get_metrics_json()
        assert metrics["tool_calls_total"]["run_show_command"]["success"] == 2
        assert metrics["tool_calls_total"]["run_show_command"]["error"] == 1

    def test_record_tool_call_tracks_duration(self):
        mc = MetricsCollector()
        mc.record_tool_call("run_show_command", "success", 100.0)
        mc.record_tool_call("run_show_command", "success", 300.0)

        metrics = mc.get_metrics_json()
        duration = metrics["tool_duration_seconds"]["run_show_command"]
        assert duration["count"] == 2
        assert duration["min_seconds"] == pytest.approx(0.1, abs=0.001)
        assert duration["max_seconds"] == pytest.approx(0.3, abs=0.001)
        assert duration["avg_seconds"] == pytest.approx(0.2, abs=0.001)

    def test_record_cache_hit(self):
        mc = MetricsCollector()
        mc.record_cache_hit()
        mc.record_cache_hit()
        mc.record_cache_hit()

        metrics = mc.get_metrics_json()
        assert metrics["cache_hits"] == 3

    def test_record_cache_miss(self):
        mc = MetricsCollector()
        mc.record_cache_miss()

        metrics = mc.get_metrics_json()
        assert metrics["cache_misses"] == 1

    def test_record_rate_limit_throttle(self):
        mc = MetricsCollector()
        mc.record_rate_limit_throttle()
        mc.record_rate_limit_throttle()

        metrics = mc.get_metrics_json()
        assert metrics["rate_limit_throttled"] == 2

    def test_reset_clears_all(self):
        mc = MetricsCollector()
        mc.record_tool_call("run_show_command", "success", 100.0)
        mc.record_cache_hit()
        mc.record_cache_miss()
        mc.record_rate_limit_throttle()

        mc.reset()
        metrics = mc.get_metrics_json()
        assert metrics["tool_calls_total"] == {}
        assert metrics["tool_duration_seconds"] == {}
        assert metrics["cache_hits"] == 0
        assert metrics["cache_misses"] == 0
        assert metrics["rate_limit_throttled"] == 0

    def test_empty_metrics_json(self):
        mc = MetricsCollector()
        metrics = mc.get_metrics_json()
        assert metrics["tool_calls_total"] == {}
        assert metrics["tool_duration_seconds"] == {}
        assert metrics["cache_hits"] == 0
        assert metrics["cache_misses"] == 0
        assert metrics["rate_limit_throttled"] == 0

    def test_multiple_tool_names(self):
        mc = MetricsCollector()
        mc.record_tool_call("run_show_command", "success", 100.0)
        mc.record_tool_call("run_config_command", "success", 200.0)

        metrics = mc.get_metrics_json()
        assert "run_show_command" in metrics["tool_calls_total"]
        assert "run_config_command" in metrics["tool_calls_total"]

    def test_duration_percentiles(self):
        mc = MetricsCollector()
        # Add 100 calls with durations 1ms to 100ms
        for i in range(1, 101):
            mc.record_tool_call("test_tool", "success", float(i))

        metrics = mc.get_metrics_json()
        duration = metrics["tool_duration_seconds"]["test_tool"]
        assert duration["count"] == 100
        assert duration["p50_seconds"] == pytest.approx(0.050, abs=0.002)
        assert duration["p99_seconds"] == pytest.approx(0.099, abs=0.002)


# --- Prometheus format tests ---


class TestPrometheusFormat:
    def test_prometheus_format_output(self):
        mc = MetricsCollector()
        mc.record_tool_call("run_show_command", "success", 100.0)
        mc.record_tool_call("run_show_command", "error", 50.0)
        mc.record_cache_hit()
        mc.record_cache_miss()
        mc.record_rate_limit_throttle()

        prom = mc.get_metrics_prometheus()

        assert "# HELP network_mcp_tool_calls_total" in prom
        assert "# TYPE network_mcp_tool_calls_total counter" in prom
        assert 'network_mcp_tool_calls_total{tool="run_show_command",status="success"} 1' in prom
        assert 'network_mcp_tool_calls_total{tool="run_show_command",status="error"} 1' in prom
        assert "network_mcp_cache_hits_total 1" in prom
        assert "network_mcp_cache_misses_total 1" in prom
        assert "network_mcp_rate_limit_throttled_total 1" in prom

    def test_prometheus_format_empty(self):
        mc = MetricsCollector()
        prom = mc.get_metrics_prometheus()

        # Should still have headers and zero values
        assert "network_mcp_cache_hits_total 0" in prom
        assert "network_mcp_cache_misses_total 0" in prom
        assert "network_mcp_rate_limit_throttled_total 0" in prom

    def test_prometheus_duration_summary(self):
        mc = MetricsCollector()
        mc.record_tool_call("run_show_command", "success", 150.0)

        prom = mc.get_metrics_prometheus()
        assert "# TYPE network_mcp_tool_call_duration_seconds summary" in prom
        assert 'network_mcp_tool_call_duration_seconds_sum{tool="run_show_command"}' in prom
        assert 'network_mcp_tool_call_duration_seconds_count{tool="run_show_command"} 1' in prom

    def test_prometheus_available_flag(self):
        # Just verify the flag is a bool — actual prometheus-client may or may not be installed
        assert isinstance(PROMETHEUS_AVAILABLE, bool)


# --- Thread safety tests ---


class TestMetricsCollectorConcurrency:
    def test_concurrent_recording(self):
        mc = MetricsCollector()
        errors = []
        barrier = threading.Barrier(10)

        def worker(thread_id: int):
            try:
                barrier.wait(timeout=5)
                for i in range(100):
                    mc.record_tool_call(f"tool_{thread_id % 3}", "success", float(i))
                    mc.record_cache_hit()
                    mc.record_cache_miss()
                    mc.record_rate_limit_throttle()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrency errors: {errors}"

        metrics = mc.get_metrics_json()
        assert metrics["cache_hits"] == 1000  # 10 threads * 100 iterations
        assert metrics["cache_misses"] == 1000
        assert metrics["rate_limit_throttled"] == 1000


# --- Uptime tests ---


class TestUptime:
    def test_uptime_is_positive(self):
        uptime = get_uptime_seconds()
        assert uptime >= 0.0

    def test_uptime_increases(self):
        u1 = get_uptime_seconds()
        import time

        time.sleep(0.01)
        u2 = get_uptime_seconds()
        assert u2 >= u1


# --- Server health tests ---


class TestServerHealth:
    def _make_mock_deps(self, open_circuits=None):
        """Create mock dependencies for get_server_health."""
        conn_mgr = MagicMock()
        conn_mgr.pool_stats.return_value = {"active": 2, "pooled": 5, "max": 100, "per_device": {}}

        cache = MagicMock()
        cache.stats.return_value = {
            "enabled": True,
            "entries": 42,
            "hit_rate": 0.73,
            "memory_mb": 1.5,
            "max_entries": 500,
            "max_memory_mb": 50.0,
            "hits": 100,
            "misses": 37,
        }

        cb_registry = MagicMock()
        cb_stats = {
            "enabled": True,
            "total_breakers": 3,
            "open_circuits": open_circuits or [],
            "breakers": {},
        }
        cb_registry.stats.return_value = cb_stats

        mc = MetricsCollector()
        return conn_mgr, cache, cb_registry, mc

    def test_health_status_healthy(self):
        conn_mgr, cache, cb_registry, mc = self._make_mock_deps(open_circuits=[])
        health = get_server_health(conn_mgr, cache, cb_registry, mc)

        assert health["status"] == "healthy"
        assert "version" in health
        assert health["uptime_seconds"] >= 0
        assert health["connections"]["active"] == 2
        assert health["connections"]["pooled"] == 5
        assert health["connections"]["max"] == 100
        assert health["circuit_breakers"]["open"] == 0
        assert health["circuit_breakers"]["devices"] == []
        assert health["cache"]["entries"] == 42
        assert health["cache"]["hit_rate"] == 0.73
        assert health["cache"]["memory_mb"] == 1.5
        assert health["rate_limiter"]["throttled_total"] == 0

    def test_health_status_degraded_with_open_breakers(self):
        conn_mgr, cache, cb_registry, mc = self._make_mock_deps(open_circuits=["leaf-03"])
        health = get_server_health(conn_mgr, cache, cb_registry, mc)

        assert health["status"] == "degraded"
        assert health["circuit_breakers"]["open"] == 1
        assert health["circuit_breakers"]["devices"] == ["leaf-03"]

    def test_health_includes_rate_limiter_metrics(self):
        conn_mgr, cache, cb_registry, mc = self._make_mock_deps()
        mc.record_rate_limit_throttle()
        mc.record_rate_limit_throttle()
        mc.record_rate_limit_throttle()

        health = get_server_health(conn_mgr, cache, cb_registry, mc)
        assert health["rate_limiter"]["throttled_total"] == 3

    def test_health_response_format_matches_v3_plan(self):
        """Verify the health response has all required top-level keys from V3 plan §14."""
        conn_mgr, cache, cb_registry, mc = self._make_mock_deps()
        health = get_server_health(conn_mgr, cache, cb_registry, mc)

        required_keys = {
            "status",
            "version",
            "uptime_seconds",
            "connections",
            "circuit_breakers",
            "cache",
            "rate_limiter",
        }
        assert required_keys.issubset(set(health.keys()))

        # Check nested key structure
        assert {"active", "pooled", "max"}.issubset(set(health["connections"].keys()))
        assert {"open", "devices"}.issubset(set(health["circuit_breakers"].keys()))
        assert {"entries", "hit_rate", "memory_mb"}.issubset(set(health["cache"].keys()))


# --- MCP tool tests ---


class TestHealthTool:
    def test_eos_server_health_returns_success(self, mock_node, mock_conn_mgr):
        from network_mcp.tools.device import eos_server_health

        result = eos_server_health()
        assert result["status"] == "success"
        assert "data" in result
        assert result["data"]["status"] in ("healthy", "degraded")

    def test_eos_server_health_connections(self, mock_node, mock_conn_mgr):
        from network_mcp.tools.device import eos_server_health

        result = eos_server_health()
        data = result["data"]
        assert "connections" in data
        assert "active" in data["connections"]
        assert "pooled" in data["connections"]
        assert "max" in data["connections"]

    def test_eos_server_health_cache_info(self, mock_node, mock_conn_mgr):
        from network_mcp.tools.device import eos_server_health

        result = eos_server_health()
        data = result["data"]
        assert "cache" in data
        assert "entries" in data["cache"]
        assert "hit_rate" in data["cache"]

    def test_eos_server_health_uptime(self, mock_node, mock_conn_mgr):
        from network_mcp.tools.device import eos_server_health

        result = eos_server_health()
        assert result["data"]["uptime_seconds"] >= 0


class TestMetricsTool:
    def test_eos_server_metrics_json_format(self, mock_node, mock_conn_mgr):
        from network_mcp.tools.device import eos_server_metrics

        result = eos_server_metrics(output_format="json")
        assert result["status"] == "success"
        assert result["format"] == "json"
        assert "tool_calls_total" in result["data"]
        assert "cache_hits" in result["data"]
        assert "cache_misses" in result["data"]
        assert "rate_limit_throttled" in result["data"]

    def test_eos_server_metrics_prometheus_format(self, mock_node, mock_conn_mgr):
        from network_mcp.tools.device import eos_server_metrics

        result = eos_server_metrics(output_format="prometheus")
        assert result["status"] == "success"
        assert result["format"] == "prometheus"
        assert isinstance(result["data"], str)
        assert "network_mcp_tool_calls_total" in result["data"]
        assert "network_mcp_cache_hits_total" in result["data"]

    def test_eos_server_metrics_default_is_json(self, mock_node, mock_conn_mgr):
        from network_mcp.tools.device import eos_server_metrics

        result = eos_server_metrics()
        assert result["format"] == "json"


# --- MCP logging utility tests ---


class TestMcpLog:
    def test_mcp_log_info(self):
        mock_logger = MagicMock()
        mcp_log("info", "Test message", logger_instance=mock_logger)
        mock_logger.log.assert_called_once_with(20, "Test message")  # 20 = INFO

    def test_mcp_log_warning(self):
        mock_logger = MagicMock()
        mcp_log("warning", "Warning message", logger_instance=mock_logger)
        mock_logger.log.assert_called_once_with(30, "Warning message")  # 30 = WARNING

    def test_mcp_log_error(self):
        mock_logger = MagicMock()
        mcp_log("error", "Error message", logger_instance=mock_logger)
        mock_logger.log.assert_called_once_with(40, "Error message")  # 40 = ERROR

    def test_mcp_log_debug(self):
        mock_logger = MagicMock()
        mcp_log("debug", "Debug message", logger_instance=mock_logger)
        mock_logger.log.assert_called_once_with(10, "Debug message")  # 10 = DEBUG

    def test_mcp_log_unknown_level_defaults_to_info(self):
        mock_logger = MagicMock()
        mcp_log("unknown", "Message", logger_instance=mock_logger)
        mock_logger.log.assert_called_once_with(20, "Message")  # Falls back to INFO


# --- Integration tests: metrics recorded by helpers ---


class TestMetricsIntegration:
    def test_show_command_records_success_metric(self, mock_node, mock_conn_mgr):
        """run_show_command should record a success metric to the collector."""
        from network_mcp.helpers import run_show_command
        from network_mcp.server import conn_mgr

        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]

        run_show_command(conn_mgr, "spine-01", ["show version"], cache_ttl=0)

        metrics = metrics_collector.get_metrics_json()
        assert "run_show_command" in metrics["tool_calls_total"]
        assert metrics["tool_calls_total"]["run_show_command"]["success"] == 1
        assert "run_show_command" in metrics["tool_duration_seconds"]

    def test_show_command_records_error_metric(self, mock_node, mock_conn_mgr):
        """run_show_command should record an error metric on connection failure."""
        import pyeapi.eapilib

        from network_mcp.helpers import run_show_command
        from network_mcp.server import conn_mgr

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("test", "test error")

        run_show_command(conn_mgr, "spine-01", ["show version"], cache_ttl=0)

        metrics = metrics_collector.get_metrics_json()
        assert metrics["tool_calls_total"]["run_show_command"]["error"] == 1

    def test_cache_hit_records_metric(self, mock_node, mock_conn_mgr):
        """Cache hit should increment the cache_hits counter."""
        from network_mcp.helpers import command_cache, run_show_command
        from network_mcp.server import conn_mgr

        command_cache.clear()
        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]

        # First call — populates cache (miss)
        run_show_command(conn_mgr, "spine-01", ["show version"], cache_ttl=300)
        # Second call — should be cache hit
        run_show_command(conn_mgr, "spine-01", ["show version"], cache_ttl=300)

        metrics = metrics_collector.get_metrics_json()
        assert metrics["cache_hits"] >= 1
        assert metrics["cache_misses"] >= 1

    def test_config_command_records_metric(self, mock_node, mock_conn_mgr):
        """run_config_command should record a metric."""
        from network_mcp.helpers import run_config_command
        from network_mcp.server import conn_mgr

        mock_node.config.return_value = [{}]

        with patch("network_mcp.helpers.check_read_only", return_value=None):
            run_config_command(conn_mgr, "spine-01", ["vlan 100", "name TEST"], "create_vlan")

        metrics = metrics_collector.get_metrics_json()
        assert "run_config_command" in metrics["tool_calls_total"]
        assert metrics["tool_calls_total"]["run_config_command"]["success"] == 1
