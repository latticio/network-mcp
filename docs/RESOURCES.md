# Resource Reference

> MCP Resources for network-mcp v4.0.0.

Resources provide read-only data endpoints that MCP clients can subscribe to for live data. They use URI templates where `{host}` is replaced with the device hostname, IP, or inventory name.

## Resource URIs

### Static Resources

| URI | Description |
|-----|-------------|
| `net://inventory` | List of all devices in the inventory (name, host, transport). |
| `net://devices` | Richer device list including roles, tags, groups, and MLAG peer info. |

### Per-Device Resources

| URI | Description |
|-----|-------------|
| `net://{host}/version` | Device version, model, serial number, and hardware info (JSON). |
| `net://{host}/running-config` | Full running configuration with credentials sanitized (text). |
| `net://{host}/running-config/{section}` | Specific config section (e.g., `router bgp`, `interface`, `vlan`) with credentials sanitized (text). |
| `net://{host}/health` | Quick health summary: hostname, model, OS version, uptime, memory %, interface up/down counts (JSON). |
| `net://{host}/interfaces/summary` | Interface summary: total count, connected/not-connected counts, interfaces with non-zero error counters (JSON). |
| `net://{host}/interfaces/{interface}` | Single interface deep-dive: full status, counters, and configuration (JSON). |
| `net://{host}/bgp/summary` | BGP peer summary: router ID, local ASN, all peers with state and prefix counts (JSON). |
| `net://{host}/vlans` | VLAN table: all VLANs with names and assigned interfaces (JSON). |

## Usage Examples

### Claude Desktop / Claude Code

Resources are automatically available when connected. The AI assistant can read them directly:

```
"Show me the running config of spine-01"
→ Reads net://spine-01/running-config

"What's the health of core-rtr-01?"
→ Reads net://core-rtr-01/health

"Show BGP config section on spine-01"
→ Reads net://spine-01/running-config/router bgp

"List all devices in the inventory"
→ Reads net://devices
```

### MCP Inspector

Use MCP Inspector to browse and test resources:

```bash
# List all resources
npx @modelcontextprotocol/inspector

# Navigate to Resources tab and browse available URIs
```

### Programmatic Access

```python
# Using MCP SDK
async with ClientSession(transport) as session:
    # Read device health (works with any vendor in inventory)
    health = await session.read_resource("net://spine-01/health")

    # Read specific config section
    bgp_config = await session.read_resource("net://core-rtr-01/running-config/router bgp")

    # List all devices
    devices = await session.read_resource("net://devices")
```

## Multi-Vendor Notes

Resources work with any device in the inventory regardless of platform. The `{host}` parameter resolves to the device's inventory name, hostname, or IP address. The server auto-selects the correct driver based on the device's `platform` field.

Examples with different vendors:

| Device | Platform | Example URI |
|--------|----------|-------------|
| `spine-01` | Arista EOS | `net://spine-01/health` |
| `core-rtr-01` | Cisco IOS-XE | `net://core-rtr-01/version` |
| `dc-core-01` | Cisco NX-OS | `net://dc-core-01/interfaces/summary` |
| `edge-rtr-01` | Juniper JunOS | `net://edge-rtr-01/running-config` |

## Security Notes

- All resources are **read-only** — they cannot modify device configuration.
- Running configuration output is automatically **sanitized** — passwords, secrets, and community strings are redacted.
- Resources respect the same rate limiting and authentication as tools.
- The `net://inventory` and `net://devices` resources expose device hostnames and IPs — ensure your MCP transport is secured.
