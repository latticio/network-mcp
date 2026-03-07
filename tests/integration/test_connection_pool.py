"""Phase 3: Connection pool and resilience tests against real devices.

Tests connection lifecycle, reconnection, concurrent access, and
circuit breaker behavior with real network latency and failures.

Run with: uv run pytest tests/integration/test_connection_pool.py -v --run-integration
"""

from __future__ import annotations

import concurrent.futures
import time

import pytest

pytestmark = [pytest.mark.integration]


class TestConnectionLifecycle:
    """Verify connection pool behavior with real devices."""

    def test_driver_connect_disconnect(self, lab_node):
        """Driver should cleanly connect and disconnect."""
        from network_mcp.driver import EosDriver

        driver = EosDriver()
        driver.connect(
            host=lab_node["ip"],
            transport="https",
            username="admin",
            password="admin",
        )
        assert driver.is_connected()
        driver.close()

    def test_driver_reconnect_after_close(self, lab_node):
        """Driver should reconnect after being closed."""
        from network_mcp.driver import EosDriver

        driver = EosDriver()
        driver.connect(
            host=lab_node["ip"],
            transport="https",
            username="admin",
            password="admin",
        )
        result1 = driver.run_show(["show hostname"])
        driver.close()

        # Reconnect
        driver.connect(
            host=lab_node["ip"],
            transport="https",
            username="admin",
            password="admin",
        )
        result2 = driver.run_show(["show hostname"])
        assert result1[0]["hostname"] == result2[0]["hostname"]
        driver.close()

    def test_multiple_sequential_commands(self, eos_driver):
        """Multiple sequential show commands should all succeed."""
        commands = [
            "show version",
            "show hostname",
            "show interfaces status",
            "show vlan",
            "show ip route",
            "show lldp neighbors",
            "show ntp status",
            "show ip interface brief",
        ]
        for cmd in commands:
            result = eos_driver.run_show([cmd])
            assert len(result) == 1, f"Failed on: {cmd}"
            assert isinstance(result[0], dict), f"Non-dict result for: {cmd}"


class TestConcurrentAccess:
    """Test concurrent command execution against real devices."""

    def test_parallel_show_commands(self, lab_node):
        """Multiple threads hitting the same device should all succeed."""
        from network_mcp.driver import EosDriver

        def run_command(cmd: str) -> dict:
            driver = EosDriver()
            driver.connect(
                host=lab_node["ip"],
                transport="https",
                username="admin",
                password="admin",
            )
            try:
                result = driver.run_show([cmd])
                return {"cmd": cmd, "success": True, "data": result[0]}
            except Exception as e:
                return {"cmd": cmd, "success": False, "error": str(e)}
            finally:
                driver.close()

        commands = [
            "show version",
            "show hostname",
            "show interfaces status",
            "show vlan",
            "show ip route",
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(run_command, cmd) for cmd in commands]
            results = [f.result(timeout=30) for f in futures]

        for r in results:
            assert r["success"], f"Failed: {r['cmd']} — {r.get('error')}"

    def test_parallel_multi_device(self, lab_nodes):
        """Parallel commands to different devices should all succeed."""
        if len(lab_nodes) < 2:
            pytest.skip("Need at least 2 nodes")

        from network_mcp.driver import EosDriver

        def get_version(node: dict) -> dict:
            driver = EosDriver()
            driver.connect(
                host=node["ip"],
                transport="https",
                username="admin",
                password="admin",
            )
            try:
                result = driver.run_show(["show version"])
                return {"node": node["name"], "success": True, "version": result[0]["version"]}
            except Exception as e:
                return {"node": node["name"], "success": False, "error": str(e)}
            finally:
                driver.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(lab_nodes)) as pool:
            futures = [pool.submit(get_version, node) for node in lab_nodes]
            results = [f.result(timeout=30) for f in futures]

        for r in results:
            assert r["success"], f"Node {r['node']} failed: {r.get('error')}"

        # All nodes should run the same EOS version (same image)
        versions = {r["version"] for r in results}
        assert len(versions) == 1, f"Version mismatch across lab: {versions}"


class TestConnectionTiming:
    """Measure real-world connection and command latency."""

    def test_connection_latency(self, lab_node):
        """Connection setup should complete within 10 seconds."""
        from network_mcp.driver import EosDriver

        start = time.monotonic()
        driver = EosDriver()
        driver.connect(
            host=lab_node["ip"],
            transport="https",
            username="admin",
            password="admin",
        )
        elapsed = time.monotonic() - start
        driver.close()
        assert elapsed < 10, f"Connection took {elapsed:.1f}s (expected <10s)"

    def test_show_command_latency(self, eos_driver):
        """A simple show command should complete within 5 seconds."""
        start = time.monotonic()
        eos_driver.run_show(["show version"])
        elapsed = time.monotonic() - start
        assert elapsed < 5, f"show version took {elapsed:.1f}s (expected <5s)"

    def test_bulk_command_latency(self, eos_driver):
        """10 sequential commands should complete within 30 seconds."""
        start = time.monotonic()
        for _ in range(10):
            eos_driver.run_show(["show version"])
        elapsed = time.monotonic() - start
        assert elapsed < 30, f"10 commands took {elapsed:.1f}s (expected <30s)"
        avg = elapsed / 10
        # Log for the report
        print(f"\n  Average show command latency: {avg:.3f}s")
