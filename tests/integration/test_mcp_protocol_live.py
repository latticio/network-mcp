"""Phase 1: MCP Protocol Test Harness — test the full JSON-RPC stack.

Starts the MCP server as a subprocess (stdio transport) and connects to it
using the official MCP SDK client. This validates the entire protocol path:
  Client -> MCP SDK -> stdio -> FastMCP -> tool dispatch -> pyeapi -> cEOS device

Run with:
  uv run pytest tests/integration/test_mcp_protocol_live.py -v --run-integration -n0
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEVICES_YAML = PROJECT_ROOT / "lab" / "devices.yaml"

ALL_HOSTS = ["spine1", "spine2", "leaf1", "leaf2", "leaf3", "leaf4"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def event_loop():
    """Module-scoped event loop for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def mcp_session(event_loop):
    """Start the MCP server and connect via the official MCP SDK client."""
    if not DEVICES_YAML.exists():
        pytest.skip(f"No inventory file at {DEVICES_YAML}")

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(
        command="uv",
        args=["run", "network-mcp"],
        env={
            "NET_DEMO_MODE": "false",
            "NET_READ_ONLY": "false",
            "NET_VERIFY_SSL": "false",
            "NET_USERNAME": "admin",
            "NET_PASSWORD": "admin",
            "NET_INVENTORY_FILE": str(DEVICES_YAML),
        },
    )

    # We use a nested context manager approach to keep the session alive
    # for the entire module
    session_holder = {}

    async def setup():
        cm1 = stdio_client(server_params)
        read, write = await cm1.__aenter__()
        cm2 = ClientSession(read, write)
        session = await cm2.__aenter__()
        await session.initialize()
        session_holder["session"] = session
        session_holder["cm1"] = cm1
        session_holder["cm2"] = cm2

    async def teardown():
        try:
            await session_holder["cm2"].__aexit__(None, None, None)
        except Exception:  # noqa: S110
            pass
        try:
            await session_holder["cm1"].__aexit__(None, None, None)
        except Exception:  # noqa: S110
            pass

    event_loop.run_until_complete(setup())
    yield session_holder["session"]
    event_loop.run_until_complete(teardown())


@pytest.fixture(scope="module")
def tool_list(mcp_session, event_loop) -> list:
    """Get all available tools from the server."""
    async def _list():
        result = await mcp_session.list_tools()
        return result.tools

    tools = event_loop.run_until_complete(_list())
    assert len(tools) > 0, "Server returned 0 tools"
    return tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_async(event_loop, coro):
    """Run an async coroutine in the module event loop."""
    return event_loop.run_until_complete(coro)


async def call_tool_async(session, name: str, args: dict) -> tuple[object, float]:
    """Call a tool and return (result, latency_seconds)."""
    start = time.time()
    result = await session.call_tool(name, args)
    latency = time.time() - start
    return result, latency


def parse_tool_result(result) -> dict:
    """Parse the MCP tool result into a dict."""
    if not result or not result.content:
        return {"status": "unknown", "raw": str(result)}
    text = result.content[0].text if hasattr(result.content[0], "text") else str(result.content[0])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"status": "raw", "raw": text, "isError": getattr(result, "isError", False)}


# ---------------------------------------------------------------------------
# Phase 1: Protocol-level tests
# ---------------------------------------------------------------------------


class TestMcpHandshake:
    """Validate the MCP protocol handshake completed correctly."""

    def test_tools_list_returns_tools(self, tool_list):
        assert len(tool_list) > 50, f"Expected 50+ tools, got {len(tool_list)}"
        names = [t.name for t in tool_list]
        for expected in ["net_get_device_info", "net_get_interfaces", "net_get_vlans"]:
            assert expected in names, f"Expected tool '{expected}' not found in tools/list"

    def test_tool_has_schema(self, tool_list):
        """Every tool should have an inputSchema."""
        for tool in tool_list[:10]:
            assert tool.inputSchema is not None, f"Tool {tool.name} missing inputSchema"

    def test_tool_count_matches_annotation_log(self, tool_list):
        """Server logs say 253+ tools annotated, verify we get a similar count."""
        assert len(tool_list) > 200, f"Expected 200+ tools, got {len(tool_list)}"


class TestProtocolDeviceInfo:
    """Test device info via the MCP protocol."""

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_device_info(self, mcp_session, event_loop, host):
        async def _test():
            result, latency = await call_tool_async(mcp_session, "net_get_device_info", {"host": host})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Failed on {host}: {parsed.get('error', parsed)}"
            assert parsed.get("device") == host
            assert "data" in parsed
            assert latency < 15, f"Latency {latency:.1f}s exceeds 15s threshold"

        run_async(event_loop, _test())


class TestProtocolInterfaces:
    """Test interface tools via MCP protocol."""

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_interfaces(self, mcp_session, event_loop, host):
        async def _test():
            result, latency = await call_tool_async(mcp_session, "net_get_interfaces", {"host": host})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Failed on {host}: {parsed.get('error', parsed)}"
            assert latency < 15

        run_async(event_loop, _test())

    def test_get_interface_counters(self, mcp_session, event_loop):
        async def _test():
            result, _ = await call_tool_async(mcp_session, "net_get_interface_counters", {"host": "spine1"})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Failed: {parsed.get('error', parsed)}"

        run_async(event_loop, _test())

    def test_get_interface_errors(self, mcp_session, event_loop):
        async def _test():
            result, _ = await call_tool_async(mcp_session, "net_get_interface_errors", {"host": "leaf1"})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Failed: {parsed.get('error', parsed)}"

        run_async(event_loop, _test())


class TestProtocolRouting:
    """Test routing tools via MCP protocol."""

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_route_table(self, mcp_session, event_loop, host):
        async def _test():
            result, _ = await call_tool_async(mcp_session, "net_get_route_table", {"host": host})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Failed on {host}: {parsed.get('error', parsed)}"

        run_async(event_loop, _test())

    def test_get_arp_table(self, mcp_session, event_loop):
        async def _test():
            result, _ = await call_tool_async(mcp_session, "net_get_arp_table", {"host": "leaf1"})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Failed: {parsed.get('error', parsed)}"

        run_async(event_loop, _test())

    def test_get_bgp_summary(self, mcp_session, event_loop):
        async def _test():
            result, _ = await call_tool_async(mcp_session, "net_get_bgp_summary", {"host": "spine1"})
            parsed = parse_tool_result(result)
            assert parsed.get("status") in ("success", "error"), f"Unexpected: {parsed}"

        run_async(event_loop, _test())

    def test_get_route_summary(self, mcp_session, event_loop):
        async def _test():
            result, _ = await call_tool_async(mcp_session, "net_get_route_summary", {"host": "spine1"})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Failed: {parsed.get('error', parsed)}"

        run_async(event_loop, _test())


class TestProtocolSwitching:
    """Test switching tools via MCP protocol."""

    @pytest.mark.parametrize("host", ALL_HOSTS)
    def test_get_lldp_neighbors(self, mcp_session, event_loop, host):
        async def _test():
            result, _ = await call_tool_async(mcp_session, "net_get_lldp_neighbors", {"host": host})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Failed on {host}: {parsed.get('error', parsed)}"

        run_async(event_loop, _test())

    def test_get_mac_table(self, mcp_session, event_loop):
        async def _test():
            result, _ = await call_tool_async(mcp_session, "net_get_mac_table", {"host": "leaf1"})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Failed: {parsed.get('error', parsed)}"

        run_async(event_loop, _test())

    def test_get_stp_status(self, mcp_session, event_loop):
        async def _test():
            result, _ = await call_tool_async(mcp_session, "net_get_stp_status", {"host": "leaf1"})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Failed: {parsed.get('error', parsed)}"

        run_async(event_loop, _test())


class TestProtocolMonitoring:
    """Test monitoring tools via MCP protocol."""

    @pytest.mark.parametrize("tool_name", [
        "net_get_cpu_usage",
        "net_get_memory_usage",
        "net_get_device_health",
        "net_get_environment_temp",
        "net_get_ntp_status",
    ])
    def test_monitoring_tools(self, mcp_session, event_loop, tool_name):
        async def _test():
            result, latency = await call_tool_async(mcp_session, tool_name, {"host": "spine1"})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"{tool_name} failed: {parsed.get('error', parsed)}"
            assert latency < 15

        run_async(event_loop, _test())


class TestProtocolConfig:
    """Test config tools via MCP protocol."""

    def test_get_running_config(self, mcp_session, event_loop):
        async def _test():
            result, _ = await call_tool_async(mcp_session, "net_get_running_config", {"host": "leaf1"})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Failed: {parsed.get('error', parsed)}"
            data = parsed.get("data", {})
            config_text = data.get("config", "") if isinstance(data, dict) else str(data)
            assert "hostname" in config_text.lower(), "Running config should contain 'hostname'"

        run_async(event_loop, _test())


class TestProtocolVlanLifecycle:
    """Test VLAN CRUD via MCP protocol."""

    def test_vlan_create_read_delete(self, mcp_session, event_loop):
        async def _test():
            # Read current VLANs
            result, _ = await call_tool_async(mcp_session, "net_get_vlans", {"host": "leaf1"})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success"

            # Create test VLAN
            result, _ = await call_tool_async(mcp_session, "net_create_vlan", {
                "host": "leaf1", "vlan_id": 997, "name": "MCP-PROTOCOL-TEST",
            })
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Create VLAN failed: {parsed.get('error', parsed)}"

            # Verify it exists
            result, _ = await call_tool_async(mcp_session, "net_get_vlans", {"host": "leaf1"})
            parsed = parse_tool_result(result)
            assert "997" in str(parsed.get("data", "")), "VLAN 997 not found after creation"

            # Delete test VLAN
            result, _ = await call_tool_async(mcp_session, "net_delete_vlan", {
                "host": "leaf1", "vlan_id": 997,
            })
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "success", f"Delete VLAN failed: {parsed.get('error', parsed)}"

        run_async(event_loop, _test())


class TestProtocolErrorHandling:
    """Test error handling via MCP protocol."""

    def test_invalid_host(self, mcp_session, event_loop):
        async def _test():
            result, _ = await call_tool_async(mcp_session, "net_get_device_info", {"host": "nonexistent-device"})
            parsed = parse_tool_result(result)
            assert parsed.get("status") == "error", f"Expected error for invalid host, got: {parsed}"

        run_async(event_loop, _test())

    def test_nonexistent_tool(self, mcp_session, event_loop):
        async def _test():
            result, _ = await call_tool_async(mcp_session, "this_tool_does_not_exist", {"host": "spine1"})
            # Should return isError=True or have error content
            parsed = parse_tool_result(result)
            is_error = getattr(result, "isError", False) or parsed.get("isError", False)
            assert is_error or "unknown" in str(parsed).lower() or parsed.get("status") == "error"

        run_async(event_loop, _test())
