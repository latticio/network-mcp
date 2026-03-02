"""Tests for OWASP MCP Top 10 compliance and auth hardening.

Covers:
- JWKS auto-discovery and key rotation
- Algorithm enforcement
- Audience validation
- M2M (client_credentials) token verification
- Expired token rejection
- Token redaction from tool outputs (MCP01)
- Plugin integrity verification (MCP03)
- RESTCONF path traversal prevention (MCP05)
- NETCONF XML injection prevention (MCP05)
- NX-API JSON injection prevention (MCP05)
- Plugin state isolation (MCP06)
- Tenant context isolation (MCP10)
"""

import hashlib
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest

from network_mcp.auth import (
    ALLOWED_ALGORITHMS,
    JWT_PATTERN,
    AccessToken,
    JWKSKeyManager,
    JWTTokenVerifier,
    create_token_verifier,
    redact_tokens_from_output,
)
from network_mcp.plugins import (
    PluginSandbox,
    _reset_plugin_sandboxes,
    get_plugin_sandbox,
    set_plugin_hash_allowlist,
    verify_plugin_integrity,
)
from network_mcp.rbac import (
    TenantDeviceFilter,
    check_authorization,
    extract_tenant_id,
)
from network_mcp.sanitizer import (
    validate_netconf_payload,
    validate_nxapi_payload,
    validate_restconf_path,
)

# --- Test constants ---

SECRET_KEY = "test-secret-key-for-owasp-hardening-tests"
ISSUER = "https://auth.example.com"
AUDIENCE = "network-mcp"


def _make_hs256_token(
    scopes: list[str] | None = None,
    expires_in: int = 3600,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    subject: str = "test-user",
    **extra,
) -> str:
    """Create a signed HS256 JWT for testing."""
    payload = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "exp": int(time.time()) + expires_in,
        "iat": int(time.time()),
    }
    if scopes is not None:
        payload["scopes"] = scopes
    payload.update(extra)
    return pyjwt.encode(payload, SECRET_KEY, algorithm="HS256")


# =========================================================================
# JWKS Auto-Discovery Tests
# =========================================================================


class TestJWKSKeyManager:
    """Tests for JWKS key fetching, caching, and rotation."""

    def test_init(self):
        mgr = JWKSKeyManager("https://idp.example.com/.well-known/jwks.json")
        assert mgr.jwks_url == "https://idp.example.com/.well-known/jwks.json"
        assert mgr._keys == {}

    def test_fetch_jwks_with_rsa_keys(self):
        """Test fetching JWKS with RSA keys from mock HTTP client."""
        jwks_response = {
            "keys": [
                {"kid": "key-1", "kty": "RSA", "n": "abc123", "e": "AQAB"},
                {"kid": "key-2", "kty": "EC", "crv": "P-256", "x": "x1", "y": "y1"},
            ]
        }
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = jwks_response
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        mgr = JWKSKeyManager("https://idp.example.com/.well-known/jwks.json")
        keys = mgr.fetch_jwks(http_client=mock_client)

        assert "key-1" in keys
        assert "key-2" in keys
        assert keys["key-1"]["kty"] == "RSA"
        assert keys["key-2"]["kty"] == "EC"

    def test_fetch_jwks_ignores_unsupported_key_types(self):
        """Keys with kty != RSA/EC should be ignored."""
        jwks_response = {
            "keys": [
                {"kid": "oct-key", "kty": "oct", "k": "secret"},
                {"kid": "rsa-key", "kty": "RSA", "n": "abc", "e": "AQAB"},
            ]
        }
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = jwks_response
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        mgr = JWKSKeyManager("https://idp.example.com/.well-known/jwks.json")
        keys = mgr.fetch_jwks(http_client=mock_client)

        assert "oct-key" not in keys
        assert "rsa-key" in keys

    def test_fetch_jwks_ignores_keys_without_kid(self):
        """Keys without 'kid' should be ignored."""
        jwks_response = {
            "keys": [
                {"kty": "RSA", "n": "abc", "e": "AQAB"},  # no kid
                {"kid": "good-key", "kty": "RSA", "n": "def", "e": "AQAB"},
            ]
        }
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = jwks_response
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        mgr = JWKSKeyManager("https://idp.example.com/.well-known/jwks.json")
        keys = mgr.fetch_jwks(http_client=mock_client)

        assert len(keys) == 1
        assert "good-key" in keys

    def test_cache_ttl_prevents_refetch(self):
        """Second call within TTL should use cache, not re-fetch."""
        jwks_response = {"keys": [{"kid": "k1", "kty": "RSA", "n": "a", "e": "AQAB"}]}
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = jwks_response
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        mgr = JWKSKeyManager("https://idp.example.com/jwks", refresh_interval=3600)
        mgr.fetch_jwks(http_client=mock_client)
        mgr.fetch_jwks(http_client=mock_client)

        # Only one HTTP call should have been made
        assert mock_client.get.call_count == 1

    def test_get_signing_key_found(self):
        """Look up key by kid."""
        mgr = JWKSKeyManager("https://idp.example.com/jwks")
        mgr._keys = {"k1": {"kid": "k1", "kty": "RSA"}, "k2": {"kid": "k2", "kty": "EC"}}
        mgr._last_refresh = time.time()

        result = mgr.get_signing_key("k1")
        assert result is not None
        assert result["kid"] == "k1"

    def test_get_signing_key_triggers_refresh_on_miss(self):
        """Missing kid should trigger a JWKS refresh."""
        jwks_response = {"keys": [{"kid": "new-key", "kty": "RSA", "n": "a", "e": "AQAB"}]}
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = jwks_response
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        mgr = JWKSKeyManager("https://idp.example.com/jwks")
        mgr._keys = {"old-key": {"kid": "old-key", "kty": "RSA"}}
        mgr._last_refresh = time.time()

        result = mgr.get_signing_key("new-key", http_client=mock_client)
        assert result is not None
        assert result["kid"] == "new-key"

    def test_clear_cache(self):
        mgr = JWKSKeyManager("https://idp.example.com/jwks")
        mgr._keys = {"k1": {"kid": "k1"}}
        mgr._last_refresh = time.time()

        mgr.clear_cache()
        assert mgr._keys == {}
        assert mgr._last_refresh == 0.0

    def test_fetch_jwks_handles_http_error(self):
        """HTTP failures should be handled gracefully."""
        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("Connection refused")

        mgr = JWKSKeyManager("https://idp.example.com/jwks")
        keys = mgr.fetch_jwks(http_client=mock_client)
        assert keys == {}

    def test_fetch_jwks_empty_keys_list(self):
        """Empty keys list should result in empty cache."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"keys": []}
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        mgr = JWKSKeyManager("https://idp.example.com/jwks")
        keys = mgr.fetch_jwks(http_client=mock_client)
        assert keys == {}


# =========================================================================
# Key Rotation Support Tests
# =========================================================================


class TestKeyRotation:
    """Test key rotation scenarios with JWTTokenVerifier."""

    def test_static_key_verification(self):
        """Basic verification with static HS256 key."""
        verifier = JWTTokenVerifier(secret_key=SECRET_KEY, issuer=ISSUER, audience=AUDIENCE, algorithms=["HS256"])
        token = _make_hs256_token(scopes=["network:read"])
        result = verifier.verify_token(token)
        assert result is not None
        assert result.scopes == ["network:read"]

    def test_jwks_fallback_to_static_key(self):
        """When JWKS is configured but token has no kid, falls back to static key."""
        verifier = JWTTokenVerifier(
            secret_key=SECRET_KEY,
            issuer=ISSUER,
            audience=AUDIENCE,
            algorithms=["HS256"],
            jwks_url="https://idp.example.com/jwks",
        )
        token = _make_hs256_token(scopes=["network:read"])
        result = verifier.verify_token(token)
        assert result is not None
        assert result.scopes == ["network:read"]


# =========================================================================
# Algorithm Enforcement Tests
# =========================================================================


class TestAlgorithmEnforcement:
    """Test algorithm validation and enforcement."""

    def test_allowed_algorithms_set(self):
        """Verify the ALLOWED_ALGORITHMS constant."""
        assert "RS256" in ALLOWED_ALGORITHMS
        assert "ES256" in ALLOWED_ALGORITHMS
        assert "HS256" in ALLOWED_ALGORITHMS
        assert "none" not in ALLOWED_ALGORITHMS

    def test_reject_unsupported_algorithm(self):
        """Constructor should reject unsupported algorithms."""
        with pytest.raises(ValueError, match="Unsupported algorithm"):
            JWTTokenVerifier(secret_key="key", algorithms=["none"])

    def test_reject_none_algorithm(self):
        """The 'none' algorithm must always be rejected."""
        with pytest.raises(ValueError, match="Unsupported algorithm"):
            JWTTokenVerifier(secret_key="key", algorithms=["none"])

    def test_token_algorithm_mismatch_rejected(self):
        """Token signed with HS256 should be rejected when only RS256 is allowed."""
        verifier = JWTTokenVerifier(secret_key=SECRET_KEY, algorithms=["RS256"])
        token = _make_hs256_token(scopes=["network:read"])
        result = verifier.verify_token(token)
        assert result is None

    def test_algorithm_confusion_warning(self):
        """Mixing symmetric and asymmetric algorithms should log a warning."""
        import logging

        with patch.object(logging.getLogger("network-mcp.auth"), "warning") as mock_warn:
            JWTTokenVerifier(secret_key="key", algorithms=["HS256", "RS256"])
            assert mock_warn.called
            assert "algorithm confusion" in mock_warn.call_args[0][0].lower()


# =========================================================================
# Audience Validation Tests
# =========================================================================


class TestAudienceValidation:
    """Test JWT audience claim enforcement."""

    def test_correct_audience_accepted(self):
        verifier = JWTTokenVerifier(secret_key=SECRET_KEY, audience=AUDIENCE, algorithms=["HS256"])
        token = _make_hs256_token(audience=AUDIENCE)
        result = verifier.verify_token(token)
        assert result is not None

    def test_wrong_audience_rejected(self):
        verifier = JWTTokenVerifier(secret_key=SECRET_KEY, audience="other-service", algorithms=["HS256"])
        token = _make_hs256_token(audience=AUDIENCE)
        result = verifier.verify_token(token)
        assert result is None

    def test_no_audience_in_token_rejected(self):
        """Token without 'aud' claim should be rejected when audience is configured."""
        verifier = JWTTokenVerifier(secret_key=SECRET_KEY, audience=AUDIENCE, algorithms=["HS256"])
        payload = {
            "sub": "test-user",
            "iss": ISSUER,
            "exp": int(time.time()) + 3600,
        }
        token = pyjwt.encode(payload, SECRET_KEY, algorithm="HS256")
        result = verifier.verify_token(token)
        assert result is None


# =========================================================================
# M2M Token Verification Tests (MCP07)
# =========================================================================


class TestM2MTokenVerification:
    """Test machine-to-machine (client_credentials) token support."""

    def test_m2m_token_with_gty_claim(self):
        """M2M tokens with 'gty': 'client-credentials' should be accepted."""
        verifier = JWTTokenVerifier(secret_key=SECRET_KEY, issuer=ISSUER, audience=AUDIENCE, algorithms=["HS256"])
        token = _make_hs256_token(
            scopes=["network:admin"],
            subject="machine-client",
            gty="client-credentials",
            client_id="my-service",
        )
        result = verifier.verify_m2m_token(token)
        assert result is not None
        assert result.is_m2m is True
        assert result.grant_type == "client-credentials"
        assert result.client_id == "my-service"

    def test_user_token_rejected_as_m2m(self):
        """Normal user tokens (no gty claim) should be rejected by verify_m2m_token."""
        verifier = JWTTokenVerifier(secret_key=SECRET_KEY, issuer=ISSUER, audience=AUDIENCE, algorithms=["HS256"])
        token = _make_hs256_token(scopes=["network:read"])
        result = verifier.verify_m2m_token(token)
        assert result is None

    def test_m2m_token_works_with_regular_verify(self):
        """M2M tokens should also work with regular verify_token."""
        verifier = JWTTokenVerifier(secret_key=SECRET_KEY, issuer=ISSUER, audience=AUDIENCE, algorithms=["HS256"])
        token = _make_hs256_token(
            scopes=["network:admin"],
            gty="client-credentials",
            client_id="automation-svc",
        )
        result = verifier.verify_token(token)
        assert result is not None
        assert result.grant_type == "client-credentials"

    def test_is_m2m_property(self):
        """Test the is_m2m property on AccessToken."""
        m2m_token = AccessToken(token="x", client_id="svc", scopes=["network:admin"], grant_type="client-credentials")
        assert m2m_token.is_m2m is True

        user_token = AccessToken(token="x", client_id="user", scopes=["network:read"])
        assert user_token.is_m2m is False


# =========================================================================
# Expired Token Rejection Tests
# =========================================================================


class TestExpiredTokenRejection:
    """Test that expired tokens are properly rejected."""

    def test_expired_token_rejected(self):
        verifier = JWTTokenVerifier(secret_key=SECRET_KEY, issuer=ISSUER, audience=AUDIENCE, algorithms=["HS256"])
        token = _make_hs256_token(scopes=["network:read"], expires_in=-100)
        result = verifier.verify_token(token)
        assert result is None

    def test_token_expired_at_boundary(self):
        """Token that just expired should be rejected."""
        verifier = JWTTokenVerifier(secret_key=SECRET_KEY, issuer=ISSUER, audience=AUDIENCE, algorithms=["HS256"])
        token = _make_hs256_token(scopes=["network:read"], expires_in=-1)
        result = verifier.verify_token(token)
        assert result is None


# =========================================================================
# Token Redaction (MCP01 — Token Mismanagement)
# =========================================================================


class TestTokenRedaction:
    """Test that JWT tokens are redacted from tool outputs."""

    def test_jwt_pattern_matches_real_tokens(self):
        """JWT_PATTERN should match actual JWT token strings."""
        token = _make_hs256_token(scopes=["network:read"])
        assert JWT_PATTERN.search(token) is not None

    def test_redact_tokens_from_string(self):
        """JWT tokens in string values should be redacted."""
        token = _make_hs256_token(scopes=["network:read"])
        data = {"status": "success", "debug": f"Token was: {token}"}
        result = redact_tokens_from_output(data)
        assert token not in str(result)
        assert "[JWT_REDACTED]" in result["debug"]

    def test_redact_tokens_nested_dict(self):
        """JWT tokens nested in dicts should be redacted."""
        token = _make_hs256_token(scopes=["network:read"])
        data = {"data": {"inner": {"token_field": token}}}
        result = redact_tokens_from_output(data)
        assert token not in json.dumps(result)

    def test_redact_tokens_in_lists(self):
        """JWT tokens in list items should be redacted."""
        token = _make_hs256_token(scopes=["network:read"])
        data = {"items": [f"bearer {token}", "normal-value"]}
        result = redact_tokens_from_output(data)
        assert token not in json.dumps(result)

    def test_no_redaction_for_normal_output(self):
        """Normal output without JWT tokens should pass through unchanged."""
        data = {"status": "success", "device": "spine-01", "data": {"version": "4.28.0F"}}
        result = redact_tokens_from_output(data)
        assert result == data

    def test_redact_preserves_non_string_values(self):
        """Non-string values (ints, bools, None) should pass through."""
        data = {"count": 42, "active": True, "value": None}
        result = redact_tokens_from_output(data)
        assert result == data


# =========================================================================
# Plugin Integrity Verification (MCP03 — Tool Poisoning)
# =========================================================================


class TestPluginIntegrity:
    """Test plugin hash verification."""

    def setup_method(self):
        """Reset allowlist before each test."""
        set_plugin_hash_allowlist({})

    def test_no_allowlist_passes_all(self):
        """When no allowlist is configured, all plugins pass."""
        ep = MagicMock()
        ep.dist.name = "some-plugin"
        ok, msg = verify_plugin_integrity(ep)
        assert ok is True
        assert "skipped" in msg.lower()

    def test_plugin_not_in_allowlist_rejected(self):
        """Plugins not in the hash allowlist should be rejected."""
        set_plugin_hash_allowlist({"allowed-plugin": "abc123"})
        ep = MagicMock()
        ep.dist.name = "unauthorized-plugin"
        ok, msg = verify_plugin_integrity(ep)
        assert ok is False
        assert "not in hash allowlist" in msg

    def test_plugin_hash_mismatch_rejected(self):
        """Plugin with wrong hash should be rejected."""
        # Use the network_mcp.plugins module file itself as the "plugin"
        from network_mcp import plugins as plugin_mod

        set_plugin_hash_allowlist({"test-plugin": "wrong-hash-value-0000"})

        mock_module = MagicMock()
        mock_module.__file__ = plugin_mod.__file__

        ep = MagicMock()
        ep.dist.name = "test-plugin"
        ep.value = "fake_plugin_module:SomeClass"

        # Patch __import__ only for the target module name
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def selective_import(name, *args, **kwargs):
            if name == "fake_plugin_module":
                return mock_module
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=selective_import):
            ok, msg = verify_plugin_integrity(ep)
        assert ok is False
        assert "Hash mismatch" in msg

    def test_plugin_hash_match_passes(self):
        """Plugin with correct hash should pass verification."""
        from network_mcp import plugins as plugin_mod

        actual_hash = hashlib.sha256(Path(plugin_mod.__file__).read_bytes()).hexdigest()
        set_plugin_hash_allowlist({"test-plugin": actual_hash})

        mock_module = MagicMock()
        mock_module.__file__ = plugin_mod.__file__

        ep = MagicMock()
        ep.dist.name = "test-plugin"
        ep.value = "fake_plugin_module2:SomeClass"

        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def selective_import(name, *args, **kwargs):
            if name == "fake_plugin_module2":
                return mock_module
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=selective_import):
            ok, msg = verify_plugin_integrity(ep)
        assert ok is True
        assert "verified" in msg.lower()


# =========================================================================
# RESTCONF Path Traversal Prevention (MCP05)
# =========================================================================


class TestRESTCONFPathTraversal:
    """Test RESTCONF URL path validation."""

    def test_normal_path_accepted(self):
        """Valid RESTCONF paths should pass."""
        assert validate_restconf_path("/restconf/data/openconfig-interfaces:interfaces") is None

    def test_path_traversal_dotdot_rejected(self):
        """../  path traversal should be rejected."""
        result = validate_restconf_path("/restconf/data/../../etc/passwd")
        assert result is not None
        assert "path traversal" in result.lower()

    def test_path_traversal_trailing_dotdot_rejected(self):
        """Trailing .. should be rejected."""
        result = validate_restconf_path("/restconf/data/..")
        assert result is not None

    def test_path_traversal_url_encoded_rejected(self):
        """%2e%2e encoded traversal should be rejected."""
        result = validate_restconf_path("/restconf/data/%2e%2e/etc/passwd")
        assert result is not None

    def test_path_traversal_mixed_encoding_rejected(self):
        """%2e./ mixed encoding should be rejected."""
        result = validate_restconf_path("/restconf/data/%2e./secret")
        assert result is not None

    def test_path_traversal_null_byte_rejected(self):
        """Null bytes in paths should be rejected."""
        result = validate_restconf_path("/restconf/data/interfaces%00.json")
        assert result is not None

    def test_empty_path_rejected(self):
        result = validate_restconf_path("")
        assert result is not None
        assert "empty" in result.lower()

    def test_path_with_dots_in_names_accepted(self):
        """Paths with dots in legitimate names should pass (e.g., IP addresses)."""
        assert validate_restconf_path("/restconf/data/ietf-routing:routing/10.0.0.1") is None

    def test_encoded_slash_rejected(self):
        """.%2f should be rejected."""
        result = validate_restconf_path("/restconf/data/.%2fsecret")
        assert result is not None


# =========================================================================
# NETCONF XML Injection Prevention (MCP05)
# =========================================================================


class TestNETCONFXMLInjection:
    """Test NETCONF XML payload validation."""

    def test_normal_xml_accepted(self):
        """Valid NETCONF XML should pass."""
        xml = "<get-config><source><running/></source></get-config>"
        assert validate_netconf_payload(xml) is None

    def test_framing_delimiter_rejected(self):
        """NETCONF framing delimiter ]]> should be rejected."""
        xml = "<rpc>]]></rpc>"
        result = validate_netconf_payload(xml)
        assert result is not None
        assert "forbidden" in result.lower()

    def test_entity_injection_rejected(self):
        """<!ENTITY declarations should be rejected (XXE)."""
        xml = '<!ENTITY xxe SYSTEM "file:///etc/passwd"><rpc>&xxe;</rpc>'
        result = validate_netconf_payload(xml)
        assert result is not None

    def test_doctype_injection_rejected(self):
        """<!DOCTYPE declarations should be rejected (XXE)."""
        xml = '<!DOCTYPE foo [<!ENTITY xxe "bar">]><rpc>&xxe;</rpc>'
        result = validate_netconf_payload(xml)
        assert result is not None

    def test_safe_xml_entities_accepted(self):
        """Common safe entities (&amp; &lt; &gt; &quot; &apos;) should be allowed."""
        xml = "<config><description>A &amp; B &lt; C</description></config>"
        assert validate_netconf_payload(xml) is None

    def test_empty_payload_accepted(self):
        """Empty payloads should pass."""
        assert validate_netconf_payload("") is None

    def test_none_payload_accepted(self):
        """None payloads should pass."""
        assert validate_netconf_payload(None) is None

    def test_element_declaration_rejected(self):
        """<!ELEMENT declarations should be rejected."""
        xml = "<!ELEMENT foo (#PCDATA)><rpc></rpc>"
        result = validate_netconf_payload(xml)
        assert result is not None

    def test_attlist_declaration_rejected(self):
        """<!ATTLIST declarations should be rejected."""
        xml = "<!ATTLIST foo bar CDATA #IMPLIED><rpc></rpc>"
        result = validate_netconf_payload(xml)
        assert result is not None

    def test_unknown_entity_reference_rejected(self):
        """Unknown entity references should be rejected."""
        xml = "<config>&custom_entity;</config>"
        result = validate_netconf_payload(xml)
        assert result is not None


# =========================================================================
# NX-API JSON Injection Prevention (MCP05)
# =========================================================================


class TestNXAPIJSONInjection:
    """Test NX-API JSON payload validation."""

    def test_normal_payload_accepted(self):
        """Normal string values should pass."""
        assert validate_nxapi_payload("show version") is None
        assert validate_nxapi_payload("interface Ethernet1/1") is None

    def test_null_byte_rejected(self):
        """Null bytes should be rejected."""
        result = validate_nxapi_payload("show\x00version")
        assert result is not None
        assert "null byte" in result.lower()

    def test_unicode_null_escape_rejected(self):
        """Unicode null escape should be rejected."""
        result = validate_nxapi_payload("show\\u0000version")
        assert result is not None
        assert "null byte" in result.lower()

    def test_control_characters_rejected(self):
        """Control characters (except tab/newline/cr) should be rejected."""
        result = validate_nxapi_payload("show\x01version")
        assert result is not None
        assert "control character" in result.lower()

    def test_tabs_and_newlines_accepted(self):
        """Tabs, newlines, and carriage returns should be allowed."""
        assert validate_nxapi_payload("show\tversion") is None
        assert validate_nxapi_payload("line1\nline2") is None
        assert validate_nxapi_payload("line1\r\nline2") is None

    def test_empty_payload_accepted(self):
        assert validate_nxapi_payload("") is None

    def test_none_payload_accepted(self):
        assert validate_nxapi_payload(None) is None


# =========================================================================
# Plugin State Isolation (MCP06 — Cross-Plugin State Leakage)
# =========================================================================


class TestPluginSandbox:
    """Test plugin state isolation."""

    def setup_method(self):
        _reset_plugin_sandboxes()

    def test_sandbox_basic_operations(self):
        """Test get/set/delete operations."""
        sandbox = PluginSandbox("test-plugin")
        sandbox.set("key1", "value1")
        assert sandbox.get("key1") == "value1"
        assert sandbox.get("missing", "default") == "default"

        sandbox.delete("key1")
        assert sandbox.get("key1") is None

    def test_sandbox_namespace(self):
        """Each sandbox should have a unique namespace."""
        s1 = PluginSandbox("plugin-a")
        s2 = PluginSandbox("plugin-b")
        assert s1.namespace == "plugin-a"
        assert s2.namespace == "plugin-b"

    def test_sandbox_isolation(self):
        """State set in one sandbox should not be visible in another."""
        s1 = get_plugin_sandbox("plugin-a")
        s2 = get_plugin_sandbox("plugin-b")

        s1.set("shared_key", "plugin-a-value")
        s2.set("shared_key", "plugin-b-value")

        assert s1.get("shared_key") == "plugin-a-value"
        assert s2.get("shared_key") == "plugin-b-value"

    def test_sandbox_clear(self):
        sandbox = PluginSandbox("test")
        sandbox.set("a", 1)
        sandbox.set("b", 2)
        sandbox.clear()
        assert sandbox.keys() == []

    def test_sandbox_keys(self):
        sandbox = PluginSandbox("test")
        sandbox.set("x", 1)
        sandbox.set("y", 2)
        assert sorted(sandbox.keys()) == ["x", "y"]

    def test_get_plugin_sandbox_returns_same_instance(self):
        """Same plugin name should always return the same sandbox."""
        s1 = get_plugin_sandbox("my-plugin")
        s2 = get_plugin_sandbox("my-plugin")
        assert s1 is s2


# =========================================================================
# Tenant Context Isolation (MCP10 — Context Over-Sharing)
# =========================================================================


class TestTenantIsolation:
    """Test tenant-based device access filtering."""

    def test_tenant_filter_restricts_device_access(self):
        """Devices with tenant lists should be restricted."""
        inventory = {
            "spine-01": {"tenants": ["acme-corp"]},
            "leaf-01": {"tenants": ["acme-corp", "beta-inc"]},
            "shared-01": {"tenants": []},
        }
        filt = TenantDeviceFilter(inventory)

        assert filt.is_allowed("spine-01", "acme-corp") is True
        assert filt.is_allowed("spine-01", "evil-corp") is False
        assert filt.is_allowed("leaf-01", "beta-inc") is True
        assert filt.is_allowed("shared-01", "anyone") is True

    def test_no_tenant_means_no_restriction(self):
        """When tenant_id is None, all devices are accessible (backward compat)."""
        inventory = {"spine-01": {"tenants": ["acme-corp"]}}
        filt = TenantDeviceFilter(inventory)
        assert filt.is_allowed("spine-01", None) is True

    def test_unknown_device_accessible_to_all(self):
        """Devices not in inventory should be accessible to all."""
        inventory = {"spine-01": {"tenants": ["acme-corp"]}}
        filt = TenantDeviceFilter(inventory)
        assert filt.is_allowed("unknown-device", "any-tenant") is True

    def test_extract_tenant_from_explicit_claim(self):
        """Explicit tenant_id claim should be extracted."""
        claims = {"tenant_id": "acme-corp", "sub": "user@example.com"}
        assert extract_tenant_id(claims) == "acme-corp"

    def test_extract_tenant_from_org_id(self):
        """Auth0 org_id claim should be extracted."""
        claims = {"org_id": "org_123abc", "sub": "user@example.com"}
        assert extract_tenant_id(claims) == "org_123abc"

    def test_extract_tenant_from_email_domain(self):
        """Email domain from sub claim should be extracted as tenant."""
        claims = {"sub": "user@acme.com"}
        assert extract_tenant_id(claims) == "acme.com"

    def test_no_tenant_info_returns_none(self):
        """Missing tenant info should return None."""
        claims = {"sub": "no-email-user"}
        assert extract_tenant_id(claims) is None


# =========================================================================
# create_token_verifier Factory Tests
# =========================================================================


class TestCreateTokenVerifier:
    """Test the create_token_verifier factory function."""

    def test_default_factory(self):
        verifier = create_token_verifier(secret_key=SECRET_KEY)
        token = _make_hs256_token(scopes=["network:read"])
        result = verifier.verify_token(token)
        assert result is not None

    def test_factory_with_jwks_url(self):
        verifier = create_token_verifier(
            secret_key=SECRET_KEY,
            jwks_url="https://idp.example.com/jwks",
            jwks_refresh_interval=1800,
        )
        assert verifier._jwks_manager is not None
        assert verifier._jwks_manager._refresh_interval == 1800

    def test_factory_with_algorithms(self):
        verifier = create_token_verifier(secret_key=SECRET_KEY, algorithms=["HS256"])
        assert verifier.algorithms == ["HS256"]


# =========================================================================
# Integration-style: RBAC + Auth combined
# =========================================================================


class TestAuthRBACIntegration:
    """Test that auth tokens and RBAC scopes work together correctly."""

    def test_read_scope_allows_read_tools(self):
        claims = {"scope": "network:read"}
        ok, _ = check_authorization("eos_get_vlans", claims)
        assert ok is True

    def test_read_scope_blocks_write_tools(self):
        claims = {"scope": "network:read"}
        ok, _ = check_authorization("eos_create_vlan", claims)
        assert ok is False

    def test_admin_scope_allows_delete_tools(self):
        claims = {"scope": "network:admin"}
        ok, _ = check_authorization("eos_delete_checkpoint", claims)
        assert ok is True

    def test_m2m_token_scopes_work_with_rbac(self):
        """M2M tokens with proper scopes should pass RBAC checks."""
        claims = {"scope": "network:admin", "gty": "client-credentials", "client_id": "svc"}
        ok, _ = check_authorization("eos_delete_checkpoint", claims)
        assert ok is True
