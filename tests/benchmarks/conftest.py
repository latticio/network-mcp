"""Benchmark-specific fixtures.

These fixtures provide isolated mocking for benchmark scenarios, ensuring that
benchmarks measure MCP server overhead only (not network latency).

The autouse fixtures from tests/conftest.py (_reset_rate_limiter, _reset_command_cache,
_reset_circuit_breaker, _reset_metrics_collector) apply here too, keeping each
benchmark iteration clean.

Rate limiter: patched to always allow (infinite rate) so benchmarks can run
hundreds of iterations without throttling.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.helpers import device_rate_limiter
from network_mcp.server import conn_mgr
from tests.conftest import (
    MOCK_BGP_SUMMARY,
    MOCK_INTERFACES_STATUS,
    MOCK_MLAG_HEALTHY,
    MOCK_SHOW_VERSION,
)


@pytest.fixture(autouse=True)
def _unlimited_rate_limiter():
    """Patch rate limiter to always allow during benchmarks.

    Benchmarks run hundreds/thousands of iterations; the default 5 req/s rate
    would cause most iterations to return rate-limit errors instead of measuring
    actual tool overhead.
    """
    with (
        patch.object(device_rate_limiter, "check_show", return_value=True),
        patch.object(device_rate_limiter, "check_config", return_value=True),
    ):
        yield


@pytest.fixture
def bench_mock_node():
    """Mock pyeapi node for benchmarks -- returns realistic response data."""
    node = MagicMock()
    node.run_commands.return_value = [MOCK_SHOW_VERSION]
    node.run_show = node.run_commands
    node.run_config = node.config
    node.platform = "eos"
    node.is_connected.return_value = True
    return node


@pytest.fixture
def bench_mock_conn_mgr(bench_mock_node):
    """Mock connection manager for benchmarks."""

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield bench_mock_node

    with (
        patch.object(conn_mgr, "get_driver", return_value=bench_mock_node),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield bench_mock_node


@pytest.fixture
def bench_bgp_mock_node():
    """Mock node returning BGP summary data."""
    node = MagicMock()
    node.run_commands.return_value = [MOCK_BGP_SUMMARY]
    node.run_show = node.run_commands
    node.run_config = node.config
    node.platform = "eos"
    node.is_connected.return_value = True
    return node


@pytest.fixture
def bench_bgp_mock_conn_mgr(bench_bgp_mock_node):
    """Mock connection manager returning BGP summary data."""

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield bench_bgp_mock_node

    with (
        patch.object(conn_mgr, "get_driver", return_value=bench_bgp_mock_node),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield bench_bgp_mock_node


@pytest.fixture
def bench_interfaces_mock_node():
    """Mock node returning interfaces status data."""
    node = MagicMock()
    node.run_commands.return_value = [MOCK_INTERFACES_STATUS]
    node.run_show = node.run_commands
    node.run_config = node.config
    node.platform = "eos"
    node.is_connected.return_value = True
    return node


@pytest.fixture
def bench_interfaces_mock_conn_mgr(bench_interfaces_mock_node):
    """Mock connection manager returning interfaces status data."""

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield bench_interfaces_mock_node

    with (
        patch.object(conn_mgr, "get_driver", return_value=bench_interfaces_mock_node),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield bench_interfaces_mock_node


@pytest.fixture
def bench_multi_device_mock_node():
    """Mock node returning health data for multi-device (fabric) benchmarks."""
    node = MagicMock()
    # Fabric health calls: show version, show env temperature, show env cooling, show env power
    node.run_commands.return_value = [
        MOCK_SHOW_VERSION,
        {"tempSensors": [], "systemStatus": "temperatureOk"},
        {"fanTraySlots": []},
        {"powerSupplies": {}},
    ]
    node.run_show = node.run_commands
    node.run_config = node.config
    node.platform = "eos"
    node.is_connected.return_value = True
    return node


@pytest.fixture
def bench_multi_device_conn_mgr(bench_multi_device_mock_node):
    """Mock connection manager for multi-device fabric benchmarks.

    Patches conn_mgr to return the same mock node for any host, and populates
    the inventory with 3 test devices so resolve_hosts("all") works.
    """

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield bench_multi_device_mock_node

    test_hosts = ["spine-01", "leaf-01", "leaf-02"]

    with (
        patch.object(conn_mgr, "get_driver", return_value=bench_multi_device_mock_node),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        patch.object(conn_mgr, "list_devices", return_value=test_hosts),
    ):
        yield bench_multi_device_mock_node


@pytest.fixture
def bench_mlag_mock_node():
    """Mock node returning MLAG data for fabric benchmarks."""
    node = MagicMock()
    node.run_commands.return_value = [MOCK_MLAG_HEALTHY]
    node.run_show = node.run_commands
    node.run_config = node.config
    node.platform = "eos"
    node.is_connected.return_value = True
    return node
