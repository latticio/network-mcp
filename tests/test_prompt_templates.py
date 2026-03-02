"""Tests for ops-focused MCP prompt templates."""

import asyncio

from network_mcp.prompts.workflows import (
    capacity_planning,
    incident_response,
    net_security_audit,
)
from network_mcp.server import mcp


def _get_prompts():
    """Helper to get all registered prompts."""
    return asyncio.run(mcp.list_prompts())


class TestIncidentResponsePrompt:
    def test_renders_with_required_args(self):
        result = incident_response(host="spine-01")
        assert "spine-01" in result
        assert "Incident Response" in result
        assert "P2" in result  # default severity

    def test_renders_with_custom_severity(self):
        result = incident_response(host="leaf-01", severity="P1")
        assert "leaf-01" in result
        assert "P1" in result
        assert "CRITICAL" in result
        assert "Page on-call" in result

    def test_p2_severity_guidance(self):
        result = incident_response(host="spine-01", severity="P2")
        assert "HIGH" in result
        assert "30 minutes" in result

    def test_p3_severity_guidance(self):
        result = incident_response(host="spine-01", severity="P3")
        assert "MEDIUM" in result

    def test_p4_severity_guidance(self):
        result = incident_response(host="spine-01", severity="P4")
        assert "LOW" in result

    def test_unknown_severity_fallback(self):
        result = incident_response(host="spine-01", severity="P99")
        assert "P99" in result
        assert "Unknown severity" in result

    def test_contains_diagnostic_steps(self):
        result = incident_response(host="spine-01")
        assert "net_ping" in result
        assert "net_get_device_info" in result
        assert "net_get_interfaces" in result
        assert "net_get_bgp_summary" in result

    def test_is_registered_as_mcp_prompt(self):
        prompts = _get_prompts()
        prompt_names = {p.name for p in prompts}
        assert "incident_response" in prompt_names


class TestCapacityPlanningPrompt:
    def test_renders_with_defaults(self):
        result = capacity_planning()
        assert "Capacity Planning" in result
        assert "all" in result
        assert "all devices" in result.lower()

    def test_renders_with_specific_host(self):
        result = capacity_planning(host="spine-01")
        assert "spine-01" in result

    def test_renders_cpu_only(self):
        result = capacity_planning(resource_type="cpu")
        assert "CPU Utilization" in result
        assert "net_get_cpu_usage" in result

    def test_renders_memory_only(self):
        result = capacity_planning(resource_type="memory")
        assert "Memory Utilization" in result
        assert "net_get_memory_usage" in result

    def test_renders_interfaces_only(self):
        result = capacity_planning(resource_type="interfaces")
        assert "Interface Utilization" in result
        assert "net_get_interfaces" in result

    def test_renders_all_sections(self):
        result = capacity_planning(resource_type="all")
        assert "CPU Utilization" in result
        assert "Memory Utilization" in result
        assert "Interface Utilization" in result
        assert "TCAM Utilization" in result

    def test_contains_scaling_recommendations(self):
        result = capacity_planning()
        assert "Scaling Recommendations" in result

    def test_is_registered_as_mcp_prompt(self):
        prompts = _get_prompts()
        prompt_names = {p.name for p in prompts}
        assert "capacity_planning" in prompt_names


class TestNetSecurityAuditPrompt:
    def test_renders_with_defaults(self):
        result = net_security_audit()
        assert "Security Compliance Audit" in result
        assert "CIS" in result
        assert "all devices" in result.lower()

    def test_renders_with_specific_host(self):
        result = net_security_audit(host="spine-01")
        assert "spine-01" in result

    def test_cis_standard(self):
        result = net_security_audit(standard="cis")
        assert "CIS Benchmarks" in result

    def test_nist_standard(self):
        result = net_security_audit(standard="nist")
        assert "NIST SP 800-53" in result

    def test_custom_standard(self):
        result = net_security_audit(standard="custom")
        assert "Custom Standard" in result

    def test_contains_audit_checks(self):
        result = net_security_audit()
        assert "AAA Configuration" in result
        assert "Access Control Lists" in result
        assert "SNMP" in result
        assert "NTP" in result
        assert "Control Plane Policing" in result
        assert "User Accounts" in result

    def test_contains_compliance_report_section(self):
        result = net_security_audit()
        assert "Compliance Report" in result
        assert "Risk Assessment" in result
        assert "Remediation Plan" in result

    def test_uses_vendor_agnostic_tools(self):
        result = net_security_audit()
        assert "net_get_aaa_status" in result
        assert "net_get_acls" in result
        assert "net_get_snmp_config" in result

    def test_is_registered_as_mcp_prompt(self):
        prompts = _get_prompts()
        prompt_names = {p.name for p in prompts}
        assert "net_security_audit" in prompt_names


class TestPromptRegistration:
    """Verify all new prompts are properly registered with the MCP server."""

    def test_new_prompts_increase_total_count(self):
        prompts = _get_prompts()
        # We added 3 new prompts (incident_response, capacity_planning, net_security_audit)
        # to the existing 12. Total should be at least 15.
        assert len(prompts) >= 15, f"Expected at least 15 prompts, got {len(prompts)}"

    def test_all_new_prompts_have_descriptions(self):
        prompts = _get_prompts()
        new_prompt_names = {"incident_response", "capacity_planning", "net_security_audit"}
        for prompt in prompts:
            if prompt.name in new_prompt_names:
                assert prompt.description, f"Prompt '{prompt.name}' has no description"
