"""Phase 3: Containerlab integration tool tests.

Tests the containerlab.py integration module against the running lab.

Run with: uv run pytest tests/integration/test_containerlab_integration.py -v --run-integration
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]


class TestContainerlabDiscover:
    """Test the net_containerlab_discover tool against a running lab."""

    def test_discover_finds_running_labs(self):
        """Discover should find at least one running lab."""
        from network_mcp.integrations.containerlab import _run_inspect

        containers = _run_inspect()
        assert len(containers) >= 1, "No containers found"

    def test_discover_container_fields(self):
        """Each container should have expected fields."""
        from network_mcp.integrations.containerlab import _run_inspect

        containers = _run_inspect()
        for c in containers:
            assert "name" in c, f"Container missing 'name': {c}"
            state = c.get("state", "").lower()
            if state in ("running", "up"):
                ip = c.get("ipv4_address") or c.get("mgmt_ipv4", "")
                assert ip, f"Running container {c['name']} has no IP"


class TestContainerlabInventoryBackend:
    """Test the ContainerlabInventoryBackend against a running lab."""

    def test_backend_loads_devices(self):
        """Backend should discover at least one device."""
        from network_mcp.integrations.containerlab import ContainerlabInventoryBackend

        backend = ContainerlabInventoryBackend()
        devices = backend.get_devices()
        assert len(devices) >= 1, "No devices loaded"

    def test_backend_device_credentials(self):
        """Each device should have valid credentials."""
        from network_mcp.integrations.containerlab import ContainerlabInventoryBackend

        backend = ContainerlabInventoryBackend()
        devices = backend.get_devices()
        for name, creds in devices.items():
            assert creds.host, f"Device {name} has no host"
            assert creds.username, f"Device {name} has no username"
            assert creds.platform == "eos", f"Device {name} platform is {creds.platform}, expected eos"
            assert "containerlab" in creds.tags, f"Device {name} missing containerlab tag"

    def test_backend_devices_are_reachable(self):
        """Devices loaded from backend should respond to eAPI."""
        import pyeapi

        from network_mcp.integrations.containerlab import ContainerlabInventoryBackend

        backend = ContainerlabInventoryBackend()
        devices = backend.get_devices()
        for name, creds in devices.items():
            node = pyeapi.connect(
                host=creds.host,
                transport="https",
                username=creds.username,
                password=creds.password.get_secret_value(),
                return_node=True,
                timeout=15,
            )
            result = node.run_commands(["show hostname"], encoding="json")
            assert "hostname" in result[0], f"Device {name} returned bad data"


class TestPlatformDetection:
    """Test platform auto-detection with real containerlab data."""

    def test_ceos_detected_as_eos(self):
        """cEOS containers should be detected as platform 'eos'."""
        from network_mcp.integrations.containerlab import _detect_platform

        assert _detect_platform("ceos:4.33.0F") == "eos"
        assert _detect_platform("ceos:latest") == "eos"
        assert _detect_platform("ceos") == "eos"

    def test_real_containers_detected_correctly(self):
        """Running containers should all be detected as 'eos' (cEOS lab)."""
        from network_mcp.integrations.containerlab import ContainerlabInventoryBackend

        backend = ContainerlabInventoryBackend()
        for name, creds in backend.get_devices().items():
            assert creds.platform == "eos", f"{name}: expected eos, got {creds.platform}"
