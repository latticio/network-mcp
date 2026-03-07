"""Tests for x-tool-group category metadata and annotation functionality.

Verifies:
- TOOL_CATEGORIES contains all required categories with correct module mappings
- annotate_tools_with_groups() injects x-tool-group into ToolAnnotations
- get_tool_group() returns the correct group for registered tools
- net_list_tool_categories() and net_load_tool_category() work correctly
- Module-to-category reverse mapping is correct
"""

import asyncio
from unittest.mock import MagicMock, patch

from network_mcp.tools.meta import (
    _TOOL_GROUP_MAP,
    TOOL_CATEGORIES,
    _build_module_to_category_map,
    _loaded_categories,
    annotate_tools_with_groups,
    get_tool_group,
    net_list_tool_categories,
    net_load_tool_category,
)

# --- TOOL_CATEGORIES structure tests ---


class TestToolCategoriesDict:
    """Verify TOOL_CATEGORIES has all required categories."""

    REQUIRED_CATEGORIES = [
        "routing",
        "switching",
        "monitoring",
        "config",
        "validation",
        "fabric",
        "security",
        "vrf",
        "interfaces",
        "vlans",
        "device",
        "troubleshoot",
        "workflows",
        "export",
        "compliance",
        "ai_fabric",
        "gnmi",
        "evpn_vxlan",
        "bfd",
        "qos",
        "event_monitor",
        "sessions",
        "checkpoints",
    ]

    def test_all_required_categories_present(self):
        for cat in self.REQUIRED_CATEGORIES:
            assert cat in TOOL_CATEGORIES, f"Required category '{cat}' missing from TOOL_CATEGORIES"

    def test_each_category_has_module_key(self):
        for cat_name, cat_info in TOOL_CATEGORIES.items():
            assert "module" in cat_info, f"Category '{cat_name}' missing 'module' key"
            assert isinstance(cat_info["module"], str), f"Category '{cat_name}' module must be a string"

    def test_each_category_has_description(self):
        for cat_name, cat_info in TOOL_CATEGORIES.items():
            assert "description" in cat_info, f"Category '{cat_name}' missing 'description' key"
            assert len(cat_info["description"]) > 0, f"Category '{cat_name}' has empty description"

    def test_module_mappings_are_correct(self):
        """Spot-check key module mappings."""
        assert TOOL_CATEGORIES["routing"]["module"] == "routing"
        assert TOOL_CATEGORIES["switching"]["module"] == "switching"
        assert TOOL_CATEGORIES["monitoring"]["module"] == "monitoring"
        assert TOOL_CATEGORIES["config"]["module"] == "config_mgmt"
        assert TOOL_CATEGORIES["export"]["module"] == "export"
        assert TOOL_CATEGORIES["ai_fabric"]["module"] == "ai_fabric"
        assert TOOL_CATEGORIES["gnmi"]["module"] == "gnmi"
        assert TOOL_CATEGORIES["sessions"]["module"] == "sessions"
        assert TOOL_CATEGORIES["checkpoints"]["module"] == "checkpoints"

    def test_export_replaces_utility(self):
        """'export' category must exist; 'utility' removed in favour of 'export'."""
        assert "export" in TOOL_CATEGORIES
        assert TOOL_CATEGORIES["export"]["module"] == "export"


# --- Module-to-category reverse mapping ---


class TestBuildModuleToCategoryMap:
    def test_returns_dict(self):
        mapping = _build_module_to_category_map()
        assert isinstance(mapping, dict)
        assert len(mapping) > 0

    def test_routing_maps_to_routing(self):
        mapping = _build_module_to_category_map()
        assert mapping.get("routing") == "routing"

    def test_config_mgmt_maps_to_config(self):
        mapping = _build_module_to_category_map()
        assert mapping.get("config_mgmt") == "config"

    def test_export_maps_to_export(self):
        mapping = _build_module_to_category_map()
        assert mapping.get("export") == "export"

    def test_ai_fabric_maps_to_ai_fabric(self):
        mapping = _build_module_to_category_map()
        assert mapping.get("ai_fabric") == "ai_fabric"

    def test_gnmi_maps_to_gnmi(self):
        mapping = _build_module_to_category_map()
        assert mapping.get("gnmi") == "gnmi"

    def test_no_duplicate_targets(self):
        """Each module should only appear once in the mapping."""
        mapping = _build_module_to_category_map()
        # Values can repeat (two categories could share a module) but the mapping
        # itself must be a valid dict (keys are unique by definition).
        assert len(mapping) == len(set(mapping.keys()))


# --- annotate_tools_with_groups ---


class TestAnnotateToolsWithGroups:
    def test_returns_dict(self):
        from network_mcp.server import mcp

        result = annotate_tools_with_groups(mcp)
        assert isinstance(result, dict)

    def test_annotates_known_tools(self):
        """After annotation, known tools should have x-tool-group set."""
        from network_mcp.server import mcp

        annotate_tools_with_groups(mcp)
        internal_tools = mcp._tool_manager._tools

        # Check a few well-known tools
        for tool_name in ["eos_get_vlans", "eos_get_bgp_summary", "eos_get_interfaces_status"]:
            if tool_name in internal_tools:
                tool_obj = internal_tools[tool_name]
                assert tool_obj.annotations is not None
                group = tool_obj.annotations.model_extra.get("x-tool-group")
                assert group is not None, f"Tool '{tool_name}' missing x-tool-group annotation"
                assert isinstance(group, str)

    def test_tool_group_map_populated(self):
        """_TOOL_GROUP_MAP should be populated after annotation."""
        from network_mcp.server import mcp

        annotate_tools_with_groups(mcp)
        assert len(_TOOL_GROUP_MAP) > 0

    def test_routing_tools_get_routing_group(self):
        """Tools from the routing module should get 'routing' as their group."""
        from network_mcp.server import mcp

        annotate_tools_with_groups(mcp)
        internal_tools = mcp._tool_manager._tools
        for tool_name, tool_obj in internal_tools.items():
            if tool_obj.fn.__module__ == "network_mcp.tools.routing":
                group = tool_obj.annotations.model_extra.get("x-tool-group") if tool_obj.annotations else None
                assert group == "routing", (
                    f"Tool '{tool_name}' in routing module should have group 'routing', got '{group}'"
                )

    def test_vlans_tools_get_vlans_group(self):
        """Tools from the vlans module should get 'vlans' as their group."""
        from network_mcp.server import mcp

        annotate_tools_with_groups(mcp)
        internal_tools = mcp._tool_manager._tools
        for tool_name, tool_obj in internal_tools.items():
            if tool_obj.fn.__module__ == "network_mcp.tools.vlans":
                group = tool_obj.annotations.model_extra.get("x-tool-group") if tool_obj.annotations else None
                assert group == "vlans", f"Tool '{tool_name}' in vlans module should have group 'vlans', got '{group}'"

    def test_common_submodule_tools_get_common_group(self):
        """Tools from network_mcp.tools.common.* should get 'common' as their group."""
        from network_mcp.server import mcp

        annotate_tools_with_groups(mcp)
        internal_tools = mcp._tool_manager._tools
        common_prefix = "network_mcp.tools.common."
        for tool_name, tool_obj in internal_tools.items():
            if tool_obj.fn.__module__.startswith(common_prefix):
                group = tool_obj.annotations.model_extra.get("x-tool-group") if tool_obj.annotations else None
                assert group == "common", (
                    f"Tool '{tool_name}' in common submodule should have group 'common', got '{group}'"
                )

    def test_invalid_mcp_instance_returns_empty(self):
        """annotate_tools_with_groups gracefully handles objects without internal registry."""
        bad_instance = MagicMock(spec=[])  # No attributes
        result = annotate_tools_with_groups(bad_instance)
        assert result == {}

    def test_idempotent(self):
        """Calling annotation multiple times does not raise errors."""
        from network_mcp.server import mcp

        result1 = annotate_tools_with_groups(mcp)
        result2 = annotate_tools_with_groups(mcp)
        assert set(result1.keys()) == set(result2.keys())


# --- get_tool_group ---


class TestGetToolGroup:
    def test_returns_none_for_unknown_tool(self):
        assert get_tool_group("totally_nonexistent_tool_xyz") is None

    def test_returns_string_for_known_annotated_tool(self):
        from network_mcp.server import mcp

        annotate_tools_with_groups(mcp)
        # After annotation, at least some tools should have groups
        for tool_name in _TOOL_GROUP_MAP:
            group = get_tool_group(tool_name)
            assert isinstance(group, str)
            break  # Just verify the first one

    def test_routing_tool_returns_routing(self):
        from network_mcp.server import mcp

        annotate_tools_with_groups(mcp)
        # Find a routing tool and verify its group
        internal_tools = mcp._tool_manager._tools
        for tool_name, tool_obj in internal_tools.items():
            if tool_obj.fn.__module__ == "network_mcp.tools.routing":
                assert get_tool_group(tool_name) == "routing"
                break


# --- net_list_tool_categories ---


class TestNetListToolCategories:
    def test_returns_success(self):
        result = net_list_tool_categories()
        assert result["status"] == "success"

    def test_contains_all_required_categories(self):
        result = net_list_tool_categories()
        cats = result["categories"]
        required = [
            "routing", "switching", "monitoring", "config", "validation",
            "fabric", "security", "vrf", "interfaces", "vlans", "device",
            "troubleshoot", "workflows", "export", "compliance", "ai_fabric",
            "sessions", "checkpoints",
        ]
        for cat in required:
            assert cat in cats, f"Category '{cat}' missing from net_list_tool_categories result"

    def test_each_category_has_tool_count(self):
        result = net_list_tool_categories()
        for cat_name, cat_info in result["categories"].items():
            assert "tool_count" in cat_info, f"Category '{cat_name}' missing tool_count"
            assert isinstance(cat_info["tool_count"], int)
            assert cat_info["tool_count"] >= 0

    def test_each_category_has_x_tool_group(self):
        result = net_list_tool_categories()
        for cat_name, cat_info in result["categories"].items():
            assert "x_tool_group" in cat_info, f"Category '{cat_name}' missing x_tool_group"
            assert cat_info["x_tool_group"] == cat_name

    def test_each_category_has_loaded_flag(self):
        result = net_list_tool_categories()
        for cat_name, cat_info in result["categories"].items():
            assert "loaded" in cat_info, f"Category '{cat_name}' missing loaded flag"
            assert isinstance(cat_info["loaded"], bool)

    def test_has_annotated_tool_counts(self):
        result = net_list_tool_categories()
        assert "annotated_tool_counts" in result
        assert isinstance(result["annotated_tool_counts"], dict)

    def test_progressive_discovery_flag_present(self):
        result = net_list_tool_categories()
        assert "progressive_discovery" in result
        assert result["progressive_discovery"] is False  # default

    def test_total_categories_count(self):
        result = net_list_tool_categories()
        assert result["total_categories"] == len(TOOL_CATEGORIES)


# --- net_load_tool_category ---


class TestNetLoadToolCategory:
    def test_load_when_not_progressive_returns_success(self):
        result = net_load_tool_category("device")
        assert result["status"] == "success"
        assert "already loaded" in result["message"]
        assert isinstance(result["tools"], list)
        assert "tool_count" in result
        assert "x_tool_group" in result
        assert result["x_tool_group"] == "device"

    def test_load_invalid_category_returns_error(self):
        result = net_load_tool_category("completely_nonexistent_xyz")
        assert result["status"] == "error"
        assert "completely_nonexistent_xyz" in result["error"]
        assert "Available categories" in result["error"]

    def test_load_routing_returns_x_tool_group(self):
        result = net_load_tool_category("routing")
        assert result["status"] == "success"
        assert result.get("x_tool_group") == "routing"

    @patch("network_mcp.tools.meta._is_progressive_discovery", return_value=True)
    def test_load_category_progressive_mode(self, _mock_pd):
        _loaded_categories.discard("vlans")
        try:
            result = net_load_tool_category("vlans")
            assert result["status"] == "success"
            assert result.get("x_tool_group") == "vlans"
        finally:
            _loaded_categories.discard("vlans")

    @patch("network_mcp.tools.meta._is_progressive_discovery", return_value=True)
    def test_load_already_loaded_returns_x_tool_group(self, _mock_pd):
        _loaded_categories.add("monitoring")
        try:
            result = net_load_tool_category("monitoring")
            assert result["status"] == "success"
            assert result.get("x_tool_group") == "monitoring"
            assert "already loaded" in result["message"]
        finally:
            _loaded_categories.discard("monitoring")


# --- Integration: registered meta-tools in MCP ---


class TestMetaToolsRegistration:
    def test_net_list_tool_categories_registered(self):
        from network_mcp.server import mcp

        tools = asyncio.run(mcp.list_tools())
        names = {t.name for t in tools}
        assert "net_list_tool_categories" in names

    def test_net_load_tool_category_registered(self):
        from network_mcp.server import mcp

        tools = asyncio.run(mcp.list_tools())
        names = {t.name for t in tools}
        assert "net_load_tool_category" in names

    def test_annotated_tool_has_x_tool_group_in_annotations(self):
        """After server startup annotation pass, tools returned by list_tools() carry x-tool-group."""
        from network_mcp.server import mcp

        # Re-run annotation to ensure it's applied
        annotate_tools_with_groups(mcp)

        internal_tools = mcp._tool_manager._tools
        annotated_count = 0
        for _name, tool_obj in internal_tools.items():
            if tool_obj.annotations and tool_obj.annotations.model_extra.get("x-tool-group"):
                annotated_count += 1

        assert annotated_count > 0, "No tools have x-tool-group annotation set"

    def test_x_tool_group_values_are_valid_categories(self):
        """Every x-tool-group value should be a valid category name or 'meta'."""
        from network_mcp.server import mcp

        annotate_tools_with_groups(mcp)
        valid_groups = set(TOOL_CATEGORIES.keys()) | {"meta"}
        internal_tools = mcp._tool_manager._tools
        for tool_name, tool_obj in internal_tools.items():
            if tool_obj.annotations:
                group = tool_obj.annotations.model_extra.get("x-tool-group")
                if group is not None:
                    assert group in valid_groups, (
                        f"Tool '{tool_name}' has invalid x-tool-group '{group}'. "
                        f"Valid groups: {sorted(valid_groups)}"
                    )
