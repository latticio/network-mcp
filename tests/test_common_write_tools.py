"""Tests for vendor-agnostic write tools (interface and routing configuration).

Tests net_configure_interface_description, net_shutdown_interface, net_no_shutdown_interface,
net_create_static_route, and net_configure_bgp_neighbor. Validates vendor-specific command
generation, input validation, read-only rejection, CLI injection prevention, and
multi-vendor interface name validation.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.server import conn_mgr, settings

# --- Fixtures ---


@pytest.fixture(autouse=True)
def _allow_writes():
    """Ensure write operations are allowed for all tests in this module."""
    with patch.object(settings, "net_read_only", False):
        yield


def _make_mock_driver(vendor: str, platform: str) -> MagicMock:
    """Create a mock driver with vendor/platform + run_config support."""
    driver = MagicMock()
    driver.vendor = vendor
    driver.platform = platform
    driver.run_config.return_value = ["ok"]
    driver.run_show = driver.run_commands
    return driver


@contextmanager
def _patch_conn_mgr(driver):
    """Patch conn_mgr.get_driver and acquire to return the given driver."""

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield driver

    with (
        patch.object(conn_mgr, "get_driver", return_value=driver),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield


@pytest.fixture
def eos_driver():
    return _make_mock_driver("arista", "eos")


@pytest.fixture
def iosxe_driver():
    return _make_mock_driver("cisco", "iosxe")


@pytest.fixture
def nxos_driver():
    return _make_mock_driver("cisco", "nxos")


@pytest.fixture
def junos_driver():
    return _make_mock_driver("juniper", "junos")


# ===========================================================================
# validate_multi_vendor_interface_name
# ===========================================================================


class TestValidateMultiVendorInterfaceName:
    """Test the multi-vendor interface name validator."""

    def test_eos_ethernet(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("Ethernet1") is None

    def test_eos_loopback(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("Loopback0") is None

    def test_eos_port_channel(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("Port-Channel10") is None

    def test_eos_vlan(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("Vlan100") is None

    def test_eos_management(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("Management1") is None

    def test_iosxe_gigabit(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("GigabitEthernet0/0/0") is None

    def test_iosxe_ten_gigabit(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("TenGigabitEthernet1/0/1") is None

    def test_iosxe_hundred_gig(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("HundredGigE1/0/1") is None

    def test_iosxe_tunnel(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("Tunnel100") is None

    def test_nxos_port_channel(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("port-channel1") is None

    def test_nxos_loopback(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("loopback0") is None

    def test_nxos_mgmt(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("mgmt0") is None

    def test_junos_ge(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("ge-0/0/0") is None

    def test_junos_xe(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("xe-0/0/1") is None

    def test_junos_et(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("et-0/0/0") is None

    def test_junos_ae(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("ae0") is None

    def test_junos_irb(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        assert validate_multi_vendor_interface_name("irb.100") is None

    def test_empty_interface(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        err = validate_multi_vendor_interface_name("")
        assert err is not None
        assert "required" in err.lower() or "empty" in err.lower()

    def test_invalid_prefix(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        err = validate_multi_vendor_interface_name("InvalidInterface0")
        assert err is not None
        assert "Invalid interface name" in err

    def test_cli_injection_semicolon(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        err = validate_multi_vendor_interface_name("Ethernet1; show run")
        assert err is not None

    def test_cli_injection_pipe(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        err = validate_multi_vendor_interface_name("Ethernet1|show run")
        assert err is not None

    def test_cli_injection_backtick(self):
        from network_mcp.helpers import validate_multi_vendor_interface_name

        err = validate_multi_vendor_interface_name("Ethernet1`cmd`")
        assert err is not None


# ===========================================================================
# net_configure_interface_description
# ===========================================================================


class TestNetConfigureInterfaceDescription:
    """Test net_configure_interface_description across vendors."""

    def test_eos_success(self, eos_driver):
        from network_mcp.tools.common.interfaces import net_configure_interface_description

        with _patch_conn_mgr(eos_driver):
            result = net_configure_interface_description("spine-01", "Ethernet1", "Uplink to core")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["action"] == "net_configure_interface_description"

    def test_eos_commands(self):
        from network_mcp.tools.common.interfaces import _build_interface_description_commands

        cmds = _build_interface_description_commands("eos", "Ethernet1", "Uplink to core")
        assert cmds == ["interface Ethernet1", "description Uplink to core"]

    def test_iosxe_commands(self):
        from network_mcp.tools.common.interfaces import _build_interface_description_commands

        cmds = _build_interface_description_commands("iosxe", "GigabitEthernet0/0", "WAN link")
        assert cmds == ["interface GigabitEthernet0/0", "description WAN link"]

    def test_nxos_commands(self):
        from network_mcp.tools.common.interfaces import _build_interface_description_commands

        cmds = _build_interface_description_commands("nxos", "Ethernet1/1", "Server port")
        assert cmds == ["interface Ethernet1/1", "description Server port"]

    def test_junos_commands(self):
        from network_mcp.tools.common.interfaces import _build_interface_description_commands

        cmds = _build_interface_description_commands("junos", "ge-0/0/0", "Core uplink")
        assert cmds == ['set interfaces ge-0/0/0 description "Core uplink"']

    def test_invalid_interface_name(self):
        from network_mcp.tools.common.interfaces import net_configure_interface_description

        result = net_configure_interface_description("spine-01", "BadInterface", "test")
        assert result["status"] == "error"
        assert "Invalid interface name" in result["error"]

    def test_description_too_long(self):
        from network_mcp.tools.common.interfaces import net_configure_interface_description

        result = net_configure_interface_description("spine-01", "Ethernet1", "x" * 255)
        assert result["status"] == "error"
        assert "254" in result["error"]

    def test_description_injection(self):
        from network_mcp.tools.common.interfaces import net_configure_interface_description

        result = net_configure_interface_description("spine-01", "Ethernet1", "test;show run")
        assert result["status"] == "error"

    def test_iosxe_success(self, iosxe_driver):
        from network_mcp.tools.common.interfaces import net_configure_interface_description

        with _patch_conn_mgr(iosxe_driver):
            result = net_configure_interface_description("router-01", "GigabitEthernet0/0", "WAN link")
        assert result["status"] == "success"

    def test_junos_success(self, junos_driver):
        from network_mcp.tools.common.interfaces import net_configure_interface_description

        with _patch_conn_mgr(junos_driver):
            result = net_configure_interface_description("junos-01", "ge-0/0/0", "Core")
        assert result["status"] == "success"


# ===========================================================================
# net_shutdown_interface
# ===========================================================================


class TestNetShutdownInterface:
    """Test net_shutdown_interface across vendors."""

    def test_eos_success(self, eos_driver):
        from network_mcp.tools.common.interfaces import net_shutdown_interface

        with _patch_conn_mgr(eos_driver):
            result = net_shutdown_interface("spine-01", "Ethernet1")
        assert result["status"] == "success"
        assert result["action"] == "net_shutdown_interface"

    def test_eos_commands(self):
        from network_mcp.tools.common.interfaces import _build_shutdown_commands

        cmds = _build_shutdown_commands("eos", "Ethernet1")
        assert cmds == ["interface Ethernet1", "shutdown"]

    def test_iosxe_commands(self):
        from network_mcp.tools.common.interfaces import _build_shutdown_commands

        cmds = _build_shutdown_commands("iosxe", "GigabitEthernet0/0")
        assert cmds == ["interface GigabitEthernet0/0", "shutdown"]

    def test_nxos_commands(self):
        from network_mcp.tools.common.interfaces import _build_shutdown_commands

        cmds = _build_shutdown_commands("nxos", "Ethernet1/1")
        assert cmds == ["interface Ethernet1/1", "shutdown"]

    def test_junos_commands(self):
        from network_mcp.tools.common.interfaces import _build_shutdown_commands

        cmds = _build_shutdown_commands("junos", "ge-0/0/0")
        assert cmds == ["set interfaces ge-0/0/0 disable"]

    def test_invalid_interface(self):
        from network_mcp.tools.common.interfaces import net_shutdown_interface

        result = net_shutdown_interface("spine-01", "BadName")
        assert result["status"] == "error"
        assert "Invalid interface name" in result["error"]

    def test_empty_interface(self):
        from network_mcp.tools.common.interfaces import net_shutdown_interface

        result = net_shutdown_interface("spine-01", "")
        assert result["status"] == "error"

    def test_cli_injection(self):
        from network_mcp.tools.common.interfaces import net_shutdown_interface

        result = net_shutdown_interface("spine-01", "Ethernet1;reload")
        assert result["status"] == "error"

    def test_junos_success(self, junos_driver):
        from network_mcp.tools.common.interfaces import net_shutdown_interface

        with _patch_conn_mgr(junos_driver):
            result = net_shutdown_interface("junos-01", "ge-0/0/0")
        assert result["status"] == "success"


# ===========================================================================
# net_no_shutdown_interface
# ===========================================================================


class TestNetNoShutdownInterface:
    """Test net_no_shutdown_interface across vendors."""

    def test_eos_success(self, eos_driver):
        from network_mcp.tools.common.interfaces import net_no_shutdown_interface

        with _patch_conn_mgr(eos_driver):
            result = net_no_shutdown_interface("spine-01", "Ethernet1")
        assert result["status"] == "success"
        assert result["action"] == "net_no_shutdown_interface"

    def test_eos_commands(self):
        from network_mcp.tools.common.interfaces import _build_no_shutdown_commands

        cmds = _build_no_shutdown_commands("eos", "Ethernet1")
        assert cmds == ["interface Ethernet1", "no shutdown"]

    def test_iosxe_commands(self):
        from network_mcp.tools.common.interfaces import _build_no_shutdown_commands

        cmds = _build_no_shutdown_commands("iosxe", "GigabitEthernet0/0")
        assert cmds == ["interface GigabitEthernet0/0", "no shutdown"]

    def test_nxos_commands(self):
        from network_mcp.tools.common.interfaces import _build_no_shutdown_commands

        cmds = _build_no_shutdown_commands("nxos", "Ethernet1/1")
        assert cmds == ["interface Ethernet1/1", "no shutdown"]

    def test_junos_commands(self):
        from network_mcp.tools.common.interfaces import _build_no_shutdown_commands

        cmds = _build_no_shutdown_commands("junos", "ge-0/0/0")
        assert cmds == ["delete interfaces ge-0/0/0 disable"]

    def test_invalid_interface(self):
        from network_mcp.tools.common.interfaces import net_no_shutdown_interface

        result = net_no_shutdown_interface("spine-01", "BadName")
        assert result["status"] == "error"

    def test_junos_interface_accepted(self, junos_driver):
        from network_mcp.tools.common.interfaces import net_no_shutdown_interface

        with _patch_conn_mgr(junos_driver):
            result = net_no_shutdown_interface("junos-01", "xe-0/0/1")
        assert result["status"] == "success"

    def test_iosxe_interface_accepted(self, iosxe_driver):
        from network_mcp.tools.common.interfaces import net_no_shutdown_interface

        with _patch_conn_mgr(iosxe_driver):
            result = net_no_shutdown_interface("iosxe-01", "TenGigabitEthernet1/0/1")
        assert result["status"] == "success"

    def test_nxos_interface_accepted(self, nxos_driver):
        from network_mcp.tools.common.interfaces import net_no_shutdown_interface

        with _patch_conn_mgr(nxos_driver):
            result = net_no_shutdown_interface("nxos-01", "port-channel1")
        assert result["status"] == "success"


# ===========================================================================
# net_create_static_route
# ===========================================================================


class TestNetCreateStaticRoute:
    """Test net_create_static_route across vendors."""

    def test_eos_success(self, eos_driver):
        from network_mcp.tools.common.routing import net_create_static_route

        with _patch_conn_mgr(eos_driver):
            result = net_create_static_route("spine-01", "10.0.0.0/24", "10.0.0.1")
        assert result["status"] == "success"
        assert result["action"] == "net_create_static_route"

    def test_eos_commands(self):
        from network_mcp.tools.common.routing import _build_static_route_commands

        cmds = _build_static_route_commands("eos", "10.0.0.0/24", "10.0.0.1", 1, "default")
        assert cmds == ["ip route 10.0.0.0/24 10.0.0.1"]

    def test_eos_commands_with_ad(self):
        from network_mcp.tools.common.routing import _build_static_route_commands

        cmds = _build_static_route_commands("eos", "10.0.0.0/24", "10.0.0.1", 200, "default")
        assert cmds == ["ip route 10.0.0.0/24 10.0.0.1 200"]

    def test_eos_commands_with_vrf(self):
        from network_mcp.tools.common.routing import _build_static_route_commands

        cmds = _build_static_route_commands("eos", "10.0.0.0/24", "10.0.0.1", 1, "MGMT")
        assert cmds == ["ip route vrf MGMT 10.0.0.0/24 10.0.0.1"]

    def test_iosxe_commands_cidr_to_mask(self):
        from network_mcp.tools.common.routing import _build_static_route_commands

        cmds = _build_static_route_commands("iosxe", "10.0.0.0/24", "10.0.0.1", 1, "default")
        assert cmds == ["ip route 10.0.0.0 255.255.255.0 10.0.0.1"]

    def test_iosxe_commands_with_ad(self):
        from network_mcp.tools.common.routing import _build_static_route_commands

        cmds = _build_static_route_commands("iosxe", "10.0.0.0/24", "10.0.0.1", 150, "default")
        assert cmds == ["ip route 10.0.0.0 255.255.255.0 10.0.0.1 150"]

    def test_iosxe_commands_with_vrf(self):
        from network_mcp.tools.common.routing import _build_static_route_commands

        cmds = _build_static_route_commands("iosxe", "10.0.0.0/24", "10.0.0.1", 1, "MGMT")
        assert cmds == ["ip route vrf MGMT 10.0.0.0 255.255.255.0 10.0.0.1"]

    def test_nxos_commands(self):
        from network_mcp.tools.common.routing import _build_static_route_commands

        cmds = _build_static_route_commands("nxos", "10.0.0.0/24", "10.0.0.1", 1, "default")
        assert cmds == ["ip route 10.0.0.0/24 10.0.0.1"]

    def test_junos_commands(self):
        from network_mcp.tools.common.routing import _build_static_route_commands

        cmds = _build_static_route_commands("junos", "10.0.0.0/24", "10.0.0.1", 1, "default")
        assert cmds == ["set routing-options static route 10.0.0.0/24 next-hop 10.0.0.1"]

    def test_junos_commands_with_preference(self):
        from network_mcp.tools.common.routing import _build_static_route_commands

        cmds = _build_static_route_commands("junos", "10.0.0.0/24", "10.0.0.1", 200, "default")
        assert cmds == ["set routing-options static route 10.0.0.0/24 next-hop 10.0.0.1 preference 200"]

    def test_junos_commands_with_vrf(self):
        from network_mcp.tools.common.routing import _build_static_route_commands

        cmds = _build_static_route_commands("junos", "10.0.0.0/24", "10.0.0.1", 1, "MGMT")
        assert cmds == ["set routing-instances MGMT routing-options static route 10.0.0.0/24 next-hop 10.0.0.1"]

    def test_junos_commands_with_vrf_and_preference(self):
        from network_mcp.tools.common.routing import _build_static_route_commands

        cmds = _build_static_route_commands("junos", "10.0.0.0/24", "10.0.0.1", 50, "MGMT")
        assert cmds == [
            "set routing-instances MGMT routing-options static route 10.0.0.0/24 next-hop 10.0.0.1 preference 50"
        ]

    def test_invalid_prefix(self):
        from network_mcp.tools.common.routing import net_create_static_route

        result = net_create_static_route("spine-01", "not-a-prefix", "10.0.0.1")
        assert result["status"] == "error"

    def test_invalid_next_hop(self):
        from network_mcp.tools.common.routing import net_create_static_route

        result = net_create_static_route("spine-01", "10.0.0.0/24", "bad-hop")
        assert result["status"] == "error"

    def test_admin_distance_too_low(self):
        from network_mcp.tools.common.routing import net_create_static_route

        result = net_create_static_route("spine-01", "10.0.0.0/24", "10.0.0.1", admin_distance=0)
        assert result["status"] == "error"
        assert "admin_distance" in result["error"]

    def test_admin_distance_too_high(self):
        from network_mcp.tools.common.routing import net_create_static_route

        result = net_create_static_route("spine-01", "10.0.0.0/24", "10.0.0.1", admin_distance=256)
        assert result["status"] == "error"
        assert "admin_distance" in result["error"]

    def test_vrf_injection(self):
        from network_mcp.tools.common.routing import net_create_static_route

        result = net_create_static_route("spine-01", "10.0.0.0/24", "10.0.0.1", vrf="test;cmd")
        assert result["status"] == "error"

    def test_iosxe_success(self, iosxe_driver):
        from network_mcp.tools.common.routing import net_create_static_route

        with _patch_conn_mgr(iosxe_driver):
            result = net_create_static_route("router-01", "192.168.1.0/24", "10.0.0.1")
        assert result["status"] == "success"

    def test_junos_success(self, junos_driver):
        from network_mcp.tools.common.routing import net_create_static_route

        with _patch_conn_mgr(junos_driver):
            result = net_create_static_route("junos-01", "10.0.0.0/24", "10.0.0.1")
        assert result["status"] == "success"


# ===========================================================================
# CIDR to mask conversion
# ===========================================================================


class TestCidrToMask:
    """Test the _cidr_to_mask helper."""

    def test_slash_24(self):
        from network_mcp.tools.common.routing import _cidr_to_mask

        assert _cidr_to_mask(24) == "255.255.255.0"

    def test_slash_32(self):
        from network_mcp.tools.common.routing import _cidr_to_mask

        assert _cidr_to_mask(32) == "255.255.255.255"

    def test_slash_0(self):
        from network_mcp.tools.common.routing import _cidr_to_mask

        assert _cidr_to_mask(0) == "0.0.0.0"  # noqa: S104

    def test_slash_16(self):
        from network_mcp.tools.common.routing import _cidr_to_mask

        assert _cidr_to_mask(16) == "255.255.0.0"

    def test_slash_8(self):
        from network_mcp.tools.common.routing import _cidr_to_mask

        assert _cidr_to_mask(8) == "255.0.0.0"

    def test_slash_25(self):
        from network_mcp.tools.common.routing import _cidr_to_mask

        assert _cidr_to_mask(25) == "255.255.255.128"

    def test_slash_30(self):
        from network_mcp.tools.common.routing import _cidr_to_mask

        assert _cidr_to_mask(30) == "255.255.255.252"


# ===========================================================================
# net_configure_bgp_neighbor
# ===========================================================================


class TestNetConfigureBgpNeighbor:
    """Test net_configure_bgp_neighbor across vendors."""

    def test_eos_success(self, eos_driver):
        from network_mcp.tools.common.routing import net_configure_bgp_neighbor

        with _patch_conn_mgr(eos_driver):
            result = net_configure_bgp_neighbor("spine-01", 65000, "10.0.0.2", 65001)
        assert result["status"] == "success"
        assert result["action"] == "net_configure_bgp_neighbor"

    def test_eos_commands(self):
        from network_mcp.tools.common.routing import _build_bgp_neighbor_commands

        cmds = _build_bgp_neighbor_commands("eos", 65000, "10.0.0.2", 65001, "")
        assert cmds == ["router bgp 65000", "neighbor 10.0.0.2 remote-as 65001"]

    def test_eos_commands_with_description(self):
        from network_mcp.tools.common.routing import _build_bgp_neighbor_commands

        cmds = _build_bgp_neighbor_commands("eos", 65000, "10.0.0.2", 65001, "Peer to leaf")
        assert cmds == [
            "router bgp 65000",
            "neighbor 10.0.0.2 remote-as 65001",
            "neighbor 10.0.0.2 description Peer to leaf",
        ]

    def test_iosxe_commands(self):
        from network_mcp.tools.common.routing import _build_bgp_neighbor_commands

        cmds = _build_bgp_neighbor_commands("iosxe", 65000, "10.0.0.2", 65001, "")
        assert cmds == ["router bgp 65000", "neighbor 10.0.0.2 remote-as 65001"]

    def test_nxos_commands(self):
        from network_mcp.tools.common.routing import _build_bgp_neighbor_commands

        cmds = _build_bgp_neighbor_commands("nxos", 65000, "10.0.0.2", 65001, "")
        assert cmds == ["router bgp 65000", "neighbor 10.0.0.2", "remote-as 65001"]

    def test_nxos_commands_with_description(self):
        from network_mcp.tools.common.routing import _build_bgp_neighbor_commands

        cmds = _build_bgp_neighbor_commands("nxos", 65000, "10.0.0.2", 65001, "Spine peer")
        assert cmds == ["router bgp 65000", "neighbor 10.0.0.2", "remote-as 65001", "description Spine peer"]

    def test_junos_commands(self):
        from network_mcp.tools.common.routing import _build_bgp_neighbor_commands

        cmds = _build_bgp_neighbor_commands("junos", 65000, "10.0.0.2", 65001, "")
        assert cmds == ["set protocols bgp group AS65001 neighbor 10.0.0.2 peer-as 65001"]

    def test_junos_commands_with_description(self):
        from network_mcp.tools.common.routing import _build_bgp_neighbor_commands

        cmds = _build_bgp_neighbor_commands("junos", 65000, "10.0.0.2", 65001, "eBGP peer")
        assert cmds == [
            "set protocols bgp group AS65001 neighbor 10.0.0.2 peer-as 65001",
            'set protocols bgp group AS65001 neighbor 10.0.0.2 description "eBGP peer"',
        ]

    def test_invalid_neighbor_ip(self):
        from network_mcp.tools.common.routing import net_configure_bgp_neighbor

        result = net_configure_bgp_neighbor("spine-01", 65000, "not-an-ip", 65001)
        assert result["status"] == "error"

    def test_invalid_local_as_zero(self):
        from network_mcp.tools.common.routing import net_configure_bgp_neighbor

        result = net_configure_bgp_neighbor("spine-01", 0, "10.0.0.2", 65001)
        assert result["status"] == "error"
        assert "local_as" in result["error"]

    def test_invalid_remote_as_zero(self):
        from network_mcp.tools.common.routing import net_configure_bgp_neighbor

        result = net_configure_bgp_neighbor("spine-01", 65000, "10.0.0.2", 0)
        assert result["status"] == "error"
        assert "remote_as" in result["error"]

    def test_invalid_remote_as_overflow(self):
        from network_mcp.tools.common.routing import net_configure_bgp_neighbor

        result = net_configure_bgp_neighbor("spine-01", 65000, "10.0.0.2", 4294967296)
        assert result["status"] == "error"
        assert "remote_as" in result["error"]

    def test_description_injection(self):
        from network_mcp.tools.common.routing import net_configure_bgp_neighbor

        result = net_configure_bgp_neighbor("spine-01", 65000, "10.0.0.2", 65001, "test;inject")
        assert result["status"] == "error"

    def test_four_byte_asn(self, eos_driver):
        from network_mcp.tools.common.routing import net_configure_bgp_neighbor

        with _patch_conn_mgr(eos_driver):
            result = net_configure_bgp_neighbor("spine-01", 4200000001, "10.0.0.2", 4200000002)
        assert result["status"] == "success"

    def test_nxos_success(self, nxos_driver):
        from network_mcp.tools.common.routing import net_configure_bgp_neighbor

        with _patch_conn_mgr(nxos_driver):
            result = net_configure_bgp_neighbor("nxos-01", 65000, "10.0.0.2", 65001)
        assert result["status"] == "success"

    def test_junos_success(self, junos_driver):
        from network_mcp.tools.common.routing import net_configure_bgp_neighbor

        with _patch_conn_mgr(junos_driver):
            result = net_configure_bgp_neighbor("junos-01", 65000, "10.0.0.2", 65001)
        assert result["status"] == "success"


# ===========================================================================
# Read-only mode rejection for all write tools
# ===========================================================================


class TestReadOnlyRejection:
    """Verify all write tools are rejected when NET_READ_ONLY=true."""

    def test_description_rejected(self, eos_driver):
        from network_mcp.tools.common.interfaces import net_configure_interface_description

        with patch.object(settings, "net_read_only", True), _patch_conn_mgr(eos_driver):
            result = net_configure_interface_description("spine-01", "Ethernet1", "test")
        assert result["status"] == "error"
        assert "Write operations" in result["error"] or "disabled" in result["error"].lower()

    def test_shutdown_rejected(self, eos_driver):
        from network_mcp.tools.common.interfaces import net_shutdown_interface

        with patch.object(settings, "net_read_only", True), _patch_conn_mgr(eos_driver):
            result = net_shutdown_interface("spine-01", "Ethernet1")
        assert result["status"] == "error"
        assert "Write operations" in result["error"] or "disabled" in result["error"].lower()

    def test_no_shutdown_rejected(self, eos_driver):
        from network_mcp.tools.common.interfaces import net_no_shutdown_interface

        with patch.object(settings, "net_read_only", True), _patch_conn_mgr(eos_driver):
            result = net_no_shutdown_interface("spine-01", "Ethernet1")
        assert result["status"] == "error"
        assert "Write operations" in result["error"] or "disabled" in result["error"].lower()

    def test_static_route_rejected(self, eos_driver):
        from network_mcp.tools.common.routing import net_create_static_route

        with patch.object(settings, "net_read_only", True), _patch_conn_mgr(eos_driver):
            result = net_create_static_route("spine-01", "10.0.0.0/24", "10.0.0.1")
        assert result["status"] == "error"
        assert "Write operations" in result["error"] or "disabled" in result["error"].lower()

    def test_bgp_neighbor_rejected(self, eos_driver):
        from network_mcp.tools.common.routing import net_configure_bgp_neighbor

        with patch.object(settings, "net_read_only", True), _patch_conn_mgr(eos_driver):
            result = net_configure_bgp_neighbor("spine-01", 65000, "10.0.0.2", 65001)
        assert result["status"] == "error"
        assert "Write operations" in result["error"] or "disabled" in result["error"].lower()
