"""Tests for the multi-device executor module."""

import pytest

from network_mcp.connection import ConnectionManager, DeviceCredentials
from network_mcp.executor import (
    MultiDeviceResult,
    execute_on_devices,
    resolve_hosts,
)
from tests.conftest import make_test_settings

# --- MultiDeviceResult tests ---


class TestMultiDeviceResult:
    def test_empty_result(self):
        r = MultiDeviceResult()
        assert r.total == 0
        assert r.success_count == 0
        assert r.error_count == 0

    def test_add_success(self):
        r = MultiDeviceResult()
        r.add_success("spine-01", {"hostname": "spine-01"})
        assert r.success_count == 1
        assert r.error_count == 0
        assert r.total == 1
        assert r.successes["spine-01"] == {"hostname": "spine-01"}

    def test_add_error(self):
        r = MultiDeviceResult()
        r.add_error("bad-host", "Connection refused")
        assert r.error_count == 1
        assert r.success_count == 0
        assert r.errors["bad-host"] == "Connection refused"

    def test_mixed_results(self):
        r = MultiDeviceResult()
        r.add_success("spine-01", {"ok": True})
        r.add_success("spine-02", {"ok": True})
        r.add_error("leaf-01", "Timeout")
        assert r.total == 3
        assert r.success_count == 2
        assert r.error_count == 1

    def test_to_dict(self):
        r = MultiDeviceResult()
        r.add_success("spine-01", {"data": 1})
        r.add_error("leaf-01", "Timeout")
        d = r.to_dict()
        assert d["summary"]["total"] == 2
        assert d["summary"]["succeeded"] == 1
        assert d["summary"]["failed"] == 1
        assert d["results"] == {"spine-01": {"data": 1}}
        assert d["errors"] == {"leaf-01": "Timeout"}

    def test_to_dict_empty(self):
        r = MultiDeviceResult()
        d = r.to_dict()
        assert d["summary"]["total"] == 0
        assert d["results"] == {}
        assert d["errors"] == {}

    def test_thread_safety(self):
        """Concurrent add_success/add_error calls should not lose data."""
        import threading

        r = MultiDeviceResult()
        barrier = threading.Barrier(20)

        def add_result(i: int):
            barrier.wait()
            if i % 2 == 0:
                r.add_success(f"device-{i}", {"index": i})
            else:
                r.add_error(f"device-{i}", f"Error {i}")

        threads = [threading.Thread(target=add_result, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert r.total == 20
        assert r.success_count == 10
        assert r.error_count == 10


# --- resolve_hosts tests ---


def _make_conn_mgr_with_inventory() -> ConnectionManager:
    """Create a ConnectionManager with test inventory (no real connections)."""
    mgr = ConnectionManager(settings=make_test_settings())
    mgr._inventory = {
        "spine-01": DeviceCredentials(
            host="10.0.0.1", role="spine", groups=["spines", "dc1"], tags=["production", "evpn"]
        ),
        "spine-02": DeviceCredentials(
            host="10.0.0.2", role="spine", groups=["spines", "dc1"], tags=["production", "evpn"]
        ),
        "leaf-01a": DeviceCredentials(
            host="10.0.1.1",
            role="leaf",
            groups=["leafs", "dc1", "pod1"],
            tags=["production", "mlag"],
            mlag_peer="leaf-01b",
        ),
        "leaf-01b": DeviceCredentials(
            host="10.0.1.2",
            role="leaf",
            groups=["leafs", "dc1", "pod1"],
            tags=["production", "mlag"],
            mlag_peer="leaf-01a",
        ),
        "border-01": DeviceCredentials(
            host="10.0.2.1", role="border-leaf", groups=["borders", "dc1"], tags=["production"]
        ),
    }
    return mgr


class TestResolveHosts:
    def test_all(self):
        mgr = _make_conn_mgr_with_inventory()
        hosts = resolve_hosts(mgr, "all")
        assert set(hosts) == {"spine-01", "spine-02", "leaf-01a", "leaf-01b", "border-01"}

    def test_group(self):
        mgr = _make_conn_mgr_with_inventory()
        hosts = resolve_hosts(mgr, "group:spines")
        assert set(hosts) == {"spine-01", "spine-02"}

    def test_group_no_match(self):
        mgr = _make_conn_mgr_with_inventory()
        hosts = resolve_hosts(mgr, "group:nonexistent")
        assert hosts == []

    def test_tag(self):
        mgr = _make_conn_mgr_with_inventory()
        hosts = resolve_hosts(mgr, "tag:mlag")
        assert set(hosts) == {"leaf-01a", "leaf-01b"}

    def test_tag_no_match(self):
        mgr = _make_conn_mgr_with_inventory()
        hosts = resolve_hosts(mgr, "tag:nonexistent")
        assert hosts == []

    def test_role(self):
        mgr = _make_conn_mgr_with_inventory()
        hosts = resolve_hosts(mgr, "role:spine")
        assert set(hosts) == {"spine-01", "spine-02"}

    def test_role_single(self):
        mgr = _make_conn_mgr_with_inventory()
        hosts = resolve_hosts(mgr, "role:border-leaf")
        assert hosts == ["border-01"]

    def test_comma_separated(self):
        mgr = _make_conn_mgr_with_inventory()
        hosts = resolve_hosts(mgr, "spine-01,leaf-01a")
        assert hosts == ["spine-01", "leaf-01a"]

    def test_comma_separated_with_spaces(self):
        mgr = _make_conn_mgr_with_inventory()
        hosts = resolve_hosts(mgr, " spine-01 , leaf-01a ")
        assert hosts == ["spine-01", "leaf-01a"]

    def test_single_host(self):
        mgr = _make_conn_mgr_with_inventory()
        hosts = resolve_hosts(mgr, "spine-01")
        assert hosts == ["spine-01"]

    def test_empty_inventory(self):
        mgr = ConnectionManager(settings=make_test_settings())
        hosts = resolve_hosts(mgr, "all")
        assert hosts == []


# --- execute_on_devices tests ---


def _success_op(conn_mgr: ConnectionManager, host: str) -> dict:
    """Simulate a successful operation."""
    return {"status": "success", "device": host, "data": {"hostname": host}}


def _error_op(conn_mgr: ConnectionManager, host: str) -> dict:
    """Simulate an error response."""
    return {"status": "error", "device": host, "error": "Connection refused"}


def _exception_op(conn_mgr: ConnectionManager, host: str) -> dict:
    """Simulate an operation that throws."""
    raise ConnectionError(f"Cannot reach {host}")


def _slow_op(conn_mgr: ConnectionManager, host: str) -> dict:
    """Simulate a slow operation that blocks."""
    import time

    time.sleep(5)
    return {"status": "success", "device": host, "data": {}}


def _mixed_op(conn_mgr: ConnectionManager, host: str) -> dict:
    """Return success for even-indexed hosts, error for odd."""
    # Extract trailing digit and check if odd
    last_char = host[-1]
    if last_char.isdigit() and int(last_char) % 2 == 1:
        return {"status": "error", "device": host, "error": "Odd host failed"}
    return {"status": "success", "device": host, "data": {"ok": True}}


@pytest.mark.asyncio
async def test_execute_all_success():
    mgr = ConnectionManager(settings=make_test_settings())
    hosts = ["spine-01", "spine-02"]
    result = await execute_on_devices(mgr, hosts, _success_op)

    assert result.success_count == 2
    assert result.error_count == 0
    assert "spine-01" in result.successes
    assert "spine-02" in result.successes


@pytest.mark.asyncio
async def test_execute_all_errors():
    mgr = ConnectionManager(settings=make_test_settings())
    hosts = ["bad-01", "bad-02"]
    result = await execute_on_devices(mgr, hosts, _error_op)

    assert result.success_count == 0
    assert result.error_count == 2


@pytest.mark.asyncio
async def test_execute_exception_handling():
    mgr = ConnectionManager(settings=make_test_settings())
    hosts = ["unreachable-01"]
    result = await execute_on_devices(mgr, hosts, _exception_op)

    assert result.error_count == 1
    assert "Cannot reach" in result.errors["unreachable-01"]


@pytest.mark.asyncio
async def test_execute_timeout():
    mgr = ConnectionManager(settings=make_test_settings())
    hosts = ["slow-device"]
    result = await execute_on_devices(mgr, hosts, _slow_op, timeout=0.1)

    assert result.error_count == 1
    assert "timed out" in result.errors["slow-device"]


@pytest.mark.asyncio
async def test_execute_partial_failure():
    mgr = ConnectionManager(settings=make_test_settings())
    hosts = ["device0", "device1", "device2", "device3"]
    result = await execute_on_devices(mgr, hosts, _mixed_op)

    assert result.success_count == 2  # device0, device2
    assert result.error_count == 2  # device1, device3


@pytest.mark.asyncio
async def test_execute_empty_hosts():
    mgr = ConnectionManager(settings=make_test_settings())
    result = await execute_on_devices(mgr, [], _success_op)

    assert result.total == 0
    assert result.successes == {}
    assert result.errors == {}


@pytest.mark.asyncio
async def test_execute_respects_semaphore():
    """Verify that max_concurrent limits concurrency."""
    import threading

    peak_concurrent = 0
    current_concurrent = 0
    lock = threading.Lock()

    def _counting_op(conn_mgr: ConnectionManager, host: str) -> dict:
        nonlocal peak_concurrent, current_concurrent
        with lock:
            current_concurrent += 1
            if current_concurrent > peak_concurrent:
                peak_concurrent = current_concurrent
        import time

        time.sleep(0.05)
        with lock:
            current_concurrent -= 1
        return {"status": "success", "device": host, "data": {}}

    mgr = ConnectionManager(settings=make_test_settings())
    hosts = [f"device-{i}" for i in range(10)]
    await execute_on_devices(mgr, hosts, _counting_op, max_concurrent=3)

    assert peak_concurrent <= 3


# --- New executor tests ---


@pytest.mark.asyncio
async def test_execute_single_device():
    """Degenerate case: single device should work correctly."""
    mgr = ConnectionManager(settings=make_test_settings())
    result = await execute_on_devices(mgr, ["only-one"], _success_op)

    assert result.total == 1
    assert result.success_count == 1
    assert "only-one" in result.successes


@pytest.mark.asyncio
async def test_execute_single_device_error():
    """Single device returning an error."""
    mgr = ConnectionManager(settings=make_test_settings())
    result = await execute_on_devices(mgr, ["bad-host"], _error_op)

    assert result.total == 1
    assert result.error_count == 1
    assert "bad-host" in result.errors


@pytest.mark.asyncio
async def test_execute_with_extra_args():
    """Operations can receive additional positional and keyword arguments."""

    def _op_with_args(conn_mgr: ConnectionManager, host: str, cmd: str, encoding: str = "json") -> dict:
        return {"status": "success", "device": host, "data": {"cmd": cmd, "encoding": encoding}}

    mgr = ConnectionManager(settings=make_test_settings())
    result = await execute_on_devices(mgr, ["spine-01"], _op_with_args, "show version", encoding="text")

    assert result.success_count == 1
    assert result.successes["spine-01"]["data"]["cmd"] == "show version"
    assert result.successes["spine-01"]["data"]["encoding"] == "text"


@pytest.mark.asyncio
async def test_execute_mixed_success_error_exception():
    """Mix of success, error dict, and exception across devices."""

    def _varied_op(conn_mgr: ConnectionManager, host: str) -> dict:
        if host == "good":
            return {"status": "success", "device": host, "data": {"ok": True}}
        elif host == "bad":
            return {"status": "error", "device": host, "error": "Device refused"}
        else:
            raise RuntimeError(f"Crash on {host}")

    mgr = ConnectionManager(settings=make_test_settings())
    result = await execute_on_devices(mgr, ["good", "bad", "crash"], _varied_op)

    assert result.success_count == 1
    assert result.error_count == 2
    assert "good" in result.successes
    assert "bad" in result.errors
    assert "crash" in result.errors
    assert "Crash on crash" in result.errors["crash"]


@pytest.mark.asyncio
async def test_execute_timeout_multiple_devices():
    """Multiple devices where some timeout and some succeed."""
    import time

    def _selective_slow_op(conn_mgr: ConnectionManager, host: str) -> dict:
        if "slow" in host:
            time.sleep(5)
        return {"status": "success", "device": host, "data": {}}

    mgr = ConnectionManager(settings=make_test_settings())
    result = await execute_on_devices(mgr, ["fast-01", "slow-01", "fast-02"], _selective_slow_op, timeout=0.1)

    assert result.success_count == 2  # fast-01, fast-02
    assert result.error_count == 1  # slow-01
    assert "timed out" in result.errors["slow-01"]


@pytest.mark.asyncio
async def test_execute_large_device_count():
    """Execute across many devices to verify scalability."""
    mgr = ConnectionManager(settings=make_test_settings())
    hosts = [f"device-{i:03d}" for i in range(50)]
    result = await execute_on_devices(mgr, hosts, _success_op, max_concurrent=10)

    assert result.total == 50
    assert result.success_count == 50
    assert result.error_count == 0


@pytest.mark.asyncio
async def test_execute_preserves_device_data():
    """Each device result should contain the correct per-device data."""

    def _data_op(conn_mgr: ConnectionManager, host: str) -> dict:
        return {"status": "success", "device": host, "data": {"hostname": host, "version": "4.32.1F"}}

    mgr = ConnectionManager(settings=make_test_settings())
    hosts = ["spine-01", "spine-02", "leaf-01"]
    result = await execute_on_devices(mgr, hosts, _data_op)

    for host in hosts:
        assert host in result.successes
        assert result.successes[host]["data"]["hostname"] == host


@pytest.mark.asyncio
async def test_execute_thread_safety_concurrent_writes():
    """Concurrent operations writing to MultiDeviceResult don't lose data."""
    import threading
    import time

    call_count = 0
    count_lock = threading.Lock()

    def _thread_tracked_op(conn_mgr: ConnectionManager, host: str) -> dict:
        nonlocal call_count
        with count_lock:
            call_count += 1
        time.sleep(0.01)  # Small delay to increase concurrency overlap
        return {"status": "success", "device": host, "data": {"host": host}}

    mgr = ConnectionManager(settings=make_test_settings())
    hosts = [f"device-{i}" for i in range(20)]
    result = await execute_on_devices(mgr, hosts, _thread_tracked_op, max_concurrent=20)

    assert call_count == 20
    assert result.total == 20
    assert result.success_count == 20


@pytest.mark.asyncio
async def test_execute_to_dict_with_mixed_results():
    """Verify to_dict() output structure with mixed results."""
    mgr = ConnectionManager(settings=make_test_settings())
    hosts = ["device0", "device1", "device2", "device3"]
    result = await execute_on_devices(mgr, hosts, _mixed_op)

    d = result.to_dict()
    assert d["summary"]["total"] == 4
    assert d["summary"]["succeeded"] == 2
    assert d["summary"]["failed"] == 2
    assert "device0" in d["results"]
    assert "device1" in d["errors"]


@pytest.mark.asyncio
async def test_execute_all_timeout():
    """All devices timing out returns errors for all."""
    mgr = ConnectionManager(settings=make_test_settings())
    hosts = ["slow-01", "slow-02", "slow-03"]
    result = await execute_on_devices(mgr, hosts, _slow_op, timeout=0.1)

    assert result.success_count == 0
    assert result.error_count == 3
    for host in hosts:
        assert "timed out" in result.errors[host]


@pytest.mark.asyncio
async def test_execute_all_exceptions():
    """All devices throwing exceptions returns errors for all."""
    mgr = ConnectionManager(settings=make_test_settings())
    hosts = ["crash-01", "crash-02"]
    result = await execute_on_devices(mgr, hosts, _exception_op)

    assert result.success_count == 0
    assert result.error_count == 2
    for host in hosts:
        assert "Cannot reach" in result.errors[host]


@pytest.mark.asyncio
async def test_execute_progress_reporting():
    """Progress reporting with ctx is best-effort and doesn't break execution."""
    from unittest.mock import AsyncMock

    mock_ctx = AsyncMock()
    mgr = ConnectionManager(settings=make_test_settings())
    hosts = ["spine-01", "spine-02"]
    result = await execute_on_devices(mgr, hosts, _success_op, ctx=mock_ctx)

    assert result.success_count == 2
    # Progress should have been reported (best-effort)
    assert mock_ctx.report_progress.call_count == 2


@pytest.mark.asyncio
async def test_execute_progress_reporting_failure_ignored():
    """If progress reporting fails, execution continues normally."""
    from unittest.mock import AsyncMock

    mock_ctx = AsyncMock()
    mock_ctx.report_progress.side_effect = RuntimeError("ctx broken")

    mgr = ConnectionManager(settings=make_test_settings())
    hosts = ["spine-01"]
    result = await execute_on_devices(mgr, hosts, _success_op, ctx=mock_ctx)

    assert result.success_count == 1  # Should still succeed


# --- resolve_hosts edge cases ---


class TestResolveHostsEdgeCases:
    def test_empty_string(self):
        mgr = _make_conn_mgr_with_inventory()
        hosts = resolve_hosts(mgr, "")
        assert hosts == []

    def test_whitespace_only(self):
        mgr = _make_conn_mgr_with_inventory()
        hosts = resolve_hosts(mgr, "   ")
        assert hosts == []
