"""Tests for OAuth 2.1 / JWT authentication module."""

import time

import jwt as pyjwt
import pytest

from network_mcp.auth import (
    AccessToken,
    JWTTokenVerifier,
    create_token_verifier,
)

# --- Test fixtures ---

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
    scopes: list[str] | None = None,
    expires_in: int = 3600,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    subject: str = "test-user",
    **extra,
) -> str:
    """Helper to create a signed JWT token for testing."""
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


# --- AccessToken tests ---


class TestAccessToken:
    """Tests for AccessToken dataclass."""

    def test_has_scope(self):
        token = AccessToken(token="x", client_id="user1", scopes=["network:read", "network:write"])
        assert token.has_scope("network:read") is True
        assert token.has_scope("network:admin") is False

    def test_is_expired_false(self):
        token = AccessToken(token="x", client_id="user1", scopes=[], expires_at=int(time.time()) + 3600)
        assert token.is_expired is False

    def test_is_expired_true(self):
        token = AccessToken(token="x", client_id="user1", scopes=[], expires_at=int(time.time()) - 100)
        assert token.is_expired is True

    def test_is_expired_none(self):
        token = AccessToken(token="x", client_id="user1", scopes=[], expires_at=None)
        assert token.is_expired is False


# --- JWTTokenVerifier tests ---


class TestJWTTokenVerifier:
    """Tests for JWT token verification."""

    def test_valid_token_with_scopes_list(self, verifier):
        """Valid token with scopes as a list."""
        raw = _make_token(scopes=["network:read", "network:write"])
        result = verifier.verify_token(raw)

        assert result is not None
        assert result.client_id == "test-user"
        assert "network:read" in result.scopes
        assert "network:write" in result.scopes
        assert result.is_expired is False

    def test_valid_token_with_scope_string(self, verifier):
        """Valid token with scope as a space-separated string (OAuth 2.0 convention)."""
        raw = _make_token(scope="network:read network:admin")
        result = verifier.verify_token(raw)

        assert result is not None
        assert "network:read" in result.scopes
        assert "network:admin" in result.scopes

    def test_valid_token_with_client_id(self, verifier):
        """Token with explicit client_id field."""
        raw = _make_token(client_id="my-app-client")
        result = verifier.verify_token(raw)

        assert result is not None
        assert result.client_id == "my-app-client"

    def test_expired_token_rejected(self, verifier):
        """Expired token returns None."""
        raw = _make_token(expires_in=-100)
        result = verifier.verify_token(raw)
        assert result is None

    def test_wrong_issuer_rejected(self, verifier):
        """Token from wrong issuer returns None."""
        raw = _make_token(issuer="https://evil.example.com")
        result = verifier.verify_token(raw)
        assert result is None

    def test_wrong_audience_rejected(self, verifier):
        """Token for wrong audience returns None."""
        raw = _make_token(audience="some-other-service")
        result = verifier.verify_token(raw)
        assert result is None

    def test_wrong_secret_rejected(self, verifier):
        """Token signed with different key returns None."""
        payload = {
            "sub": "test-user",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": int(time.time()) + 3600,
        }
        raw = pyjwt.encode(payload, "wrong-secret-key", algorithm="HS256")
        result = verifier.verify_token(raw)
        assert result is None

    def test_malformed_token_rejected(self, verifier):
        """Completely invalid token string returns None."""
        result = verifier.verify_token("not-a-jwt-token")
        assert result is None

    def test_empty_token_rejected(self, verifier):
        """Empty token string returns None."""
        result = verifier.verify_token("")
        assert result is None

    def test_no_scopes_returns_empty_list(self, verifier):
        """Token with no scope fields returns empty scopes list."""
        raw = _make_token()
        result = verifier.verify_token(raw)
        assert result is not None
        assert result.scopes == []


# --- create_token_verifier tests ---


class TestCreateTokenVerifier:
    """Tests for the verifier factory function."""

    def test_creates_verifier(self):
        verifier = create_token_verifier(
            secret_key="my-secret",
            issuer_url="https://auth.example.com",
            audience="my-app",
        )
        assert isinstance(verifier, JWTTokenVerifier)
        assert verifier.secret_key == "my-secret"
        assert verifier.issuer == "https://auth.example.com"
        assert verifier.audience == "my-app"

    def test_defaults(self):
        verifier = create_token_verifier(secret_key="secret")
        assert verifier.issuer is None
        assert verifier.audience == "network-mcp"


# --- Auth disabled by default tests ---


class TestAuthDisabledByDefault:
    """Tests that auth is off unless explicitly enabled."""

    def test_settings_auth_disabled_default(self):
        """AUTH_ENABLED defaults to False."""
        from network_mcp.config import NetworkSettings

        s = NetworkSettings()
        assert s.auth_enabled is False

    def test_settings_auth_fields_optional(self):
        """Auth fields are optional (None by default)."""
        from network_mcp.config import NetworkSettings

        s = NetworkSettings()
        assert s.auth_issuer_url is None
        assert s.auth_secret_key is None
        assert s.auth_required_scopes == "network:read"
