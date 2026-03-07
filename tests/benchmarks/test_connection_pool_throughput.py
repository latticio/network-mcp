"""Connection pool throughput benchmarks.

Measures concurrent access patterns on the ConnectionManager:
- Single-thread acquire/release cycle
- Multi-thread concurrent acquire/release
- Pool under contention (many threads, limited slots)
- Pool statistics collection overhead
"""

import threading
from unittest.mock import MagicMock

import pytest

from network_mcp.connection import BulkheadManager, ConnectionManager


def _make_mock_settings(**overrides):
    """Create a minimal mock settings object for ConnectionManager."""
    from unittest.mock import PropertyMock

    settings = MagicMock()
    settings.net_username = overrides.get("username", "admin")
    settings.net_password = MagicMock()
    settings.net_password.get_secret_value.return_value = overrides.get("password", "admin")
    settings.net_transport = "https"
    settings.timeout_connect = 30
    settings.keepalive_interval = 30
    settings.net_verify_connections = False
    settings.net_verify_ssl = False
    settings.net_demo_mode = True
    settings.net_bulkhead_per_vendor = 0
    type(settings).max_connections = PropertyMock(return_value=overrides.get("max_connections", None))
    return settings


@pytest.fixture
def pool_conn_mgr():
    """ConnectionManager with mock driver for pool benchmarks."""
    settings = _make_mock_settings()
    mgr = ConnectionManager(settings, max_connections=50)

    mock_driver = MagicMock()
    mock_driver.platform = "eos"
    mock_driver.run_show.return_value = [{"modelName": "vEOS-lab"}]
    mock_driver.close.return_value = None

    def _fast_create(creds, device_name=None):
        d = MagicMock()
        d.platform = "eos"
        d.run_show.return_value = [{"modelName": "vEOS-lab"}]
        d.close.return_value = None
        return d

    mgr._create_driver = _fast_create  # type: ignore[assignment]
    return mgr


@pytest.mark.benchmark(group="pool-single-thread")
def test_pool_acquire_release_cycle(benchmark, pool_conn_mgr):
    """Single acquire/release cycle — measures lock + dict lookup overhead."""

    def _cycle():
        with pool_conn_mgr.acquire("device-01", verify=False) as driver:
            driver.run_show(["show version"])

    benchmark(_cycle)


@pytest.mark.benchmark(group="pool-single-thread")
def test_pool_acquire_cached_connection(benchmark, pool_conn_mgr):
    """Acquire an already-cached connection (no creation overhead)."""
    # Pre-create the connection
    with pool_conn_mgr.acquire("device-01", verify=False):
        pass

    def _acquire_cached():
        with pool_conn_mgr.acquire("device-01", verify=False) as driver:
            return driver

    benchmark(_acquire_cached)


@pytest.mark.benchmark(group="pool-single-thread")
def test_pool_get_driver_cached(benchmark, pool_conn_mgr):
    """get_driver() for a cached connection (legacy API)."""
    pool_conn_mgr.get_driver("device-01", verify=False)

    benchmark(pool_conn_mgr.get_driver, "device-01", False)


@pytest.mark.benchmark(group="pool-single-thread")
def test_pool_stats_collection(benchmark, pool_conn_mgr):
    """Overhead of pool_stats() — called by health monitoring."""
    # Create a few connections to make stats non-trivial
    for i in range(5):
        pool_conn_mgr.get_driver(f"device-{i:02d}", verify=False)

    benchmark(pool_conn_mgr.pool_stats)


@pytest.mark.benchmark(group="pool-concurrent")
def test_pool_concurrent_acquire_same_device(benchmark):
    """Multiple threads acquiring the same device concurrently."""
    settings = _make_mock_settings()
    mgr = ConnectionManager(settings, max_connections=50)
    mgr._create_driver = lambda creds, device_name=None: MagicMock(  # type: ignore[assignment]
        platform="eos", run_show=MagicMock(return_value=[{}])
    )

    num_threads = 4
    barrier = threading.Barrier(num_threads)
    errors = []

    def _worker():
        try:
            barrier.wait(timeout=5)
            with mgr.acquire("shared-device", verify=False) as driver:
                driver.run_show(["show version"])
        except Exception as e:
            errors.append(e)

    def _run_concurrent():
        nonlocal barrier
        barrier = threading.Barrier(num_threads)
        threads = [threading.Thread(target=_worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

    benchmark(_run_concurrent)
    assert not errors, f"Concurrent access errors: {errors}"


@pytest.mark.benchmark(group="pool-concurrent")
def test_pool_concurrent_acquire_different_devices(benchmark):
    """Multiple threads acquiring different devices concurrently."""
    settings = _make_mock_settings()
    mgr = ConnectionManager(settings, max_connections=50)
    mgr._create_driver = lambda creds, device_name=None: MagicMock(  # type: ignore[assignment]
        platform="eos", run_show=MagicMock(return_value=[{}])
    )

    num_threads = 4
    barrier = threading.Barrier(num_threads)
    errors = []

    def _worker(device_id):
        try:
            barrier.wait(timeout=5)
            with mgr.acquire(f"device-{device_id}", verify=False) as driver:
                driver.run_show(["show version"])
        except Exception as e:
            errors.append(e)

    def _run_concurrent():
        nonlocal barrier
        barrier = threading.Barrier(num_threads)
        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

    benchmark(_run_concurrent)
    assert not errors, f"Concurrent access errors: {errors}"


@pytest.mark.benchmark(group="bulkhead")
def test_bulkhead_acquire_release(benchmark):
    """Bulkhead acquire/release cycle overhead."""
    bh = BulkheadManager(per_vendor_limit=20)

    def _cycle():
        bh.acquire("eos", timeout=1.0)
        bh.release("eos")

    benchmark(_cycle)


@pytest.mark.benchmark(group="bulkhead")
def test_bulkhead_concurrent_slots(benchmark):
    """Multiple threads competing for bulkhead slots."""
    bh = BulkheadManager(per_vendor_limit=20)
    num_threads = 4
    barrier = threading.Barrier(num_threads)
    errors = []

    def _worker():
        try:
            barrier.wait(timeout=5)
            if bh.acquire("eos", timeout=5.0):
                bh.release("eos")
        except Exception as e:
            errors.append(e)

    def _run_concurrent():
        nonlocal barrier
        barrier = threading.Barrier(num_threads)
        threads = [threading.Thread(target=_worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

    benchmark(_run_concurrent)
    assert not errors, f"Bulkhead errors: {errors}"
