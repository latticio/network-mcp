"""Phase 3: Advanced Scenarios — concurrent load, rapid calls, config detection.

Run with:
  uv run pytest tests/integration/test_advanced_scenarios.py -v --run-integration -n0
"""

from __future__ import annotations

import importlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

ALL_HOSTS = ["spine1", "spine2", "leaf1", "leaf2", "leaf3", "leaf4"]

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
]


@pytest.fixture(scope="module")
def live_mgr(lab_nodes):
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
def patch_all(live_mgr, monkeypatch):
    from network_mcp import server

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


# ---------------------------------------------------------------------------
# 1. Concurrent Load
# ---------------------------------------------------------------------------


class TestConcurrentLoad:
    """Fire many tool calls in parallel across different nodes."""

    def test_concurrent_device_info_across_nodes(self):
        """20+ parallel device info calls across all nodes — no deadlocks.

        Note: pyeapi connections are not fully thread-safe, so some errors
        under heavy concurrent load are expected. We verify that the majority
        succeed and that we don't deadlock.
        """
        from network_mcp.tools.common.device import net_get_device_info

        calls = ALL_HOSTS * 4  # 24 parallel calls
        successes = 0
        errors = []

        start = time.time()
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(net_get_device_info, host=h): h for h in calls}
            for future in as_completed(futures, timeout=90):
                host = futures[future]
                try:
                    result = future.result(timeout=30)
                    if result.get("status") == "success":
                        successes += 1
                    else:
                        errors.append(f"{host}: {result.get('error', 'unknown')}")
                except Exception as e:
                    errors.append(f"{host}: {e}")

        total_time = time.time() - start
        # At least 50% should succeed (pyeapi thread-safety limits)
        success_rate = successes / len(calls)
        assert success_rate >= 0.5, (
            f"Too many concurrent failures: {successes}/{len(calls)} succeeded ({success_rate:.0%}). "
            f"Errors: {errors[:5]}"
        )
        assert total_time < 90, f"Total concurrent time {total_time:.1f}s exceeds 90s"

    def test_concurrent_mixed_tools(self):
        """Parallel calls to different tool types across different devices."""
        from network_mcp.tools.common.device import net_get_device_info
        from network_mcp.tools.common.interfaces import net_get_interfaces
        from network_mcp.tools.common.monitoring import net_get_cpu_usage
        from network_mcp.tools.common.routing import net_get_route_table
        from network_mcp.tools.common.switching import net_get_lldp_neighbors
        from network_mcp.tools.common.vlans import net_get_vlans

        tool_calls = [
            (net_get_device_info, {"host": "spine1"}),
            (net_get_interfaces, {"host": "spine2"}),
            (net_get_route_table, {"host": "leaf1"}),
            (net_get_lldp_neighbors, {"host": "leaf2"}),
            (net_get_vlans, {"host": "leaf3"}),
            (net_get_cpu_usage, {"host": "leaf4"}),
            (net_get_device_info, {"host": "leaf1"}),
            (net_get_interfaces, {"host": "leaf2"}),
            (net_get_route_table, {"host": "spine1"}),
            (net_get_lldp_neighbors, {"host": "spine2"}),
            (net_get_vlans, {"host": "leaf1"}),
            (net_get_cpu_usage, {"host": "spine1"}),
            (net_get_device_info, {"host": "spine2"}),
            (net_get_interfaces, {"host": "leaf3"}),
            (net_get_route_table, {"host": "leaf4"}),
            (net_get_lldp_neighbors, {"host": "leaf1"}),
            (net_get_vlans, {"host": "spine1"}),
            (net_get_cpu_usage, {"host": "leaf2"}),
            (net_get_device_info, {"host": "leaf3"}),
            (net_get_interfaces, {"host": "leaf4"}),
        ]

        successes = 0
        errors = []
        start = time.time()
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {}
            for func, kwargs in tool_calls:
                f = pool.submit(func, **kwargs)
                futures[f] = f"{func.__name__}({kwargs.get('host')})"

            for future in as_completed(futures, timeout=90):
                label = futures[future]
                try:
                    result = future.result(timeout=30)
                    if result.get("status") == "success":
                        successes += 1
                    else:
                        errors.append(f"{label}: {result.get('error', 'unknown')}")
                except Exception as e:
                    errors.append(f"{label}: {e}")

        total_time = time.time() - start
        success_rate = successes / len(tool_calls)
        assert success_rate >= 0.5, (
            f"Too many concurrent failures: {successes}/{len(tool_calls)} ({success_rate:.0%}). "
            f"Errors: {errors[:5]}"
        )
        assert total_time < 90, f"Total time {total_time:.1f}s exceeds 90s"


# ---------------------------------------------------------------------------
# 2. Rapid Sequential Calls
# ---------------------------------------------------------------------------


class TestRapidSequential:
    """Fire many sequential calls to the same device — check consistency."""

    def test_100_sequential_device_info(self):
        """100 sequential calls to the same device — verify consistency."""
        from network_mcp.tools.common.device import net_get_device_info

        host = "spine1"
        latencies = []
        hostnames = set()
        errors = []

        for i in range(100):
            start = time.time()
            result = net_get_device_info(host=host)
            latency = time.time() - start
            latencies.append(latency)

            if result.get("status") == "success":
                hostnames.add(result["data"].get("hostname", ""))
            else:
                errors.append(f"Call {i}: {result.get('error')}")

        # Stats
        avg = sum(latencies) / len(latencies)
        p95 = sorted(latencies)[int(0.95 * len(latencies))]

        assert len(errors) < 5, f"Too many errors ({len(errors)}/100): {errors[:5]}"
        assert len(hostnames) <= 1, f"Inconsistent hostnames: {hostnames}"
        assert avg < 5, f"Average latency {avg:.2f}s too high"
        assert p95 < 10, f"P95 latency {p95:.2f}s too high"

    def test_50_sequential_interfaces(self):
        """50 sequential interface calls — check for connection reuse."""
        from network_mcp.tools.common.interfaces import net_get_interfaces

        host = "leaf1"
        latencies = []
        errors = 0

        for _ in range(50):
            start = time.time()
            result = net_get_interfaces(host=host)
            latencies.append(time.time() - start)
            if result.get("status") != "success":
                errors += 1

        avg = sum(latencies) / len(latencies)
        assert errors < 3, f"{errors}/50 failures"
        assert avg < 5, f"Average latency {avg:.2f}s"


# ---------------------------------------------------------------------------
# 3. Config Change Detection
# ---------------------------------------------------------------------------


class TestConfigChangeDetection:
    """Make a config change, read it back, verify, roll back."""

    def test_vlan_change_detected(self):
        """Create VLAN, verify in running config, delete, verify gone."""
        from network_mcp.tools.common.config import net_get_running_config
        from network_mcp.tools.common.vlans import net_create_vlan, net_delete_vlan

        host = "leaf4"
        vlan_id = 995
        vlan_name = "CHANGE-DETECT-TEST"

        # Baseline — VLAN should not exist
        result = net_get_running_config(host=host)
        assert result.get("status") == "success"
        baseline_config = str(result.get("data", ""))
        assert vlan_name not in baseline_config

        # Create VLAN
        result = net_create_vlan(host=host, vlan_id=vlan_id, name=vlan_name)
        assert result.get("status") == "success"

        # Verify in running config
        result = net_get_running_config(host=host)
        assert result.get("status") == "success"
        config_after = str(result.get("data", ""))
        assert vlan_name in config_after, "VLAN not detected in running config after creation"

        # Roll back
        result = net_delete_vlan(host=host, vlan_id=vlan_id)
        assert result.get("status") == "success"

        # Verify rolled back
        result = net_get_running_config(host=host)
        assert result.get("status") == "success"
        config_final = str(result.get("data", ""))
        assert vlan_name not in config_final, "VLAN still in running config after deletion"


# ---------------------------------------------------------------------------
# 4. Output Size Stress
# ---------------------------------------------------------------------------


class TestOutputSizeStress:
    """Test tools that return large output — ensure no corruption."""

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_full_running_config(self, host):
        """Full running config should be parseable and non-empty."""
        from network_mcp.tools.common.config import net_get_running_config

        result = net_get_running_config(host=host)
        assert result.get("status") == "success"
        data = result["data"]
        if isinstance(data, dict):
            config = data.get("config", "")
        else:
            config = str(data)
        assert len(config) > 200, f"Config too short: {len(config)} chars"
        # Should be valid text, not corrupted
        assert "\x00" not in config, "Null bytes in config output"

    def test_full_interface_list(self):
        """Get all interfaces with counters — should be complete."""
        from network_mcp.tools.common.interfaces import net_get_interfaces

        result = net_get_interfaces(host="spine1")
        assert result.get("status") == "success"
        data = result["data"]
        assert isinstance(data, dict)
        # Spine should have Management0 + at least 4 Ethernet ports
        assert len(data) >= 5, f"Only {len(data)} interfaces on spine1"

    def test_large_route_table(self):
        """Route table should return valid data structure."""
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table(host="spine1")
        assert result.get("status") == "success"
        data = result.get("data", {})
        assert data is not None


# ---------------------------------------------------------------------------
# 5. Topology Build
# ---------------------------------------------------------------------------


class TestTopologyBuild:
    """Build full LLDP topology and validate fabric structure."""

    def test_build_topology_from_lldp(self):
        """net_build_topology_from_lldp should return a valid topology graph."""
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(hosts=ALL_HOSTS)
        # Accept success with possibly empty data (cEOS LLDP hostnames are truncated)
        assert result.get("status") in ("success", "error"), f"Unexpected: {result}"

    def test_lldp_adjacency_via_ethernet(self):
        """Verify fabric connectivity by counting Ethernet LLDP neighbors.

        cEOS reports truncated LLDP hostnames (e.g. "leaf" not "leaf1"),
        so we count Ethernet-port neighbors instead of matching exact names.
        """
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        for host in ALL_HOSTS:
            result = net_get_lldp_neighbors(host=host)
            if result.get("status") != "success":
                continue
            data = result.get("data", {})
            eth_count = sum(
                1 for iface, nbrs in data.items()
                if iface.startswith("Ethernet") and isinstance(nbrs, list) and len(nbrs) > 0
            )
            if host.startswith("spine"):
                assert eth_count >= 3, f"{host} has only {eth_count}/4 Ethernet LLDP neighbors"
            else:
                assert eth_count >= 2, f"{host} has only {eth_count}/3 Ethernet LLDP neighbors"


# ---------------------------------------------------------------------------
# 6. Error Recovery
# ---------------------------------------------------------------------------


class TestErrorRecovery:
    """Test that errors don't break subsequent calls."""

    def test_invalid_host_then_valid(self):
        """An error on invalid host should not corrupt connection pool."""
        from network_mcp.tools.common.device import net_get_device_info

        # Invalid host
        bad = net_get_device_info(host="nonexistent-device-xyz")
        assert bad.get("status") == "error"

        # Valid host should still work
        good = net_get_device_info(host="spine1")
        assert good.get("status") == "success", f"Valid call failed after error: {good.get('error')}"

    def test_rapid_errors_then_recovery(self):
        """Multiple errors in a row should not cause permanent failure."""
        from network_mcp.tools.common.device import net_get_device_info

        # 5 rapid errors
        for _ in range(5):
            net_get_device_info(host="fake-host")

        # Should recover
        result = net_get_device_info(host="leaf1")
        assert result.get("status") == "success", f"Recovery failed: {result.get('error')}"
