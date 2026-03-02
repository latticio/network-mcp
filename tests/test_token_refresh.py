"""Tests for JWT token refresh and JTI-based revocation.

Covers:
- TokenRevocationStore: add, check, cleanup, thread-safety
- TokenRefreshManager: issue, refresh, revoke, cleanup
- Revocation integration with JWTTokenVerifier
"""

import threading
import time

import jwt as pyjwt
import pytest

from network_mcp.auth import (
    AccessToken,
    JWTTokenVerifier,
    TokenRefreshManager,
    TokenRevocationStore,
)

# --- Test constants ---

SECRET_KEY = "test-secret-key-for-refresh-tests"
ISSUER = "https://auth.example.com"
AUDIENCE = "network-mcp"


def _make_token(
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
# TokenRevocationStore Tests
# =========================================================================


class TestTokenRevocationStore:
    """Tests for the in-memory JTI revocation store."""

    def test_revoke_and_check(self):
        """Revoked JTI should be detected."""
        store = TokenRevocationStore()
        store.revoke("jti-001", int(time.time()) + 3600)
        assert store.is_revoked("jti-001") is True

    def test_not_revoked(self):
        """Non-revoked JTI should not be detected."""
        store = TokenRevocationStore()
        assert store.is_revoked("jti-unknown") is False

    def test_cleanup_removes_expired(self):
        """Cleanup should remove entries whose exp is in the past."""
        store = TokenRevocationStore()
        store.revoke("expired-jti", int(time.time()) - 100)
        store.revoke("valid-jti", int(time.time()) + 3600)

        removed = store.cleanup()
        assert removed == 1
        assert store.is_revoked("expired-jti") is False
        assert store.is_revoked("valid-jti") is True

    def test_cleanup_returns_zero_when_nothing_expired(self):
        """Cleanup with no expired entries should return 0."""
        store = TokenRevocationStore()
        store.revoke("future-jti", int(time.time()) + 9999)
        assert store.cleanup() == 0

    def test_size_property(self):
        """Size should reflect number of entries."""
        store = TokenRevocationStore()
        assert store.size == 0
        store.revoke("a", int(time.time()) + 100)
        store.revoke("b", int(time.time()) + 100)
        assert store.size == 2

    def test_thread_safety(self):
        """Concurrent revoke and is_revoked calls should not raise."""
        store = TokenRevocationStore()
        errors = []

        def writer():
            try:
                for i in range(200):
                    store.revoke(f"jti-w-{i}", int(time.time()) + 3600)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(200):
                    store.is_revoked(f"jti-w-{i}")
            except Exception as e:
                errors.append(e)

        def cleaner():
            try:
                for _ in range(50):
                    store.cleanup()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=cleaner),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Thread-safety errors: {errors}"
        # All writer JTIs should be present (none expired yet)
        assert store.size == 200


# =========================================================================
# TokenRefreshManager Tests
# =========================================================================


class TestTokenRefreshManager:
    """Tests for refresh token issuance and exchange."""

    @pytest.fixture
    def access_token(self):
        """Create a sample AccessToken."""
        return AccessToken(
            token="dummy-access-token",
            client_id="test-user",
            scopes=["network:read", "network:write"],
            expires_at=int(time.time()) + 900,
        )

    @pytest.fixture
    def manager(self):
        """Create a TokenRefreshManager."""
        return TokenRefreshManager(
            secret_key=SECRET_KEY,
            issuer=ISSUER,
            audience=AUDIENCE,
            access_token_ttl=900,
            refresh_token_ttl=14400,
        )

    def test_issue_refresh_token(self, manager, access_token):
        """Issuing a refresh token should return a valid JWT string."""
        refresh = manager.issue_refresh_token(access_token)
        assert isinstance(refresh, str)
        assert len(refresh) > 0

        # Decode and verify structure
        payload = pyjwt.decode(refresh, SECRET_KEY, algorithms=["HS256"], audience=AUDIENCE)
        assert payload["typ"] == "refresh"
        assert payload["sub"] == "test-user"
        assert payload["scopes"] == ["network:read", "network:write"]
        assert "jti" in payload
        assert "exp" in payload

    def test_issue_refresh_token_increments_active_count(self, manager, access_token):
        """Each issued refresh token should increase active count."""
        assert manager.active_count == 0
        manager.issue_refresh_token(access_token)
        assert manager.active_count == 1
        manager.issue_refresh_token(access_token)
        assert manager.active_count == 2

    def test_refresh_access_token(self, manager, access_token):
        """Exchanging a valid refresh token should return a new AccessToken."""
        refresh = manager.issue_refresh_token(access_token)
        new_token = manager.refresh_access_token(refresh)

        assert isinstance(new_token, AccessToken)
        assert new_token.client_id == "test-user"
        assert new_token.scopes == ["network:read", "network:write"]
        assert new_token.expires_at is not None
        assert new_token.expires_at > int(time.time())

    def test_refresh_preserves_scopes(self, manager, access_token):
        """Refreshed access token should have the same scopes."""
        refresh = manager.issue_refresh_token(access_token)
        new_token = manager.refresh_access_token(refresh)
        assert new_token.scopes == access_token.scopes

    def test_refresh_with_revoked_token_fails(self, manager, access_token):
        """Refreshing with a revoked token should raise ValueError."""
        refresh = manager.issue_refresh_token(access_token)

        # Extract JTI from the refresh token
        payload = pyjwt.decode(refresh, SECRET_KEY, algorithms=["HS256"], audience=AUDIENCE)
        jti = payload["jti"]

        # Revoke it
        manager.revoke_refresh_token(jti)

        with pytest.raises(ValueError, match="revoked"):
            manager.refresh_access_token(refresh)

    def test_refresh_with_expired_token_fails(self, manager, access_token):
        """Expired refresh token should raise ValueError."""
        # Create a manager with 0-second TTL
        short_manager = TokenRefreshManager(
            secret_key=SECRET_KEY,
            issuer=ISSUER,
            audience=AUDIENCE,
            refresh_token_ttl=0,
        )
        refresh = short_manager.issue_refresh_token(access_token)

        # Token expires immediately (exp = now + 0 = now, which is in the past by the time we verify)
        time.sleep(1.1)
        with pytest.raises(ValueError, match="Invalid refresh token"):
            short_manager.refresh_access_token(refresh)

    def test_refresh_with_non_refresh_token_fails(self, manager):
        """A regular access token used as refresh should fail."""
        access_jwt = _make_token(scopes=["network:read"])
        with pytest.raises(ValueError, match="not a refresh token"):
            manager.refresh_access_token(access_jwt)

    def test_refresh_with_unknown_jti_fails(self, manager):
        """A refresh token with JTI not in active set should fail."""
        # Manually craft a refresh token with an untracked JTI
        now = int(time.time())
        payload = {
            "typ": "refresh",
            "jti": "unknown-jti-123",
            "sub": "test-user",
            "scopes": ["network:read"],
            "iat": now,
            "exp": now + 14400,
            "iss": ISSUER,
            "aud": AUDIENCE,
        }
        token = pyjwt.encode(payload, SECRET_KEY, algorithm="HS256")
        with pytest.raises(ValueError, match="not recognized"):
            manager.refresh_access_token(token)

    def test_revoke_refresh_token(self, manager, access_token):
        """Revoking a refresh token should add its JTI to the revocation store."""
        refresh = manager.issue_refresh_token(access_token)
        payload = pyjwt.decode(refresh, SECRET_KEY, algorithms=["HS256"], audience=AUDIENCE)
        jti = payload["jti"]

        manager.revoke_refresh_token(jti)

        assert manager.revocation_store.is_revoked(jti) is True
        assert manager.active_count == 0

    def test_revoke_unknown_jti_is_noop(self, manager):
        """Revoking an unknown JTI should not raise."""
        manager.revoke_refresh_token("nonexistent-jti")
        # No exception, no entry in revocation store
        assert manager.revocation_store.is_revoked("nonexistent-jti") is False

    def test_cleanup_removes_expired_active_jtis(self, manager, access_token):
        """Cleanup should remove expired JTIs from both active set and revocation store."""
        # Create a manager with a very short TTL
        short_manager = TokenRefreshManager(
            secret_key=SECRET_KEY,
            issuer=ISSUER,
            audience=AUDIENCE,
            refresh_token_ttl=0,
        )
        short_manager.issue_refresh_token(access_token)
        assert short_manager.active_count == 1

        # Wait for expiry
        time.sleep(0.1)
        removed = short_manager.cleanup()
        assert removed >= 1
        assert short_manager.active_count == 0


# =========================================================================
# Revocation Integration with JWTTokenVerifier Tests
# =========================================================================


class TestRevocationIntegration:
    """Test that token revocation integrates with JWTTokenVerifier."""

    def test_revoked_jti_rejected_by_verifier(self):
        """A token with a revoked JTI should be rejected by verify_token."""
        store = TokenRevocationStore()
        verifier = JWTTokenVerifier(
            secret_key=SECRET_KEY,
            issuer=ISSUER,
            audience=AUDIENCE,
            algorithms=["HS256"],
            revocation_store=store,
        )

        jti = "revokable-jti-001"
        raw = _make_token(scopes=["network:read"], jti=jti)

        # Token works before revocation
        result = verifier.verify_token(raw)
        assert result is not None
        assert result.scopes == ["network:read"]

        # Revoke it
        store.revoke(jti, int(time.time()) + 3600)

        # Token should now be rejected
        result = verifier.verify_token(raw)
        assert result is None

    def test_token_without_jti_passes_revocation_check(self):
        """Tokens without JTI should not be affected by revocation store."""
        store = TokenRevocationStore()
        verifier = JWTTokenVerifier(
            secret_key=SECRET_KEY,
            issuer=ISSUER,
            audience=AUDIENCE,
            algorithms=["HS256"],
            revocation_store=store,
        )

        raw = _make_token(scopes=["network:read"])
        result = verifier.verify_token(raw)
        assert result is not None

    def test_verifier_without_revocation_store_ignores_jti(self):
        """When no revocation store is configured, JTI is not checked."""
        verifier = JWTTokenVerifier(
            secret_key=SECRET_KEY,
            issuer=ISSUER,
            audience=AUDIENCE,
            algorithms=["HS256"],
        )

        raw = _make_token(scopes=["network:read"], jti="some-jti")
        result = verifier.verify_token(raw)
        assert result is not None

    def test_full_refresh_then_revoke_flow(self):
        """End-to-end: issue access token → issue refresh → refresh → revoke refresh."""
        store = TokenRevocationStore()
        verifier = JWTTokenVerifier(
            secret_key=SECRET_KEY,
            issuer=ISSUER,
            audience=AUDIENCE,
            algorithms=["HS256"],
            revocation_store=store,
        )
        manager = TokenRefreshManager(
            secret_key=SECRET_KEY,
            issuer=ISSUER,
            audience=AUDIENCE,
            revocation_store=store,
        )

        # Step 1: Verify an initial access token
        access_jwt = _make_token(scopes=["network:read", "network:write"], jti="access-jti-1")
        access_token = verifier.verify_token(access_jwt)
        assert access_token is not None

        # Step 2: Issue a refresh token
        refresh_jwt = manager.issue_refresh_token(access_token)
        assert manager.active_count == 1

        # Step 3: Use refresh token to get new access token
        new_access = manager.refresh_access_token(refresh_jwt)
        assert new_access.scopes == ["network:read", "network:write"]

        # Step 4: The new access token should be verifiable
        new_result = verifier.verify_token(new_access.token)
        assert new_result is not None

        # Step 5: Revoke the refresh token
        refresh_payload = pyjwt.decode(refresh_jwt, SECRET_KEY, algorithms=["HS256"], audience=AUDIENCE)
        manager.revoke_refresh_token(refresh_payload["jti"])

        # Step 6: Refresh should now fail
        with pytest.raises(ValueError, match="revoked"):
            manager.refresh_access_token(refresh_jwt)

    def test_ttl_cleanup_removes_expired_revoked_jtis(self):
        """Cleanup should remove expired JTIs from the revocation store."""
        store = TokenRevocationStore()
        # Add already-expired entry
        store.revoke("old-jti", int(time.time()) - 100)
        # Add still-valid entry
        store.revoke("valid-jti", int(time.time()) + 3600)

        removed = store.cleanup()
        assert removed == 1
        assert store.is_revoked("old-jti") is False
        assert store.is_revoked("valid-jti") is True
