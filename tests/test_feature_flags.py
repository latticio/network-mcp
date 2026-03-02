"""Tests for tenant-based feature targeting (feature_flags module)."""

from unittest.mock import patch

import pytest

from network_mcp.config import FeatureFlag, NetworkSettings
from network_mcp.feature_flags import (
    _reset_settings,
    get_all_feature_flags,
    get_feature_flag,
    is_feature_enabled,
)
from tests.conftest import make_test_settings


@pytest.fixture(autouse=True)
def _reset_ff_settings():
    """Reset feature flag settings cache before each test."""
    _reset_settings()
    yield
    _reset_settings()


def _make_settings_with_flags(flags_json: str) -> NetworkSettings:
    """Create settings with the given feature flags JSON string."""
    return make_test_settings(net_feature_flags=flags_json)


# --- FeatureFlag model tests ---


class TestFeatureFlagModel:
    def test_defaults(self):
        flag = FeatureFlag()
        assert flag.enabled is False
        assert flag.tenants == []

    def test_enabled_no_tenants(self):
        flag = FeatureFlag(enabled=True)
        assert flag.enabled is True
        assert flag.tenants == []

    def test_enabled_with_tenants(self):
        flag = FeatureFlag(enabled=True, tenants=["tenant-a", "tenant-b"])
        assert flag.enabled is True
        assert flag.tenants == ["tenant-a", "tenant-b"]


# --- NetworkSettings.feature_flags property tests ---


class TestFeatureFlagsProperty:
    def test_empty_default(self):
        settings = make_test_settings()
        assert settings.feature_flags == {}

    def test_valid_json_single_flag(self):
        settings = _make_settings_with_flags('{"beta": {"enabled": true}}')
        flags = settings.feature_flags
        assert "beta" in flags
        assert flags["beta"].enabled is True
        assert flags["beta"].tenants == []

    def test_valid_json_multiple_flags(self):
        settings = _make_settings_with_flags(
            '{"beta": {"enabled": true}, "alpha": {"enabled": false, "tenants": ["t1"]}}'
        )
        flags = settings.feature_flags
        assert len(flags) == 2
        assert flags["beta"].enabled is True
        assert flags["alpha"].enabled is False
        assert flags["alpha"].tenants == ["t1"]

    def test_invalid_json_returns_empty(self):
        settings = _make_settings_with_flags("not-json")
        assert settings.feature_flags == {}

    def test_non_object_json_returns_empty(self):
        settings = _make_settings_with_flags("[1, 2, 3]")
        assert settings.feature_flags == {}

    def test_invalid_flag_definition_skipped(self):
        settings = _make_settings_with_flags('{"good": {"enabled": true}, "bad": "not-a-dict"}')
        flags = settings.feature_flags
        assert "good" in flags
        assert "bad" not in flags

    def test_empty_object_string(self):
        settings = _make_settings_with_flags("{}")
        assert settings.feature_flags == {}


# --- is_feature_enabled tests ---


class TestIsFeatureEnabled:
    def _patch_settings(self, flags_json: str):
        settings = _make_settings_with_flags(flags_json)
        return patch("network_mcp.feature_flags._get_settings", return_value=settings)

    def test_feature_not_defined_returns_false(self):
        with self._patch_settings("{}"):
            assert is_feature_enabled("nonexistent") is False

    def test_feature_disabled_returns_false(self):
        with self._patch_settings('{"my_feature": {"enabled": false}}'):
            assert is_feature_enabled("my_feature") is False

    def test_feature_enabled_no_tenant_restriction(self):
        with self._patch_settings('{"my_feature": {"enabled": true}}'):
            assert is_feature_enabled("my_feature") is True
            assert is_feature_enabled("my_feature", tenant_id="any-tenant") is True
            assert is_feature_enabled("my_feature", tenant_id=None) is True

    def test_feature_enabled_tenant_restricted_matching(self):
        with self._patch_settings('{"my_feature": {"enabled": true, "tenants": ["tenant-a", "tenant-b"]}}'):
            assert is_feature_enabled("my_feature", tenant_id="tenant-a") is True
            assert is_feature_enabled("my_feature", tenant_id="tenant-b") is True

    def test_feature_enabled_tenant_restricted_not_matching(self):
        with self._patch_settings('{"my_feature": {"enabled": true, "tenants": ["tenant-a"]}}'):
            assert is_feature_enabled("my_feature", tenant_id="tenant-c") is False

    def test_feature_enabled_tenant_restricted_none_tenant(self):
        with self._patch_settings('{"my_feature": {"enabled": true, "tenants": ["tenant-a"]}}'):
            assert is_feature_enabled("my_feature", tenant_id=None) is False

    def test_feature_enabled_tenant_restricted_no_tenant_arg(self):
        with self._patch_settings('{"my_feature": {"enabled": true, "tenants": ["tenant-a"]}}'):
            assert is_feature_enabled("my_feature") is False


# --- get_all_feature_flags tests ---


class TestGetAllFeatureFlags:
    def test_returns_all_flags(self):
        settings = _make_settings_with_flags(
            '{"alpha": {"enabled": true}, "beta": {"enabled": false, "tenants": ["t1"]}}'
        )
        with patch("network_mcp.feature_flags._get_settings", return_value=settings):
            flags = get_all_feature_flags()
            assert len(flags) == 2
            assert "alpha" in flags
            assert "beta" in flags

    def test_returns_empty_when_no_flags(self):
        settings = _make_settings_with_flags("{}")
        with patch("network_mcp.feature_flags._get_settings", return_value=settings):
            assert get_all_feature_flags() == {}


# --- get_feature_flag tests ---


class TestGetFeatureFlag:
    def test_returns_flag_by_name(self):
        settings = _make_settings_with_flags('{"my_feature": {"enabled": true, "tenants": ["t1"]}}')
        with patch("network_mcp.feature_flags._get_settings", return_value=settings):
            flag = get_feature_flag("my_feature")
            assert flag is not None
            assert flag.enabled is True
            assert flag.tenants == ["t1"]

    def test_returns_none_for_unknown_flag(self):
        settings = _make_settings_with_flags("{}")
        with patch("network_mcp.feature_flags._get_settings", return_value=settings):
            assert get_feature_flag("nonexistent") is None


# --- NET_FEATURE_FLAGS env var parsing tests ---


class TestEnvVarParsing:
    def test_env_var_parsing_valid(self):
        """Verify NET_FEATURE_FLAGS env var is parsed correctly."""
        import json

        flags_data = {
            "new_dashboard": {"enabled": True, "tenants": ["acme", "globex"]},
            "dark_mode": {"enabled": True},
            "experimental": {"enabled": False},
        }
        settings = _make_settings_with_flags(json.dumps(flags_data))
        parsed = settings.feature_flags

        assert len(parsed) == 3
        assert parsed["new_dashboard"].enabled is True
        assert parsed["new_dashboard"].tenants == ["acme", "globex"]
        assert parsed["dark_mode"].enabled is True
        assert parsed["dark_mode"].tenants == []
        assert parsed["experimental"].enabled is False

    def test_env_var_parsing_invalid_json(self):
        """Verify graceful handling of invalid JSON."""
        settings = _make_settings_with_flags("{invalid json}")
        assert settings.feature_flags == {}

    def test_env_var_parsing_empty_string(self):
        """Verify empty string results in no flags."""
        settings = _make_settings_with_flags("")
        assert settings.feature_flags == {}
