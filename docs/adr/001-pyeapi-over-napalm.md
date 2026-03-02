# ADR-001: Use pyeapi for EOS Instead of NAPALM

**Date:** 2024-06-15

**Status:** Accepted

## Context

When building the Arista EOS integration, we needed to choose a Python library for communicating with EOS devices via eAPI. The main options were:

1. **pyeapi** — Arista's official Python client for eAPI. Provides direct, low-level access to the eAPI JSON-RPC interface with connection management and node abstraction.

2. **NAPALM** — A multi-vendor network automation library that provides a unified API across vendors. Its EOS driver wraps pyeapi internally.

3. **Direct HTTP/JSON-RPC** — Building raw HTTP requests against the eAPI endpoint.

Key considerations:

- NAPALM wraps pyeapi for EOS, adding an abstraction layer with no additional EOS-specific functionality.
- NAPALM's unified getters normalize output, which discards vendor-specific fields that are valuable for network operators (e.g., EOS-specific MLAG states, EVPN/VXLAN details).
- NAPALM's `get_*` methods return a fixed schema; any EOS command not covered by NAPALM requires falling back to `cli()` which returns raw text.
- pyeapi returns the native eAPI JSON response, preserving all vendor-specific detail.
- pyeapi is maintained by Arista and tracks eAPI changes closely.

## Decision

Use **pyeapi** as the primary EOS driver, communicating directly with eAPI.

For multi-vendor support, we implement our own `NetworkDriver` protocol with vendor-specific drivers (pyeapi for EOS, RESTCONF for IOS-XE, NX-API for NX-OS, NETCONF/scrapli for JunOS) rather than relying on NAPALM's abstraction.

## Consequences

### Positive

- **Full access to eAPI JSON output** — no data loss from normalization. Every field EOS returns is available to MCP tools.
- **Better performance** — one fewer abstraction layer. pyeapi calls go directly to eAPI without NAPALM's getter overhead.
- **Vendor-specific features** — EOS-only features like config sessions, MLAG domain IDs, EVPN route types, and ANTA validation are directly accessible.
- **Simpler dependency tree** — pyeapi has minimal dependencies vs. NAPALM which pulls in multiple vendor libraries.
- **Consistent with Arista's tooling** — matches how Arista's own tools (CVP, AVD) interact with devices.

### Negative

- **No free multi-vendor abstraction** — we had to build our own `NetworkDriver` protocol and implement drivers for each vendor. This was intentional and gives us more control.
- **Tied to Arista's API design** — if eAPI changes, pyeapi may need updates. In practice, eAPI is very stable and backward-compatible.
- **Contributors familiar with NAPALM** need to learn the pyeapi API, though it is straightforward (`run_commands()`, `config()`).
