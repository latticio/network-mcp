"""NX-API strict validation tests.

Tests that the NxosDriver handles various NX-API response edge cases:
malformed JSON, partial responses, error codes, empty bodies, and
unexpected fields.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nxos_driver(*, nxapi_client=None, ssh_transport=None):
    """Build an NxosDriver with injected mock transports."""
    from network_mcp.drivers.cisco_nxos import NxosDriver

    driver = NxosDriver()
    driver._nxapi = nxapi_client
    driver._ssh_transport = ssh_transport
    driver._ssh_host = "192.168.1.1"
    driver._ssh_username = "admin"
    driver._ssh_password = "password"
    driver._transport = "nxapi" if nxapi_client else "ssh"
    driver._connected = True
    driver._last_command_time = 1e12
    return driver


# ---------------------------------------------------------------------------
# _parse_nxapi_response tests
# ---------------------------------------------------------------------------


class TestParseNxapiResponse:
    """Tests for NxosDriver._parse_nxapi_response instance method."""

    def test_successful_single_command_response(self):
        """NX-API returns successful single command → parsed body returned."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        response_json = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "200",
                        "msg": "Success",
                        "body": {"host_name": "nxos-spine-01", "nxos_ver_str": "10.3(1)"},
                    }
                }
            }
        }
        result = NxosDriver()._parse_nxapi_response(response_json)
        assert len(result) == 1
        assert result[0]["host_name"] == "nxos-spine-01"

    def test_successful_multi_command_response(self):
        """NX-API returns successful multi-command → list of bodies returned."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        response_json = {
            "ins_api": {
                "outputs": {
                    "output": [
                        {"code": "200", "msg": "Success", "body": {"host_name": "nxos-01"}},
                        {"code": "200", "msg": "Success", "body": {"chassis_id": "Nexus9000"}},
                    ]
                }
            }
        }
        result = NxosDriver()._parse_nxapi_response(response_json)
        assert len(result) == 2
        assert result[0]["host_name"] == "nxos-01"
        assert result[1]["chassis_id"] == "Nexus9000"

    def test_error_code_in_response_body(self):
        """NX-API returns error code → driver extracts error message."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        response_json = {
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
        result = NxosDriver()._parse_nxapi_response(response_json)
        assert len(result) == 1
        assert "error" in result[0]
        assert "NX-API error" in result[0]["error"]
        assert "400" in result[0]["error"]

    def test_empty_response_body(self):
        """NX-API returns success with empty body → empty dict returned."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        response_json = {
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
        result = NxosDriver()._parse_nxapi_response(response_json)
        assert len(result) == 1
        assert result[0] == {}

    def test_string_body_response(self):
        """NX-API returns string body (text output) → wrapped in dict."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        response_json = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "200",
                        "msg": "Success",
                        "body": "some text output",
                    }
                }
            }
        }
        result = NxosDriver()._parse_nxapi_response(response_json)
        assert len(result) == 1
        assert result[0] == {"output": "some text output"}

    def test_missing_ins_api_key(self):
        """Response missing ins_api key → raises ValueError."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        with pytest.raises(ValueError, match="missing key 'ins_api'"):
            NxosDriver()._parse_nxapi_response({})

    def test_missing_outputs_key(self):
        """Response missing outputs key → raises ValueError."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        with pytest.raises(ValueError, match="missing key 'outputs'"):
            NxosDriver()._parse_nxapi_response({"ins_api": {}})

    def test_response_with_extra_unexpected_fields(self):
        """NX-API returns response with extra fields → driver ignores extras."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        response_json = {
            "ins_api": {
                "version": "1.0",
                "sid": "session123",
                "extra_field": "should_be_ignored",
                "outputs": {
                    "output": {
                        "code": "200",
                        "msg": "Success",
                        "body": {"host_name": "nxos-01"},
                        "extra_output_field": "also_ignored",
                    }
                },
            }
        }
        result = NxosDriver()._parse_nxapi_response(response_json)
        assert len(result) == 1
        assert result[0]["host_name"] == "nxos-01"
        # Extra fields should not appear in the result
        assert "extra_output_field" not in result[0]


# ---------------------------------------------------------------------------
# NX-API response handling during get_facts
# ---------------------------------------------------------------------------


class TestNxosGetFactsValidation:
    """Tests for get_facts with various NX-API response scenarios."""

    def test_malformed_json_response(self):
        """NX-API returns malformed JSON → driver falls back to SSH or handles gracefully."""
        mock_nxapi = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = ValueError("Invalid JSON")
        mock_nxapi.post.return_value = mock_resp

        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = (
            "Cisco Nexus Operating System (NX-OS) Software\n"
            "cisco Nexus9000 C93180YC-EX chassis\n"
            "Software\n"
            "  NXOS: version 10.3(1)\n"
            "Hardware\n"
            "  cisco Nexus9000 C93180YC-EX Chassis\n"
            "  Intel(R) Xeon(R) CPU\n"
            "  Processor Board ID FDO21130TYR\n"
        )

        driver = _make_nxos_driver(nxapi_client=mock_nxapi, ssh_transport=mock_ssh)
        result = driver.get_facts()

        # Should fall back to SSH and still return a result
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"
        mock_ssh.send_command.assert_called()

    def test_partial_response_missing_keys(self):
        """NX-API returns partial data (missing keys) → driver uses available data."""
        mock_nxapi = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "200",
                        "msg": "Success",
                        "body": {
                            "host_name": "nxos-partial",
                            # Missing: chassis_id, nxos_ver_str, proc_board_id, kern_uptm_*
                        },
                    }
                }
            }
        }
        mock_nxapi.post.return_value = mock_resp

        driver = _make_nxos_driver(nxapi_client=mock_nxapi)
        result = driver.get_facts()

        assert result["hostname"] == "nxos-partial"
        assert result["model"] == ""
        assert result["version"] == ""
        assert result["serial"] == ""
        assert result["uptime_seconds"] == 0

    def test_error_code_response_falls_back_to_ssh(self):
        """NX-API returns error code in body → driver falls back to SSH."""
        mock_nxapi = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "ins_api": {
                "outputs": {
                    "output": {
                        "code": "501",
                        "msg": "Could not find the show command",
                        "body": {},
                    }
                }
            }
        }
        mock_nxapi.post.return_value = mock_resp

        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = (
            "Cisco Nexus Operating System (NX-OS) Software\nHardware\n  cisco Nexus9000 C9300v Chassis\n"
        )

        driver = _make_nxos_driver(nxapi_client=mock_nxapi, ssh_transport=mock_ssh)
        result = driver.get_facts()

        # The parsed response contains "error" key, so driver falls back to SSH
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"


# ---------------------------------------------------------------------------
# _unwrap_table utility tests
# ---------------------------------------------------------------------------


class TestUnwrapTable:
    """Tests for the _unwrap_table NX-API helper."""

    def test_single_row_as_dict(self):
        """Single row returned as dict → normalized to list."""
        from network_mcp.drivers.cisco_nxos import _unwrap_table

        data = {"TABLE_intf": {"ROW_intf": {"intf": "eth1/1", "state": "up"}}}
        result = _unwrap_table(data, "TABLE_intf", "ROW_intf")
        assert result == [{"intf": "eth1/1", "state": "up"}]

    def test_multiple_rows_as_list(self):
        """Multiple rows as list → returned as-is."""
        from network_mcp.drivers.cisco_nxos import _unwrap_table

        data = {
            "TABLE_intf": {
                "ROW_intf": [
                    {"intf": "eth1/1", "state": "up"},
                    {"intf": "eth1/2", "state": "down"},
                ]
            }
        }
        result = _unwrap_table(data, "TABLE_intf", "ROW_intf")
        assert len(result) == 2

    def test_missing_table_key(self):
        """Table key missing → empty list returned."""
        from network_mcp.drivers.cisco_nxos import _unwrap_table

        result = _unwrap_table({}, "TABLE_intf", "ROW_intf")
        assert result == []

    def test_empty_table(self):
        """Table key exists but is empty → empty list returned."""
        from network_mcp.drivers.cisco_nxos import _unwrap_table

        result = _unwrap_table({"TABLE_intf": {}}, "TABLE_intf", "ROW_intf")
        assert result == []

    def test_unexpected_row_type(self):
        """Row value is unexpected type (e.g. string) → empty list returned."""
        from network_mcp.drivers.cisco_nxos import _unwrap_table

        data = {"TABLE_intf": {"ROW_intf": "unexpected_string"}}
        result = _unwrap_table(data, "TABLE_intf", "ROW_intf")
        assert result == []


# ---------------------------------------------------------------------------
# NX-API run_show validation
# ---------------------------------------------------------------------------


class TestNxosRunShowValidation:
    """Tests for run_show command execution and response handling."""

    def test_nxapi_http_error_returns_error_dict(self):
        """NX-API HTTP error on show command → error dict in results."""
        import httpx

        mock_nxapi = MagicMock()
        request = MagicMock()
        request.url = "https://192.168.1.1/ins"
        mock_nxapi.post.side_effect = httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=request,
            response=MagicMock(status_code=503),
        )

        driver = _make_nxos_driver(nxapi_client=mock_nxapi)
        result = driver.run_show(["show version"])

        assert len(result) == 1
        assert "error" in result[0]

    def test_not_connected_raises(self):
        """run_show on disconnected driver → raises ConnectionError."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        driver = NxosDriver()
        driver._connected = False

        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_show(["show version"])
