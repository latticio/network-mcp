"""Tests for narrowed exception handling in auth.py verify_token()."""

import time
from unittest.mock import patch

import jwt as pyjwt
import pytest

from network_mcp.auth import JWTTokenVerifier

SECRET_KEY = "test-secret-key-for-unit-tests"
ISSUER = "https://auth.example.com"
AUDIENCE = "network-mcp"


@pytest.fixture
def verifier():
    """Create a JWTTokenVerifier for testing."""
    return JWTTokenVerifier(
        secret_key=SECRET_KEY,
        issuer=ISSUER,
        audience=AUDIENCE,
        algorithms=["HS256"],
    )


def _make_token(
    secret: str = SECRET_KEY,
    expires_in: int = 3600,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    subject: str = "test-user",
    **extra,
) -> str:
    """Helper to create a signed JWT token."""
    payload = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "exp": int(time.time()) + expires_in,
        "iat": int(time.time()),
        "scopes": ["network:read"],
    }
    payload.update(extra)
    return pyjwt.encode(payload, secret, algorithm="HS256")


class TestExpiredTokenReturnsNone:
    """Expired JWT should return None, not raise."""

    def test_expired_token_returns_none(self, verifier):
        raw = _make_token(expires_in=-100)
        result = verifier.verify_token(raw)
        assert result is None


class TestInvalidSignatureReturnsNone:
    """Token signed with wrong key should return None."""

    def test_invalid_signature_returns_none(self, verifier):
        raw = _make_token(secret="wrong-secret-key")
        result = verifier.verify_token(raw)
        assert result is None


class TestMalformedTokenReturnsNone:
    """Completely invalid token string should return None."""

    def test_malformed_token_returns_none(self, verifier):
        result = verifier.verify_token("not-a-jwt")
        assert result is None

    def test_empty_string_returns_none(self, verifier):
        result = verifier.verify_token("")
        assert result is None

    def test_garbage_base64_returns_none(self, verifier):
        result = verifier.verify_token("eyJhbGciOi.invalid.payload")
        assert result is None


class TestInvalidIssuerReturnsNone:
    """Token with wrong issuer should return None."""

    def test_wrong_issuer_returns_none(self, verifier):
        raw = _make_token(issuer="https://evil.example.com")
        result = verifier.verify_token(raw)
        assert result is None


class TestInvalidAudienceReturnsNone:
    """Token with wrong audience should return None."""

    def test_wrong_audience_returns_none(self, verifier):
        raw = _make_token(audience="other-service")
        result = verifier.verify_token(raw)
        assert result is None


class TestProgrammingErrorsPropagateNotSwallowed:
    """Programming errors (AttributeError, TypeError, KeyError) must propagate."""

    def _patch_header_and_decode(self, verifier, error):
        """Patch both get_unverified_header (to return valid header) and decode (to raise)."""
        valid_header = {"alg": "HS256", "typ": "JWT"}
        with (
            patch("network_mcp.auth.pyjwt.get_unverified_header", return_value=valid_header),
            patch("network_mcp.auth.pyjwt.decode", side_effect=error),
        ):
            verifier.verify_token("some-token")

    def test_attribute_error_propagates(self, verifier):
        with pytest.raises(AttributeError, match="broken attribute"):
            self._patch_header_and_decode(verifier, AttributeError("broken attribute"))

    def test_type_error_propagates(self, verifier):
        with pytest.raises(TypeError, match="unexpected type"):
            self._patch_header_and_decode(verifier, TypeError("unexpected type"))

    def test_key_error_propagates(self, verifier):
        with pytest.raises(KeyError, match="missing key"):
            self._patch_header_and_decode(verifier, KeyError("missing key"))

    def test_runtime_error_propagates(self, verifier):
        with pytest.raises(RuntimeError, match="unexpected runtime issue"):
            self._patch_header_and_decode(verifier, RuntimeError("unexpected runtime issue"))
