"""Tests for V3 resource templates (additive to existing resources)."""

import json

from network_mcp.connection import DeviceCredentials
from network_mcp.resources.inventory import (
    device_bgp_summary,
    device_vlans,
    interface_detail,
    list_devices,
    running_config_section,
)
from network_mcp.server import conn_mgr
from tests.conftest import (
    MOCK_BGP_SUMMARY,
    MOCK_INTERFACE_DETAIL,
    MOCK_VLANS,
)


class TestListDevicesResource:
    def test_returns_device_list_with_metadata(self):
        """Test net://devices returns richer device info."""
        # Inject a test device into inventory
        original_inv = dict(conn_mgr._inventory)
        conn_mgr._inventory["test-spine"] = DeviceCredentials(
            host="10.0.0.1",
            username="admin",
            password="secret",
            transport="https",
            role="spine",
            groups=["dc1"],
            tags=["production"],
            mlag_peer="test-spine-2",
        )
        try:
            result = json.loads(list_devices())
            assert isinstance(result, list)
            assert len(result) >= 1
            device = next(d for d in result if d["name"] == "test-spine")
            assert device["host"] == "10.0.0.1"
            assert device["role"] == "spine"
            assert device["groups"] == ["dc1"]
            assert device["tags"] == ["production"]
            assert device["mlag_peer"] == "test-spine-2"
        finally:
            conn_mgr._inventory = original_inv

    def test_empty_inventory(self):
        original_inv = dict(conn_mgr._inventory)
        conn_mgr._inventory = {}
        try:
            result = json.loads(list_devices())
            assert result == []
        finally:
            conn_mgr._inventory = original_inv


class TestRunningConfigSectionResource:
    def test_returns_config_section(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{"output": "router bgp 65000\n   router-id 10.0.0.1\n"}]
        result = running_config_section("spine-01", "router bgp")
        assert "router bgp 65000" in result
        assert "router-id 10.0.0.1" in result

    def test_error_on_device_failure(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = Exception("Connection refused")
        result = json.loads(running_config_section("spine-01", "router bgp"))
        assert "error" in result

    def test_invalid_section_rejected(self, mock_conn_mgr, mock_node):
        """Ensure CLI injection in section parameter is caught."""
        result = json.loads(running_config_section("spine-01", "bgp; reload"))
        assert "error" in result


class TestInterfaceDetailResource:
    def test_returns_interface_data(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_INTERFACE_DETAIL]
        result = json.loads(interface_detail("spine-01", "Ethernet1"))
        assert "name" in result
        assert result["name"] == "Ethernet1"
        assert "mtu" in result

    def test_error_on_invalid_interface(self, mock_conn_mgr, mock_node):
        result = json.loads(interface_detail("spine-01", "bad-intf"))
        assert "error" in result

    def test_error_on_device_failure(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = Exception("Timeout")
        result = json.loads(interface_detail("spine-01", "Ethernet1"))
        assert "error" in result


class TestBgpSummaryResource:
    def test_returns_bgp_data(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_BGP_SUMMARY]
        result = json.loads(device_bgp_summary("spine-01"))
        assert "router_id" in result
        assert "peers" in result
        assert "10.0.0.2" in result["peers"]

    def test_error_on_device_failure(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = Exception("Connection refused")
        result = json.loads(device_bgp_summary("spine-01"))
        assert "error" in result


class TestVlansResource:
    def test_returns_vlan_data(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_VLANS]
        result = json.loads(device_vlans("spine-01"))
        assert "1" in result
        assert "100" in result
        assert result["100"]["name"] == "SERVERS"

    def test_error_on_device_failure(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = Exception("Timeout")
        result = json.loads(device_vlans("spine-01"))
        assert "error" in result


class TestResourceTemplatesRegistered:
    def test_new_templates_registered(self):
        """Verify all new resource templates are registered with MCP."""
        import asyncio

        from network_mcp.server import mcp

        resources = asyncio.run(mcp.list_resources())
        templates = asyncio.run(mcp.list_resource_templates())

        # Static resources
        resource_uris = {str(r.uri) for r in resources}
        assert "net://devices" in resource_uris or "net://inventory" in resource_uris

        # Template URIs
        template_uris = {str(t.uriTemplate) for t in templates}
        assert "net://{host}/running-config/{section}" in template_uris
        assert "net://{host}/interfaces/{interface}" in template_uris
        assert "net://{host}/bgp/summary" in template_uris
        assert "net://{host}/vlans" in template_uris
