"""Tests for multi-device fabric tools."""

from unittest.mock import patch

import pyeapi.eapilib
import pytest

from network_mcp.connection import DeviceCredentials
from network_mcp.server import conn_mgr
from network_mcp.tools.fabric import (
    _bgp_operation,
    _config_operation,
    _health_operation,
    _mlag_operation,
    eos_compare_configs,
    eos_fabric_bgp_status,
    eos_fabric_health_async,
    eos_fabric_health_summary,
    eos_fabric_mlag_status,
    eos_list_inventory,
)
from tests.conftest import MOCK_BGP_SUMMARY, MOCK_MLAG, MOCK_SHOW_VERSION

# --- list_inventory tests ---


def test_list_inventory_empty():
    original = dict(conn_mgr._inventory)
    conn_mgr._inventory.clear()
    try:
        result = eos_list_inventory()
        assert result["status"] == "success"
        assert result["total"] == 0
        assert result["devices"] == []
    finally:
        conn_mgr._inventory.update(original)


def test_list_inventory_all():
    original = dict(conn_mgr._inventory)
    conn_mgr._inventory.clear()
    conn_mgr._inventory["spine-01"] = DeviceCredentials(host="10.0.0.1", role="spine", groups=["spines"], tags=["prod"])
    conn_mgr._inventory["leaf-01"] = DeviceCredentials(host="10.0.1.1", role="leaf", groups=["leafs"], tags=["prod"])
    try:
        result = eos_list_inventory()
        assert result["total"] == 2
        names = [d["name"] for d in result["devices"]]
        assert "spine-01" in names
        assert "leaf-01" in names
    finally:
        conn_mgr._inventory.clear()
        conn_mgr._inventory.update(original)


def test_list_inventory_filter_role():
    original = dict(conn_mgr._inventory)
    conn_mgr._inventory.clear()
    conn_mgr._inventory["spine-01"] = DeviceCredentials(host="10.0.0.1", role="spine", groups=["spines"], tags=["prod"])
    conn_mgr._inventory["leaf-01"] = DeviceCredentials(host="10.0.1.1", role="leaf", groups=["leafs"], tags=["prod"])
    try:
        result = eos_list_inventory(role="spine")
        assert result["total"] == 1
        assert result["devices"][0]["name"] == "spine-01"
    finally:
        conn_mgr._inventory.clear()
        conn_mgr._inventory.update(original)


def test_list_inventory_filter_group():
    original = dict(conn_mgr._inventory)
    conn_mgr._inventory.clear()
    conn_mgr._inventory["spine-01"] = DeviceCredentials(
        host="10.0.0.1", role="spine", groups=["spines", "dc1"], tags=["prod"]
    )
    conn_mgr._inventory["leaf-01"] = DeviceCredentials(
        host="10.0.1.1", role="leaf", groups=["leafs", "dc1"], tags=["prod"]
    )
    try:
        result = eos_list_inventory(group="spines")
        assert result["total"] == 1
        assert result["devices"][0]["name"] == "spine-01"
    finally:
        conn_mgr._inventory.clear()
        conn_mgr._inventory.update(original)


def test_list_inventory_filter_tag():
    original = dict(conn_mgr._inventory)
    conn_mgr._inventory.clear()
    conn_mgr._inventory["spine-01"] = DeviceCredentials(
        host="10.0.0.1", role="spine", groups=["spines"], tags=["prod", "evpn"]
    )
    conn_mgr._inventory["leaf-01"] = DeviceCredentials(host="10.0.1.1", role="leaf", groups=["leafs"], tags=["staging"])
    try:
        result = eos_list_inventory(tag="evpn")
        assert result["total"] == 1
        assert result["devices"][0]["name"] == "spine-01"
    finally:
        conn_mgr._inventory.clear()
        conn_mgr._inventory.update(original)


# --- Internal operation tests ---


def test_health_operation_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [
        MOCK_SHOW_VERSION,
        {"tempSensors": []},
        {"fanTraySlots": []},
        {"powerSupplies": {}},
    ]
    result = _health_operation(conn_mgr, "test-switch")
    assert result["status"] == "success"
    assert result["data"]["hostname"] == "test-switch"
    assert result["data"]["eos_version"] == "4.32.1F"
    assert result["data"]["memory_used_percent"] == 50.0


def test_health_operation_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
    result = _health_operation(conn_mgr, "bad-host")
    assert result["status"] == "error"


def test_bgp_operation_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_BGP_SUMMARY]
    result = _bgp_operation(conn_mgr, "test-switch")
    assert result["status"] == "success"
    assert result["data"]["established"] == 1
    assert result["data"]["down"] == 1
    assert "10.0.0.2" in result["data"]["peers"]
    assert result["data"]["peers"]["10.0.0.2"]["state"] == "Established"


def test_bgp_operation_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
    result = _bgp_operation(conn_mgr, "bad-host")
    assert result["status"] == "error"


def test_mlag_operation_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_MLAG]
    result = _mlag_operation(conn_mgr, "test-switch")
    assert result["status"] == "success"
    assert result["data"]["state"] == "active"
    assert result["data"]["domain_id"] == "mlag-domain-01"
    assert result["data"]["neg_status"] == "connected"


def test_mlag_operation_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
    result = _mlag_operation(conn_mgr, "bad-host")
    assert result["status"] == "error"


def test_config_operation_full(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"output": "hostname test-switch\n"}]
    result = _config_operation(conn_mgr, "test-switch")
    assert result["status"] == "success"
    assert "hostname test-switch" in result["data"]["config"]


def test_config_operation_section(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"output": "router bgp 65000\n"}]
    result = _config_operation(conn_mgr, "test-switch", section="router bgp")
    assert result["status"] == "success"
    assert "router bgp" in result["data"]["config"]
    # Verify the section command was used
    mock_node.run_commands.assert_called_once_with(["show running-config section router bgp"], encoding="text")


def test_config_operation_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
    result = _config_operation(conn_mgr, "bad-host")
    assert result["status"] == "error"


# --- Fabric tool integration tests (mocked executor) ---


@pytest.mark.asyncio
async def test_fabric_health_summary_no_match():
    original = dict(conn_mgr._inventory)
    conn_mgr._inventory.clear()
    try:
        result = await eos_fabric_health_summary("group:nonexistent")
        assert result["status"] == "error"
        assert "No devices matched" in result["error"]
    finally:
        conn_mgr._inventory.update(original)


@pytest.mark.asyncio
async def test_fabric_bgp_status_no_match():
    original = dict(conn_mgr._inventory)
    conn_mgr._inventory.clear()
    try:
        result = await eos_fabric_bgp_status("all")
        assert result["status"] == "error"
        assert "No devices matched" in result["error"]
    finally:
        conn_mgr._inventory.update(original)


@pytest.mark.asyncio
async def test_fabric_mlag_status_no_match():
    original = dict(conn_mgr._inventory)
    conn_mgr._inventory.clear()
    try:
        result = await eos_fabric_mlag_status("group:nonexistent")
        assert result["status"] == "error"
        assert "No devices matched" in result["error"]
    finally:
        conn_mgr._inventory.update(original)


@pytest.mark.asyncio
async def test_compare_configs_empty_list():
    result = await eos_compare_configs(devices=[])
    assert result["status"] == "error"
    assert "At least one device" in result["error"]


@pytest.mark.asyncio
async def test_fabric_health_summary_with_devices():
    """Test fabric_health_summary with mocked execute_on_devices."""
    from network_mcp.executor import MultiDeviceResult

    mock_result = MultiDeviceResult()
    mock_result.add_success(
        "spine-01",
        {
            "status": "success",
            "device": "spine-01",
            "data": {
                "hostname": "spine-01",
                "model": "DCS-7280SR",
                "eos_version": "4.32.1F",
                "uptime_seconds": 86400,
                "memory_used_percent": 45.0,
                "temperature": [],
                "cooling": [],
                "power": {},
            },
        },
    )
    mock_result.add_error("leaf-01", "Connection refused")

    with (
        patch("network_mcp.tools.fabric.resolve_hosts", return_value=["spine-01", "leaf-01"]),
        patch("network_mcp.tools.fabric.execute_on_devices", return_value=mock_result),
    ):
        result = await eos_fabric_health_summary("all")

    assert result["summary"]["total"] == 2
    assert result["summary"]["succeeded"] == 1
    assert result["summary"]["failed"] == 1
    assert "spine-01" in result["results"]
    assert result["results"]["spine-01"]["hostname"] == "spine-01"
    assert "leaf-01" in result["errors"]


@pytest.mark.asyncio
async def test_fabric_bgp_status_with_devices():
    """Test fabric_bgp_status with mocked execute_on_devices."""
    from network_mcp.executor import MultiDeviceResult

    mock_result = MultiDeviceResult()
    mock_result.add_success(
        "spine-01",
        {
            "status": "success",
            "device": "spine-01",
            "data": {
                "router_id": "10.0.0.1",
                "local_asn": "65000",
                "established": 3,
                "down": 1,
                "peers": {},
            },
        },
    )
    mock_result.add_success(
        "spine-02",
        {
            "status": "success",
            "device": "spine-02",
            "data": {
                "router_id": "10.0.0.2",
                "local_asn": "65000",
                "established": 4,
                "down": 0,
                "peers": {},
            },
        },
    )

    with (
        patch("network_mcp.tools.fabric.resolve_hosts", return_value=["spine-01", "spine-02"]),
        patch("network_mcp.tools.fabric.execute_on_devices", return_value=mock_result),
    ):
        result = await eos_fabric_bgp_status("group:spines")

    assert result["summary"]["total_established"] == 7
    assert result["summary"]["total_down"] == 1


@pytest.mark.asyncio
async def test_fabric_mlag_status_with_devices():
    """Test fabric_mlag_status with mocked execute_on_devices."""
    from network_mcp.executor import MultiDeviceResult

    mock_result = MultiDeviceResult()
    mock_result.add_success(
        "leaf-01a",
        {
            "status": "success",
            "device": "leaf-01a",
            "data": {
                "domain_id": "mlag-01",
                "state": "active",
                "neg_status": "connected",
                "peer_link": "Port-Channel10",
                "peer_address": "169.254.1.1",
                "mlag_ports": {"Active-full": 4},
            },
        },
    )
    mock_result.add_success(
        "leaf-01b",
        {
            "status": "success",
            "device": "leaf-01b",
            "data": {
                "domain_id": "mlag-01",
                "state": "active",
                "neg_status": "connected",
                "peer_link": "Port-Channel10",
                "peer_address": "169.254.1.2",
                "mlag_ports": {"Active-full": 4},
            },
        },
    )

    with (
        patch("network_mcp.tools.fabric.resolve_hosts", return_value=["leaf-01a", "leaf-01b"]),
        patch("network_mcp.tools.fabric.execute_on_devices", return_value=mock_result),
    ):
        result = await eos_fabric_mlag_status("tag:mlag")

    assert result["summary"]["active"] == 2
    assert result["summary"]["inactive"] == 0


@pytest.mark.asyncio
async def test_compare_configs_with_devices():
    """Test compare_configs with mocked execute_on_devices."""
    from network_mcp.executor import MultiDeviceResult

    mock_result = MultiDeviceResult()
    mock_result.add_success(
        "spine-01",
        {
            "status": "success",
            "device": "spine-01",
            "data": {"config": "hostname spine-01\n"},
        },
    )
    mock_result.add_success(
        "spine-02",
        {
            "status": "success",
            "device": "spine-02",
            "data": {"config": "hostname spine-02\n"},
        },
    )

    with patch("network_mcp.tools.fabric.execute_on_devices", return_value=mock_result):
        result = await eos_compare_configs(devices=["spine-01", "spine-02"])

    assert result["summary"]["succeeded"] == 2
    assert "spine-01" in result["results"]
    assert "spine-02" in result["results"]
    assert "hostname spine-01" in result["results"]["spine-01"]


@pytest.mark.asyncio
async def test_compare_configs_with_section():
    """Test compare_configs with a section filter."""
    from network_mcp.executor import MultiDeviceResult

    mock_result = MultiDeviceResult()
    mock_result.add_success(
        "spine-01",
        {
            "status": "success",
            "device": "spine-01",
            "data": {"config": "router bgp 65000\n"},
        },
    )

    with patch("network_mcp.tools.fabric.execute_on_devices", return_value=mock_result):
        result = await eos_compare_configs(devices=["spine-01"], section="router bgp")

    assert result["section"] == "router bgp"
    assert "router bgp" in result["results"]["spine-01"]


# --- eos_fabric_health_async tests ---


@pytest.mark.asyncio
async def test_fabric_health_async_no_devices():
    """Returns error when no devices match the target."""
    original = dict(conn_mgr._inventory)
    conn_mgr._inventory.clear()
    try:
        result = await eos_fabric_health_async("group:nonexistent")
        assert result["status"] == "error"
        assert "No devices matched" in result["error"]
    finally:
        conn_mgr._inventory.update(original)


@pytest.mark.asyncio
async def test_fabric_health_async_sync_fallback():
    """Falls back to synchronous execution when ctx is None (no MCP Tasks)."""
    from network_mcp.executor import MultiDeviceResult

    mock_result = MultiDeviceResult()
    mock_result.add_success(
        "spine-01",
        {
            "status": "success",
            "device": "spine-01",
            "data": {
                "hostname": "spine-01",
                "model": "DCS-7280SR",
                "eos_version": "4.32.1F",
                "uptime_seconds": 86400,
                "memory_used_percent": 45.0,
                "temperature": [],
                "cooling": [],
                "power": {},
            },
        },
    )

    with (
        patch("network_mcp.tools.fabric.resolve_hosts", return_value=["spine-01"]),
        patch("network_mcp.tools.fabric.execute_on_devices", return_value=mock_result),
    ):
        result = await eos_fabric_health_async("all", ctx=None)

    assert result["summary"]["total"] == 1
    assert result["summary"]["succeeded"] == 1
    assert "spine-01" in result["results"]
