"""Tests for SSL verification defaults across Cisco drivers and config.

Validates that TLS certificate verification is enabled by default for
RESTCONF (IOS-XE) and NX-API (NX-OS) connections, and can be explicitly
disabled via configuration.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from network_mcp.connection import ConnectionManager
from network_mcp.drivers.cisco_iosxe import IosXeDriver
from network_mcp.drivers.cisco_nxos import NxosDriver
from tests.conftest import make_test_settings

# --- Config setting defaults ---


class TestVerifySslConfigDefault:
    """Verify the verify_ssl setting defaults to True."""

    def test_verify_ssl_default_true(self):
        settings = make_test_settings()
        assert settings.net_verify_ssl is True

    def test_verify_ssl_explicit_false(self):
        settings = make_test_settings(net_verify_ssl=False)
        assert settings.net_verify_ssl is False

    def test_verify_ssl_explicit_true(self):
        settings = make_test_settings(net_verify_ssl=True)
        assert settings.net_verify_ssl is True


# --- IOS-XE driver ---


class TestIosXeVerifySsl:
    """Verify IOS-XE RESTCONF transport passes verify_ssl correctly."""

    def test_restconf_verify_ssl_default_true(self):
        """httpx.Client should receive verify=True when no verify_ssl kwarg is passed."""
        driver = IosXeDriver()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"capabilities": {}}

        with patch("network_mcp.drivers.cisco_iosxe.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            driver.connect(host="10.0.0.1", username="admin", password="cisco123")

            # httpx.Client should have been called with verify=True (the default)
            mock_httpx.Client.assert_called_once()
            call_kwargs = mock_httpx.Client.call_args
            assert call_kwargs.kwargs.get("verify", call_kwargs[1].get("verify")) is True

    def test_restconf_verify_ssl_explicit_false(self):
        """httpx.Client should receive verify=False when verify_ssl=False."""
        driver = IosXeDriver()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"capabilities": {}}

        with patch("network_mcp.drivers.cisco_iosxe.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            driver.connect(host="10.0.0.1", username="admin", password="cisco123", verify_ssl=False)

            mock_httpx.Client.assert_called_once()
            call_kwargs = mock_httpx.Client.call_args
            assert call_kwargs.kwargs.get("verify", call_kwargs[1].get("verify")) is False

    def test_restconf_verify_ssl_explicit_true(self):
        """httpx.Client should receive verify=True when verify_ssl=True."""
        driver = IosXeDriver()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"capabilities": {}}

        with patch("network_mcp.drivers.cisco_iosxe.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            driver.connect(host="10.0.0.1", username="admin", password="cisco123", verify_ssl=True)

            mock_httpx.Client.assert_called_once()
            call_kwargs = mock_httpx.Client.call_args
            assert call_kwargs.kwargs.get("verify", call_kwargs[1].get("verify")) is True


# --- NX-OS driver ---


class TestNxosVerifySsl:
    """Verify NX-OS NX-API transport passes verify_ssl correctly."""

    def test_nxapi_verify_ssl_default_true(self):
        """httpx.Client should receive verify=True when no verify_ssl kwarg is passed."""
        driver = NxosDriver()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "ins_api": {"outputs": {"output": {"code": "200", "msg": "Success", "body": {}}}}
        }

        with patch("network_mcp.drivers.cisco_nxos.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            driver.connect(host="10.0.0.1", username="admin", password="cisco123")

            mock_httpx.Client.assert_called_once()
            call_kwargs = mock_httpx.Client.call_args
            assert call_kwargs.kwargs.get("verify", call_kwargs[1].get("verify")) is True

    def test_nxapi_verify_ssl_explicit_false(self):
        """httpx.Client should receive verify=False when verify_ssl=False."""
        driver = NxosDriver()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "ins_api": {"outputs": {"output": {"code": "200", "msg": "Success", "body": {}}}}
        }

        with patch("network_mcp.drivers.cisco_nxos.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            driver.connect(host="10.0.0.1", username="admin", password="cisco123", verify_ssl=False)

            mock_httpx.Client.assert_called_once()
            call_kwargs = mock_httpx.Client.call_args
            assert call_kwargs.kwargs.get("verify", call_kwargs[1].get("verify")) is False

    def test_nxapi_verify_ssl_explicit_true(self):
        """httpx.Client should receive verify=True when verify_ssl=True."""
        driver = NxosDriver()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "ins_api": {"outputs": {"output": {"code": "200", "msg": "Success", "body": {}}}}
        }

        with patch("network_mcp.drivers.cisco_nxos.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            driver.connect(host="10.0.0.1", username="admin", password="cisco123", verify_ssl=True)

            mock_httpx.Client.assert_called_once()
            call_kwargs = mock_httpx.Client.call_args
            assert call_kwargs.kwargs.get("verify", call_kwargs[1].get("verify")) is True


# --- ConnectionManager wiring ---


class TestConnectionManagerVerifySsl:
    """Verify ConnectionManager passes verify_ssl to drivers."""

    def test_connection_manager_stores_verify_ssl(self):
        settings = make_test_settings(net_verify_ssl=True)
        mgr = ConnectionManager(settings=settings)
        assert mgr._verify_ssl is True

    def test_connection_manager_stores_verify_ssl_false(self):
        settings = make_test_settings(net_verify_ssl=False)
        mgr = ConnectionManager(settings=settings)
        assert mgr._verify_ssl is False

    def test_create_driver_passes_verify_ssl(self):
        """_create_driver should pass verify_ssl to the driver's connect() method."""
        settings = make_test_settings(net_verify_ssl=True)
        mgr = ConnectionManager(settings=settings)

        mock_driver = MagicMock()
        mock_driver_cls = MagicMock(return_value=mock_driver)

        creds = MagicMock()
        creds.host = "10.0.0.1"
        creds.transport = "https"
        creds.username = "admin"
        creds.password.get_secret_value.return_value = "pass"
        creds.port = 443
        creds.platform = "iosxe"

        with patch("network_mcp.connection.DRIVER_REGISTRY", {"iosxe": mock_driver_cls}):
            mgr._create_driver(creds)

        mock_driver.connect.assert_called_once()
        call_kwargs = mock_driver.connect.call_args
        assert call_kwargs.kwargs.get("verify_ssl") is True

    def test_create_driver_passes_verify_ssl_false(self):
        """_create_driver should pass verify_ssl=False when configured."""
        settings = make_test_settings(net_verify_ssl=False)
        mgr = ConnectionManager(settings=settings)

        mock_driver = MagicMock()
        mock_driver_cls = MagicMock(return_value=mock_driver)

        creds = MagicMock()
        creds.host = "10.0.0.1"
        creds.transport = "https"
        creds.username = "admin"
        creds.password.get_secret_value.return_value = "pass"
        creds.port = 443
        creds.platform = "iosxe"

        with patch("network_mcp.connection.DRIVER_REGISTRY", {"iosxe": mock_driver_cls}):
            mgr._create_driver(creds)

        mock_driver.connect.assert_called_once()
        call_kwargs = mock_driver.connect.call_args
        assert call_kwargs.kwargs.get("verify_ssl") is False
