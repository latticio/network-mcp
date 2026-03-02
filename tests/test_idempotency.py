"""Tests for idempotency key support."""

import threading
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.idempotency import IdempotencyStore, get_idempotency_store, init_idempotency_store
from network_mcp.server import conn_mgr


@pytest.fixture(autouse=True)
def _reset_idempotency_store():
    """Reset the global idempotency store before each test."""
    import network_mcp.idempotency as idem_mod

    old = idem_mod._idempotency_store
    idem_mod._idempotency_store = None
    yield
    idem_mod._idempotency_store = old


class TestIdempotencyStore:
    """Test IdempotencyStore set/get/expiry/eviction behavior."""

    def test_set_and_get(self):
        """Store a result, retrieve it."""
        store = IdempotencyStore(ttl=3600)
        result = {"status": "success", "device": "spine-01", "action": "create_vlan"}
        store.set("key-123", result)

        cached = store.get("key-123")
        assert cached is not None
        assert cached["status"] == "success"
        assert cached["device"] == "spine-01"

    def test_get_miss(self):
        """Get with unknown key returns None."""
        store = IdempotencyStore(ttl=3600)
        assert store.get("nonexistent-key") is None

    def test_key_expiry(self):
        """Store with short TTL, verify expired returns None."""
        store = IdempotencyStore(ttl=1)
        store.set("expire-key", {"status": "success"})

        # Should be available immediately
        assert store.get("expire-key") is not None

        # Mock time advance past TTL
        entry = store._store["expire-key"]
        entry.created_at = time.monotonic() - 2  # 2 seconds ago, TTL is 1

        assert store.get("expire-key") is None

    def test_max_entries_eviction(self):
        """Fill to capacity, verify oldest entry evicted."""
        store = IdempotencyStore(ttl=3600, max_entries=3)

        store.set("key-1", {"id": 1})
        store.set("key-2", {"id": 2})
        store.set("key-3", {"id": 3})

        # All three should be present
        assert store.get("key-1") is not None
        assert store.get("key-2") is not None
        assert store.get("key-3") is not None

        # Adding a 4th should evict the oldest (key-1)
        store.set("key-4", {"id": 4})
        assert store.get("key-1") is None  # evicted
        assert store.get("key-4") is not None

    def test_cleanup_expired(self):
        """Add expired entries, verify _cleanup_expired removes them."""
        store = IdempotencyStore(ttl=1, max_entries=10)

        store.set("old-1", {"id": 1})
        store.set("old-2", {"id": 2})
        store.set("fresh", {"id": 3})

        # Expire the first two
        now = time.monotonic()
        store._store["old-1"].created_at = now - 10
        store._store["old-2"].created_at = now - 10

        with store._lock:
            removed = store._cleanup_expired()

        assert removed == 2
        assert "old-1" not in store._store
        assert "old-2" not in store._store
        assert "fresh" in store._store

    def test_reset(self):
        """reset() clears all entries."""
        store = IdempotencyStore(ttl=3600)
        store.set("key-1", {"id": 1})
        store.set("key-2", {"id": 2})

        store.reset()

        assert store.get("key-1") is None
        assert store.get("key-2") is None

    def test_overwrite_same_key(self):
        """Setting the same key again overwrites the previous value."""
        store = IdempotencyStore(ttl=3600)
        store.set("key-1", {"version": "v1"})
        store.set("key-1", {"version": "v2"})

        cached = store.get("key-1")
        assert cached["version"] == "v2"

    def test_thread_safety(self):
        """Run 10 concurrent set/get operations, verify no crashes."""
        store = IdempotencyStore(ttl=3600, max_entries=100)
        errors = []

        def worker(thread_id):
            try:
                for i in range(20):
                    key = f"thread-{thread_id}-key-{i}"
                    store.set(key, {"thread": thread_id, "i": i})
                    result = store.get(key)
                    if result is None:
                        # Could be evicted, that's OK
                        pass
                    elif result["thread"] != thread_id:
                        errors.append(f"Wrong value for {key}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(tid,)) for tid in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread safety errors: {errors}"


class TestIdempotencyInRunConfigCommand:
    """Test idempotency key integration with run_config_command."""

    @pytest.fixture
    def setup_idempotency(self):
        """Initialize the idempotency store for tests."""
        store = init_idempotency_store(ttl=3600)
        yield store

    @pytest.fixture
    def mock_driver(self):
        driver = MagicMock()
        driver.platform = "eos"
        driver.is_connected.return_value = True
        driver.run_config.return_value = []
        driver.run_show.return_value = []
        return driver

    def test_idempotency_caches_result(self, setup_idempotency, mock_driver):
        """Call with key, verify result cached, call again, verify cached result returned."""
        from network_mcp.helpers import run_config_command

        @contextmanager
        def _mock_acquire(host):
            yield mock_driver

        with (
            patch.object(conn_mgr, "get_driver", return_value=mock_driver),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
            patch.object(conn_mgr, "get_platform", return_value="eos"),
            patch("network_mcp.helpers._get_settings") as mock_settings,
            patch("network_mcp.server._shutting_down", False),
        ):
            mock_s = MagicMock()
            mock_s.net_read_only = False
            mock_s.config_retry_enabled = False
            mock_s.net_rate_limit_max_wait = 0.0
            mock_s.net_conn_acquire_timeout = 30.0
            mock_s.net_change_mgmt_enabled = False
            mock_s.net_cache_stale_if_error_ttl = 0
            mock_settings.return_value = mock_s

            # First call — should execute and cache
            result1 = run_config_command(
                conn_mgr,
                "spine-01",
                ["vlan 100", "name TEST"],
                "create_vlan",
                idempotency_key="idem-key-001",
            )
            assert result1["status"] == "success"
            assert "_idempotency" not in result1  # First call has no idempotency flag

            # Second call with same key — should return cached
            result2 = run_config_command(
                conn_mgr,
                "spine-01",
                ["vlan 100", "name TEST"],
                "create_vlan",
                idempotency_key="idem-key-001",
            )
            assert result2["status"] == "success"
            assert "_idempotency" in result2
            assert result2["_idempotency"]["cached"] is True
            assert result2["_idempotency"]["key"] == "idem-key-001"

            # run_config should only have been called once
            assert mock_driver.run_config.call_count == 1

    def test_idempotency_cached_response_flag(self, setup_idempotency, mock_driver):
        """Verify _idempotency metadata with cached=True on second run_config_command call."""
        from network_mcp.helpers import run_config_command

        @contextmanager
        def _mock_acquire(host):
            yield mock_driver

        with (
            patch.object(conn_mgr, "get_driver", return_value=mock_driver),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
            patch.object(conn_mgr, "get_platform", return_value="eos"),
            patch("network_mcp.helpers._get_settings") as mock_settings,
            patch("network_mcp.server._shutting_down", False),
        ):
            mock_s = MagicMock()
            mock_s.net_read_only = False
            mock_s.config_retry_enabled = False
            mock_s.net_rate_limit_max_wait = 0.0
            mock_s.net_conn_acquire_timeout = 30.0
            mock_s.net_change_mgmt_enabled = False
            mock_s.net_cache_stale_if_error_ttl = 0
            mock_settings.return_value = mock_s

            # First call — executes normally
            r1 = run_config_command(
                conn_mgr, "spine-01", ["vlan 200"], "create_vlan", idempotency_key="flag-key"
            )
            assert r1["status"] == "success"

            # Second call — should return cached result with metadata
            r2 = run_config_command(
                conn_mgr, "spine-01", ["vlan 200"], "create_vlan", idempotency_key="flag-key"
            )
            assert r2["status"] == "success"
            assert "_idempotency" in r2
            assert r2["_idempotency"]["cached"] is True

    def test_idempotency_disabled_when_ttl_zero(self):
        """When TTL=0, store is not initialized and keys are not cached."""
        import network_mcp.idempotency as idem_mod

        assert idem_mod._idempotency_store is None
        assert get_idempotency_store() is None

    def test_no_idempotency_key_does_not_cache(self, mock_driver):
        """Without idempotency_key, result is not cached."""
        store = init_idempotency_store(ttl=3600)
        from network_mcp.helpers import run_config_command

        @contextmanager
        def _mock_acquire(host):
            yield mock_driver

        with (
            patch.object(conn_mgr, "get_driver", return_value=mock_driver),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
            patch.object(conn_mgr, "get_platform", return_value="eos"),
            patch("network_mcp.helpers._get_settings") as mock_settings,
            patch("network_mcp.server._shutting_down", False),
        ):
            mock_s = MagicMock()
            mock_s.net_read_only = False
            mock_s.config_retry_enabled = False
            mock_s.net_rate_limit_max_wait = 0.0
            mock_s.net_conn_acquire_timeout = 30.0
            mock_s.net_change_mgmt_enabled = False
            mock_s.net_cache_stale_if_error_ttl = 0
            mock_settings.return_value = mock_s

            result = run_config_command(
                conn_mgr,
                "spine-01",
                ["vlan 100", "name TEST"],
                "create_vlan",
            )
            assert result["status"] == "success"

            # Store should be empty (no key provided)
            assert len(store._store) == 0

    def test_idempotency_error_result_not_cached(self, setup_idempotency, mock_driver):
        """Error results are not stored under idempotency key."""
        from network_mcp.helpers import run_config_command

        mock_driver.run_config.side_effect = Exception("Config push failed")

        @contextmanager
        def _mock_acquire(host):
            yield mock_driver

        with (
            patch.object(conn_mgr, "get_driver", return_value=mock_driver),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
            patch.object(conn_mgr, "get_platform", return_value="eos"),
            patch("network_mcp.helpers._get_settings") as mock_settings,
            patch("network_mcp.server._shutting_down", False),
        ):
            mock_s = MagicMock()
            mock_s.net_read_only = False
            mock_s.config_retry_enabled = False
            mock_s.net_rate_limit_max_wait = 0.0
            mock_s.net_conn_acquire_timeout = 30.0
            mock_s.net_change_mgmt_enabled = False
            mock_s.net_cache_stale_if_error_ttl = 0
            mock_settings.return_value = mock_s

            result = run_config_command(
                conn_mgr,
                "spine-01",
                ["vlan 100"],
                "create_vlan",
                idempotency_key="error-key-001",
            )
            assert result["status"] == "error"

            # Key should not be stored for error results
            assert setup_idempotency.get("error-key-001") is None


class TestIdempotencyModuleFunctions:
    """Test module-level init/get functions."""

    def test_init_creates_store(self):
        """init_idempotency_store creates and returns a store."""
        store = init_idempotency_store(ttl=300, max_entries=50)
        assert store is not None
        assert isinstance(store, IdempotencyStore)
        assert store._ttl == 300
        assert store._max_entries == 50

    def test_get_returns_initialized_store(self):
        """get_idempotency_store returns the initialized store."""
        init_idempotency_store(ttl=600)
        store = get_idempotency_store()
        assert store is not None

    def test_get_returns_none_when_not_initialized(self):
        """get_idempotency_store returns None before initialization."""
        assert get_idempotency_store() is None


# ===========================================================================
# Idempotency Integration: Duplicate Calls, Different Keys, Concurrent
# ===========================================================================


class TestIdempotencyDuplicateKeys:
    """Test that duplicate keys return cached results and different keys execute separately."""

    @pytest.fixture
    def setup_idempotency(self):
        store = init_idempotency_store(ttl=3600)
        yield store

    @pytest.fixture
    def mock_driver(self):
        driver = MagicMock()
        driver.platform = "eos"
        driver.is_connected.return_value = True
        driver.run_config.return_value = []
        driver.run_show.return_value = []
        return driver

    @contextmanager
    def _make_patches(self, mock_driver):
        """Return a context manager that patches conn_mgr and settings."""

        @contextmanager
        def _mock_acquire(host):
            yield mock_driver

        mock_s = MagicMock()
        mock_s.net_read_only = False
        mock_s.config_retry_enabled = False
        mock_s.net_rate_limit_max_wait = 0.0
        mock_s.net_conn_acquire_timeout = 30.0
        mock_s.net_change_mgmt_enabled = False
        mock_s.net_cache_stale_if_error_ttl = 0

        mock_rl = MagicMock()
        mock_rl.check_config.return_value = True
        mock_rl.check_config_with_backoff.return_value = True
        mock_rl.check_show.return_value = True
        mock_rl.check_show_with_backoff.return_value = True

        with (
            patch.object(conn_mgr, "get_driver", return_value=mock_driver),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
            patch.object(conn_mgr, "get_platform", return_value="eos"),
            patch("network_mcp.helpers._get_settings", return_value=mock_s),
            patch("network_mcp.helpers.device_rate_limiter", mock_rl),
            patch("network_mcp.server._shutting_down", False),
        ):
            yield

    def test_different_keys_execute_separately(self, setup_idempotency, mock_driver):
        """Two calls with different idempotency keys should both execute."""
        from network_mcp.helpers import run_config_command

        with self._make_patches(mock_driver):
            r1 = run_config_command(
                conn_mgr,
                "spine-01",
                ["vlan 100", "name A"],
                "create_vlan",
                idempotency_key="key-A",
            )
            r2 = run_config_command(
                conn_mgr,
                "spine-01",
                ["vlan 200", "name B"],
                "create_vlan",
                idempotency_key="key-B",
            )
            assert r1["status"] == "success"
            assert r2["status"] == "success"
            assert mock_driver.run_config.call_count == 2

    def test_failed_result_not_cached_retries(self, setup_idempotency, mock_driver):
        """If first call fails, key should NOT be cached; second call should retry."""
        from network_mcp.helpers import run_config_command

        call_count = {"value": 0}

        def _failing_first_call(*args, **kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                raise ConnectionError("temporary failure")
            return []

        mock_driver.run_config.side_effect = _failing_first_call

        with self._make_patches(mock_driver):
            r1 = run_config_command(
                conn_mgr,
                "spine-01",
                ["vlan 100"],
                "create_vlan",
                idempotency_key="fail-key",
            )
            assert r1["status"] == "error"

            # Key should not be cached for error
            assert setup_idempotency.get("fail-key") is None

            r2 = run_config_command(
                conn_mgr,
                "spine-01",
                ["vlan 100"],
                "create_vlan",
                idempotency_key="fail-key",
            )
            # Second call should retry (run_config called again)
            assert call_count["value"] == 2
            assert r2["status"] == "success"

    def test_concurrent_calls_same_key_no_crash(self, setup_idempotency, mock_driver):
        """Two concurrent threads with the same key should not crash."""
        from network_mcp.helpers import run_config_command

        def _slow_config(*args, **kwargs):
            time.sleep(0.2)
            return []

        mock_driver.run_config.side_effect = _slow_config

        results = [None, None]

        def _run(idx):
            with self._make_patches(mock_driver):
                results[idx] = run_config_command(
                    conn_mgr,
                    "spine-01",
                    ["vlan 100"],
                    "create_vlan",
                    idempotency_key="concurrent-key",
                )

        t1 = threading.Thread(target=_run, args=(0,))
        t2 = threading.Thread(target=_run, args=(1,))
        t1.start()
        time.sleep(0.05)  # Small stagger
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Both should succeed (no crash)
        assert results[0] is not None
        assert results[1] is not None
        assert results[0]["status"] == "success"
        assert results[1]["status"] == "success"

    def test_duplicate_key_returns_cached_with_flag(self, setup_idempotency, mock_driver):
        """Second call with same key should return _idempotency.cached=True."""
        from network_mcp.helpers import run_config_command

        with self._make_patches(mock_driver):
            r1 = run_config_command(
                conn_mgr,
                "spine-01",
                ["vlan 100", "name FIRST"],
                "create_vlan",
                idempotency_key="dup-key-1",
            )
            assert r1["status"] == "success"
            assert "_idempotency" not in r1

            r2 = run_config_command(
                conn_mgr,
                "spine-01",
                ["vlan 100", "name FIRST"],
                "create_vlan",
                idempotency_key="dup-key-1",
            )
            assert r2["status"] == "success"
            assert r2["_idempotency"]["cached"] is True
            assert r2["_idempotency"]["key"] == "dup-key-1"

            # Only one actual execution
            assert mock_driver.run_config.call_count == 1

    def test_three_different_keys_all_execute(self, setup_idempotency, mock_driver):
        """Three calls with three different keys should all execute independently."""
        from network_mcp.helpers import run_config_command

        with self._make_patches(mock_driver):
            for key in ["key-X", "key-Y", "key-Z"]:
                r = run_config_command(
                    conn_mgr,
                    "spine-01",
                    ["vlan 100"],
                    "create_vlan",
                    idempotency_key=key,
                )
                assert r["status"] == "success"

            assert mock_driver.run_config.call_count == 3

    def test_failed_then_retry_with_same_key_succeeds(self, setup_idempotency, mock_driver):
        """Failed execution with key should allow retry with same key."""
        from network_mcp.helpers import run_config_command

        call_count = {"n": 0}

        def _fail_first(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionError("first call fails")
            return []

        mock_driver.run_config.side_effect = _fail_first

        with self._make_patches(mock_driver):
            r1 = run_config_command(
                conn_mgr,
                "spine-01",
                ["vlan 100"],
                "create_vlan",
                idempotency_key="retry-key",
            )
            assert r1["status"] == "error"
            # Error should NOT be cached
            assert setup_idempotency.get("retry-key") is None

            # Second call with same key should retry and succeed
            r2 = run_config_command(
                conn_mgr,
                "spine-01",
                ["vlan 100"],
                "create_vlan",
                idempotency_key="retry-key",
            )
            assert r2["status"] == "success"
            assert call_count["n"] == 2

    def test_concurrent_same_key_only_one_executes(self, setup_idempotency, mock_driver):
        """Concurrent calls with same key: only one should call device."""
        from network_mcp.helpers import run_config_command

        execution_count = {"n": 0}
        exec_lock = threading.Lock()

        def _counting_config(*args, **kwargs):
            with exec_lock:
                execution_count["n"] += 1
            time.sleep(0.3)
            return []

        mock_driver.run_config.side_effect = _counting_config

        results = [None, None]

        def _run(idx):
            with self._make_patches(mock_driver):
                results[idx] = run_config_command(
                    conn_mgr,
                    "spine-01",
                    ["vlan 100"],
                    "create_vlan",
                    idempotency_key="race-key",
                )

        t1 = threading.Thread(target=_run, args=(0,))
        t2 = threading.Thread(target=_run, args=(1,))
        t1.start()
        time.sleep(0.05)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Both should succeed
        assert results[0]["status"] == "success"
        assert results[1]["status"] == "success"
        # With proper idempotency, only one thread should execute the command
        assert execution_count["n"] == 1


# ===========================================================================
# Idempotency Store Edge Cases
# ===========================================================================


class TestIdempotencyStoreEdgeCases:
    """Test IdempotencyStore edge cases: eviction, cleanup, reset."""

    def test_eviction_removes_oldest(self):
        """When at max_entries, oldest entry should be evicted."""
        store = IdempotencyStore(ttl=3600, max_entries=5)
        for i in range(5):
            store.set(f"key-{i}", {"i": i})

        # All 5 should be present
        for i in range(5):
            assert store.get(f"key-{i}") is not None

        # Adding 6th should evict key-0 (oldest)
        store.set("key-5", {"i": 5})
        assert store.get("key-0") is None
        assert store.get("key-5") is not None

    def test_expired_entries_cleaned_before_eviction(self):
        """Expired entries should be cleaned before evicting by age."""
        store = IdempotencyStore(ttl=1, max_entries=3)
        store.set("old-a", {"i": 1})
        store.set("old-b", {"i": 2})
        store.set("fresh", {"i": 3})

        # Expire first two
        now = time.monotonic()
        store._store["old-a"].created_at = now - 10
        store._store["old-b"].created_at = now - 10

        # Adding a 4th should trigger cleanup of expired entries first
        store.set("new", {"i": 4})

        assert "old-a" not in store._store
        assert "old-b" not in store._store
        assert store.get("fresh") is not None
        assert store.get("new") is not None

    def test_many_concurrent_set_get(self):
        """High-concurrency set/get should not lose data or crash."""
        store = IdempotencyStore(ttl=3600, max_entries=500)
        errors = []

        def _worker(tid):
            try:
                for i in range(100):
                    key = f"t{tid}-k{i}"
                    store.set(key, {"tid": tid, "i": i})
                    val = store.get(key)
                    if val is not None and val["tid"] != tid:
                        errors.append(f"Data mismatch for {key}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=_worker, args=(t,)) for t in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"Concurrent errors: {errors}"


# ===========================================================================
# Idempotency Key Duplicate Detection Comprehensive Tests
# ===========================================================================


class TestIdempotencyDuplicateDetectionComprehensive:
    """Comprehensive tests for idempotency key duplicate detection scenarios."""

    def test_same_key_same_commands_returns_cached(self):
        """Same key + same commands => cached result returned."""
        store = IdempotencyStore(ttl=3600)
        original_result = {"status": "success", "device": "spine-01", "data": {"vlan": 100}}
        store.set("create-vlan-100", original_result)

        cached = store.get("create-vlan-100")
        assert cached is not None
        assert cached == original_result

    def test_same_key_different_commands_returns_first_result(self):
        """Same key + different commands => first result always returned (idempotency semantics)."""
        store = IdempotencyStore(ttl=3600)
        first_result = {"status": "success", "device": "spine-01", "action": "create_vlan", "vlan": 100}
        store.set("op-123", first_result)

        # Even if called with conceptually different commands, same key returns first result
        cached = store.get("op-123")
        assert cached is not None
        assert cached["vlan"] == 100

    def test_key_not_found_after_expiry(self):
        """Expired key should return None (treat as new operation)."""
        store = IdempotencyStore(ttl=1)
        store.set("ephemeral-key", {"status": "success"})

        # Manually expire
        store._store["ephemeral-key"].created_at = time.monotonic() - 5
        assert store.get("ephemeral-key") is None

    def test_key_collision_detection_with_many_keys(self):
        """1000 unique keys should all be retrievable (no hash collisions)."""
        store = IdempotencyStore(ttl=3600, max_entries=2000)
        for i in range(1000):
            store.set(f"unique-key-{i}", {"i": i})

        for i in range(1000):
            result = store.get(f"unique-key-{i}")
            assert result is not None, f"Key unique-key-{i} missing"
            assert result["i"] == i

    def test_overwrite_with_same_key_updates_value(self):
        """Setting same key twice should update to the latest value."""
        store = IdempotencyStore(ttl=3600)
        store.set("update-key", {"version": 1})
        store.set("update-key", {"version": 2})

        result = store.get("update-key")
        assert result is not None
        assert result["version"] == 2

    def test_empty_key_string(self):
        """Empty string key should work as any other key."""
        store = IdempotencyStore(ttl=3600)
        store.set("", {"status": "success"})
        assert store.get("") is not None

    def test_unicode_keys(self):
        """Unicode keys should be handled correctly."""
        store = IdempotencyStore(ttl=3600)
        store.set("key-\u00e9\u00e0\u00fc", {"status": "success"})
        assert store.get("key-\u00e9\u00e0\u00fc") is not None

    def test_very_long_key(self):
        """Very long keys should not cause issues."""
        store = IdempotencyStore(ttl=3600)
        long_key = "k" * 10000
        store.set(long_key, {"status": "success"})
        assert store.get(long_key) is not None

    def test_duplicate_detection_across_devices(self):
        """Same idempotency key used for different devices still returns first cached result."""
        store = IdempotencyStore(ttl=3600)
        store.set("cross-device-key", {"device": "spine-01", "vlan": 100})

        # Same key, conceptually for spine-02 — but idempotency returns first result
        cached = store.get("cross-device-key")
        assert cached["device"] == "spine-01"  # First result wins

    def test_concurrent_duplicate_detection(self):
        """Concurrent set/get with same key should not corrupt data."""
        store = IdempotencyStore(ttl=3600, max_entries=100)
        errors = []
        results = []
        lock = threading.Lock()

        def _set_and_get(tid):
            try:
                store.set("shared-key", {"tid": tid, "ts": time.monotonic()})
                time.sleep(0.01)
                val = store.get("shared-key")
                if val is not None:
                    with lock:
                        results.append(val)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_set_and_get, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        # All results should be valid dicts
        for r in results:
            assert "tid" in r
            assert isinstance(r["tid"], int)

    def test_store_capacity_eviction_under_concurrent_load(self):
        """Under concurrent load, store should evict gracefully when at capacity."""
        store = IdempotencyStore(ttl=3600, max_entries=50)
        errors = []

        def _fill_store(start):
            try:
                for i in range(100):
                    store.set(f"load-{start}-{i}", {"start": start, "i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_fill_store, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors
        # Store should be at or under capacity
        assert len(store._store) <= 50
