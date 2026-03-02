# ServiceNow + network-mcp Integration Guide

Use a ServiceNow MCP server alongside network-mcp to give AI agents simultaneous access to ServiceNow ITSM (incidents, changes, CMDB) and live Arista EOS device state. This enables automated incident workflows — the agent can diagnose network issues, create or update ServiceNow incidents with device diagnostics, and validate changes against live device state without manual context-switching.

Because both servers implement the Model Context Protocol, any MCP-compatible client (Claude Desktop, Claude Code, Cursor, VS Code Copilot) can compose tools from both servers in a single conversation — no custom glue code required.

## Architecture

```
                          AI Agent
                     (Claude Desktop, Cursor, etc.)
                        /                    \
                       /                      \
           ServiceNow MCP Server         network-mcp Server
           (Incidents & Changes)         (Live Device State)
                    |                          |
                    v                          v
           ServiceNow Instance           Arista EOS Switches
           (ITSM / CMDB)                 (via eAPI / gNMI)
                        \                      /
                         \                    /
                          v                  v
                     Agent automates ITSM workflows
                     with live device diagnostics
                              |
                              v
                     Incident Created / Updated
                     Change Record Validated
```

The agent calls ServiceNow MCP tools to search incidents, create tickets, and add work notes, then calls network-mcp tools to run health checks, diagnose issues, and capture device state. It combines diagnostics with ticket management to automate the incident lifecycle.

## Prerequisites

- **ServiceNow MCP Server** installed — several options are available:
  - [servicenow-mcp](https://github.com/michaelbuckner/servicenow-mcp) by Michael Buckner (pip-installable)
  - [servicenow-mcp](https://github.com/echelon-ai-labs/servicenow-mcp) by Echelon AI Labs
  - ServiceNow's native MCP support (Zurich Patch 4+)
- **network-mcp** installed (`uvx latticio` or `pip install latticio`)
- A **ServiceNow instance** with incident and change management modules
- **Arista EOS devices** accessible via eAPI (or use demo mode for testing)
- **ServiceNow credentials** (basic auth, OAuth, or API key) with permissions to read/write incidents and change requests

## Client Configuration

The examples below use the `servicenow-mcp` package by Michael Buckner. Adjust the command and arguments for your chosen ServiceNow MCP server.

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
    "servicenow": {
      "command": "python",
      "args": [
        "-m", "mcp_server_servicenow.cli",
        "--url", "https://your-instance.service-now.com/",
        "--username", "your-username",
        "--password", "your-password"
      ]
    }
  }
}
```

### Claude Code

```bash
# Add network-mcp
claude mcp add network-mcp -- uvx latticio

# Add ServiceNow MCP
claude mcp add servicenow -- \
  python -m mcp_server_servicenow.cli \
  --url https://your-instance.service-now.com/ \
  --username your-username \
  --password your-password
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
    "servicenow": {
      "command": "python",
      "args": [
        "-m", "mcp_server_servicenow.cli",
        "--url", "https://your-instance.service-now.com/",
        "--username", "your-username",
        "--password", "your-password"
      ]
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
    "servicenow": {
      "command": "python",
      "args": [
        "-m", "mcp_server_servicenow.cli",
        "--url", "https://your-instance.service-now.com/",
        "--username", "your-username",
        "--password", "your-password"
      ]
    }
  }
}
```

## ServiceNow MCP Tools Reference

The ServiceNow MCP server ([servicenow-mcp](https://github.com/michaelbuckner/servicenow-mcp)) provides tools for incident management and record operations:

| Tool | Description |
|------|-------------|
| `create_incident` | Create a new incident record with summary, description, and priority |
| `update_incident` | Modify existing incident fields (state, assignment, priority) |
| `search_records` | Query ServiceNow records using text parameters |
| `get_record` | Retrieve a specific record by sys_id |
| `perform_query` | Execute ServiceNow encoded queries |
| `add_comment` | Post a customer-visible comment on a record |
| `add_work_notes` | Add internal work notes to an incident |
| `natural_language_search` | Search records using natural language queries |

Additionally, the server exposes resources for read-only access:

| Resource | Description |
|----------|-------------|
| `servicenow://incidents` | List recent incidents |
| `servicenow://incidents/{number}` | Get a specific incident by number |
| `servicenow://tables/{table}` | Get records from any ServiceNow table |
| `servicenow://schema/{table}` | Retrieve table schema definitions |

The exact tools available depend on which ServiceNow MCP server implementation you use. Some implementations may include additional capabilities like change request management or CMDB queries.

## Example Prompts

Once both servers are configured, you can use natural language to compose tools from both:

1. **Health check with incident creation**: *"Run `eos_get_device_health` on spine-01. If the health check shows any critical issues, create a ServiceNow incident with the diagnostic details, assign it to the Network Operations team, and set priority based on severity."*

2. **Pre-change validation**: *"Before applying change CHG0012345, run `eos_pre_change_snapshot` on all affected devices (spine-01, leaf-01a, leaf-01b). Record the snapshot IDs in the change request work notes."*

3. **Incident diagnosis**: *"Look up incident INC0054321 in ServiceNow. It says BGP is flapping on leaf-02a. Run `eos_diagnose_bgp_session` for each BGP peer on that device and add the findings as work notes on the incident."*

4. **Change validation report**: *"For change CHG0012345, run `eos_post_change_validation` on spine-01 using the pre-change snapshot. Generate a report with `eos_export_report` and add it to the change record as a work note."*

5. **Proactive health sweep with tickets**: *"Run `eos_validate_fabric_health` across all devices in the 'core' group. For any device with critical findings, check if a ServiceNow incident already exists. If not, create one with the diagnostic details."*

6. **Incident enrichment**: *"Find all open P1 incidents in ServiceNow related to network devices. For each incident, identify the affected device and run `eos_get_device_health` to add current device state to the incident work notes."*

## Workflow: Incident Auto-Creation

This walkthrough shows how an AI agent detects issues during a routine health check and automatically creates ServiceNow incidents with diagnostic context.

### Step 1 — Run a fabric-wide health check

The agent runs a health check across all inventory devices:

```
eos_validate_fabric_health(role="all")
```

Returns a structured result with findings:

```json
{
  "status": "partial",
  "workflow": "validate_fabric_health",
  "diagnosis": {
    "severity": "critical",
    "summary": "Fabric health score: 85% (17/20 checks passed) across 8 device(s). Found 3 issue(s).",
    "findings": [
      {
        "component": "leaf-02a/bgp_peer_10.0.0.1",
        "status": "Active",
        "expected": "Established",
        "detail": "BGP peer 10.0.0.1 (AS 65001) on leaf-02a is Active."
      },
      {
        "component": "leaf-02a/bgp_peer_10.0.0.2",
        "status": "Active",
        "expected": "Established",
        "detail": "BGP peer 10.0.0.2 (AS 65001) on leaf-02a is Active."
      },
      {
        "component": "leaf-02b/mlag",
        "status": "config-sanity: inconsistent",
        "expected": "consistent",
        "detail": "MLAG config-sanity on leaf-02b is inconsistent."
      }
    ]
  }
}
```

### Step 2 — Check for existing incidents

Before creating duplicates, the agent searches ServiceNow:

```
search_records(query="short_description CONTAINS leaf-02a AND state != 7")
```

State 7 is "Closed" in ServiceNow. If no open incident exists, proceed to creation.

### Step 3 — Run deeper diagnostics on affected devices

```
eos_diagnose_bgp_session(host="leaf-02a", neighbor_ip="10.0.0.1")
eos_get_mlag_config_sanity(host="leaf-02b")
```

These provide detailed root cause information to include in the ticket.

### Step 4 — Create the incident with diagnostics

```
create_incident(
  short_description="BGP peers down on leaf-02a — both uplinks to spine layer in Active state",
  description="Automated detection by AI agent via network-mcp fabric health check.\n\n"
    "Affected device: leaf-02a\n"
    "Issue: BGP peers 10.0.0.1 and 10.0.0.2 (AS 65001) are in Active state, expected Established.\n"
    "Root cause analysis: TCP connection attempts failing. No route to peers in routing table.\n"
    "Possible causes: Uplink interface down, ACL blocking TCP/179, or remote peers not configured.\n\n"
    "Recommended actions:\n"
    "1. Check uplink interfaces on leaf-02a (Ethernet49, Ethernet50)\n"
    "2. Verify route to spine loopbacks\n"
    "3. Check ACLs for TCP/179 blocks",
  priority="2",
  assignment_group="Network Operations"
)
```

### Step 5 — Add device diagnostic data as work notes

```
add_work_notes(
  incident_number="INC0054322",
  notes="=== BGP Diagnostic Output ===\n"
    "Peer 10.0.0.1: Active (TCP failing)\n"
    "Peer 10.0.0.2: Active (TCP failing)\n"
    "Route to 10.0.0.1: NOT FOUND\n"
    "Route to 10.0.0.2: NOT FOUND\n"
    "Interface Ethernet49: adminDown\n"
    "Interface Ethernet50: adminDown\n\n"
    "=== Conclusion ===\n"
    "Both uplink interfaces are administratively shut down, "
    "causing loss of routes to spine peers."
)
```

## Workflow: Change Management Validation

Automate pre-change and post-change validation with results recorded in ServiceNow change records.

### Step 1 — Look up the change request

```
search_records(query="number=CHG0012345")
```

Returns the change record with details about what's being changed and which devices are affected.

### Step 2 — Capture pre-change snapshots

For each affected device listed in the change request:

```
eos_pre_change_snapshot(host="spine-01")
eos_pre_change_snapshot(host="leaf-01a")
eos_pre_change_snapshot(host="leaf-01b")
```

Each call returns a `snapshot_id` that the agent records.

### Step 3 — Record snapshot IDs in the change

```
add_work_notes(
  incident_number="CHG0012345",
  notes="=== Pre-Change Snapshots Captured ===\n"
    "spine-01: snapshot_id=a1b2c3d4-...\n"
    "leaf-01a: snapshot_id=e5f6g7h8-...\n"
    "leaf-01b: snapshot_id=i9j0k1l2-...\n\n"
    "Components captured: version, bgp_summary, interfaces_status, mlag, route_summary, vlans\n"
    "Snapshots expire in 3600 seconds."
)
```

### Step 4 — Apply the change (manual step)

The network engineer applies the change. The AI agent waits for confirmation.

### Step 5 — Run post-change validation

```
eos_post_change_validation(host="spine-01", snapshot_id="a1b2c3d4-...")
eos_post_change_validation(host="leaf-01a", snapshot_id="e5f6g7h8-...")
eos_post_change_validation(host="leaf-01b", snapshot_id="i9j0k1l2-...")
```

Returns a comparison showing regressions, changes, and improvements:

```json
{
  "status": "success",
  "workflow": "post_change_validation",
  "diagnosis": {
    "severity": "healthy",
    "summary": "Post-change validation on spine-01: 2 change(s), 1 improvement(s).",
    "findings": [
      {"component": "bgp_peer_10.0.1.3", "status": "IMPROVED: Established"},
      {"component": "interface_Ethernet5", "status": "CHANGED: connected"}
    ]
  }
}
```

### Step 6 — Record results and close the change

```
add_work_notes(
  incident_number="CHG0012345",
  notes="=== Post-Change Validation Results ===\n\n"
    "spine-01: PASS — 0 regressions, 2 expected changes, 1 improvement\n"
    "leaf-01a: PASS — 0 regressions, 1 expected change\n"
    "leaf-01b: PASS — 0 regressions, 0 changes\n\n"
    "All devices validated successfully. No regressions detected.\n"
    "Change can be closed as successful."
)
```

## Workflow: CMDB Sync Concept

Use network-mcp inventory and device data to validate ServiceNow CMDB entries. This workflow requires CMDB access via the ServiceNow MCP server (table queries on `cmdb_ci_ip_switch` or similar).

### Step 1 — Get the network-mcp inventory

```
eos_list_inventory()
```

Returns all devices in the inventory with their hostnames, roles, and management IPs.

### Step 2 — Get live device details

For each inventory device:

```
eos_get_device_info(host="spine-01")
```

Returns model, serial number, EOS version, and uptime.

### Step 3 — Query the ServiceNow CMDB

```
perform_query(
  table="cmdb_ci_ip_switch",
  query="name=spine-01"
)
```

Returns the CMDB record for the device, including fields like serial number, model, firmware version, and IP address.

### Step 4 — Compare and flag discrepancies

| Field | CMDB (ServiceNow) | Live Device (network-mcp) | Status |
|-------|-------------------|------------------------------|--------|
| Model | DCS-7050CX3-32S | DCS-7050CX3-32S | OK |
| Serial | ABC123 | ABC123 | OK |
| Firmware | 4.31.1F | 4.32.2F | **MISMATCH** |
| IP Address | 10.0.0.1 | 10.0.0.1 | OK |

The firmware version was upgraded but the CMDB was not updated. The agent can flag this for manual update or, if the ServiceNow MCP server supports writes, update the CMDB record directly.

### Step 5 — Report findings

```
eos_export_report(
  data={
    "summary": {"devices_checked": 8, "cmdb_mismatches": 3},
    "mismatches": [
      {"device": "spine-01", "field": "firmware", "cmdb": "4.31.1F", "actual": "4.32.2F"},
      {"device": "leaf-03a", "field": "serial", "cmdb": "OLD123", "actual": "NEW456"},
      {"device": "leaf-04b", "field": "model", "cmdb": "7280R", "actual": "DCS-7280R3-48C6"}
    ]
  },
  output_format="markdown",
  title="CMDB Sync Report"
)
```

## Tips and Best Practices

### Use read-only mode during incident investigation

Keep network-mcp in its default read-only mode (`EOS_READ_ONLY=true`) when investigating incidents. The goal during triage is to gather data, not make changes. Write operations should only happen during approved change windows.

### Avoid duplicate incident creation

Before creating a new incident, always search ServiceNow for existing open incidents on the same device or component. A simple query pattern:

> *"Search ServiceNow for open incidents mentioning 'spine-01'. If one already exists, add the new diagnostic data as a work note instead of creating a duplicate."*

### Include structured data in work notes

When adding diagnostics to ServiceNow tickets, format the data clearly:

```
=== Device Health Check: spine-01 ===
Timestamp: 2025-03-15T14:30:00Z
EOS Version: 4.32.2F
Uptime: 45 days
CPU: 23% used
Memory: 4.2 GB / 8.0 GB (52%)
BGP Peers: 4/4 Established
MLAG: Active, config-sanity consistent

=== Findings ===
No issues detected. Device is healthy.
```

### Use pre/post-change snapshots for every change

Make it a standard practice to capture pre-change snapshots and run post-change validation for every change request:

1. Before the change: `eos_pre_change_snapshot` on all affected devices
2. Record snapshot IDs in the change record
3. After the change: `eos_post_change_validation` to compare
4. Record validation results in the change record

This creates an audit trail and catches regressions immediately.

### Map ServiceNow CI names to inventory hostnames

ServiceNow CMDB configuration items (CIs) may use naming conventions that differ from your `devices.yaml` inventory. Establish a consistent mapping:

- If ServiceNow uses FQDNs (e.g., `spine-01.dc1.example.com`), your inventory should match or you should instruct the agent how to map them
- Consider adding a custom field in ServiceNow CIs that matches the network-mcp inventory hostname

### Use `eos_export_report` for ticket attachments

Generate structured reports to include in ServiceNow tickets:

```
eos_export_report(
  data={...diagnostic results...},
  output_format="markdown",
  title="Incident Diagnostic Report"
)
```

The Markdown output is readable directly in ServiceNow work notes. CSV format is useful for tabular data that can be attached as a file.

### Use demo mode for testing

Test the integration workflow without live devices or a production ServiceNow instance:

```json
{
  "mcpServers": {
    "latticio": {
      "command": "uvx",
      "args": ["latticio"],
      "env": { "EOS_DEMO_MODE": "true" }
    },
    "servicenow": {
      "command": "python",
      "args": [
        "-m", "mcp_server_servicenow.cli",
        "--url", "https://your-dev-instance.service-now.com/",
        "--username", "dev-user",
        "--password", "dev-password"
      ]
    }
  }
}
```

ServiceNow developer instances are available free at [developer.servicenow.com](https://developer.servicenow.com).

## Troubleshooting

### Agent only uses one server

If the AI agent only calls tools from one server, verify both servers appear in your MCP client. In Claude Desktop, open **Settings > Developer > MCP Servers** and confirm both `network-mcp` and `servicenow` show a green status. If a server shows an error, check the server logs for configuration issues.

### ServiceNow authentication failures

- Verify your credentials are correct and the account is not locked
- For basic auth, ensure the account has the `rest_api_explorer` role or equivalent
- For OAuth, ensure the OAuth application is registered and tokens are valid
- Test the credentials directly: `curl -u "user:pass" https://your-instance.service-now.com/api/now/table/incident?sysparm_limit=1`

### Incident creation fails with permission errors

- Ensure the ServiceNow account has `itil` role for incident management
- For change management operations, the `itil_admin` or `change_manager` role may be needed
- Check ACLs on the incident table if custom security rules are in place

### Device names don't match between systems

ServiceNow CMDB entries and network-mcp inventory names must match for the agent to correlate them. If naming conventions differ, tell the agent explicitly: *"In ServiceNow, the device is listed as 'DC1-SPINE-01'. In network-mcp, the same device is 'spine-01'. Use lowercase and strip the DC1- prefix when correlating."*

### Timeout errors on large operations

When running health checks across many devices and creating multiple tickets, you may hit timeouts. Strategies:
- Process devices in batches: *"Check the first 10 leaf switches, create incidents for any issues, then check the next 10"*
- Use field filtering on network-mcp tools to reduce response sizes
- Increase network-mcp timeouts: `TIMEOUT_SHOW=60` for slower devices

## Further Reading

- [ServiceNow MCP Server (michaelbuckner)](https://github.com/michaelbuckner/servicenow-mcp)
- [ServiceNow MCP Server (echelon-ai-labs)](https://github.com/echelon-ai-labs/servicenow-mcp)
- [ServiceNow Developer Portal](https://developer.servicenow.com)
- [ServiceNow MCP and A2A documentation](https://www.servicenow.com/community/now-assist-articles/enable-mcp-and-a2a-for-your-agentic-workflows-with-faqs-updated/ta-p/3373907)
- [network-mcp tool reference](../TOOLS.md)
- [network-mcp resource reference](../RESOURCES.md)
- [MCP protocol specification](https://modelcontextprotocol.io)
