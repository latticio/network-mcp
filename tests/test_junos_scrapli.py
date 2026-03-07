"""Tests for JunOS scrapli SSH transport and fallback.

Validates the JunosScrapliTransport class and its integration with
JunosDriver, including:
- NETCONF → SSH fallback when NETCONF fails
- SSH text parser correctness for all 6 normalized getters
- Session reuse, timeout handling, graceful degradation
- Existing NETCONF path remains unchanged
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.juniper_junos import (
    HAS_SCRAPLI,
    JunosDriver,
    JunosScrapliTransport,
    _parse_arp_ssh,
    _parse_bgp_summary_ssh,
    _parse_facts_ssh,
    _parse_interfaces_ssh,
    _parse_lldp_ssh,
)

# --- Sample CLI text outputs for parser tests ---

SAMPLE_SHOW_VERSION = """\
Hostname: junos-spine-01
Model: qfx5120-48t
Junos: 23.4R1.10
JUNOS Base OS boot [23.4R1.10]
JUNOS Base OS Software Suite [23.4R1.10]
JUNOS Kernel Software Suite [23.4R1.10]
JUNOS Crypto Software Suite [23.4R1.10]
JUNOS Online Documentation [23.4R1.10]
Chassis                          QFX5120-48T          Serial Number WH0218440033
"""

SAMPLE_SHOW_VERSION_UPTIME = """\
Hostname: junos-spine-01
Model: qfx5120-48t
Junos: 23.4R1.10
Current time: 2024-03-15 12:00:00 UTC
System booted: 2024-03-05 08:14:48 UTC (10 days, 3:45:12 ago)
"""

SAMPLE_SHOW_INTERFACES_TERSE = """\
Interface               Admin Link Proto    Local                 Remote
ge-0/0/0                up    up
ge-0/0/0.0              up    up   inet     10.0.0.1/30
ge-0/0/1                up    down
xe-0/0/0                up    up
xe-0/0/0.0              up    up   inet     10.0.1.1/30
lo0                     up    up
lo0.0                   up    up   inet     10.255.0.1/32
"""

SAMPLE_SHOW_BGP_SUMMARY = """\
Threading mode: BGP I/O
Default eBGP mode: advertise - Loss of Stability|Alarm
Groups: 2 Peers: 2 Down peers: 0
Table          Tot Paths  Act Paths Suppressed    History Damp State    Pending
inet.0
                     350        150          0          0          0          0
Peer                     AS      InPkt     OutPkt    OutQ   Flaps Last Up/Dwn State|#Active/Received/Accepted/Damped...
10.0.0.2              65001       1234       1235       0       0     1d 2:03:04 Establ
  inet.0: 150/200/150/0
10.0.0.3              65002          0          0       0       0        3:45:12 Active
"""

SAMPLE_SHOW_BGP_SUMMARY_WITH_ROUTER_ID = """\
Threading mode: BGP I/O
Default eBGP mode: advertise - Loss of Stability|Alarm
Router ID: 10.0.0.1  Local AS: 65000
Groups: 1 Peers: 1 Down peers: 0
Peer                     AS      InPkt     OutPkt    OutQ   Flaps Last Up/Dwn State|#Active/Received/Accepted/Damped...
10.0.0.2              65001       1234       1235       0       0     1d 2:03:04 Establ
  inet.0: 150/200/150/0
"""

SAMPLE_SHOW_ARP = """\
MAC Address       Address         Name                      Interface               Flags
00:05:86:cc:dd:01 10.0.0.2        10.0.0.2                  ge-0/0/0                none
00:05:86:cc:dd:02 10.0.0.3        10.0.0.3                  ge-0/0/1                none
Total entries: 2
"""

SAMPLE_SHOW_LLDP = """\
Local Interface    Parent Interface    Chassis Id          Port info          System Name
ge-0/0/0           -                   00:05:86:71:62:02   ge-0/0/1           spine-02.lab
xe-0/0/0           -                   00:05:86:71:62:03   xe-0/0/1           leaf-01.lab
"""


# --- SSH text parser tests ---


class TestParseFactsSsh:
    """Test _parse_facts_ssh() text parser."""

    def test_basic_fields(self):
        facts = _parse_facts_ssh(SAMPLE_SHOW_VERSION)
        assert facts["hostname"] == "junos-spine-01"
        assert facts["model"] == "qfx5120-48t"
        assert facts["version"] == "23.4R1.10"
        assert facts["vendor"] == "juniper"
        assert facts["platform"] == "junos"

    def test_serial_from_chassis_line(self):
        facts = _parse_facts_ssh(SAMPLE_SHOW_VERSION)
        assert facts["serial"] == "WH0218440033"

    def test_uptime_parsing(self):
        facts = _parse_facts_ssh(SAMPLE_SHOW_VERSION_UPTIME)
        expected = 10 * 86400 + 3 * 3600 + 45 * 60 + 12
        assert facts["uptime_seconds"] == expected

    def test_schema_keys(self):
        facts = _parse_facts_ssh(SAMPLE_SHOW_VERSION)
        required = {"hostname", "model", "version", "serial", "uptime_seconds", "vendor", "platform"}
        assert set(facts.keys()) == required

    def test_types(self):
        facts = _parse_facts_ssh(SAMPLE_SHOW_VERSION)
        assert isinstance(facts["hostname"], str)
        assert isinstance(facts["model"], str)
        assert isinstance(facts["version"], str)
        assert isinstance(facts["serial"], str)
        assert isinstance(facts["uptime_seconds"], int)

    def test_empty_output(self):
        facts = _parse_facts_ssh("")
        assert facts["hostname"] == ""
        assert facts["model"] == ""
        assert facts["version"] == ""
        assert facts["uptime_seconds"] == 0


class TestParseInterfacesSsh:
    """Test _parse_interfaces_ssh() text parser."""

    def test_returns_dict(self):
        interfaces = _parse_interfaces_ssh(SAMPLE_SHOW_INTERFACES_TERSE)
        assert isinstance(interfaces, dict)

    def test_physical_interfaces_only(self):
        """Logical interfaces (with .N suffix) should be excluded."""
        interfaces = _parse_interfaces_ssh(SAMPLE_SHOW_INTERFACES_TERSE)
        assert "ge-0/0/0" in interfaces
        assert "ge-0/0/1" in interfaces
        assert "xe-0/0/0" in interfaces
        assert "lo0" in interfaces
        # Logical interfaces excluded
        assert "ge-0/0/0.0" not in interfaces
        assert "xe-0/0/0.0" not in interfaces
        assert "lo0.0" not in interfaces

    def test_up_interface(self):
        interfaces = _parse_interfaces_ssh(SAMPLE_SHOW_INTERFACES_TERSE)
        ge0 = interfaces["ge-0/0/0"]
        assert ge0["is_up"] is True
        assert ge0["is_enabled"] is True

    def test_down_interface(self):
        interfaces = _parse_interfaces_ssh(SAMPLE_SHOW_INTERFACES_TERSE)
        ge1 = interfaces["ge-0/0/1"]
        assert ge1["is_up"] is False
        assert ge1["is_enabled"] is True  # admin up, link down

    def test_schema_keys(self):
        interfaces = _parse_interfaces_ssh(SAMPLE_SHOW_INTERFACES_TERSE)
        required = {"is_up", "is_enabled", "description", "speed", "mtu", "mac_address"}
        for name, data in interfaces.items():
            assert set(data.keys()) == required, f"Interface {name} has wrong keys"

    def test_empty_output(self):
        assert _parse_interfaces_ssh("") == {}


class TestParseBgpSummarySsh:
    """Test _parse_bgp_summary_ssh() text parser."""

    def test_schema_keys(self):
        bgp = _parse_bgp_summary_ssh(SAMPLE_SHOW_BGP_SUMMARY)
        assert set(bgp.keys()) == {"router_id", "as_number", "peers"}

    def test_router_id_and_as(self):
        bgp = _parse_bgp_summary_ssh(SAMPLE_SHOW_BGP_SUMMARY_WITH_ROUTER_ID)
        assert bgp["router_id"] == "10.0.0.1"
        assert bgp["as_number"] == 65000

    def test_peer_count(self):
        bgp = _parse_bgp_summary_ssh(SAMPLE_SHOW_BGP_SUMMARY)
        assert len(bgp["peers"]) == 2
        assert "10.0.0.2" in bgp["peers"]
        assert "10.0.0.3" in bgp["peers"]

    def test_established_peer(self):
        bgp = _parse_bgp_summary_ssh(SAMPLE_SHOW_BGP_SUMMARY)
        peer = bgp["peers"]["10.0.0.2"]
        assert peer["state"] == "Established"
        assert peer["prefixes_received"] == 200  # received count from inet.0 line

    def test_active_peer(self):
        bgp = _parse_bgp_summary_ssh(SAMPLE_SHOW_BGP_SUMMARY)
        peer = bgp["peers"]["10.0.0.3"]
        assert peer["state"] == "Active"
        assert peer["prefixes_received"] == 0

    def test_peer_schema(self):
        bgp = _parse_bgp_summary_ssh(SAMPLE_SHOW_BGP_SUMMARY)
        required = {"state", "prefixes_received", "uptime"}
        for ip, data in bgp["peers"].items():
            assert set(data.keys()) == required, f"Peer {ip} has wrong keys"

    def test_empty_output(self):
        bgp = _parse_bgp_summary_ssh("")
        assert bgp["router_id"] == ""
        assert bgp["as_number"] == 0
        assert bgp["peers"] == {}


class TestParseArpSsh:
    """Test _parse_arp_ssh() text parser."""

    def test_returns_list(self):
        arp = _parse_arp_ssh(SAMPLE_SHOW_ARP)
        assert isinstance(arp, list)
        assert len(arp) == 2

    def test_entry_values(self):
        arp = _parse_arp_ssh(SAMPLE_SHOW_ARP)
        assert arp[0]["ip"] == "10.0.0.2"
        assert arp[0]["mac"] == "00:05:86:cc:dd:01"
        assert arp[0]["interface"] == "ge-0/0/0"
        assert arp[0]["age"] == -1.0  # no age in 'show arp no-resolve'

    def test_entry_schema(self):
        arp = _parse_arp_ssh(SAMPLE_SHOW_ARP)
        required = {"ip", "mac", "interface", "age"}
        for entry in arp:
            assert set(entry.keys()) == required

    def test_types(self):
        arp = _parse_arp_ssh(SAMPLE_SHOW_ARP)
        for entry in arp:
            assert isinstance(entry["ip"], str)
            assert isinstance(entry["mac"], str)
            assert isinstance(entry["interface"], str)
            assert isinstance(entry["age"], float)

    def test_empty_output(self):
        assert _parse_arp_ssh("") == []

    def test_skips_header_and_total(self):
        """Header and 'Total entries' lines should be skipped."""
        arp = _parse_arp_ssh(SAMPLE_SHOW_ARP)
        # Should only have the 2 actual entries, not header or total
        assert len(arp) == 2


class TestParseLldpSsh:
    """Test _parse_lldp_ssh() text parser."""

    def test_returns_dict(self):
        lldp = _parse_lldp_ssh(SAMPLE_SHOW_LLDP)
        assert isinstance(lldp, dict)
        assert len(lldp) == 2

    def test_interface_names(self):
        lldp = _parse_lldp_ssh(SAMPLE_SHOW_LLDP)
        assert "ge-0/0/0" in lldp
        assert "xe-0/0/0" in lldp

    def test_neighbor_values(self):
        lldp = _parse_lldp_ssh(SAMPLE_SHOW_LLDP)
        ge0 = lldp["ge-0/0/0"]
        assert len(ge0) == 1
        assert ge0[0]["hostname"] == "spine-02.lab"
        assert ge0[0]["port"] == "ge-0/0/1"

    def test_neighbor_schema(self):
        lldp = _parse_lldp_ssh(SAMPLE_SHOW_LLDP)
        required = {"hostname", "port", "system_description", "management_ip"}
        for intf, neighbors in lldp.items():
            for n in neighbors:
                assert set(n.keys()) == required, f"{intf}: wrong keys"

    def test_empty_output(self):
        assert _parse_lldp_ssh("") == {}


# --- JunosScrapliTransport unit tests ---


class TestJunosScrapliTransportInit:
    """Test JunosScrapliTransport initialization."""

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_init_creates_driver(self):
        """Constructor creates a ScrapliJunosDriver with correct params."""
        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_cls:
            transport = JunosScrapliTransport(
                host="10.0.0.1",
                username="admin",
                password="junos123",
                port=22,
            )
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["host"] == "10.0.0.1"
            assert call_kwargs["auth_username"] == "admin"
            assert call_kwargs["auth_password"] == "junos123"
            assert call_kwargs["port"] == 22
            assert call_kwargs["auth_strict_key"] is True
            assert call_kwargs["transport"] == "system"
            assert call_kwargs["timeout_socket"] == 10
            assert call_kwargs["timeout_ops"] == 30
            assert transport.is_alive is False

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_init_with_private_key(self):
        """Private key is passed to driver when provided."""
        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_cls:
            JunosScrapliTransport(
                host="10.0.0.1",
                username="admin",
                password="",
                auth_private_key="/home/user/.ssh/id_rsa",
            )
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["auth_private_key"] == "/home/user/.ssh/id_rsa"

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_init_strict_key_disabled(self):
        """Host key verification can be disabled."""
        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_cls:
            JunosScrapliTransport(
                host="10.0.0.1",
                username="admin",
                password="junos123",
                auth_strict_key=False,
            )
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["auth_strict_key"] is False

    def test_init_without_scrapli_raises(self):
        """ImportError raised when scrapli is not installed."""
        with patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI", False):
            with pytest.raises(ImportError, match="scrapli not installed"):
                JunosScrapliTransport(
                    host="10.0.0.1",
                    username="admin",
                    password="junos123",
                )


class TestJunosScrapliTransportSendCommand:
    """Test JunosScrapliTransport.send_command()."""

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_send_command_returns_output(self):
        """send_command() returns the result string from scrapli."""
        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = "Juniper Networks JunOS Software"
            mock_driver.send_command.return_value = mock_response

            transport = JunosScrapliTransport(host="10.0.0.1", username="admin", password="junos123")
            transport._connected = True

            output = transport.send_command("show version")
            assert output == "Juniper Networks JunOS Software"
            mock_driver.send_command.assert_called_once_with("show version")

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_send_command_auto_opens(self):
        """send_command() calls open() if not connected."""
        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = "output"
            mock_driver.send_command.return_value = mock_response

            transport = JunosScrapliTransport(host="10.0.0.1", username="admin", password="junos123")
            assert transport.is_alive is False

            transport.send_command("show version")
            mock_driver.open.assert_called_once()
            assert transport.is_alive is True

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_send_command_failed_raises(self):
        """send_command() raises RuntimeError when scrapli reports failure."""
        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            mock_response = MagicMock()
            mock_response.failed = True
            mock_response.channel_input = "show bad-command"
            mock_driver.send_command.return_value = mock_response

            transport = JunosScrapliTransport(host="10.0.0.1", username="admin", password="junos123")
            transport._connected = True

            with pytest.raises(RuntimeError, match="Command failed"):
                transport.send_command("show bad-command")


class TestJunosScrapliTransportLifecycle:
    """Test open/close lifecycle."""

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_open_sets_connected(self):
        """open() marks transport as connected."""
        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            transport = JunosScrapliTransport(host="10.0.0.1", username="admin", password="junos123")
            assert transport.is_alive is False

            transport.open()
            mock_driver.open.assert_called_once()
            assert transport.is_alive is True

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_close_disconnects(self):
        """close() closes the driver and marks as disconnected."""
        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            transport = JunosScrapliTransport(host="10.0.0.1", username="admin", password="junos123")
            transport._connected = True

            transport.close()
            mock_driver.close.assert_called_once()
            assert transport.is_alive is False

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_close_when_not_connected_is_noop(self):
        """close() does nothing when not connected."""
        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_cls:
            mock_driver = MagicMock()
            mock_cls.return_value = mock_driver

            transport = JunosScrapliTransport(host="10.0.0.1", username="admin", password="junos123")
            transport.close()
            mock_driver.close.assert_not_called()


# --- JunosDriver SSH fallback integration tests ---


class TestJunosDriverNetconfToSshFallback:
    """Test JunosDriver NETCONF → SSH fallback."""

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_netconf_failure_triggers_ssh_fallback(self):
        """When NETCONF fails, driver creates scrapli SSH transport."""
        driver = JunosDriver()

        mock_netconf_cls = MagicMock()
        mock_netconf = MagicMock()
        mock_netconf.open.side_effect = Exception("Connection refused on port 830")
        mock_netconf_cls.return_value = mock_netconf

        with (
            patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI_NETCONF", True),
            patch("network_mcp.drivers.juniper_junos.NetconfDriver", mock_netconf_cls),
            patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_scrapli_cls,
        ):
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            driver.connect(host="10.0.0.1", username="admin", password="junos123")

        assert driver.is_connected()
        assert driver._transport == "ssh"
        assert driver._ssh_transport is not None
        assert driver._conn is None

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_ssh_direct_creates_scrapli_transport(self):
        """Direct SSH transport creates scrapli transport."""
        driver = JunosDriver()

        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="junos123")

        assert driver.is_connected()
        assert driver._transport == "ssh"
        assert driver._ssh_transport is not None

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_netconf_success_no_ssh(self):
        """When NETCONF succeeds, SSH transport is not created."""
        driver = JunosDriver()

        mock_netconf_cls = MagicMock()
        mock_netconf = MagicMock()
        mock_netconf_cls.return_value = mock_netconf

        with (
            patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI_NETCONF", True),
            patch("network_mcp.drivers.juniper_junos.NetconfDriver", mock_netconf_cls),
        ):
            driver.connect(host="10.0.0.1", username="admin", password="junos123")

        assert driver.is_connected()
        assert driver._transport == "netconf"
        assert driver._conn is not None
        assert driver._ssh_transport is None

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_ssh_session_reuse(self):
        """Second command reuses the same SSH session."""
        driver = JunosDriver()

        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = "output1"
            mock_scrapli_driver.send_command.return_value = mock_response

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="junos123")
            driver._ssh_transport._connected = True

            result1 = driver._ssh_exec("show version")
            mock_response.result = "output2"
            result2 = driver._ssh_exec("show interfaces terse")

            assert result1 == "output1"
            assert result2 == "output2"
            assert mock_scrapli_cls.call_count == 1

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_ssh_timeout_raises_timeout_error(self):
        """Scrapli timeout is converted to TimeoutError."""
        from scrapli.exceptions import ScrapliTimeout as Timeout

        driver = JunosDriver()

        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver
            mock_scrapli_driver.send_command.side_effect = Timeout("Timed out")

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="junos123")
            driver._ssh_transport._connected = True

            with pytest.raises(TimeoutError, match="timed out"):
                driver._ssh_exec("show version")

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_close_cleans_up_both_transports(self):
        """close() cleans up both NETCONF and scrapli transports."""
        driver = JunosDriver()

        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="junos123")
            assert driver._ssh_transport is not None
            driver._ssh_transport._connected = True

            driver.close()
            mock_scrapli_driver.close.assert_called_once()
            assert driver._ssh_transport is None
            assert not driver.is_connected()

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_ssh_config_via_scrapli(self):
        """Config commands are sent via scrapli send_configs."""
        driver = JunosDriver()

        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = ""
            mock_scrapli_driver.send_configs.return_value = mock_response

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="junos123")
            driver._ssh_transport._connected = True

            result = driver._ssh_config(["set system host-name test"])
            assert result == [""]
            mock_scrapli_driver.send_configs.assert_called_once_with(["set system host-name test"])


class TestJunosDriverNoScrapli:
    """Test graceful degradation when scrapli is not installed."""

    def test_ssh_fallback_without_scrapli_logs_warning(self):
        """When scrapli is not installed, SSH fallback logs a warning."""
        driver = JunosDriver()

        with (
            patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI", False),
            patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI_NETCONF", False),
            patch("network_mcp.drivers.juniper_junos.logger") as mock_logger,
        ):
            driver.connect(host="10.0.0.1", username="admin", password="junos123")

        assert driver.is_connected()
        assert driver._transport == "ssh"
        assert driver._ssh_transport is None
        mock_logger.warning.assert_any_call(
            "scrapli not installed — SSH fallback unavailable for %s. Install with: pip install latticio[juniper]",
            "10.0.0.1",
        )

    def test_ssh_exec_without_transport_raises(self):
        """_ssh_exec raises ConnectionError when scrapli transport is None."""
        driver = JunosDriver()
        driver._connected = True
        driver._host = "10.0.0.1"
        driver._username = "admin"
        driver._ssh_transport = None

        with pytest.raises(ConnectionError, match="SSH transport not available"):
            driver._ssh_exec("show version")

    def test_ssh_config_without_transport_raises(self):
        """_ssh_config raises ConnectionError when scrapli transport is None."""
        driver = JunosDriver()
        driver._connected = True
        driver._ssh_transport = None

        with pytest.raises(ConnectionError, match="SSH transport not available"):
            driver._ssh_config(["set system host-name test"])

    def test_ssh_show_without_transport_returns_error(self):
        """_ssh_show returns error dict when SSH transport unavailable."""
        driver = JunosDriver()
        driver._connected = True
        driver._host = "10.0.0.1"
        driver._username = "admin"
        driver._ssh_transport = None

        results = driver._ssh_show(["show version"])
        assert len(results) == 1
        assert "error" in results[0]
        assert "SSH transport not available" in results[0]["error"]

    def test_both_transports_fail_clear_error(self):
        """When NETCONF and SSH both fail, error is clear."""
        driver = JunosDriver()

        mock_netconf_cls = MagicMock()
        mock_netconf = MagicMock()
        mock_netconf.open.side_effect = Exception("NETCONF port blocked")
        mock_netconf_cls.return_value = mock_netconf

        with (
            patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI", False),
            patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI_NETCONF", True),
            patch("network_mcp.drivers.juniper_junos.NetconfDriver", mock_netconf_cls),
        ):
            driver.connect(host="10.0.0.1", username="admin", password="junos123")

        # Driver is "connected" but with no working transport
        assert driver.is_connected()
        assert driver._transport == "ssh"
        assert driver._ssh_transport is None
        assert driver._conn is None


class TestJunosDriverSshGetters:
    """Test that normalized getters fall back to SSH when NETCONF unavailable."""

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_get_facts_ssh_fallback(self):
        """get_facts() uses SSH when NETCONF is unavailable."""
        driver = JunosDriver()

        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = SAMPLE_SHOW_VERSION
            mock_scrapli_driver.send_command.return_value = mock_response

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="junos123")
            driver._ssh_transport._connected = True

            facts = driver.get_facts()
            assert facts["hostname"] == "junos-spine-01"
            assert facts["model"] == "qfx5120-48t"
            assert facts["version"] == "23.4R1.10"

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_get_interfaces_ssh_fallback(self):
        """get_interfaces() uses SSH when NETCONF is unavailable."""
        driver = JunosDriver()

        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = SAMPLE_SHOW_INTERFACES_TERSE
            mock_scrapli_driver.send_command.return_value = mock_response

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="junos123")
            driver._ssh_transport._connected = True

            interfaces = driver.get_interfaces()
            assert "ge-0/0/0" in interfaces
            assert interfaces["ge-0/0/0"]["is_up"] is True

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_get_bgp_summary_ssh_fallback(self):
        """get_bgp_summary() uses SSH when NETCONF is unavailable."""
        driver = JunosDriver()

        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = SAMPLE_SHOW_BGP_SUMMARY_WITH_ROUTER_ID
            mock_scrapli_driver.send_command.return_value = mock_response

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="junos123")
            driver._ssh_transport._connected = True

            bgp = driver.get_bgp_summary()
            assert bgp["router_id"] == "10.0.0.1"
            assert bgp["as_number"] == 65000
            assert "10.0.0.2" in bgp["peers"]

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_get_arp_table_ssh_fallback(self):
        """get_arp_table() uses SSH when NETCONF is unavailable."""
        driver = JunosDriver()

        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = SAMPLE_SHOW_ARP
            mock_scrapli_driver.send_command.return_value = mock_response

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="junos123")
            driver._ssh_transport._connected = True

            arp = driver.get_arp_table()
            assert len(arp) == 2
            assert arp[0]["ip"] == "10.0.0.2"

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_get_lldp_neighbors_ssh_fallback(self):
        """get_lldp_neighbors() uses SSH when NETCONF is unavailable."""
        driver = JunosDriver()

        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = SAMPLE_SHOW_LLDP
            mock_scrapli_driver.send_command.return_value = mock_response

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="junos123")
            driver._ssh_transport._connected = True

            lldp = driver.get_lldp_neighbors()
            assert "ge-0/0/0" in lldp
            assert lldp["ge-0/0/0"][0]["hostname"] == "spine-02.lab"

    @pytest.mark.skipif(not HAS_SCRAPLI, reason="scrapli not installed")
    def test_get_config_ssh_fallback(self):
        """get_config() uses SSH when NETCONF is unavailable."""
        driver = JunosDriver()

        with patch("network_mcp.drivers.juniper_junos.ScrapliJunosDriver") as mock_scrapli_cls:
            mock_scrapli_driver = MagicMock()
            mock_scrapli_cls.return_value = mock_scrapli_driver

            mock_response = MagicMock()
            mock_response.failed = False
            mock_response.result = "system {\n    host-name junos-spine-01;\n}"
            mock_scrapli_driver.send_command.return_value = mock_response

            driver.connect(host="10.0.0.1", transport="ssh", username="admin", password="junos123")
            driver._ssh_transport._connected = True

            config = driver.get_config(sanitized=False)
            assert "host-name" in config["running"]
            assert config["running"] == config["startup"]
