"""Tests for the SONiC NOS driver (SonicDriver)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.drivers.sonic import SonicDriver

# ---------------------------------------------------------------------------
# Mock REST API responses
# ---------------------------------------------------------------------------

MOCK_SYSTEM_RESPONSE = {
    "openconfig-system:system": {
        "state": {
            "hostname": "sonic-switch-01",
            "hardware": "Accton-AS7726-32X",
            "software-version": "SONiC.20230531.10",
            "serial-number": "SN1234567890",
            "boot-time": 1234567,
        },
        "config": {
            "hostname": "sonic-switch-01",
        },
    }
}

MOCK_INTERFACES_RESPONSE = {
    "openconfig-interfaces:interfaces": {
        "interface": [
            {
                "name": "Ethernet0",
                "config": {
                    "description": "uplink to spine",
                    "enabled": True,
                    "mtu": 9100,
                },
                "state": {
                    "oper-status": "UP",
                    "admin-status": "UP",
                    "mtu": 9100,
                    "mac-address": "00:11:22:33:44:55",
                    "description": "uplink to spine",
                    "openconfig-if-ethernet:ethernet": {
                        "state": {
                            "port-speed": "SPEED_100GB",
                        }
                    },
                },
            },
            {
                "name": "Ethernet4",
                "config": {
                    "description": "server port",
                    "enabled": True,
                    "mtu": 1500,
                },
                "state": {
                    "oper-status": "DOWN",
                    "admin-status": "UP",
                    "mtu": 1500,
                    "mac-address": "00:11:22:33:44:66",
                },
            },
        ]
    }
}

MOCK_BGP_RESPONSE = {
    "openconfig-network-instance:bgp": {
        "global": {
            "state": {
                "router-id": "10.0.0.1",
                "as": 65001,
            }
        },
        "neighbors": {
            "neighbor": [
                {
                    "neighbor-address": "10.0.0.2",
                    "state": {
                        "session-state": "ESTABLISHED",
                        "prefixes": {
                            "received": 150,
                        },
                        "established-transitions": 3600,
                    },
                },
                {
                    "neighbor-address": "10.0.0.3",
                    "state": {
                        "session-state": "ACTIVE",
                        "prefixes": {
                            "received": 0,
                        },
                        "established-transitions": 0,
                    },
                },
            ]
        },
    }
}

MOCK_LLDP_RESPONSE = {
    "openconfig-lldp:interfaces": {
        "interface": [
            {
                "name": "Ethernet0",
                "neighbors": {
                    "neighbor": [
                        {
                            "state": {
                                "system-name": "spine-01",
                                "port-id": "Ethernet1",
                                "system-description": "SONiC spine switch",
                            }
                        }
                    ]
                },
            },
            {
                "name": "Ethernet4",
                "neighbors": {
                    "neighbor": [],
                },
            },
        ]
    }
}

MOCK_VLANS_RESPONSE = {
    "sonic-vlan:VLAN": {
        "VLAN_LIST": [
            {
                "name": "Vlan100",
                "vlanid": 100,
                "alias": "servers",
            },
            {
                "name": "Vlan200",
                "vlanid": 200,
                "alias": "management",
            },
        ]
    }
}

MOCK_VLAN_MEMBERS_RESPONSE = {
    "sonic-vlan:VLAN_MEMBER": {
        "VLAN_MEMBER_LIST": [
            {"name": "Vlan100", "ifname": "Ethernet0"},
            {"name": "Vlan100", "ifname": "Ethernet4"},
            {"name": "Vlan200", "ifname": "Ethernet8"},
        ]
    }
}

MOCK_ROUTES_RESPONSE = {
    "openconfig-network-instance:afts": {
        "ipv4-unicast": {
            "ipv4-entry": [
                {
                    "prefix": "10.0.0.0/24",
                    "state": {
                        "origin-protocol": "connected",
                        "metric": 0,
                        "preference": 0,
                    },
                    "next-hops": {
                        "next-hop": [
                            {"state": {"ip-address": "0.0.0.0"}}  # noqa: S104
                        ]
                    },
                },
                {
                    "prefix": "10.1.0.0/24",
                    "state": {
                        "origin-protocol": "bgp",
                        "metric": 100,
                        "preference": 200,
                    },
                    "next-hops": {
                        "next-hop": [
                            {"state": {"ip-address": "10.0.0.2"}}
                        ]
                    },
                },
                {
                    "prefix": "10.2.0.0/24",
                    "state": {
                        "origin-protocol": "static",
                        "metric": 0,
                        "preference": 1,
                    },
                    "next-hops": {
                        "next-hop": [
                            {"state": {"ip-address": "10.0.0.1"}}
                        ]
                    },
                },
            ]
        }
    }
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver() -> SonicDriver:
    """Build a SonicDriver with a mocked httpx client (no real connection)."""
    import time as _time

    driver = SonicDriver()
    driver._connected = True
    driver._host = "sonic-test"
    driver._username = "admin"
    driver._password = "pass"
    driver._last_command_time = _time.monotonic()  # recent, so keepalive won't trigger
    driver._keepalive_interval = 99999  # disable keepalive checks

    mock_client = MagicMock()
    driver._client = mock_client
    return driver


def _mock_get_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Create a mock httpx response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ---------------------------------------------------------------------------
# Tests: Connection lifecycle
# ---------------------------------------------------------------------------


class TestSonicDriverConnection:
    """Test connection lifecycle methods."""

    def test_platform_and_vendor(self):
        driver = SonicDriver()
        assert driver.platform == "sonic"
        assert driver.vendor == "sonic"

    def test_not_connected_by_default(self):
        driver = SonicDriver()
        assert not driver.is_connected()

    def test_connect_success(self):
        driver = SonicDriver()
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_get_response(MOCK_SYSTEM_RESPONSE)

        with patch("network_mcp.drivers.sonic.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            driver.connect("sonic-host", username="admin", password="test", verify_ssl=False)

        assert driver.is_connected()
        assert driver._host == "sonic-host"

    def test_connect_auth_failure(self):
        driver = SonicDriver()
        mock_client = MagicMock()
        mock_exc = Exception("auth failed")
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_exc.response = mock_response
        mock_client.get.side_effect = mock_exc

        with patch("network_mcp.drivers.sonic.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            with pytest.raises(ConnectionError, match="Authentication failed"):
                driver.connect("sonic-host", username="admin", password="wrong")

    def test_connect_no_httpx(self):
        driver = SonicDriver()
        with patch("network_mcp.drivers.sonic.HAS_HTTPX", False):
            with pytest.raises(ImportError, match="httpx not installed"):
                driver.connect("sonic-host")

    def test_close(self):
        driver = _make_driver()
        assert driver.is_connected()
        driver.close()
        assert not driver.is_connected()
        assert driver._client is None

    def test_close_already_closed(self):
        driver = SonicDriver()
        driver.close()  # should not raise
        assert not driver.is_connected()

    def test_reconnect(self):
        driver = _make_driver()
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_get_response(MOCK_SYSTEM_RESPONSE)

        with patch("network_mcp.drivers.sonic.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            driver.reconnect()

        assert driver.is_connected()

    def test_reconnect_no_host(self):
        driver = SonicDriver()
        with pytest.raises(ConnectionError, match="Cannot reconnect"):
            driver.reconnect()


# ---------------------------------------------------------------------------
# Tests: Capabilities
# ---------------------------------------------------------------------------


class TestSonicDriverCapabilities:
    def test_capabilities_returns_dict(self):
        driver = SonicDriver()
        caps = driver.capabilities()
        assert isinstance(caps, dict)
        assert caps["gnmi"] is True
        assert caps["restconf"] is True
        assert caps["get_facts"] is True
        assert caps["get_interfaces"] is True
        assert caps["get_bgp_summary"] is True
        assert caps["get_lldp_neighbors"] is True
        assert caps["get_vlans"] is True
        assert caps["get_route_table"] is True
        # Unsupported
        assert caps["get_arp_table"] is False
        assert caps["ping"] is False


# ---------------------------------------------------------------------------
# Tests: get_facts
# ---------------------------------------------------------------------------


class TestSonicGetFacts:
    def test_get_facts(self):
        driver = _make_driver()
        driver._client.get.return_value = _mock_get_response(MOCK_SYSTEM_RESPONSE)

        facts = driver.get_facts()
        assert facts["hostname"] == "sonic-switch-01"
        assert facts["model"] == "Accton-AS7726-32X"
        assert facts["version"] == "SONiC.20230531.10"
        assert facts["serial"] == "SN1234567890"
        assert facts["vendor"] == "sonic"
        assert facts["platform"] == "sonic"

    def test_get_facts_connection_error(self):
        driver = _make_driver()
        driver._client.get.side_effect = Exception("timeout")

        with pytest.raises(ConnectionError, match="Failed to get facts"):
            driver.get_facts()


# ---------------------------------------------------------------------------
# Tests: get_interfaces
# ---------------------------------------------------------------------------


class TestSonicGetInterfaces:
    def test_get_interfaces(self):
        driver = _make_driver()
        driver._client.get.return_value = _mock_get_response(MOCK_INTERFACES_RESPONSE)

        intfs = driver.get_interfaces()
        assert "Ethernet0" in intfs
        assert "Ethernet4" in intfs

        eth0 = intfs["Ethernet0"]
        assert eth0["is_up"] is True
        assert eth0["is_enabled"] is True
        assert eth0["description"] == "uplink to spine"
        assert eth0["speed"] == 100000
        assert eth0["mtu"] == 9100
        assert eth0["mac_address"] == "00:11:22:33:44:55"

        eth4 = intfs["Ethernet4"]
        assert eth4["is_up"] is False
        assert eth4["is_enabled"] is True

    def test_get_interfaces_error(self):
        driver = _make_driver()
        driver._client.get.side_effect = Exception("connection refused")

        with pytest.raises(ConnectionError, match="Failed to get interfaces"):
            driver.get_interfaces()


# ---------------------------------------------------------------------------
# Tests: get_bgp_summary
# ---------------------------------------------------------------------------


class TestSonicGetBgpSummary:
    def test_get_bgp_summary(self):
        driver = _make_driver()
        driver._client.get.return_value = _mock_get_response(MOCK_BGP_RESPONSE)

        bgp = driver.get_bgp_summary()
        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65001
        assert "10.0.0.2" in bgp["peers"]
        assert bgp["peers"]["10.0.0.2"]["state"] == "ESTABLISHED"
        assert bgp["peers"]["10.0.0.2"]["prefixes_received"] == 150
        assert "10.0.0.3" in bgp["peers"]
        assert bgp["peers"]["10.0.0.3"]["state"] == "ACTIVE"

    def test_get_bgp_summary_custom_vrf(self):
        driver = _make_driver()
        driver._client.get.return_value = _mock_get_response(MOCK_BGP_RESPONSE)

        bgp = driver.get_bgp_summary(vrf="MGMT")
        assert bgp["router_id"] == "10.0.0.1"
        # Verify the path included the VRF
        call_args = driver._client.get.call_args
        assert "MGMT" in call_args[0][0]

    def test_get_bgp_summary_error(self):
        driver = _make_driver()
        driver._client.get.side_effect = Exception("timeout")

        with pytest.raises(ConnectionError, match="Failed to get BGP summary"):
            driver.get_bgp_summary()


# ---------------------------------------------------------------------------
# Tests: get_lldp_neighbors
# ---------------------------------------------------------------------------


class TestSonicGetLldpNeighbors:
    def test_get_lldp_neighbors(self):
        driver = _make_driver()
        driver._client.get.return_value = _mock_get_response(MOCK_LLDP_RESPONSE)

        lldp = driver.get_lldp_neighbors()
        assert "Ethernet0" in lldp
        assert len(lldp["Ethernet0"]) == 1
        assert lldp["Ethernet0"][0]["hostname"] == "spine-01"
        assert lldp["Ethernet0"][0]["port"] == "Ethernet1"
        # Ethernet4 has no neighbors, should not be in result
        assert "Ethernet4" not in lldp

    def test_get_lldp_neighbors_error(self):
        driver = _make_driver()
        driver._client.get.side_effect = Exception("timeout")

        with pytest.raises(ConnectionError, match="Failed to get LLDP neighbors"):
            driver.get_lldp_neighbors()


# ---------------------------------------------------------------------------
# Tests: get_vlans
# ---------------------------------------------------------------------------


class TestSonicGetVlans:
    def test_get_vlans(self):
        driver = _make_driver()
        # First call: vlans, second call: members
        driver._client.get.side_effect = [
            _mock_get_response(MOCK_VLANS_RESPONSE),
            _mock_get_response(MOCK_VLAN_MEMBERS_RESPONSE),
        ]

        vlans = driver.get_vlans()
        assert len(vlans) == 2

        vlan100 = vlans[0]
        assert vlan100["vlan_id"] == 100
        assert vlan100["name"] == "servers"
        assert vlan100["status"] == "active"
        assert "Ethernet0" in vlan100["interfaces"]
        assert "Ethernet4" in vlan100["interfaces"]

        vlan200 = vlans[1]
        assert vlan200["vlan_id"] == 200
        assert vlan200["name"] == "management"
        assert "Ethernet8" in vlan200["interfaces"]

    def test_get_vlans_no_members(self):
        """Vlans should still return even if members API fails."""
        driver = _make_driver()
        driver._client.get.side_effect = [
            _mock_get_response(MOCK_VLANS_RESPONSE),
            Exception("members API not available"),
        ]

        vlans = driver.get_vlans()
        assert len(vlans) == 2
        # Members should be empty since the members API failed
        assert vlans[0]["interfaces"] == []

    def test_get_vlans_error(self):
        driver = _make_driver()
        driver._client.get.side_effect = Exception("timeout")

        with pytest.raises(ConnectionError, match="Failed to get VLANs"):
            driver.get_vlans()


# ---------------------------------------------------------------------------
# Tests: get_route_table
# ---------------------------------------------------------------------------


class TestSonicGetRouteTable:
    def test_get_route_table(self):
        driver = _make_driver()
        driver._client.get.return_value = _mock_get_response(MOCK_ROUTES_RESPONSE)

        routes = driver.get_route_table()
        assert len(routes) == 3

        assert routes[0]["prefix"] == "10.0.0.0/24"
        assert routes[0]["protocol"] == "connected"
        assert routes[1]["prefix"] == "10.1.0.0/24"
        assert routes[1]["protocol"] == "bgp"
        assert routes[1]["next_hop"] == "10.0.0.2"
        assert routes[1]["metric"] == 100

    def test_get_route_table_filter_by_protocol(self):
        driver = _make_driver()
        driver._client.get.return_value = _mock_get_response(MOCK_ROUTES_RESPONSE)

        routes = driver.get_route_table(protocol="bgp")
        assert len(routes) == 1
        assert routes[0]["protocol"] == "bgp"

    def test_get_route_table_with_limit(self):
        driver = _make_driver()
        driver._client.get.return_value = _mock_get_response(MOCK_ROUTES_RESPONSE)

        routes = driver.get_route_table(limit=1)
        assert len(routes) == 1

    def test_get_route_table_custom_vrf(self):
        driver = _make_driver()
        driver._client.get.return_value = _mock_get_response(MOCK_ROUTES_RESPONSE)

        driver.get_route_table(vrf="MGMT")
        call_args = driver._client.get.call_args
        assert "MGMT" in call_args[0][0]

    def test_get_route_table_error(self):
        driver = _make_driver()
        driver._client.get.side_effect = Exception("timeout")

        with pytest.raises(ConnectionError, match="Failed to get route table"):
            driver.get_route_table()


# ---------------------------------------------------------------------------
# Tests: run_show / run_config
# ---------------------------------------------------------------------------


class TestSonicRunCommands:
    def test_run_show_mapped_command(self):
        driver = _make_driver()
        driver._client.get.return_value = _mock_get_response(MOCK_SYSTEM_RESPONSE)

        results = driver.run_show(["show version"])
        assert len(results) == 1
        assert "openconfig-system:system" in results[0]

    def test_run_show_unmapped_command(self):
        driver = _make_driver()

        results = driver.run_show(["show something-unknown"])
        assert len(results) == 1
        assert "error" in results[0]
        assert "No REST mapping" in results[0]["error"]

    def test_run_show_not_connected(self):
        driver = SonicDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_show(["show version"])

    def test_run_config(self):
        driver = _make_driver()

        results = driver.run_config(["interface Ethernet0", "shutdown"])
        assert len(results) == 2

    def test_run_config_not_connected(self):
        driver = SonicDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_config(["interface Ethernet0"])

    def test_run_commands_alias(self):
        driver = _make_driver()
        driver._client.get.return_value = _mock_get_response(MOCK_SYSTEM_RESPONSE)

        results = driver.run_commands(["show version"])
        assert len(results) == 1

    def test_config_alias(self):
        driver = _make_driver()
        results = driver.config(["interface Ethernet0"])
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Tests: Unsupported getters
# ---------------------------------------------------------------------------


class TestSonicUnsupportedGetters:
    """Verify NotSupportedError is raised for unimplemented getters."""

    def test_get_arp_table(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError):
            driver.get_arp_table()

    def test_get_config(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError):
            driver.get_config()

    def test_get_ospf_neighbors(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError):
            driver.get_ospf_neighbors()

    def test_get_mac_table(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError):
            driver.get_mac_table()

    def test_ping(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError):
            driver.ping("10.0.0.1")

    def test_traceroute(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError):
            driver.traceroute("10.0.0.1")

    def test_get_environment(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError):
            driver.get_environment()

    def test_get_cpu_memory(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError):
            driver.get_cpu_memory()

    def test_get_vrfs(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError):
            driver.get_vrfs()

    def test_get_acls(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError):
            driver.get_acls()

    def test_get_copp_policy(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError):
            driver.get_copp_policy()


# ---------------------------------------------------------------------------
# Tests: Speed parsing
# ---------------------------------------------------------------------------


class TestSonicSpeedParsing:
    def test_parse_speed_100gb(self):
        assert SonicDriver._parse_speed("SPEED_100GB") == 100000

    def test_parse_speed_10gb(self):
        assert SonicDriver._parse_speed("SPEED_10GB") == 10000

    def test_parse_speed_400gb(self):
        assert SonicDriver._parse_speed("SPEED_400GB") == 400000

    def test_parse_speed_unknown(self):
        assert SonicDriver._parse_speed("SPEED_UNKNOWN") == 0

    def test_parse_speed_empty(self):
        assert SonicDriver._parse_speed("") == 0

    def test_parse_speed_none(self):
        assert SonicDriver._parse_speed(None) == 0


# ---------------------------------------------------------------------------
# Tests: Command-to-path mapping
# ---------------------------------------------------------------------------


class TestSonicCommandMapping:
    def test_map_show_version(self):
        assert SonicDriver._map_command_to_path("show version") is not None

    def test_map_show_interfaces(self):
        assert SonicDriver._map_command_to_path("show interfaces") is not None

    def test_map_show_bgp(self):
        assert SonicDriver._map_command_to_path("show bgp summary") is not None

    def test_map_show_lldp(self):
        assert SonicDriver._map_command_to_path("show lldp neighbors") is not None

    def test_map_show_vlan(self):
        assert SonicDriver._map_command_to_path("show vlan") is not None

    def test_map_unknown_command(self):
        assert SonicDriver._map_command_to_path("show crypto pki") is None


# ---------------------------------------------------------------------------
# Tests: Driver registration
# ---------------------------------------------------------------------------


class TestSonicDriverRegistration:
    def test_sonic_in_registry(self):
        from network_mcp.drivers import DRIVER_REGISTRY

        assert "sonic" in DRIVER_REGISTRY
        assert DRIVER_REGISTRY["sonic"] is SonicDriver

    def test_sonic_platform_in_config(self):
        from network_mcp.config import Platform

        assert Platform.SONIC.value == "sonic"


# ---------------------------------------------------------------------------
# Tests: Keep-alive
# ---------------------------------------------------------------------------


class TestSonicKeepAlive:
    def test_check_alive_within_interval(self):
        """Should not probe if within keepalive interval."""
        driver = _make_driver()
        import time

        driver._last_command_time = time.monotonic()
        driver._keepalive_interval = 60
        driver._check_alive()  # should not raise or call get

        # Client.get should not have been called
        driver._client.get.assert_not_called()

    def test_check_alive_expired(self):
        """Should probe when interval expired."""
        driver = _make_driver()
        driver._last_command_time = 0.0
        driver._keepalive_interval = 0  # always expired
        driver._client.get.return_value = _mock_get_response(MOCK_SYSTEM_RESPONSE)

        driver._check_alive()
        driver._client.get.assert_called_once()

    def test_check_alive_not_connected(self):
        """Should not probe if not connected."""
        driver = SonicDriver()
        driver._check_alive()  # should not raise
