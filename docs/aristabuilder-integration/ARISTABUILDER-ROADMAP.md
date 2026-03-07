# AristaBuilder / NetworkCanvas — Roadmap

> Visual network topology design and Network-as-Code platform.
> This document tracks completed milestones, current work, and planned features.

## Versioning

AristaBuilder uses [Semantic Versioning](https://semver.org/):

```
MAJOR.MINOR.PATCH

MAJOR  — breaking changes to topology format, store schema, or export APIs
MINOR  — new features (backward-compatible)
PATCH  — bug fixes, test improvements, refactors (no new features)
```

Each minor version maps to a runner plan file at `~/.latticio/runner/plans/aristabuilder/`.
Versions are tagged in git after the plan's GATE passes.

---

## Version History (Completed)

### v0.1.0 — Foundation (2025)

- Visual topology canvas (React Flow, drag-and-drop, undo/redo, auto-save)
- Arista device catalog with switches, optics, cables
- 16 topology templates (datacenter, campus, WAN)
- BOM generation with CSV/Excel export
- AVD and ContainerLab exports
- Folder organization with Supabase cloud sync

### v0.2.0 — Multi-Vendor & Chat (Jan 2026)

- 7-vendor support (Arista, Cisco, Juniper, Nokia, Broadcom, Dell, HPE)
- Vendor-specific port naming (display varies, IDs invariant)
- 561+ device catalog (~9,000 lines)
- AI chat widget with 17 tool functions (BYOK via Supabase edge function)

### v0.3.0 — Auth, Legal & Security (Jan–Feb 2026)

- Supabase auth (email/password + Google OAuth)
- Admin dashboard with user approval workflow
- Legal framework (ToS, Privacy Policy, versioned acceptance)
- Route guards (`ProtectedRoute`, `AdminRoute`)
- Profile system (account, password, LLM config, teams)
- Security hardening (shell injection, webhook validation, input sanitization)

### v0.4.0 — Code Quality & Testing (Feb 2026)

*Plans A–F, 20 sessions*

- Store refactor: `topology-store.ts` 4,883 → 1,721 lines (3 composed slices)
- RequirementsWizard: 1,598 → 475 lines (−70%)
- Code splitting, lazy loading, component extraction
- 2,705+ frontend tests, 153 backend tests, 47 Playwright E2E tests
- CI pipeline (GitHub Actions), structured pino logging

### v0.5.0 — AI Datacenter (Feb 2026)

*Plans G–K + N, 34 commits*

- NVIDIA DGX/HGX/GB300 NVL72, AMD MI300X, Intel Gaudi catalog entries
- GPU NIC configuration (ConnectX-7, BlueField-3)
- AI topology generators (rail-optimized, GPU pod, AI CLOS, storage fabric, DSF)
- Rack visualization with drill-down and breadcrumb navigation
- Power & cooling panel (PUE, BTU/hr, PDU circuits, cooling recommendations)
- Device inspector modal (4 tabs: Ports, Specs, Docs, Notes)

### v0.6.0 — AI Design Studio & Monorepo (Feb 2026)

*Plan M, 21 sessions*

- Turborepo + pnpm monorepo: 3 apps + 11 shared packages
- Apps: `network-canvas`, `ai-design-studio` (Pixi.js), `dashboard`
- Packages: `auth`, `canvas-core`, `device-catalog`, `export-utils`, `shared-types`, `stores`, `theme`, `ui`, `tailwind-preset`, `config-eslint`, `config-typescript`
- Store factory pattern in `@aristabuilder/stores` (topology logic extracted)
- Design system foundation (tokens, typography, surfaces)

### v0.7.0 — Network Design Features (Feb 2026)

- **Validation engine** — 12 rules (orphan devices, duplicate names, missing uplinks, MLAG completeness, VLAN/VNI conflicts, subnet conflicts, asymmetric spine connectivity, minimum topology)
- **EOS config generator** — Full config generation per device with ConfigPreviewModal
- **Network services** — VLAN/VRF/tenant design panel with VNI mapping, EVPN support
- **IP pool management** — CIDR pools, allocation tracking, VRF association, reserved ranges
- **Licensing panel** — Per-device EOS feature licensing, CloudVision, CUE, WAN tiers
- **Pricing panel** — Cost breakdown by category

### v0.8.0 — Export Suite (Feb 2026)

- **AVD 4.x export** — L3LS-EVPN + L2LS, fabric vars, inventory, network services YAML
- **NetBox integration** — Full import/export/sync/diff with settings modal, webhook support
- **ANTA test generation** — Auto-generated test catalogs from topology
- **ZTP export** — Zero-touch provisioning configs and scripts
- **Runbook generation** — Deployment guides with cabling diagrams, verification commands
- **Cabling export** — CSV/Excel with breakout handling
- **Image export** — PNG/SVG with resolution options

### v0.9.0 — Collaboration & Labs (Feb 2026)

- **Team management** — Team creation, member invitation, roles, sharing
- **Lab deployment** — LabDeployModal + LabDashboardPanel + LabGuideModal
- **Demo scenarios** — Pre-built demo topologies (DemoScenariosModal)
- **Analytics panel** — Topology metrics, device distribution, link analysis
- **Customer panel** — Customer project details and metadata
- **17 panels, 24 modals** — Full sidebar and modal system

---

## Current — v0.9.x (Stabilization)

- Generator bug fixes (port collisions, oversubscription calculations)
- Export hardening (runbook PDF blank pages, edge cases)
- Test coverage expansion

---

## Planned Versions

### v1.0.0 — Production Readiness

The 1.0 release: polish, missing UI gaps, and production deployment confidence.

| Phase | Feature | Sessions | Description |
|-------|---------|----------|-------------|
| 1 | **Config diff view** | 2 | Side-by-side config diff on topology changes; snapshot before/after in config store |
| 2 | **AVD playbooks & docs** | 2 | Generate AVD YAML structure (inventory, group_vars, playbooks). Playbook *execution* delegates to network-mcp's Ansible integration in v1.1+ |
| 3 | **BOM Excel improvements** | 2 | Multi-sheet workbook (Summary, Hardware, Optics, Licensing); conditional formatting |
| 4 | **Validation auto-run** | 2 | Debounced auto-validation on topology changes; error badges on canvas nodes; fitView on click |
| 5 | **Quote PDF export** | 2-3 | Branded PDF quote documents with topology diagram, itemized BOM, customer name, terms |
| 6 | **draw.io (.drawio) export** | 1 | Export topology as .drawio XML; well-documented format, widely used by network engineers |

### v1.1.0 — network-mcp Connection & Lab Validation

> Connects AristaBuilder to [network-mcp](https://github.com/latticio/network-mcp) for live device operations.
> network-mcp is optional — AristaBuilder works fully standalone without it.
> All features degrade gracefully when network-mcp is not connected (buttons hidden or disabled with clear messaging).

Transport strategy: stdio for v1.1 (Express spawns network-mcp as child process, zero config), Streamable HTTP from v1.2+ (network-mcp as independent service, scales horizontally). Express backend is the proxy in both modes — browser never connects to network-mcp directly.

Requires new tools in network-mcp: `net_check_drift_inline` (accept golden config as string), `eos_run_anta_catalog_inline` (accept ANTA catalog as YAML string). See [Integration Architecture](docs/planning/INTEGRATION-ARCHITECTURE.md) for full data flow specifications.

| Phase | Feature | Sessions | Description |
|-------|---------|----------|-------------|
| 1 | **MCP client & backend proxy** | 2 | `McpClientManager` in Express backend (stdio transport); `/api/mcp/connect`, `/status`, `/disconnect` routes; health check loop |
| 2 | **Connection UI** | 2 | `McpConnectionPanel` (sidebar), `McpConnectionModal` (settings), `McpStatusIndicator` (header); demo mode toggle (`NET_DEMO_MODE=true`) with yellow badge |
| 3 | **Lab validation loop** | 2-3 | `/api/mcp/lab/validate` route orchestrates parallel ANTA calls per device; `LabValidationResults` component in LabDashboardPanel with pass/fail/skip per test; 180s timeout with progress indicator |
| 4 | **Config drift detection** | 2 | `/api/mcp/drift/check` sends intended config (from EOS generator) to network-mcp; `ConfigDriftPanel` with monaco-diff-editor showing severity-classified diffs (critical/warning/info); "Fix in Design" and "Accept Live" actions |
| 5 | **Live topology enrichment** | 2 | Query LLDP neighbors, BGP state, interface counters via network-mcp; telemetry badges on canvas nodes; colored edges by utilization; data cleared on disconnect |
| 6 | **network-mcp tool additions** | 1 | Contribute `net_check_drift_inline` and `eos_run_anta_catalog_inline` tools upstream; add tool category metadata for progressive discovery |

**Gate:** Demo mode works end-to-end. Lab validation shows pass/fail. Config drift shows diff. All features hidden/disabled when disconnected. Build + tests pass.

### v1.2.0 — CloudVision & Unified AI Chat (via network-mcp)

> Leverages network-mcp's existing CloudVision tools and gNMI driver — no CVaaS client built in AristaBuilder.
> Adds Streamable HTTP transport for team/production deployments.

Requires new tools in network-mcp: `eos_get_cvp_device_config`, `eos_push_config_via_cvp`, `net_build_topology_from_lldp`, SSE streaming for telemetry.

| Phase | Feature | Sessions | Description |
|-------|---------|----------|-------------|
| 1 | **HTTP transport & auth** | 2 | Streamable HTTP support in `McpClientManager`; OAuth 2.1 token forwarding (Supabase JWT -> network-mcp); connection settings for remote network-mcp |
| 2 | **Import from CVaaS** | 2-3 | `CvpImportModal`: fetch inventory via `eos_get_cvp_inventory`, fuzzy-match models to catalog, discover topology via LLDP, auto-layout with dagre/elk |
| 3 | **Push to CVaaS** | 2-3 | Push configs via EOS config sessions (`eos_create_config_session` -> `eos_commit_config_session_timer`); per-device diff review; rollback timer safety; dry-run mode |
| 4 | **Telemetry overlay** | 2 | `TelemetryOverlay` component; SSE stream from Express (`/api/mcp/telemetry/stream`); gNMI polling every 30s; node health badges; edge utilization coloring |
| 5 | **Unified AI chat** | 2-3 | Tool routing in edge function: `net_*`/`eos_*` tools proxy to network-mcp, design tools execute locally; progressive discovery via `net_discover_tools` to manage 278+ combined tools; session-scoped tool cache |
| 6 | **network-mcp tool additions** | 1 | Contribute `eos_get_cvp_device_config`, `eos_push_config_via_cvp`, `net_build_topology_from_lldp`; add SSE streaming for HTTP transport; add `/health` endpoint |

**Gate:** Import topology from CVaaS demo data. Push configs with rollback timer. See live telemetry on canvas. Single chat conversation spans both design and operations. Build + tests pass.

### v1.3.0 — Additional Exports & Imports

> Lower effort than design versioning, expands addressable market. Shipped before v1.4 to maximize user base growth.

| Phase | Feature | Sessions | Description |
|-------|---------|----------|-------------|
| 1 | **SONiC config export** | 2 | Generate SONiC `config_db.json` from topology; SONiC reaching 10%+ market share |
| 2 | **ContainerLab YAML import** | 1-2 | Parse clab YAML into AristaBuilder topology; reverse of existing export |
| 3 | **AVD 5.x/6.x alignment** | 2 | Strict schema validation, list-of-dicts format, PyAVD server-side validation |

### v1.4.0 — AI Studio Enhancements & RoCEv2

> RoCEv2/lossless fabric validation is purely design-time (no network-mcp dependency) and moved here from the original v1.5 because it can ship independently.
> AI Studio chat benefits from unified chat infrastructure built in v1.2.

| Phase | Feature | Sessions | Description |
|-------|---------|----------|-------------|
| 1 | **RoCEv2/lossless fabric validation** | 2-3 | PFC/ECN config validation (ECN thresholds < PFC thresholds), DCQCN parameter checking, buffer headroom adequacy; no competitor does this at design time |
| 2 | **Device catalog update** | 2 | NVIDIA Rubin VR200, NVL144, ConnectX-9, BlueField-4, AMD MI400, Pensando Vulcano 800G |
| 3 | **AI Studio chat integration** | 2-3 | Port NetworkCanvas chat to AI Studio; leverages unified chat infrastructure from v1.2 |
| 4 | **1.6T optics & UALink** | 1-2 | OSFP-XD 1.6T transceivers; UALink interconnect type for AMD MI400 scale-up |
| 5 | **CIS compliance at design time** | 1 | Send generated EOS configs to network-mcp's CIS compliance tools for pre-deployment validation; optional (requires v1.1 connection) |

### v1.5.0 — Design Versioning & Diff

> Moved after exports/imports and AI Studio because it's a high-effort infrastructure investment.
> Prerequisite for v2.1 Enterprise (project lifecycle states and approval workflows need version history).

| Phase | Feature | Sessions | Description |
|-------|---------|----------|-------------|
| 1 | **Version snapshots** | 2-3 | Git-like commits for topology designs; snapshot on save; version history panel |
| 2 | **Visual diff** | 2-3 | Side-by-side topology comparison; highlight added/removed/changed devices and links |
| 3 | **Branches** | 2 | Fork designs for "what-if" scenarios; merge approved changes back |

### v2.0.0 — Real-Time Collaboration

| Phase | Feature | Sessions | Description |
|-------|---------|----------|-------------|
| 1 | **CRDT infrastructure** | 3-4 | Yjs integration with Supabase Realtime; topology state as shared document |
| 2 | **Live presence** | 2 | Cursors, user avatars, selection highlights for other users |
| 3 | **Commenting** | 2 | Comments on devices/links; @mentions; notification system |
| 4 | **Conflict resolution** | 2 | Handle simultaneous edits; merge strategies for topology changes |

### v2.1.0 — Enterprise

> Leverages network-mcp's existing RBAC, audit, and ITSM patterns where possible.
> **Depends on v1.5** — project lifecycle states and approval workflows require design versioning (version snapshots, branches).

| Phase | Feature | Sessions | Description |
|-------|---------|----------|-------------|
| 1 | **SSO/SAML** | 2-3 | Enterprise auth via SAML/OIDC providers (Supabase enterprise plan) |
| 2 | **Project RBAC** | 2 | Project-level roles (Viewer/Designer/Approver/Admin); follows network-mcp's scope patterns |
| 3 | **Project lifecycle & approvals** | 2 | Draft -> Review -> Approved -> Deployed states; approval gates between stages |
| 4 | **Change management (via network-mcp)** | 2 | Design approval triggers Change Request in network-mcp; ServiceNow/Jira delegation |
| 5 | **Audit logging** | 2 | Track who changed what, when; exportable audit trail; follows network-mcp's audit patterns |
| 6 | **API & webhooks** | 2-3 | REST API for topology CRUD; webhook notifications for integrations |

### v2.2.0 — Capacity Planning

| Phase | Feature | Sessions | Description |
|-------|---------|----------|-------------|
| 1 | **Port utilization** | 2 | Track used/available ports per device; utilization heatmap on canvas |
| 2 | **Bandwidth modeling** | 2-3 | Traffic flow estimation; bandwidth utilization forecasting per link |
| 3 | **Growth scenarios** | 2 | "What-if" capacity analysis; upgrade recommendations based on headroom |

### v2.3.0 — Additional Exports

| Phase | Feature | Sessions | Description |
|-------|---------|----------|-------------|
| 1 | **Visio (.vsdx) export** | 2 | Export topology as .vsdx for enterprise stakeholder presentations |
| 2 | **Nautobot export** | 2 | Growing SoT alternative to NetBox; lower priority than NetBox (already supported) |
| 3 | **Customer portal** | 3-4 | Self-service design portal; quote requests; shared lab access |

---

## network-mcp Synergy

[network-mcp](https://github.com/latticio/network-mcp) is a companion MCP server (Python, 261 tools, 4 vendor drivers) for live network device operations. AristaBuilder connects to it optionally — design works fully standalone.

**Delegate to network-mcp (don't rebuild):**
- CloudVision/CVaaS API client (`tools/cloudvision.py`)
- ServiceNow/Jira ITSM integration (`integrations/servicenow.py`)
- Live device config retrieval (`tools/common/config.py`)
- ANTA test execution on live devices (`tools/validation.py`)
- LLDP/BGP/OSPF state queries (`tools/common/routing.py`)
- Config drift detection engine (`tools/common/drift.py`)
- Ansible playbook execution (`integrations/ansible.py`) — AristaBuilder generates AVD YAML, network-mcp runs playbooks
- CIS compliance checking (`tools/compliance.py`) — validate generated configs at design time
- Prometheus metrics export (`integrations/prometheus.py`) — live device metrics for telemetry overlay
- Config session management (`tools/sessions.py`) — atomic config push with rollback timer
- Credential management — Vault (`integrations/vault.py`), env vars, devices.yaml. AristaBuilder never stores device passwords
- Circuit breakers, rate limiting, audit logging

**Build in AristaBuilder (design-time domain):**
- Visual topology canvas, device catalog, BOM/pricing
- IaC generation (AVD, ContainerLab, ANTA tests, ZTP, SONiC)
- Config diff UI, validation rules, quote PDF
- Design versioning, project lifecycle states
- MCP connection UI and result visualization
- RoCEv2/lossless fabric validation (design-time, no network-mcp dependency)
- Demo mode fallback for all network-mcp features (realistic mock data when disconnected)

**New tools needed in network-mcp** (contributed upstream by AristaBuilder team):
- `net_check_drift_inline` — accept golden config as string parameter (v1.1)
- `eos_run_anta_catalog_inline` — accept ANTA catalog as YAML string (v1.1)
- `net_build_topology_from_lldp` — query LLDP on multiple devices, return graph (v1.2)
- `eos_get_cvp_device_config` — pull per-device config from CVaaS (v1.2)
- `eos_push_config_via_cvp` — push config via CVaaS Change Control (v1.2)

See [Convergence Report](docs/planning/CONVERGENCE-REPORT-NETWORK-MCP.md) and [Integration Architecture](docs/planning/INTEGRATION-ARCHITECTURE.md) for full analysis.

---

## Version Decision Guide

| Change Type | Version Bump | Example |
|-------------|-------------|---------|
| New feature, backward-compatible | Minor (0.X.0) | Add CloudVision panel |
| Bug fixes, test additions, refactors | Patch (0.0.X) | Fix generator port collisions |
| Breaking topology format change | Major (X.0.0) | Change node/edge schema |
| Breaking export format change | Major (X.0.0) | AVD output structure change |
| Breaking store API change | Major (X.0.0) | Remove/rename store actions |

---

## Runner Integration

Each planned version maps to a plan file:

```
~/.latticio/runner/
  projects/aristabuilder/templates/
    build.md          # Builder prompt template
    review.md         # Reviewer prompt template
    fix.md            # Fixer prompt template
  plans/aristabuilder/
    v1.0-phases.json  # Production Readiness
    v1.1-phases.json  # network-mcp Connection & Lab Validation
    v1.2-phases.json  # CloudVision & Unified AI Chat
    ...
```

Run a version:
```bash
~/.latticio/runner/orchestrator.sh \
  --project ~/Documents/AristaBuilder \
  plans/aristabuilder/v1.0-phases.json
```

---

## Detailed Planning Documents

- [docs/planning/ROADMAP.md](docs/planning/ROADMAP.md) — Original technical roadmap (Jan 2026, mostly superseded)
- [docs/planning/STRATEGIC_PLAN.md](docs/planning/STRATEGIC_PLAN.md) — Business strategy, monetization, go-to-market
- [docs/planning/ROADMAP-RESEARCH-2026-03.md](docs/planning/ROADMAP-RESEARCH-2026-03.md) — March 2026 market research, competitor analysis, feature gaps
- [docs/planning/CONVERGENCE-REPORT-NETWORK-MCP.md](docs/planning/CONVERGENCE-REPORT-NETWORK-MCP.md) — network-mcp integration analysis and synergy map
- [docs/planning/INTEGRATION-ARCHITECTURE.md](docs/planning/INTEGRATION-ARCHITECTURE.md) — Detailed integration architecture (transport, data flows, shared types, required changes)
- [docs/planning/CONTAINERLAB_INTEGRATION.md](docs/planning/CONTAINERLAB_INTEGRATION.md) — ContainerLab deep integration plan
- [docs/ai-datacenter-research/](docs/ai-datacenter-research/) — AI datacenter research (13 docs + master plans)
