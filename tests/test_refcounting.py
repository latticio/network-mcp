"""Tests for connection pool reference counting and pool_stats."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.connection import ConnectionManager
from tests.conftest import make_test_settings

# --- Reference counting basics ---


@patch("network_mcp.driver.pyeapi.connect")
def test_acquire_context_manager_returns_driver(mock_connect):
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings())

    with mgr.acquire("10.0.0.1") as driver:
        assert driver is not None
        assert driver.is_connected()


@patch("network_mcp.driver.pyeapi.connect")
def test_acquire_increments_and_decrements_refcount(mock_connect):
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings())

    with mgr.acquire("10.0.0.1"):
        assert mgr._refcounts.get("10.0.0.1", 0) == 1

    # After context exit, refcount should be cleared
    assert mgr._refcounts.get("10.0.0.1", 0) == 0


@patch("network_mcp.driver.pyeapi.connect")
def test_acquire_nested_increments(mock_connect):
    """Multiple concurrent acquires increment refcount."""
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings())

    with mgr.acquire("10.0.0.1"):
        assert mgr._refcounts.get("10.0.0.1", 0) == 1
        with mgr.acquire("10.0.0.1"):
            assert mgr._refcounts.get("10.0.0.1", 0) == 2
        assert mgr._refcounts.get("10.0.0.1", 0) == 1
    assert mgr._refcounts.get("10.0.0.1", 0) == 0


@patch("network_mcp.driver.pyeapi.connect")
def test_acquire_same_driver_returned(mock_connect):
    """Acquire returns the same cached driver for the same host."""
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings())

    with mgr.acquire("10.0.0.1") as d1:
        with mgr.acquire("10.0.0.1") as d2:
            assert d1 is d2


# --- Eviction with ref counting ---


@patch("network_mcp.driver.pyeapi.connect")
def test_eviction_skips_active_connections(mock_connect):
    """Connections with active refcount cannot be evicted."""
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings(), max_connections=2)

    with mgr.acquire("host1"):
        with mgr.acquire("host2"):
            # Pool is full (2/2), both in use
            with pytest.raises(ConnectionError, match="pool exhausted"):
                mgr.get_driver("host3")


@patch("network_mcp.driver.pyeapi.connect")
def test_eviction_evicts_idle_connection(mock_connect):
    """Idle connections (refcount 0) can be evicted when pool is full."""
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings(), max_connections=2)

    # Create two connections, one idle
    mgr.get_driver("host1")
    with mgr.acquire("host2"):
        # host1 is idle (refcount 0), host2 is active (refcount 1)
        # Getting host3 should evict host1
        driver3 = mgr.get_driver("host3")
        assert driver3 is not None
        assert "host1" not in mgr._drivers
        assert "host2" in mgr._drivers
        assert "host3" in mgr._drivers


@patch("network_mcp.driver.pyeapi.connect")
def test_pool_exhausted_error_message(mock_connect):
    """Pool full with all connections in use raises ConnectionError."""
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings(), max_connections=1)

    with mgr.acquire("host1"):
        with pytest.raises(ConnectionError, match="Connection pool exhausted"):
            mgr.get_driver("host2")


@patch("network_mcp.driver.pyeapi.connect")
def test_pool_exhausted_via_acquire(mock_connect):
    """Pool full via acquire also raises ConnectionError."""
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings(), max_connections=1)

    with mgr.acquire("host1"):
        with pytest.raises(ConnectionError, match="Connection pool exhausted"):
            with mgr.acquire("host2"):
                pass  # should not reach here


# --- pool_stats ---


@patch("network_mcp.driver.pyeapi.connect")
def test_pool_stats_empty(mock_connect):
    mgr = ConnectionManager(settings=make_test_settings())
    stats = mgr.pool_stats()
    assert stats["active"] == 0
    assert stats["idle"] == 0
    assert stats["pooled"] == 0
    assert stats["max"] == 100
    assert stats["per_device"] == {}
    assert stats["total_created"] == 0
    assert stats["total_checkouts"] == 0
    assert stats["avg_checkout_ms"] == 0.0


@patch("network_mcp.driver.pyeapi.connect")
def test_pool_stats_with_connections(mock_connect):
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings(), max_connections=10)

    mgr.get_driver("host1")
    with mgr.acquire("host2"):
        stats = mgr.pool_stats()
        assert stats["pooled"] == 2
        assert stats["active"] == 1  # host2 is acquired
        assert stats["max"] == 10
        assert stats["per_device"]["host1"]["refcount"] == 0
        assert stats["per_device"]["host2"]["refcount"] == 1

    # After release
    stats = mgr.pool_stats()
    assert stats["active"] == 0


@patch("network_mcp.driver.pyeapi.connect")
def test_pool_stats_after_close_all(mock_connect):
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings())
    mgr.get_driver("host1")
    mgr.close_all()
    stats = mgr.pool_stats()
    assert stats["pooled"] == 0
    assert stats["per_device"] == {}


# --- Refcount cleanup on remove ---


@patch("network_mcp.driver.pyeapi.connect")
def test_remove_node_clears_refcount(mock_connect):
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings())
    mgr.get_driver("host1")
    mgr._refcounts["host1"] = 1  # simulate active ref
    mgr.remove_node("host1")
    assert "host1" not in mgr._refcounts


# --- Thread safety ---


@patch("network_mcp.driver.pyeapi.connect")
def test_concurrent_acquire_release(mock_connect):
    """Multiple threads acquiring and releasing connections concurrently."""
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings(), max_connections=50)
    barrier = threading.Barrier(10)
    errors = []

    def worker(host_id):
        try:
            barrier.wait()
            host = f"host-{host_id}"
            for _ in range(20):
                with mgr.acquire(host) as driver:
                    assert driver is not None
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Errors during concurrent acquire: {errors}"
    # All refcounts should be 0 after all threads complete
    for host, rc in mgr._refcounts.items():
        assert rc == 0, f"Host {host} has refcount {rc} after all threads completed"


# --- Executor partial status ---


class TestExecutorPartialStatus:
    """Test the partial status from MultiDeviceResult.to_dict()."""

    def test_all_success(self):
        from network_mcp.executor import MultiDeviceResult

        result = MultiDeviceResult()
        result.add_success("host1", {"data": "ok"})
        result.add_success("host2", {"data": "ok"})
        d = result.to_dict()
        assert d["status"] == "success"
        assert d["summary"]["succeeded"] == 2
        assert d["summary"]["failed"] == 0

    def test_all_error(self):
        from network_mcp.executor import MultiDeviceResult

        result = MultiDeviceResult()
        result.add_error("host1", "timeout")
        result.add_error("host2", "refused")
        d = result.to_dict()
        assert d["status"] == "error"
        assert d["summary"]["succeeded"] == 0
        assert d["summary"]["failed"] == 2

    def test_partial_status(self):
        from network_mcp.executor import MultiDeviceResult

        result = MultiDeviceResult()
        result.add_success("host1", {"data": "ok"})
        result.add_error("host2", "timeout")
        result.add_success("host3", {"data": "ok"})
        d = result.to_dict()
        assert d["status"] == "partial"
        assert d["summary"]["succeeded"] == 2
        assert d["summary"]["failed"] == 1
        assert set(d["succeeded_hosts"]) == {"host1", "host3"}
        assert "host2" in d["failed_hosts"]
        assert d["errors"]["host2"] == "timeout"

    def test_empty_result(self):
        from network_mcp.executor import MultiDeviceResult

        result = MultiDeviceResult()
        d = result.to_dict()
        # No successes and no errors — n_error==0 so first branch: "success"
        assert d["status"] == "success"
        assert d["summary"]["total"] == 0
