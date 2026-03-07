# Hardening Roadmap

Prioritized list of improvements to make network-mcp production-ready across all vendors and scenarios. Items are organized by priority (P0 = critical, P1 = high, P2 = medium, P3 = nice-to-have).

---

## P0 — Critical (Before Production Use)

### 1. Connection Resilience
- [x] **Per-host locking for thread safety** — pyeapi HTTP transport is not thread-safe; concurrent access causes `OSError: Bad file descriptor`. Fixed: per-host locks in `ConnectionManager.acquire()` serialize access to the same device while allowing full parallelism across devices.
- [ ] **Automatic reconnection on stale connections** — EosDriver has `_check_alive()` keepalive, but other drivers don't. Add keepalive/reconnect to IOS-XE (HTTP session reuse), NX-OS, JunOS (NETCONF session), and SONiC drivers.
- [ ] **Connection timeout tuning per vendor** — EOS eAPI is fast (< 1s connect), JunOS NETCONF can take 5-10s, IOS-XE RESTCONF varies. Add per-platform timeout defaults in `NetworkSettings`.
- [ ] **Graceful handling of device reboots** — If a device reboots during a config session, the driver should detect the connection loss, invalidate the cache, and return a clear error (not a stack trace).

### 2. Data Normalization Audit
- [ ] **Verify all 35 getters return consistent data across 5 vendors** — Some getters may return raw vendor output instead of normalized dicts on less-tested platforms (NX-OS, JunOS, SONiC).
- [ ] **Define Pydantic models for top 20 tool responses** — Currently tool responses are unvalidated dicts. Add response models in `schemas.py` for: `get_facts`, `get_interfaces`, `get_bgp_summary`, `get_arp_table`, `get_vlans`, `get_route_table`, `get_lldp_neighbors`, `get_mac_table`, `get_interface_counters`, `get_interface_errors`, `get_config`, `get_cpu_memory`, `get_environment`, `get_ntp_status`, `get_ospf_neighbors`, `get_bgp_neighbors`, `get_acls`, `get_stp_status`, `get_port_channels`, `get_route_summary`.
- [ ] **Response validation middleware** — Validate driver output against Pydantic models before returning to tools. On schema mismatch, return `status: "error"` with details instead of malformed data.

### 3. Error Consistency
- [x] **Platform support metadata in not_supported responses** — Added `supported_platforms` list to all `not_supported` responses via `make_not_supported_response()` helper.
- [ ] **Standardize error codes across all tools** — Some tools return `status: "error"`, others `status: "not_supported"`, others raise `ToolError`. Document the taxonomy and ensure consistency.
- [ ] **Add remediation hints for all 12 error categories** — `hints.py` has the framework but may not cover all vendor-specific errors. Audit and expand.

---

## P1 — High Priority (First Month)

### 4. Multi-Vendor Test Coverage
- [ ] **Containerlab multi-vendor lab** — Deploy `lab/multi-vendor-topology.yaml` with Arista cEOS, Nokia SR Linux, and SONiC-VS. Run integration tests across all three.
- [ ] **Cross-vendor integration tests** — Test LLDP discovery across vendor boundaries, same tool called against different platforms, vendor-specific error handling.
- [ ] **Driver gap remediation** — Implement missing getters in NX-OS (7 stubs), JunOS (6 stubs), and SONiC (15+ stubs). See `docs/DRIVER_COVERAGE.md`.

### 5. Settings and Configuration
- [x] **Settings cache invalidation** — Added `refresh_settings()` public API for clearing the cached `_settings` singleton.
- [ ] **Runtime config reload** — The `/config/reload` HTTP endpoint exists but needs testing. Verify it properly reinitializes rate limiters, cache, and circuit breakers.
- [ ] **Per-device settings overrides** — Allow `devices.yaml` to override timeouts, rate limits, and transport per device.

### 6. Interface Description Clearing
- [x] **Accept empty string to clear description** — `net_configure_interface_description("")` now emits `no description` (EOS/IOS-XE/NX-OS) or `delete interfaces ... description` (JunOS).

### 7. LLDP Topology Resilience
- [x] **Fallback hostname matching** — `net_build_topology_from_lldp` now tries management IP and prefix matching when exact hostname matching fails (cEOS truncation workaround).
- [ ] **Add management_ip to LLDP neighbor output** — Done for EOS; verify all vendor drivers include it.

---

## P2 — Medium Priority (Quarter 2)

### 8. Observability
- [ ] **Verify metrics collection in production** — `observability.py` has MetricsCollector but needs validation that Prometheus export works end-to-end.
- [ ] **Add OpenTelemetry trace export** — `telemetry.py` has OTel instrumentation; verify OTLP gRPC export works with Jaeger/Tempo.
- [ ] **Dashboard templates** — Provide Grafana dashboard JSON for tool call rate, latency percentiles, error rate by device, and connection pool utilization.
- [ ] **Audit log completeness** — Verify audit.py captures all tool calls including failures, with tenant attribution and correlation IDs.

### 9. Scale & Performance
- [ ] **Parallel LLDP topology collection** — `net_build_topology_from_lldp` queries devices sequentially; use ThreadPoolExecutor for O(1) latency on large topologies.
- [ ] **Streaming pagination for large datasets** — Route tables (100k+), MAC tables (50k+), and full configs can exceed context limits. Add cursor-based pagination.
- [ ] **Connection pool warm-up** — Pre-connect to all inventory devices on startup for faster first-call latency.
- [ ] **Benchmark suite for regression detection** — `tests/benchmarks/` exists; wire into CI to catch performance regressions.

### 10. Security Hardening
- [ ] **Verify RBAC enforcement covers all 297 tools** — Spot-check that `network:write` and `network:admin` scopes are correctly required.
- [ ] **Rate limit tuning per vendor** — Some devices (e.g., small JunOS boxes) can't handle 5 req/s; add vendor-aware defaults.
- [ ] **TLS certificate validation** — Currently `NET_VERIFY_SSL=false` is common; add proper CA bundle support for production.
- [ ] **Secrets management** — Vault integration exists; document and test the full flow with HashiCorp Vault.

---

## P3 — Nice to Have (Future)

### 11. Advanced Features
- [ ] **Config template engine expansion** — `templates.py` has 17 intents; add more for common operations (BGP neighbor, OSPF area, NTP server, etc.).
- [ ] **Fleet-wide operations** — Tools that operate on groups/tags/roles of devices in a single call.
- [ ] **Async driver support** — For high-concurrency HTTP/RESTCONF/REST operations (IOS-XE, NX-OS, SONiC).
- [ ] **Config drift detection** — Compare running configs against a golden baseline stored in git.
- [ ] **Webhook notifications** — Alert on circuit breaker trips, rate limit exhaustion, or device health changes.

### 12. Edge Cases
- [ ] **Empty response handling** — Device returns 200 OK but empty body (partial boot, maintenance mode).
- [ ] **Truncated response detection** — Detect when device output was cut off mid-stream.
- [ ] **Stale inventory entries** — Periodic inventory health check to detect replaced devices or changed IPs.
- [ ] **Duplicate hostnames** — Handle multiple devices with the same LLDP system name (common in large networks).
- [ ] **IPv6-only management** — Verify all drivers work over IPv6 transport.
