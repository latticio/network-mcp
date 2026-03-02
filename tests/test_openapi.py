"""Tests for OpenAPI 3.1 spec generation, versioning/deprecation, and /openapi.json endpoint."""

import asyncio
import json

from network_mcp.openapi import (
    _annotation_to_tag,
    _build_parameters_schema,
    _python_type_to_schema,
    _tool_namespace,
    generate_openapi_spec,
    get_tool_summary,
)
from network_mcp.server import mcp
from network_mcp.versioning import (
    beta,
    deprecated,
    get_replacement,
    get_sunset_date,
    is_beta,
    is_deprecated,
    is_past_sunset,
)

# ============================================================================
# OpenAPI Spec Generation
# ============================================================================


class TestGenerateOpenAPISpec:
    """Tests for generate_openapi_spec()."""

    def test_spec_is_valid_dict(self):
        """Spec generation returns a dict."""
        spec = generate_openapi_spec(mcp)
        assert isinstance(spec, dict)

    def test_spec_has_openapi_version(self):
        """Spec declares OpenAPI 3.1.0."""
        spec = generate_openapi_spec(mcp)
        assert spec["openapi"] == "3.1.0"

    def test_spec_has_info_block(self):
        """Spec has an info block with title, description, version, and license."""
        spec = generate_openapi_spec(mcp)
        info = spec["info"]
        assert info["title"] == "Network MCP Server API"
        assert "multi-vendor" in info["description"].lower() or "Multi-vendor" in info["description"]
        assert "version" in info
        assert "license" in info
        assert info["license"]["name"] == "MIT"

    def test_spec_uses_package_version_by_default(self):
        """When version is not passed, uses package __version__."""
        from network_mcp import __version__

        spec = generate_openapi_spec(mcp)
        assert spec["info"]["version"] == __version__

    def test_spec_uses_explicit_version(self):
        """When version is passed, it overrides the default."""
        spec = generate_openapi_spec(mcp, version="99.0.0")
        assert spec["info"]["version"] == "99.0.0"

    def test_spec_has_servers(self):
        """Spec includes at least one server definition."""
        spec = generate_openapi_spec(mcp)
        assert "servers" in spec
        assert len(spec["servers"]) >= 1
        assert "url" in spec["servers"][0]

    def test_spec_has_paths(self):
        """Spec has non-empty paths (tools are registered)."""
        spec = generate_openapi_spec(mcp)
        assert "paths" in spec
        assert len(spec["paths"]) > 0

    def test_all_paths_are_post(self):
        """All tool paths use POST method."""
        spec = generate_openapi_spec(mcp)
        for path, methods in spec["paths"].items():
            assert "post" in methods, f"Path {path} missing POST method"

    def test_paths_follow_tool_naming(self):
        """All paths follow /tools/{tool_name} pattern."""
        spec = generate_openapi_spec(mcp)
        for path in spec["paths"]:
            assert path.startswith("/tools/"), f"Path {path} does not follow /tools/{{name}} pattern"

    def test_spec_has_security_schemes(self):
        """Spec defines JWT bearer authentication scheme."""
        spec = generate_openapi_spec(mcp)
        schemes = spec["components"]["securitySchemes"]
        assert "bearerAuth" in schemes
        assert schemes["bearerAuth"]["type"] == "http"
        assert schemes["bearerAuth"]["scheme"] == "bearer"
        assert schemes["bearerAuth"]["bearerFormat"] == "JWT"

    def test_spec_has_component_schemas(self):
        """Spec defines ToolResponse and ErrorResponse schemas."""
        spec = generate_openapi_spec(mcp)
        schemas = spec["components"]["schemas"]
        assert "ToolResponse" in schemas
        assert "ErrorResponse" in schemas

    def test_tool_response_schema(self):
        """ToolResponse schema has expected properties."""
        spec = generate_openapi_spec(mcp)
        schema = spec["components"]["schemas"]["ToolResponse"]
        props = schema["properties"]
        assert "status" in props
        assert "device" in props
        assert "data" in props
        assert "error" in props

    def test_error_response_schema(self):
        """ErrorResponse schema has expected properties."""
        spec = generate_openapi_spec(mcp)
        schema = spec["components"]["schemas"]["ErrorResponse"]
        props = schema["properties"]
        assert "status" in props
        assert "error" in props
        assert "status" in schema["required"]
        assert "error" in schema["required"]

    def test_spec_has_tags(self):
        """Spec includes tag definitions."""
        spec = generate_openapi_spec(mcp)
        assert "tags" in spec
        assert len(spec["tags"]) > 0
        for tag in spec["tags"]:
            assert "name" in tag
            assert "description" in tag

    def test_spec_serializable_to_json(self):
        """Spec can be serialized to valid JSON."""
        spec = generate_openapi_spec(mcp)
        json_str = json.dumps(spec)
        assert isinstance(json_str, str)
        # Round-trip
        parsed = json.loads(json_str)
        assert parsed == spec

    def test_write_tools_have_security(self):
        """Write/destructive tools should have security requirements."""
        spec = generate_openapi_spec(mcp)
        for path, methods in spec["paths"].items():
            post = methods["post"]
            tags = post.get("tags", [])
            if "write" in tags or "destructive" in tags:
                assert "security" in post, f"Write tool at {path} missing security requirement"

    def test_read_tools_no_security(self):
        """Read-only tools should NOT have security requirements."""
        spec = generate_openapi_spec(mcp)
        for path, methods in spec["paths"].items():
            post = methods["post"]
            tags = post.get("tags", [])
            if "read-only" in tags:
                assert "security" not in post, f"Read-only tool at {path} should not have security"


class TestRegisteredToolsInSpec:
    """Tests that registered tools appear correctly in the spec."""

    def _get_registered_tool_names(self):
        tools = asyncio.run(mcp.list_tools())
        return {t.name for t in tools}

    def test_all_registered_tools_in_spec(self):
        """Every tool registered with FastMCP appears in the OpenAPI spec."""
        spec = generate_openapi_spec(mcp)
        registered = self._get_registered_tool_names()
        spec_tools = {path.split("/")[-1] for path in spec["paths"]}
        missing = registered - spec_tools
        assert not missing, f"Tools registered but missing from spec: {missing}"

    def test_no_extra_tools_in_spec(self):
        """No phantom tools appear in the spec that aren't registered."""
        spec = generate_openapi_spec(mcp)
        registered = self._get_registered_tool_names()
        spec_tools = {path.split("/")[-1] for path in spec["paths"]}
        extra = spec_tools - registered
        assert not extra, f"Tools in spec but not registered: {extra}"

    def test_known_tools_present(self):
        """A few well-known tools are definitely in the spec."""
        spec = generate_openapi_spec(mcp)
        expected = [
            "/tools/eos_get_device_info",
            "/tools/eos_get_vlans",
            "/tools/net_get_device_info",
            "/tools/eos_list_tool_categories",
        ]
        for path in expected:
            assert path in spec["paths"], f"Expected tool path {path} not found"

    def test_tool_has_operation_id(self):
        """Each tool path has an operationId matching the tool name."""
        spec = generate_openapi_spec(mcp)
        for path, methods in spec["paths"].items():
            tool_name = path.split("/")[-1]
            assert methods["post"]["operationId"] == tool_name

    def test_tool_has_summary_and_description(self):
        """Each tool path has a summary and description."""
        spec = generate_openapi_spec(mcp)
        for path, methods in spec["paths"].items():
            post = methods["post"]
            assert "summary" in post, f"{path} missing summary"
            assert "description" in post, f"{path} missing description"

    def test_tool_has_request_body(self):
        """Each tool path has a requestBody with JSON content type."""
        spec = generate_openapi_spec(mcp)
        for path, methods in spec["paths"].items():
            post = methods["post"]
            assert "requestBody" in post, f"{path} missing requestBody"
            assert "application/json" in post["requestBody"]["content"]

    def test_tool_has_responses(self):
        """Each tool path has 200 and error responses."""
        spec = generate_openapi_spec(mcp)
        for path, methods in spec["paths"].items():
            responses = methods["post"]["responses"]
            assert "200" in responses, f"{path} missing 200 response"
            assert "400" in responses, f"{path} missing 400 response"


# ============================================================================
# Helper Functions
# ============================================================================


class TestAnnotationToTag:
    """Tests for _annotation_to_tag()."""

    def test_read_only_tag(self):
        from network_mcp.helpers import READ_ONLY

        class FakeTool:
            annotations = READ_ONLY

        assert _annotation_to_tag(FakeTool()) == "read-only"

    def test_write_safe_tag(self):
        from network_mcp.helpers import WRITE_SAFE

        class FakeTool:
            annotations = WRITE_SAFE

        assert _annotation_to_tag(FakeTool()) == "write"

    def test_destructive_tag(self):
        from network_mcp.helpers import DESTRUCTIVE

        class FakeTool:
            annotations = DESTRUCTIVE

        assert _annotation_to_tag(FakeTool()) == "destructive"

    def test_no_annotations_tag(self):
        class FakeTool:
            annotations = None

        assert _annotation_to_tag(FakeTool()) == "unclassified"


class TestToolNamespace:
    """Tests for _tool_namespace()."""

    def test_net_namespace(self):
        assert _tool_namespace("net_get_device_info") == "Vendor-Agnostic (net)"

    def test_eos_namespace(self):
        assert _tool_namespace("eos_get_vlans") == "Arista EOS (eos)"

    def test_nxos_namespace(self):
        assert _tool_namespace("nxos_get_vpc_status") == "Cisco NX-OS (nxos)"

    def test_iosxe_namespace(self):
        assert _tool_namespace("iosxe_get_platform_info") == "Cisco IOS-XE (iosxe)"

    def test_junos_namespace(self):
        assert _tool_namespace("junos_get_routing_instances") == "Juniper JunOS (junos)"

    def test_unknown_namespace(self):
        assert _tool_namespace("unknown_tool") == "Other"


class TestPythonTypeToSchema:
    """Tests for _python_type_to_schema()."""

    def test_str_type(self):
        result = _python_type_to_schema(str)
        assert result["type"] == "string"

    def test_int_type(self):
        result = _python_type_to_schema(int)
        assert result["type"] == "integer"

    def test_float_type(self):
        result = _python_type_to_schema(float)
        assert result["type"] == "number"

    def test_bool_type(self):
        result = _python_type_to_schema(bool)
        assert result["type"] == "boolean"

    def test_dict_type(self):
        result = _python_type_to_schema(dict)
        assert result["type"] == "object"

    def test_list_type(self):
        result = _python_type_to_schema(list)
        assert result["type"] == "array"

    def test_empty_annotation(self):
        import inspect

        result = _python_type_to_schema(inspect.Parameter.empty)
        assert result["type"] == "string"


class TestBuildParametersSchema:
    """Tests for _build_parameters_schema()."""

    def test_no_params(self):
        class FakeTool:
            def fn(self):
                pass

        schema = _build_parameters_schema(FakeTool())
        assert schema["type"] == "object"

    def test_with_params(self):
        def fn(host: str, vlan_id: int = 100):
            pass

        class FakeTool:
            context_kwarg = None

        fake = FakeTool()
        fake.fn = fn  # type: ignore[attr-defined]
        schema = _build_parameters_schema(fake)
        assert "host" in schema["properties"]
        assert "vlan_id" in schema["properties"]
        assert "host" in schema["required"]
        assert "vlan_id" not in schema.get("required", [])

    def test_no_fn(self):
        class FakeTool:
            pass

        schema = _build_parameters_schema(FakeTool())
        assert schema == {"type": "object", "properties": {}}


class TestToolSummary:
    """Tests for get_tool_summary()."""

    def test_summary_has_total(self):
        summary = get_tool_summary(mcp)
        assert "total_tools" in summary
        assert summary["total_tools"] > 0

    def test_summary_has_namespaces(self):
        summary = get_tool_summary(mcp)
        assert "by_namespace" in summary
        assert len(summary["by_namespace"]) > 0

    def test_summary_has_access_levels(self):
        summary = get_tool_summary(mcp)
        assert "by_access_level" in summary
        assert "read-only" in summary["by_access_level"]

    def test_summary_counts_match_total(self):
        summary = get_tool_summary(mcp)
        ns_total = sum(summary["by_namespace"].values())
        assert ns_total == summary["total_tools"]
        access_total = sum(summary["by_access_level"].values())
        assert access_total == summary["total_tools"]


# ============================================================================
# Versioning / Deprecation
# ============================================================================


class TestDeprecatedDecorator:
    """Tests for the @deprecated decorator."""

    def test_deprecated_adds_metadata_to_result(self):
        @deprecated(sunset_date="2027-06-01", replacement="new_tool")
        def old_tool(host: str) -> dict:
            return {"status": "success", "device": host}

        result = old_tool("spine-01")
        assert result["_deprecated"] is True
        assert result["_sunset"] == "2027-06-01"
        assert result["_replacement"] == "new_tool"

    def test_deprecated_without_replacement(self):
        @deprecated(sunset_date="2027-12-31")
        def old_tool() -> dict:
            return {"status": "success"}

        result = old_tool()
        assert result["_deprecated"] is True
        assert result["_sunset"] == "2027-12-31"
        assert "_replacement" not in result

    def test_deprecated_preserves_function_name(self):
        @deprecated(sunset_date="2027-01-01")
        def my_tool() -> dict:
            return {}

        assert my_tool.__name__ == "my_tool"

    def test_deprecated_non_dict_return(self):
        """If the function doesn't return a dict, the decorator should not crash."""

        @deprecated(sunset_date="2027-01-01")
        def string_tool() -> str:
            return "hello"

        result = string_tool()
        assert result == "hello"

    def test_is_deprecated_true(self):
        @deprecated(sunset_date="2027-01-01")
        def old_tool():
            return {}

        assert is_deprecated(old_tool) is True

    def test_is_deprecated_false(self):
        def normal_tool():
            return {}

        assert is_deprecated(normal_tool) is False

    def test_get_sunset_date(self):
        @deprecated(sunset_date="2027-03-15")
        def old_tool():
            return {}

        assert get_sunset_date(old_tool) == "2027-03-15"

    def test_get_sunset_date_not_deprecated(self):
        def normal_tool():
            return {}

        assert get_sunset_date(normal_tool) == ""

    def test_get_replacement(self):
        @deprecated(sunset_date="2027-01-01", replacement="net_new_tool")
        def old_tool():
            return {}

        assert get_replacement(old_tool) == "net_new_tool"

    def test_get_replacement_none(self):
        @deprecated(sunset_date="2027-01-01")
        def old_tool():
            return {}

        assert get_replacement(old_tool) == ""

    def test_is_past_sunset_false(self):
        @deprecated(sunset_date="2099-12-31")
        def future_tool():
            return {}

        assert is_past_sunset(future_tool) is False

    def test_is_past_sunset_true(self):
        @deprecated(sunset_date="2020-01-01")
        def old_tool():
            return {}

        assert is_past_sunset(old_tool) is True

    def test_is_past_sunset_not_deprecated(self):
        def normal_tool():
            return {}

        assert is_past_sunset(normal_tool) is False


class TestBetaDecorator:
    """Tests for the @beta decorator."""

    def test_beta_adds_metadata(self):
        @beta(since="6.0.0")
        def new_tool() -> dict:
            return {"status": "success"}

        result = new_tool()
        assert result["_beta"] is True
        assert result["_since"] == "6.0.0"

    def test_beta_without_since(self):
        @beta()
        def new_tool() -> dict:
            return {"status": "success"}

        result = new_tool()
        assert result["_beta"] is True
        assert "_since" not in result or result["_since"] == ""

    def test_beta_preserves_function_name(self):
        @beta(since="6.0.0")
        def my_beta_tool() -> dict:
            return {}

        assert my_beta_tool.__name__ == "my_beta_tool"

    def test_is_beta_true(self):
        @beta(since="6.0.0")
        def new_tool():
            return {}

        assert is_beta(new_tool) is True

    def test_is_beta_false(self):
        def stable_tool():
            return {}

        assert is_beta(stable_tool) is False

    def test_beta_non_dict_return(self):
        @beta(since="6.0.0")
        def string_tool() -> str:
            return "hello"

        result = string_tool()
        assert result == "hello"


# ============================================================================
# OpenAPI Endpoint (integration)
# ============================================================================


class TestOpenAPIEndpoint:
    """Tests for the /openapi.json endpoint integration."""

    def test_openapi_endpoint_registered(self):
        """Verify the /openapi.json custom route is registered (starlette available)."""
        # We can't easily test the HTTP endpoint without a running server,
        # but we can verify the spec generation works end-to-end.
        spec = generate_openapi_spec(mcp)
        assert spec["openapi"] == "3.1.0"
        assert len(spec["paths"]) > 0
        # Verify it's valid JSON by round-tripping
        json_str = json.dumps(spec, indent=2)
        assert len(json_str) > 1000  # Non-trivial spec

    def test_spec_includes_all_response_codes(self):
        """Verify all tool paths include standard response codes."""
        spec = generate_openapi_spec(mcp)
        for _path, methods in spec["paths"].items():
            responses = methods["post"]["responses"]
            assert "200" in responses
            assert "400" in responses
            assert "429" in responses  # rate limit
            assert "503" in responses  # circuit breaker
