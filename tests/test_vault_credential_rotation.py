"""Vault credential rotation tests.

Tests for VaultCredentialProvider handling of TTL expiry, credential
refresh, Vault unreachability, permission denial, and concurrent
rotation.
"""

from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vault_provider(*, mock_client=None):
    """Build a VaultCredentialProvider with a mocked hvac client.

    Bypasses the __init__ authentication check by patching _import_hvac.
    """
    mock_hvac = MagicMock()
    client = mock_client or MagicMock()
    client.is_authenticated.return_value = True
    mock_hvac.Client.return_value = client

    with patch("network_mcp.integrations.vault._import_hvac", return_value=mock_hvac):
        from network_mcp.integrations.vault import VaultCredentialProvider

        provider = VaultCredentialProvider(
            addr="https://vault.example.com:8200",
            token="s.test-token",
            mount_point="secret",
        )
    # Ensure the provider uses our mock client for subsequent calls
    provider._client = client
    return provider


# ---------------------------------------------------------------------------
# Basic credential retrieval
# ---------------------------------------------------------------------------


class TestVaultCredentialRetrieval:
    """Tests for basic credential retrieval from Vault."""

    def test_successful_credential_retrieval(self):
        """Vault returns valid credentials → username/password returned."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"username": "admin", "password": "secret123"}}
        }

        provider = _make_vault_provider(mock_client=mock_client)
        creds = provider.get_credentials("spine-01")

        assert creds["username"] == "admin"
        assert creds["password"] == "secret123"
        mock_client.secrets.kv.v2.read_secret_version.assert_called_once_with(
            path="network/spine-01",
            mount_point="secret",
        )

    def test_credential_retrieval_failure(self):
        """Vault returns error → exception raised."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.read_secret_version.side_effect = Exception("Permission denied")

        provider = _make_vault_provider(mock_client=mock_client)

        with pytest.raises(Exception, match="Permission denied"):
            provider.get_credentials("spine-01")


# ---------------------------------------------------------------------------
# Credential rotation (TTL expiry simulation)
# ---------------------------------------------------------------------------


class TestVaultCredentialRotation:
    """Tests for credential rotation when TTL expires."""

    def test_second_fetch_returns_new_credentials_after_rotation(self):
        """After rotation, next credential fetch returns new credentials."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True

        # First call returns old credentials, second returns new
        mock_client.secrets.kv.v2.read_secret_version.side_effect = [
            {"data": {"data": {"username": "admin", "password": "old-pass"}}},
            {"data": {"data": {"username": "admin", "password": "new-pass"}}},
        ]

        provider = _make_vault_provider(mock_client=mock_client)

        creds1 = provider.get_credentials("spine-01")
        assert creds1["password"] == "old-pass"

        creds2 = provider.get_credentials("spine-01")
        assert creds2["password"] == "new-pass"

        assert mock_client.secrets.kv.v2.read_secret_version.call_count == 2

    def test_vault_unreachable_during_rotation_raises(self, caplog):
        """Vault unreachable during rotation → exception raised with logging."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True

        # First call succeeds, second fails (Vault unreachable)
        mock_client.secrets.kv.v2.read_secret_version.side_effect = [
            {"data": {"data": {"username": "admin", "password": "cached-pass"}}},
            ConnectionError("Vault unreachable"),
        ]

        provider = _make_vault_provider(mock_client=mock_client)

        # First call succeeds
        creds1 = provider.get_credentials("spine-01")
        assert creds1["password"] == "cached-pass"

        # Second call fails — Vault is unreachable
        with (
            caplog.at_level(logging.ERROR, logger="network-mcp"),
            pytest.raises(ConnectionError, match="Vault unreachable"),
        ):
            provider.get_credentials("spine-01")

    def test_vault_permission_denied_raises(self, caplog):
        """Vault returns 403 (permission denied) → raises with clear message."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True

        # Simulate a 403 Forbidden error from Vault
        mock_client.secrets.kv.v2.read_secret_version.side_effect = Exception(
            "1 error occurred:\n\t* permission denied\n\n"
        )

        provider = _make_vault_provider(mock_client=mock_client)

        with (
            caplog.at_level(logging.ERROR, logger="network-mcp"),
            pytest.raises(Exception, match="permission denied"),
        ):
            provider.get_credentials("spine-01")

        assert "Failed to retrieve credentials" in caplog.text


# ---------------------------------------------------------------------------
# Get all credentials
# ---------------------------------------------------------------------------


class TestVaultGetAllCredentials:
    """Tests for get_all_credentials listing."""

    def test_list_all_device_credentials(self):
        """Lists and fetches credentials for all devices under network/ path."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.list_secrets.return_value = {"data": {"keys": ["spine-01/", "leaf-01/"]}}
        mock_client.secrets.kv.v2.read_secret_version.side_effect = [
            {"data": {"data": {"username": "admin", "password": "pass1"}}},
            {"data": {"data": {"username": "admin", "password": "pass2"}}},
        ]

        provider = _make_vault_provider(mock_client=mock_client)
        all_creds = provider.get_all_credentials()

        assert "spine-01" in all_creds
        assert "leaf-01" in all_creds
        assert all_creds["spine-01"]["password"] == "pass1"
        assert all_creds["leaf-01"]["password"] == "pass2"

    def test_list_empty_secrets_path(self):
        """No devices under network/ path → empty dict returned."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.list_secrets.return_value = {"data": {"keys": []}}

        provider = _make_vault_provider(mock_client=mock_client)
        all_creds = provider.get_all_credentials()

        assert all_creds == {}


# ---------------------------------------------------------------------------
# Authentication validation
# ---------------------------------------------------------------------------


class TestVaultAuthentication:
    """Tests for Vault authentication handling."""

    def test_unauthenticated_client_raises(self):
        """Vault client fails authentication → RuntimeError raised."""
        mock_hvac = MagicMock()
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = False
        mock_hvac.Client.return_value = mock_client

        with patch("network_mcp.integrations.vault._import_hvac", return_value=mock_hvac):
            from network_mcp.integrations.vault import VaultCredentialProvider

            with pytest.raises(RuntimeError, match="authentication failed"):
                VaultCredentialProvider(
                    addr="https://vault.example.com:8200",
                    token="bad-token",
                )

    def test_hvac_not_installed_raises(self):
        """hvac not installed → ImportError raised."""
        with patch("network_mcp.integrations.vault._import_hvac", return_value=None):
            from network_mcp.integrations.vault import VaultCredentialProvider

            with pytest.raises((ImportError, RuntimeError)):
                VaultCredentialProvider(
                    addr="https://vault.example.com:8200",
                    token="test-token",
                )


# ---------------------------------------------------------------------------
# Concurrent credential access
# ---------------------------------------------------------------------------


class TestVaultConcurrentAccess:
    """Tests for concurrent credential access patterns."""

    def test_concurrent_credential_requests_all_succeed(self):
        """Multiple threads requesting credentials → all get valid results."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"username": "admin", "password": "concurrent-pass"}}
        }

        provider = _make_vault_provider(mock_client=mock_client)

        results = {}
        errors = []

        def fetch_creds(device_name):
            try:
                results[device_name] = provider.get_credentials(device_name)
            except Exception as e:
                errors.append((device_name, str(e)))

        threads = []
        for i in range(5):
            t = threading.Thread(target=fetch_creds, args=(f"device-{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0
        assert len(results) == 5
        for _device_name, creds in results.items():
            assert creds["username"] == "admin"
            assert creds["password"] == "concurrent-pass"

    def test_concurrent_requests_for_same_device(self):
        """Multiple threads requesting same device → all succeed (Vault may be called multiple times)."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"username": "admin", "password": "same-device-pass"}}
        }

        provider = _make_vault_provider(mock_client=mock_client)

        results = []
        errors = []

        def fetch_creds():
            try:
                results.append(provider.get_credentials("spine-01"))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=fetch_creds) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0
        assert len(results) == 10
        assert all(r["password"] == "same-device-pass" for r in results)
        # Vault was called at least once (possibly up to 10 times without caching)
        assert mock_client.secrets.kv.v2.read_secret_version.call_count >= 1
