# Changelog

All notable changes to network-mcp will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [7.0.0] - 2026-03-01

### Added

#### Authentication & Authorization Hardening
- **Token refresh and revocation** with Redis-backed token store for distributed invalidation
- **API key management**: create, rotate, revoke, and scope-limit API keys with audit trail
- **Mutual TLS (mTLS)** client certificate authentication for machine-to-machine connections
- **OAuth 2.1 PKCE flow** for public clients (SPAs, CLI tools, mobile apps)
- **Per-user and per-tenant rate limiting** with configurable quotas and burst allowances

#### Observability & Monitoring
- **OpenTelemetry tracing** wired through tool execution pipeline with span context propagation
- **Structured error codes** and error catalog (`ErrorCode` enum) for all failure modes
- **SLO framework** with availability (99.9%) and latency (P99 < 500ms) objectives
- **Active configuration exposure** in `/health` endpoint via `get_safe_config()`

#### Reliability & Resilience
- **Graceful shutdown** with in-flight request draining and configurable drain timeout
- **Stale-if-error cache fallback** serving cached data when devices are unreachable
- **Bulkhead isolation** per vendor driver to prevent cascading failures
- **Idempotency keys** for write operations to prevent duplicate configuration changes
- **Hot reload** for safe settings (rate limits, cache TTLs, circuit breaker, log level, timeouts) via `/config/reload` endpoint

#### Security & Compliance
- **Audit log retention policies** with rotation, gzip archival, and cryptographic signing
- **Tenant isolation enforcement** across all tool execution paths
- **Encryption at rest** for cached credentials and sensitive configuration data

#### Change Management
- **Rollback validation** with pre/post state comparison
- **Dry-run mode** for configuration changes
- **Blast radius estimation** before multi-device operations
- **Four-eyes approval workflow** for critical changes

#### Multi-Vendor Drivers
- **IOS-XE RESTCONF fallback** to SSH on failure
- **NX-API response validation** and error normalization
- **Credential rotation support** across all drivers
- **gNMI connection pooling** with health checks and automatic reconnection

#### Kubernetes & Infrastructure
- **Deep health checks**: dependency health, connection pool status, circuit breaker state
- **Container hardening**: non-root execution, read-only filesystem, resource limits
- **Feature flags**: tenant-based feature targeting with JSON-configured `FeatureFlag` model

#### Testing & Quality
- **Chaos engineering test expansion**: network partitions, clock skew, resource exhaustion
- **Load test performance gates**: P99 latency, throughput, and error rate thresholds
- **Coverage closure**: driver coverage tests for all 4 vendor platforms
- 8,422 tests total, 92%+ coverage

#### Operations
- **Operational prompt templates**: `incident_response`, `capacity_planning`, `net_security_audit`
- **Resource tuning guide** for 100/500/1000 device deployments
- **Troubleshooting guide** for common operational issues (circuit breaker, rate limits, auth, memory)
- **Hot config reload**: `reload_settings()` function and `/config/reload` REST endpoint

### Changed
- Tool count increased from 239 to 261 (+22 tools)
- Test count increased from 6,524 to 8,422 (+1,898 tests)
- Coverage increased from 87% to 92%

## [6.0.0] - 2026-02-28

### Added

#### Multi-Vendor Tool Parity
- **20 new NetworkDriver protocol getters**: `get_vlans`, `get_vlan_detail`, `get_interface_counters`, `get_interface_errors`, `get_ospf_neighbors`, `get_route_table`, `get_mac_table`, `get_stp_status`, `get_ntp_status`, `get_environment`, `get_running_config`, `get_startup_config`, `get_acls`, `get_aaa`, `get_snmp`, `get_vrf_list`, `get_vrf_detail`, `get_port_channels`, `get_prefix_lists`, `get_route_maps` -- implemented across all 4 vendor drivers (EOS, IOS-XE, NX-OS, JunOS)
- **58 new vendor-agnostic tools** (`net_*` namespace): VLANs, interface counters/errors, monitoring, environment, trunks, OSPF, route table, ARP, BGP detail, MAC table, STP, port-channels, LLDP detail, ping, traceroute, interface flap detection, VRF, config management (running/startup/diff/save/push), security (ACLs, AAA, SNMP, CoPP), and write tools for interface/routing configuration
- **IOS-XE RESTCONF tools** (`iosxe_*` namespace): 3 new RESTCONF-native tools
- **NX-OS tools expansion** (`nxos_*` namespace): enhanced vPC, FEX, and feature management (6 tools)
- **JunOS-specific tools**: routing instances, firewall filters, commit confirmed

#### Enterprise Infrastructure
- **OpenTelemetry integration**: distributed tracing with context propagation, span creation for tool calls, OTLP export
- **Prometheus `/metrics` endpoint**: native metrics export, SLO definitions, and alerting hooks
- **OWASP MCP Top 10 compliance**: JWKS key rotation support, auth hardening, input validation improvements
- **Enterprise change management**: approval gate workflows with rollback capabilities
- **Cross-vendor configuration drift detection**: diff-based remediation across all 4 vendor platforms
- **Redis-backed distributed state**: distributed rate limiter, circuit breaker, and command cache for multi-replica deployments
- **K8s HA deployment**: multi-replica support, PodDisruptionBudgets, HorizontalPodAutoscaler, NetworkPolicies

#### Testing & Documentation
- **Chaos engineering suite**: fault injection, connection failure simulation, timeout testing
- **Load testing suite**: concurrent tool execution benchmarks, throughput measurement
- **OpenAPI 3.1 spec generation**: auto-generated API documentation from MCP tool definitions
- **API deprecation policy**: versioned tool lifecycle management

### Changed
- Tool count increased from 181 to 239 (+58 vendor-agnostic, +9 vendor-specific)
- NetworkDriver protocol expanded from 6 to 26 normalized getters
- Test count increased from ~4,500 to 6,524
- Coverage maintained at 87.44% (above 85% threshold)

## [5.0.0] - 2026-02-28

### Added
- Complete documentation suite with multi-vendor-first narrative
- All documentation files updated for vendor-agnostic naming (GETTING_STARTED, TOOLS, RESOURCES, PROMPTS, DEPLOYMENT, SECURITY, ARCHITECTURE, CONTRIBUTING, TROUBLESHOOTING, PERFORMANCE)
- Tool namespace guide (`net_*` for vendor-agnostic, `eos_*` for Arista, `nxos_*` for Cisco NX-OS, `iosxe_*` for Cisco IOS-XE, `junos_*` for Juniper JunOS)
- Updated `.well-known/mcp.json` with v5.0.0 metadata
- RBAC scope names standardized to `network:read/write/admin/audit`

### Fixed
- Fixed `scripts/generate_docs.py` broken `SRC_DIR` path
- Fixed stale scope references in `.env.example` and SECURITY.md
- Fixed `GETTING_STARTED.md` using wrong MCP server name
- Fixed eAPI-specific error messages in multi-vendor code paths
- Fixed `verify_ssl` env var mismatch between config and documentation

## [4.1.0] - 2026-02-28

### Fixed
- RBAC scope mismatch: aligned middleware/config/auth to `network:*` scope names
- Stale version strings in FastMCP name, `.well-known/mcp.json`, and observability fallback
- SSL verification defaults: Cisco RESTCONF/NX-API now verify TLS by default
- Plugin dict filter boolean logic bug in `get_plugin_summary()`
- Sanitization wrapping simplified in `run_show_command()`
- Host validation added to resource functions (injection prevention)
- `auth.py` exception handling narrowed to specific JWT errors

### Added
- `@handle_tool_errors` decorator on all driver-direct tools (common/*, cisco/*)
- Configurable keepalive interval (`KEEPALIVE_INTERVAL` setting)
- Driver normalization tests for all 4 vendor getters
- SSH fallback transport tests
- RBAC integration tests (scope hierarchy, denial, tenant isolation)
- Concurrency stress tests (connection pool, rate limiter, circuit breaker)
- Edge case and negative tests (settings, inventory, plugins, validation)
- Server integration tests (HTTP endpoints, CLI parsing, module loading)
- 3 vendor-agnostic prompt templates (`net_troubleshoot_interface`, `net_bgp_health_check`, `net_pre_change_audit`)
- Per-vendor metrics dimension in observability

### Changed
- `Platform` docstring reflects all 4 implemented drivers
- `_KNOWN_OPTIONAL_MODULES` annotated with `ClassVar`

## [3.2.0] - 2026-02-26

### Added
- **RBAC foundation** with scope-based tool access control (`NET_RBAC_ENABLED=true`)
  - `TOOL_SCOPES` mapping: glob-pattern tool names to `network:read`, `network:write`, `network:admin`, `network:audit` scopes
  - Scope hierarchy: `network:admin` implies `network:write` implies `network:read`
  - Predefined roles: viewer, operator, admin, auditor
  - JWT scope extraction from `scope` (OAuth2), `scopes` (list), and `permissions` (Auth0) claims
  - `check_authorization()` for tool-level access control on HTTP transport
- **Basic multi-tenancy**: tenant extraction from JWT claims, per-tenant audit logging
  - `extract_tenant_id()` supports `tenant_id`, `org_id`, and email-based `sub` claims
  - Audit log entries include `tenant_id` field for per-tenant log filtering
  - Request context propagates tenant_id via thread-local storage
- **Plugin system** for third-party drivers and compliance packs
  - Entry point groups: `network_mcp.drivers`, `network_mcp.compliance`, `network_mcp.tools`
  - `discover_plugins()` scans installed packages for registered entry points
  - `load_driver_plugins()` merges plugin drivers into `DRIVER_REGISTRY`
  - `load_compliance_plugins()` appends plugin checks to the compliance framework
  - `load_tool_plugins()` activates third-party tool modules
  - `net_list_plugins` tool for plugin introspection
- Architecture documentation (`docs/ARCHITECTURE.md`) with multi-tenancy design
- Plugin development guide (`docs/PLUGIN-DEVELOPMENT.md`)
- RBAC documentation in `docs/SECURITY.md`

## [3.1.0] - 2026-02-26

### Added
- **Juniper JunOS driver** (`JunosDriver`) with NETCONF transport via scrapli-netconf
- **Multi-vendor workflow tools**: `net_diagnose_bgp_session`, `net_troubleshoot_interface`, `net_validate_fabric_health`, `net_export_report`
- **`juniper` optional extra** (`pip install latticio[juniper]`)
- 4 production drivers: EOS, IOS-XE, NX-OS, JunOS

## [2.4.0] - 2026-02-26

### Added
- **Cisco IOS-XE driver** (`IosXeDriver`) with RESTCONF transport and SSH fallback
- **Cisco NX-OS driver** (`NxosDriver`) with NX-API transport and SSH fallback
- **Cisco-specific tools**: `nxos_get_vpc_status`, `nxos_get_fex_status` for NX-OS platform features
- **"cisco" tool category** in progressive discovery for Cisco-specific tools
- **Cross-vendor normalization tests** -- parameterized test suite verifying `net_*` common tools across EOS, IOS-XE, and NX-OS
- **`cisco` optional extra** (`pip install latticio[cisco]`) -- installs httpx for RESTCONF/NX-API transports
- **Mock data** for IOS-XE and NX-OS testing (mock drivers, show vpc, show fex responses)

### Changed
- `DRIVER_REGISTRY` now includes EOS, IOS-XE, and NX-OS drivers (conditionally loaded when `cisco` extra is installed)
- Common tools (`net_*`) verified working across all 3 vendor platforms
- Tool count increased from 145 to 147 (2 new Cisco-specific tools)

## [2.3.0] - 2026-02-26

### Added
- **`NetworkDriver` protocol** -- vendor-neutral device driver interface with 6 normalized getters (`get_facts`, `get_interfaces`, `get_bgp_summary`, `get_arp_table`, `get_lldp_neighbors`, `get_config`) for consistent data across platforms
- **5 vendor-agnostic common tools** -- `net_get_device_info`, `net_get_interfaces`, `net_get_interface_detail`, `net_get_bgp_summary`, `net_get_lldp_neighbors` work with any platform implementing the `NetworkDriver` protocol
- **Unified inventory model** -- `platform` field on devices selects the correct driver via `DRIVER_REGISTRY`; defaults to `eos` for backward compatibility
- **`Platform` enum** -- `eos`, `iosxe`, `nxos`, `junos` in config for inventory validation and driver dispatch
- **"common" tool category** in progressive discovery for vendor-agnostic tools
- **Dependency compatibility CI job** -- verifies all extras install cleanly and the driver registry is populated

### Changed
- `ConnectionManager._create_driver()` uses `DRIVER_REGISTRY` for platform-based driver selection
- `devices.example.yaml` updated with `platform` field examples and multi-vendor entries
- Tool count increased from 140 to 145 (5 new `net_*` common tools)

## [2.2.0] - 2026-02-26

### Added
- **Export report tool** (`eos_export_report`) -- convert any tool output to Markdown, CSV, or JSON report format for documentation, ticketing systems, or management reviews
- **CIS compliance checking framework** -- `eos_compliance_check` runs 15 CIS Arista EOS Benchmark checks against live devices with pass/fail results, severity, and remediation guidance; `eos_list_compliance_checks` lists available checks without a device connection
- **"compliance" tool category** for security compliance tools (optional module)
- **"utility" tool category** for export and utility tools (core module)
- NetBox integration guide -- drift detection, inventory reconciliation, and topology validation using NetBox as source of truth
- Grafana integration guide -- alert-driven diagnostics correlating Grafana metrics with live device state
- ServiceNow integration guide -- incident-driven workflows with device diagnostics and change validation

### Changed
- Tool count increased from 137 to 140 (3 new tools across 2 new modules)

## [2.1.0] - 2026-02-26

### Added
- **Field filtering** (`fields` parameter) on 8+ high-output tools for token optimization -- return only the fields you need from BGP neighbors, interfaces, ARP tables, OSPF neighbors, and more
- **Pagination** (`limit`/`offset`) on 5+ large result tools with `has_more` metadata for iterating over large datasets (BGP peers, route tables, ARP entries, OSPF neighbors)
- `paginate_list()` utility for list-based pagination (complements existing `paginate_dict()`)
- **Authentication wiring** via FastMCP's built-in `auth=` parameter for HTTP transport
- `/.well-known/mcp.json` server identity endpoint for MCP server discovery
- **`eos_interactive_troubleshoot`** tool with MCP elicitation (`ctx.elicit()`) for guided diagnostic sessions
- **Performance benchmark suite** with pytest-benchmark -- abstraction overhead baseline for driver, cache, and rate limiter subsystems
- **Structured output schemas** on 5 high-use tools (`structured_output=True`): `eos_get_device_info`, `eos_server_health`, `eos_get_bgp_summary`, `eos_diagnose_bgp_session`, `eos_validate_fabric_health`
- **MCP Tasks** (experimental) for async fabric operations -- `eos_fabric_health_async` runs health checks as a background task with progress updates when the client supports MCP Tasks
- Pydantic output models in `schemas.py`: `DeviceInfoOutput`, `ServerHealthOutput`, `BgpSummaryOutput`, `BgpDiagnosticOutput`, `FabricHealthOutput`

### Changed
- Version bumped to 2.1.0
- Tool count: 137 (added `eos_interactive_troubleshoot` and `eos_fabric_health_async`)

### Improved
- Auth module (`auth.py`) extracted with `TokenVerifier` and `JWTTokenVerifier` for cleaner separation
- New config settings: `AUTH_RESOURCE_SERVER_URL`, pagination/filtering parameters
- Test coverage maintained at 88%+ with new tests for field filtering, pagination, elicitation, and benchmarks

## [2.0.0] - 2026-02-25

### Added
- **Namespaced tools**: All tools use the `eos_` prefix (e.g., `eos_get_bgp_summary`) for namespace isolation in multi-server environments
- **Partial status**: Multi-device and workflow tools may return `"status": "partial"` when some sub-operations succeed and others fail
- **DeviceDriver protocol**: Internal device access uses the `DeviceDriver` protocol, enabling mock/test drivers
- **Demo mode**: Set `NET_DEMO_MODE=true` to run the full server with realistic mock data -- no switches required. Supports all core tools with pre-built responses for spine/leaf topologies
- **Workflow tools** (6 new tools in `tools/workflows.py`):
  - `eos_diagnose_bgp_session` -- end-to-end BGP session diagnosis with severity, findings, and recommended actions
  - `eos_troubleshoot_interface_issues` -- interface troubleshooting with link state, error counters, and transceiver analysis
  - `eos_compare_device_configs` -- section-aware config diff between two devices
  - `eos_validate_fabric_health` -- fabric-wide health scoring across all inventory devices
  - `eos_pre_change_snapshot` -- capture device state before changes (stored in-memory with optional file persistence)
  - `eos_post_change_validation` -- compare current state against pre-change snapshot, detecting regressions and improvements
- **Progressive discovery** (3 meta-tools in `tools/meta.py`):
  - `eos_list_tool_categories` -- list available tool categories with descriptions
  - `eos_list_tools_in_category` -- list tools in a category with parameters
  - `eos_load_tool_category` -- dynamically activate tool categories
- **Config sessions** (8 tools in `tools/sessions.py`):
  - Create, stage, diff, commit, commit-with-timer, confirm, abort, and list configuration sessions
- **Config checkpoints** (4 tools in `tools/checkpoints.py`):
  - Create, list, rollback, and delete configuration checkpoints
- **QoS tools** (3 tools in `tools/qos.py`): policy-maps, class-maps, traffic-policy
- **SNMP tools** (2 tools in `tools/security.py`): SNMP configuration and trap receivers
- **ACL write tools** (2 tools in `tools/security.py`): apply/remove ACLs to/from interfaces
- **BGP write tools** (3 tools in `tools/routing.py`): create/delete BGP neighbors, activate address families
- **Command cache**: TTL-based caching with per-host invalidation
- **Circuit breaker**: Per-device health tracking with automatic recovery -- prevents cascading failures when a device is unreachable
- **Resource templates** (5 new resources): `net://devices`, `net://{host}/running-config/{section}`, `net://{host}/interfaces/{interface}`, `net://{host}/bgp/summary`, `net://{host}/vlans`
- **Argument completions**: 6+ MCP completions for host, interface, VLAN, VRF, and session parameters
- **Server introspection tools**: `eos_server_health`, `eos_server_metrics`, `eos_cache_stats`, `eos_cache_clear`
- **Observability**: Optional Prometheus metrics via `prometheus-client` extra
- **Inventory backend abstraction**: `InventoryBackend` protocol for pluggable device inventory sources

### Improved
- **Error messages**: All connection and command errors now include actionable troubleshooting guidance
- **Docstrings**: All tools have standardized docstrings with "When to use" and "Output" sections
- **Test coverage**: 88% coverage with 1,475 tests (up from ~70% with ~550 tests)
- **Error handling decorator**: `@handle_tool_errors` for consistent error handling across tools
- **Rate limiter**: Thread-safe token-bucket implementation with per-device tracking
- **Connection pool**: FIFO eviction with reference counting for connection lifecycle management
- **Fabric tools**: Concurrent multi-device execution via `execute_on_devices` with structured result aggregation
- **EVPN/VXLAN**: Added `eos_get_vxlan_config_sanity`, `eos_get_bgp_evpn_instance`, `eos_get_evpn_route_type_macip`, `eos_get_evpn_route_type_imet`

### Changed
- Tool count increased from 108 to 136
- Resource count increased from 5 to 10
- Module count increased from 16 to 22
- Minimum test coverage gate raised to 85%

## [1.0.1] - 2026-02-18

### Fixed
- **CLI command injection prevention**: All 136 tools now validate host, interface, session, and checkpoint parameters against injection patterns (semicolons, pipes, backticks, subshells)
- **JWT algorithm confusion**: OAuth 2.1 auth enforces RS256-only to prevent HS256 downgrade attacks
- **Rate limiter TOCTOU race**: Token bucket uses atomic check-and-consume to prevent concurrent request bypass
- **Connection pool exhaustion**: LRU eviction policy with configurable `max_connections` (default 50) prevents unbounded connection growth
- **gNMI default security**: `GNMI_INSECURE` now defaults to `false`
- **Config validation**: `transport` restricted to `Literal["https", "http"]`, `log_level` restricted to valid log levels

### Added
- **Dependency version pinning**: All dependencies now have upper bounds (e.g., `pyeapi>=1.0,<2.0`)
- **Ruff B/UP rules**: Bugbear and pyupgrade linting rules added
- 37 new tests (549 total): resources, prompts, config, connection manager edge cases, interface error paths

## [1.0.0] - 2026-02-18

### Added
- README with architecture diagram, competitive comparison, full configuration reference, and multi-client setup guides
- Tool documentation generator (`scripts/generate_docs.py`) producing `docs/TOOLS.md` with all 136 tools
- MCP directory submission metadata for mcp.so, mcphub.com, pulsemcp.com, glama.ai
- Code of Conduct (Contributor Covenant v2.1)

## [0.5.0] - 2026-02-18

### Added
- **gNMI telemetry** (3 tools): `gnmi_get`, `gnmi_subscribe_once`, `gnmi_capabilities` via pygnmi optional dependency
- **gNMI connection manager**: per-request client creation, gRPC error mapping, OpenConfig output normalization
- **Streamable HTTP transport**: `--transport streamable-http` CLI flag for remote deployments
- **OAuth 2.1 authentication**: JWT verification for HTTP transport with configurable scopes
- **Deployment templates**: nginx reverse proxy config, systemd service unit

## [0.4.0] - 2026-02-18

### Added
- **Multi-device fabric tools** (5 tools): `list_inventory`, `fabric_health_summary`, `fabric_bgp_status`, `fabric_mlag_status`, `compare_configs`
- **Concurrent execution engine**: `executor.py` with `asyncio.to_thread()`, semaphore-limited parallelism, per-device timeout, progress reporting
- **Extended inventory schema**: `role`, `groups`, `tags`, `mlag_peer` fields for group/tag-based device selection
- **Thread-safe connection manager**: lock protection for concurrent access
- **ANTA validation tools** (5 tools, optional): `validate_bgp`, `validate_mlag`, `validate_interfaces`, `validate_system`, `run_anta_tests`
- **ANTA graceful degradation**: tools return install instructions when anta package is missing

## [0.3.0] - 2026-02-17

### Added
- **Production hardening**: SecretStr for credentials, config output sanitization (redacts sensitive data), output injection defense, output size limits (100KB), tenacity retry logic, per-device rate limiting, configurable timeouts, JSON audit logging
- **Lifespan management**: async context manager for startup/shutdown
- **Config session tools** (8 tools): create, diff, add, commit, commit-with-timer, confirm-timer, abort, list sessions
- **Config checkpoint tools** (4 tools): create, list, rollback, delete checkpoints
- **Safe config change prompt**: workflow with risk classification and commit timer guidance
- **BFD tools** (2 tools): `get_bfd_peers`, `get_bfd_peer_detail`
- **Event Monitor tools** (3 tools): `get_event_monitor`, `get_mac_move_events`, `get_route_change_events`
- **IPv6 support**: `address_family` parameter on 7 routing tools
- **CI/CD**: GitHub Actions for lint, test (Python 3.11/3.12/3.13), build, coverage, PyPI, Docker
- **Dockerfile**: multi-stage build, non-root user, config volume
- **Module toggle**: `NET_DISABLED_MODULES` env var
- Community files: CONTRIBUTING.md, issue templates, PR template

### Changed
- Tool count increased from 78 to ~95 (17 new tools)

## [0.2.0] - 2026-02-17

### Added
- Shared `helpers.py` with `run_show_command()`, input validation, tool annotation constants
- Tool annotations on all tools (`readOnlyHint`/`destructiveHint`/`idempotentHint`)
- Input validation for host, VLAN ID, and interface name parameters
- Read-only mode (`NET_READ_ONLY=true` default) and command deny-list
- **EVPN/VXLAN Module** (9 tools): VXLAN interface, VTEP, VNI, address-table, config-sanity, BGP EVPN
- **Security Module** (6 tools): ACLs, AAA, RADIUS, TACACS, CoPP, eAPI status
- **VRF Module** (2 tools): VRF listing and detail
- **Write Operations** (9 tools): VLAN CRUD, interface admin, config push, config save
- New switching tools (3): MLAG config-sanity, MLAG interfaces, trunk interfaces
- New device tools (3): hardware capacity, hardware drops, TerminAttr status
- New routing tools (2): VRRP, VARP
- 5 new prompts (8 total): daily health, post-change validation, security audit, MLAG troubleshoot, EVPN health
- 2 new resources (5 total): device health summary, interface summary
- 245 tests covering all tools, MCP protocol, annotations, and validation

### Changed
- All tools refactored to use `run_show_command()` helper
- Improved tool descriptions for LLM comprehension

## [0.1.0] - 2026-02-16

### Added
- Initial release with 43 read-only tools
- Device, interface, VLAN, routing, switching, monitoring, config management, troubleshooting modules
- 3 MCP resources (version, running-config, inventory)
- 3 MCP prompts (troubleshoot_interface, bgp_health_check, pre_change_audit)
- Connection management with pyeapi (single device and inventory modes)
- Pydantic settings from .env
