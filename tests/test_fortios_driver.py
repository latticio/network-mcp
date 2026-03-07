"""Tests for the Fortinet FortiOS driver (FortiosDriver)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.drivers.fortinet_fortios import FortiosDriver, _safe_float, _safe_int

# ---------------------------------------------------------------------------
# Sample FortiOS REST API JSON responses
# ---------------------------------------------------------------------------

SYSTEM_STATUS_RESPONSE = {
    "status": "success",
    "results": {
        "hostname": "FGT-01",
        "model": "FortiGate-100F",
        "version": "v7.4.2",
        "serial_number": "FGT1HFSK19123456",
        "uptime": 86400,
    },
}

INTERFACE_RESPONSE = {
    "status": "success",
    "results": {
        "port1": {
            "name": "port1",
            "link": True,
            "speed": 1000000000,
            "mtu": 1500,
            "mac": "00:11:22:33:44:55",
            "status": "up",
            "description": "WAN link",
        },
        "port2": {
            "name": "port2",
            "link": False,
            "speed": 1000,
            "mtu": 1500,
            "mac": "00:11:22:33:44:56",
            "status": "down",
            "description": "",
        },
    },
}

INTERFACE_LIST_RESPONSE = {
    "status": "success",
    "results": [
        {
            "name": "port3",
            "link": True,
            "speed": 10000,
            "mtu": 9000,
            "mac": "00:11:22:33:44:57",
            "status": "up",
            "description": "Uplink",
        },
    ],
}

ARP_TABLE_RESPONSE = {
    "status": "success",
    "results": [
        {
            "ip": "192.168.1.1",
            "mac": "aa:bb:cc:dd:ee:ff",
            "interface": "port1",
            "age": 120.0,
        },
        {
            "ip": "192.168.1.2",
            "mac": "aa:bb:cc:dd:ee:00",
            "interface": "port1",
            "age": 60.5,
        },
        {
            # Entry without IP should be skipped
            "mac": "aa:bb:cc:dd:ee:01",
            "interface": "port2",
        },
    ],
}

ROUTE_TABLE_RESPONSE = {
    "status": "success",
    "results": [
        {
            "ip_mask": "0.0.0.0/0",
            "type": "static",
            "gateway": [{"ip": "10.0.0.1"}],
            "metric": 10,
            "distance": 1,
        },
        {
            "ip_mask": "10.0.0.0/8",
            "type": "connected",
            "gateway": [],
            "metric": 0,
            "distance": 0,
        },
        {
            "ip_mask": "172.16.0.0/12",
            "type": "bgp",
            "gateway": "172.16.0.1",
            "metric": 100,
            "distance": 20,
        },
        {
            # Entry without prefix should be skipped
            "type": "static",
            "metric": 5,
        },
    ],
}

FIREWALL_POLICY_RESPONSE = {
    "status": 200,
    "http_method": "GET",
    "results": [
        {
            "policyid": 1,
            "name": "Allow-WAN-to-LAN",
            "action": "accept",
            "status": "enable",
            "srcintf": [{"name": "port1"}],
            "dstintf": [{"name": "port2"}],
            "srcaddr": [{"name": "all"}],
            "dstaddr": [{"name": "all"}],
            "service": [{"name": "HTTP"}, {"name": "HTTPS"}],
            "nat": "enable",
            "comments": "Main ingress policy",
        },
        {
            "policyid": 2,
            "name": "Deny-All",
            "action": "deny",
            "status": "enable",
            "srcintf": [{"name": "any"}],
            "dstintf": [{"name": "any"}],
            "srcaddr": [{"name": "all"}],
            "dstaddr": [{"name": "all"}],
            "service": [{"name": "ALL"}],
            "nat": "disable",
            "comments": "",
        },
    ],
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def driver() -> FortiosDriver:
    """Return a connected FortiosDriver with a mocked httpx client."""
    d = FortiosDriver()
    d._client = MagicMock()
    d._host = "192.168.1.1"
    d._api_key = "testkey123"
    d._connected = True
    return d


def _mock_response(data: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx response that returns the given data as JSON."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestSafeInt:
    def test_valid_int(self) -> None:
        assert _safe_int(42) == 42

    def test_valid_string(self) -> None:
        assert _safe_int("100") == 100

    def test_invalid_string(self) -> None:
        assert _safe_int("abc") == 0

    def test_none_uses_default(self) -> None:
        assert _safe_int(None, default=99) == 99

    def test_float_truncates(self) -> None:
        assert _safe_int(3.9) == 3


class TestSafeFloat:
    def test_valid_float(self) -> None:
        assert _safe_float(1.5) == pytest.approx(1.5)

    def test_valid_string(self) -> None:
        assert _safe_float("2.75") == pytest.approx(2.75)

    def test_invalid_string_default(self) -> None:
        assert _safe_float("bad", default=-1.0) == pytest.approx(-1.0)

    def test_none_default(self) -> None:
        assert _safe_float(None) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# connect() tests
# ---------------------------------------------------------------------------


class TestConnect:
    def test_connect_requires_api_key(self) -> None:
        d = FortiosDriver()
        with pytest.raises(ValueError, match="api_key must be provided"):
            with patch("network_mcp.drivers.fortinet_fortios.HAS_HTTPX", True):
                d.connect("192.168.1.1")

    def test_connect_no_httpx_raises(self) -> None:
        d = FortiosDriver()
        with patch("network_mcp.drivers.fortinet_fortios.HAS_HTTPX", False):
            with pytest.raises(ImportError, match="httpx not installed"):
                d.connect("192.168.1.1", api_key="key")

    def test_connect_probes_and_sets_connected(self) -> None:
        d = FortiosDriver()
        mock_client = MagicMock()
        mock_resp = _mock_response(SYSTEM_STATUS_RESPONSE)
        mock_client.get.return_value = mock_resp

        with patch("network_mcp.drivers.fortinet_fortios.HAS_HTTPX", True):
            with patch("network_mcp.drivers.fortinet_fortios.httpx") as mock_httpx:
                mock_httpx.Client.return_value = mock_client
                d.connect("192.168.1.1", api_key="testkey")

        assert d._connected is True
        assert d._host == "192.168.1.1"
        assert d._api_key == "testkey"

    def test_connect_failure_cleans_up(self) -> None:
        d = FortiosDriver()
        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("connection refused")

        with patch("network_mcp.drivers.fortinet_fortios.HAS_HTTPX", True):
            with patch("network_mcp.drivers.fortinet_fortios.httpx") as mock_httpx:
                mock_httpx.Client.return_value = mock_client
                with pytest.raises(ConnectionError, match="Failed to connect"):
                    d.connect("192.168.1.1", api_key="testkey")

        assert d._connected is False
        assert d._client is None


# ---------------------------------------------------------------------------
# close() / is_connected() / reconnect()
# ---------------------------------------------------------------------------


class TestConnectionLifecycle:
    def test_close_sets_disconnected(self, driver: FortiosDriver) -> None:
        driver.close()
        assert driver._connected is False
        assert driver._client is None

    def test_close_when_not_connected(self) -> None:
        d = FortiosDriver()
        d.close()  # should not raise
        assert d._connected is False

    def test_is_connected(self, driver: FortiosDriver) -> None:
        assert driver.is_connected() is True

    def test_reconnect_no_host_raises(self) -> None:
        d = FortiosDriver()
        with pytest.raises(ConnectionError, match="no previous connection"):
            d.reconnect()

    def test_reconnect_success(self, driver: FortiosDriver) -> None:
        mock_resp = _mock_response(SYSTEM_STATUS_RESPONSE)
        driver._client.get.return_value = mock_resp

        with patch("network_mcp.drivers.fortinet_fortios.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_httpx.Client.return_value = mock_client
            driver.reconnect()

        assert driver._connected is True


# ---------------------------------------------------------------------------
# get_facts()
# ---------------------------------------------------------------------------


class TestGetFacts:
    def test_returns_normalized_facts(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(SYSTEM_STATUS_RESPONSE)
        facts = driver.get_facts()

        assert facts["hostname"] == "FGT-01"
        assert facts["model"] == "FortiGate-100F"
        assert facts["version"] == "v7.4.2"
        assert facts["serial"] == "FGT1HFSK19123456"
        assert facts["uptime_seconds"] == 86400
        assert facts["vendor"] == "fortinet"
        assert facts["platform"] == "fortios"

    def test_missing_results_returns_defaults(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response({"status": "success", "results": {}})
        facts = driver.get_facts()

        assert facts["hostname"] == ""
        assert facts["uptime_seconds"] == 0


# ---------------------------------------------------------------------------
# get_interfaces()
# ---------------------------------------------------------------------------


class TestGetInterfaces:
    def test_dict_results_parsed(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(INTERFACE_RESPONSE)
        ifaces = driver.get_interfaces()

        assert "port1" in ifaces
        assert ifaces["port1"]["is_up"] is True
        assert ifaces["port1"]["is_enabled"] is True
        assert ifaces["port1"]["description"] == "WAN link"
        assert ifaces["port1"]["speed"] == 1000  # 1_000_000_000 bps → 1000 Mbps
        assert ifaces["port1"]["mtu"] == 1500

    def test_down_interface(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(INTERFACE_RESPONSE)
        ifaces = driver.get_interfaces()

        assert ifaces["port2"]["is_up"] is False
        assert ifaces["port2"]["is_enabled"] is False

    def test_list_results_parsed(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(INTERFACE_LIST_RESPONSE)
        ifaces = driver.get_interfaces()

        assert "port3" in ifaces
        assert ifaces["port3"]["speed"] == 10000  # already in Mbps
        assert ifaces["port3"]["mtu"] == 9000

    def test_skips_entries_without_name(self, driver: FortiosDriver) -> None:
        data = {"status": "success", "results": [{"link": True, "speed": 100}]}
        driver._client.get.return_value = _mock_response(data)
        ifaces = driver.get_interfaces()

        assert len(ifaces) == 0


# ---------------------------------------------------------------------------
# get_arp_table()
# ---------------------------------------------------------------------------


class TestGetArpTable:
    def test_returns_arp_entries(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(ARP_TABLE_RESPONSE)
        entries = driver.get_arp_table()

        # Entry without IP is skipped
        assert len(entries) == 2
        assert entries[0]["ip"] == "192.168.1.1"
        assert entries[0]["interface"] == "port1"
        assert entries[0]["age"] == pytest.approx(120.0)

    def test_mac_normalized(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(ARP_TABLE_RESPONSE)
        entries = driver.get_arp_table()

        # MAC should be lowercased (normalized)
        assert entries[0]["mac"] == "aa:bb:cc:dd:ee:ff"

    def test_empty_results(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response({"status": "success", "results": []})
        assert driver.get_arp_table() == []


# ---------------------------------------------------------------------------
# get_route_table()
# ---------------------------------------------------------------------------


class TestGetRouteTable:
    def test_returns_routes(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(ROUTE_TABLE_RESPONSE)
        routes = driver.get_route_table()

        # Entry without prefix is skipped; 3 valid entries
        assert len(routes) == 3

    def test_default_route_has_gateway(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(ROUTE_TABLE_RESPONSE)
        routes = driver.get_route_table()

        default_route = next(r for r in routes if r["prefix"] == "0.0.0.0/0")
        assert default_route["next_hop"] == "10.0.0.1"
        assert default_route["protocol"] == "static"
        assert default_route["metric"] == 10
        assert default_route["preference"] == 1

    def test_string_gateway_handled(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(ROUTE_TABLE_RESPONSE)
        routes = driver.get_route_table()

        bgp_route = next(r for r in routes if r["protocol"] == "bgp")
        assert bgp_route["next_hop"] == "172.16.0.1"

    def test_protocol_filter(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(ROUTE_TABLE_RESPONSE)
        routes = driver.get_route_table(protocol="connected")

        assert len(routes) == 1
        assert routes[0]["protocol"] == "connected"

    def test_limit_respected(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(ROUTE_TABLE_RESPONSE)
        routes = driver.get_route_table(limit=1)

        assert len(routes) == 1

    def test_empty_gateway_list(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(ROUTE_TABLE_RESPONSE)
        routes = driver.get_route_table(protocol="connected")

        assert routes[0]["next_hop"] == ""


# ---------------------------------------------------------------------------
# get_firewall_policies()
# ---------------------------------------------------------------------------


class TestGetFirewallPolicies:
    def test_returns_policies(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(FIREWALL_POLICY_RESPONSE)
        policies = driver.get_firewall_policies()

        assert len(policies) == 2

    def test_first_policy_fields(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(FIREWALL_POLICY_RESPONSE)
        policies = driver.get_firewall_policies()

        p = policies[0]
        assert p["policy_id"] == 1
        assert p["name"] == "Allow-WAN-to-LAN"
        assert p["action"] == "accept"
        assert p["status"] == "enable"
        assert p["srcintf"] == ["port1"]
        assert p["dstintf"] == ["port2"]
        assert p["srcaddr"] == ["all"]
        assert p["dstaddr"] == ["all"]
        assert p["service"] == ["HTTP", "HTTPS"]
        assert p["nat"] is True
        assert p["comments"] == "Main ingress policy"

    def test_deny_policy_nat_false(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response(FIREWALL_POLICY_RESPONSE)
        policies = driver.get_firewall_policies()

        deny = policies[1]
        assert deny["action"] == "deny"
        assert deny["nat"] is False

    def test_empty_results(self, driver: FortiosDriver) -> None:
        driver._client.get.return_value = _mock_response({"status": 200, "results": []})
        assert driver.get_firewall_policies() == []

    def test_cmdb_status_200_accepted(self, driver: FortiosDriver) -> None:
        """CMDB endpoints return status=200 (int), not 'success'."""
        driver._client.get.return_value = _mock_response(FIREWALL_POLICY_RESPONSE)
        # Should not raise
        policies = driver.get_firewall_policies()
        assert len(policies) > 0


# ---------------------------------------------------------------------------
# _get() error handling
# ---------------------------------------------------------------------------


class TestGetMethod:
    def test_raises_when_not_connected(self) -> None:
        d = FortiosDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            d._get("/api/v2/monitor/system/status")

    def test_raises_on_api_error_status(self, driver: FortiosDriver) -> None:
        error_resp = {"status": "error", "message": "Permission denied"}
        driver._client.get.return_value = _mock_response(error_resp)
        with pytest.raises(ValueError, match="Permission denied"):
            driver._get("/api/v2/monitor/system/status")

    def test_none_status_accepted(self, driver: FortiosDriver) -> None:
        """Endpoints without a status field should not raise."""
        data = {"results": [{"ip": "10.0.0.1"}]}
        driver._client.get.return_value = _mock_response(data)
        result = driver._get("/api/v2/monitor/network/arp")
        assert result["results"][0]["ip"] == "10.0.0.1"


# ---------------------------------------------------------------------------
# Unsupported getter stubs
# ---------------------------------------------------------------------------


class TestUnsupportedGetters:
    def test_run_show_raises(self, driver: FortiosDriver) -> None:
        with pytest.raises(NotSupportedError):
            driver.run_show(["show version"])

    def test_run_config_raises(self, driver: FortiosDriver) -> None:
        with pytest.raises(NotSupportedError):
            driver.run_config(["config system interface"])

    def test_get_bgp_summary_raises(self, driver: FortiosDriver) -> None:
        with pytest.raises(NotSupportedError):
            driver.get_bgp_summary()

    def test_get_vlans_raises(self, driver: FortiosDriver) -> None:
        with pytest.raises(NotSupportedError):
            driver.get_vlans()

    def test_get_lldp_neighbors_raises(self, driver: FortiosDriver) -> None:
        with pytest.raises(NotSupportedError):
            driver.get_lldp_neighbors()

    def test_ping_raises(self, driver: FortiosDriver) -> None:
        with pytest.raises(NotSupportedError):
            driver.ping("8.8.8.8")

    def test_traceroute_raises(self, driver: FortiosDriver) -> None:
        with pytest.raises(NotSupportedError):
            driver.traceroute("8.8.8.8")


# ---------------------------------------------------------------------------
# capabilities()
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_supported_features(self, driver: FortiosDriver) -> None:
        caps = driver.capabilities()

        assert caps["rest_api"] is True
        assert caps["get_facts"] is True
        assert caps["get_interfaces"] is True
        assert caps["get_arp_table"] is True
        assert caps["get_route_table"] is True
        assert caps["get_firewall_policies"] is True

    def test_unsupported_features(self, driver: FortiosDriver) -> None:
        caps = driver.capabilities()

        assert caps["gnmi"] is False
        assert caps["netconf"] is False
        assert caps["get_bgp_summary"] is False
        assert caps["get_vlans"] is False
        assert caps["ping"] is False

    def test_platform_identity(self, driver: FortiosDriver) -> None:
        assert driver.platform == "fortios"
        assert driver.vendor == "fortinet"


# ---------------------------------------------------------------------------
# Driver registry integration
# ---------------------------------------------------------------------------


class TestDriverRegistry:
    def test_fortios_in_registry(self) -> None:
        from network_mcp.drivers import DRIVER_REGISTRY

        assert "fortios" in DRIVER_REGISTRY
        assert DRIVER_REGISTRY["fortios"] is FortiosDriver


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_fortios_in_platform_enum(self) -> None:
        from network_mcp.config import Platform

        assert Platform.FORTIOS == "fortios"

    def test_net_fortios_api_key_setting(self) -> None:
        import os

        os.environ["NET_FORTIOS_API_KEY"] = "myapikey"
        try:
            from network_mcp.config import NetworkSettings

            settings = NetworkSettings()
            assert settings.net_fortios_api_key is not None
            assert settings.net_fortios_api_key.get_secret_value() == "myapikey"
        finally:
            del os.environ["NET_FORTIOS_API_KEY"]

    def test_fortios_valid_inventory_platform(self) -> None:
        from network_mcp.config import Platform

        valid_platforms = {p.value for p in Platform}
        assert "fortios" in valid_platforms
