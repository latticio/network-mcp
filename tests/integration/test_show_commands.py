"""Phase 3: Show command tests — validate real eAPI output parsing.

Tests that the helpers.run_show_command() correctly handles real device output.
These are the tests most likely to catch mock/reality divergence.

Run with: uv run pytest tests/integration/test_show_commands.py -v --run-integration
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]


class TestShowCommandParsing:
    """Verify run_show_command works with real eAPI output."""

    def test_show_version(self, eos_driver):
        """show version should parse cleanly."""
        result = eos_driver.run_show(["show version"])
        data = result[0]
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0
        # cEOS-lab specific: model should indicate lab/container
        model = data.get("modelName", "")
        assert model, "modelName should not be empty"

    def test_show_interfaces(self, eos_driver):
        """show interfaces should return structured interface data."""
        result = eos_driver.run_show(["show interfaces"])
        data = result[0]
        assert "interfaces" in data
        interfaces = data["interfaces"]
        assert isinstance(interfaces, dict)
        # Every cEOS node has Management0
        assert "Management0" in interfaces
        mgmt = interfaces["Management0"]
        assert "interfaceStatus" in mgmt or "lineProtocolStatus" in mgmt

    def test_show_interfaces_status(self, eos_driver):
        """show interfaces status should return the status table."""
        result = eos_driver.run_show(["show interfaces status"])
        data = result[0]
        assert "interfaceStatuses" in data

    def test_show_ip_interface_brief(self, eos_driver):
        """show ip interface brief should return interface IP summary."""
        result = eos_driver.run_show(["show ip interface brief"])
        data = result[0]
        assert "interfaces" in data

    def test_show_vlans(self, eos_driver):
        """show vlan should return VLAN data."""
        result = eos_driver.run_show(["show vlan"])
        data = result[0]
        assert "vlans" in data
        vlans = data["vlans"]
        # VLAN 1 always exists
        assert "1" in vlans

    def test_show_mac_address_table(self, eos_driver):
        """show mac address-table should return without error."""
        result = eos_driver.run_show(["show mac address-table"])
        data = result[0]
        # May have unicastTable or macTable depending on version
        assert isinstance(data, dict)

    def test_show_lldp_neighbors(self, eos_driver):
        """show lldp neighbors should return neighbor data."""
        result = eos_driver.run_show(["show lldp neighbors"])
        data = result[0]
        assert "lldpNeighbors" in data

    def test_show_ip_route(self, eos_driver):
        """show ip route should return routing table."""
        result = eos_driver.run_show(["show ip route"])
        data = result[0]
        assert "vrfs" in data
        assert "default" in data["vrfs"]

    def test_show_running_config(self, eos_driver):
        """show running-config should return text output."""
        result = eos_driver.run_show(["show running-config"], encoding="text")
        data = result[0]
        # Text encoding returns {"output": "..."} on EOS
        assert "output" in data
        text = data["output"]
        assert "hostname" in text.lower() or "management api" in text.lower()

    def test_show_ntp_status(self, eos_driver):
        """show ntp status should return without error."""
        result = eos_driver.run_show(["show ntp status"])
        data = result[0]
        assert isinstance(data, dict)

    def test_show_logging(self, eos_driver):
        """show logging should return log data (text encoding — JSON not supported on cEOS)."""
        result = eos_driver.run_show(["show logging"], encoding="text")
        data = result[0]
        assert "output" in data
        assert isinstance(data["output"], str)

    def test_show_inventory(self, eos_driver):
        """show inventory should return hardware inventory."""
        result = eos_driver.run_show(["show inventory"])
        data = result[0]
        assert isinstance(data, dict)


class TestShowCommandEdgeCases:
    """Test edge cases in show command handling."""

    def test_invalid_interface_raises_error(self, eos_driver):
        """Non-existent interface should raise CommandError on real EOS."""
        import pyeapi.eapilib

        with pytest.raises(pyeapi.eapilib.CommandError):
            eos_driver.run_show(["show interfaces Ethernet99"], encoding="json")

    def test_text_encoding(self, eos_driver):
        """Text encoding should return raw text output."""
        result = eos_driver.run_show(["show version"], encoding="text")
        data = result[0]
        assert "output" in data
        assert isinstance(data["output"], str)
        assert len(data["output"]) > 0

    def test_multiple_commands_ordering(self, eos_driver):
        """Multiple commands should return results in order."""
        cmds = ["show hostname", "show version", "show vlan"]
        result = eos_driver.run_show(cmds)
        assert len(result) == 3
        assert "hostname" in result[0]
        assert "version" in result[1]
        assert "vlans" in result[2]
