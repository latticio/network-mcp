"""Phase 2: Smoke tests — verify basic connectivity and eAPI responses.

These run first to confirm the lab is healthy before deeper tests.
Run with: uv run pytest tests/integration/test_smoke.py -v --run-integration
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]


class TestConnectivity:
    """Verify we can reach lab nodes at all."""

    def test_node_discovery(self, lab_nodes):
        """At least one lab node should be discovered."""
        assert len(lab_nodes) >= 1
        for node in lab_nodes:
            assert node["ip"], f"Node {node['name']} has no IP"

    def test_eapi_show_version(self, eapi_session):
        """Raw eAPI call to show version should return valid data."""
        result = eapi_session.run_commands(["show version"], encoding="json")
        assert len(result) == 1
        version_data = result[0]
        assert "version" in version_data
        assert "modelName" in version_data
        assert "serialNumber" in version_data

    def test_eapi_show_hostname(self, eapi_session):
        """Show hostname should return a hostname string."""
        result = eapi_session.run_commands(["show hostname"], encoding="json")
        assert len(result) == 1
        assert "hostname" in result[0]

    def test_eapi_multiple_commands(self, eapi_session):
        """Multiple commands in a single eAPI call should all return results."""
        cmds = ["show version", "show hostname", "show interfaces status"]
        result = eapi_session.run_commands(cmds, encoding="json")
        assert len(result) == len(cmds)


class TestDriverConnectivity:
    """Verify the EosDriver works against real devices."""

    def test_driver_is_connected(self, eos_driver):
        """Driver should report connected state."""
        assert eos_driver.is_connected()

    def test_driver_run_show(self, eos_driver):
        """run_show should return parsed JSON dicts."""
        result = eos_driver.run_show(["show version"])
        assert len(result) == 1
        assert isinstance(result[0], dict)
        assert "version" in result[0]

    def test_driver_platform(self, eos_driver):
        """Platform should be 'eos'."""
        assert eos_driver.platform == "eos"


class TestNormalizedGetters:
    """Verify NetworkDriver normalized getters return the expected schema."""

    def test_get_facts(self, eos_driver):
        """get_facts should return vendor-neutral device info."""
        facts = eos_driver.get_facts()
        assert isinstance(facts, dict)
        assert "hostname" in facts
        assert "version" in facts
        assert "model" in facts or "modelName" in facts
        assert facts.get("vendor") == "arista" or facts.get("platform") == "eos"

    def test_get_interfaces(self, eos_driver):
        """get_interfaces should return a dict of interfaces."""
        interfaces = eos_driver.get_interfaces()
        assert isinstance(interfaces, dict)
        # cEOS always has Management0
        mgmt_names = [k for k in interfaces if "anagement" in k or "mgmt" in k.lower()]
        assert len(mgmt_names) >= 1, f"Expected Management interface, got: {list(interfaces.keys())}"
        # Check schema of first interface
        first_iface = next(iter(interfaces.values()))
        assert "is_up" in first_iface or "lineProtocolStatus" in first_iface

    def test_get_arp_table(self, eos_driver):
        """get_arp_table should return a list of ARP entries."""
        arp = eos_driver.get_arp_table()
        assert isinstance(arp, list)
        # Management interface should have at least one ARP entry (gateway)
        if arp:
            entry = arp[0]
            assert "ip" in entry
            assert "mac" in entry or "hwAddress" in entry
