"""Tests for the vendor-agnostic config template engine."""

from __future__ import annotations

import pytest

from network_mcp.templates import (
    ConfigTemplate,
    TemplateError,
    _cidr_to_mask,
    get_intent,
    list_intents,
    render_commands,
)

# ===================================================================
# ConfigTemplate unit tests
# ===================================================================


class TestConfigTemplate:
    def test_register_and_render(self):
        t = ConfigTemplate("test_intent", "A test", ["name"])
        t.register("eos", lambda p: [f"hostname {p['name']}"])
        result = t.render({"name": "sw1"}, "eos")
        assert result == ["hostname sw1"]

    def test_register_returns_self(self):
        t = ConfigTemplate("test_intent", "A test", ["name"])
        ret = t.register("eos", lambda p: [])
        assert ret is t

    def test_register_unknown_platform_raises(self):
        t = ConfigTemplate("test_intent", "A test", [])
        with pytest.raises(TemplateError, match="Unknown platform"):
            t.register("unknown_os", lambda p: [])

    def test_render_unsupported_platform_raises(self):
        t = ConfigTemplate("test_intent", "A test", [])
        t.register("eos", lambda p: [])
        with pytest.raises(TemplateError, match="Unsupported platform"):
            t.render({}, "bad_platform")

    def test_render_no_template_for_platform_raises(self):
        t = ConfigTemplate("test_intent", "A test", [])
        t.register("eos", lambda p: [])
        with pytest.raises(TemplateError, match="has no template for platform"):
            t.render({}, "junos")

    def test_render_missing_params_raises(self):
        t = ConfigTemplate("test_intent", "A test", ["vlan_id", "name"])
        t.register("eos", lambda p: [])
        with pytest.raises(TemplateError, match="Missing required parameters"):
            t.render({"vlan_id": 100}, "eos")

    def test_optional_params_not_required(self):
        t = ConfigTemplate("test_intent", "A test", ["name"], optional_params=["description"])
        t.register("eos", lambda p: [f"name {p['name']}"])
        result = t.render({"name": "sw1"}, "eos")
        assert result == ["name sw1"]


# ===================================================================
# Registry API tests
# ===================================================================


class TestRegistryAPI:
    def test_render_commands_unknown_intent(self):
        with pytest.raises(TemplateError, match="Unknown intent"):
            render_commands("nonexistent_intent_xyz", {}, "eos")

    def test_list_intents_returns_list(self):
        intents = list_intents()
        assert isinstance(intents, list)
        assert len(intents) > 0
        for item in intents:
            assert "intent" in item
            assert "description" in item
            assert "required_params" in item
            assert "platforms" in item

    def test_get_intent_exists(self):
        t = get_intent("create_vlan")
        assert t is not None
        assert t.intent == "create_vlan"

    def test_get_intent_missing(self):
        assert get_intent("nonexistent_xyz") is None

    def test_all_intents_have_all_4_platforms(self):
        for item in list_intents():
            expected = {"eos", "iosxe", "nxos", "junos"}
            actual = set(item["platforms"])
            assert actual == expected, f"Intent '{item['intent']}' missing platforms: {expected - actual}"


# ===================================================================
# create_vlan tests (all 4 platforms)
# ===================================================================


class TestCreateVlan:
    PARAMS = {"vlan_id": 100, "name": "SERVERS"}

    def test_eos(self):
        cmds = render_commands("create_vlan", self.PARAMS, "eos")
        assert cmds == ["vlan 100", "name SERVERS"]

    def test_iosxe(self):
        cmds = render_commands("create_vlan", self.PARAMS, "iosxe")
        assert cmds == ["vlan 100", "name SERVERS"]

    def test_nxos(self):
        cmds = render_commands("create_vlan", self.PARAMS, "nxos")
        assert cmds == ["vlan 100", "name SERVERS"]

    def test_junos(self):
        cmds = render_commands("create_vlan", self.PARAMS, "junos")
        assert cmds == ["set vlans SERVERS vlan-id 100"]


# ===================================================================
# delete_vlan tests
# ===================================================================


class TestDeleteVlan:
    PARAMS = {"vlan_id": 200}

    def test_eos(self):
        assert render_commands("delete_vlan", self.PARAMS, "eos") == ["no vlan 200"]

    def test_iosxe(self):
        assert render_commands("delete_vlan", self.PARAMS, "iosxe") == ["no vlan 200"]

    def test_nxos(self):
        assert render_commands("delete_vlan", self.PARAMS, "nxos") == ["no vlan 200"]

    def test_junos(self):
        assert render_commands("delete_vlan", self.PARAMS, "junos") == ["delete vlans vlan-id 200"]


# ===================================================================
# rename_vlan tests
# ===================================================================


class TestRenameVlan:
    PARAMS = {"vlan_id": 100, "name": "WEB"}

    def test_eos(self):
        assert render_commands("rename_vlan", self.PARAMS, "eos") == ["vlan 100", "name WEB"]

    def test_junos(self):
        assert render_commands("rename_vlan", self.PARAMS, "junos") == ["set vlans WEB vlan-id 100"]


# ===================================================================
# assign_interface_vlan tests
# ===================================================================


class TestAssignInterfaceVlan:
    PARAMS = {"interface": "Ethernet1", "vlan_id": 100}

    def test_eos(self):
        cmds = render_commands("assign_interface_vlan", self.PARAMS, "eos")
        assert cmds == ["interface Ethernet1", "switchport mode access", "switchport access vlan 100"]

    def test_iosxe(self):
        cmds = render_commands("assign_interface_vlan", {"interface": "GigabitEthernet0/1", "vlan_id": 50}, "iosxe")
        assert cmds == ["interface GigabitEthernet0/1", "switchport mode access", "switchport access vlan 50"]

    def test_nxos(self):
        cmds = render_commands("assign_interface_vlan", self.PARAMS, "nxos")
        assert cmds == ["interface Ethernet1", "switchport mode access", "switchport access vlan 100"]

    def test_junos(self):
        cmds = render_commands("assign_interface_vlan", {"interface": "ge-0/0/0", "vlan_id": 100}, "junos")
        assert cmds == ["set interfaces ge-0/0/0 unit 0 family ethernet-switching vlan members vlan100"]


# ===================================================================
# set_interface_description tests
# ===================================================================


class TestSetInterfaceDescription:
    PARAMS = {"interface": "Ethernet1", "description": "Uplink to spine"}

    def test_eos(self):
        cmds = render_commands("set_interface_description", self.PARAMS, "eos")
        assert cmds == ["interface Ethernet1", "description Uplink to spine"]

    def test_iosxe(self):
        cmds = render_commands("set_interface_description", self.PARAMS, "iosxe")
        assert cmds == ["interface Ethernet1", "description Uplink to spine"]

    def test_nxos(self):
        cmds = render_commands("set_interface_description", self.PARAMS, "nxos")
        assert cmds == ["interface Ethernet1", "description Uplink to spine"]

    def test_junos(self):
        cmds = render_commands("set_interface_description", self.PARAMS, "junos")
        assert cmds == ['set interfaces Ethernet1 description "Uplink to spine"']


# ===================================================================
# shutdown_interface / enable_interface tests
# ===================================================================


class TestShutdownInterface:
    PARAMS = {"interface": "Ethernet5"}

    def test_eos(self):
        assert render_commands("shutdown_interface", self.PARAMS, "eos") == ["interface Ethernet5", "shutdown"]

    def test_iosxe(self):
        assert render_commands("shutdown_interface", self.PARAMS, "iosxe") == ["interface Ethernet5", "shutdown"]

    def test_nxos(self):
        assert render_commands("shutdown_interface", self.PARAMS, "nxos") == ["interface Ethernet5", "shutdown"]

    def test_junos(self):
        assert render_commands("shutdown_interface", self.PARAMS, "junos") == ["set interfaces Ethernet5 disable"]


class TestEnableInterface:
    PARAMS = {"interface": "Ethernet5"}

    def test_eos(self):
        assert render_commands("enable_interface", self.PARAMS, "eos") == ["interface Ethernet5", "no shutdown"]

    def test_junos(self):
        assert render_commands("enable_interface", self.PARAMS, "junos") == [
            "delete interfaces Ethernet5 disable"
        ]


# ===================================================================
# add_static_route tests
# ===================================================================


class TestAddStaticRoute:
    def test_eos_simple(self):
        cmds = render_commands("add_static_route", {"prefix": "10.0.0.0/24", "next_hop": "10.0.0.1"}, "eos")
        assert cmds == ["ip route 10.0.0.0/24 10.0.0.1"]

    def test_eos_with_vrf(self):
        cmds = render_commands(
            "add_static_route", {"prefix": "10.0.0.0/24", "next_hop": "10.0.0.1", "vrf": "MGMT"}, "eos"
        )
        assert cmds == ["ip route vrf MGMT 10.0.0.0/24 10.0.0.1"]

    def test_eos_with_admin_distance(self):
        cmds = render_commands(
            "add_static_route", {"prefix": "10.0.0.0/24", "next_hop": "10.0.0.1", "admin_distance": 200}, "eos"
        )
        assert cmds == ["ip route 10.0.0.0/24 10.0.0.1 200"]

    def test_iosxe_converts_cidr_to_mask(self):
        cmds = render_commands("add_static_route", {"prefix": "10.0.0.0/24", "next_hop": "10.0.0.1"}, "iosxe")
        assert cmds == ["ip route 10.0.0.0 255.255.255.0 10.0.0.1"]

    def test_iosxe_with_vrf(self):
        cmds = render_commands(
            "add_static_route", {"prefix": "192.168.1.0/24", "next_hop": "192.168.1.1", "vrf": "MGMT"}, "iosxe"
        )
        assert cmds == ["ip route vrf MGMT 192.168.1.0 255.255.255.0 192.168.1.1"]

    def test_iosxe_with_admin_distance(self):
        cmds = render_commands(
            "add_static_route", {"prefix": "10.0.0.0/8", "next_hop": "10.0.0.1", "admin_distance": 150}, "iosxe"
        )
        assert cmds == ["ip route 10.0.0.0 255.0.0.0 10.0.0.1 150"]

    def test_nxos_simple(self):
        cmds = render_commands("add_static_route", {"prefix": "10.0.0.0/24", "next_hop": "10.0.0.1"}, "nxos")
        assert cmds == ["ip route 10.0.0.0/24 10.0.0.1"]

    def test_junos_simple(self):
        cmds = render_commands("add_static_route", {"prefix": "10.0.0.0/24", "next_hop": "10.0.0.1"}, "junos")
        assert cmds == ["set routing-options static route 10.0.0.0/24 next-hop 10.0.0.1"]

    def test_junos_with_vrf(self):
        cmds = render_commands(
            "add_static_route", {"prefix": "10.0.0.0/24", "next_hop": "10.0.0.1", "vrf": "MGMT"}, "junos"
        )
        assert cmds == ["set routing-instances MGMT routing-options static route 10.0.0.0/24 next-hop 10.0.0.1"]

    def test_junos_with_admin_distance(self):
        cmds = render_commands(
            "add_static_route", {"prefix": "10.0.0.0/24", "next_hop": "10.0.0.1", "admin_distance": 50}, "junos"
        )
        assert cmds == ["set routing-options static route 10.0.0.0/24 next-hop 10.0.0.1 preference 50"]


# ===================================================================
# add_bgp_neighbor tests
# ===================================================================


class TestAddBgpNeighbor:
    BASE = {"local_as": 65000, "neighbor": "10.0.0.2", "remote_as": 65001}

    def test_eos_simple(self):
        cmds = render_commands("add_bgp_neighbor", self.BASE, "eos")
        assert cmds == ["router bgp 65000", "neighbor 10.0.0.2 remote-as 65001"]

    def test_eos_with_description(self):
        cmds = render_commands("add_bgp_neighbor", {**self.BASE, "description": "spine1"}, "eos")
        assert cmds == [
            "router bgp 65000",
            "neighbor 10.0.0.2 remote-as 65001",
            "neighbor 10.0.0.2 description spine1",
        ]

    def test_iosxe_same_as_eos(self):
        cmds = render_commands("add_bgp_neighbor", self.BASE, "iosxe")
        assert cmds == ["router bgp 65000", "neighbor 10.0.0.2 remote-as 65001"]

    def test_nxos_nested_format(self):
        cmds = render_commands("add_bgp_neighbor", self.BASE, "nxos")
        assert cmds == ["router bgp 65000", "neighbor 10.0.0.2", "remote-as 65001"]

    def test_nxos_with_description(self):
        cmds = render_commands("add_bgp_neighbor", {**self.BASE, "description": "spine1"}, "nxos")
        assert cmds == ["router bgp 65000", "neighbor 10.0.0.2", "remote-as 65001", "description spine1"]

    def test_junos_set_style(self):
        cmds = render_commands("add_bgp_neighbor", self.BASE, "junos")
        assert cmds == ["set protocols bgp group AS65001 neighbor 10.0.0.2 peer-as 65001"]

    def test_junos_with_description(self):
        cmds = render_commands("add_bgp_neighbor", {**self.BASE, "description": "spine1"}, "junos")
        assert cmds == [
            "set protocols bgp group AS65001 neighbor 10.0.0.2 peer-as 65001",
            'set protocols bgp group AS65001 neighbor 10.0.0.2 description "spine1"',
        ]


# ===================================================================
# set_hostname tests
# ===================================================================


class TestSetHostname:
    PARAMS = {"hostname": "leaf-01"}

    def test_eos(self):
        assert render_commands("set_hostname", self.PARAMS, "eos") == ["hostname leaf-01"]

    def test_iosxe(self):
        assert render_commands("set_hostname", self.PARAMS, "iosxe") == ["hostname leaf-01"]

    def test_nxos(self):
        assert render_commands("set_hostname", self.PARAMS, "nxos") == ["hostname leaf-01"]

    def test_junos(self):
        assert render_commands("set_hostname", self.PARAMS, "junos") == ["set system host-name leaf-01"]


# ===================================================================
# set_ntp_server tests
# ===================================================================


class TestSetNtpServer:
    PARAMS = {"server": "10.0.0.1"}

    def test_eos(self):
        assert render_commands("set_ntp_server", self.PARAMS, "eos") == ["ntp server 10.0.0.1"]

    def test_junos(self):
        assert render_commands("set_ntp_server", self.PARAMS, "junos") == ["set system ntp server 10.0.0.1"]


# ===================================================================
# set_dns_server tests
# ===================================================================


class TestSetDnsServer:
    PARAMS = {"server": "8.8.8.8"}

    def test_eos(self):
        assert render_commands("set_dns_server", self.PARAMS, "eos") == ["ip name-server 8.8.8.8"]

    def test_junos(self):
        assert render_commands("set_dns_server", self.PARAMS, "junos") == ["set system name-server 8.8.8.8"]


# ===================================================================
# set_banner tests
# ===================================================================


class TestSetBanner:
    PARAMS = {"message": "Authorized access only"}

    def test_eos(self):
        cmds = render_commands("set_banner", self.PARAMS, "eos")
        assert len(cmds) == 1
        assert "banner motd" in cmds[0]
        assert "Authorized access only" in cmds[0]

    def test_iosxe(self):
        cmds = render_commands("set_banner", self.PARAMS, "iosxe")
        assert cmds == ["banner motd ^Authorized access only^"]

    def test_nxos(self):
        cmds = render_commands("set_banner", self.PARAMS, "nxos")
        assert cmds == ["banner motd @Authorized access only@"]

    def test_junos(self):
        cmds = render_commands("set_banner", self.PARAMS, "junos")
        assert cmds == ['set system login message "Authorized access only"']


# ===================================================================
# set_interface_mtu tests
# ===================================================================


class TestSetInterfaceMtu:
    PARAMS = {"interface": "Ethernet1", "mtu": 9214}

    def test_eos(self):
        cmds = render_commands("set_interface_mtu", self.PARAMS, "eos")
        assert cmds == ["interface Ethernet1", "mtu 9214"]

    def test_junos(self):
        cmds = render_commands("set_interface_mtu", self.PARAMS, "junos")
        assert cmds == ["set interfaces Ethernet1 mtu 9214"]


# ===================================================================
# set_interface_ip tests
# ===================================================================


class TestSetInterfaceIp:
    PARAMS = {"interface": "Loopback0", "ip_address": "10.0.0.1/32"}

    def test_eos(self):
        cmds = render_commands("set_interface_ip", self.PARAMS, "eos")
        assert cmds == ["interface Loopback0", "ip address 10.0.0.1/32"]

    def test_nxos(self):
        cmds = render_commands("set_interface_ip", self.PARAMS, "nxos")
        assert cmds == ["interface Loopback0", "no switchport", "ip address 10.0.0.1/32"]

    def test_junos(self):
        cmds = render_commands("set_interface_ip", self.PARAMS, "junos")
        assert cmds == ["set interfaces Loopback0 unit 0 family inet address 10.0.0.1/32"]


# ===================================================================
# set_snmp_community tests
# ===================================================================


class TestSetSnmpCommunity:
    PARAMS = {"community": "public", "access": "ro"}

    def test_eos(self):
        cmds = render_commands("set_snmp_community", self.PARAMS, "eos")
        assert cmds == ["snmp-server community public ro"]

    def test_iosxe(self):
        cmds = render_commands("set_snmp_community", self.PARAMS, "iosxe")
        assert cmds == ["snmp-server community public RO"]

    def test_junos(self):
        cmds = render_commands("set_snmp_community", self.PARAMS, "junos")
        assert cmds == ["set snmp community public authorization ro-only"]


# ===================================================================
# set_logging_server tests
# ===================================================================


class TestSetLoggingServer:
    PARAMS = {"server": "10.0.0.5"}

    def test_eos(self):
        assert render_commands("set_logging_server", self.PARAMS, "eos") == ["logging host 10.0.0.5"]

    def test_nxos(self):
        assert render_commands("set_logging_server", self.PARAMS, "nxos") == ["logging server 10.0.0.5"]

    def test_junos(self):
        assert render_commands("set_logging_server", self.PARAMS, "junos") == [
            "set system syslog host 10.0.0.5 any any"
        ]


# ===================================================================
# _cidr_to_mask helper tests
# ===================================================================


class TestCidrToMask:
    def test_24(self):
        assert _cidr_to_mask(24) == "255.255.255.0"

    def test_32(self):
        assert _cidr_to_mask(32) == "255.255.255.255"

    def test_0(self):
        assert _cidr_to_mask(0) == "0.0.0.0"  # noqa: S104

    def test_8(self):
        assert _cidr_to_mask(8) == "255.0.0.0"

    def test_16(self):
        assert _cidr_to_mask(16) == "255.255.0.0"

    def test_30(self):
        assert _cidr_to_mask(30) == "255.255.255.252"
