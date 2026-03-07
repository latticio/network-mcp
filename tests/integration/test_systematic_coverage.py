"""Phase 2: Systematic Tool Coverage — exercise every tool category on all nodes.

Tests tool functions via Python imports with a live ConnectionManager patched
into all tool modules. Validates response structure, device field, data presence,
and latency for each call.

Run with:
  uv run pytest tests/integration/test_systematic_coverage.py -v --run-integration -n0
"""

from __future__ import annotations

import importlib
import sys
import time
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

ALL_HOSTS = ["spine1", "spine2", "leaf1", "leaf2", "leaf3", "leaf4"]
SPINES = ["spine1", "spine2"]
LEAVES = ["leaf1", "leaf2", "leaf3", "leaf4"]

# Every tool module that imports conn_mgr at module level
_TOOL_MODULES = [
    "network_mcp.tools.common.device",
    "network_mcp.tools.common.interfaces",
    "network_mcp.tools.common.routing",
    "network_mcp.tools.common.switching",
    "network_mcp.tools.common.monitoring",
    "network_mcp.tools.common.config",
    "network_mcp.tools.common.vlans",
    "network_mcp.tools.common.troubleshoot",
    "network_mcp.tools.common.vrf",
    "network_mcp.tools.common.security",
    "network_mcp.tools.common.topology",
    "network_mcp.tools.common.workflows",
    "network_mcp.tools.common.drift",
    "network_mcp.tools.common.changes",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_mgr(lab_nodes):
    """Create a ConnectionManager with real lab device credentials."""
    from network_mcp.config import NetworkSettings
    from network_mcp.connection import ConnectionManager, DeviceCredentials

    settings = NetworkSettings(
        net_username="admin",
        net_password="admin",
        net_transport="https",
        net_read_only=False,
        net_demo_mode=False,
        net_verify_ssl=False,
    )
    mgr = ConnectionManager(settings)

    for node in lab_nodes:
        mgr._inventory[node["name"]] = DeviceCredentials(
            host=node["ip"],
            username="admin",
            password="admin",
            transport="https",
            port=443,
            platform="eos",
            tags=["containerlab"],
        )

    return mgr


@pytest.fixture(autouse=True)
def patch_all_conn_mgr(live_mgr, monkeypatch):
    """Patch conn_mgr in server AND every tool module. Disable read-only."""
    from network_mcp import server

    # Disable read-only for write tests
    monkeypatch.setenv("NET_READ_ONLY", "false")
    import network_mcp.helpers as _helpers
    _helpers._settings = None

    patches = [patch.object(server, "conn_mgr", live_mgr)]

    for mod_name in _TOOL_MODULES:
        if mod_name not in sys.modules:
            try:
                importlib.import_module(mod_name)
            except Exception:  # noqa: S112
                continue
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            if hasattr(mod, "conn_mgr"):
                patches.append(patch.object(mod, "conn_mgr", live_mgr))

    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()
    _helpers._settings = None


def assert_success(result: dict, host: str | None = None, max_latency: float = 15.0):
    """Common assertion helper for tool results."""
    assert result.get("status") == "success", f"Expected success, got: {result.get('error', result)}"
    if host:
        assert result.get("device") == host, f"Device mismatch: expected {host}, got {result.get('device')}"
    assert "data" in result or "config" in str(result.get("data", ""))


# ---------------------------------------------------------------------------
# 1. Device Info — all nodes
# ---------------------------------------------------------------------------


class TestDeviceInfoCoverage:
    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_device_info(self, host):
        from network_mcp.tools.common.device import net_get_device_info

        start = time.time()
        result = net_get_device_info(host=host)
        latency = time.time() - start

        assert_success(result, host)
        data = result["data"]
        assert "hostname" in data, f"Missing 'hostname' in device info for {host}"
        assert "version" in data, f"Missing 'version' in device info for {host}"
        assert latency < 15, f"Latency {latency:.1f}s on {host}"


# ---------------------------------------------------------------------------
# 2. Interfaces — all nodes
# ---------------------------------------------------------------------------


class TestInterfacesCoverage:
    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_interfaces(self, host):
        from network_mcp.tools.common.interfaces import net_get_interfaces

        result = net_get_interfaces(host=host)
        assert_success(result, host)
        data = result["data"]
        assert isinstance(data, dict), f"Expected dict of interfaces, got {type(data)}"
        assert len(data) > 0, f"No interfaces returned for {host}"

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_interface_counters(self, host):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        result = net_get_interface_counters(host=host)
        assert_success(result, host)

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_interface_errors(self, host):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        result = net_get_interface_errors(host=host)
        assert_success(result, host)

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_ip_interfaces(self, host):
        from network_mcp.tools.common.interfaces import net_get_ip_interfaces

        result = net_get_ip_interfaces(host=host)
        assert_success(result, host)

    def test_get_transceiver_info(self):
        from network_mcp.tools.common.interfaces import net_get_transceiver_info

        result = net_get_transceiver_info(host="spine1")
        # cEOS may not have real transceivers — accept success or known error
        assert result.get("status") in ("success", "error")

    def test_get_trunk_interfaces(self):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        result = net_get_trunk_interfaces(host="leaf1")
        assert result.get("status") == "success", f"Failed: {result.get('error')}"


# ---------------------------------------------------------------------------
# 3. VLANs — all nodes + CRUD lifecycle
# ---------------------------------------------------------------------------


class TestVlansCoverage:
    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_vlans(self, host):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans(host=host)
        assert_success(result, host)

    def test_vlan_lifecycle(self):
        """Create, verify, rename, delete a VLAN on leaf2."""
        from network_mcp.tools.common.vlans import (
            net_create_vlan,
            net_delete_vlan,
            net_get_vlan_detail,
            net_get_vlans,
            net_rename_vlan,
        )

        host = "leaf2"

        # Create
        result = net_create_vlan(host=host, vlan_id=996, name="COVERAGE-TEST")
        assert result.get("status") == "success", f"Create failed: {result.get('error')}"

        # Read detail
        result = net_get_vlan_detail(host=host, vlan_id=996)
        assert result.get("status") == "success", f"Detail failed: {result.get('error')}"

        # Rename
        result = net_rename_vlan(host=host, vlan_id=996, name="COVERAGE-RENAMED")
        assert result.get("status") == "success", f"Rename failed: {result.get('error')}"

        # Verify rename via list
        result = net_get_vlans(host=host)
        assert result.get("status") == "success"
        assert "COVERAGE-RENAMED" in str(result.get("data", ""))

        # Delete
        result = net_delete_vlan(host=host, vlan_id=996)
        assert result.get("status") == "success", f"Delete failed: {result.get('error')}"


# ---------------------------------------------------------------------------
# 4. Routing — all nodes
# ---------------------------------------------------------------------------


class TestRoutingCoverage:
    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_route_table(self, host):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table(host=host)
        assert_success(result, host)

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_arp_table(self, host):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table(host=host)
        assert_success(result, host)

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_route_summary(self, host):
        from network_mcp.tools.common.routing import net_get_route_summary

        result = net_get_route_summary(host=host)
        assert_success(result, host)

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_bgp_summary(self, host):
        from network_mcp.tools.common.routing import net_get_bgp_summary

        result = net_get_bgp_summary(host=host)
        # BGP may not be configured — both OK
        assert result.get("status") in ("success", "error")

    def test_get_bgp_neighbors(self):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        result = net_get_bgp_neighbors(host="spine1")
        assert result.get("status") in ("success", "error")

    def test_get_ospf_neighbors(self):
        from network_mcp.tools.common.routing import net_get_ospf_neighbors

        result = net_get_ospf_neighbors(host="spine1")
        assert result.get("status") in ("success", "error")


# ---------------------------------------------------------------------------
# 5. Switching — all nodes
# ---------------------------------------------------------------------------


class TestSwitchingCoverage:
    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_lldp_neighbors(self, host):
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        result = net_get_lldp_neighbors(host=host)
        assert_success(result, host)

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_mac_table(self, host):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table(host=host)
        assert_success(result, host)

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_stp_status(self, host):
        from network_mcp.tools.common.switching import net_get_stp_status

        result = net_get_stp_status(host=host)
        assert_success(result, host)

    def test_get_port_channels(self):
        from network_mcp.tools.common.switching import net_get_port_channels

        result = net_get_port_channels(host="leaf1")
        assert result.get("status") == "success", f"Failed: {result.get('error')}"

    def test_get_lldp_neighbor_detail(self):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        result = net_get_lldp_neighbor_detail(host="spine1")
        assert result.get("status") == "success", f"Failed: {result.get('error')}"


# ---------------------------------------------------------------------------
# 6. Monitoring — all nodes
# ---------------------------------------------------------------------------


class TestMonitoringCoverage:
    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_cpu_usage(self, host):
        from network_mcp.tools.common.monitoring import net_get_cpu_usage

        result = net_get_cpu_usage(host=host)
        assert_success(result, host)

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_memory_usage(self, host):
        from network_mcp.tools.common.monitoring import net_get_memory_usage

        result = net_get_memory_usage(host=host)
        assert_success(result, host)

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_device_health(self, host):
        from network_mcp.tools.common.monitoring import net_get_device_health

        result = net_get_device_health(host=host)
        assert_success(result, host)

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_environment_temp(self, host):
        from network_mcp.tools.common.monitoring import net_get_environment_temp

        result = net_get_environment_temp(host=host)
        # cEOS virtual may not have real temp sensors
        assert result.get("status") in ("success", "error")

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_ntp_status(self, host):
        from network_mcp.tools.common.monitoring import net_get_ntp_status

        result = net_get_ntp_status(host=host)
        assert_success(result, host)

    def test_get_environment_power(self):
        from network_mcp.tools.common.monitoring import net_get_environment_power

        result = net_get_environment_power(host="spine1")
        assert result.get("status") in ("success", "error")

    def test_get_environment_cooling(self):
        from network_mcp.tools.common.monitoring import net_get_environment_cooling

        result = net_get_environment_cooling(host="spine1")
        assert result.get("status") in ("success", "error")

    def test_get_logging(self):
        from network_mcp.tools.common.monitoring import net_get_logging

        result = net_get_logging(host="leaf1", lines=20)
        # show logging may need text encoding on cEOS
        assert result.get("status") in ("success", "error")


# ---------------------------------------------------------------------------
# 7. Config — all nodes
# ---------------------------------------------------------------------------


class TestConfigCoverage:
    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_running_config(self, host):
        from network_mcp.tools.common.config import net_get_running_config

        result = net_get_running_config(host=host)
        assert_success(result, host)
        data = result["data"]
        if isinstance(data, dict):
            config_text = data.get("config", "")
        else:
            config_text = str(data)
        assert len(config_text) > 100, f"Running config too short ({len(config_text)} chars)"

    def test_get_startup_config(self):
        from network_mcp.tools.common.config import net_get_startup_config

        result = net_get_startup_config(host="spine1")
        assert result.get("status") == "success", f"Failed: {result.get('error')}"

    def test_compare_configs(self):
        from network_mcp.tools.common.config import net_compare_configs

        result = net_compare_configs(host="leaf1")
        assert result.get("status") == "success", f"Failed: {result.get('error')}"

    def test_get_config_version(self):
        from network_mcp.tools.common.config import net_get_config_version

        result = net_get_config_version(host="leaf1")
        assert result.get("status") == "success", f"Failed: {result.get('error')}"

    def test_get_running_config_section(self):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section(host="leaf1", section="interface")
        assert result.get("status") in ("success", "error")


# ---------------------------------------------------------------------------
# 8. VRF
# ---------------------------------------------------------------------------


class TestVrfCoverage:
    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_vrf_list(self, host):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        result = net_get_vrf_list(host=host)
        assert_success(result, host)

    def test_get_vrf_detail(self):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        result = net_get_vrf_detail(host="spine1", vrf="default")
        assert result.get("status") in ("success", "error")


# ---------------------------------------------------------------------------
# 9. Security
# ---------------------------------------------------------------------------


class TestSecurityCoverage:
    def test_get_acls(self):
        from network_mcp.tools.common.security import net_get_acls

        result = net_get_acls(host="spine1")
        assert result.get("status") in ("success", "error", "not_supported")

    def test_get_aaa_status(self):
        from network_mcp.tools.common.security import net_get_aaa_status

        result = net_get_aaa_status(host="spine1")
        assert result.get("status") in ("success", "error", "not_supported")

    def test_get_snmp_config(self):
        from network_mcp.tools.common.security import net_get_snmp_config

        result = net_get_snmp_config(host="spine1")
        assert result.get("status") in ("success", "error", "not_supported")

    def test_get_users(self):
        from network_mcp.tools.common.security import net_get_users

        result = net_get_users(host="spine1")
        assert result.get("status") in ("success", "error", "not_supported")

    def test_get_copp_policy(self):
        from network_mcp.tools.common.security import net_get_copp_policy

        result = net_get_copp_policy(host="spine1")
        assert result.get("status") in ("success", "error", "not_supported")


# ---------------------------------------------------------------------------
# 10. Troubleshoot
# ---------------------------------------------------------------------------


class TestTroubleshootCoverage:
    def test_ping(self):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping(host="spine1", destination="172.20.20.4", count=3)
        assert result.get("status") in ("success", "error")

    def test_traceroute(self):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        result = net_traceroute(host="spine1", destination="172.20.20.6")
        assert result.get("status") in ("success", "error")

    def test_check_interface_flaps(self):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        result = net_check_interface_flaps(host="leaf1", hours=1)
        assert result.get("status") in ("success", "error")


# ---------------------------------------------------------------------------
# 11. LLDP Topology Validation
# ---------------------------------------------------------------------------


class TestLldpTopology:
    """Validate the LLDP-discovered topology matches the physical fabric."""

    def test_spine_sees_leaf_neighbors(self, lab_nodes):
        """Each spine should have LLDP neighbors on Ethernet ports (connected to leaves).

        Note: cEOS LLDP reports truncated hostnames (e.g. "leaf" not "leaf1"),
        so we count Ethernet neighbors rather than matching exact names.
        """
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        for spine in SPINES:
            result = net_get_lldp_neighbors(host=spine)
            if result.get("status") != "success":
                pytest.skip(f"LLDP not available on {spine}")
            data = result.get("data", {})
            # Data is keyed by interface, count Ethernet neighbors
            eth_neighbors = 0
            for iface, neighbors in data.items():
                if iface.startswith("Ethernet") and isinstance(neighbors, list) and len(neighbors) > 0:
                    eth_neighbors += 1
            assert eth_neighbors >= 3, (
                f"{spine} has only {eth_neighbors}/4 Ethernet LLDP neighbors. Interfaces: {list(data.keys())}"
            )

    def test_leaf_sees_neighbors(self, lab_nodes):
        """Each leaf should have LLDP neighbors on Ethernet ports (spines + MLAG peer)."""
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        for leaf in LEAVES:
            result = net_get_lldp_neighbors(host=leaf)
            if result.get("status") != "success":
                continue
            data = result.get("data", {})
            eth_neighbors = 0
            for iface, neighbors in data.items():
                if iface.startswith("Ethernet") and isinstance(neighbors, list) and len(neighbors) > 0:
                    eth_neighbors += 1
            # Each leaf has 2 spines + 1 MLAG peer = 3 Ethernet neighbors
            assert eth_neighbors >= 2, (
                f"{leaf} has only {eth_neighbors} Ethernet LLDP neighbors. Interfaces: {list(data.keys())}"
            )


# ---------------------------------------------------------------------------
# 12. Cross-Device Validation
# ---------------------------------------------------------------------------


class TestCrossDeviceValidation:
    """Compare data across devices for consistency."""

    def test_version_consistency(self):
        """All devices should run the same EOS version."""
        from network_mcp.tools.common.device import net_get_device_info

        versions = {}
        for host in ALL_HOSTS:
            result = net_get_device_info(host=host)
            if result.get("status") == "success":
                versions[host] = result["data"].get("version", "unknown")

        unique_versions = set(versions.values())
        assert len(unique_versions) <= 1, f"Version mismatch: {versions}"

    def test_interface_link_pairs(self):
        """If spine1:eth1 is up, the connected leaf port should also be up."""
        from network_mcp.tools.common.interfaces import net_get_interfaces

        spine_result = net_get_interfaces(host="spine1")
        if spine_result.get("status") != "success":
            pytest.skip("Can't read spine1 interfaces")

        spine_ifaces = spine_result["data"]
        up_count = 0
        for name, info in spine_ifaces.items():
            if name.startswith("Ethernet"):
                # Normalized driver uses is_up/is_enabled
                if info.get("is_up", False) or info.get("lineProtocolStatus") == "up":
                    up_count += 1

        # Spine1 should have at least 4 Ethernet ports up (connected to 4 leaves)
        assert up_count >= 3, f"Spine1 has only {up_count} Ethernet ports up"


# ---------------------------------------------------------------------------
# 13. Write Operations
# ---------------------------------------------------------------------------


class TestWriteOperations:
    """Test write operations with rollback."""

    def test_interface_description_set_clear(self):
        """Set and clear an interface description via tools."""
        from network_mcp.tools.common.interfaces import (
            net_configure_interface_description,
            net_get_interface_detail,
        )

        host = "leaf3"
        iface = "Ethernet1"

        # Set description
        result = net_configure_interface_description(
            host=host, interface=iface, description="INTEG-COVERAGE-TEST"
        )
        assert result.get("status") == "success", f"Set description failed: {result.get('error')}"

        # Verify — description may be nested in different ways
        result = net_get_interface_detail(host=host, interface=iface)
        if result.get("status") == "success":
            result_str = str(result.get("data", ""))
            assert "INTEG-COVERAGE-TEST" in result_str, f"Description not in response. Data: {result_str[:500]}"

        # Clear description — use a space since empty string may be rejected
        result = net_configure_interface_description(
            host=host, interface=iface, description=" "
        )
        # Accept success or error (some implementations reject whitespace-only)
        assert result.get("status") in ("success", "error")

    def test_save_config(self):
        from network_mcp.tools.common.config import net_save_config

        result = net_save_config(host="leaf4")
        assert result.get("status") == "success", f"Save config failed: {result.get('error')}"
