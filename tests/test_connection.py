"""Tests for the ConnectionManager."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from network_mcp.connection import (
    DEFAULT_MAX_CONNECTIONS,
    ConnectionManager,
    DeviceCredentials,
    _calculate_max_connections,
)
from tests.conftest import make_test_settings


def test_device_credentials_defaults():
    creds = DeviceCredentials(host="10.0.0.1")
    assert creds.username == "admin"
    assert creds.password.get_secret_value() == ""
    assert creds.transport == "https"
    assert creds.port == 443
    assert creds.platform == "eos"


def test_device_credentials_platform():
    creds = DeviceCredentials(host="10.0.0.1", platform="junos")
    assert creds.platform == "junos"


def test_device_credentials_secret_str():
    """SecretStr masks password in repr/str."""
    creds = DeviceCredentials(host="10.0.0.1", password=SecretStr("mypassword"))
    assert creds.password.get_secret_value() == "mypassword"
    assert "mypassword" not in repr(creds)
    assert "mypassword" not in str(creds.password)


def test_connection_manager_init():
    mgr = ConnectionManager(settings=make_test_settings())
    assert mgr._drivers == {}
    assert mgr._inventory == {}


def test_load_inventory():
    yaml_content = """
devices:
  spine-01:
    host: 10.0.0.1
    username: testuser
    password: testpass
    transport: https
    port: 443
  leaf-01:
    host: 10.0.1.1
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            mgr = ConnectionManager(settings=make_test_settings())
            mgr.load_inventory(f.name)

            assert "spine-01" in mgr._inventory
            assert "leaf-01" in mgr._inventory
            assert mgr._inventory["spine-01"].host == "10.0.0.1"
            assert mgr._inventory["spine-01"].username == "testuser"
            assert mgr._inventory["spine-01"].password.get_secret_value() == "testpass"
            assert mgr._inventory["leaf-01"].host == "10.0.1.1"
        finally:
            os.unlink(f.name)


def test_load_inventory_with_platform():
    yaml_content = """
devices:
  spine-01:
    host: 10.0.0.1
    platform: eos
  junos-01:
    host: 10.0.0.2
    platform: junos
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            mgr = ConnectionManager(settings=make_test_settings())
            mgr.load_inventory(f.name)

            assert mgr._inventory["spine-01"].platform == "eos"
            assert mgr._inventory["junos-01"].platform == "junos"
        finally:
            os.unlink(f.name)


@patch("network_mcp.driver.pyeapi.connect")
def test_get_driver_direct(mock_connect):
    mock_pyeapi_node = MagicMock()
    mock_connect.return_value = mock_pyeapi_node

    mgr = ConnectionManager(settings=make_test_settings())
    driver = mgr.get_driver("10.0.0.1")

    assert driver is not None
    assert driver.is_connected()
    mock_connect.assert_called_once()


@patch("network_mcp.driver.pyeapi.connect")
def test_get_node_alias(mock_connect):
    """get_node is an alias for get_driver."""
    mock_pyeapi_node = MagicMock()
    mock_connect.return_value = mock_pyeapi_node

    mgr = ConnectionManager(settings=make_test_settings())
    driver = mgr.get_node("10.0.0.1")

    assert driver is not None
    assert driver.is_connected()
    mock_connect.assert_called_once()


@patch("network_mcp.driver.pyeapi.connect")
def test_get_driver_cached(mock_connect):
    mock_pyeapi_node = MagicMock()
    mock_connect.return_value = mock_pyeapi_node

    mgr = ConnectionManager(settings=make_test_settings())
    driver1 = mgr.get_driver("10.0.0.1")
    driver2 = mgr.get_driver("10.0.0.1")

    assert driver1 is driver2
    # Only one connection should be made (cached)
    mock_connect.assert_called_once()


@patch("network_mcp.driver.pyeapi.connect")
def test_get_driver_from_inventory(mock_connect):
    mock_pyeapi_node = MagicMock()
    mock_connect.return_value = mock_pyeapi_node

    mgr = ConnectionManager(settings=make_test_settings())
    mgr._inventory["spine-01"] = DeviceCredentials(
        host="10.0.0.1",
        username="admin",
        password=SecretStr("secret"),
        transport="https",
        port=443,
    )

    driver = mgr.get_driver("spine-01")

    assert driver is not None
    mock_connect.assert_called_once_with(
        host="10.0.0.1",
        transport="https",
        username="admin",
        password="secret",
        return_node=True,
        port=443,
        timeout=10.0,
        verify_ssl=True,
    )


@patch("network_mcp.driver.pyeapi.connect")
def test_get_driver_passes_timeout(mock_connect):
    mock_pyeapi_node = MagicMock()
    mock_connect.return_value = mock_pyeapi_node

    mgr = ConnectionManager(settings=make_test_settings(timeout_connect=30.0))
    mgr.get_driver("10.0.0.1")

    call_kwargs = mock_connect.call_args[1]
    assert call_kwargs["timeout"] == 30.0


def test_close_all():
    mgr = ConnectionManager(settings=make_test_settings())
    mock_driver1 = MagicMock()
    mock_driver2 = MagicMock()
    mgr._drivers["host1"] = mock_driver1
    mgr._drivers["host2"] = mock_driver2
    mgr.close_all()
    assert mgr._drivers == {}
    mock_driver1.close.assert_called_once()
    mock_driver2.close.assert_called_once()


def test_list_devices():
    mgr = ConnectionManager(settings=make_test_settings())
    mgr._inventory["spine-01"] = DeviceCredentials(host="10.0.0.1")
    mgr._inventory["leaf-01"] = DeviceCredentials(host="10.0.1.1")

    devices = mgr.list_devices()

    assert set(devices) == {"spine-01", "leaf-01"}


def test_list_devices_empty():
    mgr = ConnectionManager(settings=make_test_settings())

    assert mgr.list_devices() == []


def test_get_devices_by_group():
    mgr = ConnectionManager(settings=make_test_settings())
    mgr._inventory["spine-01"] = DeviceCredentials(host="10.0.0.1", groups=["dc1", "spines"])
    mgr._inventory["leaf-01"] = DeviceCredentials(host="10.0.1.1", groups=["dc1", "leaves"])
    mgr._inventory["leaf-02"] = DeviceCredentials(host="10.0.1.2", groups=["dc2", "leaves"])

    dc1 = mgr.get_devices_by_group("dc1")
    leaves = mgr.get_devices_by_group("leaves")
    missing = mgr.get_devices_by_group("nonexistent")

    assert set(dc1) == {"spine-01", "leaf-01"}
    assert set(leaves) == {"leaf-01", "leaf-02"}
    assert missing == []


def test_get_devices_by_tag():
    mgr = ConnectionManager(settings=make_test_settings())
    mgr._inventory["spine-01"] = DeviceCredentials(host="10.0.0.1", tags=["production", "core"])
    mgr._inventory["leaf-01"] = DeviceCredentials(host="10.0.1.1", tags=["production"])
    mgr._inventory["lab-01"] = DeviceCredentials(host="10.0.2.1", tags=["lab"])

    prod = mgr.get_devices_by_tag("production")
    lab = mgr.get_devices_by_tag("lab")

    assert set(prod) == {"spine-01", "leaf-01"}
    assert lab == ["lab-01"]


def test_get_devices_by_role():
    mgr = ConnectionManager(settings=make_test_settings())
    mgr._inventory["spine-01"] = DeviceCredentials(host="10.0.0.1", role="spine")
    mgr._inventory["spine-02"] = DeviceCredentials(host="10.0.0.2", role="spine")
    mgr._inventory["leaf-01"] = DeviceCredentials(host="10.0.1.1", role="leaf")

    spines = mgr.get_devices_by_role("spine")
    leaves = mgr.get_devices_by_role("leaf")
    missing = mgr.get_devices_by_role("border")

    assert set(spines) == {"spine-01", "spine-02"}
    assert leaves == ["leaf-01"]
    assert missing == []


@patch("network_mcp.driver.pyeapi.connect")
def test_remove_node(mock_connect):
    mock_pyeapi_node = MagicMock()
    mock_connect.return_value = mock_pyeapi_node

    mgr = ConnectionManager(settings=make_test_settings())
    mgr.get_driver("10.0.0.1")
    assert "10.0.0.1" in mgr._drivers

    mgr.remove_node("10.0.0.1")
    assert "10.0.0.1" not in mgr._drivers


def test_remove_node_nonexistent():
    mgr = ConnectionManager(settings=make_test_settings())
    mgr.remove_node("nonexistent")  # Should not raise


@patch("network_mcp.driver.pyeapi.connect")
def test_eviction_at_max_connections(mock_connect):
    mock_connect.return_value = MagicMock()

    mgr = ConnectionManager(settings=make_test_settings(), max_connections=2)
    mgr.get_driver("host1")
    mgr.get_driver("host2")
    assert len(mgr._drivers) == 2

    mgr.get_driver("host3")
    assert len(mgr._drivers) == 2
    assert "host1" not in mgr._drivers
    assert "host3" in mgr._drivers


def test_load_inventory_empty_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("")
        f.flush()
        try:
            mgr = ConnectionManager(settings=make_test_settings())
            import pytest

            with pytest.raises(ValueError, match="empty"):
                mgr.load_inventory(f.name)
        finally:
            os.unlink(f.name)


def test_load_inventory_no_devices_key():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("something_else: true\n")
        f.flush()
        try:
            mgr = ConnectionManager(settings=make_test_settings())
            import pytest

            with pytest.raises(ValueError, match="devices"):
                mgr.load_inventory(f.name)
        finally:
            os.unlink(f.name)


def test_load_inventory_skips_malformed_entries():
    yaml_content = """
devices:
  good-switch:
    host: 10.0.0.1
  bad-switch: "not a dict"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            mgr = ConnectionManager(settings=make_test_settings())
            mgr.load_inventory(f.name)
            assert "good-switch" in mgr._inventory
            assert "bad-switch" not in mgr._inventory
        finally:
            os.unlink(f.name)


# --- Connection health check tests ---


@patch("network_mcp.driver.pyeapi.connect")
def test_verify_connection_on_get_driver(mock_connect):
    """verify=True runs 'show version' on first connection."""
    mock_node = MagicMock()
    mock_connect.return_value = mock_node

    mgr = ConnectionManager(settings=make_test_settings())
    driver = mgr.get_driver("10.0.0.1", verify=True)

    assert driver is not None
    # EosDriver.run_show delegates to _node.run_commands
    mock_node.run_commands.assert_called_once_with(["show version"], encoding="json")
    assert "10.0.0.1" in mgr._verified


@patch("network_mcp.driver.pyeapi.connect")
def test_verify_cached_on_second_call(mock_connect):
    """Verification only runs once per host (cached in _verified set)."""
    mock_node = MagicMock()
    mock_connect.return_value = mock_node

    mgr = ConnectionManager(settings=make_test_settings())
    mgr.get_driver("10.0.0.1", verify=True)
    mgr.get_driver("10.0.0.1", verify=True)

    # Verification runs only once — EosDriver.run_show delegates to _node.run_commands
    mock_node.run_commands.assert_called_once_with(["show version"], encoding="json")


@patch("network_mcp.driver.pyeapi.connect")
def test_verify_failure_removes_driver(mock_connect):
    """If verification fails, the driver is removed from cache."""
    mock_node = MagicMock()
    mock_connect.return_value = mock_node

    mgr = ConnectionManager(settings=make_test_settings())
    # Make the driver's run_show raise on the mock created by EosDriver
    # We need to patch at the driver level after creation
    real_driver = mgr._create_driver(DeviceCredentials(host="10.0.0.1"))
    real_driver.run_show = MagicMock(side_effect=Exception("eAPI unreachable"))

    # Manually insert the broken driver
    mgr._drivers["10.0.0.1"] = real_driver

    with pytest.raises(ConnectionError, match="Connection verification failed"):
        mgr.get_driver("10.0.0.1", verify=True)

    assert "10.0.0.1" not in mgr._drivers
    assert "10.0.0.1" not in mgr._verified


@patch("network_mcp.driver.pyeapi.connect")
def test_auto_verify_from_settings(mock_connect):
    """NET_VERIFY_CONNECTIONS=true triggers automatic verification."""
    mock_node = MagicMock()
    mock_connect.return_value = mock_node

    mgr = ConnectionManager(settings=make_test_settings(net_verify_connections=True))
    mgr.get_driver("10.0.0.1")

    mock_node.run_commands.assert_called_once_with(["show version"], encoding="json")
    assert "10.0.0.1" in mgr._verified


@patch("network_mcp.driver.pyeapi.connect")
def test_no_verify_by_default(mock_connect):
    """Without verify=True or NET_VERIFY_CONNECTIONS, no verification runs."""
    mock_node = MagicMock()
    mock_connect.return_value = mock_node

    mgr = ConnectionManager(settings=make_test_settings())
    mgr.get_driver("10.0.0.1")

    mock_node.run_commands.assert_not_called()
    assert "10.0.0.1" not in mgr._verified


@patch("network_mcp.driver.pyeapi.connect")
def test_verify_explicit_false_overrides_auto(mock_connect):
    """verify=False overrides NET_VERIFY_CONNECTIONS=true."""
    mock_node = MagicMock()
    mock_connect.return_value = mock_node

    mgr = ConnectionManager(settings=make_test_settings(net_verify_connections=True))
    mgr.get_driver("10.0.0.1", verify=False)

    mock_node.run_commands.assert_not_called()


def test_remove_node_clears_verified():
    """remove_node should also clear the verified cache for that host."""
    mgr = ConnectionManager(settings=make_test_settings())
    mock_driver = MagicMock()
    mgr._drivers["host1"] = mock_driver
    mgr._verified.add("host1")

    mgr.remove_node("host1")

    assert "host1" not in mgr._verified


def test_close_all_clears_verified():
    """close_all should clear the entire verified set."""
    mgr = ConnectionManager(settings=make_test_settings())
    mock_driver = MagicMock()
    mgr._drivers["host1"] = mock_driver
    mgr._verified.add("host1")
    mgr._verified.add("host2")

    mgr.close_all()

    assert mgr._verified == set()


# --- Auto-tuning tests ---


def test_calculate_max_connections_10_devices():
    """10 devices → max 20 connections."""
    assert _calculate_max_connections(10) == 20


def test_calculate_max_connections_100_devices():
    """100 devices → max 200 connections."""
    assert _calculate_max_connections(100) == 200


def test_calculate_max_connections_500_devices_capped():
    """500 devices → capped at 500 (not 1000)."""
    assert _calculate_max_connections(500) == 500


def test_calculate_max_connections_1000_devices_capped():
    """1000 devices → still capped at 500."""
    assert _calculate_max_connections(1000) == 500


def test_calculate_max_connections_zero_devices():
    """0 devices → fallback to DEFAULT_MAX_CONNECTIONS."""
    assert _calculate_max_connections(0) == DEFAULT_MAX_CONNECTIONS


def test_calculate_max_connections_negative():
    """Negative inventory size → fallback to DEFAULT_MAX_CONNECTIONS."""
    assert _calculate_max_connections(-1) == DEFAULT_MAX_CONNECTIONS


def test_auto_tuning_with_inventory():
    """Auto-tuning applies when inventory is loaded and no explicit max set."""
    settings = make_test_settings()
    mgr = ConnectionManager(settings=settings)
    # Manually set inventory and re-run auto-tuning path
    # ConnectionManager auto-tunes in __init__ when inventory_backend is provided
    assert mgr._max_connections == DEFAULT_MAX_CONNECTIONS  # no inventory → default

    # Now create with inventory
    inventory = {f"device-{i}": DeviceCredentials(host=f"10.0.0.{i}", password=SecretStr("test")) for i in range(50)}
    mgr._inventory = inventory
    # Re-create to trigger auto-tuning
    from unittest.mock import MagicMock as MM

    mock_backend = MM()
    mock_backend.get_devices.return_value = inventory
    mgr2 = ConnectionManager(settings=settings, inventory_backend=mock_backend)
    assert mgr2._max_connections == 100  # min(50 * 2, 500) = 100


def test_explicit_max_connections_overrides_auto_tuning():
    """Explicit MAX_CONNECTIONS env var overrides auto-tuning."""
    settings = make_test_settings()
    settings.max_connections = 42  # type: ignore[attr-defined]

    inventory = {f"device-{i}": DeviceCredentials(host=f"10.0.0.{i}", password=SecretStr("test")) for i in range(100)}
    mock_backend = MagicMock()
    mock_backend.get_devices.return_value = inventory
    mgr = ConnectionManager(settings=settings, inventory_backend=mock_backend)

    # Should use explicit value (42), not auto-calculated (200)
    assert mgr._max_connections == 42


def test_explicit_init_param_overrides_default():
    """Explicit max_connections parameter to __init__ overrides default."""
    settings = make_test_settings()
    mgr = ConnectionManager(settings=settings, max_connections=75)
    assert mgr._max_connections == 75


# --- Enhanced pool_stats tests ---


def test_pool_stats_empty_pool():
    """Stats work correctly with an empty pool."""
    mgr = ConnectionManager(settings=make_test_settings())
    stats = mgr.pool_stats()

    assert stats["active"] == 0
    assert stats["idle"] == 0
    assert stats["pooled"] == 0
    assert stats["max"] == DEFAULT_MAX_CONNECTIONS
    assert stats["total_created"] == 0
    assert stats["total_checkouts"] == 0
    assert stats["avg_checkout_ms"] == 0.0
    assert stats["per_device"] == {}


def test_pool_stats_reflects_pool_state():
    """Stats reflect actual pool state after adding connections."""
    mgr = ConnectionManager(settings=make_test_settings())
    mock_driver1 = MagicMock()
    mock_driver2 = MagicMock()

    mgr._drivers["host1"] = mock_driver1
    mgr._drivers["host2"] = mock_driver2
    mgr._refcounts["host1"] = 1  # active

    stats = mgr.pool_stats()

    assert stats["active"] == 1
    assert stats["idle"] == 1
    assert stats["pooled"] == 2
    assert "host1" in stats["per_device"]
    assert "host2" in stats["per_device"]
    assert stats["per_device"]["host1"]["refcount"] == 1
    assert stats["per_device"]["host2"]["refcount"] == 0


@patch("network_mcp.driver.pyeapi.connect")
def test_pool_stats_tracks_created_connections(mock_connect):
    """total_created increments when new connections are created."""
    mock_connect.return_value = MagicMock()

    mgr = ConnectionManager(settings=make_test_settings())
    assert mgr.pool_stats()["total_created"] == 0

    mgr.get_driver("host1")
    assert mgr.pool_stats()["total_created"] == 1

    mgr.get_driver("host2")
    assert mgr.pool_stats()["total_created"] == 2

    # Cached connection does not increment
    mgr.get_driver("host1")
    assert mgr.pool_stats()["total_created"] == 2


@patch("network_mcp.driver.pyeapi.connect")
def test_pool_stats_tracks_checkout_metrics(mock_connect):
    """acquire() tracks checkout count and average time."""
    mock_connect.return_value = MagicMock()

    mgr = ConnectionManager(settings=make_test_settings())

    with mgr.acquire("host1"):
        pass

    stats = mgr.pool_stats()
    assert stats["total_checkouts"] == 1
    assert stats["avg_checkout_ms"] >= 0
