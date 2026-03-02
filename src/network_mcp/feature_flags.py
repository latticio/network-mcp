"""Tenant-based feature targeting for the Network MCP server.

Feature flags are configured via the ``NET_FEATURE_FLAGS`` environment variable,
which accepts a JSON string mapping feature names to ``FeatureFlag`` objects.

Example::

    NET_FEATURE_FLAGS='{"beta_ui": {"enabled": true, "tenants": ["tenant-a"]}, "new_api": {"enabled": true}}'
"""

from __future__ import annotations

from network_mcp.config import FeatureFlag, NetworkSettings

# Lazy-loaded settings instance.  Imported at call time so that tests
# can override the settings without import-order issues.
_settings: NetworkSettings | None = None


def _get_settings() -> NetworkSettings:
    """Return the cached ``NetworkSettings`` singleton."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        from network_mcp.server import settings as _server_settings

        _settings = _server_settings
    return _settings


def _reset_settings() -> None:
    """Reset the cached settings (for testing only)."""
    global _settings  # noqa: PLW0603
    _settings = None


def is_feature_enabled(feature_name: str, tenant_id: str | None = None) -> bool:
    """Check if a feature is enabled, optionally for a specific tenant.

    Rules:
    - If the feature is not defined in flags, return False.
    - If ``feature.enabled`` is False, return False.
    - If ``feature.enabled`` is True and the tenants list is empty,
      return True for all callers (the feature is globally enabled).
    - If ``feature.enabled`` is True and the tenants list is non-empty,
      return True only if *tenant_id* is in the list.
    - If *tenant_id* is None and the tenants list is non-empty, return False.
    """
    flags = _get_settings().feature_flags
    flag = flags.get(feature_name)
    if flag is None:
        return False
    if not flag.enabled:
        return False
    # Globally enabled (no tenant restriction)
    if not flag.tenants:
        return True
    # Tenant-restricted
    if tenant_id is None:
        return False
    return tenant_id in flag.tenants


def get_all_feature_flags() -> dict[str, FeatureFlag]:
    """Return all configured feature flags."""
    return _get_settings().feature_flags


def get_feature_flag(name: str) -> FeatureFlag | None:
    """Return a single feature flag by name, or None if not defined."""
    return _get_settings().feature_flags.get(name)
