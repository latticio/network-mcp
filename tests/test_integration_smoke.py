"""Integration smoke tests: full tool workflow from mock device to structured response.

Validates the complete pipeline:
  connection pool → driver → helpers (rate limit, cache, retry) → tool → sanitized output

Tests 3 representative tools from different modules:
  1. eos_get_device_info (device.py) — basic show command
  2. eos_get_vlans (vlans.py) — dict-of-dicts response
  3. eos_get_bgp_summary (routing.py) — nested VRF/peer response
"""

import json

from network_mcp.tools.device import eos_get_device_info
from network_mcp.tools.routing import eos_get_bgp_summary
from network_mcp.tools.vlans import eos_get_vlans
from tests.conftest import (
    MOCK_BGP_SUMMARY,
    MOCK_SHOW_VERSION,
    MOCK_VLANS,
)


def _assert_json_serializable(obj, path="root"):
    """Recursively assert that all values in a dict are JSON-serializable (no raw pyeapi types)."""
    # Attempt JSON round-trip to prove serializability
    try:
        json.dumps(obj)
    except (TypeError, ValueError) as e:
        raise AssertionError(f"Value at '{path}' is not JSON-serializable: {e}") from e


def _assert_success_response(result, expected_host):
    """Assert a tool result has the standard success contract."""
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result["status"] == "success", f"Expected status='success', got {result}"
    assert result["device"] == expected_host, f"Expected device='{expected_host}', got '{result.get('device')}'"
    assert "data" in result, f"Success response missing 'data' key: {result}"
    _assert_json_serializable(result)


class TestIntegrationSmoke:
    """End-to-end smoke tests through the full tool pipeline."""

    HOST = "spine-01"

    def test_get_device_info_full_pipeline(self, mock_node, mock_conn_mgr):
        """device.py: mock device → run_show_command → eos_get_device_info → structured response."""
        mock_node.run_commands.return_value = [MOCK_SHOW_VERSION]

        result = eos_get_device_info(self.HOST)

        _assert_success_response(result, self.HOST)
        data = result["data"]
        assert data["hostname"] == "test-switch"
        assert data["model"] == "vEOS-lab"
        assert data["eos_version"] == "4.32.1F"
        assert data["serial_number"] == "TEST123"
        assert data["system_mac"] == "00:1c:73:00:00:01"
        assert data["uptime_seconds"] == 86400
        assert isinstance(data["total_memory"], int)
        assert isinstance(data["free_memory"], int)

    def test_get_vlans_full_pipeline(self, mock_node, mock_conn_mgr):
        """vlans.py: mock device → run_show_command → eos_get_vlans → structured response."""
        mock_node.run_commands.return_value = [MOCK_VLANS]

        result = eos_get_vlans(self.HOST)

        _assert_success_response(result, self.HOST)
        data = result["data"]
        assert "1" in data, "Default VLAN 1 should be present"
        assert "100" in data, "VLAN 100 should be present"
        assert data["100"]["name"] == "SERVERS"
        assert data["100"]["status"] == "active"

    def test_get_bgp_summary_full_pipeline(self, mock_node, mock_conn_mgr):
        """routing.py: mock device → run_show_command → eos_get_bgp_summary → structured response."""
        mock_node.run_commands.return_value = [MOCK_BGP_SUMMARY]

        result = eos_get_bgp_summary(self.HOST)

        _assert_success_response(result, self.HOST)
        data = result["data"]
        assert data["router_id"] == "10.0.0.1"
        assert data["local_asn"] == "65000"
        peers = data["peers"]
        assert "10.0.0.2" in peers
        assert peers["10.0.0.2"]["peerState"] == "Established"
        assert peers["10.0.0.2"]["prefixReceived"] == 150
        assert "10.0.0.3" in peers
        assert peers["10.0.0.3"]["peerState"] == "Active"

    def test_response_contains_no_raw_types(self, mock_node, mock_conn_mgr):
        """Verify that no raw pyeapi types leak into tool responses (all JSON-serializable)."""
        mock_node.run_commands.return_value = [MOCK_SHOW_VERSION]
        result = eos_get_device_info(self.HOST)

        # Full JSON round-trip: serialize and deserialize must be lossless
        serialized = json.dumps(result)
        deserialized = json.loads(serialized)
        assert deserialized == result, "JSON round-trip must be lossless"

    def test_error_propagation_through_pipeline(self, mock_node, mock_conn_mgr):
        """Verify that device errors propagate correctly through the full pipeline."""
        import pyeapi.eapilib

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "connection refused")

        result = eos_get_device_info(self.HOST)

        assert result["status"] == "error"
        assert result["device"] == self.HOST
        assert "error" in result
        assert isinstance(result["error"], str)
        assert len(result["error"]) > 0
