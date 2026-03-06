#!/usr/bin/env python3
"""Generate vendor-specific tool stubs from the NetworkDriver protocol.

Reads all get_* methods from NetworkDriver and generates MCP tool functions
that call the driver's getter via the connection manager. Generated tools
follow the existing net_* naming convention and patterns.

Usage:
    uv run python scripts/generate_vendor_tools.py              # stdout
    uv run python scripts/generate_vendor_tools.py -o out.py    # write to file
    uv run python scripts/generate_vendor_tools.py --prefix eos # eos_ prefix
    uv run python scripts/generate_vendor_tools.py --list       # list getters only
"""

from __future__ import annotations

import argparse
import ast
import sys
import textwrap
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "network_mcp"
DRIVERS_BASE = SRC_DIR / "drivers" / "base.py"


def extract_getters_from_protocol(filepath: Path | None = None) -> list[dict]:
    """Parse the NetworkDriver protocol and extract all get_* method signatures.

    Returns a list of dicts, each with:
        - name: method name (e.g., 'get_facts')
        - params: list of (name, annotation_str, default_str | None) tuples
                  (excludes 'self')
        - docstring: full docstring text
        - first_line: first line of the docstring (summary)
        - returns_doc: return type description from docstring
    """
    source_path = filepath or DRIVERS_BASE
    source = source_path.read_text()
    tree = ast.parse(source)

    getters: list[dict] = []

    # Find the NetworkDriver class
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "NetworkDriver":
            continue

        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not item.name.startswith("get_"):
                continue

            # Extract parameters (skip 'self')
            params: list[tuple[str, str, str | None]] = []
            args = item.args
            num_defaults = len(args.defaults)
            num_args = len(args.args)

            for i, arg in enumerate(args.args):
                if arg.arg == "self":
                    continue
                annotation = ast.unparse(arg.annotation) if arg.annotation else "str"
                default_idx = i - (num_args - num_defaults)
                default = ast.unparse(args.defaults[default_idx]) if default_idx >= 0 else None
                params.append((arg.arg, annotation, default))

            docstring = ast.get_docstring(item) or ""
            first_line = docstring.split("\n")[0].strip() if docstring else f"Call {item.name} on the device."

            # Extract return description from docstring
            returns_doc = ""
            if "Returns:" in docstring:
                returns_section = docstring.split("Returns:")[1]
                returns_doc = returns_section.strip()

            getters.append({
                "name": item.name,
                "params": params,
                "docstring": docstring,
                "first_line": first_line,
                "returns_doc": returns_doc,
            })

    return getters


def _make_tool_name(getter_name: str, prefix: str) -> str:
    """Convert a getter name to a tool function name.

    Examples:
        get_facts, net -> net_get_facts
        get_bgp_summary, eos -> eos_get_bgp_summary
    """
    return f"{prefix}_{getter_name}"


def _build_signature(params: list[tuple[str, str, str | None]]) -> str:
    """Build the function signature parameters string.

    Always starts with 'host: str', then adds getter-specific params.
    """
    parts = ["host: str"]
    for name, annotation, default in params:
        if default is not None:
            parts.append(f"{name}: {annotation} = {default}")
        else:
            parts.append(f"{name}: {annotation}")
    return ", ".join(parts)


def _build_call_args(params: list[tuple[str, str, str | None]]) -> str:
    """Build the keyword arguments for the driver getter call."""
    if not params:
        return ""
    args = ", ".join(f"{name}={name}" for name, _, _ in params)
    return args


def _build_docstring(getter: dict, prefix: str) -> str:
    """Build the tool docstring from the getter's docstring."""
    summary = getter["first_line"]
    # Remove trailing period for re-phrasing
    if summary.endswith("."):
        summary = summary[:-1]

    lines = [f'    """{summary} from any supported network device.']
    lines.append("")
    lines.append("    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.")
    lines.append("")
    lines.append("    Args:")
    lines.append("        host: Hostname, IP address, or inventory name of the network device.")

    # Add param docs from original docstring
    for name, _annotation, _default in getter["params"]:
        # Try to find the param description in the original docstring
        param_desc = ""
        if f"{name}:" in getter["docstring"]:
            for line in getter["docstring"].split("\n"):
                stripped = line.strip()
                if stripped.startswith(f"{name}:"):
                    param_desc = stripped[len(f"{name}:") :].strip()
                    break
        if param_desc:
            lines.append(f"        {name}: {param_desc}")
        else:
            lines.append(f"        {name}: {name.replace('_', ' ').capitalize()} parameter.")

    lines.append('    """')
    return "\n".join(lines)


def generate_tool_stub(getter: dict, prefix: str) -> str:
    """Generate a single tool function stub for a getter."""
    tool_name = _make_tool_name(getter["name"], prefix)
    sig = _build_signature(getter["params"])
    docstring = _build_docstring(getter, prefix)
    call_args = _build_call_args(getter["params"])

    getter_call = f"driver.{getter['name']}({call_args})"

    lines = [
        "@mcp.tool(annotations=READ_ONLY)",
        "@handle_tool_errors",
        f"def {tool_name}({sig}) -> dict:",
        docstring,
        "    driver = conn_mgr.get_driver(host)",
        f"    data = {getter_call}",
        "    return {",
        '        "status": "success",',
        '        "device": host,',
        '        "vendor": driver.vendor,',
        '        "platform": driver.platform,',
        '        "data": data,',
        "    }",
        "",
    ]
    return "\n".join(lines)


def generate_module(getters: list[dict], prefix: str = "net") -> str:
    """Generate a complete Python module with tool stubs for all getters.

    Args:
        getters: List of getter dicts from extract_getters_from_protocol.
        prefix: Tool name prefix (e.g., 'net', 'eos', 'nxos').

    Returns:
        Complete Python module source code as a string.
    """
    header = textwrap.dedent(f'''\
        """Auto-generated vendor tool stubs ({prefix}_* namespace).

        Generated by scripts/generate_vendor_tools.py from the NetworkDriver protocol.
        Do not edit manually — re-run the generator to update.
        """

        from __future__ import annotations

        import logging

        from network_mcp.helpers import READ_ONLY, handle_tool_errors
        from network_mcp.server import conn_mgr, mcp

        logger = logging.getLogger("network-mcp")

        ''')

    stubs = [generate_tool_stub(g, prefix) for g in getters]
    return header + "\n\n".join(stubs)


def list_getters(getters: list[dict]) -> str:
    """Format a summary list of all discovered getters."""
    lines = [f"Found {len(getters)} get_* methods in NetworkDriver:\n"]
    for g in getters:
        params = ", ".join(f"{n}: {t}" for n, t, _ in g["params"])
        lines.append(f"  {g['name']}({params})")
        lines.append(f"    {g['first_line']}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the generator."""
    parser = argparse.ArgumentParser(
        description="Generate vendor tool stubs from the NetworkDriver protocol.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default="net",
        help="Tool name prefix (default: 'net'). Examples: 'eos', 'nxos', 'junos'.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_only",
        help="List discovered getters without generating code.",
    )
    parser.add_argument(
        "--protocol-file",
        type=str,
        default=None,
        help="Path to the protocol file (default: src/network_mcp/drivers/base.py).",
    )

    args = parser.parse_args(argv)

    protocol_path = Path(args.protocol_file) if args.protocol_file else None
    getters = extract_getters_from_protocol(protocol_path)

    if not getters:
        sys.stderr.write("Error: No get_* methods found in NetworkDriver protocol.\n")
        return 1

    if args.list_only:
        output = list_getters(getters)
    else:
        output = generate_module(getters, prefix=args.prefix)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output)
        sys.stderr.write(f"Generated {len(getters)} tool stubs -> {args.output}\n")
    else:
        sys.stdout.write(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
