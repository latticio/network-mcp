# ADR-002: Use Scrapli for Multi-Vendor SSH/NETCONF

**Date:** 2024-07-20

**Status:** Accepted

## Context

The Juniper JunOS driver requires NETCONF communication, and some vendors need SSH as a fallback transport. We evaluated three Python SSH/NETCONF libraries:

1. **Paramiko** — Low-level SSH2 library. Provides raw SSH channel access but requires manual handling of command prompts, output parsing, and session management.

2. **Netmiko** — Built on Paramiko. Provides a higher-level interface with auto-detection of device prompts, multi-vendor support, and command output parsing. Widely used in network automation.

3. **Scrapli** — Modern SSH/NETCONF library designed for network devices. Supports async operation, platform-specific drivers, strict host key checking, and session reuse. Built on `asyncssh` or system SSH for transport.

Key considerations:

- Paramiko requires significant boilerplate for each vendor (prompt detection, output parsing, config mode handling).
- Netmiko has a large dependency footprint and is synchronous-only. Its multi-vendor support is broad but adds weight even when targeting a single vendor.
- Scrapli provides platform-specific drivers (EOS, IOS-XE, NX-OS, JunOS) with NETCONF support via `scrapli-netconf`. It supports async out of the box and has a smaller dependency surface.
- Scrapli's `GenericDriver` allows extending to unsupported platforms with minimal code.
- Scrapli enforces strict host key checking by default, improving security posture.

## Decision

Use **scrapli** (specifically `scrapli-netconf`) for the JunOS NETCONF driver and as the SSH fallback transport for vendors that need it.

## Consequences

### Positive

- **Async-capable** — scrapli supports both sync and async transports, aligning with potential future async operation of the MCP server.
- **Platform-specific drivers** — built-in support for EOS, IOS-XE, NX-OS, and JunOS means correct prompt handling and output parsing without custom code.
- **Session reuse** — scrapli supports persistent connections and session multiplexing, reducing connection overhead for multi-command operations.
- **Strict host key checking** — enabled by default, improving security compared to Netmiko's default behavior of auto-accepting keys.
- **NETCONF native support** — `scrapli-netconf` provides proper NETCONF 1.0/1.1 framing, RPC building, and XML response parsing without needing `ncclient`.
- **Lighter dependency tree** — fewer transitive dependencies than Netmiko.

### Negative

- **Smaller community** — Netmiko has broader adoption and more community examples. Contributors may be less familiar with scrapli's API.
- **Platform driver coverage** — while the four platforms we target are well-supported, more exotic vendors may need custom `GenericDriver` implementations.
- **API differences** — scrapli's API is different from Netmiko's, so existing network automation scripts cannot be directly copied. Our `NetworkDriver` protocol abstracts this.
