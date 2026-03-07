"""Phase 3: Live tool tests — run actual MCP tools against real devices.

These tests import the actual tool functions and call them with real hosts,
validating that the full tool pipeline (validation, connection, parsing,
response formatting) works end-to-end.

Run with: uv run pytest tests/integration/test_tools_live.py -v --run-integration
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

# All tool modules that import conn_mgr at module level
_TOOL_MODULES = [
    "network_mcp.tools.common.device",
    "network_mcp.tools.common.interfaces",
    "network_mcp.tools.common.routing",
    "network_mcp.tools.common.switching",
    "network_mcp.tools.common.monitoring",
    "network_mcp.tools.common.config",
    "network_mcp.tools.common.vlans",
]


@pytest.fixture(scope="module")
def live_conn_mgr(lab_nodes):
    """Module-scoped ConnectionManager with real lab devices."""
    from network_mcp.config import NetworkSettings
    from network_mcp.connection import ConnectionManager, DeviceCredentials

    settings = NetworkSettings(
        net_username="admin",
        net_password="admin",
        net_transport="https",
        net_read_only=True,
        net_demo_mode=False,
        net_verify_ssl=False,
    )
    mgr = ConnectionManager(settings)

    for node in lab_nodes:
        mgr._inventory[node["name"]] = DeviceCredentials(
            host=node["ip"],
            username="admin",
            password="admin",
            transport="https",
            port=443,
            platform="eos",
            tags=["containerlab"],
        )

    return mgr


@pytest.fixture(autouse=True)
def patch_conn_mgr(live_conn_mgr):
    """Patch conn_mgr in server AND in every tool module that imports it."""
    import importlib
    import sys

    from network_mcp import server

    patches = [patch.object(server, "conn_mgr", live_conn_mgr)]

    # Patch conn_mgr in each tool module that imported it at module level
    for mod_name in _TOOL_MODULES:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            if hasattr(mod, "conn_mgr"):
                patches.append(patch.object(mod, "conn_mgr", live_conn_mgr))

    # Also ensure tool modules are imported so the patch targets exist
    for mod_name in _TOOL_MODULES:
        if mod_name not in sys.modules:
            importlib.import_module(mod_name)
            mod = sys.modules[mod_name]
            if hasattr(mod, "conn_mgr"):
                patches.append(patch.object(mod, "conn_mgr", live_conn_mgr))

    for p in patches:
        p.start()

    yield

    for p in patches:
        p.stop()


class TestDeviceTools:
    """Test device info/health tools against real devices."""

    def test_get_device_info(self, lab_node):
        """net_get_device_info should return real device facts."""
        from network_mcp.tools.common.device import net_get_device_info

        result = net_get_device_info(host=lab_node["name"])
        assert result["status"] == "success", f"Failed: {result.get('error')}"
        assert result["device"] == lab_node["name"]
        data = result["data"]
        assert "hostname" in data
        assert "version" in data

    def test_get_device_health(self, lab_node):
        """net_get_device_health should return health metrics."""
        from network_mcp.tools.common.monitoring import net_get_device_health

        result = net_get_device_health(host=lab_node["name"])
        assert result["status"] == "success", f"Failed: {result.get('error')}"


class TestInterfaceTools:
    """Test interface tools against real devices."""

    def test_get_interfaces(self, lab_node):
        """net_get_interfaces should return interface list."""
        from network_mcp.tools.common.interfaces import net_get_interfaces

        result = net_get_interfaces(host=lab_node["name"])
        assert result["status"] == "success", f"Failed: {result.get('error')}"
        assert "data" in result
        interfaces = result["data"]
        assert isinstance(interfaces, dict)
        # Should have Management0 at minimum
        mgmt_found = any("anagement" in k or "mgmt" in k.lower() for k in interfaces)
        assert mgmt_found, f"No management interface in: {list(interfaces.keys())}"

    def test_get_interface_counters(self, lab_node):
        """net_get_interface_counters should return counter data."""
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        result = net_get_interface_counters(host=lab_node["name"])
        assert result["status"] == "success", f"Failed: {result.get('error')}"


class TestVlanTools:
    """Test VLAN tools against real devices."""

    def test_get_vlans(self, lab_node):
        """net_get_vlans should return at least VLAN 1."""
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans(host=lab_node["name"])
        assert result["status"] == "success", f"Failed: {result.get('error')}"


class TestRoutingTools:
    """Test routing tools against real devices."""

    def test_get_route_table(self, lab_node):
        """net_get_route_table should return routing table."""
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table(host=lab_node["name"])
        assert result["status"] == "success", f"Failed: {result.get('error')}"

    def test_get_arp_table(self, lab_node):
        """net_get_arp_table should return ARP entries."""
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table(host=lab_node["name"])
        assert result["status"] == "success", f"Failed: {result.get('error')}"


class TestSwitchingTools:
    """Test switching tools against real devices."""

    def test_get_lldp_neighbors(self, lab_node):
        """net_get_lldp_neighbors should return neighbor data."""
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        result = net_get_lldp_neighbors(host=lab_node["name"])
        assert result["status"] == "success", f"Failed: {result.get('error')}"

    def test_get_mac_table(self, lab_node):
        """net_get_mac_table should return MAC address table."""
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table(host=lab_node["name"])
        assert result["status"] == "success", f"Failed: {result.get('error')}"


class TestMonitoringTools:
    """Test monitoring tools against real devices."""

    def test_get_cpu_usage(self, lab_node):
        """net_get_cpu_usage should return CPU data."""
        from network_mcp.tools.common.monitoring import net_get_cpu_usage

        result = net_get_cpu_usage(host=lab_node["name"])
        assert result["status"] == "success", f"Failed: {result.get('error')}"


class TestConfigTools:
    """Test config read tools against real devices (read-only)."""

    def test_get_running_config(self, lab_node):
        """net_get_running_config should return config text."""
        from network_mcp.tools.common.config import net_get_running_config

        result = net_get_running_config(host=lab_node["name"])
        assert result["status"] == "success", f"Failed: {result.get('error')}"
        data = result.get("data", {})
        # data may be a dict with "config" key or a raw string
        if isinstance(data, dict):
            config_text = data.get("config", "")
        else:
            config_text = data
        assert isinstance(config_text, str)
        assert len(config_text) > 0
        assert "hostname" in config_text.lower()


class TestMultiDevice:
    """Test operations across multiple lab devices."""

    def test_all_devices_reachable(self, lab_nodes, live_conn_mgr):
        """Every discovered lab node should respond to show version."""
        for node in lab_nodes:
            driver = live_conn_mgr.get_driver(node["name"])
            result = driver.run_show(["show version"])
            assert len(result) == 1
            assert "version" in result[0], f"Node {node['name']} returned unexpected data"

    def test_lldp_neighbor_consistency(self, lab_nodes, live_conn_mgr):
        """LLDP neighbors should be consistent — if A sees B, B should see A."""
        if len(lab_nodes) < 2:
            pytest.skip("Need at least 2 nodes for LLDP consistency check")

        # Collect LLDP neighbors from all nodes
        all_neighbors: dict[str, list[str]] = {}
        for node in lab_nodes:
            driver = live_conn_mgr.get_driver(node["name"])
            result = driver.run_show(["show lldp neighbors"])
            neighbors = result[0].get("lldpNeighbors", [])
            peer_names = []
            for n in neighbors:
                peer = n.get("neighborDevice", "")
                if peer:
                    peer_names.append(peer)
            all_neighbors[node["name"]] = peer_names

        # Verify bidirectional visibility
        for node_name, peers in all_neighbors.items():
            for peer_name in peers:
                # Find the peer in our lab nodes
                matching = [n for n in lab_nodes if peer_name.startswith(n["name"]) or n["name"] in peer_name]
                if matching:
                    peer_key = matching[0]["name"]
                    assert node_name in " ".join(all_neighbors.get(peer_key, [])) or \
                        any(node_name in p for p in all_neighbors.get(peer_key, [])), \
                        f"LLDP asymmetry: {node_name} sees {peer_name} but not vice versa"
