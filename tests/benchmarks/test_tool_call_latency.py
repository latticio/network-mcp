"""Tool call latency benchmarks.

Measures per-component overhead in the tool call pipeline to identify
where latency is added between a raw driver call and a full MCP tool call.

Components measured:
- Raw driver call (baseline)
- run_show_command() wrapper (validation, retry, audit, sanitization)
- Full tool function call (includes response shaping)
- Multiple tool types (simple vs complex)
"""

import pytest

from network_mcp.helpers import command_cache


@pytest.mark.benchmark(group="latency-baseline")
def test_latency_raw_driver_call(benchmark, bench_mock_node):
    """Baseline: raw mock driver call with no MCP overhead."""
    result = benchmark(bench_mock_node.run_commands, ["show version"], encoding="json")
    assert result is not None


@pytest.mark.benchmark(group="latency-baseline")
def test_latency_run_show_command(benchmark, bench_mock_conn_mgr):
    """Measure run_show_command() wrapper overhead.

    Includes: host validation, rate limiting, circuit breaker, retry,
    audit logging, credential sanitization, output size limits.
    """
    from network_mcp.helpers import run_show_command
    from network_mcp.server import conn_mgr

    command_cache._enabled = False
    try:
        result = benchmark(run_show_command, conn_mgr, "spine-01", ["show version"])
    finally:
        command_cache._enabled = True
    assert result["status"] == "success"


@pytest.mark.benchmark(group="latency-baseline")
def test_latency_full_tool_call(benchmark, bench_mock_conn_mgr):
    """Full MCP tool call: run_show_command + response shaping.

    Measures the complete overhead from tool entry to return dict.
    """
    from network_mcp.tools.device import eos_get_device_info

    command_cache._enabled = False
    try:
        result = benchmark(eos_get_device_info, "spine-01")
    finally:
        command_cache._enabled = True
    assert result["status"] == "success"
    assert result["device"] == "spine-01"


@pytest.mark.benchmark(group="latency-by-tool")
def test_latency_simple_tool(benchmark, bench_mock_conn_mgr):
    """Simple tool: single command, minimal post-processing (get_device_info)."""
    from network_mcp.tools.device import eos_get_device_info

    command_cache._enabled = False
    try:
        result = benchmark(eos_get_device_info, "spine-01")
    finally:
        command_cache._enabled = True
    assert result["status"] == "success"


@pytest.mark.benchmark(group="latency-by-tool")
def test_latency_moderate_tool(benchmark, bench_bgp_mock_conn_mgr):
    """Moderate tool: VRF lookup, field filtering (get_bgp_summary)."""
    from network_mcp.tools.routing import eos_get_bgp_summary

    command_cache._enabled = False
    try:
        result = benchmark(eos_get_bgp_summary, "spine-01")
    finally:
        command_cache._enabled = True
    assert result["status"] == "success"


@pytest.mark.benchmark(group="latency-by-tool")
def test_latency_interface_tool(benchmark, bench_interfaces_mock_conn_mgr):
    """Interface tool: list processing, status mapping (get_interfaces_status)."""
    from network_mcp.tools.interfaces import eos_get_interfaces_status

    command_cache._enabled = False
    try:
        result = benchmark(eos_get_interfaces_status, "spine-01")
    finally:
        command_cache._enabled = True
    assert result["status"] == "success"


@pytest.mark.benchmark(group="latency-cached")
def test_latency_cached_vs_uncached(benchmark, bench_mock_conn_mgr):
    """Measure cache hit path latency (should be significantly faster)."""
    from network_mcp.tools.device import eos_get_device_info

    # Warm the cache
    command_cache.clear()
    eos_get_device_info("spine-01")

    # Benchmark cache-hit path
    result = benchmark(eos_get_device_info, "spine-01")
    assert result["status"] == "success"
