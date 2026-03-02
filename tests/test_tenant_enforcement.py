"""Tests for tenant device access enforcement in command execution layer.

Covers:
- check_tenant_device_access() unit tests (pattern matching, edge cases)
- Integration with run_show_command() and run_config_command() (ACCESS_DENIED)
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from network_mcp.config import NetworkSettings
from network_mcp.helpers import check_tenant_device_access, run_config_command, run_show_command
from network_mcp.middleware import clear_request_context, set_request_context
from network_mcp.server import conn_mgr


def _mock_acquire_for(mock_node):
    """Return a patch context that mocks conn_mgr.acquire to yield mock_node."""

    @contextmanager
    def _acquire(host):  # noqa: ARG001
        yield mock_node

    return patch.object(conn_mgr, "acquire", side_effect=_acquire)


class TestCheckTenantDeviceAccess:
    """Unit tests for check_tenant_device_access()."""

    def setup_method(self):
        clear_request_context()

    def teardown_method(self):
        clear_request_context()

    def test_no_tenant_context_allows_all(self):
        """No tenant context (stdio transport) — all hosts allowed."""
        # No set_request_context called, tenant_id is None
        assert check_tenant_device_access("spine-01") is None
        assert check_tenant_device_access("leaf-01") is None

    def test_tenant_with_no_device_filter_allows_all(self):
        """Tenant set but no device filter — all hosts allowed."""
        set_request_context(user="alice", tenant_id="acme", tenant_devices=None)
        assert check_tenant_device_access("spine-01") is None
        assert check_tenant_device_access("any-device") is None

    def test_allowed_host_passes(self):
        """Host in tenant's allowed devices list passes."""
        set_request_context(user="alice", tenant_id="acme", tenant_devices=["spine-01", "leaf-01"])
        assert check_tenant_device_access("spine-01") is None
        assert check_tenant_device_access("leaf-01") is None

    def test_disallowed_host_returns_error(self):
        """Host not in tenant's allowed devices returns error."""
        set_request_context(user="alice", tenant_id="acme", tenant_devices=["spine-01"])
        result = check_tenant_device_access("leaf-02")
        assert result is not None
        assert "Access denied" in result
        assert "tenant scope" in result

    def test_wildcard_pattern_matches(self):
        """Wildcard pattern 'spine-*' matches 'spine-01'."""
        set_request_context(user="alice", tenant_id="acme", tenant_devices=["spine-*"])
        assert check_tenant_device_access("spine-01") is None
        assert check_tenant_device_access("spine-02") is None
        assert check_tenant_device_access("spine-abc") is None

    def test_wildcard_pattern_rejects_non_match(self):
        """Wildcard pattern 'spine-*' does not match 'leaf-01'."""
        set_request_context(user="alice", tenant_id="acme", tenant_devices=["spine-*"])
        result = check_tenant_device_access("leaf-01")
        assert result is not None
        assert "Access denied" in result

    def test_multiple_patterns(self):
        """Multiple patterns: 'spine-*' and 'leaf-0?' match expected hosts."""
        set_request_context(
            user="alice",
            tenant_id="acme",
            tenant_devices=["spine-*", "leaf-0?"],
        )
        assert check_tenant_device_access("spine-01") is None
        assert check_tenant_device_access("leaf-01") is None
        assert check_tenant_device_access("leaf-09") is None
        # leaf-10 doesn't match 'leaf-0?'
        result = check_tenant_device_access("leaf-10")
        assert result is not None

    def test_empty_device_list_denies_all(self):
        """Empty device list means no devices are allowed."""
        set_request_context(user="alice", tenant_id="acme", tenant_devices=[])
        result = check_tenant_device_access("spine-01")
        assert result is not None
        assert "Access denied" in result

    def test_no_tenant_id_with_device_filter_allows_all(self):
        """If tenant_id is None, skip check even if tenant_devices is set somehow."""
        # Manually set devices but no tenant_id
        set_request_context(user="alice", tenant_id=None, tenant_devices=["spine-01"])
        # No tenant_id means check returns None (allow)
        assert check_tenant_device_access("leaf-99") is None


class TestTenantEnforcementInRunShowCommand:
    """Integration: run_show_command blocks when tenant context denies device access."""

    def setup_method(self):
        clear_request_context()

    def teardown_method(self):
        clear_request_context()

    def test_run_show_command_denied_for_disallowed_device(self):
        """run_show_command returns ACCESS_DENIED when tenant context blocks the host."""
        set_request_context(user="alice", tenant_id="acme", tenant_devices=["spine-*"])
        mock_node = MagicMock()
        mock_node.run_show.return_value = [{"version": "4.32.1F"}]
        with _mock_acquire_for(mock_node):
            result = run_show_command(conn_mgr, "leaf-01", ["show version"])
        assert result["status"] == "error"
        assert "Access denied" in result["error"]
        # Device command should NOT have been called
        mock_node.run_show.assert_not_called()

    def test_run_show_command_allowed_for_matching_device(self):
        """run_show_command succeeds when tenant context allows the host."""
        set_request_context(user="alice", tenant_id="acme", tenant_devices=["spine-*"])
        mock_node = MagicMock()
        mock_node.run_show.return_value = [{"version": "4.32.1F"}]
        with _mock_acquire_for(mock_node):
            result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "success"

    def test_run_show_command_no_tenant_context_passes(self):
        """run_show_command succeeds when no tenant context is set (stdio mode)."""
        # No set_request_context - simulates local stdio transport
        mock_node = MagicMock()
        mock_node.run_show.return_value = [{"version": "4.32.1F"}]
        with _mock_acquire_for(mock_node):
            result = run_show_command(conn_mgr, "any-device", ["show version"])
        assert result["status"] == "success"


class TestTenantEnforcementInRunConfigCommand:
    """Integration: run_config_command blocks when tenant context denies device access."""

    def setup_method(self):
        clear_request_context()

    def teardown_method(self):
        clear_request_context()

    def test_run_config_command_denied_for_disallowed_device(self):
        """run_config_command returns ACCESS_DENIED when tenant context blocks the host."""
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._settings
        try:
            helpers_mod._settings = NetworkSettings(net_read_only=False)
            set_request_context(user="bob", tenant_id="beta", tenant_devices=["leaf-*"])
            mock_node = MagicMock()
            with _mock_acquire_for(mock_node):
                result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
            assert result["status"] == "error"
            assert "Access denied" in result["error"]
            mock_node.run_config.assert_not_called()
        finally:
            helpers_mod._settings = original

    def test_run_config_command_allowed_for_matching_device(self):
        """run_config_command succeeds when tenant context allows the host."""
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._settings
        try:
            helpers_mod._settings = NetworkSettings(net_read_only=False)
            set_request_context(user="bob", tenant_id="beta", tenant_devices=["leaf-*"])
            mock_node = MagicMock()
            mock_node.run_config.return_value = [{}]
            with _mock_acquire_for(mock_node):
                result = run_config_command(conn_mgr, "leaf-01", ["vlan 200", "name TEST"], "create_vlan")
            assert result["status"] == "success"
        finally:
            helpers_mod._settings = original

    def test_run_config_command_no_tenant_context_passes(self):
        """run_config_command succeeds when no tenant context is set (stdio mode)."""
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._settings
        try:
            helpers_mod._settings = NetworkSettings(net_read_only=False)
            mock_node = MagicMock()
            mock_node.run_config.return_value = [{}]
            with _mock_acquire_for(mock_node):
                result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
            assert result["status"] == "success"
        finally:
            helpers_mod._settings = original
