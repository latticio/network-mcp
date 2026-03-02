"""Tests for SSH fallback transport code paths in IOS-XE, NX-OS, and JunOS drivers.

Validates that when the primary transport (RESTCONF / NX-API / NETCONF) fails,
each driver correctly falls back to SSH via scrapli, and that both-transports-fail
scenarios produce clear errors. Also tests the CLI text parsing logic that the SSH
fallback path relies on (``_normalize_*_ssh`` / ``_parse_*_ssh`` helpers).
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.cisco_iosxe import IosXeDriver, IosXeScrapliTransport
from network_mcp.drivers.cisco_nxos import NxosDriver, NxosScrapliTransport
from network_mcp.drivers.juniper_junos import JunosDriver, JunosScrapliTransport

# ============================================================================
# Sample CLI output for SSH fallback text parsers
# ============================================================================

IOSXE_SHOW_VERSION = """\
Cisco IOS XE Software, Version 17.09.04a
Cisco IOS Software [Cupertino], Catalyst L3 Switch Software (CAT9K_IOSXE), Version 17.09.04a, RELEASE SOFTWARE (fc3)
Technical Support: http://www.cisco.com/techsupport
Copyright (c) 1986-2024 by Cisco Systems, Inc.

cisco C9300-48T (ARM64) processor with 1835627K/3071K bytes of memory.
Processor board ID FOC2145Z0AB
Running default software
15 Virtual Ethernet interfaces
52 Gigabit Ethernet interfaces
2 Ten Gigabit Ethernet interfaces

32768K bytes of flash memory.

cat9300-spine uptime is 1 year, 2 weeks, 3 days, 4 hours, 5 minutes
"""

IOSXE_SHOW_INTERFACES = """\
GigabitEthernet1/0/1 is up, line protocol is up
  Description: to-leaf-01
  MTU 9198 bytes, BW 1000000 Kbit/sec
  Hardware is Gigabit Ethernet, address is 00aa.bbcc.0001 (bia 00aa.bbcc.0001)
GigabitEthernet1/0/2 is administratively down, line protocol is down
  MTU 1500 bytes, BW 100000 Kbit/sec
  Hardware is Gigabit Ethernet, address is 00aa.bbcc.0002 (bia 00aa.bbcc.0002)
"""

IOSXE_BGP_SUMMARY = """\
BGP router identifier 10.0.0.1, local AS number 65000
BGP table version is 150, main routing table version 150
100 network entries using 25600 bytes of memory
120 path entries using 17280 bytes of memory

Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd
10.0.0.2        4        65001   12345   12346      150    0    0 01:02:03       50
10.0.0.3        4        65002       0       0        0    0    0 never    Active
"""

IOSXE_ARP_TABLE = """\
Protocol  Address          Age (min)  Hardware Addr   Type   Interface
Internet  10.0.0.2                5   0050.56a0.0001  ARPA   GigabitEthernet1/0/1
Internet  10.0.0.3               10   0050.56a0.0002  ARPA   GigabitEthernet1/0/2
"""

IOSXE_LLDP_NEIGHBORS = """\
Local Intf: GigabitEthernet1/0/1
System Name: leaf-01.lab
Port id: Gi0/0/0
System Description: Cisco IOS-XE 17.09

Local Intf: GigabitEthernet1/0/2
System Name: leaf-02.lab
Port id: Gi0/0/1
System Description: Cisco IOS-XE 17.09
"""

NXOS_SHOW_VERSION = """\
Cisco Nexus Operating System (NX-OS) Software
TAC support: http://www.cisco.com/tac
NXOS: version 10.3(2)
cisco Nexus9000 C9336C-FX2 Chassis
Device name: nxos-spine-01
Processor Board ID FDO12345678

Kernel uptime is 125 day(s), 3 hour(s), 45 minute(s), 12 second(s)
"""

NXOS_SHOW_INTERFACES = """\
Ethernet1/1 is up
  admin state is up
  Description: to-leaf-01
  MTU 9216 bytes, BW 100000000 Kbit/sec
  Hardware: 100000 Ethernet, address: 00aa.bbcc.0011 (bia 00aa.bbcc.0011)
Ethernet1/2 is down
  admin state is down
  MTU 1500 bytes, BW 100000 Kbit/sec
  Hardware: 1000 Ethernet, address: 00aa.bbcc.0012 (bia 00aa.bbcc.0012)
"""

NXOS_BGP_SUMMARY = """\
BGP summary information for VRF default, address family IPv4 Unicast
BGP router identifier 10.0.0.10, local AS number 65100
BGP table version is 200, IPv4 Unicast config peers 2, capable peers 1

Neighbor        V    AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd
10.0.0.11       4 65101   23456   23457      200    0    0 2d03h          75
10.0.0.12       4 65102       0       0        0    0    0 never    Idle
"""

NXOS_ARP_TABLE = """\
Address         Age       MAC Address     Type    Interface
10.0.0.11       00:15:32  0050.56a0.0010  dynamic Ethernet1/1
10.0.0.12       00:30:00  0050.56a0.0020  dynamic Ethernet1/2
"""

NXOS_LLDP_NEIGHBORS = """\
Local Port id: Ethernet1/1
System Name: leaf-01.dc1
Port id: Ethernet1/49
System Description: Cisco Nexus 9000

Local Port id: Ethernet1/2
System Name: leaf-02.dc1
Port id: Ethernet1/49
System Description: Cisco Nexus 9000
"""

JUNOS_SHOW_VERSION = """\
Hostname: junos-spine-01
Model: qfx5120-48t
Junos: 23.4R1.10
"""

JUNOS_SHOW_INTERFACES_TERSE = """\
Interface               Admin Link Proto    Local                 Remote
ge-0/0/0                up    up
ge-0/0/0.0              up    up   inet     10.0.0.1/30
ge-0/0/1                up    down
xe-0/0/0                up    up
"""

JUNOS_BGP_SUMMARY = """\
Threading mode: BGP I/O
Default eBGP mode: advertise - Loss of Stability|Alarm|Crit Peers: 2/2 Down peers: 1
  Restart Complete
    inet.0: 150/200/100/0

Router ID: 10.0.0.1
Local AS: 65200

Peer                     AS      InPkt     OutPkt    OutQ   Flaps Last Up/Dwn State|#Active/Received/Accepted/Damped...
10.0.0.2              65201       5678       5679       0       0     1d 2:03:04 Establ
  inet.0: 150/200/150/0
10.0.0.3              65202          0          0       0       0        3:45:12 Active
"""

JUNOS_ARP_TABLE = """\
MAC Address       Address         Name                      Interface               Flags
00:05:86:cc:dd:01 10.0.0.2        10.0.0.2                  ge-0/0/0                none
00:05:86:cc:dd:02 10.0.0.3        10.0.0.3                  ge-0/0/1                none
"""

JUNOS_LLDP_NEIGHBORS = """\
Local Interface    Parent Interface    Chassis Id          Port info          System Name
ge-0/0/0           -                   00:05:86:71:62:02   ge-0/0/1           spine-02.lab
xe-0/0/0           -                   00:05:86:71:62:03   xe-0/0/1           leaf-01.lab
"""


# ============================================================================
# Helper: build a pre-connected driver with SSH transport mocked
# ============================================================================


def _make_mock_ssh_transport(send_command_side_effect=None, send_config_return=""):
    """Build a MagicMock that quacks like any *ScrapliTransport."""
    transport = MagicMock()
    transport.is_alive = True
    transport._connected = True
    if send_command_side_effect is not None:
        transport.send_command.side_effect = send_command_side_effect
    else:
        transport.send_command.return_value = ""
    transport.send_config.return_value = send_config_return
    transport.open.return_value = None
    transport.close.return_value = None
    return transport


def _make_iosxe_ssh_driver(ssh_transport):
    """Return an IosXeDriver wired directly to SSH (no RESTCONF)."""
    driver = IosXeDriver()
    driver._restconf = None
    driver._ssh_transport = ssh_transport
    driver._ssh_host = "10.0.0.1"
    driver._ssh_username = "admin"
    driver._ssh_password = "password"
    driver._transport = "ssh"
    driver._connected = True
    driver._last_command_time = time.monotonic()
    return driver


def _make_nxos_ssh_driver(ssh_transport):
    """Return an NxosDriver wired directly to SSH (no NX-API)."""
    driver = NxosDriver()
    driver._nxapi = None
    driver._ssh_transport = ssh_transport
    driver._ssh_host = "10.0.0.10"
    driver._ssh_username = "admin"
    driver._ssh_password = "password"
    driver._transport = "ssh"
    driver._connected = True
    driver._last_command_time = time.monotonic()
    return driver


def _make_junos_ssh_driver(ssh_transport):
    """Return a JunosDriver wired directly to SSH (no NETCONF)."""
    driver = JunosDriver()
    driver._conn = None
    driver._ssh_transport = ssh_transport
    driver._host = "10.0.0.20"
    driver._username = "admin"
    driver._password = "password"
    driver._transport = "ssh"
    driver._connected = True
    driver._last_command_time = time.monotonic()
    return driver


# ============================================================================
# 1. Primary-transport-fails → SSH-fallback-succeeds
# ============================================================================


class TestIosXeFallbackSucceeds:
    """RESTCONF fails, SSH fallback returns valid data."""

    def test_get_facts_falls_back_to_ssh(self, caplog):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: IOSXE_SHOW_VERSION)
        driver = _make_iosxe_ssh_driver(transport)
        facts = driver.get_facts()

        assert facts["hostname"] == "cat9300-spine"
        assert facts["version"] == "17.09.04a"
        assert facts["serial"] == "FOC2145Z0AB"
        assert facts["model"] == "C9300-48T"
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "iosxe"
        assert facts["uptime_seconds"] > 0
        transport.send_command.assert_called()

    def test_get_interfaces_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: IOSXE_SHOW_INTERFACES)
        driver = _make_iosxe_ssh_driver(transport)
        interfaces = driver.get_interfaces()

        assert "GigabitEthernet1/0/1" in interfaces
        assert interfaces["GigabitEthernet1/0/1"]["is_up"] is True
        assert interfaces["GigabitEthernet1/0/1"]["is_enabled"] is True
        assert interfaces["GigabitEthernet1/0/1"]["description"] == "to-leaf-01"
        assert interfaces["GigabitEthernet1/0/1"]["mtu"] == 9198
        assert interfaces["GigabitEthernet1/0/2"]["is_up"] is False
        assert interfaces["GigabitEthernet1/0/2"]["is_enabled"] is False

    def test_get_bgp_summary_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: IOSXE_BGP_SUMMARY)
        driver = _make_iosxe_ssh_driver(transport)
        bgp = driver.get_bgp_summary()

        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65000
        assert "10.0.0.2" in bgp["peers"]
        assert bgp["peers"]["10.0.0.2"]["state"] == "Established"
        assert bgp["peers"]["10.0.0.2"]["prefixes_received"] == 50
        assert bgp["peers"]["10.0.0.3"]["state"] == "Active"

    def test_get_arp_table_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: IOSXE_ARP_TABLE)
        driver = _make_iosxe_ssh_driver(transport)
        arp = driver.get_arp_table()

        assert len(arp) == 2
        assert arp[0]["ip"] == "10.0.0.2"
        assert arp[0]["interface"] == "GigabitEthernet1/0/1"
        assert arp[0]["age"] == 300.0  # 5 minutes * 60

    def test_get_lldp_neighbors_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: IOSXE_LLDP_NEIGHBORS)
        driver = _make_iosxe_ssh_driver(transport)
        lldp = driver.get_lldp_neighbors()

        assert "GigabitEthernet1/0/1" in lldp
        assert lldp["GigabitEthernet1/0/1"][0]["hostname"] == "leaf-01.lab"
        assert lldp["GigabitEthernet1/0/1"][0]["port"] == "Gi0/0/0"
        assert "GigabitEthernet1/0/2" in lldp

    def test_run_show_uses_ssh_when_no_restconf(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: "output-data")
        driver = _make_iosxe_ssh_driver(transport)
        result = driver.run_show(["show version"])

        assert result == [{"output": "output-data"}]
        transport.send_command.assert_called_with("show version")

    def test_run_config_uses_ssh_when_no_restconf(self):
        transport = _make_mock_ssh_transport(send_config_return="ok")
        driver = _make_iosxe_ssh_driver(transport)
        result = driver.run_config(["interface Gi1/0/1", "shutdown"])

        assert result == ["ok"]
        transport.send_config.assert_called_once_with(["interface Gi1/0/1", "shutdown"])


class TestNxosFallbackSucceeds:
    """NX-API fails, SSH fallback returns valid data."""

    def test_get_facts_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: NXOS_SHOW_VERSION)
        driver = _make_nxos_ssh_driver(transport)
        facts = driver.get_facts()

        assert facts["hostname"] == "nxos-spine-01"
        assert facts["version"] == "10.3(2)"
        assert facts["serial"] == "FDO12345678"
        assert "Nexus" in facts["model"]
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "nxos"
        # 125d + 3h + 45m + 12s
        expected_uptime = 125 * 86400 + 3 * 3600 + 45 * 60 + 12
        assert facts["uptime_seconds"] == expected_uptime

    def test_get_interfaces_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: NXOS_SHOW_INTERFACES)
        driver = _make_nxos_ssh_driver(transport)
        interfaces = driver.get_interfaces()

        assert "Ethernet1/1" in interfaces
        assert interfaces["Ethernet1/1"]["is_up"] is True
        assert interfaces["Ethernet1/1"]["is_enabled"] is True
        assert interfaces["Ethernet1/1"]["description"] == "to-leaf-01"
        assert interfaces["Ethernet1/1"]["mtu"] == 9216
        assert interfaces["Ethernet1/2"]["is_up"] is False
        assert interfaces["Ethernet1/2"]["is_enabled"] is False

    def test_get_bgp_summary_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: NXOS_BGP_SUMMARY)
        driver = _make_nxos_ssh_driver(transport)
        bgp = driver.get_bgp_summary()

        assert bgp["router_id"] == "10.0.0.10"
        assert bgp["as_number"] == 65100
        assert "10.0.0.11" in bgp["peers"]
        assert bgp["peers"]["10.0.0.11"]["state"] == "Established"
        assert bgp["peers"]["10.0.0.11"]["prefixes_received"] == 75
        assert bgp["peers"]["10.0.0.12"]["state"] == "Idle"

    def test_get_arp_table_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: NXOS_ARP_TABLE)
        driver = _make_nxos_ssh_driver(transport)
        arp = driver.get_arp_table()

        assert len(arp) == 2
        assert arp[0]["ip"] == "10.0.0.11"
        assert arp[0]["interface"] == "Ethernet1/1"

    def test_get_lldp_neighbors_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: NXOS_LLDP_NEIGHBORS)
        driver = _make_nxos_ssh_driver(transport)
        lldp = driver.get_lldp_neighbors()

        assert "Ethernet1/1" in lldp
        assert lldp["Ethernet1/1"][0]["hostname"] == "leaf-01.dc1"
        assert lldp["Ethernet1/1"][0]["port"] == "Ethernet1/49"
        assert "Ethernet1/2" in lldp

    def test_run_show_uses_ssh_when_no_nxapi(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: "nxos-output")
        driver = _make_nxos_ssh_driver(transport)
        result = driver.run_show(["show version"])

        assert result == [{"output": "nxos-output"}]

    def test_run_config_uses_ssh_when_no_nxapi(self):
        transport = _make_mock_ssh_transport(send_config_return="ok")
        driver = _make_nxos_ssh_driver(transport)
        result = driver.run_config(["vlan 100", "name SERVERS"])

        assert result == ["ok"]
        transport.send_config.assert_called_once_with(["vlan 100", "name SERVERS"])


class TestJunosFallbackSucceeds:
    """NETCONF fails, SSH fallback returns valid data."""

    def test_get_facts_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: JUNOS_SHOW_VERSION)
        driver = _make_junos_ssh_driver(transport)
        facts = driver.get_facts()

        assert facts["hostname"] == "junos-spine-01"
        assert facts["model"] == "qfx5120-48t"
        assert facts["version"] == "23.4R1.10"
        assert facts["vendor"] == "juniper"
        assert facts["platform"] == "junos"

    def test_get_interfaces_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: JUNOS_SHOW_INTERFACES_TERSE)
        driver = _make_junos_ssh_driver(transport)
        interfaces = driver.get_interfaces()

        assert "ge-0/0/0" in interfaces
        assert interfaces["ge-0/0/0"]["is_up"] is True
        assert interfaces["ge-0/0/0"]["is_enabled"] is True
        assert "ge-0/0/1" in interfaces
        assert interfaces["ge-0/0/1"]["is_up"] is False
        # Logical interfaces (with .N suffix) should be skipped
        assert "ge-0/0/0.0" not in interfaces

    def test_get_bgp_summary_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: JUNOS_BGP_SUMMARY)
        driver = _make_junos_ssh_driver(transport)
        bgp = driver.get_bgp_summary()

        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65200
        assert "10.0.0.2" in bgp["peers"]
        assert bgp["peers"]["10.0.0.2"]["state"] == "Established"
        assert bgp["peers"]["10.0.0.2"]["prefixes_received"] == 200
        assert bgp["peers"]["10.0.0.3"]["state"] == "Active"

    def test_get_arp_table_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: JUNOS_ARP_TABLE)
        driver = _make_junos_ssh_driver(transport)
        arp = driver.get_arp_table()

        assert len(arp) == 2
        assert arp[0]["ip"] == "10.0.0.2"
        assert arp[0]["interface"] == "ge-0/0/0"

    def test_get_lldp_neighbors_falls_back_to_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: JUNOS_LLDP_NEIGHBORS)
        driver = _make_junos_ssh_driver(transport)
        lldp = driver.get_lldp_neighbors()

        assert "ge-0/0/0" in lldp
        assert lldp["ge-0/0/0"][0]["hostname"] == "spine-02.lab"
        assert lldp["ge-0/0/0"][0]["port"] == "ge-0/0/1"
        assert "xe-0/0/0" in lldp

    def test_run_show_uses_ssh_when_no_netconf(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: "junos-output")
        driver = _make_junos_ssh_driver(transport)
        result = driver.run_show(["show version"])

        assert result == [{"output": "junos-output"}]

    def test_run_config_uses_ssh_when_no_netconf(self):
        transport = _make_mock_ssh_transport(send_config_return="ok")
        driver = _make_junos_ssh_driver(transport)
        result = driver.run_config(["set interfaces ge-0/0/0 description test"])

        assert result == ["ok"]
        transport.send_config.assert_called_once_with(["set interfaces ge-0/0/0 description test"])


# ============================================================================
# 2. SSH-fallback-also-fails error handling
# ============================================================================


class TestIosXeBothTransportsFail:
    """RESTCONF failed (driver is in SSH mode) but SSH also fails."""

    def test_ssh_exec_raises_runtime_error(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=RuntimeError("SSH connection refused"))
        driver = _make_iosxe_ssh_driver(transport)

        result = driver.run_show(["show version"])
        assert "error" in result[0]
        assert "SSH connection refused" in result[0]["error"]

    def test_get_facts_returns_empty_on_ssh_failure(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=RuntimeError("SSH timeout"))
        driver = _make_iosxe_ssh_driver(transport)

        facts = driver.get_facts()
        # SSH fallback catches the error in _ssh_show; result dict has "error" key.
        # _normalize_facts_ssh gets an empty string, producing empty fields.
        assert facts["hostname"] == ""
        assert facts["version"] == ""

    def test_no_ssh_transport_raises_connection_error(self):
        driver = IosXeDriver()
        driver._restconf = None
        driver._ssh_transport = None
        driver._ssh_host = "10.0.0.1"
        driver._ssh_username = "admin"
        driver._transport = "ssh"
        driver._connected = True
        driver._last_command_time = time.monotonic()

        with pytest.raises(ConnectionError, match="SSH transport not available"):
            driver._ssh_exec("show version")

    def test_ssh_config_raises_when_no_transport(self):
        driver = IosXeDriver()
        driver._restconf = None
        driver._ssh_transport = None
        driver._connected = True
        driver._last_command_time = time.monotonic()

        with pytest.raises(ConnectionError, match="SSH transport not available"):
            driver._ssh_config(["interface Gi1/0/1", "shutdown"])

    def test_ssh_config_returns_error_string_on_exception(self):
        transport = _make_mock_ssh_transport()
        transport.send_config.side_effect = RuntimeError("config push failed")
        driver = _make_iosxe_ssh_driver(transport)

        result = driver.run_config(["interface Gi1/0/1"])
        assert len(result) == 1
        assert "Error:" in result[0]
        assert "config push failed" in result[0]


class TestNxosBothTransportsFail:
    """NX-API failed (driver is in SSH mode) but SSH also fails."""

    def test_ssh_exec_raises_runtime_error(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=RuntimeError("SSH auth failed"))
        driver = _make_nxos_ssh_driver(transport)

        result = driver.run_show(["show version"])
        assert "error" in result[0]
        assert "SSH auth failed" in result[0]["error"]

    def test_get_facts_returns_empty_on_ssh_failure(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=RuntimeError("SSH timeout"))
        driver = _make_nxos_ssh_driver(transport)

        facts = driver.get_facts()
        assert facts["hostname"] == ""
        assert facts["version"] == ""

    def test_no_ssh_transport_raises_connection_error(self):
        driver = NxosDriver()
        driver._nxapi = None
        driver._ssh_transport = None
        driver._ssh_host = "10.0.0.10"
        driver._ssh_username = "admin"
        driver._transport = "ssh"
        driver._connected = True
        driver._last_command_time = time.monotonic()

        with pytest.raises(ConnectionError, match="SSH transport not available"):
            driver._ssh_exec("show version")

    def test_ssh_config_raises_when_no_transport(self):
        driver = NxosDriver()
        driver._nxapi = None
        driver._ssh_transport = None
        driver._connected = True
        driver._last_command_time = time.monotonic()

        with pytest.raises(ConnectionError, match="SSH transport not available"):
            driver._ssh_config(["vlan 100"])


class TestJunosBothTransportsFail:
    """NETCONF failed (driver is in SSH mode) but SSH also fails."""

    def test_ssh_exec_raises_runtime_error(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=RuntimeError("SSH connection reset"))
        driver = _make_junos_ssh_driver(transport)

        result = driver.run_show(["show version"])
        assert "error" in result[0]
        assert "SSH connection reset" in result[0]["error"]

    def test_get_facts_returns_empty_on_ssh_failure(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=RuntimeError("SSH timeout"))
        driver = _make_junos_ssh_driver(transport)

        facts = driver.get_facts()
        assert facts["hostname"] == ""
        assert facts["version"] == ""

    def test_no_ssh_transport_raises_connection_error(self):
        driver = JunosDriver()
        driver._conn = None
        driver._ssh_transport = None
        driver._host = "10.0.0.20"
        driver._username = "admin"
        driver._transport = "ssh"
        driver._connected = True
        driver._last_command_time = time.monotonic()

        with pytest.raises(ConnectionError, match="SSH transport not available"):
            driver._ssh_exec("show version")

    def test_ssh_config_raises_when_no_transport(self):
        driver = JunosDriver()
        driver._conn = None
        driver._ssh_transport = None
        driver._connected = True
        driver._last_command_time = time.monotonic()

        with pytest.raises(ConnectionError, match="SSH transport not available"):
            driver._ssh_config(["set system host-name test"])


# ============================================================================
# 3. run_show text parsing for each platform's CLI output
# ============================================================================


class TestIosXeTextParsing:
    """Test IOS-XE CLI text output parsing (the _normalize_*_ssh static methods)."""

    def test_parse_facts(self):
        facts = IosXeDriver._normalize_facts_ssh(IOSXE_SHOW_VERSION)
        assert facts["hostname"] == "cat9300-spine"
        assert facts["version"] == "17.09.04a"
        assert facts["serial"] == "FOC2145Z0AB"
        assert facts["model"] == "C9300-48T"
        # 1y + 2w + 3d + 4h + 5m
        expected = 365 * 86400 + 2 * 7 * 86400 + 3 * 86400 + 4 * 3600 + 5 * 60
        assert facts["uptime_seconds"] == expected

    def test_parse_interfaces(self):
        intfs = IosXeDriver._normalize_interfaces_ssh(IOSXE_SHOW_INTERFACES)
        assert len(intfs) == 2
        gi1 = intfs["GigabitEthernet1/0/1"]
        assert gi1["is_up"] is True
        assert gi1["is_enabled"] is True
        assert gi1["description"] == "to-leaf-01"
        assert gi1["mtu"] == 9198
        assert gi1["speed"] == 1000  # 1000000 Kbit / 1000

        gi2 = intfs["GigabitEthernet1/0/2"]
        assert gi2["is_up"] is False
        assert gi2["is_enabled"] is False
        assert gi2["speed"] == 100  # 100000 Kbit / 1000

    def test_parse_bgp_summary(self):
        bgp = IosXeDriver._normalize_bgp_ssh(IOSXE_BGP_SUMMARY, "default")
        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65000
        assert bgp["peers"]["10.0.0.2"]["state"] == "Established"
        assert bgp["peers"]["10.0.0.2"]["prefixes_received"] == 50
        assert bgp["peers"]["10.0.0.3"]["state"] == "Active"
        assert bgp["peers"]["10.0.0.3"]["prefixes_received"] == 0

    def test_parse_arp_table(self):
        arp = IosXeDriver._normalize_arp_ssh(IOSXE_ARP_TABLE)
        assert len(arp) == 2
        assert arp[0]["ip"] == "10.0.0.2"
        assert arp[0]["age"] == 300.0  # 5 min * 60
        assert arp[1]["ip"] == "10.0.0.3"
        assert arp[1]["age"] == 600.0  # 10 min * 60

    def test_parse_lldp_neighbors(self):
        lldp = IosXeDriver._normalize_lldp_ssh(IOSXE_LLDP_NEIGHBORS)
        assert len(lldp) == 2
        assert lldp["GigabitEthernet1/0/1"][0]["hostname"] == "leaf-01.lab"
        assert lldp["GigabitEthernet1/0/1"][0]["port"] == "Gi0/0/0"
        assert lldp["GigabitEthernet1/0/1"][0]["system_description"] == "Cisco IOS-XE 17.09"

    def test_parse_empty_output(self):
        assert IosXeDriver._normalize_facts_ssh("")["hostname"] == ""
        assert IosXeDriver._normalize_interfaces_ssh("") == {}
        assert IosXeDriver._normalize_bgp_ssh("")["peers"] == {}
        assert IosXeDriver._normalize_arp_ssh("") == []
        assert IosXeDriver._normalize_lldp_ssh("") == {}


class TestNxosTextParsing:
    """Test NX-OS CLI text output parsing (the _normalize_*_ssh static methods)."""

    def test_parse_facts(self):
        facts = NxosDriver._normalize_facts_ssh(NXOS_SHOW_VERSION)
        assert facts["hostname"] == "nxos-spine-01"
        assert facts["version"] == "10.3(2)"
        assert facts["serial"] == "FDO12345678"
        assert "Nexus" in facts["model"]
        expected_uptime = 125 * 86400 + 3 * 3600 + 45 * 60 + 12
        assert facts["uptime_seconds"] == expected_uptime

    def test_parse_interfaces(self):
        intfs = NxosDriver._normalize_interfaces_ssh(NXOS_SHOW_INTERFACES)
        assert len(intfs) == 2
        eth1 = intfs["Ethernet1/1"]
        assert eth1["is_up"] is True
        assert eth1["is_enabled"] is True
        assert eth1["description"] == "to-leaf-01"
        assert eth1["mtu"] == 9216
        assert eth1["speed"] == 100000  # 100000000 Kbit / 1000

        eth2 = intfs["Ethernet1/2"]
        assert eth2["is_up"] is False
        assert eth2["is_enabled"] is False

    def test_parse_bgp_summary(self):
        bgp = NxosDriver._normalize_bgp_ssh(NXOS_BGP_SUMMARY)
        assert bgp["router_id"] == "10.0.0.10"
        assert bgp["as_number"] == 65100
        assert bgp["peers"]["10.0.0.11"]["state"] == "Established"
        assert bgp["peers"]["10.0.0.11"]["prefixes_received"] == 75
        assert bgp["peers"]["10.0.0.12"]["state"] == "Idle"

    def test_parse_arp_table(self):
        arp = NxosDriver._normalize_arp_ssh(NXOS_ARP_TABLE)
        assert len(arp) == 2
        assert arp[0]["ip"] == "10.0.0.11"
        assert arp[0]["interface"] == "Ethernet1/1"

    def test_parse_lldp_neighbors(self):
        lldp = NxosDriver._normalize_lldp_ssh(NXOS_LLDP_NEIGHBORS)
        assert len(lldp) == 2
        assert lldp["Ethernet1/1"][0]["hostname"] == "leaf-01.dc1"
        assert lldp["Ethernet1/1"][0]["port"] == "Ethernet1/49"

    def test_parse_empty_output(self):
        assert NxosDriver._normalize_facts_ssh("")["hostname"] == ""
        assert NxosDriver._normalize_interfaces_ssh("") == {}
        assert NxosDriver._normalize_bgp_ssh("")["peers"] == {}
        assert NxosDriver._normalize_arp_ssh("") == []
        assert NxosDriver._normalize_lldp_ssh("") == {}


class TestJunosTextParsing:
    """Test JunOS CLI text output parsing (module-level _parse_*_ssh helpers)."""

    def test_parse_facts(self):
        from network_mcp.drivers.juniper_junos import _parse_facts_ssh

        facts = _parse_facts_ssh(JUNOS_SHOW_VERSION)
        assert facts["hostname"] == "junos-spine-01"
        assert facts["model"] == "qfx5120-48t"
        assert facts["version"] == "23.4R1.10"
        assert facts["vendor"] == "juniper"
        assert facts["platform"] == "junos"

    def test_parse_interfaces_terse(self):
        from network_mcp.drivers.juniper_junos import _parse_interfaces_ssh

        intfs = _parse_interfaces_ssh(JUNOS_SHOW_INTERFACES_TERSE)
        assert "ge-0/0/0" in intfs
        assert intfs["ge-0/0/0"]["is_up"] is True
        assert intfs["ge-0/0/0"]["is_enabled"] is True
        assert "ge-0/0/1" in intfs
        assert intfs["ge-0/0/1"]["is_up"] is False
        assert intfs["ge-0/0/1"]["is_enabled"] is True
        assert "xe-0/0/0" in intfs
        # Logical interfaces should be filtered out
        assert "ge-0/0/0.0" not in intfs

    def test_parse_bgp_summary(self):
        from network_mcp.drivers.juniper_junos import _parse_bgp_summary_ssh

        bgp = _parse_bgp_summary_ssh(JUNOS_BGP_SUMMARY)
        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65200
        assert bgp["peers"]["10.0.0.2"]["state"] == "Established"
        assert bgp["peers"]["10.0.0.2"]["prefixes_received"] == 200
        assert bgp["peers"]["10.0.0.3"]["state"] == "Active"

    def test_parse_arp_table(self):
        from network_mcp.drivers.juniper_junos import _parse_arp_ssh

        arp = _parse_arp_ssh(JUNOS_ARP_TABLE)
        assert len(arp) == 2
        assert arp[0]["ip"] == "10.0.0.2"
        assert arp[0]["interface"] == "ge-0/0/0"

    def test_parse_lldp_neighbors(self):
        from network_mcp.drivers.juniper_junos import _parse_lldp_ssh

        lldp = _parse_lldp_ssh(JUNOS_LLDP_NEIGHBORS)
        assert "ge-0/0/0" in lldp
        assert lldp["ge-0/0/0"][0]["hostname"] == "spine-02.lab"
        assert lldp["ge-0/0/0"][0]["port"] == "ge-0/0/1"
        assert "xe-0/0/0" in lldp

    def test_parse_empty_output(self):
        from network_mcp.drivers.juniper_junos import (
            _parse_arp_ssh,
            _parse_bgp_summary_ssh,
            _parse_facts_ssh,
            _parse_interfaces_ssh,
            _parse_lldp_ssh,
        )

        assert _parse_facts_ssh("")["hostname"] == ""
        assert _parse_interfaces_ssh("") == {}
        assert _parse_bgp_summary_ssh("")["peers"] == {}
        assert _parse_arp_ssh("") == []
        assert _parse_lldp_ssh("") == {}


# ============================================================================
# 4. Scrapli transport class unit tests
# ============================================================================


class TestIosXeScrapliTransport:
    """Unit tests for IosXeScrapliTransport wrapper."""

    def test_init_without_scrapli_raises(self):
        with patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", False):
            with pytest.raises(ImportError, match="scrapli not installed"):
                IosXeScrapliTransport(host="10.0.0.1", username="admin", password="pass")

    @patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver")
    @patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", True)
    def test_open_and_send_command(self, mock_scrapli_class):
        mock_driver = MagicMock()
        mock_response = MagicMock()
        mock_response.failed = False
        mock_response.result = "output-text"
        mock_driver.send_command.return_value = mock_response
        mock_scrapli_class.return_value = mock_driver

        transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="pass")
        transport.open()

        assert transport.is_alive is True
        result = transport.send_command("show version")
        assert result == "output-text"

    @patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver")
    @patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", True)
    def test_send_command_auto_opens(self, mock_scrapli_class):
        mock_driver = MagicMock()
        mock_response = MagicMock()
        mock_response.failed = False
        mock_response.result = "output"
        mock_driver.send_command.return_value = mock_response
        mock_scrapli_class.return_value = mock_driver

        transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="pass")
        # Don't call open() — send_command should auto-open
        result = transport.send_command("show version")
        mock_driver.open.assert_called_once()
        assert result == "output"

    @patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver")
    @patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", True)
    def test_send_command_raises_on_failure(self, mock_scrapli_class):
        mock_driver = MagicMock()
        mock_response = MagicMock()
        mock_response.failed = True
        mock_response.channel_input = "show version"
        mock_driver.send_command.return_value = mock_response
        mock_scrapli_class.return_value = mock_driver

        transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="pass")
        transport.open()

        with pytest.raises(RuntimeError, match="Command failed"):
            transport.send_command("show version")

    @patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver")
    @patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", True)
    def test_send_config(self, mock_scrapli_class):
        mock_driver = MagicMock()
        mock_response = MagicMock()
        mock_response.failed = False
        mock_response.result = "config applied"
        mock_driver.send_configs.return_value = mock_response
        mock_scrapli_class.return_value = mock_driver

        transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="pass")
        transport.open()
        result = transport.send_config(["interface Gi1", "shutdown"])

        assert result == "config applied"

    @patch("network_mcp.drivers.cisco_iosxe.ScrapliIOSXEDriver")
    @patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", True)
    def test_close(self, mock_scrapli_class):
        mock_driver = MagicMock()
        mock_scrapli_class.return_value = mock_driver

        transport = IosXeScrapliTransport(host="10.0.0.1", username="admin", password="pass")
        transport.open()
        assert transport.is_alive is True

        transport.close()
        assert transport.is_alive is False
        mock_driver.close.assert_called_once()


class TestNxosScrapliTransport:
    """Unit tests for NxosScrapliTransport wrapper."""

    def test_init_without_scrapli_raises(self):
        with patch("network_mcp.drivers.cisco_nxos.HAS_SCRAPLI", False):
            with pytest.raises(ImportError, match="scrapli not installed"):
                NxosScrapliTransport(host="10.0.0.1", username="admin", password="pass")

    @patch("network_mcp.drivers.cisco_nxos.ScrapliNXOSDriver")
    @patch("network_mcp.drivers.cisco_nxos.HAS_SCRAPLI", True)
    def test_open_and_send_command(self, mock_scrapli_class):
        mock_driver = MagicMock()
        mock_response = MagicMock()
        mock_response.failed = False
        mock_response.result = "nxos-output"
        mock_driver.send_command.return_value = mock_response
        mock_scrapli_class.return_value = mock_driver

        transport = NxosScrapliTransport(host="10.0.0.1", username="admin", password="pass")
        transport.open()

        assert transport.is_alive is True
        result = transport.send_command("show version")
        assert result == "nxos-output"

    @patch("network_mcp.drivers.cisco_nxos.ScrapliNXOSDriver")
    @patch("network_mcp.drivers.cisco_nxos.HAS_SCRAPLI", True)
    def test_send_config_failure(self, mock_scrapli_class):
        mock_driver = MagicMock()
        mock_response = MagicMock()
        mock_response.failed = True
        mock_response.result = "invalid command"
        mock_driver.send_configs.return_value = mock_response
        mock_scrapli_class.return_value = mock_driver

        transport = NxosScrapliTransport(host="10.0.0.1", username="admin", password="pass")
        transport.open()

        with pytest.raises(RuntimeError, match="Config commands failed"):
            transport.send_config(["bad-command"])


class TestJunosScrapliTransport:
    """Unit tests for JunosScrapliTransport wrapper."""

    def test_init_without_scrapli_raises(self):
        with patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI", False):
            with pytest.raises(ImportError, match="scrapli not installed"):
                JunosScrapliTransport(host="10.0.0.1", username="admin", password="pass")

    @patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver")
    @patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI", True)
    def test_open_and_send_command(self, mock_scrapli_class):
        mock_driver = MagicMock()
        mock_response = MagicMock()
        mock_response.failed = False
        mock_response.result = "junos-output"
        mock_driver.send_command.return_value = mock_response
        mock_scrapli_class.return_value = mock_driver

        transport = JunosScrapliTransport(host="10.0.0.1", username="admin", password="pass")
        transport.open()

        assert transport.is_alive is True
        result = transport.send_command("show version")
        assert result == "junos-output"

    @patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver")
    @patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI", True)
    def test_close_when_not_connected(self, mock_scrapli_class):
        mock_driver = MagicMock()
        mock_scrapli_class.return_value = mock_driver

        transport = JunosScrapliTransport(host="10.0.0.1", username="admin", password="pass")
        # close() without ever opening should be a no-op
        transport.close()
        mock_driver.close.assert_not_called()


# ============================================================================
# 5. connect() fallback flow (primary transport → SSH)
# ============================================================================


class TestIosXeConnectFallback:
    """Test IosXeDriver.connect() falls back when RESTCONF probe fails."""

    @patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", True)
    @patch("network_mcp.drivers.cisco_iosxe.IosXeScrapliTransport")
    @patch("network_mcp.drivers.cisco_iosxe.HAS_HTTPX", True)
    @patch("network_mcp.drivers.cisco_iosxe.httpx")
    def test_restconf_probe_fails_falls_back_to_ssh(self, mock_httpx, mock_transport_cls, caplog):
        mock_client = MagicMock()
        mock_client.get.side_effect = ConnectionError("RESTCONF unreachable")
        mock_httpx.Client.return_value = mock_client

        mock_ssh = MagicMock()
        mock_transport_cls.return_value = mock_ssh

        driver = IosXeDriver()
        with caplog.at_level(logging.WARNING):
            driver.connect("10.0.0.1", transport="restconf", username="admin", password="pass")

        assert driver._transport == "ssh"
        assert driver._connected is True
        assert any("falling back to SSH" in msg for msg in caplog.messages)

    @patch("network_mcp.drivers.cisco_iosxe.HAS_HTTPX", False)
    @patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", True)
    @patch("network_mcp.drivers.cisco_iosxe.IosXeScrapliTransport")
    def test_no_httpx_falls_back_to_ssh(self, mock_transport_cls, caplog):
        mock_ssh = MagicMock()
        mock_transport_cls.return_value = mock_ssh

        driver = IosXeDriver()
        with caplog.at_level(logging.WARNING):
            driver.connect("10.0.0.1", transport="restconf", username="admin", password="pass")

        assert driver._transport == "ssh"
        assert driver._connected is True
        assert any("httpx not installed" in msg for msg in caplog.messages)


class TestNxosConnectFallback:
    """Test NxosDriver.connect() falls back when NX-API probe fails."""

    @patch("network_mcp.drivers.cisco_nxos.HAS_SCRAPLI", True)
    @patch("network_mcp.drivers.cisco_nxos.NxosScrapliTransport")
    @patch("network_mcp.drivers.cisco_nxos.HAS_HTTPX", True)
    @patch("network_mcp.drivers.cisco_nxos.httpx")
    def test_nxapi_probe_fails_falls_back_to_ssh(self, mock_httpx, mock_transport_cls, caplog):
        mock_client = MagicMock()
        mock_client.post.side_effect = ConnectionError("NX-API unreachable")
        mock_httpx.Client.return_value = mock_client

        mock_ssh = MagicMock()
        mock_transport_cls.return_value = mock_ssh

        driver = NxosDriver()
        with caplog.at_level(logging.WARNING):
            driver.connect("10.0.0.10", transport="nxapi", username="admin", password="pass")

        assert driver._transport == "ssh"
        assert driver._connected is True
        assert any("falling back to SSH" in msg for msg in caplog.messages)

    @patch("network_mcp.drivers.cisco_nxos.HAS_HTTPX", False)
    @patch("network_mcp.drivers.cisco_nxos.HAS_SCRAPLI", True)
    @patch("network_mcp.drivers.cisco_nxos.NxosScrapliTransport")
    def test_no_httpx_falls_back_to_ssh(self, mock_transport_cls, caplog):
        mock_ssh = MagicMock()
        mock_transport_cls.return_value = mock_ssh

        driver = NxosDriver()
        with caplog.at_level(logging.WARNING):
            driver.connect("10.0.0.10", transport="nxapi", username="admin", password="pass")

        assert driver._transport == "ssh"
        assert driver._connected is True
        assert any("httpx not installed" in msg for msg in caplog.messages)


class TestJunosConnectFallback:
    """Test JunosDriver.connect() falls back when NETCONF fails."""

    @patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI", True)
    @patch("network_mcp.drivers.juniper_junos.JunosScrapliTransport")
    @patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI_NETCONF", True)
    @patch("network_mcp.drivers.juniper_junos.NetconfDriver")
    def test_netconf_open_fails_falls_back_to_ssh(self, mock_netconf_cls, mock_transport_cls, caplog):
        mock_nc = MagicMock()
        mock_nc.open.side_effect = ConnectionError("NETCONF port blocked")
        mock_netconf_cls.return_value = mock_nc

        mock_ssh = MagicMock()
        mock_transport_cls.return_value = mock_ssh

        driver = JunosDriver()
        with caplog.at_level(logging.WARNING):
            driver.connect("10.0.0.20", transport="netconf", username="admin", password="pass")

        assert driver._transport == "ssh"
        assert driver._connected is True
        assert any("falling back to SSH" in msg for msg in caplog.messages)

    @patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI_NETCONF", False)
    @patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI", True)
    @patch("network_mcp.drivers.juniper_junos.JunosScrapliTransport")
    def test_no_scrapli_netconf_falls_back_to_ssh(self, mock_transport_cls, caplog):
        mock_ssh = MagicMock()
        mock_transport_cls.return_value = mock_ssh

        driver = JunosDriver()
        with caplog.at_level(logging.WARNING):
            driver.connect("10.0.0.20", transport="netconf", username="admin", password="pass")

        assert driver._transport == "ssh"
        assert driver._connected is True
        assert any("scrapli-netconf not installed" in msg for msg in caplog.messages)


# ============================================================================
# 6. get_config SSH fallback
# ============================================================================


class TestGetConfigSshFallback:
    """Test get_config() uses SSH fallback when primary transport is absent."""

    def test_iosxe_get_config_ssh(self):
        def _respond(cmd):
            if "running" in cmd:
                return "hostname iosxe-1\n!"
            return "hostname iosxe-1\n!"

        transport = _make_mock_ssh_transport(send_command_side_effect=_respond)
        driver = _make_iosxe_ssh_driver(transport)

        with patch("network_mcp.sanitizer.config_sanitizer") as mock_san:
            mock_san.sanitize.side_effect = lambda x: x
            config = driver.get_config(sanitized=True)

        assert "running" in config
        assert "startup" in config
        assert config["running"] == "hostname iosxe-1\n!"

    def test_nxos_get_config_ssh(self):
        def _respond(cmd):
            if "running" in cmd:
                return "hostname nxos-1\n!"
            return "hostname nxos-1\n!"

        transport = _make_mock_ssh_transport(send_command_side_effect=_respond)
        driver = _make_nxos_ssh_driver(transport)

        with patch("network_mcp.sanitizer.config_sanitizer") as mock_san:
            mock_san.sanitize.side_effect = lambda x: x
            config = driver.get_config(sanitized=True)

        assert "running" in config
        assert "startup" in config

    def test_junos_get_config_ssh(self):
        transport = _make_mock_ssh_transport(send_command_side_effect=lambda cmd: "system {\n  host-name junos-1;\n}")
        driver = _make_junos_ssh_driver(transport)

        with patch("network_mcp.sanitizer.config_sanitizer") as mock_san:
            mock_san.sanitize.side_effect = lambda x: x
            config = driver.get_config(sanitized=True)

        assert "running" in config
        assert "startup" in config
        assert "host-name junos-1" in config["running"]
