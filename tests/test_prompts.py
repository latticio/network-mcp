"""Tests for MCP prompt templates."""

from network_mcp.prompts.workflows import (
    bgp_health_check,
    daily_health_check,
    evpn_vxlan_health_check,
    mlag_troubleshoot,
    net_bgp_health_check,
    net_pre_change_audit,
    net_troubleshoot_interface,
    post_change_validation,
    pre_change_audit,
    safe_config_change,
    security_audit,
    troubleshoot_interface,
)


def test_troubleshoot_interface():
    result = troubleshoot_interface("spine-01", "Ethernet1")

    assert "spine-01" in result
    assert "Ethernet1" in result
    assert "eos_get_interface_detail" in result
    assert "eos_get_interface_errors" in result


def test_bgp_health_check():
    result = bgp_health_check("spine-01")

    assert "spine-01" in result
    assert "eos_get_bgp_summary" in result
    assert "eos_get_bgp_neighbors" in result
    assert "Established" in result


def test_pre_change_audit():
    result = pre_change_audit("leaf-01")

    assert "leaf-01" in result
    assert "eos_get_device_info" in result
    assert "eos_get_running_config" in result


def test_daily_health_check():
    result = daily_health_check("spine-01")

    assert "spine-01" in result
    assert "eos_get_cpu_usage" in result
    assert "HEALTHY" in result


def test_post_change_validation():
    result = post_change_validation("spine-01")

    assert "spine-01" in result
    assert "PASS/FAIL" in result
    assert "rollback" in result


def test_security_audit():
    result = security_audit("spine-01")

    assert "spine-01" in result
    assert "eos_get_eapi_status" in result
    assert "eos_get_aaa_status" in result


def test_mlag_troubleshoot():
    result = mlag_troubleshoot("leaf-01")

    assert "leaf-01" in result
    assert "eos_get_mlag_status" in result
    assert "split-brain" in result


def test_safe_config_change():
    result = safe_config_change("leaf-01", "Add VLAN 200")

    assert "leaf-01" in result
    assert "Add VLAN 200" in result
    assert "eos_create_config_session" in result
    assert "eos_rollback_to_checkpoint" in result


def test_evpn_vxlan_health_check():
    result = evpn_vxlan_health_check("leaf-01")

    assert "leaf-01" in result
    assert "eos_get_vxlan_interface" in result
    assert "eos_get_bgp_evpn_summary" in result


def test_net_troubleshoot_interface():
    result = net_troubleshoot_interface("spine-01", "Ethernet1")

    assert "spine-01" in result
    assert "Ethernet1" in result
    assert "net_get_device_info" in result
    assert "net_get_interface_detail" in result
    assert "net_get_interfaces" in result
    assert "net_get_lldp_neighbors" in result


def test_net_bgp_health_check():
    result = net_bgp_health_check("router-01")

    assert "router-01" in result
    assert "net_get_bgp_summary" in result
    assert "net_get_device_info" in result
    assert "net_diagnose_bgp_session" in result


def test_net_pre_change_audit():
    result = net_pre_change_audit("switch-01")

    assert "switch-01" in result
    assert "net_get_interfaces" in result
    assert "net_get_bgp_summary" in result
    assert "net_get_lldp_neighbors" in result
    assert "baseline" in result.lower()
