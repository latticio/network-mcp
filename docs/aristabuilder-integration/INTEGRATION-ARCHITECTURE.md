# Integration Architecture: AristaBuilder x network-mcp

> March 2026 — Detailed technical architecture for connecting AristaBuilder (visual topology design) with network-mcp (live device operations via MCP). Covers transport, data flows, shared contracts, required changes, and graceful degradation.

---

## Part 1: Convergence Report Review

### Missing Integration Opportunities

The original [Convergence Report](CONVERGENCE-REPORT-NETWORK-MCP.md) identified 8 integration opportunities. After deep analysis of network-mcp's source code, several additional synergies were missed:

**1. Prometheus/Grafana Integration**
network-mcp's `integrations/prometheus.py` exports device metrics (uptime, CPU, memory, BGP peer counts, interface states) in Prometheus format and generates Grafana dashboard JSON. AristaBuilder could:
- Embed Grafana panels in the Dashboard app for operational overview
- Use Prometheus metrics to annotate canvas nodes with live health indicators
- Generate Grafana dashboards from topology layout (matching node positions to dashboard panels)

**2. Ansible Playbook Execution**
network-mcp's `integrations/ansible.py` generates platform-aware Ansible playbooks (VLAN provisioning, BGP config, backup, compliance). AristaBuilder generates AVD YAML but not runnable playbooks. The integration path:
- AristaBuilder generates AVD structured data
- network-mcp's Ansible integration generates and executes deployment playbooks
- Eliminates the need for AristaBuilder to build its own playbook runner

**3. Vault Credential Management**
network-mcp has a full `VaultCredentialProvider` (HashiCorp Vault KV v2, auto-rotation). AristaBuilder's lab deployment and CVaaS features will need credentials. Instead of storing device credentials in Supabase:
- network-mcp handles all credential management (Vault, env vars, devices.yaml)
- AristaBuilder never touches device passwords — all access proxied through network-mcp

**4. Plugin System for Custom Tools**
network-mcp supports third-party plugins via Python entry points (`network_mcp.drivers`, `network_mcp.compliance`, `network_mcp.tools`). AristaBuilder-specific tools could be packaged as a network-mcp plugin:
- `aristabuilder.tools`: Tools for receiving topology data, config diffs, ANTA catalogs
- Keeps network-mcp generic while adding AristaBuilder-aware capabilities

**5. Progressive Discovery**
network-mcp supports `NET_PROGRESSIVE_DISCOVERY=true` which defers all tool modules except `meta` and `workflows`. With 278+ combined tools (17 design + 261 ops), the unified AI chat would overwhelm Claude's tool selection. Progressive discovery lets the AI load tool groups on demand via `net_discover_tools(category="routing")`.

**6. Config Sessions for Safe Deployment**
network-mcp's `tools/sessions.py` provides atomic config sessions for EOS:
- `eos_create_config_session` — start atomic change
- `eos_add_to_config_session` — stage changes
- `eos_get_config_session_diff` — review diff
- `eos_commit_config_session_timer` — commit with auto-rollback timer
- `eos_confirm_commit_timer` — make permanent

This enables AristaBuilder to push configs with rollback safety — a critical enterprise requirement not mentioned in the convergence report.

**7. CIS Compliance Checking**
network-mcp's `tools/compliance.py` has 3 CIS benchmark compliance tools. AristaBuilder could validate generated EOS configs against CIS benchmarks *before* deployment, catching security misconfigurations at design time.

**8. Export as Markdown/CSV/JSON**
network-mcp's `tools/export.py` can export device state as Markdown tables, CSV, or JSON. AristaBuilder's runbook generation could incorporate live device state snapshots alongside design documentation.

### Risks Not Adequately Addressed

**1. Transport Selection Has Major Implications**
The convergence report mentions "Optional MCP Connection" without specifying transport. This is the most critical architectural decision:
- **stdio**: Simple, but ties network-mcp lifetime to Express backend process. One instance per server. No horizontal scaling.
- **Streamable HTTP**: network-mcp runs independently. Multiple AristaBuilder instances share one network-mcp server. Supports OAuth 2.1. But requires network reachability.
- Choice affects deployment topology, scaling, auth, and failure modes (see Part 3 Section 1).

**2. Latency Budget**
Calling network-mcp tools adds round-trip latency. Some operations are slow by nature:
- `net_validate_fabric_health` on 20 devices: 10-30 seconds
- `eos_run_anta_tests` with 50 test cases: 30-120 seconds
- `eos_gnmi_subscribe_stream` with 10s duration: 10+ seconds

AristaBuilder's UI must handle these gracefully — loading states, progress indicators, cancellation, and timeout handling. The convergence report's "connect and query" framing underestimates the UX work.

**3. OWASP MCP Top 10 Client Compliance**
network-mcp claims OWASP MCP Top 10 compliance on the server side. AristaBuilder as an MCP client must also follow OWASP MCP client guidelines:
- Tool result validation (don't blindly trust network-mcp responses)
- Prompt injection detection in tool results
- Rate limiting of outbound MCP calls
- Audit logging of all MCP operations

**4. Demo Mode Parity**
network-mcp has `NET_DEMO_MODE=true` with mock data for all tools. AristaBuilder needs equivalent behavior when network-mcp isn't connected — every "live" feature must show realistic mock/example data for demos. The two demo modes need to produce consistent data (same device names, same topology).

**5. Version Compatibility**
network-mcp is at v7.0.0 using FastMCP. When it updates (new tools, removed tools, changed signatures), AristaBuilder must handle:
- Tool discovery at connection time (don't hardcode tool names)
- Graceful handling of missing tools (feature degradation, not crash)
- Version negotiation via `net_server_info` tool

### Delegate vs Build Corrections

| Item | Current Assignment | Correction | Reason |
|------|-------------------|------------|--------|
| Ansible playbook execution | Build (AristaBuilder v1.0 Phase 2) | Delegate to network-mcp | `integrations/ansible.py` already generates and can run playbooks. AristaBuilder should generate AVD YAML; network-mcp handles execution |
| CIS compliance checking | Not mentioned | Delegate to network-mcp | `tools/compliance.py` has 3 CIS tools. Design-time compliance = send generated config to network-mcp for validation |
| Prometheus metrics export | Not mentioned | Delegate to network-mcp | `integrations/prometheus.py` already collects and formats metrics |
| Config session management | Not mentioned | Delegate to network-mcp | `tools/sessions.py` has atomic config sessions with rollback. No need to build config push logic in AristaBuilder |
| Credential storage | Implicit in AristaBuilder | Delegate to network-mcp | Vault integration exists. AristaBuilder should never store device credentials |
| Lab deployment (clab) | Build in AristaBuilder | **Keep building** (correct) | ContainerLab deployment is local CLI, not device operations. But post-deploy validation delegates to network-mcp |

### Version Ordering Assessment

The ROADMAP.md version ordering is mostly correct but has two issues:

**1. v1.4 (SONiC + clab import) should move before v1.3 (Design Versioning)**
- SONiC export and ContainerLab import are low-medium effort features that expand the user base
- Design versioning is a high-effort infrastructure investment with no external dependencies
- Shipping more export formats faster has higher market impact

**2. v1.5 (AI Studio Enhancements) has a dependency on v1.1 (network-mcp Connection)**
- RoCEv2/lossless fabric validation (v1.5 Phase 2) is purely design-time — no network-mcp dependency
- But the AI Studio chat integration (v1.5 Phase 1) would benefit from unified chat (v1.1+)
- Consider splitting: move RoCEv2 validation and catalog updates to v1.2 or v1.3, keep AI Studio chat after unified chat infrastructure

**3. v2.1 (Enterprise) depends on v1.3 (Design Versioning)**
- Project lifecycle states (v2.1 Phase 3) need version snapshots (v1.3) to be meaningful
- Approval workflows without version history are just status labels
- This dependency is implicit; make it explicit

---

## Part 2: Combined Solution Potential

### End-to-End Lifecycle Coverage

No competitor covers the full network lifecycle. Here's what the combined platform enables:

```
DAY 0 (Design)                    DAY 1 (Deploy)                    DAY 2 (Operate)
AristaBuilder                     Both                              network-mcp
─────────────                     ────                              ───────────

Visual topology design             ContainerLab deploy              Live device queries
  + AI topology generators         + ANTA validation                BGP/OSPF diagnostics
  + Multi-vendor catalog           + Config drift check             Fabric health checks
  + BOM/pricing/licensing          + CVaaS push                     Telemetry streaming
  + Network services (VLAN/VRF)    + Ansible playbooks              Change management
  + EOS config generation          + Config sessions                ITSM integration
  + Validation (12 rules)          + Compliance check               Drift remediation
  + IaC export (AVD/clab/ANTA)                                     Incident creation
  + Quote PDF                                                      Audit logging

                    ┌─────────────────────────────────┐
                    │         REDESIGN LOOP            │
                    │  Drift detected → fix design →   │
                    │  re-export → re-validate → done  │
                    └─────────────────────────────────┘
```

### Unique Selling Propositions

**1. Visual Design with Operational Feedback (No Competitor Has This)**

Every competitor falls into one of two camps:
- **Design tools** (draw.io, Lucidchart, Visio): Pretty pictures, no intelligence, no IaC
- **Operational tools** (NetBrain, IP Fabric, Forward Networks, Apstra): Discover/validate existing networks, can't design new ones

The combined platform is the only product where you design a network visually, generate IaC, deploy to lab, validate automatically, fix issues in the design, and push to production — all from one UI with one AI assistant.

**2. Unified AI Assistant: 278 Tools in One Conversation**

No network product has an AI assistant that can both design and operate:

```
User: "Design a 3-tier datacenter with 4 spines, 16 leaves, dual-homed to each spine"
  → AristaBuilder chat tools: create nodes, connect edges, set port channels

User: "Deploy this to my ContainerLab"
  → AristaBuilder export + network-mcp clab tools

User: "Check if BGP is up on all spines"
  → network-mcp: net_get_bgp_summary on spine-01..04

User: "Spine-02 shows BGP down to leaf-09. Fix the peer IP in the design"
  → network-mcp diagnosis + AristaBuilder chat tool to update connection

User: "Regenerate configs and push the fix"
  → AristaBuilder EOS generator + network-mcp config session
```

Arista AVA, Forward AI, and NetBox Copilot are all single-domain AI assistants. None can cross the design-operate boundary.

**3. NetBox Three-Way Sync Creates a Data Flywheel**

```
AristaBuilder (Design Intent)
       │                    ▲
       │ export              │ import changes
       ▼                    │
    NetBox (Source of Truth) ←────────────┐
       │                                  │
       │ inventory                        │ drift updates
       ▼                                  │
network-mcp (Live State) ─────────────────┘
```

This three-way reconciliation means:
- Design changes propagate to NetBox, which network-mcp reads as inventory
- Operational changes detected by network-mcp update NetBox
- AristaBuilder's NetBox sync/diff catches changes from both directions
- Result: design, SoT, and live state are always reconcilable

No competitor has bidirectional design-to-live reconciliation through a shared SoT.

**4. Design-Time Compliance (Gap in Market)**

IP Fabric does 160+ runtime verification checks. Forward Networks does mathematical verification. But nobody validates at design time:
- AristaBuilder generates EOS configs → send to network-mcp CIS compliance → flag security issues before any device is touched
- RoCEv2/lossless fabric validation at design time (PFC/ECN thresholds, DCQCN parameters)
- This shifts compliance left — cheaper to fix in design than in production

### Market Positioning

**Target Personas (Priority Order):**

1. **Arista SEs and Channel Partners** — Design + quote + lab validate in one tool. Quote PDF + BOM for customer proposals. $0 (freemium for Arista ecosystem growth).

2. **Network Architects at Mid-Enterprise (500-5,000 employees)** — Design, document, deploy, validate. Replace draw.io + Excel + manual AVD YAML + CLI validation. $500-1,500/month per team.

3. **Data Center Operators with Mixed Vendors** — Multi-vendor design + operations. Replace Apstra (Juniper-centric) + NetBrain ($50-150K/year) with one platform. $2,000-5,000/month per team.

4. **AI/ML Infrastructure Teams** — GPU fabric design + lossless fabric validation + deployment. No other tool purpose-built for AI datacenter networking. $3,000-8,000/month per team (high willingness to pay, small market, high value).

**Competitive Positioning:**

| Dimension | AristaBuilder + network-mcp | Apstra | NetBrain | IP Fabric |
|-----------|---------------------------|--------|----------|-----------|
| Visual design | Drag-and-drop | Intent model (no visual) | Discovery-based maps | Discovery-based maps |
| IaC generation | AVD, clab, ANTA, ZTP, SONiC | Junos/EOS/NX-OS configs | No | No |
| AI assistant | 278 tools (design + ops) | Limited | AI-driven automation | No AI |
| Multi-vendor ops | 4 platforms, 261 tools | 3 platforms | Multi-vendor | Multi-vendor |
| BOM/pricing | Yes + Quote PDF | No | No | No |
| AI datacenter | Purpose-built (GPU NIC, rail-optimized) | Basic DC focus | No | No |
| Deployment model | SaaS + self-hosted | On-prem appliance | Cloud + on-prem | Cloud + on-prem |
| Price | $0-8K/mo | $50K+/year | $50-150K/year | Custom |

---

## Part 3: Detailed Integration Architecture

### 1. MCP Client in AristaBuilder

**Decision: Express backend as authenticated proxy (Option A/B hybrid)**

```
Browser (React)                    Express Backend                 network-mcp
─────────────                      ───────────────                 ───────────

React components  ──HTTP/WS──►  /api/mcp/* routes  ──MCP──►  FastMCP server
  McpConnectionPanel              MCP Client SDK              261 tools
  ConfigDriftPanel                Auth middleware              4 vendor drivers
  TelemetryOverlay                Result caching              Integrations
  LabDashboardPanel               Timeout handling
  CvpImportModal                  Audit logging
```

**Why Express backend as proxy (not browser-direct or edge function):**

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **(a) Express → network-mcp stdio** | Simplest setup, no network config, auth inherited from process | Single instance, can't scale, network-mcp dies with Express | **v1.1 MVP** |
| **(b) Express → network-mcp HTTP** | Scales, network-mcp independent, supports team deployments | Requires HTTP endpoint, OAuth config, network reachability | **v1.2+ production** |
| **(c) Browser → network-mcp HTTP** | Direct, low latency | Exposes device credentials to browser, CORS issues, no audit trail | **Rejected** |
| **(d) Supabase edge function** | Serverless, scales automatically | 10s timeout (Deno), too slow for fabric health / ANTA tests | **Rejected** |

**Transport Strategy:**
- **v1.1**: Express backend spawns `network-mcp` as child process via stdio. Zero configuration. Works with `NET_DEMO_MODE=true` out of the box.
- **v1.2+**: Express backend connects to remote network-mcp via Streamable HTTP. OAuth 2.1 token forwarding. Supports team deployments where network-mcp runs on a jump host near network devices.
- **Both**: Express backend exposes identical `/api/mcp/*` REST routes. Frontend code doesn't change between transport modes.

**Express MCP Client Implementation:**

```typescript
// backend/src/lib/mcp-client.ts

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

interface McpConnectionConfig {
  mode: 'stdio' | 'http';
  // stdio mode
  command?: string;        // default: 'uvx'
  args?: string[];         // default: ['latticio']
  env?: Record<string, string>;  // NET_DEMO_MODE, NET_INVENTORY_FILE, etc.
  // http mode
  url?: string;            // e.g., 'http://network-mcp.internal:8080/mcp'
  authToken?: string;      // JWT or API key
}

class McpClientManager {
  private client: Client | null = null;
  private transport: StdioClientTransport | StreamableHTTPClientTransport | null = null;
  private availableTools: Map<string, ToolDefinition> = new Map();

  async connect(config: McpConnectionConfig): Promise<void> { ... }
  async disconnect(): Promise<void> { ... }
  async callTool(name: string, args: Record<string, unknown>): Promise<ToolResult> { ... }
  async listTools(): Promise<ToolDefinition[]> { ... }
  isConnected(): boolean { ... }
  getStatus(): ConnectionStatus { ... }
}
```

### 2. Connection Configuration

**What the user provides:**

| Setting | Description | Storage | Default |
|---------|-------------|---------|---------|
| `mcp_mode` | `'off'` / `'stdio'` / `'http'` | Supabase `user_profiles` | `'off'` |
| `mcp_demo_mode` | Use mock data (no real devices) | Supabase `user_profiles` | `true` |
| `mcp_http_url` | network-mcp HTTP endpoint URL | Supabase `user_profiles` | `null` |
| `mcp_http_token` | API key for HTTP mode | Supabase `user_profiles` (encrypted) | `null` |
| `mcp_inventory_file` | Path to devices.yaml (stdio mode) | Express backend env | `null` |

**Auth flow:**

```
Browser                    Express Backend                 network-mcp
──────                     ───────────────                 ───────────

1. User logs in via Supabase Auth
2. User configures MCP connection in McpConnectionModal
3. Settings saved to user_profiles table

On MCP tool call:
4. Browser sends request with Supabase JWT ──►
5. Express validates Supabase JWT (existing auth middleware)
6. Express resolves user's MCP config from user_profiles
7. Express forwards tool call to network-mcp:
   - stdio: child process, no auth needed (same machine)
   - http: Bearer token in Authorization header
8. network-mcp validates token (if AUTH_ENABLED)
9. network-mcp checks RBAC scopes (if NET_RBAC_ENABLED)
10. Response flows back through Express to browser
```

**Critical: Device credentials never leave network-mcp.** AristaBuilder stores only the connection *to* network-mcp, not credentials *for* network devices. network-mcp manages device credentials via its own `devices.yaml`, Vault integration, or env vars.

### 3. Data Flow for Each Integration Point

#### 3.1 Lab Validation Loop

```
AristaBuilder                     Express Backend              network-mcp
─────────────                     ───────────────              ───────────

1. User clicks "Validate Lab" in LabDashboardPanel
   └─ topology-store has lab device hostnames (from clab deploy)

2. Frontend: POST /api/mcp/lab/validate
   Body: {
     devices: ["spine-01", "leaf-01a", "leaf-01b", ...],
     antaCatalog: "<ANTA YAML string from anta-export.ts>",
     tests: ["connectivity", "bgp", "mlag"]  // optional filter
   }

3. Express Backend:
   a. Validates request, checks MCP connection
   b. Writes ANTA catalog to temp file (or passes inline)
   c. For each device in parallel (ThreadPoolExecutor):
      - callTool("eos_run_anta_tests", {
          host: device,
          catalog_yaml: antaCatalogString
        })
   d. Aggregates results into per-device summary

4. network-mcp (per device):
   a. Parses ANTA catalog YAML into AntaCatalog
   b. Creates AsyncEOSDevice with credentials from inventory
   c. Runs ANTA test catalog via anta.runner
   d. Returns: {
        status: "success",
        device: "spine-01",
        data: {
          total: 12, passed: 10, failed: 1, skipped: 1,
          results: [
            { test: "VerifyBGPPeersHealth", result: "success", ... },
            { test: "VerifyMLAGStatus", result: "failure", messages: [...] },
            ...
          ]
        }
      }

5. Express aggregates and returns to frontend:
   {
     status: "success",
     summary: { total: 48, passed: 42, failed: 4, skipped: 2 },
     devices: {
       "spine-01": { passed: 10, failed: 1, skipped: 1, results: [...] },
       "leaf-01a": { passed: 11, failed: 0, skipped: 1, results: [...] },
       ...
     }
   }

6. LabDashboardPanel renders:
   - Overall health bar (87.5% pass rate)
   - Per-device accordion with pass/fail/skip counts
   - Failed test details with messages and remediation hints
   - "Re-run" button for failed tests only
```

**Timeout handling:** ANTA tests can take 30-120 seconds. Express sets a 180s timeout on the MCP call. Frontend shows a progress indicator with per-device status updates (via polling or SSE).

#### 3.2 Config Drift Detection

```
AristaBuilder                     Express Backend              network-mcp
─────────────                     ───────────────              ───────────

1. User opens ConfigDriftPanel for device "leaf-01a"

2. AristaBuilder generates intended config:
   - EOS config generator produces full running-config text
   - This is the "golden" config (design intent)

3. Frontend: POST /api/mcp/drift/check
   Body: {
     host: "leaf-01a",
     intendedConfig: "<full EOS config from generator>"
   }

4. Express Backend:
   a. callTool("net_check_drift_inline", {
        host: "leaf-01a",
        golden_config: intendedConfig
      })
   NOTE: This tool doesn't exist yet in network-mcp.
   Fallback: write intended config to temp file, call net_check_drift
   with golden_source="file" and NET_GOLDEN_CONFIG_DIR pointing to temp dir.

5. network-mcp DriftDetector:
   a. Fetches running config from device: driver.get_config()
   b. Normalizes both configs (strip comments, sort sections)
   c. Compares section by section
   d. Classifies diffs by severity:
      - critical: ACLs, routing (BGP/OSPF), AAA, crypto, route-maps
      - warning: Interfaces, VLANs, logging, NTP, SNMP, STP
      - info: Comments, banners, descriptions
   e. Returns: {
        status: "success",
        device: "leaf-01a",
        data: {
          has_drift: true,
          severity: "warning",
          sections: {
            "interface Ethernet3": {
              severity: "warning",
              intended: "  description Server-Port\n  switchport access vlan 100",
              actual: "  description OLD-Server\n  switchport access vlan 200",
              diff: "<unified diff>"
            },
            "router bgp 65001": {
              severity: "critical",
              intended: "  neighbor 10.0.0.1 remote-as 65000",
              actual: "  neighbor 10.0.0.1 remote-as 65099",
              diff: "<unified diff>"
            }
          },
          summary: {
            critical: 1, warning: 1, info: 0
          }
        }
      }

6. ConfigDriftPanel renders:
   - Severity badge: "1 Critical, 1 Warning"
   - Split-pane diff view (monaco-diff-editor) per section
   - "Fix in Design" button → navigates to affected device/config
   - "Accept Live" button → updates design to match live state
   - "Remediate" button → generates fix commands via network-mcp
```

#### 3.3 CVaaS Import

```
AristaBuilder                     Express Backend              network-mcp
─────────────                     ───────────────              ───────────

1. User clicks "Import from CloudVision" in CvpImportModal

2. Phase 1 — Fetch device inventory:
   POST /api/mcp/cvp/inventory
   → callTool("eos_get_cvp_inventory")
   → Returns: [
       { hostname: "spine-01", ip: "10.0.0.1", model: "DCS-7280SR3-48YC8",
         eos_version: "4.32.1F", serial: "ABC123" },
       { hostname: "leaf-01a", ip: "10.0.1.1", model: "DCS-7050CX3-32S",
         eos_version: "4.32.1F", serial: "DEF456" },
       ...
     ]

3. Phase 2 — Map to AristaBuilder catalog:
   Express backend (or frontend) performs fuzzy matching:
   - "DCS-7280SR3-48YC8" → catalog entry { id: "7280sr3-48yc8", ... }
   - "DCS-7050CX3-32S" → catalog entry { id: "7050cx3-32s", ... }
   - Unmatched models → generic node with manual model selection

4. Phase 3 — Discover topology via LLDP:
   For each device in parallel:
   POST /api/mcp/tool
   → callTool("net_get_lldp_neighbors", { host: device.hostname })
   → Returns: {
       "Ethernet1": [{ hostname: "spine-01", port: "Ethernet49" }],
       "Ethernet2": [{ hostname: "spine-02", port: "Ethernet49" }],
       ...
     }

5. Phase 4 — Build topology in Zustand store:
   Frontend receives merged inventory + LLDP data:
   a. Create topology nodes:
      - For each device: addNode({
          id: nanoid(),
          type: catalogEntry.type,
          data: {
            label: device.hostname,
            model: catalogEntry,
            mcpHostname: device.hostname,  // bidirectional mapping
            ipAddress: device.ip,
            eosVersion: device.eos_version
          }
        })
   b. Create edges from LLDP adjacencies:
      - For each LLDP neighbor pair: addEdge({
          source: nodeIdByHostname[localDevice],
          sourceHandle: portNameToId(localPort),  // "Ethernet1" → "eth1"
          target: nodeIdByHostname[remoteDevice],
          targetHandle: portNameToId(remotePort),
        })
   c. Auto-layout nodes (dagre/elk) based on role (spine=top, leaf=middle, host=bottom)

6. CvpImportModal shows:
   - Preview of discovered devices with catalog matches
   - Preview of discovered links
   - Unmatched devices highlighted for manual resolution
   - "Import" button to commit to topology store
```

#### 3.4 CVaaS Push (AVD → CVaaS Studios)

```
AristaBuilder                     Express Backend              network-mcp
─────────────                     ───────────────              ───────────

1. User clicks "Push to CloudVision" in export menu

2. AristaBuilder generates AVD YAML (existing export):
   - inventory.yml
   - group_vars/FABRIC.yml, SPINES.yml, LEAFS.yml
   - Network services YAML

3. Frontend: POST /api/mcp/cvp/push
   Body: {
     avdYaml: { inventory: "...", fabricVars: "...", ... },
     changeControlDescription: "DC1 Fabric Initial Config",
     dryRun: false
   }

4. Express Backend sequences:
   a. callTool("eos_create_config_session", { host: device })
      for each device — creates atomic change windows

   b. For each device, render device-specific config from AVD:
      (This requires PyAVD or equivalent — see Changes Needed)

   c. callTool("eos_add_to_config_session", {
        host: device,
        session_name: sessionId,
        commands: configLines
      })

   d. callTool("eos_get_config_session_diff", {
        host: device,
        session_name: sessionId
      })
      → Returns diff for user review

   e. If dryRun: return diffs without committing

   f. callTool("eos_commit_config_session_timer", {
        host: device,
        session_name: sessionId,
        timer_minutes: 5  // auto-rollback after 5 min
      })

   g. After validation passes:
      callTool("eos_confirm_commit_timer", {
        host: device,
        session_name: sessionId
      })

5. Frontend shows:
   - Per-device config diff (approve/reject each)
   - Progress bar during push
   - Post-push validation results (auto-triggered ANTA)
   - Rollback button (calls eos_rollback_config_session)
```

#### 3.5 Telemetry Overlay

```
AristaBuilder                     Express Backend              network-mcp
─────────────                     ───────────────              ───────────

1. User enables "Live Telemetry" toggle on canvas toolbar

2. Frontend establishes SSE connection:
   GET /api/mcp/telemetry/stream?devices=spine-01,leaf-01a,...

3. Express Backend starts polling loop (every 30s):
   For each device with mcpHostname mapping:

   a. callTool("eos_gnmi_subscribe_once", {
        host: device,
        paths: [
          "/interfaces/interface/state/counters",
          "/interfaces/interface/state/oper-status",
          "/network-instances/network-instance[name=default]/.../bgp/neighbors/neighbor/state"
        ]
      })

   b. Parse gNMI response into telemetry update:
      {
        device: "spine-01",
        timestamp: "2026-03-07T10:30:00Z",
        interfaces: {
          "Ethernet1": {
            operStatus: "UP",
            inOctetsRate: 125000000,  // 1 Gbps
            outOctetsRate: 98000000,
            inErrors: 0,
            utilization: 12.5  // percentage of link speed
          },
          ...
        },
        bgp: {
          "10.0.0.1": { state: "Established", prefixesReceived: 156 },
          ...
        }
      }

   c. Push via SSE to frontend

4. Frontend TelemetryOverlay:
   - Canvas nodes: colored health badge (green/yellow/red)
   - Canvas edges: thickness/color based on utilization
   - Hover tooltip: live counters, BGP state
   - Node inspector: real-time interface table with sparkline charts

5. Cleanup:
   - On toggle off or page leave: close SSE, stop polling
   - On device removed from canvas: stop polling that device
   - On network-mcp disconnect: clear all telemetry, disable toggle
```

#### 3.6 Unified AI Chat

```
Browser Chat Widget          Supabase Edge Function        Express Backend       network-mcp
────────────────              ─────────────────────         ───────────────       ───────────

1. User sends message: "Check BGP on spine-01 and fix any issues in the design"

2. Edge function receives message + conversation history

3. Edge function builds tool list:
   Design tools (17): addDevice, removeDevice, connectDevices, ...
   Network tools: fetched from Express backend at session start
     GET /api/mcp/tools → returns available network-mcp tools
     (cached per session, refreshed on reconnect)

4. Claude selects tools based on intent:

   Step 1: "Check BGP" → network tool
   Edge function calls: POST /api/mcp/tool
   Body: { tool: "net_get_bgp_summary", args: { host: "spine-01" } }
   → Express proxies to network-mcp
   → Returns: { peers: { "10.0.0.2": { state: "Active" } } }

   Step 2: "Fix in design" → design tool
   Edge function calls design tool directly:
   updateConnection({ source: "spine-01", target: "leaf-03", ... })
   → Returns topology update via WebSocket

5. Claude responds:
   "BGP peer 10.0.0.2 on spine-01 is in Active state (down).
    This peer connects to leaf-03. I've updated the peer IP
    in the design from 10.0.0.3 to 10.0.0.2. Regenerate configs
    to apply the fix."

6. Tool routing decision tree:
   - Tool name starts with "net_" or "eos_" → proxy to network-mcp
   - Tool name matches design tools → execute locally
   - Unknown tool → return error "Tool not available"
```

**Progressive Discovery for 278+ Tools:**

Claude can efficiently handle ~50-80 tools. With 278+, we need progressive loading:

1. Always loaded: 17 design tools + 5 meta tools (`net_server_info`, `net_list_tools`, `net_discover_tools`, `net_describe_tool`, `net_search_tools`)
2. On-demand: When Claude needs network info, it calls `net_discover_tools(category="routing")` to load routing tools
3. The edge function maintains a session-scoped tool cache, expanding it as Claude discovers more tools

### 4. Shared Types & Contracts

#### Device Identity Mapping

AristaBuilder uses `nanoid` IDs for React Flow nodes. network-mcp uses hostname strings. Both need bidirectional mapping:

```typescript
// AristaBuilder: topology node data
interface TopologyNodeData {
  label: string;          // Display name ("Spine-01")
  model: CatalogDevice;   // From device catalog
  // NEW: network-mcp mapping
  mcpHostname?: string;   // "spine-01" — maps to network-mcp inventory
  mcpPlatform?: Platform; // "eos" | "iosxe" | "nxos" | "junos"
  ipAddress?: string;     // Management IP for device
}
```

```python
# network-mcp: no changes needed
# Tools use hostname strings ("spine-01") which map to inventory entries
# AristaBuilder stores the hostname on the topology node
```

#### Platform Mapping

```typescript
// packages/shared-types/src/platform.ts

// AristaBuilder vendor enum (existing)
type SwitchVendor = 'arista' | 'cisco' | 'juniper' | 'nokia' | 'broadcom' | 'dell' | 'hpe';

// network-mcp platform enum
type McpPlatform = 'eos' | 'iosxe' | 'nxos' | 'junos';

// Mapping (AristaBuilder → network-mcp)
const VENDOR_TO_PLATFORM: Record<SwitchVendor, McpPlatform | null> = {
  arista: 'eos',
  cisco: 'iosxe',   // Default; NX-OS devices detected by model prefix
  juniper: 'junos',
  nokia: null,       // Not supported by network-mcp
  broadcom: null,
  dell: null,        // Dell SONiC devices use 'eos' driver (if EOS-based)
  hpe: null,
};

// Model-based NX-OS detection
function detectPlatform(vendor: SwitchVendor, model: string): McpPlatform | null {
  if (vendor === 'cisco' && model.startsWith('N9K')) return 'nxos';
  if (vendor === 'cisco' && model.startsWith('N7K')) return 'nxos';
  if (vendor === 'cisco' && model.startsWith('N5K')) return 'nxos';
  return VENDOR_TO_PLATFORM[vendor];
}
```

#### Shared Data Contracts

These are the key data structures that cross the boundary:

| Data | AristaBuilder Format | network-mcp Format | Alignment |
|------|---------------------|-------------------|-----------|
| Device config | EOS config text (from generator) | Config text (from `driver.get_config()`) | Compatible (both are EOS running-config format) |
| ANTA catalog | YAML string (from `anta-export.ts`) | YAML string or `AntaCatalog` object | Compatible (same ANTA YAML format) |
| LLDP neighbors | Not used currently | `dict[str, list[dict]]` per interface | Need to map interface names to port IDs |
| BGP summary | Not used currently | `dict` with peers, state, prefixes | Need new telemetry types in AristaBuilder |
| Drift results | Not used currently | `dict` with severity-classified sections | Need new drift types in AristaBuilder |
| NetBox devices | `NetBoxDevice` type (existing) | `pynetbox` objects | Both use NetBox API — compatible at API level |

**No shared schema package needed.** The boundary is a JSON-RPC protocol (MCP). AristaBuilder defines TypeScript types for network-mcp responses; network-mcp defines Python types for its outputs. They communicate via JSON. Adding a shared schema package would create a tight coupling between two different language ecosystems with no benefit.

### 5. Changes Needed in network-mcp

#### New Tools

| Tool | Purpose | Priority |
|------|---------|----------|
| `net_check_drift_inline` | Accept golden config as string parameter (not file/NetBox). Signature: `(host: str, golden_config: str) -> dict` | **v1.1** (required for config drift) |
| `eos_run_anta_catalog_inline` | Accept ANTA catalog as YAML string. Signature: `(host: str, catalog_yaml: str) -> dict` | **v1.1** (required for lab validation) |
| `net_build_topology_from_lldp` | Query LLDP on multiple devices, return graph. Signature: `(hosts: list[str]) -> dict` with nodes and edges | **v1.2** (simplifies CVaaS import) |
| `eos_get_cvp_device_config` | Pull per-device config from CVaaS (not just inventory/events). Signature: `(host: str) -> dict` | **v1.2** (CVaaS import) |
| `eos_push_config_via_cvp` | Push config to device via CVaaS Change Control. Signature: `(host: str, config: str, change_control_name: str) -> dict` | **v1.2** (CVaaS push) |

#### Enhancements to Existing Tools

| Change | Tool | Reason |
|--------|------|--------|
| Add `catalog_yaml` string parameter | `eos_run_anta_tests` | Currently reads from file. Need inline YAML for AristaBuilder integration |
| Add `golden_config` string parameter | `net_check_drift` | Currently reads from file/NetBox. Need inline config string |
| Add tool category metadata | All tools via `@mcp.tool()` | Progressive discovery needs category labels (`routing`, `switching`, `monitoring`, etc.) |
| Return interface speed in `net_get_lldp_neighbors` | `tools/common/switching.py` | AristaBuilder needs link speed for edge rendering |

#### Infrastructure Changes

| Change | Module | Reason |
|--------|--------|--------|
| SSE streaming for telemetry | `server.py` + new `sse.py` | HTTP transport needs SSE for real-time gNMI data push |
| Tool grouping in manifest | `server.py` | Add `x-tool-group` annotations for progressive discovery |
| Healthcheck endpoint | `server.py` | `/health` endpoint for Express backend connection monitoring |

### 6. Changes Needed in AristaBuilder

#### New Monorepo Package

```
packages/mcp-client/
  src/
    index.ts              # Package exports
    client.ts             # McpClientManager (Express backend MCP proxy)
    types.ts              # TypeScript types for network-mcp responses
    platform-mapping.ts   # SwitchVendor ↔ McpPlatform mapping
```

#### New Backend Routes

```
backend/src/routes/mcp.ts

POST   /api/mcp/connect          # Establish MCP connection
POST   /api/mcp/disconnect       # Tear down connection
GET    /api/mcp/status            # Connection health check
POST   /api/mcp/tool              # Proxy a tool call to network-mcp
GET    /api/mcp/tools             # List available tools
POST   /api/mcp/lab/validate      # Lab validation (orchestrates multiple ANTA calls)
POST   /api/mcp/drift/check       # Config drift check (sends intended config)
GET    /api/mcp/cvp/inventory     # Fetch CVaaS inventory
POST   /api/mcp/cvp/push          # Push configs via CVaaS
GET    /api/mcp/telemetry/stream  # SSE endpoint for live telemetry
```

#### New Stores

```typescript
// packages/stores/src/mcp/mcp-connection-store.ts
interface McpConnectionState {
  status: 'disconnected' | 'connecting' | 'connected' | 'error';
  mode: 'off' | 'stdio' | 'http';
  demoMode: boolean;
  availableTools: string[];
  error: string | null;
  lastHeartbeat: number | null;
  // Actions
  connect: (config: McpConnectionConfig) => Promise<void>;
  disconnect: () => Promise<void>;
  callTool: (name: string, args: Record<string, unknown>) => Promise<ToolResult>;
}

// packages/stores/src/telemetry/telemetry-store.ts
interface TelemetryState {
  enabled: boolean;
  data: Record<string, DeviceTelemetry>;  // keyed by mcpHostname
  // Actions
  startStreaming: (devices: string[]) => void;
  stopStreaming: () => void;
}

// packages/stores/src/drift/drift-store.ts
interface DriftState {
  results: Record<string, DriftResult>;  // keyed by mcpHostname
  loading: Record<string, boolean>;
  // Actions
  checkDrift: (hostname: string, intendedConfig: string) => Promise<void>;
  clearResults: () => void;
}
```

#### New Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `McpConnectionPanel` | `components/panels/` | Sidebar panel: connection settings, status, available tools |
| `McpConnectionModal` | `components/modals/` | Modal for configuring MCP connection (mode, URL, token) |
| `ConfigDriftPanel` | `components/panels/` | Per-device drift results with inline diff view |
| `TelemetryOverlay` | `components/nodes/` | Canvas overlay: utilization badges on nodes, colored edges |
| `CvpImportModal` | `components/modals/` | Multi-step CVaaS import wizard |
| `McpStatusIndicator` | `components/common/` | Header icon: green/yellow/red/gray connection dot |
| `LabValidationResults` | `components/panels/` | ANTA test results in LabDashboardPanel (enhances existing) |

#### Changes to Existing Components

| Component | Change |
|-----------|--------|
| `Header` | Add `McpStatusIndicator` next to user avatar |
| `LabDashboardPanel` | Add "Validate with network-mcp" section (conditional on connection) |
| `ConfigPreviewModal` | Add "Compare with Live" tab (conditional on connection) |
| `DeviceInspectorModal` | Add "Live State" tab showing real-time device info from network-mcp |
| `ChatWidget` | Network tool routing in edge function (see Section 3.6) |
| Topology node `data` | Add optional `mcpHostname`, `mcpPlatform`, `ipAddress` fields |

### 7. Error Handling & Graceful Degradation

**Core Principle:** Every feature enabled by network-mcp must have a clear, non-broken state when network-mcp is not connected. Users who only need design should never see errors about missing MCP connections.

#### Feature-by-Feature Degradation

| Feature | Connected | Not Connected |
|---------|-----------|---------------|
| **Lab Dashboard** | "Validate Lab" button runs ANTA tests via network-mcp, shows live results | Button text: "Generate ANTA Tests" (export only). Info banner: "Connect network-mcp to run live validation" |
| **Config Preview** | "Compare with Live" tab shows drift diff | Tab hidden. Config preview works normally |
| **Device Inspector** | "Live State" tab shows real-time info | Tab hidden. Static catalog info works normally |
| **Canvas Nodes** | Telemetry badge shows utilization/health | No badge. Nodes render normally |
| **Canvas Edges** | Color/thickness reflects utilization | Default styling. Edges render normally |
| **CVaaS Import** | "Import from CloudVision" available in menu | Menu item hidden |
| **CVaaS Push** | "Push to CVaaS" available in export menu | Menu item hidden |
| **AI Chat** | All 278 tools available | 17 design tools available. Network tool attempts return: "This requires a network-mcp connection. Configure it in Settings > Network Operations." |
| **Header** | Green dot indicator | Gray dot with "Not configured" tooltip, or no indicator if never configured |
| **BOM/Pricing** | No change (design-only feature) | No change |
| **AVD/clab Export** | No change (design-only feature) | No change |
| **NetBox Sync** | No change (independent integration) | No change |

#### Connection Health Monitoring

```typescript
// Express backend: health check loop (every 30s when connected)
async function healthCheckLoop() {
  while (mcpClient.isConnected()) {
    try {
      const result = await mcpClient.callTool('net_server_info', {});
      updateStatus('connected', result.data.version);
    } catch (error) {
      if (isTimeoutError(error)) {
        updateStatus('degraded', 'Connection slow');
      } else {
        updateStatus('error', error.message);
        // After 3 consecutive failures, mark disconnected
        if (consecutiveFailures >= 3) {
          await mcpClient.disconnect();
          updateStatus('disconnected', 'Connection lost');
        }
      }
    }
    await sleep(30_000);
  }
}
```

#### Error Response Contract

All `/api/mcp/*` endpoints follow this error format:

```typescript
interface McpErrorResponse {
  status: 'error';
  code: 'NOT_CONNECTED' | 'TOOL_NOT_FOUND' | 'DEVICE_UNREACHABLE' |
        'AUTH_FAILED' | 'TIMEOUT' | 'MCP_ERROR';
  message: string;        // Human-readable error
  retryable: boolean;     // Whether the client should retry
  fallback?: string;      // Suggestion for offline alternative
}

// Example:
{
  status: "error",
  code: "NOT_CONNECTED",
  message: "network-mcp is not connected",
  retryable: false,
  fallback: "Configure a connection in Settings > Network Operations"
}
```

#### Demo Mode Behavior

When `mcp_demo_mode: true`:
- network-mcp returns realistic mock data for all tools (built-in `NET_DEMO_MODE=true`)
- AristaBuilder shows a yellow "Demo Mode" badge next to the connection indicator
- All features work with mock data — useful for sales demos, training, evaluation
- Mock device hostnames match AristaBuilder's demo scenario names where possible

---

## Implementation Phases

### Phase 1: v1.1 Foundation (Estimated: 6-8 sessions)

| Session | Deliverable |
|---------|------------|
| 1 | `McpClientManager` in Express backend (stdio transport), `/api/mcp/connect`, `/status`, `/disconnect` |
| 2 | `McpConnectionPanel`, `McpConnectionModal`, `McpStatusIndicator` in frontend |
| 3 | Lab validation: `/api/mcp/lab/validate` route, `LabValidationResults` component in `LabDashboardPanel` |
| 4 | Config drift: `/api/mcp/drift/check` route, `ConfigDriftPanel` with monaco-diff |
| 5 | Live topology enrichment: LLDP/BGP queries, telemetry badges on canvas nodes |
| 6 | network-mcp changes: `net_check_drift_inline`, `eos_run_anta_catalog_inline` tools |

**Gate:** Demo mode works end-to-end. Lab validation shows pass/fail. Config drift shows diff. Build + tests pass.

### Phase 2: v1.2 CloudVision (Estimated: 6-8 sessions)

| Session | Deliverable |
|---------|------------|
| 1 | HTTP transport support in `McpClientManager`, auth token forwarding |
| 2 | CVaaS connection settings, `eos_get_cvp_inventory` proxy |
| 3 | `CvpImportModal` with LLDP-based topology discovery |
| 4 | CVaaS push via config sessions (dry-run + commit) |
| 5 | Telemetry overlay: `TelemetryOverlay` component, SSE stream, gNMI polling |
| 6 | network-mcp changes: `eos_get_cvp_device_config`, `eos_push_config_via_cvp`, SSE support |

**Gate:** Import a topology from CVaaS demo data. Push configs with rollback timer. See live telemetry on canvas.

### Phase 3: Unified AI Chat (Estimated: 3-4 sessions)

| Session | Deliverable |
|---------|------------|
| 1 | Tool routing in edge function (design vs network tools) |
| 2 | Progressive discovery integration (`net_discover_tools` → dynamic tool loading) |
| 3 | Cross-domain workflows (diagnose via network-mcp → fix via design tools) |

**Gate:** Single chat conversation spans both design and operations. "Check BGP on spine-01" and "add a spine switch" work in the same conversation.

---

## Appendix: network-mcp Source Analysis

### Repository Structure Summary

```
src/network_mcp/
  server.py           — FastMCP instance, lifespan, auth/RBAC wiring, module loading
  config.py           — NetworkSettings (180+ settings, NET_* env prefix)
  connection.py       — ConnectionManager with multi-vendor driver pool (FIFO eviction, bulkhead isolation)
  drivers/
    base.py           — NetworkDriver protocol (35 normalized getters)
    cisco_iosxe.py    — IosXeDriver (RESTCONF + SSH fallback)
    cisco_nxos.py     — NxosDriver (NX-API + SSH fallback)
    juniper_junos.py  — JunosDriver (NETCONF via scrapli)
  tools/              — 261 tools across 28 modules
    common/           — 70 vendor-agnostic tools (net_* namespace)
      drift.py        — Config drift detection with severity classification
      changes.py      — Change management lifecycle (pending→approved→executing→completed→rolled_back)
      workflows.py    — Multi-vendor diagnostic workflows (BGP diagnosis, interface troubleshoot)
    fabric.py         — Multi-device fabric health, BGP/MLAG fabric checks
    validation.py     — ANTA test execution (wraps anta.runner)
    gnmi.py           — gNMI get/subscribe/capabilities/stream (pygnmi)
    cloudvision.py    — CVP inventory and events (REST API, urllib)
    sessions.py       — EOS config sessions (atomic changes with rollback timer)
    compliance.py     — CIS benchmark compliance checks
  integrations/
    netbox.py         — Bidirectional NetBox sync, cable audit, CMDB update
    servicenow.py     — Incident CRUD, CMDB CI sync, change validation
    ansible.py        — Playbook generation, inventory export
    prometheus.py     — Metrics collection, Grafana dashboard generation
    vault.py          — HashiCorp Vault credential retrieval
  resources/          — 10 MCP resources (net:// URI scheme)
  prompts/            — 15 prompt templates for guided workflows
```

### Key Architectural Properties

1. **All tools return `{"status": "success"|"error", "device": host, "data"|"error": ...}`** — consistent contract
2. **`run_show_command()` handles: host validation, rate limiting, retry, audit, sanitization, cache** — all tools get these for free
3. **Write operations blocked by default** (`NET_READ_ONLY=true`) — explicit opt-in for config changes
4. **CLI injection prevention** built into all command execution (blocks `;`, `|`, backticks, `$()`)
5. **Credential sanitization** on all output (passwords redacted)
6. **Output size limits** prevent context window overflow in AI clients
7. **Demo mode** returns realistic mock data for all 261 tools — no devices needed
8. **Transports**: stdio (default, for Claude Desktop/Code) and Streamable HTTP (for team/remote deployment)
9. **Auth**: OAuth 2.1/JWT for HTTP, API keys, mTLS client certificates — all optional, disabled by default
10. **Plugin system** via Python entry points for third-party drivers, compliance checks, and tools
