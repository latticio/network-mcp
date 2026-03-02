"""Tests for the 20 new NetworkDriver getters implemented in IosXeDriver."""

from __future__ import annotations

from unittest.mock import MagicMock

from network_mcp.drivers.cisco_iosxe import IosXeDriver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver(ssh_responses: list[str] | Exception) -> IosXeDriver:
    """Build an IosXeDriver with a mocked SSH transport (no RESTCONF)."""
    driver = IosXeDriver()
    driver._connected = True
    driver._transport = "ssh"
    driver._ssh_host = "test-iosxe"
    driver._ssh_username = "admin"
    driver._ssh_password = "pass"
    driver._last_command_time = 0.0
    driver._keepalive_interval = 99999

    mock_transport = MagicMock()
    if isinstance(ssh_responses, Exception):
        mock_transport.send_command.side_effect = ssh_responses
    else:
        mock_transport.send_command.side_effect = ssh_responses
    driver._ssh_transport = mock_transport
    return driver


# ---------------------------------------------------------------------------
# Mock SSH outputs
# ---------------------------------------------------------------------------

MOCK_VLAN_BRIEF = """\
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Gi0/0, Gi0/1
100  SERVERS                          active    Gi0/2, Gi0/3
200  MGMT                             active
"""

MOCK_VLAN_ID_100 = """\
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
100  SERVERS                          active    Gi0/2, Gi0/3

VLAN Type  SAID       MTU   Parent RingNo BridgeNo Stp  BrdgMode Trans1 Trans2
---- ----- ---------- ----- ------ ------ -------- ---- -------- ------ ------
100  enet  100100     1500  -      -      -        -    -        0      0
"""

MOCK_SHOW_INTERFACES = """\
GigabitEthernet0/0 is up, line protocol is up
  Hardware is iGbE, address is 0050.56a0.0001 (bia 0050.56a0.0001)
  Description: uplink
  MTU 1500 bytes, BW 1000000 Kbit/sec
  5 minute input rate 1000 bits/sec, 2 packets/sec
  5 minute output rate 500 bits/sec, 1 packets/sec
     1000 packets input, 123456 bytes, 0 no buffer
     10 broadcasts, 5 multicasts
     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored
     0 runts, 0 giants, 0 throttles
     2000 packets output, 654321 bytes, 0 underruns
     20 broadcasts, 8 multicasts
     0 output errors, 0 collisions, 0 interface resets
GigabitEthernet0/1 is administratively down, line protocol is down
  Hardware is iGbE, address is 0050.56a0.0002 (bia 0050.56a0.0002)
  MTU 1500 bytes, BW 100000 Kbit/sec
     0 packets input, 0 bytes
     0 input errors, 5 CRC, 3 frame, 0 overrun, 0 ignored
     2 runts, 1 giants, 0 throttles
     0 packets output, 0 bytes
     10 output errors, 0 collisions, 0 interface resets
"""

MOCK_IP_INTF_BRIEF = """\
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0     10.0.0.1        YES manual up                    up
GigabitEthernet0/1     unassigned      YES unset  administratively down down
Loopback0              1.1.1.1         YES manual up                    up
"""

MOCK_TRANSCEIVER = """\
If device is externally calibrated, only different calibration values are printed.
                                     Optical   Optical
              Transceiver            Tx Power  Rx Power
Port          Type                   (dBm)     (dBm)      Temp (C)
---------     --------------------   --------  --------   --------
Gi0/0         SFP-10G-SR            -2.1      -5.3       35.0
Gi0/1         1000BASE-T            0.0       0.0        30.0
"""

MOCK_OSPF_NEIGHBORS = """\
Neighbor ID     Pri   State           Dead Time   Address         Interface
10.0.0.2          1   FULL/DR         00:00:35    10.0.0.2        Gi0/0
10.0.0.3          1   INIT/DROTHER    00:00:30    10.0.0.3        Gi0/1
"""

MOCK_IP_ROUTE = """\
Codes: C - connected, S - static, B - BGP, O - OSPF

Gateway of last resort is not set

C     10.0.0.0/24 is directly connected, GigabitEthernet0/0
B     10.1.0.0/24 [200/100] via 10.0.0.2, 00:15:30, GigabitEthernet0/0
O     10.2.0.0/24 [110/20] via 10.0.0.3, 00:10:00, GigabitEthernet0/1
"""

MOCK_ROUTE_SUMMARY = """\
IP routing table name is default (0x0)
IP routing table maximum-paths is 32
Route Source    Networks    Subnets     Replicates  Overhead    Memory (bytes)
connected       3           2           0           320         760
static          1           1           0           160         304
bgp 65000       200         0           0           0           0
ospf 1          38          0           0           0           0
Total of 250 routes
"""

MOCK_MAC_TABLE = """\
          Mac Address Table
-------------------------------------------
 Vlan    Mac Address       Type        Ports
----    -----------       --------    -----
 100    001c.7300.0001    DYNAMIC     Gi0/0
 200    001c.7300.0002    STATIC      Gi0/1
"""

MOCK_STP = """\
VLAN0100
  Spanning tree enabled protocol rstp
  Root ID    Priority    32868
             Address     aabb.cc00.0100
  Root port  Gi0/0
  Bridge ID  Priority    32868
             Address     aabb.cc00.0200

VLAN0200
  Spanning tree enabled protocol rstp
  Root ID    Priority    4096
             Address     aabb.cc00.0300
  Root port  Gi0/1
  Bridge ID  Priority    8192
"""

MOCK_ETHERCHANNEL = """\
Number of channel-groups in use: 1
Group  Port-channel  Protocol    Ports
------+-------------+-----------+-----------------------------------------------
1      Po1(SU)       LACP        Gi0/0(P)    Gi0/1(P)
2      Po2(SD)       PAgP        Gi0/2(D)
"""

MOCK_ENV = """\
 SYSTEM TEMPERATURE is OK
 Inlet    28C  ok
 CPU      45C  ok
 FAN 1  OK
 FAN 2  OK
 PS1 1  OK  150W
 PS2 2  FAILED
"""

MOCK_NTP_STATUS = """\
Clock is synchronized, stratum 3, reference is 10.0.0.100
"""

MOCK_NTP_ASSOCIATIONS = """\
  address         ref clock       st   when   poll reach  delay  offset   disp
*~10.0.0.100     .GPS.            2     64    64   377    1.500   0.500  1.200
+~10.0.0.101     10.0.0.100       3     32    64   377    2.000   1.000  2.500
"""

MOCK_CPU = """\
CPU utilization for five seconds: 15%/3%; one minute: 12%; five minutes: 10%
 PID Runtime(ms)   Invoked      uSecs   5Sec   1Min   5Min TTY Process
Processor Pool Total: 2048000 Used: 1024000 Free: 1024000
"""

MOCK_ACL = """\
Standard IP access list MGMT-ACCESS
    10 permit 10.0.0.0/8
    20 deny any
Extended IP access list WEB-ACL
    10 permit tcp any any eq 443
"""

MOCK_SNMP = """\
Community name: public  access: read-only
Community name: private  access: read-write
10.0.0.200  Traps  v2c  trap-comm
"""

MOCK_RUNNING_SECTION = """\
router bgp 65000
   router-id 10.0.0.1
   neighbor 10.0.0.2 remote-as 65001
"""

MOCK_STARTUP_CONFIG = """\
! startup-config
hostname test-iosxe
"""

MOCK_VRF = """\
  Name                             Default RD            Protocols   Interfaces
  MGMT                             65000:1               ipv4        Gi0/0
  PROD                             65000:2               ipv4,ipv6   Gi0/1, Lo0
"""


# ===========================================================================
# Tests: get_vlans
# ===========================================================================


class TestGetVlans:
    def test_returns_all_vlans(self):
        driver = _make_driver([MOCK_VLAN_BRIEF])
        vlans = driver.get_vlans()
        assert len(vlans) == 3
        names = {v["name"] for v in vlans}
        assert "default" in names
        assert "SERVERS" in names
        assert "MGMT" in names

    def test_vlan_fields(self):
        driver = _make_driver([MOCK_VLAN_BRIEF])
        vlans = driver.get_vlans()
        v100 = next(v for v in vlans if v["vlan_id"] == 100)
        assert v100["name"] == "SERVERS"
        assert v100["status"] == "active"
        assert len(v100["interfaces"]) >= 1

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_vlans() == []


# ===========================================================================
# Tests: get_vlan_detail
# ===========================================================================


class TestGetVlanDetail:
    def test_returns_detail(self):
        driver = _make_driver([MOCK_VLAN_ID_100])
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
        driver = _make_driver([MOCK_SHOW_INTERFACES])
        counters = driver.get_interface_counters()
        assert len(counters) == 2

    def test_counter_values(self):
        driver = _make_driver([MOCK_SHOW_INTERFACES])
        counters = driver.get_interface_counters()
        gi0 = next(c for c in counters if c["interface"] == "GigabitEthernet0/0")
        assert gi0["in_packets"] == 1000
        assert gi0["in_octets"] == 123456
        assert gi0["out_packets"] == 2000
        assert gi0["out_octets"] == 654321

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_interface_counters() == []


# ===========================================================================
# Tests: get_interface_errors
# ===========================================================================


class TestGetInterfaceErrors:
    def test_returns_interfaces(self):
        driver = _make_driver([MOCK_SHOW_INTERFACES])
        errors = driver.get_interface_errors()
        assert len(errors) == 2

    def test_error_values(self):
        driver = _make_driver([MOCK_SHOW_INTERFACES])
        errors = driver.get_interface_errors()
        gi1 = next(e for e in errors if e["interface"] == "GigabitEthernet0/1")
        assert gi1["in_crc"] == 5
        assert gi1["in_frame"] == 3
        assert gi1["in_runts"] == 2
        assert gi1["in_giants"] == 1
        assert gi1["out_errors"] == 10

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_interface_errors() == []


# ===========================================================================
# Tests: get_ip_interfaces
# ===========================================================================


class TestGetIpInterfaces:
    def test_returns_l3_interfaces(self):
        driver = _make_driver([MOCK_IP_INTF_BRIEF])
        ips = driver.get_ip_interfaces()
        assert len(ips) == 3

    def test_ip_fields(self):
        driver = _make_driver([MOCK_IP_INTF_BRIEF])
        ips = driver.get_ip_interfaces()
        gi0 = next(i for i in ips if i["interface"] == "GigabitEthernet0/0")
        assert gi0["ip_address"] == "10.0.0.1"
        assert gi0["status"] == "up"

    def test_unassigned_interface(self):
        driver = _make_driver([MOCK_IP_INTF_BRIEF])
        ips = driver.get_ip_interfaces()
        gi1 = next(i for i in ips if i["interface"] == "GigabitEthernet0/1")
        assert gi1["ip_address"] == ""
        assert gi1["status"] == "down"

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
        gi0 = next(t for t in info if t["interface"] == "Gi0/0")
        assert gi0["type"] == "SFP-10G-SR"
        assert gi0["tx_power"] == -2.1
        assert gi0["rx_power"] == -5.3
        assert gi0["temp"] == 35.0

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
        assert n1["state"] == "Full"
        assert n1["interface"] == "Gi0/0"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_ospf_neighbors() == []


# ===========================================================================
# Tests: get_route_table
# ===========================================================================


class TestGetRouteTable:
    def test_returns_routes(self):
        driver = _make_driver([MOCK_IP_ROUTE])
        routes = driver.get_route_table()
        assert len(routes) == 3

    def test_route_fields(self):
        driver = _make_driver([MOCK_IP_ROUTE])
        routes = driver.get_route_table()
        bgp = next(r for r in routes if r["protocol"] == "bgp")
        assert bgp["prefix"] == "10.1.0.0/24"
        assert bgp["next_hop"] == "10.0.0.2"
        assert bgp["preference"] == 200
        assert bgp["metric"] == 100

    def test_connected_route(self):
        driver = _make_driver([MOCK_IP_ROUTE])
        routes = driver.get_route_table()
        conn = next(r for r in routes if r["protocol"] == "connected")
        assert conn["prefix"] == "10.0.0.0/24"
        assert "GigabitEthernet0/0" in conn["next_hop"]

    def test_limit(self):
        driver = _make_driver([MOCK_IP_ROUTE])
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
        assert summary["connected"] == 3
        assert summary["static"] == 1

    def test_exception_returns_defaults(self):
        driver = _make_driver(ConnectionError("fail"))
        summary = driver.get_route_summary()
        assert summary["total"] == 0


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
        assert e1["type"] == "dynamic"

    def test_limit(self):
        driver = _make_driver([MOCK_MAC_TABLE])
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
        assert stp["mode"] == "rstp"
        assert len(stp["instances"]) == 2

    def test_root_bridge(self):
        driver = _make_driver([MOCK_STP])
        stp = driver.get_stp_status()
        assert stp["root_bridge"] != ""

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
        driver = _make_driver([MOCK_ETHERCHANNEL])
        channels = driver.get_port_channels()
        assert len(channels) == 2

    def test_active_channel(self):
        driver = _make_driver([MOCK_ETHERCHANNEL])
        channels = driver.get_port_channels()
        po1 = next(c for c in channels if c["name"] == "Po1")
        assert po1["status"] == "up"
        assert po1["protocol"] == "lacp"
        assert len(po1["members"]) == 2

    def test_down_channel(self):
        driver = _make_driver([MOCK_ETHERCHANNEL])
        channels = driver.get_port_channels()
        po2 = next(c for c in channels if c["name"] == "Po2")
        assert po2["status"] == "down"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_port_channels() == []


# ===========================================================================
# Tests: get_environment
# ===========================================================================


class TestGetEnvironment:
    def test_returns_data(self):
        driver = _make_driver([MOCK_ENV])
        env = driver.get_environment()
        assert len(env["temperature"]) >= 1
        assert len(env["fans"]) >= 1

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
        driver = _make_driver([MOCK_CPU])
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
        driver = _make_driver([MOCK_ACL])
        acls = driver.get_acls()
        assert len(acls) == 2

    def test_standard_acl(self):
        driver = _make_driver([MOCK_ACL])
        acls = driver.get_acls()
        mgmt = next(a for a in acls if a["name"] == "MGMT-ACCESS")
        assert mgmt["type"] == "standard"
        assert len(mgmt["rules"]) == 2

    def test_extended_acl(self):
        driver = _make_driver([MOCK_ACL])
        acls = driver.get_acls()
        web = next(a for a in acls if a["name"] == "WEB-ACL")
        assert web["type"] == "extended"

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

    def test_traps(self):
        driver = _make_driver([MOCK_SNMP])
        snmp = driver.get_snmp_config()
        assert len(snmp["traps"]) >= 1

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
        assert "hostname test-iosxe" in config

    def test_exception_returns_empty_string(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_startup_config() == ""


# ===========================================================================
# Tests: get_vrfs
# ===========================================================================


class TestGetVrfs:
    def test_returns_vrfs(self):
        driver = _make_driver([MOCK_VRF])
        vrfs = driver.get_vrfs()
        assert len(vrfs) == 2

    def test_vrf_fields(self):
        driver = _make_driver([MOCK_VRF])
        vrfs = driver.get_vrfs()
        mgmt = next(v for v in vrfs if v["name"] == "MGMT")
        assert mgmt["rd"] == "65000:1"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_vrfs() == []


# ===========================================================================
# Tests: capabilities includes getter flags
# ===========================================================================


class TestCapabilities:
    def test_all_getters_enabled(self):
        driver = IosXeDriver()
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
