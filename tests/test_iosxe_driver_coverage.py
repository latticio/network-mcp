"""Tests to close coverage gaps in network_mcp/drivers/cisco_iosxe.py.

Targets the uncovered static parser methods and error/exception branches:
- _normalize_lldp_detail_restconf / _normalize_lldp_detail_ssh
- _parse_trunks_ssh
- _parse_vrf_detail_ssh
- _parse_ping_output
- _parse_traceroute_output
- _parse_flap_events
- get_aaa_status / get_user_sessions / get_copp_policy (NotSupportedError)
- RESTCONF/SSH error paths
- _check_alive branches
- config/run_config aliases
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.drivers.cisco_iosxe import IosXeDriver


def _make_driver(ssh_output: str | Exception = "") -> IosXeDriver:
    """Build an IosXeDriver with a mocked SSH transport."""
    driver = IosXeDriver()
    driver._connected = True
    driver._transport = "ssh"
    driver._ssh_host = "test-iosxe"
    driver._ssh_username = "admin"
    driver._ssh_password = "pass"
    driver._last_command_time = 0.0
    driver._keepalive_interval = 99999

    mock_transport = MagicMock()
    if isinstance(ssh_output, Exception):
        mock_transport.send_command.side_effect = ssh_output
    else:
        mock_transport.send_command.return_value = ssh_output
    driver._ssh_transport = mock_transport
    driver._restconf = None
    return driver


# ---------------------------------------------------------------------------
# _normalize_lldp_detail_restconf (lines 993-1015)
# ---------------------------------------------------------------------------

class TestNormalizeLldpDetailRestconf:
    def test_basic_normalization(self):
        data = {
            "Cisco-IOS-XE-lldp-oper:lldp-entries": {
                "lldp-entry": [
                    {
                        "local-interface": "GigabitEthernet0/0",
                        "device-id": "spine-01",
                        "connecting-interface": "Ethernet1",
                        "system-desc": "Arista EOS",
                        "chassis-id": "00:1c:73:00:01:01",
                        "mgmt-addr": "10.0.0.1",
                        "capability": [
                            {"name": "Bridge"},
                            {"name": "Router"},
                        ],
                    }
                ]
            }
        }
        result = IosXeDriver._normalize_lldp_detail_restconf(data, "")
        assert "GigabitEthernet0/0" in result
        neighbor = result["GigabitEthernet0/0"][0]
        assert neighbor["hostname"] == "spine-01"
        assert neighbor["chassis_id"] == "00:1c:73:00:01:01"
        assert "bridge" in neighbor["capabilities"]
        assert "router" in neighbor["capabilities"]

    def test_filter_by_interface(self):
        data = {
            "Cisco-IOS-XE-lldp-oper:lldp-entries": {
                "lldp-entry": [
                    {"local-interface": "Gi0/0", "device-id": "sw1", "connecting-interface": "Eth1"},
                    {"local-interface": "Gi0/1", "device-id": "sw2", "connecting-interface": "Eth2"},
                ]
            }
        }
        result = IosXeDriver._normalize_lldp_detail_restconf(data, "Gi0/0")
        assert "Gi0/0" in result
        assert "Gi0/1" not in result

    def test_empty_entries(self):
        data = {"Cisco-IOS-XE-lldp-oper:lldp-entries": {"lldp-entry": []}}
        result = IosXeDriver._normalize_lldp_detail_restconf(data, "")
        assert result == {}

    def test_capability_not_list(self):
        data = {
            "lldp-entry": [
                {
                    "local-interface": "Gi0/0",
                    "device-id": "sw1",
                    "capability": "not-a-list",
                }
            ]
        }
        result = IosXeDriver._normalize_lldp_detail_restconf(data, "")
        assert result["Gi0/0"][0]["capabilities"] == []


# ---------------------------------------------------------------------------
# _normalize_lldp_detail_ssh (lines 1017-1080)
# ---------------------------------------------------------------------------

class TestNormalizeLldpDetailSsh:
    def test_basic_ssh_parsing(self):
        output = (
            "Local Intf: GigabitEthernet0/0\n"
            "Chassis id: 00:1c:73:00:01:01\n"
            "Port id: Ethernet1\n"
            "System Name: spine-01\n"
            "System Description: Arista EOS\n"
            "Management Addresses:\n"
            "  IP: 10.0.0.1\n"
            "System Capabilities: Bridge, Router\n"
        )
        result = IosXeDriver._normalize_lldp_detail_ssh(output, "")
        assert "GigabitEthernet0/0" in result
        neighbor = result["GigabitEthernet0/0"][0]
        assert neighbor["hostname"] == "spine-01"
        assert neighbor["port"] == "Ethernet1"
        assert neighbor["chassis_id"] == "00:1c:73:00:01:01"
        assert neighbor["management_address"] == "10.0.0.1"
        assert "bridge" in neighbor["capabilities"]
        assert "router" in neighbor["capabilities"]

    def test_multiple_neighbors(self):
        output = (
            "Local Intf: Gi0/0\n"
            "System Name: sw1\n"
            "Port id: Eth1\n"
            "Chassis id: aa:bb:cc:dd:ee:01\n"
            "\n"
            "Local Intf: Gi0/1\n"
            "System Name: sw2\n"
            "Port id: Eth2\n"
            "Chassis id: aa:bb:cc:dd:ee:02\n"
        )
        result = IosXeDriver._normalize_lldp_detail_ssh(output, "")
        assert "Gi0/0" in result
        assert "Gi0/1" in result

    def test_filter_by_interface(self):
        output = (
            "Local Intf: Gi0/0\n"
            "System Name: sw1\n"
            "Port id: Eth1\n"
            "\n"
            "Local Intf: Gi0/1\n"
            "System Name: sw2\n"
            "Port id: Eth2\n"
        )
        result = IosXeDriver._normalize_lldp_detail_ssh(output, "Gi0/0")
        assert "Gi0/0" in result
        assert "Gi0/1" not in result

    def test_empty_output(self):
        result = IosXeDriver._normalize_lldp_detail_ssh("", "")
        assert result == {}


# ---------------------------------------------------------------------------
# _parse_trunks_ssh (lines 1874-1928)
# ---------------------------------------------------------------------------

class TestParseTrunksSsh:
    def test_basic_trunk_parsing(self):
        # Parser expects format: "Gi0/1  on  802.1q  <native_vlan>" at parts[3]
        output = (
            "Port        Mode             Encapsulation  Native vlan\n"
            "Gi0/1       on               802.1q         1\n"
            "Gi0/2       on               802.1q         100\n"
            "\n"
            "Port        Vlans allowed on trunk\n"
            "Gi0/1       1-4094\n"
            "Gi0/2       100-200\n"
            "\n"
            "Port        Vlans in spanning tree forwarding state and not pruned\n"
            "Gi0/1       1,10,20\n"
            "Gi0/2       100,150\n"
        )
        result = IosXeDriver._parse_trunks_ssh(output)
        assert len(result) == 2
        assert result[0]["interface"] == "Gi0/1"
        assert result[0]["mode"] == "trunk"
        assert result[0]["native_vlan"] == 1
        assert result[0]["allowed_vlans"] == "1-4094"
        assert result[0]["active_vlans"] == "1,10,20"
        assert result[1]["native_vlan"] == 100

    def test_empty_output(self):
        result = IosXeDriver._parse_trunks_ssh("")
        assert result == []

    def test_native_vlan_parse_error(self):
        output = (
            "Port        Mode             Encapsulation  Status        Native vlan\n"
            "Gi0/1       on               802.1q         trunking\n"
        )
        result = IosXeDriver._parse_trunks_ssh(output)
        # Should still parse with default native_vlan=1
        assert len(result) == 1
        assert result[0]["native_vlan"] == 1


# ---------------------------------------------------------------------------
# _parse_vrf_detail_ssh (lines 1939-1965)
# ---------------------------------------------------------------------------

class TestParseVrfDetailSsh:
    def test_basic_vrf_detail(self):
        output = (
            "VRF MGMT (VRF Id = 1)\n"
            "  Description: Management VRF\n"
            "  RD 65000:100\n"
            "  Import RT: 65000:100\n"
            "  Export RT: 65000:200\n"
            "  Interfaces:\n"
            "    GigabitEthernet0/0\n"
            "    Loopback0\n"
        )
        result = IosXeDriver._parse_vrf_detail_ssh("MGMT", output)
        assert result["name"] == "MGMT"
        assert result["rd"] == "65000:100"
        assert "GigabitEthernet0/0" in result["interfaces"]
        assert "Loopback0" in result["interfaces"]

    def test_empty_output(self):
        result = IosXeDriver._parse_vrf_detail_ssh("MGMT", "")
        assert result["name"] == "MGMT"
        assert result["interfaces"] == []


# ---------------------------------------------------------------------------
# _parse_ping_output (lines 1988-2013)
# ---------------------------------------------------------------------------

class TestParsePingOutput:
    def test_successful_ping(self):
        output = (
            "Type escape sequence to abort.\n"
            "Sending 5, 100-byte ICMP Echos to 10.0.0.1\n"
            "!!!!!\n"
            "Success rate is 100 percent (5/5)\n"
            "5 packets transmitted, 5 received, 0% packet loss\n"
            "round-trip min/avg/max = 1/2/4 ms"
        )
        result = IosXeDriver._parse_ping_output(output, 5)
        assert result["success"] is True
        assert result["packets_sent"] == 5
        assert result["packets_received"] == 5
        assert result["rtt_min"] == 1.0
        assert result["rtt_avg"] == 2.0
        assert result["rtt_max"] == 4.0

    def test_failed_ping(self):
        output = "5 packets transmitted, 0 received, 100% packet loss"
        result = IosXeDriver._parse_ping_output(output, 5)
        assert result["success"] is False
        assert result["packet_loss_pct"] == 100.0

    def test_no_match(self):
        result = IosXeDriver._parse_ping_output("garbage", 5)
        assert result["success"] is False


# ---------------------------------------------------------------------------
# _parse_traceroute_output (lines 2026-2040)
# ---------------------------------------------------------------------------

class TestParseTracerouteOutput:
    def test_successful_traceroute(self):
        output = (
            "traceroute to 10.0.0.1\n"
            " 1  10.0.0.254 1.234 ms  1.1 ms  1.0 ms\n"
            " 2  10.0.0.1 2.345 ms  2.2 ms  2.1 ms\n"
        )
        result = IosXeDriver._parse_traceroute_output(output)
        assert len(result["hops"]) == 2
        assert result["hops"][0]["hop"] == 1
        assert result["hops"][0]["address"] == "10.0.0.254"

    def test_traceroute_with_stars(self):
        output = " 1  * * *\n 2  10.0.0.1 2.345 ms\n"
        result = IosXeDriver._parse_traceroute_output(output)
        assert result["hops"][0]["address"] == "*"

    def test_empty_output(self):
        result = IosXeDriver._parse_traceroute_output("")
        assert result["hops"] == []


# ---------------------------------------------------------------------------
# _parse_flap_events (lines 2050-2071)
# ---------------------------------------------------------------------------

class TestParseFlapEvents:
    def test_basic_flap_parsing(self):
        output = (
            "Jan  1 10:30:00 GigabitEthernet0/0 changed state to down\n"
            "Jan  1 10:31:00 GigabitEthernet0/0 changed state to up\n"
        )
        result = IosXeDriver._parse_flap_events(output)
        assert len(result) == 2
        assert result[0]["event"] == "link down"
        assert result[0]["interface"] == "GigabitEthernet0/0"
        assert result[1]["event"] == "link up"

    def test_line_protocol_event(self):
        output = "Jan  1 10:30:00 TenGigabitEthernet1/0 line protocol changed state to down\n"
        result = IosXeDriver._parse_flap_events(output)
        assert len(result) == 1
        assert result[0]["interface"] == "TenGigabitEthernet1/0"

    def test_unknown_interface(self):
        output = "changed state to up on something\n"
        result = IosXeDriver._parse_flap_events(output)
        assert len(result) == 1
        assert result[0]["interface"] == "unknown"

    def test_no_matching_events(self):
        output = "Jan  1 10:30:00 Some other log message\n"
        result = IosXeDriver._parse_flap_events(output)
        assert result == []


# ---------------------------------------------------------------------------
# NotSupportedError methods (lines 2073-2083)
# ---------------------------------------------------------------------------

class TestNotSupportedMethods:
    def test_get_aaa_status_raises(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError, match="get_aaa_status"):
            driver.get_aaa_status()

    def test_get_user_sessions_raises(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError, match="get_user_sessions"):
            driver.get_user_sessions()

    def test_get_copp_policy_raises(self):
        driver = _make_driver()
        with pytest.raises(NotSupportedError, match="get_copp_policy"):
            driver.get_copp_policy()


# ---------------------------------------------------------------------------
# get_trunk_interfaces (lines 1867-1872, exception path)
# ---------------------------------------------------------------------------

class TestGetTrunkInterfaces:
    def test_exception_returns_empty(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_trunk_interfaces()
        assert result == []


# ---------------------------------------------------------------------------
# get_vrf_detail (lines 1932-1937, exception path)
# ---------------------------------------------------------------------------

class TestGetVrfDetail:
    def test_exception_returns_empty(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_vrf_detail("MGMT")
        assert result == {}


# ---------------------------------------------------------------------------
# ping (lines 1969-1986, exception path)
# ---------------------------------------------------------------------------

class TestPing:
    def test_exception_returns_failure(self):
        driver = _make_driver(RuntimeError("connection refused"))
        result = driver.ping("10.0.0.1", count=3)
        assert result["success"] is False
        assert result["packets_sent"] == 3
        assert "connection refused" in result["output"]

    def test_ping_with_vrf_and_source(self):
        driver = _make_driver("5 packets transmitted, 5 received, 0% packet loss")
        driver.ping("10.0.0.1", count=3, source="Loopback0", vrf="MGMT")
        call_args = driver._ssh_transport.send_command.call_args
        cmd = call_args[0][0]
        assert "vrf MGMT" in cmd
        assert "source Loopback0" in cmd


# ---------------------------------------------------------------------------
# traceroute (lines 2015-2024, exception path)
# ---------------------------------------------------------------------------

class TestTraceroute:
    def test_exception_returns_empty(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.traceroute("10.0.0.1")
        assert result["hops"] == []
        assert "timeout" in result["output"]

    def test_traceroute_with_vrf_and_source(self):
        driver = _make_driver(" 1  10.0.0.254 1.0 ms\n")
        driver.traceroute("10.0.0.1", source="Loopback0", vrf="MGMT")
        call_args = driver._ssh_transport.send_command.call_args
        cmd = call_args[0][0]
        assert "vrf MGMT" in cmd
        assert "source Loopback0" in cmd


# ---------------------------------------------------------------------------
# get_interface_flaps (lines 2042-2048, exception path)
# ---------------------------------------------------------------------------

class TestGetInterfaceFlaps:
    def test_exception_returns_empty(self):
        driver = _make_driver(RuntimeError("timeout"))
        result = driver.get_interface_flaps()
        assert result == []


# ---------------------------------------------------------------------------
# RESTCONF-specific paths and error branches
# ---------------------------------------------------------------------------

class TestRestconfErrorPaths:
    def test_get_lldp_neighbor_detail_restconf_failure_falls_back_to_ssh(self):
        """RESTCONF failure should fall back to SSH (line 988)."""
        driver = IosXeDriver()
        driver._connected = True
        driver._last_command_time = 0.0
        driver._keepalive_interval = 99999

        mock_restconf = MagicMock()
        mock_restconf.get.side_effect = RuntimeError("RESTCONF error")
        driver._restconf = mock_restconf

        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = "Local Intf: Gi0/0\nSystem Name: sw1\nPort id: Eth1\n"
        driver._ssh_transport = mock_ssh
        driver._ssh_host = "test"
        driver._ssh_username = "admin"

        result = driver.get_lldp_neighbor_detail()
        assert "Gi0/0" in result

    def test_check_alive_restconf_path(self):
        """Test _check_alive with RESTCONF transport."""
        driver = IosXeDriver()
        driver._connected = True
        driver._last_command_time = 0.0  # long idle
        driver._keepalive_interval = 0  # always check

        mock_restconf = MagicMock()
        mock_restconf.get.return_value = MagicMock()
        driver._restconf = mock_restconf
        driver._ssh_transport = None
        driver._ssh_host = "test"

        driver._check_alive()
        mock_restconf.get.assert_called_once()

    def test_check_alive_ssh_path(self):
        """Test _check_alive with SSH transport only."""
        driver = IosXeDriver()
        driver._connected = True
        driver._last_command_time = 0.0
        driver._keepalive_interval = 0

        driver._restconf = None
        mock_ssh = MagicMock()
        mock_ssh.is_alive = True
        driver._ssh_transport = mock_ssh
        driver._ssh_host = "test"

        driver._check_alive()
        mock_ssh.send_command.assert_called_once_with("show clock")

    def test_check_alive_no_transport_reconnects(self):
        """Test _check_alive with no active transport raises and reconnects."""
        driver = IosXeDriver()
        driver._connected = True
        driver._last_command_time = 0.0
        driver._keepalive_interval = 0

        driver._restconf = None
        driver._ssh_transport = None
        driver._ssh_host = "test"

        with patch.object(driver, "reconnect") as mock_reconnect:
            driver._check_alive()
            mock_reconnect.assert_called_once()


# ---------------------------------------------------------------------------
# _ssh_exec error branches (lines 522, 530)
# ---------------------------------------------------------------------------

class TestSshExecErrors:
    def test_ssh_not_configured(self):
        driver = IosXeDriver()
        driver._connected = True
        driver._ssh_host = ""
        driver._ssh_username = ""
        driver._ssh_transport = MagicMock()
        with pytest.raises(ConnectionError, match="SSH not configured"):
            driver._ssh_exec("show version")

    def test_ssh_transport_not_available(self):
        driver = IosXeDriver()
        driver._connected = True
        driver._ssh_host = "test"
        driver._ssh_username = "admin"
        driver._ssh_transport = None
        with pytest.raises(ConnectionError, match="SSH transport not available"):
            driver._ssh_exec("show version")


# ---------------------------------------------------------------------------
# config/run_config aliases (lines 441, 445)
# ---------------------------------------------------------------------------

class TestConfigAliases:
    def test_config_alias(self):
        driver = _make_driver("")
        driver._ssh_transport.send_config.return_value = "ok"
        result = driver.config(["vlan 100"])
        assert result == ["ok"]

    def test_run_commands_alias(self):
        driver = _make_driver("show version output")
        result = driver.run_commands(["show version"])
        assert len(result) == 1
        assert result[0]["output"] == "show version output"


# ---------------------------------------------------------------------------
# _restconf_show error / fallback paths (lines 459-465)
# ---------------------------------------------------------------------------

class TestRestconfShow:
    def test_restconf_show_unmapped_command_falls_to_ssh(self):
        """Commands without RESTCONF mapping should fall back to SSH."""
        driver = IosXeDriver()
        driver._connected = True
        driver._last_command_time = 0.0
        driver._keepalive_interval = 99999

        mock_restconf = MagicMock()
        driver._restconf = mock_restconf

        mock_ssh = MagicMock()
        mock_ssh.send_command.return_value = "some output"
        driver._ssh_transport = mock_ssh
        driver._ssh_host = "test"
        driver._ssh_username = "admin"

        result = driver._restconf_show(["show some-unknown-command"])
        assert len(result) == 1
        assert result[0]["output"] == "some output"
