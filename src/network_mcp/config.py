"""Multi-vendor application settings loaded from environment variables and .env files."""

import json
import logging
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger("network-mcp.config")


class FeatureFlag(BaseModel):
    """A feature flag with optional tenant targeting.

    When ``enabled`` is True and ``tenants`` is empty, the feature is available
    to all tenants.  When ``tenants`` is non-empty, only the listed tenants
    have access.
    """

    enabled: bool = False
    tenants: list[str] = Field(default_factory=list)


class Platform(StrEnum):
    """Supported network device platforms.

    Used for driver auto-selection in the DRIVER_REGISTRY.
    All platforms have production-ready drivers: EOS (pyeapi), IOS-XE (RESTCONF),
    NX-OS (NX-API), and JunOS (NETCONF).
    """

    EOS = "eos"
    IOSXE = "iosxe"
    NXOS = "nxos"
    JUNOS = "junos"
    SONIC = "sonic"
    PANOS = "panos"


class PlatformSettings(BaseSettings):
    """Base settings shared across all MCP platform servers."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias=AliasChoices("NET_LOG_LEVEL", "LOG_LEVEL"),
    )

    # OAuth 2.1 / JWT authentication (for Streamable HTTP transport only)
    # When AUTH_ENABLED=true, Bearer tokens are validated on every HTTP request
    # via FastMCP's built-in auth middleware. Stdio transport is never affected.
    auth_enabled: bool = False
    auth_issuer_url: str | None = None
    auth_secret_key: SecretStr | None = None
    auth_required_scopes: str = "network:read"
    auth_resource_server_url: str | None = None  # MCP resource server URL (for OAuth metadata)

    # JWKS (JSON Web Key Set) — auto-discover public keys from identity provider
    auth_jwks_url: str = Field(
        default="",
        validation_alias=AliasChoices("AUTH_JWKS_URL"),
        description="JWKS endpoint URL (e.g., https://idp.example.com/.well-known/jwks.json)",
    )
    auth_algorithm: str = Field(
        default="RS256",
        validation_alias=AliasChoices("AUTH_ALGORITHM"),
        description="JWT signing algorithm (RS256, ES256, HS256)",
    )
    auth_audience: str = Field(
        default="network-mcp",
        validation_alias=AliasChoices("AUTH_AUDIENCE"),
        description="Expected JWT audience claim",
    )
    auth_jwks_refresh_interval: int = Field(
        default=3600,
        validation_alias=AliasChoices("AUTH_JWKS_REFRESH_INTERVAL"),
        description="Seconds between JWKS cache refreshes",
    )

    # Token refresh and revocation settings
    auth_access_token_ttl: int = Field(
        default=900,
        validation_alias=AliasChoices("AUTH_ACCESS_TOKEN_TTL"),
        description="Access token TTL in seconds (default 15 minutes)",
    )
    auth_refresh_token_ttl: int = Field(
        default=14400,
        validation_alias=AliasChoices("AUTH_REFRESH_TOKEN_TTL"),
        description="Refresh token TTL in seconds (default 4 hours)",
    )
    net_token_revocation_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("NET_TOKEN_REVOCATION_ENABLED"),
        description="Enable JTI-based token revocation (default true when AUTH_ENABLED)",
    )

    # RBAC (Role-Based Access Control) — requires AUTH_ENABLED=true
    # Maps JWT scope claims to tool-level permissions (HTTP transport only)
    rbac_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_RBAC_ENABLED", "EOS_RBAC_ENABLED", "RBAC_ENABLED"),
    )

    # API key authentication — alternative to JWT for service accounts and CI/CD
    api_key_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_API_KEY_ENABLED", "API_KEY_ENABLED"),
        description="Enable API key authentication as an alternative to JWT",
    )
    api_key_file: str = Field(
        default=".api-keys.json",
        validation_alias=AliasChoices("NET_API_KEY_FILE", "API_KEY_FILE"),
        description="Path to the API key storage file",
    )
    api_key_prefix: str = Field(
        default="nmcp_",
        validation_alias=AliasChoices("NET_API_KEY_PREFIX", "API_KEY_PREFIX"),
        description="Prefix for generated API keys",
    )

    # mTLS client certificate authentication
    mtls_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_MTLS_ENABLED", "MTLS_ENABLED"),
        description="Enable mTLS client certificate validation",
    )
    mtls_ca_cert: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_MTLS_CA_CERT", "MTLS_CA_CERT"),
        description="Path to CA certificate for client cert validation",
    )
    mtls_allowed_cns: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_MTLS_ALLOWED_CNS", "MTLS_ALLOWED_CNS"),
        description="Comma-separated allowed Common Names",
    )
    mtls_allowed_ous: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_MTLS_ALLOWED_OUS", "MTLS_ALLOWED_OUS"),
        description="Comma-separated allowed Organizational Units",
    )
    mtls_scope_mapping: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_MTLS_SCOPE_MAPPING", "MTLS_SCOPE_MAPPING"),
        description='JSON mapping CN -> scopes (e.g., \'{"admin-client": ["network:admin"]}\')',
    )

    # OAuth 2.1 well-known metadata endpoints
    oauth_resource_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_OAUTH_RESOURCE_URL", "OAUTH_RESOURCE_URL"),
        description="OAuth protected resource identifier URL",
    )
    oauth_auth_server_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_OAUTH_AUTH_SERVER_URL", "OAUTH_AUTH_SERVER_URL"),
        description="OAuth authorization server URL",
    )

    # Tenant isolation — restricts device access per tenant based on inventory tenants list
    tenant_isolation_enabled: bool = Field(default=False, alias="TENANT_ISOLATION_ENABLED")


class NetworkSettings(PlatformSettings):
    """Unified settings for the Network MCP platform.

    Configures all vendors (EOS, IOS-XE, NX-OS, JunOS) via NET_* environment
    variables. EOS_* prefix is supported as a backward-compatible fallback.
    """

    net_username: str = Field(
        default="admin",
        validation_alias=AliasChoices("NET_USERNAME", "EOS_USERNAME"),
    )
    net_password: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("NET_PASSWORD", "EOS_PASSWORD"),
    )
    net_transport: Literal["http", "https"] = Field(
        default="https",
        validation_alias=AliasChoices("NET_TRANSPORT", "EOS_TRANSPORT"),
    )
    net_inventory_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_INVENTORY_FILE", "EOS_INVENTORY_FILE"),
    )
    net_read_only: bool = Field(
        default=True,
        validation_alias=AliasChoices("NET_READ_ONLY", "EOS_READ_ONLY"),
        description="Blocks write tools when True (default: safe)",
    )
    net_disabled_modules: str = Field(
        default="",
        validation_alias=AliasChoices("NET_DISABLED_MODULES", "EOS_DISABLED_MODULES"),
        description="Comma-separated list of optional modules to disable",
    )
    net_enabled_modules: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_ENABLED_MODULES", "EOS_ENABLED_MODULES"),
        description="Comma-separated list of modules to load (overrides disabled)",
    )
    net_verify_connections: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_VERIFY_CONNECTIONS", "EOS_VERIFY_CONNECTIONS"),
        description="Run 'show version' on first use to validate eAPI path",
    )
    net_demo_mode: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_DEMO_MODE", "EOS_DEMO_MODE"),
        description="Use MockDriver with mock inventory (no real devices needed)",
    )

    # Connection pool
    max_connections: int | None = Field(
        default=None,
        alias="MAX_CONNECTIONS",
        description="Max connection pool size (auto-calculated from inventory size if not set)",
    )

    # Connection keepalive interval (seconds) — how long a connection can be idle
    # before a lightweight keep-alive probe is sent. Increase for high-latency WAN links.
    keepalive_interval: int = Field(
        default=60,
        ge=10,
        le=600,
        validation_alias=AliasChoices("NET_KEEPALIVE_INTERVAL", "KEEPALIVE_INTERVAL"),
        description="Connection keepalive interval in seconds",
    )

    # Connection timeouts (seconds)
    timeout_connect: float = Field(
        default=10.0,
        ge=1.0,
        le=120.0,
        validation_alias=AliasChoices("NET_TIMEOUT_CONNECT", "TIMEOUT_CONNECT"),
    )
    timeout_show: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        validation_alias=AliasChoices("NET_TIMEOUT_SHOW", "TIMEOUT_SHOW"),
    )
    timeout_config: float = Field(
        default=60.0,
        ge=1.0,
        le=600.0,
        validation_alias=AliasChoices("NET_TIMEOUT_CONFIG", "TIMEOUT_CONFIG"),
    )

    # Rate limiting (commands per second per device)
    rate_limit_show: float = Field(
        default=5.0,
        gt=0,
        le=100.0,
        validation_alias=AliasChoices("NET_RATE_LIMIT_SHOW", "RATE_LIMIT_SHOW"),
    )
    rate_limit_config: float = Field(
        default=1.0,
        gt=0,
        le=50.0,
        validation_alias=AliasChoices("NET_RATE_LIMIT_CONFIG", "RATE_LIMIT_CONFIG"),
    )

    # Rate limiter backoff: max seconds to wait with exponential backoff when rate limited
    net_rate_limit_max_wait: float = Field(
        default=5.0,
        ge=0.0,
        le=30.0,
        validation_alias=AliasChoices("NET_RATE_LIMIT_MAX_WAIT"),
        description="Max seconds to wait with backoff when rate limited (0=fail immediately)",
    )

    # Bulkhead isolation: per-vendor concurrency limit (0=disabled)
    net_bulkhead_per_vendor: int = Field(
        default=20,
        ge=0,
        le=200,
        validation_alias=AliasChoices("NET_BULKHEAD_PER_VENDOR"),
        description="Max concurrent operations per vendor platform (0=disabled)",
    )

    # Connection acquire timeout for bulkhead slots
    net_conn_acquire_timeout: float = Field(
        default=30.0,
        ge=1.0,
        le=120.0,
        validation_alias=AliasChoices("NET_CONN_ACQUIRE_TIMEOUT"),
        description="Timeout in seconds to acquire a bulkhead slot or connection",
    )

    # Per-user rate limiting (requests per second, by scope tier)
    rate_limit_user_read: float = Field(
        default=10.0,
        gt=0,
        le=200.0,
        validation_alias=AliasChoices("NET_RATE_LIMIT_USER_READ", "RATE_LIMIT_USER_READ"),
        description="Per-user rate limit for read-scoped requests (req/s)",
    )
    rate_limit_user_write: float = Field(
        default=2.0,
        gt=0,
        le=100.0,
        validation_alias=AliasChoices("NET_RATE_LIMIT_USER_WRITE", "RATE_LIMIT_USER_WRITE"),
        description="Per-user rate limit for write-scoped requests (req/s)",
    )
    rate_limit_user_admin: float = Field(
        default=20.0,
        gt=0,
        le=200.0,
        validation_alias=AliasChoices("NET_RATE_LIMIT_USER_ADMIN", "RATE_LIMIT_USER_ADMIN"),
        description="Per-user rate limit for admin-scoped requests (req/s)",
    )

    # Per-tenant rate limiting (aggregate requests per second across all users)
    rate_limit_tenant: float = Field(
        default=50.0,
        gt=0,
        le=1000.0,
        validation_alias=AliasChoices("NET_RATE_LIMIT_TENANT", "RATE_LIMIT_TENANT"),
        description="Per-tenant aggregate rate limit (req/s)",
    )

    # Session management
    net_session_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_SESSION_ENABLED"),
        description="Enable session tracking and management",
    )
    net_session_max_idle: int = Field(
        default=3600,
        ge=60,
        le=86400,
        validation_alias=AliasChoices("NET_SESSION_MAX_IDLE"),
        description="Maximum idle time before session expiry (seconds)",
    )
    net_session_max_per_user: int = Field(
        default=10,
        ge=1,
        le=100,
        validation_alias=AliasChoices("NET_SESSION_MAX_PER_USER"),
        description="Maximum concurrent sessions per user",
    )

    # HTTP session resumption (session state persistence for client reconnection)
    net_http_session_resumption: bool = Field(
        default=True,
        validation_alias=AliasChoices("NET_HTTP_SESSION_RESUMPTION"),
        description="Enable HTTP session state persistence for transport session resumption",
    )
    net_http_session_ttl: int = Field(
        default=3600,
        ge=60,
        le=86400,
        validation_alias=AliasChoices("NET_HTTP_SESSION_TTL"),
        description="HTTP session state TTL in seconds (default: 1 hour)",
    )
    net_http_session_max: int = Field(
        default=1000,
        ge=10,
        le=10000,
        validation_alias=AliasChoices("NET_HTTP_SESSION_MAX"),
        description="Maximum concurrent HTTP sessions tracked",
    )
    net_http_session_max_history: int = Field(
        default=100,
        ge=10,
        le=1000,
        validation_alias=AliasChoices("NET_HTTP_SESSION_MAX_HISTORY"),
        description="Maximum tool call history entries per HTTP session",
    )

    # Cache settings
    net_cache_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("NET_CACHE_ENABLED", "EOS_CACHE_ENABLED"),
    )
    net_cache_max_entries: int = Field(
        default=500,
        validation_alias=AliasChoices("NET_CACHE_MAX_ENTRIES", "EOS_CACHE_MAX_ENTRIES"),
    )
    net_cache_max_mb: int = Field(
        default=50,
        validation_alias=AliasChoices("NET_CACHE_MAX_MB", "EOS_CACHE_MAX_MB"),
    )
    net_cache_default_ttl: int = Field(
        default=30,
        ge=0,
        le=3600,
        validation_alias=AliasChoices("NET_CACHE_DEFAULT_TTL"),
        description="Default cache TTL in seconds for commands without a pattern-specific TTL",
    )
    net_cache_stale_if_error_ttl: int = Field(
        default=300,
        ge=0,
        le=3600,
        validation_alias=AliasChoices("NET_CACHE_STALE_IF_ERROR_TTL"),
        description="How long (seconds) to serve stale cache entries when device is unreachable (0=disabled)",
    )
    net_cache_encrypt: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_CACHE_ENCRYPT"),
        description="Enable Fernet encryption for cached command values at rest",
    )
    net_cache_encryption_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_CACHE_ENCRYPTION_KEY"),
        description="Explicit Fernet encryption key (32 url-safe base64 bytes); falls back to AUTH_SECRET_KEY",
    )

    # Graceful shutdown settings
    net_shutdown_timeout: int = Field(
        default=30,
        ge=5,
        le=300,
        validation_alias=AliasChoices("NET_SHUTDOWN_TIMEOUT"),
        description="Seconds to wait for in-flight requests during graceful shutdown",
    )

    # Circuit breaker settings
    net_circuit_breaker: bool = Field(
        default=True,
        validation_alias=AliasChoices("NET_CIRCUIT_BREAKER", "EOS_CIRCUIT_BREAKER"),
        description="Enabled by default; opt-out via NET_CIRCUIT_BREAKER=false",
    )
    net_cb_threshold: int = Field(
        default=3,
        validation_alias=AliasChoices("NET_CB_THRESHOLD", "EOS_CB_THRESHOLD"),
        description="Consecutive failures before opening circuit",
    )
    net_cb_cooldown: int = Field(
        default=60,
        validation_alias=AliasChoices("NET_CB_COOLDOWN", "EOS_CB_COOLDOWN"),
        description="Seconds before CIRCUIT_OPEN -> HALF_OPEN retry",
    )

    # Workflow tool settings
    net_workflow_timeout: int = Field(
        default=30,
        validation_alias=AliasChoices("NET_WORKFLOW_TIMEOUT", "EOS_WORKFLOW_TIMEOUT"),
        description="Max seconds per workflow tool execution",
    )
    net_snapshot_ttl: int = Field(
        default=3600,
        validation_alias=AliasChoices("NET_SNAPSHOT_TTL", "EOS_SNAPSHOT_TTL"),
        description="Snapshot expiry in seconds (default 1 hour)",
    )
    net_snapshot_dir: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_SNAPSHOT_DIR", "EOS_SNAPSHOT_DIR"),
        description="Optional dir for persistent snapshots (None = in-memory only)",
    )

    # Progressive discovery
    net_progressive_discovery: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_PROGRESSIVE_DISCOVERY", "EOS_PROGRESSIVE_DISCOVERY"),
        description="When True, only meta-tools + workflow tools at startup",
    )

    # Selective vendor loading
    net_vendors: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_VENDORS"),
        description=(
            "Comma-separated vendor platforms to load tools for (eos, iosxe, nxos, junos). "
            "Default: all platforms whose dependencies are installed."
        ),
    )

    # Config retry (disabled by default to prevent double-apply risk)
    config_retry_enabled: bool = Field(
        default=False, description="Enable retry for config commands (risk: double-apply)"
    )

    # Observability settings
    net_metrics: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_METRICS", "EOS_METRICS"),
        description="Opt-in metrics collection via NET_METRICS=true",
    )

    # SLO/SLI tracking
    net_slo_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("NET_SLO_ENABLED"),
        description="Enable SLO/SLI tracking",
    )
    net_slo_window: int = Field(
        default=3600,
        validation_alias=AliasChoices("NET_SLO_WINDOW"),
        description="Default SLO evaluation window in seconds",
    )

    # OpenTelemetry settings
    net_otel_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_OTEL_ENABLED", "EOS_OTEL_ENABLED"),
        description="Enable OpenTelemetry tracing and metrics",
    )
    net_otel_endpoint: str = Field(
        default="",
        validation_alias=AliasChoices("NET_OTEL_ENDPOINT", "EOS_OTEL_ENDPOINT"),
        description="OTLP gRPC endpoint (e.g., http://localhost:4317)",
    )
    net_otel_service_name: str = Field(
        default="network-mcp",
        validation_alias=AliasChoices("NET_OTEL_SERVICE_NAME", "EOS_OTEL_SERVICE_NAME"),
        description="Service name reported to the OTel collector",
    )

    # TLS certificate verification for RESTCONF/NX-API connections
    net_verify_ssl: bool = Field(
        default=True,
        validation_alias=AliasChoices("NET_VERIFY_SSL", "EOS_VERIFY_SSL", "VERIFY_SSL"),
        description="Verify TLS certificates for RESTCONF/NX-API connections",
    )

    # SSH settings (for IOS-XE scrapli transport)
    ssh_strict_host_key: bool = Field(default=True, description="Verify SSH host keys")
    ssh_known_hosts_file: str = Field(default="~/.ssh/known_hosts", description="SSH known hosts file path")

    # gNMI settings
    gnmi_port: int = Field(
        default=6030,
        validation_alias=AliasChoices("NET_GNMI_PORT", "GNMI_PORT"),
    )
    gnmi_insecure: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_GNMI_INSECURE", "GNMI_INSECURE"),
    )
    gnmi_timeout: float = Field(
        default=10.0,
        ge=1.0,
        le=120.0,
        validation_alias=AliasChoices("NET_GNMI_TIMEOUT", "GNMI_TIMEOUT"),
    )
    gnmi_tls_cert: str | None = None
    gnmi_tls_key: str | None = None
    gnmi_tls_ca: str | None = None
    net_gnmi_pool_max: int = Field(default=20, ge=1, le=500)
    net_gnmi_pool_ttl: int = Field(default=300, ge=10, le=3600)

    # Palo Alto PAN-OS settings
    net_panos_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_PANOS_API_KEY"),
        description="PAN-OS XML API key for authentication (alternative to username/password)",
    )

    # CloudVision Portal settings
    eos_cvp_url: str | None = None  # CVP URL (e.g., https://cvp.example.com)
    eos_cvp_token: SecretStr | None = None  # CVP service account token
    eos_cvp_verify_ssl: bool = False  # Verify SSL certificates for CVP connections

    # NetBox DCIM integration settings
    netbox_url: str | None = None  # NetBox URL (e.g., https://netbox.example.com)
    netbox_token: SecretStr | None = None  # NetBox API token
    net_netbox_retries: int = Field(default=3, ge=1, le=10)
    net_netbox_retry_delay: float = Field(default=1.0, ge=0.1, le=30.0)

    # Plugin security
    plugin_allowlist: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("NET_PLUGIN_ALLOWLIST", "EOS_PLUGIN_ALLOWLIST"),
    )
    plugin_blocklist: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("NET_PLUGIN_BLOCKLIST", "EOS_PLUGIN_BLOCKLIST"),
    )

    # Configuration drift detection settings
    net_golden_config_dir: str = Field(
        default="",
        validation_alias=AliasChoices("NET_GOLDEN_CONFIG_DIR", "EOS_GOLDEN_CONFIG_DIR"),
        description="Directory containing golden config files ({device}.conf)",
    )
    net_drift_check_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_DRIFT_CHECK_ENABLED", "EOS_DRIFT_CHECK_ENABLED"),
        description="Enable configuration drift detection tools",
    )

    # Change management workflow settings
    net_change_mgmt_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_CHANGE_MGMT_ENABLED", "EOS_CHANGE_MGMT_ENABLED"),
        description="Enable enterprise change management workflows for write operations",
    )
    net_change_require_approval: bool = Field(
        default=True,
        validation_alias=AliasChoices("NET_CHANGE_REQUIRE_APPROVAL", "EOS_CHANGE_REQUIRE_APPROVAL"),
        description="Require explicit approval for change requests (auto-approve LOW risk when False)",
    )
    net_maintenance_windows: str = Field(
        default="*",
        validation_alias=AliasChoices("NET_MAINTENANCE_WINDOWS", "EOS_MAINTENANCE_WINDOWS"),
        description="Maintenance windows: 'Sun 02:00-06:00,Sat 02:00-06:00' or '*' for always",
    )
    net_auto_rollback_timeout: int = Field(
        default=300,
        validation_alias=AliasChoices("NET_AUTO_ROLLBACK_TIMEOUT", "EOS_AUTO_ROLLBACK_TIMEOUT"),
        description="Seconds before auto-rollback on failed validation (0=disabled)",
    )
    net_four_eyes_threshold: str = Field(
        default="CRITICAL",
        validation_alias=AliasChoices("NET_FOUR_EYES_THRESHOLD", "EOS_FOUR_EYES_THRESHOLD"),
        description="Minimum risk level requiring two approvals (LOW, MEDIUM, HIGH, CRITICAL)",
    )

    # Feature flags (JSON string parsed into dict[str, FeatureFlag])
    net_feature_flags: str = Field(
        default="{}",
        validation_alias=AliasChoices("NET_FEATURE_FLAGS", "EOS_FEATURE_FLAGS"),
        description="JSON string mapping feature names to {enabled, tenants} objects",
    )

    # Idempotency key support (prevents duplicate write operations)
    net_idempotency_key_ttl: int = Field(
        default=3600,
        ge=0,
        le=86400,
        validation_alias=AliasChoices("NET_IDEMPOTENCY_KEY_TTL"),
        description="TTL for idempotency keys in seconds (0=disabled, default=3600)",
    )

    # Distributed state backend (for horizontal scaling)
    net_distributed_backend: str = Field(
        default="local",
        validation_alias=AliasChoices("NET_DISTRIBUTED_BACKEND", "EOS_DISTRIBUTED_BACKEND"),
        description="State backend: 'local' (in-memory) or 'redis' (distributed)",
    )
    net_redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("NET_REDIS_URL", "EOS_REDIS_URL"),
        description="Redis connection URL for distributed backend",
    )

    # Leader election (requires NET_DISTRIBUTED_BACKEND=redis)
    net_leader_election: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_LEADER_ELECTION"),
        description="Enable Redis-based leader election for singleton operations",
    )
    net_leader_lease_ttl: int = Field(
        default=30,
        ge=5,
        le=300,
        validation_alias=AliasChoices("NET_LEADER_LEASE_TTL"),
        description="Leader lease TTL in seconds (5-300)",
    )

    # Prometheus metrics export
    prometheus_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_PROMETHEUS_ENABLED", "PROMETHEUS_ENABLED"),
    )
    net_prometheus_max_workers: int = Field(default=5, ge=1, le=50)

    # ServiceNow integration settings
    snow_instance: str | None = Field(default=None, alias="SNOW_INSTANCE")  # Instance name (e.g., "mycompany")
    snow_username: str | None = Field(default=None, alias="SNOW_USERNAME")
    snow_password: SecretStr | None = Field(default=None, alias="SNOW_PASSWORD")
    net_snow_ci_class: str = "cmdb_ci_ip_switch"

    # Audit log hardening
    audit_file: str | None = Field(default=None, alias="AUDIT_FILE")
    audit_max_bytes: int = Field(default=10_485_760, alias="AUDIT_MAX_BYTES")  # 10MB
    audit_backup_count: int = Field(default=5, alias="AUDIT_BACKUP_COUNT")
    audit_compress: bool = Field(default=True, alias="AUDIT_COMPRESS")
    audit_signing_key: SecretStr | None = Field(default=None, alias="AUDIT_SIGNING_KEY")
    net_audit_syslog: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_AUDIT_SYSLOG"),
        description="Enable RFC 5424 structured syslog forwarding for SIEM integration",
    )
    audit_syslog_host: str = Field(
        default="localhost",
        validation_alias=AliasChoices("NET_AUDIT_SYSLOG_HOST", "AUDIT_SYSLOG_HOST"),
    )
    audit_syslog_port: int = Field(
        default=514,
        validation_alias=AliasChoices("NET_AUDIT_SYSLOG_PORT", "AUDIT_SYSLOG_PORT"),
    )

    # Audit retention and archival
    net_audit_retention_days: int = Field(
        default=2555,
        ge=1,
        validation_alias=AliasChoices("NET_AUDIT_RETENTION_DAYS", "EOS_AUDIT_RETENTION_DAYS"),
        description="Audit log retention period in days (default 7 years)",
    )
    net_audit_archive_dir: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NET_AUDIT_ARCHIVE_DIR", "EOS_AUDIT_ARCHIVE_DIR"),
        description="Directory for archived audit logs",
    )
    net_require_audit_signing: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_REQUIRE_AUDIT_SIGNING", "EOS_REQUIRE_AUDIT_SIGNING"),
        description="When True, all audit entries must be HMAC-signed",
    )
    audit_signing_key_version: int = Field(
        default=1,
        ge=1,
        validation_alias=AliasChoices("AUDIT_SIGNING_KEY_VERSION"),
        description="Version identifier included in signed audit entries for key rotation",
    )

    # Containerlab lifecycle management (deploy/destroy/status)
    net_containerlab_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("NET_CONTAINERLAB_ENABLED"),
        description="Enable Containerlab lifecycle management tools (deploy, destroy, status)",
    )

    # HashiCorp Vault integration settings
    vault_enabled: bool = Field(default=False, alias="VAULT_ENABLED")
    vault_addr: str | None = Field(default=None, alias="VAULT_ADDR")
    vault_token: SecretStr | None = Field(default=None, alias="VAULT_TOKEN")
    vault_mount: str = Field(default="secret", alias="VAULT_MOUNT_POINT")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "populate_by_name": True}

    @property
    def disabled_modules(self) -> set[str]:
        """Parse NET_DISABLED_MODULES into a set of module names."""
        if not self.net_disabled_modules:
            return set()
        return {m.strip() for m in self.net_disabled_modules.split(",") if m.strip()}

    _KNOWN_OPTIONAL_MODULES: ClassVar[frozenset[str]] = frozenset(
        {"evpn_vxlan", "security", "vrf", "bfd", "event_monitor", "qos", "compliance"}
    )
    _KNOWN_VENDORS: ClassVar[frozenset[str]] = frozenset({"eos", "iosxe", "nxos", "junos", "sonic", "panos"})

    @model_validator(mode="after")
    def _validate_module_names(self) -> "NetworkSettings":
        """Validate that disabled/enabled module names and vendor names are recognized."""
        for name in self.disabled_modules:
            if name not in self._KNOWN_OPTIONAL_MODULES:
                raise ValueError(
                    f"Unknown module in NET_DISABLED_MODULES: {name!r}. Valid: {sorted(self._KNOWN_OPTIONAL_MODULES)}"
                )
        if self.enabled_modules is not None:
            for name in self.enabled_modules:
                if name not in self._KNOWN_OPTIONAL_MODULES:
                    raise ValueError(
                        f"Unknown module in NET_ENABLED_MODULES: {name!r}. "
                        f"Valid: {sorted(self._KNOWN_OPTIONAL_MODULES)}"
                    )
        if self.enabled_vendors is not None:
            for vendor in self.enabled_vendors:
                if vendor not in self._KNOWN_VENDORS:
                    raise ValueError(
                        f"Unknown vendor in NET_VENDORS: {vendor!r}. Valid: {sorted(self._KNOWN_VENDORS)}"
                    )
        return self

    @property
    def enabled_modules(self) -> set[str] | None:
        """Parse NET_ENABLED_MODULES into a set of module names, or None if not set."""
        if self.net_enabled_modules is None:
            return None
        return {m.strip() for m in self.net_enabled_modules.split(",") if m.strip()}

    @property
    def enabled_vendors(self) -> set[str] | None:
        """Parse NET_VENDORS into a set of vendor names, or None if not set (meaning all vendors).

        Returns None when NET_VENDORS is unset, which means all installed vendor tools should load.
        Returns a set of lowercase vendor strings when NET_VENDORS is specified.
        """
        if self.net_vendors is None:
            return None
        return {v.strip().lower() for v in self.net_vendors.split(",") if v.strip()}

    @property
    def feature_flags(self) -> dict[str, FeatureFlag]:
        """Parse NET_FEATURE_FLAGS JSON into a dict of FeatureFlag objects.

        Returns an empty dict if the JSON is invalid or empty.
        """
        if not self.net_feature_flags or self.net_feature_flags.strip() == "{}":
            return {}
        try:
            raw = json.loads(self.net_feature_flags)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid JSON in NET_FEATURE_FLAGS; treating as empty")
            return {}
        if not isinstance(raw, dict):
            logger.warning("NET_FEATURE_FLAGS must be a JSON object; treating as empty")
            return {}
        result: dict[str, FeatureFlag] = {}
        for name, value in raw.items():
            if isinstance(value, dict):
                try:
                    result[name] = FeatureFlag(**value)
                except Exception:  # noqa: BLE001
                    logger.warning("Invalid feature flag definition for %r; skipping", name)
            else:
                logger.warning("Feature flag %r must be an object; skipping", name)
        return result


# --- Settings that are safe to reload at runtime ---
# These do NOT require a server restart to take effect.
_RELOADABLE_SETTINGS: frozenset[str] = frozenset(
    {
        "rate_limit_show",
        "rate_limit_config",
        "net_cache_default_ttl",
        "net_cb_threshold",
        "net_cb_cooldown",
        "log_level",
        "timeout_connect",
        "timeout_show",
        "timeout_config",
    }
)

# --- Secret field names (values MUST be redacted in audit logs and config output) ---
_SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "net_password",
        "auth_secret_key",
        "eos_cvp_token",
        "netbox_token",
        "snow_password",
        "audit_signing_key",
        "vault_token",
        "net_panos_api_key",
    }
)


def reload_settings(live_settings: "NetworkSettings") -> dict[str, dict[str, object]]:
    """Re-read environment variables and update safe-to-reload settings on the live instance.

    This function reads fresh values from the environment and .env file, then
    updates ONLY the settings in _RELOADABLE_SETTINGS. Unsafe settings (auth,
    modules, credentials, transport) are never modified.

    After updating, propagates changes to live subsystems:
    - Rate limiter: updates show/config rates on existing buckets
    - Circuit breaker: updates threshold/cooldown on the registry
    - Log level: applies immediately via logging.getLogger().setLevel()
    - Cache default TTL: updates the module-level _FALLBACK_TTL

    Args:
        live_settings: The running NetworkSettings instance to update in place.

    Returns:
        Dict of changed settings: {"setting_name": {"old": old_val, "new": new_val}}.
    """
    # Read fresh settings from env/dotenv
    fresh = NetworkSettings()

    changes: dict[str, dict[str, object]] = {}

    for field_name in _RELOADABLE_SETTINGS:
        old_value = getattr(live_settings, field_name)
        new_value = getattr(fresh, field_name)

        if old_value != new_value:
            changes[field_name] = {"old": old_value, "new": new_value}
            # Update the live instance (Pydantic models are normally frozen,
            # but we use object.__setattr__ to bypass immutability)
            object.__setattr__(live_settings, field_name, new_value)

    # --- Propagate changes to live subsystems ---

    if "log_level" in changes:
        new_level = changes["log_level"]["new"]
        logging.getLogger().setLevel(getattr(logging, str(new_level)))
        logger.info("Log level changed to %s", new_level)

    if "rate_limit_show" in changes or "rate_limit_config" in changes:
        _propagate_rate_limiter(live_settings)

    if "net_cb_threshold" in changes or "net_cb_cooldown" in changes:
        _propagate_circuit_breaker(live_settings)

    if "net_cache_default_ttl" in changes:
        _propagate_cache_ttl(live_settings)

    return changes


def _propagate_rate_limiter(settings: "NetworkSettings") -> None:
    """Update the live rate limiter with new show/config rates."""
    try:
        from network_mcp.helpers import device_rate_limiter

        # Update the internal rates — new buckets will use these values.
        # For DeviceRateLimiter, update the stored rates so new buckets pick them up.
        device_rate_limiter._show_rate = settings.rate_limit_show
        device_rate_limiter._config_rate = settings.rate_limit_config
        # Clear existing buckets so they are re-created with new rates
        with device_rate_limiter._lock:
            device_rate_limiter._show_buckets.clear()
            device_rate_limiter._config_buckets.clear()
        logger.info(
            "Rate limiter updated: show=%.1f/s, config=%.1f/s",
            settings.rate_limit_show,
            settings.rate_limit_config,
        )
    except Exception:
        logger.warning("Failed to propagate rate limiter changes", exc_info=True)


def _propagate_circuit_breaker(settings: "NetworkSettings") -> None:
    """Update the live circuit breaker registry with new threshold/cooldown."""
    try:
        from network_mcp.helpers import circuit_breaker_registry

        circuit_breaker_registry._threshold = settings.net_cb_threshold
        circuit_breaker_registry._cooldown = settings.net_cb_cooldown
        logger.info(
            "Circuit breaker updated: threshold=%d, cooldown=%ds",
            settings.net_cb_threshold,
            settings.net_cb_cooldown,
        )
    except Exception:
        logger.warning("Failed to propagate circuit breaker changes", exc_info=True)


def _propagate_cache_ttl(settings: "NetworkSettings") -> None:
    """Update the default cache TTL used for commands without a pattern-specific TTL."""
    try:
        import network_mcp.cache as cache_module

        cache_module._FALLBACK_TTL = settings.net_cache_default_ttl
        logger.info("Cache default TTL updated: %ds", settings.net_cache_default_ttl)
    except Exception:
        logger.warning("Failed to propagate cache TTL changes", exc_info=True)


def get_safe_config(settings: "NetworkSettings") -> dict[str, object]:
    """Return all non-secret settings as a plain dict for health/config endpoints.

    Excludes ALL secret fields (passwords, tokens, keys) and internal model metadata.
    Safe for inclusion in HTTP responses and logs.
    """
    safe: dict[str, object] = {}
    for field_name in type(settings).model_fields:
        if field_name in _SECRET_FIELDS:
            continue
        value = getattr(settings, field_name)
        # Convert SecretStr to redacted marker (extra safety for any SecretStr we missed)
        if isinstance(value, SecretStr):
            safe[field_name] = "***"
        else:
            safe[field_name] = value
    return safe


# Backward compatibility alias
EosSettings = NetworkSettings
