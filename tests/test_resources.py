"""Tests for MCP resource endpoints."""

import json

import pytest

from network_mcp.resources.inventory import (
    device_health_summary,
    device_inventory,
    device_version,
    interfaces_summary,
    running_config,
)
from network_mcp.server import conn_mgr
from tests.conftest import MOCK_INTERFACES_STATUS, MOCK_SHOW_VERSION


def test_device_version(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_SHOW_VERSION]

    result = json.loads(device_version("test-switch"))

    assert result["hostname"] == "test-switch"
    assert result["version"] == "4.32.1F"
    mock_node.run_commands.assert_called_once_with(["show version"], encoding="json")


def test_device_version_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = Exception("Connection refused")

    result = json.loads(device_version("test-switch"))

    assert "error" in result
    assert "Connection refused" in result["error"]


def test_device_version_invalid_host():
    """Invalid host returns JSON error (from validate_host)."""
    result = json.loads(device_version(""))
    assert "error" in result


def test_running_config(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"output": "! device: test-switch\nhostname test-switch\n"}]

    result = running_config("test-switch")

    assert "hostname test-switch" in result
    mock_node.run_commands.assert_called_once_with(["show running-config"], encoding="text")


def test_running_config_sanitizes_passwords(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"output": "username admin secret 0 s3cretpass\n"}]

    result = running_config("test-switch")

    assert "s3cretpass" not in result


def test_running_config_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = Exception("Timeout")

    result = running_config("test-switch")
    parsed = json.loads(result)

    assert "error" in parsed


def test_device_inventory():
    """Test inventory listing with populated inventory."""
    from pydantic import SecretStr

    from network_mcp.connection import DeviceCredentials

    original = dict(conn_mgr._inventory)
    try:
        conn_mgr._inventory["spine-01"] = DeviceCredentials(host="10.0.0.1", transport="https", password=SecretStr(""))
        conn_mgr._inventory["leaf-01"] = DeviceCredentials(host="10.0.1.1", transport="http", password=SecretStr(""))

        result = json.loads(device_inventory())

        assert isinstance(result, list)
        assert len(result) >= 2
        names = [d["name"] for d in result]
        assert "spine-01" in names
        assert "leaf-01" in names
    finally:
        conn_mgr._inventory.clear()
        conn_mgr._inventory.update(original)


def test_device_inventory_empty():
    """Empty inventory returns empty list."""
    original = dict(conn_mgr._inventory)
    try:
        conn_mgr._inventory.clear()

        result = json.loads(device_inventory())

        assert result == []
    finally:
        conn_mgr._inventory.update(original)


def test_device_health_summary(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_SHOW_VERSION, MOCK_INTERFACES_STATUS]

    result = json.loads(device_health_summary("test-switch"))

    assert result["hostname"] == "test-switch"
    assert result["model"] == "vEOS-lab"
    assert result["version"] == "4.32.1F"
    assert "memory_used_percent" in result
    assert "interfaces_up" in result
    assert "interfaces_down" in result


def test_device_health_summary_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = Exception("Connection failed")

    result = json.loads(device_health_summary("test-switch"))

    assert "error" in result


def test_interfaces_summary(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [
        MOCK_INTERFACES_STATUS,
        {"interfaceErrorCounters": {"Ethernet1": {"crcErrors": 5, "inputErrors": 3}}},
    ]

    result = json.loads(interfaces_summary("test-switch"))

    assert result["total_interfaces"] == 2
    assert result["connected"] == 1
    assert result["not_connected"] == 1
    assert len(result["interfaces_with_errors"]) == 1


def test_interfaces_summary_no_errors(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [
        MOCK_INTERFACES_STATUS,
        {"interfaceErrorCounters": {"Ethernet1": {"crcErrors": 0, "inputErrors": 0}}},
    ]

    result = json.loads(interfaces_summary("test-switch"))

    assert result["interfaces_with_errors"] == []


# --- V1 Resource Host Validation Tests ---

INJECTION_PAYLOADS = [
    "host; rm -rf /",
    "host$(whoami)",
    "host`id`",
    "host${HOME}",
    "host|cat /etc/passwd",
    "host\nreload",
    "host\x00evil",
]


class TestV1ResourceHostValidation:
    """Verify that V1 resource functions reject injection payloads."""

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_device_version_rejects_injection(self, payload):
        result = json.loads(device_version(payload))
        assert "error" in result

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_running_config_rejects_injection(self, payload):
        result = running_config(payload)
        # running_config may return plain text or JSON on error
        assert "error" in result.lower() or "invalid" in result.lower()

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_health_summary_rejects_injection(self, payload):
        result = json.loads(device_health_summary(payload))
        assert "error" in result

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_interfaces_summary_rejects_injection(self, payload):
        result = json.loads(interfaces_summary(payload))
        assert "error" in result

    def test_health_summary_accepts_valid_host(self, mock_conn_mgr, mock_node):
        """Valid host should proceed past validation (may fail on connection, not validation)."""
        mock_node.run_commands.return_value = [MOCK_SHOW_VERSION, MOCK_INTERFACES_STATUS]
        result = json.loads(device_health_summary("valid-host"))
        # Should NOT be a validation error
        assert (
            result.get("hostname") == "test-switch"
            or "error" not in result
            or "Invalid host" not in result.get("error", "")
        )

    def test_device_version_rejects_empty_host(self):
        """Empty host returns JSON error."""
        result = json.loads(device_version(""))
        assert "error" in result

    def test_running_config_rejects_empty_host(self):
        """Empty host returns JSON error."""
        result = running_config("")
        assert "error" in result.lower()
