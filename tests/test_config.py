"""Tests for NetworkSettings configuration model."""

import os

import pytest
from pydantic import ValidationError

from network_mcp.config import NetworkSettings


def test_settings_defaults():
    s = NetworkSettings(net_username="admin", net_password="", net_transport="https")

    assert s.net_username == "admin"
    assert s.net_password.get_secret_value() == ""
    assert s.net_transport == "https"
    assert s.log_level == "INFO"
    assert s.net_read_only is True
    assert s.timeout_connect == 10.0
    assert s.timeout_show == 30.0
    assert s.timeout_config == 60.0
    assert s.rate_limit_show == 5.0
    assert s.rate_limit_config == 1.0
    assert s.auth_enabled is False
    assert s.gnmi_insecure is False


def test_settings_from_env():
    env = {
        "NET_USERNAME": "testuser",
        "NET_PASSWORD": "testpass",
        "NET_TRANSPORT": "http",
        "LOG_LEVEL": "DEBUG",
        "NET_READ_ONLY": "false",
    }
    for k, v in env.items():
        os.environ[k] = v
    try:
        s = NetworkSettings()
        assert s.net_username == "testuser"
        assert s.net_password.get_secret_value() == "testpass"
        assert s.net_transport == "http"
        assert s.log_level == "DEBUG"
        assert s.net_read_only is False
    finally:
        for k in env:
            os.environ.pop(k, None)


def test_settings_disabled_modules_empty():
    s = NetworkSettings(net_username="admin", net_password="", net_transport="https", net_disabled_modules="")

    assert s.disabled_modules == set()


def test_settings_disabled_modules_parsed():
    s = NetworkSettings(
        net_username="admin",
        net_password="",
        net_transport="https",
        net_disabled_modules="security, evpn_vxlan , bfd",
    )

    assert s.disabled_modules == {"security", "evpn_vxlan", "bfd"}


def test_settings_secret_str_hides_password():
    s = NetworkSettings(net_username="admin", net_password="supersecret", net_transport="https")

    assert "supersecret" not in repr(s.net_password)
    assert "supersecret" not in str(s.net_password)
    assert s.net_password.get_secret_value() == "supersecret"


def test_settings_gnmi_defaults():
    s = NetworkSettings(net_username="admin", net_password="", net_transport="https")

    assert s.gnmi_port == 6030
    assert s.gnmi_insecure is False
    assert s.gnmi_timeout == 10
    assert s.gnmi_tls_cert is None


def test_settings_auth_defaults():
    s = NetworkSettings(net_username="admin", net_password="", net_transport="https")

    assert s.auth_enabled is False
    assert s.auth_issuer_url is None
    assert s.auth_required_scopes == "network:read"


def test_settings_enabled_modules_none_by_default():
    s = NetworkSettings(net_username="admin", net_password="", net_transport="https")

    assert s.net_enabled_modules is None
    assert s.enabled_modules is None


def test_settings_enabled_modules_parsed():
    s = NetworkSettings(
        net_username="admin",
        net_password="",
        net_transport="https",
        net_enabled_modules="evpn_vxlan, security , qos",
    )

    assert s.enabled_modules == {"evpn_vxlan", "security", "qos"}


def test_settings_enabled_modules_empty_string():
    s = NetworkSettings(
        net_username="admin",
        net_password="",
        net_transport="https",
        net_enabled_modules="",
    )

    # Empty string parses to empty set (not None)
    assert s.enabled_modules == set()


def test_settings_verify_connections_default():
    s = NetworkSettings(net_username="admin", net_password="", net_transport="https")

    assert s.net_verify_connections is False


def test_settings_verify_connections_enabled():
    s = NetworkSettings(
        net_username="admin",
        net_password="",
        net_transport="https",
        net_verify_connections=True,
    )

    assert s.net_verify_connections is True


# --- Bounds validation tests ---


class TestConfigBoundsValidation:
    """Test that numeric settings reject out-of-bounds values."""

    def test_timeout_connect_negative_rejected(self):
        with pytest.raises(ValidationError, match="timeout_connect"):
            NetworkSettings(net_username="admin", net_password="", net_transport="https", timeout_connect=-1)

    def test_timeout_connect_zero_rejected(self):
        with pytest.raises(ValidationError, match="timeout_connect"):
            NetworkSettings(net_username="admin", net_password="", net_transport="https", timeout_connect=0)

    def test_timeout_connect_too_high_rejected(self):
        with pytest.raises(ValidationError, match="timeout_connect"):
            NetworkSettings(net_username="admin", net_password="", net_transport="https", timeout_connect=121)

    def test_timeout_show_too_high_rejected(self):
        with pytest.raises(ValidationError, match="timeout_show"):
            NetworkSettings(net_username="admin", net_password="", net_transport="https", timeout_show=999999)

    def test_timeout_config_too_high_rejected(self):
        with pytest.raises(ValidationError, match="timeout_config"):
            NetworkSettings(net_username="admin", net_password="", net_transport="https", timeout_config=601)

    def test_rate_limit_show_zero_rejected(self):
        with pytest.raises(ValidationError, match="rate_limit_show"):
            NetworkSettings(net_username="admin", net_password="", net_transport="https", rate_limit_show=0)

    def test_rate_limit_show_too_high_rejected(self):
        with pytest.raises(ValidationError, match="rate_limit_show"):
            NetworkSettings(net_username="admin", net_password="", net_transport="https", rate_limit_show=101)

    def test_rate_limit_config_too_high_rejected(self):
        with pytest.raises(ValidationError, match="rate_limit_config"):
            NetworkSettings(net_username="admin", net_password="", net_transport="https", rate_limit_config=51)

    def test_gnmi_timeout_negative_rejected(self):
        with pytest.raises(ValidationError, match="gnmi_timeout"):
            NetworkSettings(net_username="admin", net_password="", net_transport="https", gnmi_timeout=-1)

    def test_gnmi_timeout_too_high_rejected(self):
        with pytest.raises(ValidationError, match="gnmi_timeout"):
            NetworkSettings(net_username="admin", net_password="", net_transport="https", gnmi_timeout=121)

    def test_valid_bounds_accepted(self):
        s = NetworkSettings(
            net_username="admin",
            net_password="",
            net_transport="https",
            timeout_connect=1.0,
            timeout_show=300.0,
            timeout_config=600.0,
            rate_limit_show=100.0,
            rate_limit_config=50.0,
            gnmi_timeout=120.0,
        )
        assert s.timeout_connect == 1.0
        assert s.timeout_show == 300.0
        assert s.timeout_config == 600.0
        assert s.rate_limit_show == 100.0
        assert s.rate_limit_config == 50.0
        assert s.gnmi_timeout == 120.0


class TestConfigModuleNameValidation:
    """Test that unknown module names in NET_DISABLED_MODULES / NET_ENABLED_MODULES are rejected."""

    def test_unknown_disabled_module_rejected(self):
        with pytest.raises(ValidationError, match="Unknown module in NET_DISABLED_MODULES"):
            NetworkSettings(
                net_username="admin",
                net_password="",
                net_transport="https",
                net_disabled_modules="bogus_module",
            )

    def test_valid_disabled_modules_accepted(self):
        s = NetworkSettings(
            net_username="admin",
            net_password="",
            net_transport="https",
            net_disabled_modules="evpn_vxlan, security, bfd",
        )
        assert s.disabled_modules == {"evpn_vxlan", "security", "bfd"}

    def test_unknown_enabled_module_rejected(self):
        with pytest.raises(ValidationError, match="Unknown module in NET_ENABLED_MODULES"):
            NetworkSettings(
                net_username="admin",
                net_password="",
                net_transport="https",
                net_enabled_modules="nonexistent",
            )

    def test_valid_enabled_modules_accepted(self):
        s = NetworkSettings(
            net_username="admin",
            net_password="",
            net_transport="https",
            net_enabled_modules="vrf, qos, compliance",
        )
        assert s.enabled_modules == {"vrf", "qos", "compliance"}

    def test_empty_disabled_modules_accepted(self):
        s = NetworkSettings(
            net_username="admin",
            net_password="",
            net_transport="https",
            net_disabled_modules="",
        )
        assert s.disabled_modules == set()


class TestVendorSettings:
    """Tests for NET_VENDORS parsing and validation."""

    def _base(self, **kwargs):
        return NetworkSettings(net_username="admin", net_password="", net_transport="https", **kwargs)

    def test_enabled_vendors_none_by_default(self):
        """NET_VENDORS unset means None — all installed vendor tools should load."""
        s = self._base()
        assert s.net_vendors is None
        assert s.enabled_vendors is None

    def test_enabled_vendors_single(self):
        s = self._base(net_vendors="eos")
        assert s.enabled_vendors == {"eos"}

    def test_enabled_vendors_multiple(self):
        s = self._base(net_vendors="eos,iosxe")
        assert s.enabled_vendors == {"eos", "iosxe"}

    def test_enabled_vendors_whitespace_stripped(self):
        s = self._base(net_vendors=" eos , nxos , junos ")
        assert s.enabled_vendors == {"eos", "nxos", "junos"}

    def test_enabled_vendors_lowercased(self):
        """Vendor names should be normalised to lowercase."""
        s = self._base(net_vendors="EOS,IOSXE")
        assert s.enabled_vendors == {"eos", "iosxe"}

    def test_all_four_vendors_accepted(self):
        s = self._base(net_vendors="eos,iosxe,nxos,junos")
        assert s.enabled_vendors == {"eos", "iosxe", "nxos", "junos"}

    def test_unknown_vendor_rejected(self):
        with pytest.raises(ValidationError, match="Unknown vendor in NET_VENDORS"):
            self._base(net_vendors="bogus")

    def test_unknown_vendor_mixed_with_valid_rejected(self):
        with pytest.raises(ValidationError, match="Unknown vendor in NET_VENDORS"):
            self._base(net_vendors="eos,bogus")

    def test_empty_string_yields_empty_set(self):
        """An empty NET_VENDORS string yields an empty set (no vendors)."""
        s = self._base(net_vendors="")
        assert s.enabled_vendors == set()

    def test_env_var_read(self):
        """NET_VENDORS is read from the environment variable."""
        env = {"NET_VENDORS": "iosxe,nxos"}
        for k, v in env.items():
            os.environ[k] = v
        try:
            s = NetworkSettings()
            assert s.enabled_vendors == {"iosxe", "nxos"}
        finally:
            for k in env:
                os.environ.pop(k, None)
