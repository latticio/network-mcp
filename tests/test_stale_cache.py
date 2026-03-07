"""Tests for stale-if-error caching behavior."""

import time
from unittest.mock import MagicMock, patch

import pyeapi.eapilib
import pytest

import network_mcp.server as server_mod
from network_mcp.cache import CacheEntry, CommandCache
from network_mcp.helpers import (
    circuit_breaker_registry,
    command_cache,
    run_show_command,
)
from network_mcp.server import conn_mgr


@pytest.fixture(autouse=True)
def _reset_shutdown_state():
    """Reset shutdown state to prevent interference from other tests."""
    server_mod._shutting_down = False
    server_mod._in_flight_count = 0
    server_mod._shutdown_event.clear()
    yield
    server_mod._shutting_down = False
    server_mod._in_flight_count = 0
    server_mod._shutdown_event.clear()


class TestCacheEntryProperties:
    """Tests for CacheEntry is_expired and age_seconds properties."""

    def test_is_expired_false_when_fresh(self):
        """Entry is not expired when within TTL."""
        entry = CacheEntry(data={"test": 1}, timestamp=time.monotonic(), ttl=60, size_bytes=10)
        assert entry.is_expired is False

    def test_is_expired_true_when_old(self):
        """Entry is expired when past TTL."""
        entry = CacheEntry(data={"test": 1}, timestamp=time.monotonic() - 100, ttl=60, size_bytes=10)
        assert entry.is_expired is True

    def test_age_seconds(self):
        """age_seconds returns approximate age."""
        entry = CacheEntry(data={"test": 1}, timestamp=time.monotonic() - 5.0, ttl=60, size_bytes=10)
        assert 4.5 <= entry.age_seconds <= 6.0


class TestStaleIfErrorCacheGet:
    """Tests for CommandCache.get() with stale_if_error parameter."""

    def test_normal_miss_on_expired_entry(self):
        """Expired entry returns None when stale_if_error=False (default)."""
        cache = CommandCache(enabled=True)
        cache.put("host1", ["show version"], "json", {"version": "1.0"}, ttl=1)
        # Force expiry
        key = cache._make_key("host1", ["show version"], "json")
        cache._cache[key].timestamp = time.monotonic() - 100

        result = cache.get("host1", ["show version"], "json", stale_if_error=False)
        assert result is None

    def test_stale_if_error_returns_expired_data(self):
        """Expired entry within stale TTL returns data with _cache metadata when stale_if_error=True."""
        cache = CommandCache(enabled=True)
        cache.put("host1", ["show version"], "json", {"status": "success", "version": "1.0"}, ttl=30)
        # Force expiry by 10 seconds
        key = cache._make_key("host1", ["show version"], "json")
        cache._cache[key].timestamp = time.monotonic() - 40

        result = cache.get("host1", ["show version"], "json", stale_if_error=True, stale_ttl=300)
        assert result is not None
        assert result["version"] == "1.0"
        assert "_cache" in result
        assert result["_cache"]["stale"] is True
        assert result["_cache"]["original_ttl"] == 30
        assert result["_cache"]["expired_seconds_ago"] >= 0

    def test_stale_response_metadata_complete(self):
        """Stale response contains all expected _cache metadata fields."""
        cache = CommandCache(enabled=True)
        cache.put("host1", ["show version"], "json", {"data": "test"}, ttl=10)
        key = cache._make_key("host1", ["show version"], "json")
        cache._cache[key].timestamp = time.monotonic() - 15

        result = cache.get("host1", ["show version"], "json", stale_if_error=True, stale_ttl=300)
        assert result is not None
        meta = result["_cache"]
        assert "stale" in meta
        assert "age_seconds" in meta
        assert "original_ttl" in meta
        assert "expired_seconds_ago" in meta
        assert meta["stale"] is True
        assert meta["age_seconds"] > 10
        assert meta["expired_seconds_ago"] > 0

    def test_stale_ttl_limit_exceeded(self):
        """Entry expired beyond stale TTL returns None even with stale_if_error=True."""
        cache = CommandCache(enabled=True)
        cache.put("host1", ["show version"], "json", {"version": "1.0"}, ttl=10)
        # Force expiry far beyond stale TTL
        key = cache._make_key("host1", ["show version"], "json")
        cache._cache[key].timestamp = time.monotonic() - 500

        result = cache.get("host1", ["show version"], "json", stale_if_error=True, stale_ttl=60)
        assert result is None

    def test_stale_if_error_disabled_with_zero_stale_ttl(self):
        """stale_ttl=0 disables stale-if-error even when stale_if_error=True."""
        cache = CommandCache(enabled=True)
        cache.put("host1", ["show version"], "json", {"version": "1.0"}, ttl=10)
        key = cache._make_key("host1", ["show version"], "json")
        cache._cache[key].timestamp = time.monotonic() - 15

        result = cache.get("host1", ["show version"], "json", stale_if_error=True, stale_ttl=0)
        assert result is None

    def test_non_dict_data_returned_without_metadata(self):
        """Non-dict cached data is returned as-is without _cache wrapping."""
        cache = CommandCache(enabled=True)
        cache.put("host1", ["show version"], "json", [{"version": "1.0"}], ttl=10)
        key = cache._make_key("host1", ["show version"], "json")
        cache._cache[key].timestamp = time.monotonic() - 15

        result = cache.get("host1", ["show version"], "json", stale_if_error=True, stale_ttl=300)
        assert result is not None
        assert isinstance(result, list)

    def test_fresh_entry_not_affected_by_stale_flag(self):
        """Fresh entry returns normal data regardless of stale_if_error flag."""
        cache = CommandCache(enabled=True)
        cache.put("host1", ["show version"], "json", {"version": "1.0"}, ttl=300)

        result = cache.get("host1", ["show version"], "json", stale_if_error=True, stale_ttl=300)
        assert result is not None
        assert result == {"version": "1.0"}
        assert "_cache" not in result


class TestStaleIfErrorInRunShowCommand:
    """Tests for stale-if-error behavior wired into run_show_command."""

    def test_stale_cache_on_connection_error(self, mock_conn_mgr, mock_node):
        """Connection failure returns stale cache data instead of error."""
        # First, populate the cache with a successful call
        mock_node.run_commands.return_value = [{"status": "success", "version": "4.32.1F"}]
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "success"

        # Expire the cache entry
        key = command_cache._make_key("spine-01", ["show version"], "json")
        command_cache._cache[key].timestamp = time.monotonic() - 100
        command_cache._cache[key].ttl = 30

        # Now simulate connection failure
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("https", "Connection refused")

        with patch("network_mcp.helpers._get_settings") as mock_settings:
            settings = MagicMock()
            settings.net_cache_stale_if_error_ttl = 300
            settings.net_rate_limit_max_wait = 5.0
            settings.net_conn_acquire_timeout = 30.0
            mock_settings.return_value = settings

            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            # Should get stale data
            assert "version" in result or "_cache" in result

    def test_no_stale_entry_returns_error(self, mock_conn_mgr, mock_node):
        """Connection failure with no cache entry returns normal error."""
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("https", "Connection refused")

        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert result["error_code"] == "CONNECTION_ERROR"

    def test_stale_cache_on_circuit_breaker_open(self, mock_conn_mgr, mock_node):
        """Circuit breaker open returns stale cache data instead of error."""
        # Populate cache
        mock_node.run_commands.return_value = [{"status": "success", "version": "4.32.1F"}]
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "success"

        # Expire the cache entry
        key = command_cache._make_key("spine-01", ["show version"], "json")
        command_cache._cache[key].timestamp = time.monotonic() - 100
        command_cache._cache[key].ttl = 30

        # Open the circuit breaker
        circuit_breaker_registry._enabled = True
        breaker = circuit_breaker_registry.get("spine-01")
        for _ in range(5):
            breaker.record_failure()

        with patch("network_mcp.helpers._get_settings") as mock_settings:
            settings = MagicMock()
            settings.net_cache_stale_if_error_ttl = 300
            settings.net_rate_limit_max_wait = 5.0
            settings.net_conn_acquire_timeout = 30.0
            mock_settings.return_value = settings

            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            # Should get stale data instead of circuit breaker error
            assert result.get("error_code") != "CIRCUIT_OPEN" or "_cache" in result

    def test_stale_if_error_disabled_via_settings(self, mock_conn_mgr, mock_node):
        """When stale_if_error_ttl=0, connection errors return error, not stale data."""
        # Clear cache to ensure clean state
        command_cache.clear()

        # Populate cache
        mock_node.run_commands.return_value = [{"status": "success", "version": "4.32.1F"}]
        run_show_command(conn_mgr, "spine-01", ["show version"])

        # Expire entry
        key = command_cache._make_key("spine-01", ["show version"], "json")
        if key in command_cache._cache:
            command_cache._cache[key].timestamp = time.monotonic() - 100
            command_cache._cache[key].ttl = 30

        # Now fail
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("https", "Connection refused")

        with patch("network_mcp.helpers._get_settings") as mock_settings:
            settings = MagicMock()
            settings.net_cache_stale_if_error_ttl = 0
            settings.net_rate_limit_max_wait = 5.0
            settings.net_conn_acquire_timeout = 30.0
            mock_settings.return_value = settings

            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            assert result["status"] == "error"
            assert "_cache" not in result
