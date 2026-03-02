"""Thread safety stress tests for the ConnectionManager.

Session 01 of V4 Phase 0: Verifies concurrent-access safety, pool exhaustion
behavior, lock patterns, and rate limiter thread safety.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.connection import ConnectionManager
from network_mcp.rate_limiter import DeviceRateLimiter
from tests.conftest import make_test_settings

# --- Connection Pool Concurrent Access ---


@patch("network_mcp.driver.pyeapi.connect")
def test_concurrent_acquire_different_hosts(mock_connect):
    """10 threads simultaneously acquiring connections for different hosts.

    Verifies no deadlock and all drivers are valid.
    """
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings())
    results = {}
    errors = []

    def acquire_host(host):
        try:
            with mgr.acquire(host) as driver:
                results[host] = driver
                assert driver is not None
                assert driver.is_connected()
        except Exception as e:
            errors.append((host, e))

    threads = [threading.Thread(target=acquire_host, args=(f"host-{i}",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Thread errors: {errors}"
    assert len(results) == 10
    # Verify no threads are still alive (no deadlock)
    for t in threads:
        assert not t.is_alive(), "Thread deadlocked"


@patch("network_mcp.driver.pyeapi.connect")
def test_concurrent_acquire_same_host(mock_connect):
    """10 threads repeatedly acquiring and releasing the SAME host.

    Tests contention on a single pool entry with refcount tracking.
    """
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings())
    errors = []
    iterations_per_thread = 20

    def acquire_repeatedly(host):
        try:
            for _ in range(iterations_per_thread):
                with mgr.acquire(host) as driver:
                    assert driver is not None
                    assert driver.is_connected()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=acquire_repeatedly, args=("shared-host",)) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Thread errors: {errors}"
    for t in threads:
        assert not t.is_alive(), "Thread deadlocked"

    # After all threads complete, refcount should be 0
    with mgr._nodes_lock:
        rc = mgr._refcounts.get("shared-host", 0)
    assert rc == 0, f"Refcount leak: {rc}"


@patch("network_mcp.driver.pyeapi.connect")
def test_concurrent_acquire_and_get_driver_mixed(mock_connect):
    """Threads using both acquire() and get_driver() concurrently."""
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings())
    errors = []

    def use_acquire(host):
        try:
            with mgr.acquire(host) as driver:
                assert driver is not None
        except Exception as e:
            errors.append(e)

    def use_get_driver(host):
        try:
            driver = mgr.get_driver(host)
            assert driver is not None
        except Exception as e:
            errors.append(e)

    threads = []
    for i in range(5):
        threads.append(threading.Thread(target=use_acquire, args=(f"host-{i}",)))
        threads.append(threading.Thread(target=use_get_driver, args=(f"host-{i}",)))

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Thread errors: {errors}"


# --- Pool Exhaustion ---


@patch("network_mcp.driver.pyeapi.connect")
def test_pool_exhaustion_evicts_idle(mock_connect):
    """Pool at max_connections evicts oldest idle connection (refcount=0)."""
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings(), max_connections=3)

    # Fill the pool — use get_driver so connections have refcount=0
    mgr.get_driver("host-1")
    mgr.get_driver("host-2")
    mgr.get_driver("host-3")
    assert len(mgr._drivers) == 3

    # Adding a 4th should evict host-1 (FIFO, oldest idle)
    mgr.get_driver("host-4")
    assert len(mgr._drivers) == 3
    assert "host-1" not in mgr._drivers
    assert "host-4" in mgr._drivers


@patch("network_mcp.driver.pyeapi.connect")
def test_pool_exhaustion_no_evict_active(mock_connect):
    """All connections actively held — pool raises ConnectionError."""
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings(), max_connections=3)

    # Hold all 3 via acquire() context managers (refcount > 0)
    with mgr.acquire("host-1"), mgr.acquire("host-2"), mgr.acquire("host-3"):
        # All 3 are in-use, a 4th should fail
        with pytest.raises(ConnectionError, match="Connection pool exhausted"):
            with mgr.acquire("host-4"):
                pass


@patch("network_mcp.driver.pyeapi.connect")
def test_pool_exhaustion_evicts_idle_not_active(mock_connect):
    """With a mix of active and idle connections, only idle ones get evicted."""
    mock_connect.return_value = MagicMock()
    mgr = ConnectionManager(settings=make_test_settings(), max_connections=3)

    # host-1 is idle (get_driver, refcount=0)
    mgr.get_driver("host-1")

    # host-2 and host-3 are held via acquire
    with mgr.acquire("host-2"), mgr.acquire("host-3"):
        # Adding host-4 should evict host-1 (the idle one)
        with mgr.acquire("host-4") as driver:
            assert driver is not None
            assert "host-1" not in mgr._drivers
            assert "host-4" in mgr._drivers


# --- _verify_driver Lock Behavior ---


@patch("network_mcp.driver.pyeapi.connect")
def test_verify_driver_runs_outside_lock(mock_connect):
    """Verify that _verify_driver() does NOT hold _nodes_lock during I/O.

    The connection.py docstring states: "Called WITHOUT _nodes_lock held."
    We verify this by checking that another thread can acquire the lock
    while verification is running.
    """
    verify_started = threading.Event()
    can_continue = threading.Event()
    lock_acquired_during_verify = threading.Event()

    def slow_verify(cmds, **kwargs):
        verify_started.set()
        can_continue.wait(timeout=5)
        return [{"version": "4.32.1F"}]

    mock_node = MagicMock()
    mock_node.run_commands = slow_verify
    mock_node.is_connected.return_value = True
    mock_connect.return_value = mock_node

    mgr = ConnectionManager(settings=make_test_settings(net_verify_connections=True))

    errors = []

    def try_lock():
        """Try to acquire _nodes_lock while verify is running."""
        verify_started.wait(timeout=5)
        # If _verify_driver holds the lock, this will block
        acquired = mgr._nodes_lock.acquire(timeout=2)
        if acquired:
            lock_acquired_during_verify.set()
            mgr._nodes_lock.release()
        else:
            errors.append("Could not acquire lock during verification — deadlock risk")

    lock_thread = threading.Thread(target=try_lock)
    lock_thread.start()

    # This triggers _verify_driver during acquire
    verify_thread = threading.Thread(target=lambda: mgr.get_driver("10.0.0.1", verify=True))
    verify_thread.start()

    # Wait for verification to start, then let lock_thread try
    verify_started.wait(timeout=5)
    # Give lock_thread time to acquire
    lock_acquired_during_verify.wait(timeout=3)
    # Let verification complete
    can_continue.set()

    verify_thread.join(timeout=5)
    lock_thread.join(timeout=5)

    assert not errors, f"Lock errors: {errors}"
    assert lock_acquired_during_verify.is_set(), "_nodes_lock was blocked during verify — potential deadlock"


# --- Rate Limiter Thread Safety ---


def test_rate_limiter_concurrent_check_show():
    """10 threads calling check_show() for the same host simultaneously.

    The DeviceRateLimiter uses double-check locking for bucket creation
    and TokenBucket uses its own lock for acquire(). This test verifies
    no errors or data corruption under contention.
    """
    limiter = DeviceRateLimiter(show_rate=1000.0, show_burst=10000.0)
    errors = []
    results = []
    iterations_per_thread = 100

    def hammer_check_show(host):
        try:
            for _ in range(iterations_per_thread):
                result = limiter.check_show(host)
                results.append(result)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=hammer_check_show, args=("device-1",)) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Thread errors: {errors}"
    assert len(results) == 10 * iterations_per_thread
    # All should be True since we set very high rate/burst
    assert all(r is True for r in results)
    for t in threads:
        assert not t.is_alive(), "Thread deadlocked"


def test_rate_limiter_concurrent_bucket_creation():
    """Multiple threads triggering bucket creation for different hosts simultaneously.

    Exercises the double-check locking in _get_show_bucket and _get_config_bucket.
    """
    limiter = DeviceRateLimiter(show_rate=1000.0, show_burst=10000.0)
    errors = []

    def create_buckets(start_idx):
        try:
            for i in range(20):
                limiter.check_show(f"device-{start_idx}-{i}")
                limiter.check_config(f"device-{start_idx}-{i}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=create_buckets, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Thread errors: {errors}"
    # Each thread creates 20 show + 20 config buckets = 200 each
    assert len(limiter._show_buckets) == 200
    assert len(limiter._config_buckets) == 200


def test_rate_limiter_double_check_locking_verified():
    """Verify the double-check locking pattern in _get_show_bucket.

    The DeviceRateLimiter._get_show_bucket() and _get_config_bucket() use
    a double-check locking pattern:
    1. First check without lock (fast path for existing buckets)
    2. Acquire lock and check again (prevents duplicate creation)

    This is correct and thread-safe. This test documents the finding.
    """
    limiter = DeviceRateLimiter()

    # First call creates the bucket
    limiter.check_show("host-a")
    bucket_1 = limiter._show_buckets["host-a"]

    # Second call should return the same bucket (no duplicate creation)
    limiter.check_show("host-a")
    bucket_2 = limiter._show_buckets["host-a"]

    assert bucket_1 is bucket_2, "Double-check locking failed — duplicate bucket created"
