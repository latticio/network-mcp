"""Tests for the 20 new NetworkDriver getters implemented in JunosDriver."""

from __future__ import annotations

from unittest.mock import MagicMock

from network_mcp.drivers.juniper_junos import JunosDriver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _netconf_response(xml_str: str) -> MagicMock:
    """Build a mock NETCONF RPC response with a .result attribute."""
    resp = MagicMock()
    resp.result = xml_str
    return resp


def _make_driver(xml_responses: list[str] | Exception) -> JunosDriver:
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
        responses = [_netconf_response(xml) for xml in xml_responses]
        mock_conn.rpc.side_effect = responses
    driver._conn = mock_conn
    return driver


# ---------------------------------------------------------------------------
# Mock NETCONF XML responses
# ---------------------------------------------------------------------------

MOCK_VLANS_XML = """\
<rpc-reply>
<vlan-information>
  <vlan>
    <vlan-tag>1</vlan-tag>
    <vlan-name>default</vlan-name>
    <vlan-status>active</vlan-status>
  </vlan>
  <vlan>
    <vlan-tag>100</vlan-tag>
    <vlan-name>SERVERS</vlan-name>
    <vlan-status>active</vlan-status>
    <vlan-detail>
      <vlan-member-interface>
        <vlan-member-interface-name>ge-0/0/0</vlan-member-interface-name>
      </vlan-member-interface>
      <vlan-member-interface>
        <vlan-member-interface-name>ge-0/0/1</vlan-member-interface-name>
      </vlan-member-interface>
    </vlan-detail>
  </vlan>
  <vlan>
    <vlan-tag>200</vlan-tag>
    <vlan-name>MGMT</vlan-name>
    <vlan-status>active</vlan-status>
  </vlan>
</vlan-information>
</rpc-reply>"""

MOCK_VLAN_DETAIL_XML = """\
<rpc-reply>
<vlan-information>
  <vlan>
    <vlan-tag>100</vlan-tag>
    <vlan-name>SERVERS</vlan-name>
    <vlan-status>active</vlan-status>
    <vlan-detail>
      <vlan-member-interface>
        <vlan-member-interface-name>ge-0/0/0</vlan-member-interface-name>
      </vlan-member-interface>
    </vlan-detail>
  </vlan>
</vlan-information>
</rpc-reply>"""

MOCK_COUNTERS_XML = """\
<rpc-reply>
<interface-information>
  <physical-interface>
    <name>ge-0/0/0</name>
    <traffic-statistics>
      <input-bytes>123456</input-bytes>
      <output-bytes>654321</output-bytes>
      <input-packets>1000</input-packets>
      <output-packets>2000</output-packets>
      <input-broadcast-packets>10</input-broadcast-packets>
      <output-broadcast-packets>20</output-broadcast-packets>
      <input-multicast-packets>5</input-multicast-packets>
      <output-multicast-packets>8</output-multicast-packets>
    </traffic-statistics>
  </physical-interface>
  <physical-interface>
    <name>ge-0/0/1</name>
    <traffic-statistics>
      <input-bytes>0</input-bytes>
      <output-bytes>0</output-bytes>
      <input-packets>0</input-packets>
      <output-packets>0</output-packets>
    </traffic-statistics>
  </physical-interface>
</interface-information>
</rpc-reply>"""

MOCK_ERRORS_XML = """\
<rpc-reply>
<interface-information>
  <physical-interface>
    <name>ge-0/0/0</name>
    <input-error-list>
      <input-errors>5</input-errors>
      <input-crc-errors>2</input-crc-errors>
      <framing-errors>1</framing-errors>
      <input-runts>0</input-runts>
      <input-giants>0</input-giants>
    </input-error-list>
    <output-error-list>
      <output-errors>3</output-errors>
    </output-error-list>
  </physical-interface>
  <physical-interface>
    <name>ge-0/0/1</name>
    <input-error-list>
      <input-errors>500</input-errors>
      <input-fcs-errors>250</input-fcs-errors>
      <framing-errors>5</framing-errors>
      <input-runts>3</input-runts>
      <input-giants>2</input-giants>
    </input-error-list>
    <output-error-list>
      <output-errors>10</output-errors>
    </output-error-list>
  </physical-interface>
</interface-information>
</rpc-reply>"""

MOCK_IP_INTERFACES_XML = """\
<rpc-reply>
<interface-information>
  <logical-interface>
    <name>ge-0/0/0.0</name>
    <oper-status>up</oper-status>
    <address-family>
      <address-family-name>inet</address-family-name>
      <interface-address>
        <ifa-local>10.0.0.1/24</ifa-local>
      </interface-address>
    </address-family>
  </logical-interface>
  <logical-interface>
    <name>lo0.0</name>
    <oper-status>up</oper-status>
    <address-family>
      <address-family-name>inet</address-family-name>
      <interface-address>
        <ifa-local>1.1.1.1/32</ifa-local>
      </interface-address>
    </address-family>
  </logical-interface>
  <logical-interface>
    <name>ge-0/0/1.0</name>
    <oper-status>down</oper-status>
    <address-family>
      <address-family-name>inet</address-family-name>
    </address-family>
  </logical-interface>
</interface-information>
</rpc-reply>"""

MOCK_TRANSCEIVER_XML = """\
<rpc-reply>
<interface-optics-diagnostics-information>
  <physical-interface>
    <name>ge-0/0/0</name>
    <optics-diagnostics>
      <optic-type>SFP-10G-SR</optic-type>
      <laser-output-power-dbm>-2.1</laser-output-power-dbm>
      <rx-signal-avg-optical-power-dbm>-5.3</rx-signal-avg-optical-power-dbm>
      <module-temperature-celsius>35.0</module-temperature-celsius>
    </optics-diagnostics>
  </physical-interface>
  <physical-interface>
    <name>ge-0/0/1</name>
    <optic-type>1000BASE-T</optic-type>
    <optics-diagnostics>
      <laser-output-power-dbm>0.0</laser-output-power-dbm>
      <laser-rx-optical-power-dbm>0.0</laser-rx-optical-power-dbm>
      <module-temperature>30.0</module-temperature>
    </optics-diagnostics>
  </physical-interface>
</interface-optics-diagnostics-information>
</rpc-reply>"""

MOCK_OSPF_XML = """\
<rpc-reply>
<ospf-neighbor-information>
  <ospf-neighbor>
    <neighbor-id>10.0.0.2</neighbor-id>
    <ospf-neighbor-state>full</ospf-neighbor-state>
    <interface-name>ge-0/0/0.0</interface-name>
    <ospf-area>0.0.0.0</ospf-area>
    <neighbor-adjacency-time>P1DT2H</neighbor-adjacency-time>
  </ospf-neighbor>
  <ospf-neighbor>
    <neighbor-id>10.0.0.3</neighbor-id>
    <ospf-neighbor-state>init</ospf-neighbor-state>
    <interface-name>ge-0/0/1.0</interface-name>
    <ospf-area>0.0.0.1</ospf-area>
    <neighbor-adjacency-time>PT5M</neighbor-adjacency-time>
  </ospf-neighbor>
</ospf-neighbor-information>
</rpc-reply>"""

MOCK_ROUTES_XML = """\
<rpc-reply>
<route-information>
  <route-table>
    <rt>
      <rt-destination>10.0.0.0/24</rt-destination>
      <rt-entry>
        <protocol-name>Direct</protocol-name>
        <nh><via>ge-0/0/0.0</via></nh>
        <metric>0</metric>
        <preference>0</preference>
      </rt-entry>
    </rt>
    <rt>
      <rt-destination>10.1.0.0/24</rt-destination>
      <rt-entry>
        <protocol-name>BGP</protocol-name>
        <nh><to>10.0.0.2</to></nh>
        <metric>100</metric>
        <preference>170</preference>
      </rt-entry>
    </rt>
    <rt>
      <rt-destination>10.2.0.0/24</rt-destination>
      <rt-entry>
        <protocol-name>OSPF</protocol-name>
        <nh><to>10.0.0.3</to></nh>
        <metric>20</metric>
        <preference>10</preference>
      </rt-entry>
    </rt>
  </route-table>
</route-information>
</rpc-reply>"""

MOCK_ROUTE_SUMMARY_XML = """\
<rpc-reply>
<route-summary-information>
  <route-table>
    <total-route-count>250</total-route-count>
    <protocols>
      <protocol-name>Direct</protocol-name>
      <protocol-route-count>10</protocol-route-count>
    </protocols>
    <protocols>
      <protocol-name>Static</protocol-name>
      <protocol-route-count>2</protocol-route-count>
    </protocols>
    <protocols>
      <protocol-name>BGP</protocol-name>
      <protocol-route-count>200</protocol-route-count>
    </protocols>
    <protocols>
      <protocol-name>OSPF</protocol-name>
      <protocol-route-count>38</protocol-route-count>
    </protocols>
  </route-table>
</route-summary-information>
</rpc-reply>"""

MOCK_MAC_TABLE_XML = """\
<rpc-reply>
<ethernet-switching-table-information>
  <ethernet-switching-table>
    <mac-table-entry>
      <mac-address>00:1c:73:00:00:01</mac-address>
      <mac-vlan>VLAN100</mac-vlan>
      <mac-interfaces>ge-0/0/0.0</mac-interfaces>
      <mac-type>Learn</mac-type>
    </mac-table-entry>
    <mac-table-entry>
      <mac-address>00:1c:73:00:00:02</mac-address>
      <mac-vlan>200</mac-vlan>
      <mac-interface>ge-0/0/1.0</mac-interface>
      <mac-type>Static</mac-type>
    </mac-table-entry>
  </ethernet-switching-table>
</ethernet-switching-table-information>
</rpc-reply>"""

MOCK_STP_XML = """\
<rpc-reply>
<stp-bridge-information>
  <stp-protocol>rstp</stp-protocol>
  <stp-instance>
    <stp-instance-id>100</stp-instance-id>
    <stp-root-bridge-address>aa:bb:cc:00:01:00</stp-root-bridge-address>
    <stp-root-port>ge-0/0/0</stp-root-port>
    <stp-bridge-priority>32768</stp-bridge-priority>
  </stp-instance>
  <stp-instance>
    <stp-instance-id>200</stp-instance-id>
    <stp-designated-bridge-address>aa:bb:cc:00:02:00</stp-designated-bridge-address>
    <stp-root-port>ge-0/0/1</stp-root-port>
    <stp-bridge-priority>4096</stp-bridge-priority>
  </stp-instance>
</stp-bridge-information>
</rpc-reply>"""

MOCK_PORT_CHANNELS_XML = """\
<rpc-reply>
<lacp-interface-information>
  <lag-lacp-header>
    <aggregate-name>ae0</aggregate-name>
    <aggregate-oper-state>up</aggregate-oper-state>
    <aggregate-lacp-mode>active-lacp</aggregate-lacp-mode>
    <lag-lacp-protocol>
      <name>ge-0/0/0</name>
      <lacp-mux-state>Collecting distributing</lacp-mux-state>
    </lag-lacp-protocol>
    <lag-lacp-protocol>
      <name>ge-0/0/1</name>
      <lacp-mux-state>Collecting distributing</lacp-mux-state>
    </lag-lacp-protocol>
  </lag-lacp-header>
  <lag-lacp-header>
    <aggregate-name>ae1</aggregate-name>
    <aggregate-oper-state>down</aggregate-oper-state>
    <aggregate-lacp-mode>passive</aggregate-lacp-mode>
    <lag-lacp-protocol>
      <name>ge-0/0/2</name>
      <lacp-mux-state>detached</lacp-mux-state>
    </lag-lacp-protocol>
  </lag-lacp-header>
</lacp-interface-information>
</rpc-reply>"""

MOCK_ENVIRONMENT_XML = """\
<rpc-reply>
<environment-information>
  <environment-item>
    <name>CPU</name>
    <status>OK</status>
    <temperature>45 degrees C</temperature>
  </environment-item>
  <environment-item>
    <name>Inlet</name>
    <status>OK</status>
    <temperature>28 degrees C</temperature>
  </environment-item>
  <environment-item>
    <name>Fan 1</name>
    <status>OK</status>
  </environment-item>
  <environment-item>
    <name>Fan 2</name>
    <status>OK</status>
  </environment-item>
  <environment-item>
    <name>PEM 0</name>
    <status>OK</status>
    <power-output>150W</power-output>
  </environment-item>
  <environment-item>
    <name>PEM 1</name>
    <status>Check</status>
  </environment-item>
</environment-information>
</rpc-reply>"""

MOCK_NTP_XML = """\
<rpc-reply>
<ntp-associations-information>
  <ntp-peer>
    <peer-address>10.0.0.100</peer-address>
    <tally-code>*</tally-code>
    <peer-stratum>2</peer-stratum>
    <peer-offset>0.500</peer-offset>
    <peer-jitter>1.200</peer-jitter>
  </ntp-peer>
  <ntp-peer>
    <peer-address>10.0.0.101</peer-address>
    <tally-code>+</tally-code>
    <peer-stratum>3</peer-stratum>
    <peer-offset>1.000</peer-offset>
    <peer-jitter>2.500</peer-jitter>
  </ntp-peer>
</ntp-associations-information>
</rpc-reply>"""

MOCK_CPU_MEMORY_XML = """\
<rpc-reply>
<route-engine-information>
  <route-engine>
    <cpu-idle>85</cpu-idle>
    <memory-dram-size>2048</memory-dram-size>
    <memory-buffer-utilization>50</memory-buffer-utilization>
  </route-engine>
</route-engine-information>
</rpc-reply>"""

MOCK_ACLS_XML = """\
<rpc-reply>
<firewall-information>
  <filter-information>
    <filter-name>MGMT-FILTER</filter-name>
    <counter>
      <counter-name>permit-mgmt</counter-name>
    </counter>
    <counter>
      <counter-name>discard-all</counter-name>
    </counter>
  </filter-information>
  <filter-information>
    <filter-name>WEB-FILTER</filter-name>
    <counter>
      <counter-name>permit-https</counter-name>
    </counter>
  </filter-information>
</firewall-information>
</rpc-reply>"""

MOCK_SNMP_XML = """\
<rpc-reply>
<configuration>
  <snmp>
    <community>
      <name>public</name>
      <authorization>read-only</authorization>
    </community>
    <community>
      <name>private</name>
      <authorization>read-write</authorization>
    </community>
    <v3>
      <usm>
        <local-engine>
          <user>
            <name>snmpv3user</name>
          </user>
        </local-engine>
      </usm>
    </v3>
    <trap-group>
      <targets>
        <name>10.0.0.200</name>
      </targets>
    </trap-group>
  </snmp>
</configuration>
</rpc-reply>"""

MOCK_RUNNING_SECTION_XML = """\
<rpc-reply>
<configuration>
  <protocols>
    <bgp>
      <group>
        <name>PEERS</name>
      </group>
    </bgp>
  </protocols>
</configuration>
</rpc-reply>"""

MOCK_STARTUP_CONFIG_XML = """\
<rpc-reply>
<configuration-text>
system {
    host-name test-junos;
}
</configuration-text>
</rpc-reply>"""

MOCK_VRFS_XML = """\
<rpc-reply>
<instance-information>
  <instance-core>
    <instance-name>master</instance-name>
    <instance-rd>---</instance-rd>
  </instance-core>
  <instance-core>
    <instance-name>PROD</instance-name>
    <instance-rd>10.0.0.1:1</instance-rd>
    <instance-interface>
      <interface-name>ge-0/0/0.100</interface-name>
    </instance-interface>
    <instance-interface>
      <interface-name>ge-0/0/1.100</interface-name>
    </instance-interface>
    <instance-rib-route-count>42</instance-rib-route-count>
  </instance-core>
</instance-information>
</rpc-reply>"""


# ===========================================================================
# Tests: get_vlans
# ===========================================================================


class TestGetVlans:
    def test_returns_all_vlans(self):
        driver = _make_driver([MOCK_VLANS_XML])
        vlans = driver.get_vlans()
        assert len(vlans) == 3
        names = {v["name"] for v in vlans}
        assert "default" in names
        assert "SERVERS" in names
        assert "MGMT" in names

    def test_vlan_fields(self):
        driver = _make_driver([MOCK_VLANS_XML])
        vlans = driver.get_vlans()
        v100 = next(v for v in vlans if v["vlan_id"] == 100)
        assert v100["name"] == "SERVERS"
        assert v100["status"] == "active"
        assert len(v100["interfaces"]) == 2
        assert "ge-0/0/0" in v100["interfaces"]

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_vlans() == []


# ===========================================================================
# Tests: get_vlan_detail
# ===========================================================================


class TestGetVlanDetail:
    def test_returns_detail(self):
        driver = _make_driver([MOCK_VLAN_DETAIL_XML])
        detail = driver.get_vlan_detail(100)
        assert detail["vlan_id"] == 100
        assert detail["name"] == "SERVERS"
        assert detail["status"] == "active"
        assert detail["type"] == "static"
        assert len(detail["interfaces"]) >= 1

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
        driver = _make_driver([MOCK_COUNTERS_XML])
        counters = driver.get_interface_counters()
        assert len(counters) == 2

    def test_counter_values(self):
        driver = _make_driver([MOCK_COUNTERS_XML])
        counters = driver.get_interface_counters()
        ge0 = next(c for c in counters if c["interface"] == "ge-0/0/0")
        assert ge0["in_octets"] == 123456
        assert ge0["out_octets"] == 654321
        assert ge0["in_packets"] == 1000
        assert ge0["out_packets"] == 2000
        assert ge0["in_broadcast"] == 10
        assert ge0["out_broadcast"] == 20
        assert ge0["in_multicast"] == 5
        assert ge0["out_multicast"] == 8

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_interface_counters() == []


# ===========================================================================
# Tests: get_interface_errors
# ===========================================================================


class TestGetInterfaceErrors:
    def test_returns_interfaces(self):
        driver = _make_driver([MOCK_ERRORS_XML])
        errors = driver.get_interface_errors()
        assert len(errors) == 2

    def test_error_values(self):
        driver = _make_driver([MOCK_ERRORS_XML])
        errors = driver.get_interface_errors()
        ge1 = next(e for e in errors if e["interface"] == "ge-0/0/1")
        assert ge1["in_errors"] == 500
        assert ge1["in_crc"] == 250
        assert ge1["in_frame"] == 5
        assert ge1["in_runts"] == 3
        assert ge1["in_giants"] == 2
        assert ge1["out_errors"] == 10

    def test_crc_vs_fcs_fallback(self):
        """JunOS uses input-fcs-errors when input-crc-errors is absent."""
        driver = _make_driver([MOCK_ERRORS_XML])
        errors = driver.get_interface_errors()
        ge1 = next(e for e in errors if e["interface"] == "ge-0/0/1")
        # Uses input-fcs-errors (250) as CRC fallback
        assert ge1["in_crc"] == 250

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_interface_errors() == []


# ===========================================================================
# Tests: get_ip_interfaces
# ===========================================================================


class TestGetIpInterfaces:
    def test_returns_l3_interfaces(self):
        driver = _make_driver([MOCK_IP_INTERFACES_XML])
        ips = driver.get_ip_interfaces()
        assert len(ips) == 3

    def test_ip_fields(self):
        driver = _make_driver([MOCK_IP_INTERFACES_XML])
        ips = driver.get_ip_interfaces()
        ge0 = next(i for i in ips if i["interface"] == "ge-0/0/0.0")
        assert ge0["ip_address"] == "10.0.0.1"
        assert ge0["subnet"] == "24"
        assert ge0["status"] == "up"

    def test_interface_without_ip(self):
        driver = _make_driver([MOCK_IP_INTERFACES_XML])
        ips = driver.get_ip_interfaces()
        ge1 = next(i for i in ips if i["interface"] == "ge-0/0/1.0")
        assert ge1["ip_address"] == ""
        assert ge1["status"] == "down"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_ip_interfaces() == []


# ===========================================================================
# Tests: get_transceiver_info
# ===========================================================================


class TestGetTransceiverInfo:
    def test_returns_transceivers(self):
        driver = _make_driver([MOCK_TRANSCEIVER_XML])
        info = driver.get_transceiver_info()
        assert len(info) == 2

    def test_transceiver_fields(self):
        driver = _make_driver([MOCK_TRANSCEIVER_XML])
        info = driver.get_transceiver_info()
        ge0 = next(t for t in info if t["interface"] == "ge-0/0/0")
        assert ge0["type"] == "SFP-10G-SR"
        assert ge0["tx_power"] == -2.1
        assert ge0["rx_power"] == -5.3
        assert ge0["temp"] == 35.0

    def test_fallback_optic_type(self):
        driver = _make_driver([MOCK_TRANSCEIVER_XML])
        info = driver.get_transceiver_info()
        ge1 = next(t for t in info if t["interface"] == "ge-0/0/1")
        assert ge1["type"] == "1000BASE-T"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_transceiver_info() == []


# ===========================================================================
# Tests: get_ospf_neighbors
# ===========================================================================


class TestGetOspfNeighbors:
    def test_returns_neighbors(self):
        driver = _make_driver([MOCK_OSPF_XML])
        neighbors = driver.get_ospf_neighbors()
        assert len(neighbors) == 2

    def test_neighbor_fields(self):
        driver = _make_driver([MOCK_OSPF_XML])
        neighbors = driver.get_ospf_neighbors()
        n1 = next(n for n in neighbors if n["neighbor_id"] == "10.0.0.2")
        assert n1["state"] == "Full"
        assert n1["interface"] == "ge-0/0/0.0"
        assert n1["area"] == "0.0.0.0"  # noqa: S104

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_ospf_neighbors() == []


# ===========================================================================
# Tests: get_route_table
# ===========================================================================


class TestGetRouteTable:
    def test_returns_routes(self):
        driver = _make_driver([MOCK_ROUTES_XML])
        routes = driver.get_route_table()
        assert len(routes) == 3

    def test_route_fields(self):
        driver = _make_driver([MOCK_ROUTES_XML])
        routes = driver.get_route_table()
        bgp = next(r for r in routes if r["protocol"] == "bgp")
        assert bgp["prefix"] == "10.1.0.0/24"
        assert bgp["next_hop"] == "10.0.0.2"
        assert bgp["metric"] == 100
        assert bgp["preference"] == 170

    def test_connected_route_normalization(self):
        """JunOS 'Direct' protocol should normalize to 'connected'."""
        driver = _make_driver([MOCK_ROUTES_XML])
        routes = driver.get_route_table()
        conn = next(r for r in routes if r["protocol"] == "connected")
        assert conn["prefix"] == "10.0.0.0/24"
        assert "ge-0/0/0.0" in conn["next_hop"]

    def test_limit(self):
        driver = _make_driver([MOCK_ROUTES_XML])
        routes = driver.get_route_table(limit=2)
        assert len(routes) == 2

    def test_protocol_filter(self):
        driver = _make_driver([MOCK_ROUTES_XML])
        routes = driver.get_route_table(protocol="bgp")
        assert len(routes) == 1
        assert routes[0]["protocol"] == "bgp"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_route_table() == []


# ===========================================================================
# Tests: get_route_summary
# ===========================================================================


class TestGetRouteSummary:
    def test_returns_summary(self):
        driver = _make_driver([MOCK_ROUTE_SUMMARY_XML])
        summary = driver.get_route_summary()
        assert summary["total"] == 250
        assert summary["connected"] == 10  # Direct -> connected
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
        driver = _make_driver([MOCK_MAC_TABLE_XML])
        entries = driver.get_mac_table()
        assert len(entries) == 2

    def test_entry_fields(self):
        driver = _make_driver([MOCK_MAC_TABLE_XML])
        entries = driver.get_mac_table()
        e1 = entries[0]
        assert e1["vlan"] == 100
        assert e1["interface"] == "ge-0/0/0.0"
        assert e1["type"] == "dynamic"
        assert ":" in e1["mac_address"]

    def test_static_type(self):
        driver = _make_driver([MOCK_MAC_TABLE_XML])
        entries = driver.get_mac_table()
        e2 = entries[1]
        assert e2["type"] == "static"

    def test_vlan_filter(self):
        driver = _make_driver([MOCK_MAC_TABLE_XML])
        entries = driver.get_mac_table(vlan=100)
        assert len(entries) == 1
        assert entries[0]["vlan"] == 100

    def test_limit(self):
        driver = _make_driver([MOCK_MAC_TABLE_XML])
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
        driver = _make_driver([MOCK_STP_XML])
        stp = driver.get_stp_status()
        assert stp["mode"] == "rstp"
        assert len(stp["instances"]) == 2

    def test_root_bridge(self):
        driver = _make_driver([MOCK_STP_XML])
        stp = driver.get_stp_status()
        assert stp["root_bridge"] == "aa:bb:cc:00:01:00"

    def test_instance_fields(self):
        driver = _make_driver([MOCK_STP_XML])
        stp = driver.get_stp_status()
        inst1 = next(i for i in stp["instances"] if i["id"] == "100")
        assert inst1["root_bridge"] == "aa:bb:cc:00:01:00"
        assert inst1["root_port"] == "ge-0/0/0"
        assert inst1["priority"] == 32768

    def test_designated_bridge_fallback(self):
        """Second instance uses stp-designated-bridge-address as fallback."""
        driver = _make_driver([MOCK_STP_XML])
        stp = driver.get_stp_status()
        inst2 = next(i for i in stp["instances"] if i["id"] == "200")
        assert inst2["root_bridge"] == "aa:bb:cc:00:02:00"

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
        driver = _make_driver([MOCK_PORT_CHANNELS_XML])
        channels = driver.get_port_channels()
        assert len(channels) == 2

    def test_active_channel(self):
        driver = _make_driver([MOCK_PORT_CHANNELS_XML])
        channels = driver.get_port_channels()
        ae0 = next(c for c in channels if c["name"] == "ae0")
        assert ae0["status"] == "up"
        assert ae0["protocol"] == "lacp"
        assert len(ae0["members"]) == 2
        assert all(m["status"] == "bundled" for m in ae0["members"])

    def test_down_channel(self):
        driver = _make_driver([MOCK_PORT_CHANNELS_XML])
        channels = driver.get_port_channels()
        ae1 = next(c for c in channels if c["name"] == "ae1")
        assert ae1["status"] == "down"
        assert len(ae1["members"]) == 1
        assert ae1["members"][0]["status"] == "down"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_port_channels() == []


# ===========================================================================
# Tests: get_environment
# ===========================================================================


class TestGetEnvironment:
    def test_returns_all_sections(self):
        driver = _make_driver([MOCK_ENVIRONMENT_XML])
        env = driver.get_environment()
        assert len(env["temperature"]) == 2
        assert len(env["fans"]) == 2
        assert len(env["power"]) == 2

    def test_temperature_fields(self):
        driver = _make_driver([MOCK_ENVIRONMENT_XML])
        env = driver.get_environment()
        cpu = next(t for t in env["temperature"] if t["sensor"] == "CPU")
        assert cpu["value"] == 45.0
        assert cpu["status"] == "ok"

    def test_power_fields(self):
        driver = _make_driver([MOCK_ENVIRONMENT_XML])
        env = driver.get_environment()
        pem0 = next(p for p in env["power"] if p["name"] == "PEM 0")
        assert pem0["status"] == "ok"
        assert pem0["output_watts"] == 150.0

    def test_failed_power_status(self):
        driver = _make_driver([MOCK_ENVIRONMENT_XML])
        env = driver.get_environment()
        pem1 = next(p for p in env["power"] if p["name"] == "PEM 1")
        assert pem1["status"] == "failed"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        env = driver.get_environment()
        assert env == {"temperature": [], "fans": [], "power": []}


# ===========================================================================
# Tests: get_ntp_status
# ===========================================================================


class TestGetNtpStatus:
    def test_synchronized(self):
        driver = _make_driver([MOCK_NTP_XML])
        ntp = driver.get_ntp_status()
        assert ntp["synchronized"] is True
        assert ntp["reference"] == "10.0.0.100"

    def test_peers(self):
        driver = _make_driver([MOCK_NTP_XML])
        ntp = driver.get_ntp_status()
        assert len(ntp["peers"]) == 2
        synced = next(p for p in ntp["peers"] if p["peer"] == "10.0.0.100")
        assert synced["stratum"] == 2
        assert synced["offset"] == 0.5
        assert synced["jitter"] == 1.2

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
        driver = _make_driver([MOCK_CPU_MEMORY_XML])
        cm = driver.get_cpu_memory()
        assert cm["cpu_percent"] == 15.0  # 100 - 85 idle
        assert cm["memory_total"] == 2048 * 1024  # 2048 MB -> KB
        assert cm["memory_used"] == (2048 * 1024 * 50) // 100  # 50% utilization
        assert cm["memory_free"] == cm["memory_total"] - cm["memory_used"]

    def test_exception_returns_defaults(self):
        driver = _make_driver(ConnectionError("fail"))
        cm = driver.get_cpu_memory()
        assert cm["cpu_percent"] == 0.0
        assert cm["memory_total"] == 0


# ===========================================================================
# Tests: get_acls (JunOS firewall filters)
# ===========================================================================


class TestGetAcls:
    def test_returns_acls(self):
        driver = _make_driver([MOCK_ACLS_XML])
        acls = driver.get_acls()
        assert len(acls) == 2

    def test_filter_fields(self):
        driver = _make_driver([MOCK_ACLS_XML])
        acls = driver.get_acls()
        mgmt = next(a for a in acls if a["name"] == "MGMT-FILTER")
        assert mgmt["type"] == "extended"
        assert len(mgmt["rules"]) == 2
        assert mgmt["rules"][0]["action"] == "permit"
        assert mgmt["rules"][1]["action"] == "deny"  # "discard-all"

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_acls() == []


# ===========================================================================
# Tests: get_snmp_config
# ===========================================================================


class TestGetSnmpConfig:
    def test_communities(self):
        driver = _make_driver([MOCK_SNMP_XML])
        snmp = driver.get_snmp_config()
        assert len(snmp["communities"]) == 2
        pub = next(c for c in snmp["communities"] if c["name"] == "public")
        assert pub["access"] == "ro"

    def test_users(self):
        driver = _make_driver([MOCK_SNMP_XML])
        snmp = driver.get_snmp_config()
        assert len(snmp["users"]) == 1
        assert snmp["users"][0]["name"] == "snmpv3user"

    def test_traps(self):
        driver = _make_driver([MOCK_SNMP_XML])
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
        driver = _make_driver([MOCK_RUNNING_SECTION_XML])
        section = driver.get_running_config_section("protocols")
        assert "protocols" in section or "bgp" in section

    def test_exception_returns_empty_string(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_running_config_section("protocols") == ""


# ===========================================================================
# Tests: get_startup_config
# ===========================================================================


class TestGetStartupConfig:
    def test_returns_config(self):
        driver = _make_driver([MOCK_STARTUP_CONFIG_XML])
        config = driver.get_startup_config()
        assert "host-name test-junos" in config

    def test_exception_returns_empty_string(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_startup_config() == ""


# ===========================================================================
# Tests: get_vrfs (JunOS routing instances)
# ===========================================================================


class TestGetVrfs:
    def test_returns_vrfs(self):
        driver = _make_driver([MOCK_VRFS_XML])
        vrfs = driver.get_vrfs()
        assert len(vrfs) == 2

    def test_vrf_fields(self):
        driver = _make_driver([MOCK_VRFS_XML])
        vrfs = driver.get_vrfs()
        prod = next(v for v in vrfs if v["name"] == "PROD")
        assert prod["rd"] == "10.0.0.1:1"
        assert "ge-0/0/0.100" in prod["interfaces"]
        assert "ge-0/0/1.100" in prod["interfaces"]
        assert prod["route_count"] == 42

    def test_default_vrf_rd(self):
        driver = _make_driver([MOCK_VRFS_XML])
        vrfs = driver.get_vrfs()
        master = next(v for v in vrfs if v["name"] == "master")
        assert master["rd"] == ""

    def test_exception_returns_empty(self):
        driver = _make_driver(ConnectionError("fail"))
        assert driver.get_vrfs() == []


# ===========================================================================
# Tests: capabilities includes getter flags
# ===========================================================================


class TestCapabilities:
    def test_all_getters_enabled(self):
        driver = JunosDriver()
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
