"""Tests for the 20 new NetworkDriver getters implemented in EosDriver."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from network_mcp.driver import EosDriver
from network_mcp.drivers.base import NotSupportedError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver(run_commands_return: list[dict] | Exception) -> EosDriver:
    """Build an EosDriver with a mocked pyeapi node."""
    driver = EosDriver()
    node = MagicMock()
    if isinstance(run_commands_return, Exception):
        node.run_commands.side_effect = run_commands_return
    else:
        node.run_commands.return_value = run_commands_return
    driver._node = node
    driver._host = "test-switch"
    driver._last_command_time = 0.0
    driver._keepalive_interval = 99999  # prevent keep-alive during tests
    return driver


# ---------------------------------------------------------------------------
# Mock EOS responses
# ---------------------------------------------------------------------------

MOCK_VLANS = {
    "vlans": {
        "1": {"name": "default", "status": "active", "interfaces": {}},
        "100": {
            "name": "SERVERS",
            "status": "active",
            "interfaces": {"Ethernet1": {}, "Ethernet2": {}},
        },
        "200": {"name": "MGMT", "status": "active", "interfaces": {"Ethernet3": {}}, "dynamic": True},
    }
}

MOCK_VLAN_DETAIL = {
    "vlans": {
        "100": {
            "name": "SERVERS",
            "status": "active",
            "interfaces": {"Ethernet1": {}, "Ethernet2": {}},
            "dynamic": False,
        }
    }
}

MOCK_INTERFACE_COUNTERS = {
    "interfaces": {
        "Ethernet1": {
            "inOctets": 123456,
            "outOctets": 654321,
            "inUcastPkts": 1000,
            "outUcastPkts": 2000,
            "inBroadcastPkts": 10,
            "outBroadcastPkts": 20,
            "inMulticastPkts": 5,
            "outMulticastPkts": 8,
        },
        "Ethernet2": {
            "inOctets": 0,
            "outOctets": 0,
            "inUcastPkts": 0,
            "outUcastPkts": 0,
            "inBroadcastPkts": 0,
            "outBroadcastPkts": 0,
            "inMulticastPkts": 0,
            "outMulticastPkts": 0,
        },
    }
}

MOCK_INTERFACE_ERRORS = {
    "interfaceErrorCounters": {
        "Ethernet1": {
            "inputErrors": 500,
            "outputErrors": 10,
            "fcsErrors": 250,
            "alignmentErrors": 5,
            "runtFrames": 3,
            "frameTooLongs": 2,
        },
        "Ethernet2": {
            "inputErrors": 0,
            "outputErrors": 0,
            "fcsErrors": 0,
            "alignmentErrors": 0,
            "runtFrames": 0,
            "frameTooLongs": 0,
        },
    }
}

MOCK_IP_INTERFACES = {
    "interfaces": {
        "Ethernet1": {
            "interfaceAddress": {
                "ipAddr": {"address": "10.0.0.1", "maskLen": 24},
            },
            "vrf": "default",
            "lineProtocolStatus": "up",
        },
        "Loopback0": {
            "interfaceAddress": {
                "ipAddr": {"address": "1.1.1.1", "maskLen": 32},
            },
            "vrf": "MGMT",
            "lineProtocolStatus": "up",
        },
        "Ethernet2": {
            "interfaceAddress": {},
            "lineProtocolStatus": "down",
        },
    }
}

MOCK_TRANSCEIVER = {
    "interfaces": {
        "Ethernet1": {
            "mediaType": "10GBASE-SR",
            "txPower": -2.1,
            "rxPower": -5.2,
            "temperature": 35.0,
        },
        "Ethernet2": {
            "mediaType": "1000BASE-T",
            "txPower": 0.0,
            "rxPower": 0.0,
            "temperature": 30.0,
        },
    }
}

MOCK_OSPF_NEIGHBORS = {
    "vrfs": {
        "default": {
            "instList": {
                "1": {
                    "ospfNeighborEntries": [
                        {
                            "routerId": "10.0.0.2",
                            "adjacencyState": "full",
                            "interfaceName": "Ethernet1",
                            "areaId": "0.0.0.0",  # noqa: S104
                            "uptime": "1d02h",
                        },
                        {
                            "routerId": "10.0.0.3",
                            "adjacencyState": "init",
                            "interfaceName": "Ethernet2",
                            "areaId": "0.0.0.1",
                            "uptime": "00:05:30",
                        },
                    ]
                }
            }
        }
    }
}

MOCK_ROUTE_TABLE = {
    "vrfs": {
        "default": {
            "routes": {
                "10.0.0.0/24": {
                    "routeType": "connected",
                    "metric": 0,
                    "preference": 0,
                    "vias": [{"interface": "Ethernet1"}],
                },
                "10.1.0.0/24": {
                    "routeType": "bgp",
                    "metric": 100,
                    "preference": 200,
                    "vias": [{"nexthopAddr": "10.0.0.2"}],
                },
                "10.2.0.0/24": {
                    "routeType": "ospf",
                    "metric": 110,
                    "preference": 110,
                    "vias": [{"nexthopAddr": "10.0.0.3"}],
                },
            }
        }
    }
}

MOCK_ROUTE_SUMMARY = {
    "vrfs": {
        "default": {
            "totalRoutes": 250,
            "routes": {
                "connected": 10,
                "static": 2,
                "bgp": 200,
                "ospf": 38,
            },
        }
    }
}

MOCK_MAC_TABLE = {
    "unicastTable": {
        "tableEntries": [
            {
                "macAddress": "00:1c:73:00:00:01",
                "vlanId": 100,
                "interface": "Ethernet1",
                "entryType": "dynamic",
            },
            {
                "macAddress": "00:1c:73:00:00:02",
                "vlanId": 200,
                "interface": "Ethernet2",
                "entryType": "static",
            },
        ]
    }
}

MOCK_STP = {
    "spanningTreeMode": "mstp",
    "spanningTreeInstances": {
        "MST0": {
            "rootBridge": {"bridgeId": "0000.001c.7300.0001"},
            "rootPort": "Ethernet1",
            "bridge": {"priority": 32768},
        },
        "MST1": {
            "rootBridge": {"bridgeId": "0000.001c.7300.0002"},
            "rootPort": "Ethernet2",
            "bridge": {"priority": 4096},
        },
    },
}

MOCK_PORT_CHANNELS = {
    "portChannels": {
        "Port-Channel1": {
            "protocol": "lacp",
            "activePorts": {
                "Ethernet1": {"bundled": True},
                "Ethernet2": {"bundled": True},
            },
            "inactivePorts": {},
        },
        "Port-Channel2": {
            "protocol": "static",
            "activePorts": {},
            "inactivePorts": {
                "Ethernet3": {},
            },
        },
    }
}

MOCK_ENV_TEMP = {
    "tempSensors": [
        {"name": "CPU", "currentTemperature": 45.0, "hwStatus": "ok"},
        {"name": "Inlet", "currentTemperature": 28.0, "hwStatus": "ok"},
    ]
}

MOCK_ENV_COOLING = {
    "fanTraySlots": [
        {"label": "FanTray1", "status": "ok"},
        {"label": "FanTray2", "status": "ok"},
    ]
}

MOCK_ENV_POWER = {
    "powerSupplies": {
        "PSU1": {"state": "ok", "outputPower": 150.0},
        "PSU2": {"state": "ok", "outputPower": 145.0},
    }
}

MOCK_NTP_STATUS = {
    "status": "synchronised",
    "peerAddress": "10.0.0.100",
}

MOCK_NTP_ASSOCIATIONS = {
    "peers": {
        "10.0.0.100": {"stratum": 2, "offset": 0.5, "jitter": 1.2},
        "10.0.0.101": {"stratum": 3, "offset": 1.0, "jitter": 2.5},
    }
}

MOCK_CPU_TOP = {
    "cpuInfo": {
        "idle": 85.0,
    }
}

MOCK_VERSION_MEM = {
    "memTotal": 2048000,
    "memFree": 1024000,
}

MOCK_IP_ACLS = {
    "aclList": [
        {
            "name": "MGMT-ACCESS",
            "type": "Standard",
            "sequence": [
                {"sequenceNumber": 10, "action": "permit", "source": "10.0.0.0/8"},
                {"sequenceNumber": 20, "action": "deny", "source": "any"},
            ],
        },
        {
            "name": "WEB-ACL",
            "type": "Extended",
            "sequence": [
                {"sequenceNumber": 10, "action": "permit", "text": "tcp any any eq 443"},
            ],
        },
    ]
}

MOCK_SNMP = {
    "communities": {
        "public": {"access": "ro"},
        "private": {"access": "rw"},
    },
    "users": {
        "snmpv3user": {"group": "network-admin", "version": "v3"},
    },
    "notifications": {
        "hosts": {
            "10.0.0.200": {"community": "trap-comm", "version": "2c"},
        }
    },
}

MOCK_RUNNING_CONFIG_SECTION = {
    "output": "router bgp 65000\n   router-id 10.0.0.1\n   neighbor 10.0.0.2 remote-as 65001\n"
}

MOCK_STARTUP_CONFIG = {"output": "! startup-config\nhostname test-switch\n"}

MOCK_VRFS = {
    "vrfs": {
        "default": {"routeDistinguisher": "", "interfaces": [], "routeCount": 250},
        "PROD": {"routeDistinguisher": "10.0.0.1:1", "interfaces": ["Vlan100", "Vlan200"], "routeCount": 50},
    }
}


# ===========================================================================
# Tests: get_vlans
# ===========================================================================


class TestGetVlans:
    def test_returns_all_vlans(self):
        driver = _make_driver([MOCK_VLANS])
        vlans = driver.get_vlans()
        assert len(vlans) == 3
        names = {v["name"] for v in vlans}
        assert "default" in names
        assert "SERVERS" in names
        assert "MGMT" in names

    def test_vlan_fields(self):
        driver = _make_driver([MOCK_VLANS])
        vlans = driver.get_vlans()
        server_vlan = next(v for v in vlans if v["vlan_id"] == 100)
        assert server_vlan["name"] == "SERVERS"
        assert server_vlan["status"] == "active"
        assert "Ethernet1" in server_vlan["interfaces"]
        assert "Ethernet2" in server_vlan["interfaces"]

    def test_empty_response(self):
        driver = _make_driver([{}])
        vlans = driver.get_vlans()
        assert vlans == []

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        vlans = driver.get_vlans()
        assert vlans == []


# ===========================================================================
# Tests: get_vlan_detail
# ===========================================================================


class TestGetVlanDetail:
    def test_returns_detail(self):
        driver = _make_driver([MOCK_VLAN_DETAIL])
        detail = driver.get_vlan_detail(100)
        assert detail["vlan_id"] == 100
        assert detail["name"] == "SERVERS"
        assert detail["status"] == "active"
        assert "Ethernet1" in detail["interfaces"]
        assert detail["type"] == "static"

    def test_dynamic_vlan_type(self):
        mock_dynamic = {"vlans": {"200": {"name": "DYN", "status": "active", "interfaces": {}, "dynamic": True}}}
        driver = _make_driver([mock_dynamic])
        detail = driver.get_vlan_detail(200)
        assert detail["type"] == "dynamic"

    def test_missing_vlan(self):
        driver = _make_driver([{"vlans": {}}])
        detail = driver.get_vlan_detail(999)
        assert detail["vlan_id"] == 999
        assert detail["name"] == ""

    def test_exception_returns_defaults(self):
        driver = _make_driver(ConnectionError("fail"))
        detail = driver.get_vlan_detail(100)
        assert detail["vlan_id"] == 100
        assert detail["name"] == ""


# ===========================================================================
# Tests: get_interface_counters
# ===========================================================================


class TestGetInterfaceCounters:
    def test_returns_all_interfaces(self):
        driver = _make_driver([MOCK_INTERFACE_COUNTERS])
        counters = driver.get_interface_counters()
        assert len(counters) == 2

    def test_counter_values(self):
        driver = _make_driver([MOCK_INTERFACE_COUNTERS])
        counters = driver.get_interface_counters()
        eth1 = next(c for c in counters if c["interface"] == "Ethernet1")
        assert eth1["in_octets"] == 123456
        assert eth1["out_octets"] == 654321
        assert eth1["in_packets"] == 1000
        assert eth1["out_packets"] == 2000
        assert eth1["in_broadcast"] == 10
        assert eth1["out_broadcast"] == 20
        assert eth1["in_multicast"] == 5
        assert eth1["out_multicast"] == 8

    def test_zero_counters(self):
        driver = _make_driver([MOCK_INTERFACE_COUNTERS])
        counters = driver.get_interface_counters()
        eth2 = next(c for c in counters if c["interface"] == "Ethernet2")
        assert eth2["in_octets"] == 0

    def test_empty_response(self):
        driver = _make_driver([{}])
        assert driver.get_interface_counters() == []

    def test_exception_returns_empty(self):
        driver = _make_driver(TimeoutError("timeout"))
        assert driver.get_interface_counters() == []


# ===========================================================================
# Tests: get_interface_errors
# ===========================================================================


class TestGetInterfaceErrors:
    def test_returns_all_interfaces(self):
        driver = _make_driver([MOCK_INTERFACE_ERRORS])
        errors = driver.get_interface_errors()
        assert len(errors) == 2

    def test_error_values(self):
        driver = _make_driver([MOCK_INTERFACE_ERRORS])
        errors = driver.get_interface_errors()
        eth1 = next(e for e in errors if e["interface"] == "Ethernet1")
        assert eth1["in_errors"] == 500
        assert eth1["out_errors"] == 10
        assert eth1["in_crc"] == 250
        assert eth1["in_frame"] == 5
        assert eth1["in_runts"] == 3
        assert eth1["in_giants"] == 2

    def test_clean_errors(self):
        driver = _make_driver([MOCK_INTERFACE_ERRORS])
        errors = driver.get_interface_errors()
        eth2 = next(e for e in errors if e["interface"] == "Ethernet2")
        assert eth2["in_errors"] == 0
        assert eth2["out_errors"] == 0

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_interface_errors() == []


# ===========================================================================
# Tests: get_ip_interfaces
# ===========================================================================


class TestGetIpInterfaces:
    def test_returns_all_l3_interfaces(self):
        driver = _make_driver([MOCK_IP_INTERFACES])
        ips = driver.get_ip_interfaces()
        assert len(ips) == 3

    def test_interface_with_ip(self):
        driver = _make_driver([MOCK_IP_INTERFACES])
        ips = driver.get_ip_interfaces()
        eth1 = next(i for i in ips if i["interface"] == "Ethernet1")
        assert eth1["ip_address"] == "10.0.0.1"
        assert eth1["subnet"] == "24"
        assert eth1["vrf"] == "default"
        assert eth1["status"] == "up"

    def test_interface_with_vrf(self):
        driver = _make_driver([MOCK_IP_INTERFACES])
        ips = driver.get_ip_interfaces()
        lo = next(i for i in ips if i["interface"] == "Loopback0")
        assert lo["vrf"] == "MGMT"

    def test_interface_without_ip(self):
        driver = _make_driver([MOCK_IP_INTERFACES])
        ips = driver.get_ip_interfaces()
        eth2 = next(i for i in ips if i["interface"] == "Ethernet2")
        assert eth2["ip_address"] == ""
        assert eth2["status"] == "down"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_ip_interfaces() == []


# ===========================================================================
# Tests: get_transceiver_info
# ===========================================================================


class TestGetTransceiverInfo:
    def test_returns_all_transceivers(self):
        driver = _make_driver([MOCK_TRANSCEIVER])
        info = driver.get_transceiver_info()
        assert len(info) == 2

    def test_transceiver_fields(self):
        driver = _make_driver([MOCK_TRANSCEIVER])
        info = driver.get_transceiver_info()
        eth1 = next(t for t in info if t["interface"] == "Ethernet1")
        assert eth1["type"] == "10GBASE-SR"
        assert eth1["tx_power"] == -2.1
        assert eth1["rx_power"] == -5.2
        assert eth1["temp"] == 35.0

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_transceiver_info() == []


# ===========================================================================
# Tests: get_ospf_neighbors
# ===========================================================================


class TestGetOspfNeighbors:
    def test_returns_neighbors(self):
        driver = _make_driver([MOCK_OSPF_NEIGHBORS])
        neighbors = driver.get_ospf_neighbors()
        assert len(neighbors) == 2

    def test_neighbor_fields(self):
        driver = _make_driver([MOCK_OSPF_NEIGHBORS])
        neighbors = driver.get_ospf_neighbors()
        n1 = next(n for n in neighbors if n["neighbor_id"] == "10.0.0.2")
        assert n1["state"] == "full"
        assert n1["interface"] == "Ethernet1"
        assert n1["area"] == "0.0.0.0"  # noqa: S104
        assert n1["uptime"] == "1d02h"

    def test_non_default_vrf_empty(self):
        driver = _make_driver([MOCK_OSPF_NEIGHBORS])
        neighbors = driver.get_ospf_neighbors(vrf="PROD")
        assert neighbors == []

    def test_empty_response(self):
        driver = _make_driver([{"vrfs": {}}])
        assert driver.get_ospf_neighbors() == []

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_ospf_neighbors() == []


# ===========================================================================
# Tests: get_route_table
# ===========================================================================


class TestGetRouteTable:
    def test_returns_all_routes(self):
        driver = _make_driver([MOCK_ROUTE_TABLE])
        routes = driver.get_route_table()
        assert len(routes) == 3

    def test_route_fields(self):
        driver = _make_driver([MOCK_ROUTE_TABLE])
        routes = driver.get_route_table()
        bgp_route = next(r for r in routes if r["prefix"] == "10.1.0.0/24")
        assert bgp_route["next_hop"] == "10.0.0.2"
        assert bgp_route["protocol"] == "bgp"
        assert bgp_route["metric"] == 100
        assert bgp_route["preference"] == 200

    def test_connected_route_uses_interface(self):
        driver = _make_driver([MOCK_ROUTE_TABLE])
        routes = driver.get_route_table()
        conn_route = next(r for r in routes if r["prefix"] == "10.0.0.0/24")
        assert conn_route["next_hop"] == "Ethernet1"
        assert conn_route["protocol"] == "connected"

    def test_filter_by_protocol(self):
        driver = _make_driver([MOCK_ROUTE_TABLE])
        routes = driver.get_route_table(protocol="bgp")
        assert len(routes) == 1
        assert routes[0]["protocol"] == "bgp"

    def test_limit(self):
        driver = _make_driver([MOCK_ROUTE_TABLE])
        routes = driver.get_route_table(limit=2)
        assert len(routes) == 2

    def test_empty_response(self):
        driver = _make_driver([{"vrfs": {"default": {"routes": {}}}}])
        assert driver.get_route_table() == []

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_route_table() == []


# ===========================================================================
# Tests: get_route_summary
# ===========================================================================


class TestGetRouteSummary:
    def test_returns_summary(self):
        driver = _make_driver([MOCK_ROUTE_SUMMARY])
        summary = driver.get_route_summary()
        assert summary["total"] == 250
        assert summary["connected"] == 10
        assert summary["static"] == 2
        assert summary["bgp"] == 200
        assert summary["ospf"] == 38
        assert summary["isis"] == 0

    def test_empty_response(self):
        driver = _make_driver([{}])
        summary = driver.get_route_summary()
        assert summary["total"] == 0

    def test_exception_returns_defaults(self):
        driver = _make_driver(ConnectionError("fail"))
        summary = driver.get_route_summary()
        assert summary["total"] == 0
        assert summary["bgp"] == 0


# ===========================================================================
# Tests: get_mac_table
# ===========================================================================


class TestGetMacTable:
    def test_returns_entries(self):
        driver = _make_driver([MOCK_MAC_TABLE])
        entries = driver.get_mac_table()
        assert len(entries) == 2

    def test_entry_fields(self):
        driver = _make_driver([MOCK_MAC_TABLE])
        entries = driver.get_mac_table()
        e1 = entries[0]
        assert e1["vlan"] == 100
        assert e1["interface"] == "Ethernet1"
        assert e1["type"] == "dynamic"
        # MAC should be normalized
        assert ":" in e1["mac_address"] or e1["mac_address"] == "00:1c:73:00:00:01"

    def test_static_entry(self):
        driver = _make_driver([MOCK_MAC_TABLE])
        entries = driver.get_mac_table()
        e2 = entries[1]
        assert e2["type"] == "static"

    def test_limit(self):
        driver = _make_driver([MOCK_MAC_TABLE])
        entries = driver.get_mac_table(limit=1)
        assert len(entries) == 1

    def test_empty_table(self):
        driver = _make_driver([{"unicastTable": {"tableEntries": []}}])
        assert driver.get_mac_table() == []

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_mac_table() == []


# ===========================================================================
# Tests: get_stp_status
# ===========================================================================


class TestGetStpStatus:
    def test_returns_status(self):
        driver = _make_driver([MOCK_STP])
        stp = driver.get_stp_status()
        assert stp["mode"] == "mstp"
        assert stp["root_bridge"] == "0000.001c.7300.0001"
        assert len(stp["instances"]) == 2

    def test_instance_fields(self):
        driver = _make_driver([MOCK_STP])
        stp = driver.get_stp_status()
        inst0 = next(i for i in stp["instances"] if i["id"] == "MST0")
        assert inst0["root_bridge"] == "0000.001c.7300.0001"
        assert inst0["root_port"] == "Ethernet1"
        assert inst0["priority"] == 32768

    def test_empty_stp(self):
        driver = _make_driver([{"spanningTreeInstances": {}}])
        stp = driver.get_stp_status()
        assert stp["instances"] == []
        assert stp["root_bridge"] == ""

    def test_exception_returns_defaults(self):
        driver = _make_driver(ConnectionError("fail"))
        stp = driver.get_stp_status()
        assert stp["mode"] == ""
        assert stp["instances"] == []


# ===========================================================================
# Tests: get_port_channels
# ===========================================================================


class TestGetPortChannels:
    def test_returns_channels(self):
        driver = _make_driver([MOCK_PORT_CHANNELS])
        channels = driver.get_port_channels()
        assert len(channels) == 2

    def test_active_channel(self):
        driver = _make_driver([MOCK_PORT_CHANNELS])
        channels = driver.get_port_channels()
        pc1 = next(c for c in channels if c["name"] == "Port-Channel1")
        assert pc1["status"] == "up"
        assert pc1["protocol"] == "lacp"
        assert len(pc1["members"]) == 2
        assert all(m["status"] == "bundled" for m in pc1["members"])

    def test_down_channel(self):
        driver = _make_driver([MOCK_PORT_CHANNELS])
        channels = driver.get_port_channels()
        pc2 = next(c for c in channels if c["name"] == "Port-Channel2")
        assert pc2["status"] == "down"
        assert len(pc2["members"]) == 1
        assert pc2["members"][0]["status"] == "down"

    def test_empty_channels(self):
        driver = _make_driver([{"portChannels": {}}])
        assert driver.get_port_channels() == []

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_port_channels() == []


# ===========================================================================
# Tests: get_environment
# ===========================================================================


class TestGetEnvironment:
    def test_returns_all_sections(self):
        driver = _make_driver([MOCK_ENV_TEMP, MOCK_ENV_COOLING, MOCK_ENV_POWER])
        env = driver.get_environment()
        assert len(env["temperature"]) == 2
        assert len(env["fans"]) == 2
        assert len(env["power"]) == 2

    def test_temperature_fields(self):
        driver = _make_driver([MOCK_ENV_TEMP, MOCK_ENV_COOLING, MOCK_ENV_POWER])
        env = driver.get_environment()
        cpu = next(t for t in env["temperature"] if t["sensor"] == "CPU")
        assert cpu["value"] == 45.0
        assert cpu["status"] == "ok"

    def test_fan_fields(self):
        driver = _make_driver([MOCK_ENV_TEMP, MOCK_ENV_COOLING, MOCK_ENV_POWER])
        env = driver.get_environment()
        fan1 = env["fans"][0]
        assert fan1["name"] == "FanTray1"
        assert fan1["status"] == "ok"

    def test_power_fields(self):
        driver = _make_driver([MOCK_ENV_TEMP, MOCK_ENV_COOLING, MOCK_ENV_POWER])
        env = driver.get_environment()
        psu1 = next(p for p in env["power"] if p["name"] == "PSU1")
        assert psu1["status"] == "ok"
        assert psu1["output_watts"] == 150.0

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        env = driver.get_environment()
        assert env == {"temperature": [], "fans": [], "power": []}


# ===========================================================================
# Tests: get_ntp_status
# ===========================================================================


class TestGetNtpStatus:
    def test_synchronized(self):
        driver = _make_driver([MOCK_NTP_STATUS, MOCK_NTP_ASSOCIATIONS])
        ntp = driver.get_ntp_status()
        assert ntp["synchronized"] is True
        assert ntp["reference"] == "10.0.0.100"

    def test_peers(self):
        driver = _make_driver([MOCK_NTP_STATUS, MOCK_NTP_ASSOCIATIONS])
        ntp = driver.get_ntp_status()
        assert len(ntp["peers"]) == 2
        p1 = next(p for p in ntp["peers"] if p["peer"] == "10.0.0.100")
        assert p1["stratum"] == 2
        assert p1["offset"] == 0.5
        assert p1["jitter"] == 1.2

    def test_unsynchronized(self):
        unsync = {"status": "unsynchronised"}
        driver = _make_driver([unsync, {"peers": {}}])
        ntp = driver.get_ntp_status()
        assert ntp["synchronized"] is False
        assert ntp["peers"] == []

    def test_exception_returns_defaults(self):
        driver = _make_driver(ConnectionError("fail"))
        ntp = driver.get_ntp_status()
        assert ntp["synchronized"] is False
        assert ntp["peers"] == []


# ===========================================================================
# Tests: get_cpu_memory
# ===========================================================================


class TestGetCpuMemory:
    def test_returns_values(self):
        driver = _make_driver([MOCK_CPU_TOP, MOCK_VERSION_MEM])
        cm = driver.get_cpu_memory()
        assert cm["cpu_percent"] == 15.0
        assert cm["memory_total"] == 2048000
        assert cm["memory_used"] == 1024000
        assert cm["memory_free"] == 1024000

    def test_exception_returns_defaults(self):
        driver = _make_driver(ConnectionError("fail"))
        cm = driver.get_cpu_memory()
        assert cm["cpu_percent"] == 0.0
        assert cm["memory_total"] == 0


# ===========================================================================
# Tests: get_acls
# ===========================================================================


class TestGetAcls:
    def test_returns_acls(self):
        driver = _make_driver([MOCK_IP_ACLS])
        acls = driver.get_acls()
        assert len(acls) == 2

    def test_standard_acl(self):
        driver = _make_driver([MOCK_IP_ACLS])
        acls = driver.get_acls()
        mgmt = next(a for a in acls if a["name"] == "MGMT-ACCESS")
        assert mgmt["type"] == "standard"
        assert len(mgmt["rules"]) == 2
        assert mgmt["rules"][0]["sequence"] == 10
        assert mgmt["rules"][0]["action"] == "permit"
        assert mgmt["rules"][0]["source"] == "10.0.0.0/8"

    def test_extended_acl_with_text(self):
        driver = _make_driver([MOCK_IP_ACLS])
        acls = driver.get_acls()
        web = next(a for a in acls if a["name"] == "WEB-ACL")
        assert web["type"] == "extended"
        assert web["rules"][0]["source"] == "tcp any any eq 443"

    def test_empty_acls(self):
        driver = _make_driver([{"aclList": []}])
        assert driver.get_acls() == []

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_acls() == []


# ===========================================================================
# Tests: get_snmp_config
# ===========================================================================


class TestGetSnmpConfig:
    def test_communities(self):
        driver = _make_driver([MOCK_SNMP])
        snmp = driver.get_snmp_config()
        assert len(snmp["communities"]) == 2
        pub = next(c for c in snmp["communities"] if c["name"] == "public")
        assert pub["access"] == "ro"
        priv = next(c for c in snmp["communities"] if c["name"] == "private")
        assert priv["access"] == "rw"

    def test_users(self):
        driver = _make_driver([MOCK_SNMP])
        snmp = driver.get_snmp_config()
        assert len(snmp["users"]) == 1
        assert snmp["users"][0]["name"] == "snmpv3user"

    def test_traps(self):
        driver = _make_driver([MOCK_SNMP])
        snmp = driver.get_snmp_config()
        assert len(snmp["traps"]) == 1
        assert snmp["traps"][0]["host"] == "10.0.0.200"

    def test_exception_returns_defaults(self):
        driver = _make_driver(ConnectionError("fail"))
        snmp = driver.get_snmp_config()
        assert snmp == {"communities": [], "users": [], "traps": []}


# ===========================================================================
# Tests: get_running_config_section
# ===========================================================================


class TestGetRunningConfigSection:
    def test_returns_section(self):
        driver = _make_driver([MOCK_RUNNING_CONFIG_SECTION])
        section = driver.get_running_config_section("router bgp")
        assert "router bgp 65000" in section
        assert "neighbor 10.0.0.2" in section

    def test_empty_section(self):
        driver = _make_driver([{"output": ""}])
        section = driver.get_running_config_section("nonexistent")
        assert section == ""

    def test_exception_returns_empty_string(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_running_config_section("router bgp") == ""


# ===========================================================================
# Tests: get_startup_config
# ===========================================================================


class TestGetStartupConfig:
    def test_returns_config(self):
        driver = _make_driver([MOCK_STARTUP_CONFIG])
        config = driver.get_startup_config()
        assert "hostname test-switch" in config

    def test_exception_returns_empty_string(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_startup_config() == ""


# ===========================================================================
# Tests: get_vrfs
# ===========================================================================


class TestGetVrfs:
    def test_returns_vrfs(self):
        driver = _make_driver([MOCK_VRFS])
        vrfs = driver.get_vrfs()
        assert len(vrfs) == 2

    def test_vrf_fields(self):
        driver = _make_driver([MOCK_VRFS])
        vrfs = driver.get_vrfs()
        prod = next(v for v in vrfs if v["name"] == "PROD")
        assert prod["rd"] == "10.0.0.1:1"
        assert "Vlan100" in prod["interfaces"]
        assert "Vlan200" in prod["interfaces"]
        assert prod["route_count"] == 50

    def test_default_vrf(self):
        driver = _make_driver([MOCK_VRFS])
        vrfs = driver.get_vrfs()
        default = next(v for v in vrfs if v["name"] == "default")
        assert default["rd"] == ""
        assert default["route_count"] == 250

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_vrfs() == []


# ===========================================================================
# Tests: capabilities includes getter flags
# ===========================================================================


class TestCapabilities:
    def test_includes_getter_flags(self):
        driver = EosDriver()
        caps = driver.capabilities()
        # Platform features
        assert caps["config_sessions"] is True
        assert caps["gnmi"] is True
        # Getter flags
        assert caps["get_vlans"] is True
        assert caps["get_vlan_detail"] is True
        assert caps["get_interface_counters"] is True
        assert caps["get_interface_errors"] is True
        assert caps["get_ip_interfaces"] is True
        assert caps["get_transceiver_info"] is True
        assert caps["get_ospf_neighbors"] is True
        assert caps["get_route_table"] is True
        assert caps["get_route_summary"] is True
        assert caps["get_mac_table"] is True
        assert caps["get_stp_status"] is True
        assert caps["get_port_channels"] is True
        assert caps["get_environment"] is True
        assert caps["get_ntp_status"] is True
        assert caps["get_cpu_memory"] is True
        assert caps["get_acls"] is True
        assert caps["get_snmp_config"] is True
        assert caps["get_running_config_section"] is True
        assert caps["get_startup_config"] is True
        assert caps["get_vrfs"] is True


# ===========================================================================
# Tests: NotSupportedError
# ===========================================================================


class TestNotSupportedError:
    def test_is_exception(self):
        assert issubclass(NotSupportedError, Exception)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(NotSupportedError, match="not supported"):
            raise NotSupportedError("get_vlans is not supported on this platform")
