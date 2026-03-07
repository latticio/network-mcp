"""Phase 3: Write operation tests — validate config changes on real devices.

These tests actually modify device configuration and then roll it back.
They run with NET_READ_ONLY=false and are the most "dangerous" tests —
but containerlab devices are ephemeral, so no real risk.

Run with: uv run pytest tests/integration/test_write_operations.py -v --run-integration
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]


class TestVlanCrud:
    """Test VLAN create/read/delete on a real device."""

    TEST_VLAN_ID = 999
    TEST_VLAN_NAME = "INTEG-TEST"

    def test_vlan_lifecycle(self, eos_driver):
        """Create a VLAN, verify it exists, delete it, verify it's gone."""
        # Create
        eos_driver.run_config([
            f"vlan {self.TEST_VLAN_ID}",
            f"name {self.TEST_VLAN_NAME}",
        ])

        # Verify created
        result = eos_driver.run_show(["show vlan"])
        vlans = result[0]["vlans"]
        assert str(self.TEST_VLAN_ID) in vlans, f"VLAN {self.TEST_VLAN_ID} not found after creation"
        assert vlans[str(self.TEST_VLAN_ID)]["name"] == self.TEST_VLAN_NAME

        # Delete
        eos_driver.run_config([f"no vlan {self.TEST_VLAN_ID}"])

        # Verify deleted
        result = eos_driver.run_show(["show vlan"])
        vlans = result[0]["vlans"]
        assert str(self.TEST_VLAN_ID) not in vlans, f"VLAN {self.TEST_VLAN_ID} still exists after deletion"


class TestInterfaceConfig:
    """Test interface configuration on a real device."""

    def test_interface_description(self, eos_driver):
        """Set and clear an interface description."""
        # Find a test interface (Ethernet1 should exist on cEOS)
        result = eos_driver.run_show(["show interfaces status"])
        statuses = result[0]["interfaceStatuses"]
        test_iface = None
        for name in statuses:
            if name.startswith("Ethernet"):
                test_iface = name
                break
        if not test_iface:
            pytest.skip("No Ethernet interface found")

        # Set description
        eos_driver.run_config([
            f"interface {test_iface}",
            "description INTEG-TEST-DESCRIPTION",
        ])

        # Verify
        result = eos_driver.run_show([f"show interfaces {test_iface}"])
        iface_data = result[0]["interfaces"][test_iface]
        assert iface_data.get("description") == "INTEG-TEST-DESCRIPTION"

        # Clean up
        eos_driver.run_config([
            f"interface {test_iface}",
            "no description",
        ])

        # Verify cleanup
        result = eos_driver.run_show([f"show interfaces {test_iface}"])
        iface_data = result[0]["interfaces"][test_iface]
        assert iface_data.get("description", "") == ""


class TestConfigSession:
    """Test config sessions if supported by the cEOS version."""

    def test_config_session_lifecycle(self, eos_driver):
        """Create a config session, make a change, abort it."""
        try:
            # Create session
            eos_driver.run_show(["show configuration sessions"])
        except Exception:
            pytest.skip("Config sessions not supported on this cEOS version")

        session_name = "integ-test-session"
        try:
            # Enter session
            eos_driver.run_config([
                f"configure session {session_name}",
                "vlan 998",
                "name SESSION-TEST",
            ])

            # Verify session exists
            result = eos_driver.run_show(["show configuration sessions"])
            # Abort (don't commit)
            eos_driver.run_config([f"no configure session {session_name}"])
        except Exception:
            # Clean up on failure
            try:
                eos_driver.run_config([f"no configure session {session_name}"])
            except Exception:
                pass
            raise

        # VLAN 998 should NOT exist (session was aborted)
        result = eos_driver.run_show(["show vlan"])
        assert "998" not in result[0]["vlans"], "VLAN 998 exists — session abort failed"
