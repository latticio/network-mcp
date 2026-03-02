"""Tests to close coverage gaps in network_mcp/drivers/juniper_junos.py.

Targets uncovered static parser methods, error/exception paths, and
the NETCONF-based getters:
- _normalize_lldp_detail / _normalize_lldp_detail_ssh
- _normalize_trunks
- _normalize_ospf_interfaces
- _normalize_bgp_neighbors
- _parse_ping_output / _parse_traceroute_output / _parse_flap_events
- get_aaa_status / get_user_sessions / get_copp_policy (NotSupportedError)
- Exception paths for all getter methods
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.drivers.juniper_junos import JunosDriver


def _make_driver(netconf_return: str | Exception = "") -> JunosDriver:
    """Build a JunosDriver with mocked NETCONF/SSH transport."""
    driver = JunosDriver()
    driver._connected = True
    driver._host = "test-junos"
    driver._username = "admin"
    driver._password = "pass"
    driver._last_command_time = time.monotonic()
    driver._keepalive_interval = 99999

    mock_conn = MagicMock()
    if isinstance(netconf_return, Exception):
        mock_conn.rpc.side_effect = netconf_return
    else:
        mock_resp = MagicMock()
        mock_resp.result = netconf_return
        mock_conn.rpc.return_value = mock_resp

    driver._conn = mock_conn
    driver._ssh_transport = None
    return driver


# ---------------------------------------------------------------------------
# _normalize_lldp_detail (lines 1168-1212) - NETCONF XML
# ---------------------------------------------------------------------------

class TestNormalizeLldpDetail:
    def test_basic_netconf_parsing(self):
        cap_tag = "lldp-remote-system-capabilities-supported"
        xml = f"""
        <rpc-reply>
            <lldp-neighbors-information>
                <lldp-neighbor-information>
                    <lldp-local-port-id>ge-0/0/0</lldp-local-port-id>
                    <lldp-remote-system-name>spine-01</lldp-remote-system-name>
                    <lldp-remote-port-id>Ethernet49</lldp-remote-port-id>
                    <lldp-remote-system-description>Arista EOS</lldp-remote-system-description>
                    <lldp-remote-chassis-id>00:1c:73:00:01:01</lldp-remote-chassis-id>
                    <lldp-remote-management-address>10.0.0.1</lldp-remote-management-address>
                    <{cap_tag}>Bridge, Router</{cap_tag}>
                </lldp-neighbor-information>
            </lldp-neighbors-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_lldp_detail(xml, "")
        assert "ge-0/0/0" in result
        neighbor = result["ge-0/0/0"][0]
        assert neighbor["hostname"] == "spine-01"
        assert neighbor["chassis_id"] == "00:1c:73:00:01:01"
        assert "bridge" in neighbor["capabilities"]
        assert "router" in neighbor["capabilities"]

    def test_filter_by_interface(self):
        xml = """
        <rpc-reply>
            <lldp-neighbors-information>
                <lldp-neighbor-information>
                    <lldp-local-port-id>ge-0/0/0</lldp-local-port-id>
                    <lldp-remote-system-name>sw1</lldp-remote-system-name>
                    <lldp-remote-port-id>Eth1</lldp-remote-port-id>
                </lldp-neighbor-information>
                <lldp-neighbor-information>
                    <lldp-local-port-id>ge-0/0/1</lldp-local-port-id>
                    <lldp-remote-system-name>sw2</lldp-remote-system-name>
                    <lldp-remote-port-id>Eth2</lldp-remote-port-id>
                </lldp-neighbor-information>
            </lldp-neighbors-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_lldp_detail(xml, "ge-0/0/0")
        assert "ge-0/0/0" in result
        assert "ge-0/0/1" not in result

    def test_no_local_port_id_uses_local_interface(self):
        xml = """
        <rpc-reply>
            <lldp-neighbors-information>
                <lldp-neighbor-information>
                    <lldp-local-interface>ge-0/0/0</lldp-local-interface>
                    <lldp-remote-system-name>sw1</lldp-remote-system-name>
                    <lldp-remote-port-description>Eth1</lldp-remote-port-description>
                </lldp-neighbor-information>
            </lldp-neighbors-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_lldp_detail(xml, "")
        assert "ge-0/0/0" in result
        assert result["ge-0/0/0"][0]["port"] == "Eth1"

    def test_no_local_intf_skips(self):
        xml = """
        <rpc-reply>
            <lldp-neighbors-information>
                <lldp-neighbor-information>
                    <lldp-remote-system-name>sw1</lldp-remote-system-name>
                </lldp-neighbor-information>
            </lldp-neighbors-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_lldp_detail(xml, "")
        assert result == {}

    def test_no_capabilities(self):
        xml = """
        <rpc-reply>
            <lldp-neighbors-information>
                <lldp-neighbor-information>
                    <lldp-local-port-id>ge-0/0/0</lldp-local-port-id>
                    <lldp-remote-system-name>sw1</lldp-remote-system-name>
                </lldp-neighbor-information>
            </lldp-neighbors-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_lldp_detail(xml, "")
        assert result["ge-0/0/0"][0]["capabilities"] == []


# ---------------------------------------------------------------------------
# _normalize_lldp_detail_ssh (lines 1214-1275)
# ---------------------------------------------------------------------------

class TestNormalizeLldpDetailSsh:
    def test_basic_ssh_parsing(self):
        output = (
            "Local Interface: ge-0/0/0\n"
            "Chassis ID: 00:1c:73:00:01:01\n"
            "Port ID: Ethernet49\n"
            "System Name: spine-01\n"
            "System Description: Arista EOS\n"
            "Management Address: 10.0.0.1\n"
            "System Capabilities: Bridge, Router\n"
        )
        result = JunosDriver._normalize_lldp_detail_ssh(output, "")
        assert "ge-0/0/0" in result
        neighbor = result["ge-0/0/0"][0]
        assert neighbor["hostname"] == "spine-01"
        assert "bridge" in neighbor["capabilities"]

    def test_local_port_id_prefix(self):
        output = (
            "Local Port ID: ge-0/0/0\n"
            "System Name: sw1\n"
            "Port ID: Eth1\n"
        )
        result = JunosDriver._normalize_lldp_detail_ssh(output, "")
        assert "ge-0/0/0" in result

    def test_filter_by_interface(self):
        output = (
            "Local Interface: ge-0/0/0\n"
            "System Name: sw1\n"
            "Port ID: Eth1\n"
            "\n"
            "Local Interface: ge-0/0/1\n"
            "System Name: sw2\n"
            "Port ID: Eth2\n"
        )
        result = JunosDriver._normalize_lldp_detail_ssh(output, "ge-0/0/0")
        assert "ge-0/0/0" in result
        assert "ge-0/0/1" not in result

    def test_empty_output(self):
        result = JunosDriver._normalize_lldp_detail_ssh("", "")
        assert result == {}


# ---------------------------------------------------------------------------
# _normalize_trunks (lines 2241-2272)
# ---------------------------------------------------------------------------

class TestNormalizeTrunks:
    def test_basic_trunk_parsing(self):
        xml = """
        <rpc-reply>
            <switching-interface-information>
                <interface>
                    <interface-name>ge-0/0/0</interface-name>
                    <interface-port-mode>Trunk</interface-port-mode>
                    <interface-native-vlan>1</interface-native-vlan>
                    <interface-vlan-member>
                        <interface-vlan-member-tagid>10</interface-vlan-member-tagid>
                    </interface-vlan-member>
                    <interface-vlan-member>
                        <interface-vlan-member-tagid>20</interface-vlan-member-tagid>
                    </interface-vlan-member>
                </interface>
            </switching-interface-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_trunks(xml)
        assert len(result) == 1
        assert result[0]["interface"] == "ge-0/0/0"
        assert result[0]["mode"] == "trunk"
        assert result[0]["native_vlan"] == 1
        assert "10" in result[0]["allowed_vlans"]
        assert "20" in result[0]["allowed_vlans"]

    def test_access_mode_skipped(self):
        xml = """
        <rpc-reply>
            <switching-interface-information>
                <interface>
                    <interface-name>ge-0/0/0</interface-name>
                    <interface-port-mode>Access</interface-port-mode>
                </interface>
            </switching-interface-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_trunks(xml)
        assert result == []

    def test_no_vlan_members(self):
        xml = """
        <rpc-reply>
            <switching-interface-information>
                <interface>
                    <interface-name>ge-0/0/0</interface-name>
                    <interface-port-mode>Trunk</interface-port-mode>
                </interface>
            </switching-interface-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_trunks(xml)
        assert result[0]["allowed_vlans"] == "all"

    def test_native_vlan_parse_error(self):
        xml = """
        <rpc-reply>
            <switching-interface-information>
                <interface>
                    <interface-name>ge-0/0/0</interface-name>
                    <interface-port-mode>Trunk</interface-port-mode>
                    <interface-native-vlan>none</interface-native-vlan>
                </interface>
            </switching-interface-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_trunks(xml)
        assert result[0]["native_vlan"] == 0


# ---------------------------------------------------------------------------
# _normalize_ospf_interfaces (lines 1623-1647)
# ---------------------------------------------------------------------------

class TestNormalizeOspfInterfaces:
    def test_basic_ospf_interfaces(self):
        xml = """
        <rpc-reply>
            <ospf-interface-information>
                <ospf-interface>
                    <interface-name>ge-0/0/0.0</interface-name>
                    <ospf-area>0.0.0.0</ospf-area>
                    <interface-type>p2p</interface-type>
                    <interface-cost>10</interface-cost>
                    <neighbor-count>1</neighbor-count>
                </ospf-interface>
            </ospf-interface-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_ospf_interfaces(xml)
        assert len(result) == 1
        assert result[0]["interface"] == "ge-0/0/0.0"
        assert result[0]["area"] == "0.0.0.0"  # noqa: S104
        assert result[0]["cost"] == 10

    def test_no_interface_name_skipped(self):
        xml = """
        <rpc-reply>
            <ospf-interface-information>
                <ospf-interface>
                    <ospf-area>0.0.0.0</ospf-area>
                </ospf-interface>
            </ospf-interface-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_ospf_interfaces(xml)
        assert result == []


# ---------------------------------------------------------------------------
# _normalize_bgp_neighbors (lines 1662-1691)
# ---------------------------------------------------------------------------

class TestNormalizeBgpNeighbors:
    def test_basic_bgp_neighbors(self):
        xml = """
        <rpc-reply>
            <bgp-information>
                <bgp-peer>
                    <peer-address>10.0.0.1+179</peer-address>
                    <peer-as>65001</peer-as>
                    <peer-state>established</peer-state>
                    <bgp-rib>
                        <received-prefix-count>100</received-prefix-count>
                        <advertised-prefix-count>50</advertised-prefix-count>
                    </bgp-rib>
                    <elapsed-time>1d2h</elapsed-time>
                    <description>spine-01</description>
                </bgp-peer>
            </bgp-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_bgp_neighbors(xml)
        assert len(result) == 1
        assert result[0]["neighbor"] == "10.0.0.1"  # port stripped
        assert result[0]["remote_as"] == 65001
        assert result[0]["state"] == "Established"

    def test_peer_without_port(self):
        xml = """
        <rpc-reply>
            <bgp-information>
                <bgp-peer>
                    <peer-address>10.0.0.2</peer-address>
                    <peer-as>65002</peer-as>
                    <peer-state>active</peer-state>
                </bgp-peer>
            </bgp-information>
        </rpc-reply>
        """
        result = JunosDriver._normalize_bgp_neighbors(xml)
        assert result[0]["neighbor"] == "10.0.0.2"
        assert result[0]["state"] == "Active"


# ---------------------------------------------------------------------------
# _parse_ping_output (lines 2317-2343)
# ---------------------------------------------------------------------------

class TestParsePingOutput:
    def test_successful_ping(self):
        output = (
            "PING 10.0.0.1 (10.0.0.1): 56 data bytes\n"
            "5 packets transmitted, 5 packets received, 0% packet loss\n"
            "round-trip min/avg/max/stddev = 0.123/0.456/0.789/0.012 ms"
        )
        result = JunosDriver._parse_ping_output(output, 5)
        assert result["success"] is True
        assert result["packets_sent"] == 5
        assert result["packets_received"] == 5
        assert result["rtt_min"] == 0.123
        assert result["rtt_avg"] == 0.456
        assert result["rtt_max"] == 0.789

    def test_failed_ping(self):
        output = "5 packets transmitted, 0 packets received, 100% packet loss"
        result = JunosDriver._parse_ping_output(output, 5)
        assert result["success"] is False

    def test_no_match(self):
        result = JunosDriver._parse_ping_output("garbage", 5)
        assert result["success"] is False


# ---------------------------------------------------------------------------
# _parse_traceroute_output (lines 2359-2373)
# ---------------------------------------------------------------------------

class TestParseTracerouteOutput:
    def test_successful_traceroute(self):
        output = " 1  10.0.0.254 1.234 ms\n 2  10.0.0.1 2.345 ms\n"
        result = JunosDriver._parse_traceroute_output(output)
        assert len(result["hops"]) == 2

    def test_traceroute_with_stars(self):
        output = " 1  * * *\n"
        result = JunosDriver._parse_traceroute_output(output)
        assert result["hops"][0]["address"] == "*"

    def test_empty_output(self):
        result = JunosDriver._parse_traceroute_output("")
        assert result["hops"] == []


# ---------------------------------------------------------------------------
# _parse_flap_events (lines 2384-2401)
# ---------------------------------------------------------------------------

class TestParseFlapEvents:
    def test_link_up_down(self):
        output = (
            "Jan  1 10:30:00 rpd ge-0/0/0 LINK_DOWN\n"
            "Jan  1 10:31:00 rpd ge-0/0/0 LINK_UP\n"
        )
        result = JunosDriver._parse_flap_events(output)
        assert len(result) == 2
        assert result[0]["event"] == "link down"
        assert result[0]["interface"] == "ge-0/0/0"
        assert result[1]["event"] == "link up"

    def test_changed_state(self):
        output = "Jan  1 10:30:00 ae0 changed state to down\n"
        result = JunosDriver._parse_flap_events(output)
        assert len(result) == 1
        assert result[0]["event"] == "link down"

    def test_unknown_interface(self):
        output = "LINK_UP on some unknown interface\n"
        result = JunosDriver._parse_flap_events(output)
        assert len(result) == 1
        assert result[0]["interface"] == "unknown"

    def test_no_matching_events(self):
        output = "Jan  1 10:30:00 Some other log message\n"
        result = JunosDriver._parse_flap_events(output)
        assert result == []


# ---------------------------------------------------------------------------
# NotSupportedError methods (lines 2403-2413)
# ---------------------------------------------------------------------------

class TestNotSupportedMethods:
    def test_get_aaa_status_raises(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError, match="get_aaa_status"):
            driver.get_aaa_status()

    def test_get_user_sessions_raises(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError, match="get_user_sessions"):
            driver.get_user_sessions()

    def test_get_copp_policy_raises(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError, match="get_copp_policy"):
            driver.get_copp_policy()


# ---------------------------------------------------------------------------
# Exception paths for getter methods
# ---------------------------------------------------------------------------

class TestExceptionPaths:
    def test_get_trunk_interfaces_exception(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_trunk_interfaces()
        assert result == []

    def test_get_vrf_detail_exception(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_vrf_detail("MGMT")
        assert result == {}

    def test_get_vrf_detail_empty_result(self):
        """When _normalize_vrfs returns empty, get_vrf_detail returns {}."""
        xml = "<rpc-reply><instance-information></instance-information></rpc-reply>"
        driver = _make_driver(xml)
        result = driver.get_vrf_detail("NONEXISTENT")
        assert result == {}

    def test_ping_exception(self):
        """When NETCONF RPC fails, _netconf_show returns error dict, ping returns failure."""
        driver = _make_driver(RuntimeError("connection refused"))
        result = driver.ping("10.0.0.1", count=3)
        assert result["success"] is False

    def test_ping_run_show_raises(self):
        """When run_show itself raises, ping catches the exception (lines 2304-2314)."""
        from unittest.mock import patch

        driver = _make_driver("")
        with patch.object(driver, "run_show", side_effect=RuntimeError("total failure")):
            result = driver.ping("10.0.0.1", count=3)
        assert result["success"] is False
        assert "total failure" in result["output"]

    def test_traceroute_exception(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.traceroute("10.0.0.1")
        assert result["hops"] == []

    def test_get_interface_flaps_exception(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_interface_flaps()
        assert result == []

    def test_get_ospf_interfaces_exception(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_ospf_interfaces()
        assert result == []

    def test_get_bgp_neighbors_exception(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_bgp_neighbors()
        assert result == []

    def test_get_lldp_neighbor_detail_exception_falls_to_ssh(self):
        """When NETCONF fails, should fall back to SSH."""
        driver = _make_driver(RuntimeError("NETCONF error"))

        # Set up SSH transport mock
        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = (
            "Local Interface: ge-0/0/0\n"
            "System Name: sw1\n"
            "Port ID: Eth1\n"
        )
        driver._ssh_transport = mock_ssh
        result = driver.get_lldp_neighbor_detail()
        assert "ge-0/0/0" in result


# ---------------------------------------------------------------------------
# ping with vrf and source options
# ---------------------------------------------------------------------------

class TestPingOptions:
    def test_ping_with_vrf_and_source(self):
        driver = _make_driver()
        # Mock run_show to return text output
        ping_out = "5 packets transmitted, 5 packets received, 0% packet loss"
        driver.run_show = MagicMock(return_value=[{"output": ping_out}])
        result = driver.ping("10.0.0.1", count=3, source="lo0", vrf="MGMT")
        assert result["success"] is True
        call_cmd = driver.run_show.call_args[0][0][0]
        assert "source lo0" in call_cmd
        assert "routing-instance MGMT" in call_cmd


# ---------------------------------------------------------------------------
# traceroute with vrf and source options
# ---------------------------------------------------------------------------

class TestTracerouteOptions:
    def test_traceroute_with_vrf_and_source(self):
        driver = _make_driver()
        driver.run_show = MagicMock(return_value=[{"output": " 1  10.0.0.254 1.0 ms\n"}])
        driver.traceroute("10.0.0.1", source="lo0", vrf="MGMT")
        call_cmd = driver.run_show.call_args[0][0][0]
        assert "source lo0" in call_cmd
        assert "routing-instance MGMT" in call_cmd
