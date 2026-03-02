# Architecture

Internal architecture of network-mcp, covering data flow, module loading, security layers, and multi-tenancy design.

## Request Flow

```
MCP Client (Claude Desktop, Cursor, VS Code, Claude Code)
  │
  │  stdio or Streamable HTTP (JSON-RPC 2.0)
  ▼
server.py (FastMCP instance)
  │
  ├─ Auth middleware (HTTP only, when AUTH_ENABLED=true)
  │   └─ JWT validation → extract user, scopes, tenant_id
  │
  ├─ RBAC check (HTTP only, when NET_RBAC_ENABLED=true)
  │   └─ Match tool name against TOOL_SCOPES → verify user has required scope
  │
  ├─ Tool dispatch
  │   └─ helpers.py: run_show_command / run_config_command
  │       ├─ Rate limiter (token bucket, per-device)
  │       ├─ Circuit breaker (per-device, optional)
  │       ├─ Cache lookup (TTL-based, per-host)
  │       ├─ ConnectionManager → NetworkDriver → device
  │       ├─ Output sanitization (credential redaction, size limits)
  │       └─ Audit logging (JSON, includes tenant_id)
  │
  └─ Response (structured JSON dict)
```

## Module Loading

The server uses a two-tier module loading strategy:

- **Core modules** (always loaded): checkpoints, config_mgmt, device, fabric, interfaces, monitoring, routing, sessions, switching, troubleshoot, validation, vlans, workflows
- **Optional modules** (conditionally loaded): evpn_vxlan, security, vrf, bfd, event_monitor, qos, cloudvision, gnmi
- **Plugin modules** (via entry points): third-party drivers, compliance packs, tool modules

Optional modules can be disabled with `NET_DISABLED_MODULES` or allowlisted with `NET_ENABLED_MODULES`. Progressive discovery mode (`NET_PROGRESSIVE_DISCOVERY=true`) defers all modules except meta and workflows until an LLM requests them.

## Driver Architecture

```
ConnectionManager
  │
  ├─ get_node(host) → NetworkDriver
  │   └─ DRIVER_REGISTRY selects driver by platform field in inventory
  │
  ├─ Built-in drivers
  │   ├─ EosDriver (pyeapi, eAPI/HTTPS)
  │   ├─ IosXeDriver (httpx, RESTCONF/SSH)
  │   ├─ NxosDriver (httpx, NX-API/SSH)
  │   └─ JunosDriver (scrapli-netconf, NETCONF)
  │
  └─ Plugin drivers
      └─ Discovered via network_mcp.drivers entry points
```

All drivers implement the `NetworkDriver` protocol with 35 normalized getters covering device facts, interfaces, routing (BGP, OSPF), switching (VLANs, MAC, STP, LLDP, port-channels), monitoring, security (ACLs, AAA, SNMP), config management, VRFs, and more — for consistent cross-vendor data.

## Security Layers

See [SECURITY.md](SECURITY.md) for full details. Summary:

1. **Read-only mode** — blocks all write operations by default
2. **Command deny-list** — blocks dangerous commands (reload, write erase, etc.)
3. **CLI injection prevention** — validates all user parameters
4. **Credential management** — SecretStr, env vars, never hardcoded
5. **Output sanitization** — credential redaction, size limits
6. **Rate limiting** — per-device token bucket
7. **Circuit breaker** — per-device fail-fast
8. **Audit logging** — JSON audit trail with user and tenant attribution
9. **Authentication** — OAuth 2.1 / JWT on HTTP transport
10. **RBAC** — scope-based tool access control
11. **Multi-tenancy** — tenant extraction from JWT claims

## Multi-Tenancy Design

### Overview

network-mcp supports basic multi-tenancy on HTTP transport via JWT-based tenant identification. This enables shared server deployments where multiple organizations use the same server instance with isolated audit trails.

### Architecture

```
HTTP Request
  │
  ▼
Auth Middleware (FastMCP built-in + TokenVerifier)
  │  Validates JWT → extracts claims
  ▼
Tenant Extraction (rbac.extract_tenant_id)
  │  JWT claims → tenant_id
  │  Priority: tenant_id claim > org_id claim > sub domain
  ▼
Request Context (middleware.set_request_context)
  │  Stores user, scopes, tenant_id in thread-local
  ▼
RBAC Check (rbac.check_authorization)
  │  Verifies user scopes against tool requirements
  ▼
Tool Execution
  │  run_show_command / run_config_command
  ▼
Audit Logging (audit.log_tool_call)
  │  Includes tenant_id in every log entry
  ▼
Response
```

### Current Implementation (v6.0.0)

The current multi-tenancy support provides:

- **Tenant extraction from JWT claims**: `extract_tenant_id()` in `rbac.py` supports three claim formats:
  - `tenant_id` — explicit tenant identifier (highest priority)
  - `org_id` — Auth0 organization identifier
  - `sub` — email domain extracted from `user@tenant.com` format

- **Per-tenant audit log entries**: Every audit log entry includes a `tenant_id` field, enabling log filtering and analysis by tenant.

- **Request context propagation**: The middleware thread-local context carries `tenant_id` alongside `user` and `scopes`, making tenant information available to all layers without changing tool signatures.

### JWT Claim Examples

Explicit tenant:
```json
{
  "sub": "admin@acme-corp.com",
  "tenant_id": "acme-corp",
  "scope": "network:read network:write"
}
```

Auth0 organization:
```json
{
  "sub": "auth0|abc123",
  "org_id": "org_acme",
  "scope": "network:read"
}
```

Email-based (automatic):
```json
{
  "sub": "engineer@megacorp.com",
  "scope": "network:read network:write"
}
```
→ tenant_id = `megacorp.com`

### Audit Log Format

```json
{
  "timestamp": "2026-02-26T12:00:00+00:00",
  "level": "INFO",
  "logger": "network-mcp.audit",
  "message": "Tool call: eos_get_vlans on spine-01",
  "event": "tool_invocation",
  "tool": "eos_get_vlans",
  "device": "spine-01",
  "user": "admin@acme-corp.com",
  "tenant_id": "acme-corp",
  "parameters": {"host": "spine-01"},
  "result_status": "success",
  "duration_ms": 42.5
}
```

For single-tenant deployments (stdio transport or no tenant claims in JWT), `tenant_id` is `null`.

### Future Enhancements (Not Yet Implemented)

These features are documented for future development. They are **not** part of v6.0.0:

- **Per-tenant connection pools**: Limit each tenant to N concurrent device connections, preventing one tenant from exhausting the connection pool.

- **Per-tenant rate limits**: Apply separate rate limit buckets per tenant, independent of per-device rate limits.

- **Per-tenant inventory isolation**: Restrict which devices a tenant can access based on inventory tags or groups. A tenant would only see devices tagged with their tenant ID.

- **Per-tenant configuration namespaces**: Isolate configuration sessions and checkpoints by tenant to prevent cross-tenant interference.

- **Tenant management API**: Admin endpoints for creating/listing/disabling tenants, managing tenant-device mappings, and viewing per-tenant usage metrics.

- **Database-backed tenant store**: Move from JWT-only tenant identification to a persistent tenant registry with quota tracking and billing hooks.

### Design Decisions

1. **JWT-only, no database**: Tenant identity comes entirely from the JWT. This avoids operational complexity (no tenant database to manage) while still enabling per-tenant audit separation.

2. **Thread-local context**: Tenant ID propagates via `threading.local()` in `middleware.py`. This is compatible with FastMCP's synchronous tool execution model and avoids changing every tool's signature.

3. **Graceful degradation**: When no tenant information is present (stdio transport, missing JWT claims), `tenant_id` is `None`. All tools continue to work identically — multi-tenancy is purely additive.

4. **Priority order for claims**: `tenant_id` > `org_id` > `sub` domain. This lets operators explicitly set tenant identity while supporting common OAuth providers out of the box.
