"""Tests for API key authentication system.

Covers:
- Key generation format (prefix, length, uniqueness)
- Key validation (correct key, wrong key, expired key, revoked key)
- Key rotation (old key invalid, new key works)
- Scope binding (key carries correct scopes)
- Tenant binding
- File persistence (save, reload)
- Thread-safe concurrent operations
- API key auth integration with TokenVerifier and RBAC
"""

import json
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from network_mcp.api_keys import APIKey, APIKeyManager, _hash_key

# =========================================================================
# APIKey Dataclass Tests
# =========================================================================


class TestAPIKey:
    """Tests for the APIKey dataclass."""

    def test_not_expired_when_no_expiration(self):
        """Key without expiration should never be expired."""
        key = APIKey(key_id="nmcp_test1234", key_hash="abc", name="test", scopes=["network:read"])
        assert not key.is_expired

    def test_not_expired_when_future(self):
        """Key with future expiration should not be expired."""
        future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        key = APIKey(key_id="nmcp_test1234", key_hash="abc", name="test", scopes=["network:read"], expires_at=future)
        assert not key.is_expired

    def test_expired_when_past(self):
        """Key with past expiration should be expired."""
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        key = APIKey(key_id="nmcp_test1234", key_hash="abc", name="test", scopes=["network:read"], expires_at=past)
        assert key.is_expired

    def test_expired_with_invalid_timestamp(self):
        """Key with invalid expiration should not be considered expired."""
        key = APIKey(
            key_id="nmcp_test1234", key_hash="abc", name="test", scopes=["network:read"], expires_at="not-a-date"
        )
        assert not key.is_expired


# =========================================================================
# Key Generation Tests
# =========================================================================


class TestKeyGeneration:
    """Tests for API key generation format and uniqueness."""

    def test_key_has_correct_prefix(self, tmp_path):
        """Generated key should start with the configured prefix."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"), prefix="nmcp_")
        raw_key, _ = mgr.generate_key("test", ["network:read"])
        assert raw_key.startswith("nmcp_")

    def test_key_has_correct_length(self, tmp_path):
        """Generated key should be prefix + 64 hex chars (32 bytes)."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"), prefix="nmcp_")
        raw_key, _ = mgr.generate_key("test", ["network:read"])
        # prefix (5 chars) + 64 hex chars = 69
        assert len(raw_key) == len("nmcp_") + 64

    def test_key_hex_chars_only(self, tmp_path):
        """Hex portion of key should only contain valid hex characters."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"), prefix="nmcp_")
        raw_key, _ = mgr.generate_key("test", ["network:read"])
        hex_part = raw_key[len("nmcp_") :]
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_keys_are_unique(self, tmp_path):
        """Multiple generated keys should be unique."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        keys = set()
        for i in range(20):
            raw_key, _ = mgr.generate_key(f"test-{i}", ["network:read"])
            keys.add(raw_key)
        assert len(keys) == 20

    def test_key_id_format(self, tmp_path):
        """Key ID should be prefix + first 8 hex chars."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"), prefix="nmcp_")
        raw_key, api_key = mgr.generate_key("test", ["network:read"])
        expected_id = raw_key[: len("nmcp_") + 8]
        assert api_key.key_id == expected_id

    def test_custom_prefix(self, tmp_path):
        """Custom prefix should be used in key generation."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"), prefix="myprefix_")
        raw_key, api_key = mgr.generate_key("test", ["network:read"])
        assert raw_key.startswith("myprefix_")
        assert api_key.key_id.startswith("myprefix_")

    def test_scopes_stored_correctly(self, tmp_path):
        """Scopes should be preserved in the generated key metadata."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, api_key = mgr.generate_key("test", ["network:read", "network:write"])
        assert api_key.scopes == ["network:read", "network:write"]

    def test_tenant_id_stored(self, tmp_path):
        """Tenant ID should be stored when provided."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, api_key = mgr.generate_key("test", ["network:read"], tenant_id="acme-corp")
        assert api_key.tenant_id == "acme-corp"

    def test_ttl_sets_expiration(self, tmp_path):
        """TTL in days should set an expiration timestamp."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, api_key = mgr.generate_key("test", ["network:read"], ttl_days=30)
        assert api_key.expires_at is not None
        exp = datetime.fromisoformat(api_key.expires_at)
        # Should expire roughly 30 days from now
        expected = datetime.now(UTC) + timedelta(days=30)
        delta = abs((exp - expected).total_seconds())
        assert delta < 60  # within 60 seconds of expected

    def test_no_ttl_means_no_expiration(self, tmp_path):
        """No TTL should mean no expiration."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, api_key = mgr.generate_key("test", ["network:read"])
        assert api_key.expires_at is None

    def test_created_at_set(self, tmp_path):
        """Created timestamp should be set."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, api_key = mgr.generate_key("test", ["network:read"])
        assert api_key.created_at
        # Should parse as valid ISO datetime
        datetime.fromisoformat(api_key.created_at)

    def test_key_is_active_by_default(self, tmp_path):
        """Newly created key should be active."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, api_key = mgr.generate_key("test", ["network:read"])
        assert api_key.is_active is True


# =========================================================================
# Key Validation Tests
# =========================================================================


class TestKeyValidation:
    """Tests for API key validation."""

    def test_valid_key_succeeds(self, tmp_path):
        """A correct raw key should validate successfully."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        raw_key, api_key = mgr.generate_key("test", ["network:read"])
        result = mgr.validate_key(raw_key)
        assert result is not None
        assert result.key_id == api_key.key_id
        assert result.name == "test"

    def test_wrong_key_fails(self, tmp_path):
        """An incorrect key should return None."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        mgr.generate_key("test", ["network:read"])
        result = mgr.validate_key("nmcp_0000000000000000000000000000000000000000000000000000000000000000")
        assert result is None

    def test_wrong_prefix_fails(self, tmp_path):
        """A key with wrong prefix should return None."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"), prefix="nmcp_")
        mgr.generate_key("test", ["network:read"])
        result = mgr.validate_key("wrong_abcdef1234567890abcdef1234567890abcdef1234567890abcdef12345678")
        assert result is None

    def test_expired_key_fails(self, tmp_path):
        """An expired key should return None."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        raw_key, api_key = mgr.generate_key("test", ["network:read"])
        # Manually set expiration to the past
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        with mgr._lock:
            mgr._keys[api_key.key_id].expires_at = past
        result = mgr.validate_key(raw_key)
        assert result is None

    def test_revoked_key_fails(self, tmp_path):
        """A revoked key should return None."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        raw_key, api_key = mgr.generate_key("test", ["network:read"])
        mgr.revoke_key(api_key.key_id)
        result = mgr.validate_key(raw_key)
        assert result is None

    def test_validation_updates_last_used(self, tmp_path):
        """Successful validation should update last_used timestamp."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        raw_key, api_key = mgr.generate_key("test", ["network:read"])
        assert api_key.last_used is None
        mgr.validate_key(raw_key)
        # Re-fetch from internal state
        updated = mgr._keys[api_key.key_id]
        assert updated.last_used is not None

    def test_empty_string_fails(self, tmp_path):
        """An empty string should return None."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        assert mgr.validate_key("") is None


# =========================================================================
# Key Revocation Tests
# =========================================================================


class TestKeyRevocation:
    """Tests for API key revocation."""

    def test_revoke_existing_key(self, tmp_path):
        """Revoking an existing key should succeed."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, api_key = mgr.generate_key("test", ["network:read"])
        assert mgr.revoke_key(api_key.key_id) is True

    def test_revoke_nonexistent_key(self, tmp_path):
        """Revoking a nonexistent key should return False."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        assert mgr.revoke_key("nmcp_nonexist") is False

    def test_revoked_key_appears_inactive_in_list(self, tmp_path):
        """Revoked key should show as inactive in list."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, api_key = mgr.generate_key("test", ["network:read"])
        mgr.revoke_key(api_key.key_id)
        keys = mgr.list_keys()
        found = [k for k in keys if k.key_id == api_key.key_id]
        assert len(found) == 1
        assert found[0].is_active is False


# =========================================================================
# Key Rotation Tests
# =========================================================================


class TestKeyRotation:
    """Tests for API key rotation."""

    def test_rotate_produces_new_key(self, tmp_path):
        """Rotation should produce a new key with a different ID."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        raw_old, old_key = mgr.generate_key("test", ["network:read", "network:write"])
        result = mgr.rotate_key(old_key.key_id)
        assert result is not None
        raw_new, new_key = result
        assert raw_new != raw_old
        assert new_key.key_id != old_key.key_id

    def test_old_key_invalid_after_rotation(self, tmp_path):
        """Old key should be invalid after rotation."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        raw_old, old_key = mgr.generate_key("test", ["network:read"])
        mgr.rotate_key(old_key.key_id)
        assert mgr.validate_key(raw_old) is None

    def test_new_key_valid_after_rotation(self, tmp_path):
        """New key should be valid after rotation."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, old_key = mgr.generate_key("test", ["network:read"])
        result = mgr.rotate_key(old_key.key_id)
        assert result is not None
        raw_new, _ = result
        assert mgr.validate_key(raw_new) is not None

    def test_rotation_preserves_scopes(self, tmp_path):
        """Rotated key should preserve original scopes."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, old_key = mgr.generate_key("test", ["network:read", "network:write"])
        result = mgr.rotate_key(old_key.key_id)
        assert result is not None
        _, new_key = result
        assert new_key.scopes == ["network:read", "network:write"]

    def test_rotation_preserves_tenant(self, tmp_path):
        """Rotated key should preserve tenant binding."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, old_key = mgr.generate_key("test", ["network:read"], tenant_id="acme")
        result = mgr.rotate_key(old_key.key_id)
        assert result is not None
        _, new_key = result
        assert new_key.tenant_id == "acme"

    def test_rotation_preserves_name(self, tmp_path):
        """Rotated key should preserve the original name."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, old_key = mgr.generate_key("ci-pipeline", ["network:read"])
        result = mgr.rotate_key(old_key.key_id)
        assert result is not None
        _, new_key = result
        assert new_key.name == "ci-pipeline"

    def test_rotate_nonexistent_key(self, tmp_path):
        """Rotating a nonexistent key should return None."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        assert mgr.rotate_key("nmcp_nonexist") is None


# =========================================================================
# Scope Binding Tests
# =========================================================================


class TestScopeBinding:
    """Tests for scope enforcement on API keys."""

    def test_read_only_scopes(self, tmp_path):
        """Key with read-only scope should carry only that scope."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        raw_key, _ = mgr.generate_key("reader", ["network:read"])
        validated = mgr.validate_key(raw_key)
        assert validated is not None
        assert validated.scopes == ["network:read"]
        assert "network:write" not in validated.scopes

    def test_admin_scopes(self, tmp_path):
        """Key with admin scope should carry admin scope."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        raw_key, _ = mgr.generate_key("admin", ["network:admin"])
        validated = mgr.validate_key(raw_key)
        assert validated is not None
        assert "network:admin" in validated.scopes

    def test_multiple_scopes(self, tmp_path):
        """Key with multiple scopes should carry all scopes."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        scopes = ["network:read", "network:write", "network:audit"]
        raw_key, _ = mgr.generate_key("multi", scopes)
        validated = mgr.validate_key(raw_key)
        assert validated is not None
        assert validated.scopes == scopes


# =========================================================================
# Tenant Binding Tests
# =========================================================================


class TestTenantBinding:
    """Tests for tenant binding on API keys."""

    def test_tenant_stored_and_retrieved(self, tmp_path):
        """Tenant ID should be stored and retrievable via validation."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        raw_key, _ = mgr.generate_key("test", ["network:read"], tenant_id="acme-corp")
        validated = mgr.validate_key(raw_key)
        assert validated is not None
        assert validated.tenant_id == "acme-corp"

    def test_no_tenant_when_not_set(self, tmp_path):
        """Tenant ID should be None when not provided."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        raw_key, _ = mgr.generate_key("test", ["network:read"])
        validated = mgr.validate_key(raw_key)
        assert validated is not None
        assert validated.tenant_id is None


# =========================================================================
# File Persistence Tests
# =========================================================================


class TestFilePersistence:
    """Tests for API key file persistence."""

    def test_save_and_reload(self, tmp_path):
        """Keys should survive save and reload."""
        key_file = str(tmp_path / "keys.json")
        mgr1 = APIKeyManager(key_file=key_file)
        raw_key, api_key = mgr1.generate_key("persist-test", ["network:read", "network:write"])

        # Create a new manager that loads from the same file
        mgr2 = APIKeyManager(key_file=key_file)
        validated = mgr2.validate_key(raw_key)
        assert validated is not None
        assert validated.key_id == api_key.key_id
        assert validated.name == "persist-test"
        assert validated.scopes == ["network:read", "network:write"]

    def test_file_contains_valid_json(self, tmp_path):
        """Key file should contain valid JSON."""
        key_file = tmp_path / "keys.json"
        mgr = APIKeyManager(key_file=str(key_file))
        mgr.generate_key("test", ["network:read"])
        data = json.loads(key_file.read_text())
        assert "keys" in data
        assert len(data["keys"]) == 1

    def test_hash_stored_not_raw_key(self, tmp_path):
        """File should contain hash, not raw key."""
        key_file = tmp_path / "keys.json"
        mgr = APIKeyManager(key_file=str(key_file))
        raw_key, _ = mgr.generate_key("test", ["network:read"])
        file_content = key_file.read_text()
        assert raw_key not in file_content
        assert _hash_key(raw_key) in file_content

    def test_multiple_keys_persisted(self, tmp_path):
        """Multiple keys should all be persisted."""
        key_file = str(tmp_path / "keys.json")
        mgr = APIKeyManager(key_file=key_file)
        keys = []
        for i in range(5):
            raw, meta = mgr.generate_key(f"key-{i}", ["network:read"])
            keys.append((raw, meta))

        mgr2 = APIKeyManager(key_file=key_file)
        for raw, meta in keys:
            validated = mgr2.validate_key(raw)
            assert validated is not None
            assert validated.key_id == meta.key_id

    def test_revocation_persisted(self, tmp_path):
        """Key revocation should survive reload."""
        key_file = str(tmp_path / "keys.json")
        mgr1 = APIKeyManager(key_file=key_file)
        raw_key, api_key = mgr1.generate_key("test", ["network:read"])
        mgr1.revoke_key(api_key.key_id)

        mgr2 = APIKeyManager(key_file=key_file)
        assert mgr2.validate_key(raw_key) is None

    def test_nonexistent_file_starts_empty(self, tmp_path):
        """Manager with nonexistent file should start with empty keys."""
        mgr = APIKeyManager(key_file=str(tmp_path / "nonexistent.json"))
        assert mgr.list_keys() == []

    def test_corrupt_file_handled_gracefully(self, tmp_path):
        """Corrupt key file should be handled without crash."""
        key_file = tmp_path / "keys.json"
        key_file.write_text("not valid json!!!")
        mgr = APIKeyManager(key_file=str(key_file))
        assert mgr.list_keys() == []


# =========================================================================
# List Keys Tests
# =========================================================================


class TestListKeys:
    """Tests for listing API keys."""

    def test_list_redacts_hashes(self, tmp_path):
        """Listed keys should have hashes redacted."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        mgr.generate_key("test", ["network:read"])
        keys = mgr.list_keys()
        assert len(keys) == 1
        assert keys[0].key_hash == "[REDACTED]"

    def test_list_includes_all_keys(self, tmp_path):
        """List should include both active and inactive keys."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, key1 = mgr.generate_key("active", ["network:read"])
        _, key2 = mgr.generate_key("revoked", ["network:read"])
        mgr.revoke_key(key2.key_id)
        keys = mgr.list_keys()
        assert len(keys) == 2


# =========================================================================
# Thread Safety Tests
# =========================================================================


class TestThreadSafety:
    """Tests for thread-safe concurrent operations."""

    def test_concurrent_generation(self, tmp_path):
        """Concurrent key generation should not lose keys."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        results = []
        errors = []

        def generate(name):
            try:
                raw, meta = mgr.generate_key(name, ["network:read"])
                results.append((raw, meta))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=generate, args=(f"key-{i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 20
        # All keys should be valid
        for raw, _meta in results:
            assert mgr.validate_key(raw) is not None

    def test_concurrent_validation(self, tmp_path):
        """Concurrent validation should be thread-safe."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        raw_key, _ = mgr.generate_key("test", ["network:read"])
        results = []
        errors = []

        def validate():
            try:
                result = mgr.validate_key(raw_key)
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=validate) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 20
        assert all(r is not None for r in results)

    def test_concurrent_revocation(self, tmp_path):
        """Concurrent revocation of the same key should not crash."""
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        _, api_key = mgr.generate_key("test", ["network:read"])
        results = []
        errors = []

        def revoke():
            try:
                result = mgr.revoke_key(api_key.key_id)
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=revoke) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # At least one should return True (the first successful revocation)
        assert any(results)


# =========================================================================
# TokenVerifier API Key Integration Tests
# =========================================================================


class TestTokenVerifierIntegration:
    """Tests for API key integration with the TokenVerifier adapter."""

    def _make_verifier(self, tmp_path):
        """Create a TokenVerifier with API key support."""
        from network_mcp.auth import JWTTokenVerifier, TokenVerifier

        jwt_verifier = JWTTokenVerifier(
            secret_key="test-secret-key",
            issuer="https://auth.example.com",
        )
        verifier = TokenVerifier(jwt_verifier)
        mgr = APIKeyManager(key_file=str(tmp_path / "keys.json"))
        verifier.set_api_key_manager(mgr, prefix="nmcp_")
        return verifier, mgr

    @pytest.mark.asyncio
    async def test_api_key_verified_via_token_verifier(self, tmp_path):
        """API key should be verified through TokenVerifier.verify_token."""
        verifier, mgr = self._make_verifier(tmp_path)
        raw_key, _ = mgr.generate_key("test", ["network:read"])
        result = await verifier.verify_token(raw_key)
        assert result is not None
        assert result.client_id.startswith("apikey:")
        assert "network:read" in result.scopes

    @pytest.mark.asyncio
    async def test_invalid_api_key_rejected(self, tmp_path):
        """Invalid API key should be rejected by TokenVerifier."""
        verifier, mgr = self._make_verifier(tmp_path)
        result = await verifier.verify_token("nmcp_0000000000000000000000000000000000000000000000000000000000000000")
        assert result is None

    @pytest.mark.asyncio
    async def test_revoked_api_key_rejected(self, tmp_path):
        """Revoked API key should be rejected by TokenVerifier."""
        verifier, mgr = self._make_verifier(tmp_path)
        raw_key, api_key = mgr.generate_key("test", ["network:read"])
        mgr.revoke_key(api_key.key_id)
        result = await verifier.verify_token(raw_key)
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_api_key_rejected(self, tmp_path):
        """Expired API key should be rejected by TokenVerifier."""
        verifier, mgr = self._make_verifier(tmp_path)
        raw_key, api_key = mgr.generate_key("test", ["network:read"])
        # Manually expire the key
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        with mgr._lock:
            mgr._keys[api_key.key_id].expires_at = past
        result = await verifier.verify_token(raw_key)
        assert result is None

    @pytest.mark.asyncio
    async def test_api_key_expiration_converted_to_epoch(self, tmp_path):
        """API key expiration should be converted to epoch timestamp."""
        verifier, mgr = self._make_verifier(tmp_path)
        raw_key, _ = mgr.generate_key("test", ["network:read"], ttl_days=30)
        result = await verifier.verify_token(raw_key)
        assert result is not None
        assert result.expires_at is not None
        assert isinstance(result.expires_at, int)
        # Should be roughly 30 days from now
        expected = int((datetime.now(UTC) + timedelta(days=30)).timestamp())
        assert abs(result.expires_at - expected) < 60

    @pytest.mark.asyncio
    async def test_jwt_still_works_with_api_key_manager(self, tmp_path):
        """JWT tokens should still work when API key manager is attached."""
        import jwt as pyjwt

        verifier, mgr = self._make_verifier(tmp_path)
        # Create a valid JWT
        token = pyjwt.encode(
            {
                "sub": "test-user",
                "iss": "https://auth.example.com",
                "aud": "network-mcp",
                "exp": int(time.time()) + 3600,
                "scopes": ["network:read"],
            },
            "test-secret-key",
            algorithm="HS256",
        )
        result = await verifier.verify_token(token)
        assert result is not None
        assert result.client_id == "test-user"

    @pytest.mark.asyncio
    async def test_api_key_without_manager_falls_to_jwt(self, tmp_path):
        """Without API key manager, prefixed tokens should fall through to JWT (and fail)."""
        from network_mcp.auth import JWTTokenVerifier, TokenVerifier

        jwt_verifier = JWTTokenVerifier(
            secret_key="test-secret-key",
            issuer="https://auth.example.com",
        )
        verifier = TokenVerifier(jwt_verifier)
        # No API key manager attached — nmcp_ prefixed string is not a valid JWT
        result = await verifier.verify_token("nmcp_abcdef1234567890abcdef1234567890abcdef1234567890abcdef12345678")
        assert result is None


# =========================================================================
# RBAC Integration Tests
# =========================================================================


class TestRBACIntegration:
    """Tests for API key integration with RBAC scope checking."""

    def test_api_key_scopes_checked_by_rbac(self, tmp_path):
        """RBAC should check scopes from API key correctly."""
        from network_mcp.rbac import check_authorization

        # Simulate token claims from an API key with read-only scope
        token_claims = {
            "scope": "network:read",
            "client_id": "apikey:nmcp_test1234",
        }
        authorized, msg = check_authorization("net_get_device_info", token_claims)
        assert authorized is True

    def test_api_key_read_scope_blocks_write(self):
        """API key with read scope should be blocked from write tools."""
        from network_mcp.rbac import check_authorization

        token_claims = {
            "scope": "network:read",
            "client_id": "apikey:nmcp_test1234",
        }
        authorized, msg = check_authorization("net_create_vlan", token_claims)
        assert authorized is False
        assert "network:write" in msg

    def test_api_key_admin_scope_allows_admin_tools(self):
        """API key with admin scope should access admin tools."""
        from network_mcp.rbac import check_authorization

        token_claims = {
            "scope": "network:admin",
            "client_id": "apikey:nmcp_admin1234",
        }
        authorized, msg = check_authorization("net_create_api_key", token_claims)
        assert authorized is True

    def test_api_key_write_scope_blocks_admin_tools(self):
        """API key with write scope should be blocked from admin-only tools."""
        from network_mcp.rbac import check_authorization

        token_claims = {
            "scope": "network:write",
            "client_id": "apikey:nmcp_writer1234",
        }
        authorized, msg = check_authorization("net_create_api_key", token_claims)
        assert authorized is False
        assert "network:admin" in msg

    def test_api_key_management_tools_require_admin(self):
        """All API key management tools should require admin scope."""
        from network_mcp.rbac import get_required_scope

        admin_tools = ["net_create_api_key", "net_list_api_keys", "net_revoke_api_key", "net_rotate_api_key"]
        for tool_name in admin_tools:
            scope = get_required_scope(tool_name)
            assert scope == "network:admin", f"{tool_name} should require network:admin, got {scope}"


# =========================================================================
# Hash Function Tests
# =========================================================================


class TestHashFunction:
    """Tests for the key hashing utility."""

    def test_deterministic(self):
        """Same input should produce same hash."""
        assert _hash_key("nmcp_abc123") == _hash_key("nmcp_abc123")

    def test_different_inputs_different_hashes(self):
        """Different inputs should produce different hashes."""
        assert _hash_key("nmcp_abc123") != _hash_key("nmcp_def456")

    def test_hash_is_hex_string(self):
        """Hash should be a 64-char hex string (SHA-256)."""
        h = _hash_key("nmcp_test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)
