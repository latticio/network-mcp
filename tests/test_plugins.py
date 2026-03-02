"""Tests for plugin discovery system."""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.plugins import (
    PLUGIN_GROUPS,
    _reset_plugins,
    discover_plugins,
    get_plugin_summary,
    load_compliance_plugins,
    load_driver_plugins,
    load_tool_plugins,
)


@pytest.fixture(autouse=True)
def _reset_plugin_cache():
    """Reset plugin cache before each test."""
    _reset_plugins()
    yield
    _reset_plugins()


def _make_mock_entry_point(name: str, value: str, group: str):
    """Create a mock entry point."""
    ep = MagicMock()
    ep.name = name
    ep.value = value
    ep.group = group
    return ep


# --- PLUGIN_GROUPS ---


class TestPluginGroups:
    """Test that plugin groups are correctly defined."""

    def test_has_drivers_group(self):
        assert "network_mcp.drivers" in PLUGIN_GROUPS

    def test_has_compliance_group(self):
        assert "network_mcp.compliance" in PLUGIN_GROUPS

    def test_has_tools_group(self):
        assert "network_mcp.tools" in PLUGIN_GROUPS

    def test_all_groups_have_descriptions(self):
        for group, desc in PLUGIN_GROUPS.items():
            assert isinstance(desc, str), f"Group {group} has non-string description"
            assert len(desc) > 0, f"Group {group} has empty description"


# --- discover_plugins ---


class TestDiscoverPlugins:
    """Test plugin discovery from entry points."""

    def test_no_plugins_installed(self):
        """With no plugins installed, returns empty lists for all groups."""
        with patch("network_mcp.plugins.importlib.metadata.entry_points", return_value=[]):
            result = discover_plugins()

        assert isinstance(result, dict)
        for group in PLUGIN_GROUPS:
            assert group in result
            assert result[group] == []

    def test_discovers_driver_plugin(self):
        """Discovers a mock driver plugin entry point."""
        mock_ep = _make_mock_entry_point("nokia_sros", "network_mcp_driver_nokia:SROSDriver", "network_mcp.drivers")

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = discover_plugins()

        assert len(result["network_mcp.drivers"]) == 1
        plugin = result["network_mcp.drivers"][0]
        assert plugin["name"] == "nokia_sros"
        assert plugin["module"] == "network_mcp_driver_nokia:SROSDriver"
        assert plugin["loaded"] is False
        assert plugin["error"] is None

    def test_discovers_compliance_plugin(self):
        """Discovers a mock compliance plugin entry point."""
        mock_ep = _make_mock_entry_point("disa_stig", "stig_checks:CHECKS", "network_mcp.compliance")

        def mock_entry_points(group=None):
            if group == "network_mcp.compliance":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = discover_plugins()

        assert len(result["network_mcp.compliance"]) == 1
        assert result["network_mcp.compliance"][0]["name"] == "disa_stig"

    def test_discovers_tool_plugin(self):
        """Discovers a mock tool plugin entry point."""
        mock_ep = _make_mock_entry_point("custom_tools", "my_tools:setup", "network_mcp.tools")

        def mock_entry_points(group=None):
            if group == "network_mcp.tools":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = discover_plugins()

        assert len(result["network_mcp.tools"]) == 1
        assert result["network_mcp.tools"][0]["name"] == "custom_tools"

    def test_discovers_multiple_plugins_across_groups(self):
        """Discovers plugins in multiple groups."""
        driver_ep = _make_mock_entry_point("nokia_sros", "nokia:SROSDriver", "network_mcp.drivers")
        tool_ep = _make_mock_entry_point("custom", "tools:setup", "network_mcp.tools")

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [driver_ep]
            if group == "network_mcp.tools":
                return [tool_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = discover_plugins()

        assert len(result["network_mcp.drivers"]) == 1
        assert len(result["network_mcp.tools"]) == 1
        assert len(result["network_mcp.compliance"]) == 0

    def test_caches_results(self):
        """Results are cached after first call."""
        with patch("network_mcp.plugins.importlib.metadata.entry_points", return_value=[]) as mock_eps:
            result1 = discover_plugins()
            result2 = discover_plugins()

        assert result1 is result2
        # entry_points called 3 times (once per group) on first call, 0 on second
        assert mock_eps.call_count == 3

    def test_reset_clears_cache(self):
        """_reset_plugins() clears the cache."""
        with patch("network_mcp.plugins.importlib.metadata.entry_points", return_value=[]) as mock_eps:
            discover_plugins()
            _reset_plugins()
            discover_plugins()

        # 3 calls per discover_plugins invocation (one per group)
        assert mock_eps.call_count == 6


# --- load_driver_plugins ---


class TestLoadDriverPlugins:
    """Test driver plugin loading and registry merge."""

    def test_no_plugins_returns_registry_unchanged(self):
        """With no driver plugins, registry is returned as-is."""
        registry = {"eos": "EosDriver"}

        with patch("network_mcp.plugins.importlib.metadata.entry_points", return_value=[]):
            result = load_driver_plugins(registry)

        assert result == {"eos": "EosDriver"}

    def test_loads_driver_into_registry(self):
        """Successfully loads a driver plugin into the registry."""
        registry = {"eos": "EosDriver"}
        mock_driver_cls = type("MockDriver", (), {})

        mock_ep = _make_mock_entry_point("mock_platform", "mock_pkg:MockDriver", "network_mcp.drivers")
        mock_ep.load.return_value = mock_driver_cls

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = load_driver_plugins(registry)

        assert "mock_platform" in result
        assert result["mock_platform"] is mock_driver_cls
        assert result["eos"] == "EosDriver"  # built-in not overwritten

    def test_skips_conflicting_builtin_driver(self):
        """Plugin driver that conflicts with built-in is skipped."""
        registry = {"eos": "EosDriver"}

        mock_ep = _make_mock_entry_point("eos", "bad_pkg:FakeEos", "network_mcp.drivers")

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = load_driver_plugins(registry)

        assert result["eos"] == "EosDriver"  # unchanged

    def test_handles_load_error_gracefully(self):
        """Plugin that fails to load is skipped with error logged."""
        registry = {"eos": "EosDriver"}

        mock_ep = _make_mock_entry_point("broken", "broken_pkg:BrokenDriver", "network_mcp.drivers")
        mock_ep.load.side_effect = ImportError("missing dependency")

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = load_driver_plugins(registry)

        assert "broken" not in result
        # Check that the plugin info recorded the error
        plugins = discover_plugins()
        broken_info = plugins["network_mcp.drivers"][0]
        assert broken_info["error"] is not None
        assert "missing dependency" in broken_info["error"]


# --- load_compliance_plugins ---


class TestLoadCompliancePlugins:
    """Test compliance plugin loading and check list merge."""

    def test_no_plugins_returns_checks_unchanged(self):
        """With no compliance plugins, check list is returned as-is."""
        checks = [{"id": "CIS-1.1.1"}]

        with patch("network_mcp.plugins.importlib.metadata.entry_points", return_value=[]):
            result = load_compliance_plugins(checks)

        assert result == [{"id": "CIS-1.1.1"}]

    def test_merges_plugin_checks(self):
        """Successfully merges plugin compliance checks into list."""
        checks = [{"id": "CIS-1.1.1"}]
        plugin_checks = [{"id": "STIG-001"}, {"id": "STIG-002"}]

        mock_ep = _make_mock_entry_point("disa_stig", "stig:CHECKS", "network_mcp.compliance")
        mock_ep.load.return_value = plugin_checks

        def mock_entry_points(group=None):
            if group == "network_mcp.compliance":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = load_compliance_plugins(checks)

        assert len(result) == 3
        assert result[0]["id"] == "CIS-1.1.1"
        assert result[1]["id"] == "STIG-001"
        assert result[2]["id"] == "STIG-002"

    def test_handles_non_list_return(self):
        """Plugin returning non-list is skipped."""
        checks = []

        mock_ep = _make_mock_entry_point("bad_plugin", "bad:CHECKS", "network_mcp.compliance")
        mock_ep.load.return_value = "not a list"

        def mock_entry_points(group=None):
            if group == "network_mcp.compliance":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = load_compliance_plugins(checks)

        assert result == []

    def test_handles_load_error_gracefully(self):
        """Plugin that fails to load is skipped."""
        checks = [{"id": "CIS-1.1.1"}]

        mock_ep = _make_mock_entry_point("broken", "broken:CHECKS", "network_mcp.compliance")
        mock_ep.load.side_effect = ImportError("broken")

        def mock_entry_points(group=None):
            if group == "network_mcp.compliance":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = load_compliance_plugins(checks)

        assert result == [{"id": "CIS-1.1.1"}]  # unchanged


# --- load_tool_plugins ---


class TestLoadToolPlugins:
    """Test tool plugin loading."""

    def test_no_plugins_returns_empty_list(self):
        """With no tool plugins, returns empty list."""
        mcp_instance = MagicMock()

        with patch("network_mcp.plugins.importlib.metadata.entry_points", return_value=[]):
            result = load_tool_plugins(mcp_instance)

        assert result == []

    def test_loads_callable_plugin(self):
        """Loads a tool plugin that is a callable (setup function)."""
        mcp_instance = MagicMock()
        setup_fn = MagicMock()

        mock_ep = _make_mock_entry_point("custom_tools", "my_tools:setup", "network_mcp.tools")
        mock_ep.load.return_value = setup_fn

        def mock_entry_points(group=None):
            if group == "network_mcp.tools":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = load_tool_plugins(mcp_instance)

        assert result == ["custom_tools"]
        setup_fn.assert_called_once_with(mcp_instance)

    def test_loads_module_plugin(self):
        """Loads a tool plugin that is a module (class, not callable in the setup-function sense)."""
        mcp_instance = MagicMock()
        # A class is callable but is a type — should NOT be called with mcp_instance
        module_cls = type("ToolModule", (), {})

        mock_ep = _make_mock_entry_point("mod_plugin", "my_mod:ToolModule", "network_mcp.tools")
        mock_ep.load.return_value = module_cls

        def mock_entry_points(group=None):
            if group == "network_mcp.tools":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = load_tool_plugins(mcp_instance)

        assert result == ["mod_plugin"]

    def test_handles_load_error_gracefully(self):
        """Plugin that fails to load is skipped."""
        mcp_instance = MagicMock()

        mock_ep = _make_mock_entry_point("broken", "broken:setup", "network_mcp.tools")
        mock_ep.load.side_effect = ImportError("broken dependency")

        def mock_entry_points(group=None):
            if group == "network_mcp.tools":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = load_tool_plugins(mcp_instance)

        assert result == []


# --- get_plugin_summary ---


class TestGetPluginSummary:
    """Test the plugin summary used by net_list_plugins."""

    def test_empty_summary_structure(self):
        """Summary with no plugins has correct structure."""
        with patch("network_mcp.plugins.importlib.metadata.entry_points", return_value=[]):
            result = get_plugin_summary()

        assert result["status"] == "success"
        assert "data" in result
        data = result["data"]
        assert data["total_plugins"] == 0
        assert data["network_mcp.drivers"] == []
        assert data["network_mcp.compliance"] == []
        assert data["network_mcp.tools"] == []

    def test_summary_with_plugins(self):
        """Summary correctly reports discovered plugins."""
        driver_ep = _make_mock_entry_point("nokia_sros", "nokia:SROSDriver", "network_mcp.drivers")

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [driver_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = get_plugin_summary()

        assert result["status"] == "success"
        data = result["data"]
        assert data["total_plugins"] == 1
        assert len(data["network_mcp.drivers"]) == 1
        assert data["network_mcp.drivers"][0]["name"] == "nokia_sros"

    def test_summary_filters_none_error(self):
        """Plugin with error=None should have 'error' key removed from summary."""
        driver_ep = _make_mock_entry_point("foo", "foo:Driver", "network_mcp.drivers")

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [driver_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = get_plugin_summary()

        plugin = result["data"]["network_mcp.drivers"][0]
        assert "error" not in plugin
        assert plugin["name"] == "foo"

    def test_summary_keeps_non_none_error(self):
        """Plugin with a real error string should keep the 'error' key in summary."""
        driver_ep = _make_mock_entry_point("bar", "bar:Driver", "network_mcp.drivers")
        driver_ep.load.side_effect = ImportError("missing dep")

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [driver_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            from network_mcp.plugins import load_driver_plugins

            load_driver_plugins({})
            result = get_plugin_summary()

        plugin = result["data"]["network_mcp.drivers"][0]
        assert "error" in plugin
        assert "missing dep" in plugin["error"]

    def test_summary_keeps_none_non_error_keys(self):
        """Non-'error' keys with None values should be preserved in summary."""
        driver_ep = _make_mock_entry_point("baz", "baz:Driver", "network_mcp.drivers")

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [driver_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            plugins = discover_plugins()
            # Manually set a non-error key to None to test it's preserved
            plugins["network_mcp.drivers"][0]["version"] = None
            result = get_plugin_summary()

        plugin = result["data"]["network_mcp.drivers"][0]
        assert "version" in plugin
        assert plugin["version"] is None
        # error=None should still be filtered
        assert "error" not in plugin


# --- net_list_plugins tool ---


class TestNetListPluginsTool:
    """Test the net_list_plugins MCP tool."""

    def test_returns_success_with_no_plugins(self):
        """Tool returns success with empty plugin lists."""
        from network_mcp.tools.meta import net_list_plugins

        with patch("network_mcp.plugins.importlib.metadata.entry_points", return_value=[]):
            result = net_list_plugins()

        assert result["status"] == "success"
        assert result["data"]["total_plugins"] == 0

    def test_returns_discovered_plugins(self):
        """Tool returns discovered plugins."""
        from network_mcp.tools.meta import net_list_plugins

        driver_ep = _make_mock_entry_point("test_driver", "test:Driver", "network_mcp.drivers")

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [driver_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = net_list_plugins()

        assert result["status"] == "success"
        assert result["data"]["total_plugins"] == 1
        assert result["data"]["network_mcp.drivers"][0]["name"] == "test_driver"


# --- Edge cases ---


class TestPluginEdgeCases:
    """Test edge cases and error handling."""

    def test_entry_points_type_error_fallback(self):
        """Handles older Python entry_points() that returns dict instead of supporting group kwarg."""

        def mock_entry_points(group=None):
            # Simulate older API that doesn't accept group kwarg
            if group is not None:
                raise TypeError("entry_points() got an unexpected keyword argument 'group'")
            return {"network_mcp.drivers": [], "network_mcp.compliance": [], "network_mcp.tools": []}

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = discover_plugins()

        for group in PLUGIN_GROUPS:
            assert group in result
            assert result[group] == []

    def test_plugin_with_runtime_error_during_load(self):
        """Plugin that raises RuntimeError during load is handled gracefully."""
        registry = {}

        mock_ep = _make_mock_entry_point("bad_driver", "bad:Driver", "network_mcp.drivers")
        mock_ep.load.side_effect = RuntimeError("initialization failed")

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = load_driver_plugins(registry)

        assert "bad_driver" not in result

    def test_tool_plugin_setup_function_error(self):
        """Tool plugin setup function that raises is handled gracefully."""
        mcp_instance = MagicMock()

        def broken_setup(mcp):
            raise ValueError("setup failed")

        mock_ep = _make_mock_entry_point("broken_setup", "pkg:setup", "network_mcp.tools")
        mock_ep.load.return_value = broken_setup

        def mock_entry_points(group=None):
            if group == "network_mcp.tools":
                return [mock_ep]
            return []

        with patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points):
            result = load_tool_plugins(mcp_instance)

        assert result == []
