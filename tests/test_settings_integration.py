"""Verify settings injection works end-to-end.

Tests that _init_settings() / _get_settings() in helpers.py correctly
propagate injected settings rather than creating a fresh NetworkSettings default.
"""

from network_mcp.config import NetworkSettings
from network_mcp.helpers import _get_settings, _init_settings


class TestSettingsInjection:
    """Verify the settings injection mechanism in helpers.py."""

    def test_init_and_get_settings_returns_injected_instance(self):
        """Injecting a custom settings instance makes _get_settings() return it."""
        custom = NetworkSettings(net_cache_enabled=False, net_read_only=False)
        _init_settings(custom)
        result = _get_settings()
        assert result is custom, "_get_settings() should return the exact injected instance"

    def test_injected_settings_preserves_non_default_values(self):
        """Injected settings with non-default values are preserved through _get_settings()."""
        custom = NetworkSettings(
            net_cache_enabled=False,
            rate_limit_show=99.0,
            rate_limit_config=42.0,
        )
        _init_settings(custom)
        result = _get_settings()
        assert result.net_cache_enabled is False, "cache_enabled should be False (non-default)"
        assert result.rate_limit_show == 99.0, "rate_limit_show should be 99.0"
        assert result.rate_limit_config == 42.0, "rate_limit_config should be 42.0"

    def test_server_injects_settings_at_import(self):
        """server.py calls _init_settings(settings) at import time — verify it's not None."""
        # This test relies on the fact that importing anything from server.py
        # triggers _init_settings(). Since conftest.py imports conn_mgr from
        # server.py, settings are already injected by the time tests run.
        result = _get_settings()
        assert result is not None, "_get_settings() should not return None after server import"
        assert isinstance(result, NetworkSettings), "Should be an NetworkSettings instance"

    def teardown_method(self):
        """Restore original settings after each test to avoid polluting other tests."""
        # Re-inject the server's settings to restore normal state
        from network_mcp.server import settings

        _init_settings(settings)
