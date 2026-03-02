"""Tests for Cisco IOS-XE driver (RESTCONF + SSH fallback).

Validates IosXeDriver protocol compliance, RESTCONF/SSH transport logic,
normalized getter output, and cross-vendor compatibility with common tools.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.cisco_iosxe import IosXeDriver, _normalize_mac, _parse_ios_uptime
from network_mcp.server import conn_mgr

# Load mock data for RESTCONF response simulation
_MOCK_DIR = Path(__file__).parent / "mock_data" / "iosxe"


def _load_mock(name: str) -> dict:
    with open(_MOCK_DIR / name) as f:
        return json.load(f)


MOCK_CAPABILITIES = _load_mock("restconf_capabilities.json")
MOCK_SHOW_VERSION = _load_mock("show_version.json")
MOCK_INTERFACES = _load_mock("interfaces.json")
MOCK_BGP_SUMMARY = _load_mock("bgp_summary.json")
MOCK_ARP_TABLE = _load_mock("arp_table.json")
MOCK_LLDP_NEIGHBORS = _load_mock("lldp_neighbors.json")


# --- Driver properties ---


class TestIosXeDriverProperties:
    """Verify IosXeDriver has correct platform, vendor, and API version."""

    def test_platform(self):
        driver = IosXeDriver()
        assert driver.platform == "iosxe"

    def test_vendor(self):
        driver = IosXeDriver()
        assert driver.vendor == "cisco"

    def test_vendor_api_version(self):
        assert IosXeDriver.VENDOR_API_VERSION == "restconf-1.0"


# --- Connection lifecycle ---


class TestIosXeConnection:
    """Test RESTCONF connection with fallback to SSH."""

    def test_restconf_connect_success(self):
        """RESTCONF probe succeeds — driver uses RESTCONF transport."""
        driver = IosXeDriver()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = MOCK_CAPABILITIES

        with patch("network_mcp.drivers.cisco_iosxe.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            driver.connect(host="10.0.0.1", username="admin", password="cisco123")

        assert driver.is_connected()
        assert driver._transport == "restconf"
        assert driver._restconf is not None

    def test_restconf_connect_failure_falls_back_to_ssh(self):
        """RESTCONF probe fails — driver falls back to SSH."""
        driver = IosXeDriver()

        with patch("network_mcp.drivers.cisco_iosxe.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.get.side_effect = Exception("Connection refused")
            mock_httpx.Client.return_value = mock_client

            driver.connect(host="10.0.0.1", username="admin", password="cisco123")

        assert driver.is_connected()
        assert driver._transport == "ssh"
        assert driver._restconf is None

    def test_ssh_direct_connect(self):
        """Explicit SSH transport — no RESTCONF attempt."""
        driver = IosXeDriver()
        driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="cisco123")

        assert driver.is_connected()
        assert driver._transport == "ssh"
        assert driver._restconf is None

    def test_close(self):
        """close() cleans up RESTCONF client."""
        driver = IosXeDriver()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("network_mcp.drivers.cisco_iosxe.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            driver.connect(host="10.0.0.1", username="admin", password="cisco123")
            assert driver.is_connected()

            driver.close()
            assert not driver.is_connected()
            assert driver._restconf is None
            mock_client.close.assert_called_once()

    def test_is_connected_default(self):
        """New driver instance is not connected."""
        driver = IosXeDriver()
        assert not driver.is_connected()

    def test_run_show_not_connected(self):
        """run_show() raises ConnectionError when not connected."""
        driver = IosXeDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_show(["show version"])

    def test_run_config_not_connected(self):
        """run_config() raises ConnectionError when not connected."""
        driver = IosXeDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_config(["hostname test"])

    def test_httpx_not_available_falls_back(self):
        """When httpx is not installed, driver falls back to SSH."""
        driver = IosXeDriver()
        with patch("network_mcp.drivers.cisco_iosxe.HAS_HTTPX", False):
            driver.connect(host="10.0.0.1", username="admin", password="cisco123")
        assert driver._transport == "ssh"
        assert driver.is_connected()


# --- RESTCONF show command execution ---


class TestIosXeRestconfShow:
    """Test run_show() via RESTCONF transport."""

    @pytest.fixture
    def restconf_driver(self):
        """Create an IosXeDriver with a mock RESTCONF client."""
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True
        driver._transport = "restconf"
        return driver, mock_client

    def test_show_version_via_restconf(self, restconf_driver):
        driver, mock_client = restconf_driver
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_SHOW_VERSION
        mock_client.get.return_value = mock_resp

        result = driver.run_show(["show version"])
        assert len(result) == 1
        assert "Cisco-IOS-XE-native:native" in result[0]

    def test_show_interfaces_via_restconf(self, restconf_driver):
        driver, mock_client = restconf_driver
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_INTERFACES
        mock_client.get.return_value = mock_resp

        result = driver.run_show(["show interfaces"])
        assert len(result) == 1
        assert "ietf-interfaces:interfaces" in result[0]


# --- Normalized getters ---


class TestIosXeGetFacts:
    """Test get_facts() normalized getter."""

    def test_get_facts_restconf(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_SHOW_VERSION
        mock_client.get.return_value = mock_resp

        facts = driver.get_facts()
        assert facts["hostname"] == "csr1000v-01"
        assert facts["model"] == "CSR1000V"
        assert facts["version"] == "17.3.4a"
        assert facts["serial"] == "9ESGOBARV9D"
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "iosxe"
        assert isinstance(facts["uptime_seconds"], int)

    def test_get_facts_schema_keys(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_SHOW_VERSION
        mock_client.get.return_value = mock_resp

        facts = driver.get_facts()
        expected_keys = {"hostname", "model", "version", "serial", "uptime_seconds", "vendor", "platform"}
        assert set(facts.keys()) == expected_keys

    def test_get_facts_ssh_fallback(self):
        driver = IosXeDriver()
        driver._connected = True
        driver._restconf = None
        driver._ssh_host = "10.0.0.1"
        driver._ssh_username = "admin"

        ssh_output = (
            "Cisco IOS XE Software, Version 17.03.04a\n"
            "cisco CSR1000V (VXE) processor with 2392579K/3075K bytes of memory.\n"
            "router1 uptime is 1 year, 2 weeks, 3 days, 4 hours, 5 minutes\n"
            "Processor board ID 9ESGOBARV9D\n"
        )

        with patch.object(driver, "_ssh_exec", return_value=ssh_output):
            facts = driver.get_facts()
        assert facts["hostname"] == "router1"
        assert facts["serial"] == "9ESGOBARV9D"
        assert facts["version"] == "17.03.04a"
        assert facts["model"] == "CSR1000V"
        assert facts["uptime_seconds"] > 0
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "iosxe"


class TestIosXeGetInterfaces:
    """Test get_interfaces() normalized getter."""

    def test_get_interfaces_restconf(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_INTERFACES
        mock_client.get.return_value = mock_resp

        interfaces = driver.get_interfaces()

        assert "GigabitEthernet1" in interfaces
        assert "GigabitEthernet2" in interfaces
        assert "GigabitEthernet3" in interfaces
        assert "Loopback0" in interfaces

        gi1 = interfaces["GigabitEthernet1"]
        assert gi1["is_up"] is True
        assert gi1["is_enabled"] is True
        assert gi1["description"] == "Management interface"
        assert gi1["speed"] == 1000  # 1Gbps = 1000 Mbps
        assert gi1["mtu"] == 1500
        assert gi1["mac_address"] == "00:50:56:a0:00:01"

    def test_get_interfaces_down(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_INTERFACES
        mock_client.get.return_value = mock_resp

        interfaces = driver.get_interfaces()
        gi3 = interfaces["GigabitEthernet3"]
        assert gi3["is_up"] is False
        assert gi3["is_enabled"] is False

    def test_get_interfaces_schema(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_INTERFACES
        mock_client.get.return_value = mock_resp

        interfaces = driver.get_interfaces()
        expected_keys = {"is_up", "is_enabled", "description", "speed", "mtu", "mac_address"}
        for name, data in interfaces.items():
            assert isinstance(name, str)
            assert set(data.keys()) == expected_keys
            assert isinstance(data["is_up"], bool)
            assert isinstance(data["is_enabled"], bool)
            assert isinstance(data["description"], str)
            assert isinstance(data["speed"], int)
            assert isinstance(data["mtu"], int)
            assert isinstance(data["mac_address"], str)

    def test_get_interfaces_10g_speed(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_INTERFACES
        mock_client.get.return_value = mock_resp

        interfaces = driver.get_interfaces()
        gi2 = interfaces["GigabitEthernet2"]
        assert gi2["speed"] == 10000  # 10Gbps


class TestIosXeGetBgpSummary:
    """Test get_bgp_summary() normalized getter."""

    def test_get_bgp_summary_default_vrf(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_BGP_SUMMARY
        mock_client.get.return_value = mock_resp

        bgp = driver.get_bgp_summary()
        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65001
        assert len(bgp["peers"]) == 2
        assert "10.0.0.2" in bgp["peers"]
        assert "10.0.0.3" in bgp["peers"]

    def test_bgp_peer_details(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_BGP_SUMMARY
        mock_client.get.return_value = mock_resp

        bgp = driver.get_bgp_summary()
        peer = bgp["peers"]["10.0.0.2"]
        assert peer["state"] == "established"
        assert peer["prefixes_received"] == 85
        assert peer["uptime"] == 1296000

    def test_bgp_schema(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_BGP_SUMMARY
        mock_client.get.return_value = mock_resp

        bgp = driver.get_bgp_summary()
        assert set(bgp.keys()) == {"router_id", "as_number", "peers"}
        assert isinstance(bgp["router_id"], str)
        assert isinstance(bgp["as_number"], int)
        for peer_ip, peer_data in bgp["peers"].items():
            assert isinstance(peer_ip, str)
            assert set(peer_data.keys()) == {"state", "prefixes_received", "uptime"}
            assert isinstance(peer_data["state"], str)
            assert isinstance(peer_data["prefixes_received"], int)
            assert isinstance(peer_data["uptime"], int)


class TestIosXeGetArpTable:
    """Test get_arp_table() normalized getter."""

    def test_get_arp_table_restconf(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_ARP_TABLE
        mock_client.get.return_value = mock_resp

        arp = driver.get_arp_table()
        assert isinstance(arp, list)
        # 3 entries in default VRF + 1 in MGMT = 4 total
        assert len(arp) == 4

    def test_arp_entry_schema(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_ARP_TABLE
        mock_client.get.return_value = mock_resp

        arp = driver.get_arp_table()
        for entry in arp:
            assert set(entry.keys()) == {"ip", "mac", "interface", "age"}
            assert isinstance(entry["ip"], str)
            assert isinstance(entry["mac"], str)
            assert isinstance(entry["interface"], str)
            assert isinstance(entry["age"], float)

    def test_arp_mac_normalization(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_ARP_TABLE
        mock_client.get.return_value = mock_resp

        arp = driver.get_arp_table()
        first = arp[0]
        # "0050.56a0.0010" should become "00:50:56:a0:00:10"
        assert first["mac"] == "00:50:56:a0:00:10"
        assert first["ip"] == "10.0.0.2"
        assert first["interface"] == "GigabitEthernet2"


class TestIosXeGetLldpNeighbors:
    """Test get_lldp_neighbors() normalized getter."""

    def test_get_lldp_neighbors_restconf(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_LLDP_NEIGHBORS
        mock_client.get.return_value = mock_resp

        lldp = driver.get_lldp_neighbors()
        assert isinstance(lldp, dict)
        assert "GigabitEthernet2" in lldp
        assert "GigabitEthernet1" in lldp

    def test_lldp_neighbor_details(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_LLDP_NEIGHBORS
        mock_client.get.return_value = mock_resp

        lldp = driver.get_lldp_neighbors()
        gi2_neighbors = lldp["GigabitEthernet2"]
        assert len(gi2_neighbors) == 1
        assert gi2_neighbors[0]["hostname"] == "spine-01.lab.local"
        assert gi2_neighbors[0]["port"] == "Ethernet1/1"
        assert "Arista" in gi2_neighbors[0]["system_description"]

    def test_lldp_schema(self):
        driver = IosXeDriver()
        mock_client = MagicMock()
        driver._restconf = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_LLDP_NEIGHBORS
        mock_client.get.return_value = mock_resp

        lldp = driver.get_lldp_neighbors()
        for intf, neighbors in lldp.items():
            assert isinstance(intf, str)
            assert isinstance(neighbors, list)
            for n in neighbors:
                assert set(n.keys()) == {"hostname", "port", "system_description"}
                assert isinstance(n["hostname"], str)
                assert isinstance(n["port"], str)
                assert isinstance(n["system_description"], str)


class TestIosXeGetConfig:
    """Test get_config() normalized getter."""

    def test_get_config(self):
        driver = IosXeDriver()
        driver._connected = True
        driver._restconf = None
        driver._ssh_host = "10.0.0.1"
        driver._ssh_username = "admin"

        with patch.object(driver, "_ssh_exec", return_value="hostname csr1000v-01\n!\nend"):
            config = driver.get_config()
        assert "running" in config
        assert "startup" in config
        assert isinstance(config["running"], str)
        assert isinstance(config["startup"], str)

    def test_get_config_sanitized(self):
        driver = IosXeDriver()
        driver._connected = True
        driver._restconf = None
        driver._ssh_host = "10.0.0.1"
        driver._ssh_username = "admin"

        config_text = "hostname csr1000v-01\n enable secret 5 $1$abc\n!\nend"
        with patch.object(driver, "_ssh_exec", return_value=config_text):
            config = driver.get_config(sanitized=True)
        assert "***REDACTED***" in config["running"]


# --- Helper functions ---


class TestHelperFunctions:
    """Test MAC normalization and IOS uptime parsing."""

    def test_normalize_mac_dot_notation(self):
        assert _normalize_mac("0050.56a0.0001") == "00:50:56:a0:00:01"

    def test_normalize_mac_colon_notation(self):
        assert _normalize_mac("00:50:56:a0:00:01") == "00:50:56:a0:00:01"

    def test_normalize_mac_hyphen_notation(self):
        assert _normalize_mac("0050-56a0-0001") == "00:50:56:a0:00:01"

    def test_normalize_mac_empty(self):
        assert _normalize_mac("") == ""

    def test_normalize_mac_uppercase(self):
        assert _normalize_mac("0050.56A0.0001") == "00:50:56:a0:00:01"

    def test_normalize_mac_invalid(self):
        # Short MAC should be returned as-is
        assert _normalize_mac("abc") == "abc"

    def test_parse_uptime_full(self):
        line = "router1 uptime is 2 years, 3 weeks, 4 days, 5 hours, 6 minutes"
        result = _parse_ios_uptime(line)
        expected = (2 * 365 * 24 * 3600) + (3 * 7 * 24 * 3600) + (4 * 24 * 3600) + (5 * 3600) + (6 * 60)
        assert result == expected

    def test_parse_uptime_partial(self):
        line = "router1 uptime is 5 hours, 30 minutes"
        result = _parse_ios_uptime(line)
        assert result == (5 * 3600) + (30 * 60)

    def test_parse_uptime_empty(self):
        assert _parse_ios_uptime("") == 0


# --- MockIosXeDriver ---


class TestMockIosXeDriver:
    """Test that MockIosXeDriver satisfies NetworkDriver protocol."""

    def test_properties(self):
        from tests.mock_data.iosxe.mock_driver import MockIosXeDriver

        driver = MockIosXeDriver()
        assert driver.platform == "iosxe"
        assert driver.vendor == "cisco"

    def test_connect_and_close(self):
        from tests.mock_data.iosxe.mock_driver import MockIosXeDriver

        driver = MockIosXeDriver()
        assert not driver.is_connected()
        driver.connect(host="test-device")
        assert driver.is_connected()
        driver.close()
        assert not driver.is_connected()

    def test_get_facts(self):
        from tests.mock_data.iosxe.mock_driver import MockIosXeDriver

        driver = MockIosXeDriver()
        driver.connect()
        facts = driver.get_facts()
        assert facts["hostname"] == "csr1000v-01"
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "iosxe"
        expected_keys = {"hostname", "model", "version", "serial", "uptime_seconds", "vendor", "platform"}
        assert set(facts.keys()) == expected_keys

    def test_get_interfaces(self):
        from tests.mock_data.iosxe.mock_driver import MockIosXeDriver

        driver = MockIosXeDriver()
        driver.connect()
        interfaces = driver.get_interfaces()
        assert "GigabitEthernet1" in interfaces
        expected_keys = {"is_up", "is_enabled", "description", "speed", "mtu", "mac_address"}
        for data in interfaces.values():
            assert set(data.keys()) == expected_keys

    def test_get_bgp_summary(self):
        from tests.mock_data.iosxe.mock_driver import MockIosXeDriver

        driver = MockIosXeDriver()
        driver.connect()
        bgp = driver.get_bgp_summary()
        assert set(bgp.keys()) == {"router_id", "as_number", "peers"}
        assert bgp["as_number"] == 65001

    def test_get_arp_table(self):
        from tests.mock_data.iosxe.mock_driver import MockIosXeDriver

        driver = MockIosXeDriver()
        driver.connect()
        arp = driver.get_arp_table()
        assert isinstance(arp, list)
        assert len(arp) > 0
        for entry in arp:
            assert set(entry.keys()) == {"ip", "mac", "interface", "age"}

    def test_get_lldp_neighbors(self):
        from tests.mock_data.iosxe.mock_driver import MockIosXeDriver

        driver = MockIosXeDriver()
        driver.connect()
        lldp = driver.get_lldp_neighbors()
        assert isinstance(lldp, dict)
        for _intf, neighbors in lldp.items():
            for n in neighbors:
                assert set(n.keys()) == {"hostname", "port", "system_description"}

    def test_get_config(self):
        from tests.mock_data.iosxe.mock_driver import MockIosXeDriver

        driver = MockIosXeDriver()
        driver.connect()
        config = driver.get_config()
        assert "running" in config
        assert "startup" in config
        assert isinstance(config["running"], str)
        assert isinstance(config["startup"], str)

    def test_run_show_not_connected(self):
        from tests.mock_data.iosxe.mock_driver import MockIosXeDriver

        driver = MockIosXeDriver()
        with pytest.raises(ConnectionError):
            driver.run_show(["show version"])


# --- Common tools with IOS-XE mock driver ---


class TestCommonToolsWithIosXe:
    """Verify common tools dispatch correctly to IOS-XE driver."""

    @pytest.fixture
    def iosxe_mock_driver(self):
        from tests.mock_data.iosxe.mock_driver import MockIosXeDriver

        driver = MockIosXeDriver()
        driver.connect(host="csr1000v-01")
        return driver

    @pytest.fixture
    def mock_iosxe_conn_mgr(self, iosxe_mock_driver):
        @contextmanager
        def _mock_acquire(host):
            yield iosxe_mock_driver

        with (
            patch.object(conn_mgr, "get_driver", return_value=iosxe_mock_driver),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        ):
            yield

    def test_net_get_device_info(self, mock_iosxe_conn_mgr):
        from network_mcp.tools.common.device import net_get_device_info

        result = net_get_device_info("csr1000v-01")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"
        assert result["data"]["hostname"] == "csr1000v-01"

    def test_net_get_interfaces(self, mock_iosxe_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interfaces

        result = net_get_interfaces("csr1000v-01")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"
        assert "GigabitEthernet1" in result["data"]
        assert "GigabitEthernet2" in result["data"]

    def test_net_get_bgp_summary(self, mock_iosxe_conn_mgr):
        from network_mcp.tools.common.routing import net_get_bgp_summary

        result = net_get_bgp_summary("csr1000v-01")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"
        assert result["data"]["as_number"] == 65001

    def test_net_get_lldp_neighbors(self, mock_iosxe_conn_mgr):
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        result = net_get_lldp_neighbors("csr1000v-01")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"
        assert "GigabitEthernet2" in result["data"]

    def test_net_get_interface_detail(self, mock_iosxe_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_detail

        result = net_get_interface_detail("csr1000v-01", "GigabitEthernet1")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"
        assert "GigabitEthernet1" in result["data"]

    def test_net_get_interface_detail_not_found(self, mock_iosxe_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_detail

        result = net_get_interface_detail("csr1000v-01", "Ethernet99")
        assert result["status"] == "error"
        assert "not found" in result["error"]


# --- DRIVER_REGISTRY ---


class TestDriverRegistry:
    """Verify IosXeDriver is registered in DRIVER_REGISTRY."""

    def test_iosxe_in_registry(self):
        from network_mcp.drivers import DRIVER_REGISTRY

        assert "iosxe" in DRIVER_REGISTRY
        assert DRIVER_REGISTRY["iosxe"] is IosXeDriver

    def test_eos_still_in_registry(self):
        from network_mcp.drivers import DRIVER_REGISTRY

        assert "eos" in DRIVER_REGISTRY

    def test_registry_driver_instantiation(self):
        from network_mcp.drivers import DRIVER_REGISTRY

        driver = DRIVER_REGISTRY["iosxe"]()
        assert driver.platform == "iosxe"
        assert driver.vendor == "cisco"


class TestIosXeUtilityFunctions:
    """Test IOS-XE driver utility functions in isolation."""

    def test_driver_not_connected_run_show_raises(self):
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        driver = IosXeDriver()
        with pytest.raises(ConnectionError):
            driver.run_show(["show version"])

    def test_driver_not_connected_run_config_raises(self):
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        driver = IosXeDriver()
        with pytest.raises(ConnectionError):
            driver.run_config(["vlan 100"])

    def test_driver_is_connected_default_false(self):
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        driver = IosXeDriver()
        assert driver.is_connected() is False

    def test_driver_close_when_not_connected(self):
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        driver = IosXeDriver()
        driver.close()
        assert driver.is_connected() is False

    def test_driver_backward_compat_aliases(self):
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        driver = IosXeDriver()
        assert hasattr(driver, "run_commands")
        assert hasattr(driver, "config")

    def test_vendor_api_version(self):
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        driver = IosXeDriver()
        assert driver.VENDOR_API_VERSION == "restconf-1.0"

    def test_platform_and_vendor(self):
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        driver = IosXeDriver()
        assert driver.platform == "iosxe"
        assert driver.vendor == "cisco"


class TestIosXeParserFunctions:
    """Test IOS-XE parser/utility functions."""

    def test_normalize_mac_dot_notation(self):
        from network_mcp.drivers.cisco_iosxe import _normalize_mac

        assert _normalize_mac("0050.56a0.0001") == "00:50:56:a0:00:01"

    def test_normalize_mac_colon_notation(self):
        from network_mcp.drivers.cisco_iosxe import _normalize_mac

        assert _normalize_mac("00:50:56:a0:00:01") == "00:50:56:a0:00:01"

    def test_normalize_mac_hyphen_notation(self):
        from network_mcp.drivers.cisco_iosxe import _normalize_mac

        assert _normalize_mac("0050-56a0-0001") == "00:50:56:a0:00:01"

    def test_normalize_mac_empty(self):
        from network_mcp.drivers.cisco_iosxe import _normalize_mac

        assert _normalize_mac("") == ""

    def test_normalize_mac_invalid_length(self):
        from network_mcp.drivers.cisco_iosxe import _normalize_mac

        assert _normalize_mac("001122") == "001122"

    def test_parse_ios_uptime_full(self):
        from network_mcp.drivers.cisco_iosxe import _parse_ios_uptime

        line = "router1 uptime is 2 years, 3 weeks, 4 days, 5 hours, 6 minutes"
        result = _parse_ios_uptime(line)
        expected = 2 * 365 * 86400 + 3 * 7 * 86400 + 4 * 86400 + 5 * 3600 + 6 * 60
        assert result == expected

    def test_parse_ios_uptime_days_hours(self):
        from network_mcp.drivers.cisco_iosxe import _parse_ios_uptime

        assert _parse_ios_uptime("uptime is 10 days, 5 hours") == 10 * 86400 + 5 * 3600

    def test_parse_ios_uptime_no_match(self):
        from network_mcp.drivers.cisco_iosxe import _parse_ios_uptime

        assert _parse_ios_uptime("no uptime info") == 0

    def test_normalize_facts_ssh(self):
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        output = """Cisco IOS XE Software, Version 17.09.04a
Cisco IOS Software [Cupertino], Catalyst L3 Switch Software (CAT9K_IOSXE), Version 17.9.4a
cisco C9300-48P (ARM64) processor with 1318078K/6147K bytes of memory.
Processor board ID FOC2234L0HB
Router uptime is 125 days, 3 hours, 45 minutes"""
        facts = IosXeDriver._normalize_facts_ssh(output)
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "iosxe"
        assert facts["serial"] == "FOC2234L0HB"
        assert facts["uptime_seconds"] > 0

    def test_normalize_interfaces_restconf(self):
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        raw = [
            {
                "name": "GigabitEthernet1",
                "oper-status": "up",
                "enabled": True,
                "description": "Management",
                "speed": 1000000000,
                "mtu": 1500,
                "phys-address": "00:50:56:a0:00:01",
            },
            {
                "name": "GigabitEthernet2",
                "oper-status": "down",
                "enabled": False,
                "speed": 0,
            },
        ]
        result = IosXeDriver._normalize_interfaces(raw)
        assert len(result) == 2
        assert result["GigabitEthernet1"]["is_up"] is True
        assert result["GigabitEthernet1"]["speed"] == 1000
        assert result["GigabitEthernet1"]["description"] == "Management"
        assert result["GigabitEthernet2"]["is_up"] is False

    def test_normalize_interfaces_ssh(self):
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        output = """GigabitEthernet1 is up, line protocol is up
  Description: Management Interface
  MTU 1500 bytes, BW 1000000 Kbit/sec
  Hardware is CSR vNIC, address is 5000.0001.0001
GigabitEthernet2 is administratively down, line protocol is down
  MTU 1500 bytes, BW 1000000 Kbit/sec
  Hardware is CSR vNIC, address is 5000.0001.0002"""
        result = IosXeDriver._normalize_interfaces_ssh(output)
        assert len(result) == 2
        assert result["GigabitEthernet1"]["is_up"] is True
        assert result["GigabitEthernet1"]["is_enabled"] is True
        assert result["GigabitEthernet1"]["description"] == "Management Interface"
        assert result["GigabitEthernet1"]["speed"] == 1000
        assert result["GigabitEthernet2"]["is_up"] is False
        assert result["GigabitEthernet2"]["is_enabled"] is False
