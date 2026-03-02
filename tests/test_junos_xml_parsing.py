"""JunOS XML parse logging tests.

Tests that the JunosDriver correctly handles various XML parsing edge
cases: missing elements, unexpected namespaces, empty responses, and
malformed XML.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_junos_driver(*, netconf_conn=None, ssh_transport=None):
    """Build a JunosDriver with injected mock transports."""
    from network_mcp.drivers.juniper_junos import JunosDriver

    driver = JunosDriver()
    driver._conn = netconf_conn
    driver._ssh_transport = ssh_transport
    driver._host = "192.168.1.1"
    driver._username = "admin"
    driver._password = "password"
    driver._transport = "netconf" if netconf_conn else "ssh"
    driver._connected = True
    driver._last_command_time = 1e12
    return driver


# ---------------------------------------------------------------------------
# XML helper function tests
# ---------------------------------------------------------------------------


class TestXmlFindText:
    """Tests for _xml_find_text helper."""

    def test_direct_match(self):
        """Direct child element match → returns text."""
        from network_mcp.drivers.juniper_junos import _xml_find_text

        root = ET.fromstring("<root><hostname>spine-01</hostname></root>")  # noqa: S314
        assert _xml_find_text(root, "hostname") == "spine-01"

    def test_namespace_match(self):
        """Element with namespace → still matches by local name."""
        from network_mcp.drivers.juniper_junos import _xml_find_text

        root = ET.fromstring(  # noqa: S314
            '<root xmlns:junos="http://xml.juniper.net/junos/99.1R1/junos">'
            '<junos:hostname xmlns:junos="http://xml.juniper.net/junos/99.1R1/junos">'
            "spine-01"
            "</junos:hostname>"
            "</root>"
        )
        result = _xml_find_text(root, "hostname")
        assert result == "spine-01"

    def test_missing_element_returns_default(self):
        """Missing element → returns default value."""
        from network_mcp.drivers.juniper_junos import _xml_find_text

        root = ET.fromstring("<root><other>value</other></root>")  # noqa: S314
        assert _xml_find_text(root, "hostname") == ""
        assert _xml_find_text(root, "hostname", "N/A") == "N/A"

    def test_element_with_empty_text(self):
        """Element with no text content → returns default."""
        from network_mcp.drivers.juniper_junos import _xml_find_text

        root = ET.fromstring("<root><hostname></hostname></root>")  # noqa: S314
        result = _xml_find_text(root, "hostname")
        assert result == ""

    def test_element_with_whitespace_text(self):
        """Element with whitespace text → stripped result."""
        from network_mcp.drivers.juniper_junos import _xml_find_text

        root = ET.fromstring("<root><hostname>  spine-01  </hostname></root>")  # noqa: S314
        assert _xml_find_text(root, "hostname") == "spine-01"


class TestXmlFindAll:
    """Tests for _xml_findall helper."""

    def test_finds_multiple_elements(self):
        """Multiple matching elements → all returned."""
        from network_mcp.drivers.juniper_junos import _xml_findall

        root = ET.fromstring(  # noqa: S314
            "<root>"
            "<physical-interface><name>ge-0/0/0</name></physical-interface>"
            "<physical-interface><name>ge-0/0/1</name></physical-interface>"
            "</root>"
        )
        results = _xml_findall(root, "physical-interface")
        assert len(results) == 2

    def test_no_matches_returns_empty(self):
        """No matching elements → empty list returned."""
        from network_mcp.drivers.juniper_junos import _xml_findall

        root = ET.fromstring("<root><other>value</other></root>")  # noqa: S314
        results = _xml_findall(root, "physical-interface")
        assert results == []

    def test_namespace_agnostic_match(self):
        """Elements with namespace → matched by local name."""
        from network_mcp.drivers.juniper_junos import _xml_findall

        root = ET.fromstring(  # noqa: S314
            '<root xmlns="http://xml.juniper.net/junos">'
            "<physical-interface><name>ge-0/0/0</name></physical-interface>"
            "</root>"
        )
        results = _xml_findall(root, "physical-interface")
        assert len(results) == 1


class TestXmlFind:
    """Tests for _xml_find helper."""

    def test_finds_first_element(self):
        """First matching element returned."""
        from network_mcp.drivers.juniper_junos import _xml_find

        root = ET.fromstring(  # noqa: S314
            "<root>"
            "<interface-information>"
            "<physical-interface><name>ge-0/0/0</name></physical-interface>"
            "</interface-information>"
            "</root>"
        )
        result = _xml_find(root, "interface-information")
        assert result is not None

    def test_no_match_returns_none(self):
        """No matching element → None returned."""
        from network_mcp.drivers.juniper_junos import _xml_find

        root = ET.fromstring("<root><other>value</other></root>")  # noqa: S314
        result = _xml_find(root, "interface-information")
        assert result is None


# ---------------------------------------------------------------------------
# _normalize_facts XML parsing tests
# ---------------------------------------------------------------------------


class TestNormalizeFactsXml:
    """Tests for JunosDriver._normalize_facts with various XML inputs."""

    def test_valid_system_information(self):
        """Valid XML → all fields parsed correctly."""
        from network_mcp.drivers.juniper_junos import JunosDriver

        xml_str = (
            "<rpc-reply>"
            "<system-information>"
            "<host-name>junos-spine-01</host-name>"
            "<hardware-model>QFX5120-48T</hardware-model>"
            "<os-version>23.4R1.10</os-version>"
            "<serial-number>JN1234567890</serial-number>"
            "<up-time>864000</up-time>"
            "</system-information>"
            "</rpc-reply>"
        )
        result = JunosDriver._normalize_facts(xml_str)
        assert result["hostname"] == "junos-spine-01"
        assert result["model"] == "QFX5120-48T"
        assert result["version"] == "23.4R1.10"
        assert result["serial"] == "JN1234567890"
        assert result["uptime_seconds"] == 864000
        assert result["vendor"] == "juniper"
        assert result["platform"] == "junos"

    def test_missing_system_information_element(self, caplog):
        """XML missing <system-information> → driver returns empty/partial result."""
        from network_mcp.drivers.juniper_junos import JunosDriver

        xml_str = "<rpc-reply><other-data>something</other-data></rpc-reply>"
        result = JunosDriver._normalize_facts(xml_str)

        # Falls back to searching root element; all fields default to empty
        assert result["hostname"] == ""
        assert result["model"] == ""
        assert result["version"] == ""
        assert result["vendor"] == "juniper"

    def test_xml_with_unexpected_namespace_prefix(self):
        """XML with Juniper namespace → fields still extracted."""
        from network_mcp.drivers.juniper_junos import JunosDriver

        xml_str = (
            '<rpc-reply xmlns:junos="http://xml.juniper.net/junos/23.4R1/junos">'
            '<system-information xmlns="http://xml.juniper.net/junos/23.4R1/junos">'
            "<host-name>junos-ns-test</host-name>"
            "<hardware-model>MX240</hardware-model>"
            "<os-version>23.4R1</os-version>"
            "</system-information>"
            "</rpc-reply>"
        )
        result = JunosDriver._normalize_facts(xml_str)
        assert result["hostname"] == "junos-ns-test"
        assert result["model"] == "MX240"

    def test_empty_xml_response_returns_defaults(self):
        """Empty XML string → returns default dict (safe parse catches error)."""
        from network_mcp.drivers.juniper_junos import JunosDriver

        result = JunosDriver._normalize_facts("")
        assert isinstance(result, dict)

    def test_malformed_xml_returns_defaults(self):
        """Malformed XML → returns default dict (safe parse catches error)."""
        from network_mcp.drivers.juniper_junos import JunosDriver

        result = JunosDriver._normalize_facts("<broken><unclosed>")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# _normalize_interfaces XML parsing tests
# ---------------------------------------------------------------------------


class TestNormalizeInterfacesXml:
    """Tests for JunosDriver._normalize_interfaces with various XML inputs."""

    def test_valid_interface_information(self):
        """Valid XML → interfaces parsed correctly."""
        from network_mcp.drivers.juniper_junos import JunosDriver

        xml_str = (
            "<rpc-reply>"
            "<interface-information>"
            "<physical-interface>"
            "<name>ge-0/0/0</name>"
            "<oper-status>up</oper-status>"
            "<admin-status>up</admin-status>"
            "<description>Uplink to spine</description>"
            "<speed>1000mbps</speed>"
            "<mtu>1514</mtu>"
            "<current-physical-address>00:05:86:71:62:01</current-physical-address>"
            "</physical-interface>"
            "</interface-information>"
            "</rpc-reply>"
        )
        result = JunosDriver._normalize_interfaces(xml_str)
        assert "ge-0/0/0" in result
        iface = result["ge-0/0/0"]
        assert iface["is_up"] is True
        assert iface["is_enabled"] is True
        assert iface["description"] == "Uplink to spine"
        assert iface["speed"] == 1000
        assert iface["mtu"] == 1514

    def test_missing_interface_information(self):
        """XML missing <interface-information> → returns empty dict."""
        from network_mcp.drivers.juniper_junos import JunosDriver

        xml_str = "<rpc-reply><other-data>something</other-data></rpc-reply>"
        result = JunosDriver._normalize_interfaces(xml_str)
        assert result == {}

    def test_interface_with_unlimited_mtu(self):
        """Interface with 'Unlimited' MTU → defaults to 1500."""
        from network_mcp.drivers.juniper_junos import JunosDriver

        xml_str = (
            "<rpc-reply>"
            "<interface-information>"
            "<physical-interface>"
            "<name>lo0</name>"
            "<oper-status>up</oper-status>"
            "<admin-status>up</admin-status>"
            "<mtu>Unlimited</mtu>"
            "</physical-interface>"
            "</interface-information>"
            "</rpc-reply>"
        )
        result = JunosDriver._normalize_interfaces(xml_str)
        assert result["lo0"]["mtu"] == 1500

    def test_interface_with_empty_name_skipped(self):
        """Interface with empty name → skipped."""
        from network_mcp.drivers.juniper_junos import JunosDriver

        xml_str = (
            "<rpc-reply>"
            "<interface-information>"
            "<physical-interface>"
            "<name></name>"
            "<oper-status>up</oper-status>"
            "</physical-interface>"
            "</interface-information>"
            "</rpc-reply>"
        )
        result = JunosDriver._normalize_interfaces(xml_str)
        assert result == {}


# ---------------------------------------------------------------------------
# get_facts NETCONF-to-SSH fallback with XML errors
# ---------------------------------------------------------------------------


class TestJunosGetFactsFallback:
    """Tests for get_facts behavior when NETCONF returns problematic XML."""

    def test_netconf_rpc_failure_falls_back_to_ssh(self, caplog):
        """NETCONF RPC fails → driver falls back to SSH."""
        mock_conn = MagicMock()
        mock_conn.rpc.side_effect = Exception("NETCONF channel error")

        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = "Hostname: junos-ssh-01\nModel: QFX5120-48T\nJunos: 23.4R1.10\n"

        driver = _make_junos_driver(netconf_conn=mock_conn, ssh_transport=mock_ssh)

        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            result = driver.get_facts()

        assert result["hostname"] == "junos-ssh-01"
        assert result["vendor"] == "juniper"
        assert "NETCONF RPC failed" in caplog.text or "get_facts" in caplog.text

    def test_netconf_returns_valid_xml(self):
        """NETCONF returns valid XML → SSH not called."""
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.result = (
            "<rpc-reply>"
            "<system-information>"
            "<host-name>junos-netconf-01</host-name>"
            "<hardware-model>MX240</hardware-model>"
            "<os-version>21.4R1</os-version>"
            "<serial-number>JN9876543210</serial-number>"
            "<up-time>172800</up-time>"
            "</system-information>"
            "</rpc-reply>"
        )
        mock_conn.rpc.return_value = mock_response

        mock_ssh = MagicMock()
        driver = _make_junos_driver(netconf_conn=mock_conn, ssh_transport=mock_ssh)
        result = driver.get_facts()

        assert result["hostname"] == "junos-netconf-01"
        assert result["model"] == "MX240"
        assert result["uptime_seconds"] == 172800
        mock_ssh.send_command.assert_not_called()


# ---------------------------------------------------------------------------
# JunOS uptime parser tests
# ---------------------------------------------------------------------------


class TestParseJunosUptime:
    """Tests for _parse_junos_uptime with various formats."""

    def test_plain_seconds(self):
        """Plain seconds string → parsed as integer."""
        from network_mcp.drivers.juniper_junos import _parse_junos_uptime

        assert _parse_junos_uptime("864000") == 864000

    def test_xml_style_seconds(self):
        """XML-style '{ junos:seconds 864000 }' → seconds extracted."""
        from network_mcp.drivers.juniper_junos import _parse_junos_uptime

        assert _parse_junos_uptime("{ junos:seconds 864000 }") == 864000

    def test_human_readable_format(self):
        """Human-readable '10 days, 3:45:12' → total seconds."""
        from network_mcp.drivers.juniper_junos import _parse_junos_uptime

        result = _parse_junos_uptime("10 days, 3:45:12")
        expected = 10 * 86400 + 3 * 3600 + 45 * 60 + 12
        assert result == expected

    def test_empty_string(self):
        """Empty string → 0 seconds."""
        from network_mcp.drivers.juniper_junos import _parse_junos_uptime

        assert _parse_junos_uptime("") == 0


# ---------------------------------------------------------------------------
# JunOS speed parser tests
# ---------------------------------------------------------------------------


class TestParseJunosSpeed:
    """Tests for _parse_junos_speed with various formats."""

    def test_mbps_format(self):
        """'1000mbps' → 1000 Mbps."""
        from network_mcp.drivers.juniper_junos import _parse_junos_speed

        assert _parse_junos_speed("1000mbps") == 1000

    def test_gbps_format(self):
        """'10Gbps' → 10000 Mbps."""
        from network_mcp.drivers.juniper_junos import _parse_junos_speed

        assert _parse_junos_speed("10Gbps") == 10000

    def test_auto_speed(self):
        """'Auto' → 0."""
        from network_mcp.drivers.juniper_junos import _parse_junos_speed

        assert _parse_junos_speed("Auto") == 0

    def test_empty_speed(self):
        """Empty string → 0."""
        from network_mcp.drivers.juniper_junos import _parse_junos_speed

        assert _parse_junos_speed("") == 0

    def test_numeric_bps(self):
        """Large numeric value (bps) → converted to Mbps."""
        from network_mcp.drivers.juniper_junos import _parse_junos_speed

        assert _parse_junos_speed("1000000000") == 1000
