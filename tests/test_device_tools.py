"""Tests for device information tools."""

import pyeapi.eapilib

from network_mcp.tools.device import (
    eos_get_device_health,
    eos_get_device_info,
    eos_get_hardware_capacity,
    eos_get_hardware_drops,
    eos_get_hardware_inventory,
    eos_get_running_extensions,
    eos_get_terminattr_status,
    eos_get_users,
)
from tests.conftest import (
    MOCK_EMPTY_RESPONSE,
    MOCK_HARDWARE_CAPACITY,
    MOCK_HARDWARE_DROPS,
    MOCK_SHOW_VERSION,
    MOCK_TERMINATTR,
)


def test_get_device_info(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_SHOW_VERSION]

    result = eos_get_device_info("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert result["data"]["model"] == "vEOS-lab"
    assert result["data"]["eos_version"] == "4.32.1F"
    assert result["data"]["serial_number"] == "TEST123"
    assert result["data"]["uptime_seconds"] == 86400
    mock_node.run_commands.assert_called_once_with(["show version"], encoding="json")


def test_get_device_info_connection_error(mock_conn_mgr, mock_node):
    import pyeapi.eapilib

    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_device_info("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_get_device_health(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [
        MOCK_SHOW_VERSION,
        {"cpuInfo": {"%Cpu(s)": "5.0"}},
        {"tempSensors": []},
        {"fanTraySlots": []},
        {"powerSupplies": {}},
    ]

    result = eos_get_device_health("test-switch")

    assert result["status"] == "success"
    assert result["data"]["memory"]["total_kb"] == 2048000
    assert result["data"]["memory"]["used_percent"] == 50.0


def test_get_hardware_inventory(mock_conn_mgr, mock_node):
    mock_response = {"systemInformation": {"name": "vEOS-lab"}}
    mock_node.run_commands.return_value = [mock_response]

    result = eos_get_hardware_inventory("test-switch")

    assert result["status"] == "success"
    assert result["data"] == mock_response


def test_get_running_extensions(mock_conn_mgr, mock_node):
    mock_response = {"extensions": {}}
    mock_node.run_commands.return_value = [mock_response]

    result = eos_get_running_extensions("test-switch")

    assert result["status"] == "success"


def test_get_users(mock_conn_mgr, mock_node):
    mock_response = {"users": {"admin": {"line": "con0"}}}
    mock_node.run_commands.return_value = [mock_response]

    result = eos_get_users("test-switch")

    assert result["status"] == "success"


# --- New device tool tests ---


def test_get_hardware_capacity(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_HARDWARE_CAPACITY]

    result = eos_get_hardware_capacity("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert len(result["data"]["tables"]) == 1
    assert result["data"]["tables"][0]["table"] == "IPv4 /32 hosts"
    assert result["data"]["tables"][0]["used"] == 50
    mock_node.run_commands.assert_called_once_with(["show hardware capacity"], encoding="json")


def test_get_hardware_capacity_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_hardware_capacity("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_get_hardware_drops(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_HARDWARE_DROPS]

    result = eos_get_hardware_drops("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert result["data"]["totalPacketProcessor"]["sobcDrop"] == 0
    mock_node.run_commands.assert_called_once_with(["show hardware counter drop"], encoding="json")


def test_get_hardware_drops_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_hardware_drops("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_get_terminattr_status(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_TERMINATTR]

    result = eos_get_terminattr_status("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert result["data"]["daemons"]["TerminAttr"]["running"] is True
    mock_node.run_commands.assert_called_once_with(["show daemon TerminAttr"], encoding="json")


def test_get_terminattr_status_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_terminattr_status("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- Timeout and error scenario tests ---


def test_get_device_info_timeout(mock_conn_mgr, mock_node_timeout):
    """Device info returns error on timeout."""
    result = eos_get_device_info("test-switch")

    assert result["status"] == "error"
    assert "timed out" in result["error"].lower() or "error" in result["error"].lower()


def test_get_device_health_timeout(mock_conn_mgr, mock_node_timeout):
    """Device health returns error on timeout."""
    result = eos_get_device_health("test-switch")

    assert result["status"] == "error"


def test_get_device_info_empty_response(mock_conn_mgr, mock_node_empty):
    """Device info handles empty response gracefully."""
    result = eos_get_device_info("test-switch")

    assert result["status"] == "success"
    # Empty response means missing keys default to None via .get()
    assert result["data"]["model"] is None
    assert result["data"]["eos_version"] is None


def test_get_hardware_capacity_empty_response(mock_conn_mgr, mock_node):
    """Hardware capacity handles empty response without crashing."""
    mock_node.run_commands.return_value = [MOCK_EMPTY_RESPONSE]

    result = eos_get_hardware_capacity("test-switch")

    assert result["status"] == "success"
    # Raw dict returned as-is — no 'tables' key in empty response
    assert result["data"] == {}


def test_get_hardware_drops_timeout(mock_conn_mgr, mock_node_timeout):
    """Hardware drops returns error on timeout."""
    result = eos_get_hardware_drops("test-switch")

    assert result["status"] == "error"
