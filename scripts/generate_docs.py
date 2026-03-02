#!/usr/bin/env python3
"""Generate docs/TOOLS.md from registered MCP tools, resources, and prompts.

Usage:
    uv run python scripts/generate_docs.py
"""

import ast
import sys
from pathlib import Path

# Project paths
SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "network_mcp"
TOOLS_DIR = SRC_DIR / "tools"
RESOURCES_DIR = SRC_DIR / "resources"
PROMPTS_DIR = SRC_DIR / "prompts"
OUTPUT_FILE = Path(__file__).resolve().parent.parent / "docs" / "TOOLS.md"

# Module display order and human-readable names
MODULE_ORDER = [
    ("device", "Device Information"),
    ("interfaces", "Interfaces"),
    ("vlans", "VLANs"),
    ("routing", "Routing & Protocols"),
    ("switching", "Switching & L2"),
    ("evpn_vxlan", "EVPN / VXLAN"),
    ("monitoring", "Monitoring & Environment"),
    ("config_mgmt", "Configuration Management"),
    ("security", "Security"),
    ("troubleshoot", "Troubleshooting"),
    ("vrf", "VRF"),
    ("bfd", "BFD"),
    ("event_monitor", "Event Monitor"),
    ("fabric", "Multi-Device Fabric"),
    ("validation", "ANTA Validation"),
    ("gnmi", "gNMI Telemetry"),
]


def extract_tools_from_file(filepath: Path) -> list[dict]:
    """Extract tool functions decorated with @mcp.tool from a Python file."""
    source = filepath.read_text()
    tree = ast.parse(source)
    tools = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Check if decorated with @mcp.tool
        is_tool = False
        annotations_hint = ""
        for dec in node.decorator_list:
            if isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Name):
                if dec.value.id == "mcp" and dec.attr == "tool":
                    is_tool = True
            elif isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Attribute) and isinstance(dec.func.value, ast.Name):
                    if dec.func.value.id == "mcp" and dec.func.attr == "tool":
                        is_tool = True
                        # Check for annotation keywords
                        for kw in dec.keywords:
                            if kw.arg == "annotations" and isinstance(kw.value, ast.Name):
                                annotations_hint = kw.value.id

        if not is_tool:
            continue

        # Extract docstring
        docstring = ast.get_docstring(node) or ""
        description = docstring.split("\n")[0].strip() if docstring else ""

        # Extract parameters
        params = []
        args = node.args
        for i, arg in enumerate(args.args):
            param_name = arg.arg
            param_type = ""
            if arg.annotation:
                param_type = ast.unparse(arg.annotation)

            # Get default value
            default = None
            # defaults are right-aligned with args
            num_defaults = len(args.defaults)
            num_args = len(args.args)
            default_idx = i - (num_args - num_defaults)
            if default_idx >= 0:
                default = ast.unparse(args.defaults[default_idx])

            params.append({
                "name": param_name,
                "type": param_type,
                "default": default,
            })

        # Determine read/write from annotations or docstring
        tool_type = "Read"
        if "WRITE" in annotations_hint or "DESTRUCTIVE" in annotations_hint:
            tool_type = "Write"
        elif "[WRITE]" in description or "[CAUTION]" in description or "[DESTRUCTIVE]" in description:
            tool_type = "Write"
        elif annotations_hint in ("WRITE_OP", "DESTRUCTIVE_OP"):
            tool_type = "Write"

        tools.append({
            "name": node.name,
            "description": description,
            "params": params,
            "type": tool_type,
            "line": node.lineno,
        })

    return tools


def extract_resources_from_file(filepath: Path) -> list[dict]:
    """Extract resource functions from a Python file."""
    source = filepath.read_text()
    tree = ast.parse(source)
    resources = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for dec in node.decorator_list:
            uri = None
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if hasattr(dec.func, "attr") and dec.func.attr == "resource":
                    if dec.args and isinstance(dec.args[0], ast.Constant):
                        uri = dec.args[0].value

            if uri:
                docstring = ast.get_docstring(node) or ""
                description = docstring.split("\n")[0].strip() if docstring else ""
                resources.append({"uri": uri, "name": node.name, "description": description})

    return resources


def extract_prompts_from_file(filepath: Path) -> list[dict]:
    """Extract prompt functions from a Python file."""
    source = filepath.read_text()
    tree = ast.parse(source)
    prompts = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for dec in node.decorator_list:
            is_prompt = False
            if isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Name):
                if dec.value.id == "mcp" and dec.attr == "prompt":
                    is_prompt = True
            elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if hasattr(dec.func, "attr") and dec.func.attr == "prompt":
                    is_prompt = True

            if is_prompt:
                docstring = ast.get_docstring(node) or ""
                description = docstring.split("\n")[0].strip() if docstring else ""

                params = []
                for arg in node.args.args:
                    if arg.arg == "self":
                        continue
                    param_type = ast.unparse(arg.annotation) if arg.annotation else "str"
                    params.append({"name": arg.arg, "type": param_type})

                prompts.append({"name": node.name, "description": description, "params": params})

    return prompts


def generate_markdown(all_tools: dict, resources: list, prompts: list) -> str:
    """Generate the full TOOLS.md markdown content."""
    lines = [
        "# Tool Reference",
        "",
        "> Auto-generated by `scripts/generate_docs.py`. Do not edit manually.",
        "",
    ]

    # Summary
    total_tools = sum(len(tools) for tools in all_tools.values())
    total_read = sum(1 for tools in all_tools.values() for t in tools if t["type"] == "Read")
    total_write = total_tools - total_read
    lines.append(f"**{total_tools} tools** ({total_read} read-only + {total_write} write) | "
                 f"**{len(resources)} resources** | **{len(prompts)} prompts**")
    lines.append("")

    # Table of contents
    lines.append("## Contents")
    lines.append("")
    for module_name, display_name in MODULE_ORDER:
        if module_name in all_tools:
            count = len(all_tools[module_name])
            anchor = display_name.lower().replace(" ", "-").replace("/", "").replace("&", "")
            lines.append(f"- [{display_name}](#{anchor}) ({count} tools)")
    lines.append(f"- [Resources](#resources) ({len(resources)})")
    lines.append(f"- [Prompts](#prompts) ({len(prompts)})")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Tools by module
    for module_name, display_name in MODULE_ORDER:
        if module_name not in all_tools:
            continue
        tools = all_tools[module_name]
        lines.append(f"## {display_name}")
        lines.append("")
        lines.append(f"Module: `tools/{module_name}.py` ({len(tools)} tools)")
        lines.append("")
        lines.append("| Tool | Description | Parameters | Type |")
        lines.append("|------|-------------|------------|------|")

        for tool in tools:
            # Format parameters (skip 'host' since it's always first)
            param_strs = []
            for p in tool["params"]:
                s = f"`{p['name']}: {p['type']}`"
                if p["default"] is not None:
                    s += f" = {p['default']}"
                param_strs.append(s)
            params_cell = ", ".join(param_strs) if param_strs else "-"

            desc = tool["description"].replace("|", "\\|")
            type_badge = "Write" if tool["type"] == "Write" else "Read"
            lines.append(f"| `{tool['name']}` | {desc} | {params_cell} | {type_badge} |")

        lines.append("")

    # Resources
    lines.append("---")
    lines.append("")
    lines.append("## Resources")
    lines.append("")
    lines.append("Read-only data endpoints accessible via MCP resource URIs.")
    lines.append("")
    lines.append("| URI | Description |")
    lines.append("|-----|-------------|")
    for r in resources:
        lines.append(f"| `{r['uri']}` | {r['description']} |")
    lines.append("")

    # Prompts
    lines.append("---")
    lines.append("")
    lines.append("## Prompts")
    lines.append("")
    lines.append("Reusable workflow templates that guide the AI through multi-step operations.")
    lines.append("")
    lines.append("| Prompt | Description | Parameters |")
    lines.append("|--------|-------------|------------|")
    for p in prompts:
        params = ", ".join(f"`{pp['name']}: {pp['type']}`" for pp in p["params"])
        lines.append(f"| `{p['name']}` | {p['description']} | {params} |")
    lines.append("")

    return "\n".join(lines)


def main():
    # Collect tools from all modules
    all_tools: dict[str, list[dict]] = {}
    for module_name, _display_name in MODULE_ORDER:
        filepath = TOOLS_DIR / f"{module_name}.py"
        if filepath.exists():
            tools = extract_tools_from_file(filepath)
            if tools:
                all_tools[module_name] = tools

    # Collect resources
    resources = []
    for filepath in sorted(RESOURCES_DIR.glob("*.py")):
        if filepath.name == "__init__.py":
            continue
        resources.extend(extract_resources_from_file(filepath))

    # Collect prompts
    prompts = []
    for filepath in sorted(PROMPTS_DIR.glob("*.py")):
        if filepath.name == "__init__.py":
            continue
        prompts.extend(extract_prompts_from_file(filepath))

    # Generate markdown
    markdown = generate_markdown(all_tools, resources, prompts)

    # Write output
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(markdown)

    total_tools = sum(len(tools) for tools in all_tools.values())
    print(f"Generated {OUTPUT_FILE}")
    print(f"  Tools: {total_tools} across {len(all_tools)} modules")
    print(f"  Resources: {len(resources)}")
    print(f"  Prompts: {len(prompts)}")


if __name__ == "__main__":
    main()
