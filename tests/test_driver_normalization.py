"""Tests for normalized getter methods across all 4 vendor drivers.

Validates that EOS (PyeapiDriver), IOS-XE (IosXeDriver), NX-OS (NxosDriver),
and JunOS (JunosDriver) correctly normalize vendor-specific API responses into
the common NetworkDriver schema.

Covers: get_facts, get_interfaces, get_bgp_summary, get_arp_table,
get_lldp_neighbors, get_config — plus MAC normalization, uptime conversion,
interface status mapping, and missing-field handling.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from network_mcp.driver import EosDriver
from network_mcp.drivers.cisco_iosxe import IosXeDriver
from network_mcp.drivers.cisco_nxos import NxosDriver
from network_mcp.drivers.juniper_junos import JunosDriver

# ============================================================================
# Mock raw API responses — one per vendor per getter
# ============================================================================

# --- EOS (pyeapi) mock responses ---

EOS_SHOW_VERSION = {
    "hostname": "eos-spine-01",
    "modelName": "DCS-7050CX3-32S",
    "version": "4.31.1F",
    "serialNumber": "JPE12345678",
    "uptime": 864000.5,
}

EOS_SHOW_INTERFACES = {
    "interfaces": {
        "Ethernet1": {
            "lineProtocolStatus": "up",
            "interfaceStatus": "connected",
            "description": "to-leaf-01",
            "bandwidth": 100000000000,
            "mtu": 9214,
            "physicalAddress": "00:1c:73:aa:bb:01",
        },
        "Ethernet2": {
            "lineProtocolStatus": "down",
            "interfaceStatus": "disabled",
            "description": "",
            "bandwidth": 10000000000,
            "mtu": 1500,
            "burnedInAddress": "001c.73aa.bb02",
        },
    }
}

EOS_BGP_SUMMARY = {
    "vrfs": {
        "default": {
            "routerId": "10.0.0.1",
            "asn": "65000",
            "peers": {
                "10.0.0.2": {
                    "peerState": "Established",
                    "prefixReceived": 150,
                    "upDownTime": 86400,
                },
                "10.0.0.3": {
                    "peerState": "Active",
                    "prefixReceived": 0,
                    "upDownTime": 0,
                },
            },
        }
    }
}

EOS_BGP_EMPTY = {"vrfs": {"default": {"routerId": "", "asn": 0, "peers": {}}}}

EOS_ARP_TABLE = {
    "ipV4Neighbors": [
        {
            "address": "10.0.0.2",
            "hwAddress": "00:1c:73:cc:dd:01",
            "interface": "Ethernet1",
            "age": 300.0,
        },
        {
            "address": "10.0.0.3",
            "hwAddress": "001c.73cc.dd02",
            "interface": "Ethernet2",
            "age": -1,
        },
    ]
}

EOS_LLDP_NEIGHBORS = {
    "lldpNeighbors": {
        "Ethernet1": [
            {
                "lldpNeighborInfo": [
                    {
                        "systemName": "leaf-01",
                        "neighborInterfaceInfo": {"interfaceId_v2": "Ethernet49"},
                        "systemDescription": "Arista EOS",
                    }
                ]
            }
        ],
    }
}

EOS_RUNNING_CONFIG = {"output": "! device: eos-spine-01\nhostname eos-spine-01\n"}
EOS_STARTUP_CONFIG = {"output": "! device: eos-spine-01\nhostname eos-spine-01\n"}

# --- IOS-XE (RESTCONF) mock responses ---

IOSXE_NATIVE = {
    "Cisco-IOS-XE-native:native": {
        "hostname": "iosxe-rtr-01",
        "version": "17.9.4",
        "license": {
            "udi": {
                "pid": "C8300-1N1S-4T2X",
                "sn": "FDO12345678",
            }
        },
    }
}

IOSXE_INTERFACES = {
    "ietf-interfaces:interfaces": {
        "interface": [
            {
                "name": "GigabitEthernet1",
                "oper-status": "up",
                "enabled": True,
                "description": "WAN uplink",
                "speed": 1000000000,
                "mtu": 1500,
                "phys-address": "00:50:56:a0:00:01",
            },
            {
                "name": "GigabitEthernet2",
                "oper-status": "down",
                "enabled": False,
                "description": "",
                "speed": 100000000,
                "mtu": 9000,
                "phys-address": "0050.56A0.0002",
            },
        ]
    }
}

IOSXE_BGP_OPER = {
    "Cisco-IOS-XE-bgp-oper:bgp-state-data": {
        "address-families": {
            "address-family": [
                {
                    "vrf-name": "default",
                    "router-id": "192.168.1.1",
                    "local-as": 65100,
                }
            ]
        },
        "neighbors": {
            "neighbor": [
                {
                    "vrf-name": "default",
                    "neighbor-id": "192.168.1.2",
                    "connection": {"state": "Established"},
                    "prefix-activity": {"received": {"total-prefixes": 200}},
                    "up-time": 7200,
                },
            ]
        },
    }
}

IOSXE_BGP_EMPTY = {
    "Cisco-IOS-XE-bgp-oper:bgp-state-data": {
        "address-families": {"address-family": []},
        "neighbors": {"neighbor": []},
    }
}

IOSXE_ARP_OPER = {
    "Cisco-IOS-XE-arp-oper:arp-data": {
        "arp-vrf": [
            {
                "vrf": "default",
                "arp-entry": [
                    {
                        "address": "10.1.1.1",
                        "hardware": "AA:BB:CC:DD:EE:01",
                        "interface": "GigabitEthernet1",
                        "time": 120.0,
                    },
                    {
                        "address": "10.1.1.2",
                        "hardware": "aabb.ccdd.ee02",
                        "interface": "GigabitEthernet2",
                        "time": -1,
                    },
                ],
            }
        ]
    }
}

IOSXE_LLDP_OPER = {
    "Cisco-IOS-XE-lldp-oper:lldp-entries": {
        "lldp-entry": [
            {
                "local-interface": "GigabitEthernet1",
                "device-id": "switch-01.lab",
                "connecting-interface": "Gi0/1",
                "system-desc": "Cisco IOS XE",
            }
        ]
    }
}

# --- NX-OS (NX-API) mock responses ---

NXOS_SHOW_VERSION_BODY = {
    "host_name": "nxos-leaf-01",
    "chassis_id": "Nexus9000 C93180YC-FX3",
    "nxos_ver_str": "10.3(2)",
    "proc_board_id": "FDO99887766",
    "kern_uptm_days": 10,
    "kern_uptm_hrs": 3,
    "kern_uptm_mins": 45,
    "kern_uptm_secs": 12,
}

NXOS_SHOW_INTERFACE_BODY = {
    "TABLE_interface": {
        "ROW_interface": [
            {
                "interface": "Ethernet1/1",
                "state": "up",
                "admin_state": "up",
                "desc": "to-spine-01",
                "eth_speed": "100 Gb/s",
                "eth_mtu": "9216",
                "eth_hw_addr": "00aa.bbcc.dd01",
            },
            {
                "interface": "Ethernet1/2",
                "state": "down",
                "admin_state": "down",
                "desc": "",
                "eth_speed": "auto",
                "eth_mtu": 1500,
                "eth_bia_addr": "00:aa:bb:cc:dd:02",
            },
        ]
    }
}

NXOS_BGP_SUMMARY_BODY = {
    "TABLE_vrf": {
        "ROW_vrf": {
            "vrf-name-out": "default",
            "router-id": "10.255.0.1",
            "local-as": 65200,
            "TABLE_af": {
                "ROW_af": {
                    "TABLE_saf": {
                        "ROW_saf": {
                            "TABLE_neighbor": {
                                "ROW_neighbor": [
                                    {
                                        "neighborid": "10.255.0.2",
                                        "state": "Established",
                                        "prefixreceived": 300,
                                        "resettime": "P5DT2H30M",
                                    },
                                    {
                                        "neighborid": "10.255.0.3",
                                        "state": "Idle",
                                        "prefixreceived": 0,
                                        "resettime": "never",
                                    },
                                ]
                            }
                        }
                    }
                }
            },
        }
    }
}

NXOS_BGP_EMPTY_BODY = {
    "TABLE_vrf": {
        "ROW_vrf": {
            "vrf-name-out": "default",
            "router-id": "",
            "local-as": 0,
        }
    }
}

NXOS_ARP_BODY = {
    "TABLE_vrf": {
        "ROW_vrf": {
            "TABLE_adj": {
                "ROW_adj": [
                    {
                        "ip-addr-out": "10.10.0.1",
                        "mac": "AABB.CCDD.EE03",
                        "intf-out": "Ethernet1/1",
                        "time-stamp": "00:05:30",
                    },
                    {
                        "ip-addr-out": "10.10.0.2",
                        "mac": "aa-bb-cc-dd-ee-04",
                        "intf-out": "Ethernet1/2",
                        "time-stamp": "-",
                    },
                ]
            }
        }
    }
}

NXOS_LLDP_BODY = {
    "TABLE_nbor_detail": {
        "ROW_nbor_detail": [
            {
                "l_port_id": "Ethernet1/1",
                "sys_name": "spine-01",
                "port_id": "Ethernet2/1",
                "sys_desc": "Cisco NX-OS",
            }
        ]
    }
}


# NX-API wraps responses in ins_api structure
def _nxapi_wrap(body: dict) -> dict:
    """Wrap a body dict in NX-API response envelope."""
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


def _nxapi_wrap_text(text: str) -> dict:
    """Wrap text output in NX-API response envelope."""
    return {
        "ins_api": {
            "outputs": {
                "output": {
                    "code": "200",
                    "msg": "Success",
                    "body": text,
                }
            }
        }
    }


# --- JunOS (NETCONF XML) mock responses ---

JUNOS_SYSTEM_INFO_XML = """<rpc-reply>
  <system-information>
    <host-name>junos-spine-01</host-name>
    <hardware-model>QFX5120-48T</hardware-model>
    <os-version>23.4R1.10</os-version>
    <serial-number>WR0012345678</serial-number>
    <up-time>864000</up-time>
  </system-information>
</rpc-reply>"""

JUNOS_INTERFACES_XML = """<rpc-reply>
  <interface-information>
    <physical-interface>
      <name>ge-0/0/0</name>
      <oper-status>up</oper-status>
      <admin-status>up</admin-status>
      <description>to-leaf-01</description>
      <speed>1000mbps</speed>
      <mtu>9192</mtu>
      <current-physical-address>00:05:86:71:62:01</current-physical-address>
    </physical-interface>
    <physical-interface>
      <name>ge-0/0/1</name>
      <oper-status>down</oper-status>
      <admin-status>down</admin-status>
      <description></description>
      <speed>Auto</speed>
      <mtu>1514</mtu>
      <hardware-physical-address>0005.8671.6202</hardware-physical-address>
    </physical-interface>
  </interface-information>
</rpc-reply>"""

JUNOS_BGP_SUMMARY_XML = """<rpc-reply>
  <bgp-information>
    <local-id>10.100.0.1</local-id>
    <local-as>65300</local-as>
    <bgp-peer>
      <peer-address>10.100.0.2+179</peer-address>
      <peer-state>Established</peer-state>
      <bgp-rib>
        <received-prefix-count>450</received-prefix-count>
      </bgp-rib>
      <elapsed-time>86400</elapsed-time>
    </bgp-peer>
    <bgp-peer>
      <peer-address>10.100.0.3</peer-address>
      <peer-state>Active</peer-state>
      <elapsed-time>0</elapsed-time>
    </bgp-peer>
  </bgp-information>
</rpc-reply>"""

JUNOS_BGP_EMPTY_XML = """<rpc-reply>
  <bgp-information>
    <local-id></local-id>
    <local-as></local-as>
  </bgp-information>
</rpc-reply>"""

JUNOS_ARP_TABLE_XML = """<rpc-reply>
  <arp-table-information>
    <arp-table-entry>
      <ip-address>10.200.0.1</ip-address>
      <mac-address>00:05:86:AA:BB:01</mac-address>
      <interface-name>ge-0/0/0</interface-name>
      <timer>600</timer>
    </arp-table-entry>
    <arp-table-entry>
      <ip-address>10.200.0.2</ip-address>
      <mac-address>0005.86AA.BB02</mac-address>
      <interface-name>ge-0/0/1</interface-name>
      <timer></timer>
    </arp-table-entry>
  </arp-table-information>
</rpc-reply>"""

JUNOS_LLDP_XML = """<rpc-reply>
  <lldp-neighbors-information>
    <lldp-neighbor-information>
      <lldp-local-port-id>ge-0/0/0</lldp-local-port-id>
      <lldp-remote-system-name>leaf-01.lab</lldp-remote-system-name>
      <lldp-remote-port-id>ge-0/0/1</lldp-remote-port-id>
      <lldp-remote-system-description>Juniper JunOS</lldp-remote-system-description>
    </lldp-neighbor-information>
  </lldp-neighbors-information>
</rpc-reply>"""

JUNOS_RUNNING_CONFIG = "<configuration>system { host-name junos-spine-01; }</configuration>"


# ============================================================================
# Helper to set up drivers with mocked transports
# ============================================================================


def _make_eos_driver(run_show_return: list[dict]) -> EosDriver:
    """Create an EosDriver with a mocked _node."""
    driver = EosDriver()
    driver._node = MagicMock()
    driver._node.run_commands.return_value = run_show_return
    driver._last_command_time = time.monotonic()
    return driver


def _make_iosxe_driver() -> IosXeDriver:
    """Create an IosXeDriver with a mocked RESTCONF client."""
    driver = IosXeDriver()
    driver._restconf = MagicMock()
    driver._connected = True
    driver._last_command_time = time.monotonic()
    return driver


def _make_nxos_driver() -> NxosDriver:
    """Create an NxosDriver with a mocked NX-API client."""
    driver = NxosDriver()
    driver._nxapi = MagicMock()
    driver._connected = True
    driver._last_command_time = time.monotonic()
    return driver


def _make_junos_driver() -> JunosDriver:
    """Create a JunosDriver with a mocked NETCONF connection."""
    driver = JunosDriver()
    driver._conn = MagicMock()
    driver._connected = True
    driver._last_command_time = time.monotonic()
    return driver


# ============================================================================
# get_facts() tests
# ============================================================================


class TestEosGetFacts:
    """Test EosDriver.get_facts() normalization."""

    def test_get_facts(self):
        driver = _make_eos_driver([EOS_SHOW_VERSION])
        facts = driver.get_facts()
        assert facts["hostname"] == "eos-spine-01"
        assert facts["model"] == "DCS-7050CX3-32S"
        assert facts["version"] == "4.31.1F"
        assert facts["serial"] == "JPE12345678"
        assert facts["uptime_seconds"] == 864000
        assert isinstance(facts["uptime_seconds"], int)
        assert facts["vendor"] == "arista"
        assert facts["platform"] == "eos"

    def test_get_facts_missing_fields(self):
        driver = _make_eos_driver([{}])
        facts = driver.get_facts()
        assert facts["hostname"] == ""
        assert facts["uptime_seconds"] == 0


class TestIosXeGetFacts:
    """Test IosXeDriver.get_facts() normalization via RESTCONF."""

    def test_get_facts_restconf(self):
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_NATIVE
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp

        facts = driver.get_facts()
        assert facts["hostname"] == "iosxe-rtr-01"
        assert facts["model"] == "C8300-1N1S-4T2X"
        assert facts["version"] == "17.9.4"
        assert facts["serial"] == "FDO12345678"
        assert isinstance(facts["uptime_seconds"], int)
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "iosxe"

    def test_get_facts_missing_license(self):
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"Cisco-IOS-XE-native:native": {"hostname": "rtr"}}
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp

        facts = driver.get_facts()
        assert facts["hostname"] == "rtr"
        assert facts["model"] == ""
        assert facts["serial"] == ""


class TestNxosGetFacts:
    """Test NxosDriver.get_facts() normalization via NX-API."""

    def test_get_facts_nxapi(self):
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_SHOW_VERSION_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp

        facts = driver.get_facts()
        assert facts["hostname"] == "nxos-leaf-01"
        assert facts["model"] == "Nexus9000 C93180YC-FX3"
        assert facts["version"] == "10.3(2)"
        assert facts["serial"] == "FDO99887766"
        # 10 days * 86400 + 3 * 3600 + 45 * 60 + 12 = 877512
        assert facts["uptime_seconds"] == 877512
        assert isinstance(facts["uptime_seconds"], int)
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "nxos"


class TestJunosGetFacts:
    """Test JunosDriver.get_facts() normalization via NETCONF."""

    def test_get_facts_netconf(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_SYSTEM_INFO_XML
        driver._conn.rpc.return_value = mock_response

        facts = driver.get_facts()
        assert facts["hostname"] == "junos-spine-01"
        assert facts["model"] == "QFX5120-48T"
        assert facts["version"] == "23.4R1.10"
        assert facts["serial"] == "WR0012345678"
        assert facts["uptime_seconds"] == 864000
        assert isinstance(facts["uptime_seconds"], int)
        assert facts["vendor"] == "juniper"
        assert facts["platform"] == "junos"


# ============================================================================
# get_facts() — parametrized schema validation across all drivers
# ============================================================================


FACTS_SCHEMA_KEYS = {"hostname", "model", "version", "serial", "uptime_seconds", "vendor", "platform"}


class TestFactsSchemaConsistency:
    """All drivers return the same keys in get_facts()."""

    def test_eos_facts_keys(self):
        driver = _make_eos_driver([EOS_SHOW_VERSION])
        assert set(driver.get_facts().keys()) == FACTS_SCHEMA_KEYS

    def test_iosxe_facts_keys(self):
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_NATIVE
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp
        assert set(driver.get_facts().keys()) == FACTS_SCHEMA_KEYS

    def test_nxos_facts_keys(self):
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_SHOW_VERSION_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp
        assert set(driver.get_facts().keys()) == FACTS_SCHEMA_KEYS

    def test_junos_facts_keys(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_SYSTEM_INFO_XML
        driver._conn.rpc.return_value = mock_response
        assert set(driver.get_facts().keys()) == FACTS_SCHEMA_KEYS


# ============================================================================
# get_interfaces() tests
# ============================================================================


INTF_SCHEMA_KEYS = {"is_up", "is_enabled", "description", "speed", "mtu", "mac_address"}


class TestEosGetInterfaces:
    """Test EosDriver.get_interfaces() normalization."""

    def test_get_interfaces(self):
        driver = _make_eos_driver([EOS_SHOW_INTERFACES])
        intfs = driver.get_interfaces()

        assert "Ethernet1" in intfs
        eth1 = intfs["Ethernet1"]
        assert eth1["is_up"] is True
        assert eth1["is_enabled"] is True
        assert eth1["description"] == "to-leaf-01"
        assert eth1["speed"] == 100000  # 100Gbps
        assert eth1["mtu"] == 9214
        # MAC normalized to xx:xx:xx:xx:xx:xx
        assert eth1["mac_address"] == "00:1c:73:aa:bb:01"

        eth2 = intfs["Ethernet2"]
        assert eth2["is_up"] is False
        assert eth2["is_enabled"] is False
        # Cisco dot-notation burnedInAddress normalized
        assert eth2["mac_address"] == "00:1c:73:aa:bb:02"

    def test_interface_schema_keys(self):
        driver = _make_eos_driver([EOS_SHOW_INTERFACES])
        intfs = driver.get_interfaces()
        for name, data in intfs.items():
            assert set(data.keys()) == INTF_SCHEMA_KEYS, f"{name} has wrong keys"


class TestIosXeGetInterfaces:
    """Test IosXeDriver.get_interfaces() normalization via RESTCONF."""

    def test_get_interfaces_restconf(self):
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_INTERFACES
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp

        intfs = driver.get_interfaces()
        assert "GigabitEthernet1" in intfs
        gi1 = intfs["GigabitEthernet1"]
        assert gi1["is_up"] is True
        assert gi1["is_enabled"] is True
        assert gi1["description"] == "WAN uplink"
        assert gi1["speed"] == 1000
        assert gi1["mtu"] == 1500
        assert gi1["mac_address"] == "00:50:56:a0:00:01"

        gi2 = intfs["GigabitEthernet2"]
        assert gi2["is_up"] is False
        assert gi2["is_enabled"] is False
        # Cisco dot-notation normalized
        assert gi2["mac_address"] == "00:50:56:a0:00:02"

    def test_interface_schema_keys(self):
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_INTERFACES
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp
        intfs = driver.get_interfaces()
        for name, data in intfs.items():
            assert set(data.keys()) == INTF_SCHEMA_KEYS, f"{name} has wrong keys"


class TestNxosGetInterfaces:
    """Test NxosDriver.get_interfaces() normalization via NX-API."""

    def test_get_interfaces_nxapi(self):
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_SHOW_INTERFACE_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp

        intfs = driver.get_interfaces()
        assert "Ethernet1/1" in intfs
        e1 = intfs["Ethernet1/1"]
        assert e1["is_up"] is True
        assert e1["is_enabled"] is True
        assert e1["description"] == "to-spine-01"
        assert e1["speed"] == 100000  # "100 Gb/s" → 100000 Mbps
        assert e1["mtu"] == 9216
        assert e1["mac_address"] == "00:aa:bb:cc:dd:01"

        e2 = intfs["Ethernet1/2"]
        assert e2["is_up"] is False
        assert e2["is_enabled"] is False
        assert e2["speed"] == 0  # "auto"
        assert e2["mac_address"] == "00:aa:bb:cc:dd:02"

    def test_interface_schema_keys(self):
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_SHOW_INTERFACE_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp
        intfs = driver.get_interfaces()
        for name, data in intfs.items():
            assert set(data.keys()) == INTF_SCHEMA_KEYS, f"{name} has wrong keys"


class TestJunosGetInterfaces:
    """Test JunosDriver.get_interfaces() normalization via NETCONF."""

    def test_get_interfaces_netconf(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_INTERFACES_XML
        driver._conn.rpc.return_value = mock_response

        intfs = driver.get_interfaces()
        assert "ge-0/0/0" in intfs
        ge0 = intfs["ge-0/0/0"]
        assert ge0["is_up"] is True
        assert ge0["is_enabled"] is True
        assert ge0["description"] == "to-leaf-01"
        assert ge0["speed"] == 1000
        assert ge0["mtu"] == 9192
        assert ge0["mac_address"] == "00:05:86:71:62:01"

        ge1 = intfs["ge-0/0/1"]
        assert ge1["is_up"] is False
        assert ge1["is_enabled"] is False
        assert ge1["speed"] == 0  # "Auto"
        # dot-notation hardware address normalized
        assert ge1["mac_address"] == "00:05:86:71:62:02"

    def test_interface_schema_keys(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_INTERFACES_XML
        driver._conn.rpc.return_value = mock_response
        intfs = driver.get_interfaces()
        for name, data in intfs.items():
            assert set(data.keys()) == INTF_SCHEMA_KEYS, f"{name} has wrong keys"


# ============================================================================
# get_bgp_summary() tests
# ============================================================================


BGP_SCHEMA_KEYS = {"router_id", "as_number", "peers"}
BGP_PEER_KEYS = {"state", "prefixes_received", "uptime"}


class TestEosGetBgpSummary:
    """Test EosDriver.get_bgp_summary() normalization."""

    def test_get_bgp_summary(self):
        driver = _make_eos_driver([EOS_BGP_SUMMARY])
        bgp = driver.get_bgp_summary()
        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65000
        assert isinstance(bgp["as_number"], int)
        assert len(bgp["peers"]) == 2
        assert bgp["peers"]["10.0.0.2"]["state"] == "Established"
        assert bgp["peers"]["10.0.0.2"]["prefixes_received"] == 150
        assert bgp["peers"]["10.0.0.3"]["state"] == "Active"
        assert bgp["peers"]["10.0.0.3"]["prefixes_received"] == 0

    def test_bgp_no_peers(self):
        driver = _make_eos_driver([EOS_BGP_EMPTY])
        bgp = driver.get_bgp_summary()
        assert bgp["peers"] == {}
        assert set(bgp.keys()) == BGP_SCHEMA_KEYS


class TestIosXeGetBgpSummary:
    """Test IosXeDriver.get_bgp_summary() normalization via RESTCONF."""

    def test_get_bgp_summary_restconf(self):
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_BGP_OPER
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp

        bgp = driver.get_bgp_summary()
        assert bgp["router_id"] == "192.168.1.1"
        assert bgp["as_number"] == 65100
        assert isinstance(bgp["as_number"], int)
        assert "192.168.1.2" in bgp["peers"]
        assert bgp["peers"]["192.168.1.2"]["state"] == "Established"
        assert bgp["peers"]["192.168.1.2"]["prefixes_received"] == 200

    def test_bgp_no_peers(self):
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_BGP_EMPTY
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp

        bgp = driver.get_bgp_summary()
        assert bgp["peers"] == {}
        assert set(bgp.keys()) == BGP_SCHEMA_KEYS


class TestNxosGetBgpSummary:
    """Test NxosDriver.get_bgp_summary() normalization via NX-API."""

    def test_get_bgp_summary_nxapi(self):
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_BGP_SUMMARY_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp

        bgp = driver.get_bgp_summary()
        assert bgp["router_id"] == "10.255.0.1"
        assert bgp["as_number"] == 65200
        assert isinstance(bgp["as_number"], int)
        assert len(bgp["peers"]) == 2
        assert bgp["peers"]["10.255.0.2"]["state"] == "Established"
        assert bgp["peers"]["10.255.0.2"]["prefixes_received"] == 300
        # ISO 8601 uptime: P5DT2H30M = 5*86400 + 2*3600 + 30*60 = 441000
        assert bgp["peers"]["10.255.0.2"]["uptime"] == 441000
        assert bgp["peers"]["10.255.0.3"]["state"] == "Idle"
        assert bgp["peers"]["10.255.0.3"]["uptime"] == 0  # "never"

    def test_bgp_no_peers(self):
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_BGP_EMPTY_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp

        bgp = driver.get_bgp_summary()
        assert bgp["peers"] == {}
        assert set(bgp.keys()) == BGP_SCHEMA_KEYS


class TestJunosGetBgpSummary:
    """Test JunosDriver.get_bgp_summary() normalization via NETCONF."""

    def test_get_bgp_summary_netconf(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_BGP_SUMMARY_XML
        driver._conn.rpc.return_value = mock_response

        bgp = driver.get_bgp_summary()
        assert bgp["router_id"] == "10.100.0.1"
        assert bgp["as_number"] == 65300
        assert isinstance(bgp["as_number"], int)
        # Peer address with +179 port suffix stripped
        assert "10.100.0.2" in bgp["peers"]
        assert bgp["peers"]["10.100.0.2"]["state"] == "Established"
        assert bgp["peers"]["10.100.0.2"]["prefixes_received"] == 450
        assert bgp["peers"]["10.100.0.2"]["uptime"] == 86400
        assert bgp["peers"]["10.100.0.3"]["state"] == "Active"

    def test_bgp_no_peers(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_BGP_EMPTY_XML
        driver._conn.rpc.return_value = mock_response

        bgp = driver.get_bgp_summary()
        assert bgp["peers"] == {}
        assert set(bgp.keys()) == BGP_SCHEMA_KEYS


# ============================================================================
# get_arp_table() tests
# ============================================================================


ARP_ENTRY_KEYS = {"ip", "mac", "interface", "age"}


class TestEosGetArpTable:
    """Test EosDriver.get_arp_table() normalization."""

    def test_get_arp_table(self):
        driver = _make_eos_driver([EOS_ARP_TABLE])
        arp = driver.get_arp_table()
        assert len(arp) == 2
        assert arp[0]["ip"] == "10.0.0.2"
        assert arp[0]["mac"] == "00:1c:73:cc:dd:01"
        assert arp[0]["interface"] == "Ethernet1"
        assert arp[0]["age"] == 300.0
        # Cisco dot-notation MAC normalized
        assert arp[1]["mac"] == "00:1c:73:cc:dd:02"
        assert set(arp[0].keys()) == ARP_ENTRY_KEYS

    def test_empty_arp(self):
        driver = _make_eos_driver([{"ipV4Neighbors": []}])
        arp = driver.get_arp_table()
        assert arp == []


class TestIosXeGetArpTable:
    """Test IosXeDriver.get_arp_table() normalization via RESTCONF."""

    def test_get_arp_table_restconf(self):
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_ARP_OPER
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp

        arp = driver.get_arp_table()
        assert len(arp) == 2
        assert arp[0]["ip"] == "10.1.1.1"
        assert arp[0]["mac"] == "aa:bb:cc:dd:ee:01"
        assert arp[0]["interface"] == "GigabitEthernet1"
        # Cisco dot-notation normalized
        assert arp[1]["mac"] == "aa:bb:cc:dd:ee:02"
        assert set(arp[0].keys()) == ARP_ENTRY_KEYS


class TestNxosGetArpTable:
    """Test NxosDriver.get_arp_table() normalization via NX-API."""

    def test_get_arp_table_nxapi(self):
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_ARP_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp

        arp = driver.get_arp_table()
        assert len(arp) == 2
        assert arp[0]["ip"] == "10.10.0.1"
        # Cisco dot-notation upper-case normalized
        assert arp[0]["mac"] == "aa:bb:cc:dd:ee:03"
        assert arp[0]["interface"] == "Ethernet1/1"
        # age from "00:05:30" = 330 seconds
        assert arp[0]["age"] == 330.0
        # Hyphen-notation MAC normalized
        assert arp[1]["mac"] == "aa:bb:cc:dd:ee:04"
        assert arp[1]["age"] == -1.0  # "-" → -1.0
        assert set(arp[0].keys()) == ARP_ENTRY_KEYS


class TestJunosGetArpTable:
    """Test JunosDriver.get_arp_table() normalization via NETCONF."""

    def test_get_arp_table_netconf(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_ARP_TABLE_XML
        driver._conn.rpc.return_value = mock_response

        arp = driver.get_arp_table()
        assert len(arp) == 2
        assert arp[0]["ip"] == "10.200.0.1"
        assert arp[0]["mac"] == "00:05:86:aa:bb:01"
        assert arp[0]["interface"] == "ge-0/0/0"
        assert arp[0]["age"] == 600.0
        # Dot-notation MAC normalized
        assert arp[1]["mac"] == "00:05:86:aa:bb:02"
        assert arp[1]["age"] == -1.0  # empty timer → -1.0
        assert set(arp[0].keys()) == ARP_ENTRY_KEYS


# ============================================================================
# get_lldp_neighbors() tests
# ============================================================================


LLDP_NEIGHBOR_KEYS = {"hostname", "port", "system_description", "management_ip"}


class TestEosGetLldpNeighbors:
    """Test EosDriver.get_lldp_neighbors() normalization."""

    def test_get_lldp_neighbors(self):
        driver = _make_eos_driver([EOS_LLDP_NEIGHBORS])
        lldp = driver.get_lldp_neighbors()
        assert "Ethernet1" in lldp
        assert len(lldp["Ethernet1"]) == 1
        n = lldp["Ethernet1"][0]
        assert n["hostname"] == "leaf-01"
        assert n["port"] == "Ethernet49"
        assert n["system_description"] == "Arista EOS"
        assert set(n.keys()) == LLDP_NEIGHBOR_KEYS

    def test_empty_lldp(self):
        driver = _make_eos_driver([{"lldpNeighbors": {}}])
        lldp = driver.get_lldp_neighbors()
        assert lldp == {}


class TestIosXeGetLldpNeighbors:
    """Test IosXeDriver.get_lldp_neighbors() normalization via RESTCONF."""

    def test_get_lldp_neighbors_restconf(self):
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_LLDP_OPER
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp

        lldp = driver.get_lldp_neighbors()
        assert "GigabitEthernet1" in lldp
        n = lldp["GigabitEthernet1"][0]
        assert n["hostname"] == "switch-01.lab"
        assert n["port"] == "Gi0/1"
        assert n["system_description"] == "Cisco IOS XE"
        assert set(n.keys()) == LLDP_NEIGHBOR_KEYS


class TestNxosGetLldpNeighbors:
    """Test NxosDriver.get_lldp_neighbors() normalization via NX-API."""

    def test_get_lldp_neighbors_nxapi(self):
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_LLDP_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp

        lldp = driver.get_lldp_neighbors()
        assert "Ethernet1/1" in lldp
        n = lldp["Ethernet1/1"][0]
        assert n["hostname"] == "spine-01"
        assert n["port"] == "Ethernet2/1"
        assert n["system_description"] == "Cisco NX-OS"
        assert set(n.keys()) == LLDP_NEIGHBOR_KEYS


class TestJunosGetLldpNeighbors:
    """Test JunosDriver.get_lldp_neighbors() normalization via NETCONF."""

    def test_get_lldp_neighbors_netconf(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_LLDP_XML
        driver._conn.rpc.return_value = mock_response

        lldp = driver.get_lldp_neighbors()
        assert "ge-0/0/0" in lldp
        n = lldp["ge-0/0/0"][0]
        assert n["hostname"] == "leaf-01.lab"
        assert n["port"] == "ge-0/0/1"
        assert n["system_description"] == "Juniper JunOS"
        assert set(n.keys()) == LLDP_NEIGHBOR_KEYS


# ============================================================================
# get_config() tests
# ============================================================================


class TestEosGetConfig:
    """Test EosDriver.get_config() normalization."""

    def test_get_config(self):
        driver = _make_eos_driver([EOS_RUNNING_CONFIG, EOS_STARTUP_CONFIG])
        with patch("network_mcp.driver.EosDriver.run_show") as mock_run:
            mock_run.return_value = [EOS_RUNNING_CONFIG, EOS_STARTUP_CONFIG]
            cfg = driver.get_config(sanitized=False)
        assert "running" in cfg
        assert "startup" in cfg
        assert "hostname eos-spine-01" in cfg["running"]

    def test_get_config_keys(self):
        driver = _make_eos_driver([EOS_RUNNING_CONFIG, EOS_STARTUP_CONFIG])
        with patch("network_mcp.driver.EosDriver.run_show") as mock_run:
            mock_run.return_value = [EOS_RUNNING_CONFIG, EOS_STARTUP_CONFIG]
            cfg = driver.get_config(sanitized=False)
        assert set(cfg.keys()) == {"running", "startup"}


class TestIosXeGetConfig:
    """Test IosXeDriver.get_config() normalization."""

    def test_get_config(self):
        driver = _make_iosxe_driver()
        # get_config always uses SSH for text config
        with patch.object(driver, "_ssh_show") as mock_ssh:
            mock_ssh.return_value = [
                {"output": "hostname iosxe-rtr-01\n"},
                {"output": "hostname iosxe-rtr-01\n"},
            ]
            cfg = driver.get_config(sanitized=False)
        assert "running" in cfg
        assert "startup" in cfg
        assert "hostname iosxe-rtr-01" in cfg["running"]

    def test_get_config_keys(self):
        driver = _make_iosxe_driver()
        with patch.object(driver, "_ssh_show") as mock_ssh:
            mock_ssh.return_value = [
                {"output": "config text"},
                {"output": "config text"},
            ]
            cfg = driver.get_config(sanitized=False)
        assert set(cfg.keys()) == {"running", "startup"}


class TestNxosGetConfig:
    """Test NxosDriver.get_config() normalization via NX-API."""

    def test_get_config_nxapi(self):
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        # Two calls: running-config and startup-config
        mock_resp.json.side_effect = [
            _nxapi_wrap_text("hostname nxos-leaf-01\n"),
            _nxapi_wrap_text("hostname nxos-leaf-01\n"),
        ]
        driver._nxapi.post.return_value = mock_resp

        cfg = driver.get_config(sanitized=False)
        assert "running" in cfg
        assert "startup" in cfg
        assert set(cfg.keys()) == {"running", "startup"}


class TestJunosGetConfig:
    """Test JunosDriver.get_config() normalization via NETCONF."""

    def test_get_config_netconf(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_RUNNING_CONFIG
        driver._conn.get_config.return_value = mock_response

        cfg = driver.get_config(sanitized=False)
        assert "running" in cfg
        assert "startup" in cfg
        # JunOS running == startup
        assert cfg["running"] == cfg["startup"]
        assert "host-name" in cfg["running"]

    def test_get_config_keys(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_RUNNING_CONFIG
        driver._conn.get_config.return_value = mock_response
        cfg = driver.get_config(sanitized=False)
        assert set(cfg.keys()) == {"running", "startup"}


# ============================================================================
# MAC address normalization — cross-vendor
# ============================================================================


class TestMacNormalization:
    """All drivers normalize MAC addresses to xx:xx:xx:xx:xx:xx format."""

    def test_eos_colon_mac(self):
        """EOS physicalAddress already in colon format stays normalized."""
        driver = _make_eos_driver([EOS_SHOW_INTERFACES])
        intfs = driver.get_interfaces()
        assert intfs["Ethernet1"]["mac_address"] == "00:1c:73:aa:bb:01"

    def test_eos_dot_mac(self):
        """EOS burnedInAddress in Cisco dot format gets normalized."""
        driver = _make_eos_driver([EOS_SHOW_INTERFACES])
        intfs = driver.get_interfaces()
        assert intfs["Ethernet2"]["mac_address"] == "00:1c:73:aa:bb:02"

    def test_iosxe_colon_mac(self):
        """IOS-XE RESTCONF phys-address in colon format."""
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_INTERFACES
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp
        intfs = driver.get_interfaces()
        assert intfs["GigabitEthernet1"]["mac_address"] == "00:50:56:a0:00:01"

    def test_iosxe_dot_mac(self):
        """IOS-XE dot-notation MAC gets normalized."""
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_INTERFACES
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp
        intfs = driver.get_interfaces()
        assert intfs["GigabitEthernet2"]["mac_address"] == "00:50:56:a0:00:02"

    def test_nxos_dot_mac_upper(self):
        """NX-OS upper-case dot-notation MAC gets normalized."""
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_SHOW_INTERFACE_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp
        intfs = driver.get_interfaces()
        assert intfs["Ethernet1/1"]["mac_address"] == "00:aa:bb:cc:dd:01"

    def test_nxos_colon_mac(self):
        """NX-OS colon-format MAC gets normalized."""
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_SHOW_INTERFACE_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp
        intfs = driver.get_interfaces()
        assert intfs["Ethernet1/2"]["mac_address"] == "00:aa:bb:cc:dd:02"

    def test_junos_colon_mac(self):
        """JunOS colon-format MAC gets normalized."""
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_INTERFACES_XML
        driver._conn.rpc.return_value = mock_response
        intfs = driver.get_interfaces()
        assert intfs["ge-0/0/0"]["mac_address"] == "00:05:86:71:62:01"

    def test_junos_dot_mac(self):
        """JunOS dot-notation hardware-physical-address gets normalized."""
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_INTERFACES_XML
        driver._conn.rpc.return_value = mock_response
        intfs = driver.get_interfaces()
        assert intfs["ge-0/0/1"]["mac_address"] == "00:05:86:71:62:02"

    def test_arp_mac_formats(self):
        """ARP table MAC addresses are normalized across vendors."""
        # EOS
        eos_driver = _make_eos_driver([EOS_ARP_TABLE])
        arp = eos_driver.get_arp_table()
        for entry in arp:
            assert ":" in entry["mac"] and len(entry["mac"]) == 17, f"Bad MAC: {entry['mac']}"

        # NX-OS
        nxos_driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_ARP_BODY)
        mock_resp.raise_for_status = MagicMock()
        nxos_driver._nxapi.post.return_value = mock_resp
        arp = nxos_driver.get_arp_table()
        for entry in arp:
            assert ":" in entry["mac"] and len(entry["mac"]) == 17, f"Bad MAC: {entry['mac']}"


# ============================================================================
# Uptime conversion tests — all drivers return int seconds
# ============================================================================


class TestUptimeConversion:
    """All drivers return uptime as an integer in seconds."""

    def test_eos_uptime_is_int(self):
        driver = _make_eos_driver([EOS_SHOW_VERSION])
        facts = driver.get_facts()
        assert isinstance(facts["uptime_seconds"], int)
        assert facts["uptime_seconds"] == 864000

    def test_nxos_uptime_components(self):
        """NX-OS calculates uptime from days/hrs/mins/secs components."""
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_SHOW_VERSION_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp
        facts = driver.get_facts()
        expected = (10 * 86400) + (3 * 3600) + (45 * 60) + 12
        assert facts["uptime_seconds"] == expected
        assert isinstance(facts["uptime_seconds"], int)

    def test_junos_uptime_seconds_string(self):
        """JunOS plain seconds string parsed correctly."""
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_SYSTEM_INFO_XML
        driver._conn.rpc.return_value = mock_response
        facts = driver.get_facts()
        assert facts["uptime_seconds"] == 864000
        assert isinstance(facts["uptime_seconds"], int)


# ============================================================================
# Interface status normalization
# ============================================================================


class TestInterfaceStatusNormalization:
    """All drivers correctly map interface up/down/disabled states."""

    def test_eos_up_interface(self):
        driver = _make_eos_driver([EOS_SHOW_INTERFACES])
        intfs = driver.get_interfaces()
        assert intfs["Ethernet1"]["is_up"] is True
        assert intfs["Ethernet1"]["is_enabled"] is True

    def test_eos_disabled_interface(self):
        driver = _make_eos_driver([EOS_SHOW_INTERFACES])
        intfs = driver.get_interfaces()
        assert intfs["Ethernet2"]["is_up"] is False
        assert intfs["Ethernet2"]["is_enabled"] is False

    def test_iosxe_enabled_and_disabled(self):
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_INTERFACES
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp
        intfs = driver.get_interfaces()
        assert intfs["GigabitEthernet1"]["is_up"] is True
        assert intfs["GigabitEthernet1"]["is_enabled"] is True
        assert intfs["GigabitEthernet2"]["is_up"] is False
        assert intfs["GigabitEthernet2"]["is_enabled"] is False

    def test_nxos_admin_state(self):
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_SHOW_INTERFACE_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp
        intfs = driver.get_interfaces()
        assert intfs["Ethernet1/1"]["is_up"] is True
        assert intfs["Ethernet1/1"]["is_enabled"] is True
        assert intfs["Ethernet1/2"]["is_up"] is False
        assert intfs["Ethernet1/2"]["is_enabled"] is False

    def test_junos_admin_status(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_INTERFACES_XML
        driver._conn.rpc.return_value = mock_response
        intfs = driver.get_interfaces()
        assert intfs["ge-0/0/0"]["is_up"] is True
        assert intfs["ge-0/0/0"]["is_enabled"] is True
        assert intfs["ge-0/0/1"]["is_up"] is False
        assert intfs["ge-0/0/1"]["is_enabled"] is False


# ============================================================================
# EOS errdisabled interface status
# ============================================================================


class TestEosErrdisabledInterface:
    """EOS errdisabled interface is_enabled=False."""

    def test_errdisabled(self):
        mock_data = {
            "interfaces": {
                "Ethernet3": {
                    "lineProtocolStatus": "down",
                    "interfaceStatus": "errdisabled",
                    "description": "",
                    "bandwidth": 0,
                    "mtu": 1500,
                    "physicalAddress": "",
                },
            }
        }
        driver = _make_eos_driver([mock_data])
        intfs = driver.get_interfaces()
        assert intfs["Ethernet3"]["is_up"] is False
        assert intfs["Ethernet3"]["is_enabled"] is False


# ============================================================================
# Missing field handling — BGP not configured
# ============================================================================


class TestBgpNotConfigured:
    """When BGP is not configured, drivers return empty peers, not an error."""

    def test_eos_no_bgp(self):
        driver = _make_eos_driver([EOS_BGP_EMPTY])
        bgp = driver.get_bgp_summary()
        assert isinstance(bgp, dict)
        assert bgp["peers"] == {}

    def test_iosxe_no_bgp(self):
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_BGP_EMPTY
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp
        bgp = driver.get_bgp_summary()
        assert isinstance(bgp, dict)
        assert bgp["peers"] == {}

    def test_nxos_no_bgp(self):
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_BGP_EMPTY_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp
        bgp = driver.get_bgp_summary()
        assert isinstance(bgp, dict)
        assert bgp["peers"] == {}

    def test_junos_no_bgp(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_BGP_EMPTY_XML
        driver._conn.rpc.return_value = mock_response
        bgp = driver.get_bgp_summary()
        assert isinstance(bgp, dict)
        assert bgp["peers"] == {}


# ============================================================================
# BGP peer schema validation
# ============================================================================


class TestBgpPeerSchema:
    """All BGP peers have the same schema keys."""

    def test_eos_peer_keys(self):
        driver = _make_eos_driver([EOS_BGP_SUMMARY])
        bgp = driver.get_bgp_summary()
        for peer_ip, peer_data in bgp["peers"].items():
            assert set(peer_data.keys()) == BGP_PEER_KEYS, f"EOS peer {peer_ip} has wrong keys"

    def test_nxos_peer_keys(self):
        driver = _make_nxos_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _nxapi_wrap(NXOS_BGP_SUMMARY_BODY)
        mock_resp.raise_for_status = MagicMock()
        driver._nxapi.post.return_value = mock_resp
        bgp = driver.get_bgp_summary()
        for peer_ip, peer_data in bgp["peers"].items():
            assert set(peer_data.keys()) == BGP_PEER_KEYS, f"NX-OS peer {peer_ip} has wrong keys"

    def test_junos_peer_keys(self):
        driver = _make_junos_driver()
        mock_response = MagicMock()
        mock_response.result = JUNOS_BGP_SUMMARY_XML
        driver._conn.rpc.return_value = mock_response
        bgp = driver.get_bgp_summary()
        for peer_ip, peer_data in bgp["peers"].items():
            assert set(peer_data.keys()) == BGP_PEER_KEYS, f"JunOS peer {peer_ip} has wrong keys"

    def test_iosxe_peer_keys(self):
        driver = _make_iosxe_driver()
        mock_resp = MagicMock()
        mock_resp.json.return_value = IOSXE_BGP_OPER
        mock_resp.raise_for_status = MagicMock()
        driver._restconf.get.return_value = mock_resp
        bgp = driver.get_bgp_summary()
        for peer_ip, peer_data in bgp["peers"].items():
            assert set(peer_data.keys()) == BGP_PEER_KEYS, f"IOS-XE peer {peer_ip} has wrong keys"


# ============================================================================
# NX-OS NX-API helper tests — _unwrap_table and _parse_nxapi_response
# ============================================================================


class TestNxapiHelpers:
    """Test NX-API response parsing helpers."""

    def test_unwrap_table_single_row(self):
        """Single-element TABLE_ wraps as dict, not list."""
        from network_mcp.drivers.cisco_nxos import _unwrap_table

        data = {"TABLE_intf": {"ROW_intf": {"interface": "Ethernet1/1"}}}
        rows = _unwrap_table(data, "TABLE_intf", "ROW_intf")
        assert len(rows) == 1
        assert rows[0]["interface"] == "Ethernet1/1"

    def test_unwrap_table_multi_row(self):
        from network_mcp.drivers.cisco_nxos import _unwrap_table

        data = {
            "TABLE_intf": {
                "ROW_intf": [
                    {"interface": "Ethernet1/1"},
                    {"interface": "Ethernet1/2"},
                ]
            }
        }
        rows = _unwrap_table(data, "TABLE_intf", "ROW_intf")
        assert len(rows) == 2

    def test_unwrap_table_missing(self):
        from network_mcp.drivers.cisco_nxos import _unwrap_table

        rows = _unwrap_table({}, "TABLE_intf", "ROW_intf")
        assert rows == []

    def test_parse_nxapi_response_single(self):
        resp = _nxapi_wrap({"host_name": "switch"})
        parsed = NxosDriver()._parse_nxapi_response(resp)
        assert len(parsed) == 1
        assert parsed[0]["host_name"] == "switch"

    def test_parse_nxapi_response_error(self):
        resp = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "500",
                        "msg": "Command failed",
                        "body": {},
                    }
                }
            }
        }
        parsed = NxosDriver()._parse_nxapi_response(resp)
        assert "error" in parsed[0]


# ============================================================================
# JunOS helper function tests
# ============================================================================


class TestJunosHelpers:
    """Test JunOS-specific helper functions."""

    def test_parse_junos_uptime_plain_seconds(self):
        from network_mcp.drivers.juniper_junos import _parse_junos_uptime

        assert _parse_junos_uptime("864000") == 864000

    def test_parse_junos_uptime_xml_style(self):
        from network_mcp.drivers.juniper_junos import _parse_junos_uptime

        assert _parse_junos_uptime("{ junos:seconds 864000 }") == 864000

    def test_parse_junos_uptime_human_readable(self):
        from network_mcp.drivers.juniper_junos import _parse_junos_uptime

        result = _parse_junos_uptime("10 days, 3:45:12")
        expected = 10 * 86400 + 3 * 3600 + 45 * 60 + 12
        assert result == expected

    def test_parse_junos_uptime_empty(self):
        from network_mcp.drivers.juniper_junos import _parse_junos_uptime

        assert _parse_junos_uptime("") == 0

    def test_parse_junos_speed_mbps(self):
        from network_mcp.drivers.juniper_junos import _parse_junos_speed

        assert _parse_junos_speed("1000mbps") == 1000

    def test_parse_junos_speed_gbps(self):
        from network_mcp.drivers.juniper_junos import _parse_junos_speed

        assert _parse_junos_speed("10Gbps") == 10000

    def test_parse_junos_speed_auto(self):
        from network_mcp.drivers.juniper_junos import _parse_junos_speed

        assert _parse_junos_speed("Auto") == 0

    def test_parse_junos_speed_empty(self):
        from network_mcp.drivers.juniper_junos import _parse_junos_speed

        assert _parse_junos_speed("") == 0


# ============================================================================
# NX-OS speed and uptime parser tests
# ============================================================================


class TestNxosHelpers:
    """Test NX-OS-specific helper functions."""

    def test_parse_speed_gbps_string(self):
        from network_mcp.drivers.cisco_nxos import _parse_speed

        assert _parse_speed("100 Gb/s") == 100000

    def test_parse_speed_mbps_string(self):
        from network_mcp.drivers.cisco_nxos import _parse_speed

        assert _parse_speed("1000 Mb/s") == 1000

    def test_parse_speed_auto(self):
        from network_mcp.drivers.cisco_nxos import _parse_speed

        assert _parse_speed("auto") == 0

    def test_parse_speed_int_kbit(self):
        from network_mcp.drivers.cisco_nxos import _parse_speed

        assert _parse_speed(100000) == 100

    def test_parse_nxos_uptime(self):
        from network_mcp.drivers.cisco_nxos import _parse_nxos_uptime

        result = _parse_nxos_uptime("Kernel uptime is 125 day(s), 3 hour(s), 45 minute(s), 12 second(s)")
        expected = 125 * 86400 + 3 * 3600 + 45 * 60 + 12
        assert result == expected

    def test_parse_bgp_uptime_iso8601(self):
        from network_mcp.drivers.cisco_nxos import _parse_bgp_uptime

        assert _parse_bgp_uptime("P5DT2H30M") == (5 * 86400) + (2 * 3600) + (30 * 60)

    def test_parse_bgp_uptime_never(self):
        from network_mcp.drivers.cisco_nxos import _parse_bgp_uptime

        assert _parse_bgp_uptime("never") == 0

    def test_parse_bgp_uptime_hhmmss(self):
        from network_mcp.drivers.cisco_nxos import _parse_bgp_uptime

        assert _parse_bgp_uptime("01:23:45") == 1 * 3600 + 23 * 60 + 45

    def test_parse_arp_age_hhmmss(self):
        from network_mcp.drivers.cisco_nxos import _parse_arp_age

        assert _parse_arp_age("00:05:30") == 330.0

    def test_parse_arp_age_permanent(self):
        from network_mcp.drivers.cisco_nxos import _parse_arp_age

        assert _parse_arp_age("-") == -1.0


# ============================================================================
# IOS-XE uptime parser test
# ============================================================================


class TestIosXeHelpers:
    """Test IOS-XE-specific helper functions."""

    def test_parse_ios_uptime(self):
        from network_mcp.drivers.cisco_iosxe import _parse_ios_uptime

        result = _parse_ios_uptime("router1 uptime is 2 years, 3 weeks, 4 days, 5 hours, 6 minutes")
        expected = 2 * 365 * 86400 + 3 * 7 * 86400 + 4 * 86400 + 5 * 3600 + 6 * 60
        assert result == expected

    def test_parse_ios_uptime_partial(self):
        from network_mcp.drivers.cisco_iosxe import _parse_ios_uptime

        result = _parse_ios_uptime("router1 uptime is 4 days, 5 hours")
        expected = 4 * 86400 + 5 * 3600
        assert result == expected


# ============================================================================
# IOS-XE SSH fallback parsers
# ============================================================================


class TestIosXeSshParsers:
    """Test IOS-XE SSH text parsers (fallback path)."""

    def test_normalize_facts_ssh(self):
        output = (
            "Cisco IOS XE Software, Version 17.09.04\n"
            "cisco C8300-1N1S-4T2X (1RU) processor\n"
            "rtr-01 uptime is 10 days, 3 hours, 45 minutes\n"
            "Processor board ID FDO12345678\n"
        )
        facts = IosXeDriver._normalize_facts_ssh(output)
        assert facts["hostname"] == "rtr-01"
        assert facts["model"] == "C8300-1N1S-4T2X"
        assert facts["version"] == "17.09.04"
        assert facts["serial"] == "FDO12345678"
        assert facts["uptime_seconds"] == 10 * 86400 + 3 * 3600 + 45 * 60

    def test_normalize_bgp_ssh(self):
        output = (
            "BGP router identifier 10.0.0.1, local AS number 65100\n"
            "Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd\n"
            "10.0.0.2        4        65200    1234    1235        5    0    0 01:23:45          150\n"
            "10.0.0.3        4        65300       0       0        0    0    0 00:00:00       Active\n"
        )
        bgp = IosXeDriver._normalize_bgp_ssh(output)
        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65100
        assert bgp["peers"]["10.0.0.2"]["state"] == "Established"
        assert bgp["peers"]["10.0.0.2"]["prefixes_received"] == 150
        assert bgp["peers"]["10.0.0.3"]["state"] == "Active"
        assert bgp["peers"]["10.0.0.3"]["prefixes_received"] == 0


# ============================================================================
# JunOS SSH fallback parsers
# ============================================================================


class TestJunosSshParsers:
    """Test JunOS SSH text parsers (fallback path)."""

    def test_parse_facts_ssh(self):
        from network_mcp.drivers.juniper_junos import _parse_facts_ssh

        output = "Hostname: junos-spine-01\nModel: qfx5120-48t\nJunos: 23.4R1.10\nSerial Number: WR0012345678\n"
        facts = _parse_facts_ssh(output)
        assert facts["hostname"] == "junos-spine-01"
        assert facts["model"] == "qfx5120-48t"
        assert facts["version"] == "23.4R1.10"
        assert facts["serial"] == "WR0012345678"
        assert facts["vendor"] == "juniper"
        assert facts["platform"] == "junos"

    def test_parse_interfaces_ssh(self):
        from network_mcp.drivers.juniper_junos import _parse_interfaces_ssh

        output = (
            "Interface               Admin Link Proto    Local                 Remote\n"
            "ge-0/0/0                up    up\n"
            "ge-0/0/0.0              up    up   inet     10.0.0.1/30\n"
            "ge-0/0/1                up    down\n"
        )
        intfs = _parse_interfaces_ssh(output)
        assert "ge-0/0/0" in intfs
        assert intfs["ge-0/0/0"]["is_up"] is True
        assert intfs["ge-0/0/0"]["is_enabled"] is True
        # Logical interfaces (with .N) are skipped
        assert "ge-0/0/0.0" not in intfs
        assert "ge-0/0/1" in intfs
        assert intfs["ge-0/0/1"]["is_up"] is False

    def test_parse_bgp_summary_ssh(self):
        from network_mcp.drivers.juniper_junos import _parse_bgp_summary_ssh

        output = (
            "Threading mode: BGP I/O\n"
            "Router ID: 10.0.0.1\n"
            "Local AS: 65000\n"
            "Groups: 1 Peers: 1\n"
            "Peer                     AS      InPkt     OutPkt    OutQ   Flaps Last Up/Dwn State|#Active/Received/...\n"
            "10.0.0.2              65001       1234       1235       0       0     1d 2:03:04 Establ\n"
            "  inet.0: 150/200/150/0\n"
        )
        bgp = _parse_bgp_summary_ssh(output)
        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65000
        assert "10.0.0.2" in bgp["peers"]
        assert bgp["peers"]["10.0.0.2"]["state"] == "Established"
        assert bgp["peers"]["10.0.0.2"]["prefixes_received"] == 200

    def test_parse_arp_ssh(self):
        from network_mcp.drivers.juniper_junos import _parse_arp_ssh

        output = (
            "MAC Address       Address         Name                      Interface               Flags\n"
            "00:05:86:cc:dd:01 10.0.0.2        10.0.0.2                  ge-0/0/0                none\n"
        )
        arp = _parse_arp_ssh(output)
        assert len(arp) == 1
        assert arp[0]["ip"] == "10.0.0.2"
        assert arp[0]["mac"] == "00:05:86:cc:dd:01"
        assert arp[0]["interface"] == "ge-0/0/0"

    def test_parse_lldp_ssh(self):
        from network_mcp.drivers.juniper_junos import _parse_lldp_ssh

        output = (
            "Local Interface    Parent Interface    Chassis Id          Port info          System Name\n"
            "ge-0/0/0           -                   00:05:86:71:62:02   ge-0/0/1           spine-02.lab\n"
        )
        lldp = _parse_lldp_ssh(output)
        assert "ge-0/0/0" in lldp
        assert lldp["ge-0/0/0"][0]["hostname"] == "spine-02.lab"
        assert lldp["ge-0/0/0"][0]["port"] == "ge-0/0/1"
