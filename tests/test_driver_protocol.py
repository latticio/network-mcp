"""Tests for driver protocol compliance, capabilities, reconnect, retry, keep-alive, and timeout propagation.

Validates that all 4 drivers (EOS, IOS-XE, NX-OS, JunOS) implement the
NetworkDriver protocol methods: capabilities(), is_connected(), reconnect(),
and that connection retry, keep-alive, and timeout propagation work correctly.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.driver import _DEFAULT_KEEPALIVE_INTERVAL as EOS_KEEPALIVE
from network_mcp.driver import EosDriver
from network_mcp.drivers.cisco_iosxe import _DEFAULT_KEEPALIVE_INTERVAL as IOSXE_KEEPALIVE
from network_mcp.drivers.cisco_iosxe import IosXeDriver
from network_mcp.drivers.cisco_nxos import _DEFAULT_KEEPALIVE_INTERVAL as NXOS_KEEPALIVE
from network_mcp.drivers.cisco_nxos import NxosDriver
from network_mcp.drivers.juniper_junos import _DEFAULT_KEEPALIVE_INTERVAL as JUNOS_KEEPALIVE
from network_mcp.drivers.juniper_junos import JunosDriver

# ============================================================================
# Protocol compliance tests
# ============================================================================


class TestProtocolCompliance:
    """Verify all drivers implement the required NetworkDriver methods."""

    @pytest.mark.parametrize("driver_cls", [EosDriver, IosXeDriver, NxosDriver, JunosDriver])
    def test_has_capabilities(self, driver_cls):
        """Each driver has a capabilities() method."""
        assert hasattr(driver_cls, "capabilities")

    @pytest.mark.parametrize("driver_cls", [EosDriver, IosXeDriver, NxosDriver, JunosDriver])
    def test_has_is_connected(self, driver_cls):
        """Each driver has an is_connected() method."""
        assert hasattr(driver_cls, "is_connected")

    @pytest.mark.parametrize("driver_cls", [EosDriver, IosXeDriver, NxosDriver, JunosDriver])
    def test_has_reconnect(self, driver_cls):
        """Each driver has a reconnect() method."""
        assert hasattr(driver_cls, "reconnect")

    @pytest.mark.parametrize("driver_cls", [EosDriver, IosXeDriver, NxosDriver, JunosDriver])
    def test_has_normalized_getters(self, driver_cls):
        """Each driver has all 6 normalized getters."""
        for method in [
            "get_facts",
            "get_interfaces",
            "get_bgp_summary",
            "get_arp_table",
            "get_lldp_neighbors",
            "get_config",
        ]:
            assert hasattr(driver_cls, method), f"{driver_cls.__name__} missing {method}"


# ============================================================================
# Capabilities tests
# ============================================================================


class TestEosCapabilities:
    """Test EOS driver capabilities."""

    def test_capabilities_returns_dict(self):
        driver = EosDriver()
        caps = driver.capabilities()
        assert isinstance(caps, dict)

    def test_capabilities_expected_keys(self):
        driver = EosDriver()
        caps = driver.capabilities()
        assert caps["config_sessions"] is True
        assert caps["gnmi"] is True
        assert caps["restconf"] is False
        assert caps["netconf"] is False


class TestIosXeCapabilities:
    """Test IOS-XE driver capabilities."""

    def test_capabilities_returns_dict(self):
        driver = IosXeDriver()
        caps = driver.capabilities()
        assert isinstance(caps, dict)

    def test_capabilities_expected_keys(self):
        driver = IosXeDriver()
        caps = driver.capabilities()
        assert caps["config_sessions"] is False
        assert caps["restconf"] is True
        assert caps["ssh"] is True
        assert caps["netconf"] is False
        assert caps["gnmi"] is False


class TestNxosCapabilities:
    """Test NX-OS driver capabilities."""

    def test_capabilities_returns_dict(self):
        driver = NxosDriver()
        caps = driver.capabilities()
        assert isinstance(caps, dict)

    def test_capabilities_expected_keys(self):
        driver = NxosDriver()
        caps = driver.capabilities()
        assert caps["config_sessions"] is False
        assert caps["nxapi"] is True
        assert caps["ssh"] is True
        assert caps["restconf"] is False
        assert caps["gnmi"] is False


class TestJunosCapabilities:
    """Test JunOS driver capabilities."""

    def test_capabilities_returns_dict(self):
        driver = JunosDriver()
        caps = driver.capabilities()
        assert isinstance(caps, dict)

    def test_capabilities_expected_keys(self):
        driver = JunosDriver()
        caps = driver.capabilities()
        assert caps["config_sessions"] is True
        assert caps["netconf"] is True
        assert caps["ssh"] is True
        assert caps["restconf"] is False
        assert caps["gnmi"] is False


# ============================================================================
# is_connected tests
# ============================================================================


class TestEosIsConnected:
    """Test EOS is_connected behavior."""

    def test_not_connected_when_no_node(self):
        driver = EosDriver()
        assert driver.is_connected() is False

    def test_connected_when_node_exists(self):
        driver = EosDriver()
        driver._node = MagicMock()
        assert driver.is_connected() is True

    def test_not_connected_after_close(self):
        driver = EosDriver()
        driver._node = MagicMock()
        driver.close()
        assert driver.is_connected() is False


class TestIosXeIsConnected:
    """Test IOS-XE is_connected behavior."""

    def test_not_connected_initially(self):
        driver = IosXeDriver()
        assert driver.is_connected() is False

    def test_connected_when_flag_set(self):
        driver = IosXeDriver()
        driver._connected = True
        assert driver.is_connected() is True

    def test_not_connected_after_close(self):
        driver = IosXeDriver()
        driver._connected = True
        driver.close()
        assert driver.is_connected() is False


class TestNxosIsConnected:
    """Test NX-OS is_connected behavior."""

    def test_not_connected_initially(self):
        driver = NxosDriver()
        assert driver.is_connected() is False

    def test_connected_when_flag_set(self):
        driver = NxosDriver()
        driver._connected = True
        assert driver.is_connected() is True

    def test_not_connected_after_close(self):
        driver = NxosDriver()
        driver._connected = True
        driver.close()
        assert driver.is_connected() is False


class TestJunosIsConnected:
    """Test JunOS is_connected behavior."""

    def test_not_connected_initially(self):
        driver = JunosDriver()
        assert driver.is_connected() is False

    def test_connected_when_flag_set(self):
        driver = JunosDriver()
        driver._connected = True
        assert driver.is_connected() is True

    def test_not_connected_after_close(self):
        driver = JunosDriver()
        driver._connected = True
        driver.close()
        assert driver.is_connected() is False


# ============================================================================
# Reconnect tests
# ============================================================================


class TestEosReconnect:
    """Test EOS reconnect behavior."""

    def test_reconnect_without_prior_connection_raises(self):
        driver = EosDriver()
        with pytest.raises(ConnectionError, match="Cannot reconnect"):
            driver.reconnect()

    def test_reconnect_closes_and_reopens(self):
        driver = EosDriver()
        driver._host = "10.0.0.1"
        driver._transport_name = "https"
        driver._username = "admin"
        driver._password = "pass"
        driver._node = MagicMock()

        with patch.object(driver, "_connect_with_retry") as mock_connect:
            driver.reconnect()
            assert driver._node is None  # close() nullifies _node
            mock_connect.assert_called_once()


class TestIosXeReconnect:
    """Test IOS-XE reconnect behavior."""

    def test_reconnect_without_prior_connection_raises(self):
        driver = IosXeDriver()
        with pytest.raises(ConnectionError, match="Cannot reconnect"):
            driver.reconnect()

    def test_reconnect_calls_connect_with_retry(self):
        driver = IosXeDriver()
        driver._ssh_host = "10.0.0.2"
        driver._ssh_username = "admin"
        driver._ssh_password = "cisco"
        driver._connect_transport = "ssh"
        driver._connect_kwargs = {}

        with patch.object(driver, "_connect_with_retry") as mock_connect:
            driver.reconnect()
            mock_connect.assert_called_once()


class TestNxosReconnect:
    """Test NX-OS reconnect behavior."""

    def test_reconnect_without_prior_connection_raises(self):
        driver = NxosDriver()
        with pytest.raises(ConnectionError, match="Cannot reconnect"):
            driver.reconnect()

    def test_reconnect_calls_connect_with_retry(self):
        driver = NxosDriver()
        driver._ssh_host = "10.0.0.3"
        driver._ssh_username = "admin"
        driver._ssh_password = "cisco"
        driver._connect_transport = "ssh"
        driver._connect_kwargs = {}

        with patch.object(driver, "_connect_with_retry") as mock_connect:
            driver.reconnect()
            mock_connect.assert_called_once()


class TestJunosReconnect:
    """Test JunOS reconnect behavior."""

    def test_reconnect_without_prior_connection_raises(self):
        driver = JunosDriver()
        with pytest.raises(ConnectionError, match="Cannot reconnect"):
            driver.reconnect()

    def test_reconnect_calls_connect_with_retry(self):
        driver = JunosDriver()
        driver._host = "10.0.0.4"
        driver._username = "admin"
        driver._password = "juniper"
        driver._connect_transport = "ssh"
        driver._connect_kwargs = {}

        with patch.object(driver, "_connect_with_retry") as mock_connect:
            with patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI_NETCONF", True):
                driver.reconnect()
                mock_connect.assert_called_once()


# ============================================================================
# Connection retry tests
# ============================================================================


class TestEosConnectionRetry:
    """Test EOS connection retry with tenacity."""

    def test_retry_succeeds_on_third_attempt(self):
        driver = EosDriver()
        call_count = 0
        mock_node = MagicMock()

        def mock_connect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection refused")
            return mock_node

        with patch("network_mcp.driver.pyeapi.connect", side_effect=mock_connect):
            driver._connect_with_retry("10.0.0.1", "https", "admin", "pass")
            assert call_count == 3
            assert driver._node is mock_node

    def test_retry_fails_after_max_attempts(self):
        driver = EosDriver()

        with patch("network_mcp.driver.pyeapi.connect", side_effect=ConnectionError("refused")):
            with pytest.raises(ConnectionError):
                driver._connect_with_retry("10.0.0.1", "https", "admin", "pass")


class TestIosXeConnectionRetry:
    """Test IOS-XE connection retry with tenacity."""

    def test_retry_succeeds_on_third_attempt(self):
        driver = IosXeDriver()
        driver._timeout_show = 30.0
        driver._timeout_connect = 10.0
        call_count = 0

        def mock_init(host, username, password, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection refused")
            driver._connected = True

        with patch.object(driver, "_init_ssh_fallback", side_effect=mock_init):
            driver._connect_with_retry("10.0.0.2", "ssh", "admin", "cisco")
            assert call_count == 3

    def test_retry_fails_after_max_attempts(self):
        driver = IosXeDriver()
        driver._timeout_show = 30.0
        driver._timeout_connect = 10.0

        with patch.object(driver, "_init_ssh_fallback", side_effect=ConnectionError("refused")):
            with pytest.raises(ConnectionError):
                driver._connect_with_retry("10.0.0.2", "ssh", "admin", "cisco")


class TestNxosConnectionRetry:
    """Test NX-OS connection retry with tenacity."""

    def test_retry_succeeds_on_third_attempt(self):
        driver = NxosDriver()
        driver._timeout_show = 30.0
        driver._timeout_connect = 10.0
        call_count = 0

        def mock_init(host, username, password, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection refused")
            driver._connected = True

        with patch.object(driver, "_init_ssh_fallback", side_effect=mock_init):
            driver._connect_with_retry("10.0.0.3", "ssh", "admin", "cisco")
            assert call_count == 3

    def test_retry_fails_after_max_attempts(self):
        driver = NxosDriver()
        driver._timeout_show = 30.0
        driver._timeout_connect = 10.0

        with patch.object(driver, "_init_ssh_fallback", side_effect=ConnectionError("refused")):
            with pytest.raises(ConnectionError):
                driver._connect_with_retry("10.0.0.3", "ssh", "admin", "cisco")


class TestJunosConnectionRetry:
    """Test JunOS connection retry with tenacity."""

    def test_retry_succeeds_on_third_attempt(self):
        driver = JunosDriver()
        driver._timeout_show = 30.0
        driver._timeout_connect = 10.0
        call_count = 0

        def mock_init(host, username, password, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection refused")
            driver._connected = True

        with patch.object(driver, "_init_ssh_fallback", side_effect=mock_init):
            driver._connect_with_retry("10.0.0.4", "ssh", "admin", "juniper")
            assert call_count == 3

    def test_retry_fails_after_max_attempts(self):
        driver = JunosDriver()
        driver._timeout_show = 30.0
        driver._timeout_connect = 10.0

        with patch.object(driver, "_init_ssh_fallback", side_effect=ConnectionError("refused")):
            with pytest.raises(ConnectionError):
                driver._connect_with_retry("10.0.0.4", "ssh", "admin", "juniper")


# ============================================================================
# Keep-alive tests
# ============================================================================


class TestEosKeepAlive:
    """Test EOS keep-alive behavior."""

    def test_no_keepalive_when_recently_active(self):
        """No reconnect attempt if session is recently active."""
        driver = EosDriver()
        driver._node = MagicMock()
        driver._last_command_time = time.monotonic()

        with patch.object(driver, "reconnect") as mock_reconnect:
            driver._check_alive()
            mock_reconnect.assert_not_called()

    def test_keepalive_triggers_on_idle_session(self):
        """Keepalive probe fires when session has been idle."""
        driver = EosDriver()
        driver._node = MagicMock()
        driver._host = "10.0.0.1"
        # Simulate idle session
        driver._last_command_time = time.monotonic() - EOS_KEEPALIVE - 1

        # show hostname succeeds — no reconnect needed
        driver._node.run_commands.return_value = [{"hostname": "eos-01"}]
        with patch.object(driver, "reconnect") as mock_reconnect:
            driver._check_alive()
            mock_reconnect.assert_not_called()
            driver._node.run_commands.assert_called_with(["show hostname"])

    def test_keepalive_reconnects_on_failure(self):
        """Stale session triggers reconnect."""
        driver = EosDriver()
        driver._node = MagicMock()
        driver._host = "10.0.0.1"
        driver._transport_name = "https"
        driver._username = "admin"
        driver._password = "pass"
        driver._last_command_time = time.monotonic() - EOS_KEEPALIVE - 1
        driver._node.run_commands.side_effect = ConnectionError("stale")

        with patch.object(driver, "reconnect") as mock_reconnect:
            driver._check_alive()
            mock_reconnect.assert_called_once()


class TestIosXeKeepAlive:
    """Test IOS-XE keep-alive behavior."""

    def test_no_keepalive_when_recently_active(self):
        driver = IosXeDriver()
        driver._connected = True
        driver._last_command_time = time.monotonic()

        with patch.object(driver, "reconnect") as mock_reconnect:
            driver._check_alive()
            mock_reconnect.assert_not_called()

    def test_keepalive_reconnects_on_restconf_failure(self):
        driver = IosXeDriver()
        driver._connected = True
        driver._ssh_host = "10.0.0.2"
        driver._ssh_username = "admin"
        driver._ssh_password = "cisco"
        driver._last_command_time = time.monotonic() - IOSXE_KEEPALIVE - 1

        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("connection reset")
        driver._restconf = mock_client

        with patch.object(driver, "reconnect") as mock_reconnect:
            driver._check_alive()
            mock_reconnect.assert_called_once()

    def test_keepalive_succeeds_with_restconf(self):
        driver = IosXeDriver()
        driver._connected = True
        driver._last_command_time = time.monotonic() - IOSXE_KEEPALIVE - 1

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_resp
        driver._restconf = mock_client

        with patch.object(driver, "reconnect") as mock_reconnect:
            driver._check_alive()
            mock_reconnect.assert_not_called()


class TestNxosKeepAlive:
    """Test NX-OS keep-alive behavior."""

    def test_no_keepalive_when_recently_active(self):
        driver = NxosDriver()
        driver._connected = True
        driver._last_command_time = time.monotonic()

        with patch.object(driver, "reconnect") as mock_reconnect:
            driver._check_alive()
            mock_reconnect.assert_not_called()

    def test_keepalive_reconnects_on_nxapi_failure(self):
        driver = NxosDriver()
        driver._connected = True
        driver._ssh_host = "10.0.0.3"
        driver._ssh_username = "admin"
        driver._ssh_password = "cisco"
        driver._last_command_time = time.monotonic() - NXOS_KEEPALIVE - 1

        mock_client = MagicMock()
        mock_client.post.side_effect = Exception("connection reset")
        driver._nxapi = mock_client

        with patch.object(driver, "reconnect") as mock_reconnect:
            driver._check_alive()
            mock_reconnect.assert_called_once()


class TestJunosKeepAlive:
    """Test JunOS keep-alive behavior."""

    def test_no_keepalive_when_recently_active(self):
        driver = JunosDriver()
        driver._connected = True
        driver._last_command_time = time.monotonic()

        with patch.object(driver, "reconnect") as mock_reconnect:
            driver._check_alive()
            mock_reconnect.assert_not_called()

    def test_keepalive_reconnects_on_netconf_failure(self):
        driver = JunosDriver()
        driver._connected = True
        driver._host = "10.0.0.4"
        driver._username = "admin"
        driver._password = "juniper"
        driver._last_command_time = time.monotonic() - JUNOS_KEEPALIVE - 1

        mock_conn = MagicMock()
        mock_conn.rpc.side_effect = Exception("NETCONF closed")
        driver._conn = mock_conn

        with patch.object(driver, "reconnect") as mock_reconnect:
            driver._check_alive()
            mock_reconnect.assert_called_once()

    def test_keepalive_succeeds_with_netconf(self):
        driver = JunosDriver()
        driver._connected = True
        driver._last_command_time = time.monotonic() - JUNOS_KEEPALIVE - 1

        mock_conn = MagicMock()
        mock_conn.rpc.return_value = MagicMock(result="<ok/>")
        driver._conn = mock_conn

        with patch.object(driver, "reconnect") as mock_reconnect:
            driver._check_alive()
            mock_reconnect.assert_not_called()


# ============================================================================
# Timeout propagation tests
# ============================================================================


class TestEosTimeoutPropagation:
    """Test EOS timeout propagation from settings."""

    def test_timeout_stored_on_connect(self):
        driver = EosDriver()
        with patch("network_mcp.driver.pyeapi.connect", return_value=MagicMock()):
            driver.connect("10.0.0.1", "https", "admin", "pass", timeout_show=45.0, timeout_config=90.0)
            assert driver._timeout_show == 45.0
            assert driver._timeout_config == 90.0

    def test_default_timeouts(self):
        driver = EosDriver()
        assert driver._timeout_show == 30.0
        assert driver._timeout_config == 60.0


class TestIosXeTimeoutPropagation:
    """Test IOS-XE timeout propagation from settings."""

    def test_timeout_propagated_to_scrapli(self):
        """SSH transport receives timeout settings from driver."""
        driver = IosXeDriver()
        driver._timeout_connect = 15.0
        driver._timeout_show = 45.0
        driver._timeout_config = 90.0

        with (
            patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", True),
            patch("network_mcp.drivers.cisco_iosxe.IosXeScrapliTransport") as mock_transport_cls,
        ):
            driver._init_ssh_fallback("10.0.0.2", "admin", "cisco")
            call_kwargs = mock_transport_cls.call_args[1]
            assert call_kwargs["timeout_connect"] == 15.0
            assert call_kwargs["timeout_ops"] == 45.0

    def test_timeout_propagated_to_restconf(self):
        """RESTCONF client receives timeout from settings."""
        driver = IosXeDriver()
        driver._timeout_show = 45.0

        with patch("network_mcp.drivers.cisco_iosxe.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_resp = MagicMock(status_code=200)
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {}
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            driver._init_restconf("10.0.0.2", "admin", "cisco")
            call_kwargs = mock_client_cls.call_args[1]
            assert call_kwargs["timeout"] == 45.0


class TestNxosTimeoutPropagation:
    """Test NX-OS timeout propagation from settings."""

    def test_timeout_propagated_to_scrapli(self):
        driver = NxosDriver()
        driver._timeout_connect = 15.0
        driver._timeout_show = 45.0

        with (
            patch("network_mcp.drivers.cisco_nxos.HAS_SCRAPLI", True),
            patch("network_mcp.drivers.cisco_nxos.NxosScrapliTransport") as mock_transport_cls,
        ):
            driver._init_ssh_fallback("10.0.0.3", "admin", "cisco")
            call_kwargs = mock_transport_cls.call_args[1]
            assert call_kwargs["timeout_connect"] == 15.0
            assert call_kwargs["timeout_ops"] == 45.0

    def test_timeout_propagated_to_nxapi(self):
        driver = NxosDriver()
        driver._timeout_show = 45.0

        with patch("network_mcp.drivers.cisco_nxos.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_resp = MagicMock(status_code=200)
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {
                "ins_api": {"outputs": {"output": {"code": "200", "msg": "Success", "body": {}}}}
            }
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            driver._init_nxapi("10.0.0.3", "admin", "cisco")
            call_kwargs = mock_client_cls.call_args[1]
            assert call_kwargs["timeout"] == 45.0


class TestJunosTimeoutPropagation:
    """Test JunOS timeout propagation from settings."""

    def test_timeout_propagated_to_netconf(self):
        driver = JunosDriver()
        driver._timeout_connect = 15.0
        driver._timeout_show = 45.0

        with patch("network_mcp.drivers.juniper_junos.NetconfDriver") as mock_netconf_cls:
            mock_conn = MagicMock()
            mock_netconf_cls.return_value = mock_conn

            driver._init_netconf("10.0.0.4", "admin", "juniper")
            call_kwargs = mock_netconf_cls.call_args[1]
            assert call_kwargs["timeout_socket"] == 15.0
            assert call_kwargs["timeout_transport"] == 15.0
            assert call_kwargs["timeout_ops"] == 45.0

    def test_timeout_propagated_to_scrapli(self):
        driver = JunosDriver()
        driver._timeout_connect = 15.0
        driver._timeout_show = 45.0

        with (
            patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI", True),
            patch("network_mcp.drivers.juniper_junos.JunosScrapliTransport") as mock_transport_cls,
        ):
            driver._init_ssh_fallback("10.0.0.4", "admin", "juniper")
            call_kwargs = mock_transport_cls.call_args[1]
            assert call_kwargs["timeout_connect"] == 15.0
            assert call_kwargs["timeout_ops"] == 45.0
