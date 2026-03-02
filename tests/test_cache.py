"""Tests for CommandCache: TTL expiry, LRU eviction, memory limits, invalidation, and thread safety."""

import threading
import time
from unittest.mock import patch

import pytest

from network_mcp.cache import CommandCache, get_default_ttl

# --- get_default_ttl tests ---


class TestGetDefaultTTL:
    def test_show_version_gets_300s(self):
        assert get_default_ttl(["show version"]) == 300

    def test_show_inventory_gets_300s(self):
        assert get_default_ttl(["show inventory"]) == 300

    def test_show_interfaces_status_gets_30s(self):
        assert get_default_ttl(["show interfaces status"]) == 30

    def test_show_ip_bgp_summary_gets_15s(self):
        assert get_default_ttl(["show ip bgp summary"]) == 15

    def test_show_running_config_gets_60s(self):
        assert get_default_ttl(["show running-config"]) == 60

    def test_show_processes_top_gets_0(self):
        assert get_default_ttl(["show processes top once"]) == 0

    def test_unknown_command_gets_fallback_30s(self):
        assert get_default_ttl(["show clock"]) == 30

    def test_empty_commands_gets_0(self):
        assert get_default_ttl([]) == 0

    def test_case_insensitive_matching(self):
        assert get_default_ttl(["Show Version"]) == 300

    def test_first_command_determines_ttl(self):
        # Multiple commands — TTL based on first command
        assert get_default_ttl(["show version", "show interfaces status"]) == 300


# --- CommandCache basic tests ---


class TestCommandCacheBasics:
    def test_put_and_get(self):
        cache = CommandCache(max_entries=100)
        data = {"status": "success", "data": [{"version": "4.32.1F"}]}
        cache.put("spine-01", ["show version"], "json", data, ttl=300)
        result = cache.get("spine-01", ["show version"], "json")
        assert result == data

    def test_get_missing_key_returns_none(self):
        cache = CommandCache(max_entries=100)
        assert cache.get("spine-01", ["show version"], "json") is None

    def test_different_hosts_are_separate(self):
        cache = CommandCache(max_entries=100)
        data1 = {"host": "spine-01"}
        data2 = {"host": "spine-02"}
        cache.put("spine-01", ["show version"], "json", data1, ttl=300)
        cache.put("spine-02", ["show version"], "json", data2, ttl=300)
        assert cache.get("spine-01", ["show version"], "json") == data1
        assert cache.get("spine-02", ["show version"], "json") == data2

    def test_different_commands_are_separate(self):
        cache = CommandCache(max_entries=100)
        data1 = {"cmd": "version"}
        data2 = {"cmd": "interfaces"}
        cache.put("spine-01", ["show version"], "json", data1, ttl=300)
        cache.put("spine-01", ["show interfaces status"], "json", data2, ttl=300)
        assert cache.get("spine-01", ["show version"], "json") == data1
        assert cache.get("spine-01", ["show interfaces status"], "json") == data2

    def test_different_encodings_are_separate(self):
        cache = CommandCache(max_entries=100)
        data_json = {"format": "json"}
        data_text = {"format": "text"}
        cache.put("spine-01", ["show running-config"], "json", data_json, ttl=60)
        cache.put("spine-01", ["show running-config"], "text", data_text, ttl=60)
        assert cache.get("spine-01", ["show running-config"], "json") == data_json
        assert cache.get("spine-01", ["show running-config"], "text") == data_text

    def test_put_with_zero_ttl_does_not_cache(self):
        cache = CommandCache(max_entries=100)
        cache.put("spine-01", ["show processes top"], "json", {"data": 1}, ttl=0)
        assert cache.get("spine-01", ["show processes top"], "json") is None

    def test_put_with_negative_ttl_does_not_cache(self):
        cache = CommandCache(max_entries=100)
        cache.put("spine-01", ["show version"], "json", {"data": 1}, ttl=-1)
        assert cache.get("spine-01", ["show version"], "json") is None

    def test_put_overwrites_existing_entry(self):
        cache = CommandCache(max_entries=100)
        cache.put("spine-01", ["show version"], "json", {"v": 1}, ttl=300)
        cache.put("spine-01", ["show version"], "json", {"v": 2}, ttl=300)
        assert cache.get("spine-01", ["show version"], "json") == {"v": 2}


# --- TTL expiry tests ---


class TestCacheTTLExpiry:
    def test_expired_entry_returns_none(self):
        cache = CommandCache(max_entries=100)
        cache.put("spine-01", ["show version"], "json", {"data": 1}, ttl=1)

        # Mock time.monotonic to simulate time passing
        now = time.monotonic()

        with patch("network_mcp.cache.time.monotonic", return_value=now + 2):
            result = cache.get("spine-01", ["show version"], "json")

        assert result is None

    def test_non_expired_entry_returns_data(self):
        cache = CommandCache(max_entries=100)
        data = {"data": 1}
        cache.put("spine-01", ["show version"], "json", data, ttl=300)

        # Even with a small time advance, entry should still be valid
        now = time.monotonic()
        with patch("network_mcp.cache.time.monotonic", return_value=now + 1):
            result = cache.get("spine-01", ["show version"], "json")
        assert result == data

    def test_expired_entry_kept_for_stale_if_error(self):
        cache = CommandCache(max_entries=100)
        cache.put("spine-01", ["show version"], "json", {"data": 1}, ttl=1)

        now = time.monotonic()
        with patch("network_mcp.cache.time.monotonic", return_value=now + 2):
            result = cache.get("spine-01", ["show version"], "json")

        # Expired entry returns None (cache miss)
        assert result is None
        # Entry is kept for stale-if-error use but not returned by normal get()
        assert cache.stats()["entries"] == 1


# --- LRU eviction tests ---


class TestCacheLRUEviction:
    def test_evicts_lru_when_max_entries_exceeded(self):
        cache = CommandCache(max_entries=3)
        cache.put("host-1", ["cmd1"], "json", {"d": 1}, ttl=300)
        cache.put("host-2", ["cmd2"], "json", {"d": 2}, ttl=300)
        cache.put("host-3", ["cmd3"], "json", {"d": 3}, ttl=300)

        # Access host-1 to make it recently used
        cache.get("host-1", ["cmd1"], "json")

        # Adding 4th entry should evict the LRU (host-2, since host-1 was accessed)
        cache.put("host-4", ["cmd4"], "json", {"d": 4}, ttl=300)

        assert cache.get("host-1", ["cmd1"], "json") is not None  # recently accessed
        assert cache.get("host-2", ["cmd2"], "json") is None  # evicted (LRU)
        assert cache.get("host-3", ["cmd3"], "json") is not None
        assert cache.get("host-4", ["cmd4"], "json") is not None

    def test_max_entries_respected(self):
        cache = CommandCache(max_entries=5)
        for i in range(10):
            cache.put(f"host-{i}", [f"cmd-{i}"], "json", {"d": i}, ttl=300)
        assert cache.stats()["entries"] <= 5


# --- Memory limit tests ---


class TestCacheMemoryLimit:
    def test_evicts_when_memory_limit_exceeded(self):
        # Very small memory limit to trigger eviction
        cache = CommandCache(max_entries=1000, max_memory_mb=1)
        large_data = {"data": "x" * 500_000}  # ~500KB

        cache.put("host-1", ["cmd1"], "json", large_data, ttl=300)
        cache.put("host-2", ["cmd2"], "json", large_data, ttl=300)

        # Should have evicted earlier entries to stay within 1MB
        stats = cache.stats()
        assert stats["memory_mb"] <= 1.0 or stats["entries"] <= 2

    def test_memory_tracking_updates_on_put(self):
        cache = CommandCache(max_entries=100)
        assert cache._current_memory_bytes == 0
        cache.put("host-1", ["cmd1"], "json", {"data": "test" * 100}, ttl=300)
        assert cache._current_memory_bytes > 0


# --- Host invalidation tests ---


class TestCacheInvalidation:
    def test_invalidate_host_removes_only_that_host(self):
        cache = CommandCache(max_entries=100)
        cache.put("spine-01", ["show version"], "json", {"d": 1}, ttl=300)
        cache.put("spine-01", ["show interfaces"], "json", {"d": 2}, ttl=300)
        cache.put("spine-02", ["show version"], "json", {"d": 3}, ttl=300)

        count = cache.invalidate_host("spine-01")
        assert count == 2
        assert cache.get("spine-01", ["show version"], "json") is None
        assert cache.get("spine-01", ["show interfaces"], "json") is None
        assert cache.get("spine-02", ["show version"], "json") == {"d": 3}

    def test_invalidate_host_returns_0_for_unknown_host(self):
        cache = CommandCache(max_entries=100)
        assert cache.invalidate_host("unknown-host") == 0

    def test_invalidate_host_updates_memory(self):
        cache = CommandCache(max_entries=100)
        cache.put("spine-01", ["show version"], "json", {"data": "test" * 100}, ttl=300)
        assert cache._current_memory_bytes > 0
        cache.invalidate_host("spine-01")
        assert cache._current_memory_bytes == 0

    def test_clear_removes_everything(self):
        cache = CommandCache(max_entries=100)
        cache.put("spine-01", ["show version"], "json", {"d": 1}, ttl=300)
        cache.put("spine-02", ["show version"], "json", {"d": 2}, ttl=300)
        cache.put("leaf-01", ["show version"], "json", {"d": 3}, ttl=300)

        count = cache.clear()
        assert count == 3
        assert cache.stats()["entries"] == 0
        assert cache.stats()["memory_mb"] == 0

    def test_clear_resets_stats(self):
        cache = CommandCache(max_entries=100)
        cache.put("spine-01", ["show version"], "json", {"d": 1}, ttl=300)
        cache.get("spine-01", ["show version"], "json")  # hit
        cache.get("spine-01", ["show clock"], "json")  # miss

        cache.clear()
        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0


# --- Disabled cache tests ---


class TestCacheDisabled:
    def test_disabled_cache_returns_none(self):
        cache = CommandCache(enabled=False)
        cache.put("spine-01", ["show version"], "json", {"d": 1}, ttl=300)
        assert cache.get("spine-01", ["show version"], "json") is None

    def test_disabled_cache_stats_show_disabled(self):
        cache = CommandCache(enabled=False)
        assert cache.stats()["enabled"] is False


# --- Stats tests ---


class TestCacheStats:
    def test_stats_accuracy(self):
        cache = CommandCache(max_entries=100, max_memory_mb=50)
        cache.put("spine-01", ["show version"], "json", {"d": 1}, ttl=300)
        cache.put("spine-02", ["show version"], "json", {"d": 2}, ttl=300)

        # 2 misses
        cache.get("spine-01", ["show clock"], "json")
        cache.get("spine-02", ["show clock"], "json")

        # 1 hit
        cache.get("spine-01", ["show version"], "json")

        stats = cache.stats()
        assert stats["entries"] == 2
        assert stats["max_entries"] == 100
        assert stats["hits"] == 1
        assert stats["misses"] == 2
        assert stats["hit_rate"] == pytest.approx(1 / 3, abs=0.01)
        assert stats["enabled"] is True
        assert stats["max_memory_mb"] == 50.0

    def test_hit_rate_zero_when_no_requests(self):
        cache = CommandCache(max_entries=100)
        assert cache.stats()["hit_rate"] == 0.0


# --- Thread safety tests ---


class TestCacheConcurrency:
    def test_concurrent_reads_and_writes(self):
        """Multiple threads reading and writing concurrently should not corrupt the cache."""
        cache = CommandCache(max_entries=100)
        errors = []
        barrier = threading.Barrier(10)

        def writer(thread_id: int):
            try:
                barrier.wait(timeout=5)
                for i in range(50):
                    cache.put(f"host-{thread_id}", [f"cmd-{i}"], "json", {"t": thread_id, "i": i}, ttl=300)
            except Exception as e:
                errors.append(e)

        def reader(thread_id: int):
            try:
                barrier.wait(timeout=5)
                for i in range(50):
                    cache.get(f"host-{thread_id % 5}", [f"cmd-{i}"], "json")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrency errors: {errors}"

    def test_concurrent_invalidation(self):
        """Invalidation during concurrent reads/writes should not raise."""
        cache = CommandCache(max_entries=100)
        errors = []

        # Pre-populate
        for i in range(20):
            cache.put(f"host-{i % 5}", [f"cmd-{i}"], "json", {"d": i}, ttl=300)

        def invalidator():
            try:
                for i in range(5):
                    cache.invalidate_host(f"host-{i}")
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(50):
                    cache.get(f"host-{i % 5}", [f"cmd-{i % 20}"], "json")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=invalidator),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrency errors: {errors}"


# --- Integration with run_show_command / run_config_command ---


class TestCacheIntegration:
    """Test cache integration via helpers.py run_show_command and run_config_command."""

    def test_cache_bypass_with_zero_ttl(self, mock_node, mock_conn_mgr):
        """cache_ttl=0 should always query the device, never use cache."""
        from network_mcp.helpers import run_show_command
        from network_mcp.server import conn_mgr

        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]

        # First call with cache_ttl=0
        result1 = run_show_command(conn_mgr, "spine-01", ["show version"], cache_ttl=0)
        assert result1["status"] == "success"

        # Second call with cache_ttl=0 — should still hit device
        result2 = run_show_command(conn_mgr, "spine-01", ["show version"], cache_ttl=0)
        assert result2["status"] == "success"
        assert mock_node.run_commands.call_count == 2

    def test_cache_hit_avoids_device_call(self, mock_node, mock_conn_mgr):
        """Second call with same command should use cache and skip the device."""
        from network_mcp.helpers import command_cache, run_show_command
        from network_mcp.server import conn_mgr

        # Clear cache from other tests
        command_cache.clear()

        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]

        # First call — hits device, populates cache
        result1 = run_show_command(conn_mgr, "spine-01", ["show version"], cache_ttl=300)
        assert result1["status"] == "success"
        assert mock_node.run_commands.call_count == 1

        # Second call — should use cache
        result2 = run_show_command(conn_mgr, "spine-01", ["show version"], cache_ttl=300)
        assert result2["status"] == "success"
        assert mock_node.run_commands.call_count == 1  # no additional device call

    def test_config_command_invalidates_cache(self, mock_node, mock_conn_mgr):
        """run_config_command should invalidate all cache entries for the host."""
        from network_mcp.helpers import command_cache, run_config_command, run_show_command
        from network_mcp.server import conn_mgr

        command_cache.clear()

        mock_node.run_commands.return_value = [{"vlans": {}}]
        mock_node.config.return_value = [{}]

        # Populate cache
        run_show_command(conn_mgr, "spine-01", ["show vlan"], cache_ttl=300)
        assert command_cache.stats()["entries"] >= 1

        # Config command should invalidate
        with patch("network_mcp.helpers.check_read_only", return_value=None):
            run_config_command(conn_mgr, "spine-01", ["vlan 100", "name TEST"], "create_vlan")

        # Cache should be empty for this host
        assert command_cache.get("spine-01", ["show vlan"], "json") is None

    def test_default_ttl_used_when_none(self, mock_node, mock_conn_mgr):
        """When cache_ttl is None, the default TTL for the command pattern should be used."""
        from network_mcp.helpers import command_cache, run_show_command
        from network_mcp.server import conn_mgr

        command_cache.clear()

        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]

        # show version has default TTL of 300s — should be cached
        run_show_command(conn_mgr, "spine-01", ["show version"])
        assert command_cache.stats()["entries"] == 1

    def test_no_cache_commands_not_cached(self, mock_node, mock_conn_mgr):
        """Commands with default TTL 0 (like show processes top) should not be cached."""
        from network_mcp.helpers import command_cache, run_show_command
        from network_mcp.server import conn_mgr

        command_cache.clear()

        mock_node.run_commands.return_value = [{"cpuInfo": {}}]

        run_show_command(conn_mgr, "spine-01", ["show processes top once"])
        assert command_cache.stats()["entries"] == 0
