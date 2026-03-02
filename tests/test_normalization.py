"""Tests for NetworkDriver normalized getters on EosDriver and cross-vendor normalization.

Validates that EosDriver's get_facts(), get_interfaces(), get_bgp_summary(),
get_arp_table(), get_lldp_neighbors(), and get_config() produce output
conforming to the vendor-neutral schema defined in drivers/base.py.

Also includes 4-vendor parameterized tests that verify all mock drivers
(EOS, IOS-XE, NX-OS, JunOS) produce identical output schemas.

Additionally tests:
- normalize_mac() handles all vendor MAC formats consistently.
- normalize_speed() normalizes speed strings to a standard format.
- Pydantic schema models enforce required fields via model_validator.
- Each driver produces consistent output when given golden fixture inputs.
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.driver import EosDriver
from network_mcp.drivers.base import NetworkDriver
from network_mcp.drivers.cisco_iosxe import IosXeDriver
from network_mcp.drivers.cisco_nxos import NxosDriver
from network_mcp.drivers.juniper_junos import JunosDriver
from network_mcp.helpers import normalize_mac, normalize_speed
from network_mcp.schemas import ArpEntry, BgpPeerEntry, DeviceFacts, InterfaceEntry

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# --- Mock EOS responses for normalized getters ---

MOCK_EOS_SHOW_VERSION = {
    "modelName": "DCS-7050TX3-48C8",
    "version": "4.32.1F",
    "serialNumber": "SSJ17856312",
    "hostname": "spine-01",
    "uptime": 864000.5,
    "memTotal": 8048832,
    "memFree": 4024416,
    "systemMacAddress": "00:1c:73:aa:bb:cc",
    "hardwareRevision": "12.05",
}

MOCK_EOS_SHOW_INTERFACES = {
    "interfaces": {
        "Ethernet1": {
            "name": "Ethernet1",
            "lineProtocolStatus": "up",
            "interfaceStatus": "connected",
            "hardware": "Ethernet",
            "mtu": 9214,
            "bandwidth": 100000000000,
            "description": "uplink to spine-02",
            "physicalAddress": "00:1c:73:aa:bb:01",
            "burnedInAddress": "00:1c:73:aa:bb:01",
            "interfaceCounters": {
                "inOctets": 123456789,
                "outOctets": 987654321,
            },
        },
        "Ethernet2": {
            "name": "Ethernet2",
            "lineProtocolStatus": "down",
            "interfaceStatus": "disabled",
            "hardware": "Ethernet",
            "mtu": 1500,
            "bandwidth": 1000000000,
            "description": "",
            "physicalAddress": "00:1c:73:aa:bb:02",
            "burnedInAddress": "00:1c:73:aa:bb:02",
            "interfaceCounters": {},
        },
        "Management1": {
            "name": "Management1",
            "lineProtocolStatus": "up",
            "interfaceStatus": "connected",
            "hardware": "Ethernet",
            "mtu": 1500,
            "bandwidth": 1000000000,
            "description": "oob-mgmt",
            "physicalAddress": "00:1c:73:aa:bb:ff",
            "burnedInAddress": "00:1c:73:aa:bb:ff",
            "interfaceCounters": {},
        },
    }
}

MOCK_EOS_BGP_SUMMARY = {
    "vrfs": {
        "default": {
            "routerId": "10.0.0.1",
            "asn": "65000",
            "peers": {
                "10.0.0.2": {
                    "peerState": "Established",
                    "prefixReceived": 150,
                    "upDownTime": 1234567,
                    "asn": "65001",
                },
                "10.0.0.3": {
                    "peerState": "Active",
                    "prefixReceived": 0,
                    "upDownTime": 100,
                    "asn": "65002",
                },
            },
        }
    }
}

MOCK_EOS_BGP_SUMMARY_VRF = {
    "vrfs": {
        "PROD": {
            "routerId": "10.100.0.1",
            "asn": "65100",
            "peers": {
                "10.100.0.2": {
                    "peerState": "Established",
                    "prefixReceived": 42,
                    "upDownTime": 999999,
                    "asn": "65101",
                },
            },
        }
    }
}

MOCK_EOS_ARP_TABLE = {
    "ipV4Neighbors": [
        {
            "address": "10.0.0.2",
            "hwAddress": "00:1c:73:cc:dd:01",
            "interface": "Ethernet1",
            "age": 120.5,
        },
        {
            "address": "10.0.0.3",
            "hwAddress": "00:1c:73:cc:dd:02",
            "interface": "Ethernet2",
            "age": 0,
        },
    ]
}

MOCK_EOS_LLDP_NEIGHBORS_DETAIL = {
    "lldpNeighbors": {
        "Ethernet1": {
            "lldpNeighborInfo": [
                {
                    "systemName": "spine-02.lab",
                    "neighborInterfaceInfo": {
                        "interfaceId_v2": "Ethernet49/1",
                        "interfaceId": "Ethernet49/1",
                    },
                    "systemDescription": "Arista Networks EOS version 4.32.1F",
                    "portId": "Ethernet49/1",
                }
            ]
        },
        "Ethernet2": {
            "lldpNeighborInfo": [
                {
                    "systemName": "leaf-01.lab",
                    "neighborInterfaceInfo": {
                        "interfaceId_v2": "Ethernet1",
                        "interfaceId": "Ethernet1",
                    },
                    "systemDescription": "Arista Networks EOS version 4.31.0F",
                    "portId": "Ethernet1",
                }
            ]
        },
    }
}

MOCK_EOS_RUNNING_CONFIG = {"output": "! device: spine-01\nhostname spine-01\ninterface Ethernet1\n"}
MOCK_EOS_STARTUP_CONFIG = {"output": "! device: spine-01\nhostname spine-01\n"}


# --- Helpers ---


@pytest.fixture
def eos_driver():
    """Create an EosDriver with a mocked pyeapi node."""
    driver = EosDriver()
    driver._node = MagicMock()  # noqa: SLF001
    return driver


def _setup_run_commands(driver: EosDriver, response: list[dict]):
    """Configure the mock node to return given responses for run_commands."""
    driver._node.run_commands.return_value = response  # noqa: SLF001


# --- Protocol compliance ---


class TestNetworkDriverProtocol:
    """Verify EosDriver satisfies the NetworkDriver protocol."""

    def test_eos_driver_is_network_driver(self):
        """EosDriver should be recognized as implementing NetworkDriver."""
        assert isinstance(EosDriver(), NetworkDriver)

    def test_eos_driver_platform(self):
        driver = EosDriver()
        assert driver.platform == "eos"

    def test_eos_driver_vendor(self):
        driver = EosDriver()
        assert driver.vendor == "arista"

    def test_eos_driver_vendor_api_version(self):
        assert EosDriver.VENDOR_API_VERSION == "eapi-1.0"


# --- get_facts ---


class TestGetFacts:
    """Test EosDriver.get_facts() normalization."""

    def test_schema_keys(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_SHOW_VERSION])
        facts = eos_driver.get_facts()
        required_keys = {"hostname", "model", "version", "serial", "uptime_seconds", "vendor", "platform"}
        assert set(facts.keys()) == required_keys

    def test_values(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_SHOW_VERSION])
        facts = eos_driver.get_facts()
        assert facts["hostname"] == "spine-01"
        assert facts["model"] == "DCS-7050TX3-48C8"
        assert facts["version"] == "4.32.1F"
        assert facts["serial"] == "SSJ17856312"
        assert facts["uptime_seconds"] == 864000
        assert facts["vendor"] == "arista"
        assert facts["platform"] == "eos"

    def test_types(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_SHOW_VERSION])
        facts = eos_driver.get_facts()
        assert isinstance(facts["hostname"], str)
        assert isinstance(facts["model"], str)
        assert isinstance(facts["version"], str)
        assert isinstance(facts["serial"], str)
        assert isinstance(facts["uptime_seconds"], int)
        assert isinstance(facts["vendor"], str)
        assert isinstance(facts["platform"], str)

    def test_missing_fields_default(self, eos_driver):
        """Gracefully handle missing keys in EOS output."""
        _setup_run_commands(eos_driver, [{"version": "4.32.1F"}])
        facts = eos_driver.get_facts()
        assert facts["hostname"] == ""
        assert facts["model"] == ""
        assert facts["serial"] == ""
        assert facts["uptime_seconds"] == 0


# --- get_interfaces ---


class TestGetInterfaces:
    """Test EosDriver.get_interfaces() normalization."""

    def test_schema_keys(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_SHOW_INTERFACES])
        interfaces = eos_driver.get_interfaces()
        assert "Ethernet1" in interfaces
        assert "Ethernet2" in interfaces
        assert "Management1" in interfaces
        required_keys = {"is_up", "is_enabled", "description", "speed", "mtu", "mac_address"}
        for intf_data in interfaces.values():
            assert set(intf_data.keys()) == required_keys

    def test_up_interface(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_SHOW_INTERFACES])
        interfaces = eos_driver.get_interfaces()
        eth1 = interfaces["Ethernet1"]
        assert eth1["is_up"] is True
        assert eth1["is_enabled"] is True
        assert eth1["description"] == "uplink to spine-02"
        assert eth1["speed"] == 100000  # 100Gbps = 100000 Mbps
        assert eth1["mtu"] == 9214
        assert eth1["mac_address"] == "00:1c:73:aa:bb:01"

    def test_down_disabled_interface(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_SHOW_INTERFACES])
        interfaces = eos_driver.get_interfaces()
        eth2 = interfaces["Ethernet2"]
        assert eth2["is_up"] is False
        assert eth2["is_enabled"] is False
        assert eth2["description"] == ""
        assert eth2["speed"] == 1000  # 1Gbps
        assert eth2["mtu"] == 1500

    def test_types(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_SHOW_INTERFACES])
        interfaces = eos_driver.get_interfaces()
        for intf_data in interfaces.values():
            assert isinstance(intf_data["is_up"], bool)
            assert isinstance(intf_data["is_enabled"], bool)
            assert isinstance(intf_data["description"], str)
            assert isinstance(intf_data["speed"], int)
            assert isinstance(intf_data["mtu"], int)
            assert isinstance(intf_data["mac_address"], str)

    def test_empty_interfaces(self, eos_driver):
        _setup_run_commands(eos_driver, [{"interfaces": {}}])
        interfaces = eos_driver.get_interfaces()
        assert interfaces == {}


# --- get_bgp_summary ---


class TestGetBgpSummary:
    """Test EosDriver.get_bgp_summary() normalization."""

    def test_schema_keys(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_BGP_SUMMARY])
        bgp = eos_driver.get_bgp_summary()
        assert set(bgp.keys()) == {"router_id", "as_number", "peers"}
        assert isinstance(bgp["peers"], dict)

    def test_peer_schema(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_BGP_SUMMARY])
        bgp = eos_driver.get_bgp_summary()
        required_peer_keys = {"state", "prefixes_received", "uptime"}
        for peer_data in bgp["peers"].values():
            assert set(peer_data.keys()) == required_peer_keys

    def test_values(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_BGP_SUMMARY])
        bgp = eos_driver.get_bgp_summary()
        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65000
        assert len(bgp["peers"]) == 2
        assert bgp["peers"]["10.0.0.2"]["state"] == "Established"
        assert bgp["peers"]["10.0.0.2"]["prefixes_received"] == 150
        assert bgp["peers"]["10.0.0.3"]["state"] == "Active"
        assert bgp["peers"]["10.0.0.3"]["prefixes_received"] == 0

    def test_vrf_query(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_BGP_SUMMARY_VRF])
        bgp = eos_driver.get_bgp_summary(vrf="PROD")
        assert bgp["router_id"] == "10.100.0.1"
        assert bgp["as_number"] == 65100
        assert "10.100.0.2" in bgp["peers"]

    def test_types(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_BGP_SUMMARY])
        bgp = eos_driver.get_bgp_summary()
        assert isinstance(bgp["router_id"], str)
        assert isinstance(bgp["as_number"], int)
        for peer_data in bgp["peers"].values():
            assert isinstance(peer_data["state"], str)
            assert isinstance(peer_data["prefixes_received"], int)
            assert isinstance(peer_data["uptime"], int)

    def test_empty_vrf(self, eos_driver):
        """No data for requested VRF returns empty peers."""
        _setup_run_commands(eos_driver, [{"vrfs": {}}])
        bgp = eos_driver.get_bgp_summary(vrf="NONEXISTENT")
        assert bgp["router_id"] == ""
        assert bgp["as_number"] == 0
        assert bgp["peers"] == {}


# --- get_arp_table ---


class TestGetArpTable:
    """Test EosDriver.get_arp_table() normalization."""

    def test_schema_keys(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_ARP_TABLE])
        arp = eos_driver.get_arp_table()
        assert isinstance(arp, list)
        assert len(arp) == 2
        required_keys = {"ip", "mac", "interface", "age"}
        for entry in arp:
            assert set(entry.keys()) == required_keys

    def test_values(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_ARP_TABLE])
        arp = eos_driver.get_arp_table()
        assert arp[0]["ip"] == "10.0.0.2"
        assert arp[0]["mac"] == "00:1c:73:cc:dd:01"
        assert arp[0]["interface"] == "Ethernet1"
        assert arp[0]["age"] == 120.5
        assert arp[1]["ip"] == "10.0.0.3"

    def test_types(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_ARP_TABLE])
        arp = eos_driver.get_arp_table()
        for entry in arp:
            assert isinstance(entry["ip"], str)
            assert isinstance(entry["mac"], str)
            assert isinstance(entry["interface"], str)
            assert isinstance(entry["age"], float)

    def test_empty_arp_table(self, eos_driver):
        _setup_run_commands(eos_driver, [{"ipV4Neighbors": []}])
        arp = eos_driver.get_arp_table()
        assert arp == []


# --- get_lldp_neighbors ---


class TestGetLldpNeighbors:
    """Test EosDriver.get_lldp_neighbors() normalization."""

    def test_schema_keys(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_LLDP_NEIGHBORS_DETAIL])
        lldp = eos_driver.get_lldp_neighbors()
        assert isinstance(lldp, dict)
        assert "Ethernet1" in lldp
        assert "Ethernet2" in lldp
        required_keys = {"hostname", "port", "system_description"}
        for neighbors in lldp.values():
            assert isinstance(neighbors, list)
            for n in neighbors:
                assert set(n.keys()) == required_keys

    def test_values(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_LLDP_NEIGHBORS_DETAIL])
        lldp = eos_driver.get_lldp_neighbors()
        eth1_neighbors = lldp["Ethernet1"]
        assert len(eth1_neighbors) == 1
        assert eth1_neighbors[0]["hostname"] == "spine-02.lab"
        assert eth1_neighbors[0]["port"] == "Ethernet49/1"
        assert "Arista" in eth1_neighbors[0]["system_description"]

    def test_types(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_LLDP_NEIGHBORS_DETAIL])
        lldp = eos_driver.get_lldp_neighbors()
        for neighbors in lldp.values():
            for n in neighbors:
                assert isinstance(n["hostname"], str)
                assert isinstance(n["port"], str)
                assert isinstance(n["system_description"], str)

    def test_empty_lldp(self, eos_driver):
        _setup_run_commands(eos_driver, [{"lldpNeighbors": {}}])
        lldp = eos_driver.get_lldp_neighbors()
        assert lldp == {}

    def test_lldp_list_format(self, eos_driver):
        """Handle empty list format from conftest MOCK_LLDP_NEIGHBORS."""
        _setup_run_commands(eos_driver, [{"lldpNeighbors": []}])
        lldp = eos_driver.get_lldp_neighbors()
        assert lldp == {}


# --- get_config ---


class TestGetConfig:
    """Test EosDriver.get_config() normalization."""

    def test_schema_keys(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_RUNNING_CONFIG, MOCK_EOS_STARTUP_CONFIG])
        config = eos_driver.get_config()
        assert set(config.keys()) == {"running", "startup"}

    def test_values(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_RUNNING_CONFIG, MOCK_EOS_STARTUP_CONFIG])
        config = eos_driver.get_config()
        assert "hostname spine-01" in config["running"]
        assert "hostname spine-01" in config["startup"]

    def test_types(self, eos_driver):
        _setup_run_commands(eos_driver, [MOCK_EOS_RUNNING_CONFIG, MOCK_EOS_STARTUP_CONFIG])
        config = eos_driver.get_config()
        assert isinstance(config["running"], str)
        assert isinstance(config["startup"], str)

    def test_sanitized_by_default(self, eos_driver):
        """Config should sanitize passwords by default."""
        config_with_password = {"output": "hostname spine-01\nusername admin secret sha512 $6$abc123\n"}
        startup_with_password = {"output": "hostname spine-01\nusername admin secret sha512 $6$abc123\n"}
        _setup_run_commands(eos_driver, [config_with_password, startup_with_password])
        config = eos_driver.get_config(sanitized=True)
        assert "$6$abc123" not in config["running"]
        assert "***HASHED***" in config["running"]

    def test_unsanitized(self, eos_driver):
        """Config with sanitized=False should preserve raw output."""
        config_with_password = {"output": "hostname spine-01\nusername admin secret sha512 $6$abc123\n"}
        startup_clean = {"output": "hostname spine-01\n"}
        _setup_run_commands(eos_driver, [config_with_password, startup_clean])
        config = eos_driver.get_config(sanitized=False)
        assert "$6$abc123" in config["running"]


# --- Abstraction overhead benchmark baseline ---


class TestNormalizationOverhead:
    """Measure normalization overhead vs raw run_show().

    These tests establish a baseline for the abstraction cost. The normalized
    getters add a Python-level transformation on top of run_show(). The overhead
    should be negligible compared to network I/O (typically <1ms for in-memory
    mock data transformation).
    """

    def test_get_facts_overhead(self, eos_driver):
        """Benchmark: get_facts() overhead vs direct run_show()."""
        _setup_run_commands(eos_driver, [MOCK_EOS_SHOW_VERSION])

        iterations = 1000

        # Measure raw run_show
        start = time.perf_counter()
        for _ in range(iterations):
            eos_driver.run_show(["show version"])
        raw_elapsed = time.perf_counter() - start

        # Measure get_facts (run_show + normalization)
        start = time.perf_counter()
        for _ in range(iterations):
            eos_driver.get_facts()
        normalized_elapsed = time.perf_counter() - start

        overhead_ms = ((normalized_elapsed - raw_elapsed) / iterations) * 1000
        # Normalization overhead should be <1ms per call for simple transforms
        assert overhead_ms < 1.0, f"Normalization overhead too high: {overhead_ms:.3f}ms per call"

    def test_get_interfaces_overhead(self, eos_driver):
        """Benchmark: get_interfaces() overhead vs direct run_show()."""
        _setup_run_commands(eos_driver, [MOCK_EOS_SHOW_INTERFACES])

        iterations = 1000

        start = time.perf_counter()
        for _ in range(iterations):
            eos_driver.run_show(["show interfaces"])
        raw_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        for _ in range(iterations):
            eos_driver.get_interfaces()
        normalized_elapsed = time.perf_counter() - start

        overhead_ms = ((normalized_elapsed - raw_elapsed) / iterations) * 1000
        assert overhead_ms < 1.0, f"Normalization overhead too high: {overhead_ms:.3f}ms per call"


# ============================================================================
# Cross-vendor 4-driver parameterized normalization tests
# ============================================================================


from tests.mock_data.eos.mock_driver import MockEosDriver  # noqa: E402
from tests.mock_data.iosxe.mock_driver import MockIosXeDriver  # noqa: E402
from tests.mock_data.junos.mock_driver import MockJunosDriver  # noqa: E402
from tests.mock_data.nxos.mock_driver import MockNxosDriver  # noqa: E402

ALL_DRIVERS = [MockEosDriver(), MockIosXeDriver(), MockNxosDriver(), MockJunosDriver()]
DRIVER_IDS = ["eos", "iosxe", "nxos", "junos"]


@pytest.fixture(params=ALL_DRIVERS, ids=DRIVER_IDS)
def mock_driver(request):
    """Parameterized fixture providing all 4 vendor mock drivers."""
    driver = request.param
    driver.connect()
    yield driver
    driver.close()


class TestCrossVendorGetFacts:
    """Verify get_facts() output schema across all 4 vendors."""

    def test_required_keys(self, mock_driver):
        facts = mock_driver.get_facts()
        required = {"hostname", "model", "version", "serial", "uptime_seconds", "vendor", "platform"}
        assert set(facts.keys()) == required

    def test_types(self, mock_driver):
        facts = mock_driver.get_facts()
        assert isinstance(facts["hostname"], str)
        assert isinstance(facts["model"], str)
        assert isinstance(facts["version"], str)
        assert isinstance(facts["serial"], str)
        assert isinstance(facts["uptime_seconds"], int)
        assert isinstance(facts["vendor"], str)
        assert isinstance(facts["platform"], str)

    def test_non_empty_hostname(self, mock_driver):
        facts = mock_driver.get_facts()
        assert facts["hostname"] != ""

    def test_non_empty_version(self, mock_driver):
        facts = mock_driver.get_facts()
        assert facts["version"] != ""

    def test_vendor_matches_driver(self, mock_driver):
        facts = mock_driver.get_facts()
        assert facts["vendor"] == mock_driver.vendor
        assert facts["platform"] == mock_driver.platform


class TestCrossVendorGetInterfaces:
    """Verify get_interfaces() output schema across all 4 vendors."""

    def test_returns_dict(self, mock_driver):
        interfaces = mock_driver.get_interfaces()
        assert isinstance(interfaces, dict)
        assert len(interfaces) > 0

    def test_interface_schema(self, mock_driver):
        interfaces = mock_driver.get_interfaces()
        required = {"is_up", "is_enabled", "description", "speed", "mtu", "mac_address"}
        for name, intf_data in interfaces.items():
            assert set(intf_data.keys()) == required, f"Interface {name} has wrong keys"

    def test_types(self, mock_driver):
        interfaces = mock_driver.get_interfaces()
        for name, intf_data in interfaces.items():
            assert isinstance(intf_data["is_up"], bool), f"{name}: is_up"
            assert isinstance(intf_data["is_enabled"], bool), f"{name}: is_enabled"
            assert isinstance(intf_data["description"], str), f"{name}: description"
            assert isinstance(intf_data["speed"], int), f"{name}: speed"
            assert isinstance(intf_data["mtu"], int), f"{name}: mtu"
            assert isinstance(intf_data["mac_address"], str), f"{name}: mac_address"

    def test_has_up_and_down_interfaces(self, mock_driver):
        """Mock data should contain at least one up and one down interface."""
        interfaces = mock_driver.get_interfaces()
        states = [v["is_up"] for v in interfaces.values()]
        assert True in states, "Expected at least one up interface"
        assert False in states, "Expected at least one down interface"


class TestCrossVendorGetBgpSummary:
    """Verify get_bgp_summary() output schema across all 4 vendors."""

    def test_required_keys(self, mock_driver):
        bgp = mock_driver.get_bgp_summary()
        assert set(bgp.keys()) == {"router_id", "as_number", "peers"}

    def test_types(self, mock_driver):
        bgp = mock_driver.get_bgp_summary()
        assert isinstance(bgp["router_id"], str)
        assert isinstance(bgp["as_number"], int)
        assert isinstance(bgp["peers"], dict)

    def test_peer_schema(self, mock_driver):
        bgp = mock_driver.get_bgp_summary()
        required_peer_keys = {"state", "prefixes_received", "uptime"}
        for ip, peer_data in bgp["peers"].items():
            assert set(peer_data.keys()) == required_peer_keys, f"Peer {ip} has wrong keys"

    def test_peer_types(self, mock_driver):
        bgp = mock_driver.get_bgp_summary()
        for ip, peer_data in bgp["peers"].items():
            assert isinstance(peer_data["state"], str), f"Peer {ip}: state"
            assert isinstance(peer_data["prefixes_received"], int), f"Peer {ip}: prefixes_received"
            assert isinstance(peer_data["uptime"], int), f"Peer {ip}: uptime"

    def test_has_established_peer(self, mock_driver):
        bgp = mock_driver.get_bgp_summary()
        states = [p["state"].lower() for p in bgp["peers"].values()]
        assert "established" in states, "Expected at least one Established peer"


class TestCrossVendorGetArpTable:
    """Verify get_arp_table() output schema across all 4 vendors."""

    def test_returns_list(self, mock_driver):
        arp = mock_driver.get_arp_table()
        assert isinstance(arp, list)
        assert len(arp) > 0

    def test_entry_schema(self, mock_driver):
        arp = mock_driver.get_arp_table()
        required = {"ip", "mac", "interface", "age"}
        for entry in arp:
            assert set(entry.keys()) == required

    def test_types(self, mock_driver):
        arp = mock_driver.get_arp_table()
        for entry in arp:
            assert isinstance(entry["ip"], str)
            assert isinstance(entry["mac"], str)
            assert isinstance(entry["interface"], str)
            assert isinstance(entry["age"], float)


class TestCrossVendorGetLldpNeighbors:
    """Verify get_lldp_neighbors() output schema across all 4 vendors."""

    def test_returns_dict(self, mock_driver):
        lldp = mock_driver.get_lldp_neighbors()
        assert isinstance(lldp, dict)
        assert len(lldp) > 0

    def test_neighbor_schema(self, mock_driver):
        lldp = mock_driver.get_lldp_neighbors()
        required = {"hostname", "port", "system_description"}
        for intf, neighbors in lldp.items():
            assert isinstance(neighbors, list), f"{intf}: neighbors should be a list"
            for n in neighbors:
                assert set(n.keys()) == required, f"{intf}: neighbor has wrong keys"

    def test_types(self, mock_driver):
        lldp = mock_driver.get_lldp_neighbors()
        for intf, neighbors in lldp.items():
            for n in neighbors:
                assert isinstance(n["hostname"], str), f"{intf}: hostname"
                assert isinstance(n["port"], str), f"{intf}: port"
                assert isinstance(n["system_description"], str), f"{intf}: system_description"


class TestCrossVendorGetConfig:
    """Verify get_config() output schema across all 4 vendors."""

    def test_required_keys(self, mock_driver):
        config = mock_driver.get_config()
        assert set(config.keys()) == {"running", "startup"}

    def test_types(self, mock_driver):
        config = mock_driver.get_config()
        assert isinstance(config["running"], str)
        assert isinstance(config["startup"], str)

    def test_non_empty_running(self, mock_driver):
        config = mock_driver.get_config()
        assert config["running"] != ""


# ============================================================================
# MAC address normalization
# ============================================================================


class TestNormalizeMac:
    """Test normalize_mac across all vendor formats."""

    def test_colon_lowercase(self):
        assert normalize_mac("aa:bb:cc:dd:ee:ff") == "aa:bb:cc:dd:ee:ff"

    def test_colon_uppercase(self):
        assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"

    def test_colon_mixed_case(self):
        assert normalize_mac("Aa:Bb:Cc:Dd:Ee:Ff") == "aa:bb:cc:dd:ee:ff"

    def test_cisco_dot_notation(self):
        assert normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"

    def test_cisco_dot_notation_uppercase(self):
        assert normalize_mac("AABB.CCDD.EEFF") == "aa:bb:cc:dd:ee:ff"

    def test_hyphen_notation(self):
        assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"

    def test_hyphen_notation_lowercase(self):
        assert normalize_mac("aa-bb-cc-dd-ee-ff") == "aa:bb:cc:dd:ee:ff"

    def test_empty_string(self):
        assert normalize_mac("") == ""

    def test_invalid_mac_too_short(self):
        assert normalize_mac("aa:bb:cc") == "aa:bb:cc"

    def test_invalid_mac_too_long(self):
        assert normalize_mac("aa:bb:cc:dd:ee:ff:00") == "aa:bb:cc:dd:ee:ff:00"

    def test_real_eos_mac(self):
        assert normalize_mac("00:1c:73:3a:b4:01") == "00:1c:73:3a:b4:01"

    def test_real_cisco_mac(self):
        assert normalize_mac("0050.56a0.0001") == "00:50:56:a0:00:01"

    def test_no_separators(self):
        assert normalize_mac("aabbccddeeff") == "aa:bb:cc:dd:ee:ff"


# ============================================================================
# Speed normalization
# ============================================================================


class TestNormalizeSpeed:
    """Test normalize_speed across vendor representations."""

    def test_numeric_1000(self):
        assert normalize_speed("1000") == "1Gbps"

    def test_numeric_10000(self):
        assert normalize_speed("10000") == "10Gbps"

    def test_numeric_100(self):
        assert normalize_speed("100") == "100Mbps"

    def test_numeric_25000(self):
        assert normalize_speed("25000") == "25Gbps"

    def test_numeric_40000(self):
        assert normalize_speed("40000") == "40Gbps"

    def test_numeric_100000(self):
        assert normalize_speed("100000") == "100Gbps"

    def test_mbps_suffix(self):
        assert normalize_speed("1000Mbps") == "1Gbps"

    def test_gbps_suffix(self):
        assert normalize_speed("10Gbps") == "10Gbps"

    def test_g_suffix(self):
        assert normalize_speed("10G") == "10Gbps"

    def test_auto(self):
        assert normalize_speed("auto") == "auto"

    def test_auto_uppercase(self):
        assert normalize_speed("Auto") == "auto"

    def test_empty_string(self):
        assert normalize_speed("") == "unknown"

    def test_unknown_value_passthrough(self):
        assert normalize_speed("weird-speed") == "weird-speed"

    def test_whitespace_handling(self):
        assert normalize_speed("  1000  ") == "1Gbps"

    def test_400g(self):
        assert normalize_speed("400G") == "400Gbps"

    def test_10mbps(self):
        assert normalize_speed("10Mbps") == "10Mbps"


# ============================================================================
# Schema validation (model_validator)
# ============================================================================


class TestDeviceFactsValidation:
    """Test DeviceFacts model validation."""

    def test_valid_facts(self):
        facts = DeviceFacts(hostname="spine-01", model="DCS-7280SR", version="4.31.1F")
        assert facts.hostname == "spine-01"

    def test_empty_hostname_raises(self):
        with pytest.raises(ValueError, match="hostname must not be empty"):
            DeviceFacts(hostname="", model="test")

    def test_defaults(self):
        facts = DeviceFacts(hostname="sw-01")
        assert facts.serial_number == ""
        assert facts.version == ""
        assert facts.uptime_seconds == 0
        assert facts.platform == "unknown"
        assert facts.vendor == "unknown"

    def test_all_fields(self):
        facts = DeviceFacts(
            hostname="spine-01",
            model="DCS-7280SR-48C6",
            serial_number="SSJ17310456",
            version="4.31.1F",
            uptime_seconds=864000,
            platform="eos",
            vendor="arista",
        )
        assert facts.model == "DCS-7280SR-48C6"
        assert facts.uptime_seconds == 864000


class TestInterfaceEntryValidation:
    """Test InterfaceEntry model."""

    def test_defaults(self):
        entry = InterfaceEntry()
        assert entry.is_up is False
        assert entry.speed == "unknown"
        assert entry.mtu == 0
        assert entry.mac_address == ""

    def test_populated(self):
        entry = InterfaceEntry(is_up=True, is_enabled=True, speed="1Gbps", mtu=9214, mac_address="aa:bb:cc:dd:ee:ff")
        assert entry.is_up is True
        assert entry.speed == "1Gbps"


class TestArpEntryValidation:
    """Test ArpEntry model validation."""

    def test_valid(self):
        entry = ArpEntry(ip="10.0.0.1", mac="aa:bb:cc:dd:ee:ff", interface="Ethernet1")
        assert entry.ip == "10.0.0.1"

    def test_empty_ip_raises(self):
        with pytest.raises(ValueError, match="ip must not be empty"):
            ArpEntry(ip="", mac="aa:bb:cc:dd:ee:ff")

    def test_defaults(self):
        entry = ArpEntry(ip="10.0.0.1")
        assert entry.mac == ""
        assert entry.age == -1.0


class TestBgpPeerEntryValidation:
    """Test BgpPeerEntry model."""

    def test_defaults(self):
        entry = BgpPeerEntry()
        assert entry.state == "Unknown"
        assert entry.prefixes_received == 0
        assert entry.uptime == 0


# ============================================================================
# Golden fixture — EOS driver (using fixtures directory)
# ============================================================================


class TestEosGoldenFixture:
    """Test EOS driver normalization using golden fixture data."""

    @pytest.fixture()
    def fixture_data(self):
        with open(FIXTURES_DIR / "eos_show_version.json") as f:
            return json.load(f)

    def test_get_facts(self, fixture_data):
        """EOS get_facts produces correct normalized output."""
        driver = EosDriver()
        driver._node = MagicMock()
        driver._node.run_commands.return_value = [fixture_data]
        driver._last_command_time = 0.0

        with patch.object(driver, "_check_alive"):
            facts = driver.get_facts()

        assert facts["hostname"] == "eos-spine-01"
        assert facts["model"] == "DCS-7280SR-48C6"
        assert facts["version"] == "4.31.1F"
        assert facts["serial"] == "SSJ17310456"
        assert facts["uptime_seconds"] == 8640000
        assert facts["vendor"] == "arista"
        assert facts["platform"] == "eos"
        DeviceFacts(
            hostname=facts["hostname"],
            model=facts["model"],
            serial_number=facts["serial"],
            version=facts["version"],
            uptime_seconds=facts["uptime_seconds"],
            platform=facts["platform"],
            vendor=facts["vendor"],
        )

    def test_get_interfaces_mac_normalization(self, fixture_data):
        """EOS get_interfaces normalizes MAC addresses."""
        driver = EosDriver()
        driver._node = MagicMock()
        driver._node.run_commands.return_value = [fixture_data]

        with patch.object(driver, "_check_alive"):
            interfaces = driver.get_interfaces()

        eth1 = interfaces["Ethernet1"]
        assert eth1["mac_address"] == "aa:bb:cc:dd:ee:01"
        eth2 = interfaces["Ethernet2"]
        assert eth2["mac_address"] == "aa:bb:cc:dd:ee:02"

    def test_get_arp_table_mac_normalization(self, fixture_data):
        """EOS get_arp_table normalizes MAC addresses."""
        driver = EosDriver()
        driver._node = MagicMock()
        driver._node.run_commands.return_value = [fixture_data]

        with patch.object(driver, "_check_alive"):
            arp = driver.get_arp_table()

        assert arp[0]["mac"] == "aa:bb:cc:dd:ee:10"
        assert arp[1]["mac"] == "aa:bb:cc:dd:ee:11"


# ============================================================================
# Golden fixture — IOS-XE driver
# ============================================================================


class TestIosXeGoldenFixture:
    """Test IOS-XE driver normalization using golden fixture data."""

    @pytest.fixture()
    def fixture_data(self):
        with open(FIXTURES_DIR / "iosxe_restconf_interfaces.json") as f:
            return json.load(f)

    def test_normalize_facts_restconf(self, fixture_data):
        """IOS-XE RESTCONF facts normalization."""
        native = fixture_data["Cisco-IOS-XE-native:native"]
        facts = IosXeDriver._normalize_facts_restconf(IosXeDriver(), native)
        assert facts["hostname"] == "iosxe-rtr-01"
        assert facts["model"] == "C8300-2N2S-4T2X"
        assert facts["serial"] == "FDO24360AP3"
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "iosxe"
        DeviceFacts(
            hostname=facts["hostname"],
            model=facts["model"],
            serial_number=facts["serial"],
            version=facts["version"],
            platform=facts["platform"],
            vendor=facts["vendor"],
        )

    def test_normalize_interfaces_restconf(self, fixture_data):
        """IOS-XE RESTCONF interface normalization with MAC normalization."""
        raw = fixture_data["ietf-interfaces:interfaces"]["interface"]
        interfaces = IosXeDriver._normalize_interfaces(raw)

        ge1 = interfaces["GigabitEthernet1"]
        assert ge1["is_up"] is True
        # Cisco dot-notation MAC should be normalized
        assert ge1["mac_address"] == "00:50:56:a0:00:01"
        ge2 = interfaces["GigabitEthernet2"]
        assert ge2["mac_address"] == "00:50:56:a0:00:02"

    def test_normalize_arp_restconf(self, fixture_data):
        """IOS-XE RESTCONF ARP normalization with mixed MAC formats."""
        arp = IosXeDriver._normalize_arp_restconf(fixture_data)
        assert len(arp) == 2
        assert arp[0]["mac"] == "00:50:56:a0:00:10"
        assert arp[1]["mac"] == "aa:bb:cc:dd:ee:ff"

    def test_normalize_facts_ssh(self, fixture_data):
        """IOS-XE SSH facts parsing."""
        facts = IosXeDriver._normalize_facts_ssh(fixture_data["show_version_ssh"])
        assert facts["version"] == "17.09.04a"
        assert facts["model"] == "C8300-2N2S-4T2X"
        assert facts["serial"] == "FDO24360AP3"
        assert facts["uptime_seconds"] > 0


# ============================================================================
# Golden fixture — NX-OS driver
# ============================================================================


class TestNxosGoldenFixture:
    """Test NX-OS driver normalization using golden fixture data."""

    @pytest.fixture()
    def fixture_data(self):
        with open(FIXTURES_DIR / "nxos_nxapi_show_version.json") as f:
            return json.load(f)

    def test_normalize_facts_nxapi(self, fixture_data):
        """NX-OS NX-API facts normalization."""
        driver = NxosDriver()
        facts = driver._normalize_facts_nxapi(fixture_data["show_version"])
        assert facts["hostname"] == "nxos-leaf-01"
        assert facts["model"] == "Nexus9000 C93180YC-FX"
        assert facts["version"] == "10.3(2)"
        assert facts["serial"] == "FDO24511LJB"
        assert facts["uptime_seconds"] == (100 * 86400) + (5 * 3600) + (30 * 60) + 15
        DeviceFacts(
            hostname=facts["hostname"],
            model=facts["model"],
            serial_number=facts["serial"],
            version=facts["version"],
            uptime_seconds=facts["uptime_seconds"],
            platform=facts["platform"],
            vendor=facts["vendor"],
        )

    def test_normalize_interfaces_nxapi(self, fixture_data):
        """NX-OS NX-API interface normalization with MAC normalization."""
        interfaces = NxosDriver._normalize_interfaces_nxapi(fixture_data["show_interface"])

        eth1 = interfaces["Ethernet1/1"]
        assert eth1["mac_address"] == "aa:bb:cc:dd:ee:01"

        eth2 = interfaces["Ethernet1/2"]
        assert eth2["mac_address"] == "aa:bb:cc:dd:ee:02"

    def test_normalize_arp_nxapi(self, fixture_data):
        """NX-OS NX-API ARP normalization with mixed MAC formats."""
        arp = NxosDriver._normalize_arp_nxapi(fixture_data["show_ip_arp"])
        assert len(arp) == 2
        assert arp[0]["mac"] == "aa:bb:cc:dd:00:10"
        assert arp[1]["mac"] == "aa:bb:cc:dd:00:11"

    def test_normalize_lldp_nxapi(self, fixture_data):
        """NX-OS NX-API LLDP normalization."""
        lldp = NxosDriver._normalize_lldp_nxapi(fixture_data["show_lldp_neighbors_detail"])
        assert "Ethernet1/1" in lldp
        assert lldp["Ethernet1/1"][0]["hostname"] == "spine-01.lab"


# ============================================================================
# Golden fixture — JunOS driver
# ============================================================================


class TestJunosGoldenFixture:
    """Test JunOS driver normalization using golden fixture data."""

    @pytest.fixture()
    def fixture_data(self):
        with open(FIXTURES_DIR / "junos_netconf_facts.json") as f:
            return json.load(f)

    def test_normalize_facts_netconf(self, fixture_data):
        """JunOS NETCONF facts normalization."""
        facts = JunosDriver._normalize_facts(fixture_data["system_information_xml"])
        assert facts["hostname"] == "junos-spine-01"
        assert facts["model"] == "QFX5120-48T"
        assert facts["version"] == "23.4R1.10"
        assert facts["serial"] == "WS3718420042"
        assert facts["uptime_seconds"] == 864000
        DeviceFacts(
            hostname=facts["hostname"],
            model=facts["model"],
            serial_number=facts["serial"],
            version=facts["version"],
            uptime_seconds=facts["uptime_seconds"],
            platform=facts["platform"],
            vendor=facts["vendor"],
        )

    def test_normalize_interfaces_netconf(self, fixture_data):
        """JunOS NETCONF interface normalization with MAC normalization."""
        interfaces = JunosDriver._normalize_interfaces(fixture_data["interface_information_xml"])

        ge0 = interfaces["ge-0/0/0"]
        assert ge0["mac_address"] == "00:1c:73:aa:bb:01"

        ge1 = interfaces["ge-0/0/1"]
        # Dot-notation MAC → colon-format
        assert ge1["mac_address"] == "00:1c:73:aa:bb:02"

    def test_normalize_arp_netconf(self, fixture_data):
        """JunOS NETCONF ARP normalization with mixed MAC formats."""
        arp = JunosDriver._normalize_arp_table(fixture_data["arp_table_xml"])
        assert len(arp) == 2
        assert arp[0]["mac"] == "00:1c:73:cc:dd:01"
        assert arp[1]["mac"] == "00:1c:73:cc:dd:02"

    def test_normalize_bgp_netconf(self, fixture_data):
        """JunOS NETCONF BGP summary normalization."""
        bgp = JunosDriver._normalize_bgp_summary(fixture_data["bgp_summary_xml"])
        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65100
        assert bgp["peers"]["10.0.0.2"]["state"] == "Established"
        assert bgp["peers"]["10.0.0.2"]["prefixes_received"] == 150
        assert bgp["peers"]["10.0.0.3"]["state"] == "Active"

    def test_normalize_lldp_netconf(self, fixture_data):
        """JunOS NETCONF LLDP normalization."""
        lldp = JunosDriver._normalize_lldp_neighbors(fixture_data["lldp_neighbors_xml"])
        assert lldp["ge-0/0/0"][0]["hostname"] == "leaf-01.lab"
        assert lldp["ge-0/0/0"][0]["port"] == "xe-0/0/0"

    def test_normalize_facts_ssh(self, fixture_data):
        """JunOS SSH facts parsing."""
        from network_mcp.drivers.juniper_junos import _parse_facts_ssh

        facts = _parse_facts_ssh(fixture_data["show_version_ssh"])
        assert facts["hostname"] == "junos-spine-01"
        assert facts["model"] == "qfx5120-48t"
        assert facts["version"] == "23.4R1.10"
        assert facts["serial"] == "WS3718420042"

    def test_normalize_interfaces_ssh(self, fixture_data):
        """JunOS SSH interface parsing skips logical interfaces."""
        from network_mcp.drivers.juniper_junos import _parse_interfaces_ssh

        interfaces = _parse_interfaces_ssh(fixture_data["show_interfaces_terse_ssh"])
        assert "ge-0/0/0" in interfaces
        assert interfaces["ge-0/0/0"]["is_up"] is True
        assert "ge-0/0/0.0" not in interfaces

    def test_normalize_arp_ssh(self, fixture_data):
        """JunOS SSH ARP parsing with MAC normalization."""
        from network_mcp.drivers.juniper_junos import _parse_arp_ssh

        arp = _parse_arp_ssh(fixture_data["show_arp_ssh"])
        assert len(arp) == 2
        assert arp[0]["mac"] == "00:1c:73:cc:dd:01"
        assert arp[1]["mac"] == "00:1c:73:cc:dd:02"

    def test_normalize_bgp_ssh(self, fixture_data):
        """JunOS SSH BGP summary parsing."""
        from network_mcp.drivers.juniper_junos import _parse_bgp_summary_ssh

        bgp = _parse_bgp_summary_ssh(fixture_data["show_bgp_summary_ssh"])
        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65100
        assert bgp["peers"]["10.0.0.2"]["state"] == "Established"
        assert bgp["peers"]["10.0.0.2"]["prefixes_received"] == 150
        assert bgp["peers"]["10.0.0.3"]["state"] == "Active"


# ============================================================================
# Cross-vendor consistency — all drivers produce the same schema shape
# ============================================================================


class TestCrossVendorSchemaConsistency:
    """Verify all 4 drivers produce output compatible with the same schema models."""

    def test_all_mac_formats_produce_colon_notation(self):
        """All vendor MAC formats normalize to aa:bb:cc:dd:ee:ff."""
        test_macs = [
            "aa:bb:cc:dd:ee:ff",  # EOS
            "aabb.ccdd.eeff",  # Cisco
            "AA:BB:CC:DD:EE:FF",  # Uppercase
            "aa-bb-cc-dd-ee-ff",  # Hyphen
        ]
        expected = "aa:bb:cc:dd:ee:ff"
        for mac in test_macs:
            assert normalize_mac(mac) == expected, f"Failed for format: {mac}"
