"""Tests to close coverage gaps in network_mcp/drivers/cisco_nxos.py.

Targets uncovered static parser methods, error/exception branches, and
helper functions:
- _normalize_lldp_detail_nxapi / _normalize_lldp_detail_ssh
- _normalize_trunks_nxapi
- _normalize_vrf_detail_nxapi
- _normalize_ospf_interfaces_nxapi
- _normalize_bgp_neighbors_nxapi
- _parse_ping_output / _parse_traceroute_output / _parse_flap_events
- get_aaa_status / get_user_sessions / get_copp_policy (NotSupportedError)
- get_trunk_interfaces / get_vrf_detail / ping / traceroute / get_interface_flaps (exception paths)
- _parse_speed helper
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.drivers.cisco_nxos import NxosDriver, _parse_speed


def _make_driver(nxapi_return: dict | Exception = None) -> NxosDriver:
    """Build an NxosDriver with mocked NX-API transport."""
    driver = NxosDriver()
    driver._connected = True
    driver._nxapi_base = "https://test-nxos/ins"
    driver._ssh_host = "test-nxos"
    driver._ssh_username = "admin"
    driver._ssh_password = "pass"
    driver._last_command_time = 0.0
    driver._keepalive_interval = 99999
    driver._timeout_show = 30.0

    mock_nxapi = MagicMock()
    if isinstance(nxapi_return, Exception):
        mock_nxapi.post.side_effect = nxapi_return
    elif nxapi_return is not None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": {"body": nxapi_return}}
        mock_resp.raise_for_status.return_value = None
        mock_nxapi.post.return_value = mock_resp
    driver._nxapi = mock_nxapi
    driver._ssh_transport = None
    return driver


# ---------------------------------------------------------------------------
# _normalize_lldp_detail_nxapi (lines 1110-1131)
# ---------------------------------------------------------------------------

class TestNormalizeLldpDetailNxapi:
    def test_basic_normalization(self):
        data = {
            "TABLE_nbor_detail": {
                "ROW_nbor_detail": [
                    {
                        "l_port_id": "Ethernet1/1",
                        "sys_name": "spine-01",
                        "port_id": "Ethernet49",
                        "sys_desc": "Arista EOS",
                        "chassis_id": "00:1c:73:00:01:01",
                        "mgmt_addr": "10.0.0.1",
                        "system_capability": "Bridge, Router",
                    }
                ]
            }
        }
        result = NxosDriver._normalize_lldp_detail_nxapi(data, "")
        assert "Ethernet1/1" in result
        neighbor = result["Ethernet1/1"][0]
        assert neighbor["hostname"] == "spine-01"
        assert neighbor["chassis_id"] == "00:1c:73:00:01:01"
        assert "bridge" in neighbor["capabilities"]
        assert "router" in neighbor["capabilities"]

    def test_filter_by_interface(self):
        data = {
            "TABLE_nbor_detail": {
                "ROW_nbor_detail": [
                    {"l_port_id": "Eth1/1", "sys_name": "sw1", "port_id": "Eth1"},
                    {"l_port_id": "Eth1/2", "sys_name": "sw2", "port_id": "Eth2"},
                ]
            }
        }
        result = NxosDriver._normalize_lldp_detail_nxapi(data, "Eth1/1")
        assert "Eth1/1" in result
        assert "Eth1/2" not in result

    def test_empty_capabilities(self):
        data = {
            "TABLE_nbor_detail": {
                "ROW_nbor_detail": [
                    {"l_port_id": "Eth1/1", "sys_name": "sw1", "system_capability": ""}
                ]
            }
        }
        result = NxosDriver._normalize_lldp_detail_nxapi(data, "")
        assert result["Eth1/1"][0]["capabilities"] == []


# ---------------------------------------------------------------------------
# _normalize_lldp_detail_ssh (lines 1133-1194)
# ---------------------------------------------------------------------------

class TestNormalizeLldpDetailSsh:
    def test_basic_ssh_parsing(self):
        output = (
            "Local Port id: Ethernet1/1\n"
            "Chassis id: 00:1c:73:00:01:01\n"
            "Port id: Ethernet49\n"
            "System Name: spine-01\n"
            "System Description: Arista EOS\n"
            "Management Address: 10.0.0.1\n"
            "System Capabilities: Bridge, Router\n"
        )
        result = NxosDriver._normalize_lldp_detail_ssh(output, "")
        assert "Ethernet1/1" in result
        neighbor = result["Ethernet1/1"][0]
        assert neighbor["hostname"] == "spine-01"
        assert neighbor["chassis_id"] == "00:1c:73:00:01:01"
        assert neighbor["management_address"] == "10.0.0.1"
        assert "bridge" in neighbor["capabilities"]

    def test_multiple_neighbors(self):
        output = (
            "Local Port id: Eth1/1\n"
            "System Name: sw1\n"
            "Port id: Eth1\n"
            "\n"
            "Local Port id: Eth1/2\n"
            "System Name: sw2\n"
            "Port id: Eth2\n"
        )
        result = NxosDriver._normalize_lldp_detail_ssh(output, "")
        assert "Eth1/1" in result
        assert "Eth1/2" in result

    def test_filter_by_interface(self):
        output = (
            "Local Port id: Eth1/1\n"
            "System Name: sw1\n"
            "Port id: Eth1\n"
            "\n"
            "Local Port id: Eth1/2\n"
            "System Name: sw2\n"
            "Port id: Eth2\n"
        )
        result = NxosDriver._normalize_lldp_detail_ssh(output, "Eth1/1")
        assert "Eth1/1" in result
        assert "Eth1/2" not in result

    def test_empty_output(self):
        result = NxosDriver._normalize_lldp_detail_ssh("", "")
        assert result == {}


# ---------------------------------------------------------------------------
# _normalize_trunks_nxapi (lines 1892-1913)
# ---------------------------------------------------------------------------

class TestNormalizeTrunksNxapi:
    def test_basic_trunk_data(self):
        data = {
            "TABLE_interface": {
                "ROW_interface": [
                    {
                        "interface": "Ethernet1/1",
                        "native": "1",
                        "vlans_allowed": "1-100",
                        "vlans_active": "1,10,20",
                    }
                ]
            }
        }
        result = NxosDriver._normalize_trunks_nxapi(data)
        assert len(result) == 1
        assert result[0]["interface"] == "Ethernet1/1"
        assert result[0]["native_vlan"] == 1
        assert result[0]["allowed_vlans"] == "1-100"

    def test_native_vlan_parse_error(self):
        data = {
            "TABLE_interface": {
                "ROW_interface": [{"interface": "Eth1/1", "native": "auto"}]
            }
        }
        result = NxosDriver._normalize_trunks_nxapi(data)
        assert result[0]["native_vlan"] == 1  # default


# ---------------------------------------------------------------------------
# _normalize_vrf_detail_nxapi (lines 1924-1947)
# ---------------------------------------------------------------------------

class TestNormalizeVrfDetailNxapi:
    def test_basic_vrf_detail(self):
        data = {
            "TABLE_vrf": {
                "ROW_vrf": [
                    {
                        "vrf_name": "MGMT",
                        "rd": "65000:100",
                        "vrf_intf": ["mgmt0", "Loopback0"],
                    }
                ]
            }
        }
        result = NxosDriver._normalize_vrf_detail_nxapi("MGMT", data)
        assert result["name"] == "MGMT"
        assert result["rd"] == "65000:100"
        assert result["interfaces"] == ["mgmt0", "Loopback0"]

    def test_vrf_detail_rd_dash(self):
        data = {"TABLE_vrf": {"ROW_vrf": [{"vrf_name": "TEST", "rd": "--"}]}}
        result = NxosDriver._normalize_vrf_detail_nxapi("TEST", data)
        assert result["rd"] == ""

    def test_vrf_detail_intf_string(self):
        data = {
            "TABLE_vrf": {
                "ROW_vrf": [{"vrf_name": "TEST", "vrf_intf": "mgmt0, Loopback0"}]
            }
        }
        result = NxosDriver._normalize_vrf_detail_nxapi("TEST", data)
        assert "mgmt0" in result["interfaces"]
        assert "Loopback0" in result["interfaces"]

    def test_vrf_detail_empty_rows(self):
        data = {"TABLE_vrf": {"ROW_vrf": []}}
        result = NxosDriver._normalize_vrf_detail_nxapi("TEST", data)
        assert result == {}


# ---------------------------------------------------------------------------
# _normalize_ospf_interfaces_nxapi (lines 1479-1495)
# ---------------------------------------------------------------------------

class TestNormalizeOspfInterfacesNxapi:
    def test_basic_ospf_interfaces(self):
        data = {
            "TABLE_ctx": {
                "ROW_ctx": [
                    {
                        "TABLE_intf": {
                            "ROW_intf": [
                                {
                                    "ifname": "Ethernet1/1",
                                    "area": "0.0.0.0",  # noqa: S104
                                    "type": "P2P",
                                    "cost": 40,
                                    "nbr_adjs": 1,
                                }
                            ]
                        }
                    }
                ]
            }
        }
        result = NxosDriver._normalize_ospf_interfaces_nxapi(data)
        assert len(result) == 1
        assert result[0]["interface"] == "Ethernet1/1"
        assert result[0]["area"] == "0.0.0.0"  # noqa: S104
        assert result[0]["cost"] == 40


# ---------------------------------------------------------------------------
# _normalize_bgp_neighbors_nxapi (lines 1507-1523)
# ---------------------------------------------------------------------------

class TestNormalizeBgpNeighborsNxapi:
    def test_basic_bgp_neighbors(self):
        data = {
            "TABLE_neighbor": {
                "ROW_neighbor": [
                    {
                        "neighbor-id": "10.0.0.1",
                        "remoteas": 65001,
                        "state": "Established",
                        "prefixesrcvd": 100,
                        "prefixessent": 50,
                        "uptimeepoch": "1d2h",
                        "description": "spine-01",
                    }
                ]
            }
        }
        result = NxosDriver._normalize_bgp_neighbors_nxapi(data)
        assert len(result) == 1
        assert result[0]["neighbor"] == "10.0.0.1"
        assert result[0]["remote_as"] == 65001


# ---------------------------------------------------------------------------
# _parse_ping_output (lines 1970-1994)
# ---------------------------------------------------------------------------

class TestParsePingOutput:
    def test_successful_ping(self):
        output = (
            "5 packets transmitted, 5 received, 0% packet loss\n"
            "round-trip min/avg/max = 1/2/4 ms"
        )
        result = NxosDriver._parse_ping_output(output, 5)
        assert result["success"] is True
        assert result["rtt_min"] == 1.0
        assert result["rtt_avg"] == 2.0
        assert result["rtt_max"] == 4.0

    def test_failed_ping(self):
        output = "5 packets transmitted, 0 received, 100% packet loss"
        result = NxosDriver._parse_ping_output(output, 5)
        assert result["success"] is False

    def test_no_match(self):
        result = NxosDriver._parse_ping_output("garbage", 5)
        assert result["success"] is False


# ---------------------------------------------------------------------------
# _parse_traceroute_output (lines 2007-2021)
# ---------------------------------------------------------------------------

class TestParseTracerouteOutput:
    def test_successful_traceroute(self):
        output = " 1  10.0.0.254 1.234 ms\n 2  10.0.0.1 2.345 ms\n"
        result = NxosDriver._parse_traceroute_output(output)
        assert len(result["hops"]) == 2

    def test_traceroute_with_stars(self):
        output = " 1  * * *\n"
        result = NxosDriver._parse_traceroute_output(output)
        assert result["hops"][0]["address"] == "*"

    def test_empty_output(self):
        result = NxosDriver._parse_traceroute_output("")
        assert result["hops"] == []


# ---------------------------------------------------------------------------
# _parse_flap_events (lines 2031-2048)
# ---------------------------------------------------------------------------

class TestParseFlapEvents:
    def test_basic_flap_parsing(self):
        output = (
            "2024 Jan 01 10:30:00 Ethernet1/1 changed state to down\n"
            "2024 Jan 01 10:31:00 Ethernet1/1 changed state to up\n"
        )
        result = NxosDriver._parse_flap_events(output)
        assert len(result) == 2
        assert result[0]["event"] == "link down"
        assert result[1]["event"] == "link up"

    def test_link_status_changed(self):
        output = "Vlan100 link status changed to up\n"
        result = NxosDriver._parse_flap_events(output)
        assert len(result) == 1

    def test_unknown_interface(self):
        output = "changed state to up on something unknown\n"
        result = NxosDriver._parse_flap_events(output)
        assert len(result) == 1
        assert result[0]["interface"] == "unknown"

    def test_no_matching_events(self):
        output = "2024 Jan 01 10:30:00 Some other log message\n"
        result = NxosDriver._parse_flap_events(output)
        assert result == []


# ---------------------------------------------------------------------------
# NotSupportedError methods (lines 2050-2060)
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

    def test_ping_exception(self):
        driver = _make_driver(RuntimeError("connection refused"))
        result = driver.ping("10.0.0.1", count=3)
        assert result["success"] is False
        assert "connection refused" in result["output"]

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


# ---------------------------------------------------------------------------
# _parse_speed helper (lines 2066-2094)
# ---------------------------------------------------------------------------

class TestParseSpeed:
    def test_int_kbit(self):
        assert _parse_speed(10000) == 10

    def test_int_below_1000(self):
        assert _parse_speed(100) == 100

    def test_string_gb(self):
        assert _parse_speed("10 Gb/s") == 10000

    def test_string_mb(self):
        assert _parse_speed("1000 Mb/s") == 1000

    def test_string_auto(self):
        assert _parse_speed("auto") == 0

    def test_string_dash(self):
        assert _parse_speed("--") == 0

    def test_string_empty(self):
        assert _parse_speed("") == 0

    def test_string_plain_int_kbit(self):
        assert _parse_speed("40000") == 40

    def test_string_plain_int_below_1000(self):
        assert _parse_speed("100") == 100

    def test_string_invalid(self):
        assert _parse_speed("unknown") == 0

    def test_other_type(self):
        assert _parse_speed(None) == 0
