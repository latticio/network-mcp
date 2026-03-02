# Troubleshooting Guide

> Decision-tree debugging guide for network-mcp v4.0.0.

## Quick Diagnosis

### Can't connect to device

```
Connection error
├── Is the device reachable?
│   ├── No → Check network connectivity (ping, route)
│   └── Yes ↓
├── Which vendor/platform?
│   ├── Arista EOS (eAPI) ↓
│   │   ├── Is eAPI enabled on the device?
│   │   │   ├── No → Enable it:
│   │   │   │         management api http-commands
│   │   │   │           protocol https
│   │   │   │           no shutdown
│   │   │   └── Yes ↓
│   │   └── Is the transport correct?
│   │       ├── HTTPS cert issues → Set NET_TRANSPORT=https (default)
│   │       │   or check self-signed cert handling
│   │       └── HTTP not enabled → Either enable HTTP on device
│   │           or switch to HTTPS transport
│   │
│   ├── Cisco IOS-XE (RESTCONF) ↓
│   │   ├── Is RESTCONF enabled?
│   │   │   ├── No → Enable it:
│   │   │   │         restconf
│   │   │   │         ip http server
│   │   │   │         ip http secure-server
│   │   │   └── Yes ↓
│   │   ├── Is NET_VERIFY_SSL=true causing cert issues?
│   │   │   └── For self-signed certs, set NET_VERIFY_SSL=false
│   │   └── Is the RESTCONF port correct? (default: 443)
│   │
│   ├── Cisco NX-OS (NX-API) ↓
│   │   ├── Is NX-API enabled?
│   │   │   ├── No → Enable it:
│   │   │   │         feature nxapi
│   │   │   └── Yes ↓
│   │   ├── Is NX-API using HTTPS?
│   │   │   └── Verify: show nxapi
│   │   └── Is NET_VERIFY_SSL=true causing cert issues?
│   │       └── For self-signed certs, set NET_VERIFY_SSL=false
│   │
│   └── Juniper JunOS (NETCONF) ↓
│       ├── Is NETCONF enabled?
│       │   ├── No → Enable it:
│       │   │         set system services netconf ssh
│       │   └── Yes ↓
│       ├── Is SSH accessible on port 830?
│       │   └── Verify: show system services netconf
│       └── Are SSH keys or credentials configured?
│
├── Are credentials correct?
│   ├── No → Check NET_USERNAME / NET_PASSWORD env vars
│   │         or device entry in devices.yaml
│   └── Yes ↓
└── Is the device in the inventory?
    ├── Using direct host → Verify NET_HOST is set correctly
    └── Using inventory → Verify NET_INVENTORY_FILE path
        and device entry exists in devices.yaml
        with the correct platform field (eos, iosxe, nxos, junos)
```

### Tool returns error

```
Tool error
├── "Read-only mode: write operations are disabled"
│   └── Set NET_READ_ONLY=false to enable write operations
│
├── "Command denied: ..."
│   └── The command is on the safety deny-list
│       (reload, write erase, zerotouch, bash, etc.)
│       These commands are always blocked for safety
│
├── "Rate limited"
│   └── Too many commands sent too quickly
│       Wait a moment and retry, or adjust:
│       RATE_LIMIT_SHOW (default: 5.0 cmds/sec)
│       RATE_LIMIT_CONFIG (default: 1.0 cmds/sec)
│
├── "Circuit breaker open for ..."
│   └── Device has had too many consecutive failures
│       The circuit breaker will auto-reset after the
│       cooldown period. Check device connectivity.
│       Use net_server_health() to see circuit breaker states
│
├── "Invalid host: ..."
│   └── Host parameter contains invalid characters
│       Use alphanumeric, dots, hyphens, or underscores only
│
├── "Connection to X failed: ..."
│   └── See "Can't connect to device" above
│
├── "Unsupported platform: ..."
│   └── Device inventory entry has an unrecognized platform
│       Supported platforms: eos, iosxe, nxos, junos
│       Check the platform field in devices.yaml
│
├── "Driver not available: ..."
│   └── The driver for this platform is not installed
│       Arista EOS: pip install pyeapi
│       Cisco IOS-XE/NX-OS: pip install httpx
│       Juniper JunOS: pip install scrapli-netconf
│
├── "Configuration command error: ..."
│   └── The CLI command syntax is invalid or unsupported
│       on this device's software version. Check command
│       against the device OS version
│
└── "Output size limit exceeded"
    └── Response was too large for the MCP context window
        Try filtering: use specific sections, VLANs, or
        interfaces rather than fetching all data
```

### Demo mode not working

```
Demo mode issues
├── Server won't start in demo mode
│   ├── Is NET_DEMO_MODE=true set?
│   │   └── Set it: export NET_DEMO_MODE=true
│   └── Check for import errors in logs
│       └── Run: uv run network-mcp 2>/tmp/mcp.log
│           then check /tmp/mcp.log for errors
│
├── Tools return unexpected data
│   └── Demo mode returns realistic mock data
│       It is pre-configured and not customizable
│       This is expected behavior
│
├── Write tools fail in demo mode
│   └── NET_READ_ONLY still applies in demo mode
│       Set NET_READ_ONLY=false to test write tools
│
└── Not all tools return data
    └── Some optional modules may need their
        dependencies installed even in demo mode:
        - pygnmi for gNMI tools
        - anta for validation tools
```

### MCP client not connecting

```
Client connection issues
├── Claude Desktop
│   ├── Check claude_desktop_config.json syntax
│   │   (must be valid JSON)
│   ├── Verify the command path is correct:
│   │   "command": "uv",
│   │   "args": ["run", "--directory", "/path/to/network-mcp", "network-mcp"]
│   └── Restart Claude Desktop after config changes
│
├── Claude Code
│   └── Run: claude mcp add network-mcp \
│         "uv run --directory /path/to/network-mcp network-mcp"
│
├── Cursor
│   ├── Add to .cursor/mcp.json in your project root
│   └── Restart Cursor after config changes
│
├── VS Code Copilot
│   └── Add to .vscode/mcp.json:
│       { "servers": { "latticio": { ... } } }
│
└── HTTP Transport
    ├── Start server: uv run network-mcp --transport streamable-http
    ├── Connect client to: http://localhost:8000/mcp
    └── For remote access, configure appropriate auth
```

## Vendor-Specific Issues

### Arista EOS (eAPI)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ConnectionError: unable to connect to eAPI` | eAPI not enabled or wrong transport | Enable eAPI: `management api http-commands` / `no shutdown` |
| `CommandError: invalid command` | EOS version doesn't support the command | Check `show version` for software version compatibility |
| SSL certificate errors | Self-signed certs on device | Set `NET_TRANSPORT=http` for lab, or install device cert in trust store |
| `Authorization denied` | User lacks privilege level | Ensure user has privilege 15 or appropriate role |

### Cisco IOS-XE (RESTCONF)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Connection refused on port 443` | RESTCONF not enabled | Enable: `restconf` + `ip http secure-server` |
| `401 Unauthorized` | Wrong credentials or auth method | Verify NET_USERNAME/NET_PASSWORD; check `aaa` config |
| SSL errors with self-signed certs | TLS verification failing | Set `NET_VERIFY_SSL=false` for lab environments |
| `404 Not Found` on RESTCONF paths | IOS-XE version too old | RESTCONF requires IOS-XE 16.6+ |
| Slow responses | Large config or routing table | Increase `TIMEOUT_SHOW` to 60+ seconds |

### Cisco NX-OS (NX-API)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Connection refused` | NX-API not enabled | Enable: `feature nxapi` |
| `401 Unauthorized` | Wrong credentials | Verify NET_USERNAME/NET_PASSWORD |
| `403 Forbidden` | User lacks NX-API access | Check `show nxapi` and user role assignments |
| SSL errors | Self-signed certs | Set `NET_VERIFY_SSL=false` for lab environments |
| JSON parse errors | NX-API returning CLI text | Ensure NX-API is configured for JSON output format |

### Juniper JunOS (NETCONF)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Connection refused on port 830` | NETCONF not enabled | Enable: `set system services netconf ssh` |
| `Authentication failed` | Wrong credentials or SSH config | Verify credentials; check SSH key setup |
| `RPC error` | JunOS version incompatibility | Check `show version` for minimum NETCONF support |
| Timeout on large configs | Config retrieval too slow | Increase `TIMEOUT_SHOW`; use config section filters |
| `Permission denied` | User class lacks permissions | Assign appropriate login class with NETCONF permissions |

## Common Scenarios

### BGP neighbor is down

1. Quick diagnosis: `eos_diagnose_bgp_session(host, neighbor_ip)` (EOS) or `net_diagnose_bgp_session(host, neighbor_ip)` (any vendor)
2. This runs multiple commands and returns severity + findings + recommended actions
3. Common causes:
   - Interface to peer is down
   - AS number mismatch
   - ACL blocking TCP/179
   - Peer not configured

### Interface has errors

1. Quick diagnosis: `eos_troubleshoot_interface_issues(host, interface)` (EOS) or `net_troubleshoot_interface(host, interface)` (any vendor)
2. Checks: link state, error counters, transceiver power, status
3. Common causes:
   - Bad cable or dirty fiber connectors
   - Speed/duplex mismatch
   - Faulty transceiver

### Making a safe configuration change

1. Capture baseline: `eos_pre_change_snapshot(host)`
2. Create session: `eos_create_config_session(host)`
3. Stage commands: `eos_add_to_config_session(host, session, commands)`
4. Review diff: `eos_get_config_session_diff(host, session)`
5. Apply with timer: `eos_commit_config_session_timer(host, session)`
6. Verify: `eos_post_change_validation(host, snapshot_id)`
7. Confirm or let timer rollback: `eos_confirm_commit_timer(host, session)`

### Fabric-wide health check

1. Quick score: `eos_validate_fabric_health(role='all')` or `net_validate_fabric_health(role='all')`
2. Returns: health score, BGP peer issues, MLAG issues per device
3. For specific roles: `eos_validate_fabric_health(role='spine')`

## Environment Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `NET_DEMO_MODE` | `false` | Enable demo mode with mock data (no real devices needed) |
| `NET_HOST` | `localhost` | Default device host (single-device mode) |
| `NET_USERNAME` | `admin` | Default device username |
| `NET_PASSWORD` | *(empty)* | Default device password |
| `NET_TRANSPORT` | `https` | Default connection transport (http, https) |
| `NET_VERIFY_SSL` | `true` | Verify TLS certificates for RESTCONF/NX-API/eAPI |
| `NET_INVENTORY_FILE` | *(none)* | Path to devices.yaml for multi-device |
| `NET_READ_ONLY` | `true` | Block all write operations |
| `NET_DISABLED_MODULES` | *(none)* | Comma-separated modules to disable |
| `RATE_LIMIT_SHOW` | `5.0` | Show commands/sec per device |
| `RATE_LIMIT_CONFIG` | `1.0` | Config commands/sec per device |
| `NET_CACHE_TTL` | `30` | Command cache TTL in seconds |
| `NET_SNAPSHOT_TTL` | `3600` | Pre-change snapshot TTL in seconds |
| `NET_SNAPSHOT_DIR` | *(none)* | Directory for persistent snapshot storage |
| `LOG_LEVEL` | `INFO` | Logging level |

## Getting Help

- GitHub Issues: <https://github.com/latticio/network-mcp/issues>
- See [TOOLS.md](TOOLS.md) for complete tool reference
- See [RESOURCES.md](RESOURCES.md) for MCP resource URIs
- See [PROMPTS.md](PROMPTS.md) for workflow prompt templates
