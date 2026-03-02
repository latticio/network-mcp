# NetBox + network-mcp Integration Guide

Use NetBox MCP alongside network-mcp to give AI agents simultaneous access to your intended network state (NetBox — the source of truth) and the actual device state (Arista EOS switches via eAPI). This enables automated drift detection, inventory reconciliation, and topology verification through natural language prompts.

Because both servers implement the Model Context Protocol, any MCP-compatible client (Claude Desktop, Claude Code, Cursor, VS Code Copilot) can compose tools from both servers in a single conversation — no custom glue code required.

## Architecture

```
                          AI Agent
                     (Claude Desktop, Cursor, etc.)
                        /                    \
                       /                      \
              NetBox MCP Server          network-mcp Server
              (Intended State)           (Actual Device State)
                    |                          |
                    v                          v
              NetBox Instance            Arista EOS Switches
              (Source of Truth)           (via eAPI / gNMI)
                        \                      /
                         \                    /
                          v                  v
                     Agent compares both sides
                              |
                              v
                     Drift Detection Report
                     (Markdown / CSV / JSON)
```

The agent calls NetBox MCP tools to retrieve intended state (device records, assigned interfaces, IP addresses, cable connections) and network-mcp tools to retrieve actual state (live device info, interface status, BGP neighbors, LLDP neighbors). It then reasons about the differences and produces a structured report.

## Prerequisites

- **NetBox MCP Server** installed and configured ([netboxlabs/netbox-mcp-server](https://github.com/netboxlabs/netbox-mcp-server))
- **network-mcp** installed (`uvx latticio` or `pip install latticio`)
- A **NetBox instance** (v3.5+) with device, interface, IP address, and cable data populated
- **Arista EOS devices** accessible via eAPI (or use demo mode for testing)
- A **NetBox API token** with read-only permissions

## Client Configuration

### Claude Desktop

File: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)

```json
{
  "mcpServers": {
    "latticio": {
      "command": "uvx",
      "args": ["latticio"],
      "env": {
        "EOS_USERNAME": "admin",
        "EOS_PASSWORD": "your-eos-password",
        "EOS_INVENTORY_FILE": "/path/to/devices.yaml"
      }
    },
    "netbox": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/netbox-mcp-server",
        "run",
        "netbox-mcp-server"
      ],
      "env": {
        "NETBOX_URL": "https://netbox.example.com/",
        "NETBOX_TOKEN": "your-netbox-api-token"
      }
    }
  }
}
```

### Claude Code

```bash
# Add network-mcp
claude mcp add network-mcp -- uvx latticio

# Add NetBox MCP
claude mcp add --transport stdio netbox \
  --env NETBOX_URL=https://netbox.example.com/ \
  --env NETBOX_TOKEN=your-netbox-api-token \
  -- uv --directory /path/to/netbox-mcp-server run netbox-mcp-server
```

### Cursor

File: `.cursor/mcp.json`

```json
{
  "mcpServers": {
    "latticio": {
      "command": "uvx",
      "args": ["latticio"],
      "env": {
        "EOS_USERNAME": "admin",
        "EOS_PASSWORD": "your-eos-password",
        "EOS_INVENTORY_FILE": "/path/to/devices.yaml"
      }
    },
    "netbox": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/netbox-mcp-server",
        "run",
        "netbox-mcp-server"
      ],
      "env": {
        "NETBOX_URL": "https://netbox.example.com/",
        "NETBOX_TOKEN": "your-netbox-api-token"
      }
    }
  }
}
```

### VS Code

File: `.vscode/mcp.json`

```json
{
  "mcpServers": {
    "latticio": {
      "command": "uvx",
      "args": ["latticio"],
      "env": {
        "EOS_USERNAME": "admin",
        "EOS_PASSWORD": "your-eos-password"
      }
    },
    "netbox": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/netbox-mcp-server",
        "run",
        "netbox-mcp-server"
      ],
      "env": {
        "NETBOX_URL": "https://netbox.example.com/",
        "NETBOX_TOKEN": "your-netbox-api-token"
      }
    }
  }
}
```

## NetBox MCP Tools Reference

The NetBox MCP server (v1.0.0+) provides four read-only tools:

| Tool | Description |
|------|-------------|
| `netbox_get_objects` | Retrieve objects by type with filtering, pagination, and field selection |
| `netbox_get_object_by_id` | Get detailed information about a specific object by ID |
| `netbox_search_objects` | Search across multiple object types by name, IP, serial, or description |
| `netbox_get_changelogs` | Retrieve change history and audit trail |

Supported object types include: `devices`, `interfaces`, `ip-addresses`, `prefixes`, `sites`, `racks`, `cables`, `vlans`, `vrfs`, `circuits`, `virtual-machines`, and more across DCIM, IPAM, circuits, and virtualization modules.

## Example Prompts

Once both servers are configured, you can use natural language to compose tools from both:

1. **Inventory comparison**: *"Compare the device list in NetBox with the live inventory from network-mcp. Are there any devices in NetBox that aren't reachable via eAPI?"*

2. **Interface drift**: *"For spine-01, get the interfaces assigned in NetBox and compare them with the live interface status from the switch. Flag any mismatches in description, enabled state, or IP address."*

3. **BGP peering validation**: *"List all BGP sessions defined in NetBox for the DC1 site and verify each one is Established on the actual devices."*

4. **IP address audit**: *"Get all IP addresses assigned in NetBox for the 10.0.0.0/16 prefix and check if those IPs actually exist on the corresponding device interfaces."*

5. **Drift report generation**: *"Generate a full drift report between NetBox and live device state for all leaf switches. Export it as Markdown."*

6. **Serial number verification**: *"Compare the serial numbers recorded in NetBox against what the devices report via show version."*

7. **VLAN consistency check**: *"Get all VLANs defined in NetBox for site DC1 and verify they exist on each leaf switch in that site."*

## Workflow: Drift Detection

This walkthrough shows how an AI agent uses both servers to detect configuration drift between NetBox (intended state) and live devices (actual state).

### Step 1 — Get the device list from NetBox

The agent calls `netbox_get_objects` to retrieve all active devices:

```
netbox_get_objects(
  object_type="devices",
  filters={"site": "dc1", "status": "active"},
  fields=["id", "name", "device_type", "primary_ip4", "serial"]
)
```

Returns a list of devices NetBox considers active in DC1, e.g.:

```json
[
  {"id": 1, "name": "spine-01", "device_type": "7050X3", "primary_ip4": "10.0.0.1/32", "serial": "ABC123"},
  {"id": 2, "name": "leaf-01a", "device_type": "7280R3", "primary_ip4": "10.0.1.1/32", "serial": "DEF456"}
]
```

### Step 2 — Get live device info from network-mcp

For each device returned by NetBox, the agent calls `eos_get_device_info`:

```
eos_get_device_info(host="spine-01")
eos_get_device_info(host="leaf-01a")
```

Returns live data:

```json
{
  "status": "success",
  "device": "spine-01",
  "data": {
    "hostname": "spine-01",
    "model": "DCS-7050CX3-32S",
    "eos_version": "4.32.2F",
    "serial_number": "ABC123",
    "uptime_seconds": 8640000
  }
}
```

### Step 3 — Compare and identify drift

The agent compares the data from both sources:

| Field | NetBox (Intended) | Live Device (Actual) | Status |
|-------|-------------------|----------------------|--------|
| Model | 7050X3 | DCS-7050CX3-32S | OK (partial match) |
| Serial | ABC123 | ABC123 | OK |
| Primary IP | 10.0.0.1/32 | *(check via interfaces)* | Needs verification |

### Step 4 — Deep-dive into interfaces

For IP address verification, the agent calls both sides:

```
netbox_get_objects(
  object_type="interfaces",
  filters={"device": "spine-01"},
  fields=["id", "name", "type", "enabled", "description"]
)
```

```
eos_get_interfaces_status(host="spine-01", fields=["linkStatus", "description"])
```

The agent then cross-references interfaces defined in NetBox with those present on the live device, flagging:
- Interfaces in NetBox but missing on the device
- Interfaces on the device but not documented in NetBox
- Description mismatches
- Interfaces marked enabled in NetBox but admin-down on the device

### Step 5 — Export the drift report

The agent compiles its findings and calls `eos_export_report` to produce a formatted report:

```
eos_export_report(
  data={
    "summary": {"devices_checked": 2, "drifts_found": 3},
    "drifts": [
      {"device": "spine-01", "field": "Ethernet5 description", "netbox": "To leaf-02a", "actual": ""},
      {"device": "leaf-01a", "field": "Loopback0 IP", "netbox": "10.0.1.1/32", "actual": "10.0.1.100/32"},
      {"device": "leaf-01a", "field": "Ethernet49 enabled", "netbox": true, "actual": false}
    ]
  },
  output_format="markdown",
  title="DC1 Configuration Drift Report"
)
```

## Workflow: Topology Validation

Verify that physical cabling matches what NetBox documents by comparing LLDP neighbor data from live devices with cable records in NetBox.

### Step 1 — Get cable data from NetBox

```
netbox_get_objects(
  object_type="cables",
  filters={"site": "dc1"},
  fields=["id", "a_terminations", "b_terminations", "status", "type"]
)
```

This returns all documented cables, e.g.:
- Cable #1: spine-01 Ethernet1 <-> leaf-01a Ethernet49
- Cable #2: spine-01 Ethernet2 <-> leaf-01b Ethernet49

### Step 2 — Get LLDP neighbors from live devices

```
eos_get_lldp_neighbors(host="spine-01")
```

Returns actual LLDP adjacencies:

```json
{
  "status": "success",
  "device": "spine-01",
  "data": {
    "Ethernet1": [{"neighborDevice": "leaf-01a", "neighborPort": "Ethernet49"}],
    "Ethernet2": [{"neighborDevice": "leaf-01b", "neighborPort": "Ethernet49"}],
    "Ethernet3": [{"neighborDevice": "leaf-02a", "neighborPort": "Ethernet49"}]
  }
}
```

### Step 3 — Cross-reference and flag mismatches

The agent compares:

| Cable | NetBox A-Side | NetBox B-Side | LLDP Actual | Status |
|-------|---------------|---------------|-------------|--------|
| #1 | spine-01 Eth1 | leaf-01a Eth49 | leaf-01a Eth49 | OK |
| #2 | spine-01 Eth2 | leaf-01b Eth49 | leaf-01b Eth49 | OK |
| — | *(not in NetBox)* | — | spine-01 Eth3 <-> leaf-02a Eth49 | **UNDOCUMENTED** |

This identifies:
- Cables present in NetBox but not seen via LLDP (potentially disconnected)
- LLDP neighbors seen on the device but not documented in NetBox (undocumented cables)
- Port mismatches where a cable exists but connects to a different port than expected

## Workflow: Inventory Reconciliation

Ensure every device in NetBox is reachable and every reachable device is documented in NetBox.

### Step 1 — Get all devices from NetBox

```
netbox_get_objects(
  object_type="devices",
  filters={"status": "active", "role": "leaf"},
  fields=["id", "name", "primary_ip4", "serial", "status"]
)
```

### Step 2 — Get the network-mcp inventory

```
eos_list_inventory()
```

This returns all devices configured in the `devices.yaml` inventory file.

### Step 3 — Compare both inventories

| Device | In NetBox | In network-mcp | Reachable | Action Needed |
|--------|-----------|-------------------|-----------|---------------|
| spine-01 | Yes | Yes | Yes | None |
| leaf-01a | Yes | Yes | Yes | None |
| leaf-02a | Yes | No | — | Add to devices.yaml |
| lab-switch | No | Yes | Yes | Document in NetBox |

### Step 4 — Verify reachable devices match NetBox records

For each device present in both inventories, compare:

```
# From NetBox
netbox_get_object_by_id(object_type="devices", object_id=1)

# From live device
eos_get_device_info(host="spine-01")
```

Verify: hostname, model, serial number, management IP, and EOS version align between NetBox records and live device state.

## Tips and Best Practices

### Use field filtering to reduce token usage

Both servers support field filtering. Always request only the fields you need:

```
# NetBox: select only relevant fields
netbox_get_objects("devices", {"site": "dc1"}, fields=["name", "primary_ip4", "serial"])

# network-mcp: select only relevant fields
eos_get_interfaces_status(host="spine-01", fields=["linkStatus", "description"])
```

This reduces token consumption by up to 90%, which matters when comparing large inventories.

### Start with read-only operations

Both servers default to read-only mode. Keep it that way during drift detection — the goal is to *detect* drift, not fix it. Remediation should be a separate, deliberate step.

### Use `eos_export_report` to format results

After comparing data from both servers, use `eos_export_report` to produce a clean Markdown, CSV, or JSON report. This is useful for:
- Pasting into tickets or change requests
- Sharing with team members who don't have MCP access
- Creating audit documentation

### Use pagination for large datasets

When working with hundreds of devices or thousands of interfaces, use pagination on both sides:

```
# NetBox: built-in pagination via API
netbox_get_objects("interfaces", {"site": "dc1"}, fields=["name", "device"])

# network-mcp: limit/offset parameters
eos_get_interfaces_status(host="spine-01", limit=50, offset=0)
```

### Use demo mode for testing

You can test the integration workflow without live Arista switches by running network-mcp in demo mode:

```json
{
  "mcpServers": {
    "latticio": {
      "command": "uvx",
      "args": ["latticio"],
      "env": { "EOS_DEMO_MODE": "true" }
    },
    "netbox": {
      "command": "uv",
      "args": ["--directory", "/path/to/netbox-mcp-server", "run", "netbox-mcp-server"],
      "env": {
        "NETBOX_URL": "https://netbox.example.com/",
        "NETBOX_TOKEN": "your-api-token"
      }
    }
  }
}
```

### Structure prompts for multi-server workflows

When composing both servers, be explicit about the comparison you want:

> *"First, get all active devices from NetBox for site DC1. Then, for each device, call eos_get_device_info to get the live version and serial number. Compare the serial numbers and flag any mismatches. Export the results as a Markdown table."*

This gives the agent a clear sequence of operations rather than leaving it to guess the workflow.

## Troubleshooting

### Agent only uses one server

If the AI agent only calls tools from one server, verify both servers appear in your MCP client. In Claude Desktop, open **Settings > Developer > MCP Servers** and confirm both `network-mcp` and `netbox` show a green status. If a server shows an error, check the server logs for configuration issues.

### NetBox returns empty results

- Verify your `NETBOX_TOKEN` has read permissions on the relevant object types (DCIM, IPAM)
- Check that the filters match your data — `site` filter values are case-sensitive slugs in NetBox (e.g., `dc1` not `DC1`)
- Test the NetBox API directly: `curl -H "Authorization: Token <token>" https://netbox.example.com/api/dcim/devices/`

### Device names don't match between servers

NetBox device names and network-mcp inventory names must match for the agent to correlate them. If NetBox uses `dc1-spine-01` but your `devices.yaml` uses `spine-01`, the agent can't automatically link them. Options:
- Align naming conventions between NetBox and `devices.yaml`
- Tell the agent explicitly how to map names: *"In NetBox, devices are named dc1-spine-01. In network-mcp, the same device is just spine-01. Strip the dc1- prefix when correlating."*

### Timeout errors on large inventories

When checking many devices, you may hit timeouts. Strategies:
- Use field filtering on both servers to reduce response sizes
- Process devices in batches: *"Check the first 10 leaf switches, then the next 10"*
- Increase network-mcp timeouts: `TIMEOUT_SHOW=60` for slower devices

### SSL certificate errors

If NetBox uses a self-signed certificate, set `VERIFY_SSL=false` in the NetBox MCP server configuration. For network-mcp, switch to HTTP transport with `EOS_TRANSPORT=http` for lab environments.

## Further Reading

- [NetBox MCP Server documentation](https://netboxlabs.com/docs/mcp/)
- [NetBox MCP Server on GitHub](https://github.com/netboxlabs/netbox-mcp-server)
- [network-mcp tool reference](../TOOLS.md)
- [network-mcp resource reference](../RESOURCES.md)
- [MCP protocol specification](https://modelcontextprotocol.io)
