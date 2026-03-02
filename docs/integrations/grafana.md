# Grafana + network-mcp Integration Guide

Use the Grafana MCP server alongside network-mcp to give AI agents simultaneous access to Grafana metrics, alerts, and dashboards alongside live Arista EOS device state. This enables alert-driven diagnostics — when Grafana detects a threshold breach, the agent can immediately pull live device data to diagnose the root cause without manual CLI work.

Because both servers implement the Model Context Protocol, any MCP-compatible client (Claude Desktop, Claude Code, Cursor, VS Code Copilot) can compose tools from both servers in a single conversation — no custom glue code required.

## Architecture

```
                          AI Agent
                     (Claude Desktop, Cursor, etc.)
                        /                    \
                       /                      \
             Grafana MCP Server          network-mcp Server
             (Metrics & Alerts)          (Live Device State)
                    |                          |
                    v                          v
             Grafana Instance            Arista EOS Switches
             (Prometheus, Loki, etc.)     (via eAPI / gNMI)
                        \                      /
                         \                    /
                          v                  v
                     Agent correlates metrics
                     with device diagnostics
                              |
                              v
                     Root Cause Analysis Report
                     (Markdown / CSV / JSON)
```

The agent calls Grafana MCP tools to retrieve metric values, active alerts, and dashboard data, then calls network-mcp tools to retrieve live device state (CPU, memory, interface counters, BGP status). It reasons about the correlation between metric anomalies and device behavior to produce actionable diagnostics.

## Prerequisites

- **Grafana MCP Server** installed ([grafana/mcp-grafana](https://github.com/grafana/mcp-grafana))
- **network-mcp** installed (`uvx network-mcp` or `pip install network-mcp`)
- A **Grafana instance** (v9.0+) with datasources configured (Prometheus, Loki, etc.)
- **Arista EOS devices** accessible via eAPI (or use demo mode for testing)
- A **Grafana service account token** with at least Viewer permissions

## Client Configuration

### Claude Desktop

File: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)

```json
{
  "mcpServers": {
    "network-mcp": {
      "command": "uvx",
      "args": ["network-mcp"],
      "env": {
        "EOS_USERNAME": "admin",
        "EOS_PASSWORD": "your-eos-password",
        "EOS_INVENTORY_FILE": "/path/to/devices.yaml"
      }
    },
    "grafana": {
      "command": "uvx",
      "args": ["mcp-grafana"],
      "env": {
        "GRAFANA_URL": "https://grafana.example.com",
        "GRAFANA_SERVICE_ACCOUNT_TOKEN": "your-grafana-token"
      }
    }
  }
}
```

### Claude Code

```bash
# Add network-mcp
claude mcp add network-mcp -- uvx network-mcp

# Add Grafana MCP
claude mcp add grafana \
  --env GRAFANA_URL=https://grafana.example.com \
  --env GRAFANA_SERVICE_ACCOUNT_TOKEN=your-grafana-token \
  -- uvx mcp-grafana
```

### Cursor

File: `.cursor/mcp.json`

```json
{
  "mcpServers": {
    "network-mcp": {
      "command": "uvx",
      "args": ["network-mcp"],
      "env": {
        "EOS_USERNAME": "admin",
        "EOS_PASSWORD": "your-eos-password",
        "EOS_INVENTORY_FILE": "/path/to/devices.yaml"
      }
    },
    "grafana": {
      "command": "uvx",
      "args": ["mcp-grafana"],
      "env": {
        "GRAFANA_URL": "https://grafana.example.com",
        "GRAFANA_SERVICE_ACCOUNT_TOKEN": "your-grafana-token"
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
    "network-mcp": {
      "command": "uvx",
      "args": ["network-mcp"],
      "env": {
        "EOS_USERNAME": "admin",
        "EOS_PASSWORD": "your-eos-password"
      }
    },
    "grafana": {
      "command": "uvx",
      "args": ["mcp-grafana"],
      "env": {
        "GRAFANA_URL": "https://grafana.example.com",
        "GRAFANA_SERVICE_ACCOUNT_TOKEN": "your-grafana-token"
      }
    }
  }
}
```

## Grafana MCP Tools Reference

The Grafana MCP server ([mcp-grafana](https://github.com/grafana/mcp-grafana)) provides tools across several categories. The most relevant for network diagnostics:

| Category | Key Tools | Description |
|----------|-----------|-------------|
| **Alerting** | `list_alert_rules`, `get_alert_rule_by_uid` | View active alert rules and their current state |
| **Prometheus** | `query_prometheus`, `list_prometheus_metric_names` | Execute PromQL queries against time-series data |
| **Loki** | `query_loki_logs`, `query_loki_patterns` | Search device logs forwarded to Loki |
| **Dashboards** | `search_dashboards`, `get_dashboard_by_uid` | Find and inspect network dashboards |
| **Incidents** | `list_incidents`, `create_incident` | View and create Grafana Incident records |
| **OnCall** | `get_current_on_call_users` | Find who's on-call for escalation |
| **Annotations** | `create_annotation` | Mark events on dashboards (e.g., "diagnosed by AI agent") |

Tool categories can be enabled/disabled via `--disable-<category>` flags. Use `--disable-write` to run in read-only mode.

## Example Prompts

Once both servers are configured, you can use natural language to compose tools from both:

1. **Alert-driven CPU diagnosis**: *"Check Grafana for any active alerts on spine-01. If there's a high CPU alert, run `eos_get_cpu_usage` on the device and compare the Grafana metric with the live reading."*

2. **Interface utilization correlation**: *"Grafana shows Ethernet1 on leaf-01a at 95% utilization. Get the live interface counters with `eos_get_interface_counters` and check for errors with `eos_get_interface_errors` to determine if it's a capacity issue or an error condition."*

3. **BGP metric validation**: *"Query Prometheus via Grafana for the BGP peer count metric on all spine switches. Then run `eos_get_bgp_summary` on each spine to compare the metric with live BGP state. Flag any discrepancies."*

4. **Log correlation**: *"Search Grafana Loki for syslog messages from leaf-02a containing 'LINEPROTO-5-UPDOWN' in the last hour. For each interface mentioned, run `eos_troubleshoot_interface_issues` to check the current state."*

5. **Dashboard-enriched health check**: *"Find the network health dashboard in Grafana and list what metrics it tracks. Then run `eos_validate_fabric_health` to get live health data and identify any gaps between what Grafana monitors and what the devices report."*

6. **Alert-to-report pipeline**: *"List all firing Grafana alerts for network devices. For each alert, run the appropriate network-mcp diagnostic (health check, BGP diagnosis, or interface troubleshoot). Export the combined findings using `eos_export_report`."*

## Workflow: Alert-Driven Diagnostics

This walkthrough shows how an AI agent responds to a Grafana alert by automatically diagnosing the issue on the live device.

### Step 1 — Check active Grafana alerts

The agent queries Grafana for firing alert rules:

```
list_alert_rules()
```

Returns alerts such as:

```json
[
  {
    "uid": "abc123",
    "title": "High CPU on spine-01",
    "state": "firing",
    "labels": {"host": "spine-01", "severity": "warning"},
    "annotations": {"summary": "CPU usage > 80% for 5 minutes"}
  }
]
```

### Step 2 — Get metric context from Grafana

The agent queries Prometheus for the specific metric that triggered the alert:

```
query_prometheus(
  expr="100 - (avg by (instance) (rate(node_cpu_seconds_total{mode='idle', instance='spine-01'}[5m])) * 100)",
  time="now"
)
```

Returns the current CPU utilization value as seen by the monitoring system (e.g., 87%).

### Step 3 — Get live device state from network-mcp

The agent calls network-mcp to get the current CPU state directly from the device:

```
eos_get_cpu_usage(host="spine-01")
```

Returns live CPU data including idle percentage and top processes:

```json
{
  "status": "success",
  "device": "spine-01",
  "data": {
    "cpuInfo": {"%Cpu(s)": {"idle": 12.5, "user": 65.3, "system": 22.2}},
    "processes": [
      {"pid": 1234, "command": "Bgp-main", "cpuPct": 45.1},
      {"pid": 5678, "command": "Stp", "cpuPct": 12.3}
    ]
  }
}
```

### Step 4 — Correlate and diagnose

The agent compares the Grafana metric (87% CPU) with the live device reading (87.5% busy = 12.5% idle) and identifies that the BGP process is consuming 45% CPU. It then runs a deeper check:

```
eos_get_bgp_summary(host="spine-01")
```

This reveals a large number of route updates from a peer, explaining the CPU spike.

### Step 5 — Document findings

The agent compiles the diagnosis into a structured report:

```
eos_export_report(
  data={
    "alert": "High CPU on spine-01",
    "grafana_metric": "87% CPU utilization",
    "live_device": "87.5% CPU, Bgp-main at 45.1%",
    "root_cause": "Excessive BGP route updates from peer 10.0.0.2",
    "recommendation": "Investigate peer 10.0.0.2 for route instability"
  },
  output_format="markdown",
  title="Alert Diagnosis: High CPU on spine-01"
)
```

Optionally, the agent can create an annotation on the Grafana dashboard:

```
create_annotation(
  dashboard_uid="network-overview",
  text="AI diagnosis: High CPU caused by BGP route churn from 10.0.0.2",
  time_from="now"
)
```

## Workflow: Metric-to-CLI Correlation

Use Grafana's historical time-series data to decide which network-mcp tools to run for investigation.

### Step 1 — Identify anomalous metrics

```
query_prometheus(
  expr="rate(interface_errors_total{device='leaf-01a'}[1h]) > 0"
)
```

Prometheus returns a list of interfaces with non-zero error rates over the past hour.

### Step 2 — Run targeted diagnostics

For each interface with errors, the agent runs the appropriate network-mcp workflow tool:

```
eos_troubleshoot_interface_issues(host="leaf-01a", interface="Ethernet3")
eos_troubleshoot_interface_issues(host="leaf-01a", interface="Ethernet7")
```

The workflow tool runs multiple show commands (interface detail, error counters, transceiver levels) and produces a structured diagnosis with severity, findings, and recommended actions.

### Step 3 — Check for patterns

The agent looks for patterns across the results — for example, multiple interfaces with CRC errors might indicate a common cause (e.g., a bad line card), while errors on a single interface suggest a cable or transceiver issue.

### Step 4 — Cross-reference with Grafana logs

```
query_loki_logs(
  query="{host=\"leaf-01a\"} |= \"LINEPROTO\" | json",
  limit=50
)
```

This retrieves recent syslog messages forwarded to Loki, showing whether the interfaces have been flapping.

## Workflow: Dashboard Enrichment

Use live device data from network-mcp to validate and supplement what Grafana dashboards display.

### Step 1 — Find the relevant dashboard

```
search_dashboards(query="network health")
```

Returns a dashboard UID that the agent can inspect for its panels and queries.

### Step 2 — Identify monitored metrics

```
get_dashboard_panel_queries(dashboard_uid="network-health-01")
```

Returns the PromQL queries behind each panel, showing which metrics the dashboard tracks (e.g., interface utilization, BGP peer count, device uptime).

### Step 3 — Get complementary live data

For metrics Grafana tracks via SNMP or streaming telemetry, the agent fetches the same data directly from devices to verify consistency:

```
eos_get_device_health(host="spine-01")
eos_get_interfaces_status(host="spine-01")
eos_get_bgp_summary(host="spine-01")
```

### Step 4 — Identify monitoring gaps

The agent compares what Grafana monitors against what network-mcp can report. For example:
- Grafana tracks CPU and memory but not TCAM utilization → recommend adding `eos_get_hardware_capacity` data
- Grafana tracks BGP peer count but not prefix counts → recommend monitoring prefix received/advertised
- Grafana has no MLAG monitoring → recommend adding `eos_get_mlag_status` data

This gap analysis helps teams improve their monitoring coverage.

## Tips and Best Practices

### Use Grafana's read-only mode for safety

Start the Grafana MCP server with `--disable-write` to prevent the AI agent from modifying dashboards or alert rules during diagnostics. This is especially important in production environments.

```json
{
  "mcpServers": {
    "grafana": {
      "command": "uvx",
      "args": ["mcp-grafana", "--disable-write"],
      "env": {
        "GRAFANA_URL": "https://grafana.example.com",
        "GRAFANA_SERVICE_ACCOUNT_TOKEN": "your-token"
      }
    }
  }
}
```

### Match device names between systems

Grafana metrics typically use labels like `instance`, `host`, or `device` to identify network equipment. These labels must match the hostnames used in your network-mcp inventory (`devices.yaml`) for the agent to correlate data automatically.

If naming conventions differ, instruct the agent explicitly: *"In Grafana, spine-01 is labeled as 10.0.0.1 in Prometheus. In network-mcp, the same device is 'spine-01'. Map by IP address."*

### Use PromQL for targeted queries

Instead of asking the agent to search broadly, provide specific PromQL patterns:

- CPU: `100 - (avg(rate(node_cpu_seconds_total{mode="idle",instance=~"spine.*"}[5m])) * 100)`
- Interface errors: `rate(interface_errors_total{device="leaf-01a"}[1h])`
- BGP peers: `bgp_peer_count{device="spine-01"}`

This gives the agent precise data to compare against live device state.

### Combine historical metrics with point-in-time CLI data

Grafana excels at historical trends (metrics over hours/days). network-mcp provides real-time point-in-time data. The combination is powerful:

- Grafana shows CPU spiked 2 hours ago → network-mcp shows what's running now
- Grafana shows interface error rate increasing → network-mcp shows current transceiver power levels
- Grafana shows BGP peer count dropped → network-mcp shows which peers are currently down and why

### Use `eos_export_report` for combined reports

After correlating Grafana metrics with live device data, use `eos_export_report` to produce a clean report:

```
eos_export_report(
  data={...combined findings...},
  output_format="markdown",
  title="Network Diagnostic Report: Grafana Alert Correlation"
)
```

### Use demo mode for testing

You can test the integration workflow without live Arista switches by running network-mcp in demo mode:

```json
{
  "mcpServers": {
    "network-mcp": {
      "command": "uvx",
      "args": ["network-mcp"],
      "env": { "EOS_DEMO_MODE": "true" }
    },
    "grafana": {
      "command": "uvx",
      "args": ["mcp-grafana"],
      "env": {
        "GRAFANA_URL": "https://grafana.example.com",
        "GRAFANA_SERVICE_ACCOUNT_TOKEN": "your-token"
      }
    }
  }
}
```

### Structure prompts for multi-server workflows

When composing both servers, be explicit about the correlation you want:

> *"First, list all firing Grafana alerts with 'network' in the title. For each alert, identify the affected device. Then run eos_get_device_health on that device to get a live health check. Compare the alert details with the live data and summarize the findings in a Markdown report."*

This gives the agent a clear sequence of operations rather than leaving it to guess the workflow.

## Troubleshooting

### Agent only uses one server

If the AI agent only calls tools from one server, verify both servers appear in your MCP client. In Claude Desktop, open **Settings > Developer > MCP Servers** and confirm both `network-mcp` and `grafana` show a green status. If a server shows an error, check the server logs for configuration issues.

### Grafana returns authentication errors

- Verify your `GRAFANA_SERVICE_ACCOUNT_TOKEN` is valid and hasn't expired
- Ensure the service account has at least Viewer role (Editor if you want annotation/incident creation)
- For Grafana Cloud, use the full instance URL: `https://your-org.grafana.net`
- Test the token directly: `curl -H "Authorization: Bearer <token>" https://grafana.example.com/api/org`

### PromQL queries return empty results

- Verify the metric name exists: use `list_prometheus_metric_names` to search
- Check label values match your expectations: use `list_prometheus_labels`
- Ensure the time range is appropriate — default queries return current values

### Dashboard tools return large responses

Dashboard JSON can be large and consume significant context window space. Use `get_dashboard_summary` instead of `get_dashboard_by_uid` for an overview, or `get_dashboard_property` with a JSONPath to extract specific panels.

### Timeout errors on device queries

When correlating alerts across many devices, you may hit timeouts. Strategies:
- Use field filtering on network-mcp tools to reduce response sizes
- Process devices in batches: *"Check the first 5 spine switches, then the next 5"*
- Increase network-mcp timeouts: `TIMEOUT_SHOW=60` for slower devices

## Further Reading

- [Grafana MCP Server on GitHub](https://github.com/grafana/mcp-grafana)
- [Grafana MCP Server documentation](https://grafana.com/docs/grafana-cloud/machine-learning/assistant/mcp/understand-mcp-servers/)
- [network-mcp tool reference](../TOOLS.md)
- [network-mcp resource reference](../RESOURCES.md)
- [MCP protocol specification](https://modelcontextprotocol.io)
