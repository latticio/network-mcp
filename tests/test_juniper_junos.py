"""Tests for JunosDriver — NETCONF-based Juniper JunOS driver.

Validates:
- Protocol compliance (platform, vendor, VENDOR_API_VERSION)
- XML parsing and normalization for all getters
- NETCONF RPC construction patterns
- MockJunosDriver returns correct schemas
"""

from __future__ import annotations

import pytest

from network_mcp.drivers.base import NetworkDriver
from network_mcp.drivers.juniper_junos import (
    JunosDriver,
    _normalize_mac,
    _parse_junos_speed,
    _parse_junos_uptime,
)
from tests.mock_data.junos.mock_driver import (
    MOCK_ARP_TABLE,
    MOCK_BGP_SUMMARY,
    MOCK_INTERFACE_INFORMATION,
    MOCK_LLDP_NEIGHBORS,
    MOCK_SYSTEM_INFORMATION,
    MockJunosDriver,
)

# --- Protocol compliance ---


class TestJunosDriverProtocol:
    """Verify JunosDriver satisfies the NetworkDriver protocol."""

    def test_junos_driver_is_network_driver(self):
        assert isinstance(JunosDriver(), NetworkDriver)

    def test_platform(self):
        driver = JunosDriver()
        assert driver.platform == "junos"

    def test_vendor(self):
        driver = JunosDriver()
        assert driver.vendor == "juniper"

    def test_vendor_api_version(self):
        assert JunosDriver.VENDOR_API_VERSION == "netconf-1.0"


# --- Helper function tests ---


class TestHelpers:
    """Test JunOS-specific helper functions."""

    def test_normalize_mac_colon(self):
        assert _normalize_mac("00:05:86:71:62:01") == "00:05:86:71:62:01"

    def test_normalize_mac_dot(self):
        assert _normalize_mac("0005.8671.6201") == "00:05:86:71:62:01"

    def test_normalize_mac_hyphen(self):
        assert _normalize_mac("00-05-86-71-62-01") == "00:05:86:71:62:01"

    def test_normalize_mac_empty(self):
        assert _normalize_mac("") == ""

    def test_normalize_mac_invalid(self):
        assert _normalize_mac("invalid") == "invalid"

    def test_parse_uptime_seconds(self):
        assert _parse_junos_uptime("864000") == 864000

    def test_parse_uptime_xml_style(self):
        assert _parse_junos_uptime("{ junos:seconds 864000 }") == 864000

    def test_parse_uptime_human_readable(self):
        assert _parse_junos_uptime("10 days, 3:45:12") == 10 * 86400 + 3 * 3600 + 45 * 60 + 12

    def test_parse_uptime_empty(self):
        assert _parse_junos_uptime("") == 0

    def test_parse_speed_mbps(self):
        assert _parse_junos_speed("1000mbps") == 1000

    def test_parse_speed_gbps(self):
        assert _parse_junos_speed("10Gbps") == 10000

    def test_parse_speed_numeric(self):
        assert _parse_junos_speed("1000") == 1000

    def test_parse_speed_auto(self):
        assert _parse_junos_speed("Auto") == 0

    def test_parse_speed_empty(self):
        assert _parse_junos_speed("") == 0

    def test_parse_speed_large_bps(self):
        """Large numeric value treated as bps."""
        assert _parse_junos_speed("10000000000") == 10000  # 10Gbps in bps


# --- get_facts XML parsing ---


class TestJunosGetFacts:
    """Test JunosDriver._normalize_facts() XML parsing."""

    def test_schema_keys(self):
        facts = JunosDriver._normalize_facts(MOCK_SYSTEM_INFORMATION)
        required = {"hostname", "model", "version", "serial", "uptime_seconds", "vendor", "platform"}
        assert set(facts.keys()) == required

    def test_values(self):
        facts = JunosDriver._normalize_facts(MOCK_SYSTEM_INFORMATION)
        assert facts["hostname"] == "junos-spine-01"
        assert facts["model"] == "QFX5120-48T"
        assert facts["version"] == "23.4R1.10"
        assert facts["serial"] == "WH0218440033"
        assert facts["uptime_seconds"] == 864000
        assert facts["vendor"] == "juniper"
        assert facts["platform"] == "junos"

    def test_types(self):
        facts = JunosDriver._normalize_facts(MOCK_SYSTEM_INFORMATION)
        assert isinstance(facts["hostname"], str)
        assert isinstance(facts["model"], str)
        assert isinstance(facts["version"], str)
        assert isinstance(facts["serial"], str)
        assert isinstance(facts["uptime_seconds"], int)

    def test_empty_xml(self):
        """Gracefully handle empty system-information."""
        facts = JunosDriver._normalize_facts("<rpc-reply><system-information/></rpc-reply>")
        assert facts["hostname"] == ""
        assert facts["model"] == ""
        assert facts["uptime_seconds"] == 0


# --- get_interfaces XML parsing ---


class TestJunosGetInterfaces:
    """Test JunosDriver._normalize_interfaces() XML parsing."""

    def test_returns_dict(self):
        interfaces = JunosDriver._normalize_interfaces(MOCK_INTERFACE_INFORMATION)
        assert isinstance(interfaces, dict)
        assert len(interfaces) == 3  # ge-0/0/0, ge-0/0/1, xe-0/0/0

    def test_interface_names(self):
        interfaces = JunosDriver._normalize_interfaces(MOCK_INTERFACE_INFORMATION)
        assert "ge-0/0/0" in interfaces
        assert "ge-0/0/1" in interfaces
        assert "xe-0/0/0" in interfaces

    def test_schema_keys(self):
        interfaces = JunosDriver._normalize_interfaces(MOCK_INTERFACE_INFORMATION)
        required = {"is_up", "is_enabled", "description", "speed", "mtu", "mac_address"}
        for name, data in interfaces.items():
            assert set(data.keys()) == required, f"Interface {name} has wrong keys"

    def test_up_interface(self):
        interfaces = JunosDriver._normalize_interfaces(MOCK_INTERFACE_INFORMATION)
        ge0 = interfaces["ge-0/0/0"]
        assert ge0["is_up"] is True
        assert ge0["is_enabled"] is True
        assert ge0["description"] == "uplink to spine-02"
        assert ge0["speed"] == 1000
        assert ge0["mtu"] == 9216
        assert ge0["mac_address"] == "00:05:86:71:62:01"

    def test_down_interface(self):
        interfaces = JunosDriver._normalize_interfaces(MOCK_INTERFACE_INFORMATION)
        ge1 = interfaces["ge-0/0/1"]
        assert ge1["is_up"] is False
        assert ge1["is_enabled"] is False
        assert ge1["description"] == ""

    def test_10g_interface(self):
        interfaces = JunosDriver._normalize_interfaces(MOCK_INTERFACE_INFORMATION)
        xe0 = interfaces["xe-0/0/0"]
        assert xe0["speed"] == 10000  # 10Gbps
        assert xe0["is_up"] is True

    def test_types(self):
        interfaces = JunosDriver._normalize_interfaces(MOCK_INTERFACE_INFORMATION)
        for name, data in interfaces.items():
            assert isinstance(data["is_up"], bool), f"{name}: is_up"
            assert isinstance(data["is_enabled"], bool), f"{name}: is_enabled"
            assert isinstance(data["description"], str), f"{name}: description"
            assert isinstance(data["speed"], int), f"{name}: speed"
            assert isinstance(data["mtu"], int), f"{name}: mtu"
            assert isinstance(data["mac_address"], str), f"{name}: mac_address"

    def test_empty_xml(self):
        interfaces = JunosDriver._normalize_interfaces("<rpc-reply><interface-information/></rpc-reply>")
        assert interfaces == {}


# --- get_bgp_summary XML parsing ---


class TestJunosGetBgpSummary:
    """Test JunosDriver._normalize_bgp_summary() XML parsing."""

    def test_schema_keys(self):
        bgp = JunosDriver._normalize_bgp_summary(MOCK_BGP_SUMMARY)
        assert set(bgp.keys()) == {"router_id", "as_number", "peers"}

    def test_values(self):
        bgp = JunosDriver._normalize_bgp_summary(MOCK_BGP_SUMMARY)
        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65000
        assert len(bgp["peers"]) == 2

    def test_peer_values(self):
        bgp = JunosDriver._normalize_bgp_summary(MOCK_BGP_SUMMARY)
        # Peer addresses should have port suffix stripped
        assert "10.0.0.2" in bgp["peers"]
        assert "10.0.0.3" in bgp["peers"]

        peer1 = bgp["peers"]["10.0.0.2"]
        assert peer1["state"] == "Established"
        assert peer1["prefixes_received"] == 150
        assert peer1["uptime"] == 1234567

        peer2 = bgp["peers"]["10.0.0.3"]
        assert peer2["state"] == "Active"
        assert peer2["prefixes_received"] == 0

    def test_peer_schema(self):
        bgp = JunosDriver._normalize_bgp_summary(MOCK_BGP_SUMMARY)
        required = {"state", "prefixes_received", "uptime"}
        for ip, data in bgp["peers"].items():
            assert set(data.keys()) == required, f"Peer {ip} has wrong keys"

    def test_types(self):
        bgp = JunosDriver._normalize_bgp_summary(MOCK_BGP_SUMMARY)
        assert isinstance(bgp["router_id"], str)
        assert isinstance(bgp["as_number"], int)
        for _ip, data in bgp["peers"].items():
            assert isinstance(data["state"], str)
            assert isinstance(data["prefixes_received"], int)
            assert isinstance(data["uptime"], int)

    def test_empty_xml(self):
        bgp = JunosDriver._normalize_bgp_summary("<rpc-reply><bgp-information/></rpc-reply>")
        assert bgp["router_id"] == ""
        assert bgp["as_number"] == 0
        assert bgp["peers"] == {}


# --- get_arp_table XML parsing ---


class TestJunosGetArpTable:
    """Test JunosDriver._normalize_arp_table() XML parsing."""

    def test_returns_list(self):
        arp = JunosDriver._normalize_arp_table(MOCK_ARP_TABLE)
        assert isinstance(arp, list)
        assert len(arp) == 2

    def test_entry_schema(self):
        arp = JunosDriver._normalize_arp_table(MOCK_ARP_TABLE)
        required = {"ip", "mac", "interface", "age"}
        for entry in arp:
            assert set(entry.keys()) == required

    def test_values(self):
        arp = JunosDriver._normalize_arp_table(MOCK_ARP_TABLE)
        assert arp[0]["ip"] == "10.0.0.2"
        assert arp[0]["mac"] == "00:05:86:cc:dd:01"
        assert arp[0]["interface"] == "ge-0/0/0"
        assert arp[0]["age"] == 120.0
        assert arp[1]["ip"] == "10.0.0.3"

    def test_types(self):
        arp = JunosDriver._normalize_arp_table(MOCK_ARP_TABLE)
        for entry in arp:
            assert isinstance(entry["ip"], str)
            assert isinstance(entry["mac"], str)
            assert isinstance(entry["interface"], str)
            assert isinstance(entry["age"], float)

    def test_empty_xml(self):
        arp = JunosDriver._normalize_arp_table("<rpc-reply><arp-table-information/></rpc-reply>")
        assert arp == []


# --- get_lldp_neighbors XML parsing ---


class TestJunosGetLldpNeighbors:
    """Test JunosDriver._normalize_lldp_neighbors() XML parsing."""

    def test_returns_dict(self):
        lldp = JunosDriver._normalize_lldp_neighbors(MOCK_LLDP_NEIGHBORS)
        assert isinstance(lldp, dict)
        assert len(lldp) == 2

    def test_interface_names(self):
        lldp = JunosDriver._normalize_lldp_neighbors(MOCK_LLDP_NEIGHBORS)
        assert "ge-0/0/0" in lldp
        assert "xe-0/0/0" in lldp

    def test_neighbor_schema(self):
        lldp = JunosDriver._normalize_lldp_neighbors(MOCK_LLDP_NEIGHBORS)
        required = {"hostname", "port", "system_description"}
        for intf, neighbors in lldp.items():
            assert isinstance(neighbors, list)
            for n in neighbors:
                assert set(n.keys()) == required, f"{intf}: neighbor has wrong keys"

    def test_values(self):
        lldp = JunosDriver._normalize_lldp_neighbors(MOCK_LLDP_NEIGHBORS)
        ge0_neighbors = lldp["ge-0/0/0"]
        assert len(ge0_neighbors) == 1
        assert ge0_neighbors[0]["hostname"] == "spine-02.lab"
        assert ge0_neighbors[0]["port"] == "ge-0/0/1"
        assert "Juniper" in ge0_neighbors[0]["system_description"]

    def test_types(self):
        lldp = JunosDriver._normalize_lldp_neighbors(MOCK_LLDP_NEIGHBORS)
        for _intf, neighbors in lldp.items():
            for n in neighbors:
                assert isinstance(n["hostname"], str)
                assert isinstance(n["port"], str)
                assert isinstance(n["system_description"], str)

    def test_empty_xml(self):
        lldp = JunosDriver._normalize_lldp_neighbors("<rpc-reply><lldp-neighbors-information/></rpc-reply>")
        assert lldp == {}


# --- MockJunosDriver ---


class TestMockJunosDriver:
    """Verify MockJunosDriver implements full protocol and returns correct data."""

    @pytest.fixture
    def driver(self):
        d = MockJunosDriver()
        d.connect()
        yield d
        d.close()

    def test_platform_vendor(self, driver):
        assert driver.platform == "junos"
        assert driver.vendor == "juniper"
        assert driver.VENDOR_API_VERSION == "netconf-1.0"

    def test_get_facts_schema(self, driver):
        facts = driver.get_facts()
        required = {"hostname", "model", "version", "serial", "uptime_seconds", "vendor", "platform"}
        assert set(facts.keys()) == required
        assert facts["vendor"] == "juniper"
        assert facts["platform"] == "junos"

    def test_get_interfaces_schema(self, driver):
        interfaces = driver.get_interfaces()
        assert isinstance(interfaces, dict)
        assert len(interfaces) > 0
        for data in interfaces.values():
            assert "is_up" in data
            assert "speed" in data

    def test_get_bgp_summary_schema(self, driver):
        bgp = driver.get_bgp_summary()
        assert "router_id" in bgp
        assert "as_number" in bgp
        assert "peers" in bgp

    def test_get_arp_table_schema(self, driver):
        arp = driver.get_arp_table()
        assert isinstance(arp, list)
        assert len(arp) > 0
        for entry in arp:
            assert "ip" in entry
            assert "mac" in entry

    def test_get_lldp_neighbors_schema(self, driver):
        lldp = driver.get_lldp_neighbors()
        assert isinstance(lldp, dict)
        assert len(lldp) > 0

    def test_get_config_schema(self, driver):
        config = driver.get_config()
        assert set(config.keys()) == {"running", "startup"}
        assert config["running"] != ""
        # JunOS startup == running (committed config)
        assert config["running"] == config["startup"]

    def test_run_show(self, driver):
        results = driver.run_show(["show version"])
        assert isinstance(results, list)
        assert len(results) == 1

    def test_run_config(self, driver):
        results = driver.run_config(["set system host-name test"])
        assert isinstance(results, list)

    def test_connect_close(self):
        d = MockJunosDriver()
        assert not d.is_connected()
        d.connect()
        assert d.is_connected()
        d.close()
        assert not d.is_connected()

    def test_not_connected_raises(self):
        d = MockJunosDriver()
        with pytest.raises(ConnectionError):
            d.run_show(["show version"])
