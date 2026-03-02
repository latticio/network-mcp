"""Tests for the DeviceDriver protocol and EosDriver implementation."""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.driver import DeviceDriver, EosDriver


class TestDeviceDriverProtocol:
    """Verify EosDriver satisfies the DeviceDriver protocol."""

    def test_eos_driver_is_device_driver(self):
        """EosDriver must satisfy the DeviceDriver runtime_checkable protocol."""
        driver = EosDriver()
        assert isinstance(driver, DeviceDriver)

    def test_protocol_has_required_methods(self):
        """DeviceDriver protocol requires platform, connect, run_show, run_config, close, is_connected."""
        assert hasattr(DeviceDriver, "platform")
        assert hasattr(DeviceDriver, "connect")
        assert hasattr(DeviceDriver, "run_show")
        assert hasattr(DeviceDriver, "run_config")
        assert hasattr(DeviceDriver, "close")
        assert hasattr(DeviceDriver, "is_connected")


class TestEosDriverInit:
    """Test EosDriver initialization and connection state."""

    def test_initial_state(self):
        driver = EosDriver()
        assert driver.platform == "eos"
        assert driver.is_connected() is False
        assert driver._node is None

    def test_not_connected_run_show_raises(self):
        driver = EosDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_show(["show version"])

    def test_not_connected_run_config_raises(self):
        driver = EosDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_config(["vlan 100"])

    def test_not_connected_run_commands_raises(self):
        """Backward-compat alias also raises when not connected."""
        driver = EosDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_commands(["show version"])

    def test_not_connected_config_raises(self):
        """Backward-compat alias also raises when not connected."""
        driver = EosDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.config(["vlan 100"])


class TestEosDriverConnect:
    """Test EosDriver connect lifecycle."""

    @patch("network_mcp.driver.pyeapi.connect")
    def test_connect_success(self, mock_connect):
        mock_node = MagicMock()
        mock_connect.return_value = mock_node

        driver = EosDriver()
        driver.connect(host="10.0.0.1", transport="https", username="admin", password="pass")

        assert driver.is_connected() is True
        mock_connect.assert_called_once_with(
            host="10.0.0.1",
            transport="https",
            username="admin",
            password="pass",
            return_node=True,
        )

    @patch("network_mcp.driver.pyeapi.connect")
    def test_connect_with_kwargs(self, mock_connect):
        mock_connect.return_value = MagicMock()

        driver = EosDriver()
        driver.connect(host="10.0.0.1", transport="https", username="admin", password="pass", port=443, timeout=30)

        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs["port"] == 443
        assert call_kwargs["timeout"] == 30


class TestEosDriverRunShow:
    """Test EosDriver run_show method."""

    @patch("network_mcp.driver.pyeapi.connect")
    def test_run_show_json(self, mock_connect):
        mock_node = MagicMock()
        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]
        mock_connect.return_value = mock_node

        driver = EosDriver()
        driver.connect(host="10.0.0.1", transport="https", username="admin", password="pass")
        result = driver.run_show(["show version"])

        assert result == [{"version": "4.32.1F"}]
        mock_node.run_commands.assert_called_once_with(["show version"], encoding="json")

    @patch("network_mcp.driver.pyeapi.connect")
    def test_run_show_text(self, mock_connect):
        mock_node = MagicMock()
        mock_node.run_commands.return_value = [{"output": "running-config..."}]
        mock_connect.return_value = mock_node

        driver = EosDriver()
        driver.connect(host="10.0.0.1", transport="https", username="admin", password="pass")
        result = driver.run_show(["show running-config"], encoding="text")

        mock_node.run_commands.assert_called_once_with(["show running-config"], encoding="text")
        assert result == [{"output": "running-config..."}]


class TestEosDriverRunConfig:
    """Test EosDriver run_config method."""

    @patch("network_mcp.driver.pyeapi.connect")
    def test_run_config(self, mock_connect):
        mock_node = MagicMock()
        mock_node.config.return_value = [{}]
        mock_connect.return_value = mock_node

        driver = EosDriver()
        driver.connect(host="10.0.0.1", transport="https", username="admin", password="pass")
        result = driver.run_config(["vlan 100", "name SERVERS"])

        assert result == [{}]
        mock_node.config.assert_called_once_with(["vlan 100", "name SERVERS"])


class TestEosDriverBackwardCompat:
    """Test backward-compatibility aliases (run_commands, config)."""

    @patch("network_mcp.driver.pyeapi.connect")
    def test_run_commands_alias(self, mock_connect):
        mock_node = MagicMock()
        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]
        mock_connect.return_value = mock_node

        driver = EosDriver()
        driver.connect(host="10.0.0.1", transport="https", username="admin", password="pass")
        result = driver.run_commands(["show version"])

        assert result == [{"version": "4.32.1F"}]

    @patch("network_mcp.driver.pyeapi.connect")
    def test_config_alias_with_kwargs(self, mock_connect):
        """config() alias passes through kwargs (e.g., session=)."""
        mock_node = MagicMock()
        mock_node.config.return_value = [{}]
        mock_connect.return_value = mock_node

        driver = EosDriver()
        driver.connect(host="10.0.0.1", transport="https", username="admin", password="pass")
        driver.config(["vlan 100"], session="test-session")

        mock_node.config.assert_called_once_with(["vlan 100"], session="test-session")


class TestEosDriverClose:
    """Test EosDriver close and cleanup."""

    @patch("network_mcp.driver.pyeapi.connect")
    def test_close(self, mock_connect):
        mock_node = MagicMock()
        mock_transport = MagicMock()
        mock_node._connection = mock_transport
        mock_connect.return_value = mock_node

        driver = EosDriver()
        driver.connect(host="10.0.0.1", transport="https", username="admin", password="pass")
        assert driver.is_connected() is True

        driver.close()
        assert driver.is_connected() is False
        mock_transport.close.assert_called_once()

    @patch("network_mcp.driver.pyeapi.connect")
    def test_close_no_transport(self, mock_connect):
        """close() handles nodes without a _connection attribute."""
        mock_node = MagicMock(spec=[])  # no attributes
        mock_connect.return_value = mock_node

        driver = EosDriver()
        driver.connect(host="10.0.0.1", transport="https", username="admin", password="pass")
        driver.close()  # should not raise
        assert driver.is_connected() is False

    def test_close_when_not_connected(self):
        driver = EosDriver()
        driver.close()  # should not raise
        assert driver.is_connected() is False

    @patch("network_mcp.driver.pyeapi.connect")
    def test_close_transport_error_suppressed(self, mock_connect):
        mock_node = MagicMock()
        mock_transport = MagicMock()
        mock_transport.close.side_effect = RuntimeError("close failed")
        mock_node._connection = mock_transport
        mock_connect.return_value = mock_node

        driver = EosDriver()
        driver.connect(host="10.0.0.1", transport="https", username="admin", password="pass")
        driver.close()  # should not raise despite transport error
        assert driver.is_connected() is False
