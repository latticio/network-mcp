"""Tests for config command retry behavior (Bug 1: double-apply risk)."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pyeapi.eapilib

from network_mcp.config import NetworkSettings
from network_mcp.helpers import run_config_command
from network_mcp.server import conn_mgr


def _mock_acquire_for(mock_node):
    """Return a patch context that mocks conn_mgr.acquire to yield mock_node."""

    @contextmanager
    def _acquire(host):  # noqa: ARG001
        yield mock_node

    return patch.object(conn_mgr, "acquire", side_effect=_acquire)


class TestConfigRetryDefault:
    """Config retry is disabled by default to prevent double-apply."""

    def test_config_retry_disabled_by_default(self):
        """config_retry_enabled defaults to False."""
        s = NetworkSettings()
        assert s.config_retry_enabled is False

    def test_no_retry_on_timeout_when_disabled(self):
        """With retry disabled (default), a timeout results in single execution — no double-apply."""
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._settings
        try:
            helpers_mod._settings = NetworkSettings(net_read_only=False, config_retry_enabled=False)
            mock_node = MagicMock()
            mock_node.run_config.side_effect = TimeoutError("HTTP response timed out")
            with _mock_acquire_for(mock_node):
                result = run_config_command(conn_mgr, "spine-01", ["vlan 100", "name SERVERS"], "create_vlan")
            assert result["status"] == "error"
            # Key assertion: config was only attempted once (no retry)
            assert mock_node.run_config.call_count == 1
        finally:
            helpers_mod._settings = original

    def test_no_retry_on_connection_error_when_disabled(self):
        """With retry disabled, ConnectionError is not retried."""
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._settings
        try:
            helpers_mod._settings = NetworkSettings(net_read_only=False, config_retry_enabled=False)
            mock_node = MagicMock()
            mock_node.run_config.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "refused")
            with _mock_acquire_for(mock_node):
                result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
            assert result["status"] == "error"
            assert mock_node.run_config.call_count == 1
        finally:
            helpers_mod._settings = original


class TestConfigRetryEnabled:
    """Config retry can be opted into via config_retry_enabled=True."""

    def test_retry_on_timeout_when_enabled(self):
        """With retry enabled, a timeout on first attempt retries and succeeds."""
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._settings
        try:
            helpers_mod._settings = NetworkSettings(net_read_only=False, config_retry_enabled=True)
            mock_node = MagicMock()
            # First call times out, second call succeeds
            mock_node.run_config.side_effect = [TimeoutError("HTTP response timed out"), None]
            with _mock_acquire_for(mock_node):
                result = run_config_command(conn_mgr, "spine-01", ["vlan 100", "name SERVERS"], "create_vlan")
            assert result["status"] == "success"
            assert mock_node.run_config.call_count == 2
        finally:
            helpers_mod._settings = original

    def test_retry_on_connection_error_when_enabled(self):
        """With retry enabled, ConnectionError on first attempt retries."""
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._settings
        try:
            helpers_mod._settings = NetworkSettings(net_read_only=False, config_retry_enabled=True)
            mock_node = MagicMock()
            mock_node.run_config.side_effect = [
                pyeapi.eapilib.ConnectionError("spine-01", "refused"),
                None,
            ]
            with _mock_acquire_for(mock_node):
                result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
            assert result["status"] == "success"
            assert mock_node.run_config.call_count == 2
        finally:
            helpers_mod._settings = original

    def test_config_retry_enabled_setting(self):
        """config_retry_enabled=True is respected in settings."""
        s = NetworkSettings(config_retry_enabled=True)
        assert s.config_retry_enabled is True
