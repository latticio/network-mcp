"""Tests for progressive discovery meta-tools and field filtering."""

import asyncio
from unittest.mock import patch

from network_mcp.helpers import filter_fields, paginate_dict
from network_mcp.server import mcp
from network_mcp.tools.meta import (
    TOOL_CATEGORIES,
    _get_tool_count_for_module,
    _get_tools_for_module,
    _loaded_categories,
    eos_list_tool_categories,
    eos_list_tools_in_category,
    eos_load_tool_category,
    get_category_names,
)
from tests.conftest import (
    MOCK_BGP_SUMMARY,
    MOCK_INTERFACES_STATUS,
    MOCK_VLANS,
)

# --- Helper Tests ---


class TestFilterFields:
    def test_filter_returns_subset(self):
        data = {"a": 1, "b": 2, "c": 3}
        result = filter_fields(data, ["a", "c"])
        assert result == {"a": 1, "c": 3}

    def test_filter_none_returns_all(self):
        data = {"a": 1, "b": 2}
        result = filter_fields(data, None)
        assert result == data

    def test_filter_empty_list_returns_empty(self):
        data = {"a": 1, "b": 2}
        result = filter_fields(data, [])
        assert result == data  # empty list is falsy, returns original

    def test_filter_nonexistent_fields(self):
        data = {"a": 1, "b": 2}
        result = filter_fields(data, ["x", "y"])
        assert result == {}


class TestPaginateDict:
    def test_paginate_with_limit(self):
        data = {"a": 1, "b": 2, "c": 3, "d": 4}
        result, meta = paginate_dict(data, limit=2, offset=0)
        assert len(result) == 2
        assert meta["total"] == 4
        assert meta["returned"] == 2
        assert meta["has_more"] is True

    def test_paginate_with_offset(self):
        data = {"a": 1, "b": 2, "c": 3, "d": 4}
        result, meta = paginate_dict(data, limit=2, offset=2)
        assert len(result) == 2
        assert meta["has_more"] is False

    def test_paginate_offset_past_end(self):
        data = {"a": 1, "b": 2}
        result, meta = paginate_dict(data, limit=10, offset=5)
        assert result == {}
        assert meta["has_more"] is False

    def test_paginate_no_limit(self):
        data = {"a": 1, "b": 2}
        result, meta = paginate_dict(data, limit=None, offset=0)
        assert len(result) == 2
        assert meta["has_more"] is False


# --- Meta-Tool Tests ---


class TestListToolCategories:
    def test_returns_all_categories(self):
        result = eos_list_tool_categories()
        assert result["status"] == "success"
        assert result["total_categories"] == len(TOOL_CATEGORIES)
        assert "device" in result["categories"]
        assert "routing" in result["categories"]
        assert "workflows" in result["categories"]

    def test_categories_have_description_and_count(self):
        result = eos_list_tool_categories()
        for cat_name, cat_info in result["categories"].items():
            assert "description" in cat_info, f"Category '{cat_name}' missing description"
            assert "tool_count" in cat_info, f"Category '{cat_name}' missing tool_count"
            assert "loaded" in cat_info, f"Category '{cat_name}' missing loaded status"

    def test_progressive_discovery_flag(self):
        result = eos_list_tool_categories()
        assert "progressive_discovery" in result
        # Default is False
        assert result["progressive_discovery"] is False


class TestListToolsInCategory:
    def test_valid_category_returns_tools(self):
        result = eos_list_tools_in_category("device")
        assert result["status"] == "success"
        assert result["category"] == "device"
        assert len(result["tools"]) > 0
        # Check tool structure
        tool = result["tools"][0]
        assert "name" in tool
        assert "description" in tool
        assert "parameters" in tool

    def test_routing_category_has_expected_tools(self):
        result = eos_list_tools_in_category("routing")
        assert result["status"] == "success"
        tool_names = [t["name"] for t in result["tools"]]
        assert "eos_get_bgp_summary" in tool_names
        assert "eos_get_route_table" in tool_names

    def test_invalid_category_returns_error(self):
        result = eos_list_tools_in_category("nonexistent")
        assert result["status"] == "error"
        assert "nonexistent" in result["error"]
        assert "Available categories" in result["error"]

    def test_tool_parameters_include_host(self):
        """Verify tools have host as their first parameter."""
        result = eos_list_tools_in_category("interfaces")
        for tool in result["tools"]:
            param_names = [p["name"] for p in tool["parameters"]]
            assert "host" in param_names, f"Tool {tool['name']} missing 'host' parameter"


class TestLoadToolCategory:
    def test_load_when_not_progressive(self):
        """When progressive discovery is off, loading is a no-op."""
        result = eos_load_tool_category("device")
        assert result["status"] == "success"
        assert "already loaded" in result["message"]
        assert isinstance(result["tools"], list)

    def test_load_invalid_category_returns_error(self):
        result = eos_load_tool_category("nonexistent")
        assert result["status"] == "error"
        assert "nonexistent" in result["error"]
        assert "Available categories" in result["error"]

    @patch("network_mcp.tools.meta._is_progressive_discovery", return_value=True)
    def test_load_category_progressive_mode(self, mock_pd):
        """When progressive mode is on, load registers tools."""
        _loaded_categories.discard("device")
        try:
            result = eos_load_tool_category("device")
            assert result["status"] == "success"
            assert "device" in _loaded_categories
            assert len(result["tools"]) > 0
        finally:
            _loaded_categories.discard("device")

    @patch("network_mcp.tools.meta._is_progressive_discovery", return_value=True)
    def test_load_already_loaded_noop(self, mock_pd):
        """Loading an already-loaded category is a no-op."""
        _loaded_categories.add("routing")
        try:
            result = eos_load_tool_category("routing")
            assert result["status"] == "success"
            assert "already loaded" in result["message"]
        finally:
            _loaded_categories.discard("routing")


class TestCategoryNames:
    def test_get_category_names(self):
        names = get_category_names()
        assert isinstance(names, list)
        assert "device" in names
        assert "routing" in names
        assert names == sorted(names)  # Should be sorted


class TestModuleIntrospection:
    def test_get_tools_for_known_module(self):
        tools = _get_tools_for_module("device")
        assert len(tools) > 0
        # Check structure
        for t in tools:
            assert t["name"].startswith("eos_")

    def test_get_tools_for_unknown_module(self):
        tools = _get_tools_for_module("does_not_exist")
        assert tools == []

    def test_get_tool_count_matches_tools_list(self):
        tools = _get_tools_for_module("device")
        count = _get_tool_count_for_module("device")
        assert count == len(tools)


# --- Meta-Tools Registered in MCP ---


class TestMetaToolsRegistered:
    def test_meta_tools_in_registry(self):
        """Verify meta-tools are registered with MCP."""
        tools = asyncio.run(mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert "eos_list_tool_categories" in tool_names
        assert "eos_list_tools_in_category" in tool_names
        assert "eos_load_tool_category" in tool_names


# --- Discovery Mode: All Tools Loaded When OFF ---


class TestDiscoveryOff:
    def test_all_tools_registered_when_discovery_off(self):
        """When progressive discovery is off (default), all tools should be registered."""
        tools = asyncio.run(mcp.list_tools())
        # Should have many tools (100+) when all are loaded
        assert len(tools) >= 60


# --- Field Filtering on High-Output Tools ---


class TestFieldFilteringBgpSummary:
    def test_fields_filter_peers(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_get_bgp_summary

        mock_node.run_commands.return_value = [MOCK_BGP_SUMMARY]
        result = eos_get_bgp_summary("spine-01", fields=["peerState"])

        assert result["status"] == "success"
        for peer_data in result["data"]["peers"].values():
            assert list(peer_data.keys()) == ["peerState"]

    def test_pagination_limit(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_get_bgp_summary

        mock_node.run_commands.return_value = [MOCK_BGP_SUMMARY]
        result = eos_get_bgp_summary("spine-01", limit=1)

        assert result["status"] == "success"
        assert len(result["data"]["peers"]) == 1
        assert result["pagination"]["total"] == 2
        assert result["pagination"]["has_more"] is True

    def test_no_pagination_without_limit(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_get_bgp_summary

        mock_node.run_commands.return_value = [MOCK_BGP_SUMMARY]
        result = eos_get_bgp_summary("spine-01")

        assert result["status"] == "success"
        assert "pagination" not in result


class TestFieldFilteringInterfacesStatus:
    def test_fields_filter(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_get_interfaces_status

        mock_node.run_commands.return_value = [MOCK_INTERFACES_STATUS]
        result = eos_get_interfaces_status("spine-01", fields=["linkStatus"])

        assert result["status"] == "success"
        for intf_data in result["data"].values():
            assert list(intf_data.keys()) == ["linkStatus"]

    def test_pagination(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_get_interfaces_status

        mock_node.run_commands.return_value = [MOCK_INTERFACES_STATUS]
        result = eos_get_interfaces_status("spine-01", limit=1)

        assert len(result["data"]) == 1
        assert result["pagination"]["total"] == 2


class TestFieldFilteringVlans:
    def test_fields_filter(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_get_vlans

        mock_node.run_commands.return_value = [MOCK_VLANS]
        result = eos_get_vlans("spine-01", fields=["name"])

        assert result["status"] == "success"
        for vlan_data in result["data"].values():
            assert list(vlan_data.keys()) == ["name"]

    def test_pagination(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_get_vlans

        mock_node.run_commands.return_value = [MOCK_VLANS]
        result = eos_get_vlans("spine-01", limit=1, offset=0)

        assert len(result["data"]) == 1
        assert result["pagination"]["total"] == 2
        assert result["pagination"]["has_more"] is True


class TestFieldFilteringMacTable:
    def test_fields_filter(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_get_mac_address_table

        mock_data = {
            "unicastTable": {
                "tableEntries": [
                    {"macAddress": "aa:bb:cc:dd:ee:01", "interface": "Ethernet1", "vlanId": 100, "type": "dynamic"},
                    {"macAddress": "aa:bb:cc:dd:ee:02", "interface": "Ethernet2", "vlanId": 200, "type": "static"},
                ]
            }
        }
        mock_node.run_commands.return_value = [mock_data]
        result = eos_get_mac_address_table("spine-01", fields=["macAddress", "interface"])

        assert result["status"] == "success"
        for entry in result["data"]["unicastTable"]["tableEntries"]:
            assert set(entry.keys()) == {"macAddress", "interface"}

    def test_pagination_mac_table(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_get_mac_address_table

        mock_data = {
            "unicastTable": {
                "tableEntries": [{"macAddress": f"aa:bb:cc:dd:ee:0{i}", "interface": f"Ethernet{i}"} for i in range(5)]
            }
        }
        mock_node.run_commands.return_value = [mock_data]
        result = eos_get_mac_address_table("spine-01", limit=2, offset=1)

        entries = result["data"]["unicastTable"]["tableEntries"]
        assert len(entries) == 2
        assert result["pagination"]["total"] == 5
        assert result["pagination"]["offset"] == 1
        assert result["pagination"]["has_more"] is True
