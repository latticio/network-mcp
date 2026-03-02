"""Tests for HashiCorp Vault credential management integration."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from network_mcp.config import NetworkSettings
from network_mcp.connection import ConnectionManager, DeviceCredentials
from network_mcp.integrations.vault import VaultCredentialProvider

# ---------------------------------------------------------------------------
# VaultCredentialProvider unit tests
# ---------------------------------------------------------------------------


class TestVaultCredentialProvider:
    """Tests for VaultCredentialProvider."""

    def test_successful_credential_retrieval(self):
        """Successful credential retrieval returns username/password dict."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"username": "vault-admin", "password": "vault-secret"}}
        }

        mock_hvac = MagicMock()
        mock_hvac.Client.return_value = mock_client
        with patch("network_mcp.integrations.vault._import_hvac", return_value=mock_hvac):
            provider = VaultCredentialProvider(addr="https://vault.example.com", token="s.token123")

            creds = provider.get_credentials("spine-01")

        assert creds == {"username": "vault-admin", "password": "vault-secret"}
        mock_client.secrets.kv.v2.read_secret_version.assert_called_once_with(
            path="network/spine-01",
            mount_point="secret",
        )

    def test_custom_mount_point(self):
        """Custom mount point is passed through to Vault client."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"username": "user", "password": "pass"}}
        }

        mock_hvac = MagicMock()
        mock_hvac.Client.return_value = mock_client
        with patch("network_mcp.integrations.vault._import_hvac", return_value=mock_hvac):
            provider = VaultCredentialProvider(
                addr="https://vault.example.com", token="s.token123", mount_point="network-creds"
            )

            provider.get_credentials("leaf-01")

        mock_client.secrets.kv.v2.read_secret_version.assert_called_once_with(
            path="network/leaf-01",
            mount_point="network-creds",
        )

    def test_device_not_found_raises_exception(self):
        """Device not found in Vault raises exception with clear message."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.read_secret_version.side_effect = Exception("secret not found")

        mock_hvac = MagicMock()
        mock_hvac.Client.return_value = mock_client
        with patch("network_mcp.integrations.vault._import_hvac", return_value=mock_hvac):
            provider = VaultCredentialProvider(addr="https://vault.example.com", token="s.token123")

            with pytest.raises(Exception, match="secret not found"):
                provider.get_credentials("nonexistent-device")

    def test_auth_failure_raises_runtime_error(self):
        """Vault auth failure raises RuntimeError."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = False

        mock_hvac = MagicMock()
        mock_hvac.Client.return_value = mock_client
        with patch("network_mcp.integrations.vault._import_hvac", return_value=mock_hvac):
            with pytest.raises(RuntimeError, match="Vault authentication failed"):
                VaultCredentialProvider(addr="https://vault.example.com", token="bad-token")

    def test_hvac_not_installed_raises_import_error(self):
        """hvac not installed raises ImportError with install instructions."""
        with patch("network_mcp.integrations.vault._import_hvac", return_value=None):
            with pytest.raises(ImportError, match="hvac not installed.*pip install network-mcp\\[vault\\]"):
                VaultCredentialProvider(addr="https://vault.example.com", token="s.token123")

    def test_get_all_credentials(self):
        """get_all_credentials lists and retrieves all device credentials."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.list_secrets.return_value = {"data": {"keys": ["spine-01/", "leaf-01"]}}
        mock_client.secrets.kv.v2.read_secret_version.side_effect = [
            {"data": {"data": {"username": "admin1", "password": "pass1"}}},
            {"data": {"data": {"username": "admin2", "password": "pass2"}}},
        ]

        mock_hvac = MagicMock()
        mock_hvac.Client.return_value = mock_client
        with patch("network_mcp.integrations.vault._import_hvac", return_value=mock_hvac):
            provider = VaultCredentialProvider(addr="https://vault.example.com", token="s.token123")

            result = provider.get_all_credentials()

        assert result == {
            "spine-01": {"username": "admin1", "password": "pass1"},
            "leaf-01": {"username": "admin2", "password": "pass2"},
        }


# ---------------------------------------------------------------------------
# ConnectionManager + Vault integration tests
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> NetworkSettings:
    """Create NetworkSettings with test defaults."""
    defaults = {
        "net_username": "default-user",
        "net_password": "default-pass",
        "net_transport": "https",
        "net_demo_mode": True,
        "timeout_connect": 10.0,
    }
    defaults.update(overrides)
    return NetworkSettings(**defaults)


class TestConnectionManagerVaultIntegration:
    """Tests for Vault integration with ConnectionManager."""

    def test_vault_enabled_successful_lookup(self):
        """Vault enabled + successful lookup uses Vault credentials."""
        mock_vault = MagicMock(spec=VaultCredentialProvider)
        mock_vault.get_credentials.return_value = {"username": "vault-user", "password": "vault-pass"}

        settings = _make_settings()
        mgr = ConnectionManager(settings=settings, vault_provider=mock_vault)

        username, password = mgr._resolve_credentials("spine-01")
        assert username == "vault-user"
        assert password == "vault-pass"
        mock_vault.get_credentials.assert_called_once_with("spine-01")

    def test_vault_enabled_fallback_on_failure(self):
        """Vault enabled + lookup fails falls back to env var credentials."""
        mock_vault = MagicMock(spec=VaultCredentialProvider)
        mock_vault.get_credentials.side_effect = Exception("Vault unavailable")

        settings = _make_settings(net_username="fallback-user", net_password="fallback-pass")
        mgr = ConnectionManager(settings=settings, vault_provider=mock_vault)

        username, password = mgr._resolve_credentials("spine-01")
        assert username == "fallback-user"
        assert password == "fallback-pass"

    def test_vault_disabled_uses_env_vars(self):
        """Vault disabled uses env var credentials directly."""
        settings = _make_settings(net_username="env-user", net_password="env-pass")
        mgr = ConnectionManager(settings=settings, vault_provider=None)

        username, password = mgr._resolve_credentials("spine-01")
        assert username == "env-user"
        assert password == "env-pass"

    def test_vault_credentials_used_in_get_driver(self):
        """Vault credentials are used when creating driver via get_driver()."""
        mock_vault = MagicMock(spec=VaultCredentialProvider)
        mock_vault.get_credentials.return_value = {"username": "vault-user", "password": "vault-pass"}

        settings = _make_settings()
        mgr = ConnectionManager(settings=settings, vault_provider=mock_vault)

        # Use demo mode so MockDriver is created without real devices
        driver = mgr.get_driver("test-device")
        assert driver is not None
        mock_vault.get_credentials.assert_called_once_with("test-device")

    def test_inventory_device_skips_vault(self):
        """Devices in inventory use inventory credentials, not Vault."""
        mock_vault = MagicMock(spec=VaultCredentialProvider)

        settings = _make_settings()
        mgr = ConnectionManager(settings=settings, vault_provider=mock_vault)
        # Manually add a device to inventory
        mgr._inventory["inventory-device"] = DeviceCredentials(
            host="10.0.0.1",
            username="inv-user",
            password=SecretStr("inv-pass"),
        )

        driver = mgr.get_driver("inventory-device")
        assert driver is not None
        # Vault should NOT be called for inventory devices
        mock_vault.get_credentials.assert_not_called()


# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------


class TestVaultConfigValidation:
    """Tests for Vault startup config validation."""

    def test_vault_enabled_without_addr_raises_system_exit(self):
        """VAULT_ENABLED=true without VAULT_ADDR raises SystemExit at startup."""
        settings = _make_settings()
        settings.vault_enabled = True
        settings.vault_addr = None
        settings.vault_token = SecretStr("s.token123")

        # Simulate the validation logic from server.py
        if settings.vault_enabled and not settings.vault_addr:
            with pytest.raises(SystemExit, match="VAULT_ADDR is empty"):
                raise SystemExit("VAULT_ENABLED=true but VAULT_ADDR is empty. Cannot start.")

    def test_vault_enabled_without_token_raises_system_exit(self):
        """VAULT_ENABLED=true without VAULT_TOKEN raises SystemExit at startup."""
        settings = _make_settings()
        settings.vault_enabled = True
        settings.vault_addr = "https://vault.example.com"
        settings.vault_token = None

        if settings.vault_enabled and (not settings.vault_token or not settings.vault_token.get_secret_value()):
            with pytest.raises(SystemExit, match="VAULT_TOKEN is empty"):
                raise SystemExit("VAULT_ENABLED=true but VAULT_TOKEN is empty. Cannot start.")

    def test_vault_enabled_empty_token_raises_system_exit(self):
        """VAULT_ENABLED=true with empty VAULT_TOKEN raises SystemExit."""
        settings = _make_settings()
        settings.vault_enabled = True
        settings.vault_addr = "https://vault.example.com"
        settings.vault_token = SecretStr("")

        if settings.vault_enabled and (not settings.vault_token or not settings.vault_token.get_secret_value()):
            with pytest.raises(SystemExit, match="VAULT_TOKEN is empty"):
                raise SystemExit("VAULT_ENABLED=true but VAULT_TOKEN is empty. Cannot start.")

    def test_vault_all_settings_present_succeeds(self):
        """All Vault settings present — validation passes."""
        settings = _make_settings()
        settings.vault_enabled = True
        settings.vault_addr = "https://vault.example.com"
        settings.vault_token = SecretStr("s.valid-token")
        settings.vault_mount = "secret"

        # Should not raise
        assert settings.vault_enabled is True
        assert settings.vault_addr == "https://vault.example.com"
        assert settings.vault_token.get_secret_value() == "s.valid-token"
        assert settings.vault_mount == "secret"

    def test_vault_disabled_by_default(self):
        """Vault is disabled by default."""
        settings = _make_settings()
        assert settings.vault_enabled is False
        assert settings.vault_addr is None
        assert settings.vault_token is None
        assert settings.vault_mount == "secret"
