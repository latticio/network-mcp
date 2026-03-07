# Network MCP — Research, Hardening & Multi-Vendor Expansion Session

## Context

We just completed a successful overnight integration test against a live 6-node Arista cEOS spine-leaf fabric (2 spines, 4 leaves) running in containerlab on an OrbStack VM (`orb run -m clab`). All 265 integration tests pass at 100% in ~20 seconds. The test suite lives in `tests/integration/` across three files:

- `test_mcp_protocol_live.py` — 43 tests: full MCP JSON-RPC protocol via official SDK client
- `test_systematic_coverage.py` — 149 tests: all 13 tool categories across 6 nodes
- `test_advanced_scenarios.py` — 17 tests: concurrency, rapid sequential, config change detection, stress, error recovery

The cEOS lab is still running. All commands execute inside the VM: `orb run -m clab bash -c "cd /Users/jacobfehn/Documents/network-mcp && <command>"`

## Mission

This is a **research and design session** — equal parts investigation and implementation. The goal is to understand how this app will be used in the real world, identify every gap and weakness, and produce a concrete plan (with initial implementations) for making network-mcp bulletproof across all vendors and scenarios.

---

## Part 1: Implement Learnings from Overnight Test

These are confirmed bugs and gaps discovered during integration testing. Fix all of them.

### 1.1 pyeapi Thread-Safety (Critical)
**Problem**: `ConnectionManager` shares pyeapi connections across threads. Under concurrent load (6+ threads), we get `OSError: [Errno 9] Bad file descriptor`. Our tests work around this with a ≥50% success threshold.
**Fix**: Implement per-thread connection instances or a proper thread-safe connection pool with checkout/return semantics. The pool in `connection.py` uses FIFO eviction but doesn't isolate connections per thread.
**Acceptance**: Concurrent load tests should achieve ≥95% success rate with 6 workers.

### 1.2 Interface Description Clearing (Bug)
**Problem**: `net_configure_interface_description` rejects empty string `""` with `"description cannot be empty"`. There's no way to clear a description.
**Fix**: Accept `""` and translate to `no description` command on EOS. Other vendors may have different syntax.
**Test**: Add unit test + update integration test to verify clearing works.

### 1.3 LLDP Hostname Truncation on cEOS
**Problem**: cEOS reports truncated LLDP system names ("leaf" not "leaf1"), breaking `net_build_topology_from_lldp`.
**Fix**: Add fallback correlation — try management IP mapping or interface-to-interface pairing when hostname matching fails. Consider making the topology builder more resilient to partial hostnames.

### 1.4 Security Tools `not_supported` Without Guidance
**Problem**: Several security tools (`net_get_acl_summary`, `net_get_copp_policy`, etc.) return `status: "not_supported"` with no indication of which platforms support them.
**Fix**: Add platform support metadata to tool responses, e.g., `"supported_platforms": ["eos", "nxos"]` in the error response. Consider adding this to tool annotations.

### 1.5 Settings Singleton Cache Invalidation
**Problem**: `helpers.py` caches `_settings` as a module-level singleton. When env vars change (common in testing, but also in dynamic config scenarios), the cached settings are stale. We had to manually clear `_helpers._settings = None`.
**Fix**: Either make settings invalidation explicit and documented, or add a `refresh_settings()` function to the public API.

---

## Part 2: Real-World Use Case Research

Research and document how network engineers and AI assistants will actually use this tool in production. For each use case, identify:
- What tools are called and in what sequence
- What data formats the AI assistant expects back
- Where our current implementation falls short
- What error scenarios are likely

### 2.1 Day-1 Provisioning Workflows
How would an AI assistant help provision a new switch? Think through:
- Initial device discovery and inventory addition
- Base configuration (hostname, management, NTP, DNS, AAA)
- VLAN provisioning across a fabric
- Interface configuration (access ports, trunks, port-channels)
- Routing protocol setup (BGP, OSPF)
- Verification and validation

### 2.2 Day-2 Operations & Troubleshooting
The most common real-world use case. Research:
- "Why is this interface down?" — what tools does the AI call?
- "Why can't host A reach host B?" — traceroute, ARP, route table, MAC table sequence
- "What changed?" — config diff, event monitor, route changes
- "Is the fabric healthy?" — multi-device health check workflows
- BGP neighbor flapping — what data does the AI need?
- MLAG/vPC inconsistency debugging

### 2.3 Compliance & Audit
- CIS benchmark checking across a fleet
- Configuration drift detection (baseline vs. running)
- Change window validation (pre-check, change, post-check, rollback)
- Generating compliance reports for auditors

### 2.4 Capacity Planning & Monitoring
- Interface utilization trending
- Route table growth analysis
- CPU/memory trending across fleet
- Power and environmental monitoring

### 2.5 Multi-Vendor Brownfield Networks
The hardest real-world scenario. Most networks are NOT single-vendor:
- Mixed Arista/Cisco/Juniper fabrics
- Different CLI syntax, different data models, different failure modes
- How do our common tools handle vendor differences?
- What happens when one vendor returns data in an unexpected format?

### 2.6 Scale Testing
- 50-device inventory — does discovery/listing stay fast?
- 1000 VLANs on a device — does `net_get_vlans` handle it?
- 100k+ route table — does `net_get_route_table` truncate or OOM?
- Full mesh LLDP topology with 20+ devices

---

## Part 3: Multi-Vendor Testing Strategy

### 3.1 Vendor Coverage Audit
Read every driver in `src/network_mcp/drivers/` and every common tool in `src/network_mcp/tools/common/`. For each:
- Map which `NetworkDriver` protocol methods are actually implemented vs. stubbed
- Identify which common tools will fail on which vendors and why
- Document vendor-specific quirks (e.g., NX-OS returns VLAN data differently than EOS)

### 3.2 Containerlab Multi-Vendor Lab Design
Design a containerlab topology that includes:
- **Arista cEOS** (already working)
- **Nokia SR Linux** (free, containerlab-native, excellent for testing)
- **Cisco IOS-XE** (CSR1000v/Cat8kv if available, or mock)
- **SONiC** (VS image for containerlab)

Produce a `lab/multi-vendor-topology.yaml` containerlab file and updated `lab/setup.sh` that can deploy it. For vendors without free container images, design a mock-device strategy.

### 3.3 Cross-Vendor Integration Tests
Design test scenarios that exercise:
- Same tool called against different vendor devices
- Topology discovery across vendor boundaries (LLDP between Arista and Nokia)
- Config operations with vendor-specific syntax
- Error handling when a vendor doesn't support an operation

### 3.4 Driver Gap Analysis
For each of the 5 drivers (eos, iosxe, nxos, junos, sonic), produce a coverage matrix:
- Which of the 35 NetworkDriver protocol methods are implemented?
- Which return normalized data vs. raw vendor output?
- Which have unit tests vs. integration tests?
- What's the confidence level for production use?

---

## Part 4: Hardening Plan

### 4.1 Connection Resilience
- Connection pool per-thread safety (from Part 1)
- Automatic reconnection on connection drop
- Graceful handling of device reboots mid-session
- Connection timeout tuning per vendor
- SSH keepalive for long-lived connections

### 4.2 Data Normalization
- Audit all common tools: does every tool return consistent data across vendors?
- Define explicit output schemas (Pydantic models) for the top 20 most-used tools
- Add response validation — if a vendor returns unexpected data, return a clear error instead of garbage

### 4.3 Error Taxonomy
- Categorize all possible errors: connection, authentication, authorization, timeout, parse, not_supported, rate_limited, circuit_broken
- Ensure every tool returns errors in a consistent format with actionable messages
- Add remediation hints for common errors (the hints.py system — is it complete?)

### 4.4 Observability
- Are metrics actually being collected in production use?
- Can an operator see: tool call rate, latency percentiles, error rate by device, connection pool utilization?
- Is the audit log complete and useful?

### 4.5 Edge Cases
- Device returns empty response (no data, but no error)
- Device returns partial response (truncated output)
- Device responds in a language other than English (some vendors support localization)
- Device is in a degraded state (partial boot, maintenance mode)
- Inventory has stale entries (device replaced, IP changed)
- Multiple devices with the same hostname
- IPv6-only management

---

## Part 5: Deliverables

By the end of this session, produce:

1. **Bug fixes** for all Part 1 items, with tests
2. **Research document** at `docs/REAL_WORLD_USECASES.md` covering Part 2 findings
3. **Multi-vendor lab topology** at `lab/multi-vendor-topology.yaml` (Part 3.2)
4. **Driver coverage matrix** at `docs/DRIVER_COVERAGE.md` (Part 3.4)
5. **Hardening roadmap** at `docs/HARDENING_ROADMAP.md` with prioritized items from Part 4
6. **Updated integration tests** that exercise any new fixes
7. **Commit everything** to `main` with author `Latticio <dev@latticio.com>`

## Important Technical Notes

- All commands run in VM: `orb run -m clab bash -c "cd /Users/jacobfehn/Documents/network-mcp && <command>"`
- Test execution: `uv run pytest tests/integration/ -v --run-integration -n0`
- Unit tests: `uv run pytest tests/ -v -n auto` (parallel)
- Sync deps: `uv sync --extra dev --extra arista`
- The cEOS lab is still running with 6 nodes (spine1/2, leaf1-4)
- MCP SDK 1.26 uses binary framing over stdio — must use official SDK client
- pyeapi is NOT thread-safe — connections throw OSError under concurrent access
- `_helpers._settings` singleton must be cleared when changing env vars in tests
- cEOS LLDP truncates system names
- Security tools may return `status: "not_supported"` (not "error")
- Read `CLAUDE.md` at the project root for all conventions and patterns
