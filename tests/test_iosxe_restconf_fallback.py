"""IOS-XE RESTCONF auto-fallback tests.

Tests that the IosXeDriver correctly falls back from RESTCONF to SSH
when RESTCONF fails, and verifies proper behavior for various failure
scenarios.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers — create a driver wired to mock transports
# ---------------------------------------------------------------------------


def _make_driver(*, restconf_client=None, ssh_transport=None):
    """Build an IosXeDriver with injected mock transports.

    Bypasses connect() entirely so tests don't need live devices.
    """
    from network_mcp.drivers.cisco_iosxe import IosXeDriver

    driver = IosXeDriver()
    driver._restconf = restconf_client
    driver._ssh_transport = ssh_transport
    driver._ssh_host = "192.168.1.1"
    driver._ssh_username = "admin"
    driver._ssh_password = "password"
    driver._transport = "restconf" if restconf_client else "ssh"
    driver._connected = True
    driver._last_command_time = 1e12  # far future so keepalive never triggers
    return driver


def _mock_restconf_response(*, status_code=200, json_data=None, raise_exc=None):
    """Create a mock httpx Response for RESTCONF calls."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code

    if raise_exc:
        mock_resp.raise_for_status.side_effect = raise_exc
    elif status_code >= 400:
        import httpx

        request = MagicMock()
        request.url = "https://192.168.1.1/restconf/data/test"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=request,
            response=mock_resp,
        )
    else:
        mock_resp.raise_for_status.return_value = None

    mock_resp.json.return_value = json_data or {}
    return mock_resp


# ---------------------------------------------------------------------------
# RESTCONF → SSH fallback on get_facts
# ---------------------------------------------------------------------------


class TestIosXeRestconfFallbackGetFacts:
    """Tests for get_facts RESTCONF-to-SSH fallback."""

    def test_restconf_404_falls_back_to_ssh(self, caplog):
        """RESTCONF returns 404 → driver falls back to SSH → returns valid data."""
        import httpx

        mock_restconf = MagicMock()
        request = MagicMock()
        request.url = "https://192.168.1.1/restconf/data/native"
        mock_restconf.get.side_effect = httpx.HTTPStatusError(
            "404 Not Found",
            request=request,
            response=MagicMock(status_code=404),
        )

        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = (
            "Cisco IOS XE Software, Version 17.06.01\n"
            "cisco ISR4451-X uptime is 10 days, 3 hours, 45 minutes\n"
            "Processor board ID FJC2301A0AB\n"
            "cisco ISR4451/K9\n"
        )

        driver = _make_driver(restconf_client=mock_restconf, ssh_transport=mock_ssh)

        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            result = driver.get_facts()

        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"
        assert "RESTCONF get_facts failed" in caplog.text
        mock_ssh.send_command.assert_called()

    def test_restconf_500_falls_back_to_ssh(self, caplog):
        """RESTCONF returns 500 → driver falls back to SSH → returns valid data."""
        import httpx

        mock_restconf = MagicMock()
        request = MagicMock()
        request.url = "https://192.168.1.1/restconf/data/native"
        mock_restconf.get.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=request,
            response=MagicMock(status_code=500),
        )

        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = (
            "Cisco IOS XE Software, Version 17.06.01\n"
            "router01 uptime is 5 days, 2 hours, 30 minutes\n"
            "Processor board ID ABC1234567\n"
            "cisco CSR1000V\n"
        )

        driver = _make_driver(restconf_client=mock_restconf, ssh_transport=mock_ssh)

        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            result = driver.get_facts()

        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"
        assert "RESTCONF get_facts failed" in caplog.text

    def test_restconf_connection_timeout_falls_back_to_ssh(self, caplog):
        """RESTCONF connection timeout → driver falls back to SSH → returns valid data."""
        import httpx

        mock_restconf = MagicMock()
        mock_restconf.get.side_effect = httpx.ConnectTimeout("Connection timed out")

        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = (
            "Cisco IOS XE Software, Version 17.03.04\n"
            "switch01 uptime is 30 days, 1 hour, 15 minutes\n"
            "Processor board ID XYZ9876543\n"
            "cisco C9300-48T\n"
        )

        driver = _make_driver(restconf_client=mock_restconf, ssh_transport=mock_ssh)

        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            result = driver.get_facts()

        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"
        assert "RESTCONF get_facts failed" in caplog.text

    def test_both_restconf_and_ssh_fail(self):
        """Both RESTCONF and SSH fail → driver returns empty/degraded result.

        When RESTCONF fails, get_facts falls back to SSH. If SSH transport
        is also unavailable, _ssh_show catches the ConnectionError internally
        and returns {"error": ...}. The result is then parsed from empty text
        output, yielding a mostly-empty facts dict.
        """
        import httpx

        mock_restconf = MagicMock()
        mock_restconf.get.side_effect = httpx.ConnectError("RESTCONF down")

        # SSH transport is None — no SSH fallback available
        driver = _make_driver(restconf_client=mock_restconf, ssh_transport=None)

        result = driver.get_facts()
        # Both transports failed, so the result is a degraded facts dict
        # parsed from empty output — all fields empty/zero
        assert result["hostname"] == ""
        assert result["model"] == ""
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"

    def test_restconf_succeeds_no_ssh_fallback(self):
        """RESTCONF succeeds → no SSH fallback triggered (verify SSH not called)."""
        mock_restconf = MagicMock()
        mock_restconf.get.return_value = _mock_restconf_response(
            json_data={
                "Cisco-IOS-XE-native:native": {
                    "hostname": "router01",
                    "version": "17.06.01",
                    "license": {"udi": {"pid": "ISR4451/K9", "sn": "FJC2301A0AB"}},
                }
            }
        )

        mock_ssh = MagicMock()
        driver = _make_driver(restconf_client=mock_restconf, ssh_transport=mock_ssh)

        result = driver.get_facts()

        assert result["hostname"] == "router01"
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"
        # SSH should NOT be called when RESTCONF succeeds
        mock_ssh.send_command.assert_not_called()


# ---------------------------------------------------------------------------
# RESTCONF → SSH fallback on get_interfaces
# ---------------------------------------------------------------------------


class TestIosXeRestconfFallbackGetInterfaces:
    """Tests for get_interfaces RESTCONF-to-SSH fallback."""

    def test_restconf_failure_falls_back_to_ssh_for_interfaces(self, caplog):
        """RESTCONF fails for get_interfaces → SSH fallback used."""
        import httpx

        mock_restconf = MagicMock()
        mock_restconf.get.side_effect = httpx.ReadTimeout("Read timed out")

        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = (
            "GigabitEthernet1 is up, line protocol is up\n"
            "  Description: WAN\n"
            "  MTU 1500 bytes, BW 1000000 Kbit/sec\n"
            "  Hardware is CSR vNIC, address is 0050.5680.0001\n"
        )

        driver = _make_driver(restconf_client=mock_restconf, ssh_transport=mock_ssh)

        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            result = driver.get_interfaces()

        assert "GigabitEthernet1" in result
        assert result["GigabitEthernet1"]["is_up"] is True
        assert "RESTCONF get_interfaces failed" in caplog.text

    def test_restconf_success_returns_normalized_interfaces(self):
        """RESTCONF succeeds → returns normalized interface data."""
        mock_restconf = MagicMock()
        mock_restconf.get.return_value = _mock_restconf_response(
            json_data={
                "ietf-interfaces:interfaces": {
                    "interface": [
                        {
                            "name": "GigabitEthernet1",
                            "oper-status": "up",
                            "enabled": True,
                            "description": "WAN Link",
                            "speed": 1000000000,
                            "mtu": 1500,
                            "phys-address": "00:50:56:80:00:01",
                        }
                    ]
                }
            }
        )

        mock_ssh = MagicMock()
        driver = _make_driver(restconf_client=mock_restconf, ssh_transport=mock_ssh)
        result = driver.get_interfaces()

        assert "GigabitEthernet1" in result
        assert result["GigabitEthernet1"]["is_up"] is True
        assert result["GigabitEthernet1"]["description"] == "WAN Link"
        assert result["GigabitEthernet1"]["speed"] == 1000
        mock_ssh.send_command.assert_not_called()


# ---------------------------------------------------------------------------
# RESTCONF → SSH fallback on get_bgp_summary
# ---------------------------------------------------------------------------


class TestIosXeRestconfFallbackBgpSummary:
    """Tests for get_bgp_summary RESTCONF-to-SSH fallback."""

    def test_restconf_failure_falls_back_to_ssh_for_bgp(self, caplog):
        """RESTCONF fails for get_bgp_summary → SSH fallback used."""
        mock_restconf = MagicMock()
        mock_restconf.get.side_effect = Exception("RESTCONF error")

        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = (
            "BGP router identifier 10.0.0.1, local AS number 65001\n"
            "Neighbor        V  AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd\n"
            "10.0.0.2        4 65002    1234    1235       42    0    0 1d02h         150\n"
        )

        driver = _make_driver(restconf_client=mock_restconf, ssh_transport=mock_ssh)

        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            result = driver.get_bgp_summary()

        assert result["router_id"] == "10.0.0.1"
        assert "RESTCONF get_bgp_summary failed" in caplog.text


# ---------------------------------------------------------------------------
# Connection-level fallback during connect()
# ---------------------------------------------------------------------------


class TestIosXeConnectFallback:
    """Tests for RESTCONF-to-SSH fallback during the connect() phase."""

    @patch("network_mcp.drivers.cisco_iosxe.HAS_HTTPX", True)
    @patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", True)
    def test_restconf_probe_failure_falls_back_to_ssh(self, caplog):
        """RESTCONF probe fails during connect() → driver auto-falls back to SSH."""
        import httpx

        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        driver = IosXeDriver()

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")

        with (
            patch("network_mcp.drivers.cisco_iosxe.httpx.Client", return_value=mock_client),
            patch("network_mcp.drivers.cisco_iosxe.IosXeScrapliTransport") as mock_scrapli,
            caplog.at_level(logging.WARNING, logger="network-mcp"),
        ):
            driver.connect("192.168.1.1", transport="restconf", username="admin", password="pass")

        assert driver._transport == "ssh"
        assert driver._connected is True
        assert "RESTCONF unavailable" in caplog.text
        mock_scrapli.assert_called_once()

    @patch("network_mcp.drivers.cisco_iosxe.HAS_HTTPX", False)
    @patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", True)
    def test_no_httpx_falls_back_to_ssh(self, caplog):
        """httpx not installed → driver auto-falls back to SSH."""
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        driver = IosXeDriver()

        with (
            patch("network_mcp.drivers.cisco_iosxe.IosXeScrapliTransport"),
            caplog.at_level(logging.WARNING, logger="network-mcp"),
        ):
            driver.connect("192.168.1.1", transport="restconf", username="admin", password="pass")

        assert driver._transport == "ssh"
        assert driver._connected is True
        assert "httpx not installed" in caplog.text


# ---------------------------------------------------------------------------
# run_show with RESTCONF → SSH mixed command fallback
# ---------------------------------------------------------------------------


class TestIosXeRunShowFallback:
    """Tests for run_show RESTCONF-to-SSH per-command fallback."""

    def test_unmapped_command_falls_back_to_ssh(self):
        """Command without RESTCONF mapping → SSH used for that command only."""
        mock_restconf = MagicMock()
        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = "some output"

        driver = _make_driver(restconf_client=mock_restconf, ssh_transport=mock_ssh)
        result = driver.run_show(["show clock"])

        # show clock has no RESTCONF path → SSH fallback
        assert len(result) == 1
        mock_ssh.send_command.assert_called_once_with("show clock")

    def test_restconf_500_falls_back_to_ssh(self):
        """RESTCONF 500 error falls back to SSH (server errors are transient)."""
        import httpx

        mock_restconf = MagicMock()
        request = MagicMock()
        request.url = "https://192.168.1.1/restconf/data/ietf-interfaces:interfaces"
        mock_restconf.get.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=request,
            response=MagicMock(status_code=500),
        )

        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = '{"output": "ssh-data"}'
        driver = _make_driver(restconf_client=mock_restconf, ssh_transport=mock_ssh)
        result = driver.run_show(["show interfaces"])

        assert len(result) == 1
        assert "error" not in result[0]
        mock_ssh.send_command.assert_called()

    def test_restconf_client_error_returns_error(self):
        """RESTCONF 4xx (non-404) returns error dict (not fallback)."""
        import httpx

        mock_restconf = MagicMock()
        request = MagicMock()
        request.url = "https://192.168.1.1/restconf/data/ietf-interfaces:interfaces"
        mock_restconf.get.side_effect = httpx.HTTPStatusError(
            "400 Bad Request",
            request=request,
            response=MagicMock(status_code=400),
        )

        driver = _make_driver(restconf_client=mock_restconf, ssh_transport=MagicMock())
        result = driver.run_show(["show interfaces"])

        assert len(result) == 1
        assert "error" in result[0]
