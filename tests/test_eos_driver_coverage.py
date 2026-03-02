"""Tests to close coverage gaps in network_mcp/driver.py (EosDriver).

Covers: get_lldp_neighbor_detail, get_trunk_interfaces, get_vrf_detail,
ping, _parse_ping_output, traceroute, _parse_traceroute_output,
get_interface_flaps, _parse_flap_events, get_ospf_interfaces,
get_bgp_neighbors, get_aaa_status, get_user_sessions, get_copp_policy,
get_config_section (str-return branch), get_startup_config (str-return branch),
and as_number ValueError branch.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from network_mcp.driver import EosDriver
from network_mcp.drivers.base import NotSupportedError


def _make_driver(run_commands_return: list[dict] | Exception, encoding: str = "json") -> EosDriver:
    """Build an EosDriver with a mocked pyeapi node."""
    driver = EosDriver()
    node = MagicMock()
    if isinstance(run_commands_return, Exception):
        node.run_commands.side_effect = run_commands_return
    else:
        node.run_commands.return_value = run_commands_return
    driver._node = node
    driver._host = "test-switch"
    driver._last_command_time = time.monotonic()  # prevent keepalive check
    driver._keepalive_interval = 99999
    return driver


# ---------------------------------------------------------------------------
# get_lldp_neighbor_detail
# ---------------------------------------------------------------------------

class TestGetLldpNeighborDetail:
    def test_returns_detail_for_all_interfaces(self):
        mock_response = {
            "lldpNeighbors": {
                "Ethernet1": {
                    "lldpNeighborInfo": [
                        {
                            "systemName": "spine-01",
                            "neighborInterfaceInfo": {"interfaceId_v2": "Ethernet49"},
                            "systemDescription": "Arista EOS",
                            "chassisId": "00:1c:73:00:00:01",
                            "managementAddress": "10.0.0.1",
                            "systemCapabilities": {"bridge": True, "router": True, "wlan": False},
                        }
                    ]
                }
            }
        }
        driver = _make_driver([mock_response])
        result = driver.get_lldp_neighbor_detail()
        assert "Ethernet1" in result
        detail = result["Ethernet1"][0]
        assert detail["hostname"] == "spine-01"
        assert detail["chassis_id"] == "00:1c:73:00:00:01"
        assert detail["management_address"] == "10.0.0.1"
        assert "bridge" in detail["capabilities"]
        assert "router" in detail["capabilities"]
        assert "wlan" not in detail["capabilities"]

    def test_filter_by_interface(self):
        mock_response = {
            "lldpNeighbors": {
                "Ethernet1": {
                    "lldpNeighborInfo": [
                        {"systemName": "spine-01", "neighborInterfaceInfo": {"interfaceId_v2": "Ethernet49"}}
                    ]
                },
                "Ethernet2": {
                    "lldpNeighborInfo": [
                        {"systemName": "spine-02", "neighborInterfaceInfo": {"interfaceId_v2": "Ethernet50"}}
                    ]
                },
            }
        }
        driver = _make_driver([mock_response])
        result = driver.get_lldp_neighbor_detail(interface="Ethernet1")
        assert "Ethernet1" in result
        assert "Ethernet2" not in result

    def test_neighbor_info_as_dict(self):
        """When lldpNeighborInfo is a dict (not list), it should be wrapped."""
        mock_response = {
            "lldpNeighbors": {
                "Ethernet1": {
                    "lldpNeighborInfo": {
                        "systemName": "leaf-01",
                        "portId": "ge-0/0/0",
                        "systemCapabilities": {},
                    }
                }
            }
        }
        driver = _make_driver([mock_response])
        result = driver.get_lldp_neighbor_detail()
        assert "Ethernet1" in result
        assert result["Ethernet1"][0]["hostname"] == "leaf-01"
        assert result["Ethernet1"][0]["port"] == "ge-0/0/0"

    def test_empty_neighbors(self):
        driver = _make_driver([{"lldpNeighbors": {}}])
        result = driver.get_lldp_neighbor_detail()
        assert result == {}


# ---------------------------------------------------------------------------
# get_trunk_interfaces
# ---------------------------------------------------------------------------

class TestGetTrunkInterfaces:
    def test_returns_trunk_info(self):
        mock_response = {
            "trunkGroups": {
                "Ethernet1": {
                    "nativeVlan": 1,
                    "allowedVlans": "1-100",
                    "activeVlans": "1,10,20",
                }
            }
        }
        driver = _make_driver([mock_response])
        result = driver.get_trunk_interfaces()
        assert len(result) == 1
        assert result[0]["interface"] == "Ethernet1"
        assert result[0]["mode"] == "trunk"
        assert result[0]["native_vlan"] == 1
        assert result[0]["allowed_vlans"] == "1-100"
        assert result[0]["active_vlans"] == "1,10,20"

    def test_empty_trunk_groups(self):
        driver = _make_driver([{"trunkGroups": {}}])
        result = driver.get_trunk_interfaces()
        assert result == []

    def test_exception_returns_empty(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_trunk_interfaces()
        assert result == []


# ---------------------------------------------------------------------------
# get_vrf_detail
# ---------------------------------------------------------------------------

class TestGetVrfDetail:
    def test_returns_vrf_detail(self):
        mock_response = {
            "vrfs": {
                "MGMT": {
                    "routeDistinguisher": "1:1",
                    "interfaces": ["Management1"],
                    "routeCount": 5,
                    "importRts": ["65000:100"],
                    "exportRts": ["65000:200"],
                }
            }
        }
        driver = _make_driver([mock_response])
        result = driver.get_vrf_detail("MGMT")
        assert result["name"] == "MGMT"
        assert result["rd"] == "1:1"
        assert result["interfaces"] == ["Management1"]
        assert result["route_count"] == 5
        assert result["import_targets"] == ["65000:100"]
        assert result["export_targets"] == ["65000:200"]

    def test_missing_vrf_returns_empty(self):
        driver = _make_driver([{"vrfs": {}}])
        result = driver.get_vrf_detail("NONEXISTENT")
        assert result == {}

    def test_exception_returns_empty(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_vrf_detail("MGMT")
        assert result == {}


# ---------------------------------------------------------------------------
# ping and _parse_ping_output
# ---------------------------------------------------------------------------

class TestPing:
    def test_successful_ping(self):
        output = (
            "PING 10.0.0.1 (10.0.0.1) 72(100) bytes of data.\n"
            "5 packets transmitted, 5 received, 0% packet loss, time 40ms\n"
            "rtt min/avg/max/mdev = 0.123/0.456/0.789/0.012 ms"
        )
        driver = _make_driver([{"output": output}])
        result = driver.ping("10.0.0.1")
        assert result["success"] is True
        assert result["packets_sent"] == 5
        assert result["packets_received"] == 5
        assert result["packet_loss_pct"] == 0.0
        assert result["rtt_min"] == 0.123
        assert result["rtt_avg"] == 0.456
        assert result["rtt_max"] == 0.789

    def test_ping_with_vrf_and_source(self):
        driver = _make_driver([{"output": "5 packets transmitted, 5 received, 0% packet loss"}])
        result = driver.ping("10.0.0.1", count=3, source="Loopback0", vrf="MGMT")
        assert result["success"] is True
        call_args = driver._node.run_commands.call_args
        cmd = call_args[0][0][0]
        assert "vrf MGMT" in cmd
        assert "source Loopback0" in cmd
        assert "repeat 3" in cmd

    def test_ping_failure(self):
        output = "5 packets transmitted, 0 received, 100% packet loss"
        driver = _make_driver([{"output": output}])
        result = driver.ping("10.0.0.1")
        assert result["success"] is False
        assert result["packets_received"] == 0
        assert result["packet_loss_pct"] == 100.0

    def test_ping_exception(self):
        driver = _make_driver(RuntimeError("connection refused"))
        result = driver.ping("10.0.0.1", count=3)
        assert result["success"] is False
        assert result["packets_sent"] == 3
        assert "connection refused" in result["output"]

    def test_parse_ping_no_match(self):
        result = EosDriver._parse_ping_output("garbage output", 5)
        assert result["success"] is False
        assert result["packet_loss_pct"] == 100.0


# ---------------------------------------------------------------------------
# traceroute and _parse_traceroute_output
# ---------------------------------------------------------------------------

class TestTraceroute:
    def test_successful_traceroute(self):
        output = (
            "traceroute to 10.0.0.1 (10.0.0.1), 30 hops max, 60 byte packets\n"
            " 1  10.0.0.254 1.234 ms  1.100 ms  1.050 ms\n"
            " 2  10.0.0.1 2.345 ms  2.200 ms  2.100 ms\n"
        )
        driver = _make_driver([{"output": output}])
        result = driver.traceroute("10.0.0.1")
        assert len(result["hops"]) == 2
        assert result["hops"][0]["hop"] == 1
        assert result["hops"][0]["address"] == "10.0.0.254"
        assert result["hops"][1]["hop"] == 2

    def test_traceroute_with_stars(self):
        output = " 1  * * *\n 2  10.0.0.1 2.345 ms\n"
        result = EosDriver._parse_traceroute_output(output)
        assert result["hops"][0]["address"] == "*"
        assert result["hops"][0]["rtt_ms"] == 0.0
        assert result["hops"][1]["address"] == "10.0.0.1"

    def test_traceroute_with_vrf_and_source(self):
        driver = _make_driver([{"output": " 1  10.0.0.254 1.0 ms\n"}])
        driver.traceroute("10.0.0.1", source="Loopback0", vrf="MGMT")
        call_args = driver._node.run_commands.call_args
        cmd = call_args[0][0][0]
        assert "vrf MGMT" in cmd
        assert "source Loopback0" in cmd

    def test_traceroute_exception(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.traceroute("10.0.0.1")
        assert result["hops"] == []
        assert "timeout" in result["output"]


# ---------------------------------------------------------------------------
# get_interface_flaps and _parse_flap_events
# ---------------------------------------------------------------------------

class TestGetInterfaceFlaps:
    def test_parses_link_up_down_events(self):
        output = (
            "Jan 15 10:30:00 Ethernet1 changed state to down\n"
            "Jan 15 10:31:00 Ethernet1 changed state to up\n"
            "Jan 15 11:00:00 Vlan100 link status changed\n"
        )
        result = EosDriver._parse_flap_events(output)
        assert len(result) == 3
        assert result[0]["event"] == "link down"
        assert result[0]["interface"] == "Ethernet1"
        assert result[1]["event"] == "link up"
        assert result[2]["event"] == "link state change"
        assert result[2]["interface"] == "Vlan100"

    def test_no_matching_events(self):
        output = "Jan 15 10:30:00 Some other log message\n"
        result = EosDriver._parse_flap_events(output)
        assert result == []

    def test_exception_returns_empty(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_interface_flaps()
        assert result == []

    def test_unknown_interface(self):
        output = "changed state to up on some unknown interface\n"
        result = EosDriver._parse_flap_events(output)
        assert len(result) == 1
        assert result[0]["interface"] == "unknown"


# ---------------------------------------------------------------------------
# get_ospf_interfaces
# ---------------------------------------------------------------------------

class TestGetOspfInterfaces:
    def test_returns_ospf_interfaces(self):
        mock_response = {
            "vrfs": {
                "default": {
                    "instList": {
                        "1": {
                            "interfaces": {
                                "Ethernet1": {
                                    "area": "0.0.0.0",  # noqa: S104
                                    "networkType": "point-to-point",
                                    "cost": 10,
                                    "numAdj": 1,
                                },
                                "Loopback0": {
                                    "area": "0.0.0.0",  # noqa: S104
                                    "networkType": "loopback",
                                    "cost": 1,
                                    "numAdj": 0,
                                },
                            }
                        }
                    }
                }
            }
        }
        driver = _make_driver([mock_response])
        result = driver.get_ospf_interfaces()
        assert len(result) == 2
        assert result[0]["interface"] == "Ethernet1"
        assert result[0]["area"] == "0.0.0.0"  # noqa: S104
        assert result[0]["cost"] == 10
        assert result[0]["neighbor_count"] == 1

    def test_empty_ospf(self):
        driver = _make_driver([{"vrfs": {}}])
        result = driver.get_ospf_interfaces()
        assert result == []

    def test_exception_returns_empty(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_ospf_interfaces()
        assert result == []


# ---------------------------------------------------------------------------
# get_bgp_neighbors
# ---------------------------------------------------------------------------

class TestGetBgpNeighbors:
    def test_returns_bgp_neighbors(self):
        mock_response = {
            "vrfs": {
                "default": {
                    "peerList": [
                        {
                            "peerAddress": "10.0.0.1",
                            "asn": 65001,
                            "state": "Established",
                            "prefixesReceived": 100,
                            "prefixesSent": 50,
                            "upDownTime": "1d2h",
                            "description": "spine-01",
                        }
                    ]
                }
            }
        }
        driver = _make_driver([mock_response])
        result = driver.get_bgp_neighbors()
        assert len(result) == 1
        assert result[0]["neighbor"] == "10.0.0.1"
        assert result[0]["remote_as"] == 65001
        assert result[0]["state"] == "Established"
        assert result[0]["description"] == "spine-01"

    def test_non_default_vrf(self):
        driver = _make_driver([{"vrfs": {"MGMT": {"peerList": []}}}])
        result = driver.get_bgp_neighbors(vrf="MGMT")
        assert result == []

    def test_exception_returns_empty(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_bgp_neighbors()
        assert result == []


# ---------------------------------------------------------------------------
# get_bgp_summary - as_number ValueError branch
# ---------------------------------------------------------------------------

class TestGetBgpSummaryEdgeCases:
    def test_as_number_non_numeric(self):
        mock_response = {
            "vrfs": {
                "default": {
                    "asn": "not-a-number",
                    "routerId": "1.2.3.4",
                    "peers": {},
                }
            }
        }
        driver = _make_driver([mock_response])
        result = driver.get_bgp_summary()
        assert result["as_number"] == 0
        assert result["router_id"] == "1.2.3.4"


# ---------------------------------------------------------------------------
# get_config_section - str return branch (line 1105)
# ---------------------------------------------------------------------------

class TestGetConfigSectionStrReturn:
    def test_non_dict_response(self):
        """When result is not a dict, return str(data)."""
        driver = _make_driver(["some raw text output"])
        result = driver.get_running_config_section("router bgp")
        assert result == "some raw text output"


# ---------------------------------------------------------------------------
# get_startup_config - str return branch (line 1122)
# ---------------------------------------------------------------------------

class TestGetStartupConfigStrReturn:
    def test_non_dict_response(self):
        """When result is not a dict, return str(data)."""
        driver = _make_driver(["raw startup config text"])
        result = driver.get_startup_config()
        assert result == "raw startup config text"


# ---------------------------------------------------------------------------
# NotSupportedError methods
# ---------------------------------------------------------------------------

class TestNotSupportedMethods:
    def test_get_aaa_status_raises(self):
        driver = _make_driver([{}])
        with pytest.raises(NotSupportedError, match="get_aaa_status"):
            driver.get_aaa_status()

    def test_get_user_sessions_raises(self):
        driver = _make_driver([{}])
        with pytest.raises(NotSupportedError, match="get_user_sessions"):
            driver.get_user_sessions()

    def test_get_copp_policy_raises(self):
        driver = _make_driver([{}])
        with pytest.raises(NotSupportedError, match="get_copp_policy"):
            driver.get_copp_policy()


# ---------------------------------------------------------------------------
# get_lldp_neighbors - neighbor_info as non-list branch (line 362)
# ---------------------------------------------------------------------------

class TestGetLldpNeighborsNonList:
    def test_neighbor_info_as_dict(self):
        """When lldpNeighborInfo is a dict (not list), the else branch fires."""
        mock_response = {
            "lldpNeighbors": {
                "Ethernet1": {
                    "lldpNeighborInfo": {
                        "systemName": "leaf-01",
                        "portId": "ge-0/0/0",
                        "systemDescription": "JunOS device",
                    }
                }
            }
        }
        driver = _make_driver([mock_response])
        result = driver.get_lldp_neighbors()
        assert "Ethernet1" in result
        assert result["Ethernet1"][0]["hostname"] == "leaf-01"
        assert result["Ethernet1"][0]["port"] == "ge-0/0/0"
