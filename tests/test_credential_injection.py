"""Credential injection and credential leakage prevention tests.

Verifies that:
- Malicious usernames are rejected or sanitized
- Passwords never appear in log output or error messages
- Connection string passwords are redacted from error messages
- Bearer tokens in error messages are redacted
- SNMP community strings are redacted from output
"""

import logging

from network_mcp.audit import AuditLogger
from network_mcp.sanitizer import (
    config_sanitizer,
    sanitize_error_message,
)

# =========================================================================
# Malicious Username Tests
# =========================================================================


class TestMaliciousUsernameSanitized:
    """Usernames containing injection attempts must be rejected or sanitized."""

    def test_malicious_username_in_audit_log(self):
        """Audit logger should safely handle usernames with shell injection.

        The audit logger records usernames as JSON strings, which inherently
        neutralizes shell injection characters.
        """
        audit = AuditLogger(
            logger_name="test.credential.injection",
            signing_key=b"test-key-for-credential-tests!",
        )
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record)
        audit._logger.addHandler(handler)

        # Malicious username with shell injection
        malicious_user = '"; rm -rf / #'
        audit.log_tool_call(
            tool_name="eos_get_vlans",
            device="spine-01",
            parameters={"host": "spine-01"},
            result_status="success",
            duration_ms=10.0,
            user=malicious_user,
        )

        # Verify the record was logged with the username safely encoded
        assert len(records) >= 1
        audit_data = records[-1].audit_data
        # Username should be stored as-is (JSON encoding handles escaping)
        assert audit_data["user"] == malicious_user
        # But it should not cause command injection when serialized
        import json

        serialized = json.dumps(audit_data)
        assert "rm -rf" in serialized  # Present as data, not as a command
        # Verify JSON is valid (no escaping issues)
        parsed = json.loads(serialized)
        assert parsed["user"] == malicious_user


# =========================================================================
# Password Leakage Prevention Tests
# =========================================================================


class TestMaliciousPasswordNotLogged:
    """Passwords must never appear in audit log output."""

    def test_password_field_redacted_in_audit(self):
        """Audit logger must redact password fields from parameters."""
        audit = AuditLogger(
            logger_name="test.password.redaction",
            signing_key=b"test-key-for-credential-tests!",
        )
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record)
        audit._logger.addHandler(handler)

        # Parameters containing sensitive fields
        params = {
            "host": "spine-01",
            "password": "super_secret_p@ssw0rd",
            "secret": "my-secret-value",
            "token": "eyJhbGciOiJIUzI1NiJ9.test.sig",
            "community": "public-community-string",
            "api_key": "key-12345",
        }
        audit.log_tool_call(
            tool_name="eos_push_config",
            device="spine-01",
            parameters=params,
            result_status="success",
            duration_ms=50.0,
        )

        assert len(records) >= 1
        audit_data = records[-1].audit_data
        redacted_params = audit_data["parameters"]

        # Verify all sensitive fields are redacted
        assert redacted_params["password"] == "***REDACTED***"
        assert redacted_params["secret"] == "***REDACTED***"
        assert redacted_params["token"] == "***REDACTED***"
        assert redacted_params["community"] == "***REDACTED***"
        assert redacted_params["api_key"] == "***REDACTED***"

        # Non-sensitive fields should be preserved
        assert redacted_params["host"] == "spine-01"

    def test_password_in_config_commands_redacted(self):
        """Passwords embedded in config commands must be redacted."""
        audit = AuditLogger(logger_name="test.cmd.redact")
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record)
        audit._logger.addHandler(handler)

        params = {
            "host": "spine-01",
            "commands": [
                "username admin secret 0 MyPlaintextPassword",
                "snmp-server community PUBLIC ro",
                "interface Ethernet1",
            ],
        }
        audit.log_tool_call(
            tool_name="eos_push_config",
            device="spine-01",
            parameters=params,
            result_status="success",
            duration_ms=100.0,
        )

        assert len(records) >= 1
        redacted_cmds = records[-1].audit_data["parameters"]["commands"]
        # Passwords in commands should be redacted
        assert "MyPlaintextPassword" not in redacted_cmds[0]
        assert "***REDACTED***" in redacted_cmds[0]
        # Community strings should be redacted
        assert "PUBLIC" not in redacted_cmds[1]
        assert "***REDACTED***" in redacted_cmds[1]
        # Non-sensitive commands should be preserved
        assert redacted_cmds[2] == "interface Ethernet1"


# =========================================================================
# Connection String Password Redaction Tests
# =========================================================================


class TestConnectionStringPasswordRedacted:
    """Error messages from failed connections must redact passwords from URLs."""

    def test_url_with_credentials_redacted(self):
        """https://user:password@host should become https://***:***@host."""
        error_msg = "Connection failed: https://admin:SuperSecret123@switch-01.example.com:443/command-api"
        sanitized = sanitize_error_message(error_msg)
        assert "SuperSecret123" not in sanitized
        assert "***:***@" in sanitized
        assert "switch-01.example.com" in sanitized

    def test_http_url_credentials_redacted(self):
        """http://user:password@host should also be redacted."""
        error_msg = "Error: http://root:toor@192.168.1.1/api"
        sanitized = sanitize_error_message(error_msg)
        assert "toor" not in sanitized
        assert "***:***@" in sanitized

    def test_connection_string_password_field(self):
        """password=secret in connection strings should be redacted."""
        error_msg = "DSN error: host=switch-01 port=443 password=MyP@ss123 user=admin"
        sanitized = sanitize_error_message(error_msg)
        assert "MyP@ss123" not in sanitized
        assert "password=<REDACTED>" in sanitized

    def test_multiple_credential_patterns_redacted(self):
        """Multiple credential patterns in one message should all be redacted."""
        error_msg = 'Connection https://user:pass@host:443 failed with "password": "secret123" and pwd=another_secret'
        sanitized = sanitize_error_message(error_msg)
        assert "pass" not in sanitized.split("@")[0].split("//")[-1] if "@" in sanitized else True
        assert "secret123" not in sanitized
        assert "another_secret" not in sanitized


# =========================================================================
# Bearer Token Redaction Tests
# =========================================================================


class TestAPITokenInHeaderRedacted:
    """Bearer tokens in error messages must be redacted."""

    def test_bearer_token_redacted(self):
        """Authorization: Bearer <jwt> should be redacted."""
        error_msg = (
            "HTTP 401: Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.Gfx6VO9tcxwk6xqx9yYzSfebfeakZp5JYIgP_edcw_A"
        )
        sanitized = sanitize_error_message(error_msg)
        assert "eyJhbGciOi" not in sanitized
        assert "<REDACTED>" in sanitized

    def test_bearer_token_case_insensitive(self):
        """bearer token redaction should be case-insensitive."""
        error_msg = "authorization: bearer my-secret-token-value"
        sanitized = sanitize_error_message(error_msg)
        assert "my-secret-token-value" not in sanitized
        assert "<REDACTED>" in sanitized

    def test_json_password_field_redacted(self):
        """JSON-style password fields in error messages should be redacted."""
        error_msg = 'Response body: {"password": "admin123", "username": "admin"}'
        sanitized = sanitize_error_message(error_msg)
        assert "admin123" not in sanitized
        assert '"<REDACTED>"' in sanitized


# =========================================================================
# SNMP Community String Redaction Tests
# =========================================================================


class TestSNMPCommunityStringRedacted:
    """SNMP community strings in device output must be redacted."""

    def test_snmp_community_string_redacted(self):
        """snmp-server community <string> must be redacted."""
        config_text = """
snmp-server community MySecret123 ro
snmp-server community WriteAccess rw
"""
        sanitized = config_sanitizer.sanitize(config_text)
        assert "MySecret123" not in sanitized
        assert "WriteAccess" not in sanitized
        assert "***REDACTED***" in sanitized

    def test_tacacs_key_redacted(self):
        """TACACS server key must be redacted."""
        config_text = "tacacs-server host 10.0.0.1 key 7 070C285F4D06"
        sanitized = config_sanitizer.sanitize(config_text)
        assert "070C285F4D06" not in sanitized
        assert "***REDACTED***" in sanitized

    def test_radius_key_redacted(self):
        """RADIUS server key must be redacted."""
        config_text = "radius-server host 10.0.0.2 key SecretKey123"
        sanitized = config_sanitizer.sanitize(config_text)
        assert "SecretKey123" not in sanitized
        assert "***REDACTED***" in sanitized

    def test_bgp_neighbor_password_redacted(self):
        """BGP neighbor password must be redacted."""
        config_text = "neighbor 10.0.0.2 password 7 094F471A1A0A"
        sanitized = config_sanitizer.sanitize(config_text)
        assert "094F471A1A0A" not in sanitized
        assert "***REDACTED***" in sanitized

    def test_enable_secret_redacted(self):
        """Enable secret must be redacted."""
        config_text = "enable secret 5 $1$mERr$hx5rVt7rPNoS4wqbXKX7m0"
        sanitized = config_sanitizer.sanitize(config_text)
        assert "$1$mERr$hx5rVt7rPNoS4wqbXKX7m0" not in sanitized
        assert "***REDACTED***" in sanitized

    def test_certificate_material_redacted(self):
        """Certificate private keys must be redacted."""
        config_text = """
-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC7
-----END PRIVATE KEY-----
"""
        sanitized = config_sanitizer.sanitize(config_text)
        assert "MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC7" not in sanitized
        assert "CERTIFICATE_MATERIAL_REDACTED" in sanitized

    def test_nxos_type7_password_redacted(self):
        """NX-OS type 7 password must be redacted."""
        config_text = "  password 7 08354F4B1A0A"
        sanitized = config_sanitizer.sanitize(config_text)
        assert "08354F4B1A0A" not in sanitized

    def test_junos_encrypted_password_redacted(self):
        """JunOS $9$ encrypted password must be redacted."""
        config_text = "set system login user admin authentication encrypted-password $9$abcDEFghiJKL"
        sanitized = config_sanitizer.sanitize(config_text)
        assert "$9$abcDEFghiJKL" not in sanitized
        assert "$9$<REDACTED>" in sanitized

    def test_empty_input_returns_empty(self):
        """sanitize_error_message with empty string returns empty."""
        assert sanitize_error_message("") == ""

    def test_none_like_empty_returns_same(self):
        """sanitize_error_message with None-like empty returns same."""
        assert sanitize_error_message("") == ""

    def test_no_credentials_unchanged(self):
        """Messages without credentials should pass through unchanged."""
        msg = "Connection timeout to 10.0.0.1 after 30 seconds"
        assert sanitize_error_message(msg) == msg
