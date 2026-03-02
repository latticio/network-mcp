"""Tests for the 20 new NetworkDriver getters implemented in NxosDriver."""

from __future__ import annotations

from unittest.mock import MagicMock

from network_mcp.drivers.cisco_nxos import NxosDriver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nxapi_response(body: dict | str, code: str = "200", msg: str = "Success") -> dict:
    """Build a realistic NX-API JSON response wrapper."""
    return {
        "ins_api": {
            "outputs": {
                "output": {
                    "code": code,
                    "msg": msg,
                    "body": body,
                }
            }
        }
    }


def _make_driver(nxapi_bodies: list[dict | str] | Exception) -> NxosDriver:
    """Build an NxosDriver with a mocked NX-API httpx client."""
    driver = NxosDriver()
    driver._connected = True
    driver._transport = "nxapi"
    driver._ssh_host = "test-nxos"
    driver._ssh_username = "admin"
    driver._ssh_password = "pass"
    driver._last_command_time = 0.0
    driver._keepalive_interval = 99999

    mock_client = MagicMock()
    if isinstance(nxapi_bodies, Exception):
        mock_client.post.side_effect = nxapi_bodies
    else:
        responses = []
        for body in nxapi_bodies:
            resp = MagicMock()
            resp.json.return_value = _nxapi_response(body)
            resp.raise_for_status.return_value = None
            responses.append(resp)
        mock_client.post.side_effect = responses
    driver._nxapi = mock_client
    return driver


# ---------------------------------------------------------------------------
# Mock NX-API response bodies
# ---------------------------------------------------------------------------

MOCK_VLANS = {
    "TABLE_vlanbrief": {
        "ROW_vlanbrief": [
            {
                "vlanshowbr-vlanid": "1",
                "vlanshowbr-vlanname": "default",
                "vlanshowbr-vlanstate": "active",
                "vlanshowplist-ifidx": "",
            },
            {
                "vlanshowbr-vlanid": "100",
                "vlanshowbr-vlanname": "SERVERS",
                "vlanshowbr-vlanstate": "active",
                "vlanshowplist-ifidx": "Eth1/1, Eth1/2",
            },
            {
                "vlanshowbr-vlanid": "200",
                "vlanshowbr-vlanname": "MGMT",
                "vlanshowbr-vlanstate": "active",
                "vlanshowplist-ifidx": "",
            },
        ]
    }
}

MOCK_VLAN_DETAIL = {
    "TABLE_vlanbriefid": {
        "ROW_vlanbriefid": {
            "vlanshowbr-vlanid": "100",
            "vlanshowbr-vlanname": "SERVERS",
            "vlanshowbr-vlanstate": "active",
            "vlanshowplist-ifidx": "Eth1/1, Eth1/2",
            "vlanshowbr-vlantype": "enet",
        }
    }
}

MOCK_INTERFACE = {
    "TABLE_interface": {
        "ROW_interface": [
            {
                "interface": "Ethernet1/1",
                "state": "up",
                "admin_state": "up",
                "eth_inbytes": "123456",
                "eth_outbytes": "654321",
                "eth_inucast": "1000",
                "eth_outucast": "2000",
                "eth_inbcast": "10",
                "eth_outbcast": "20",
                "eth_inmcast": "5",
                "eth_outmcast": "8",
                "eth_inerr": "500",
                "eth_outerr": "10",
                "eth_crc": "250",
                "eth_frame": "5",
                "eth_runts": "3",
                "eth_giants": "2",
            },
            {
                "interface": "Ethernet1/2",
                "state": "down",
                "admin_state": "down",
                "eth_inbytes": "0",
                "eth_outbytes": "0",
                "eth_inucast": "0",
                "eth_outucast": "0",
                "eth_inbcast": "0",
                "eth_outbcast": "0",
                "eth_inmcast": "0",
                "eth_outmcast": "0",
                "eth_inerr": "0",
                "eth_outerr": "0",
                "eth_crc": "0",
                "eth_frame": "0",
                "eth_runts": "0",
                "eth_giants": "0",
            },
        ]
    }
}

MOCK_IP_INTF = {
    "TABLE_intf": {
        "ROW_intf": [
            {"intf-name": "Eth1/1", "prefix": "10.0.0.1/24", "vrf-name-out": "default", "proto-state": "up"},
            {"intf-name": "Lo0", "prefix": "1.1.1.1/32", "vrf-name-out": "MGMT", "proto-state": "up"},
            {"intf-name": "Eth1/2", "prefix": "", "vrf-name-out": "default", "proto-state": "down"},
        ]
    }
}

MOCK_TRANSCEIVER = {
    "TABLE_interface": {
        "ROW_interface": [
            {
                "interface": "Ethernet1/1",
                "type": "10GBASE-SR",
                "tx_pwr": "-2.1",
                "rx_pwr": "-5.3",
                "temperature": "35.0",
            },
            {"interface": "Ethernet1/2", "sfp": "1000BASE-T", "tx_pwr": "0.0", "rx_pwr": "0.0", "temperature": "30.0"},
        ]
    }
}

MOCK_OSPF = {
    "TABLE_ctx": {
        "ROW_ctx": {
            "TABLE_nbr": {
                "ROW_nbr": [
                    {
                        "rid": "10.0.0.2",
                        "state": "FULL/DR",
                        "intf": "Eth1/1",
                        "area": "0.0.0.0",  # noqa: S104
                        "uptime": "P1DT2H",
                    },
                    {"rid": "10.0.0.3", "state": "INIT/DROTHER", "intf": "Eth1/2", "area": "0.0.0.1", "uptime": "PT5M"},
                ]
            }
        }
    }
}

MOCK_ROUTES = {
    "TABLE_vrf": {
        "ROW_vrf": {
            "TABLE_addrf": {
                "ROW_addrf": {
                    "TABLE_prefix": {
                        "ROW_prefix": [
                            {
                                "ipprefix": "10.0.0.0/24",
                                "TABLE_path": {
                                    "ROW_path": {"clientname": "direct", "ifname": "Eth1/1", "metric": "0", "pref": "0"}
                                },
                            },
                            {
                                "ipprefix": "10.1.0.0/24",
                                "TABLE_path": {
                                    "ROW_path": {
                                        "clientname": "bgp",
                                        "ipnexthop": "10.0.0.2",
                                        "metric": "100",
                                        "pref": "200",
                                    }
                                },
                            },
                            {
                                "ipprefix": "10.2.0.0/24",
                                "TABLE_path": {
                                    "ROW_path": {
                                        "clientname": "ospf",
                                        "ipnexthop": "10.0.0.3",
                                        "metric": "20",
                                        "pref": "110",
                                    }
                                },
                            },
                        ]
                    }
                }
            }
        }
    }
}

MOCK_ROUTE_SUMMARY = {
    "TABLE_vrf": {
        "ROW_vrf": {
            "routes-total": "250",
            "TABLE_route_count": {
                "ROW_route_count": [
                    {"clientnamecount": "connected", "count": "10"},
                    {"clientnamecount": "static", "count": "2"},
                    {"clientnamecount": "bgp", "count": "200"},
                    {"clientnamecount": "ospf", "count": "38"},
                ]
            },
        }
    }
}

MOCK_MAC = {
    "TABLE_mac_address": {
        "ROW_mac_address": [
            {"disp_mac_addr": "001c.7300.0001", "disp_vlan": "100", "disp_port": "Eth1/1", "disp_type": "dynamic"},
            {"disp_mac_addr": "001c.7300.0002", "disp_vlan": "200", "disp_port": "Eth1/2", "disp_type": "static"},
        ]
    }
}

MOCK_STP = {
    "stp_mode": "rapid-pvst",
    "TABLE_vlan": {
        "ROW_vlan": [
            {"vlan_id": "100", "root_bridge_addr": "aabb.cc00.0100", "root_port": "Eth1/1", "bridge_priority": "32768"},
            {"vlan_id": "200", "root_bridge_addr": "aabb.cc00.0200", "root_port": "Eth1/2", "bridge_priority": "4096"},
        ]
    },
}

MOCK_PORT_CHANNELS = {
    "TABLE_channel": {
        "ROW_channel": [
            {
                "port-channel": "Po1",
                "status": "up",
                "prtcl": "lacp",
                "TABLE_member": {
                    "ROW_member": [
                        {"port": "Eth1/1", "port-status": "P"},
                        {"port": "Eth1/2", "port-status": "P"},
                    ]
                },
            },
            {
                "port-channel": "Po2",
                "status": "down",
                "prtcl": "none",
                "TABLE_member": {"ROW_member": {"port": "Eth1/3", "port-status": "D"}},
            },
        ]
    }
}

MOCK_ENV = {
    "TABLE_tempinfo": {
        "ROW_tempinfo": [
            {"tempmod": "CPU", "curtemp": "45", "alarmstatus": "Ok"},
            {"tempmod": "Inlet", "curtemp": "28", "alarmstatus": "Ok"},
        ]
    },
    "TABLE_faninfo": {
        "ROW_faninfo": [
            {"fanname": "Fan1", "fanstatus": "Ok"},
            {"fanname": "Fan2", "fanstatus": "Ok"},
        ]
    },
    "TABLE_psinfo": {
        "ROW_psinfo": [
            {"psmodel": "PSU1", "ps_status": "Ok", "actual_out": "150W"},
            {"psmodel": "PSU2", "ps_status": "Ok", "actual_out": "145"},
        ]
    },
}

MOCK_NTP = {
    "TABLE_peersstatus": {
        "ROW_peersstatus": [
            {"remote": "*10.0.0.100", "syncmode": "*", "st": "2", "delay": "0.5", "jitter": "1.2"},
            {"remote": "+10.0.0.101", "syncmode": "+", "st": "3", "delay": "1.0", "disp": "2.5"},
        ]
    }
}

MOCK_SYSTEM_RESOURCES = {
    "cpu_state_idle": "85.0",
    "memory_usage_total": "2048000",
    "memory_usage_used": "1024000",
    "memory_usage_free": "1024000",
}

MOCK_ACLS = {
    "TABLE_ip_acl": {
        "ROW_ip_acl": [
            {
                "acl_name": "MGMT-ACCESS",
                "acl_type": "Standard",
                "TABLE_seqno": {
                    "ROW_seqno": [
                        {"seqno": "10", "permitdeny": "permit", "src_any": "10.0.0.0/8"},
                        {"seqno": "20", "permitdeny": "deny", "src_any": "any"},
                    ]
                },
            },
            {
                "acl_name": "WEB-ACL",
                "acl_type": "Extended",
                "TABLE_seqno": {
                    "ROW_seqno": {"seqno": "10", "permitdeny": "permit", "src_ip_prefix": "tcp any any eq 443"},
                },
            },
        ]
    }
}

MOCK_SNMP = {
    "TABLE_snmp_community": {
        "ROW_snmp_community": [
            {"community_name": "public", "grouporaccess": "network-operator (ro)"},
            {"community_name": "private", "grouporaccess": "network-admin (rw)"},
        ]
    },
    "TABLE_snmp_users": {
        "ROW_snmp_users": {"user": "snmpv3user"},
    },
    "TABLE_host": {
        "ROW_host": {"host": "10.0.0.200"},
    },
}

MOCK_RUNNING_SECTION = "router bgp 65000\n  router-id 10.0.0.1\n  neighbor 10.0.0.2 remote-as 65001\n"

MOCK_STARTUP_CONFIG = "! startup-config\nhostname test-nxos\n"

MOCK_VRFS = {
    "TABLE_vrf": {
        "ROW_vrf": [
            {"vrf_name": "default", "rd": "--", "vrf_intf": ""},
            {"vrf_name": "PROD", "rd": "10.0.0.1:1", "vrf_intf": "Vlan100, Vlan200"},
        ]
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

    def test_vlan_fields(self):
        driver = _make_driver([MOCK_VLANS])
        vlans = driver.get_vlans()
        v100 = next(v for v in vlans if v["vlan_id"] == 100)
        assert v100["name"] == "SERVERS"
        assert v100["status"] == "active"
        assert len(v100["interfaces"]) == 2

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_vlans() == []


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
        assert detail["type"] == "static"

    def test_exception_returns_defaults(self):
        driver = _make_driver(ConnectionError("fail"))
        detail = driver.get_vlan_detail(100)
        assert detail["vlan_id"] == 100
        assert detail["name"] == ""


# ===========================================================================
# Tests: get_interface_counters
# ===========================================================================


class TestGetInterfaceCounters:
    def test_returns_interfaces(self):
        driver = _make_driver([MOCK_INTERFACE])
        counters = driver.get_interface_counters()
        assert len(counters) == 2

    def test_counter_values(self):
        driver = _make_driver([MOCK_INTERFACE])
        counters = driver.get_interface_counters()
        eth1 = next(c for c in counters if c["interface"] == "Ethernet1/1")
        assert eth1["in_octets"] == 123456
        assert eth1["out_octets"] == 654321
        assert eth1["in_packets"] == 1000
        assert eth1["out_packets"] == 2000
        assert eth1["in_broadcast"] == 10
        assert eth1["out_broadcast"] == 20
        assert eth1["in_multicast"] == 5
        assert eth1["out_multicast"] == 8

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_interface_counters() == []


# ===========================================================================
# Tests: get_interface_errors
# ===========================================================================


class TestGetInterfaceErrors:
    def test_returns_interfaces(self):
        driver = _make_driver([MOCK_INTERFACE])
        errors = driver.get_interface_errors()
        assert len(errors) == 2

    def test_error_values(self):
        driver = _make_driver([MOCK_INTERFACE])
        errors = driver.get_interface_errors()
        eth1 = next(e for e in errors if e["interface"] == "Ethernet1/1")
        assert eth1["in_errors"] == 500
        assert eth1["out_errors"] == 10
        assert eth1["in_crc"] == 250
        assert eth1["in_frame"] == 5
        assert eth1["in_runts"] == 3
        assert eth1["in_giants"] == 2

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_interface_errors() == []


# ===========================================================================
# Tests: get_ip_interfaces
# ===========================================================================


class TestGetIpInterfaces:
    def test_returns_l3_interfaces(self):
        driver = _make_driver([MOCK_IP_INTF])
        ips = driver.get_ip_interfaces()
        assert len(ips) == 3

    def test_ip_fields(self):
        driver = _make_driver([MOCK_IP_INTF])
        ips = driver.get_ip_interfaces()
        eth1 = next(i for i in ips if i["interface"] == "Eth1/1")
        assert eth1["ip_address"] == "10.0.0.1"
        assert eth1["subnet"] == "24"
        assert eth1["vrf"] == "default"
        assert eth1["status"] == "up"

    def test_interface_without_ip(self):
        driver = _make_driver([MOCK_IP_INTF])
        ips = driver.get_ip_interfaces()
        eth2 = next(i for i in ips if i["interface"] == "Eth1/2")
        assert eth2["ip_address"] == ""
        assert eth2["status"] == "down"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_ip_interfaces() == []


# ===========================================================================
# Tests: get_transceiver_info
# ===========================================================================


class TestGetTransceiverInfo:
    def test_returns_transceivers(self):
        driver = _make_driver([MOCK_TRANSCEIVER])
        info = driver.get_transceiver_info()
        assert len(info) == 2

    def test_transceiver_fields(self):
        driver = _make_driver([MOCK_TRANSCEIVER])
        info = driver.get_transceiver_info()
        eth1 = next(t for t in info if t["interface"] == "Ethernet1/1")
        assert eth1["type"] == "10GBASE-SR"
        assert eth1["tx_power"] == -2.1
        assert eth1["rx_power"] == -5.3
        assert eth1["temp"] == 35.0

    def test_fallback_sfp_key(self):
        driver = _make_driver([MOCK_TRANSCEIVER])
        info = driver.get_transceiver_info()
        eth2 = next(t for t in info if t["interface"] == "Ethernet1/2")
        assert eth2["type"] == "1000BASE-T"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_transceiver_info() == []


# ===========================================================================
# Tests: get_ospf_neighbors
# ===========================================================================


class TestGetOspfNeighbors:
    def test_returns_neighbors(self):
        driver = _make_driver([MOCK_OSPF])
        neighbors = driver.get_ospf_neighbors()
        assert len(neighbors) == 2

    def test_neighbor_fields(self):
        driver = _make_driver([MOCK_OSPF])
        neighbors = driver.get_ospf_neighbors()
        n1 = next(n for n in neighbors if n["neighbor_id"] == "10.0.0.2")
        assert n1["state"] == "Full"
        assert n1["interface"] == "Eth1/1"
        assert n1["area"] == "0.0.0.0"  # noqa: S104

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_ospf_neighbors() == []


# ===========================================================================
# Tests: get_route_table
# ===========================================================================


class TestGetRouteTable:
    def test_returns_routes(self):
        driver = _make_driver([MOCK_ROUTES])
        routes = driver.get_route_table()
        assert len(routes) == 3

    def test_route_fields(self):
        driver = _make_driver([MOCK_ROUTES])
        routes = driver.get_route_table()
        bgp = next(r for r in routes if r["protocol"] == "bgp")
        assert bgp["prefix"] == "10.1.0.0/24"
        assert bgp["next_hop"] == "10.0.0.2"
        assert bgp["metric"] == 100
        assert bgp["preference"] == 200

    def test_limit(self):
        driver = _make_driver([MOCK_ROUTES])
        routes = driver.get_route_table(limit=2)
        assert len(routes) == 2

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

    def test_exception_returns_defaults(self):
        driver = _make_driver(ConnectionError("fail"))
        summary = driver.get_route_summary()
        assert summary["total"] == 0


# ===========================================================================
# Tests: get_mac_table
# ===========================================================================


class TestGetMacTable:
    def test_returns_entries(self):
        driver = _make_driver([MOCK_MAC])
        entries = driver.get_mac_table()
        assert len(entries) == 2

    def test_entry_fields(self):
        driver = _make_driver([MOCK_MAC])
        entries = driver.get_mac_table()
        e1 = entries[0]
        assert e1["vlan"] == 100
        assert e1["interface"] == "Eth1/1"
        assert e1["type"] == "dynamic"
        assert ":" in e1["mac_address"]

    def test_limit(self):
        driver = _make_driver([MOCK_MAC])
        entries = driver.get_mac_table(limit=1)
        assert len(entries) == 1

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
        assert stp["mode"] == "rapid-pvst"
        assert len(stp["instances"]) == 2

    def test_root_bridge(self):
        driver = _make_driver([MOCK_STP])
        stp = driver.get_stp_status()
        assert stp["root_bridge"] == "aabb.cc00.0100"

    def test_instance_fields(self):
        driver = _make_driver([MOCK_STP])
        stp = driver.get_stp_status()
        inst1 = next(i for i in stp["instances"] if i["id"] == "100")
        assert inst1["root_bridge"] == "aabb.cc00.0100"
        assert inst1["root_port"] == "Eth1/1"
        assert inst1["priority"] == 32768

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
        po1 = next(c for c in channels if c["name"] == "Po1")
        assert po1["status"] == "up"
        assert po1["protocol"] == "lacp"
        assert len(po1["members"]) == 2
        assert all(m["status"] == "bundled" for m in po1["members"])

    def test_down_channel(self):
        driver = _make_driver([MOCK_PORT_CHANNELS])
        channels = driver.get_port_channels()
        po2 = next(c for c in channels if c["name"] == "Po2")
        assert po2["status"] == "down"
        assert len(po2["members"]) == 1
        assert po2["members"][0]["status"] == "down"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_port_channels() == []


# ===========================================================================
# Tests: get_environment
# ===========================================================================


class TestGetEnvironment:
    def test_returns_all_sections(self):
        driver = _make_driver([MOCK_ENV])
        env = driver.get_environment()
        assert len(env["temperature"]) == 2
        assert len(env["fans"]) == 2
        assert len(env["power"]) == 2

    def test_temperature_fields(self):
        driver = _make_driver([MOCK_ENV])
        env = driver.get_environment()
        cpu = next(t for t in env["temperature"] if t["sensor"] == "CPU")
        assert cpu["value"] == 45.0
        assert cpu["status"] == "ok"

    def test_power_fields(self):
        driver = _make_driver([MOCK_ENV])
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
        driver = _make_driver([MOCK_NTP])
        ntp = driver.get_ntp_status()
        assert ntp["synchronized"] is True
        assert ntp["reference"] == "10.0.0.100"

    def test_peers(self):
        driver = _make_driver([MOCK_NTP])
        ntp = driver.get_ntp_status()
        assert len(ntp["peers"]) == 2

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
        driver = _make_driver([MOCK_SYSTEM_RESOURCES])
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
        driver = _make_driver([MOCK_ACLS])
        acls = driver.get_acls()
        assert len(acls) == 2

    def test_standard_acl(self):
        driver = _make_driver([MOCK_ACLS])
        acls = driver.get_acls()
        mgmt = next(a for a in acls if a["name"] == "MGMT-ACCESS")
        assert mgmt["type"] == "standard"
        assert len(mgmt["rules"]) == 2

    def test_extended_acl(self):
        driver = _make_driver([MOCK_ACLS])
        acls = driver.get_acls()
        web = next(a for a in acls if a["name"] == "WEB-ACL")
        assert web["type"] == "extended"
        assert web["rules"][0]["source"] == "tcp any any eq 443"

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

    def test_users(self):
        driver = _make_driver([MOCK_SNMP])
        snmp = driver.get_snmp_config()
        assert len(snmp["users"]) == 1

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
        driver = _make_driver([MOCK_RUNNING_SECTION])
        section = driver.get_running_config_section("router bgp")
        assert "router bgp 65000" in section

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
        assert "hostname test-nxos" in config

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

    def test_default_vrf_rd(self):
        driver = _make_driver([MOCK_VRFS])
        vrfs = driver.get_vrfs()
        default = next(v for v in vrfs if v["name"] == "default")
        assert default["rd"] == ""

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_vrfs() == []


# ===========================================================================
# Tests: capabilities includes getter flags
# ===========================================================================


class TestCapabilities:
    def test_all_getters_enabled(self):
        driver = NxosDriver()
        caps = driver.capabilities()
        for getter in [
            "get_vlans",
            "get_vlan_detail",
            "get_interface_counters",
            "get_interface_errors",
            "get_ip_interfaces",
            "get_transceiver_info",
            "get_ospf_neighbors",
            "get_route_table",
            "get_route_summary",
            "get_mac_table",
            "get_stp_status",
            "get_port_channels",
            "get_environment",
            "get_ntp_status",
            "get_cpu_memory",
            "get_acls",
            "get_snmp_config",
            "get_running_config_section",
            "get_startup_config",
            "get_vrfs",
        ]:
            assert caps[getter] is True, f"{getter} should be True"
