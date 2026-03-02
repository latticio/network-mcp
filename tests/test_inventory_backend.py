"""Tests for inventory backend protocol and implementations."""

import os
import tempfile

import pytest

from network_mcp.inventory import (
    InventoryBackend,
    YamlInventoryBackend,
)


class TestInventoryBackendProtocol:
    """Verify backends satisfy the InventoryBackend protocol."""

    def test_yaml_backend_is_inventory_backend(self):
        yaml_content = "devices:\n  sw1:\n    host: 10.0.0.1\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            try:
                backend = YamlInventoryBackend(path=f.name)
                assert isinstance(backend, InventoryBackend)
            finally:
                os.unlink(f.name)


class TestYamlInventoryBackend:
    """Test YamlInventoryBackend."""

    def _write_yaml(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.flush()
        f.close()
        return f.name

    def test_load_basic(self):
        path = self._write_yaml("""
devices:
  spine-01:
    host: 10.0.0.1
    username: testuser
    password: testpass
    transport: https
    port: 443
  leaf-01:
    host: 10.0.1.1
""")
        try:
            backend = YamlInventoryBackend(path=path)
            devices = backend.get_devices()

            assert "spine-01" in devices
            assert "leaf-01" in devices
            assert devices["spine-01"].host == "10.0.0.1"
            assert devices["spine-01"].username == "testuser"
            assert devices["spine-01"].password.get_secret_value() == "testpass"
            assert devices["leaf-01"].host == "10.0.1.1"
        finally:
            os.unlink(path)

    def test_load_with_platform(self):
        path = self._write_yaml("""
devices:
  eos-sw:
    host: 10.0.0.1
    platform: eos
  junos-sw:
    host: 10.0.0.2
    platform: junos
  default-sw:
    host: 10.0.0.3
""")
        try:
            backend = YamlInventoryBackend(path=path)
            devices = backend.get_devices()

            assert devices["eos-sw"].platform == "eos"
            assert devices["junos-sw"].platform == "junos"
            assert devices["default-sw"].platform == "eos"  # default
        finally:
            os.unlink(path)

    def test_load_with_metadata(self):
        path = self._write_yaml("""
devices:
  spine-01:
    host: 10.0.0.1
    role: spine
    groups:
      - dc1
      - spines
    tags:
      - production
    mlag_peer: spine-02
""")
        try:
            backend = YamlInventoryBackend(path=path)
            device = backend.get_device("spine-01")

            assert device is not None
            assert device.role == "spine"
            assert device.groups == ["dc1", "spines"]
            assert device.tags == ["production"]
            assert device.mlag_peer == "spine-02"
        finally:
            os.unlink(path)

    def test_get_device_found(self):
        path = self._write_yaml("devices:\n  sw1:\n    host: 10.0.0.1\n")
        try:
            backend = YamlInventoryBackend(path=path)
            device = backend.get_device("sw1")
            assert device is not None
            assert device.host == "10.0.0.1"
        finally:
            os.unlink(path)

    def test_get_device_not_found(self):
        path = self._write_yaml("devices:\n  sw1:\n    host: 10.0.0.1\n")
        try:
            backend = YamlInventoryBackend(path=path)
            assert backend.get_device("nonexistent") is None
        finally:
            os.unlink(path)

    def test_default_credentials(self):
        path = self._write_yaml("devices:\n  sw1:\n    host: 10.0.0.1\n")
        try:
            backend = YamlInventoryBackend(
                path=path,
                default_username="myuser",
                default_password="mypass",
                default_transport="http",
            )
            device = backend.get_device("sw1")
            assert device is not None
            assert device.username == "myuser"
            assert device.password.get_secret_value() == "mypass"
            assert device.transport == "http"
        finally:
            os.unlink(path)

    def test_empty_file_raises(self):
        path = self._write_yaml("")
        try:
            with pytest.raises(ValueError, match="empty"):
                YamlInventoryBackend(path=path)
        finally:
            os.unlink(path)

    def test_no_devices_key_raises(self):
        path = self._write_yaml("other_key: true\n")
        try:
            with pytest.raises(ValueError, match="devices"):
                YamlInventoryBackend(path=path)
        finally:
            os.unlink(path)

    def test_nonexistent_file_raises(self):
        with pytest.raises(ValueError, match="Failed to load"):
            YamlInventoryBackend(path="/nonexistent/path.yaml")

    def test_skips_malformed_entries(self):
        path = self._write_yaml("""
devices:
  good-switch:
    host: 10.0.0.1
  bad-switch: "not a dict"
""")
        try:
            backend = YamlInventoryBackend(path=path)
            devices = backend.get_devices()
            assert "good-switch" in devices
            assert "bad-switch" not in devices
        finally:
            os.unlink(path)

    def test_missing_host_raises(self):
        """Missing 'host' field raises ValueError."""
        path = self._write_yaml("devices:\n  myswitch: {}\n")
        try:
            with pytest.raises(ValueError, match="missing required 'host' field"):
                YamlInventoryBackend(path=path)
        finally:
            os.unlink(path)

    def test_unknown_platform_raises(self):
        """Unknown platform raises ValueError instead of defaulting."""
        path = self._write_yaml("devices:\n  sw1:\n    host: 10.0.0.1\n    platform: netbsd\n")
        try:
            with pytest.raises(ValueError, match="unknown platform 'netbsd'"):
                YamlInventoryBackend(path=path)
        finally:
            os.unlink(path)

    def test_duplicate_host_warns(self, caplog):
        """Two devices sharing the same host IP logs a warning."""
        import logging

        path = self._write_yaml("devices:\n  sw1:\n    host: 10.0.0.1\n  sw2:\n    host: 10.0.0.1\n")
        try:
            with caplog.at_level(logging.WARNING, logger="network-mcp"):
                backend = YamlInventoryBackend(path=path)
                devices = backend.get_devices()
            assert len(devices) == 2
            assert "Duplicate host '10.0.0.1'" in caplog.text
        finally:
            os.unlink(path)

    def test_valid_inventory_passes(self):
        """A fully valid inventory loads without errors."""
        path = self._write_yaml(
            "devices:\n"
            "  spine-01:\n"
            "    host: 10.0.0.1\n"
            "    platform: eos\n"
            "  leaf-01:\n"
            "    host: 10.0.0.2\n"
            "    platform: iosxe\n"
        )
        try:
            backend = YamlInventoryBackend(path=path)
            devices = backend.get_devices()
            assert len(devices) == 2
            assert devices["spine-01"].platform == "eos"
            assert devices["leaf-01"].platform == "iosxe"
        finally:
            os.unlink(path)
