"""Network MCP Server — AI-powered multi-vendor network automation."""

import argparse
import asyncio
import importlib
import logging
import os
import signal
import sys
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from . import __version__
from .config import NetworkSettings
from .connection import ConnectionManager

# Configure logging to stderr (CRITICAL: never log to stdout with stdio transport)
settings = NetworkSettings()

from .middleware import RequestIdFilter  # noqa: E402

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] %(message)s",
    stream=sys.stderr,
)
# Add request ID filter to root logger AND all its handlers so request_id is always injected
_request_id_filter = RequestIdFilter()
logging.getLogger().addFilter(_request_id_filter)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_request_id_filter)
logger = logging.getLogger("network-mcp")


# --- Auth fail-fast validation ---
# Validate auth configuration early so operators see clear errors at startup.
if settings.auth_enabled:
    if not settings.auth_secret_key or not settings.auth_secret_key.get_secret_value():
        raise SystemExit("AUTH_ENABLED=true but AUTH_SECRET_KEY is empty. Cannot start.")
    if settings.rbac_enabled:
        logger.info("RBAC enabled with scopes: network:read, network:write, network:admin, network:audit")

if settings.rbac_enabled and not settings.auth_enabled:
    logger.warning(
        "NET_RBAC_ENABLED=true but AUTH_ENABLED=false. RBAC requires authentication — RBAC will be inactive."
    )


# --- Auth configuration for FastMCP ---
# When AUTH_ENABLED=true, we build AuthSettings + TokenVerifier for FastMCP's
# built-in Bearer token middleware. Auth only applies to HTTP transport; stdio is unaffected.

_auth_settings = None
_token_verifier = None

if settings.auth_enabled:
    from .auth import JWT_AVAILABLE, JWTTokenVerifier, TokenVerifier

    if not JWT_AVAILABLE:
        logger.warning(
            "AUTH_ENABLED=true but PyJWT is not installed. "
            "Auth will be disabled. Install with: pip install latticio[auth]"
        )
    elif not settings.auth_issuer_url:
        logger.warning(
            "AUTH_ENABLED=true but AUTH_ISSUER_URL is not set. "
            "Auth will be disabled. Set AUTH_ISSUER_URL in your environment."
        )
    else:
        from mcp.server.auth.settings import AuthSettings

        jwt_verifier = JWTTokenVerifier(
            secret_key=settings.auth_secret_key.get_secret_value(),  # type: ignore[union-attr]
            issuer=settings.auth_issuer_url,
        )
        _token_verifier = TokenVerifier(jwt_verifier)
        _required_scopes = [s.strip() for s in settings.auth_required_scopes.split(",") if s.strip()]
        _auth_settings = AuthSettings(
            issuer_url=settings.auth_issuer_url,  # type: ignore[arg-type]
            required_scopes=_required_scopes or None,
            resource_server_url=settings.auth_resource_server_url,  # type: ignore[arg-type]
        )
        logger.info(
            "JWT authentication enabled for HTTP transport "
            f"(issuer={settings.auth_issuer_url}, scopes={_required_scopes})"
        )

# --- API Key authentication integration ---
# When NET_API_KEY_ENABLED=true, attach an APIKeyManager to the token verifier.
# API keys are detected by prefix and validated via hash lookup instead of JWT.
_api_key_manager = None
if settings.api_key_enabled:
    from .api_keys import APIKeyManager

    _api_key_manager = APIKeyManager(
        key_file=settings.api_key_file,
        prefix=settings.api_key_prefix,
    )
    if _token_verifier is not None:
        _token_verifier.set_api_key_manager(_api_key_manager, prefix=settings.api_key_prefix)
        logger.info(
            "API key authentication enabled (prefix=%s, file=%s)",
            settings.api_key_prefix,
            settings.api_key_file,
        )
    else:
        logger.warning(
            "NET_API_KEY_ENABLED=true but AUTH_ENABLED=false or JWT not configured. "
            "API keys require AUTH_ENABLED=true to function."
        )

# --- mTLS client certificate authentication ---
# When NET_MTLS_ENABLED=true, attach a CertificateVerifier to the token verifier.
# Client certificates are validated against the CA and mapped to access tokens.
_cert_verifier = None
if settings.mtls_enabled:
    from .auth import CRYPTO_AVAILABLE, CertificateVerifier

    if not CRYPTO_AVAILABLE:
        logger.warning(
            "NET_MTLS_ENABLED=true but cryptography is not installed. "
            "mTLS will be disabled. Install with: pip install latticio[mtls]"
        )
    else:
        _cert_verifier = CertificateVerifier.from_settings(
            ca_cert_path=settings.mtls_ca_cert,
            allowed_cns_str=settings.mtls_allowed_cns,
            allowed_ous_str=settings.mtls_allowed_ous,
            scope_mapping_json=settings.mtls_scope_mapping,
        )
        if _token_verifier is not None:
            _token_verifier.set_cert_verifier(_cert_verifier)
            logger.info(
                "mTLS client certificate authentication enabled (ca=%s)",
                settings.mtls_ca_cert or "any-issuer",
            )
        else:
            logger.warning(
                "NET_MTLS_ENABLED=true but AUTH_ENABLED=false or JWT not configured. "
                "mTLS requires AUTH_ENABLED=true to function."
            )

# --- Vault fail-fast validation ---
_vault_provider = None
if settings.vault_enabled:
    if not settings.vault_addr:
        raise SystemExit("VAULT_ENABLED=true but VAULT_ADDR is empty. Cannot start.")
    if not settings.vault_token or not settings.vault_token.get_secret_value():
        raise SystemExit("VAULT_ENABLED=true but VAULT_TOKEN is empty. Cannot start.")
    try:
        from network_mcp.integrations.vault import VaultCredentialProvider

        _vault_provider = VaultCredentialProvider(
            addr=settings.vault_addr,
            token=settings.vault_token.get_secret_value(),
            mount_point=settings.vault_mount,
        )
        logger.info("Vault credential provider enabled (addr=%s, mount=%s)", settings.vault_addr, settings.vault_mount)
    except ImportError as e:
        raise SystemExit(
            "VAULT_ENABLED=true but hvac is not installed. Install with: pip install latticio[vault]"
        ) from e
    except RuntimeError as e:
        raise SystemExit(f"Vault authentication failed at startup: {e}") from e

# --- Inventory file validation ---
if settings.net_inventory_file and not settings.net_demo_mode and not os.path.exists(settings.net_inventory_file):
    raise SystemExit(
        f"NET_INVENTORY_FILE is set to '{settings.net_inventory_file}' but the file does not exist. "
        "Check the path or unset NET_INVENTORY_FILE to run without an inventory file."
    )


@dataclass
class AppContext:
    """Application context available during server lifespan."""

    conn_mgr: ConnectionManager
    config: NetworkSettings


def _build_inventory_backend(settings: NetworkSettings):
    """Build the appropriate InventoryBackend based on settings."""
    if settings.net_demo_mode:
        from .mock_data.inventory import DemoInventoryBackend

        return DemoInventoryBackend()

    if settings.net_inventory_file and os.path.exists(settings.net_inventory_file):
        from .inventory import YamlInventoryBackend

        return YamlInventoryBackend(
            path=settings.net_inventory_file,
            default_username=settings.net_username,
            default_password=settings.net_password.get_secret_value(),
            default_transport=settings.net_transport,
        )
    return None


# Module-level connection manager — tool modules import this directly.
# Lifespan populates it on startup and cleans it up on shutdown.
_inventory_backend = _build_inventory_backend(settings)
conn_mgr = ConnectionManager(settings=settings, inventory_backend=_inventory_backend, vault_provider=_vault_provider)

if settings.net_demo_mode:
    logger.info("Running in DEMO MODE — no real devices connected")
    logger.info(f"Demo inventory loaded: {len(conn_mgr._inventory)} devices ({', '.join(conn_mgr.list_devices())})")
elif _inventory_backend is not None:
    logger.info(f"Loaded inventory: {len(conn_mgr._inventory)} devices")

# --- HTTP session resumption state store ---
# Module-level store for HTTP session state persistence. Initialized when
# NET_HTTP_SESSION_RESUMPTION=true (default). Tools and wrappers import this.
http_session_store = None
if settings.net_http_session_resumption:
    from .http_session import HTTPSessionStore

    http_session_store = HTTPSessionStore(
        ttl_seconds=settings.net_http_session_ttl,
        max_sessions=settings.net_http_session_max,
        max_history_per_session=settings.net_http_session_max_history,
    )
    logger.info(
        "HTTP session resumption enabled (ttl=%ds, max=%d, history=%d)",
        settings.net_http_session_ttl,
        settings.net_http_session_max,
        settings.net_http_session_max_history,
    )

# --- Tenant isolation filter ---
# Built from the inventory so TenantDeviceFilter can map device names to allowed tenants.
_tenant_filter = None
if settings.tenant_isolation_enabled:
    from .rbac import TenantDeviceFilter

    _tenant_filter = TenantDeviceFilter(conn_mgr.get_inventory())
    logger.info("Tenant isolation enabled — device access restricted by tenant_id")


# --- Graceful Shutdown State ---
_shutting_down = False
_in_flight_count = 0
_in_flight_lock = threading.Lock()
_shutdown_event = threading.Event()


def increment_in_flight() -> bool:
    """Increment in-flight counter. Returns False if server is shutting down (reject new work)."""
    global _in_flight_count
    with _in_flight_lock:
        if _shutting_down:
            return False
        _in_flight_count += 1
        return True


def decrement_in_flight() -> None:
    """Decrement in-flight counter and signal shutdown waiter if at zero."""
    global _in_flight_count
    with _in_flight_lock:
        _in_flight_count = max(0, _in_flight_count - 1)
        if _shutting_down and _in_flight_count == 0:
            _shutdown_event.set()


def is_shutting_down() -> bool:
    """Return True if the server is in shutdown mode."""
    return _shutting_down


def get_in_flight_count() -> int:
    """Return the current number of in-flight requests."""
    with _in_flight_lock:
        return _in_flight_count


def _initiate_shutdown() -> None:
    """Signal handler callback — sets shutdown flag."""
    global _shutting_down
    logger.info("Received shutdown signal")
    _shutting_down = True


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Lifespan context manager for proper startup/shutdown."""
    global _shutting_down
    logger.info("Network MCP server starting up")

    # Initialize idempotency store if enabled
    if settings.net_idempotency_key_ttl > 0:
        from .idempotency import init_idempotency_store

        init_idempotency_store(ttl=settings.net_idempotency_key_ttl)
        logger.info("Idempotency key support enabled (TTL=%ds)", settings.net_idempotency_key_ttl)

    # M1: Initialize session manager if enabled
    _session_mgr = None
    if settings.net_session_enabled:
        from .session_mgr import SessionManager

        _session_mgr = SessionManager(
            max_idle=settings.net_session_max_idle,
            max_per_user=settings.net_session_max_per_user,
        )
        logger.info(
            "Session manager enabled (max_idle=%ds, max_per_user=%d)",
            settings.net_session_max_idle,
            settings.net_session_max_per_user,
        )

    # M2: Initialize leader elector if enabled
    _leader_elector = None
    if settings.net_leader_election:
        from .leader import LeaderElector

        _leader_elector = LeaderElector(ttl=settings.net_leader_lease_ttl)
        _leader_elector.start_renewal()
        logger.info("Leader election enabled (lease_ttl=%ds)", settings.net_leader_lease_ttl)

    # Initialize OpenTelemetry if enabled
    if settings.net_otel_enabled:
        from .telemetry import setup_telemetry

        ok = setup_telemetry(
            service_name=settings.net_otel_service_name,
            endpoint=settings.net_otel_endpoint,
        )
        if ok:
            logger.info("OpenTelemetry telemetry active")
        else:
            logger.warning("NET_OTEL_ENABLED=true but OpenTelemetry SDK not installed — telemetry inactive")

    # Register signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _initiate_shutdown)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    ctx = AppContext(conn_mgr=conn_mgr, config=settings)
    try:
        yield ctx
    finally:
        shutdown_timeout = settings.net_shutdown_timeout
        logger.info("Network MCP server shutting down — draining in-flight requests")
        _shutting_down = True

        # Wait for in-flight requests to complete (with timeout)
        in_flight = get_in_flight_count()
        if in_flight > 0:
            logger.info("Waiting for %d in-flight requests to complete (timeout=%ds)", in_flight, shutdown_timeout)
            drained = _shutdown_event.wait(timeout=shutdown_timeout)
            if not drained:
                remaining = get_in_flight_count()
                logger.warning("Shutdown timeout — %d requests still in-flight, force-closing", remaining)
            else:
                logger.info("All in-flight requests completed")

        # Stop leader elector if running
        if _leader_elector is not None:
            _leader_elector.stop_renewal()
            logger.debug("Leader elector stopped")

        # Clean up HTTP session store
        if http_session_store is not None:
            cleaned = http_session_store.cleanup_expired()
            remaining = http_session_store.session_count
            if cleaned or remaining:
                logger.debug("HTTP session store: cleaned %d expired, %d remaining", cleaned, remaining)

        # Shut down OTel providers if they were initialized
        if settings.net_otel_enabled:
            from .telemetry import shutdown as otel_shutdown

            otel_shutdown()
        conn_mgr.close_all()
        logger.info("Network MCP server shutdown complete")


# Initialize MCP server with lifespan and optional auth
mcp = FastMCP(
    "Network MCP",
    token_verifier=_token_verifier,
    lifespan=app_lifespan,
    auth=_auth_settings,
)

# Enable experimental MCP Tasks support for long-running operations
try:
    mcp._mcp_server.experimental.enable_tasks()  # type: ignore[attr-defined]
    logger.info("MCP Tasks (experimental) enabled for async operations")
except Exception:
    logger.debug("MCP Tasks not available in this SDK version", exc_info=True)


# --- RBAC integration (opt-in, HTTP transport only) ---
# When NET_RBAC_ENABLED=true AND AUTH_ENABLED=true, wrap call_tool to check
# JWT scopes against TOOL_SCOPES before executing the tool.
if settings.rbac_enabled and settings.auth_enabled:
    from .audit import audit_logger as _audit_logger
    from .middleware import clear_request_context
    from .rbac import check_authorization, extract_tenant_id, get_required_scope

    _original_call_tool = mcp.call_tool

    async def _rbac_call_tool(name, arguments):  # type: ignore[no-untyped-def]
        """RBAC-aware call_tool wrapper — checks scopes and tenant isolation before tool execution."""
        clear_request_context()  # clean slate: prevent context leak from previous request
        try:
            from mcp.server.auth.middleware.auth_context import get_access_token
        except ImportError:
            # Auth middleware not available — skip RBAC check
            return await _original_call_tool(name, arguments)

        try:
            token = get_access_token()
            if token is not None:
                # Build claims dict from the MCP AccessToken
                token_claims = {
                    "scope": " ".join(token.scopes) if token.scopes else "",
                    "client_id": token.client_id,
                }
                authorized, message = check_authorization(name, token_claims)
                if not authorized:
                    logger.warning(f"RBAC denied: {message} (client={token.client_id})")
                    _audit_logger.log_auth_failure(
                        tool_name=name,
                        reason=message,
                        user=token.client_id,
                        attempted_scope=get_required_scope(name),
                        device=arguments.get("host") if arguments else None,
                    )
                    from mcp.types import TextContent

                    return [
                        TextContent(
                            type="text",
                            text=(f'{{"status": "error", "error": "{message}"}}'),
                        )
                    ]

                # Tenant isolation check
                if _tenant_filter is not None:
                    tenant_id = extract_tenant_id(token_claims)
                    host = arguments.get("host") if arguments else None
                    if host and not _tenant_filter.is_allowed(host, tenant_id):
                        msg = f"Tenant '{tenant_id}' does not have access to device '{host}'"
                        logger.warning(f"Tenant isolation denied: {msg} (client={token.client_id})")
                        _audit_logger.log_auth_failure(
                            tool_name=name,
                            reason=msg,
                            user=token.client_id,
                            device=host,
                        )
                        from mcp.types import TextContent

                        return [
                            TextContent(
                                type="text",
                                text=f'{{"status": "error", "error": "{msg}"}}',
                            )
                        ]

            # Token is None means either:
            # 1. stdio transport (no auth context) — allow through
            # 2. Auth not configured properly — FastMCP's own auth middleware handles this
            return await _original_call_tool(name, arguments)
        finally:
            clear_request_context()  # cleanup after: prevent context leak to next request

    mcp.call_tool = _rbac_call_tool  # type: ignore[assignment]
    logger.info("RBAC enabled — tool access controlled by JWT scope claims")


# --- HTTP session tracking (records tool calls per active session) ---
# Wraps call_tool to automatically record tool calls into tracked sessions.
# Works with or without RBAC — chains onto whatever call_tool is currently set.
if http_session_store is not None:
    from .http_session import ToolCallRecord as _ToolCallRecord

    _tracked_call_tool = mcp.call_tool

    async def _session_tracking_call_tool(name, arguments):  # type: ignore[no-untyped-def]
        """Wrapper that records tool calls into HTTP sessions for resumption."""
        import time as _time

        start = _time.monotonic()
        result = await _tracked_call_tool(name, arguments)
        duration_ms = (_time.monotonic() - start) * 1000

        # Extract result summary (truncate to avoid storing large payloads)
        try:
            if isinstance(result, list) and result:
                text = getattr(result[0], "text", "")
                summary = text[:500] if text else ""
            else:
                summary = str(result)[:500]
        except Exception:
            summary = ""

        # Determine status from result
        status = "success"
        try:
            if summary and '"status": "error"' in summary:
                status = "error"
        except Exception:  # noqa: S110
            pass  # Status detection is best-effort

        # Build record for session history tracking
        record = _ToolCallRecord(
            tool_name=name,
            arguments=arguments or {},
            result_summary=summary,
            timestamp=_time.time(),
            duration_ms=duration_ms,
            status=status,
        )

        # Record into the most recently active non-expired session
        try:
            http_session_store.record_to_most_recent(record)
        except Exception:  # noqa: S110
            pass  # Session tracking is best-effort

        return result

    mcp.call_tool = _session_tracking_call_tool  # type: ignore[assignment]
    logger.info("HTTP session tracking enabled — tool calls recorded for session resumption")


# --- Vendor dependency availability flags ---
# Checked once at startup so module loading blocks can gate on them efficiently.
# These mirror the pattern used by gNMI (GNMI_AVAILABLE from gnmi_connection.py).

try:
    import pyeapi as _pyeapi_check  # noqa: F401

    _PYEAPI_AVAILABLE = True
except ImportError:
    _PYEAPI_AVAILABLE = False

try:
    import httpx as _httpx_check  # noqa: F401

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

try:
    from scrapli_netconf.driver import NetconfDriver as _netconf_check  # noqa: F401

    _SCRAPLI_NETCONF_AVAILABLE = True
except ImportError:
    _SCRAPLI_NETCONF_AVAILABLE = False


def _vendor_enabled(vendor: str) -> bool:
    """Return True when the vendor is included in NET_VENDORS (or NET_VENDORS is unset).

    Args:
        vendor: Lowercase vendor platform string (e.g. "eos", "iosxe", "nxos", "junos").
    """
    ev = settings.enabled_vendors
    return ev is None or vendor in ev


# --- Module load tracking ---
# Track loaded/failed modules for health endpoint visibility
_loaded_modules: set[str] = set()
_failed_modules: dict[str, str] = {}  # module_name -> error message


def _load_module(name: str, path: str, *, required: bool = False) -> None:
    """Import a tool module and track success/failure.

    Args:
        name: Short module name for tracking (e.g. "device", "vlans").
        path: Dotted import path (e.g. ".tools.device").
        required: If True, log at ERROR level on failure; otherwise WARNING.
    """
    try:
        if path.startswith("."):
            importlib.import_module(path, package="network_mcp")
        else:
            importlib.import_module(path)
        _loaded_modules.add(name)
    except Exception as e:
        _failed_modules[name] = str(e)
        if required:
            logger.error("CORE module %s failed to load: %s", name, e)
        else:
            logger.warning("Optional module %s failed to load: %s", name, e)


def get_module_status() -> dict:
    """Return module load status for health endpoint consumption."""
    return {
        "loaded_modules": sorted(_loaded_modules),
        "failed_modules": dict(_failed_modules),
    }


# Import and register all tool modules
# Each module uses `from network_mcp.server import mcp, conn_mgr`
# and decorates functions with @mcp.tool, @mcp.resource, @mcp.prompt

# Inject server settings into helpers module to avoid duplicate NetworkSettings instances (H2)
from .helpers import _init_settings  # noqa: E402

_init_settings(settings)

# Prompts and resources — always loaded (not affected by module filtering)
# Argument completions — always loaded
from . import completions  # noqa: F401, E402
from .prompts import workflows  # noqa: F401, E402
from .resources import inventory  # noqa: F401, E402

# Meta-tools — always loaded (progressive discovery entry point)
from .tools import meta  # noqa: F401, E402
from .tools.meta import _init_meta_settings  # noqa: E402

_init_meta_settings(settings)

# Common tools — always loaded (vendor-agnostic, not affected by module filtering)
_COMMON_TOOL_MODULES = [
    "changes",
    "config",
    "device",
    "drift",
    "interfaces",
    "monitoring",
    "routing",
    "security",
    "switching",
    "troubleshoot",
    "vlans",
    "vrf",
    "workflows",
]
for _common_mod in _COMMON_TOOL_MODULES:
    _load_module(f"common.{_common_mod}", f".tools.common.{_common_mod}", required=True)

# --- Module loading with NET_ENABLED_MODULES / NET_DISABLED_MODULES support ---

_CORE_MODULES = [
    "checkpoints",
    "config_mgmt",
    "device",
    "export",
    "fabric",
    "interfaces",
    "monitoring",
    "routing",
    "sessions",
    "switching",
    "troubleshoot",
    "validation",
    "vlans",
    "workflows",
]
_OPTIONAL_MODULES = ["evpn_vxlan", "security", "vrf", "bfd", "event_monitor", "qos", "compliance", "ai_fabric"]
_ALL_MODULES = _CORE_MODULES + _OPTIONAL_MODULES

_enabled = settings.enabled_modules
_disabled = settings.disabled_modules

if _enabled is not None and _disabled:
    logger.warning(
        "Both NET_ENABLED_MODULES and NET_DISABLED_MODULES are set. "
        "NET_ENABLED_MODULES takes precedence; NET_DISABLED_MODULES will be ignored."
    )

# Progressive discovery: when enabled, only load meta-tools + workflow tools at startup
_progressive = settings.net_progressive_discovery
if _progressive:
    logger.info("Progressive discovery enabled — loading only meta-tools + workflow tools at startup")

# --- EOS-specific tool modules (require pyeapi + eos vendor allowed) ---
# These tools send Arista EOS commands via pyeapi through the connection manager.
# Skip them when pyeapi is not installed or the operator has excluded eos via NET_VENDORS.
_eos_tools_allowed = _PYEAPI_AVAILABLE and _vendor_enabled("eos")

if not _PYEAPI_AVAILABLE:
    logger.info("EOS tools not loaded (pyeapi not installed; install with: pip install latticio[eos])")
elif not _vendor_enabled("eos"):
    logger.info("EOS tools not loaded (eos not in NET_VENDORS)")

if _eos_tools_allowed:
    if _progressive:
        _modules_to_load = ["workflows"]
    elif _enabled is not None:
        # Allowlist mode: only load explicitly enabled modules
        _modules_to_load = [m for m in _ALL_MODULES if m in _enabled]
        _skipped = [m for m in _ALL_MODULES if m not in _enabled]
        if _skipped:
            logger.info(f"NET_ENABLED_MODULES active — loading only: {', '.join(_modules_to_load)}")
    else:
        # Default mode: load all core, apply disabled filter to optional
        _modules_to_load = list(_CORE_MODULES)
        for _mod in _OPTIONAL_MODULES:
            if _mod not in _disabled:
                _modules_to_load.append(_mod)
            else:
                logger.info(f"Module disabled by configuration: {_mod}")

    for _module_name in _modules_to_load:
        _load_module(
            _module_name,
            f".tools.{_module_name}",
            required=(_module_name in _CORE_MODULES),
        )

# gNMI tools — loaded only when pygnmi is installed
# Also respects NET_ENABLED_MODULES and NET_VENDORS (eos): gNMI targets Arista EOS devices.
# Not loaded in progressive discovery mode (use eos_load_tool_category to load)
from .gnmi_connection import GNMI_AVAILABLE  # noqa: E402

_gnmi_allowed = (_enabled is None or "gnmi" in _enabled) and not _progressive and _vendor_enabled("eos")
if GNMI_AVAILABLE and _gnmi_allowed:
    _load_module("gnmi", ".tools.gnmi")
elif not GNMI_AVAILABLE:
    logger.info("gNMI tools not available (install with: pip install latticio[gnmi])")
elif not _vendor_enabled("eos"):
    logger.info("gNMI tools not loaded (eos not in NET_VENDORS)")
elif _progressive:
    logger.info("gNMI tools deferred (progressive discovery enabled — use eos_load_tool_category('gnmi'))")
elif not _gnmi_allowed:
    logger.info("gNMI tools not loaded (not in NET_ENABLED_MODULES)")

# CloudVision tools — loaded only when CVP URL is configured and eos vendor is enabled
_cvp_allowed = (_enabled is None or "cloudvision" in _enabled) and not _progressive and _vendor_enabled("eos")
if settings.eos_cvp_url and _cvp_allowed:
    _load_module("cloudvision", ".tools.cloudvision")
elif not settings.eos_cvp_url:
    logger.info("CloudVision tools not available (set EOS_CVP_URL to enable)")
elif not _vendor_enabled("eos"):
    logger.info("CloudVision tools not loaded (eos not in NET_VENDORS)")
elif _progressive:
    logger.info("CloudVision tools deferred (progressive discovery enabled)")
elif not _cvp_allowed:
    logger.info("CloudVision tools not loaded (not in NET_ENABLED_MODULES)")

# NetBox integration tools — loaded only when NETBOX_URL is configured
_netbox_allowed = (_enabled is None or "netbox" in _enabled) and not _progressive
if settings.netbox_url and _netbox_allowed:
    _load_module("netbox", ".integrations.netbox")
elif not settings.netbox_url:
    logger.info("NetBox tools not available (set NETBOX_URL to enable)")
elif _progressive:
    logger.info("NetBox tools deferred (progressive discovery enabled)")
elif not _netbox_allowed:
    logger.info("NetBox tools not loaded (not in NET_ENABLED_MODULES)")

# Prometheus metrics export — loaded when PROMETHEUS_ENABLED=true or prometheus-client is importable
_prometheus_allowed = (_enabled is None or "prometheus" in _enabled) and not _progressive
if settings.prometheus_enabled and _prometheus_allowed:
    _load_module("prometheus", ".integrations.prometheus")
elif not settings.prometheus_enabled:
    logger.info("Prometheus tools not available (set PROMETHEUS_ENABLED=true to enable)")
elif _progressive:
    logger.info("Prometheus tools deferred (progressive discovery enabled)")
elif not _prometheus_allowed:
    logger.info("Prometheus tools not loaded (not in NET_ENABLED_MODULES)")

# ServiceNow integration tools — loaded only when SNOW_INSTANCE is configured
_snow_allowed = (_enabled is None or "servicenow" in _enabled) and not _progressive
if settings.snow_instance and _snow_allowed:
    _load_module("servicenow", ".integrations.servicenow")
elif not settings.snow_instance:
    logger.info("ServiceNow tools not available (set SNOW_INSTANCE to enable)")
elif _progressive:
    logger.info("ServiceNow tools deferred (progressive discovery enabled)")
elif not _snow_allowed:
    logger.info("ServiceNow tools not loaded (not in NET_ENABLED_MODULES)")

# Ansible integration tools — no external deps required (stdlib yaml only)
_load_module("ansible", ".integrations.ansible")

# Containerlab integration tools — loaded only when NET_CONTAINERLAB_ENABLED=true
if settings.net_containerlab_enabled:
    _load_module("containerlab", ".integrations.containerlab")
else:
    logger.info("Containerlab tools not loaded (set NET_CONTAINERLAB_ENABLED=true to enable)")

# API key admin tools — loaded only when NET_API_KEY_ENABLED=true
if settings.api_key_enabled:
    _load_module("admin", ".tools.admin")

# HTTP session management tools — loaded when session resumption is enabled
if http_session_store is not None:
    _load_module("http_sessions", ".tools.http_sessions")

# Cisco-specific tools — loaded only when httpx is installed (cisco extra) and
# at least one Cisco platform (iosxe or nxos) is included in NET_VENDORS.
# Not affected by progressive discovery or module filtering since they are
# vendor-specific tools, not EOS core/optional modules.
_cisco_vendor_enabled = _vendor_enabled("iosxe") or _vendor_enabled("nxos")
if _HTTPX_AVAILABLE and _cisco_vendor_enabled:
    if _vendor_enabled("iosxe"):
        _load_module("cisco.iosxe", "network_mcp.tools.cisco.iosxe")
    if _vendor_enabled("nxos"):
        _load_module("cisco.vpc", "network_mcp.tools.cisco.vpc")
        _load_module("cisco.fex", "network_mcp.tools.cisco.fex")
        _load_module("cisco.nxos", "network_mcp.tools.cisco.nxos")
elif not _HTTPX_AVAILABLE:
    logger.info("Cisco tools not loaded (install with: pip install latticio[cisco])")
elif not _cisco_vendor_enabled:
    logger.info("Cisco tools not loaded (iosxe and nxos not in NET_VENDORS)")

# Juniper-specific tools — loaded only when scrapli-netconf is installed (juniper extra)
# and junos is included in NET_VENDORS.
# Not affected by progressive discovery or module filtering since they are
# vendor-specific tools, not EOS core/optional modules.
if _SCRAPLI_NETCONF_AVAILABLE and _vendor_enabled("junos"):
    _load_module("juniper.junos", "network_mcp.tools.juniper.junos")
elif not _SCRAPLI_NETCONF_AVAILABLE:
    logger.info("Juniper tools not loaded (install with: pip install latticio[juniper])")
elif not _vendor_enabled("junos"):
    logger.info("Juniper tools not loaded (junos not in NET_VENDORS)")

# --- Plugin discovery (third-party drivers, compliance packs, tool modules) ---

from .plugins import discover_plugins, load_driver_plugins, load_tool_plugins  # noqa: E402

_plugins = discover_plugins()
if any(_plugins.values()):
    from .drivers import DRIVER_REGISTRY  # noqa: E402

    load_driver_plugins(DRIVER_REGISTRY)
    load_tool_plugins(mcp)
    logger.info("Loaded plugins: %s", {k: len(v) for k, v in _plugins.items() if v})
else:
    logger.debug("No third-party plugins discovered")

# Mark all modules as loaded for readiness probe
from .health import mark_modules_loaded  # noqa: E402

mark_modules_loaded()
logger.debug("All tool modules loaded — readiness probe will report ready")

# Annotate all registered tools with x-tool-group metadata
from .tools.meta import annotate_tools_with_groups  # noqa: E402

annotate_tools_with_groups(mcp)

# --- HTTP routes (available when using streamable-http transport) ---

# Transport mode tracking — updated by main() before mcp.run()
_transport_mode: str = "stdio"

try:
    from starlette.requests import Request  # noqa: E402
    from starlette.responses import JSONResponse, Response  # noqa: E402

    @mcp.custom_route("/health", methods=["GET"])
    async def health_endpoint(request: Request) -> JSONResponse:  # noqa: ARG001
        """HTTP health check endpoint — returns server health as JSON."""
        from network_mcp.config import get_safe_config
        from network_mcp.helpers import circuit_breaker_registry, command_cache
        from network_mcp.observability import get_server_health, metrics_collector

        health = get_server_health(
            conn_mgr, command_cache, circuit_breaker_registry, metrics_collector, get_module_status()
        )
        health["config"] = get_safe_config(settings)
        health["connected_device_count"] = len(conn_mgr.list_devices())
        health["transport_mode"] = _transport_mode
        status_code = 200 if health["status"] == "healthy" else 503
        return JSONResponse(health, status_code=status_code)

    @mcp.custom_route("/health/live", methods=["GET"])
    async def liveness_endpoint(request: Request) -> JSONResponse:  # noqa: ARG001
        """Lightweight liveness probe — never blocks, no external calls."""
        from network_mcp.health import get_liveness

        return JSONResponse(get_liveness(), status_code=200)

    @mcp.custom_route("/health/ready", methods=["GET"])
    async def readiness_endpoint(request: Request) -> JSONResponse:  # noqa: ARG001
        """Readiness probe — checks modules loaded and pool initialized."""
        from network_mcp.health import get_readiness

        response, status_code = get_readiness(conn_mgr, settings)
        return JSONResponse(response, status_code=status_code)

    @mcp.custom_route("/health/deep", methods=["GET"])
    async def deep_health_endpoint(request: Request) -> JSONResponse:  # noqa: ARG001
        """Deep health check — validates device reachability, Redis, auth."""
        from network_mcp.health import get_deep_health

        response, status_code = get_deep_health(conn_mgr, settings)
        return JSONResponse(response, status_code=status_code)

    @mcp.custom_route("/metrics", methods=["GET"])
    async def metrics_endpoint(request: Request) -> Response:  # noqa: ARG001
        """Prometheus metrics endpoint — returns metrics in exposition format."""
        from network_mcp.circuit_breaker import DeviceHealth
        from network_mcp.helpers import circuit_breaker_registry
        from network_mcp.observability import metrics_collector

        # Snapshot circuit breaker states for per-device gauge
        _state_map = {
            DeviceHealth.HEALTHY: 0,
            DeviceHealth.CIRCUIT_OPEN: 1,
            DeviceHealth.HALF_OPEN: 2,
        }
        cb_stats = circuit_breaker_registry.stats()
        cb_device_states = {}
        for device, breaker_info in cb_stats.get("breakers", {}).items():
            state_str = breaker_info.get("state", "healthy")
            if state_str == "open":
                cb_device_states[device] = 1
            elif state_str == "half_open":
                cb_device_states[device] = 2
            else:
                cb_device_states[device] = 0
        metrics_collector.set_circuit_breaker_states(cb_device_states)

        # Snapshot connection pool stats
        pool = conn_mgr.pool_stats()
        metrics_collector.set_connection_pool_stats(
            pool_size=pool.get("max", 0),
            active=pool.get("active", 0),
        )

        metrics_text = metrics_collector.get_metrics_prometheus()
        return Response(content=metrics_text, media_type="text/plain; version=0.0.4")

    @mcp.custom_route("/.well-known/mcp.json", methods=["GET"])
    async def mcp_server_identity(request: Request) -> JSONResponse:  # noqa: ARG001
        """MCP server identity — no auth required (custom_route bypasses auth)."""
        return JSONResponse(
            {
                "name": "network-mcp",
                "version": __version__,
                "description": "Multi-vendor Network MCP Platform — AI-powered network automation",
                "capabilities": {
                    "tools": True,
                    "resources": True,
                    "prompts": True,
                },
            }
        )

    @mcp.custom_route("/openapi.json", methods=["GET"])
    async def openapi_spec_endpoint(request: Request) -> JSONResponse:  # noqa: ARG001
        """Serve OpenAPI 3.1 specification for all registered MCP tools."""
        from network_mcp.openapi import generate_openapi_spec

        spec = generate_openapi_spec(mcp)
        return JSONResponse(spec)

    # --- OAuth 2.1 well-known metadata endpoints ---
    # Registered when OAuth resource URL or auth server URL is configured.
    _well_known_routes: list[str] = []

    if settings.oauth_resource_url:
        from network_mcp.oauth import OAuthMetadata

        _oauth_resource_meta = OAuthMetadata(
            resource_url=settings.oauth_resource_url,
            authorization_servers=[settings.oauth_auth_server_url] if settings.oauth_auth_server_url else [],
        )

        @mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
        async def oauth_protected_resource(request: Request) -> JSONResponse:  # noqa: ARG001
            """OAuth 2.1 protected resource metadata — no auth required."""
            return JSONResponse(_oauth_resource_meta.to_dict())

        _well_known_routes.append("/.well-known/oauth-protected-resource")

    if settings.oauth_auth_server_url:
        from network_mcp.oauth import OAuthServerMetadata

        _oauth_server_meta = OAuthServerMetadata(issuer=settings.oauth_auth_server_url)

        @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
        async def oauth_authorization_server(request: Request) -> JSONResponse:  # noqa: ARG001
            """OAuth 2.1 authorization server metadata — no auth required."""
            return JSONResponse(_oauth_server_meta.to_dict())

        _well_known_routes.append("/.well-known/oauth-authorization-server")

    if _well_known_routes:
        logger.info("OAuth 2.1 well-known endpoints registered: %s", ", ".join(_well_known_routes))

    @mcp.custom_route("/config/reload", methods=["POST"])
    async def config_reload_endpoint(request: Request) -> JSONResponse:
        """Reload safe-to-change settings from environment variables.

        Requires network:admin scope when RBAC is enabled. Updates rate limits,
        timeouts, circuit breaker params, log level, and cache TTL without
        requiring a server restart.
        """
        from network_mcp.audit import audit_logger as _reload_audit
        from network_mcp.config import reload_settings

        # RBAC check: require network:admin scope if RBAC is enabled
        if settings.rbac_enabled and settings.auth_enabled:
            try:
                from mcp.server.auth.middleware.auth_context import get_access_token

                token = get_access_token()
                if token is not None:
                    from network_mcp.rbac import check_authorization

                    # Use a synthetic tool name for RBAC check
                    authorized, message = check_authorization(
                        "config_reload",
                        {
                            "scope": " ".join(token.scopes) if token.scopes else "",
                            "client_id": token.client_id,
                        },
                    )
                    if not authorized:
                        logger.warning("Config reload denied: %s (client=%s)", message, token.client_id)
                        return JSONResponse(
                            {"status": "error", "error": message},
                            status_code=403,
                        )
                # If token is None, it could be stdio transport — allow through
            except ImportError:
                pass  # Auth middleware not available — skip RBAC check

        try:
            changes = reload_settings(settings)
        except Exception as exc:
            logger.error("Config reload failed: %s", exc, exc_info=True)
            return JSONResponse(
                {"status": "error", "error": f"Config reload failed: {exc}"},
                status_code=500,
            )

        # Audit log the reload event
        if changes:
            _reload_audit.log_config_reload(changes)

        return JSONResponse(
            {
                "status": "success",
                "changed": len(changes),
                "settings": {k: {"old": str(v["old"]), "new": str(v["new"])} for k, v in changes.items()},
            }
        )

    logger.info("HTTP /health, /metrics, /openapi.json, /config/reload, and /.well-known/mcp.json endpoints registered")
except ImportError:
    logger.debug("Starlette not available — HTTP endpoints not registered")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the MCP server.

    Args:
        argv: Argument list to parse. Defaults to sys.argv[1:] when None.
    """
    parser = argparse.ArgumentParser(description="network-mcp server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host (default: 0.0.0.0)")  # noqa: S104
    parser.add_argument("--port", type=int, default=8000, help="HTTP bind port (default: 8000)")
    return parser.parse_args(argv)


def main():
    """Entry point for the MCP server."""
    global _transport_mode
    args = _parse_args()
    _transport_mode = args.transport
    logger.info(f"Starting MCP server with transport: {args.transport}")
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
