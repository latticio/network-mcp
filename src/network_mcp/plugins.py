"""Plugin discovery for network-mcp.

Discovers and loads plugins via Python entry points. Third-party packages
can register drivers, compliance checks, and tool modules by declaring
entry points in their pyproject.toml.

Entry point groups:
- network_mcp.drivers — NetworkDriver implementations
- network_mcp.compliance — Compliance check sets
- network_mcp.tools — Custom tool modules

Example third-party package (pyproject.toml):
    [project.entry-points."network_mcp.drivers"]
    nokia_sros = "network_mcp_driver_nokia:SROSDriver"

Security controls:
- EOS_PLUGIN_ALLOWLIST: Only load plugins whose package name is in this list.
- EOS_PLUGIN_BLOCKLIST: Never load plugins whose package name is in this list.
  Blocklist takes precedence over allowlist. When both lists are empty, all
  discovered plugins are loaded (backward-compatible default).

OWASP MCP compliance:
- MCP03 (Tool Poisoning): Optional hash verification for plugin integrity.
- MCP06 (Cross-Plugin State Leakage): PluginSandbox provides state isolation.
"""

import hashlib
import importlib.metadata
import logging
from pathlib import Path
from typing import Any

from network_mcp.audit import audit_logger

logger = logging.getLogger("network-mcp")

PLUGIN_GROUPS = {
    "network_mcp.drivers": "Driver plugins",
    "network_mcp.compliance": "Compliance plugins",
    "network_mcp.tools": "Tool plugins",
}

# Cache of discovered plugins (populated by discover_plugins)
_discovered_plugins: dict[str, list[dict]] | None = None

# Plugin hash allowlist — maps "package_name:module_path" to expected SHA-256 hash.
# When non-empty, plugins not in this dict (or with mismatched hashes) are rejected.
# Populated via NET_PLUGIN_HASH_ALLOWLIST env var or set_plugin_hash_allowlist().
_plugin_hash_allowlist: dict[str, str] = {}


def set_plugin_hash_allowlist(allowlist: dict[str, str]) -> None:
    """Set the plugin hash allowlist for integrity verification.

    Args:
        allowlist: Dict mapping "package_name" to expected SHA-256 hex digest
                   of the plugin module file.
    """
    global _plugin_hash_allowlist
    _plugin_hash_allowlist = dict(allowlist)


def verify_plugin_integrity(entry_point) -> tuple[bool, str]:
    """Verify plugin module file hasn't been tampered with.

    OWASP MCP03 — Tool Poisoning: checks module file hash against allowlist.

    Args:
        entry_point: The importlib.metadata EntryPoint to verify.

    Returns:
        (is_valid, message) — is_valid is True if integrity check passes.
        When no allowlist is configured, all plugins pass (backward-compatible).
    """
    if not _plugin_hash_allowlist:
        return True, "No hash allowlist configured (integrity check skipped)"

    dist = entry_point.dist
    package_name = dist.name if dist else "unknown"

    expected_hash = _plugin_hash_allowlist.get(package_name)
    if expected_hash is None:
        logger.warning("Plugin '%s' not in hash allowlist — rejecting", package_name)
        return False, f"Plugin '{package_name}' not in hash allowlist"

    # Resolve the module file path
    try:
        module_name = entry_point.value.split(":")[0]
        # Try to find the actual module file
        module = __import__(module_name, fromlist=["__file__"])
        module_file = getattr(module, "__file__", None)
        if not module_file:
            return False, f"Cannot locate module file for '{module_name}'"

        file_path = Path(module_file)
        if not file_path.exists():
            return False, f"Module file not found: {file_path}"

        actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            logger.warning(
                "Plugin '%s' integrity check FAILED: expected=%s actual=%s",
                package_name,
                expected_hash[:16] + "...",
                actual_hash[:16] + "...",
            )
            return False, f"Hash mismatch for plugin '{package_name}'"

        return True, f"Plugin '{package_name}' integrity verified"
    except Exception as exc:
        logger.warning("Plugin integrity check failed for '%s': %s", package_name, exc)
        return False, f"Integrity check error for '{package_name}': {exc}"


class PluginSandbox:
    """Isolate plugin state from core server and other plugins.

    OWASP MCP06 — Cross-Plugin State Leakage: each plugin gets its own
    namespace for state storage, preventing one plugin from reading or
    modifying another plugin's data.
    """

    def __init__(self, plugin_name: str):
        self._namespace = plugin_name
        self._state: dict[str, Any] = {}

    @property
    def namespace(self) -> str:
        """The plugin's unique namespace identifier."""
        return self._namespace

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the plugin's isolated state."""
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value in the plugin's isolated state."""
        self._state[key] = value

    def delete(self, key: str) -> None:
        """Delete a value from the plugin's isolated state."""
        self._state.pop(key, None)

    def clear(self) -> None:
        """Clear all state for this plugin."""
        self._state.clear()

    def keys(self) -> list[str]:
        """Return all keys in this plugin's state."""
        return list(self._state.keys())


# Registry of plugin sandboxes — one per loaded plugin
_plugin_sandboxes: dict[str, PluginSandbox] = {}


def get_plugin_sandbox(plugin_name: str) -> PluginSandbox:
    """Get or create a sandbox for a plugin.

    Returns an isolated state container for the plugin. Each plugin
    gets its own sandbox that cannot be accessed by other plugins.
    """
    if plugin_name not in _plugin_sandboxes:
        _plugin_sandboxes[plugin_name] = PluginSandbox(plugin_name)
    return _plugin_sandboxes[plugin_name]


def _reset_plugin_sandboxes() -> None:
    """Reset all plugin sandboxes. Used in tests."""
    _plugin_sandboxes.clear()


def _get_plugin_metadata(entry_point) -> dict:
    """Extract metadata from an entry point for logging."""
    dist = entry_point.dist
    return {
        "name": dist.name if dist else "unknown",
        "version": dist.version if dist else "unknown",
        "entry_point": f"{entry_point.group}:{entry_point.name}",
    }


def _is_plugin_allowed(package_name: str) -> bool:
    """Check if a plugin is allowed to load based on allowlist/blocklist.

    Blocklist takes precedence over allowlist. When both lists are empty,
    all plugins are allowed (backward-compatible).
    """
    from network_mcp.config import NetworkSettings

    settings = NetworkSettings()

    if settings.plugin_blocklist and package_name in settings.plugin_blocklist:
        logger.warning("Plugin '%s' is blocklisted — skipping", package_name)
        return False
    if settings.plugin_allowlist and package_name not in settings.plugin_allowlist:
        logger.warning("Plugin '%s' not in allowlist — skipping", package_name)
        return False
    return True


def _log_plugin_event(name: str, group: str, action: str, version: str = "", detail: str = "") -> None:
    """Log a plugin lifecycle event to both the application log and the audit trail."""
    msg = f"Plugin {name}: {action}"
    if version:
        msg += f" v{version}"
    if detail:
        msg += f" — {detail}"
    logger.info(msg)

    audit_data = {
        "event": "plugin_lifecycle",
        "plugin_name": name,
        "plugin_group": group,
        "action": action,
    }
    if version:
        audit_data["plugin_version"] = version
    if detail:
        audit_data["detail"] = detail

    record = audit_logger._logger.makeRecord(
        name=audit_logger._logger.name,
        level=logging.INFO,
        fn="",
        lno=0,
        msg=f"Plugin {action}: {name}",
        args=(),
        exc_info=None,
    )
    record.audit_data = audit_data  # type: ignore[attr-defined]
    audit_logger._logger.handle(record)


def discover_plugins() -> dict[str, list[dict]]:
    """Discover all installed plugins across all entry point groups.

    Returns a dict of ``{group: [{name, module, loaded, error}]}``.
    Results are cached after first call; use ``_reset_plugins()`` to clear.
    """
    global _discovered_plugins
    if _discovered_plugins is not None:
        return _discovered_plugins

    result: dict[str, list[dict]] = {group: [] for group in PLUGIN_GROUPS}

    for group in PLUGIN_GROUPS:
        try:
            eps = importlib.metadata.entry_points(group=group)
        except TypeError:
            # Python 3.9/3.10 compat: entry_points() returns a dict
            all_eps = importlib.metadata.entry_points()
            eps = all_eps.get(group, [])  # type: ignore[arg-type,assignment]

        for ep in eps:
            plugin_info: dict = {
                "name": ep.name,
                "module": ep.value,
                "loaded": False,
                "error": None,
            }
            result[group].append(plugin_info)
            logger.debug("Discovered plugin: %s=%s (group=%s)", ep.name, ep.value, group)

    _discovered_plugins = result
    return result


def load_driver_plugins(registry: dict) -> dict:
    """Load driver plugins and merge into DRIVER_REGISTRY.

    Plugin drivers are only added if they don't conflict with built-in drivers.
    Plugins are checked against the allowlist/blocklist before loading.
    Returns updated registry with plugin drivers added.
    """
    plugins = discover_plugins()
    for plugin_info in plugins.get("network_mcp.drivers", []):
        name = plugin_info["name"]
        if name in registry:
            logger.warning(
                "Driver plugin '%s' skipped — conflicts with built-in driver '%s'",
                name,
                name,
            )
            plugin_info["error"] = f"conflicts with built-in driver '{name}'"
            continue
        try:
            eps = importlib.metadata.entry_points(group="network_mcp.drivers")
            ep = next((e for e in eps if e.name == name), None)
            if ep is None:
                continue
            metadata = _get_plugin_metadata(ep)
            if not _is_plugin_allowed(metadata["name"]):
                plugin_info["error"] = "blocked by allowlist/blocklist"
                _log_plugin_event(metadata["name"], "network_mcp.drivers", "blocked", metadata["version"])
                continue
            # MCP03: Verify plugin integrity before loading
            integrity_ok, integrity_msg = verify_plugin_integrity(ep)
            if not integrity_ok:
                plugin_info["error"] = integrity_msg
                _log_plugin_event(
                    metadata["name"], "network_mcp.drivers", "integrity_failed", metadata["version"], integrity_msg
                )
                continue
            driver_cls = ep.load()
            registry[name] = driver_cls
            plugin_info["loaded"] = True
            _log_plugin_event(metadata["name"], "network_mcp.drivers", "loaded", metadata["version"])
        except Exception as exc:
            plugin_info["error"] = str(exc)
            logger.error("Plugin %s failed to load: %s", name, exc)
            metadata = {"name": name, "version": "unknown"}
            _log_plugin_event(name, "network_mcp.drivers", "failed", detail=str(exc))

    return registry


def load_compliance_plugins(checks: list) -> list:
    """Load compliance plugins and merge into compliance check list.

    Each plugin entry point should resolve to a list of ComplianceCheck objects.
    Plugins are checked against the allowlist/blocklist before loading.
    Returns updated check list with plugin checks appended.
    """
    plugins = discover_plugins()
    for plugin_info in plugins.get("network_mcp.compliance", []):
        name = plugin_info["name"]
        try:
            eps = importlib.metadata.entry_points(group="network_mcp.compliance")
            ep = next((e for e in eps if e.name == name), None)
            if ep is None:
                continue
            metadata = _get_plugin_metadata(ep)
            if not _is_plugin_allowed(metadata["name"]):
                plugin_info["error"] = "blocked by allowlist/blocklist"
                _log_plugin_event(metadata["name"], "network_mcp.compliance", "blocked", metadata["version"])
                continue
            # MCP03: Verify plugin integrity before loading
            integrity_ok, integrity_msg = verify_plugin_integrity(ep)
            if not integrity_ok:
                plugin_info["error"] = integrity_msg
                _log_plugin_event(
                    metadata["name"],
                    "network_mcp.compliance",
                    "integrity_failed",
                    metadata["version"],
                    integrity_msg,
                )
                continue
            plugin_checks = ep.load()
            if isinstance(plugin_checks, list):
                checks.extend(plugin_checks)
                plugin_info["loaded"] = True
                _log_plugin_event(
                    metadata["name"],
                    "network_mcp.compliance",
                    "loaded",
                    metadata["version"],
                    f"{len(plugin_checks)} checks",
                )
            else:
                plugin_info["error"] = "plugin did not return a list of checks"
                logger.warning("Compliance plugin '%s' did not return a list", name)
        except Exception as exc:
            plugin_info["error"] = str(exc)
            logger.error("Plugin %s failed to load: %s", name, exc)
            _log_plugin_event(name, "network_mcp.compliance", "failed", detail=str(exc))

    return checks


def load_tool_plugins(mcp_instance) -> list[str]:
    """Load tool module plugins.

    Each plugin module is expected to register its tools via ``@mcp.tool()``
    decorators when imported. The plugin entry point should resolve to a module
    that accepts the mcp instance, or a module with tool decorators already
    referencing the server.

    Plugins are checked against the allowlist/blocklist before loading.
    Returns list of loaded plugin module names.
    """
    loaded: list[str] = []
    plugins = discover_plugins()
    for plugin_info in plugins.get("network_mcp.tools", []):
        name = plugin_info["name"]
        try:
            eps = importlib.metadata.entry_points(group="network_mcp.tools")
            ep = next((e for e in eps if e.name == name), None)
            if ep is None:
                continue
            metadata = _get_plugin_metadata(ep)
            if not _is_plugin_allowed(metadata["name"]):
                plugin_info["error"] = "blocked by allowlist/blocklist"
                _log_plugin_event(metadata["name"], "network_mcp.tools", "blocked", metadata["version"])
                continue
            # MCP03: Verify plugin integrity before loading
            integrity_ok, integrity_msg = verify_plugin_integrity(ep)
            if not integrity_ok:
                plugin_info["error"] = integrity_msg
                _log_plugin_event(
                    metadata["name"],
                    "network_mcp.tools",
                    "integrity_failed",
                    metadata["version"],
                    integrity_msg,
                )
                continue
            module = ep.load()
            # If the loaded object is a callable (setup function), call it with mcp
            if callable(module) and not isinstance(module, type):
                module(mcp_instance)
            plugin_info["loaded"] = True
            loaded.append(name)
            _log_plugin_event(metadata["name"], "network_mcp.tools", "loaded", metadata["version"])
        except Exception as exc:
            plugin_info["error"] = str(exc)
            logger.error("Plugin %s failed to load: %s", name, exc)
            _log_plugin_event(name, "network_mcp.tools", "failed", detail=str(exc))

    return loaded


def get_plugin_summary() -> dict:
    """Get a summary of all discovered plugins for the net_list_plugins tool."""
    plugins = discover_plugins()
    total = sum(len(v) for v in plugins.values())
    return {
        "status": "success",
        "data": {
            **{
                group: [{k: v for k, v in p.items() if not (k == "error" and v is None)} for p in group_plugins]
                for group, group_plugins in plugins.items()
            },
            "total_plugins": total,
        },
    }


def _reset_plugins() -> None:
    """Reset the plugin cache. Used in tests."""
    global _discovered_plugins
    _discovered_plugins = None
