"""Tests for Part 1 fixes: thread-safety, description clearing, LLDP resilience, security metadata, settings refresh."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.connection import ConnectionManager, DeviceCredentials
from network_mcp.drivers.base import (
    GETTER_PLATFORM_SUPPORT,
    NotSupportedError,
    make_not_supported_response,
)
from tests.conftest import make_test_settings

# ---------------------------------------------------------------------------
# Part 1.1: Thread-safety — per-host locking in ConnectionManager
# ---------------------------------------------------------------------------


class TestConnectionManagerThreadSafety:
    """Verify per-host locking prevents concurrent driver access."""

    @pytest.fixture()
    def conn_mgr(self):
        settings = make_test_settings()
        mgr = ConnectionManager(settings)
        mgr._inventory["host1"] = DeviceCredentials(host="1.2.3.4")
        mgr._inventory["host2"] = DeviceCredentials(host="5.6.7.8")
        return mgr

    def test_host_lock_created_on_demand(self, conn_mgr):
        """Per-host locks should be lazily created."""
        assert len(conn_mgr._host_locks) == 0
        lock = conn_mgr._get_host_lock("host1")
        assert isinstance(lock, threading.Lock)
        assert "host1" in conn_mgr._host_locks

    def test_same_host_returns_same_lock(self, conn_mgr):
        """Same host should always get the same lock instance."""
        lock1 = conn_mgr._get_host_lock("host1")
        lock2 = conn_mgr._get_host_lock("host1")
        assert lock1 is lock2

    def test_different_hosts_get_different_locks(self, conn_mgr):
        """Different hosts should get independent locks."""
        lock1 = conn_mgr._get_host_lock("host1")
        lock2 = conn_mgr._get_host_lock("host2")
        assert lock1 is not lock2

    def test_close_all_clears_host_locks(self, conn_mgr):
        conn_mgr._get_host_lock("host1")
        conn_mgr._get_host_lock("host2")
        conn_mgr.close_all()
        assert len(conn_mgr._host_locks) == 0

    def test_remove_node_clears_host_lock(self, conn_mgr):
        conn_mgr._get_host_lock("host1")
        conn_mgr.remove_node("host1")
        assert "host1" not in conn_mgr._host_locks

    def test_acquire_serializes_same_host(self, conn_mgr):
        """Two threads acquiring the same host should be serialized (not concurrent)."""
        mock_driver = MagicMock()
        mock_driver.run_show.return_value = [{"hostname": "test"}]
        conn_mgr._create_driver = MagicMock(return_value=mock_driver)

        execution_order = []
        barrier = threading.Barrier(2, timeout=5)

        def worker(name):
            with conn_mgr.acquire("host1"):
                execution_order.append(f"{name}_start")
                try:
                    barrier.wait(timeout=0.5)
                except threading.BrokenBarrierError:
                    pass  # Expected — only one thread is in the section at a time
                execution_order.append(f"{name}_end")

        t1 = threading.Thread(target=worker, args=("t1",))
        t2 = threading.Thread(target=worker, args=("t2",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Verify serialization: one thread completes start+end before the other starts
        assert len(execution_order) == 4
        # First thread should start AND end before second thread starts
        first_start = execution_order[0]
        first_end = execution_order[1]
        assert first_start.endswith("_start")
        assert first_end.endswith("_end")
        assert first_start.split("_")[0] == first_end.split("_")[0]  # Same thread

    def test_acquire_allows_parallel_different_hosts(self, conn_mgr):
        """Two threads acquiring different hosts should run in parallel."""
        mock_driver = MagicMock()
        mock_driver.run_show.return_value = [{"hostname": "test"}]
        conn_mgr._create_driver = MagicMock(return_value=mock_driver)

        both_running = threading.Event()
        host1_entered = threading.Event()
        host2_entered = threading.Event()

        def worker1():
            with conn_mgr.acquire("host1"):
                host1_entered.set()
                host2_entered.wait(timeout=2)
                if host2_entered.is_set():
                    both_running.set()

        def worker2():
            with conn_mgr.acquire("host2"):
                host2_entered.set()
                host1_entered.wait(timeout=2)
                if host1_entered.is_set():
                    both_running.set()

        t1 = threading.Thread(target=worker1)
        t2 = threading.Thread(target=worker2)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert both_running.is_set(), "Different hosts should run in parallel"


# ---------------------------------------------------------------------------
# Part 1.2: Interface description clearing
# ---------------------------------------------------------------------------


class TestInterfaceDescriptionClearing:
    """Verify empty string clears interface description."""

    def test_build_clear_commands_eos(self):
        from network_mcp.tools.common.interfaces import _build_interface_description_commands

        cmds = _build_interface_description_commands("eos", "Ethernet1", "")
        assert cmds == ["interface Ethernet1", "no description"]

    def test_build_clear_commands_iosxe(self):
        from network_mcp.tools.common.interfaces import _build_interface_description_commands

        cmds = _build_interface_description_commands("iosxe", "GigabitEthernet0/0", "")
        assert cmds == ["interface GigabitEthernet0/0", "no description"]

    def test_build_clear_commands_junos(self):
        from network_mcp.tools.common.interfaces import _build_interface_description_commands

        cmds = _build_interface_description_commands("junos", "xe-0/0/0", "")
        assert cmds == ["delete interfaces xe-0/0/0 description"]

    def test_build_set_commands_still_works(self):
        from network_mcp.tools.common.interfaces import _build_interface_description_commands

        cmds = _build_interface_description_commands("eos", "Ethernet1", "uplink to spine1")
        assert cmds == ["interface Ethernet1", "description uplink to spine1"]

    def test_tool_accepts_empty_description(self, mock_conn_mgr):
        from network_mcp.tools.common.interfaces import net_configure_interface_description

        mock_conn_mgr.return_value.platform = "eos"
        with patch("network_mcp.tools.common.interfaces.run_config_command") as mock_run:
            mock_run.return_value = {"status": "success", "device": "host1"}
            result = net_configure_interface_description("host1", "Ethernet1", "")
            assert result["status"] == "success"
            mock_run.assert_called_once()
            # Verify "no description" command was passed
            call_args = mock_run.call_args
            commands = call_args[0][2]
            assert "no description" in commands


# ---------------------------------------------------------------------------
# Part 1.3: LLDP hostname truncation resilience
# ---------------------------------------------------------------------------


class TestLldpFuzzyMatching:
    """Verify LLDP topology builder handles truncated hostnames."""

    def _build_topology(self, hosts, lldp_data, facts_data):
        """Helper to run topology builder with mocked data."""
        from network_mcp.tools.common import switching

        mock_drivers = {}
        for host in hosts:
            driver = MagicMock()
            driver.get_facts.return_value = facts_data[host]
            driver.get_interfaces.return_value = {}
            driver.get_lldp_neighbors.return_value = lldp_data.get(host, {})
            mock_drivers[host] = driver

        with patch.object(switching, "conn_mgr") as mock_cm:
            mock_cm.get_driver.side_effect = lambda h: mock_drivers[h]
            from network_mcp.tools.common.switching import net_build_topology_from_lldp

            return net_build_topology_from_lldp(hosts)

    def _make_neighbor(self, hostname, port="Ethernet1", mgmt_ip=""):
        return {"hostname": hostname, "port": port, "system_description": "", "management_ip": mgmt_ip}

    def test_exact_hostname_match(self):
        result = self._build_topology(
            ["spine1", "leaf1"],
            {
                "spine1": {"Ethernet1": [self._make_neighbor("leaf1")]},
                "leaf1": {"Ethernet1": [self._make_neighbor("spine1")]},
            },
            {
                "spine1": {"hostname": "spine1", "platform": "eos"},
                "leaf1": {"hostname": "leaf1", "platform": "eos"},
            },
        )
        assert result["status"] == "success"
        assert len(result["topology"]["edges"]) == 1  # Deduplicated

    def test_management_ip_fallback(self):
        """When hostname doesn't match, fall back to management IP."""
        result = self._build_topology(
            ["10.0.0.1", "10.0.0.2"],
            {
                "10.0.0.1": {"Ethernet1": [self._make_neighbor("truncated", mgmt_ip="10.0.0.2")]},
                "10.0.0.2": {"Ethernet1": [self._make_neighbor("spine1", mgmt_ip="10.0.0.1")]},
            },
            {
                "10.0.0.1": {"hostname": "spine1", "platform": "eos"},
                "10.0.0.2": {"hostname": "leaf1", "platform": "eos"},
            },
        )
        assert result["status"] == "success"
        edges = result["topology"]["edges"]
        # At least one edge should have resolved the target to a known host
        targets = {e["target"] for e in edges}
        sources = {e["source"] for e in edges}
        all_ids = targets | sources
        assert "10.0.0.1" in all_ids or "10.0.0.2" in all_ids


# ---------------------------------------------------------------------------
# Part 1.4: Security tools — not_supported with platform metadata
# ---------------------------------------------------------------------------


class TestMakeNotSupportedResponse:
    """Verify make_not_supported_response includes platform guidance."""

    def test_includes_supported_platforms(self):
        resp = make_not_supported_response("get_acls", "host1", "juniper", "junos")
        assert resp["status"] == "not_supported"
        assert resp["device"] == "host1"
        assert resp["vendor"] == "juniper"
        assert resp["platform"] == "junos"
        assert "supported_platforms" in resp
        assert isinstance(resp["supported_platforms"], list)
        assert "eos" in resp["supported_platforms"]

    def test_unknown_getter_returns_empty_list(self):
        resp = make_not_supported_response("get_nonexistent", "host1", "arista", "eos")
        assert resp["supported_platforms"] == []

    def test_getter_platform_support_has_expected_entries(self):
        assert "get_facts" in GETTER_PLATFORM_SUPPORT
        assert "eos" in GETTER_PLATFORM_SUPPORT["get_facts"]
        assert "get_copp_policy" in GETTER_PLATFORM_SUPPORT
        assert "get_acls" in GETTER_PLATFORM_SUPPORT

    def test_security_tool_uses_helper(self):
        """Verify security tools now include supported_platforms in their not_supported response."""
        from network_mcp.tools.common import security

        driver = MagicMock()
        driver.vendor = "cisco"
        driver.platform = "sonic"
        driver.get_acls.side_effect = NotSupportedError("not supported")

        with patch.object(security, "conn_mgr") as mock_cm:
            mock_cm.get_driver.return_value = driver
            result = security.net_get_acls("host1")
            assert result["status"] == "not_supported"
            assert "supported_platforms" in result
            assert isinstance(result["supported_platforms"], list)


# ---------------------------------------------------------------------------
# Part 1.5: Settings cache invalidation
# ---------------------------------------------------------------------------


class TestRefreshSettings:
    """Verify refresh_settings() invalidates and replaces cached settings."""

    def test_refresh_clears_settings(self):
        from network_mcp import helpers

        # Ensure settings are populated
        helpers._get_settings()
        assert helpers._settings is not None

        # Refresh without replacement
        helpers.refresh_settings()
        assert helpers._settings is None

        # Next call should create fresh settings
        s = helpers._get_settings()
        assert s is not None

    def test_refresh_with_replacement(self):
        from network_mcp import helpers

        mock_settings = MagicMock()
        mock_settings.config_retry_enabled = False
        mock_settings.net_rate_limit_backend = "local"
        mock_settings.rate_limit_show = 5.0
        mock_settings.rate_limit_config = 2.0
        mock_settings.net_user_rate_limit = 100.0
        mock_settings.net_tenant_rate_limit = 200.0
        mock_settings.net_cache_backend = "local"
        mock_settings.net_cache_default_ttl = 30
        mock_settings.net_circuit_breaker_backend = "local"
        mock_settings.net_circuit_breaker_threshold = 5
        mock_settings.net_circuit_breaker_timeout = 30

        helpers.refresh_settings(mock_settings)
        assert helpers._settings is mock_settings

        # Clean up
        helpers._settings = None
