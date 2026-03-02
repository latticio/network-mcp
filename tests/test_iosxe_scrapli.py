"""Tests for IOS-XE scrapli SSH transport.

Validates the IosXeScrapliTransport class and its integration with
IosXeDriver, including session reuse, failover from RESTCONF to SSH,
authentication failure handling, and graceful degradation when scrapli
is not installed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.cisco_iosxe import HAS_SCRAPLI, IosXeDriver, IosXeScrapliTransport

# --- IosXeScrapliTransport unit tests ---


class TestScrapliTransportInit:
    """Test IosXeScrapliTransport initialization."""

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_init_creates_driver(self):
        """Constructor creates a ScrapliIOSXEDriver with correct params."""
        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_cls:
            transport = IosXeScrapliTransport(
                host="10.0.0.1",
                username="admin",
                password="cisco123",
                port=22,
            )
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["host"] == "10.0.0.1"
            assert call_kwargs["auth_username"] == "admin"
            assert call_kwargs["auth_password"] == "cisco123"
            assert call_kwargs["port"] == 22
            assert call_kwargs["auth_strict_key"] is True
            assert call_kwargs["transport"] == "system"
            assert call_kwargs["timeout_socket"] == 10
            assert call_kwargs["timeout_ops"] == 30
            assert transport.is_alive is False

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_init_with_private_key(self):
        """Private key is passed to driver when provided."""
        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_cls:
            IosXeScrapliTransport(
                host="10.0.0.1",
                username="admin",
                password="",
                auth_private_key="/home/user/.ssh/id_rsa",
            )
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["auth_private_key"] == "/home/user/.ssh/id_rsa"

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_init_strict_key_disabled(self):
        """Host key verification can be disabled."""
        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_cls:
            IosXeScrapliTransport(
                host="10.0.0.1",
                username="admin",
                password="cisco123",
                auth_strict_key=False,
            )
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["auth_strict_key"] is False

    def test_init_without_scrapli_raises(self):
        """ImportError raised when scrapli is not installed."""
        with patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", False):
            with pytest.raises(ImportError, match="scrapli not installed"):
                IosXeScrapliTransport(
                    host="10.0.0.1",
                    username="admin",
                    password="cisco123",
                )


class TestScrapliTransportSendCommand:
    """Test IosXeScrapliTransport.send_command()."""

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_send_command_returns_output(self):
        """send_command() returns the result string from scrapli."""
        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = "Cisco IOS XE Software, Version 17.3.4a"
            mock_driver.send_command.return_value = mock_response

            transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="cisco123")
            # Mark as connected to skip auto-open
            transport._connected = True

            output = transport.send_command("show version")
            assert output == "Cisco IOS XE Software, Version 17.3.4a"
            mock_driver.send_command.assert_called_once_with("show version")

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_send_command_auto_opens(self):
        """send_command() calls open() if not connected."""
        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = "output"
            mock_driver.send_command.return_value = mock_response

            transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="cisco123")
            assert transport.is_alive is False

            transport.send_command("show version")
            mock_driver.open.assert_called_once()
            assert transport.is_alive is True

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_send_command_failed_raises(self):
        """send_command() raises RuntimeError when scrapli reports failure."""
        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            mock_response = MagicMock()
            mock_response.failed = True
            mock_response.channel_input = "show bad-command"
            mock_driver.send_command.return_value = mock_response

            transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="cisco123")
            transport._connected = True

            with pytest.raises(RuntimeError, match="Command failed"):
                transport.send_command("show bad-command")


class TestScrapliTransportSendConfig:
    """Test IosXeScrapliTransport.send_config()."""

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_send_config_returns_output(self):
        """send_config() sends configs via scrapli and returns result."""
        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = ""
            mock_driver.send_configs.return_value = mock_response

            transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="cisco123")
            transport._connected = True

            output = transport.send_config(["vlan 100", "name SERVERS"])
            assert output == ""
            mock_driver.send_configs.assert_called_once_with(["vlan 100", "name SERVERS"])

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_send_config_failed_raises(self):
        """send_config() raises RuntimeError on failure."""
        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            mock_response = MagicMock()
            mock_response.failed = True
            mock_response.result = "Invalid input"
            mock_driver.send_configs.return_value = mock_response

            transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="cisco123")
            transport._connected = True

            with pytest.raises(RuntimeError, match="Config commands failed"):
                transport.send_config(["invalid-command"])


class TestScrapliTransportLifecycle:
    """Test open/close lifecycle."""

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_open_sets_connected(self):
        """open() marks transport as connected."""
        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="cisco123")
            assert transport.is_alive is False

            transport.open()
            mock_driver.open.assert_called_once()
            assert transport.is_alive is True

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_close_disconnects(self):
        """close() closes the driver and marks as disconnected."""
        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="cisco123")
            transport._connected = True

            transport.close()
            mock_driver.close.assert_called_once()
            assert transport.is_alive is False

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_close_when_not_connected_is_noop(self):
        """close() does nothing when not connected."""
        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="cisco123")
            transport.close()
            mock_driver.close.assert_not_called()


# --- IosXeDriver SSH integration tests ---


class TestIosXeDriverScrapliIntegration:
    """Test IosXeDriver SSH fallback using scrapli transport."""

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_ssh_fallback_creates_scrapli_transport(self):
        """When RESTCONF fails, driver creates scrapli SSH transport."""
        driver = IosXeDriver()

        with (
            patch("network_mcp.drivers.cisco_iosxe.httpx") as mock_httpx,
            patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_scrapli_cls,
        ):
            mock_client = MagicMock()
            mock_client.get.side_effect = Exception("Connection refused")
            mock_httpx.Client.return_value = mock_client

            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            driver.connect(host="10.0.0.1", username="admin", password="cisco123")

        assert driver.is_connected()
        assert driver._transport == "ssh"
        assert driver._ssh_transport is not None

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_ssh_direct_creates_scrapli_transport(self):
        """Direct SSH transport creates scrapli transport."""
        driver = IosXeDriver()

        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="cisco123")

        assert driver.is_connected()
        assert driver._transport == "ssh"
        assert driver._ssh_transport is not None

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_ssh_session_reuse(self):
        """Second command reuses the same SSH session (no new open)."""
        driver = IosXeDriver()

        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = "output1"
            mock_scrapli_driver.send_command.return_value = mock_response

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="cisco123")
            # Simulate the transport being connected already
            driver._ssh_transport._connected = True

            # First command
            result1 = driver._ssh_exec("show version")
            # Second command
            mock_response.result = "output2"
            result2 = driver._ssh_exec("show interfaces")

            assert result1 == "output1"
            assert result2 == "output2"
            # ScrapliIOSXEDriver was only instantiated once (session reuse)
            assert mock_scrapli_cls.call_count == 1

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_ssh_auth_failure(self):
        """Authentication failure raises RuntimeError."""
        from scrapli.exceptions import ScrapliAuthenticationFailed as AuthFailed

        driver = IosXeDriver()

        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver
            mock_scrapli_driver.send_command.side_effect = AuthFailed("Auth failed")

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="wrong")
            driver._ssh_transport._connected = True

            with pytest.raises(RuntimeError, match="SSH command failed"):
                driver._ssh_exec("show version")

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_ssh_timeout_raises_timeout_error(self):
        """Scrapli timeout is converted to TimeoutError."""
        from scrapli.exceptions import ScrapliTimeout as Timeout

        driver = IosXeDriver()

        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver
            mock_scrapli_driver.send_command.side_effect = Timeout("Timed out")

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="cisco123")
            driver._ssh_transport._connected = True

            with pytest.raises(TimeoutError, match="timed out"):
                driver._ssh_exec("show version")

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_close_cleans_up_scrapli(self):
        """close() cleans up both RESTCONF and scrapli transports."""
        driver = IosXeDriver()

        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="cisco123")
            assert driver._ssh_transport is not None
            driver._ssh_transport._connected = True

            driver.close()
            mock_scrapli_driver.close.assert_called_once()
            assert driver._ssh_transport is None
            assert not driver.is_connected()

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_ssh_config_via_scrapli(self):
        """Config commands are sent via scrapli send_configs."""
        driver = IosXeDriver()

        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = ""
            mock_scrapli_driver.send_configs.return_value = mock_response

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="cisco123")
            driver._ssh_transport._connected = True

            result = driver._ssh_config(["vlan 100", "name SERVERS"])
            assert result == [""]
            mock_scrapli_driver.send_configs.assert_called_once_with(["vlan 100", "name SERVERS"])


class TestIosXeDriverNoScrapli:
    """Test graceful degradation when scrapli is not installed."""

    def test_ssh_fallback_without_scrapli_logs_warning(self):
        """When scrapli is not installed, SSH fallback logs a warning."""
        driver = IosXeDriver()

        with (
            patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", False),
            patch("network_mcp.drivers.cisco_iosxe.HAS_HTTPX", False),
            patch("network_mcp.drivers.cisco_iosxe.logger") as mock_logger,
        ):
            driver.connect(host="10.0.0.1", username="admin", password="cisco123")

        assert driver.is_connected()
        assert driver._transport == "ssh"
        assert driver._ssh_transport is None
        mock_logger.warning.assert_any_call(
            "scrapli not installed — SSH fallback unavailable for %s. Install with: pip install network-mcp[ssh]",
            "10.0.0.1",
        )

    def test_ssh_exec_without_transport_raises(self):
        """_ssh_exec raises ConnectionError when scrapli transport is None."""
        driver = IosXeDriver()
        driver._connected = True
        driver._ssh_host = "10.0.0.1"
        driver._ssh_username = "admin"
        driver._ssh_transport = None

        with pytest.raises(ConnectionError, match="SSH transport not available"):
            driver._ssh_exec("show version")

    def test_ssh_config_without_transport_raises(self):
        """_ssh_config raises ConnectionError when scrapli transport is None."""
        driver = IosXeDriver()
        driver._connected = True
        driver._ssh_transport = None

        with pytest.raises(ConnectionError, match="SSH transport not available"):
            driver._ssh_config(["vlan 100"])

    def test_ssh_show_without_transport_returns_error(self):
        """_ssh_show returns error dict when SSH transport unavailable."""
        driver = IosXeDriver()
        driver._connected = True
        driver._ssh_host = "10.0.0.1"
        driver._ssh_username = "admin"
        driver._ssh_transport = None

        results = driver._ssh_show(["show version"])
        assert len(results) == 1
        assert "error" in results[0]
        assert "SSH transport not available" in results[0]["error"]


class TestIosXeDriverSshKwargsPassthrough:
    """Test that SSH-specific kwargs are passed through to scrapli."""

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_private_key_passthrough(self):
        """auth_private_key kwarg is stored and passed to scrapli."""
        driver = IosXeDriver()

        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_scrapli_cls:
            mock_scrapli_cls.return_value = MagicMock()

            driver.connect(
                host="10.0.0.1",
                transport="ssh",
                username="admin",
                password="",
                auth_private_key="/home/user/.ssh/id_rsa",
            )

            call_kwargs = mock_scrapli_cls.call_args[1]
            assert call_kwargs["auth_private_key"] == "/home/user/.ssh/id_rsa"

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_strict_key_disabled(self):
        """ssh_strict_key=False disables host key verification."""
        driver = IosXeDriver()

        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_scrapli_cls:
            mock_scrapli_cls.return_value = MagicMock()

            driver.connect(
                host="10.0.0.1",
                transport="ssh",
                username="admin",
                password="cisco123",
                ssh_strict_key=False,
            )

            call_kwargs = mock_scrapli_cls.call_args[1]
            assert call_kwargs["auth_strict_key"] is False

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_custom_known_hosts_file(self):
        """ssh_known_hosts_file kwarg is passed to scrapli."""
        driver = IosXeDriver()

        with patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver") as mock_scrapli_cls:
            mock_scrapli_cls.return_value = MagicMock()

            driver.connect(
                host="10.0.0.1",
                transport="ssh",
                username="admin",
                password="cisco123",
                ssh_known_hosts_file="/custom/known_hosts",
            )

            call_kwargs = mock_scrapli_cls.call_args[1]
            assert call_kwargs["ssh_known_hosts_file"] == "/custom/known_hosts"
