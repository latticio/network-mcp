"""Tests for BFD (Bidirectional Forwarding Detection) tools.

Covers:
- eos_get_bfd_peers with mock data (default and non-default VRF)
- eos_get_bfd_peer_detail with mock data and counters
- Empty peers response
- Invalid neighbor/peer address input validation
- Device unreachable (connection error)
- CLI injection prevention in VRF and peer params
"""

import pyeapi.eapilib

from network_mcp.tools.bfd import eos_get_bfd_peer_detail, eos_get_bfd_peers

# --- Mock BFD responses ---

MOCK_BFD_PEERS = {
    "vrfs": {
        "default": {
            "ipv4Neighbors": {
                "10.0.0.2": {
                    "peerStats": {
                        "": {
                            "status": "up",
                            "remoteDisc": 12345,
                            "lastDown": 0,
                            "lastUp": 1234567890,
                            "peerStatsDetail": {
                                "operTxInterval": 300,
                                "operRxInterval": 300,
                                "detectMult": 3,
                            },
                        }
                    }
                },
                "10.0.0.3": {
                    "peerStats": {
                        "": {
                            "status": "down",
                            "remoteDisc": 0,
                            "lastDown": 1234567800,
                            "lastUp": 0,
                            "peerStatsDetail": {
                                "operTxInterval": 300,
                                "operRxInterval": 300,
                                "detectMult": 3,
                            },
                        }
                    }
                },
            }
        }
    }
}

MOCK_BFD_PEER_DETAIL = {
    "vrfs": {
        "default": {
            "ipv4Neighbors": {
                "10.0.0.2": {
                    "peerStats": {
                        "": {
                            "status": "up",
                            "remoteDisc": 12345,
                            "lastDown": 0,
                            "lastUp": 1234567890,
                            "peerStatsDetail": {
                                "operTxInterval": 300,
                                "operRxInterval": 300,
                                "detectMult": 3,
                                "hwAccelEnabled": True,
                                "controlPktIn": 50000,
                                "controlPktOut": 50000,
                                "echoPktIn": 0,
                                "echoPktOut": 0,
                            },
                            "registeredProtocols": ["bgp", "ospf"],
                        }
                    }
                }
            }
        }
    }
}

MOCK_BFD_EMPTY = {"vrfs": {"default": {"ipv4Neighbors": {}}}}


# --- Tests ---


def test_eos_get_bfd_peers(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_BFD_PEERS]

    result = eos_get_bfd_peers("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    neighbors = result["data"]["vrfs"]["default"]["ipv4Neighbors"]
    assert "10.0.0.2" in neighbors
    assert "10.0.0.3" in neighbors
    assert neighbors["10.0.0.2"]["peerStats"][""]["status"] == "up"
    assert neighbors["10.0.0.3"]["peerStats"][""]["status"] == "down"
    mock_node.run_commands.assert_called_once_with(["show bfd peers"], encoding="json")


def test_get_bfd_peers_vrf(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_BFD_EMPTY]

    result = eos_get_bfd_peers("test-switch", vrf="MGMT")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show bfd peers vrf MGMT"], encoding="json")


def test_get_bfd_peers_empty(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_BFD_EMPTY]

    result = eos_get_bfd_peers("test-switch")

    assert result["status"] == "success"
    assert result["data"]["vrfs"]["default"]["ipv4Neighbors"] == {}


def test_get_bfd_peers_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_bfd_peers("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_get_bfd_peers_command_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid command")

    result = eos_get_bfd_peers("test-switch")

    assert result["status"] == "error"
    assert "Command error" in result["error"]


def test_eos_get_bfd_peer_detail(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_BFD_PEER_DETAIL]

    result = eos_get_bfd_peer_detail("test-switch", peer="10.0.0.2")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    neighbor = result["data"]["vrfs"]["default"]["ipv4Neighbors"]["10.0.0.2"]
    detail = neighbor["peerStats"][""]["peerStatsDetail"]
    assert detail["operTxInterval"] == 300
    assert detail["hwAccelEnabled"] is True
    mock_node.run_commands.assert_called_once_with(["show bfd peers 10.0.0.2 detail"], encoding="json")


def test_get_bfd_peer_detail_vrf(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_BFD_PEER_DETAIL]

    result = eos_get_bfd_peer_detail("test-switch", peer="10.0.0.2", vrf="PROD")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show bfd peers 10.0.0.2 vrf PROD detail"], encoding="json")


def test_get_bfd_peer_detail_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_bfd_peer_detail("bad-host", peer="10.0.0.2")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_get_bfd_peer_detail_command_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid command")

    result = eos_get_bfd_peer_detail("test-switch", peer="10.0.0.2")

    assert result["status"] == "error"
    assert "Command error" in result["error"]


# --- Input Validation Tests ---


def test_get_bfd_peers_invalid_vrf_injection(mock_conn_mgr, mock_node):
    """Should reject VRF names containing CLI injection characters."""
    result = eos_get_bfd_peers("test-switch", vrf="default; show running-config")
    assert result["status"] == "error"
    assert "invalid" in result["error"].lower()
    mock_node.run_commands.assert_not_called()


def test_get_bfd_peers_vrf_with_pipe(mock_conn_mgr, mock_node):
    """Should reject VRF names containing pipe character."""
    result = eos_get_bfd_peers("test-switch", vrf="PROD|include")
    assert result["status"] == "error"
    assert "invalid" in result["error"].lower()
    mock_node.run_commands.assert_not_called()


def test_get_bfd_peers_vrf_with_backtick(mock_conn_mgr, mock_node):
    """Should reject VRF names containing backtick."""
    result = eos_get_bfd_peers("test-switch", vrf="PROD`reboot`")
    assert result["status"] == "error"
    assert "invalid" in result["error"].lower()
    mock_node.run_commands.assert_not_called()


def test_get_bfd_peer_detail_invalid_peer_empty(mock_conn_mgr, mock_node):
    """Should reject empty peer address."""
    result = eos_get_bfd_peer_detail("test-switch", peer="")
    assert result["status"] == "error"
    mock_node.run_commands.assert_not_called()


def test_get_bfd_peer_detail_invalid_peer_injection(mock_conn_mgr, mock_node):
    """Should reject peer addresses with injection characters."""
    result = eos_get_bfd_peer_detail("test-switch", peer="10.0.0.1$(reboot)")
    assert result["status"] == "error"
    assert "invalid" in result["error"].lower()
    mock_node.run_commands.assert_not_called()


def test_get_bfd_peer_detail_invalid_peer_semicolon(mock_conn_mgr, mock_node):
    """Should reject peer addresses with semicolons."""
    result = eos_get_bfd_peer_detail("test-switch", peer="10.0.0.1; show run")
    assert result["status"] == "error"
    mock_node.run_commands.assert_not_called()


def test_get_bfd_peer_detail_invalid_vrf_injection(mock_conn_mgr, mock_node):
    """Should reject VRF with injection in peer detail."""
    result = eos_get_bfd_peer_detail("test-switch", peer="10.0.0.2", vrf="PROD|grep password")
    assert result["status"] == "error"
    assert "invalid" in result["error"].lower()
    mock_node.run_commands.assert_not_called()


def test_get_bfd_peer_detail_invalid_peer_newline(mock_conn_mgr, mock_node):
    """Should reject peer addresses with newlines."""
    result = eos_get_bfd_peer_detail("test-switch", peer="10.0.0.1\nshow run")
    assert result["status"] == "error"
    mock_node.run_commands.assert_not_called()


# --- Registered Protocols and Counter Detail ---


def test_get_bfd_peer_detail_registered_protocols(mock_conn_mgr, mock_node):
    """Should return registered protocols in detailed BFD output."""
    mock_node.run_commands.return_value = [MOCK_BFD_PEER_DETAIL]

    result = eos_get_bfd_peer_detail("test-switch", peer="10.0.0.2")

    assert result["status"] == "success"
    neighbor = result["data"]["vrfs"]["default"]["ipv4Neighbors"]["10.0.0.2"]
    protocols = neighbor["peerStats"][""]["registeredProtocols"]
    assert "bgp" in protocols
    assert "ospf" in protocols


def test_get_bfd_peer_detail_counters(mock_conn_mgr, mock_node):
    """Should return packet counters in detailed BFD output."""
    mock_node.run_commands.return_value = [MOCK_BFD_PEER_DETAIL]

    result = eos_get_bfd_peer_detail("test-switch", peer="10.0.0.2")

    assert result["status"] == "success"
    detail = result["data"]["vrfs"]["default"]["ipv4Neighbors"]["10.0.0.2"]["peerStats"][""]["peerStatsDetail"]
    assert detail["controlPktIn"] == 50000
    assert detail["controlPktOut"] == 50000


# --- Device Unreachable Edge Cases ---


def test_get_bfd_peers_generic_exception(mock_conn_mgr, mock_node):
    """Should return error on unexpected exceptions."""
    mock_node.run_commands.side_effect = RuntimeError("Something broke")

    result = eos_get_bfd_peers("broken-switch")

    assert result["status"] == "error"
    assert "Unexpected error" in result["error"]


def test_get_bfd_peer_detail_generic_exception(mock_conn_mgr, mock_node):
    """Should return error on unexpected exceptions for detail query."""
    mock_node.run_commands.side_effect = RuntimeError("Something broke")

    result = eos_get_bfd_peer_detail("broken-switch", peer="10.0.0.2")

    assert result["status"] == "error"
    assert "Unexpected error" in result["error"]


def test_get_bfd_peers_with_multiple_up_peers(mock_conn_mgr, mock_node):
    """Should handle response with multiple peers in various states."""
    mock_node.run_commands.return_value = [MOCK_BFD_PEERS]

    result = eos_get_bfd_peers("test-switch")

    assert result["status"] == "success"
    neighbors = result["data"]["vrfs"]["default"]["ipv4Neighbors"]
    # Verify both peers are present
    assert len(neighbors) == 2
    # One up, one down
    assert neighbors["10.0.0.2"]["peerStats"][""]["status"] == "up"
    assert neighbors["10.0.0.3"]["peerStats"][""]["status"] == "down"
