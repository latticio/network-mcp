# network-mcp

[![PyPI version](https://img.shields.io/pypi/v/network-mcp.svg)](https://pypi.org/project/network-mcp/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-8%2C422%20passing-brightgreen)](tests/)
[![Coverage](https://img.shields.io/badge/coverage-92%25-brightgreen)](tests/)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)

**AI-powered multi-vendor network automation.** 261 purpose-built tools, 10 resources, and 15 prompt templates give AI assistants structured access to **Arista EOS**, **Cisco IOS-XE**, **Cisco NX-OS**, and **Juniper JunOS** devices — turning natural-language questions into real-time network queries, diagnostics, and configuration changes.

Works with **Claude Desktop**, **Claude Code**, **Cursor**, **VS Code Copilot**, and any MCP-compatible client.

---

## Why network-mcp?

> *43% of organizations have zero dedicated network automation staff. 67% of network operations are still manual.* — NANOG / Gartner

network-mcp lets any network engineer manage multi-vendor infrastructure through natural language. No scripting required.

- **261 purpose-built tools** across 4 vendor platforms — the most comprehensive network MCP server available
- **Multi-vendor by design** — one tool works across Arista, Cisco, and Juniper. Add a device, set the `platform` field, done.
- **Enterprise-grade** — RBAC, audit logging, OWASP MCP Top 10 compliance, mTLS, circuit breakers, OpenTelemetry
- **Operational workflows** — not just "run show commands." Full troubleshooting, validation, change management, and drift detection
- **5-minute setup** — `pip install network-mcp` and connect. Or try demo mode with zero configuration.

## How It Works

```
You: "Are any BGP neighbors down on router-01?"
  │
  ▼
AI Assistant (Claude, Cursor, VS Code Copilot)
  │  selects tool: net_get_bgp_summary(host="router-01")
  ▼
network-mcp server
  │  vendor-appropriate API call (eAPI / RESTCONF / NX-API / NETCONF)
  ▼
Network Device (any supported vendor)
  │  returns structured JSON
  ▼
AI: "All 4 BGP neighbors on router-01 are Established.
     Total prefixes received: 612."
```

The [Model Context Protocol](https://modelcontextprotocol.io) (MCP) is an open standard that lets AI assistants interact with external systems through structured interfaces. Instead of pasting CLI output into a chat window, MCP gives your AI direct access to tools that query and configure your network.

## Quick Start

### Try it now (no devices needed)

```bash
NET_DEMO_MODE=true uvx network-mcp
```

Demo mode returns realistic mock data for all tools — perfect for evaluation and learning.

### Install

```bash
# Option 1: uvx (zero-install)
uvx network-mcp

# Option 2: pip
pip install network-mcp

# Option 3: Docker
docker run -e NET_USERNAME=admin -e NET_PASSWORD=secret latticio/network-mcp

# Option 4: From source
git clone https://github.com/latticio/network-mcp.git
cd network-mcp
uv sync --extra dev
```

### Vendor extras

```bash
pip install network-mcp[cisco]    # Cisco IOS-XE and NX-OS (httpx)
pip install network-mcp[juniper]  # Juniper JunOS (scrapli-netconf)
pip install network-mcp[gnmi]     # gNMI telemetry (pygnmi)
pip install network-mcp[anta]     # ANTA validation framework
pip install network-mcp[all]      # Everything
```

### Connect to Claude Desktop

File: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)

```json
{
  "mcpServers": {
    "network-mcp": {
      "command": "uvx",
      "args": ["network-mcp"],
      "env": {
        "NET_DEMO_MODE": "true"
      }
    }
  }
}
```

Then ask: *"What version is spine-01 running?"* or *"Run a fabric health check across all devices."*

Replace `NET_DEMO_MODE` with `NET_USERNAME`, `NET_PASSWORD`, and `NET_INVENTORY_FILE` to connect to real devices.

### More clients

<details>
<summary>Claude Code</summary>

```bash
claude mcp add network-mcp -- uvx network-mcp
```
</details>

<details>
<summary>Cursor</summary>

File: `.cursor/mcp.json`

```json
{
  "mcpServers": {
    "network-mcp": {
      "command": "uvx",
      "args": ["network-mcp"],
      "env": {
        "NET_USERNAME": "admin",
        "NET_PASSWORD": "your-password"
      }
    }
  }
}
```
</details>

<details>
<summary>VS Code</summary>

File: `.vscode/mcp.json`

```json
{
  "mcpServers": {
    "network-mcp": {
      "command": "uvx",
      "args": ["network-mcp"],
      "env": {
        "NET_USERNAME": "admin",
        "NET_PASSWORD": "your-password"
      }
    }
  }
}
```
</details>

<details>
<summary>HTTP Transport (remote/team deployment)</summary>

```bash
# Start the server over HTTP
network-mcp --transport streamable-http

# With OAuth 2.1 authentication
AUTH_ENABLED=true AUTH_ISSUER_URL=https://auth.example.com \
  network-mcp --transport streamable-http
```
</details>

## Vendor Support

| Vendor | Platform | Driver | Transport | Tools |
|--------|----------|--------|-----------|------:|
| Arista | EOS | `EosDriver` | eAPI (HTTPS) | 175 |
| Cisco | IOS-XE | `IosXeDriver` | RESTCONF / SSH | 73+ |
| Cisco | NX-OS | `NxosDriver` | NX-API / SSH | 76+ |
| Juniper | JunOS | `JunosDriver` | NETCONF | 77+ |

All four drivers implement the `NetworkDriver` protocol with 35 normalized getters. The 70 vendor-agnostic `net_*` tools work identically across all platforms.

## Architecture

```
MCP Client (Claude Desktop, Cursor, VS Code, Claude Code)
  │
  │  stdio or Streamable HTTP (JSON-RPC 2.0)
  ▼
┌────────────────────────────────────────────────────────────┐
│  network-mcp (FastMCP)                                     │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Common Tools (70) — net_* namespace                  │  │
│  │  Vendor-agnostic: works with ANY supported platform  │  │
│  ├──────────────────────────────────────────────────────┤  │
│  │  EOS Tools (175) — eos_* namespace                   │  │
│  │  Deep Arista EOS feature coverage                    │  │
│  ├──────────────────────────────────────────────────────┤  │
│  │  Vendor-Specific Tools (16) — nxos/iosxe/junos_*     │  │
│  │  NX-OS vPC/FEX, IOS-XE RESTCONF, JunOS NETCONF      │  │
│  ├──────────────────────────────────────────────────────┤  │
│  │  Resources (10) │ Prompts (15) │ Completions         │  │
│  ├──────────────────────────────────────────────────────┤  │
│  │  Enterprise Layer                                    │  │
│  │  Cache │ Circuit Breaker │ Rate Limiting │ Audit     │  │
│  │  RBAC │ mTLS │ OpenTelemetry │ Sanitization          │  │
│  └──────────────────────────────────────────────────────┘  │
│                        │                                   │
│              NetworkDriver Protocol                        │
│         ┌──────────┬──────────┬──────────┐                 │
│         ▼          ▼          ▼          ▼                  │
│    EosDriver  IosXeDriver NxosDriver JunosDriver           │
│    (pyeapi)   (RESTCONF)  (NX-API)   (NETCONF)             │
└─────┼──────────┼──────────┼──────────┼─────────────────────┘
      ▼          ▼          ▼          ▼
  Arista EOS  Cisco IOS-XE  Cisco NX-OS  Juniper JunOS
```

## Tool Overview

### `net_*` — Vendor-Agnostic (70 tools)

Work with **any** supported platform. Same tool, same output format, regardless of vendor.

| Category | Example Tools |
|----------|--------------|
| Device & monitoring | `net_get_device_info`, `net_get_device_health`, `net_get_environment_temp` |
| Interfaces | `net_get_interfaces`, `net_get_interface_counters`, `net_get_interface_errors` |
| Routing | `net_get_bgp_summary`, `net_get_ospf_neighbors`, `net_get_route_table` |
| Switching | `net_get_vlans`, `net_get_mac_table`, `net_get_stp_status`, `net_get_lldp_neighbors` |
| Security | `net_get_acls`, `net_get_aaa_status`, `net_get_snmp_config` |
| Config management | `net_get_running_config`, `net_save_config`, `net_push_config` |
| Troubleshooting | `net_diagnose_bgp_session`, `net_troubleshoot_interface`, `net_ping` |
| Change management | `net_approve_change`, `net_rollback_change`, `net_check_drift` |

### `eos_*` — Arista EOS (175 tools)

Deep EOS coverage: EVPN-VXLAN, config sessions, ANTA validation, gNMI telemetry, MLAG, CloudVision, and more.

### `nxos_*` / `iosxe_*` / `junos_*` — Vendor-Specific (16 tools)

Platform-unique features: NX-OS vPC and FEX, IOS-XE RESTCONF capabilities, JunOS commit confirmed and firewall filters.

> See [docs/TOOLS.md](docs/TOOLS.md) for the complete tool reference with all parameters.

## Example Workflows

Ask your AI assistant natural-language questions — it selects the right tools automatically.

### Multi-vendor BGP troubleshooting

> "Check the BGP neighbors on router-01 and tell me if any are down."

1. `net_get_bgp_summary("router-01")` — works on EOS, IOS-XE, NX-OS, or JunOS
2. If peers are down: `net_diagnose_bgp_session("router-01", peer="10.0.0.2")` for automated root-cause analysis
3. Structured findings with severity, root causes, and recommended actions

### Config session with rollback timer (EOS)

> "Create VLAN 200 named CAMERAS on leaf-01a with a 5-minute rollback timer."

1. `eos_create_config_session("leaf-01a")` — start atomic session
2. `eos_add_to_config_session(...)` — stage changes
3. `eos_get_config_session_diff(...)` — review diff
4. `eos_commit_config_session_timer(...)` — commit with auto-rollback
5. `eos_confirm_commit_timer(...)` — make permanent after verification

### Fabric-wide health check

> "Run a health check across all spine switches."

1. `net_validate_fabric_health(role="spine")` — single workflow tool
2. Returns health score, per-device findings, flagged issues

### Cross-vendor comparison

> "Compare BGP status across all my routers, regardless of vendor."

The AI calls `net_get_bgp_summary` on each device and compares peer counts, states, and prefixes across Arista, Cisco, and Juniper in a single summary.

## Device Inventory

Create a `devices.yaml` for multi-device management:

```yaml
devices:
  spine-01:                    # Arista EOS (eAPI)
    host: 10.0.0.1
    platform: eos
    username: admin
    password: your-password
    role: spine
    groups: [dc1]

  router-01:                   # Cisco IOS-XE (RESTCONF)
    host: 10.0.1.1
    platform: iosxe
    username: admin
    password: your-password

  nexus-01:                    # Cisco NX-OS (NX-API)
    host: 10.0.2.1
    platform: nxos
    username: admin
    password: your-password

  juniper-01:                  # Juniper JunOS (NETCONF)
    host: 10.0.3.1
    platform: junos
    username: admin
    password: your-password
```

Set `NET_INVENTORY_FILE=/path/to/devices.yaml` and every tool works across all devices.

## Integrations

Built-in connections to popular infrastructure platforms:

| Integration | What It Does |
|-------------|-------------|
| **NetBox** | DCIM inventory sync, drift detection, topology validation |
| **ServiceNow** | Incident workflows, diagnostic attachments, change validation |
| **HashiCorp Vault** | Dynamic credential management for production deployments |
| **Prometheus / Grafana** | Metrics export, alert-driven diagnostic workflows |
| **Ansible** | Inventory export in Ansible-compatible format |
| **CloudVision** | Arista CVP inventory sync and config compliance |

See [docs/integrations/](docs/integrations/) for setup guides.

## Enterprise Features

| Feature | Description |
|---------|-------------|
| **RBAC** | Scope-based access control: `network:read`, `network:write`, `network:admin`, `network:audit` |
| **OAuth 2.1 / JWT** | Token-based auth with PKCE, refresh, and revocation |
| **mTLS** | Mutual TLS for machine-to-machine authentication |
| **Audit logging** | JSON audit trail with tenant attribution and cryptographic signing |
| **OWASP MCP Top 10** | Compliant with the OWASP MCP security framework |
| **Circuit breakers** | Per-device fail-fast to prevent cascading failures |
| **Rate limiting** | Token-bucket per device with per-user quotas |
| **OpenTelemetry** | Distributed tracing with OTLP export |
| **Prometheus metrics** | `/metrics` endpoint with SLO definitions |
| **Bulkhead isolation** | Per-vendor driver isolation |
| **Graceful shutdown** | In-flight request draining |
| **Change management** | Approval workflows, dry-run, blast radius estimation |
| **Drift detection** | Cross-vendor config drift detection and remediation |
| **Hot config reload** | Update rate limits, cache TTLs, and timeouts without restart |

## Safety

| Feature | Description |
|---------|-------------|
| **Read-only mode** | `NET_READ_ONLY=true` (default) blocks all write operations |
| **Command deny-list** | Blocks `reload`, `write erase`, `zerotouch`, `bash`, and other dangerous commands |
| **CLI injection prevention** | Validates all parameters against injection patterns |
| **Credential sanitization** | Passwords and keys redacted from all output |
| **Config sessions** | Atomic changes with review-before-commit workflow |
| **Commit timers** | Auto-rollback if changes aren't confirmed |
| **Output size limits** | Prevents oversized responses from consuming context window |
| **Tool annotations** | Every tool has `readOnlyHint`/`destructiveHint` metadata |

## Configuration

All settings via environment variables. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `NET_USERNAME` | `admin` | Device username |
| `NET_PASSWORD` | *(empty)* | Device password |
| `NET_INVENTORY_FILE` | *(none)* | Path to `devices.yaml` |
| `NET_READ_ONLY` | `true` | Block write operations |
| `NET_DEMO_MODE` | `false` | Use mock data |
| `NET_PROGRESSIVE_DISCOVERY` | `false` | Deferred module loading |
| `AUTH_ENABLED` | `false` | Enable OAuth 2.1/JWT |
| `NET_RBAC_ENABLED` | `false` | Enable RBAC |
| `LOG_LEVEL` | `INFO` | Logging level |

See [.env.example](.env.example) for the complete list (40+ settings).

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/GETTING_STARTED.md) | 5-minute quickstart with example output |
| [Tools Reference](docs/TOOLS.md) | All 261 tools with parameters |
| [Architecture](docs/ARCHITECTURE.md) | Internal design and multi-tenancy |
| [Deployment](docs/DEPLOYMENT.md) | Docker, Kubernetes, HTTP transport |
| [Security](docs/SECURITY.md) | Auth, RBAC, OWASP compliance |
| [Plugin Development](docs/PLUGIN-DEVELOPMENT.md) | Build custom drivers and tools |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues and solutions |
| [Contributing](docs/CONTRIBUTING.md) | Development setup and guidelines |

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code style, testing patterns, and how to add new tools.

```bash
git clone https://github.com/latticio/network-mcp.git
cd network-mcp
uv sync --extra dev
uv run pytest -v              # Run tests
uv run ruff check src/        # Lint
```

## Community

- [GitHub Discussions](https://github.com/latticio/network-mcp/discussions) — Questions, ideas, and show-and-tell
- [Discord](https://discord.gg/latticio) — Real-time chat and support
- [Issues](https://github.com/latticio/network-mcp/issues) — Bug reports and feature requests

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.
