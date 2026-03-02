"""Generate OpenAPI 3.1 specification from registered MCP tools.

Introspects all registered tools, their parameters, and return types
to produce a complete OpenAPI specification for the HTTP transport.
"""

import inspect
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("network-mcp")

# Map Python type annotation names to JSON Schema types
_TYPE_MAP: dict[str, dict[str, Any]] = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "dict": {"type": "object"},
    "list": {"type": "array"},
    "list[str]": {"type": "array", "items": {"type": "string"}},
    "list[int]": {"type": "array", "items": {"type": "integer"}},
    "list[dict]": {"type": "array", "items": {"type": "object"}},
    "list[dict[str, Any]]": {"type": "array", "items": {"type": "object"}},
}


def _annotation_to_tag(tool: Any) -> str:
    """Derive a tag string from tool annotations."""
    annotations = getattr(tool, "annotations", None)
    if annotations is None:
        return "unclassified"
    if getattr(annotations, "destructiveHint", False):
        return "destructive"
    if getattr(annotations, "readOnlyHint", False):
        return "read-only"
    return "write"


def _tool_namespace(name: str) -> str:
    """Derive a namespace tag from tool name prefix."""
    if name.startswith("net_"):
        return "Vendor-Agnostic (net)"
    if name.startswith("eos_"):
        return "Arista EOS (eos)"
    if name.startswith("nxos_"):
        return "Cisco NX-OS (nxos)"
    if name.startswith("iosxe_"):
        return "Cisco IOS-XE (iosxe)"
    if name.startswith("junos_"):
        return "Juniper JunOS (junos)"
    return "Other"


def _python_type_to_schema(annotation: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema fragment."""
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}

    type_str = getattr(annotation, "__name__", str(annotation))

    # Check direct mapping first
    if type_str in _TYPE_MAP:
        return dict(_TYPE_MAP[type_str])

    # Handle Optional types (str | None, etc.)
    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())

    # Union types (including Optional)
    if origin is type(str | int):  # types.UnionType in 3.10+
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _python_type_to_schema(non_none[0])

    # typing.Union / typing.Optional
    try:
        import typing

        if origin is typing.Union:
            non_none = [a for a in args if a is not type(None)]
            if non_none:
                return _python_type_to_schema(non_none[0])
    except Exception:
        logger.debug("Could not resolve Union type for %s", annotation)

    # list[X]
    if origin is list:
        if args:
            return {"type": "array", "items": _python_type_to_schema(args[0])}
        return {"type": "array"}

    # dict[X, Y]
    if origin is dict:
        return {"type": "object"}

    # Fallback: try string lookup
    clean = str(annotation).replace("typing.", "")
    if clean in _TYPE_MAP:
        return dict(_TYPE_MAP[clean])

    return {"type": "string"}


def _build_parameters_schema(tool: Any) -> dict[str, Any]:
    """Build a JSON Schema for tool parameters from its function signature."""
    fn = getattr(tool, "fn", None)
    if fn is None:
        return {"type": "object", "properties": {}}

    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        # Skip 'self', 'cls', and context injection kwargs
        if param_name in ("self", "cls"):
            continue
        context_kwarg = getattr(tool, "context_kwarg", None)
        if context_kwarg and param_name == context_kwarg:
            continue

        prop = _python_type_to_schema(param.annotation)
        # Add description from docstring if available
        prop["description"] = f"Parameter: {param_name}"

        if param.default is not inspect.Parameter.empty:
            if param.default is not None:
                prop["default"] = param.default
        else:
            required.append(param_name)

        properties[param_name] = prop

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _build_tool_description(tool: Any) -> str:
    """Extract tool description from the tool object."""
    desc = getattr(tool, "description", "") or ""
    if not desc:
        fn = getattr(tool, "fn", None)
        if fn:
            desc = inspect.getdoc(fn) or ""
    # Return first paragraph only
    if "\n\n" in desc:
        desc = desc.split("\n\n")[0]
    return desc.strip()


def generate_openapi_spec(mcp_server: FastMCP, version: str | None = None) -> dict[str, Any]:
    """Generate OpenAPI 3.1 spec from MCP server tools.

    Args:
        mcp_server: The FastMCP server instance.
        version: API version string. If None, reads from package __version__.

    Returns:
        OpenAPI 3.1 specification as a dict.
    """
    if version is None:
        from network_mcp import __version__

        version = __version__

    spec: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": "Network MCP Server API",
            "description": (
                "Multi-vendor network management via MCP tools. "
                "Supports Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS."
            ),
            "version": version,
            "license": {"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
            "contact": {"name": "Network MCP", "url": "https://github.com/jf/network-mcp"},
        },
        "servers": [
            {"url": "http://localhost:8000", "description": "Local development"},
        ],
        "paths": {},
        "components": {
            "schemas": {
                "ToolResponse": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["success", "error", "partial"],
                            "description": "Operation result status",
                        },
                        "device": {"type": "string", "description": "Target device hostname or IP"},
                        "data": {
                            "type": "object",
                            "description": "Response data (varies by tool)",
                            "additionalProperties": True,
                        },
                        "error": {"type": "string", "description": "Error message if status is 'error'"},
                    },
                    "required": ["status"],
                },
                "ErrorResponse": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "const": "error"},
                        "device": {"type": "string"},
                        "error": {"type": "string", "description": "Error description"},
                        "request_id": {"type": "string", "description": "Correlation ID for tracing"},
                    },
                    "required": ["status", "error"],
                },
            },
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": "JWT bearer token with network:read/write/admin/audit scopes",
                }
            },
        },
        "tags": [],
    }

    # Collect unique tags
    tags_seen: set[str] = set()

    # Access registered tools via internal API
    tools_dict = getattr(getattr(mcp_server, "_tool_manager", None), "_tools", {})
    if not tools_dict:
        logger.warning("No tools found in MCP server — OpenAPI spec will have empty paths")
        return spec

    for tool_name, tool in sorted(tools_dict.items()):
        access_tag = _annotation_to_tag(tool)
        namespace_tag = _tool_namespace(tool_name)
        tags = [namespace_tag, access_tag]

        for tag in tags:
            if tag not in tags_seen:
                tags_seen.add(tag)

        description = _build_tool_description(tool)
        parameters_schema = _build_parameters_schema(tool)

        path = f"/tools/{tool_name}"
        spec["paths"][path] = {
            "post": {
                "operationId": tool_name,
                "summary": description.split(".")[0] if description else tool_name,
                "description": description,
                "tags": tags,
                "requestBody": {
                    "required": bool(parameters_schema.get("required")),
                    "content": {
                        "application/json": {
                            "schema": parameters_schema,
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Successful operation",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ToolResponse"},
                            }
                        },
                    },
                    "400": {
                        "description": "Invalid parameters",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                            }
                        },
                    },
                    "401": {
                        "description": "Authentication required",
                    },
                    "403": {
                        "description": "Insufficient scope (RBAC)",
                    },
                    "429": {
                        "description": "Rate limit exceeded",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                            }
                        },
                    },
                    "503": {
                        "description": "Circuit breaker open / device unreachable",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                            }
                        },
                    },
                },
            },
        }

        # Add security requirement for write/destructive tools
        if access_tag in ("write", "destructive"):
            spec["paths"][path]["post"]["security"] = [{"bearerAuth": []}]

    # Build sorted tag list with descriptions
    tag_descriptions = {
        "read-only": "Read-only tools that do not modify device state",
        "write": "Write tools that modify device configuration (requires NET_READ_ONLY=false)",
        "destructive": "Destructive tools that may cause service impact",
        "unclassified": "Tools without explicit access annotations",
        "Vendor-Agnostic (net)": "Cross-vendor tools using the NetworkDriver protocol",
        "Arista EOS (eos)": "Arista EOS-specific tools using pyeapi/eAPI",
        "Cisco NX-OS (nxos)": "Cisco NX-OS-specific tools for Nexus platforms",
        "Cisco IOS-XE (iosxe)": "Cisco IOS-XE-specific tools using RESTCONF",
        "Juniper JunOS (junos)": "Juniper JunOS-specific tools using NETCONF",
        "Other": "Other tools",
    }
    spec["tags"] = [{"name": tag, "description": tag_descriptions.get(tag, tag)} for tag in sorted(tags_seen)]

    return spec


def get_tool_summary(mcp_server: FastMCP) -> dict[str, Any]:
    """Get a summary of registered tools by namespace and access level.

    Args:
        mcp_server: The FastMCP server instance.

    Returns:
        Summary dict with counts by namespace and access level.
    """
    tools_dict = getattr(getattr(mcp_server, "_tool_manager", None), "_tools", {})

    by_namespace: dict[str, int] = {}
    by_access: dict[str, int] = {}

    for tool_name, tool in tools_dict.items():
        ns = _tool_namespace(tool_name)
        access = _annotation_to_tag(tool)
        by_namespace[ns] = by_namespace.get(ns, 0) + 1
        by_access[access] = by_access.get(access, 0) + 1

    return {
        "total_tools": len(tools_dict),
        "by_namespace": dict(sorted(by_namespace.items())),
        "by_access_level": dict(sorted(by_access.items())),
    }
