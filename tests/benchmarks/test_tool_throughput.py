"""Tool throughput and abstraction overhead benchmarks.

Measures single-device tool execution speed through the full MCP stack
(validation, rate limiting, caching, audit, sanitization) vs. direct mock
pyeapi calls. The delta is the MCP server abstraction overhead.

Abstraction overhead target: <5ms per call.
If overhead exceeds 5ms, investigate the middleware pipeline.
If under 5ms, acceptable for the value provided (input validation, rate
limiting, circuit breaker, caching, audit logging, credential sanitization).

This baseline will be compared against Phase 4a multi-vendor abstraction.
"""

from network_mcp.helpers import command_cache
from network_mcp.server import conn_mgr

# --- Abstraction Overhead Baseline ---


def test_benchmark_direct_pyeapi_call(benchmark, bench_mock_node):
    """Baseline: direct pyeapi call without MCP server overhead.

    Measures the cost of calling a mock node.run_commands() directly.
    This represents the absolute minimum cost for a device interaction.
    """
    result = benchmark(bench_mock_node.run_commands, ["show version"])
    assert result is not None


def test_benchmark_mcp_tool_call(benchmark, bench_mock_conn_mgr):
    """MCP tool call: measures full stack overhead.

    Passes through: host validation, cache check, circuit breaker, rate limiter,
    connection acquire, retry wrapper, sanitization, audit logging, metrics.

    The difference between this and test_benchmark_direct_pyeapi_call is the
    MCP server abstraction overhead.
    """
    from network_mcp.tools.device import eos_get_device_info

    # Disable cache to measure full pipeline each iteration
    command_cache._enabled = False
    try:
        result = benchmark(eos_get_device_info, "spine-01")
    finally:
        command_cache._enabled = True
    assert result["status"] == "success"
    assert result["data"]["model"] == "vEOS-lab"


# --- Single-Device Tool Throughput ---


def test_benchmark_get_device_info(benchmark, bench_mock_conn_mgr):
    """Benchmark eos_get_device_info throughput (simplest tool, baseline).

    This is the simplest read-only tool: one show command, minimal post-processing.
    """
    from network_mcp.tools.device import eos_get_device_info

    command_cache._enabled = False
    try:
        result = benchmark(eos_get_device_info, "spine-01")
    finally:
        command_cache._enabled = True
    assert result["status"] == "success"
    assert result["data"]["eos_version"] == "4.32.1F"


def test_benchmark_get_bgp_summary(benchmark, bench_bgp_mock_conn_mgr):
    """Benchmark eos_get_bgp_summary throughput (moderate complexity).

    Includes VRF lookup, field filtering/pagination support, address family validation.
    """
    from network_mcp.tools.routing import eos_get_bgp_summary

    command_cache._enabled = False
    try:
        result = benchmark(eos_get_bgp_summary, "spine-01")
    finally:
        command_cache._enabled = True
    assert result["status"] == "success"
    assert "peers" in result["data"]


def test_benchmark_get_interfaces_status(benchmark, bench_interfaces_mock_conn_mgr):
    """Benchmark eos_get_interfaces_status throughput (moderate complexity).

    Includes field filtering and pagination support.
    """
    from network_mcp.tools.interfaces import eos_get_interfaces_status

    command_cache._enabled = False
    try:
        result = benchmark(eos_get_interfaces_status, "spine-01")
    finally:
        command_cache._enabled = True
    assert result["status"] == "success"
    assert "Ethernet1" in result["data"]


# --- Multi-Device Tool Throughput ---


def test_benchmark_fabric_health_operation(benchmark, bench_multi_device_conn_mgr):
    """Benchmark the per-device fabric health operation (sync, single device).

    Measures the cost of _health_operation (4 show commands + data extraction)
    through the full MCP stack. The async orchestration overhead of
    eos_fabric_health_summary (asyncio.gather, semaphore, thread dispatch) is
    not measured here — this isolates the per-device compute cost.
    """
    from network_mcp.tools.fabric import _health_operation

    command_cache._enabled = False
    try:
        result = benchmark(_health_operation, conn_mgr, "spine-01")
    finally:
        command_cache._enabled = True
    assert result["status"] == "success"
    assert result["data"]["model"] == "vEOS-lab"
