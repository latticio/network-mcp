"""Tests for demo mode: MockDriver, mock data, and demo inventory."""

import pytest

from network_mcp.driver import DeviceDriver
from network_mcp.mock_data.inventory import DemoInventoryBackend
from network_mcp.mock_data.responses import (
    BGP_SUMMARY,
    DEVICE_INFO,
    LLDP_NEIGHBORS,
    MLAG_STATUS,
    get_response,
)
from network_mcp.mock_driver import MockDriver

# ─── MockDriver protocol compliance ─────────────────────────────────────


class TestMockDriverProtocol:
    """Verify MockDriver satisfies the DeviceDriver protocol."""

    def test_is_device_driver(self):
        driver = MockDriver()
        assert isinstance(driver, DeviceDriver)

    def test_platform(self):
        driver = MockDriver()
        assert driver.platform == "eos-mock"

    def test_connect_disconnect(self):
        driver = MockDriver()
        assert not driver.is_connected()
        driver.connect(host="spine-01")
        assert driver.is_connected()
        driver.close()
        assert not driver.is_connected()

    def test_run_show_not_connected_raises(self):
        driver = MockDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_show(["show version"])

    def test_run_config_not_connected_raises(self):
        driver = MockDriver()
        with pytest.raises(ConnectionError, match="not connected"):
            driver.run_config(["vlan 100"])

    def test_run_commands_alias(self):
        driver = MockDriver()
        driver.connect(host="spine-01")
        result_show = driver.run_show(["show version"])
        result_alias = driver.run_commands(["show version"])
        assert result_show == result_alias

    def test_config_alias(self):
        driver = MockDriver()
        driver.connect(host="spine-01")
        result_config = driver.run_config(["vlan 100"])
        result_alias = driver.config(["vlan 100"])
        assert len(result_config) == len(result_alias)


# ─── MockDriver show command responses ───────────────────────────────────


class TestMockDriverResponses:
    """Test that MockDriver returns correct data for mocked commands."""

    @pytest.fixture
    def spine_driver(self):
        d = MockDriver()
        d.connect(host="spine-01")
        return d

    @pytest.fixture
    def leaf_driver(self):
        d = MockDriver()
        d.connect(host="leaf-01")
        return d

    def test_show_version(self, spine_driver):
        result = spine_driver.run_show(["show version"])
        assert len(result) == 1
        assert result[0]["hostname"] == "spine-01"
        assert result[0]["version"] == "4.32.1F"
        assert "modelName" in result[0]

    def test_show_hostname(self, spine_driver):
        result = spine_driver.run_show(["show hostname"])
        assert result[0]["hostname"] == "spine-01"

    def test_show_bgp_summary(self, spine_driver):
        result = spine_driver.run_show(["show ip bgp summary"])
        assert len(result) == 1
        bgp = result[0]
        assert "vrfs" in bgp
        peers = bgp["vrfs"]["default"]["peers"]
        assert len(peers) == 4  # spine-01 peers with all 4 leaves

    def test_show_bgp_summary_leaf(self, leaf_driver):
        result = leaf_driver.run_show(["show ip bgp summary"])
        peers = result[0]["vrfs"]["default"]["peers"]
        assert len(peers) == 2  # leaf-01 peers with 2 spines

    def test_show_interfaces_status(self, spine_driver):
        result = spine_driver.run_show(["show interfaces status"])
        assert "interfaceStatuses" in result[0]
        interfaces = result[0]["interfaceStatuses"]
        assert "Ethernet1" in interfaces
        assert interfaces["Ethernet1"]["linkStatus"] == "connected"

    def test_show_interfaces_counters(self, leaf_driver):
        result = leaf_driver.run_show(["show interfaces counters"])
        assert "interfaces" in result[0]

    def test_show_interfaces_counters_errors(self, leaf_driver):
        result = leaf_driver.run_show(["show interfaces counters errors"])
        assert "interfaceErrorCounters" in result[0]

    def test_show_mlag_leaf(self, leaf_driver):
        result = leaf_driver.run_show(["show mlag"])
        assert result[0]["state"] == "active"
        assert result[0]["negStatus"] == "connected"

    def test_show_mlag_spine_empty(self, spine_driver):
        # Spines don't run MLAG, but should return something (empty dict)
        result = spine_driver.run_show(["show mlag"])
        assert isinstance(result[0], dict)

    def test_show_vlan(self, leaf_driver):
        result = leaf_driver.run_show(["show vlan"])
        vlans = result[0]["vlans"]
        assert "100" in vlans
        assert vlans["100"]["name"] == "Servers"

    def test_show_ip_route(self, spine_driver):
        result = spine_driver.run_show(["show ip route"])
        assert "vrfs" in result[0]

    def test_show_mac_address_table(self, leaf_driver):
        result = leaf_driver.run_show(["show mac address-table"])
        assert "unicastTable" in result[0]

    def test_show_lldp_neighbors(self, spine_driver):
        result = spine_driver.run_show(["show lldp neighbors"])
        neighbors = result[0]["lldpNeighbors"]
        assert len(neighbors) == 4  # spine-01 connects to 4 leaves

    def test_show_ntp_status(self, leaf_driver):
        result = leaf_driver.run_show(["show ntp status"])
        assert result[0]["status"] == "synchronised"

    def test_show_running_config(self, spine_driver):
        result = spine_driver.run_show(["show running-config"])
        assert "output" in result[0]
        assert "spine-01" in result[0]["output"]

    def test_show_running_config_diffs(self, spine_driver):
        result = spine_driver.run_show(["show running-config diffs"])
        assert "output" in result[0]

    def test_show_temperature(self, leaf_driver):
        result = leaf_driver.run_show(["show system environment temperature"])
        assert result[0]["systemStatus"] == "temperatureOk"

    def test_show_processes_top(self, spine_driver):
        result = spine_driver.run_show(["show processes top once"])
        assert "cpuInfo" in result[0]

    def test_show_inventory(self, leaf_driver):
        result = leaf_driver.run_show(["show inventory"])
        assert "systemInformation" in result[0]

    def test_show_memory(self, spine_driver):
        result = spine_driver.run_show(["show memory"])
        assert "memTotal" in result[0]
        assert "memFree" in result[0]

    def test_show_logging(self, leaf_driver):
        result = leaf_driver.run_show(["show logging"])
        assert "output" in result[0]

    def test_show_bgp_neighbors_all_devices(self, spine_driver):
        result = spine_driver.run_show(["show ip bgp neighbors"])
        assert "vrfs" in result[0]
        peer_list = result[0]["vrfs"]["default"]["peerList"]
        assert len(peer_list) == 4  # spine peers with 4 leaves

    def test_multiple_commands(self, spine_driver):
        result = spine_driver.run_show(["show version", "show hostname"])
        assert len(result) == 2
        assert result[0]["hostname"] == "spine-01"
        assert result[1]["hostname"] == "spine-01"


# ─── Unmocked commands return graceful error ──────────────────────────────


class TestMockDriverUnmockedCommands:
    """Unmocked commands should return a helpful error, not crash."""

    def test_unmocked_command_returns_error(self):
        driver = MockDriver()
        driver.connect(host="spine-01")
        result = driver.run_show(["show some unknown command"])
        assert len(result) == 1
        assert "errors" in result[0]
        assert "demo mode" in result[0]["errors"][0].lower()

    def test_unmocked_command_mixed(self):
        """Mix of mocked and unmocked commands in a single call."""
        driver = MockDriver()
        driver.connect(host="spine-01")
        result = driver.run_show(["show version", "show foobar"])
        assert len(result) == 2
        assert result[0]["hostname"] == "spine-01"  # mocked
        assert "errors" in result[1]  # unmocked

    def test_unmocked_device_returns_error(self):
        """Unknown device should return demo error for all commands."""
        driver = MockDriver()
        driver.connect(host="unknown-device-99")
        result = driver.run_show(["show version"])
        assert "errors" in result[0]


# ─── Config commands are no-ops ──────────────────────────────────────────


class TestMockDriverConfig:
    """Config commands should succeed as no-ops in demo mode."""

    def test_config_commands_succeed(self):
        driver = MockDriver()
        driver.connect(host="leaf-01")
        result = driver.run_config(["vlan 100", "name SERVERS"])
        assert len(result) == 2

    def test_config_commands_via_alias(self):
        driver = MockDriver()
        driver.connect(host="leaf-01")
        result = driver.config(["interface Ethernet1", "description test"])
        assert len(result) == 2


# ─── Deep copy prevents mutation ─────────────────────────────────────────


class TestMockDriverImmutability:
    """Mock responses should be independent copies, not shared references."""

    def test_responses_are_independent(self):
        driver = MockDriver()
        driver.connect(host="spine-01")
        r1 = driver.run_show(["show version"])
        r2 = driver.run_show(["show version"])
        r1[0]["hostname"] = "MUTATED"
        assert r2[0]["hostname"] == "spine-01"


# ─── Demo inventory ─────────────────────────────────────────────────────


class TestDemoInventory:
    """Test the DemoInventoryBackend."""

    def test_has_six_devices(self):
        backend = DemoInventoryBackend()
        devices = backend.get_devices()
        assert len(devices) == 6

    def test_device_names(self):
        backend = DemoInventoryBackend()
        devices = backend.get_devices()
        expected = {"spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04"}
        assert set(devices.keys()) == expected

    def test_spine_roles(self):
        backend = DemoInventoryBackend()
        devices = backend.get_devices()
        assert devices["spine-01"].role == "spine"
        assert devices["spine-02"].role == "spine"

    def test_leaf_roles(self):
        backend = DemoInventoryBackend()
        devices = backend.get_devices()
        for name in ["leaf-01", "leaf-02", "leaf-03", "leaf-04"]:
            assert devices[name].role == "leaf"

    def test_mlag_peers(self):
        backend = DemoInventoryBackend()
        devices = backend.get_devices()
        assert devices["leaf-01"].mlag_peer == "leaf-02"
        assert devices["leaf-02"].mlag_peer == "leaf-01"
        assert devices["leaf-03"].mlag_peer == "leaf-04"
        assert devices["leaf-04"].mlag_peer == "leaf-03"

    def test_platform_is_eos_mock(self):
        backend = DemoInventoryBackend()
        devices = backend.get_devices()
        for creds in devices.values():
            assert creds.platform == "eos-mock"

    def test_get_device_found(self):
        backend = DemoInventoryBackend()
        device = backend.get_device("spine-01")
        assert device is not None
        assert device.role == "spine"

    def test_get_device_not_found(self):
        backend = DemoInventoryBackend()
        assert backend.get_device("nonexistent") is None

    def test_groups(self):
        backend = DemoInventoryBackend()
        devices = backend.get_devices()
        assert "spines" in devices["spine-01"].groups
        assert "leaves" in devices["leaf-01"].groups
        assert "dc1" in devices["spine-01"].groups
        assert "dc1" in devices["leaf-01"].groups


# ─── BGP data consistency across topology ────────────────────────────────


class TestBGPConsistency:
    """Verify BGP mock data is consistent across spine-leaf topology."""

    def test_spine_peers_with_all_leaves(self):
        for spine in ["spine-01", "spine-02"]:
            peers = BGP_SUMMARY[spine]["vrfs"]["default"]["peers"]
            peer_ips = set(peers.keys())
            # spine should peer with all 4 leaves
            leaf_ips = {"10.0.1.1", "10.0.1.2", "10.0.2.1", "10.0.2.2"}
            assert peer_ips == leaf_ips, f"{spine} missing leaf peers"

    def test_leaf_peers_with_both_spines(self):
        for leaf in ["leaf-01", "leaf-02", "leaf-03", "leaf-04"]:
            peers = BGP_SUMMARY[leaf]["vrfs"]["default"]["peers"]
            peer_ips = set(peers.keys())
            spine_ips = {"10.0.0.1", "10.0.0.2"}
            assert peer_ips == spine_ips, f"{leaf} missing spine peers"

    def test_all_peers_established(self):
        for dev, bgp in BGP_SUMMARY.items():
            for peer_ip, peer_data in bgp["vrfs"]["default"]["peers"].items():
                assert peer_data["peerState"] == "Established", f"{dev} peer {peer_ip} is {peer_data['peerState']}"

    def test_spine_asn_consistent(self):
        for spine in ["spine-01", "spine-02"]:
            assert BGP_SUMMARY[spine]["vrfs"]["default"]["asn"] == "65000"

    def test_leaf_asn_pairs(self):
        assert BGP_SUMMARY["leaf-01"]["vrfs"]["default"]["asn"] == "65001"
        assert BGP_SUMMARY["leaf-02"]["vrfs"]["default"]["asn"] == "65001"
        assert BGP_SUMMARY["leaf-03"]["vrfs"]["default"]["asn"] == "65002"
        assert BGP_SUMMARY["leaf-04"]["vrfs"]["default"]["asn"] == "65002"


# ─── LLDP consistency ────────────────────────────────────────────────────


class TestLLDPConsistency:
    """Verify LLDP neighbors are consistent (bidirectional)."""

    def test_spine_to_leaf_lldp(self):
        # spine-01 Ethernet1 → leaf-01
        spine_neighbors = LLDP_NEIGHBORS["spine-01"]["lldpNeighbors"]
        eth1_neighbor = next(n for n in spine_neighbors if n["port"] == "Ethernet1")
        assert eth1_neighbor["neighborDevice"] == "leaf-01"

    def test_leaf_to_spine_lldp(self):
        # leaf-01 Ethernet1 → spine-01
        leaf_neighbors = LLDP_NEIGHBORS["leaf-01"]["lldpNeighbors"]
        eth1_neighbor = next(n for n in leaf_neighbors if n["port"] == "Ethernet1")
        assert eth1_neighbor["neighborDevice"] == "spine-01"

    def test_mlag_peer_link_lldp(self):
        # leaf-01 Ethernet3 → leaf-02
        leaf_neighbors = LLDP_NEIGHBORS["leaf-01"]["lldpNeighbors"]
        eth3_neighbor = next(n for n in leaf_neighbors if n["port"] == "Ethernet3")
        assert eth3_neighbor["neighborDevice"] == "leaf-02"


# ─── MLAG consistency ───────────────────────────────────────────────────


class TestMLAGConsistency:
    """Verify MLAG data consistency across pairs."""

    def test_mlag_pair_01_same_domain(self):
        assert MLAG_STATUS["leaf-01"]["domainId"] == "MLAG-PAIR-01"
        assert MLAG_STATUS["leaf-02"]["domainId"] == "MLAG-PAIR-01"

    def test_mlag_pair_02_same_domain(self):
        assert MLAG_STATUS["leaf-03"]["domainId"] == "MLAG-PAIR-02"
        assert MLAG_STATUS["leaf-04"]["domainId"] == "MLAG-PAIR-02"

    def test_mlag_pair_same_system_id(self):
        assert MLAG_STATUS["leaf-01"]["systemId"] == MLAG_STATUS["leaf-02"]["systemId"]
        assert MLAG_STATUS["leaf-03"]["systemId"] == MLAG_STATUS["leaf-04"]["systemId"]

    def test_mlag_active_state(self):
        for leaf in ["leaf-01", "leaf-02", "leaf-03", "leaf-04"]:
            assert MLAG_STATUS[leaf]["state"] == "active"
            assert MLAG_STATUS[leaf]["negStatus"] == "connected"

    def test_spines_no_mlag(self):
        # Spines should have empty MLAG data
        assert MLAG_STATUS["spine-01"] == {}
        assert MLAG_STATUS["spine-02"] == {}


# ─── get_response dispatch ──────────────────────────────────────────────


class TestGetResponse:
    """Test the get_response dispatch function."""

    def test_exact_match(self):
        result = get_response("spine-01", "show version")
        assert result is not None
        assert result["hostname"] == "spine-01"

    def test_partial_match_bgp(self):
        result = get_response("spine-01", "show ip bgp summary vrf default")
        assert result is not None
        assert "vrfs" in result

    def test_partial_match_interfaces(self):
        result = get_response("leaf-01", "show interfaces Ethernet1 status")
        assert result is not None

    def test_unknown_command(self):
        result = get_response("spine-01", "show something-nonexistent")
        assert result is None

    def test_unknown_device(self):
        result = get_response("unknown-device", "show version")
        assert result is None

    def test_all_devices_have_version(self):
        for dev in DEVICE_INFO:
            result = get_response(dev, "show version")
            assert result is not None
            assert result["hostname"] == dev


# ─── Integration: ConnectionManager with demo mode ──────────────────────


class TestConnectionManagerDemoMode:
    """Test that ConnectionManager uses MockDriver in demo mode."""

    def test_demo_mode_creates_mock_driver(self):
        from network_mcp.config import NetworkSettings
        from network_mcp.connection import ConnectionManager
        from network_mcp.mock_data.inventory import DemoInventoryBackend

        settings = NetworkSettings(net_demo_mode=True)
        backend = DemoInventoryBackend()
        mgr = ConnectionManager(settings=settings, inventory_backend=backend)

        driver = mgr.get_driver("spine-01")
        assert isinstance(driver, MockDriver)
        assert driver.is_connected()

    def test_demo_mode_returns_data(self):
        from network_mcp.config import NetworkSettings
        from network_mcp.connection import ConnectionManager
        from network_mcp.mock_data.inventory import DemoInventoryBackend

        settings = NetworkSettings(net_demo_mode=True)
        backend = DemoInventoryBackend()
        mgr = ConnectionManager(settings=settings, inventory_backend=backend)

        driver = mgr.get_driver("spine-01")
        result = driver.run_show(["show version"])
        assert result[0]["hostname"] == "spine-01"

    def test_demo_mode_list_devices(self):
        from network_mcp.config import NetworkSettings
        from network_mcp.connection import ConnectionManager
        from network_mcp.mock_data.inventory import DemoInventoryBackend

        settings = NetworkSettings(net_demo_mode=True)
        backend = DemoInventoryBackend()
        mgr = ConnectionManager(settings=settings, inventory_backend=backend)

        devices = mgr.list_devices()
        assert len(devices) == 6
        assert "spine-01" in devices
        assert "leaf-01" in devices

    def test_demo_mode_get_devices_by_role(self):
        from network_mcp.config import NetworkSettings
        from network_mcp.connection import ConnectionManager
        from network_mcp.mock_data.inventory import DemoInventoryBackend

        settings = NetworkSettings(net_demo_mode=True)
        backend = DemoInventoryBackend()
        mgr = ConnectionManager(settings=settings, inventory_backend=backend)

        spines = mgr.get_devices_by_role("spine")
        assert set(spines) == {"spine-01", "spine-02"}

        leaves = mgr.get_devices_by_role("leaf")
        assert set(leaves) == {"leaf-01", "leaf-02", "leaf-03", "leaf-04"}


# ─── All 6 devices × top 20 commands produce valid output ───────────────

# Top 20 commands that must be fully mocked
TOP_20_COMMANDS = [
    "show version",
    "show hostname",
    "show ip bgp summary",
    "show ip bgp neighbors",
    "show interfaces status",
    "show interfaces counters",
    "show interfaces counters errors",
    "show mlag",
    "show vlan",
    "show ip route",
    "show mac address-table",
    "show lldp neighbors",
    "show ntp status",
    "show running-config",
    "show running-config diffs",
    "show system environment temperature",
    "show processes top once",
    "show inventory",
    "show memory",
    "show logging",
]

# Additional commands backfilled for expanded demo mode coverage (40+ total)
EXPANDED_COMMANDS = TOP_20_COMMANDS + [
    "show spanning-tree",
    "show port-channel summary",
    "show lldp neighbors detail",
    "show route-map",
    "show ip prefix-list",
    "show ip access-lists",
    "show reload cause",
    "show configuration sessions detail",
    "show configuration checkpoints",
    "show ip ospf neighbor",
    "show ip arp",
    "show environment power",
    "show environment cooling",
    "show vrf",
    "show hardware capacity",
    "show hardware counter drop",
    "show ip interface",
    "show interfaces transceiver",
    "show extensions",
    "show users",
    "show aaa",
    "show snmp",
    "show policy-map interface control-plane",
    "show management api http-commands",
    "show vrrp",
    "show ip virtual-router",
    "show interfaces trunk",
    "show mlag config-sanity",
    "show mlag interfaces",
    "show ip route summary",
    "show startup-config",
]

ALL_DEVICES = ["spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04"]


class TestTop20Coverage:
    """Verify all 6 devices return valid data for the top commands."""

    @pytest.mark.parametrize("device", ALL_DEVICES)
    @pytest.mark.parametrize("command", TOP_20_COMMANDS)
    def test_command_returns_data(self, device, command):
        response = get_response(device, command)
        assert response is not None, f"{device}: '{command}' returned None"
        assert isinstance(response, dict), f"{device}: '{command}' is not a dict"
        # Should not be the demo error
        assert "errors" not in response, f"{device}: '{command}' returned error: {response}"


class TestExpandedCoverage:
    """Verify all 6 devices return valid data for all 50+ mocked commands."""

    @pytest.mark.parametrize("device", ALL_DEVICES)
    @pytest.mark.parametrize("command", EXPANDED_COMMANDS)
    def test_expanded_command_returns_data(self, device, command):
        response = get_response(device, command)
        assert response is not None, f"{device}: '{command}' returned None"
        assert isinstance(response, dict), f"{device}: '{command}' is not a dict"

    def test_expanded_coverage_count(self):
        """Verify we have at least 40 unique commands mocked."""
        assert len(EXPANDED_COMMANDS) >= 40, f"Only {len(EXPANDED_COMMANDS)} commands mocked, need 40+"


# ─── Demo mode end-to-end via run_show_command ────────────────────────


class TestDemoModeEndToEnd:
    """Verify demo mode works end-to-end through run_show_command helper."""

    def test_demo_mode_setting_recognized(self):
        """NET_DEMO_MODE=true is recognized by NetworkSettings."""
        from network_mcp.config import NetworkSettings

        settings = NetworkSettings(net_demo_mode=True)
        assert settings.net_demo_mode is True

    def test_demo_show_version_through_helper(self):
        """Demo mode returns realistic mock data through run_show_command."""
        from network_mcp.config import NetworkSettings
        from network_mcp.connection import ConnectionManager
        from network_mcp.helpers import run_show_command
        from network_mcp.mock_data.inventory import DemoInventoryBackend

        settings = NetworkSettings(net_demo_mode=True)
        backend = DemoInventoryBackend()
        cm = ConnectionManager(settings=settings, inventory_backend=backend)

        result = run_show_command(cm, "spine-01", ["show version"])

        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        data = result["data"][0]
        assert data["hostname"] == "spine-01"
        assert data["modelName"] == "DCS-7280SR3-48YC8"
        assert data["version"] == "4.32.1F"

        cm.close_all()

    def test_demo_unmocked_command_through_helper(self):
        """Demo mode returns data for unmocked commands (with error key) through helper."""
        from network_mcp.config import NetworkSettings
        from network_mcp.connection import ConnectionManager
        from network_mcp.helpers import run_show_command
        from network_mcp.mock_data.inventory import DemoInventoryBackend

        settings = NetworkSettings(net_demo_mode=True)
        backend = DemoInventoryBackend()
        cm = ConnectionManager(settings=settings, inventory_backend=backend)

        result = run_show_command(cm, "spine-01", ["show some-unmocked-command"])

        assert result["status"] == "success"
        data = result["data"][0]
        assert "errors" in data

        cm.close_all()
