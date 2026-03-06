"""Tests for scripts/generate_vendor_tools.py — vendor tool stub generator."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

# Import the generator functions directly
from scripts.generate_vendor_tools import (
    _build_call_args,
    _build_signature,
    _make_tool_name,
    extract_getters_from_protocol,
    generate_module,
    generate_tool_stub,
    list_getters,
    main,
)

DRIVERS_BASE = Path(__file__).resolve().parent.parent / "src" / "network_mcp" / "drivers" / "base.py"


# --- extract_getters_from_protocol ---


class TestExtractGetters:
    """Test getter extraction from the NetworkDriver protocol."""

    def test_finds_all_getters(self):
        getters = extract_getters_from_protocol()
        names = [g["name"] for g in getters]
        # Must find core getters
        assert "get_facts" in names
        assert "get_interfaces" in names
        assert "get_bgp_summary" in names
        assert "get_arp_table" in names
        assert "get_vlans" in names
        assert "get_environment" in names

    def test_getter_count(self):
        getters = extract_getters_from_protocol()
        # Protocol defines 35 get_* methods
        assert len(getters) >= 30, f"Expected >=30 getters, got {len(getters)}"

    def test_no_non_getters(self):
        getters = extract_getters_from_protocol()
        names = [g["name"] for g in getters]
        # Should NOT include non-get methods
        assert "connect" not in names
        assert "close" not in names
        assert "run_show" not in names
        assert "run_config" not in names
        assert "capabilities" not in names
        assert "reconnect" not in names

    def test_getter_has_docstring(self):
        getters = extract_getters_from_protocol()
        for g in getters:
            assert g["first_line"], f"{g['name']} missing docstring"

    def test_parameterless_getter(self):
        getters = extract_getters_from_protocol()
        facts = next(g for g in getters if g["name"] == "get_facts")
        assert facts["params"] == []

    def test_getter_with_params(self):
        getters = extract_getters_from_protocol()
        bgp = next(g for g in getters if g["name"] == "get_bgp_summary")
        assert len(bgp["params"]) == 1
        name, annotation, default = bgp["params"][0]
        assert name == "vrf"
        assert annotation == "str"
        assert default == "'default'"

    def test_getter_with_multiple_params(self):
        getters = extract_getters_from_protocol()
        routes = next(g for g in getters if g["name"] == "get_route_table")
        assert len(routes["params"]) == 3
        param_names = [p[0] for p in routes["params"]]
        assert param_names == ["protocol", "vrf", "limit"]

    def test_custom_protocol_file(self, tmp_path):
        """Test extraction from a custom protocol file."""
        custom = tmp_path / "proto.py"
        custom.write_text(textwrap.dedent('''\
            from typing import Protocol

            class NetworkDriver(Protocol):
                def get_widgets(self, count: int = 10) -> list:
                    """Get widgets from the device."""
                    ...

                def not_a_getter(self) -> None:
                    """Should be ignored."""
                    ...
        '''))
        getters = extract_getters_from_protocol(custom)
        assert len(getters) == 1
        assert getters[0]["name"] == "get_widgets"
        assert getters[0]["params"] == [("count", "int", "10")]

    def test_empty_protocol_file(self, tmp_path):
        """Test with a protocol file that has no getters."""
        custom = tmp_path / "empty.py"
        custom.write_text(textwrap.dedent('''\
            from typing import Protocol

            class NetworkDriver(Protocol):
                def connect(self) -> None:
                    ...
        '''))
        getters = extract_getters_from_protocol(custom)
        assert getters == []


# --- _make_tool_name ---


class TestMakeToolName:
    def test_net_prefix(self):
        assert _make_tool_name("get_facts", "net") == "net_get_facts"

    def test_vendor_prefix(self):
        assert _make_tool_name("get_bgp_summary", "eos") == "eos_get_bgp_summary"

    def test_nxos_prefix(self):
        assert _make_tool_name("get_vlans", "nxos") == "nxos_get_vlans"


# --- _build_signature ---


class TestBuildSignature:
    def test_no_params(self):
        assert _build_signature([]) == "host: str"

    def test_one_param_with_default(self):
        params = [("vrf", "str", "'default'")]
        assert _build_signature(params) == "host: str, vrf: str = 'default'"

    def test_param_without_default(self):
        params = [("section", "str", None)]
        assert _build_signature(params) == "host: str, section: str"

    def test_multiple_params(self):
        params = [
            ("protocol", "str", "''"),
            ("vrf", "str", "'default'"),
            ("limit", "int", "100"),
        ]
        result = _build_signature(params)
        assert result == "host: str, protocol: str = '', vrf: str = 'default', limit: int = 100"


# --- _build_call_args ---


class TestBuildCallArgs:
    def test_no_params(self):
        assert _build_call_args([]) == ""

    def test_one_param(self):
        params = [("vrf", "str", "'default'")]
        assert _build_call_args(params) == "vrf=vrf"

    def test_multiple_params(self):
        params = [("protocol", "str", "''"), ("vrf", "str", "'default'"), ("limit", "int", "100")]
        assert _build_call_args(params) == "protocol=protocol, vrf=vrf, limit=limit"


# --- generate_tool_stub ---


class TestGenerateToolStub:
    def test_stub_is_valid_python(self):
        """Generated stub must parse as valid Python."""
        getter = {
            "name": "get_facts",
            "params": [],
            "docstring": "Get device facts.",
            "first_line": "Get device facts.",
            "returns_doc": "",
        }
        code = generate_tool_stub(getter, "net")
        # Wrap in a module context so decorators are parseable
        module_code = (
            "from network_mcp.helpers import READ_ONLY, handle_tool_errors\n"
            "from network_mcp.server import conn_mgr, mcp\n"
            + code
        )
        tree = ast.parse(module_code)
        funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert len(funcs) == 1
        assert funcs[0].name == "net_get_facts"

    def test_stub_includes_host_param(self):
        getter = {
            "name": "get_vlans",
            "params": [],
            "docstring": "Get VLANs.",
            "first_line": "Get VLANs.",
            "returns_doc": "",
        }
        code = generate_tool_stub(getter, "net")
        assert "host: str" in code

    def test_stub_has_decorators(self):
        getter = {
            "name": "get_facts",
            "params": [],
            "docstring": "Get device facts.",
            "first_line": "Get device facts.",
            "returns_doc": "",
        }
        code = generate_tool_stub(getter, "net")
        assert "@mcp.tool(annotations=READ_ONLY)" in code
        assert "@handle_tool_errors" in code

    def test_stub_calls_driver_getter(self):
        getter = {
            "name": "get_bgp_summary",
            "params": [("vrf", "str", "'default'")],
            "docstring": "Get BGP summary.\n\nArgs:\n    vrf: VRF name to query. Defaults to 'default'.",
            "first_line": "Get BGP summary.",
            "returns_doc": "",
        }
        code = generate_tool_stub(getter, "net")
        assert "driver.get_bgp_summary(vrf=vrf)" in code

    def test_stub_returns_structured_dict(self):
        getter = {
            "name": "get_facts",
            "params": [],
            "docstring": "Get device facts.",
            "first_line": "Get device facts.",
            "returns_doc": "",
        }
        code = generate_tool_stub(getter, "net")
        assert '"status": "success"' in code
        assert '"device": host' in code
        assert '"vendor": driver.vendor' in code
        assert '"platform": driver.platform' in code
        assert '"data": data' in code

    def test_stub_with_vendor_prefix(self):
        getter = {
            "name": "get_interfaces",
            "params": [],
            "docstring": "Get interfaces.",
            "first_line": "Get interfaces.",
            "returns_doc": "",
        }
        code = generate_tool_stub(getter, "eos")
        assert "def eos_get_interfaces" in code


# --- generate_module ---


class TestGenerateModule:
    def test_full_module_is_valid_python(self):
        """The full generated module must parse as valid Python."""
        getters = extract_getters_from_protocol()
        code = generate_module(getters, prefix="net")
        ast.parse(code)  # Raises SyntaxError if invalid

    def test_full_module_eos_prefix(self):
        """Module with eos prefix is valid Python."""
        getters = extract_getters_from_protocol()
        code = generate_module(getters, prefix="eos")
        ast.parse(code)

    def test_module_has_header(self):
        getters = extract_getters_from_protocol()
        code = generate_module(getters, prefix="net")
        assert "Auto-generated vendor tool stubs" in code
        assert "from network_mcp.helpers import READ_ONLY, handle_tool_errors" in code
        assert "from network_mcp.server import conn_mgr, mcp" in code

    def test_module_has_all_getters(self):
        getters = extract_getters_from_protocol()
        code = generate_module(getters, prefix="net")
        for g in getters:
            tool_name = f"net_{g['name']}"
            assert f"def {tool_name}" in code

    def test_module_no_print_statements(self):
        """Generated code must never use print() (MCP rule)."""
        getters = extract_getters_from_protocol()
        code = generate_module(getters, prefix="net")
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", "Generated code contains print()"


# --- list_getters ---


class TestListGetters:
    def test_list_output(self):
        getters = extract_getters_from_protocol()
        output = list_getters(getters)
        assert "get_facts" in output
        assert "get_bgp_summary" in output
        assert f"Found {len(getters)}" in output


# --- CLI (main) ---


class TestCLI:
    def test_list_mode(self, capsys):
        rc = main(["--list"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "get_facts" in captured.out

    def test_stdout_output(self, capsys):
        rc = main([])
        assert rc == 0
        captured = capsys.readouterr()
        assert "net_get_facts" in captured.out
        # Verify it's valid Python
        ast.parse(captured.out)

    def test_file_output(self, tmp_path):
        outfile = tmp_path / "generated.py"
        rc = main(["-o", str(outfile)])
        assert rc == 0
        assert outfile.exists()
        code = outfile.read_text()
        ast.parse(code)
        assert "net_get_facts" in code

    def test_custom_prefix(self, capsys):
        rc = main(["--prefix", "junos"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "junos_get_facts" in captured.out
        ast.parse(captured.out)

    def test_custom_protocol_file(self, tmp_path, capsys):
        proto = tmp_path / "custom_proto.py"
        proto.write_text(textwrap.dedent('''\
            from typing import Protocol

            class NetworkDriver(Protocol):
                def get_custom_data(self) -> dict:
                    """Get custom data from the device."""
                    ...
        '''))
        rc = main(["--protocol-file", str(proto)])
        assert rc == 0
        captured = capsys.readouterr()
        assert "net_get_custom_data" in captured.out
        ast.parse(captured.out)

    def test_empty_protocol_returns_error(self, tmp_path):
        proto = tmp_path / "empty.py"
        proto.write_text("class NetworkDriver:\n    pass\n")
        rc = main(["--protocol-file", str(proto)])
        assert rc == 1
