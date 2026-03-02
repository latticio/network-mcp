"""Tests for argument completions."""

import asyncio
from unittest.mock import MagicMock, patch

from network_mcp.completions import (
    _complete_category,
    _complete_host,
    _complete_interface,
    _complete_neighbor_ip,
    _complete_vlan_id,
    clear_completion_cache,
    handle_completion,
)
from network_mcp.connection import DeviceCredentials
from network_mcp.server import conn_mgr
from tests.conftest import MOCK_BGP_SUMMARY, MOCK_INTERFACES_STATUS, MOCK_VLANS


class TestCompleteHost:
    def test_returns_inventory_devices(self):
        original_inv = dict(conn_mgr._inventory)
        conn_mgr._inventory["spine-01"] = DeviceCredentials(host="10.0.0.1")
        conn_mgr._inventory["spine-02"] = DeviceCredentials(host="10.0.0.2")
        conn_mgr._inventory["leaf-01"] = DeviceCredentials(host="10.0.0.3")
        try:
            result = _complete_host("")
            assert "spine-01" in result
            assert "spine-02" in result
            assert "leaf-01" in result
        finally:
            conn_mgr._inventory = original_inv

    def test_filters_by_prefix(self):
        original_inv = dict(conn_mgr._inventory)
        conn_mgr._inventory["spine-01"] = DeviceCredentials(host="10.0.0.1")
        conn_mgr._inventory["leaf-01"] = DeviceCredentials(host="10.0.0.2")
        try:
            result = _complete_host("sp")
            assert "spine-01" in result
            assert "leaf-01" not in result
        finally:
            conn_mgr._inventory = original_inv

    def test_empty_inventory(self):
        original_inv = dict(conn_mgr._inventory)
        conn_mgr._inventory = {}
        try:
            result = _complete_host("")
            assert result == []
        finally:
            conn_mgr._inventory = original_inv


class TestCompleteInterface:
    def setup_method(self):
        clear_completion_cache()

    @patch("network_mcp.completions.run_show_command")
    def test_returns_interface_names(self, mock_show, mock_conn_mgr):
        mock_show.return_value = {"status": "success", "data": [MOCK_INTERFACES_STATUS]}
        result = _complete_interface("spine-01", "")
        assert "Ethernet1" in result
        assert "Ethernet2" in result

    @patch("network_mcp.completions.run_show_command")
    def test_filters_by_prefix(self, mock_show, mock_conn_mgr):
        mock_show.return_value = {"status": "success", "data": [MOCK_INTERFACES_STATUS]}
        result = _complete_interface("spine-01", "Ethernet1")
        assert "Ethernet1" in result
        assert "Ethernet2" not in result

    @patch("network_mcp.completions.run_show_command")
    def test_uses_cache(self, mock_show, mock_conn_mgr):
        mock_show.return_value = {"status": "success", "data": [MOCK_INTERFACES_STATUS]}
        _complete_interface("spine-01", "")
        _complete_interface("spine-01", "E")
        # Should only call run_show_command once due to caching
        assert mock_show.call_count == 1


class TestCompleteVlanId:
    def setup_method(self):
        clear_completion_cache()

    @patch("network_mcp.completions.run_show_command")
    def test_returns_vlan_ids(self, mock_show, mock_conn_mgr):
        mock_show.return_value = {"status": "success", "data": [MOCK_VLANS]}
        result = _complete_vlan_id("spine-01", "")
        assert "1" in result
        assert "100" in result

    @patch("network_mcp.completions.run_show_command")
    def test_filters_by_prefix(self, mock_show, mock_conn_mgr):
        mock_show.return_value = {"status": "success", "data": [MOCK_VLANS]}
        result = _complete_vlan_id("spine-01", "1")
        assert "1" in result
        assert "100" in result


class TestCompleteNeighborIp:
    def setup_method(self):
        clear_completion_cache()

    @patch("network_mcp.completions.run_show_command")
    def test_returns_peer_ips(self, mock_show, mock_conn_mgr):
        mock_show.return_value = {"status": "success", "data": [MOCK_BGP_SUMMARY]}
        result = _complete_neighbor_ip("spine-01", "")
        assert "10.0.0.2" in result
        assert "10.0.0.3" in result


class TestCompleteCategory:
    def test_returns_all_categories(self):
        result = _complete_category("")
        assert "device" in result
        assert "routing" in result
        assert "interfaces" in result

    def test_filters_by_prefix(self):
        result = _complete_category("ro")
        assert "routing" in result
        assert "device" not in result


class TestCompletionHandler:
    def setup_method(self):
        clear_completion_cache()

    def test_host_completion_via_handler(self):
        """Test the MCP completion handler for host argument."""
        original_inv = dict(conn_mgr._inventory)
        conn_mgr._inventory["spine-01"] = DeviceCredentials(host="10.0.0.1")
        try:
            from mcp.types import ResourceTemplateReference

            ref = ResourceTemplateReference(type="ref/resource", uri="net://{host}/version")
            argument = MagicMock()
            argument.name = "host"
            argument.value = "sp"
            context = None

            result = asyncio.run(handle_completion(ref, argument, context))
            assert result is not None
            assert "spine-01" in result.values
        finally:
            conn_mgr._inventory = original_inv

    def test_category_completion_via_handler(self):
        """Test the MCP completion handler for category argument."""
        from mcp.types import PromptReference

        ref = PromptReference(type="ref/prompt", name="test")
        argument = MagicMock()
        argument.name = "category"
        argument.value = "ro"
        context = None

        result = asyncio.run(handle_completion(ref, argument, context))
        assert result is not None
        assert "routing" in result.values

    @patch("network_mcp.completions.run_show_command")
    def test_interface_completion_with_context(self, mock_show, mock_conn_mgr):
        """Test interface completion when host is in context."""
        mock_show.return_value = {"status": "success", "data": [MOCK_INTERFACES_STATUS]}

        from mcp.types import ResourceTemplateReference

        ref = ResourceTemplateReference(type="ref/resource", uri="net://{host}/interfaces/{interface}")
        argument = MagicMock()
        argument.name = "interface"
        argument.value = "E"
        context = MagicMock()
        context.arguments = {"host": "spine-01"}

        result = asyncio.run(handle_completion(ref, argument, context))
        assert result is not None
        assert any("Ethernet" in v for v in result.values)

    def test_unknown_argument_returns_none(self):
        """Test that unknown argument names return None."""
        from mcp.types import PromptReference

        ref = PromptReference(type="ref/prompt", name="test")
        argument = MagicMock()
        argument.name = "unknown_arg"
        argument.value = ""
        context = None

        result = asyncio.run(handle_completion(ref, argument, context))
        assert result is None

    def test_section_completion_for_resource_template(self):
        """Test section completion for running-config resource template."""
        from mcp.types import ResourceTemplateReference

        ref = ResourceTemplateReference(type="ref/resource", uri="net://{host}/running-config/{section}")
        argument = MagicMock()
        argument.name = "section"
        argument.value = "ro"
        context = None

        result = asyncio.run(handle_completion(ref, argument, context))
        assert result is not None
        assert any("route" in v for v in result.values)


class TestCompletionRegistered:
    def test_completion_handler_registered(self):
        """Verify the completion handler is registered with MCP."""
        from mcp.types import CompleteRequest

        from network_mcp.server import mcp

        # The lowlevel server should have a CompleteRequest handler registered
        assert CompleteRequest in mcp._mcp_server.request_handlers
