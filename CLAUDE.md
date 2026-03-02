# CLAUDE.md — Network MCP Server

Multi-vendor network MCP platform (v7.0.0) enabling AI assistants to interact with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS devices. Wraps vendor APIs behind 261 MCP tools, 10 resources, and 15 prompt templates for Claude Desktop, Cursor, VS Code, etc.

## Stack

Python 3.11+ | FastMCP (`mcp[cli]>=1.26,<2.0`) | pydantic v2 | pydantic-settings | tenacity | uv | ruff | pytest

Vendor extras: pyeapi (Arista), httpx (Cisco), scrapli-netconf (Juniper), pygnmi (gNMI), anta (validation), pyjwt (auth)

## Project Structure

```
src/network_mcp/
  server.py                   # FastMCP instance, lifespan, RBAC wiring, module loading
  connection.py               # Thread-safe pyeapi connection pool (FIFO eviction)
  config.py                   # NetworkSettings (Pydantic BaseSettings, env + .env)
  helpers.py                  # run_show_command/run_config_command, validation, retry, rate limiting
  executor.py                 # Low-level command execution
  driver.py                   # DeviceDriver protocol and PyeapiDriver adapter
  mock_driver.py              # MockDriver for demo mode (no real devices)
  inventory.py                # YamlInventoryBackend for devices.yaml loading
  cache.py                    # TTL-based command cache with per-host invalidation
  circuit_breaker.py          # Per-device circuit breaker (fail-fast for unreachable devices)
  rate_limiter.py             # Token-bucket per-device rate limiter
  observability.py            # Metrics collector and server health reporting
  middleware.py               # Request context propagation
  completions.py              # MCP argument completions (host, interface, VLAN, VRF, session)
  audit.py                    # JSON audit logging with tenant attribution
  sanitizer.py                # Credential redaction, output size limits
  auth.py                     # OAuth 2.1/JWT TokenVerifier helpers
  rbac.py                     # Scope-based tool access control (network:read/write/admin/audit)
  plugins.py                  # Entry-point plugin discovery (drivers, compliance, tools)
  schemas.py                  # Pydantic output models for structured output
  gnmi_connection.py          # gNMI connection wrapper (auto-detected)
  feature_flags.py            # Tenant-based feature targeting
  mock_data/                  # Demo mode mock inventory and responses
  integrations/               # NetBox, ServiceNow, Prometheus, Vault, Ansible
  drivers/                    # Multi-vendor driver implementations
    base.py                   # NetworkDriver protocol (35 normalized getters)
    __init__.py               # DRIVER_REGISTRY: eos, iosxe, nxos, junos
    cisco_iosxe.py            # IosXeDriver (RESTCONF + SSH)
    cisco_nxos.py             # NxosDriver (NX-API + SSH)
    juniper_junos.py          # JunosDriver (NETCONF)
  tools/                      # Tool modules organized by function
    meta.py                   # Progressive discovery (5 tools)
    device.py                 # Device info, health, inventory (12 tools)
    interfaces.py             # Interface status, counters, errors, transceivers (13 tools)
    vlans.py                  # VLAN CRUD (6 tools)
    routing.py                # BGP, OSPF, routes, ARP/NDP, prefix-lists (21 tools)
    switching.py              # MLAG, STP, MAC table, LLDP, port-channels (14 tools)
    monitoring.py             # CPU, memory, temp, power, fans, NTP (8 tools)
    config_mgmt.py            # Running/startup config, diffs, save (10 tools)
    sessions.py               # Config sessions (8 tools)
    checkpoints.py            # Config checkpoints (4 tools)
    workflows.py              # Multi-step diagnostic workflows (7 tools)
    troubleshoot.py           # Ping, traceroute, flap detection (3 tools)
    fabric.py                 # Multi-device fabric health (6 tools)
    validation.py             # ANTA-based validation (5 tools)
    evpn_vxlan.py             # VXLAN/EVPN (optional, 9 tools)
    security.py               # ACLs, AAA, RADIUS, CoPP, SNMP (optional, 15 tools)
    vrf.py                    # VRF (optional, 5 tools)
    bfd.py                    # BFD (optional, 2 tools)
    event_monitor.py          # Events, MAC moves, route changes (optional, 3 tools)
    qos.py                    # QoS (optional, 3 tools)
    gnmi.py                   # gNMI (requires pygnmi, 5 tools)
    cloudvision.py            # CloudVision Portal (2 tools)
    export.py                 # Export as Markdown/CSV/JSON (2 tools)
    compliance.py             # CIS compliance (3 tools)
    common/                   # 13 vendor-agnostic modules (net_* namespace, 70 tools)
    cisco/                    # Cisco-specific (nxos_*/iosxe_*, 9 tools)
    juniper/                  # JunOS-specific (junos_*, 7 tools)
  resources/inventory.py      # 10 resources (net:// URIs)
  prompts/workflows.py        # 15 prompt templates

tests/                        # 8,422 tests, 92%+ coverage
```

## Architecture

```
MCP Host (Claude/Cursor) --JSON-RPC--> server.py --drivers--> Network Devices
                                          |
                            FastMCP + ConnectionManager + RBAC + Plugins
                                          |
                           ┌──────────────┼──────────────┐
                           ▼              ▼              ▼
                     PyeapiDriver   IosXeDriver    JunosDriver
                       (eAPI)       (RESTCONF)     (NETCONF)
                           ▼              ▼              ▼
                      Arista EOS    Cisco IOS-XE   Juniper JunOS
                                   Cisco NX-OS
                                   (NxosDriver/NX-API)
```

**Settings**: `NetworkSettings` in `config.py` (Pydantic BaseSettings). All settings use `NET_*` env var prefix.

**Module loading**: Core modules always load. Optional modules (evpn_vxlan, security, vrf, bfd, event_monitor, qos, compliance) load conditionally via `NET_DISABLED_MODULES` / `NET_ENABLED_MODULES`. Vendor-specific tools load when their dependencies are installed. Progressive discovery mode (`NET_PROGRESSIVE_DISCOVERY=true`) defers all modules except meta and workflows.

**Multi-vendor**: The `NetworkDriver` protocol defines 35 normalized getters. All 4 drivers implement this. `DRIVER_REGISTRY` maps platform strings to driver classes. Common tools in `tools/common/` use the registry for vendor-agnostic operations.

**RBAC**: When `NET_RBAC_ENABLED=true`, tool calls are checked against JWT scope claims. Scopes: `network:read`, `network:write`, `network:admin`, `network:audit`.

**Plugins**: Third-party extensions via Python entry points: `network_mcp.drivers`, `network_mcp.compliance`, `network_mcp.tools`.

## Tool Patterns

### Read-only tools use `run_show_command()`

```python
from network_mcp.helpers import READ_ONLY, run_show_command
from network_mcp.server import conn_mgr, mcp

@mcp.tool(annotations=READ_ONLY)
def get_vlans(host: str) -> dict:
    """Get all VLANs with names and ports from a network device."""
    result = run_show_command(conn_mgr, host, ["show vlan"])
    if result["status"] == "error":
        return result
    return {"status": "success", "device": host, "data": result["data"][0].get("vlans", {})}
```

### Write tools use `run_config_command()`

```python
from network_mcp.helpers import WRITE_SAFE, run_config_command, validate_vlan_id
from network_mcp.server import conn_mgr, mcp

@mcp.tool(annotations=WRITE_SAFE)
def create_vlan(host: str, vlan_id: int, name: str) -> dict:
    """[WRITE] Create a VLAN on a device. Requires NET_READ_ONLY=false."""
    vlan_err = validate_vlan_id(vlan_id)
    if vlan_err:
        return {"status": "error", "device": host, "error": vlan_err}
    return run_config_command(conn_mgr, host, [f"vlan {vlan_id}", f"name {name}"], "create_vlan")
```

### Key conventions

- `host` is ALWAYS the first parameter on every tool
- Tool annotations: `READ_ONLY`, `WRITE_SAFE`, `DESTRUCTIVE` (from helpers.py)
- Decorators MUST have parentheses: `@mcp.tool()`, `@mcp.resource("uri")`, `@mcp.prompt()`
- Return structured dicts: `{"status": "success"|"error", "device": host, "data"|"error": ...}`
- `run_show_command` handles: host validation, rate limiting, retry, audit logging, credential sanitization, output size limits
- `run_config_command` adds: read-only check, command deny-list enforcement

## Safety Features

- **Read-only mode**: `NET_READ_ONLY=true` (default) blocks ALL write operations
- **Command deny-list**: Blocks `reload`, `write erase`, `zerotouch`, `bash`, `delete`, `format`, etc.
- **CLI injection prevention**: Blocks `;`, `|`, backticks, `$()`, `${}`, newlines, null bytes
- **Credential sanitization**: Passwords redacted from all output
- **Rate limiting**: Token-bucket per device
- **Output size limits**: Prevents context window overflow
- **Audit logging**: JSON audit trail with tenant attribution
- **Circuit breaker**: Per-device fail-fast to prevent cascading failures

## Testing

```bash
uv run pytest                              # run all tests
uv run pytest -v                           # verbose
uv run pytest tests/test_device_tools.py   # single file
uv run pytest --cov=network_mcp            # with coverage
```

### Mocking pattern

Tool modules import `conn_mgr` as a module-level reference. You must `patch.object()` on the real instance:

```python
from network_mcp.server import conn_mgr

@pytest.fixture
def mock_conn_mgr(mock_node):
    with patch.object(conn_mgr, "get_node", return_value=mock_node) as patched:
        yield patched
```

## Development Commands

```bash
uv sync --extra dev                    # install for development
uv run ruff check src/                 # lint
uv run ruff check --fix src/           # lint + autofix
uv run ruff format src/                # format
uv run network-mcp                     # run server (stdio)
uv run network-mcp --transport streamable-http  # run with HTTP transport
```

## Critical Rules

1. **Never log to stdout** — stdout is the JSON-RPC transport
2. **Never use `print()`** — it corrupts the MCP protocol stream
3. **Never hardcode credentials** — use env vars or inventory file
4. **Always handle exceptions** — use `run_show_command`/`run_config_command` helpers
5. **Always use parentheses on decorators** — `@mcp.tool()` not `@mcp.tool`
6. **FastMCP() does not accept `version` kwarg** — use `FastMCP("name")` only
7. **JSON encoding is default** — only use `encoding="text"` for text-only commands

## Environment Variables

Key settings (full list in `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `NET_USERNAME` | `admin` | Device username |
| `NET_PASSWORD` | (empty) | Device password |
| `NET_TRANSPORT` | `https` | Connection transport |
| `NET_INVENTORY_FILE` | (none) | Path to devices.yaml |
| `NET_READ_ONLY` | `true` | Block all write operations |
| `NET_DEMO_MODE` | `false` | Use mock data (no real devices) |
| `NET_PROGRESSIVE_DISCOVERY` | `false` | Deferred module loading |
| `RATE_LIMIT_SHOW` | `5.0` | Show commands/sec per device |
| `AUTH_ENABLED` | `false` | Enable OAuth 2.1/JWT auth |
| `NET_RBAC_ENABLED` | `false` | Enable scope-based RBAC |
| `LOG_LEVEL` | `INFO` | Logging level |

## Git Conventions

- Branch: `main`
- Commits: conventional (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`)
- All code must pass `ruff check` and `pytest` before merge

## Reference Docs

- [docs/TOOLS.md](docs/TOOLS.md) — tool reference with parameters
- [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) — quickstart guide
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — internal architecture
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Docker, HTTP transport, Kubernetes
- [docs/SECURITY.md](docs/SECURITY.md) — security features and configuration
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — contribution guide
- [.env.example](.env.example) — all environment variables
- [devices.example.yaml](devices.example.yaml) — inventory format
