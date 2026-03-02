"""Tests for error message sanitization and vendor-specific password redaction."""

from network_mcp.sanitizer import (
    config_sanitizer,
    sanitize_error_message,
)

# ---------------------------------------------------------------------------
# sanitize_error_message — URL credentials
# ---------------------------------------------------------------------------


class TestSanitizeErrorMessageURLs:
    def test_http_url_with_credentials(self):
        msg = "Connection failed: https://admin:s3cret@switch01.lab:443/command-api"
        result = sanitize_error_message(msg)
        assert "s3cret" not in result
        assert "admin" not in result
        assert "***:***@switch01.lab" in result

    def test_http_url_without_credentials_unchanged(self):
        msg = "Connection failed: https://switch01.lab:443/command-api"
        result = sanitize_error_message(msg)
        assert result == msg

    def test_multiple_urls_with_credentials(self):
        msg = "Failed https://user1:pass1@host1 and https://user2:pass2@host2"
        result = sanitize_error_message(msg)
        assert "pass1" not in result
        assert "pass2" not in result
        assert "***:***@host1" in result
        assert "***:***@host2" in result


# ---------------------------------------------------------------------------
# sanitize_error_message — Bearer tokens
# ---------------------------------------------------------------------------


class TestSanitizeErrorMessageBearerTokens:
    def test_bearer_token_redacted(self):
        msg = "Request failed with Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
        result = sanitize_error_message(msg)
        assert "eyJ" not in result
        assert "<REDACTED>" in result
        assert "Authorization: Bearer" in result

    def test_bearer_case_insensitive(self):
        msg = "authorization: bearer mytoken123"
        result = sanitize_error_message(msg)
        assert "mytoken123" not in result
        assert "<REDACTED>" in result


# ---------------------------------------------------------------------------
# sanitize_error_message — JSON password fields
# ---------------------------------------------------------------------------


class TestSanitizeErrorMessageJSONPasswords:
    def test_double_quoted_password(self):
        msg = 'Error in config: {"password": "super_secret", "host": "switch01"}'
        result = sanitize_error_message(msg)
        assert "super_secret" not in result
        assert '"<REDACTED>"' in result

    def test_single_quoted_password(self):
        msg = "Error in config: {'password': 'super_secret', 'host': 'switch01'}"
        result = sanitize_error_message(msg)
        assert "super_secret" not in result
        assert '"<REDACTED>"' in result

    def test_secret_field_redacted(self):
        msg = 'Failed: {"secret": "my_token_value"}'
        result = sanitize_error_message(msg)
        assert "my_token_value" not in result

    def test_token_field_redacted(self):
        msg = 'Failed: {"token": "abc123def"}'
        result = sanitize_error_message(msg)
        assert "abc123def" not in result


# ---------------------------------------------------------------------------
# sanitize_error_message — Connection string passwords
# ---------------------------------------------------------------------------


class TestSanitizeErrorMessageConnStrings:
    def test_password_equals_value(self):
        msg = "Connection failed: host=db1 password=s3cret123 port=5432"
        result = sanitize_error_message(msg)
        assert "s3cret123" not in result
        assert "password=<REDACTED>" in result

    def test_pwd_equals_value(self):
        msg = "Failed: pwd=mysecret"
        result = sanitize_error_message(msg)
        assert "mysecret" not in result
        assert "pwd=<REDACTED>" in result


# ---------------------------------------------------------------------------
# sanitize_error_message — passthrough
# ---------------------------------------------------------------------------


class TestSanitizeErrorMessagePassthrough:
    def test_plain_message_unchanged(self):
        msg = "Connection to switch01 timed out after 30 seconds"
        assert sanitize_error_message(msg) == msg

    def test_empty_string_unchanged(self):
        assert sanitize_error_message("") == ""

    def test_none_returns_none(self):
        assert sanitize_error_message(None) is None

    def test_technical_error_unchanged(self):
        msg = "pyeapi.eapilib.ConnectionError: unable to connect to switch01"
        assert sanitize_error_message(msg) == msg


# ---------------------------------------------------------------------------
# Vendor-specific password redaction via config_sanitizer
# ---------------------------------------------------------------------------


class TestJunosPasswordRedaction:
    def test_junos_9_dollar_password_redacted(self):
        config = "set system login user admin authentication encrypted-password $9$abcDEFghiJKLmnoPQR"
        result = config_sanitizer.sanitize(config)
        assert "$9$abcDEFghiJKLmnoPQR" not in result
        assert "$9$<REDACTED>" in result

    def test_junos_9_dollar_inline(self):
        config = 'secret "$9$rKLvWXsYfTzF3"'
        result = config_sanitizer.sanitize(config)
        assert "$9$rKLvWXsYfTzF3" not in result
        assert "$9$<REDACTED>" in result


class TestNxosPasswordRedaction:
    def test_nxos_type7_password_redacted(self):
        config = "  password 7 08354F4B1A\n"
        result = config_sanitizer.sanitize(config)
        assert "08354F4B1A" not in result
        # May be caught by type7_password or nxos_type7_password rule
        assert "ENCRYPTED" in result or "TYPE7_REDACTED" in result

    def test_nxos_type5_password_redacted(self):
        config = "  password 5 $1$abc$DEFghiJKLmnop\n"
        result = config_sanitizer.sanitize(config)
        assert "$1$abc$DEFghiJKLmnop" not in result
        assert "TYPE5_REDACTED" in result or "REDACTED" in result

    def test_nxos_secret_type7(self):
        config = "  secret 7 0A3F2B4C5D\n"
        result = config_sanitizer.sanitize(config)
        assert "0A3F2B4C5D" not in result


class TestIosXePasswordRedaction:
    def test_iosxe_secret5_redacted(self):
        config = "  secret 5 $1$xyz$ABCdefGHIjkl\n"
        result = config_sanitizer.sanitize(config)
        assert "$1$xyz$ABCdefGHIjkl" not in result

    def test_iosxe_secret9_redacted(self):
        config = "  secret 9 $9$abcdefghijklmnop\n"
        result = config_sanitizer.sanitize(config)
        assert "$9$abcdefghijklmnop" not in result


# ---------------------------------------------------------------------------
# Tool validation error integration tests
# ---------------------------------------------------------------------------


class TestToolValidationErrors:
    """Verify that tools reject invalid parameter values via the new validators."""

    def test_eos_create_vlan_rejects_invalid_name(self):
        """eos_create_vlan should reject names with special characters."""
        from network_mcp.tools.vlans import eos_create_vlan

        result = eos_create_vlan("spine-01", 100, "vlan;drop")
        assert result["status"] == "error"
        assert "VLAN name" in result["error"] or "alphanumeric" in result["error"]

    def test_eos_create_vlan_rejects_long_name(self):
        """eos_create_vlan should reject names over 32 chars."""
        from network_mcp.tools.vlans import eos_create_vlan

        result = eos_create_vlan("spine-01", 100, "a" * 33)
        assert result["status"] == "error"
        assert "32" in result["error"]

    def test_net_get_vrf_detail_rejects_spaces(self):
        """net_get_vrf_detail should reject VRF names with spaces."""
        from network_mcp.helpers import validate_vrf_name

        err = validate_vrf_name("my vrf")
        assert err is not None
        assert "no spaces" in err.lower()
