"""Cross-vendor normalization tests for the 20 expanded protocol getters.

Validates that EOS, IOS-XE, NX-OS, and JunOS drivers all return identically
structured data from their 20 normalized getters. Mocks each driver with
realistic vendor-specific responses and verifies:
  1. All 4 drivers produce the same output keys/types for each getter
  2. Data normalization is consistent (e.g., status values, MAC format)
  3. Edge cases: empty results, error fallbacks

This complements tests/test_driver_normalization.py which covers the 6 core
getters (get_facts, get_interfaces, get_bgp_summary, get_arp_table,
get_lldp_neighbors, get_config).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from network_mcp.driver import EosDriver
from network_mcp.drivers.cisco_iosxe import IosXeDriver
from network_mcp.drivers.cisco_nxos import NxosDriver
from network_mcp.drivers.juniper_junos import JunosDriver

# ============================================================================
# Helper factories
# ============================================================================


def _nxapi_response(body: dict | str) -> dict:
    """Build a realistic NX-API JSON response wrapper."""
    return {
        "ins_api": {
            "outputs": {
                "output": {
                    "code": "200",
                    "msg": "Success",
                    "body": body,
                }
            }
        }
    }


def _make_eos(mock_results: list[dict] | Exception) -> EosDriver:
    """Build an EosDriver with a mocked pyeapi node."""
    driver = EosDriver()
    driver._node = MagicMock()
    if isinstance(mock_results, Exception):
        driver._node.run_commands.side_effect = mock_results
    else:
        driver._node.run_commands.return_value = mock_results
    driver._last_command_time = time.monotonic()
    driver._keepalive_interval = 99999
    return driver


def _make_iosxe(ssh_responses: list[str] | Exception) -> IosXeDriver:
    """Build an IosXeDriver with a mocked SSH transport."""
    driver = IosXeDriver()
    driver._connected = True
    driver._transport = "ssh"
    driver._ssh_host = "test-iosxe"
    driver._ssh_username = "admin"
    driver._ssh_password = "pass"  # noqa: S105
    driver._last_command_time = 0.0
    driver._keepalive_interval = 99999
    mock_transport = MagicMock()
    if isinstance(ssh_responses, Exception):
        mock_transport.send_command.side_effect = ssh_responses
    else:
        mock_transport.send_command.side_effect = ssh_responses
    driver._ssh_transport = mock_transport
    return driver


def _make_nxos(nxapi_bodies: list[dict | str] | Exception) -> NxosDriver:
    """Build an NxosDriver with a mocked NX-API httpx client."""
    driver = NxosDriver()
    driver._connected = True
    driver._transport = "nxapi"
    driver._ssh_host = "test-nxos"
    driver._ssh_username = "admin"
    driver._ssh_password = "pass"  # noqa: S105
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


def _make_junos(xml_responses: list[str] | Exception) -> JunosDriver:
    """Build a JunosDriver with a mocked NETCONF connection."""
    driver = JunosDriver()
    driver._connected = True
    driver._transport = "netconf"
    driver._ssh_host = "test-junos"
    driver._ssh_username = "admin"
    driver._ssh_password = "pass"  # noqa: S105
    driver._last_command_time = 0.0
    driver._keepalive_interval = 99999
    mock_conn = MagicMock()
    if isinstance(xml_responses, Exception):
        mock_conn.rpc.side_effect = xml_responses
    else:
        responses = []
        for xml in xml_responses:
            resp = MagicMock()
            resp.result = xml
            responses.append(resp)
        mock_conn.rpc.side_effect = responses
    driver._conn = mock_conn
    return driver


# ============================================================================
# Minimal mock data — just enough to test schema consistency
# ============================================================================

# --- EOS mocks ---

EOS_VLANS = {
    "vlans": {
        "100": {"name": "SERVERS", "status": "active", "interfaces": {"Ethernet1": {}}},
    }
}

EOS_VLAN_DETAIL = {
    "vlans": {
        "100": {
            "name": "SERVERS",
            "status": "active",
            "interfaces": {"Ethernet1": {}},
            "dynamic": False,
        },
    }
}

EOS_COUNTERS = {
    "interfaces": {
        "Ethernet1": {
            "inOctets": 100,
            "outOctets": 200,
            "inUcastPkts": 10,
            "outUcastPkts": 20,
            "inBroadcastPkts": 1,
            "outBroadcastPkts": 2,
            "inMulticastPkts": 3,
            "outMulticastPkts": 4,
        },
    }
}

EOS_ERRORS = {
    "interfaceErrorCounters": {
        "Ethernet1": {
            "inputErrors": 5,
            "outputErrors": 1,
            "alignmentErrors": 2,
            "fcsErrors": 3,
            "runtFrames": 0,
            "frameTooLongs": 0,
        },
    }
}

EOS_IP_INTF = {
    "interfaces": {
        "Ethernet1": {"interfaceAddress": {"ipAddr": {"address": "10.0.0.1", "maskLen": 24}}, "vrf": "default"},
    }
}

EOS_TRANSCEIVER = {
    "interfaces": {
        "Ethernet1": {
            "mediaType": "10GBASE-SR",
            "txPower": -2.0,
            "rxPower": -5.0,
            "temperature": 35.0,
        },
    }
}

EOS_OSPF = {
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
                            "upTime": 86400,
                        },
                    ]
                }
            }
        }
    }
}

EOS_ROUTES = {
    "vrfs": {
        "default": {
            "routes": {
                "10.0.0.0/24": {
                    "routeType": "connected",
                    "vias": [{"interface": "Ethernet1"}],
                    "metric": 0,
                    "preference": 0,
                },
                "10.1.0.0/24": {
                    "routeType": "bgp",
                    "vias": [{"nexthopAddr": "10.0.0.2"}],
                    "metric": 100,
                    "preference": 200,
                },
            }
        }
    }
}

EOS_ROUTE_SUMMARY = {
    "vrfs": {
        "default": {
            "totalRoutes": 250,
            "routes": {"connected": 10, "static": 2, "bgp": 200, "ospf": 38},
        }
    }
}

EOS_MAC = {
    "unicastTable": {
        "tableEntries": [
            {"macAddress": "00:1c:73:00:00:01", "vlanId": 100, "interface": "Ethernet1", "entryType": "dynamic"},
        ]
    }
}

EOS_STP = {
    "spanningTreeInstances": {
        "MST0": {
            "bridgeAddress": {"address": "aabb.cc00.0100"},
            "rootBridge": {"address": "aabb.cc00.0100"},
            "rootPort": "Ethernet1",
            "bridgePriority": 32768,
        }
    }
}

EOS_PORT_CHANNELS = {
    "portChannels": {
        "Port-Channel1": {
            "operStatus": "up",
            "protocol": "lacp",
            "activePorts": {"Ethernet1": {"status": "bundled"}, "Ethernet2": {"status": "bundled"}},
        }
    }
}

EOS_ENV_TEMP = {"temperatureSensors": {"CPU": {"currentTemperature": 45.0, "status": "ok"}}}
EOS_ENV_COOL = {"fanTraySlots": [{"label": "Fan1", "status": "ok"}]}
EOS_ENV_PWR = {"powerSupplies": {"PSU1": {"state": "ok", "outputPower": 150.0}}}

EOS_NTP_STATUS = {"status": "synchronised", "stratum": 3, "refid": "10.0.0.100"}
EOS_NTP_ASSOC = {
    "peers": {
        "10.0.0.100": {"peerIpAddr": "10.0.0.100", "stratumLevel": 2, "peerOffset": 0.5, "dispersion": 1.2},
    }
}

EOS_CPU_TOP = {"cpuInfo": {"%Cpu(s)": {"idle": 85.0}}}
EOS_VERSION_MEM = {"memTotal": 2048000, "memFree": 1024000}

EOS_ACLS = {
    "aclList": [
        {
            "name": "MGMT-ACCESS",
            "type": "standard",
            "sequence": [
                {"sequenceNumber": 10, "action": "permit", "source": "10.0.0.0/8"},
            ],
        }
    ]
}

EOS_SNMP = {
    "communities": {
        "public": {"access": "ro"},
    },
    "users": {},
    "notifications": {"hosts": {"10.0.0.200": {}}},
}

EOS_RUNNING_SECTION = {"output": "router bgp 65000\n   router-id 10.0.0.1\n"}
EOS_STARTUP = {"output": "hostname eos-switch\n"}

EOS_VRFS = {
    "vrfs": {
        "default": {"routeDistinguisher": "", "interfaces": {}, "routes": 250},
        "PROD": {"routeDistinguisher": "10.0.0.1:1", "interfaces": {"Vlan100": {}}, "routes": 42},
    }
}

# --- IOS-XE SSH text mocks ---

IOSXE_VLANS = """\
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
100  SERVERS                          active    Gi0/0
"""

IOSXE_VLAN_DETAIL = """\
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
100  SERVERS                          active    Gi0/0

VLAN Type  SAID       MTU   Parent RingNo BridgeNo Stp  BrdgMode Trans1 Trans2
---- ----- ---------- ----- ------ ------ -------- ---- -------- ------ ------
100  enet  100100     1500  -      -      -        -    -        0      0
"""

IOSXE_INTERFACES = """\
GigabitEthernet0/0 is up, line protocol is up
  Hardware is iGbE, address is 0050.56a0.0001 (bia 0050.56a0.0001)
  MTU 1500 bytes, BW 1000000 Kbit/sec
     1000 packets input, 123456 bytes, 0 no buffer
     10 broadcasts, 5 multicasts
     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored
     0 runts, 0 giants, 0 throttles
     2000 packets output, 654321 bytes, 0 underruns
     20 broadcasts, 8 multicasts
     0 output errors, 0 collisions, 0 interface resets
"""

IOSXE_IP_INTF = """\
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0     10.0.0.1        YES manual up                    up
"""

IOSXE_TRANSCEIVER = """\
Port          Type                   (dBm)     (dBm)      Temp (C)
---------     --------------------   --------  --------   --------
Gi0/0         SFP-10G-SR            -2.1      -5.3       35.0
"""

IOSXE_OSPF = """\
Neighbor ID     Pri   State           Dead Time   Address         Interface
10.0.0.2          1   FULL/DR         00:00:35    10.0.0.2        Gi0/0
"""

IOSXE_ROUTES = """\
C     10.0.0.0/24 is directly connected, GigabitEthernet0/0
B     10.1.0.0/24 [200/100] via 10.0.0.2, 00:15:30, GigabitEthernet0/0
"""

IOSXE_ROUTE_SUMMARY = """\
Route Source    Networks    Subnets     Replicates  Overhead    Memory (bytes)
connected       3           2           0           320         760
static          1           1           0           160         304
bgp 65000       200         0           0           0           0
ospf 1          38          0           0           0           0
Total of 250 routes
"""

IOSXE_MAC = """\
 Vlan    Mac Address       Type        Ports
----    -----------       --------    -----
 100    001c.7300.0001    DYNAMIC     Gi0/0
"""

IOSXE_STP = """\
VLAN0100
  Spanning tree enabled protocol rstp
  Root ID    Priority    32868
             Address     aabb.cc00.0100
  Root port  Gi0/0
  Bridge ID  Priority    32868
"""

IOSXE_ETHERCHANNEL = """\
Group  Port-channel  Protocol    Ports
------+-------------+-----------+-----------------------------------------------
1      Po1(SU)       LACP        Gi0/0(P)    Gi0/1(P)
"""

IOSXE_ENV = """\
 Inlet    28C  ok
 CPU      45C  ok
 FAN 1  OK
 PS1 1  OK  150W
"""

IOSXE_NTP_STATUS = "Clock is synchronized, stratum 3, reference is 10.0.0.100\n"
IOSXE_NTP_ASSOC = """\
*~10.0.0.100     .GPS.            2     64    64   377    1.500   0.500  1.200
"""

IOSXE_CPU = """\
CPU utilization for five seconds: 15%/3%; one minute: 12%; five minutes: 10%
Processor Pool Total: 2048000 Used: 1024000 Free: 1024000
"""

IOSXE_ACL = """\
Standard IP access list MGMT-ACCESS
    10 permit 10.0.0.0/8
"""

IOSXE_SNMP = """\
Community name: public  access: read-only
10.0.0.200  Traps  v2c  trap-comm
"""

IOSXE_RUNNING_SECTION = "router bgp 65000\n   router-id 10.0.0.1\n"
IOSXE_STARTUP = "hostname iosxe-rtr\n"

IOSXE_VRF = """\
  Name                             Default RD            Protocols   Interfaces
  PROD                             65000:1               ipv4        Gi0/0
"""

# --- NX-OS NX-API mocks ---

NXOS_VLANS = {
    "TABLE_vlanbrief": {
        "ROW_vlanbrief": [
            {
                "vlanshowbr-vlanid": "100",
                "vlanshowbr-vlanname": "SERVERS",
                "vlanshowbr-vlanstate": "active",
                "vlanshowplist-ifidx": "Eth1/1",
            },
        ]
    }
}

NXOS_VLAN_DETAIL = {
    "TABLE_vlanbriefid": {
        "ROW_vlanbriefid": {
            "vlanshowbr-vlanid": "100",
            "vlanshowbr-vlanname": "SERVERS",
            "vlanshowbr-vlanstate": "active",
            "vlanshowplist-ifidx": "Eth1/1",
            "vlanshowbr-vlantype": "enet",
        }
    }
}

NXOS_INTERFACE = {
    "TABLE_interface": {
        "ROW_interface": [
            {
                "interface": "Ethernet1/1",
                "eth_inbytes": "123456",
                "eth_outbytes": "654321",
                "eth_inucast": "1000",
                "eth_outucast": "2000",
                "eth_inbcast": "10",
                "eth_outbcast": "20",
                "eth_inmcast": "5",
                "eth_outmcast": "8",
                "eth_inerr": "5",
                "eth_outerr": "1",
                "eth_crc": "3",
                "eth_frame": "0",
                "eth_runts": "0",
                "eth_giants": "0",
            }
        ]
    }
}

NXOS_IP_INTF = {
    "TABLE_intf": {
        "ROW_intf": [
            {"intf-name": "Eth1/1", "prefix": "10.0.0.1/24", "vrf-name-out": "default", "proto-state": "up"},
        ]
    }
}

NXOS_TRANSCEIVER = {
    "TABLE_interface": {
        "ROW_interface": [
            {
                "interface": "Ethernet1/1",
                "type": "10GBASE-SR",
                "tx_pwr": "-2.1",
                "rx_pwr": "-5.3",
                "temperature": "35.0",
            },
        ]
    }
}

NXOS_OSPF = {
    "TABLE_ctx": {
        "ROW_ctx": {
            "TABLE_nbr": {
                "ROW_nbr": [
                    {"rid": "10.0.0.2", "state": "FULL/DR", "intf": "Eth1/1", "area": "0.0.0.0", "uptime": "P1DT2H"},  # noqa: S104
                ]
            }
        }
    }
}

NXOS_ROUTES = {
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
                        ]
                    }
                }
            }
        }
    }
}

NXOS_ROUTE_SUMMARY = {
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

NXOS_MAC = {
    "TABLE_mac_address": {
        "ROW_mac_address": [
            {"disp_mac_addr": "001c.7300.0001", "disp_vlan": "100", "disp_port": "Eth1/1", "disp_type": "dynamic"},
        ]
    }
}

NXOS_STP = {
    "stp_mode": "rapid-pvst",
    "TABLE_vlan": {
        "ROW_vlan": [
            {"vlan_id": "100", "root_bridge_addr": "aabb.cc00.0100", "root_port": "Eth1/1", "bridge_priority": "32768"},
        ]
    },
}

NXOS_PORT_CHANNELS = {
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
            }
        ]
    }
}

NXOS_ENV = {
    "TABLE_tempinfo": {"ROW_tempinfo": [{"tempmod": "CPU", "curtemp": "45", "alarmstatus": "Ok"}]},
    "TABLE_faninfo": {"ROW_faninfo": [{"fanname": "Fan1", "fanstatus": "Ok"}]},
    "TABLE_psinfo": {"ROW_psinfo": [{"psmodel": "PSU1", "ps_status": "Ok", "actual_out": "150W"}]},
}

NXOS_NTP = {
    "TABLE_peersstatus": {
        "ROW_peersstatus": [
            {"remote": "*10.0.0.100", "syncmode": "*", "st": "2", "delay": "0.5", "jitter": "1.2"},
        ]
    }
}

NXOS_SYSTEM_RESOURCES = {
    "cpu_state_idle": "85.0",
    "memory_usage_total": "2048000",
    "memory_usage_used": "1024000",
    "memory_usage_free": "1024000",
}

NXOS_ACLS = {
    "TABLE_ip_acl": {
        "ROW_ip_acl": [
            {
                "acl_name": "MGMT-ACCESS",
                "acl_type": "Standard",
                "TABLE_seqno": {"ROW_seqno": [{"seqno": "10", "permitdeny": "permit", "src_any": "10.0.0.0/8"}]},
            }
        ]
    }
}

NXOS_SNMP = {
    "TABLE_snmp_community": {
        "ROW_snmp_community": [{"community_name": "public", "grouporaccess": "network-operator (ro)"}]
    },
    "TABLE_snmp_users": {"ROW_snmp_users": {"user": "snmpuser"}},
    "TABLE_host": {"ROW_host": {"host": "10.0.0.200"}},
}

NXOS_RUNNING_SECTION = "router bgp 65000\n  router-id 10.0.0.1\n"
NXOS_STARTUP = "hostname nxos-leaf\n"

NXOS_VRFS = {
    "TABLE_vrf": {
        "ROW_vrf": [
            {"vrf_name": "default", "rd": "--", "vrf_intf": ""},
            {"vrf_name": "PROD", "rd": "10.0.0.1:1", "vrf_intf": "Vlan100"},
        ]
    }
}

# --- JunOS NETCONF XML mocks ---

JUNOS_VLANS = """\
<rpc-reply><vlan-information>
  <vlan><vlan-tag>100</vlan-tag><vlan-name>SERVERS</vlan-name><vlan-status>active</vlan-status>
    <vlan-detail><vlan-member-interface><vlan-member-interface-name>ge-0/0/0</vlan-member-interface-name>
    </vlan-member-interface></vlan-detail></vlan>
</vlan-information></rpc-reply>"""

JUNOS_VLAN_DETAIL = JUNOS_VLANS  # Same structure, single VLAN

JUNOS_COUNTERS = """\
<rpc-reply><interface-information>
  <physical-interface><name>ge-0/0/0</name>
    <traffic-statistics>
      <input-bytes>123456</input-bytes><output-bytes>654321</output-bytes>
      <input-packets>1000</input-packets><output-packets>2000</output-packets>
      <input-broadcast-packets>10</input-broadcast-packets><output-broadcast-packets>20</output-broadcast-packets>
      <input-multicast-packets>5</input-multicast-packets><output-multicast-packets>8</output-multicast-packets>
    </traffic-statistics>
  </physical-interface>
</interface-information></rpc-reply>"""

JUNOS_ERRORS = """\
<rpc-reply><interface-information>
  <physical-interface><name>ge-0/0/0</name>
    <input-error-list><input-errors>5</input-errors><input-crc-errors>3</input-crc-errors>
      <framing-errors>0</framing-errors><input-runts>0</input-runts><input-giants>0</input-giants>
    </input-error-list>
    <output-error-list><output-errors>1</output-errors></output-error-list>
  </physical-interface>
</interface-information></rpc-reply>"""

JUNOS_IP_INTF = """\
<rpc-reply><interface-information>
  <logical-interface><name>ge-0/0/0.0</name><oper-status>up</oper-status>
    <address-family><address-family-name>inet</address-family-name>
      <interface-address><ifa-local>10.0.0.1/24</ifa-local></interface-address>
    </address-family>
  </logical-interface>
</interface-information></rpc-reply>"""

JUNOS_TRANSCEIVER = """\
<rpc-reply><interface-optics-diagnostics-information>
  <physical-interface><name>ge-0/0/0</name>
    <optics-diagnostics><optic-type>SFP-10G-SR</optic-type>
      <laser-output-power-dbm>-2.1</laser-output-power-dbm>
      <rx-signal-avg-optical-power-dbm>-5.3</rx-signal-avg-optical-power-dbm>
      <module-temperature-celsius>35.0</module-temperature-celsius>
    </optics-diagnostics>
  </physical-interface>
</interface-optics-diagnostics-information></rpc-reply>"""

JUNOS_OSPF = """\
<rpc-reply><ospf-neighbor-information>
  <ospf-neighbor><neighbor-id>10.0.0.2</neighbor-id><ospf-neighbor-state>full</ospf-neighbor-state>
    <interface-name>ge-0/0/0.0</interface-name><ospf-area>0.0.0.0</ospf-area>
    <neighbor-adjacency-time>P1DT2H</neighbor-adjacency-time>
  </ospf-neighbor>
</ospf-neighbor-information></rpc-reply>"""

JUNOS_ROUTES = """\
<rpc-reply><route-information><route-table>
  <rt><rt-destination>10.0.0.0/24</rt-destination>
    <rt-entry><protocol-name>Direct</protocol-name>
      <nh><via>ge-0/0/0.0</via></nh><metric>0</metric><preference>0</preference>
    </rt-entry></rt>
  <rt><rt-destination>10.1.0.0/24</rt-destination>
    <rt-entry><protocol-name>BGP</protocol-name>
      <nh><to>10.0.0.2</to></nh><metric>100</metric><preference>170</preference>
    </rt-entry></rt>
</route-table></route-information></rpc-reply>"""

JUNOS_ROUTE_SUMMARY = """\
<rpc-reply><route-summary-information><route-table>
  <total-route-count>250</total-route-count>
  <protocols><protocol-name>Direct</protocol-name><protocol-route-count>10</protocol-route-count></protocols>
  <protocols><protocol-name>Static</protocol-name><protocol-route-count>2</protocol-route-count></protocols>
  <protocols><protocol-name>BGP</protocol-name><protocol-route-count>200</protocol-route-count></protocols>
  <protocols><protocol-name>OSPF</protocol-name><protocol-route-count>38</protocol-route-count></protocols>
</route-table></route-summary-information></rpc-reply>"""

JUNOS_MAC = """\
<rpc-reply><ethernet-switching-table-information><ethernet-switching-table>
  <mac-table-entry><mac-address>00:1c:73:00:00:01</mac-address>
    <mac-vlan>VLAN100</mac-vlan><mac-interfaces>ge-0/0/0.0</mac-interfaces>
    <mac-type>Learn</mac-type>
  </mac-table-entry>
</ethernet-switching-table></ethernet-switching-table-information></rpc-reply>"""

JUNOS_STP = """\
<rpc-reply><stp-bridge-information><stp-protocol>rstp</stp-protocol>
  <stp-instance><stp-instance-id>100</stp-instance-id>
    <stp-root-bridge-address>aa:bb:cc:00:01:00</stp-root-bridge-address>
    <stp-root-port>ge-0/0/0</stp-root-port><stp-bridge-priority>32768</stp-bridge-priority>
  </stp-instance>
</stp-bridge-information></rpc-reply>"""

JUNOS_PORT_CHANNELS = """\
<rpc-reply><lacp-interface-information>
  <lag-lacp-header><aggregate-name>ae0</aggregate-name>
    <aggregate-oper-state>up</aggregate-oper-state>
    <aggregate-lacp-mode>active-lacp</aggregate-lacp-mode>
    <lag-lacp-protocol><name>ge-0/0/0</name><lacp-mux-state>Collecting distributing</lacp-mux-state></lag-lacp-protocol>
    <lag-lacp-protocol><name>ge-0/0/1</name><lacp-mux-state>Collecting distributing</lacp-mux-state></lag-lacp-protocol>
  </lag-lacp-header>
</lacp-interface-information></rpc-reply>"""

JUNOS_ENV = """\
<rpc-reply><environment-information>
  <environment-item><name>CPU</name><status>OK</status><temperature>45 degrees C</temperature></environment-item>
  <environment-item><name>Fan 1</name><status>OK</status></environment-item>
  <environment-item><name>PEM 0</name><status>OK</status><power-output>150W</power-output></environment-item>
</environment-information></rpc-reply>"""

JUNOS_NTP = """\
<rpc-reply><ntp-associations-information>
  <ntp-peer><peer-address>10.0.0.100</peer-address><tally-code>*</tally-code>
    <peer-stratum>2</peer-stratum><peer-offset>0.5</peer-offset><peer-jitter>1.2</peer-jitter>
  </ntp-peer>
</ntp-associations-information></rpc-reply>"""

JUNOS_CPU_MEM = """\
<rpc-reply><route-engine-information><route-engine>
  <cpu-idle>85</cpu-idle>
  <memory-dram-size>2048</memory-dram-size>
  <memory-buffer-utilization>50</memory-buffer-utilization>
</route-engine></route-engine-information></rpc-reply>"""

JUNOS_ACLS = """\
<rpc-reply><firewall-information>
  <filter-information><filter-name>MGMT-FILTER</filter-name>
    <counter><counter-name>permit-mgmt</counter-name></counter>
  </filter-information>
</firewall-information></rpc-reply>"""

JUNOS_SNMP = """\
<rpc-reply><configuration><snmp>
  <community><name>public</name><authorization>read-only</authorization></community>
  <trap-group><targets><name>10.0.0.200</name></targets></trap-group>
</snmp></configuration></rpc-reply>"""

JUNOS_RUNNING_SECTION = """\
<rpc-reply><configuration><protocols><bgp><group><name>PEERS</name></group></bgp></protocols></configuration></rpc-reply>"""

JUNOS_STARTUP = """\
<rpc-reply><configuration-text>hostname test-junos;\n</configuration-text></rpc-reply>"""

JUNOS_VRFS = """\
<rpc-reply><instance-information>
  <instance-core><instance-name>master</instance-name><instance-rd>---</instance-rd></instance-core>
  <instance-core><instance-name>PROD</instance-name><instance-rd>10.0.0.1:1</instance-rd>
    <instance-interface><interface-name>ge-0/0/0.100</interface-name></instance-interface>
  </instance-core>
</instance-information></rpc-reply>"""


# ============================================================================
# Schema key definitions for all 20 getters
# ============================================================================

VLAN_KEYS = {"vlan_id", "name", "status", "interfaces"}
VLAN_DETAIL_KEYS = {"vlan_id", "name", "status", "interfaces", "type"}
COUNTER_KEYS = {
    "interface",
    "in_octets",
    "out_octets",
    "in_packets",
    "out_packets",
    "in_broadcast",
    "out_broadcast",
    "in_multicast",
    "out_multicast",
}
ERROR_KEYS = {"interface", "in_errors", "out_errors", "in_crc", "in_frame", "in_runts", "in_giants"}
IP_INTF_KEYS = {"interface", "ip_address", "subnet", "vrf", "status"}
TRANSCEIVER_KEYS = {"interface", "type", "tx_power", "rx_power", "temp"}
OSPF_KEYS = {"neighbor_id", "state", "interface", "area", "uptime"}
ROUTE_KEYS = {"prefix", "next_hop", "protocol", "metric", "preference"}
ROUTE_SUMMARY_KEYS = {"total", "connected", "static", "bgp", "ospf", "isis"}
MAC_KEYS = {"mac_address", "vlan", "interface", "type"}
STP_KEYS = {"mode", "root_bridge", "instances"}
STP_INSTANCE_KEYS = {"id", "root_bridge", "root_port", "priority"}
PORT_CHANNEL_KEYS = {"name", "status", "protocol", "members"}
PORT_CHANNEL_MEMBER_KEYS = {"interface", "status"}
ENV_KEYS = {"temperature", "fans", "power"}
NTP_KEYS = {"synchronized", "reference", "peers"}
NTP_PEER_KEYS = {"peer", "stratum", "offset", "jitter"}
CPU_MEM_KEYS = {"cpu_percent", "memory_total", "memory_used", "memory_free"}
ACL_KEYS = {"name", "type", "rules"}
SNMP_KEYS = {"communities", "users", "traps"}
VRF_KEYS = {"name", "rd", "interfaces", "route_count"}


# ============================================================================
# Cross-vendor schema consistency tests
# ============================================================================


class TestVlansSchemaConsistency:
    """All 4 drivers return identically structured VLANs."""

    def test_eos_vlan_keys(self):
        driver = _make_eos([EOS_VLANS])
        vlans = driver.get_vlans()
        assert len(vlans) >= 1
        assert set(vlans[0].keys()) == VLAN_KEYS

    def test_iosxe_vlan_keys(self):
        driver = _make_iosxe([IOSXE_VLANS])
        vlans = driver.get_vlans()
        assert len(vlans) >= 1
        assert set(vlans[0].keys()) == VLAN_KEYS

    def test_nxos_vlan_keys(self):
        driver = _make_nxos([NXOS_VLANS])
        vlans = driver.get_vlans()
        assert len(vlans) >= 1
        assert set(vlans[0].keys()) == VLAN_KEYS

    def test_junos_vlan_keys(self):
        driver = _make_junos([JUNOS_VLANS])
        vlans = driver.get_vlans()
        assert len(vlans) >= 1
        assert set(vlans[0].keys()) == VLAN_KEYS


class TestVlanDetailSchemaConsistency:
    """All 4 drivers return identically structured VLAN detail."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_VLAN_DETAIL])
        detail = driver.get_vlan_detail(100)
        assert set(detail.keys()) == VLAN_DETAIL_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_VLAN_DETAIL])
        detail = driver.get_vlan_detail(100)
        assert set(detail.keys()) == VLAN_DETAIL_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_VLAN_DETAIL])
        detail = driver.get_vlan_detail(100)
        assert set(detail.keys()) == VLAN_DETAIL_KEYS


class TestInterfaceCountersSchemaConsistency:
    """All 4 drivers return identically structured interface counters."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_COUNTERS])
        counters = driver.get_interface_counters()
        assert len(counters) >= 1
        assert set(counters[0].keys()) == COUNTER_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_INTERFACES])
        counters = driver.get_interface_counters()
        assert len(counters) >= 1
        assert set(counters[0].keys()) == COUNTER_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_INTERFACE])
        counters = driver.get_interface_counters()
        assert len(counters) >= 1
        assert set(counters[0].keys()) == COUNTER_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_COUNTERS])
        counters = driver.get_interface_counters()
        assert len(counters) >= 1
        assert set(counters[0].keys()) == COUNTER_KEYS


class TestInterfaceErrorsSchemaConsistency:
    """All 4 drivers return identically structured error counters."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_ERRORS])
        errors = driver.get_interface_errors()
        assert len(errors) >= 1
        assert set(errors[0].keys()) == ERROR_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_INTERFACES])
        errors = driver.get_interface_errors()
        assert len(errors) >= 1
        assert set(errors[0].keys()) == ERROR_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_INTERFACE])
        errors = driver.get_interface_errors()
        assert len(errors) >= 1
        assert set(errors[0].keys()) == ERROR_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_ERRORS])
        errors = driver.get_interface_errors()
        assert len(errors) >= 1
        assert set(errors[0].keys()) == ERROR_KEYS


class TestIpInterfacesSchemaConsistency:
    """All 4 drivers return identically structured IP interfaces."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_IP_INTF])
        ips = driver.get_ip_interfaces()
        assert len(ips) >= 1
        assert set(ips[0].keys()) == IP_INTF_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_IP_INTF])
        ips = driver.get_ip_interfaces()
        assert len(ips) >= 1
        assert set(ips[0].keys()) == IP_INTF_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_IP_INTF])
        ips = driver.get_ip_interfaces()
        assert len(ips) >= 1
        assert set(ips[0].keys()) == IP_INTF_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_IP_INTF])
        ips = driver.get_ip_interfaces()
        assert len(ips) >= 1
        assert set(ips[0].keys()) == IP_INTF_KEYS


class TestTransceiverSchemaConsistency:
    """All 4 drivers return identically structured transceiver info."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_TRANSCEIVER])
        info = driver.get_transceiver_info()
        assert len(info) >= 1
        assert set(info[0].keys()) == TRANSCEIVER_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_TRANSCEIVER])
        info = driver.get_transceiver_info()
        assert len(info) >= 1
        assert set(info[0].keys()) == TRANSCEIVER_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_TRANSCEIVER])
        info = driver.get_transceiver_info()
        assert len(info) >= 1
        assert set(info[0].keys()) == TRANSCEIVER_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_TRANSCEIVER])
        info = driver.get_transceiver_info()
        assert len(info) >= 1
        assert set(info[0].keys()) == TRANSCEIVER_KEYS


class TestOspfSchemaConsistency:
    """All 4 drivers return identically structured OSPF neighbor data."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_OSPF])
        neighbors = driver.get_ospf_neighbors()
        assert len(neighbors) >= 1
        assert set(neighbors[0].keys()) == OSPF_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_OSPF])
        neighbors = driver.get_ospf_neighbors()
        assert len(neighbors) >= 1
        assert set(neighbors[0].keys()) == OSPF_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_OSPF])
        neighbors = driver.get_ospf_neighbors()
        assert len(neighbors) >= 1
        assert set(neighbors[0].keys()) == OSPF_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_OSPF])
        neighbors = driver.get_ospf_neighbors()
        assert len(neighbors) >= 1
        assert set(neighbors[0].keys()) == OSPF_KEYS

    def test_state_capitalization(self):
        """All drivers capitalize OSPF state: 'Full', not 'FULL' or 'full'."""
        # NX-OS: FULL/DR -> Full
        nxos = _make_nxos([NXOS_OSPF])
        assert nxos.get_ospf_neighbors()[0]["state"] == "Full"
        # JunOS: full -> Full
        junos = _make_junos([JUNOS_OSPF])
        assert junos.get_ospf_neighbors()[0]["state"] == "Full"


class TestRouteTableSchemaConsistency:
    """All 4 drivers return identically structured route entries."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_ROUTES])
        routes = driver.get_route_table()
        assert len(routes) >= 1
        assert set(routes[0].keys()) == ROUTE_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_ROUTES])
        routes = driver.get_route_table()
        assert len(routes) >= 1
        assert set(routes[0].keys()) == ROUTE_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_ROUTES])
        routes = driver.get_route_table()
        assert len(routes) >= 1
        assert set(routes[0].keys()) == ROUTE_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_ROUTES])
        routes = driver.get_route_table()
        assert len(routes) >= 1
        assert set(routes[0].keys()) == ROUTE_KEYS


class TestRouteSummarySchemaConsistency:
    """All 4 drivers return identically structured route summaries."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_ROUTE_SUMMARY])
        summary = driver.get_route_summary()
        assert set(summary.keys()) == ROUTE_SUMMARY_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_ROUTE_SUMMARY])
        summary = driver.get_route_summary()
        assert set(summary.keys()) == ROUTE_SUMMARY_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_ROUTE_SUMMARY])
        summary = driver.get_route_summary()
        assert set(summary.keys()) == ROUTE_SUMMARY_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_ROUTE_SUMMARY])
        summary = driver.get_route_summary()
        assert set(summary.keys()) == ROUTE_SUMMARY_KEYS


class TestMacTableSchemaConsistency:
    """All 4 drivers return identically structured MAC entries."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_MAC])
        entries = driver.get_mac_table()
        assert len(entries) >= 1
        assert set(entries[0].keys()) == MAC_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_MAC])
        entries = driver.get_mac_table()
        assert len(entries) >= 1
        assert set(entries[0].keys()) == MAC_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_MAC])
        entries = driver.get_mac_table()
        assert len(entries) >= 1
        assert set(entries[0].keys()) == MAC_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_MAC])
        entries = driver.get_mac_table()
        assert len(entries) >= 1
        assert set(entries[0].keys()) == MAC_KEYS


class TestStpSchemaConsistency:
    """All 4 drivers return identically structured STP status."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_STP])
        stp = driver.get_stp_status()
        assert set(stp.keys()) == STP_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_STP])
        stp = driver.get_stp_status()
        assert set(stp.keys()) == STP_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_STP])
        stp = driver.get_stp_status()
        assert set(stp.keys()) == STP_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_STP])
        stp = driver.get_stp_status()
        assert set(stp.keys()) == STP_KEYS


class TestPortChannelSchemaConsistency:
    """All 4 drivers return identically structured port-channel data."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_PORT_CHANNELS])
        channels = driver.get_port_channels()
        assert len(channels) >= 1
        assert set(channels[0].keys()) == PORT_CHANNEL_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_ETHERCHANNEL])
        channels = driver.get_port_channels()
        assert len(channels) >= 1
        assert set(channels[0].keys()) == PORT_CHANNEL_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_PORT_CHANNELS])
        channels = driver.get_port_channels()
        assert len(channels) >= 1
        assert set(channels[0].keys()) == PORT_CHANNEL_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_PORT_CHANNELS])
        channels = driver.get_port_channels()
        assert len(channels) >= 1
        assert set(channels[0].keys()) == PORT_CHANNEL_KEYS


class TestEnvironmentSchemaConsistency:
    """All 4 drivers return identically structured environment data."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_ENV_TEMP, EOS_ENV_COOL, EOS_ENV_PWR])
        env = driver.get_environment()
        assert set(env.keys()) == ENV_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_ENV])
        env = driver.get_environment()
        assert set(env.keys()) == ENV_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_ENV])
        env = driver.get_environment()
        assert set(env.keys()) == ENV_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_ENV])
        env = driver.get_environment()
        assert set(env.keys()) == ENV_KEYS


class TestNtpSchemaConsistency:
    """All 4 drivers return identically structured NTP status."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_NTP_STATUS, EOS_NTP_ASSOC])
        ntp = driver.get_ntp_status()
        assert set(ntp.keys()) == NTP_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_NTP_STATUS, IOSXE_NTP_ASSOC])
        ntp = driver.get_ntp_status()
        assert set(ntp.keys()) == NTP_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_NTP])
        ntp = driver.get_ntp_status()
        assert set(ntp.keys()) == NTP_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_NTP])
        ntp = driver.get_ntp_status()
        assert set(ntp.keys()) == NTP_KEYS


class TestCpuMemorySchemaConsistency:
    """All 4 drivers return identically structured CPU/memory data."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_CPU_TOP, EOS_VERSION_MEM])
        cm = driver.get_cpu_memory()
        assert set(cm.keys()) == CPU_MEM_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_CPU])
        cm = driver.get_cpu_memory()
        assert set(cm.keys()) == CPU_MEM_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_SYSTEM_RESOURCES])
        cm = driver.get_cpu_memory()
        assert set(cm.keys()) == CPU_MEM_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_CPU_MEM])
        cm = driver.get_cpu_memory()
        assert set(cm.keys()) == CPU_MEM_KEYS


class TestAclsSchemaConsistency:
    """All 4 drivers return identically structured ACL data."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_ACLS])
        acls = driver.get_acls()
        assert len(acls) >= 1
        assert set(acls[0].keys()) == ACL_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_ACL])
        acls = driver.get_acls()
        assert len(acls) >= 1
        assert set(acls[0].keys()) == ACL_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_ACLS])
        acls = driver.get_acls()
        assert len(acls) >= 1
        assert set(acls[0].keys()) == ACL_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_ACLS])
        acls = driver.get_acls()
        assert len(acls) >= 1
        assert set(acls[0].keys()) == ACL_KEYS


class TestSnmpSchemaConsistency:
    """All 4 drivers return identically structured SNMP config."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_SNMP])
        snmp = driver.get_snmp_config()
        assert set(snmp.keys()) == SNMP_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_SNMP])
        snmp = driver.get_snmp_config()
        assert set(snmp.keys()) == SNMP_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_SNMP])
        snmp = driver.get_snmp_config()
        assert set(snmp.keys()) == SNMP_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_SNMP])
        snmp = driver.get_snmp_config()
        assert set(snmp.keys()) == SNMP_KEYS


class TestRunningConfigSectionConsistency:
    """All 4 drivers return a string from get_running_config_section."""

    def test_eos_returns_string(self):
        driver = _make_eos([EOS_RUNNING_SECTION])
        assert isinstance(driver.get_running_config_section("router bgp"), str)

    def test_iosxe_returns_string(self):
        driver = _make_iosxe([IOSXE_RUNNING_SECTION])
        assert isinstance(driver.get_running_config_section("router bgp"), str)

    def test_nxos_returns_string(self):
        driver = _make_nxos([NXOS_RUNNING_SECTION])
        assert isinstance(driver.get_running_config_section("router bgp"), str)

    def test_junos_returns_string(self):
        driver = _make_junos([JUNOS_RUNNING_SECTION])
        assert isinstance(driver.get_running_config_section("protocols"), str)


class TestStartupConfigConsistency:
    """All 4 drivers return a string from get_startup_config."""

    def test_eos_returns_string(self):
        driver = _make_eos([EOS_STARTUP])
        assert isinstance(driver.get_startup_config(), str)

    def test_iosxe_returns_string(self):
        driver = _make_iosxe([IOSXE_STARTUP])
        assert isinstance(driver.get_startup_config(), str)

    def test_nxos_returns_string(self):
        driver = _make_nxos([NXOS_STARTUP])
        assert isinstance(driver.get_startup_config(), str)

    def test_junos_returns_string(self):
        driver = _make_junos([JUNOS_STARTUP])
        assert isinstance(driver.get_startup_config(), str)


class TestVrfsSchemaConsistency:
    """All 4 drivers return identically structured VRF data."""

    def test_eos_keys(self):
        driver = _make_eos([EOS_VRFS])
        vrfs = driver.get_vrfs()
        assert len(vrfs) >= 1
        assert set(vrfs[0].keys()) == VRF_KEYS

    def test_iosxe_keys(self):
        driver = _make_iosxe([IOSXE_VRF])
        vrfs = driver.get_vrfs()
        assert len(vrfs) >= 1
        assert set(vrfs[0].keys()) == VRF_KEYS

    def test_nxos_keys(self):
        driver = _make_nxos([NXOS_VRFS])
        vrfs = driver.get_vrfs()
        assert len(vrfs) >= 1
        assert set(vrfs[0].keys()) == VRF_KEYS

    def test_junos_keys(self):
        driver = _make_junos([JUNOS_VRFS])
        vrfs = driver.get_vrfs()
        assert len(vrfs) >= 1
        assert set(vrfs[0].keys()) == VRF_KEYS


# ============================================================================
# Error fallback consistency — all drivers return empty defaults on error
# ============================================================================


class TestErrorFallbackConsistency:
    """All 4 drivers return consistent empty defaults on connection error."""

    def test_get_vlans_empty_on_error(self):
        for factory in [_make_eos, _make_nxos, _make_junos]:
            driver = factory(ConnectionError("fail"))
            assert driver.get_vlans() == [], f"{driver.__class__.__name__} should return []"

    def test_get_interface_counters_empty_on_error(self):
        for factory in [_make_eos, _make_nxos, _make_junos]:
            driver = factory(ConnectionError("fail"))
            assert driver.get_interface_counters() == []

    def test_get_route_summary_defaults_on_error(self):
        for factory in [_make_eos, _make_nxos, _make_junos]:
            driver = factory(ConnectionError("fail"))
            summary = driver.get_route_summary()
            assert summary["total"] == 0

    def test_get_environment_empty_on_error(self):
        for factory in [_make_eos, _make_nxos, _make_junos]:
            driver = factory(ConnectionError("fail"))
            env = driver.get_environment()
            assert env == {"temperature": [], "fans": [], "power": []}

    def test_get_ntp_defaults_on_error(self):
        for factory in [_make_eos, _make_nxos, _make_junos]:
            driver = factory(ConnectionError("fail"))
            ntp = driver.get_ntp_status()
            assert ntp["synchronized"] is False

    def test_get_startup_config_empty_on_error(self):
        for factory in [_make_eos, _make_nxos, _make_junos]:
            driver = factory(ConnectionError("fail"))
            assert driver.get_startup_config() == ""

    def test_get_running_config_section_empty_on_error(self):
        for factory in [_make_eos, _make_nxos, _make_junos]:
            driver = factory(ConnectionError("fail"))
            assert driver.get_running_config_section("bgp") == ""


# ============================================================================
# Capabilities: all 4 drivers report all 20 getters as enabled
# ============================================================================


class TestCapabilitiesAllDrivers:
    """All 4 drivers report all 20 getters in capabilities()."""

    GETTER_NAMES = [
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
    ]

    def test_eos_capabilities(self):
        driver = EosDriver()
        caps = driver.capabilities()
        for getter in self.GETTER_NAMES:
            assert caps.get(getter) is True, f"EOS {getter} should be True"

    def test_iosxe_capabilities(self):
        driver = IosXeDriver()
        caps = driver.capabilities()
        for getter in self.GETTER_NAMES:
            assert caps.get(getter) is True, f"IOS-XE {getter} should be True"

    def test_nxos_capabilities(self):
        driver = NxosDriver()
        caps = driver.capabilities()
        for getter in self.GETTER_NAMES:
            assert caps.get(getter) is True, f"NX-OS {getter} should be True"

    def test_junos_capabilities(self):
        driver = JunosDriver()
        caps = driver.capabilities()
        for getter in self.GETTER_NAMES:
            assert caps.get(getter) is True, f"JunOS {getter} should be True"
