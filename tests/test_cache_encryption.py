"""Tests for cache encryption at rest (Fernet-based)."""

import base64
import time
from unittest.mock import patch

import pytest

from network_mcp.cache import CommandCache, _CacheEncryptor, _derive_fernet_key

# Generate a valid Fernet key for testing (32 bytes, url-safe base64-encoded)
_TEST_FERNET_KEY = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()
_TEST_AUTH_SECRET = "my-auth-secret-key"


class TestCacheWithoutEncryption:
    """Baseline: cache set/get works without encryption."""

    def test_basic_set_get(self):
        cache = CommandCache(max_entries=100)
        data = {"vlans": {"100": {"name": "SERVERS"}}}
        cache.put("spine-01", ["show vlan"], "json", data, ttl=60)
        result = cache.get("spine-01", ["show vlan"])
        assert result == data

    def test_cache_miss(self):
        cache = CommandCache(max_entries=100)
        result = cache.get("spine-01", ["show vlan"])
        assert result is None


class TestCacheWithEncryptionExplicitKey:
    """Cache set/get with encryption enabled and explicit Fernet key."""

    def test_set_get_encrypted(self):
        cache = CommandCache(max_entries=100, encrypt=True, encryption_key=_TEST_FERNET_KEY)
        data = {"vlans": {"100": {"name": "SERVERS"}, "200": {"name": "MGMT"}}}
        cache.put("spine-01", ["show vlan"], "json", data, ttl=60)
        result = cache.get("spine-01", ["show vlan"])
        assert result == data

    def test_stored_data_is_encrypted_bytes(self):
        cache = CommandCache(max_entries=100, encrypt=True, encryption_key=_TEST_FERNET_KEY)
        data = {"version": "4.28.0F"}
        cache.put("spine-01", ["show version"], "json", data, ttl=60)
        # Access internal cache to verify stored data is bytes, not plaintext
        key = cache._make_key("spine-01", ["show version"], "json")
        entry = cache._cache[key]
        assert isinstance(entry.data, bytes)
        # Plaintext should not appear in the stored bytes
        assert b'"version"' not in entry.data

    def test_multiple_entries_encrypted(self):
        cache = CommandCache(max_entries=100, encrypt=True, encryption_key=_TEST_FERNET_KEY)
        data1 = {"vlans": {}}
        data2 = {"interfaces": {}}
        cache.put("spine-01", ["show vlan"], "json", data1, ttl=60)
        cache.put("spine-01", ["show interfaces"], "json", data2, ttl=60)
        assert cache.get("spine-01", ["show vlan"]) == data1
        assert cache.get("spine-01", ["show interfaces"]) == data2


class TestCacheWithEncryptionDerivedKey:
    """Cache set/get with encryption enabled and key derived from AUTH_SECRET_KEY."""

    def test_set_get_with_auth_secret_key(self):
        cache = CommandCache(max_entries=100, encrypt=True, auth_secret_key=_TEST_AUTH_SECRET)
        data = {"hostname": "spine-01", "version": "4.28.0F"}
        cache.put("spine-01", ["show version"], "json", data, ttl=60)
        result = cache.get("spine-01", ["show version"])
        assert result == data

    def test_derived_key_is_deterministic(self):
        key1 = _derive_fernet_key("same-secret")
        key2 = _derive_fernet_key("same-secret")
        assert key1 == key2

    def test_different_secrets_produce_different_keys(self):
        key1 = _derive_fernet_key("secret-a")
        key2 = _derive_fernet_key("secret-b")
        assert key1 != key2


class TestCacheEncryptionErrors:
    """Error handling for encryption edge cases."""

    def test_no_key_raises_value_error(self):
        with pytest.raises(ValueError, match="NET_CACHE_ENCRYPTION_KEY or AUTH_SECRET_KEY required"):
            CommandCache(max_entries=100, encrypt=True)

    def test_missing_cryptography_raises_import_error(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "cryptography.fernet" or (
                name == "cryptography" and args and args[0] and "fernet" in str(args[0])
            ):
                raise ImportError("No module named 'cryptography'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(ImportError, match="cryptography is required for cache encryption"):
                _CacheEncryptor(encryption_key=_TEST_FERNET_KEY)

    def test_corrupted_data_returns_cache_miss(self):
        cache = CommandCache(max_entries=100, encrypt=True, encryption_key=_TEST_FERNET_KEY)
        # Store valid encrypted data
        data = {"test": "value"}
        cache.put("spine-01", ["show version"], "json", data, ttl=60)
        # Corrupt the stored data
        key = cache._make_key("spine-01", ["show version"], "json")
        cache._cache[key].data = b"corrupted-not-valid-fernet-token"
        # Should return None (cache miss) rather than raising
        result = cache.get("spine-01", ["show version"])
        assert result is None


class TestCacheEncryptionWithInvalidation:
    """Cache invalidation works with encryption enabled."""

    def test_invalidate_host_with_encryption(self):
        cache = CommandCache(max_entries=100, encrypt=True, encryption_key=_TEST_FERNET_KEY)
        cache.put("spine-01", ["show vlan"], "json", {"vlans": {}}, ttl=60)
        cache.put("spine-01", ["show version"], "json", {"version": "4.28"}, ttl=60)
        cache.put("leaf-01", ["show vlan"], "json", {"vlans": {}}, ttl=60)

        removed = cache.invalidate_host("spine-01")
        assert removed == 2
        assert cache.get("spine-01", ["show vlan"]) is None
        assert cache.get("spine-01", ["show version"]) is None
        assert cache.get("leaf-01", ["show vlan"]) == {"vlans": {}}

    def test_clear_with_encryption(self):
        cache = CommandCache(max_entries=100, encrypt=True, encryption_key=_TEST_FERNET_KEY)
        cache.put("spine-01", ["show vlan"], "json", {"vlans": {}}, ttl=60)
        cache.put("leaf-01", ["show vlan"], "json", {"vlans": {}}, ttl=60)
        removed = cache.clear()
        assert removed == 2
        assert cache.get("spine-01", ["show vlan"]) is None


class TestCacheEncryptionWithTTL:
    """TTL expiration works with encryption enabled."""

    def test_ttl_expiry_with_encryption(self):
        cache = CommandCache(max_entries=100, encrypt=True, encryption_key=_TEST_FERNET_KEY)
        data = {"vlans": {"100": {"name": "TEST"}}}
        cache.put("spine-01", ["show vlan"], "json", data, ttl=1)

        # Should be available immediately
        result = cache.get("spine-01", ["show vlan"])
        assert result == data

        # Wait for TTL expiry
        time.sleep(1.1)

        # Should be expired now
        result = cache.get("spine-01", ["show vlan"])
        assert result is None


class TestCacheEncryptionStats:
    """Stats tracking works correctly with encryption."""

    def test_stats_with_encryption(self):
        cache = CommandCache(max_entries=100, encrypt=True, encryption_key=_TEST_FERNET_KEY)
        cache.put("spine-01", ["show vlan"], "json", {"vlans": {}}, ttl=60)

        # Hit
        cache.get("spine-01", ["show vlan"])
        # Miss
        cache.get("spine-01", ["show interfaces"])

        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_corrupted_entry_counts_as_miss(self):
        cache = CommandCache(max_entries=100, encrypt=True, encryption_key=_TEST_FERNET_KEY)
        cache.put("spine-01", ["show version"], "json", {"v": "1"}, ttl=60)
        # Corrupt
        key = cache._make_key("spine-01", ["show version"], "json")
        cache._cache[key].data = b"corrupted"
        cache.get("spine-01", ["show version"])

        stats = cache.stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 0
