"""Mock EOS responses for demo mode.

Topology:
    spine-01 (10.0.0.1, AS 65000)  ──── spine-02 (10.0.0.2, AS 65000)
       │  │  │  │                          │  │  │  │
       │  │  │  └─ leaf-04                 │  │  │  └─ leaf-04
       │  │  └─── leaf-03                  │  │  └─── leaf-03
       │  └────── leaf-02                  │  └────── leaf-02
       └──────── leaf-01                   └──────── leaf-01

    leaf-01 (10.0.1.1, AS 65001) ──MLAG── leaf-02 (10.0.1.2, AS 65001)
    leaf-03 (10.0.2.1, AS 65002) ──MLAG── leaf-04 (10.0.2.2, AS 65002)

Each device has Ethernet1-8, Loopback0, Management1.
Spines: Ethernet1-4 connect to leaf-01..leaf-04, Loopback0 is router-id.
Leaves: Ethernet1-2 uplinks to spines, Ethernet3 MLAG peer-link, Ethernet4-8 hosts.
"""

from __future__ import annotations

# ─── Device metadata ────────────────────────────────────────────────────

DEVICE_INFO = {
    "spine-01": {
        "modelName": "DCS-7280SR3-48YC8",
        "version": "4.32.1F",
        "serialNumber": "SSJ17115308",
        "systemMacAddress": "00:1c:73:00:01:01",
        "hostname": "spine-01",
        "uptime": 8640000.0,
        "memTotal": 8155840,
        "memFree": 5423104,
        "hardwareRevision": "11.01",
        "architecture": "x86_64",
        "internalVersion": "4.32.1F-37186770.4321F",
        "bootupTimestamp": 1706000000.0,
        "configMacAddress": "00:00:00:00:00:00",
        "isIntlVersion": False,
        "imageFormatVersion": "3.0",
        "internalBuildId": "demo-build-001",
    },
    "spine-02": {
        "modelName": "DCS-7280SR3-48YC8",
        "version": "4.32.1F",
        "serialNumber": "SSJ17115309",
        "systemMacAddress": "00:1c:73:00:01:02",
        "hostname": "spine-02",
        "uptime": 8640000.0,
        "memTotal": 8155840,
        "memFree": 5390080,
        "hardwareRevision": "11.01",
        "architecture": "x86_64",
        "internalVersion": "4.32.1F-37186770.4321F",
        "bootupTimestamp": 1706000000.0,
        "configMacAddress": "00:00:00:00:00:00",
        "isIntlVersion": False,
        "imageFormatVersion": "3.0",
        "internalBuildId": "demo-build-001",
    },
    "leaf-01": {
        "modelName": "DCS-7050CX3-32S",
        "version": "4.32.1F",
        "serialNumber": "JPE17210456",
        "systemMacAddress": "00:1c:73:00:02:01",
        "hostname": "leaf-01",
        "uptime": 7776000.0,
        "memTotal": 8155840,
        "memFree": 4915200,
        "hardwareRevision": "11.00",
        "architecture": "x86_64",
        "internalVersion": "4.32.1F-37186770.4321F",
        "bootupTimestamp": 1706864000.0,
        "configMacAddress": "00:00:00:00:00:00",
        "isIntlVersion": False,
        "imageFormatVersion": "3.0",
        "internalBuildId": "demo-build-001",
    },
    "leaf-02": {
        "modelName": "DCS-7050CX3-32S",
        "version": "4.32.1F",
        "serialNumber": "JPE17210457",
        "systemMacAddress": "00:1c:73:00:02:02",
        "hostname": "leaf-02",
        "uptime": 7776000.0,
        "memTotal": 8155840,
        "memFree": 4882432,
        "hardwareRevision": "11.00",
        "architecture": "x86_64",
        "internalVersion": "4.32.1F-37186770.4321F",
        "bootupTimestamp": 1706864000.0,
        "configMacAddress": "00:00:00:00:00:00",
        "isIntlVersion": False,
        "imageFormatVersion": "3.0",
        "internalBuildId": "demo-build-001",
    },
    "leaf-03": {
        "modelName": "DCS-7050CX3-32S",
        "version": "4.32.1F",
        "serialNumber": "JPE17210458",
        "systemMacAddress": "00:1c:73:00:03:01",
        "hostname": "leaf-03",
        "uptime": 6912000.0,
        "memTotal": 8155840,
        "memFree": 4947968,
        "hardwareRevision": "11.00",
        "architecture": "x86_64",
        "internalVersion": "4.32.1F-37186770.4321F",
        "bootupTimestamp": 1707728000.0,
        "configMacAddress": "00:00:00:00:00:00",
        "isIntlVersion": False,
        "imageFormatVersion": "3.0",
        "internalBuildId": "demo-build-001",
    },
    "leaf-04": {
        "modelName": "DCS-7050CX3-32S",
        "version": "4.32.1F",
        "serialNumber": "JPE17210459",
        "systemMacAddress": "00:1c:73:00:03:02",
        "hostname": "leaf-04",
        "uptime": 6912000.0,
        "memTotal": 8155840,
        "memFree": 4816896,
        "hardwareRevision": "11.00",
        "architecture": "x86_64",
        "internalVersion": "4.32.1F-37186770.4321F",
        "bootupTimestamp": 1707728000.0,
        "configMacAddress": "00:00:00:00:00:00",
        "isIntlVersion": False,
        "imageFormatVersion": "3.0",
        "internalBuildId": "demo-build-001",
    },
}

# ─── Hostname ────────────────────────────────────────────────────────────

HOSTNAME = {name: {"hostname": name, "fqdn": f"{name}.lab.arista.com"} for name in DEVICE_INFO}

# ─── BGP Summary ─────────────────────────────────────────────────────────

# Spine-01 peers with all 4 leaves
BGP_SUMMARY = {
    "spine-01": {
        "vrfs": {
            "default": {
                "routerId": "10.0.0.1",
                "asn": "65000",
                "vrf": "default",
                "peers": {
                    "10.0.1.1": {
                        "peerState": "Established",
                        "prefixReceived": 24,
                        "prefixAccepted": 24,
                        "upDownTime": 8640000.0,
                        "asn": "65001",
                        "peerGroup": "LEAF-PEERS",
                        "msgReceived": 172800,
                        "msgSent": 172810,
                        "description": "leaf-01",
                    },
                    "10.0.1.2": {
                        "peerState": "Established",
                        "prefixReceived": 24,
                        "prefixAccepted": 24,
                        "upDownTime": 8640000.0,
                        "asn": "65001",
                        "peerGroup": "LEAF-PEERS",
                        "msgReceived": 172800,
                        "msgSent": 172810,
                        "description": "leaf-02",
                    },
                    "10.0.2.1": {
                        "peerState": "Established",
                        "prefixReceived": 20,
                        "prefixAccepted": 20,
                        "upDownTime": 6912000.0,
                        "asn": "65002",
                        "peerGroup": "LEAF-PEERS",
                        "msgReceived": 138240,
                        "msgSent": 138250,
                        "description": "leaf-03",
                    },
                    "10.0.2.2": {
                        "peerState": "Established",
                        "prefixReceived": 20,
                        "prefixAccepted": 20,
                        "upDownTime": 6912000.0,
                        "asn": "65002",
                        "peerGroup": "LEAF-PEERS",
                        "msgReceived": 138240,
                        "msgSent": 138250,
                        "description": "leaf-04",
                    },
                },
            }
        }
    },
    "spine-02": {
        "vrfs": {
            "default": {
                "routerId": "10.0.0.2",
                "asn": "65000",
                "vrf": "default",
                "peers": {
                    "10.0.1.1": {
                        "peerState": "Established",
                        "prefixReceived": 24,
                        "prefixAccepted": 24,
                        "upDownTime": 8640000.0,
                        "asn": "65001",
                        "peerGroup": "LEAF-PEERS",
                        "msgReceived": 172800,
                        "msgSent": 172810,
                        "description": "leaf-01",
                    },
                    "10.0.1.2": {
                        "peerState": "Established",
                        "prefixReceived": 24,
                        "prefixAccepted": 24,
                        "upDownTime": 8640000.0,
                        "asn": "65001",
                        "peerGroup": "LEAF-PEERS",
                        "msgReceived": 172800,
                        "msgSent": 172810,
                        "description": "leaf-02",
                    },
                    "10.0.2.1": {
                        "peerState": "Established",
                        "prefixReceived": 20,
                        "prefixAccepted": 20,
                        "upDownTime": 6912000.0,
                        "asn": "65002",
                        "peerGroup": "LEAF-PEERS",
                        "msgReceived": 138240,
                        "msgSent": 138250,
                        "description": "leaf-03",
                    },
                    "10.0.2.2": {
                        "peerState": "Established",
                        "prefixReceived": 20,
                        "prefixAccepted": 20,
                        "upDownTime": 6912000.0,
                        "asn": "65002",
                        "peerGroup": "LEAF-PEERS",
                        "msgReceived": 138240,
                        "msgSent": 138250,
                        "description": "leaf-04",
                    },
                },
            }
        }
    },
    "leaf-01": {
        "vrfs": {
            "default": {
                "routerId": "10.0.1.1",
                "asn": "65001",
                "vrf": "default",
                "peers": {
                    "10.0.0.1": {
                        "peerState": "Established",
                        "prefixReceived": 80,
                        "prefixAccepted": 80,
                        "upDownTime": 8640000.0,
                        "asn": "65000",
                        "peerGroup": "SPINE-PEERS",
                        "msgReceived": 172810,
                        "msgSent": 172800,
                        "description": "spine-01",
                    },
                    "10.0.0.2": {
                        "peerState": "Established",
                        "prefixReceived": 80,
                        "prefixAccepted": 80,
                        "upDownTime": 8640000.0,
                        "asn": "65000",
                        "peerGroup": "SPINE-PEERS",
                        "msgReceived": 172810,
                        "msgSent": 172800,
                        "description": "spine-02",
                    },
                },
            }
        }
    },
    "leaf-02": {
        "vrfs": {
            "default": {
                "routerId": "10.0.1.2",
                "asn": "65001",
                "vrf": "default",
                "peers": {
                    "10.0.0.1": {
                        "peerState": "Established",
                        "prefixReceived": 80,
                        "prefixAccepted": 80,
                        "upDownTime": 8640000.0,
                        "asn": "65000",
                        "peerGroup": "SPINE-PEERS",
                        "msgReceived": 172810,
                        "msgSent": 172800,
                        "description": "spine-01",
                    },
                    "10.0.0.2": {
                        "peerState": "Established",
                        "prefixReceived": 80,
                        "prefixAccepted": 80,
                        "upDownTime": 8640000.0,
                        "asn": "65000",
                        "peerGroup": "SPINE-PEERS",
                        "msgReceived": 172810,
                        "msgSent": 172800,
                        "description": "spine-02",
                    },
                },
            }
        }
    },
    "leaf-03": {
        "vrfs": {
            "default": {
                "routerId": "10.0.2.1",
                "asn": "65002",
                "vrf": "default",
                "peers": {
                    "10.0.0.1": {
                        "peerState": "Established",
                        "prefixReceived": 80,
                        "prefixAccepted": 80,
                        "upDownTime": 6912000.0,
                        "asn": "65000",
                        "peerGroup": "SPINE-PEERS",
                        "msgReceived": 138250,
                        "msgSent": 138240,
                        "description": "spine-01",
                    },
                    "10.0.0.2": {
                        "peerState": "Established",
                        "prefixReceived": 80,
                        "prefixAccepted": 80,
                        "upDownTime": 6912000.0,
                        "asn": "65000",
                        "peerGroup": "SPINE-PEERS",
                        "msgReceived": 138250,
                        "msgSent": 138240,
                        "description": "spine-02",
                    },
                },
            }
        }
    },
    "leaf-04": {
        "vrfs": {
            "default": {
                "routerId": "10.0.2.2",
                "asn": "65002",
                "vrf": "default",
                "peers": {
                    "10.0.0.1": {
                        "peerState": "Established",
                        "prefixReceived": 80,
                        "prefixAccepted": 80,
                        "upDownTime": 6912000.0,
                        "asn": "65000",
                        "peerGroup": "SPINE-PEERS",
                        "msgReceived": 138250,
                        "msgSent": 138240,
                        "description": "spine-01",
                    },
                    "10.0.0.2": {
                        "peerState": "Established",
                        "prefixReceived": 80,
                        "prefixAccepted": 80,
                        "upDownTime": 6912000.0,
                        "asn": "65000",
                        "peerGroup": "SPINE-PEERS",
                        "msgReceived": 138250,
                        "msgSent": 138240,
                        "description": "spine-02",
                    },
                },
            }
        }
    },
}

# ─── BGP Neighbors (detailed) ────────────────────────────────────────────


def _make_bgp_neighbor(
    peer_addr: str,
    peer_as: int,
    local_as: int,
    router_id: str,
    peer_group: str,
    uptime: float = 8640000.0,
    prefix_rx: int = 80,
) -> dict:
    """Build a single BGP neighbor entry."""
    return {
        "peerAddress": peer_addr,
        "state": "Established",
        "peerAs": peer_as,
        "localAs": local_as,
        "routerId": router_id,
        "prefixReceived": prefix_rx,
        "prefixAccepted": prefix_rx,
        "holdTime": 180,
        "keepAliveTime": 60,
        "upDownTime": uptime,
        "underMaintenance": False,
        "peerGroup": peer_group,
    }


BGP_NEIGHBORS = {
    "spine-01": {
        "vrfs": {
            "default": {
                "peerList": [
                    _make_bgp_neighbor("10.0.1.1", 65001, 65000, "10.0.1.1", "LEAF-PEERS", prefix_rx=24),
                    _make_bgp_neighbor("10.0.1.2", 65001, 65000, "10.0.1.2", "LEAF-PEERS", prefix_rx=24),
                    _make_bgp_neighbor(
                        "10.0.2.1",
                        65002,
                        65000,
                        "10.0.2.1",
                        "LEAF-PEERS",
                        uptime=6912000.0,
                        prefix_rx=20,
                    ),
                    _make_bgp_neighbor(
                        "10.0.2.2",
                        65002,
                        65000,
                        "10.0.2.2",
                        "LEAF-PEERS",
                        uptime=6912000.0,
                        prefix_rx=20,
                    ),
                ],
            }
        }
    },
    "spine-02": {
        "vrfs": {
            "default": {
                "peerList": [
                    _make_bgp_neighbor("10.0.1.1", 65001, 65000, "10.0.1.1", "LEAF-PEERS", prefix_rx=24),
                    _make_bgp_neighbor("10.0.1.2", 65001, 65000, "10.0.1.2", "LEAF-PEERS", prefix_rx=24),
                    _make_bgp_neighbor(
                        "10.0.2.1",
                        65002,
                        65000,
                        "10.0.2.1",
                        "LEAF-PEERS",
                        uptime=6912000.0,
                        prefix_rx=20,
                    ),
                    _make_bgp_neighbor(
                        "10.0.2.2",
                        65002,
                        65000,
                        "10.0.2.2",
                        "LEAF-PEERS",
                        uptime=6912000.0,
                        prefix_rx=20,
                    ),
                ],
            }
        }
    },
    "leaf-01": {
        "vrfs": {
            "default": {
                "peerList": [
                    _make_bgp_neighbor("10.0.0.1", 65000, 65001, "10.0.0.1", "SPINE-PEERS"),
                    _make_bgp_neighbor("10.0.0.2", 65000, 65001, "10.0.0.2", "SPINE-PEERS"),
                ],
            }
        }
    },
    "leaf-02": {
        "vrfs": {
            "default": {
                "peerList": [
                    _make_bgp_neighbor("10.0.0.1", 65000, 65001, "10.0.0.1", "SPINE-PEERS"),
                    _make_bgp_neighbor("10.0.0.2", 65000, 65001, "10.0.0.2", "SPINE-PEERS"),
                ],
            }
        }
    },
    "leaf-03": {
        "vrfs": {
            "default": {
                "peerList": [
                    _make_bgp_neighbor("10.0.0.1", 65000, 65002, "10.0.0.1", "SPINE-PEERS", uptime=6912000.0),
                    _make_bgp_neighbor("10.0.0.2", 65000, 65002, "10.0.0.2", "SPINE-PEERS", uptime=6912000.0),
                ],
            }
        }
    },
    "leaf-04": {
        "vrfs": {
            "default": {
                "peerList": [
                    _make_bgp_neighbor("10.0.0.1", 65000, 65002, "10.0.0.1", "SPINE-PEERS", uptime=6912000.0),
                    _make_bgp_neighbor("10.0.0.2", 65000, 65002, "10.0.0.2", "SPINE-PEERS", uptime=6912000.0),
                ],
            }
        }
    },
}


# ─── Interface Status ─────────────────────────────────────────────────────


def _make_spine_interfaces(name: str, mac_prefix: str) -> dict:
    """Generate interface status for a spine switch."""
    return {
        "interfaceStatuses": {
            "Ethernet1": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 100000000000,
                "interfaceType": "100GBASE-SR4",
                "description": "to-leaf-01",
                "lineProtocolStatus": "up",
            },
            "Ethernet2": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 100000000000,
                "interfaceType": "100GBASE-SR4",
                "description": "to-leaf-02",
                "lineProtocolStatus": "up",
            },
            "Ethernet3": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 100000000000,
                "interfaceType": "100GBASE-SR4",
                "description": "to-leaf-03",
                "lineProtocolStatus": "up",
            },
            "Ethernet4": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 100000000000,
                "interfaceType": "100GBASE-SR4",
                "description": "to-leaf-04",
                "lineProtocolStatus": "up",
            },
            "Loopback0": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 0,
                "interfaceType": "",
                "description": f"Router-ID {name}",
                "lineProtocolStatus": "up",
            },
            "Management1": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 1000000000,
                "interfaceType": "1000BASE-T",
                "description": "oob-management",
                "lineProtocolStatus": "up",
            },
        }
    }


def _make_leaf_interfaces(name: str, peer_name: str) -> dict:
    """Generate interface status for a leaf switch."""
    return {
        "interfaceStatuses": {
            "Ethernet1": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 100000000000,
                "interfaceType": "100GBASE-SR4",
                "description": "to-spine-01",
                "lineProtocolStatus": "up",
            },
            "Ethernet2": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 100000000000,
                "interfaceType": "100GBASE-SR4",
                "description": "to-spine-02",
                "lineProtocolStatus": "up",
            },
            "Ethernet3": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "trunk", "vlanId": 1},
                "bandwidth": 100000000000,
                "interfaceType": "100GBASE-SR4",
                "description": f"MLAG-peer-link-to-{peer_name}",
                "lineProtocolStatus": "up",
            },
            "Ethernet4": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "access", "vlanId": 100},
                "bandwidth": 10000000000,
                "interfaceType": "10GBASE-SR",
                "description": "server-01",
                "lineProtocolStatus": "up",
            },
            "Ethernet5": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "access", "vlanId": 200},
                "bandwidth": 10000000000,
                "interfaceType": "10GBASE-SR",
                "description": "server-02",
                "lineProtocolStatus": "up",
            },
            "Ethernet6": {
                "linkStatus": "notconnect",
                "vlanInformation": {"interfaceMode": "access", "vlanId": 100},
                "bandwidth": 10000000000,
                "interfaceType": "10GBASE-SR",
                "description": "",
                "lineProtocolStatus": "down",
            },
            "Loopback0": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 0,
                "interfaceType": "",
                "description": f"Router-ID {name}",
                "lineProtocolStatus": "up",
            },
            "Port-Channel10": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "trunk", "vlanId": 1},
                "bandwidth": 100000000000,
                "interfaceType": "",
                "description": "MLAG-peer-link",
                "lineProtocolStatus": "up",
            },
            "Vlan100": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 0,
                "interfaceType": "",
                "description": "Servers",
                "lineProtocolStatus": "up",
            },
            "Vlan200": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 0,
                "interfaceType": "",
                "description": "Storage",
                "lineProtocolStatus": "up",
            },
            "Vlan4094": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 0,
                "interfaceType": "",
                "description": "MLAG-peer",
                "lineProtocolStatus": "up",
            },
            "Management1": {
                "linkStatus": "connected",
                "vlanInformation": {"interfaceMode": "routed"},
                "bandwidth": 1000000000,
                "interfaceType": "1000BASE-T",
                "description": "oob-management",
                "lineProtocolStatus": "up",
            },
        }
    }


INTERFACE_STATUS = {
    "spine-01": _make_spine_interfaces("spine-01", "00:1c:73:00:01:01"),
    "spine-02": _make_spine_interfaces("spine-02", "00:1c:73:00:01:02"),
    "leaf-01": _make_leaf_interfaces("leaf-01", "leaf-02"),
    "leaf-02": _make_leaf_interfaces("leaf-02", "leaf-01"),
    "leaf-03": _make_leaf_interfaces("leaf-03", "leaf-04"),
    "leaf-04": _make_leaf_interfaces("leaf-04", "leaf-03"),
}


# ─── Interface Counters ────────────────────────────────────────────────────


def _make_counters(names: list[str]) -> dict:
    """Generate interface counters for given interface names."""
    interfaces = {}
    for i, name in enumerate(names):
        base = (i + 1) * 1000000
        interfaces[name] = {
            "inOctets": base * 500,
            "outOctets": base * 400,
            "inUcastPkts": base * 10,
            "outUcastPkts": base * 8,
            "inMulticastPkts": base,
            "outMulticastPkts": base // 2,
            "inBroadcastPkts": 100,
            "outBroadcastPkts": 50,
            "inDiscards": 0,
            "outDiscards": 0,
            "inErrors": 0,
            "outErrors": 0,
            "counterRefreshTime": 1706000000.0,
        }
    return {"interfaces": interfaces}


INTERFACE_COUNTERS = {
    "spine-01": _make_counters(["Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4"]),
    "spine-02": _make_counters(["Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4"]),
    "leaf-01": _make_counters(["Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4", "Ethernet5"]),
    "leaf-02": _make_counters(["Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4", "Ethernet5"]),
    "leaf-03": _make_counters(["Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4", "Ethernet5"]),
    "leaf-04": _make_counters(["Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4", "Ethernet5"]),
}

# ─── Interface Errors ──────────────────────────────────────────────────────


def _make_errors(names: list[str]) -> dict:
    """Generate interface error counters (all zeros — healthy network)."""
    interfaces = {}
    for name in names:
        interfaces[name] = {
            "interfaceErrorCounters": {
                "inputErrors": 0,
                "outputErrors": 0,
                "frameTooShorts": 0,
                "frameTooLongs": 0,
                "fcsErrors": 0,
                "alignmentErrors": 0,
                "symbolErrors": 0,
                "runtFrames": 0,
                "giantFrames": 0,
            }
        }
    return {"interfaceErrorCounters": interfaces}


INTERFACE_ERRORS = {dev: _make_errors(list(INTERFACE_STATUS[dev]["interfaceStatuses"].keys())) for dev in DEVICE_INFO}

# ─── MLAG Status ───────────────────────────────────────────────────────────

_MLAG_LEAF_TEMPLATE = {
    "domainId": "",
    "localInterface": "Vlan4094",
    "peerAddress": "",
    "peerLink": "Port-Channel10",
    "state": "active",
    "negStatus": "connected",
    "systemId": "",
    "configSanity": "consistent",
    "portsErrdisabled": False,
    "mlagPorts": {
        "Active-full": 4,
        "Active-partial": 0,
        "Disabled": 0,
        "Inactive": 0,
    },
}

MLAG_STATUS: dict[str, dict] = {
    "spine-01": {},  # Spines don't run MLAG
    "spine-02": {},
    "leaf-01": {
        **_MLAG_LEAF_TEMPLATE,
        "domainId": "MLAG-PAIR-01",
        "peerAddress": "169.254.1.2",
        "systemId": "02:1c:73:00:02:ff",
    },
    "leaf-02": {
        **_MLAG_LEAF_TEMPLATE,
        "domainId": "MLAG-PAIR-01",
        "peerAddress": "169.254.1.1",
        "systemId": "02:1c:73:00:02:ff",
    },
    "leaf-03": {
        **_MLAG_LEAF_TEMPLATE,
        "domainId": "MLAG-PAIR-02",
        "peerAddress": "169.254.2.2",
        "systemId": "02:1c:73:00:03:ff",
    },
    "leaf-04": {
        **_MLAG_LEAF_TEMPLATE,
        "domainId": "MLAG-PAIR-02",
        "peerAddress": "169.254.2.1",
        "systemId": "02:1c:73:00:03:ff",
    },
}

# ─── VLANs ─────────────────────────────────────────────────────────────────

_LEAF_VLANS = {
    "vlans": {
        "1": {"name": "default", "status": "active", "interfaces": {}},
        "100": {
            "name": "Servers",
            "status": "active",
            "interfaces": {"Ethernet4": {"type": "tagged"}, "Port-Channel10": {"type": "tagged"}},
        },
        "200": {
            "name": "Storage",
            "status": "active",
            "interfaces": {"Ethernet5": {"type": "tagged"}, "Port-Channel10": {"type": "tagged"}},
        },
        "4094": {
            "name": "MLAG-PEER",
            "status": "active",
            "interfaces": {"Port-Channel10": {"type": "tagged"}},
        },
    }
}

VLANS: dict[str, dict] = {
    "spine-01": {"vlans": {"1": {"name": "default", "status": "active", "interfaces": {}}}},
    "spine-02": {"vlans": {"1": {"name": "default", "status": "active", "interfaces": {}}}},
    "leaf-01": _LEAF_VLANS,
    "leaf-02": _LEAF_VLANS,
    "leaf-03": _LEAF_VLANS,
    "leaf-04": _LEAF_VLANS,
}

# ─── Routing Table ─────────────────────────────────────────────────────────

ROUTING_TABLE = {
    "spine-01": {
        "vrfs": {
            "default": {
                "routes": {
                    "10.0.0.1/32": {
                        "routeType": "connected",
                        "hardwareProgrammed": True,
                        "kernelProgrammed": True,
                        "directlyConnected": True,
                        "routeAction": "forward",
                        "vias": [{"interface": "Loopback0"}],
                    },
                    "10.0.1.0/24": {
                        "routeType": "bgp",
                        "hardwareProgrammed": True,
                        "kernelProgrammed": True,
                        "routeAction": "forward",
                        "vias": [
                            {"nexthopAddr": "10.0.1.1", "interface": "Ethernet1"},
                            {"nexthopAddr": "10.0.1.2", "interface": "Ethernet2"},
                        ],
                    },
                    "10.0.2.0/24": {
                        "routeType": "bgp",
                        "hardwareProgrammed": True,
                        "kernelProgrammed": True,
                        "routeAction": "forward",
                        "vias": [
                            {"nexthopAddr": "10.0.2.1", "interface": "Ethernet3"},
                            {"nexthopAddr": "10.0.2.2", "interface": "Ethernet4"},
                        ],
                    },
                }
            }
        }
    },
}
# Provide same structure for all devices (simplified)
for _dev in ["spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04"]:
    ROUTING_TABLE[_dev] = {
        "vrfs": {
            "default": {
                "routes": {
                    f"{DEVICE_INFO[_dev]['hostname']}-loopback/32": {
                        "routeType": "connected",
                        "hardwareProgrammed": True,
                        "kernelProgrammed": True,
                        "directlyConnected": True,
                        "routeAction": "forward",
                        "vias": [{"interface": "Loopback0"}],
                    },
                }
            }
        }
    }

# ─── MAC Address Table ─────────────────────────────────────────────────────

MAC_TABLE = {
    "leaf-01": {
        "unicastTable": {
            "tableEntries": [
                {
                    "macAddress": "00:50:56:a1:00:01",
                    "type": "dynamic",
                    "vlanId": 100,
                    "interface": "Ethernet4",
                    "moves": 0,
                    "lastMove": 0.0,
                },
                {
                    "macAddress": "00:50:56:a1:00:02",
                    "type": "dynamic",
                    "vlanId": 200,
                    "interface": "Ethernet5",
                    "moves": 0,
                    "lastMove": 0.0,
                },
                {
                    "macAddress": "00:1c:73:00:02:02",
                    "type": "dynamic",
                    "vlanId": 4094,
                    "interface": "Port-Channel10",
                    "moves": 0,
                    "lastMove": 0.0,
                },
            ]
        }
    },
}
# Provide minimal MACs for other devices
for _dev in ["spine-01", "spine-02", "leaf-02", "leaf-03", "leaf-04"]:
    MAC_TABLE[_dev] = {"unicastTable": {"tableEntries": []}}

# ─── LLDP Neighbors ───────────────────────────────────────────────────────

LLDP_NEIGHBORS: dict[str, dict] = {
    "spine-01": {
        "lldpNeighbors": [
            {
                "port": "Ethernet1",
                "neighborDevice": "leaf-01",
                "neighborPort": "Ethernet1",
                "ttl": 120,
            },
            {
                "port": "Ethernet2",
                "neighborDevice": "leaf-02",
                "neighborPort": "Ethernet1",
                "ttl": 120,
            },
            {
                "port": "Ethernet3",
                "neighborDevice": "leaf-03",
                "neighborPort": "Ethernet1",
                "ttl": 120,
            },
            {
                "port": "Ethernet4",
                "neighborDevice": "leaf-04",
                "neighborPort": "Ethernet1",
                "ttl": 120,
            },
        ]
    },
    "spine-02": {
        "lldpNeighbors": [
            {
                "port": "Ethernet1",
                "neighborDevice": "leaf-01",
                "neighborPort": "Ethernet2",
                "ttl": 120,
            },
            {
                "port": "Ethernet2",
                "neighborDevice": "leaf-02",
                "neighborPort": "Ethernet2",
                "ttl": 120,
            },
            {
                "port": "Ethernet3",
                "neighborDevice": "leaf-03",
                "neighborPort": "Ethernet2",
                "ttl": 120,
            },
            {
                "port": "Ethernet4",
                "neighborDevice": "leaf-04",
                "neighborPort": "Ethernet2",
                "ttl": 120,
            },
        ]
    },
    "leaf-01": {
        "lldpNeighbors": [
            {"port": "Ethernet1", "neighborDevice": "spine-01", "neighborPort": "Ethernet1", "ttl": 120},
            {"port": "Ethernet2", "neighborDevice": "spine-02", "neighborPort": "Ethernet1", "ttl": 120},
            {"port": "Ethernet3", "neighborDevice": "leaf-02", "neighborPort": "Ethernet3", "ttl": 120},
        ]
    },
    "leaf-02": {
        "lldpNeighbors": [
            {"port": "Ethernet1", "neighborDevice": "spine-01", "neighborPort": "Ethernet2", "ttl": 120},
            {"port": "Ethernet2", "neighborDevice": "spine-02", "neighborPort": "Ethernet2", "ttl": 120},
            {"port": "Ethernet3", "neighborDevice": "leaf-01", "neighborPort": "Ethernet3", "ttl": 120},
        ]
    },
    "leaf-03": {
        "lldpNeighbors": [
            {"port": "Ethernet1", "neighborDevice": "spine-01", "neighborPort": "Ethernet3", "ttl": 120},
            {"port": "Ethernet2", "neighborDevice": "spine-02", "neighborPort": "Ethernet3", "ttl": 120},
            {"port": "Ethernet3", "neighborDevice": "leaf-04", "neighborPort": "Ethernet3", "ttl": 120},
        ]
    },
    "leaf-04": {
        "lldpNeighbors": [
            {"port": "Ethernet1", "neighborDevice": "spine-01", "neighborPort": "Ethernet4", "ttl": 120},
            {"port": "Ethernet2", "neighborDevice": "spine-02", "neighborPort": "Ethernet4", "ttl": 120},
            {"port": "Ethernet3", "neighborDevice": "leaf-03", "neighborPort": "Ethernet3", "ttl": 120},
        ]
    },
}

# ─── Running Config (text encoding) ──────────────────────────────────────

RUNNING_CONFIG = {
    dev: {
        "output": (
            f"! device: {dev} ({DEVICE_INFO[dev]['modelName']}, EOS-{DEVICE_INFO[dev]['version']})\n"
            "!\n"
            f"hostname {dev}\n"
            "!\n"
            "spanning-tree mode mstp\n"
            "!\n"
            "management api http-commands\n"
            "   no shutdown\n"
            "!\n"
            "interface Loopback0\n"
            f"   description Router-ID {dev}\n"
            "!\n"
            "end\n"
        )
    }
    for dev in DEVICE_INFO
}

# ─── Config Diff ──────────────────────────────────────────────────────────

CONFIG_DIFF = {dev: {"output": ""} for dev in DEVICE_INFO}  # No diff in demo — running == startup

# ─── NTP Status ────────────────────────────────────────────────────────────

NTP_STATUS = {
    dev: {
        "status": "synchronised",
        "peers": {
            "10.100.0.1": {
                "condition": "sys.peer",
                "stratum": 2,
                "refid": "GPS",
                "delay": 1.234,
                "offset": 0.567,
                "jitter": 0.123,
                "poll": 64,
            },
            "10.100.0.2": {
                "condition": "candidate",
                "stratum": 2,
                "refid": "GPS",
                "delay": 2.345,
                "offset": 0.789,
                "jitter": 0.234,
                "poll": 64,
            },
        },
    }
    for dev in DEVICE_INFO
}

# ─── CPU / Memory ─────────────────────────────────────────────────────────

CPU_PROCESS = {
    dev: {
        "cpuInfo": {
            "%Cpu(s)": {
                "idle": 92.5,
                "user": 5.2,
                "system": 2.3,
                "nice": 0.0,
            }
        },
        "processes": {},
    }
    for dev in DEVICE_INFO
}

MEMORY = {
    dev: {
        "memTotal": DEVICE_INFO[dev]["memTotal"],
        "memFree": DEVICE_INFO[dev]["memFree"],
        "buffers": 204800,
        "cached": 1024000,
    }
    for dev in DEVICE_INFO
}

# ─── Temperature ──────────────────────────────────────────────────────────

TEMPERATURE = {
    dev: {
        "systemStatus": "temperatureOk",
        "tempSensors": [
            {"name": "TempSensor1", "currentTemperature": 32.0, "overheatThreshold": 95.0, "status": "ok"},
            {"name": "TempSensor2", "currentTemperature": 34.5, "overheatThreshold": 95.0, "status": "ok"},
            {"name": "TempSensor3", "currentTemperature": 28.0, "overheatThreshold": 95.0, "status": "ok"},
        ],
    }
    for dev in DEVICE_INFO
}

# ─── System Logging ──────────────────────────────────────────────────────

LOGGING = {
    dev: {
        "output": (
            f"Jan 25 10:00:00 {dev} Ebra: %LINEPROTO-5-UPDOWN: "
            "Line protocol on Interface Ethernet1, changed state to up\n"
            f"Jan 25 10:00:01 {dev} Bgp: %BGP-5-ADJCHANGE: peer 10.0.0.1 Up\n"
            f"Jan 25 10:00:02 {dev} Mlag: %MLAG-5-STATE: MLAG state changed to active\n"
        )
    }
    for dev in DEVICE_INFO
}

# ─── Inventory (show inventory) ──────────────────────────────────────────

INVENTORY = {
    dev: {
        "systemInformation": {
            "name": dev,
            "description": DEVICE_INFO[dev]["modelName"],
            "mfgDate": "2024-01-15",
            "hardwareRev": DEVICE_INFO[dev]["hardwareRevision"],
            "serialNum": DEVICE_INFO[dev]["serialNumber"],
        },
        "xcvrSlots": {},
    }
    for dev in DEVICE_INFO
}


# ─── Spanning Tree ─────────────────────────────────────────────────────────

STP = {
    dev: {
        "spanningTreeInstances": {
            "MST0": {
                "bridge": {"priority": 32768 if "spine" in dev else 61440, "systemIdExtension": 0},
                "rootBridge": {"priority": 32768, "systemIdExtension": 0, "macAddress": "00:1c:73:00:01:01"},
                "interfaces": {
                    "Ethernet1": {"state": "forwarding", "role": "designated" if "spine" in dev else "root"},
                    "Ethernet2": {"state": "forwarding", "role": "designated" if "spine" in dev else "root"},
                },
            }
        }
    }
    for dev in DEVICE_INFO
}

# ─── Port-Channels ────────────────────────────────────────────────────────

PORT_CHANNELS: dict[str, dict] = {
    "spine-01": {"portChannels": {}},
    "spine-02": {"portChannels": {}},
}
for _dev in ["leaf-01", "leaf-02", "leaf-03", "leaf-04"]:
    PORT_CHANNELS[_dev] = {
        "portChannels": {
            "Port-Channel10": {
                "activePorts": {"Ethernet3": {"protocol": "lacp"}},
                "inactivePorts": {},
                "fallbackState": "unconfigured",
                "minLinks": 0,
            }
        }
    }

# ─── LLDP Neighbor Detail ─────────────────────────────────────────────────

LLDP_NEIGHBOR_DETAIL: dict[str, dict] = {
    dev: {
        "lldpNeighbors": {
            intf["port"]: {
                "lldpNeighborInfo": [
                    {
                        "chassisId": f"00:1c:73:00:0{i}:01",
                        "chassisIdType": "macAddress",
                        "neighborInterfaceInfo": {
                            "interfaceId": intf["neighborPort"],
                            "interfaceIdType": "interfaceName",
                        },
                        "systemName": intf["neighborDevice"],
                        "systemDescription": "Arista EOS",
                        "systemCapabilities": {"bridge": True, "router": True},
                        "managementAddresses": [{"address": f"10.0.{i}.1", "addressType": "ipv4"}],
                        "ttl": 120,
                    }
                ]
            }
            for i, intf in enumerate(LLDP_NEIGHBORS[dev].get("lldpNeighbors", []))
        }
    }
    for dev in DEVICE_INFO
}

# ─── Route-Maps ───────────────────────────────────────────────────────────

ROUTE_MAPS = {
    dev: {
        "routeMaps": {
            "RM-BGP-EXPORT": {
                "entries": [
                    {
                        "sequence": 10,
                        "action": "permit",
                        "matchRules": ["prefix-list PL-DEFAULT"],
                        "setActions": ["local-preference 150"],
                    },
                    {"sequence": 20, "action": "deny", "matchRules": [], "setActions": []},
                ]
            },
            "RM-BGP-IMPORT": {
                "entries": [
                    {"sequence": 10, "action": "permit", "matchRules": [], "setActions": ["local-preference 100"]}
                ]
            },
        }
    }
    for dev in DEVICE_INFO
}

# ─── Prefix-Lists ─────────────────────────────────────────────────────────

PREFIX_LISTS = {
    dev: {
        "ipPrefixLists": {
            "PL-DEFAULT": {
                "entries": [
                    {"sequence": 10, "action": "permit", "prefix": "0.0.0.0/0"},
                ]
            },
            "PL-LOOPBACKS": {
                "entries": [
                    {"sequence": 10, "action": "permit", "prefix": "10.0.0.0/24", "le": 32},
                ]
            },
        }
    }
    for dev in DEVICE_INFO
}

# ─── IP Access Lists ─────────────────────────────────────────────────────

ACCESS_LISTS = {
    dev: {
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
                "name": "COPP-SYSTEM",
                "type": "Extended",
                "sequence": [
                    {"sequenceNumber": 10, "action": "permit", "protocol": "icmp", "source": "any"},
                ],
            },
        ]
    }
    for dev in DEVICE_INFO
}

# ─── Reload Cause ─────────────────────────────────────────────────────────

RELOAD_CAUSE = {
    dev: {
        "resetCauses": [
            {
                "recommendedAction": "",
                "description": "The system rebooted due to a Power Loss",
                "timestamp": 1706000000.0,
                "debugInfoIsDir": False,
            }
        ],
        "full": False,
    }
    for dev in DEVICE_INFO
}

# ─── Config Sessions ─────────────────────────────────────────────────────

CONFIG_SESSIONS = {
    dev: {
        "sessions": {},
        "maxSavedSessions": 5,
    }
    for dev in DEVICE_INFO
}

# ─── Config Checkpoints ──────────────────────────────────────────────────

CONFIG_CHECKPOINTS = {dev: {"output": "Checkpoint  Time\n"} for dev in DEVICE_INFO}

# ─── OSPF Neighbors ──────────────────────────────────────────────────────

OSPF_NEIGHBORS: dict[str, dict] = {
    "spine-01": {"vrfs": {"default": {"instList": {"1": {"ospfNeighborEntries": []}}}}},
    "spine-02": {"vrfs": {"default": {"instList": {"1": {"ospfNeighborEntries": []}}}}},
}
for _dev in ["leaf-01", "leaf-02", "leaf-03", "leaf-04"]:
    OSPF_NEIGHBORS[_dev] = {"vrfs": {"default": {"instList": {"1": {"ospfNeighborEntries": []}}}}}

# ─── ARP Table ────────────────────────────────────────────────────────────

ARP_TABLE: dict[str, dict] = {}
for _dev in DEVICE_INFO:
    ARP_TABLE[_dev] = {
        "ipV4Neighbors": [
            {
                "hwAddress": "00:1c:73:00:01:01",
                "address": "10.0.0.1",
                "interface": "Ethernet1",
                "age": 300,
            },
        ]
    }

# ─── Environment Power ───────────────────────────────────────────────────

ENV_POWER = {
    dev: {
        "powerSupplies": {
            "1": {
                "state": "ok",
                "modelName": "PWR-500AC-R",
                "inputCurrent": 1.5,
                "outputCurrent": 12.0,
                "outputPower": 150.0,
                "capacity": 500.0,
            },
            "2": {
                "state": "ok",
                "modelName": "PWR-500AC-R",
                "inputCurrent": 1.5,
                "outputCurrent": 12.0,
                "outputPower": 150.0,
                "capacity": 500.0,
            },
        }
    }
    for dev in DEVICE_INFO
}

# ─── Environment Cooling ─────────────────────────────────────────────────

ENV_COOLING = {
    dev: {
        "fanTraySlots": [
            {"label": "FanTray1", "status": "ok", "speed": 50, "actualSpeed": 4500},
            {"label": "FanTray2", "status": "ok", "speed": 50, "actualSpeed": 4500},
            {"label": "FanTray3", "status": "ok", "speed": 50, "actualSpeed": 4500},
        ]
    }
    for dev in DEVICE_INFO
}

# ─── VRFs ─────────────────────────────────────────────────────────────────

VRFS: dict[str, dict] = {}
for _dev in DEVICE_INFO:
    VRFS[_dev] = {
        "vrfs": {
            "default": {"routeDistinguisher": "", "protocols": {"ipv4": True}},
            "MGMT": {"routeDistinguisher": "10.0.0.1:99", "protocols": {"ipv4": True}},
        }
    }

# ─── Hardware Capacity ───────────────────────────────────────────────────

HARDWARE_CAPACITY = {
    dev: {
        "tables": [
            {
                "table": "IPv4 /32 hosts",
                "chip": "memory",
                "maxLimit": 131072,
                "highWatermark": 120,
                "used": 85,
                "free": 130987,
            },
            {
                "table": "IPv4 routes",
                "chip": "memory",
                "maxLimit": 262144,
                "highWatermark": 500,
                "used": 350,
                "free": 261794,
            },
        ]
    }
    for dev in DEVICE_INFO
}

# ─── Hardware Drops ──────────────────────────────────────────────────────

HARDWARE_DROPS = {
    dev: {
        "totalPacketProcessor": {
            "sobcDrop": 0,
            "sobcQueue": 0,
        }
    }
    for dev in DEVICE_INFO
}

# ─── IP Interfaces ───────────────────────────────────────────────────────

IP_INTERFACES: dict[str, dict] = {}
for _dev in DEVICE_INFO:
    IP_INTERFACES[_dev] = {
        "interfaces": {
            "Loopback0": {
                "name": "Loopback0",
                "interfaceAddress": {
                    "primaryIp": {"address": DEVICE_INFO[_dev].get("hostname", "10.0.0.1"), "maskLen": 32},
                },
                "ipv4Routable240": False,
            },
            "Management1": {
                "name": "Management1",
                "interfaceAddress": {"primaryIp": {"address": "172.16.0.1", "maskLen": 24}},
                "ipv4Routable240": False,
            },
        }
    }

# ─── Transceiver Info ────────────────────────────────────────────────────

TRANSCEIVER_INFO: dict[str, dict] = {}
for _dev in DEVICE_INFO:
    TRANSCEIVER_INFO[_dev] = {
        "interfaces": {
            "Ethernet1": {
                "rxPower": -3.2,
                "txPower": -1.8,
                "txBias": 6.5,
                "temperature": 33.0,
                "voltage": 3.3,
                "mediaType": "100GBASE-SR4",
            },
            "Ethernet2": {
                "rxPower": -3.5,
                "txPower": -1.9,
                "txBias": 6.4,
                "temperature": 34.0,
                "voltage": 3.3,
                "mediaType": "100GBASE-SR4",
            },
        }
    }

# ─── Extensions ──────────────────────────────────────────────────────────

EXTENSIONS = {
    dev: {
        "extensions": {},
    }
    for dev in DEVICE_INFO
}

# ─── Users ───────────────────────────────────────────────────────────────

USERS = {
    dev: {
        "users": {
            "admin": {
                "idleTime": 300.0,
                "terminal": "ttyS0",
                "from": "console",
            }
        }
    }
    for dev in DEVICE_INFO
}

# ─── AAA ─────────────────────────────────────────────────────────────────

AAA = {
    dev: {
        "authentication": {"methods": ["local"]},
        "authorization": {"methods": ["local"]},
        "accounting": {},
    }
    for dev in DEVICE_INFO
}

# ─── SNMP ────────────────────────────────────────────────────────────────

SNMP = {
    dev: {
        "enabled": True,
        "communities": {},
        "users": {},
    }
    for dev in DEVICE_INFO
}

# ─── CoPP Policy ─────────────────────────────────────────────────────────

COPP_POLICY = {
    dev: {
        "policyMaps": {
            "copp-system-policy": {
                "classes": {
                    "copp-system-bgp": {"rate": 500, "burst": 5000, "packets": 1000, "drops": 0},
                    "copp-system-default": {"rate": 100, "burst": 1000, "packets": 500, "drops": 0},
                }
            }
        }
    }
    for dev in DEVICE_INFO
}

# ─── eAPI Status ─────────────────────────────────────────────────────────

EAPI_STATUS = {
    dev: {
        "enabled": True,
        "httpServer": {"running": True, "port": 80},
        "httpsServer": {"running": True, "port": 443},
        "connectedClients": [],
    }
    for dev in DEVICE_INFO
}

# ─── VRRP ────────────────────────────────────────────────────────────────

VRRP = {dev: {"virtualRouters": {}} for dev in DEVICE_INFO}

# ─── VARP ────────────────────────────────────────────────────────────────

VARP = {dev: {"virtualMac": "00:1c:73:00:00:99", "interfaces": {}} for dev in DEVICE_INFO}

# ─── OSPF Interfaces ─────────────────────────────────────────────────────

OSPF_INTERFACES = {dev: {"vrfs": {"default": {"instList": {"1": {"interfaces": {}}}}}} for dev in DEVICE_INFO}

# ─── Route Summary ───────────────────────────────────────────────────────

ROUTE_SUMMARY = {
    dev: {
        "vrfs": {
            "default": {
                "totalRoutes": 15,
                "routes": {"connected": 5, "static": 2, "bgp": 8},
            }
        }
    }
    for dev in DEVICE_INFO
}

# ─── MLAG Config Sanity ──────────────────────────────────────────────────

MLAG_CONFIG_SANITY: dict[str, dict] = {
    "spine-01": {"mlagActive": False, "response": ""},
    "spine-02": {"mlagActive": False, "response": ""},
}
for _dev in ["leaf-01", "leaf-02", "leaf-03", "leaf-04"]:
    MLAG_CONFIG_SANITY[_dev] = {"mlagActive": True, "response": "consistent", "detail": {}}

# ─── MLAG Interfaces ─────────────────────────────────────────────────────

MLAG_INTERFACES: dict[str, dict] = {
    "spine-01": {"interfaces": {}},
    "spine-02": {"interfaces": {}},
}
for _dev in ["leaf-01", "leaf-02", "leaf-03", "leaf-04"]:
    MLAG_INTERFACES[_dev] = {
        "interfaces": {
            "Port-Channel10": {
                "localInterface": "Port-Channel10",
                "localLinkStatus": "up",
                "peerLinkStatus": "up",
                "mlagId": 10,
            }
        }
    }

# ─── Trunk Interfaces ────────────────────────────────────────────────────

TRUNK_INTERFACES = {
    dev: {
        "trunkGroups": {},
        "trunks": {
            "Ethernet1": {"allowedVlans": "1-4094", "nativeVlan": 1},
        },
    }
    for dev in DEVICE_INFO
}

# ─── Command → Response dispatch ─────────────────────────────────────────


def get_response(host: str, command: str) -> dict | None:
    """Look up a mock response for a given host and command.

    Returns None if the command is not mocked (caller should handle gracefully).
    """
    # Normalize command (strip whitespace)
    cmd = command.strip()

    # Map commands to response data
    dispatch: dict[str, dict[str, dict]] = {
        "show version": DEVICE_INFO,
        "show hostname": HOSTNAME,
        "show ip bgp summary": BGP_SUMMARY,
        "show ip bgp summary vrf all": BGP_SUMMARY,
        "show bgp summary": BGP_SUMMARY,
        "show ip bgp neighbors": BGP_NEIGHBORS,
        "show ip bgp neighbors vrf all": BGP_NEIGHBORS,
        "show interfaces status": INTERFACE_STATUS,
        "show interfaces counters": INTERFACE_COUNTERS,
        "show interfaces counters errors": INTERFACE_ERRORS,
        "show mlag": MLAG_STATUS,
        "show mlag detail": MLAG_STATUS,
        "show mlag config-sanity": MLAG_CONFIG_SANITY,
        "show mlag interfaces": MLAG_INTERFACES,
        "show vlan": VLANS,
        "show ip route": ROUTING_TABLE,
        "show ip route vrf all": ROUTING_TABLE,
        "show ip route summary": ROUTE_SUMMARY,
        "show mac address-table": MAC_TABLE,
        "show lldp neighbors": LLDP_NEIGHBORS,
        "show lldp neighbors detail": LLDP_NEIGHBOR_DETAIL,
        "show ntp status": NTP_STATUS,
        "show ntp associations": NTP_STATUS,
        "show processes top once": CPU_PROCESS,
        "show version | json": DEVICE_INFO,
        "show system environment temperature": TEMPERATURE,
        "show environment temperature": TEMPERATURE,
        "show environment power": ENV_POWER,
        "show environment cooling": ENV_COOLING,
        "show inventory": INVENTORY,
        "show memory": MEMORY,
        "show spanning-tree": STP,
        "show spanning-tree detail": STP,
        "show port-channel summary": PORT_CHANNELS,
        "show port-channel": PORT_CHANNELS,
        "show route-map": ROUTE_MAPS,
        "show ip prefix-list": PREFIX_LISTS,
        "show ip access-lists": ACCESS_LISTS,
        "show reload cause": RELOAD_CAUSE,
        "show configuration sessions detail": CONFIG_SESSIONS,
        "show ip ospf neighbor": OSPF_NEIGHBORS,
        "show ip ospf interface": OSPF_INTERFACES,
        "show ip arp": ARP_TABLE,
        "show arp": ARP_TABLE,
        "show vrf": VRFS,
        "show hardware capacity": HARDWARE_CAPACITY,
        "show hardware counter drop": HARDWARE_DROPS,
        "show ip interface": IP_INTERFACES,
        "show interfaces transceiver": TRANSCEIVER_INFO,
        "show extensions": EXTENSIONS,
        "show users": USERS,
        "show aaa": AAA,
        "show snmp": SNMP,
        "show policy-map interface control-plane": COPP_POLICY,
        "show management api http-commands": EAPI_STATUS,
        "show vrrp": VRRP,
        "show ip virtual-router": VARP,
        "show interfaces trunk": TRUNK_INTERFACES,
    }

    # Text-encoded commands
    text_dispatch: dict[str, dict[str, dict]] = {
        "show running-config": RUNNING_CONFIG,
        "show running-config diffs": CONFIG_DIFF,
        "show logging last 100": LOGGING,
        "show logging": LOGGING,
        "show configuration checkpoints": CONFIG_CHECKPOINTS,
    }

    # Check both dispatch tables
    if cmd in dispatch:
        data = dispatch[cmd]
        return data.get(host)
    if cmd in text_dispatch:
        data = text_dispatch[cmd]
        return data.get(host)

    # Partial matching for common patterns
    if cmd.startswith("show ip bgp summary"):
        return BGP_SUMMARY.get(host)
    if cmd.startswith("show ip bgp neighbors"):
        return BGP_NEIGHBORS.get(host)
    if cmd.startswith("show interfaces") and "transceiver" in cmd:
        return TRANSCEIVER_INFO.get(host)
    if cmd.startswith("show interfaces") and "counters errors" in cmd:
        return INTERFACE_ERRORS.get(host)
    if cmd.startswith("show interfaces") and "counters" in cmd:
        return INTERFACE_COUNTERS.get(host)
    if cmd.startswith("show interfaces") and "trunk" in cmd:
        return TRUNK_INTERFACES.get(host)
    if cmd.startswith("show interfaces") and "status" in cmd:
        return INTERFACE_STATUS.get(host)
    if cmd.startswith("show interfaces"):
        return INTERFACE_STATUS.get(host)
    if cmd.startswith("show ip route summary"):
        return ROUTE_SUMMARY.get(host)
    if cmd.startswith("show ip route"):
        return ROUTING_TABLE.get(host)
    if cmd.startswith("show running-config"):
        return RUNNING_CONFIG.get(host)
    if cmd.startswith("show startup-config"):
        return RUNNING_CONFIG.get(host)
    if cmd.startswith("show logging"):
        return LOGGING.get(host)
    if cmd.startswith("show vlan"):
        return VLANS.get(host)
    if cmd.startswith("show mac address-table"):
        return MAC_TABLE.get(host)
    if cmd.startswith("show lldp neighbors detail") or cmd.startswith("show lldp neighbor"):
        return LLDP_NEIGHBOR_DETAIL.get(host)
    if cmd.startswith("show lldp"):
        return LLDP_NEIGHBORS.get(host)
    if cmd.startswith("show mlag config-sanity"):
        return MLAG_CONFIG_SANITY.get(host)
    if cmd.startswith("show mlag interfaces"):
        return MLAG_INTERFACES.get(host)
    if cmd.startswith("show mlag"):
        return MLAG_STATUS.get(host)
    if cmd.startswith("show ntp"):
        return NTP_STATUS.get(host)
    if cmd.startswith("show processes"):
        return CPU_PROCESS.get(host)
    if cmd.startswith("show system environment temperature") or cmd.startswith("show environment temperature"):
        return TEMPERATURE.get(host)
    if cmd.startswith("show environment power"):
        return ENV_POWER.get(host)
    if cmd.startswith("show environment cooling"):
        return ENV_COOLING.get(host)
    if cmd.startswith("show inventory"):
        return INVENTORY.get(host)
    if cmd.startswith("show memory"):
        return MEMORY.get(host)
    if cmd.startswith("show spanning-tree"):
        return STP.get(host)
    if cmd.startswith("show port-channel"):
        return PORT_CHANNELS.get(host)
    if cmd.startswith("show route-map"):
        return ROUTE_MAPS.get(host)
    if cmd.startswith("show ip prefix-list"):
        return PREFIX_LISTS.get(host)
    if cmd.startswith("show ip access-list"):
        return ACCESS_LISTS.get(host)
    if cmd.startswith("show reload cause"):
        return RELOAD_CAUSE.get(host)
    if cmd.startswith("show configuration sessions"):
        return CONFIG_SESSIONS.get(host)
    if cmd.startswith("show configuration checkpoints"):
        return CONFIG_CHECKPOINTS.get(host)
    if cmd.startswith("show ip ospf neighbor"):
        return OSPF_NEIGHBORS.get(host)
    if cmd.startswith("show ip ospf interface"):
        return OSPF_INTERFACES.get(host)
    if cmd.startswith("show ip arp") or cmd.startswith("show arp"):
        return ARP_TABLE.get(host)
    if cmd.startswith("show vrf"):
        return VRFS.get(host)
    if cmd.startswith("show hardware capacity"):
        return HARDWARE_CAPACITY.get(host)
    if cmd.startswith("show hardware counter drop"):
        return HARDWARE_DROPS.get(host)
    if cmd.startswith("show ip interface"):
        return IP_INTERFACES.get(host)
    if cmd.startswith("show extensions"):
        return EXTENSIONS.get(host)
    if cmd.startswith("show users"):
        return USERS.get(host)
    if cmd.startswith("show aaa"):
        return AAA.get(host)
    if cmd.startswith("show snmp"):
        return SNMP.get(host)
    if cmd.startswith("show policy-map"):
        return COPP_POLICY.get(host)
    if cmd.startswith("show management api"):
        return EAPI_STATUS.get(host)
    if cmd.startswith("show vrrp"):
        return VRRP.get(host)
    if cmd.startswith("show ip virtual-router"):
        return VARP.get(host)

    return None
