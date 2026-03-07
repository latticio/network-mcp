# Handoff: network-mcp scope for AristaBuilder integration

> March 2026 — This document defines what network-mcp will and will not provide for the AristaBuilder integration. Use this to plan AristaBuilder-side work.

---

## What network-mcp will provide (v9.0.0)

These changes are on the network-mcp roadmap and will be available for AristaBuilder to consume:

### 1. Inline config drift detection

**Existing tool:** `net_check_drift`
**Change:** New optional parameter `golden_config: str | None = None`

When `golden_config` is provided, the tool diffs it against the device's running config instead of reading from file or NetBox. AristaBuilder sends its generated EOS config as a string.

```python
# Current signature
net_check_drift(host: str, source: str = "file", config_path: str = "")

# New signature
net_check_drift(host: str, source: str = "file", config_path: str = "", golden_config: str | None = None)
# When golden_config is set, source and config_path are ignored
```

**Response format** (unchanged):
```json
{
  "status": "success",
  "device": "spine-01",
  "data": {
    "drift_detected": true,
    "sections": [
      {
        "section": "router bgp 65000",
        "severity": "critical",
        "intended": "neighbor 10.0.0.2 remote-as 65001",
        "actual": "neighbor 10.0.0.3 remote-as 65001"
      }
    ]
  }
}
```

### 2. Inline ANTA test execution

**Existing tool:** `eos_run_anta_tests`
**Change:** New optional parameter `catalog_yaml: str | None = None`

When `catalog_yaml` is provided, the tool parses it as an ANTA catalog instead of reading from a file path. AristaBuilder generates ANTA catalogs from topology design and sends them as YAML strings.

```python
# Current signature
eos_run_anta_tests(host: str, catalog_path: str = "")

# New signature
eos_run_anta_tests(host: str, catalog_path: str = "", catalog_yaml: str | None = None)
# When catalog_yaml is set, catalog_path is ignored
```

**Response format** (unchanged):
```json
{
  "status": "success",
  "device": "leaf-01a",
  "data": {
    "total": 12,
    "success": 10,
    "failure": 1,
    "skipped": 1,
    "results": [
      { "test": "VerifyBGPPeersHealth", "status": "success", "message": "All BGP peers established" },
      { "test": "VerifyInterfacesStatus", "status": "failure", "message": "Ethernet3 is down" }
    ]
  }
}
```

### 3. LLDP topology discovery

**New tool:** `net_build_topology_from_lldp`

Queries LLDP neighbors on multiple devices and returns a graph.

```python
net_build_topology_from_lldp(hosts: list[str]) -> dict
```

**Response format:**
```json
{
  "status": "success",
  "data": {
    "nodes": [
      { "hostname": "spine-01", "platform": "eos", "model": "DCS-7050CX3-32S", "mgmt_ip": "10.0.0.1" },
      { "hostname": "leaf-01a", "platform": "eos", "model": "DCS-7280CR3-32P4", "mgmt_ip": "10.0.0.11" }
    ],
    "edges": [
      {
        "source": "spine-01", "source_port": "Ethernet1",
        "target": "leaf-01a", "target_port": "Ethernet49",
        "speed": "100G"
      }
    ]
  }
}
```

### 4. Tool category metadata

All tools will carry an `x-tool-group` annotation in their MCP manifest. Categories map to existing module structure:

| Category | Example tools |
|----------|--------------|
| `meta` | `net_server_info`, `net_list_tools`, `net_discover_tools` |
| `device` | `net_get_device_info`, `net_get_inventory` |
| `interfaces` | `net_get_interfaces`, `net_get_interface_counters` |
| `routing` | `net_get_bgp_summary`, `net_get_routes` |
| `switching` | `net_get_vlans`, `net_get_mlag_status` |
| `monitoring` | `net_get_cpu`, `net_get_memory` |
| `config` | `net_get_running_config`, `net_check_drift` |
| `validation` | `eos_run_anta_tests`, `net_validate_fabric` |
| `fabric` | `net_fabric_health`, `net_get_fabric_bgp` |

Use `net_discover_tools(category="routing")` to load a group on demand.

### 5. LLDP response enhancement

`net_get_lldp_neighbors` will include `speed` in its per-neighbor output (e.g., `"speed": "100G"`). Use this for edge rendering.

### 6. Health endpoint

HTTP transport mode will expose `GET /health`:

```json
{
  "status": "healthy",
  "version": "9.0.0",
  "uptime_seconds": 3600,
  "devices": 12,
  "modules_loaded": ["device", "interfaces", "routing", "switching", "monitoring", "config", "validation"],
  "demo_mode": false
}
```

Use this for connection monitoring (poll every 30s). Falls back to `net_server_info` tool call over stdio transport.

---

## What network-mcp will NOT provide (deferred)

These were discussed in the integration architecture but are **out of scope** for now:

| Feature | Reason | Workaround for AristaBuilder |
|---------|--------|------------------------------|
| **CloudVision/CVaaS tools** (`eos_get_cvp_device_config`, `eos_push_config_via_cvp`) | Deferred — low priority | AristaBuilder can integrate with CVaaS directly if needed later |
| **SSE streaming for telemetry** | Adds significant complexity to network-mcp's request-response model | Use polling: call `net_get_interfaces` / `net_get_bgp_summary` on a timer for live data |
| **gNMI telemetry push** | Depends on SSE streaming | Same polling approach |
| **Telemetry overlay support** | network-mcp stays request-response | AristaBuilder can build its own polling loop using existing tools |

### Polling alternative for live data

Instead of SSE streaming, AristaBuilder can poll for live device state using existing tools:

```
// Every 30s per device (adjust interval as needed)
net_get_device_health(host)     -> CPU, memory, uptime
net_get_interfaces(host)        -> interface status, counters
net_get_bgp_summary(host)       -> BGP peer states
```

This is simpler, works over both stdio and HTTP transports, and is already rate-limited by network-mcp.

---

## Existing tools AristaBuilder should use as-is

These tools already exist in network-mcp v8.0.0 and require no changes:

| Use case | Tool(s) |
|----------|---------|
| Device inventory | `net_get_inventory`, `net_get_device_info` |
| Interface state | `net_get_interfaces`, `net_get_interface_counters`, `net_get_transceiver_info` |
| BGP state | `net_get_bgp_summary`, `net_get_bgp_neighbors` |
| LLDP neighbors | `net_get_lldp_neighbors` |
| Running config | `net_get_running_config` |
| Config sessions | `eos_create_config_session`, `eos_add_to_config_session`, `eos_get_config_session_diff`, `eos_commit_config_session_timer`, `eos_confirm_commit_timer` |
| ANTA validation | `eos_run_anta_tests` (with new inline parameter) |
| CIS compliance | `eos_check_cis_compliance` |
| Server info | `net_server_info` |
| Progressive discovery | `net_list_tools`, `net_discover_tools`, `net_describe_tool`, `net_search_tools` |

---

## Integration contract

All network-mcp tools return this shape:

```json
{ "status": "success" | "error", "device": "<hostname>", "data": { ... } | "error": "..." }
```

- `host` is always the first parameter
- `NET_READ_ONLY=true` (default) blocks write operations — AristaBuilder must document this for users
- `NET_DEMO_MODE=true` returns realistic mock data for all tools — use for demos and testing
- Device credentials never leave network-mcp — AristaBuilder stores connection config to network-mcp, not device passwords
- Tool names use `net_*` (vendor-agnostic) and `eos_*` (Arista-specific) prefixes

---

## Transport

| Phase | Transport | How it works |
|-------|-----------|-------------|
| v1.1 | **stdio** | Express backend spawns `network-mcp` as a child process. Zero config. |
| v1.2+ | **Streamable HTTP** | network-mcp runs as independent service. Express connects via URL + optional auth token. |

Both transports support the same tool interface. No code changes needed in AristaBuilder when switching — only the `McpClientManager` connection config changes.
