"""Tests for Ansible integration — playbook generation and inventory export."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml

from network_mcp.connection import DeviceCredentials
from network_mcp.integrations.ansible import (
    _PLATFORM_COLLECTION,
    _PLATFORM_NETWORK_OS,
    _TASK_GENERATORS,
    net_generate_playbook,
    net_inventory_to_ansible,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_inventory():
    """Mock inventory with multiple devices and groups."""
    return {
        "spine-01": DeviceCredentials(
            host="10.0.0.1",
            platform="eos",
            groups=["spines", "dc1"],
            role="spine",
        ),
        "spine-02": DeviceCredentials(
            host="10.0.0.2",
            platform="eos",
            groups=["spines", "dc1"],
            role="spine",
        ),
        "leaf-01": DeviceCredentials(
            host="10.0.1.1",
            platform="nxos",
            groups=["leafs", "dc1"],
            role="leaf",
        ),
        "router-01": DeviceCredentials(
            host="10.0.2.1",
            platform="iosxe",
            groups=["routers"],
            role="router",
        ),
        "fw-01": DeviceCredentials(
            host="10.0.3.1",
            platform="junos",
            groups=["firewalls"],
            role="firewall",
        ),
    }


@pytest.fixture
def mock_empty_inventory():
    """Mock empty inventory."""
    return {}


# ---------------------------------------------------------------------------
# Playbook generation tests
# ---------------------------------------------------------------------------


class TestNetGeneratePlaybook:
    """Tests for the net_generate_playbook MCP tool."""

    @pytest.mark.parametrize("task", ["vlan_provision", "bgp_neighbor", "interface_config", "backup_config"])
    def test_each_task_generates_valid_yaml(self, task):
        result = net_generate_playbook(task=task)
        assert result["status"] == "success"
        assert result["task"] == task
        parsed = yaml.safe_load(result["playbook"])
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert "name" in parsed[0]
        assert "hosts" in parsed[0]
        assert parsed[0]["gather_facts"] is False
        assert "tasks" in parsed[0]
        assert len(parsed[0]["tasks"]) >= 1

    @pytest.mark.parametrize(
        "platform,expected_collection",
        [
            ("eos", "arista.eos"),
            ("iosxe", "cisco.ios"),
            ("nxos", "cisco.nxos"),
            ("junos", "junipernetworks.junos"),
        ],
    )
    def test_platform_mapping(self, platform, expected_collection):
        result = net_generate_playbook(task="vlan_provision", platform=platform)
        assert result["status"] == "success"
        assert result["platform"] == platform
        parsed = yaml.safe_load(result["playbook"])
        assert expected_collection in parsed[0]["collections"]

    def test_unknown_task_returns_error(self):
        result = net_generate_playbook(task="nonexistent_task")
        assert result["status"] == "error"
        assert "Unknown task" in result["error"]
        assert "nonexistent_task" in result["error"]
        # Should list supported tasks
        for task_name in _TASK_GENERATORS:
            assert task_name in result["error"]

    def test_hosts_parameter_respected(self):
        result = net_generate_playbook(task="vlan_provision", hosts="spine-switches")
        assert result["status"] == "success"
        assert result["hosts"] == "spine-switches"
        parsed = yaml.safe_load(result["playbook"])
        assert parsed[0]["hosts"] == "spine-switches"

    def test_default_hosts_is_all(self):
        result = net_generate_playbook(task="vlan_provision")
        assert result["status"] == "success"
        assert result["hosts"] == "all"
        parsed = yaml.safe_load(result["playbook"])
        assert parsed[0]["hosts"] == "all"

    def test_unknown_platform_uses_as_is(self):
        result = net_generate_playbook(task="vlan_provision", platform="custom_os")
        assert result["status"] == "success"
        parsed = yaml.safe_load(result["playbook"])
        # Unknown platform falls through to using the platform string directly
        assert "custom_os" in parsed[0]["collections"]

    def test_vlan_playbook_has_jinja_vars(self):
        result = net_generate_playbook(task="vlan_provision", platform="eos")
        assert result["status"] == "success"
        assert "{{ vlan_id }}" in result["playbook"]
        assert "{{ vlan_name }}" in result["playbook"]

    def test_bgp_playbook_has_jinja_vars(self):
        result = net_generate_playbook(task="bgp_neighbor", platform="eos")
        assert result["status"] == "success"
        assert "{{ bgp_asn }}" in result["playbook"]
        assert "{{ neighbor_ip }}" in result["playbook"]
        assert "{{ remote_asn }}" in result["playbook"]

    def test_interface_playbook_has_jinja_vars(self):
        result = net_generate_playbook(task="interface_config", platform="eos")
        assert result["status"] == "success"
        assert "{{ interface_name }}" in result["playbook"]

    def test_backup_playbook_has_register(self):
        result = net_generate_playbook(task="backup_config", platform="eos")
        assert result["status"] == "success"
        parsed = yaml.safe_load(result["playbook"])
        # First task should register config_output
        tasks = parsed[0]["tasks"]
        assert tasks[0].get("register") == "config_output"
        # Second task should save to file
        assert "ansible.builtin.copy" in tasks[1]

    def test_playbook_yaml_is_parseable(self):
        for task in _TASK_GENERATORS:
            result = net_generate_playbook(task=task)
            parsed = yaml.safe_load(result["playbook"])
            assert parsed is not None
            assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# Inventory export tests
# ---------------------------------------------------------------------------


class TestNetInventoryToAnsible:
    """Tests for the net_inventory_to_ansible MCP tool."""

    def test_basic_export(self, mock_inventory):
        with patch("network_mcp.integrations.ansible.conn_mgr") as mock_cm:
            mock_cm.get_inventory.return_value = mock_inventory
            result = net_inventory_to_ansible()

        assert result["status"] == "success"
        assert result["device_count"] == 5
        parsed = yaml.safe_load(result["inventory"])
        assert "all" in parsed
        assert "hosts" in parsed["all"]

    def test_ansible_host_set_correctly(self, mock_inventory):
        with patch("network_mcp.integrations.ansible.conn_mgr") as mock_cm:
            mock_cm.get_inventory.return_value = mock_inventory
            result = net_inventory_to_ansible()

        parsed = yaml.safe_load(result["inventory"])
        hosts = parsed["all"]["hosts"]
        assert hosts["spine-01"]["ansible_host"] == "10.0.0.1"
        assert hosts["leaf-01"]["ansible_host"] == "10.0.1.1"
        assert hosts["router-01"]["ansible_host"] == "10.0.2.1"
        assert hosts["fw-01"]["ansible_host"] == "10.0.3.1"

    def test_ansible_network_os_set_correctly(self, mock_inventory):
        with patch("network_mcp.integrations.ansible.conn_mgr") as mock_cm:
            mock_cm.get_inventory.return_value = mock_inventory
            result = net_inventory_to_ansible()

        parsed = yaml.safe_load(result["inventory"])
        hosts = parsed["all"]["hosts"]
        assert hosts["spine-01"]["ansible_network_os"] == "arista.eos.eos"
        assert hosts["leaf-01"]["ansible_network_os"] == "cisco.nxos.nxos"
        assert hosts["router-01"]["ansible_network_os"] == "cisco.ios.ios"
        assert hosts["fw-01"]["ansible_network_os"] == "junipernetworks.junos.junos"

    def test_groups_structured_correctly(self, mock_inventory):
        with patch("network_mcp.integrations.ansible.conn_mgr") as mock_cm:
            mock_cm.get_inventory.return_value = mock_inventory
            result = net_inventory_to_ansible()

        parsed = yaml.safe_load(result["inventory"])
        children = parsed["all"]["children"]
        assert "spines" in children
        assert "dc1" in children
        assert "leafs" in children
        assert "routers" in children
        assert "firewalls" in children

        # Check group memberships
        assert "spine-01" in children["spines"]["hosts"]
        assert "spine-02" in children["spines"]["hosts"]
        assert "leaf-01" in children["leafs"]["hosts"]
        assert "router-01" in children["routers"]["hosts"]
        assert "fw-01" in children["firewalls"]["hosts"]

        # dc1 should contain spines and leaf
        dc1_members = list(children["dc1"]["hosts"].keys())
        assert "spine-01" in dc1_members
        assert "spine-02" in dc1_members
        assert "leaf-01" in dc1_members

    def test_group_count(self, mock_inventory):
        with patch("network_mcp.integrations.ansible.conn_mgr") as mock_cm:
            mock_cm.get_inventory.return_value = mock_inventory
            result = net_inventory_to_ansible()

        # spines, dc1, leafs, routers, firewalls = 5 groups
        assert result["group_count"] == 5

    def test_empty_inventory_returns_error(self, mock_empty_inventory):
        with patch("network_mcp.integrations.ansible.conn_mgr") as mock_cm:
            mock_cm.get_inventory.return_value = mock_empty_inventory
            result = net_inventory_to_ansible()

        assert result["status"] == "error"
        assert "No devices" in result["error"]

    def test_inventory_exception_returns_error(self):
        with patch("network_mcp.integrations.ansible.conn_mgr") as mock_cm:
            mock_cm.get_inventory.side_effect = RuntimeError("Inventory backend not initialized")
            result = net_inventory_to_ansible()

        assert result["status"] == "error"
        assert "Failed to read inventory" in result["error"]

    def test_inventory_yaml_is_parseable(self, mock_inventory):
        with patch("network_mcp.integrations.ansible.conn_mgr") as mock_cm:
            mock_cm.get_inventory.return_value = mock_inventory
            result = net_inventory_to_ansible()

        parsed = yaml.safe_load(result["inventory"])
        assert parsed is not None
        assert isinstance(parsed, dict)

    def test_no_groups_omits_children(self):
        """Devices with no groups should not produce children section."""
        inventory = {
            "device-01": DeviceCredentials(
                host="10.0.0.1",
                platform="eos",
                groups=[],
            ),
        }
        with patch("network_mcp.integrations.ansible.conn_mgr") as mock_cm:
            mock_cm.get_inventory.return_value = inventory
            result = net_inventory_to_ansible()

        assert result["status"] == "success"
        assert result["group_count"] == 0
        parsed = yaml.safe_load(result["inventory"])
        assert "children" not in parsed["all"]

    def test_unknown_platform_uses_string(self):
        """Unknown platform should use the platform string as ansible_network_os."""
        inventory = {
            "custom-device": DeviceCredentials(
                host="10.0.0.99",
                platform="eos",  # Use valid platform for DeviceCredentials
                groups=[],
            ),
        }
        # Manually override platform after creation to test fallback
        inventory["custom-device"].platform = "custom_os"
        with patch("network_mcp.integrations.ansible.conn_mgr") as mock_cm:
            mock_cm.get_inventory.return_value = inventory
            result = net_inventory_to_ansible()

        parsed = yaml.safe_load(result["inventory"])
        assert parsed["all"]["hosts"]["custom-device"]["ansible_network_os"] == "custom_os"


# ---------------------------------------------------------------------------
# Mapping constant tests
# ---------------------------------------------------------------------------


class TestMappingConstants:
    """Tests for platform mapping constants."""

    def test_platform_collection_has_all_platforms(self):
        assert "eos" in _PLATFORM_COLLECTION
        assert "iosxe" in _PLATFORM_COLLECTION
        assert "nxos" in _PLATFORM_COLLECTION
        assert "junos" in _PLATFORM_COLLECTION

    def test_platform_network_os_has_all_platforms(self):
        assert "eos" in _PLATFORM_NETWORK_OS
        assert "iosxe" in _PLATFORM_NETWORK_OS
        assert "nxos" in _PLATFORM_NETWORK_OS
        assert "junos" in _PLATFORM_NETWORK_OS

    def test_all_task_generators_registered(self):
        expected = {"vlan_provision", "bgp_neighbor", "interface_config", "backup_config"}
        assert set(_TASK_GENERATORS.keys()) == expected
