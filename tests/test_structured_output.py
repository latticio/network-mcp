"""Tests for structured output schemas and their application to tools."""

import asyncio

from network_mcp.schemas import (
    BgpDiagnosticOutput,
    BgpSummaryOutput,
    DeviceInfoOutput,
    FabricHealthOutput,
    ServerHealthOutput,
)
from network_mcp.server import mcp


def _get_tools():
    """Helper to get all registered tools."""
    return asyncio.run(mcp.list_tools())


def _get_tool_by_name(name: str):
    """Get a specific tool by name."""
    tools = _get_tools()
    for tool in tools:
        if tool.name == name:
            return tool
    return None


# --- Schema validation tests ---


class TestOutputSchemas:
    """Verify Pydantic output models validate both success and error responses."""

    def test_device_info_success(self):
        data = {
            "status": "success",
            "device": "spine-01",
            "data": {"hostname": "spine-01", "model": "DCS-7050SX3-48YC12"},
        }
        result = DeviceInfoOutput.model_validate(data)
        assert result.status == "success"
        assert result.device == "spine-01"
        assert result.data is not None
        assert result.error is None

    def test_device_info_error(self):
        data = {"status": "error", "device": "spine-01", "error": "Connection refused"}
        result = DeviceInfoOutput.model_validate(data)
        assert result.status == "error"
        assert result.data is None
        assert result.error == "Connection refused"

    def test_server_health_success(self):
        data = {"status": "success", "data": {"uptime": 3600, "connections": 5}}
        result = ServerHealthOutput.model_validate(data)
        assert result.status == "success"
        assert result.data["uptime"] == 3600

    def test_server_health_error(self):
        data = {"status": "error", "error": "Internal error"}
        result = ServerHealthOutput.model_validate(data)
        assert result.status == "error"

    def test_bgp_summary_success(self):
        data = {
            "status": "success",
            "device": "spine-01",
            "data": {"vrfs": {"default": {"peers": {}}}},
        }
        result = BgpSummaryOutput.model_validate(data)
        assert result.status == "success"
        assert result.pagination is None

    def test_bgp_summary_with_pagination(self):
        data = {
            "status": "success",
            "device": "spine-01",
            "data": {"peers": {"10.0.0.1": {}}},
            "pagination": {"total": 100, "limit": 10, "offset": 0, "has_more": True},
        }
        result = BgpSummaryOutput.model_validate(data)
        assert result.pagination is not None
        assert result.pagination["has_more"] is True

    def test_bgp_diagnostic_success(self):
        data = {
            "status": "success",
            "device": "spine-01",
            "workflow": "diagnose_bgp_session",
            "duration_ms": 150,
            "diagnosis": {"severity": "healthy", "summary": "BGP session is healthy"},
            "raw_data": {"bgp_summary": {}},
            "sub_operations": [{"command": "show ip bgp summary", "status": "success", "duration_ms": 50}],
        }
        result = BgpDiagnosticOutput.model_validate(data)
        assert result.workflow == "diagnose_bgp_session"
        assert result.duration_ms == 150
        assert result.diagnosis["severity"] == "healthy"

    def test_bgp_diagnostic_error(self):
        data = {"status": "error", "device": "spine-01", "error": "Timeout"}
        result = BgpDiagnosticOutput.model_validate(data)
        assert result.status == "error"
        assert result.workflow == ""
        assert result.duration_ms == 0

    def test_fabric_health_success(self):
        data = {
            "status": "success",
            "device": "fabric",
            "workflow": "validate_fabric_health",
            "duration_ms": 500,
            "diagnosis": {"severity": "healthy", "health_score": 95},
            "raw_data": {"spine-01": {}, "leaf-01": {}},
            "sub_operations": [],
        }
        result = FabricHealthOutput.model_validate(data)
        assert result.device == "fabric"
        assert result.diagnosis["health_score"] == 95

    def test_fabric_health_partial(self):
        data = {
            "status": "partial",
            "device": "fabric",
            "workflow": "validate_fabric_health",
            "duration_ms": 300,
            "diagnosis": {"severity": "warning"},
            "raw_data": {},
            "sub_operations": [{"host": "spine-01", "status": "error"}],
        }
        result = FabricHealthOutput.model_validate(data)
        assert result.status == "partial"


# --- Tool registration tests ---


class TestStructuredOutputRegistration:
    """Verify structured_output is declared on the target tools."""

    _STRUCTURED_TOOLS = [
        "eos_get_device_info",
        "eos_server_health",
        "eos_get_bgp_summary",
        "eos_diagnose_bgp_session",
        "eos_validate_fabric_health",
    ]

    def test_structured_tools_have_output_schema(self):
        """Verify all 5 target tools have outputSchema in the MCP manifest."""
        for tool_name in self._STRUCTURED_TOOLS:
            tool = _get_tool_by_name(tool_name)
            assert tool is not None, f"Tool '{tool_name}' not found"
            assert tool.outputSchema is not None, f"Tool '{tool_name}' missing outputSchema"

    def test_output_schema_has_properties(self):
        """Verify output schemas include properties with status field."""
        for tool_name in self._STRUCTURED_TOOLS:
            tool = _get_tool_by_name(tool_name)
            assert tool is not None
            schema = tool.outputSchema
            assert "properties" in schema, f"Tool '{tool_name}' outputSchema missing 'properties'"
            props = schema["properties"]
            assert "status" in props, f"Tool '{tool_name}' outputSchema missing 'status' property"

    def test_output_schema_status_is_string(self):
        """Verify status field is typed as string in all schemas."""
        for tool_name in self._STRUCTURED_TOOLS:
            tool = _get_tool_by_name(tool_name)
            assert tool is not None
            status_schema = tool.outputSchema["properties"]["status"]
            assert status_schema.get("type") == "string"

    # Tools that return dict[str, Any] get an auto-generated generic outputSchema
    # from FastMCP — these are not custom structured output models.
    _DICT_RETURN_TOOLS = [
        "net_sync_from_netbox",
        "net_push_to_netbox",
        "net_netbox_drift_check",
        "net_netbox_cable_audit",
        "net_collect_prometheus_metrics",
        "net_get_grafana_dashboard",
        "net_create_incident",
        "net_update_incident",
        "net_sync_cmdb",
        "net_validate_change",
        "net_generate_playbook",
        "net_inventory_to_ansible",
    ]

    def test_non_structured_tools_have_no_output_schema(self):
        """Verify tools without structured_output don't declare outputSchema."""
        tools = _get_tools()
        skip_set = set(self._STRUCTURED_TOOLS) | set(self._DICT_RETURN_TOOLS)
        for tool in tools:
            if tool.name not in skip_set:
                assert tool.outputSchema is None, f"Tool '{tool.name}' unexpectedly has outputSchema"


class TestFabricHealthAsyncTool:
    """Verify the MCP Tasks-enabled fabric health tool is registered correctly."""

    def test_tool_registered(self):
        tool = _get_tool_by_name("eos_fabric_health_async")
        assert tool is not None, "eos_fabric_health_async tool not registered"

    def test_tool_has_targets_parameter(self):
        tool = _get_tool_by_name("eos_fabric_health_async")
        assert tool is not None
        props = tool.inputSchema.get("properties", {})
        assert "targets" in props

    def test_tool_is_read_only(self):
        tool = _get_tool_by_name("eos_fabric_health_async")
        assert tool is not None
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
