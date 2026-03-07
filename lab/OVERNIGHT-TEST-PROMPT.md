# Overnight MCP Integration Test — Session Prompt

Copy this entire prompt into a new Claude Code session from within the `network-mcp` project directory. The 6-node containerlab spine-leaf fabric must be running inside the OrbStack `clab` VM.

---

## Context

You are working on the `network-mcp` project — a multi-vendor network MCP server (297 tools, 11 resources, 16 prompts). Read CLAUDE.md for full project details.

A 6-node Arista cEOS-lab containerlab topology is currently running inside an OrbStack Linux VM named `clab`. The nodes are:

- spine1 (172.20.20.5), spine2 (172.20.20.4)
- leaf1 (172.20.20.6), leaf2 (172.20.20.2), leaf3 (172.20.20.7), leaf4 (172.20.20.3)

All nodes run cEOS 4.35.2F (aarch64), eAPI enabled, credentials admin/admin, HTTPS on port 443.

The topology is a spine-leaf fabric:
- Each spine connects to all 4 leaves (eth1-eth4)
- leaf1-leaf2 have an MLAG peer-link (eth3-eth3)
- leaf3-leaf4 have an MLAG peer-link (eth3-eth3)

**Important:** The containers are only reachable from inside the OrbStack VM. To run commands against them, use: `orb run -m clab <command>` from macOS, or prefix shell commands with the VM context. The project is mounted at `/Users/jacobfehn/Documents/network-mcp` inside the VM. Python/uv are installed in the VM. Run tests with:

```bash
orb run -m clab bash -c "cd /Users/jacobfehn/Documents/network-mcp && uv run pytest tests/integration/ -v --run-integration -n0"
```

56 integration tests already pass (see `tests/integration/`). The existing tests cover smoke, show commands, tool functions, connection pool, containerlab integration, and write operations.

## Your Mission

Build and execute a comprehensive overnight MCP integration test suite that exercises the **full MCP protocol stack** against the live 6-node fabric. This is the first time this project has been tested against real devices — everything before this was mocked. Be ambitious. Find bugs. Break things.

### Phase 1: Build the MCP Protocol Test Harness

Create `tests/integration/test_mcp_protocol_live.py` — a pytest-based test suite that starts the actual MCP server as a subprocess (stdio transport) and sends JSON-RPC messages to it. This tests the real MCP protocol path, not just Python function imports.

The harness should:
- Start `uv run network-mcp` as a subprocess with env vars: `NET_INVENTORY_FILE=lab/devices.yaml`, `NET_READ_ONLY=false`, `NET_DEMO_MODE=false`, `NET_VERIFY_SSL=false`, `NET_USERNAME=admin`, `NET_PASSWORD=admin`
- But first generate `lab/devices.yaml` from the running containers (use `containerlab inspect` to get IPs)
- Send MCP `initialize` handshake
- Send `tools/list` to enumerate available tools
- Send `tools/call` with proper arguments for each tool
- Parse JSON-RPC responses and validate structure
- Measure latency for each call
- Handle errors gracefully (some tools may not work on cEOS — document which ones and why)

### Phase 2: Systematic Tool Coverage

Exercise every tool category against every applicable node. For each tool call, validate:
- Response has `status: success` or a documented/expected error
- Response `device` field matches the requested host
- Data structure matches the expected schema (check key fields exist)
- Latency is reasonable (< 10s for show commands, < 30s for config operations)

**Tool categories to test (use the actual function names from the codebase):**

1. **Device info** — `net_get_device_info` on all 6 nodes
2. **Interfaces** — `net_get_interfaces`, `net_get_interface_counters`, `net_get_interface_errors` on all nodes
3. **VLANs** — `net_get_vlans` on all nodes, then `net_create_vlan` + `net_delete_vlan` lifecycle on one leaf
4. **Routing** — `net_get_route_table`, `net_get_arp_table`, `net_get_bgp_summary`, `net_get_route_summary` on all nodes
5. **Switching** — `net_get_lldp_neighbors`, `net_get_mac_table`, `net_get_stp_status` on all nodes
6. **Monitoring** — `net_get_cpu_usage`, `net_get_memory_usage`, `net_get_device_health`, `net_get_environment_temp`, `net_get_ntp_status` on all nodes
7. **Config** — `net_get_running_config` on all nodes, `net_get_config_diff` if available
8. **Containerlab** — `net_containerlab_discover`, `net_containerlab_inventory`, `net_containerlab_status`
9. **LLDP topology** — call `net_get_lldp_neighbors` on all nodes, build adjacency map, validate it matches the physical topology (spine1 should see 4 leaf neighbors, each leaf should see 2 spine neighbors + 1 MLAG peer)
10. **Cross-device fabric validation** — compare running configs across nodes, verify version consistency, check interface states across linked pairs (if spine1:eth1 is up, leaf1:eth1 should be up)
11. **Write operations** — VLAN CRUD, interface description set/clear, config session create/abort
12. **Error handling** — call tools with invalid hosts, missing parameters, read-only violations

### Phase 3: Advanced Scenarios

1. **Concurrent load** — Fire 20+ tool calls in parallel across different nodes. Verify no connection pool exhaustion or deadlocks.
2. **Rapid sequential calls** — 100 sequential `net_get_device_info` calls to the same device. Check for connection reuse and consistent results.
3. **Config change detection** — Make a config change via tool, then read it back and verify. Roll back afterward.
4. **Output size stress** — Call tools that return large output (full routing tables, all interfaces, full running-config). Verify output sanitization doesn't corrupt data.
5. **Reconnection resilience** — If possible, simulate a brief connection interruption and verify recovery.

### Phase 4: Report Generation

After all tests run, generate a comprehensive report at `lab/reports/overnight-<timestamp>.md` that includes:
- Total tests run, passed, failed, skipped
- Per-tool-category pass rates
- Latency statistics (min, max, avg, p95) per tool category
- List of every failure with the tool name, arguments, error message, and full response
- List of tools that don't work on cEOS (with reasons — "unconverted command", "not supported", etc.)
- Recommendations for code fixes based on failures found
- Schema/parsing issues discovered (mock vs reality divergence)

### Important Notes

- **Check actual function names** before using them. Grep the codebase: `grep -rn "^def net_" src/network_mcp/tools/`. Don't guess names.
- **The conn_mgr patching pattern matters.** Each tool module imports `conn_mgr` at module level from `network_mcp.server`. If testing via Python imports (not MCP protocol), you must patch it in every tool module. See `tests/integration/test_tools_live.py` for the working pattern.
- **For MCP protocol tests**, patching isn't needed — the server subprocess uses its own conn_mgr with the real inventory.
- **SSL verification must be disabled** — cEOS uses self-signed certs. Set `NET_VERIFY_SSL=false`.
- **show logging requires text encoding** on cEOS (JSON mode returns "unconverted command").
- **Non-existent interfaces raise CommandError**, not empty results.
- **net_get_running_config returns `{"data": {"config": "...", "sanitized": True}}`**, not a raw string.
- All commands must run from inside the OrbStack VM: `orb run -m clab bash -c "cd /Users/jacobfehn/Documents/network-mcp && <command>"`
- The existing `.venv` inside the VM has all deps installed (pyeapi, etc.). Use `uv run pytest` or `uv run python`.
- Commit and push your work to `main` when done. Use author `Latticio <dev@latticio.com>`.
- Keep the lab running when you're done — don't destroy it.
