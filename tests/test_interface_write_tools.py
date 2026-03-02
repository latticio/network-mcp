"""Tests for interface configuration write tools (speed, MTU, SVI, trunk)."""

from unittest.mock import patch

import pytest

import network_mcp.helpers as helpers_mod
from network_mcp.config import NetworkSettings


@pytest.fixture(autouse=True)
def _allow_writes():
    """Ensure write operations are allowed for all tests in this module."""
    original = helpers_mod._settings
    helpers_mod._settings = NetworkSettings(net_read_only=False)
    yield
    helpers_mod._settings = original


# --- eos_configure_interface_speed ---


class TestConfigureInterfaceSpeed:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_speed

        mock_node.config.return_value = [{}]
        result = eos_configure_interface_speed("test-switch", "Ethernet1", "1000", "full")

        assert result["status"] == "success"
        assert result["action"] == "configure_interface_speed"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "speed 1000", "duplex full"])

    def test_success_auto_speed(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_speed

        mock_node.config.return_value = [{}]
        result = eos_configure_interface_speed("test-switch", "Ethernet1", "auto")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "speed auto", "duplex auto"])

    def test_success_100g(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_speed

        mock_node.config.return_value = [{}]
        result = eos_configure_interface_speed("test-switch", "Ethernet1", "100000", "full")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "speed 100000", "duplex full"])

    def test_default_duplex_auto(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_speed

        mock_node.config.return_value = [{}]
        result = eos_configure_interface_speed("test-switch", "Ethernet1", "1000")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "speed 1000", "duplex auto"])

    def test_invalid_speed(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_speed

        result = eos_configure_interface_speed("test-switch", "Ethernet1", "999")

        assert result["status"] == "error"
        assert "Invalid speed" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_duplex(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_speed

        result = eos_configure_interface_speed("test-switch", "Ethernet1", "1000", "triple")

        assert result["status"] == "error"
        assert "Invalid duplex" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_speed

        result = eos_configure_interface_speed("test-switch", "bad-intf", "1000")

        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_speed

        with patch("network_mcp.helpers.check_read_only", return_value="Write operations are disabled."):
            result = eos_configure_interface_speed("test-switch", "Ethernet1", "1000")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_configure_interface_mtu ---


class TestConfigureInterfaceMtu:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_mtu

        mock_node.config.return_value = [{}]
        result = eos_configure_interface_mtu("test-switch", "Ethernet1", 9214)

        assert result["status"] == "success"
        assert result["action"] == "configure_interface_mtu"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "mtu 9214"])

    def test_success_standard_mtu(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_mtu

        mock_node.config.return_value = [{}]
        result = eos_configure_interface_mtu("test-switch", "Ethernet1", 1500)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "mtu 1500"])

    def test_success_minimum_mtu(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_mtu

        mock_node.config.return_value = [{}]
        result = eos_configure_interface_mtu("test-switch", "Ethernet1", 68)

        assert result["status"] == "success"

    def test_mtu_too_low(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_mtu

        result = eos_configure_interface_mtu("test-switch", "Ethernet1", 67)

        assert result["status"] == "error"
        assert "MTU must be between 68 and 9214" in result["error"]
        mock_node.config.assert_not_called()

    def test_mtu_too_high(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_mtu

        result = eos_configure_interface_mtu("test-switch", "Ethernet1", 9215)

        assert result["status"] == "error"
        assert "MTU must be between 68 and 9214" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_mtu

        result = eos_configure_interface_mtu("test-switch", "bad-intf", 1500)

        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_port_channel_mtu(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_mtu

        mock_node.config.return_value = [{}]
        result = eos_configure_interface_mtu("test-switch", "Port-Channel10", 9214)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface Port-Channel10", "mtu 9214"])

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_mtu

        with patch("network_mcp.helpers.check_read_only", return_value="Write operations are disabled."):
            result = eos_configure_interface_mtu("test-switch", "Ethernet1", 9214)
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_create_svi ---


class TestCreateSvi:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_create_svi

        mock_node.config.return_value = [{}]
        result = eos_create_svi("test-switch", 100, "10.0.100.1", 24)

        assert result["status"] == "success"
        assert result["action"] == "create_svi"
        mock_node.config.assert_called_once_with(["interface vlan 100", "ip address 10.0.100.1/24", "no shutdown"])

    def test_success_slash_32(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_create_svi

        mock_node.config.return_value = [{}]
        result = eos_create_svi("test-switch", 200, "10.0.200.1", 32)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface vlan 200", "ip address 10.0.200.1/32", "no shutdown"])

    def test_invalid_vlan_id_zero(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_create_svi

        result = eos_create_svi("test-switch", 0, "10.0.100.1", 24)

        assert result["status"] == "error"
        assert "VLAN" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_vlan_id_too_high(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_create_svi

        result = eos_create_svi("test-switch", 4095, "10.0.100.1", 24)

        assert result["status"] == "error"
        assert "VLAN" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_ip_address(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_create_svi

        result = eos_create_svi("test-switch", 100, "not-an-ip", 24)

        assert result["status"] == "error"
        assert "IP address" in result["error"] or "ip_address" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_mask_too_high(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_create_svi

        result = eos_create_svi("test-switch", 100, "10.0.100.1", 33)

        assert result["status"] == "error"
        assert "mask" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_mask_negative(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_create_svi

        result = eos_create_svi("test-switch", 100, "10.0.100.1", -1)

        assert result["status"] == "error"
        assert "mask" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_create_svi

        with patch("network_mcp.helpers.check_read_only", return_value="Write operations are disabled."):
            result = eos_create_svi("test-switch", 100, "10.0.100.1", 24)
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_configure_trunk ---


class TestConfigureTrunk:
    def test_success_all_vlans(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_trunk

        mock_node.config.return_value = [{}]
        result = eos_configure_trunk("test-switch", "Ethernet1")

        assert result["status"] == "success"
        assert result["action"] == "configure_trunk"
        mock_node.config.assert_called_once_with(
            ["interface Ethernet1", "switchport mode trunk", "switchport trunk allowed vlan all"]
        )

    def test_success_specific_vlans(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_trunk

        mock_node.config.return_value = [{}]
        result = eos_configure_trunk("test-switch", "Ethernet1", "100,200,300-400")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "interface Ethernet1",
                "switchport mode trunk",
                "switchport trunk allowed vlan 100,200,300-400",
            ]
        )

    def test_success_with_native_vlan(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_trunk

        mock_node.config.return_value = [{}]
        result = eos_configure_trunk("test-switch", "Ethernet1", "100,200", 100)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "interface Ethernet1",
                "switchport mode trunk",
                "switchport trunk allowed vlan 100,200",
                "switchport trunk native vlan 100",
            ]
        )

    def test_success_port_channel(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_trunk

        mock_node.config.return_value = [{}]
        result = eos_configure_trunk("test-switch", "Port-Channel10", "all", 1)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "interface Port-Channel10",
                "switchport mode trunk",
                "switchport trunk allowed vlan all",
                "switchport trunk native vlan 1",
            ]
        )

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_trunk

        result = eos_configure_trunk("test-switch", "bad-intf", "all")

        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_native_vlan_zero(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_trunk

        result = eos_configure_trunk("test-switch", "Ethernet1", "all", 0)

        assert result["status"] == "error"
        assert "VLAN" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_native_vlan_too_high(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_trunk

        result = eos_configure_trunk("test-switch", "Ethernet1", "all", 4095)

        assert result["status"] == "error"
        assert "VLAN" in result["error"]
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_trunk

        with patch("network_mcp.helpers.check_read_only", return_value="Write operations are disabled."):
            result = eos_configure_trunk("test-switch", "Ethernet1", "all")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- Validator unit tests ---


class TestValidateSpeed:
    def test_valid_speeds(self):
        from network_mcp.helpers import validate_speed

        for speed in ["auto", "10", "100", "1000", "2500", "5000", "10000", "25000", "40000", "50000", "100000"]:
            assert validate_speed(speed) is None

    def test_invalid_speed(self):
        from network_mcp.helpers import validate_speed

        err = validate_speed("999")
        assert err is not None
        assert "Invalid speed" in err


class TestValidateDuplex:
    def test_valid_duplex(self):
        from network_mcp.helpers import validate_duplex

        for duplex in ["auto", "full", "half"]:
            assert validate_duplex(duplex) is None

    def test_invalid_duplex(self):
        from network_mcp.helpers import validate_duplex

        err = validate_duplex("triple")
        assert err is not None
        assert "Invalid duplex" in err


class TestValidateMtu:
    def test_valid_mtu(self):
        from network_mcp.helpers import validate_mtu

        assert validate_mtu(68) is None
        assert validate_mtu(1500) is None
        assert validate_mtu(9214) is None

    def test_mtu_too_low(self):
        from network_mcp.helpers import validate_mtu

        err = validate_mtu(67)
        assert err is not None
        assert "68" in err and "9214" in err

    def test_mtu_too_high(self):
        from network_mcp.helpers import validate_mtu

        err = validate_mtu(9215)
        assert err is not None
        assert "68" in err and "9214" in err
