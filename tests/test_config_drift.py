"""Tests for eos_detect_config_drift tool."""

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from network_mcp.tools.config_mgmt import eos_detect_config_drift


@pytest.fixture
def mock_diff_response():
    """Helper to build a mock show running-config diffs response."""

    def _make(output_text: str):
        return [{"output": output_text}]

    return _make


class TestDetectConfigDrift:
    """Tests for eos_detect_config_drift."""

    def test_no_drift(self, mock_conn_mgr, mock_node, mock_diff_response):
        mock_node.run_commands.return_value = mock_diff_response("")
        result = eos_detect_config_drift(host="spine-01")
        assert result["status"] == "success"
        assert result["data"]["has_drift"] is False
        assert result["data"]["sections"] == []
        assert result["data"]["summary"]["total"] == 0

    def test_critical_drift_bgp(self, mock_conn_mgr, mock_node, mock_diff_response):
        diff_text = "router bgp 65000\n   neighbor 10.0.0.1 remote-as 65001\n"
        mock_node.run_commands.return_value = mock_diff_response(diff_text)
        result = eos_detect_config_drift(host="spine-01")
        assert result["status"] == "success"
        assert result["data"]["has_drift"] is True
        assert result["data"]["summary"]["critical"] >= 1

    def test_critical_drift_mlag(self, mock_conn_mgr, mock_node, mock_diff_response):
        diff_text = "mlag configuration\n   domain-id MLAG01\n"
        mock_node.run_commands.return_value = mock_diff_response(diff_text)
        result = eos_detect_config_drift(host="spine-01")
        assert result["data"]["has_drift"] is True
        sections = result["data"]["sections"]
        assert any(s["severity"] == "critical" for s in sections)

    def test_warning_drift_interface(self, mock_conn_mgr, mock_node, mock_diff_response):
        diff_text = "interface Ethernet1\n   description uplink\n   no shutdown\n"
        mock_node.run_commands.return_value = mock_diff_response(diff_text)
        result = eos_detect_config_drift(host="spine-01")
        assert result["data"]["has_drift"] is True
        sections = result["data"]["sections"]
        assert any(s["severity"] == "warning" for s in sections)

    def test_info_drift(self, mock_conn_mgr, mock_node, mock_diff_response):
        diff_text = "logging console informational\n"
        mock_node.run_commands.return_value = mock_diff_response(diff_text)
        result = eos_detect_config_drift(host="spine-01")
        assert result["data"]["has_drift"] is True
        sections = result["data"]["sections"]
        assert any(s["severity"] == "info" for s in sections)

    def test_mixed_severity(self, mock_conn_mgr, mock_node, mock_diff_response):
        diff_text = (
            "router bgp 65000\n"
            "   neighbor 10.0.0.1 remote-as 65001\n"
            "interface Ethernet1\n"
            "   description uplink\n"
            "logging console informational\n"
        )
        mock_node.run_commands.return_value = mock_diff_response(diff_text)
        result = eos_detect_config_drift(host="spine-01")
        assert result["data"]["has_drift"] is True
        summary = result["data"]["summary"]
        assert summary["total"] == 3
        assert summary["critical"] >= 1
        assert summary["warning"] >= 1
        assert summary["info"] >= 1

    def test_diff_with_plus_minus_prefixes(self, mock_conn_mgr, mock_node, mock_diff_response):
        diff_text = "+router bgp 65000\n+   neighbor 10.0.0.1 remote-as 65001\n-vlan 100\n-   name OLD_SERVERS\n"
        mock_node.run_commands.return_value = mock_diff_response(diff_text)
        result = eos_detect_config_drift(host="spine-01")
        assert result["data"]["has_drift"] is True
        assert result["data"]["summary"]["total"] >= 2

    def test_connection_error(self, mock_conn_mgr, mock_node):
        import pyeapi.eapilib

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("eapi", "unreachable")
        result = eos_detect_config_drift(host="spine-01")
        assert result["status"] == "error"

    def test_empty_host(self):
        with pytest.raises(ToolError):
            eos_detect_config_drift(host="")
