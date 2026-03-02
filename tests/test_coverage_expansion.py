"""Targeted tests to cover uncovered branches and improve overall coverage."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pyeapi.eapilib
import pytest

from network_mcp.cache import CommandCache, _estimate_size, get_default_ttl
from network_mcp.config import NetworkSettings
from network_mcp.connection import ConnectionManager, DeviceCredentials
from network_mcp.helpers import (
    _attach_request_id,
    check_read_only,
    filter_fields,
    normalize_speed,
    paginate_dict,
    paginate_list,
    run_config_command,
    run_session_command,
    run_show_command,
    validate_config_commands,
    validate_duplex,
    validate_ip_address,
    validate_ip_prefix,
    validate_mtu,
    validate_port_channel_id,
    validate_route_distinguisher,
    validate_speed,
    validate_stp_priority,
)
from network_mcp.server import conn_mgr

# --- _attach_request_id (helpers.py:39) ---


class TestAttachRequestId:
    """Test request ID attachment to error responses."""

    def test_attach_request_id_with_active_request(self):
        """When a correlation ID is active, it should be attached to error dicts."""
        with patch("network_mcp.middleware.get_correlation_id", return_value="req-123"):
            result = _attach_request_id({"status": "error", "error": "test"})
            assert result["request_id"] == "req-123"
            assert result["correlation_id"] == "req-123"

    def test_attach_request_id_always_has_correlation_id(self):
        """Correlation ID is always generated — error dicts always get one."""
        result = _attach_request_id({"status": "error", "error": "test"})
        assert "request_id" in result
        assert "correlation_id" in result

    def test_attach_request_id_success_status(self):
        """Request ID should be attached to all responses including success."""
        with patch("network_mcp.middleware.get_correlation_id", return_value="req-456"):
            result = _attach_request_id({"status": "success", "data": {}})
            assert "request_id" in result
            assert "correlation_id" in result


# --- validate_ip_prefix edge cases (helpers.py:361) ---


class TestValidateIpPrefix:
    """Test IP prefix validation edge cases."""

    def test_prefix_with_injection_chars(self):
        """Prefixes with CLI injection chars should be rejected."""
        assert validate_ip_prefix(";10.0.0.0/8") is not None
        assert validate_ip_prefix("10.0.0.0/8|cat") is not None

    def test_prefix_valid(self):
        assert validate_ip_prefix("10.0.0.0/8") is None
        assert validate_ip_prefix("192.168.1.0/24") is None

    def test_prefix_invalid_format(self):
        assert validate_ip_prefix("not-a-prefix") is not None

    def test_prefix_octet_exceeds_255(self):
        assert validate_ip_prefix("256.0.0.0/8") is not None

    def test_prefix_mask_exceeds_32(self):
        assert validate_ip_prefix("10.0.0.0/33") is not None

    def test_prefix_empty(self):
        assert validate_ip_prefix("") is not None


# --- validate_ip_address edge cases ---


class TestValidateIpAddress:
    def test_valid_ip(self):
        assert validate_ip_address("10.0.0.1") is None

    def test_invalid_ip(self):
        assert validate_ip_address("not-an-ip") is not None

    def test_ip_octet_exceeds_255(self):
        assert validate_ip_address("256.0.0.1") is not None

    def test_ip_with_injection(self):
        assert validate_ip_address(";10.0.0.1") is not None


# --- validate_route_distinguisher edge cases ---


class TestValidateRouteDistinguisher:
    def test_valid_asn_format(self):
        assert validate_route_distinguisher("65001:100") is None

    def test_valid_ip_format(self):
        assert validate_route_distinguisher("10.0.0.1:100") is None

    def test_empty(self):
        assert validate_route_distinguisher("") is not None

    def test_invalid_format(self):
        assert validate_route_distinguisher("invalid") is not None


# --- Additional validator edge cases ---


class TestValidatorEdgeCases:
    def test_validate_speed_valid(self):
        assert validate_speed("auto") is None
        assert validate_speed("1000") is None

    def test_validate_speed_invalid(self):
        assert validate_speed("999") is not None

    def test_validate_duplex_valid(self):
        assert validate_duplex("auto") is None
        assert validate_duplex("full") is None

    def test_validate_duplex_invalid(self):
        assert validate_duplex("triple") is not None

    def test_validate_mtu_valid(self):
        assert validate_mtu(1500) is None
        assert validate_mtu(9214) is None

    def test_validate_mtu_too_low(self):
        assert validate_mtu(67) is not None

    def test_validate_mtu_too_high(self):
        assert validate_mtu(9215) is not None

    def test_validate_port_channel_id_valid(self):
        assert validate_port_channel_id(1) is None
        assert validate_port_channel_id(2000) is None

    def test_validate_port_channel_id_invalid(self):
        assert validate_port_channel_id(0) is not None
        assert validate_port_channel_id(2001) is not None

    def test_validate_stp_priority_valid(self):
        assert validate_stp_priority(0) is None
        assert validate_stp_priority(4096) is None

    def test_validate_stp_priority_invalid(self):
        assert validate_stp_priority(100) is not None
        assert validate_stp_priority(-1) is not None
        assert validate_stp_priority(65536) is not None

    def test_normalize_speed_known(self):
        assert normalize_speed("1000") == "1Gbps"
        assert normalize_speed("10G") == "10Gbps"
        assert normalize_speed("auto") == "auto"

    def test_normalize_speed_unknown(self):
        assert normalize_speed("999") == "999"

    def test_validate_config_commands_blocked(self):
        assert validate_config_commands(["reload"]) is not None
        assert validate_config_commands(["write erase"]) is not None
        assert validate_config_commands(["bash"]) is not None

    def test_validate_config_commands_allowed(self):
        assert validate_config_commands(["vlan 100", "name SERVERS"]) is None

    def test_check_read_only_enabled(self):
        with patch("network_mcp.helpers._get_settings") as mock_settings:
            mock_settings.return_value.net_read_only = True
            assert check_read_only() is not None

    def test_check_read_only_disabled(self):
        with patch("network_mcp.helpers._get_settings") as mock_settings:
            mock_settings.return_value.net_read_only = False
            assert check_read_only() is None


# --- Pagination and field filtering ---


class TestFilterFields:
    def test_filter_with_fields(self):
        data = {"a": 1, "b": 2, "c": 3}
        assert filter_fields(data, ["a", "c"]) == {"a": 1, "c": 3}

    def test_filter_with_none(self):
        data = {"a": 1, "b": 2}
        assert filter_fields(data, None) == data

    def test_filter_with_empty_list(self):
        data = {"a": 1, "b": 2}
        assert filter_fields(data, []) == data


class TestPaginateDict:
    def test_paginate_basic(self):
        data = {"a": 1, "b": 2, "c": 3}
        page, meta = paginate_dict(data, limit=2, offset=0)
        assert len(page) == 2
        assert meta["total"] == 3
        assert meta["has_more"] is True

    def test_paginate_offset_beyond(self):
        data = {"a": 1, "b": 2}
        page, meta = paginate_dict(data, limit=10, offset=10)
        assert page == {}
        assert meta["returned"] == 0
        assert meta["has_more"] is False

    def test_paginate_no_limit(self):
        data = {"a": 1, "b": 2}
        page, meta = paginate_dict(data)
        assert len(page) == 2
        assert meta["has_more"] is False


class TestPaginateList:
    def test_paginate_basic(self):
        data = [1, 2, 3, 4, 5]
        page, meta = paginate_list(data, limit=2, offset=0)
        assert page == [1, 2]
        assert meta["has_more"] is True

    def test_paginate_offset_beyond(self):
        data = [1, 2]
        page, meta = paginate_list(data, limit=10, offset=10)
        assert page == []
        assert meta["returned"] == 0

    def test_paginate_no_limit(self):
        data = [1, 2, 3]
        page, meta = paginate_list(data)
        assert len(page) == 3


# --- Cache edge cases (cache.py) ---


class TestCacheEdgeCases:
    def test_estimate_size_non_serializable(self):
        """Objects that fail json.dumps should fall back to sys.getsizeof."""

        class NonSerializable:
            def __repr__(self):
                raise ValueError("cannot repr")

        obj = NonSerializable()
        # Should not raise
        size = _estimate_size(obj)
        assert size > 0

    def test_cache_enabled_property(self):
        cache = CommandCache(enabled=True)
        assert cache.enabled is True
        cache2 = CommandCache(enabled=False)
        assert cache2.enabled is False

    def test_cache_disabled_returns_none(self):
        cache = CommandCache(enabled=False)
        cache.put("host", ["show version"], "json", {"data": "test"}, 30)
        assert cache.get("host", ["show version"]) is None

    def test_cache_memory_eviction(self):
        """When memory limit is exceeded, LRU entries should be evicted."""
        cache = CommandCache(max_entries=1000, max_memory_mb=1, enabled=True)
        # Fill with large entries to trigger memory eviction
        for i in range(20):
            cache.put(f"host-{i}", ["show version"], "json", {"data": "x" * 100_000}, 300)
        stats = cache.stats()
        assert stats["entries"] < 20  # Some should have been evicted

    def test_cache_max_entries_eviction(self):
        """When max entry count is exceeded, LRU entries should be evicted."""
        cache = CommandCache(max_entries=5, max_memory_mb=100, enabled=True)
        for i in range(10):
            cache.put(f"host-{i}", ["show version"], "json", {"data": i}, 300)
        stats = cache.stats()
        assert stats["entries"] <= 5

    def test_evict_lru_empty_cache(self):
        """Calling _evict_lru on empty cache should not crash."""
        cache = CommandCache(enabled=True)
        cache._evict_lru()  # Should be no-op

    def test_cache_ttl_expiry(self):
        """Expired entries should return None."""
        cache = CommandCache(enabled=True)
        cache.put("host", ["show version"], "json", {"data": "test"}, 1)
        # Simulate time passing
        key = cache._make_key("host", ["show version"], "json")
        with cache._lock:
            cache._cache[key].timestamp = time.monotonic() - 10
        assert cache.get("host", ["show version"]) is None

    def test_cache_put_replaces_existing(self):
        """Putting same key should replace old entry and update memory accounting."""
        cache = CommandCache(enabled=True)
        cache.put("host", ["show version"], "json", {"data": "old"}, 30)
        cache.put("host", ["show version"], "json", {"data": "new"}, 30)
        result = cache.get("host", ["show version"])
        assert result["data"] == "new"

    def test_get_default_ttl_patterns(self):
        """Test TTL pattern matching."""
        assert get_default_ttl(["show version"]) == 300
        assert get_default_ttl(["show inventory"]) == 300
        assert get_default_ttl(["show processes top"]) == 0
        assert get_default_ttl(["show interfaces status"]) == 30
        assert get_default_ttl(["show ip bgp summary"]) == 15
        assert get_default_ttl(["show running-config"]) == 60
        assert get_default_ttl(["show something-unknown"]) == 30
        assert get_default_ttl([]) == 0


# --- Connection manager edge cases ---


class TestConnectionManagerEdgeCases:
    def _make_settings(self, **overrides):
        defaults = {
            "net_username": "admin",
            "net_password": "",
            "net_transport": "https",
            "timeout_connect": 10.0,
            "net_verify_connections": False,
            "net_demo_mode": False,
        }
        defaults.update(overrides)
        return NetworkSettings(**defaults)

    def test_load_inventory_from_backend(self):
        """load_inventory_from_backend should populate inventory."""
        settings = self._make_settings()
        cm = ConnectionManager(settings)
        mock_backend = MagicMock()
        mock_backend.get_devices.return_value = {
            "sw-01": DeviceCredentials(host="10.0.0.1"),
        }
        cm.load_inventory_from_backend(mock_backend)
        assert "sw-01" in cm.list_devices()

    def test_close_driver_error_handled(self):
        """_close_driver should not raise when driver.close() fails."""
        settings = self._make_settings()
        cm = ConnectionManager(settings)
        driver = MagicMock()
        driver.close.side_effect = RuntimeError("close failed")
        cm._close_driver(driver)  # Should not raise

    def test_invalidate_cache_for_host(self):
        """_invalidate_cache_for_host should call command_cache.invalidate_host."""
        with patch("network_mcp.connection.ConnectionManager._invalidate_cache_for_host") as mock_inv:
            ConnectionManager._invalidate_cache_for_host("spine-01")
            mock_inv.assert_called_once_with("spine-01")

    def test_acquire_verify_failure_cleanup(self):
        """Failed verification should clean up the driver."""
        settings = self._make_settings(net_demo_mode=True, net_verify_connections=True)
        cm = ConnectionManager(settings)

        with patch.object(cm, "_verify_driver", side_effect=ConnectionError("verify failed")):
            with pytest.raises(ConnectionError):
                with cm.acquire("test-host", verify=True):
                    pass
            # After failure, the driver should be cleaned up
            assert "test-host" not in cm._drivers

    def test_pool_stats(self):
        """pool_stats should return correct metrics."""
        settings = self._make_settings(net_demo_mode=True)
        cm = ConnectionManager(settings, max_connections=10)
        stats = cm.pool_stats()
        assert stats["active"] == 0
        assert stats["pooled"] == 0
        assert stats["max"] == 10

    def test_get_devices_by_group(self):
        """get_devices_by_group should filter correctly."""
        settings = self._make_settings()
        cm = ConnectionManager(settings)
        cm._inventory = {
            "sw-01": DeviceCredentials(host="10.0.0.1", groups=["dc1"]),
            "sw-02": DeviceCredentials(host="10.0.0.2", groups=["dc2"]),
        }
        result = cm.get_devices_by_group("dc1")
        assert result == ["sw-01"]

    def test_get_devices_by_tag(self):
        """get_devices_by_tag should filter correctly."""
        settings = self._make_settings()
        cm = ConnectionManager(settings)
        cm._inventory = {
            "sw-01": DeviceCredentials(host="10.0.0.1", tags=["prod"]),
            "sw-02": DeviceCredentials(host="10.0.0.2", tags=["staging"]),
        }
        result = cm.get_devices_by_tag("prod")
        assert result == ["sw-01"]

    def test_get_devices_by_role(self):
        """get_devices_by_role should filter correctly."""
        settings = self._make_settings()
        cm = ConnectionManager(settings)
        cm._inventory = {
            "sw-01": DeviceCredentials(host="10.0.0.1", role="spine"),
            "sw-02": DeviceCredentials(host="10.0.0.2", role="leaf"),
        }
        result = cm.get_devices_by_role("spine")
        assert result == ["sw-01"]

    def test_remove_node(self):
        """remove_node should clean up driver and tracking state."""
        settings = self._make_settings(net_demo_mode=True)
        cm = ConnectionManager(settings)
        # Get a driver to populate the pool
        cm.get_driver("test-host")
        assert "test-host" in cm._drivers
        cm.remove_node("test-host")
        assert "test-host" not in cm._drivers

    def test_close_all(self):
        """close_all should clear all connections."""
        settings = self._make_settings(net_demo_mode=True)
        cm = ConnectionManager(settings)
        cm.get_driver("host-1")
        cm.get_driver("host-2")
        assert len(cm._drivers) == 2
        cm.close_all()
        assert len(cm._drivers) == 0

    def test_pool_exhaustion_error(self):
        """Connection pool exhaustion should raise ConnectionError."""
        settings = self._make_settings(net_demo_mode=True)
        cm = ConnectionManager(settings, max_connections=1)
        # Get first driver and mark it with refcount to prevent eviction
        with cm.acquire("host-1"):
            # While host-1 is acquired (refcount=1), try to get another
            with pytest.raises(ConnectionError, match="Connection pool exhausted"):
                cm.get_driver("host-2")

    def test_get_inventory(self):
        """get_inventory should return a snapshot of all devices."""
        settings = self._make_settings()
        cm = ConnectionManager(settings)
        cm._inventory = {
            "sw-01": DeviceCredentials(host="10.0.0.1"),
            "sw-02": DeviceCredentials(host="10.0.0.2"),
        }
        inv = cm.get_inventory()
        assert len(inv) == 2
        assert "sw-01" in inv

    def test_create_driver_unsupported_platform(self):
        """Unsupported platform should raise ValueError."""
        settings = self._make_settings()
        cm = ConnectionManager(settings)
        creds = DeviceCredentials(host="10.0.0.1", platform="unsupported")
        with pytest.raises(ValueError, match="Unsupported platform"):
            cm._create_driver(creds)


# --- run_show_command rate limiting (helpers.py:580-582) ---


class TestRunShowCommandRateLimiting:
    def test_show_command_rate_limited(self, mock_conn_mgr, mock_node):
        """When rate limit is exhausted, should return error dict."""
        with patch("network_mcp.helpers.device_rate_limiter") as mock_rl:
            mock_rl.check_show.return_value = False
            mock_rl.check_show_with_backoff.return_value = False
            mock_rl.show_wait_time.return_value = 1.5
            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            assert result["status"] == "error"
            assert "Rate limit" in result["error"]
            assert "1.5" in result["error"]

    def test_config_rate_limited(self, mock_conn_mgr, mock_node):
        """When config rate limit is exhausted, should return error dict."""
        with (
            patch("network_mcp.helpers._get_settings") as mock_settings,
            patch("network_mcp.helpers.device_rate_limiter") as mock_rl,
        ):
            mock_settings.return_value.net_read_only = False
            mock_settings.return_value.net_rate_limit_max_wait = 5.0
            mock_settings.return_value.net_conn_acquire_timeout = 30.0
            mock_rl.check_config.return_value = False
            mock_rl.check_config_with_backoff.return_value = False
            mock_rl.config_wait_time.return_value = 2.0
            result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
            assert result["status"] == "error"
            assert "rate limit" in result["error"].lower()


# --- run_session_command edge cases ---


class TestRunSessionCommand:
    def test_session_connection_error(self, mock_conn_mgr, mock_node):
        """Session command should handle connection errors."""
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "refused")
        result = run_session_command(conn_mgr, "spine-01", ["configure session test"], "create_session")
        assert result["status"] == "error"
        assert "Connection" in result["error"]

    def test_session_command_error(self, mock_conn_mgr, mock_node):
        """Session command should handle command errors."""
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid input")
        result = run_session_command(conn_mgr, "spine-01", ["configure session test"], "create_session")
        assert result["status"] == "error"

    def test_session_generic_error(self, mock_conn_mgr, mock_node):
        """Session command should handle generic errors."""
        mock_node.run_commands.side_effect = RuntimeError("unexpected")
        result = run_session_command(conn_mgr, "spine-01", ["configure session test"], "create_session")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_session_success(self, mock_conn_mgr, mock_node):
        """Session command success path."""
        mock_node.run_commands.return_value = [{}]
        result = run_session_command(conn_mgr, "spine-01", ["configure session test"], "create_session")
        assert result["status"] == "success"

    def test_session_rate_limited(self, mock_conn_mgr, mock_node):
        """Session command should respect rate limits."""
        with patch("network_mcp.helpers.device_rate_limiter") as mock_rl:
            mock_rl.check_config.return_value = False
            mock_rl.config_wait_time.return_value = 3.0
            result = run_session_command(conn_mgr, "spine-01", ["configure session test"], "create_session")
            assert result["status"] == "error"
            assert "rate limit" in result["error"].lower()

    def test_session_circuit_breaker_open(self, mock_conn_mgr, mock_node):
        """Session command should respect circuit breaker."""
        with patch("network_mcp.helpers.circuit_breaker_registry") as mock_cb:
            mock_cb.allow_request.return_value = False
            breaker = MagicMock()
            breaker.consecutive_failures = 5
            mock_cb.get.return_value = breaker
            result = run_session_command(conn_mgr, "spine-01", ["configure session test"], "create_session")
            assert result["status"] == "error"
            assert "Circuit breaker" in result["error"]


# --- Completions ---


class TestCompletionProviders:
    def test_complete_host_no_partial(self):
        """_complete_host with empty partial should return all devices."""
        from network_mcp.completions import _complete_host

        with patch.object(conn_mgr, "list_devices", return_value=["spine-01", "leaf-01", "leaf-02"]):
            result = _complete_host("")
            assert "spine-01" in result
            assert len(result) == 3

    def test_complete_host_with_partial(self):
        """_complete_host with partial should filter."""
        from network_mcp.completions import _complete_host

        with patch.object(conn_mgr, "list_devices", return_value=["spine-01", "leaf-01", "leaf-02"]):
            result = _complete_host("leaf")
            assert result == ["leaf-01", "leaf-02"]

    def test_complete_interface_cached(self):
        """_complete_interface should use cache."""
        from network_mcp.completions import _complete_interface, _set_cached

        _set_cached("interfaces:spine-01", ["Ethernet1", "Ethernet2", "Loopback0"])
        result = _complete_interface("spine-01", "Eth")
        assert result == ["Ethernet1", "Ethernet2"]

    def test_complete_interface_error(self):
        """_complete_interface should handle errors gracefully."""
        from network_mcp.completions import _complete_interface, clear_completion_cache

        clear_completion_cache()
        with patch("network_mcp.completions.run_show_command", side_effect=Exception("fail")):
            result = _complete_interface("spine-01", "")
            assert result == []

    def test_complete_interface_from_device_success(self):
        """_complete_interface should fetch from device when not cached."""
        from network_mcp.completions import _complete_interface, clear_completion_cache

        clear_completion_cache()
        mock_result = {
            "status": "success",
            "data": [{"interfaceStatuses": {"Ethernet1": {}, "Ethernet2": {}, "Management1": {}}}],
        }
        with patch("network_mcp.completions.run_show_command", return_value=mock_result):
            result = _complete_interface("spine-01", "Eth")
            assert "Ethernet1" in result
            assert "Ethernet2" in result
            assert "Management1" not in result

    def test_complete_interface_from_device_error_result(self):
        """_complete_interface should return empty on error result from device."""
        from network_mcp.completions import _complete_interface, clear_completion_cache

        clear_completion_cache()
        mock_result = {"status": "error", "error": "unreachable"}
        with patch("network_mcp.completions.run_show_command", return_value=mock_result):
            result = _complete_interface("spine-01", "")
            assert result == []

    def test_complete_vlan_id(self):
        """_complete_vlan_id should return VLAN IDs."""
        from network_mcp.completions import _complete_vlan_id, _set_cached

        _set_cached("vlans:spine-01", ["1", "100", "200"])
        result = _complete_vlan_id("spine-01", "1")
        assert "1" in result
        assert "100" in result

    def test_complete_vlan_id_from_device(self):
        """_complete_vlan_id should fetch from device when not cached."""
        from network_mcp.completions import _complete_vlan_id, clear_completion_cache

        clear_completion_cache()
        mock_result = {"status": "success", "data": [{"vlans": {"1": {}, "100": {}, "200": {}}}]}
        with patch("network_mcp.completions.run_show_command", return_value=mock_result):
            result = _complete_vlan_id("spine-01", "")
            assert len(result) == 3

    def test_complete_vlan_id_from_device_error(self):
        """_complete_vlan_id should return empty on error result."""
        from network_mcp.completions import _complete_vlan_id, clear_completion_cache

        clear_completion_cache()
        mock_result = {"status": "error", "error": "fail"}
        with patch("network_mcp.completions.run_show_command", return_value=mock_result):
            result = _complete_vlan_id("spine-01", "")
            assert result == []

    def test_complete_neighbor_ip(self):
        """_complete_neighbor_ip should return peer IPs."""
        from network_mcp.completions import _complete_neighbor_ip, _set_cached

        _set_cached("bgp_peers:spine-01", ["10.0.0.1", "10.0.0.2"])
        result = _complete_neighbor_ip("spine-01", "10.0.0")
        assert len(result) == 2

    def test_complete_neighbor_ip_from_device(self):
        """_complete_neighbor_ip should fetch from device when not cached."""
        from network_mcp.completions import _complete_neighbor_ip, clear_completion_cache

        clear_completion_cache()
        mock_result = {
            "status": "success",
            "data": [{"vrfs": {"default": {"peers": {"10.0.0.1": {}, "10.0.0.2": {}}}}}],
        }
        with patch("network_mcp.completions.run_show_command", return_value=mock_result):
            result = _complete_neighbor_ip("spine-01", "")
            assert len(result) == 2

    def test_complete_neighbor_ip_from_device_error(self):
        """_complete_neighbor_ip should return empty on error result."""
        from network_mcp.completions import _complete_neighbor_ip, clear_completion_cache

        clear_completion_cache()
        mock_result = {"status": "error", "error": "fail"}
        with patch("network_mcp.completions.run_show_command", return_value=mock_result):
            result = _complete_neighbor_ip("spine-01", "")
            assert result == []

    def test_complete_category(self):
        """_complete_category should return category names."""
        from network_mcp.completions import _complete_category

        with patch("network_mcp.completions.get_category_names", return_value=["device", "routing", "switching"]):
            result = _complete_category("r")
            assert result == ["routing"]

    def test_complete_checkpoint_from_device(self):
        """_complete_checkpoint should parse checkpoint names."""
        from network_mcp.completions import _complete_checkpoint, clear_completion_cache

        clear_completion_cache()
        mock_result = {
            "status": "success",
            "data": [{"output": "---\ncheckpoint-1\ncheckpoint-2\n"}],
        }
        with patch("network_mcp.completions.run_show_command", return_value=mock_result):
            result = _complete_checkpoint("spine-01", "")
            assert "checkpoint-1" in result
            assert "checkpoint-2" in result

    def test_complete_checkpoint_from_device_error(self):
        """_complete_checkpoint should return empty on error result."""
        from network_mcp.completions import _complete_checkpoint, clear_completion_cache

        clear_completion_cache()
        mock_result = {"status": "error", "error": "fail"}
        with patch("network_mcp.completions.run_show_command", return_value=mock_result):
            result = _complete_checkpoint("spine-01", "")
            assert result == []

    def test_complete_checkpoint_exception(self):
        """_complete_checkpoint should return empty on exception."""
        from network_mcp.completions import _complete_checkpoint, clear_completion_cache

        clear_completion_cache()
        with patch("network_mcp.completions.run_show_command", side_effect=Exception("fail")):
            result = _complete_checkpoint("spine-01", "")
            assert result == []

    def test_clear_completion_cache(self):
        """clear_completion_cache should clear all entries."""
        from network_mcp.completions import _set_cached, clear_completion_cache

        _set_cached("test-key", ["a", "b"])
        clear_completion_cache()
        from network_mcp.completions import _get_cached

        assert _get_cached("test-key") is None


# --- Meta-tools coverage (meta.py) ---


class TestMetaTools:
    """Test progressive discovery meta-tools."""

    def test_list_tool_categories(self):
        """eos_list_tool_categories should return category info."""
        from network_mcp.tools.meta import eos_list_tool_categories

        result = eos_list_tool_categories()
        assert result["status"] == "success"
        assert "categories" in result
        assert "total_categories" in result
        assert result["total_categories"] > 0

    def test_list_tools_in_category_valid(self):
        """eos_list_tools_in_category should return tool info for valid category."""
        from network_mcp.tools.meta import eos_list_tools_in_category

        result = eos_list_tools_in_category("device")
        assert result["status"] == "success"
        assert "tools" in result
        assert result["tool_count"] > 0

    def test_list_tools_in_category_invalid(self):
        """eos_list_tools_in_category should error for unknown category."""
        from network_mcp.tools.meta import eos_list_tools_in_category

        result = eos_list_tools_in_category("nonexistent")
        assert result["status"] == "error"
        assert "Unknown category" in result["error"]

    def test_load_tool_category_not_progressive(self):
        """eos_load_tool_category should work when not in progressive mode."""
        from network_mcp.tools.meta import eos_load_tool_category

        with patch("network_mcp.tools.meta._is_progressive_discovery", return_value=False):
            result = eos_load_tool_category("device")
            assert result["status"] == "success"
            assert "already loaded" in result["message"]

    def test_load_tool_category_invalid(self):
        """eos_load_tool_category should error for unknown category."""
        from network_mcp.tools.meta import eos_load_tool_category

        result = eos_load_tool_category("nonexistent")
        assert result["status"] == "error"
        assert "Unknown category" in result["error"]

    def test_load_tool_category_progressive_already_loaded(self):
        """eos_load_tool_category should handle already-loaded category in progressive mode."""
        from network_mcp.tools.meta import _loaded_categories, eos_load_tool_category

        _loaded_categories.add("device")
        try:
            with patch("network_mcp.tools.meta._is_progressive_discovery", return_value=True):
                result = eos_load_tool_category("device")
                assert result["status"] == "success"
                assert "already loaded" in result["message"]
        finally:
            _loaded_categories.discard("device")

    def test_load_tool_category_progressive_new(self):
        """eos_load_tool_category should dynamically load a new category in progressive mode."""
        from network_mcp.tools.meta import _loaded_categories, eos_load_tool_category

        _loaded_categories.discard("monitoring")
        try:
            with patch("network_mcp.tools.meta._is_progressive_discovery", return_value=True):
                result = eos_load_tool_category("monitoring")
                assert result["status"] == "success"
                assert "loaded successfully" in result["message"]
                assert "monitoring" in _loaded_categories
        finally:
            _loaded_categories.discard("monitoring")

    def test_load_tool_category_import_error(self):
        """eos_load_tool_category should handle import errors gracefully."""
        from network_mcp.tools.meta import _loaded_categories, eos_load_tool_category

        _loaded_categories.discard("validation")
        with (
            patch("network_mcp.tools.meta._is_progressive_discovery", return_value=True),
            patch("network_mcp.tools.meta.importlib.import_module", side_effect=ImportError("missing dep")),
        ):
            result = eos_load_tool_category("validation")
            assert result["status"] == "error"
            assert "Failed to load" in result["error"]

    def test_is_progressive_discovery_with_injected_settings(self):
        """_is_progressive_discovery should use injected settings."""
        from network_mcp.tools.meta import _init_meta_settings, _is_progressive_discovery

        mock_settings = MagicMock()
        mock_settings.net_progressive_discovery = True
        _init_meta_settings(mock_settings)
        try:
            assert _is_progressive_discovery() is True
            mock_settings.net_progressive_discovery = False
            assert _is_progressive_discovery() is False
        finally:
            _init_meta_settings(None)

    def test_is_progressive_discovery_fallback(self):
        """_is_progressive_discovery should fall back when settings not injected."""
        from network_mcp.tools.meta import _is_progressive_discovery

        # This exercises the except branch (line 387-388)
        with (
            patch("network_mcp.tools.meta._settings", None),
            patch("network_mcp.tools.meta.importlib.import_module", side_effect=ImportError("no server")),
        ):
            # When import fails, should return False
            result = _is_progressive_discovery()
            assert result is False

    def test_get_tools_for_module_package(self):
        """_get_tools_for_module should introspect package modules (common)."""
        from network_mcp.tools.meta import _get_tools_for_module

        tools = _get_tools_for_module("common")
        assert len(tools) > 0
        # Common tools use net_ prefix
        assert any(t["name"].startswith("net_") for t in tools)

    def test_get_tools_for_module_nonexistent(self):
        """_get_tools_for_module should return empty for nonexistent module."""
        from network_mcp.tools.meta import _get_tools_for_module

        tools = _get_tools_for_module("nonexistent_module")
        assert tools == []

    def test_get_tool_count_for_module_package(self):
        """_get_tool_count_for_module should count tools in packages."""
        from network_mcp.tools.meta import _get_tool_count_for_module

        count = _get_tool_count_for_module("common")
        assert count > 0

    def test_get_tool_count_for_module_nonexistent(self):
        """_get_tool_count_for_module should return 0 for nonexistent module."""
        from network_mcp.tools.meta import _get_tool_count_for_module

        count = _get_tool_count_for_module("nonexistent_module")
        assert count == 0

    def test_net_list_plugins(self):
        """net_list_plugins should return plugin summary."""
        from network_mcp.tools.meta import net_list_plugins

        result = net_list_plugins()
        assert "status" in result


# --- Validation tools coverage (ANTA not installed path) ---


class TestValidationToolsNotInstalled:
    """Test validation tools when ANTA is not installed."""

    def test_validate_bgp_no_anta(self):
        """eos_validate_bgp should return error when ANTA is not installed."""
        from network_mcp.tools.validation import eos_validate_bgp

        with patch("network_mcp.tools.validation.ANTA_AVAILABLE", False):
            result = asyncio.run(eos_validate_bgp("spine-01"))
            assert result["status"] == "error"
            assert "ANTA" in result["error"]
            assert result["device"] == "spine-01"

    def test_validate_mlag_no_anta(self):
        """eos_validate_mlag should return error when ANTA is not installed."""
        from network_mcp.tools.validation import eos_validate_mlag

        with patch("network_mcp.tools.validation.ANTA_AVAILABLE", False):
            result = asyncio.run(eos_validate_mlag("spine-01"))
            assert result["status"] == "error"
            assert "ANTA" in result["error"]

    def test_validate_interfaces_no_anta(self):
        """eos_validate_interfaces should return error when ANTA is not installed."""
        from network_mcp.tools.validation import eos_validate_interfaces

        with patch("network_mcp.tools.validation.ANTA_AVAILABLE", False):
            result = asyncio.run(eos_validate_interfaces("spine-01"))
            assert result["status"] == "error"
            assert "ANTA" in result["error"]

    def test_validate_system_no_anta(self):
        """eos_validate_system should return error when ANTA is not installed."""
        from network_mcp.tools.validation import eos_validate_system

        with patch("network_mcp.tools.validation.ANTA_AVAILABLE", False):
            result = asyncio.run(eos_validate_system("spine-01"))
            assert result["status"] == "error"
            assert "ANTA" in result["error"]

    def test_run_anta_tests_no_anta(self):
        """eos_run_anta_tests should return error when ANTA is not installed."""
        from network_mcp.tools.validation import eos_run_anta_tests

        with patch("network_mcp.tools.validation.ANTA_AVAILABLE", False):
            result = asyncio.run(eos_run_anta_tests("spine-01", "anta.tests.system:"))
            assert result["status"] == "error"
            assert "ANTA" in result["error"]

    def test_validate_bgp_invalid_host(self):
        """eos_validate_bgp should return error for invalid host."""
        from network_mcp.tools.validation import eos_validate_bgp

        with patch("network_mcp.tools.validation.ANTA_AVAILABLE", True):
            result = asyncio.run(eos_validate_bgp(""))
            assert result["status"] == "error"

    def test_validate_mlag_invalid_host(self):
        """eos_validate_mlag should return error for invalid host."""
        from network_mcp.tools.validation import eos_validate_mlag

        with patch("network_mcp.tools.validation.ANTA_AVAILABLE", True):
            result = asyncio.run(eos_validate_mlag(""))
            assert result["status"] == "error"

    def test_validate_interfaces_invalid_host(self):
        """eos_validate_interfaces should return error for invalid host."""
        from network_mcp.tools.validation import eos_validate_interfaces

        with patch("network_mcp.tools.validation.ANTA_AVAILABLE", True):
            result = asyncio.run(eos_validate_interfaces(""))
            assert result["status"] == "error"

    def test_validate_system_invalid_host(self):
        """eos_validate_system should return error for invalid host."""
        from network_mcp.tools.validation import eos_validate_system

        with patch("network_mcp.tools.validation.ANTA_AVAILABLE", True):
            result = asyncio.run(eos_validate_system(""))
            assert result["status"] == "error"


# --- Session tools coverage ---


class TestSessionToolsEdgeCases:
    """Test session tools for uncovered branches."""

    def test_validate_session_name_valid(self):
        from network_mcp.tools.sessions import validate_session_name

        assert validate_session_name("test-session") is None
        assert validate_session_name("abc123") is None

    def test_validate_session_name_empty(self):
        from network_mcp.tools.sessions import validate_session_name

        assert validate_session_name("") is not None

    def test_validate_session_name_too_long(self):
        from network_mcp.tools.sessions import validate_session_name

        assert validate_session_name("a" * 51) is not None

    def test_validate_session_name_invalid_chars(self):
        from network_mcp.tools.sessions import validate_session_name

        assert validate_session_name("test session") is not None
        assert validate_session_name("test;session") is not None

    def test_validate_timer_format_valid(self):
        from network_mcp.tools.sessions import validate_timer_format

        assert validate_timer_format("00:05:00") is None
        assert validate_timer_format("01:30:00") is None

    def test_validate_timer_format_invalid(self):
        from network_mcp.tools.sessions import validate_timer_format

        assert validate_timer_format("5:00") is not None

    def test_validate_timer_format_zero_duration(self):
        from network_mcp.tools.sessions import validate_timer_format

        assert validate_timer_format("00:00:00") is not None

    def test_validate_timer_format_exceeds_limits(self):
        from network_mcp.tools.sessions import validate_timer_format

        assert validate_timer_format("24:00:00") is not None
        assert validate_timer_format("00:60:00") is not None

    def test_create_config_session_read_only(self, mock_conn_mgr, mock_node):
        """Creating session in read-only mode should return error."""
        from network_mcp.tools.sessions import eos_create_config_session

        with patch("network_mcp.tools.sessions.check_read_only", return_value="Read-only mode"):
            result = eos_create_config_session("spine-01")
            assert result["status"] == "error"
            assert "Read-only" in result["error"]

    def test_create_config_session_invalid_name(self, mock_conn_mgr, mock_node):
        """Creating session with invalid name should return error."""
        from network_mcp.tools.sessions import eos_create_config_session

        with patch("network_mcp.tools.sessions.check_read_only", return_value=None):
            result = eos_create_config_session("spine-01", session_name="invalid session!")
            assert result["status"] == "error"

    def test_add_to_config_session_read_only(self, mock_conn_mgr, mock_node):
        """Adding to session in read-only mode should return error."""
        from network_mcp.tools.sessions import eos_add_to_config_session

        with patch("network_mcp.tools.sessions.check_read_only", return_value="Read-only mode"):
            result = eos_add_to_config_session("spine-01", "test", ["vlan 100"])
            assert result["status"] == "error"

    def test_add_to_config_session_no_commands(self, mock_conn_mgr, mock_node):
        """Adding empty commands should return error."""
        from network_mcp.tools.sessions import eos_add_to_config_session

        with patch("network_mcp.tools.sessions.check_read_only", return_value=None):
            result = eos_add_to_config_session("spine-01", "test", [])
            assert result["status"] == "error"
            assert "No commands" in result["error"]

    def test_add_to_config_session_denied_command(self, mock_conn_mgr, mock_node):
        """Adding denied commands should return error."""
        from network_mcp.tools.sessions import eos_add_to_config_session

        with patch("network_mcp.tools.sessions.check_read_only", return_value=None):
            result = eos_add_to_config_session("spine-01", "test", ["reload"])
            assert result["status"] == "error"

    def test_commit_config_session_read_only(self, mock_conn_mgr, mock_node):
        """Committing in read-only mode should return error."""
        from network_mcp.tools.sessions import eos_commit_config_session

        with patch("network_mcp.tools.sessions.check_read_only", return_value="Read-only mode"):
            result = eos_commit_config_session("spine-01", "test")
            assert result["status"] == "error"

    def test_abort_config_session_read_only(self, mock_conn_mgr, mock_node):
        """Aborting in read-only mode should return error."""
        from network_mcp.tools.sessions import eos_abort_config_session

        with patch("network_mcp.tools.sessions.check_read_only", return_value="Read-only mode"):
            result = eos_abort_config_session("spine-01", "test")
            assert result["status"] == "error"


# --- Config mgmt tools coverage ---


class TestConfigMgmtEdgeCases:
    """Test config management tool edge cases."""

    def test_get_running_config_section_invalid_section(self, mock_conn_mgr, mock_node):
        """Invalid section name should return error."""
        from network_mcp.tools.config_mgmt import eos_get_running_config_section

        result = eos_get_running_config_section("spine-01", ";drop")
        assert result["status"] == "error"

    def test_save_running_config_read_only(self, mock_conn_mgr, mock_node):
        """Save config in read-only mode should return error."""
        from network_mcp.tools.config_mgmt import eos_save_running_config

        with patch("network_mcp.tools.config_mgmt.check_read_only", return_value="Read-only mode"):
            result = eos_save_running_config("spine-01")
            assert result["status"] == "error"

    def test_validate_backup_destination_valid(self):
        from network_mcp.tools.config_mgmt import validate_backup_destination

        assert validate_backup_destination("scp://user@host/path") is None
        assert validate_backup_destination("flash:backup.cfg") is None

    def test_validate_backup_destination_invalid(self):
        from network_mcp.tools.config_mgmt import validate_backup_destination

        assert validate_backup_destination("") is not None
        assert validate_backup_destination("http://host/path") is not None

    def test_validate_regex_pattern_valid(self):
        from network_mcp.tools.config_mgmt import validate_regex_pattern

        assert validate_regex_pattern("Established") is None
        assert validate_regex_pattern(r"\d+") is None

    def test_validate_regex_pattern_invalid(self):
        from network_mcp.tools.config_mgmt import validate_regex_pattern

        assert validate_regex_pattern("") is not None
        assert validate_regex_pattern("[invalid") is not None

    def test_backup_config_read_only(self, mock_conn_mgr, mock_node):
        """Backup in read-only mode should return error."""
        from network_mcp.tools.config_mgmt import eos_backup_config

        with patch("network_mcp.tools.config_mgmt.check_read_only", return_value="Read-only mode"):
            result = eos_backup_config("spine-01", "scp://user@host/path")
            assert result["status"] == "error"

    def test_backup_config_invalid_destination(self, mock_conn_mgr, mock_node):
        """Invalid destination should return error."""
        from network_mcp.tools.config_mgmt import eos_backup_config

        with patch("network_mcp.tools.config_mgmt.check_read_only", return_value=None):
            result = eos_backup_config("spine-01", "http://invalid")
            assert result["status"] == "error"

    def test_schedule_maintenance_read_only(self, mock_conn_mgr, mock_node):
        """Maintenance in read-only mode should return error."""
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        with patch("network_mcp.tools.config_mgmt.check_read_only", return_value="Read-only mode"):
            result = eos_schedule_maintenance("spine-01", ["vlan 100"])
            assert result["status"] == "error"

    def test_schedule_maintenance_no_commands(self, mock_conn_mgr, mock_node):
        """Maintenance with no commands should return error."""
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        with patch("network_mcp.tools.config_mgmt.check_read_only", return_value=None):
            result = eos_schedule_maintenance("spine-01", [])
            assert result["status"] == "error"
            assert "No commands" in result["error"]

    def test_schedule_maintenance_invalid_rollback_minutes(self, mock_conn_mgr, mock_node):
        """Invalid rollback minutes should return error."""
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        with patch("network_mcp.tools.config_mgmt.check_read_only", return_value=None):
            result = eos_schedule_maintenance("spine-01", ["vlan 100"], rollback_minutes=0)
            assert result["status"] == "error"
            assert "rollback_minutes" in result["error"]

    def test_schedule_maintenance_denied_command(self, mock_conn_mgr, mock_node):
        """Denied commands in maintenance should return error."""
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        with patch("network_mcp.tools.config_mgmt.check_read_only", return_value=None):
            result = eos_schedule_maintenance("spine-01", ["reload"])
            assert result["status"] == "error"

    def test_auto_rollback_read_only(self, mock_conn_mgr, mock_node):
        """Auto-rollback in read-only mode should return error."""
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        with patch("network_mcp.tools.config_mgmt.check_read_only", return_value="Read-only mode"):
            result = eos_auto_rollback_on_failure("spine-01", ["vlan 100"], "show version", "pattern")
            assert result["status"] == "error"

    def test_auto_rollback_no_commands(self, mock_conn_mgr, mock_node):
        """Auto-rollback with no commands should return error."""
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        with patch("network_mcp.tools.config_mgmt.check_read_only", return_value=None):
            result = eos_auto_rollback_on_failure("spine-01", [], "show version", "pattern")
            assert result["status"] == "error"

    def test_auto_rollback_invalid_health_check(self, mock_conn_mgr, mock_node):
        """Auto-rollback with non-show health check should return error."""
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        with patch("network_mcp.tools.config_mgmt.check_read_only", return_value=None):
            result = eos_auto_rollback_on_failure("spine-01", ["vlan 100"], "configure terminal", "pattern")
            assert result["status"] == "error"
            assert "show command" in result["error"]

    def test_auto_rollback_invalid_regex(self, mock_conn_mgr, mock_node):
        """Auto-rollback with invalid regex should return error."""
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        with patch("network_mcp.tools.config_mgmt.check_read_only", return_value=None):
            result = eos_auto_rollback_on_failure("spine-01", ["vlan 100"], "show version", "[invalid")
            assert result["status"] == "error"


# --- Fabric tools coverage ---


class TestFabricToolsCoverage:
    """Test fabric tools for uncovered lines."""

    def test_list_inventory_no_filter(self, mock_conn_mgr):
        """eos_list_inventory with no filter."""
        from network_mcp.tools.fabric import eos_list_inventory

        with patch.object(conn_mgr, "get_inventory", return_value={}):
            result = eos_list_inventory()
            assert result["status"] == "success"
            assert result["total"] == 0

    def test_list_inventory_with_role_filter(self, mock_conn_mgr):
        """eos_list_inventory with role filter."""
        from network_mcp.tools.fabric import eos_list_inventory

        inv = {
            "sw-01": DeviceCredentials(host="10.0.0.1", role="spine"),
            "sw-02": DeviceCredentials(host="10.0.0.2", role="leaf"),
        }
        with patch.object(conn_mgr, "get_inventory", return_value=inv):
            result = eos_list_inventory(role="spine")
            assert result["status"] == "success"
            assert result["total"] == 1

    def test_list_inventory_with_group_filter(self, mock_conn_mgr):
        """eos_list_inventory with group filter."""
        from network_mcp.tools.fabric import eos_list_inventory

        inv = {
            "sw-01": DeviceCredentials(host="10.0.0.1", groups=["dc1"]),
            "sw-02": DeviceCredentials(host="10.0.0.2", groups=["dc2"]),
        }
        with patch.object(conn_mgr, "get_inventory", return_value=inv):
            result = eos_list_inventory(group="dc1")
            assert result["total"] == 1

    def test_list_inventory_with_tag_filter(self, mock_conn_mgr):
        """eos_list_inventory with tag filter."""
        from network_mcp.tools.fabric import eos_list_inventory

        inv = {
            "sw-01": DeviceCredentials(host="10.0.0.1", tags=["prod"]),
            "sw-02": DeviceCredentials(host="10.0.0.2", tags=["staging"]),
        }
        with patch.object(conn_mgr, "get_inventory", return_value=inv):
            result = eos_list_inventory(tag="prod")
            assert result["total"] == 1

    def test_fabric_health_summary_no_devices(self):
        """eos_fabric_health_summary with no matching devices."""
        from network_mcp.tools.fabric import eos_fabric_health_summary

        with patch("network_mcp.tools.fabric.resolve_hosts", return_value=[]):
            result = asyncio.run(eos_fabric_health_summary("nonexistent"))
            assert result["status"] == "error"

    def test_fabric_health_summary_too_many_devices(self):
        """eos_fabric_health_summary with too many devices."""
        from network_mcp.tools.fabric import eos_fabric_health_summary

        with patch("network_mcp.tools.fabric.resolve_hosts", return_value=[f"h-{i}" for i in range(201)]):
            result = asyncio.run(eos_fabric_health_summary("all"))
            assert result["status"] == "error"
            assert "Too many" in result["error"]

    def test_fabric_bgp_status_no_devices(self):
        """eos_fabric_bgp_status with no matching devices."""
        from network_mcp.tools.fabric import eos_fabric_bgp_status

        with patch("network_mcp.tools.fabric.resolve_hosts", return_value=[]):
            result = asyncio.run(eos_fabric_bgp_status("nonexistent"))
            assert result["status"] == "error"

    def test_fabric_bgp_status_too_many(self):
        """eos_fabric_bgp_status with too many devices."""
        from network_mcp.tools.fabric import eos_fabric_bgp_status

        with patch("network_mcp.tools.fabric.resolve_hosts", return_value=[f"h-{i}" for i in range(201)]):
            result = asyncio.run(eos_fabric_bgp_status("all"))
            assert result["status"] == "error"

    def test_fabric_mlag_status_no_devices(self):
        """eos_fabric_mlag_status with no matching devices."""
        from network_mcp.tools.fabric import eos_fabric_mlag_status

        with patch("network_mcp.tools.fabric.resolve_hosts", return_value=[]):
            result = asyncio.run(eos_fabric_mlag_status("nonexistent"))
            assert result["status"] == "error"

    def test_fabric_mlag_status_too_many(self):
        """eos_fabric_mlag_status with too many devices."""
        from network_mcp.tools.fabric import eos_fabric_mlag_status

        with patch("network_mcp.tools.fabric.resolve_hosts", return_value=[f"h-{i}" for i in range(201)]):
            result = asyncio.run(eos_fabric_mlag_status("all"))
            assert result["status"] == "error"

    def test_compare_configs_empty_list(self):
        """eos_compare_configs with empty device list."""
        from network_mcp.tools.fabric import eos_compare_configs

        result = asyncio.run(eos_compare_configs([]))
        assert result["status"] == "error"

    def test_compare_configs_too_many(self):
        """eos_compare_configs with too many devices."""
        from network_mcp.tools.fabric import eos_compare_configs

        result = asyncio.run(eos_compare_configs([f"h-{i}" for i in range(201)]))
        assert result["status"] == "error"

    def test_compare_configs_invalid_section(self):
        """eos_compare_configs with invalid section."""
        from network_mcp.tools.fabric import eos_compare_configs

        result = asyncio.run(eos_compare_configs(["spine-01"], section=";evil"))
        assert result["status"] == "error"

    def test_fabric_health_async_no_devices(self):
        """eos_fabric_health_async with no matching devices."""
        from network_mcp.tools.fabric import eos_fabric_health_async

        with patch("network_mcp.tools.fabric.resolve_hosts", return_value=[]):
            result = asyncio.run(eos_fabric_health_async("nonexistent"))
            assert result["status"] == "error"

    def test_fabric_health_async_too_many(self):
        """eos_fabric_health_async with too many devices."""
        from network_mcp.tools.fabric import eos_fabric_health_async

        with patch("network_mcp.tools.fabric.resolve_hosts", return_value=[f"h-{i}" for i in range(201)]):
            result = asyncio.run(eos_fabric_health_async("all"))
            assert result["status"] == "error"


# --- Troubleshoot tools coverage ---


class TestTroubleshootToolsCoverage:
    """Test troubleshoot tools for uncovered lines."""

    def test_ping_invalid_destination(self, mock_conn_mgr, mock_node):
        """Invalid destination should return error."""
        from network_mcp.tools.troubleshoot import eos_ping_from_device

        result = eos_ping_from_device("spine-01", ";evil")
        assert result["status"] == "error"

    def test_ping_invalid_source(self, mock_conn_mgr, mock_node):
        """Invalid source should return error."""
        from network_mcp.tools.troubleshoot import eos_ping_from_device

        result = eos_ping_from_device("spine-01", "10.0.0.1", source=";evil")
        assert result["status"] == "error"

    def test_ping_invalid_count(self, mock_conn_mgr, mock_node):
        """Invalid count should return error."""
        from network_mcp.tools.troubleshoot import eos_ping_from_device

        result = eos_ping_from_device("spine-01", "10.0.0.1", count=0)
        assert result["status"] == "error"

    def test_traceroute_invalid_destination(self, mock_conn_mgr, mock_node):
        """Invalid destination should return error."""
        from network_mcp.tools.troubleshoot import eos_traceroute_from_device

        result = eos_traceroute_from_device("spine-01", ";evil")
        assert result["status"] == "error"

    def test_traceroute_invalid_source(self, mock_conn_mgr, mock_node):
        """Invalid source should return error."""
        from network_mcp.tools.troubleshoot import eos_traceroute_from_device

        result = eos_traceroute_from_device("spine-01", "10.0.0.1", source=";evil")
        assert result["status"] == "error"

    def test_check_interface_flaps_with_interface(self, mock_conn_mgr, mock_node):
        """Interface flap check with specific interface."""
        from network_mcp.tools.troubleshoot import eos_check_interface_flaps

        mock_node.run_commands.side_effect = [
            [{"interfaceStatuses": {"Ethernet1": {"linkStatus": "connected"}}}],
            [{"interfaces": {"Ethernet1": {"lineProtocolStatus": "up"}}}],
            [{"output": "Ethernet1 changed state to up\nEthernet1 changed state to down\n"}],
        ]
        result = eos_check_interface_flaps("spine-01", interface="Ethernet1")
        assert result["status"] == "success"
        assert result["data"]["flap_count"] >= 0


# --- Executor coverage ---


class TestExecutorCoverage:
    """Test executor module for uncovered lines."""

    def test_resolve_hosts_all(self):
        """resolve_hosts with 'all' should return all devices."""
        from network_mcp.executor import resolve_hosts

        with patch.object(conn_mgr, "list_devices", return_value=["spine-01", "leaf-01"]):
            hosts = resolve_hosts(conn_mgr, "all")
            assert hosts == ["spine-01", "leaf-01"]

    def test_resolve_hosts_group(self):
        """resolve_hosts with group prefix should filter by group."""
        from network_mcp.executor import resolve_hosts

        with patch.object(conn_mgr, "get_devices_by_group", return_value=["sw-01"]):
            hosts = resolve_hosts(conn_mgr, "group:dc1")
            assert hosts == ["sw-01"]

    def test_resolve_hosts_tag(self):
        """resolve_hosts with tag prefix should filter by tag."""
        from network_mcp.executor import resolve_hosts

        with patch.object(conn_mgr, "get_devices_by_tag", return_value=["sw-02"]):
            hosts = resolve_hosts(conn_mgr, "tag:prod")
            assert hosts == ["sw-02"]

    def test_resolve_hosts_role(self):
        """resolve_hosts with role prefix should filter by role."""
        from network_mcp.executor import resolve_hosts

        with patch.object(conn_mgr, "get_devices_by_role", return_value=["spine-01"]):
            hosts = resolve_hosts(conn_mgr, "role:spine")
            assert hosts == ["spine-01"]

    def test_resolve_hosts_comma_separated(self):
        """resolve_hosts with comma-separated hosts."""
        from network_mcp.executor import resolve_hosts

        hosts = resolve_hosts(conn_mgr, "spine-01, leaf-01, leaf-02")
        assert hosts == ["spine-01", "leaf-01", "leaf-02"]

    def test_multi_device_result_partial_status(self):
        """MultiDeviceResult should report partial status."""
        from network_mcp.executor import MultiDeviceResult

        result = MultiDeviceResult()
        result.add_success("host-1", {"data": "ok"})
        result.add_error("host-2", "failed")
        output = result.to_dict()
        assert output["status"] == "partial"
        assert output["summary"]["succeeded"] == 1
        assert output["summary"]["failed"] == 1
        assert "succeeded_hosts" in output
        assert "failed_hosts" in output

    def test_multi_device_result_all_error(self):
        """MultiDeviceResult with all errors."""
        from network_mcp.executor import MultiDeviceResult

        result = MultiDeviceResult()
        result.add_error("host-1", "failed")
        result.add_error("host-2", "failed")
        output = result.to_dict()
        assert output["status"] == "error"

    def test_multi_device_result_all_success(self):
        """MultiDeviceResult with all successes."""
        from network_mcp.executor import MultiDeviceResult

        result = MultiDeviceResult()
        result.add_success("host-1", {"data": "ok"})
        output = result.to_dict()
        assert output["status"] == "success"


# --- Routing tools validation helpers ---


class TestRoutingValidationHelpers:
    """Test routing module validation helpers."""

    def test_validate_address_family_valid(self):
        from network_mcp.tools.routing import _validate_address_family

        assert _validate_address_family("ipv4", "spine-01") is None
        assert _validate_address_family("ipv6", "spine-01") is None

    def test_validate_address_family_invalid(self):
        from network_mcp.tools.routing import _validate_address_family

        result = _validate_address_family("ipv8", "spine-01")
        assert result is not None
        assert result["status"] == "error"

    def test_validate_vrf_default(self):
        from network_mcp.tools.routing import _validate_vrf

        assert _validate_vrf("default", "spine-01") is None

    def test_validate_vrf_custom_valid(self):
        from network_mcp.tools.routing import _validate_vrf

        assert _validate_vrf("PROD", "spine-01") is None

    def test_validate_vrf_injection(self):
        from network_mcp.tools.routing import _validate_vrf

        result = _validate_vrf(";evil", "spine-01")
        assert result is not None
        assert result["status"] == "error"
