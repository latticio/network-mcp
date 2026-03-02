"""Tests for session 5.3: version centralization, vendor metrics dimension, audit HMAC hardening."""

from unittest.mock import MagicMock, patch

from network_mcp.audit import AuditLogger, _resolve_signing_key
from network_mcp.observability import MetricsCollector, get_server_health, get_version

# ---------------------------------------------------------------------------
# Fix 1: Centralized version resolution
# ---------------------------------------------------------------------------


class TestGetVersion:
    def test_get_version_returns_string(self):
        ver = get_version()
        assert isinstance(ver, str)
        assert len(ver) > 0

    def test_get_version_matches_init(self):
        from network_mcp import __version__

        ver = get_version()
        # May differ if installed package metadata differs, but should match __version__ in dev
        assert ver == __version__ or ver.endswith("-dev")

    def test_get_version_fallback_on_metadata_failure(self):
        """When importlib.metadata.version raises, falls back to __version__."""
        from network_mcp import __version__

        with patch("importlib.metadata.version", side_effect=Exception("not found")):
            ver = get_version()
            assert ver == __version__

    def test_server_health_uses_get_version(self):
        """get_server_health should use the centralized get_version()."""
        conn_mgr = MagicMock()
        conn_mgr.pool_stats.return_value = {"active": 0, "pooled": 0, "max": 10}

        cache = MagicMock()
        cache.stats.return_value = {"enabled": True, "entries": 0, "hit_rate": 0.0, "memory_mb": 0.0}

        cb_registry = MagicMock()
        cb_registry.stats.return_value = {"enabled": False, "open_circuits": []}

        mc = MetricsCollector()

        with patch("network_mcp.observability.get_version", return_value="4.0.0-test") as mock_ver:
            health = get_server_health(conn_mgr, cache, cb_registry, mc)
            mock_ver.assert_called_once()
            assert health["version"] == "4.0.0-test"


# ---------------------------------------------------------------------------
# Fix 2: Vendor dimension in metrics
# ---------------------------------------------------------------------------


class TestVendorMetrics:
    def test_record_tool_call_accepts_vendor(self):
        mc = MetricsCollector()
        mc.record_tool_call("run_show_command", "success", 100.0, vendor="eos")
        mc.record_tool_call("run_show_command", "success", 150.0, vendor="cisco")
        mc.record_tool_call("run_show_command", "error", 50.0, vendor="cisco")

        metrics = mc.get_metrics_json()
        assert metrics["vendor_calls"]["eos"]["success"] == 1
        assert metrics["vendor_calls"]["cisco"]["success"] == 1
        assert metrics["vendor_calls"]["cisco"]["error"] == 1

    def test_record_tool_call_defaults_to_eos(self):
        mc = MetricsCollector()
        mc.record_tool_call("run_show_command", "success", 100.0)

        metrics = mc.get_metrics_json()
        assert "eos" in metrics["vendor_calls"]
        assert metrics["vendor_calls"]["eos"]["success"] == 1

    def test_vendor_calls_in_metrics_json(self):
        mc = MetricsCollector()
        mc.record_tool_call("tool_a", "success", 10.0, vendor="juniper")
        mc.record_tool_call("tool_b", "success", 20.0, vendor="juniper")
        mc.record_tool_call("tool_c", "error", 5.0, vendor="eos")

        metrics = mc.get_metrics_json()
        assert "vendor_calls" in metrics
        assert metrics["vendor_calls"]["juniper"]["success"] == 2
        assert metrics["vendor_calls"]["eos"]["error"] == 1

    def test_vendor_calls_empty_initially(self):
        mc = MetricsCollector()
        metrics = mc.get_metrics_json()
        assert metrics["vendor_calls"] == {}

    def test_vendor_calls_reset(self):
        mc = MetricsCollector()
        mc.record_tool_call("tool", "success", 10.0, vendor="cisco")
        mc.reset()
        metrics = mc.get_metrics_json()
        assert metrics["vendor_calls"] == {}

    def test_vendor_calls_in_prometheus_format(self):
        mc = MetricsCollector()
        mc.record_tool_call("tool", "success", 10.0, vendor="eos")
        mc.record_tool_call("tool", "error", 5.0, vendor="cisco")

        prom = mc.get_metrics_prometheus()
        assert "# HELP network_mcp_vendor_calls_total" in prom
        assert "# TYPE network_mcp_vendor_calls_total counter" in prom
        assert 'network_mcp_vendor_calls_total{vendor="eos",status="success"} 1' in prom
        assert 'network_mcp_vendor_calls_total{vendor="cisco",status="error"} 1' in prom

    def test_server_health_includes_vendor_breakdown(self):
        conn_mgr = MagicMock()
        conn_mgr.pool_stats.return_value = {"active": 0, "pooled": 0, "max": 10}

        cache = MagicMock()
        cache.stats.return_value = {"enabled": True, "entries": 0, "hit_rate": 0.0, "memory_mb": 0.0}

        cb_registry = MagicMock()
        cb_registry.stats.return_value = {"enabled": False, "open_circuits": []}

        mc = MetricsCollector()
        mc.record_tool_call("run_show_command", "success", 100.0, vendor="eos")
        mc.record_tool_call("run_show_command", "success", 200.0, vendor="cisco")

        health = get_server_health(conn_mgr, cache, cb_registry, mc)
        assert "vendor_calls" in health
        assert health["vendor_calls"]["eos"]["success"] == 1
        assert health["vendor_calls"]["cisco"]["success"] == 1


# ---------------------------------------------------------------------------
# Fix 3: Audit logger HMAC hardening
# ---------------------------------------------------------------------------


class TestAuditHMACHardening:
    def test_resolve_signing_key_with_audit_key(self):
        """AUDIT_SIGNING_KEY takes priority."""
        mock_settings = MagicMock()
        mock_settings.audit_signing_key.get_secret_value.return_value = "my-audit-key"
        mock_settings.auth_secret_key.get_secret_value.return_value = "my-auth-key"

        with patch("network_mcp.config.NetworkSettings", return_value=mock_settings):
            key = _resolve_signing_key()
            assert key == b"my-audit-key"

    def test_resolve_signing_key_falls_back_to_auth_key(self):
        """Falls back to AUTH_SECRET_KEY when AUDIT_SIGNING_KEY is not set."""
        mock_settings = MagicMock()
        mock_settings.audit_signing_key = None
        mock_settings.auth_secret_key.get_secret_value.return_value = "auth-secret"

        with patch("network_mcp.config.NetworkSettings", return_value=mock_settings):
            key = _resolve_signing_key()
            assert key == b"auth-secret"

    def test_resolve_signing_key_returns_none_when_no_keys(self):
        """Returns None when neither key is set."""
        mock_settings = MagicMock()
        mock_settings.audit_signing_key = None
        mock_settings.auth_secret_key = None

        with patch("network_mcp.config.NetworkSettings", return_value=mock_settings):
            key = _resolve_signing_key()
            assert key is None

    def test_resolve_signing_key_warns_on_empty_audit_key(self):
        """Logs warning when AUDIT_SIGNING_KEY is set but empty."""
        import logging as _logging

        mock_settings = MagicMock()
        mock_settings.audit_signing_key.get_secret_value.return_value = ""
        mock_settings.auth_secret_key = None

        setup_logger = _logging.getLogger("network-mcp.audit.setup")
        with (
            patch("network_mcp.config.NetworkSettings", return_value=mock_settings),
            patch.object(setup_logger, "warning") as mock_warn,
        ):
            key = _resolve_signing_key()
            assert key is None
            mock_warn.assert_any_call("AUDIT_SIGNING_KEY is set but empty; falling back to AUTH_SECRET_KEY")

    def test_resolve_signing_key_warns_on_empty_auth_key(self):
        """Logs warning when AUTH_SECRET_KEY is set but empty."""
        import logging as _logging

        mock_settings = MagicMock()
        mock_settings.audit_signing_key = None
        mock_settings.auth_secret_key.get_secret_value.return_value = ""

        setup_logger = _logging.getLogger("network-mcp.audit.setup")
        with (
            patch("network_mcp.config.NetworkSettings", return_value=mock_settings),
            patch.object(setup_logger, "warning") as mock_warn,
        ):
            key = _resolve_signing_key()
            assert key is None
            mock_warn.assert_any_call("AUTH_SECRET_KEY is set but empty; HMAC audit signing disabled")

    def test_resolve_signing_key_warns_on_settings_failure(self):
        """Logs warning when NetworkSettings fails to initialize."""
        import logging as _logging

        setup_logger = _logging.getLogger("network-mcp.audit.setup")
        with (
            patch("network_mcp.config.NetworkSettings", side_effect=Exception("config error")),
            patch.object(setup_logger, "warning") as mock_warn,
        ):
            key = _resolve_signing_key()
            assert key is None
            mock_warn.assert_called_once_with(
                "Could not load NetworkSettings for audit HMAC key resolution; HMAC signing disabled"
            )

    def test_audit_logger_works_without_signing_key(self, capfd):
        """AuditLogger should work fine without HMAC signing — no crash."""
        audit = AuditLogger(logger_name="test-no-hmac-hardening", signing_key=None)
        audit.log_tool_call(
            tool_name="get_vlans",
            device="spine-01",
            parameters={"host": "spine-01"},
            result_status="success",
            duration_ms=10.0,
        )
        captured = capfd.readouterr()
        assert "get_vlans" in captured.err
