"""Tests for demo mode coverage of integration tools.

Verifies that all integration tools (NetBox, ServiceNow, Prometheus)
return valid mock responses when NET_DEMO_MODE=true, without requiring
external service dependencies.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def demo_settings():
    """Patch settings to enable demo mode with no external service config."""
    mock_settings = MagicMock()
    mock_settings.net_demo_mode = True
    mock_settings.netbox_url = None
    mock_settings.netbox_token = None
    mock_settings.snow_instance = None
    mock_settings.snow_username = None
    mock_settings.snow_password = None
    return mock_settings


# ---------------------------------------------------------------------------
# NetBox demo mode tests
# ---------------------------------------------------------------------------


class TestNetBoxDemoMode:
    """Verify NetBox integration tools return valid data in demo mode."""

    def test_sync_from_netbox_demo(self, demo_settings):
        with patch("network_mcp.integrations.netbox.settings", demo_settings):
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox()
        assert result["status"] == "success"
        assert result["demo_mode"] is True
        assert result["synced_count"] == 6
        assert len(result["devices"]) == 6
        # Verify device structure
        device = result["devices"][0]
        assert "name" in device
        assert "host" in device
        assert "platform" in device
        assert "serial" in device

    def test_push_to_netbox_demo(self, demo_settings):
        with patch("network_mcp.integrations.netbox.settings", demo_settings):
            from network_mcp.integrations.netbox import net_push_to_netbox

            result = net_push_to_netbox(host="spine-01")
        assert result["status"] == "success"
        assert result["demo_mode"] is True
        assert result["device"] == "spine-01"
        assert result["action"] == "updated"
        assert "netbox_id" in result

    def test_drift_check_demo(self, demo_settings):
        with patch("network_mcp.integrations.netbox.settings", demo_settings):
            from network_mcp.integrations.netbox import net_netbox_drift_check

            result = net_netbox_drift_check()
        assert result["status"] == "success"
        assert result["demo_mode"] is True
        assert result["total_devices"] == 6
        assert isinstance(result["drifts"], list)
        assert result["drift_count"] == len(result["drifts"])

    def test_cable_audit_demo(self, demo_settings):
        with patch("network_mcp.integrations.netbox.settings", demo_settings):
            from network_mcp.integrations.netbox import net_netbox_cable_audit

            result = net_netbox_cable_audit()
        assert result["status"] == "success"
        assert result["demo_mode"] is True
        assert result["cables_checked"] == 16
        assert isinstance(result["mismatches"], list)


# ---------------------------------------------------------------------------
# ServiceNow demo mode tests
# ---------------------------------------------------------------------------


class TestServiceNowDemoMode:
    """Verify ServiceNow integration tools return valid data in demo mode."""

    def test_create_incident_demo(self, demo_settings):
        with patch("network_mcp.integrations.servicenow.settings", demo_settings):
            from network_mcp.integrations.servicenow import net_create_incident

            result = net_create_incident(host="spine-01", short_description="Test incident")
        assert result["status"] == "success"
        assert result["demo_mode"] is True
        assert result["device"] == "spine-01"
        assert "incident_number" in result
        assert result["incident_number"].startswith("INC")

    def test_sync_cmdb_demo(self, demo_settings):
        with patch("network_mcp.integrations.servicenow.settings", demo_settings):
            from network_mcp.integrations.servicenow import net_sync_cmdb

            result = net_sync_cmdb(host="leaf-01")
        assert result["status"] == "success"
        assert result["demo_mode"] is True
        assert result["device"] == "leaf-01"
        assert "cmdb_sys_id" in result

    def test_update_incident_demo(self, demo_settings):
        with patch("network_mcp.integrations.servicenow.settings", demo_settings):
            from network_mcp.integrations.servicenow import net_update_incident

            result = net_update_incident(host="spine-01", sys_id="abc123", work_notes="Test note")
        assert result["status"] == "success"
        assert result["demo_mode"] is True
        assert result["device"] == "spine-01"
        assert "incident_number" in result

    def test_validate_change_demo(self, demo_settings):
        with patch("network_mcp.integrations.servicenow.settings", demo_settings):
            from network_mcp.integrations.servicenow import net_validate_change

            result = net_validate_change(
                host="spine-01",
                change_sys_id="chg001",
                pre_snapshot_commands=["show version"],
            )
        assert result["status"] == "success"
        assert result["demo_mode"] is True
        assert result["device"] == "spine-01"
        assert "snapshot" in result


# ---------------------------------------------------------------------------
# Prometheus demo mode tests
# ---------------------------------------------------------------------------


class TestPrometheusDemoMode:
    """Verify Prometheus metrics tool returns valid data in demo mode."""

    def test_collect_metrics_demo(self, demo_settings):
        with patch("network_mcp.integrations.prometheus.settings", demo_settings):
            from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

            result = net_collect_prometheus_metrics()
        assert result["status"] == "success"
        assert result["demo_mode"] is True
        assert result["hosts_collected"] == 6
        assert result["hosts_with_errors"] == 0
        # Verify Prometheus text format contains expected metrics
        text = result["prometheus_text"]
        assert "network_device_cpu_percent" in text
        assert "network_device_memory_percent" in text
        assert "network_bgp_peers_established" in text
        assert "network_device_uptime_seconds" in text
        assert "spine-01" in text
        assert "leaf-01" in text

    def test_collect_metrics_demo_with_hosts(self, demo_settings):
        """Demo mode ignores host filter and returns full mock data."""
        with patch("network_mcp.integrations.prometheus.settings", demo_settings):
            from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

            result = net_collect_prometheus_metrics(hosts=["spine-01"])
        assert result["status"] == "success"
        assert result["demo_mode"] is True


# ---------------------------------------------------------------------------
# Mock data validity tests
# ---------------------------------------------------------------------------


class TestMockDataValidity:
    """Verify mock data structures are well-formed."""

    def test_netbox_sync_has_required_fields(self):
        from network_mcp.mock_data.integrations import NETBOX_SYNC_RESPONSE

        assert NETBOX_SYNC_RESPONSE["status"] == "success"
        for device in NETBOX_SYNC_RESPONSE["devices"]:
            assert "name" in device
            assert "host" in device
            assert "platform" in device
            assert "serial" in device

    def test_prometheus_text_is_valid_format(self):
        from network_mcp.mock_data.integrations import PROMETHEUS_METRICS_RESPONSE

        text = PROMETHEUS_METRICS_RESPONSE["prometheus_text"]
        lines = text.strip().split("\n")
        for line in lines:
            # Lines should be HELP, TYPE, or metric lines
            assert line.startswith("# HELP") or line.startswith("# TYPE") or line.startswith("network_"), (
                f"Invalid Prometheus line: {line}"
            )

    def test_servicenow_incident_number_format(self):
        from network_mcp.mock_data.integrations import SERVICENOW_CREATE_INCIDENT_RESPONSE

        assert SERVICENOW_CREATE_INCIDENT_RESPONSE["incident_number"].startswith("INC")

    def test_demo_responses_are_deep_copied(self, demo_settings):
        """Verify that demo responses are deep-copied to prevent mutation."""
        with patch("network_mcp.integrations.netbox.settings", demo_settings):
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result1 = net_sync_from_netbox()
            result2 = net_sync_from_netbox()
        # Mutating one should not affect the other
        result1["devices"].append({"name": "extra"})
        assert len(result2["devices"]) == 6
