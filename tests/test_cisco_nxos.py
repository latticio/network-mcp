"""Tests for Cisco NX-OS driver (NX-API + SSH fallback).

Validates NxosDriver protocol compliance, NX-API transport logic,
TABLE_*/ROW_* response parsing, normalized getter output, and
cross-vendor compatibility with common tools.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.cisco_nxos import (
    NxosDriver,
    _normalize_mac,
    _parse_arp_age,
    _parse_bgp_uptime,
    _parse_nxos_uptime,
    _parse_speed,
    _unwrap_table,
)
from network_mcp.server import conn_mgr

# Load mock data for NX-API response simulation
_MOCK_DIR = Path(__file__).parent / "mock_data" / "nxos"


def _load_mock(name: str) -> dict:
    with open(_MOCK_DIR / name) as f:
        return json.load(f)


MOCK_SHOW_VERSION = _load_mock("show_version.json")
MOCK_SHOW_INTERFACE = _load_mock("show_interface.json")
MOCK_BGP_SUMMARY = _load_mock("show_ip_bgp_summary.json")
MOCK_ARP_TABLE = _load_mock("show_ip_arp.json")
MOCK_LLDP_NEIGHBORS = _load_mock("show_lldp_neighbors_detail.json")


def _extract_body(mock_data: dict) -> dict:
    """Extract the body from an NX-API mock response envelope."""
    return mock_data.get("ins_api", {}).get("outputs", {}).get("output", {}).get("body", {})


# --- Driver properties ---


class TestNxosDriverProperties:
    """Verify NxosDriver has correct platform, vendor, and API version."""

    def test_platform(self):
        driver = NxosDriver()
        assert driver.platform == "nxos"

    def test_vendor(self):
        driver = NxosDriver()
        assert driver.vendor == "cisco"

    def test_vendor_api_version(self):
        assert NxosDriver.VENDOR_API_VERSION == "nxapi-1.0"


# --- NX-API payload construction ---


class TestNxapiPayload:
    """Test NX-API payload construction."""

    def test_build_show_payload(self):
        payload = NxosDriver._build_nxapi_payload("show version")
        assert payload["ins_api"]["version"] == "1"
        assert payload["ins_api"]["type"] == "cli_show"
        assert payload["ins_api"]["chunk"] == "0"
        assert payload["ins_api"]["sid"] == "1"
        assert payload["ins_api"]["input"] == "show version"
        assert payload["ins_api"]["output_format"] == "json"

    def test_build_config_payload(self):
        payload = NxosDriver._build_nxapi_payload("vlan 100 ; name SERVERS", cmd_type="cli_conf")
        assert payload["ins_api"]["type"] == "cli_conf"
        assert payload["ins_api"]["input"] == "vlan 100 ; name SERVERS"

    def test_build_show_ascii_payload(self):
        payload = NxosDriver._build_nxapi_payload("show running-config", cmd_type="cli_show_ascii")
        assert payload["ins_api"]["type"] == "cli_show_ascii"


# --- NX-API response parsing ---


class TestNxapiResponseParsing:
    """Test _parse_nxapi_response for single/multi-command and error responses."""

    def test_parse_single_command_success(self):
        response = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "200",
                        "msg": "Success",
                        "body": {"host_name": "n9k-spine-01"},
                    }
                }
            }
        }
        results = NxosDriver()._parse_nxapi_response(response)
        assert len(results) == 1
        assert results[0]["host_name"] == "n9k-spine-01"

    def test_parse_multi_command_success(self):
        response = {
            "ins_api": {
                "outputs": {
                    "output": [
                        {"code": "200", "msg": "Success", "body": {"host_name": "n9k"}},
                        {"code": "200", "msg": "Success", "body": {"vlans": {}}},
                    ]
                }
            }
        }
        results = NxosDriver()._parse_nxapi_response(response)
        assert len(results) == 2
        assert results[0]["host_name"] == "n9k"
        assert "vlans" in results[1]

    def test_parse_error_response(self):
        response = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "400",
                        "msg": "Invalid command",
                        "body": {},
                    }
                }
            }
        }
        results = NxosDriver()._parse_nxapi_response(response)
        assert len(results) == 1
        assert "error" in results[0]
        assert "400" in results[0]["error"]

    def test_parse_text_body(self):
        response = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "200",
                        "msg": "Success",
                        "body": "hostname n9k-spine-01\n!\nend",
                    }
                }
            }
        }
        results = NxosDriver()._parse_nxapi_response(response)
        assert len(results) == 1
        assert results[0]["output"] == "hostname n9k-spine-01\n!\nend"

    def test_parse_empty_body(self):
        response = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "200",
                        "msg": "Success",
                        "body": {},
                    }
                }
            }
        }
        results = NxosDriver()._parse_nxapi_response(response)
        assert len(results) == 1
        assert results[0] == {}

    def test_parse_real_show_version(self):
        """Parse a realistic show version NX-API response."""
        results = NxosDriver()._parse_nxapi_response(MOCK_SHOW_VERSION)
        assert len(results) == 1
        assert results[0]["host_name"] == "n9k-spine-01"
        assert results[0]["chassis_id"] == "Nexus9000 C9396PX"


# --- TABLE_*/ROW_* unwrap helper ---


class TestUnwrapTable:
    """Test the _unwrap_table helper for NX-API nesting."""

    def test_unwrap_list(self):
        data = {"TABLE_interface": {"ROW_interface": [{"name": "eth1"}, {"name": "eth2"}]}}
        result = _unwrap_table(data, "TABLE_interface", "ROW_interface")
        assert len(result) == 2
        assert result[0]["name"] == "eth1"

    def test_unwrap_single_dict(self):
        """Single-element result is a dict, not a list — must be wrapped."""
        data = {"TABLE_interface": {"ROW_interface": {"name": "eth1"}}}
        result = _unwrap_table(data, "TABLE_interface", "ROW_interface")
        assert len(result) == 1
        assert result[0]["name"] == "eth1"

    def test_unwrap_missing_table(self):
        data = {}
        result = _unwrap_table(data, "TABLE_interface", "ROW_interface")
        assert result == []

    def test_unwrap_empty_table(self):
        data = {"TABLE_interface": {}}
        result = _unwrap_table(data, "TABLE_interface", "ROW_interface")
        assert result == []

    def test_unwrap_missing_row(self):
        data = {"TABLE_interface": {"ROW_other": [{"name": "eth1"}]}}
        result = _unwrap_table(data, "TABLE_interface", "ROW_interface")
        assert result == []


# --- Connection lifecycle ---


class TestNxosConnection:
    """Test NX-API connection with fallback to SSH."""

    def test_nxapi_connect_success(self):
        """NX-API probe succeeds — driver uses NX-API transport."""
        driver = NxosDriver()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = MOCK_SHOW_VERSION

        with patch("network_mcp.drivers.cisco_nxos.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            driver.connect(host="10.0.0.1", username="admin", password="cisco123")

        assert driver.is_connected()
        assert driver._transport == "nxapi"
        assert driver._nxapi is not None

    def test_nxapi_connect_failure_falls_back_to_ssh(self):
        """NX-API probe fails — driver falls back to SSH."""
        driver = NxosDriver()

        with patch("network_mcp.drivers.cisco_nxos.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.post.side_effect = Exception("Connection refused")
            mock_httpx.Client.return_value = mock_client

            driver.connect(host="10.0.0.1", username="admin", password="cisco123")

        assert driver.is_connected()
        assert driver._transport == "ssh"
        assert driver._nxapi is None

    def test_ssh_direct_connect(self):
        """Explicit SSH transport — no NX-API attempt."""
        driver = NxosDriver()
        driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="cisco123")

        assert driver.is_connected()
        assert driver._transport == "ssh"
        assert driver._nxapi is None

    def test_close(self):
        """close() cleans up NX-API client."""
        driver = NxosDriver()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = MOCK_SHOW_VERSION

        with patch("network_mcp.drivers.cisco_nxos.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            driver.connect(host="10.0.0.1", username="admin", password="cisco123")
            assert driver.is_connected()

            driver.close()
            assert not driver.is_connected()
            assert driver._nxapi is None
            mock_client.close.assert_called_once()

    def test_is_connected_default(self):
        """New driver instance is not connected."""
        driver = NxosDriver()
        assert not driver.is_connected()

    def test_run_show_not_connected(self):
        """run_show() raises ConnectionError when not connected."""
        driver = NxosDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_show(["show version"])

    def test_run_config_not_connected(self):
        """run_config() raises ConnectionError when not connected."""
        driver = NxosDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_config(["vlan 100"])

    def test_httpx_not_available_falls_back(self):
        """When httpx is not installed, driver falls back to SSH."""
        driver = NxosDriver()
        with patch("network_mcp.drivers.cisco_nxos.HAS_HTTPX", False):
            driver.connect(host="10.0.0.1", username="admin", password="cisco123")
        assert driver._transport == "ssh"
        assert driver.is_connected()


# --- NX-API show command execution ---


class TestNxosNxapiShow:
    """Test run_show() via NX-API transport."""

    @pytest.fixture
    def nxapi_driver(self):
        """Create an NxosDriver with a mock NX-API client."""
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True
        driver._transport = "nxapi"
        return driver, mock_client

    def test_show_version_via_nxapi(self, nxapi_driver):
        driver, mock_client = nxapi_driver
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_SHOW_VERSION
        mock_client.post.return_value = mock_resp

        result = driver.run_show(["show version"])
        assert len(result) == 1
        assert "host_name" in result[0]
        assert result[0]["host_name"] == "n9k-spine-01"

    def test_show_interface_via_nxapi(self, nxapi_driver):
        driver, mock_client = nxapi_driver
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_SHOW_INTERFACE
        mock_client.post.return_value = mock_resp

        result = driver.run_show(["show interface"])
        assert len(result) == 1
        assert "TABLE_interface" in result[0]

    def test_run_show_text_encoding(self, nxapi_driver):
        """Text encoding uses cli_show_ascii type."""
        driver, mock_client = nxapi_driver
        text_response = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "200",
                        "msg": "Success",
                        "body": "hostname n9k-spine-01\n!\nend",
                    }
                }
            }
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = text_response
        mock_client.post.return_value = mock_resp

        result = driver.run_show(["show running-config"], encoding="text")
        assert len(result) == 1
        # Verify cli_show_ascii type was used
        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json", call_args[1].get("json", {}))
        assert payload["ins_api"]["type"] == "cli_show_ascii"


# --- Normalized getters ---


class TestNxosGetFacts:
    """Test get_facts() normalized getter."""

    def test_get_facts_nxapi(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_SHOW_VERSION
        mock_client.post.return_value = mock_resp

        facts = driver.get_facts()
        assert facts["hostname"] == "n9k-spine-01"
        assert facts["model"] == "Nexus9000 C9396PX"
        assert facts["version"] == "10.3(2)"
        assert facts["serial"] == "SAL1911C4LR"
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "nxos"
        assert isinstance(facts["uptime_seconds"], int)
        assert facts["uptime_seconds"] > 0

    def test_get_facts_uptime_calculation(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_SHOW_VERSION
        mock_client.post.return_value = mock_resp

        facts = driver.get_facts()
        # 125 days + 3 hours + 45 minutes + 12 seconds
        expected = (125 * 86400) + (3 * 3600) + (45 * 60) + 12
        assert facts["uptime_seconds"] == expected

    def test_get_facts_schema_keys(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_SHOW_VERSION
        mock_client.post.return_value = mock_resp

        facts = driver.get_facts()
        expected_keys = {"hostname", "model", "version", "serial", "uptime_seconds", "vendor", "platform"}
        assert set(facts.keys()) == expected_keys

    def test_get_facts_ssh_fallback(self):
        driver = NxosDriver()
        driver._connected = True
        driver._nxapi = None
        driver._ssh_host = "10.0.0.1"
        driver._ssh_username = "admin"

        ssh_output = (
            "Cisco Nexus Operating System (NX-OS) Software\n"
            "  NXOS: version 10.3(2)\n"
            "  Device name: n9k-spine-01\n"
            "  cisco Nexus9000 C9396PX Chassis\n"
            "  Processor Board ID SAL1911C4LR\n"
            "  Kernel uptime is 125 day(s), 3 hour(s), 45 minute(s), 12 second(s)\n"
        )

        with patch.object(driver, "_ssh_exec", return_value=ssh_output):
            facts = driver.get_facts()
        assert facts["hostname"] == "n9k-spine-01"
        assert facts["serial"] == "SAL1911C4LR"
        assert facts["version"] == "10.3(2)"
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "nxos"


class TestNxosGetInterfaces:
    """Test get_interfaces() normalized getter."""

    def test_get_interfaces_nxapi(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_SHOW_INTERFACE
        mock_client.post.return_value = mock_resp

        interfaces = driver.get_interfaces()

        assert "Ethernet1/1" in interfaces
        assert "Ethernet1/2" in interfaces
        assert "Ethernet1/3" in interfaces
        assert "mgmt0" in interfaces
        assert "loopback0" in interfaces

        eth1 = interfaces["Ethernet1/1"]
        assert eth1["is_up"] is True
        assert eth1["is_enabled"] is True
        assert eth1["description"] == "Uplink to spine-02"
        assert eth1["speed"] == 10000  # 10 Gb/s -> 10000 Mbps
        assert eth1["mtu"] == 9216
        assert eth1["mac_address"] == "00:50:56:a0:01:01"

    def test_get_interfaces_down(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_SHOW_INTERFACE
        mock_client.post.return_value = mock_resp

        interfaces = driver.get_interfaces()
        eth3 = interfaces["Ethernet1/3"]
        assert eth3["is_up"] is False
        assert eth3["is_enabled"] is False

    def test_get_interfaces_mgmt(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_SHOW_INTERFACE
        mock_client.post.return_value = mock_resp

        interfaces = driver.get_interfaces()
        mgmt = interfaces["mgmt0"]
        assert mgmt["is_up"] is True
        assert mgmt["description"] == "Management interface"
        assert mgmt["speed"] == 1000  # 1000 Mb/s
        assert mgmt["mtu"] == 1500

    def test_get_interfaces_schema(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_SHOW_INTERFACE
        mock_client.post.return_value = mock_resp

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


class TestNxosGetBgpSummary:
    """Test get_bgp_summary() normalized getter."""

    def test_get_bgp_summary_default_vrf(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_BGP_SUMMARY
        mock_client.post.return_value = mock_resp

        bgp = driver.get_bgp_summary()
        assert bgp["router_id"] == "10.255.0.1"
        assert bgp["as_number"] == 65001
        assert len(bgp["peers"]) == 2
        assert "10.0.0.2" in bgp["peers"]
        assert "10.0.0.3" in bgp["peers"]

    def test_bgp_peer_details(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_BGP_SUMMARY
        mock_client.post.return_value = mock_resp

        bgp = driver.get_bgp_summary()
        peer = bgp["peers"]["10.0.0.2"]
        assert peer["state"] == "Established"
        assert peer["prefixes_received"] == 12
        assert peer["uptime"] > 0

    def test_bgp_deeply_nested_vrf_structure(self):
        """Verify the TABLE_vrf -> TABLE_af -> TABLE_saf -> TABLE_neighbor nesting."""
        body = _extract_body(MOCK_BGP_SUMMARY)
        bgp = NxosDriver._normalize_bgp_nxapi(body, "default")
        assert bgp["router_id"] == "10.255.0.1"
        assert bgp["as_number"] == 65001
        assert len(bgp["peers"]) == 2

    def test_bgp_single_vrf_row(self):
        """BGP mock has single VRF row (dict not list) — test unwrap."""
        body = _extract_body(MOCK_BGP_SUMMARY)
        # The mock has a single ROW_vrf as a dict — verify _unwrap_table handles it
        vrf_rows = _unwrap_table(body, "TABLE_vrf", "ROW_vrf")
        assert len(vrf_rows) == 1
        assert vrf_rows[0]["vrf-name-out"] == "default"

    def test_bgp_schema(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_BGP_SUMMARY
        mock_client.post.return_value = mock_resp

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


class TestNxosGetArpTable:
    """Test get_arp_table() normalized getter."""

    def test_get_arp_table_nxapi(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_ARP_TABLE
        mock_client.post.return_value = mock_resp

        arp = driver.get_arp_table()
        assert isinstance(arp, list)
        # 3 entries in default VRF + 1 in management VRF = 4 total
        assert len(arp) == 4

    def test_arp_entry_schema(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_ARP_TABLE
        mock_client.post.return_value = mock_resp

        arp = driver.get_arp_table()
        for entry in arp:
            assert set(entry.keys()) == {"ip", "mac", "interface", "age"}
            assert isinstance(entry["ip"], str)
            assert isinstance(entry["mac"], str)
            assert isinstance(entry["interface"], str)
            assert isinstance(entry["age"], float)

    def test_arp_mac_normalization(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_ARP_TABLE
        mock_client.post.return_value = mock_resp

        arp = driver.get_arp_table()
        first = arp[0]
        # "0050.56a0.0201" should become "00:50:56:a0:02:01"
        assert first["mac"] == "00:50:56:a0:02:01"
        assert first["ip"] == "10.0.0.2"
        assert first["interface"] == "Ethernet1/1"

    def test_arp_single_entry_vrf(self):
        """Management VRF has a single ARP entry (dict not list) — test unwrap."""
        body = _extract_body(MOCK_ARP_TABLE)
        vrf_rows = _unwrap_table(body, "TABLE_vrf", "ROW_vrf")
        mgmt_vrf = vrf_rows[1]
        assert mgmt_vrf["vrf-name-out"] == "management"
        # Single entry — ROW_adj is a dict
        adj_rows = _unwrap_table(mgmt_vrf, "TABLE_adj", "ROW_adj")
        assert len(adj_rows) == 1
        assert adj_rows[0]["ip-addr-out"] == "192.168.1.1"

    def test_arp_age_parsing(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_ARP_TABLE
        mock_client.post.return_value = mock_resp

        arp = driver.get_arp_table()
        first = arp[0]
        # "00:15:32" -> 932 seconds
        assert first["age"] == (0 * 3600) + (15 * 60) + 32


class TestNxosGetLldpNeighbors:
    """Test get_lldp_neighbors() normalized getter."""

    def test_get_lldp_neighbors_nxapi(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_LLDP_NEIGHBORS
        mock_client.post.return_value = mock_resp

        lldp = driver.get_lldp_neighbors()
        assert isinstance(lldp, dict)
        assert "Ethernet1/1" in lldp
        assert "Ethernet1/2" in lldp

    def test_lldp_neighbor_details(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_LLDP_NEIGHBORS
        mock_client.post.return_value = mock_resp

        lldp = driver.get_lldp_neighbors()
        # Ethernet1/1 has 2 neighbors (spine-02 and arista-leaf-03)
        assert len(lldp["Ethernet1/1"]) == 2
        hostnames = [n["hostname"] for n in lldp["Ethernet1/1"]]
        assert "n9k-spine-02" in hostnames
        assert "arista-leaf-03.lab.local" in hostnames

    def test_lldp_schema(self):
        driver = NxosDriver()
        mock_client = MagicMock()
        driver._nxapi = mock_client
        driver._connected = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = MOCK_LLDP_NEIGHBORS
        mock_client.post.return_value = mock_resp

        lldp = driver.get_lldp_neighbors()
        for intf, neighbors in lldp.items():
            assert isinstance(intf, str)
            assert isinstance(neighbors, list)
            for n in neighbors:
                assert set(n.keys()) == {"hostname", "port", "system_description"}
                assert isinstance(n["hostname"], str)
                assert isinstance(n["port"], str)
                assert isinstance(n["system_description"], str)


class TestNxosGetConfig:
    """Test get_config() normalized getter."""

    def test_get_config_ssh_fallback(self):
        driver = NxosDriver()
        driver._connected = True
        driver._nxapi = None
        driver._ssh_host = "10.0.0.1"
        driver._ssh_username = "admin"

        with patch.object(driver, "_ssh_exec", return_value="hostname n9k-spine-01\n!\nend"):
            config = driver.get_config()
        assert "running" in config
        assert "startup" in config
        assert isinstance(config["running"], str)
        assert isinstance(config["startup"], str)

    def test_get_config_sanitized(self):
        driver = NxosDriver()
        driver._connected = True
        driver._nxapi = None
        driver._ssh_host = "10.0.0.1"
        driver._ssh_username = "admin"

        config_text = "hostname n9k-spine-01\n enable secret 5 $1$abc\n!\nend"
        with patch.object(driver, "_ssh_exec", return_value=config_text):
            config = driver.get_config(sanitized=True)
        assert "***REDACTED***" in config["running"]


# --- Helper functions ---


class TestHelperFunctions:
    """Test MAC normalization, speed parsing, uptime parsing, and ARP age parsing."""

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
        assert _normalize_mac("abc") == "abc"

    def test_parse_speed_gbps_string(self):
        assert _parse_speed("10 Gb/s") == 10000

    def test_parse_speed_mbps_string(self):
        assert _parse_speed("1000 Mb/s") == 1000

    def test_parse_speed_auto(self):
        assert _parse_speed("auto") == 0

    def test_parse_speed_integer_kbps(self):
        assert _parse_speed(10000000) == 10000

    def test_parse_speed_integer_mbps(self):
        assert _parse_speed(1000) == 1

    def test_parse_speed_empty(self):
        assert _parse_speed("") == 0

    def test_parse_nxos_uptime(self):
        line = "Kernel uptime is 125 day(s), 3 hour(s), 45 minute(s), 12 second(s)"
        expected = (125 * 86400) + (3 * 3600) + (45 * 60) + 12
        assert _parse_nxos_uptime(line) == expected

    def test_parse_nxos_uptime_partial(self):
        line = "Kernel uptime is 3 hour(s), 30 minute(s)"
        assert _parse_nxos_uptime(line) == (3 * 3600) + (30 * 60)

    def test_parse_nxos_uptime_empty(self):
        assert _parse_nxos_uptime("") == 0

    def test_parse_bgp_uptime_iso8601(self):
        assert _parse_bgp_uptime("P125DT3H45M") == (125 * 86400) + (3 * 3600) + (45 * 60)

    def test_parse_bgp_uptime_hms(self):
        assert _parse_bgp_uptime("01:23:45") == (1 * 3600) + (23 * 60) + 45

    def test_parse_bgp_uptime_never(self):
        assert _parse_bgp_uptime("never") == 0

    def test_parse_bgp_uptime_empty(self):
        assert _parse_bgp_uptime("") == 0

    def test_parse_arp_age_hms(self):
        assert _parse_arp_age("00:15:32") == 932.0

    def test_parse_arp_age_dash(self):
        assert _parse_arp_age("-") == -1.0

    def test_parse_arp_age_empty(self):
        assert _parse_arp_age("") == -1.0


# --- MockNxosDriver ---


class TestMockNxosDriver:
    """Test that MockNxosDriver satisfies NetworkDriver protocol."""

    def test_properties(self):
        from tests.mock_data.nxos.mock_driver import MockNxosDriver

        driver = MockNxosDriver()
        assert driver.platform == "nxos"
        assert driver.vendor == "cisco"

    def test_connect_and_close(self):
        from tests.mock_data.nxos.mock_driver import MockNxosDriver

        driver = MockNxosDriver()
        assert not driver.is_connected()
        driver.connect(host="test-device")
        assert driver.is_connected()
        driver.close()
        assert not driver.is_connected()

    def test_get_facts(self):
        from tests.mock_data.nxos.mock_driver import MockNxosDriver

        driver = MockNxosDriver()
        driver.connect()
        facts = driver.get_facts()
        assert facts["hostname"] == "n9k-spine-01"
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "nxos"
        expected_keys = {"hostname", "model", "version", "serial", "uptime_seconds", "vendor", "platform"}
        assert set(facts.keys()) == expected_keys

    def test_get_interfaces(self):
        from tests.mock_data.nxos.mock_driver import MockNxosDriver

        driver = MockNxosDriver()
        driver.connect()
        interfaces = driver.get_interfaces()
        assert "Ethernet1/1" in interfaces
        expected_keys = {"is_up", "is_enabled", "description", "speed", "mtu", "mac_address"}
        for data in interfaces.values():
            assert set(data.keys()) == expected_keys

    def test_get_bgp_summary(self):
        from tests.mock_data.nxos.mock_driver import MockNxosDriver

        driver = MockNxosDriver()
        driver.connect()
        bgp = driver.get_bgp_summary()
        assert set(bgp.keys()) == {"router_id", "as_number", "peers"}
        assert bgp["as_number"] == 65001

    def test_get_arp_table(self):
        from tests.mock_data.nxos.mock_driver import MockNxosDriver

        driver = MockNxosDriver()
        driver.connect()
        arp = driver.get_arp_table()
        assert isinstance(arp, list)
        assert len(arp) > 0
        for entry in arp:
            assert set(entry.keys()) == {"ip", "mac", "interface", "age"}

    def test_get_lldp_neighbors(self):
        from tests.mock_data.nxos.mock_driver import MockNxosDriver

        driver = MockNxosDriver()
        driver.connect()
        lldp = driver.get_lldp_neighbors()
        assert isinstance(lldp, dict)
        for _intf, neighbors in lldp.items():
            for n in neighbors:
                assert set(n.keys()) == {"hostname", "port", "system_description"}

    def test_get_config(self):
        from tests.mock_data.nxos.mock_driver import MockNxosDriver

        driver = MockNxosDriver()
        driver.connect()
        config = driver.get_config()
        assert "running" in config
        assert "startup" in config
        assert isinstance(config["running"], str)
        assert isinstance(config["startup"], str)

    def test_run_show_not_connected(self):
        from tests.mock_data.nxos.mock_driver import MockNxosDriver

        driver = MockNxosDriver()
        with pytest.raises(ConnectionError):
            driver.run_show(["show version"])


# --- Common tools with NX-OS mock driver ---


class TestCommonToolsWithNxos:
    """Verify common tools dispatch correctly to NX-OS driver."""

    @pytest.fixture
    def nxos_mock_driver(self):
        from tests.mock_data.nxos.mock_driver import MockNxosDriver

        driver = MockNxosDriver()
        driver.connect(host="n9k-spine-01")
        return driver

    @pytest.fixture
    def mock_nxos_conn_mgr(self, nxos_mock_driver):
        @contextmanager
        def _mock_acquire(host):
            yield nxos_mock_driver

        with (
            patch.object(conn_mgr, "get_driver", return_value=nxos_mock_driver),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        ):
            yield

    def test_net_get_device_info(self, mock_nxos_conn_mgr):
        from network_mcp.tools.common.device import net_get_device_info

        result = net_get_device_info("n9k-spine-01")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"
        assert result["data"]["hostname"] == "n9k-spine-01"

    def test_net_get_interfaces(self, mock_nxos_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interfaces

        result = net_get_interfaces("n9k-spine-01")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"
        assert "Ethernet1/1" in result["data"]
        assert "Ethernet1/2" in result["data"]

    def test_net_get_bgp_summary(self, mock_nxos_conn_mgr):
        from network_mcp.tools.common.routing import net_get_bgp_summary

        result = net_get_bgp_summary("n9k-spine-01")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"
        assert result["data"]["as_number"] == 65001

    def test_net_get_lldp_neighbors(self, mock_nxos_conn_mgr):
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        result = net_get_lldp_neighbors("n9k-spine-01")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"
        assert "Ethernet1/1" in result["data"]

    def test_net_get_interface_detail(self, mock_nxos_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_detail

        result = net_get_interface_detail("n9k-spine-01", "Ethernet1/1")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"
        assert "Ethernet1/1" in result["data"]

    def test_net_get_interface_detail_not_found(self, mock_nxos_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_detail

        result = net_get_interface_detail("n9k-spine-01", "Ethernet99/99")
        assert result["status"] == "error"
        assert "not found" in result["error"]


# --- DRIVER_REGISTRY ---


class TestDriverRegistryNxos:
    """Verify NxosDriver is registered in DRIVER_REGISTRY."""

    def test_nxos_in_registry(self):
        from network_mcp.drivers import DRIVER_REGISTRY

        assert "nxos" in DRIVER_REGISTRY
        assert DRIVER_REGISTRY["nxos"] is NxosDriver

    def test_eos_still_in_registry(self):
        from network_mcp.drivers import DRIVER_REGISTRY

        assert "eos" in DRIVER_REGISTRY

    def test_iosxe_still_in_registry(self):
        from network_mcp.drivers import DRIVER_REGISTRY

        assert "iosxe" in DRIVER_REGISTRY

    def test_registry_driver_instantiation(self):
        from network_mcp.drivers import DRIVER_REGISTRY

        driver = DRIVER_REGISTRY["nxos"]()
        assert driver.platform == "nxos"
        assert driver.vendor == "cisco"


class TestNxosUtilityFunctions:
    """Test NX-OS driver utility functions in isolation."""

    def test_unwrap_table_empty_data(self):
        from network_mcp.drivers.cisco_nxos import _unwrap_table

        assert _unwrap_table({}, "TABLE_x", "ROW_x") == []

    def test_unwrap_table_dict_row(self):
        from network_mcp.drivers.cisco_nxos import _unwrap_table

        data = {"TABLE_x": {"ROW_x": {"id": 1}}}
        result = _unwrap_table(data, "TABLE_x", "ROW_x")
        assert result == [{"id": 1}]

    def test_unwrap_table_list_rows(self):
        from network_mcp.drivers.cisco_nxos import _unwrap_table

        data = {"TABLE_x": {"ROW_x": [{"id": 1}, {"id": 2}]}}
        result = _unwrap_table(data, "TABLE_x", "ROW_x")
        assert len(result) == 2

    def test_unwrap_table_unexpected_type(self):
        from network_mcp.drivers.cisco_nxos import _unwrap_table

        data = {"TABLE_x": {"ROW_x": "unexpected"}}
        result = _unwrap_table(data, "TABLE_x", "ROW_x")
        assert result == []

    def test_normalize_mac_dot_notation(self):
        from network_mcp.drivers.cisco_nxos import _normalize_mac

        assert _normalize_mac("0050.56a0.0001") == "00:50:56:a0:00:01"

    def test_normalize_mac_colon_notation(self):
        from network_mcp.drivers.cisco_nxos import _normalize_mac

        assert _normalize_mac("00:50:56:a0:00:01") == "00:50:56:a0:00:01"

    def test_normalize_mac_hyphen_notation(self):
        from network_mcp.drivers.cisco_nxos import _normalize_mac

        assert _normalize_mac("0050-56a0-0001") == "00:50:56:a0:00:01"

    def test_normalize_mac_empty(self):
        from network_mcp.drivers.cisco_nxos import _normalize_mac

        assert _normalize_mac("") == ""

    def test_normalize_mac_invalid_length(self):
        from network_mcp.drivers.cisco_nxos import _normalize_mac

        assert _normalize_mac("001122") == "001122"

    def test_build_nxapi_payload(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        payload = NxosDriver._build_nxapi_payload("show version")
        assert "ins_api" in payload
        assert payload["ins_api"]["input"] == "show version"
        assert payload["ins_api"]["type"] == "cli_show"

    def test_build_nxapi_payload_config_type(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        payload = NxosDriver._build_nxapi_payload("vlan 100", cmd_type="cli_conf")
        assert payload["ins_api"]["type"] == "cli_conf"

    def test_parse_nxapi_response_success(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        response = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "200",
                        "msg": "Success",
                        "body": {"hostname": "n9k-01"},
                    }
                }
            }
        }
        results = NxosDriver()._parse_nxapi_response(response)
        assert len(results) == 1
        assert results[0]["hostname"] == "n9k-01"

    def test_parse_nxapi_response_error(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        response = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "400",
                        "msg": "Invalid command",
                        "body": {},
                    }
                }
            }
        }
        results = NxosDriver()._parse_nxapi_response(response)
        assert len(results) == 1
        assert "error" in results[0]

    def test_parse_nxapi_response_text_body(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        response = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "200",
                        "msg": "Success",
                        "body": "hostname n9k-01",
                    }
                }
            }
        }
        results = NxosDriver()._parse_nxapi_response(response)
        assert results[0]["output"] == "hostname n9k-01"

    def test_parse_nxapi_response_empty_body(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        response = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "200",
                        "msg": "Success",
                        "body": None,
                    }
                }
            }
        }
        results = NxosDriver()._parse_nxapi_response(response)
        assert results[0] == {}

    def test_parse_nxapi_response_multi_command(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        response = {
            "ins_api": {
                "outputs": {
                    "output": [
                        {"code": "200", "msg": "Success", "body": {"a": 1}},
                        {"code": "200", "msg": "Success", "body": {"b": 2}},
                    ]
                }
            }
        }
        results = NxosDriver()._parse_nxapi_response(response)
        assert len(results) == 2

    def test_driver_not_connected_raises(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        driver = NxosDriver()
        with pytest.raises(ConnectionError):
            driver.run_show(["show version"])

    def test_driver_run_config_not_connected_raises(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        driver = NxosDriver()
        with pytest.raises(ConnectionError):
            driver.run_config(["vlan 100"])

    def test_driver_backward_compat_aliases(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        driver = NxosDriver()
        assert hasattr(driver, "run_commands")
        assert hasattr(driver, "config")

    def test_driver_is_connected_default_false(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        driver = NxosDriver()
        assert driver.is_connected() is False

    def test_driver_close_when_not_connected(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        driver = NxosDriver()
        driver.close()  # Should not raise
        assert driver.is_connected() is False


class TestNxosParserFunctions:
    """Test NX-OS parser/utility functions for uptime, BGP, and ARP."""

    def test_parse_nxos_uptime_days_hours_mins_secs(self):
        from network_mcp.drivers.cisco_nxos import _parse_nxos_uptime

        line = "Kernel uptime is 2 day(s), 3 hour(s), 45 minute(s), 12 second(s)"
        result = _parse_nxos_uptime(line)
        assert result == 2 * 86400 + 3 * 3600 + 45 * 60 + 12

    def test_parse_nxos_uptime_only_hours(self):
        from network_mcp.drivers.cisco_nxos import _parse_nxos_uptime

        line = "uptime is 5 hour(s)"
        assert _parse_nxos_uptime(line) == 5 * 3600

    def test_parse_nxos_uptime_no_match(self):
        from network_mcp.drivers.cisco_nxos import _parse_nxos_uptime

        assert _parse_nxos_uptime("no uptime info here") == 0

    def test_parse_bgp_uptime_iso8601(self):
        from network_mcp.drivers.cisco_nxos import _parse_bgp_uptime

        assert _parse_bgp_uptime("P2DT3H45M12S") == 2 * 86400 + 3 * 3600 + 45 * 60 + 12

    def test_parse_bgp_uptime_hms(self):
        from network_mcp.drivers.cisco_nxos import _parse_bgp_uptime

        assert _parse_bgp_uptime("01:23:45") == 1 * 3600 + 23 * 60 + 45

    def test_parse_bgp_uptime_never(self):
        from network_mcp.drivers.cisco_nxos import _parse_bgp_uptime

        assert _parse_bgp_uptime("never") == 0

    def test_parse_bgp_uptime_empty(self):
        from network_mcp.drivers.cisco_nxos import _parse_bgp_uptime

        assert _parse_bgp_uptime("") == 0

    def test_parse_bgp_uptime_unrecognized(self):
        from network_mcp.drivers.cisco_nxos import _parse_bgp_uptime

        assert _parse_bgp_uptime("unknown-format") == 0

    def test_parse_arp_age_hms(self):
        from network_mcp.drivers.cisco_nxos import _parse_arp_age

        assert _parse_arp_age("00:05:30") == 5.0 * 60 + 30

    def test_parse_arp_age_permanent(self):
        from network_mcp.drivers.cisco_nxos import _parse_arp_age

        assert _parse_arp_age("-") == -1.0

    def test_parse_arp_age_empty(self):
        from network_mcp.drivers.cisco_nxos import _parse_arp_age

        assert _parse_arp_age("") == -1.0

    def test_parse_arp_age_numeric_string(self):
        from network_mcp.drivers.cisco_nxos import _parse_arp_age

        assert _parse_arp_age("120.5") == 120.5

    def test_parse_arp_age_invalid(self):
        from network_mcp.drivers.cisco_nxos import _parse_arp_age

        assert _parse_arp_age("not-a-number") == -1.0

    def test_normalize_facts_ssh(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        output = """Cisco Nexus Operating System (NX-OS) Software
NXOS: version 10.3(2)
Device name: n9k-spine-01
cisco Nexus9000 C93180YC-EX Chassis
Processor Board ID FOC12345678
Kernel uptime is 125 day(s), 3 hour(s), 45 minute(s), 12 second(s)"""
        facts = NxosDriver._normalize_facts_ssh(output)
        assert facts["hostname"] == "n9k-spine-01"
        assert facts["version"] == "10.3(2)"
        assert facts["serial"] == "FOC12345678"
        assert facts["vendor"] == "cisco"
        assert facts["platform"] == "nxos"
        assert facts["uptime_seconds"] > 0

    def test_normalize_interfaces_ssh(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        output = """Ethernet1/1 is up
  Description: Uplink to spine-01
  admin state is up
  MTU 9216 bytes, BW 10000000 Kbit/sec
  Hardware: 100/1000/10000 Ethernet, address: 0050.56a0.0001
Ethernet1/2 is down
  admin state is down
  MTU 1500 bytes, BW 1000000 Kbit/sec
  Hardware: 100/1000/10000 Ethernet, address: 0050.56a0.0002"""
        result = NxosDriver._normalize_interfaces_ssh(output)
        assert len(result) == 2
        assert result["Ethernet1/1"]["is_up"] is True
        assert result["Ethernet1/1"]["is_enabled"] is True
        assert result["Ethernet1/1"]["description"] == "Uplink to spine-01"
        assert result["Ethernet1/1"]["mtu"] == 9216
        assert result["Ethernet1/1"]["speed"] == 10000
        assert result["Ethernet1/2"]["is_up"] is False
        assert result["Ethernet1/2"]["is_enabled"] is False
