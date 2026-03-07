# Roadmap

This document outlines the direction of network-mcp. It is organized by version and updated at each major release. For detailed changelogs, see [CHANGELOG.md](CHANGELOG.md).

Items here represent goals and themes, not commitments. Priorities may shift based on community feedback, security needs, and ecosystem changes. Have an idea? Open an [issue](https://github.com/latticio/network-mcp/issues) or start a [discussion](https://github.com/latticio/network-mcp/discussions).

---

## Current — v8.x (Stable)

Released 2026-03-07. Focus: **usability, lab support, and AI networking**.

- [x] Containerlab integration with 5 lab lifecycle tools and inventory auto-population
- [x] Interactive onboarding CLI (`latticio init`) with guided wizard
- [x] Error messages with remediation hints (12 categories)
- [x] AI fabric tools — RoCEv2/RDMA health, ECN/PFC monitoring, GPU path validation
- [x] SONiC NOS driver (5th vendor platform)
- [x] Tool playground / REPL (`latticio playground`)
- [x] Plugin SDK with MockDriverFactory and ToolTestHarness
- [x] Vendor-agnostic config template engine (17 intents)
- [x] Auto-generate vendor tools from NetworkDriver protocol
- [x] Selective vendor loading (`NET_VENDORS`, `NET_ENABLED_MODULES`)
- [x] HTTP session resumption with TTL-based session store
- [x] Performance benchmark suite with CI report generation
- [x] 297 tools, 9,070 tests, 5 vendor drivers

## Planned — v9.0.0

Theme: **ecosystem expansion and topology intelligence**.

### New vendor drivers
- [ ] **Palo Alto PAN-OS driver** — extend multi-vendor coverage to firewalls (PAN-OS XML/REST API)
- [ ] **Fortinet FortiOS driver** — FortiGate firewall support via REST API

### Topology and context
- [ ] **Topology-aware tools** — understand device relationships (spine-leaf, ECMP paths, redundancy groups) to answer "show me the path from server-01 to the internet"
- [ ] **Change impact analysis** — before pushing config, analyze affected devices and services based on topology and protocol adjacencies
- [ ] **Multi-site topology visualization** — MCP resources that expose network maps for AI spatial reasoning

### AristaBuilder integration

[AristaBuilder](docs/aristabuilder-integration/) is a companion visual topology design app. It connects to network-mcp as an MCP client to close the loop between design and operations. AristaBuilder is optional — network-mcp works standalone. These changes make network-mcp a better backend for design tools without adding complexity.

See [docs/aristabuilder-integration/INTEGRATION-ARCHITECTURE.md](docs/aristabuilder-integration/INTEGRATION-ARCHITECTURE.md) for full architecture. See [docs/aristabuilder-integration/HANDOFF-TO-ARISTABUILDER.md](docs/aristabuilder-integration/HANDOFF-TO-ARISTABUILDER.md) for the AristaBuilder-side scope.

#### Inline parameters for existing tools
- [ ] **`net_check_drift` — add `golden_config: str` parameter** — accept intended config as a string instead of requiring a file or NetBox lookup. When provided, diff against the device's running config. This lets AristaBuilder send its generated EOS config directly for drift detection
- [ ] **`eos_run_anta_tests` — add `catalog_yaml: str` parameter** — accept an ANTA test catalog as a YAML string instead of requiring a file path. AristaBuilder generates ANTA catalogs from topology and needs to run them inline

#### New tools
- [ ] **`net_build_topology_from_lldp`** — query LLDP neighbors on a list of devices and return a graph structure (`{nodes: [...], edges: [...]}`) representing discovered topology. Signature: `(hosts: list[str]) -> dict`. Useful for importing live topologies into design tools or validating cabling against intent

#### Tool discovery enhancements
- [ ] **Tool category metadata** — add `x-tool-group` annotation to all tools (e.g., `routing`, `switching`, `monitoring`, `config`, `validation`). Derived from existing module structure. Enables progressive discovery: clients call `net_discover_tools(category="routing")` to load tool groups on demand instead of receiving 297 tools at once
- [ ] **Interface speed in LLDP response** — add link speed to `net_get_lldp_neighbors` output so clients can render edges with correct bandwidth

#### Infrastructure
- [ ] **`/health` endpoint** — simple health check for HTTP transport mode. Returns server version, uptime, connected device count, and module status. Used by AristaBuilder's Express backend to monitor the connection

### Other integrations
- [ ] **Nautobot integration** — source-of-truth support alongside NetBox for organizations using Nautobot
- [ ] **Terraform state reader** — correlate Terraform-intended infrastructure with actual device state
- [ ] **Git-backed config store** — automatic config versioning (like oxidized/rancid) with tools to diff any two points in time

### AI-assisted operations
- [ ] **Runbook execution engine** — define runbooks as YAML sequences of tool calls with decision points; AI follows the runbook but can deviate on unexpected findings
- [ ] **Prompt engineering for network context** — pre-built system prompts that teach the AI about specific topologies, naming conventions, and operational procedures

## Future — v10.0.0+

Longer-term ideas under consideration. Not yet committed.

### Platform expansion
- [ ] **Wi-Fi and wireless controller support** — Cisco WLC, Aruba, Mist
- [ ] **SD-WAN visibility** — Cisco Viptela/SD-WAN, Arista WAN routing
- [ ] **EVE-NG and GNS3 lab support** — lab integration beyond Containerlab

### Advanced automation
- [ ] **Event-driven automation** — webhook/syslog listeners that trigger MCP tool workflows automatically
- [ ] **Natural language policy engine** — translate intent ("no external BGP peers on leaf switches") into cross-vendor validation rules
- [ ] **Historical trending** — store tool results over time (SQLite/Prometheus) to answer "has this BGP peer been flapping?" or "when did interface errors start?"
- [ ] **MCP server federation** — chain multiple MCP servers for cross-domain automation (network + cloud + security)

### Community
- [ ] **Compliance-as-code library** — community-contributed compliance profiles (CIS, NIST, DISA STIG)
- [ ] **Tool contribution marketplace** — registry or curated collection for sharing community-built tools and plugins
- [ ] **"Works with network-mcp" validation** — certification badges for vendor integrations tested against real hardware

## Completed

| Version | Theme | Date | Highlights |
|---------|-------|------|------------|
| v8.0.0 | Usability, labs, AI networking | 2026-03-07 | Containerlab, SONiC, AI fabric, onboarding CLI, playground, plugin SDK |
| v7.0.0 | Enterprise hardening | 2026-03-01 | Auth, observability, reliability, change management |
| v6.0.0 | Production readiness | 2026-02-28 | 58 new tools, OTel, OWASP, Redis HA, K8s |
| v5.0.0 | Naming consistency | 2026-02-26 | `EOS_*` → `NET_*`, `eos://` → `net://`, multi-vendor branding |
| v4.1.0 | Code review remediation | 2026-02-24 | RBAC alignment, driver normalization, test hardening |
| v4.0.0 | Multi-vendor, multi-session | 2026-02-22 | 4 vendor drivers, plugins, RBAC, rename to `network-mcp` |

See [CHANGELOG.md](CHANGELOG.md) for full release details.
