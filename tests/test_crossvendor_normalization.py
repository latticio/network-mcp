"""Cross-vendor normalization tests.

Parameterized tests that verify all three vendor drivers (EOS, IOS-XE, NX-OS)
produce output conforming to the same normalized schema. Each test class
validates a single normalized getter across all drivers.

These tests check schema consistency, NOT data correctness (that is tested
in the individual driver test files).
"""

from __future__ import annotations

import pytest

from tests.mock_data.eos.mock_driver import MockEosDriver
from tests.mock_data.iosxe.mock_driver import MockIosXeDriver
from tests.mock_data.nxos.mock_driver import MockNxosDriver

# Create and connect all mock drivers
_eos = MockEosDriver()
_eos.connect()
_iosxe = MockIosXeDriver()
_iosxe.connect()
_nxos = MockNxosDriver()
_nxos.connect()

ALL_DRIVERS = [
    pytest.param(_eos, id="eos"),
    pytest.param(_iosxe, id="iosxe"),
    pytest.param(_nxos, id="nxos"),
]
DRIVER_IDS = ["eos", "iosxe", "nxos"]


@pytest.mark.parametrize("driver", ALL_DRIVERS)
class TestGetFactsSchema:
    """Verify get_facts() produces identical schema across all vendors."""

    def test_required_keys(self, driver):
        facts = driver.get_facts()
        required_keys = {"hostname", "model", "version", "serial", "uptime_seconds", "vendor", "platform"}
        assert set(facts.keys()) == required_keys

    def test_hostname_is_string(self, driver):
        facts = driver.get_facts()
        assert isinstance(facts["hostname"], str)

    def test_model_is_string(self, driver):
        facts = driver.get_facts()
        assert isinstance(facts["model"], str)

    def test_version_is_string(self, driver):
        facts = driver.get_facts()
        assert isinstance(facts["version"], str)

    def test_serial_is_string(self, driver):
        facts = driver.get_facts()
        assert isinstance(facts["serial"], str)

    def test_uptime_is_numeric(self, driver):
        facts = driver.get_facts()
        assert isinstance(facts["uptime_seconds"], (int, float))

    def test_vendor_is_valid(self, driver):
        facts = driver.get_facts()
        assert facts["vendor"] in ("arista", "cisco")

    def test_platform_is_valid(self, driver):
        facts = driver.get_facts()
        assert facts["platform"] in ("eos", "iosxe", "nxos")


@pytest.mark.parametrize("driver", ALL_DRIVERS)
class TestGetInterfacesSchema:
    """Verify get_interfaces() produces identical schema across all vendors."""

    def test_returns_dict(self, driver):
        interfaces = driver.get_interfaces()
        assert isinstance(interfaces, dict)

    def test_interface_keys_are_strings(self, driver):
        interfaces = driver.get_interfaces()
        for name in interfaces:
            assert isinstance(name, str)

    def test_interface_required_fields(self, driver):
        interfaces = driver.get_interfaces()
        required_fields = {"is_up", "is_enabled", "description", "speed", "mtu", "mac_address"}
        for name, data in interfaces.items():
            missing = required_fields - set(data.keys())
            assert set(data.keys()) >= required_fields, f"Interface {name} missing: {missing}"

    def test_is_up_is_bool(self, driver):
        interfaces = driver.get_interfaces()
        for name, data in interfaces.items():
            assert isinstance(data["is_up"], bool), f"Interface {name}: is_up should be bool"

    def test_is_enabled_is_bool(self, driver):
        interfaces = driver.get_interfaces()
        for name, data in interfaces.items():
            assert isinstance(data["is_enabled"], bool), f"Interface {name}: is_enabled should be bool"

    def test_description_is_string(self, driver):
        interfaces = driver.get_interfaces()
        for name, data in interfaces.items():
            assert isinstance(data["description"], str), f"Interface {name}: description should be str"

    def test_speed_is_int(self, driver):
        interfaces = driver.get_interfaces()
        for name, data in interfaces.items():
            assert isinstance(data["speed"], int), f"Interface {name}: speed should be int"

    def test_mtu_is_int(self, driver):
        interfaces = driver.get_interfaces()
        for name, data in interfaces.items():
            assert isinstance(data["mtu"], int), f"Interface {name}: mtu should be int"

    def test_mac_address_is_string(self, driver):
        interfaces = driver.get_interfaces()
        for name, data in interfaces.items():
            assert isinstance(data["mac_address"], str), f"Interface {name}: mac_address should be str"

    def test_at_least_one_interface(self, driver):
        interfaces = driver.get_interfaces()
        assert len(interfaces) > 0


@pytest.mark.parametrize("driver", ALL_DRIVERS)
class TestGetBgpSummarySchema:
    """Verify get_bgp_summary() produces identical schema across all vendors."""

    def test_required_keys(self, driver):
        bgp = driver.get_bgp_summary()
        assert set(bgp.keys()) == {"router_id", "as_number", "peers"}

    def test_router_id_is_string(self, driver):
        bgp = driver.get_bgp_summary()
        assert isinstance(bgp["router_id"], str)

    def test_as_number_is_int(self, driver):
        bgp = driver.get_bgp_summary()
        assert isinstance(bgp["as_number"], int)

    def test_peers_is_dict(self, driver):
        bgp = driver.get_bgp_summary()
        assert isinstance(bgp["peers"], dict)

    def test_peer_required_fields(self, driver):
        bgp = driver.get_bgp_summary()
        for peer_ip, peer_data in bgp["peers"].items():
            assert isinstance(peer_ip, str), "Peer key should be a string (IP address)"
            assert "state" in peer_data
            assert "prefixes_received" in peer_data
            assert "uptime" in peer_data

    def test_peer_field_types(self, driver):
        bgp = driver.get_bgp_summary()
        for peer_ip, peer_data in bgp["peers"].items():
            assert isinstance(peer_data["state"], str), f"Peer {peer_ip}: state should be str"
            assert isinstance(peer_data["prefixes_received"], int), f"Peer {peer_ip}: prefixes_received should be int"
            assert isinstance(peer_data["uptime"], (int, float)), f"Peer {peer_ip}: uptime should be numeric"


@pytest.mark.parametrize("driver", ALL_DRIVERS)
class TestGetArpTableSchema:
    """Verify get_arp_table() produces identical schema across all vendors."""

    def test_returns_list(self, driver):
        arp = driver.get_arp_table()
        assert isinstance(arp, list)

    def test_entry_required_fields(self, driver):
        arp = driver.get_arp_table()
        for entry in arp:
            assert "ip" in entry
            assert "mac" in entry
            assert "interface" in entry

    def test_ip_is_string(self, driver):
        arp = driver.get_arp_table()
        for entry in arp:
            assert isinstance(entry["ip"], str)

    def test_mac_is_string(self, driver):
        arp = driver.get_arp_table()
        for entry in arp:
            assert isinstance(entry["mac"], str)

    def test_interface_is_string(self, driver):
        arp = driver.get_arp_table()
        for entry in arp:
            assert isinstance(entry["interface"], str)

    def test_at_least_one_entry(self, driver):
        arp = driver.get_arp_table()
        assert len(arp) > 0


@pytest.mark.parametrize("driver", ALL_DRIVERS)
class TestGetLldpNeighborsSchema:
    """Verify get_lldp_neighbors() produces identical schema across all vendors."""

    def test_returns_dict(self, driver):
        lldp = driver.get_lldp_neighbors()
        assert isinstance(lldp, dict)

    def test_keys_are_interface_names(self, driver):
        lldp = driver.get_lldp_neighbors()
        for intf in lldp:
            assert isinstance(intf, str)

    def test_values_are_lists(self, driver):
        lldp = driver.get_lldp_neighbors()
        for intf, neighbors in lldp.items():
            assert isinstance(neighbors, list), f"Interface {intf}: neighbors should be a list"

    def test_neighbor_required_fields(self, driver):
        lldp = driver.get_lldp_neighbors()
        for intf, neighbors in lldp.items():
            for neighbor in neighbors:
                assert "hostname" in neighbor, f"Interface {intf}: neighbor missing 'hostname'"
                assert "port" in neighbor, f"Interface {intf}: neighbor missing 'port'"

    def test_neighbor_field_types(self, driver):
        lldp = driver.get_lldp_neighbors()
        for _intf, neighbors in lldp.items():
            for neighbor in neighbors:
                assert isinstance(neighbor["hostname"], str)
                assert isinstance(neighbor["port"], str)

    def test_at_least_one_interface(self, driver):
        lldp = driver.get_lldp_neighbors()
        assert len(lldp) > 0


@pytest.mark.parametrize("driver", ALL_DRIVERS)
class TestGetConfigSchema:
    """Verify get_config() produces identical schema across all vendors."""

    def test_required_keys(self, driver):
        config = driver.get_config()
        assert set(config.keys()) == {"running", "startup"}

    def test_running_is_string(self, driver):
        config = driver.get_config()
        assert isinstance(config["running"], str)

    def test_startup_is_string(self, driver):
        config = driver.get_config()
        assert isinstance(config["startup"], str)

    def test_running_not_empty(self, driver):
        config = driver.get_config()
        assert len(config["running"]) > 0

    def test_startup_not_empty(self, driver):
        config = driver.get_config()
        assert len(config["startup"]) > 0
