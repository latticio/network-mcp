# Getting Started with Network MCP Server

## First 5 Minutes (No Hardware Required)

Get from zero to network automation in under 5 minutes using **demo mode** — no real network devices needed.

### Step 1: Install

```bash
# Option A: Run directly with uvx (recommended — no install needed)
uvx latticio

# Option B: Install with pip
pip install latticio

# Option C: Install with uv
uv pip install latticio
```

**Requirements:** Python 3.11 or later.

**Optional extras** for vendor-specific drivers:

```bash
pip install latticio[arista]    # Arista EOS support (pyeapi)
pip install latticio[cisco]     # Cisco IOS-XE and NX-OS support (httpx)
pip install latticio[juniper]   # Juniper JunOS support (scrapli-netconf)
pip install latticio[gnmi]      # gNMI telemetry support (pygnmi)
pip install latticio[anta]      # ANTA network validation framework
pip install latticio[auth]      # OAuth 2.1 for HTTP transport (PyJWT)
pip install latticio[all]       # All vendor drivers + gNMI + ANTA
```

### Step 2: Start in Demo Mode

Demo mode provides 6 simulated Arista EOS devices with realistic mock data. Every tool works the same as it would against real hardware.

```bash
NET_DEMO_MODE=true network-mcp
```

The demo topology is a spine-leaf fabric with MLAG pairs:

```
       spine-01 (DCS-7280SR3, AS 65000)     spine-02 (DCS-7280SR3, AS 65000)
          │       │       │       │              │       │       │       │
          │       │       │       └──────────────┼───────┼───────┼───────┘
          │       │       └──────────────────────┼───────┼───────┘
          │       └──────────────────────────────┼───────┘
          │                                      │
   ┌──────┼──────────────────────────────────────┼──────┐
   │      │                                      │      │
leaf-01  leaf-02                           leaf-03  leaf-04
(7050CX3) (7050CX3)                        (7050CX3) (7050CX3)
   └──MLAG──┘                                └──MLAG──┘
    AS 65001                                  AS 65002
  MLAG-PAIR-01                              MLAG-PAIR-02
```

Each device runs EOS 4.32.1F with BGP, MLAG, VLANs (1, 100, 200, 4094), and full interface counters.

### Step 3: Connect to Your AI Assistant

Choose your MCP client and add the server configuration.

#### Claude Desktop

Add to your config file:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "latticio": {
      "command": "uvx",
      "args": ["latticio"],
      "env": {
        "NET_DEMO_MODE": "true"
      }
    }
  }
}
```

#### Claude Code

Create `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "latticio": {
      "command": "uvx",
      "args": ["latticio"],
      "env": {
        "NET_DEMO_MODE": "true"
      }
    }
  }
}
```

Or add it via the CLI:

```bash
claude mcp add latticio -- env NET_DEMO_MODE=true uvx latticio
```

#### Cursor

Add to `.cursor/mcp.json` in your project:

```json
{
  "mcpServers": {
    "latticio": {
      "command": "uvx",
      "args": ["latticio"],
      "env": {
        "NET_DEMO_MODE": "true"
      }
    }
  }
}
```

#### VS Code Copilot

Add to `.vscode/mcp.json` in your project:

```json
{
  "mcpServers": {
    "latticio": {
      "command": "uvx",
      "args": ["latticio"],
      "env": {
        "NET_DEMO_MODE": "true"
      }
    }
  }
}
```

### Step 4: First Queries

Once your AI assistant is connected, try these prompts.

#### Vendor-agnostic tools (`net_*`)

These work with any supported platform (EOS, IOS-XE, NX-OS, JunOS):

- **"Get device info for spine-01"** — calls `net_get_device_info` for normalized device facts
- **"Show interfaces on spine-01"** — calls `net_get_interfaces` for interface status across any vendor
- **"Get BGP summary for spine-01"** — calls `net_get_bgp_summary` for a normalized BGP peer view

#### EOS-specific tools (`eos_*`)

These use pyeapi for deeper Arista EOS functionality:

- **"Show me the health of spine-01"** — device version, CPU, memory, temperature via `eos_get_device_health`
- **"Are any BGP neighbors down on spine-01?"** — checks all 4 leaf peers via `eos_get_bgp_summary`
- **"What VLANs are configured on leaf-01?"** — returns VLANs 1, 100, 200, 4094 via `eos_get_vlans`
- **"Compare the running config between spine-01 and spine-02"** — section-aware diff via `eos_compare_device_configs`

### Step 5: Explore

Discover all 239 tools across 24+ modules:

- **"What tool categories are available?"** — triggers `eos_list_tool_categories` for progressive discovery
- **"Run a full health check on the fabric"** — uses `eos_validate_fabric_health` to score all devices
- **"Show me interface errors across all leaf switches"** — multi-device interface counter analysis
- **"Check MLAG status on leaf-01 and leaf-02"** — verifies peer link, config sanity, and port states

---

## What the Output Looks Like

MCP tools return structured JSON that your AI assistant interprets into natural language. Here are examples of raw tool responses.

### Vendor-agnostic device info (`net_get_device_info`)

```json
{
  "status": "success",
  "device": "spine-01",
  "data": {
    "hostname": "spine-01",
    "vendor": "Arista",
    "model": "DCS-7280SR3-48YC8",
    "os_version": "4.32.1F",
    "serial_number": "SPINE01-SERIAL",
    "uptime": 8640000.0
  }
}
```

### EOS-specific device info (`eos_get_device_info`)

```json
{
  "status": "success",
  "device": "spine-01",
  "data": {
    "modelName": "DCS-7280SR3-48YC8",
    "version": "4.32.1F",
    "serialNumber": "SPINE01-SERIAL",
    "systemMacAddress": "02:1c:73:00:01:01",
    "uptime": 8640000.0,
    "memTotal": 8155840,
    "memFree": 5423104
  }
}
```

### Error response (standard format)

```json
{
  "status": "error",
  "device": "unknown-host",
  "error": "Device 'unknown-host' not found in inventory. Available devices: spine-01, spine-02, leaf-01, leaf-02, leaf-03, leaf-04"
}
```

All tools follow this pattern: `{"status": "success"|"error", "device": "<host>", "data"|"error": ...}`.

---

## Connecting to Real Devices

### Multi-vendor device inventory

For managing devices from multiple vendors, create a `devices.yaml`:

```yaml
devices:
  # Arista EOS — uses pyeapi (eAPI)
  spine-01:
    host: 10.0.0.1
    platform: eos
    transport: https
    role: spine
    groups: [dc1]
    tags: [production]

  # Cisco IOS-XE — uses RESTCONF (with SSH fallback)
  core-rtr-01:
    host: 10.0.0.10
    platform: iosxe
    transport: restconf
    role: core
    groups: [dc1]
    tags: [production]

  # Cisco NX-OS — uses NX-API (with SSH fallback)
  dc-core-01:
    host: 10.0.0.20
    platform: nxos
    transport: nxapi
    role: core
    groups: [dc1]
    tags: [production]

  # Juniper JunOS — uses NETCONF
  edge-rtr-01:
    host: 10.0.0.30
    platform: junos
    transport: netconf
    role: edge
    groups: [wan]
    tags: [production]
```

Point the server to your inventory:

```bash
export NET_INVENTORY_FILE=/path/to/devices.yaml
```

The `platform` field selects the driver for each device. Valid platforms: `eos`, `iosxe`, `nxos`, `junos`. Defaults to `eos` if omitted (backward compatible with existing inventories).

See [devices.example.yaml](../devices.example.yaml) for the full format including roles, groups, tags, and MLAG peer mapping.

### Setting up credentials

```bash
export NET_USERNAME=admin
export NET_PASSWORD=your_password
export NET_TRANSPORT=https   # default; use "http" for lab environments
```

Remove `NET_DEMO_MODE` from your client configuration (or set it to `false`) to connect to real devices.

### Client config for real devices

```json
{
  "mcpServers": {
    "latticio": {
      "command": "uvx",
      "args": ["latticio"],
      "env": {
        "NET_USERNAME": "admin",
        "NET_PASSWORD": "your_password",
        "NET_INVENTORY_FILE": "/path/to/devices.yaml"
      }
    }
  }
}
```

### Direct connection (no inventory file)

Without an inventory file, pass the hostname or IP address directly as the `host` parameter in any tool call. The server uses credentials from the `NET_USERNAME` and `NET_PASSWORD` environment variables. Direct connections default to Arista EOS (pyeapi). For other vendors, use an inventory file with the `platform` field.

### Enabling write operations

By default, the server is read-only. To enable configuration changes:

```bash
export NET_READ_ONLY=false   # CAUTION: allows configuration changes on your devices
```

Write operations include VLAN creation/deletion, BGP neighbor management, interface admin state changes, and configuration pushes. Use config sessions with commit timers for safe change workflows.

---

## Install Troubleshooting

### Python version mismatch

```
ERROR: network-mcp requires Python >=3.11
```

Check your Python version:

```bash
python3 --version
```

If below 3.11, install a newer version from [python.org](https://www.python.org/downloads/) or via pyenv:

```bash
pyenv install 3.12
pyenv global 3.12
```

### Permission errors on Linux/macOS

```
ERROR: Could not install packages due to an EnvironmentError: [Errno 13] Permission denied
```

Use a virtual environment instead of installing globally:

```bash
uv venv && source .venv/bin/activate
uv pip install latticio
```

Or use the `--user` flag: `pip install --user network-mcp`

### uvx not found

If `uvx` is not available, install uv first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On macOS with Homebrew: `brew install uv`

On Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

### SSL certificate errors

```
ssl.SSLCertificateVerifyError: certificate verify failed
```

Network devices often use self-signed certificates. For lab environments:

- **Arista EOS (eAPI):** Switch to HTTP transport: `export NET_TRANSPORT=http`
- **Cisco IOS-XE / NX-OS (RESTCONF / NX-API):** Disable SSL verification: `export NET_VERIFY_SSL=false`
- **Production:** Install the device's CA certificate or configure certificate verification in your inventory file.

### Connection refused

**Arista EOS (eAPI not enabled):**

```
ConnectionError: Unable to connect to eAPI
```

Enable eAPI on the switch:

```
configure
management api http-commands
   no shutdown
end
write memory
```

Verify eAPI is listening: `curl -k https://<switch-ip>/command-api` should return a response (even a 401 means eAPI is running).

**Cisco IOS-XE (RESTCONF not enabled):**

```
ConnectionError: Unable to connect via RESTCONF
```

Enable RESTCONF on the device:

```
configure terminal
restconf
ip http secure-server
end
```

**Cisco NX-OS (NX-API not enabled):**

```
ConnectionError: Unable to connect via NX-API
```

Enable NX-API on the device:

```
configure terminal
feature nxapi
end
```

**Juniper JunOS (NETCONF not enabled):**

```
ConnectionError: Unable to connect via NETCONF
```

Enable NETCONF on the device:

```
set system services netconf ssh
commit
```

### Claude Desktop not detecting the server

1. Verify your config JSON is valid (no trailing commas, correct syntax)
2. Quit Claude Desktop completely (not just close the window) and reopen it
3. Check the Claude Desktop logs for MCP connection errors
4. Ensure `uvx` is in your system PATH — try running `uvx latticio` in a terminal first

---

## Backward Compatibility

Environment variables use `NET_*` as the primary prefix. The legacy `EOS_*` prefix is still accepted as a fallback for all settings (e.g., `EOS_USERNAME` still works but `NET_USERNAME` takes precedence). Migrate to `NET_*` for new deployments.

---

## Next Steps

- [docs/TOOLS.md](TOOLS.md) — complete tool reference with parameters for all 239 tools
- [docs/RESOURCES.md](RESOURCES.md) — MCP resource URIs for live data access
- [docs/PROMPTS.md](PROMPTS.md) — workflow prompt templates for guided operations
- [docs/DEPLOYMENT.md](DEPLOYMENT.md) — Docker, HTTP transport, health checks, and reverse proxy setup
- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — internal architecture deep-dive with data flow diagrams
- [docs/CONTRIBUTING.md](CONTRIBUTING.md) — development setup, testing patterns, and adding new tools
- [docs/SECURITY.md](SECURITY.md) — credential management and security best practices
- [README.md](../README.md) — full feature list and configuration reference
