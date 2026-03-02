"""Tests for hot reload of safe settings, config audit trail, and env var standardization."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.config import (
    _RELOADABLE_SETTINGS,
    _SECRET_FIELDS,
    NetworkSettings,
    get_safe_config,
    reload_settings,
)

# --- Fixtures ---


@pytest.fixture
def live_settings():
    """Create a fresh NetworkSettings instance for testing reload."""
    return NetworkSettings(
        rate_limit_show=5.0,
        rate_limit_config=1.0,
        log_level="INFO",
        timeout_connect=10.0,
        timeout_show=30.0,
        timeout_config=60.0,
        net_cb_threshold=3,
        net_cb_cooldown=60,
        net_cache_default_ttl=30,
    )


# --- reload_settings() tests ---


class TestReloadSettings:
    """Tests for the reload_settings() function."""

    def test_detects_changed_rate_limit(self, live_settings):
        """reload_settings() detects changed rate limit values."""
        with patch("network_mcp.config.NetworkSettings") as mock_cls:
            fresh = NetworkSettings(rate_limit_show=10.0, rate_limit_config=2.0)
            mock_cls.return_value = fresh

            # Pre-patch helpers to avoid import side effects
            with (
                patch("network_mcp.config._propagate_rate_limiter"),
                patch("network_mcp.config._propagate_circuit_breaker"),
                patch("network_mcp.config._propagate_cache_ttl"),
            ):
                changes = reload_settings(live_settings)

        assert "rate_limit_show" in changes
        assert changes["rate_limit_show"]["old"] == 5.0
        assert changes["rate_limit_show"]["new"] == 10.0
        assert "rate_limit_config" in changes
        assert changes["rate_limit_config"]["old"] == 1.0
        assert changes["rate_limit_config"]["new"] == 2.0

    def test_detects_changed_log_level(self, live_settings):
        """reload_settings() detects and applies changed log level."""
        with patch("network_mcp.config.NetworkSettings") as mock_cls:
            fresh = NetworkSettings(log_level="DEBUG")
            mock_cls.return_value = fresh

            with (
                patch("network_mcp.config._propagate_rate_limiter"),
                patch("network_mcp.config._propagate_circuit_breaker"),
                patch("network_mcp.config._propagate_cache_ttl"),
            ):
                changes = reload_settings(live_settings)

        assert "log_level" in changes
        assert changes["log_level"]["old"] == "INFO"
        assert changes["log_level"]["new"] == "DEBUG"
        # Verify the setting was actually updated on the live instance
        assert live_settings.log_level == "DEBUG"

    def test_detects_changed_timeouts(self, live_settings):
        """reload_settings() detects changed timeout values."""
        with patch("network_mcp.config.NetworkSettings") as mock_cls:
            fresh = NetworkSettings(timeout_connect=20.0, timeout_show=60.0, timeout_config=120.0)
            mock_cls.return_value = fresh

            with (
                patch("network_mcp.config._propagate_rate_limiter"),
                patch("network_mcp.config._propagate_circuit_breaker"),
                patch("network_mcp.config._propagate_cache_ttl"),
            ):
                changes = reload_settings(live_settings)

        assert "timeout_connect" in changes
        assert changes["timeout_connect"]["old"] == 10.0
        assert changes["timeout_connect"]["new"] == 20.0
        assert "timeout_show" in changes
        assert "timeout_config" in changes
        # Verify live instance was updated
        assert live_settings.timeout_connect == 20.0
        assert live_settings.timeout_show == 60.0
        assert live_settings.timeout_config == 120.0

    def test_detects_changed_circuit_breaker(self, live_settings):
        """reload_settings() detects changed circuit breaker settings."""
        with patch("network_mcp.config.NetworkSettings") as mock_cls:
            fresh = NetworkSettings(net_cb_threshold=5, net_cb_cooldown=120)
            mock_cls.return_value = fresh

            with (
                patch("network_mcp.config._propagate_rate_limiter"),
                patch("network_mcp.config._propagate_circuit_breaker") as mock_cb,
                patch("network_mcp.config._propagate_cache_ttl"),
            ):
                changes = reload_settings(live_settings)

        assert "net_cb_threshold" in changes
        assert changes["net_cb_threshold"]["old"] == 3
        assert changes["net_cb_threshold"]["new"] == 5
        assert "net_cb_cooldown" in changes
        mock_cb.assert_called_once()

    def test_detects_changed_cache_ttl(self, live_settings):
        """reload_settings() detects changed cache default TTL."""
        with patch("network_mcp.config.NetworkSettings") as mock_cls:
            fresh = NetworkSettings(net_cache_default_ttl=60)
            mock_cls.return_value = fresh

            with (
                patch("network_mcp.config._propagate_rate_limiter"),
                patch("network_mcp.config._propagate_circuit_breaker"),
                patch("network_mcp.config._propagate_cache_ttl") as mock_cache,
            ):
                changes = reload_settings(live_settings)

        assert "net_cache_default_ttl" in changes
        assert changes["net_cache_default_ttl"]["old"] == 30
        assert changes["net_cache_default_ttl"]["new"] == 60
        mock_cache.assert_called_once()

    def test_no_changes_returns_empty(self, live_settings):
        """reload_settings() returns empty dict when nothing changed."""
        with patch("network_mcp.config.NetworkSettings") as mock_cls:
            # Create fresh with same defaults
            fresh = NetworkSettings(
                rate_limit_show=5.0,
                rate_limit_config=1.0,
                log_level="INFO",
                timeout_connect=10.0,
                timeout_show=30.0,
                timeout_config=60.0,
                net_cb_threshold=3,
                net_cb_cooldown=60,
                net_cache_default_ttl=30,
            )
            mock_cls.return_value = fresh

            with (
                patch("network_mcp.config._propagate_rate_limiter"),
                patch("network_mcp.config._propagate_circuit_breaker"),
                patch("network_mcp.config._propagate_cache_ttl"),
            ):
                changes = reload_settings(live_settings)

        assert changes == {}


class TestReloadDoesNotReloadUnsafe:
    """Verify that reload_settings() does NOT change unsafe settings."""

    def test_does_not_reload_auth_settings(self, live_settings):
        """Auth settings must NOT be reloaded (require restart)."""
        live_settings_auth = NetworkSettings(auth_enabled=False)

        with patch("network_mcp.config.NetworkSettings") as mock_cls:
            fresh = NetworkSettings(auth_enabled=False)
            # Simulate auth_enabled changing in env
            object.__setattr__(fresh, "auth_enabled", True)
            mock_cls.return_value = fresh

            with (
                patch("network_mcp.config._propagate_rate_limiter"),
                patch("network_mcp.config._propagate_circuit_breaker"),
                patch("network_mcp.config._propagate_cache_ttl"),
            ):
                changes = reload_settings(live_settings_auth)

        # auth_enabled is NOT in _RELOADABLE_SETTINGS
        assert "auth_enabled" not in changes
        assert live_settings_auth.auth_enabled is False

    def test_does_not_reload_module_settings(self, live_settings):
        """Module loading settings must NOT be reloaded."""
        assert "net_disabled_modules" not in _RELOADABLE_SETTINGS
        assert "net_enabled_modules" not in _RELOADABLE_SETTINGS
        assert "net_progressive_discovery" not in _RELOADABLE_SETTINGS

    def test_does_not_reload_credentials(self, live_settings):
        """Credentials must NOT be reloaded."""
        assert "net_username" not in _RELOADABLE_SETTINGS
        assert "net_password" not in _RELOADABLE_SETTINGS

    def test_does_not_reload_transport(self, live_settings):
        """Transport settings must NOT be reloaded."""
        assert "net_transport" not in _RELOADABLE_SETTINGS
        assert "net_inventory_file" not in _RELOADABLE_SETTINGS

    def test_does_not_reload_demo_mode(self, live_settings):
        """Demo mode must NOT be reloaded."""
        assert "net_demo_mode" not in _RELOADABLE_SETTINGS

    def test_does_not_reload_rbac(self, live_settings):
        """RBAC settings must NOT be reloaded."""
        assert "rbac_enabled" not in _RELOADABLE_SETTINGS


class TestReloadPropagation:
    """Test that reload actually propagates to live subsystems."""

    def test_log_level_applied_immediately(self, live_settings):
        """Log level change is applied to root logger immediately."""
        with patch("network_mcp.config.NetworkSettings") as mock_cls:
            fresh = NetworkSettings(log_level="WARNING")
            mock_cls.return_value = fresh

            with (
                patch("network_mcp.config._propagate_rate_limiter"),
                patch("network_mcp.config._propagate_circuit_breaker"),
                patch("network_mcp.config._propagate_cache_ttl"),
            ):
                reload_settings(live_settings)

        assert logging.getLogger().level == logging.WARNING
        # Restore for other tests
        logging.getLogger().setLevel(logging.INFO)

    def test_rate_limiter_propagated(self):
        """Rate limiter update clears existing buckets and updates rates."""
        from network_mcp.helpers import device_rate_limiter

        # Create a bucket for a host
        device_rate_limiter.check_show("test-host")
        assert "test-host" in device_rate_limiter._show_buckets

        settings = NetworkSettings(rate_limit_show=20.0, rate_limit_config=5.0)

        from network_mcp.config import _propagate_rate_limiter

        _propagate_rate_limiter(settings)

        # Buckets should be cleared
        assert len(device_rate_limiter._show_buckets) == 0
        assert len(device_rate_limiter._config_buckets) == 0
        # Rates should be updated
        assert device_rate_limiter._show_rate == 20.0
        assert device_rate_limiter._config_rate == 5.0

        # Restore original rates
        device_rate_limiter._show_rate = 5.0
        device_rate_limiter._config_rate = 1.0

    def test_circuit_breaker_propagated(self):
        """Circuit breaker update changes threshold and cooldown on registry."""
        from network_mcp.helpers import circuit_breaker_registry

        old_threshold = circuit_breaker_registry._threshold
        old_cooldown = circuit_breaker_registry._cooldown

        settings = NetworkSettings(net_cb_threshold=10, net_cb_cooldown=180)

        from network_mcp.config import _propagate_circuit_breaker

        _propagate_circuit_breaker(settings)

        assert circuit_breaker_registry._threshold == 10
        assert circuit_breaker_registry._cooldown == 180

        # Restore
        circuit_breaker_registry._threshold = old_threshold
        circuit_breaker_registry._cooldown = old_cooldown

    def test_cache_ttl_propagated(self):
        """Cache default TTL update changes the module-level _FALLBACK_TTL."""
        import network_mcp.cache as cache_module

        old_ttl = cache_module._FALLBACK_TTL

        settings = NetworkSettings(net_cache_default_ttl=120)

        from network_mcp.config import _propagate_cache_ttl

        _propagate_cache_ttl(settings)

        assert cache_module._FALLBACK_TTL == 120

        # Restore
        cache_module._FALLBACK_TTL = old_ttl


# --- get_safe_config() tests ---


class TestGetSafeConfig:
    """Tests for get_safe_config() which excludes secret fields."""

    def test_excludes_password(self):
        """net_password must be excluded from safe config."""
        settings = NetworkSettings()
        safe = get_safe_config(settings)
        assert "net_password" not in safe

    def test_excludes_auth_secret_key(self):
        """auth_secret_key must be excluded from safe config."""
        settings = NetworkSettings()
        safe = get_safe_config(settings)
        assert "auth_secret_key" not in safe

    def test_excludes_all_secret_fields(self):
        """All fields in _SECRET_FIELDS must be excluded."""
        settings = NetworkSettings()
        safe = get_safe_config(settings)
        for field_name in _SECRET_FIELDS:
            assert field_name not in safe, f"Secret field '{field_name}' should not appear in safe config"

    def test_includes_non_secret_fields(self):
        """Non-secret settings must be included."""
        settings = NetworkSettings()
        safe = get_safe_config(settings)
        assert "rate_limit_show" in safe
        assert "timeout_connect" in safe
        assert "log_level" in safe
        assert "net_read_only" in safe
        assert "net_demo_mode" in safe
        assert "net_cache_enabled" in safe

    def test_safe_config_values_correct(self):
        """Values in safe config must match the settings object."""
        settings = NetworkSettings(rate_limit_show=7.5, log_level="DEBUG")
        safe = get_safe_config(settings)
        assert safe["rate_limit_show"] == 7.5
        assert safe["log_level"] == "DEBUG"


# --- Audit logging for config reload ---


class TestAuditConfigReload:
    """Tests for audit logging of configuration changes."""

    def test_audit_logs_changes(self):
        """Config reload changes are logged to the audit trail."""
        from network_mcp.audit import AuditLogger

        audit = AuditLogger(signing_key=None)
        mock_emit = MagicMock()
        audit._emit = mock_emit

        changes = {
            "rate_limit_show": {"old": 5.0, "new": 10.0},
            "log_level": {"old": "INFO", "new": "DEBUG"},
        }

        with (
            patch("network_mcp.middleware.get_correlation_id", return_value="test-corr-id"),
            patch("network_mcp.middleware.get_current_user", return_value="test-user"),
            patch("network_mcp.middleware.get_current_tenant_id", return_value="test-tenant"),
        ):
            audit.log_config_reload(changes)

        assert mock_emit.call_count == 2
        # Check first call (rate_limit_show)
        first_call = mock_emit.call_args_list[0]
        audit_data = first_call[0][0]
        assert audit_data["event"] == "config_reload"
        assert audit_data["action"] == "config_reload"
        assert audit_data["setting_name"] == "rate_limit_show"
        assert audit_data["old_value"] == 5.0
        assert audit_data["new_value"] == 10.0
        assert audit_data["user"] == "test-user"

    def test_audit_redacts_secret_values(self):
        """Secret values must be redacted as '***' in audit logs."""
        from network_mcp.audit import AuditLogger

        audit = AuditLogger(signing_key=None)
        mock_emit = MagicMock()
        audit._emit = mock_emit

        # Simulate a secret field being reloaded (should not happen, but defense-in-depth)
        changes = {
            "net_password": {"old": "old-secret", "new": "new-secret"},
        }

        with (
            patch("network_mcp.middleware.get_correlation_id", return_value=None),
            patch("network_mcp.middleware.get_current_user", return_value=None),
            patch("network_mcp.middleware.get_current_tenant_id", return_value=None),
        ):
            audit.log_config_reload(changes)

        call_data = mock_emit.call_args_list[0][0][0]
        assert call_data["old_value"] == "***"
        assert call_data["new_value"] == "***"


# --- ENV var naming with NET_ prefix ---


class TestNetPrefixAliases:
    """Test that NET_ prefixed env var names work and take priority."""

    def test_net_log_level_alias(self):
        """NET_LOG_LEVEL should be accepted."""
        with patch.dict("os.environ", {"NET_LOG_LEVEL": "WARNING"}, clear=False):
            s = NetworkSettings()
            assert s.log_level == "WARNING"

    def test_old_log_level_still_works(self):
        """LOG_LEVEL (without NET_ prefix) should still work as fallback."""
        with patch.dict("os.environ", {"LOG_LEVEL": "ERROR"}, clear=False):
            s = NetworkSettings()
            assert s.log_level == "ERROR"

    def test_net_prefix_takes_priority(self):
        """NET_ prefixed name should take priority over old name."""
        with patch.dict("os.environ", {"NET_LOG_LEVEL": "DEBUG", "LOG_LEVEL": "ERROR"}, clear=False):
            s = NetworkSettings()
            assert s.log_level == "DEBUG"

    def test_net_timeout_connect_alias(self):
        """NET_TIMEOUT_CONNECT should be accepted."""
        with patch.dict("os.environ", {"NET_TIMEOUT_CONNECT": "15.0"}, clear=False):
            s = NetworkSettings()
            assert s.timeout_connect == 15.0

    def test_old_timeout_connect_still_works(self):
        """TIMEOUT_CONNECT (without NET_ prefix) should still work."""
        with patch.dict("os.environ", {"TIMEOUT_CONNECT": "20.0"}, clear=False):
            s = NetworkSettings()
            assert s.timeout_connect == 20.0

    def test_net_timeout_show_alias(self):
        """NET_TIMEOUT_SHOW should be accepted."""
        with patch.dict("os.environ", {"NET_TIMEOUT_SHOW": "45.0"}, clear=False):
            s = NetworkSettings()
            assert s.timeout_show == 45.0

    def test_net_timeout_config_alias(self):
        """NET_TIMEOUT_CONFIG should be accepted."""
        with patch.dict("os.environ", {"NET_TIMEOUT_CONFIG": "90.0"}, clear=False):
            s = NetworkSettings()
            assert s.timeout_config == 90.0

    def test_net_rate_limit_show_alias(self):
        """NET_RATE_LIMIT_SHOW should be accepted."""
        with patch.dict("os.environ", {"NET_RATE_LIMIT_SHOW": "15.0"}, clear=False):
            s = NetworkSettings()
            assert s.rate_limit_show == 15.0

    def test_old_rate_limit_show_still_works(self):
        """RATE_LIMIT_SHOW (without NET_ prefix) should still work."""
        with patch.dict("os.environ", {"RATE_LIMIT_SHOW": "12.0"}, clear=False):
            s = NetworkSettings()
            assert s.rate_limit_show == 12.0

    def test_net_rate_limit_config_alias(self):
        """NET_RATE_LIMIT_CONFIG should be accepted."""
        with patch.dict("os.environ", {"NET_RATE_LIMIT_CONFIG": "3.0"}, clear=False):
            s = NetworkSettings()
            assert s.rate_limit_config == 3.0

    def test_net_keepalive_interval_alias(self):
        """NET_KEEPALIVE_INTERVAL should be accepted."""
        with patch.dict("os.environ", {"NET_KEEPALIVE_INTERVAL": "120"}, clear=False):
            s = NetworkSettings()
            assert s.keepalive_interval == 120

    def test_old_keepalive_interval_still_works(self):
        """KEEPALIVE_INTERVAL (without NET_ prefix) should still work."""
        with patch.dict("os.environ", {"KEEPALIVE_INTERVAL": "90"}, clear=False):
            s = NetworkSettings()
            assert s.keepalive_interval == 90

    def test_net_gnmi_port_alias(self):
        """NET_GNMI_PORT should be accepted."""
        with patch.dict("os.environ", {"NET_GNMI_PORT": "50051"}, clear=False):
            s = NetworkSettings()
            assert s.gnmi_port == 50051

    def test_old_gnmi_port_still_works(self):
        """GNMI_PORT (without NET_ prefix) should still work."""
        with patch.dict("os.environ", {"GNMI_PORT": "50051"}, clear=False):
            s = NetworkSettings()
            assert s.gnmi_port == 50051

    def test_net_gnmi_insecure_alias(self):
        """NET_GNMI_INSECURE should be accepted."""
        with patch.dict("os.environ", {"NET_GNMI_INSECURE": "true"}, clear=False):
            s = NetworkSettings()
            assert s.gnmi_insecure is True

    def test_net_gnmi_timeout_alias(self):
        """NET_GNMI_TIMEOUT should be accepted."""
        with patch.dict("os.environ", {"NET_GNMI_TIMEOUT": "30.0"}, clear=False):
            s = NetworkSettings()
            assert s.gnmi_timeout == 30.0

    def test_net_prometheus_enabled_alias(self):
        """NET_PROMETHEUS_ENABLED should be accepted."""
        with patch.dict("os.environ", {"NET_PROMETHEUS_ENABLED": "true"}, clear=False):
            s = NetworkSettings()
            assert s.prometheus_enabled is True

    def test_old_prometheus_enabled_still_works(self):
        """PROMETHEUS_ENABLED (without NET_ prefix) should still work."""
        with patch.dict("os.environ", {"PROMETHEUS_ENABLED": "true"}, clear=False):
            s = NetworkSettings()
            assert s.prometheus_enabled is True

    def test_net_cache_default_ttl(self):
        """NET_CACHE_DEFAULT_TTL should set the cache default TTL."""
        with patch.dict("os.environ", {"NET_CACHE_DEFAULT_TTL": "120"}, clear=False):
            s = NetworkSettings()
            assert s.net_cache_default_ttl == 120


# --- RBAC scope for config_reload ---


class TestConfigReloadRBAC:
    """Tests for RBAC scope requirement on config_reload."""

    def test_config_reload_requires_admin_scope(self):
        """config_reload tool should require network:admin scope."""
        from network_mcp.rbac import get_required_scope

        scope = get_required_scope("config_reload")
        assert scope == "network:admin"

    def test_config_reload_denied_without_admin(self):
        """config_reload denied for users without network:admin scope."""
        from network_mcp.rbac import check_authorization

        authorized, msg = check_authorization("config_reload", {"scope": "network:read"})
        assert authorized is False
        assert "network:admin" in msg

    def test_config_reload_allowed_with_admin(self):
        """config_reload allowed for users with network:admin scope."""
        from network_mcp.rbac import check_authorization

        authorized, _ = check_authorization("config_reload", {"scope": "network:admin"})
        assert authorized is True


# --- Reloadable settings completeness check ---


class TestReloadableSettingsDefinition:
    """Ensure _RELOADABLE_SETTINGS is well-defined."""

    def test_all_reloadable_settings_exist_on_model(self):
        """Every entry in _RELOADABLE_SETTINGS must be a real field on NetworkSettings."""
        for field_name in _RELOADABLE_SETTINGS:
            assert field_name in NetworkSettings.model_fields, (
                f"Reloadable setting '{field_name}' is not a field on NetworkSettings"
            )

    def test_no_secret_fields_are_reloadable(self):
        """No secret field should be in the reloadable set."""
        overlap = _RELOADABLE_SETTINGS & _SECRET_FIELDS
        assert not overlap, f"Secret fields must not be reloadable: {overlap}"

    def test_unsafe_fields_not_reloadable(self):
        """Critical infrastructure settings must not be in the reloadable set."""
        unsafe = {
            "auth_enabled",
            "auth_issuer_url",
            "auth_secret_key",
            "rbac_enabled",
            "net_demo_mode",
            "net_progressive_discovery",
            "net_disabled_modules",
            "net_enabled_modules",
            "net_username",
            "net_password",
            "net_transport",
            "net_inventory_file",
        }
        overlap = _RELOADABLE_SETTINGS & unsafe
        assert not overlap, f"Unsafe settings must not be reloadable: {overlap}"
