# Security Architecture

Defense-in-depth approach across multiple layers to protect devices and credentials.

## 1. Read-Only Mode (Default)

`NET_READ_ONLY=true` (default) blocks **all** write operations at the `check_read_only()` gate in `helpers.py`. No configuration changes can be made until explicitly set to `false`.

## 2. Command Deny-List

Even with read-only mode disabled, dangerous commands are blocked:

- `reload`, `write erase`, `zerotouch`, `bash`, `delete`, `format`
- Full list in `helpers.py` (`DENIED_COMMANDS`)
- Blocks exact matches and prefix matches

## 3. CLI Injection Prevention

All user-supplied parameters are validated before being sent to devices:

- **`validate_host()`** — rejects empty/whitespace-only hostnames
- **`validate_interface_name()`** — allows only alphanumeric + `/.-` characters
- **`validate_vlan_id()`** — integer range 1-4094
- **`validate_cli_param()`** — blocks dangerous characters: `;`, `|`, `` ` ``, `$()`, newlines

The `DANGEROUS_CLI_CHARS` regex catches shell metacharacters that could enable command injection.

## 4. Credential Management

- Passwords stored as `SecretStr` (pydantic) — never serialized to logs or output
- Credentials loaded exclusively from environment variables or `.env` files
- No hardcoded credentials anywhere in the codebase
- Inventory file (`devices.yaml`) supports per-device credentials

### Best Practices

#### Never Hardcode Credentials

Do not put credentials in source code, configuration files committed to git, or command-line arguments (which are visible in `ps` output). Always use environment variables or `.env` files.

#### Environment Variables

Set device credentials via environment variables:

```bash
export NET_USERNAME=admin
export NET_PASSWORD=your_password
```

For OAuth/JWT tokens and CloudVision:

```bash
export AUTH_SECRET_KEY=your_jwt_secret
export NET_CVP_TOKEN=your_cvp_service_account_token
```

#### `.env` File for Local Development

Copy `.env.example` to `.env` for local overrides:

```bash
cp .env.example .env
# Edit .env with your credentials
```

- `.env` is listed in `.gitignore` — it is never committed
- pydantic-settings loads `.env` automatically at startup
- Use `.env.local` for secrets that should override `.env` (also gitignored)

#### Environment Variable Precedence

pydantic-settings resolves values in this order (highest priority first):

1. **Actual environment variables** (e.g., `export NET_PASSWORD=secret`)
2. **`.env` file values**
3. **Field defaults** in `NetworkSettings` class (lowest priority)

This means you can set a default in `.env` for development and override it with a real environment variable in production.

#### Inventory File Credentials

Passwords in `devices.yaml` are per-device overrides. If a device entry doesn't specify credentials, the defaults from `NET_USERNAME`/`NET_PASSWORD` are used.

```yaml
devices:
  spine-01:
    host: 10.0.0.1
    username: admin          # optional per-device override
    password: device_secret  # optional per-device override
  leaf-01:
    host: 10.0.1.1
    # Uses NET_USERNAME / NET_PASSWORD defaults
```

If your `devices.yaml` contains passwords, keep it out of version control. The default `.gitignore` already excludes `devices.yaml`.

#### SecretStr Protection

Password fields (`NET_PASSWORD`, `AUTH_SECRET_KEY`, `NET_CVP_TOKEN`) use pydantic's `SecretStr` type. This means:

- `str(settings.net_password)` returns `**********`, not the actual value
- Calling `.get_secret_value()` is required to access the real password
- Credentials are never accidentally serialized to logs, JSON output, or error messages

#### Audit Trail and Redaction

All tool invocations are logged by `audit.py`, but output passes through `sanitize_dict_values()` which redacts values for keys matching `password`, `secret`, `key`, `community`, `token`, and `auth`. Running-config output is additionally scrubbed by the config sanitizer to redact passwords, SNMP communities, and keys in text output.

#### Vault Integration Patterns

For production deployments, consider fetching secrets from a secrets manager at startup rather than storing them in `.env` files:

**Wrapper script pattern:**

```bash
#!/bin/bash
# fetch-and-run.sh — fetches secrets, then starts the MCP server
export NET_PASSWORD=$(vault kv get -field=password secret/network/admin)
export NET_CVP_TOKEN=$(vault kv get -field=token secret/network/cvp)
exec network-mcp "$@"
```

**Docker with secrets:**

```bash
docker run \
  -e NET_USERNAME=admin \
  -e NET_PASSWORD="$(aws secretsmanager get-secret-value --secret-id network/password --query SecretString --output text)" \
  ghcr.io/latticio/network-mcp
```

The key principle: the MCP server reads credentials from environment variables, so any secrets manager that can inject environment variables works without code changes.

#### Files to Keep Out of Version Control

The `.gitignore` already excludes these sensitive file patterns:

- `.env` — local environment overrides
- `devices.yaml` — inventory with potential credentials
- `*.pem`, `*.key`, `*.crt` — TLS certificates and private keys

## 5. Output Sanitization

Two layers of output sanitization:

- **`sanitize_dict_values()`** — recursively scans all tool output dicts, redacting values for keys matching `password`, `secret`, `key`, `community`, `token`, `auth`
- **`config_sanitizer`** — regex-based redaction of passwords, SNMP communities, and keys in running-config text output

All tool responses pass through sanitization before reaching the MCP client.

## 6. Rate Limiting

Token-bucket rate limiter (`rate_limiter.py`) prevents overwhelming devices:

| Setting | Default | Purpose |
|---------|---------|---------|
| `RATE_LIMIT_SHOW` | 5.0/s | Show commands per device per second |
| `RATE_LIMIT_CONFIG` | 1.0/s | Config commands per device per second |

Rate limits are per-device, preventing any single device from being overloaded.

## 7. Audit Logging

Every tool invocation is logged as structured JSON (`audit.py`):

- Tool name, device, parameters (redacted), status, duration
- User identity (when auth is enabled)
- Configurable via `LOG_LEVEL` and `LOG_FILE`

Logs go to stderr (never stdout, which is the JSON-RPC transport).

## 8. Output Size Limits

`enforce_dict_output_limit()` in `sanitizer.py` prevents excessively large tool outputs from overflowing the LLM context window. Large responses are truncated with a warning.

## 9. Circuit Breaker

Optional per-device circuit breaker (`NET_CIRCUIT_BREAKER=true`):

- Opens after `NET_CB_THRESHOLD` consecutive failures (default: 3)
- Fails fast for `NET_CB_COOLDOWN` seconds (default: 60)
- Prevents cascading failures when a device is unreachable

## 10. Authentication (Optional)

OAuth 2.1 / JWT middleware is defined (`auth.py`, `middleware.py`) but not enforced by default. When enabled:

- JWT token validation with configurable issuer and audience
- Bearer token required on all HTTP requests
- Designed for the Streamable HTTP transport (stdio transport is never affected)

Enable authentication:

```bash
AUTH_ENABLED=true
AUTH_SECRET_KEY=your-jwt-secret-key
AUTH_ISSUER_URL=https://auth.example.com
AUTH_REQUIRED_SCOPES=network:read   # Minimum scope to access the server
```

## 11. Role-Based Access Control (RBAC)

RBAC adds fine-grained, tool-level authorization on top of authentication. When enabled, each tool call is checked against the JWT `scope` claim to determine if the user has permission to execute it.

**RBAC is opt-in** and requires authentication to be enabled first.

### Enabling RBAC

```bash
AUTH_ENABLED=true
AUTH_SECRET_KEY=your-jwt-secret-key
AUTH_ISSUER_URL=https://auth.example.com
NET_RBAC_ENABLED=true
```

RBAC only applies to **HTTP transport** (Streamable HTTP). Stdio transport bypasses RBAC because local CLI usage is considered trusted.

### Scopes

RBAC uses the `network:` scope namespace. Four scopes control access:

| Scope | Description | Example Tools |
|-------|-------------|---------------|
| `network:read` | Read-only operations — show commands, diagnostics, validation | `eos_get_vlans`, `net_get_bgp_summary`, `eos_validate_bgp` |
| `network:write` | Configuration changes — create, modify, commit | `eos_create_vlan`, `eos_push_config_commands`, `eos_commit_config_session` |
| `network:admin` | Destructive operations — delete, rollback | `eos_delete_vlan`, `eos_rollback_to_checkpoint`, `eos_delete_config_checkpoint` |
| `network:audit` | Compliance and audit operations | `eos_compliance_check` |

### Scope Hierarchy

Higher scopes automatically grant lower permissions:

- `network:admin` grants `network:write` + `network:read`
- `network:write` grants `network:read`
- `network:audit` is independent (does not imply read/write)

### Predefined Roles

Roles are convenience groupings of scopes. They are not enforced by the server — scopes are the authority:

| Role | Scopes | Use Case |
|------|--------|----------|
| `viewer` | `network:read` | NOC operators, monitoring dashboards |
| `operator` | `network:read`, `network:write` | Network engineers making changes |
| `admin` | `network:read`, `network:write`, `network:admin` | Full access including destructive operations |
| `auditor` | `network:read`, `network:audit` | Compliance teams running audit checks |

### Tool-to-Scope Mapping

Tools are mapped to scopes using glob patterns on the tool function name. The mapping is defined in `src/network_mcp/rbac.py` (`TOOL_SCOPES`):

```python
# Pattern matching examples:
"eos_get_*"     -> "network:read"     # All EOS read operations
"eos_create_*"  -> "network:write"    # Create operations
"eos_delete_*"  -> "network:admin"    # Delete operations (destructive)
"net_get_*"     -> "network:read"     # Vendor-agnostic read operations
"nxos_get_*"    -> "network:read"     # Cisco NX-OS reads
"eos_compliance_*" -> "network:audit" # Compliance checks
```

Tools not matching any pattern are accessible to all authenticated users.

### JWT Scope Claim Format

RBAC extracts scopes from JWT tokens in three formats (checked in order):

1. **`scope`** (OAuth2 standard) — space-separated string:
   ```json
   {"scope": "network:read network:write"}
   ```

2. **`scopes`** — JSON array:
   ```json
   {"scopes": ["network:read", "network:write"]}
   ```

3. **`permissions`** (Auth0 style) — JSON array:
   ```json
   {"permissions": ["network:read", "network:write"]}
   ```

All three formats are supported simultaneously. Scopes from all matching claims are merged.

### Error Response

When a user lacks the required scope, the tool returns a structured error:

```json
{
  "status": "error",
  "error": "Forbidden: tool 'eos_create_vlan' requires scope 'network:write'. Your scopes: ['network:read']"
}
```

### Configuring an OAuth2 Provider

Example: Adding network scopes to your identity provider.

**Auth0:**
1. Create an API in Auth0 with identifier `network-mcp`
2. Define permissions: `network:read`, `network:write`, `network:admin`, `network:audit`
3. Enable RBAC for the API and "Add Permissions in the Access Token"
4. Assign permissions to users/roles in Auth0

**Keycloak:**
1. Create a client scope `network-scopes` in your realm
2. Add scope mappers for `network:read`, `network:write`, etc.
3. Assign the client scope to your MCP client
4. Map realm roles to scopes

**Generic JWT issuer:**
Include the appropriate scopes in the `scope` claim of the JWT:

```python
import jwt

token = jwt.encode(
    {
        "sub": "engineer@example.com",
        "scope": "network:read network:write",
        "aud": "network-mcp",
        "iss": "https://auth.example.com",
        "exp": 1700000000,
    },
    "your-secret-key",
    algorithm="HS256",
)
```

### Interaction with Read-Only Mode

RBAC and `NET_READ_ONLY` mode are independent layers:

- `NET_READ_ONLY=true` blocks **all** write operations regardless of scopes
- RBAC checks scopes **before** the read-only check
- A user with `network:write` scope will still be blocked if `NET_READ_ONLY=true`

Both must allow the operation for it to proceed.

## 12. CI Security Scanning

Recommended CI configuration for static analysis with bandit, blocking on HIGH/CRITICAL severity findings:

```bash
# Report only (non-blocking) — show all HIGH+ findings:
uv run bandit -r src/ -ll -ii --exit-zero

# Blocking check — fail the build on HIGH+ findings:
uv run bandit -r src/ -ll -ii
```

The project's `pyproject.toml` includes bandit configuration under `[tool.bandit]` with test exclusions and safe skip lists already applied.
