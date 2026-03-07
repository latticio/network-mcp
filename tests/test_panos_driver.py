"""Tests for the Palo Alto PAN-OS driver (PanosDriver)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.drivers.palo_alto_panos import (
    PanosDriver,
    _cli_to_xml,
    _normalize_panos_route_flags,
    _parse_panos_uptime,
    _xml_text,
)

# ---------------------------------------------------------------------------
# Sample PAN-OS XML responses
# ---------------------------------------------------------------------------

SYSTEM_INFO_XML = """
<response status="success">
  <result>
    <system>
      <hostname>pa-fw-01</hostname>
      <model>PA-3220</model>
      <sw-version>10.2.4</sw-version>
      <serial>012345678901234</serial>
      <uptime>2 days, 14:22:51</uptime>
    </system>
  </result>
</response>
"""

INTERFACE_ALL_XML = """
<response status="success">
  <result>
    <ifnet>
      <entry>
        <name>ethernet1/1</name>
        <state>up</state>
        <speed>1000</speed>
        <mtu>1500</mtu>
        <mac>00:1a:2b:3c:4d:5e</mac>
      </entry>
      <entry>
        <name>ethernet1/2</name>
        <state>down</state>
        <speed>1000</speed>
        <mtu>1500</mtu>
        <mac>00:1a:2b:3c:4d:5f</mac>
      </entry>
    </ifnet>
    <hw>
      <entry>
        <name>ethernet1/3</name>
        <state>up</state>
        <speed>10000</speed>
        <mtu>9000</mtu>
        <mac>00:1a:2b:3c:4d:60</mac>
      </entry>
    </hw>
  </result>
</response>
"""

ARP_TABLE_XML = """
<response status="success">
  <result>
    <entries>
      <entry>
        <ip>192.168.1.1</ip>
        <hw>aa:bb:cc:dd:ee:ff</hw>
        <interface>ethernet1/1</interface>
        <status>c</status>
        <ttl>600</ttl>
      </entry>
      <entry>
        <ip>192.168.1.2</ip>
        <hw>aa:bb:cc:dd:ee:00</hw>
        <interface>ethernet1/1</interface>
        <status>e</status>
        <ttl>1200</ttl>
      </entry>
    </entries>
  </result>
</response>
"""

ROUTE_TABLE_XML = """
<response status="success">
  <result>
    <entry>
      <destination>0.0.0.0/0</destination>
      <nexthop>10.0.0.1</nexthop>
      <flags>A S</flags>
      <metric>10</metric>
    </entry>
    <entry>
      <destination>192.168.1.0/24</destination>
      <nexthop>0.0.0.0</nexthop>
      <flags>A C</flags>
      <metric>0</metric>
    </entry>
    <entry>
      <destination>10.10.0.0/16</destination>
      <nexthop>10.0.0.254</nexthop>
      <flags>A B</flags>
      <metric>100</metric>
    </entry>
  </result>
</response>
"""

LLDP_NEIGHBORS_XML = """
<response status="success">
  <result>
    <entry>
      <local-interface>ethernet1/1</local-interface>
      <system-name>peer-switch</system-name>
      <port-id>ge-0/0/1</port-id>
      <system-description>Juniper Networks EX2300</system-description>
    </entry>
  </result>
</response>
"""

KEYGEN_XML = """
<response status="success">
  <result>
    <key>LUFRPT14MW5xOEo1R09KVlBZNnpnemh0VHRBOWl6TGM=</key>
  </result>
</response>
"""

KEYGEN_ERROR_XML = """
<response status="error">
  <result>
    <msg>Invalid credentials</msg>
  </result>
</response>
"""

ERROR_RESPONSE_XML = """
<response status="error">
  <result>
    <msg>Command not found</msg>
  </result>
</response>
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_response(text: str, status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response-like object."""
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture()
def connected_driver() -> PanosDriver:
    """Return a PanosDriver that bypasses real network calls via mocked httpx."""
    driver = PanosDriver()
    # Inject state directly to avoid real HTTP
    driver._connected = True
    driver._host = "192.0.2.1"
    driver._api_key = "test-api-key"
    driver._last_command_time = time.monotonic()  # prevent keepalive trigger
    mock_client = MagicMock()
    driver._client = mock_client
    return driver


# ---------------------------------------------------------------------------
# Unit tests — helper functions
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    def test_parse_uptime_days_hms(self):
        assert _parse_panos_uptime("2 days, 14:22:51") == 2 * 86400 + 14 * 3600 + 22 * 60 + 51

    def test_parse_uptime_hms_only(self):
        assert _parse_panos_uptime("5:30:00") == 5 * 3600 + 30 * 60

    def test_parse_uptime_zero_days(self):
        assert _parse_panos_uptime("0 days, 0:00:00") == 0

    def test_parse_uptime_empty(self):
        assert _parse_panos_uptime("") == 0

    def test_parse_uptime_invalid(self):
        assert _parse_panos_uptime("unknown") == 0

    def test_normalize_route_flags_static(self):
        assert _normalize_panos_route_flags("A S") == "static"

    def test_normalize_route_flags_connected(self):
        assert _normalize_panos_route_flags("A C") == "connected"

    def test_normalize_route_flags_bgp(self):
        assert _normalize_panos_route_flags("A B") == "bgp"

    def test_normalize_route_flags_ospf(self):
        assert _normalize_panos_route_flags("A O") == "ospf"

    def test_normalize_route_flags_unknown(self):
        assert _normalize_panos_route_flags("X") == "unknown"

    def test_normalize_route_flags_empty(self):
        assert _normalize_panos_route_flags("") == "unknown"

    def test_cli_to_xml_known_commands(self):
        assert _cli_to_xml("show system info") is not None
        assert _cli_to_xml("show interface all") is not None
        assert _cli_to_xml("show arp all") is not None
        assert _cli_to_xml("show routing route") is not None

    def test_cli_to_xml_unknown_command(self):
        assert _cli_to_xml("show version") is None

    def test_cli_to_xml_case_insensitive(self):
        assert _cli_to_xml("Show System Info") is not None

    def test_xml_text_found(self):
        import xml.etree.ElementTree as ET

        el = ET.fromstring("<root><child>hello</child></root>")
        assert _xml_text(el, "child") == "hello"

    def test_xml_text_missing(self):
        import xml.etree.ElementTree as ET

        el = ET.fromstring("<root></root>")
        assert _xml_text(el, "child") == ""


# ---------------------------------------------------------------------------
# Driver class tests
# ---------------------------------------------------------------------------


class TestPanosDriverInit:
    def test_platform_and_vendor(self):
        driver = PanosDriver()
        assert driver.platform == "panos"
        assert driver.vendor == "palo_alto"

    def test_initial_state(self):
        driver = PanosDriver()
        assert not driver.is_connected()
        assert driver._client is None
        assert driver._api_key is None


class TestPanosDriverConnect:
    def test_connect_with_api_key(self):
        """Connection using a pre-supplied API key skips keygen."""
        driver = PanosDriver()
        probe_resp = _make_mock_response(SYSTEM_INFO_XML)
        mock_client = MagicMock()
        mock_client.get.return_value = probe_resp

        with patch("network_mcp.drivers.palo_alto_panos.httpx") as mock_httpx:
            mock_httpx.Client.return_value.__enter__ = lambda s: s
            mock_httpx.Client.return_value = mock_client
            driver.connect("192.0.2.1", api_key="my-api-key", verify_ssl=False)

        assert driver.is_connected()
        assert driver._api_key == "my-api-key"

    def test_connect_with_username_password(self):
        """Connection using username/password calls keygen then probes."""
        driver = PanosDriver()
        keygen_resp = _make_mock_response(KEYGEN_XML)
        probe_resp = _make_mock_response(SYSTEM_INFO_XML)
        mock_client = MagicMock()
        mock_client.get.side_effect = [keygen_resp, probe_resp]

        with patch("network_mcp.drivers.palo_alto_panos.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            driver.connect("192.0.2.1", username="admin", password="secret", verify_ssl=False)

        assert driver.is_connected()
        assert driver._api_key == "LUFRPT14MW5xOEo1R09KVlBZNnpnemh0VHRBOWl6TGM="

    def test_connect_no_credentials_raises(self):
        driver = PanosDriver()
        with patch("network_mcp.drivers.palo_alto_panos.httpx") as mock_httpx:
            mock_httpx.Client.return_value = MagicMock()
            with pytest.raises(ValueError, match="api_key or username"):
                driver.connect("192.0.2.1", username="", password="")

    def test_connect_probe_failure_raises(self):
        driver = PanosDriver()
        mock_client = MagicMock()
        mock_client.get.side_effect = ConnectionError("refused")

        with patch("network_mcp.drivers.palo_alto_panos.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            with pytest.raises(ConnectionError):
                driver.connect("192.0.2.1", api_key="key")

        assert not driver.is_connected()

    def test_connect_without_httpx_raises(self):
        driver = PanosDriver()
        with patch("network_mcp.drivers.palo_alto_panos.HAS_HTTPX", False):
            with pytest.raises(ImportError, match="httpx not installed"):
                driver.connect("192.0.2.1", api_key="key")


class TestPanosDriverClose:
    def test_close_disconnects(self, connected_driver: PanosDriver):
        connected_driver.close()
        assert not connected_driver.is_connected()
        assert connected_driver._client is None

    def test_close_idempotent(self, connected_driver: PanosDriver):
        connected_driver.close()
        connected_driver.close()  # Second close should not raise

    def test_is_connected_false_after_close(self, connected_driver: PanosDriver):
        connected_driver.close()
        assert not connected_driver.is_connected()


class TestPanosDriverGetFacts:
    def test_get_facts(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(SYSTEM_INFO_XML)

        facts = connected_driver.get_facts()

        assert facts["hostname"] == "pa-fw-01"
        assert facts["model"] == "PA-3220"
        assert facts["version"] == "10.2.4"
        assert facts["serial"] == "012345678901234"
        assert facts["vendor"] == "palo_alto"
        assert facts["platform"] == "panos"
        # 2 days, 14:22:51 = 2*86400 + 14*3600 + 22*60 + 51 = 224571 seconds
        assert facts["uptime_seconds"] == 224571

    def test_get_facts_missing_system_element_raises(self, connected_driver: PanosDriver):
        bad_xml = '<response status="success"><result></result></response>'
        connected_driver._client.get.return_value = _make_mock_response(bad_xml)

        with pytest.raises(ValueError, match="missing <system>"):
            connected_driver.get_facts()


class TestPanosDriverGetInterfaces:
    def test_get_interfaces_returns_all(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(INTERFACE_ALL_XML)

        ifaces = connected_driver.get_interfaces()

        assert "ethernet1/1" in ifaces
        assert "ethernet1/2" in ifaces
        assert "ethernet1/3" in ifaces  # From <hw> section

    def test_get_interfaces_up_state(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(INTERFACE_ALL_XML)

        ifaces = connected_driver.get_interfaces()

        assert ifaces["ethernet1/1"]["is_up"] is True
        assert ifaces["ethernet1/1"]["is_enabled"] is True
        assert ifaces["ethernet1/1"]["speed"] == 1000
        assert ifaces["ethernet1/1"]["mtu"] == 1500

    def test_get_interfaces_down_state(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(INTERFACE_ALL_XML)

        ifaces = connected_driver.get_interfaces()

        assert ifaces["ethernet1/2"]["is_up"] is False

    def test_get_interfaces_hw_section(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(INTERFACE_ALL_XML)

        ifaces = connected_driver.get_interfaces()

        assert ifaces["ethernet1/3"]["speed"] == 10000
        assert ifaces["ethernet1/3"]["mtu"] == 9000


class TestPanosDriverGetArpTable:
    def test_get_arp_table(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(ARP_TABLE_XML)

        arp = connected_driver.get_arp_table()

        assert len(arp) == 2
        assert arp[0]["ip"] == "192.168.1.1"
        assert arp[0]["interface"] == "ethernet1/1"
        assert arp[0]["age"] == 600.0

    def test_get_arp_table_empty(self, connected_driver: PanosDriver):
        empty_xml = '<response status="success"><result><entries></entries></result></response>'
        connected_driver._client.get.return_value = _make_mock_response(empty_xml)

        arp = connected_driver.get_arp_table()

        assert arp == []


class TestPanosDriverGetRouteTable:
    def test_get_route_table_all(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(ROUTE_TABLE_XML)

        routes = connected_driver.get_route_table()

        assert len(routes) == 3
        prefixes = [r["prefix"] for r in routes]
        assert "0.0.0.0/0" in prefixes
        assert "192.168.1.0/24" in prefixes

    def test_get_route_table_filter_by_protocol(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(ROUTE_TABLE_XML)

        routes = connected_driver.get_route_table(protocol="bgp")

        assert len(routes) == 1
        assert routes[0]["prefix"] == "10.10.0.0/16"
        assert routes[0]["protocol"] == "bgp"

    def test_get_route_table_limit(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(ROUTE_TABLE_XML)

        routes = connected_driver.get_route_table(limit=1)

        assert len(routes) == 1

    def test_get_route_table_static_protocol(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(ROUTE_TABLE_XML)

        routes = connected_driver.get_route_table(protocol="static")

        assert any(r["prefix"] == "0.0.0.0/0" for r in routes)

    def test_get_route_table_metric_parsed(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(ROUTE_TABLE_XML)

        routes = connected_driver.get_route_table()

        default_route = next(r for r in routes if r["prefix"] == "0.0.0.0/0")
        assert default_route["metric"] == 10


class TestPanosDriverGetLldpNeighbors:
    def test_get_lldp_neighbors(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(LLDP_NEIGHBORS_XML)

        neighbors = connected_driver.get_lldp_neighbors()

        assert "ethernet1/1" in neighbors
        assert len(neighbors["ethernet1/1"]) == 1
        assert neighbors["ethernet1/1"][0]["hostname"] == "peer-switch"
        assert neighbors["ethernet1/1"][0]["port"] == "ge-0/0/1"

    def test_get_lldp_neighbors_error_returns_empty(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(ERROR_RESPONSE_XML)

        neighbors = connected_driver.get_lldp_neighbors()

        assert neighbors == {}

    def test_get_lldp_neighbors_exception_returns_empty(self, connected_driver: PanosDriver):
        connected_driver._client.get.side_effect = OSError("connection reset")

        neighbors = connected_driver.get_lldp_neighbors()

        assert neighbors == {}


class TestPanosDriverRunShow:
    def test_run_show_known_command(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(SYSTEM_INFO_XML)

        results = connected_driver.run_show(["show system info"])

        assert len(results) == 1
        assert "output" in results[0]
        assert "error" not in results[0]

    def test_run_show_unknown_command(self, connected_driver: PanosDriver):
        results = connected_driver.run_show(["show version"])

        assert len(results) == 1
        assert "error" in results[0]
        assert "No XML mapping" in results[0]["error"]

    def test_run_show_multiple_commands(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(SYSTEM_INFO_XML)

        results = connected_driver.run_show(["show system info", "show arp all"])

        assert len(results) == 2

    def test_run_show_not_connected_raises(self):
        driver = PanosDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_show(["show system info"])


class TestPanosDriverRunConfig:
    def test_run_config_raises_not_supported(self, connected_driver: PanosDriver):
        with pytest.raises(NotSupportedError, match="not yet implemented"):
            connected_driver.run_config(["set deviceconfig"])


class TestPanosDriverCapabilities:
    def test_capabilities_returns_dict(self, connected_driver: PanosDriver):
        caps = connected_driver.capabilities()
        assert isinstance(caps, dict)

    def test_capabilities_xml_api_true(self, connected_driver: PanosDriver):
        caps = connected_driver.capabilities()
        assert caps["xml_api"] is True

    def test_capabilities_supported_getters(self, connected_driver: PanosDriver):
        caps = connected_driver.capabilities()
        assert caps["get_facts"] is True
        assert caps["get_interfaces"] is True
        assert caps["get_arp_table"] is True
        assert caps["get_route_table"] is True
        assert caps["get_lldp_neighbors"] is True

    def test_capabilities_unsupported_getters(self, connected_driver: PanosDriver):
        caps = connected_driver.capabilities()
        assert caps["get_bgp_summary"] is False
        assert caps["get_vlans"] is False
        assert caps["config_sessions"] is False

    def test_capabilities_gnmi_false(self, connected_driver: PanosDriver):
        caps = connected_driver.capabilities()
        assert caps["gnmi"] is False


class TestPanosDriverUnsupportedGetters:
    @pytest.mark.parametrize(
        "method_name,kwargs",
        [
            ("get_bgp_summary", {}),
            ("get_bgp_neighbors", {}),
            ("get_config", {}),
            ("get_vlans", {}),
            ("get_vlan_detail", {"vlan_id": 100}),
            ("get_interface_counters", {}),
            ("get_interface_errors", {}),
            ("get_ip_interfaces", {}),
            ("get_transceiver_info", {}),
            ("get_ospf_neighbors", {}),
            ("get_ospf_interfaces", {}),
            ("get_route_summary", {}),
            ("get_mac_table", {}),
            ("get_stp_status", {}),
            ("get_port_channels", {}),
            ("get_environment", {}),
            ("get_ntp_status", {}),
            ("get_cpu_memory", {}),
            ("get_acls", {}),
            ("get_snmp_config", {}),
            ("get_running_config_section", {"section": "bgp"}),
            ("get_startup_config", {}),
            ("get_vrfs", {}),
            ("get_vrf_detail", {"vrf": "default"}),
            ("get_trunk_interfaces", {}),
            ("get_lldp_neighbor_detail", {}),
            ("ping", {"destination": "8.8.8.8"}),
            ("traceroute", {"destination": "8.8.8.8"}),
            ("get_interface_flaps", {}),
            ("get_aaa_status", {}),
            ("get_user_sessions", {}),
            ("get_copp_policy", {}),
        ],
    )
    def test_unsupported_getter_raises(
        self, connected_driver: PanosDriver, method_name: str, kwargs: dict
    ):
        with pytest.raises(NotSupportedError):
            getattr(connected_driver, method_name)(**kwargs)


class TestPanosDriverXmlOp:
    def test_xml_op_not_connected_raises(self):
        driver = PanosDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver._xml_op("<show><system><info/></system></show>")

    def test_xml_op_error_response_raises(self, connected_driver: PanosDriver):
        connected_driver._client.get.return_value = _make_mock_response(ERROR_RESPONSE_XML)

        with pytest.raises(ValueError, match="PAN-OS API error"):
            connected_driver._xml_op("<show><system><info/></system></show>")


class TestPanosDriverInRegistry:
    def test_panos_in_driver_registry(self):
        from network_mcp.drivers import DRIVER_REGISTRY

        assert "panos" in DRIVER_REGISTRY
        assert DRIVER_REGISTRY["panos"] is PanosDriver


class TestPanosDriverInPlatform:
    def test_panos_platform_enum(self):
        from network_mcp.config import Platform

        assert Platform.PANOS == "panos"
        assert "panos" in {p.value for p in Platform}
