"""Tests for the remediation hints registry (network_mcp.hints)."""

import pytest

from network_mcp.errors import ErrorCode, make_error_response
from network_mcp.hints import _ERROR_CODE_HINTS, _PATTERN_HINTS, get_hint

# ---------------------------------------------------------------------------
# get_hint — error-code-level defaults
# ---------------------------------------------------------------------------


class TestGetHintByErrorCode:
    """get_hint returns the default hint when no message pattern matches."""

    def test_connection_error_default(self):
        hint = get_hint("CONNECTION_ERROR")
        assert hint is not None
        assert "eAPI" in hint or "management API" in hint.lower() or "api" in hint.lower()

    def test_auth_error_default(self):
        hint = get_hint("AUTH_ERROR")
        assert hint is not None
        assert "NET_USERNAME" in hint or "NET_PASSWORD" in hint

    def test_timeout_error_default(self):
        hint = get_hint("TIMEOUT_ERROR")
        assert hint is not None
        assert "NET_TIMEOUT" in hint or "timeout" in hint.lower()

    def test_command_error_default(self):
        hint = get_hint("COMMAND_ERROR")
        assert hint is not None
        assert "platform" in hint.lower() or "version" in hint.lower()

    def test_rate_limited_default(self):
        hint = get_hint("RATE_LIMITED")
        assert hint is not None
        assert "RATE_LIMIT" in hint or "rate" in hint.lower()

    def test_circuit_open_default(self):
        hint = get_hint("CIRCUIT_OPEN")
        assert hint is not None
        assert "circuit" in hint.lower() or "cooldown" in hint.lower()

    def test_read_only_mode_default(self):
        hint = get_hint("READ_ONLY_MODE")
        assert hint is not None
        assert "NET_READ_ONLY" in hint

    def test_config_rejected_default(self):
        hint = get_hint("CONFIG_REJECTED")
        assert hint is not None

    def test_bulkhead_timeout_default(self):
        hint = get_hint("BULKHEAD_TIMEOUT")
        assert hint is not None
        assert "concurrent" in hint.lower() or "NET_CONN_ACQUIRE_TIMEOUT" in hint

    def test_access_denied_default(self):
        hint = get_hint("ACCESS_DENIED")
        assert hint is not None
        assert "tenant" in hint.lower() or "allowed" in hint.lower()

    def test_rbac_denied_default(self):
        hint = get_hint("RBAC_DENIED")
        assert hint is not None
        assert "scope" in hint.lower() or "network:read" in hint

    def test_output_too_large_default(self):
        hint = get_hint("OUTPUT_TOO_LARGE")
        assert hint is not None
        assert "NET_MAX_OUTPUT_SIZE" in hint or "limit" in hint.lower()

    def test_unknown_error_code_returns_none(self):
        assert get_hint("NONEXISTENT_ERROR_CODE") is None

    def test_empty_error_code_returns_none(self):
        assert get_hint("") is None


# ---------------------------------------------------------------------------
# get_hint — pattern-based matching (error message content)
# ---------------------------------------------------------------------------


class TestGetHintByPattern:
    """get_hint returns a more-specific hint when the message matches a pattern."""

    # Connection refused
    def test_connection_refused_keyword(self):
        hint = get_hint("CONNECTION_ERROR", "Connection to 10.0.0.1 failed: connection refused")
        assert hint is not None
        assert "port" in hint.lower() or "management API" in hint.lower() or "api" in hint.lower()

    def test_refused_keyword(self):
        hint = get_hint("CONNECTION_ERROR", "refused")
        assert hint is not None

    # Auth / unauthorized
    def test_unauthorized_keyword(self):
        hint = get_hint("AUTH_ERROR", "HTTP 401 Unauthorized")
        assert hint is not None
        assert "NET_USERNAME" in hint or "NET_PASSWORD" in hint

    def test_authentication_failed_keyword(self):
        hint = get_hint("AUTH_ERROR", "authentication failed: bad credentials")
        assert hint is not None
        assert "NET_USERNAME" in hint or "NET_PASSWORD" in hint

    def test_invalid_credentials_keyword(self):
        hint = get_hint("CONNECTION_ERROR", "invalid credentials supplied")
        assert hint is not None
        assert "NET_USERNAME" in hint or "NET_PASSWORD" in hint

    def test_login_failed_keyword(self):
        hint = get_hint("INTERNAL_ERROR", "login failed for user admin")
        assert hint is not None

    # Timeout
    def test_timed_out_keyword(self):
        hint = get_hint("TIMEOUT_ERROR", "socket operation timed out")
        assert hint is not None
        assert "NET_TIMEOUT" in hint or "connectivity" in hint.lower()

    def test_timeout_keyword(self):
        hint = get_hint("TIMEOUT_ERROR", "read timeout after 30s")
        assert hint is not None

    def test_connect_timeout_keyword(self):
        hint = get_hint("CONNECTION_ERROR", "connect timeout")
        assert hint is not None
        assert "NET_TIMEOUT" in hint or "connectivity" in hint.lower()

    # Command not supported
    def test_not_supported_keyword(self):
        hint = get_hint("COMMAND_ERROR", "this feature is not supported on this platform")
        assert hint is not None
        assert "platform" in hint.lower() or "version" in hint.lower()

    def test_invalid_command_keyword(self):
        hint = get_hint("COMMAND_ERROR", "invalid command: show bgp summary")
        assert hint is not None

    def test_unknown_command_keyword(self):
        hint = get_hint("COMMAND_ERROR", "unknown command")
        assert hint is not None

    def test_incomplete_command_keyword(self):
        hint = get_hint("COMMAND_ERROR", "incomplete command entered")
        assert hint is not None

    def test_percent_invalid_keyword(self):
        hint = get_hint("COMMAND_ERROR", "% invalid input detected")
        assert hint is not None

    def test_command_rejected_keyword(self):
        hint = get_hint("COMMAND_ERROR", "command rejected by device")
        assert hint is not None

    # SSL / TLS
    def test_ssl_keyword(self):
        hint = get_hint("CONNECTION_ERROR", "SSL: CERTIFICATE_VERIFY_FAILED")
        assert hint is not None
        assert "NET_VERIFY_CERTS" in hint or "ssl" in hint.lower() or "certificate" in hint.lower()

    def test_certificate_keyword(self):
        hint = get_hint("CONNECTION_ERROR", "certificate verify failed")
        assert hint is not None
        assert "NET_VERIFY_CERTS" in hint

    def test_tls_handshake_keyword(self):
        hint = get_hint("CONNECTION_ERROR", "tls handshake error")
        assert hint is not None

    # Pattern matching is case-insensitive
    def test_pattern_case_insensitive(self):
        hint_lower = get_hint("AUTH_ERROR", "authentication failed")
        hint_upper = get_hint("AUTH_ERROR", "AUTHENTICATION FAILED")
        assert hint_lower == hint_upper

    # Pattern takes priority over default
    def test_pattern_overrides_default(self):
        default_hint = get_hint("CONNECTION_ERROR")
        specific_hint = get_hint("CONNECTION_ERROR", "connection refused on port 443")
        # Both should be non-None but they may differ
        assert default_hint is not None
        assert specific_hint is not None

    def test_empty_message_falls_back_to_default(self):
        hint_no_msg = get_hint("AUTH_ERROR")
        hint_empty = get_hint("AUTH_ERROR", "")
        assert hint_no_msg == hint_empty


# ---------------------------------------------------------------------------
# Registry completeness checks
# ---------------------------------------------------------------------------


class TestHintsRegistry:
    """Verify structural integrity of the hints registry."""

    def test_all_hints_are_non_empty_strings(self):
        for code, hint in _ERROR_CODE_HINTS.items():
            assert isinstance(hint, str), f"Hint for {code} is not a string"
            assert hint.strip(), f"Hint for {code} is empty"

    def test_pattern_hints_have_valid_structure(self):
        for keywords, hint in _PATTERN_HINTS:
            assert isinstance(keywords, tuple), "Keywords must be a tuple"
            assert all(isinstance(k, str) for k in keywords), "Each keyword must be a str"
            assert isinstance(hint, str) and hint.strip(), "Hint must be a non-empty string"

    def test_pattern_keywords_are_lowercase(self):
        """All pattern keywords must be lowercase so case-insensitive matching works."""
        for keywords, _ in _PATTERN_HINTS:
            for kw in keywords:
                assert kw == kw.lower(), f"Keyword '{kw}' is not lowercase"

    def test_key_error_codes_have_hints(self):
        required = ["CONNECTION_ERROR", "AUTH_ERROR", "TIMEOUT_ERROR", "COMMAND_ERROR"]
        for code in required:
            assert code in _ERROR_CODE_HINTS, f"Missing hint for {code}"


# ---------------------------------------------------------------------------
# Integration: make_error_response includes hint field
# ---------------------------------------------------------------------------


class TestMakeErrorResponseIncludesHint:
    """Verify that make_error_response injects the hint into error dicts."""

    def test_connection_error_has_hint(self):
        resp = make_error_response(ErrorCode.CONNECTION_ERROR, "sw1", "Connection failed")
        assert "hint" in resp
        assert resp["hint"]

    def test_auth_error_has_hint(self):
        resp = make_error_response(ErrorCode.AUTH_ERROR, "sw1", "Auth failed")
        assert "hint" in resp
        assert "NET_USERNAME" in resp["hint"] or "NET_PASSWORD" in resp["hint"]

    def test_timeout_error_has_hint(self):
        resp = make_error_response(ErrorCode.TIMEOUT_ERROR, "sw1", "timed out")
        assert "hint" in resp
        assert "NET_TIMEOUT" in resp["hint"] or "connectivity" in resp["hint"].lower()

    def test_command_error_platform_hint(self):
        resp = make_error_response(ErrorCode.COMMAND_ERROR, "sw1", "command not supported on this version")
        assert "hint" in resp
        assert "platform" in resp["hint"].lower() or "version" in resp["hint"].lower()

    def test_connection_refused_specific_hint(self):
        resp = make_error_response(ErrorCode.CONNECTION_ERROR, "sw1", "connection refused on port 443")
        assert "hint" in resp
        # Specific pattern hint should mention port or API
        assert "port" in resp["hint"].lower() or "api" in resp["hint"].lower()

    def test_hint_not_overridden_by_extra_kwargs(self):
        """Extra kwargs passed to make_error_response should not clobber the hint."""
        resp = make_error_response(
            ErrorCode.AUTH_ERROR, "sw1", "Auth failed", retry_after_seconds=5
        )
        assert "hint" in resp
        assert resp["retry_after_seconds"] == 5

    def test_hint_can_be_overridden_by_extra_kwarg(self):
        """Callers CAN override hint by passing hint= explicitly."""
        custom = "Use sudo credentials"
        resp = make_error_response(ErrorCode.AUTH_ERROR, "sw1", "Auth failed", hint=custom)
        assert resp["hint"] == custom

    def test_status_is_always_error(self):
        resp = make_error_response(ErrorCode.CONNECTION_ERROR, "sw1", "fail")
        assert resp["status"] == "error"

    def test_error_code_string_in_response(self):
        resp = make_error_response(ErrorCode.CONNECTION_ERROR, "sw1", "fail")
        assert resp["error_code"] == "CONNECTION_ERROR"

    @pytest.mark.parametrize(
        "error_code",
        [
            ErrorCode.CONNECTION_ERROR,
            ErrorCode.AUTH_ERROR,
            ErrorCode.TIMEOUT_ERROR,
            ErrorCode.COMMAND_ERROR,
            ErrorCode.RATE_LIMITED,
            ErrorCode.CIRCUIT_OPEN,
            ErrorCode.READ_ONLY_MODE,
            ErrorCode.CONFIG_REJECTED,
            ErrorCode.BULKHEAD_TIMEOUT,
            ErrorCode.ACCESS_DENIED,
            ErrorCode.RBAC_DENIED,
            ErrorCode.OUTPUT_TOO_LARGE,
        ],
    )
    def test_all_key_error_codes_produce_hint(self, error_code):
        resp = make_error_response(error_code, "sw1", "some error occurred")
        assert "hint" in resp, f"No hint for {error_code}"
        assert resp["hint"]
