"""Tests for the interactive tool playground REPL."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.playground import (
    _call_tool,
    _coerce_value,
    _describe_tool,
    _format_result,
    _list_tools,
    _parse_args,
    _print_help,
    _tokenize,
    run_repl,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(fn, description: str = "A test tool.") -> MagicMock:
    """Create a mock tool object that mimics FastMCP's Tool."""
    tool = MagicMock()
    tool.fn = fn
    tool.description = description
    return tool


def _make_input(responses: list[str]):
    """Return an input() mock that yields *responses* in sequence."""
    it = iter(responses)

    def _mock_input(prompt: str = "") -> str:  # noqa: ARG001
        return next(it)

    return _mock_input


# ---------------------------------------------------------------------------
# _coerce_value
# ---------------------------------------------------------------------------


class TestCoerceValue:
    def test_string_quoted_double(self):
        assert _coerce_value('"hello"') == "hello"

    def test_string_quoted_single(self):
        assert _coerce_value("'world'") == "world"

    def test_boolean_true(self):
        assert _coerce_value("true") is True
        assert _coerce_value("True") is True

    def test_boolean_false(self):
        assert _coerce_value("false") is False

    def test_none(self):
        assert _coerce_value("none") is None
        assert _coerce_value("None") is None

    def test_integer(self):
        assert _coerce_value("42") == 42
        assert _coerce_value("-7") == -7

    def test_float(self):
        assert _coerce_value("3.14") == 3.14

    def test_json_list(self):
        assert _coerce_value("[1,2,3]") == [1, 2, 3]

    def test_json_dict(self):
        assert _coerce_value('{"a":1}') == {"a": 1}

    def test_plain_string(self):
        assert _coerce_value("spine-01") == "spine-01"

    def test_invalid_json_fallback(self):
        assert _coerce_value("[not json") == "[not json"


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_simple_tokens(self):
        assert _tokenize("a=1 b=2") == ["a=1", "b=2"]

    def test_quoted_value(self):
        assert _tokenize('host="spine 01" vlan=10') == ['host="spine 01"', "vlan=10"]

    def test_single_quoted(self):
        assert _tokenize("name='my vlan'") == ["name='my vlan'"]

    def test_empty(self):
        assert _tokenize("") == []


# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_simple_kwargs(self):
        result = _parse_args("host=spine-01 vlan_id=100")
        assert result == {"host": "spine-01", "vlan_id": 100}

    def test_empty_string(self):
        assert _parse_args("") == {}
        assert _parse_args("  ") == {}

    def test_missing_equals(self):
        with pytest.raises(ValueError, match="Invalid argument"):
            _parse_args("badarg")

    def test_empty_key(self):
        with pytest.raises(ValueError, match="Empty key"):
            _parse_args("=value")

    def test_quoted_value(self):
        result = _parse_args('name="my vlan"')
        assert result == {"name": "my vlan"}

    def test_boolean_arg(self):
        result = _parse_args("verbose=true")
        assert result == {"verbose": True}


# ---------------------------------------------------------------------------
# _format_result
# ---------------------------------------------------------------------------


class TestFormatResult:
    def test_dict(self):
        result = _format_result({"status": "success"})
        assert '"status": "success"' in result

    def test_list(self):
        result = _format_result([1, 2, 3])
        assert "1" in result and "3" in result

    def test_plain_string(self):
        assert _format_result("hello") == "hello"

    def test_number(self):
        assert _format_result(42) == "42"


# ---------------------------------------------------------------------------
# _print_help
# ---------------------------------------------------------------------------


def test_print_help(capsys):
    tools = {"tool_a": _make_tool(lambda: None), "tool_b": _make_tool(lambda: None)}
    _print_help(tools)
    output = capsys.readouterr().out
    assert "Commands:" in output
    assert "2 tools available" in output


# ---------------------------------------------------------------------------
# _list_tools
# ---------------------------------------------------------------------------


def test_list_tools(capsys):
    tools = {"alpha": _make_tool(lambda: None), "beta": _make_tool(lambda: None)}
    _list_tools(tools)
    output = capsys.readouterr().out
    assert "alpha" in output
    assert "beta" in output
    assert "2 tools total" in output


def test_list_tools_empty(capsys):
    _list_tools({})
    output = capsys.readouterr().out
    assert "No tools registered" in output


# ---------------------------------------------------------------------------
# _describe_tool
# ---------------------------------------------------------------------------


def test_describe_tool(capsys):
    def my_tool(host: str, vlan_id: int = 10) -> dict:
        pass

    tools = {"my_tool": _make_tool(my_tool, "Get VLANs from device.")}
    _describe_tool(tools, "my_tool")
    output = capsys.readouterr().out
    assert "my_tool" in output
    assert "Get VLANs from device." in output
    assert "host" in output
    assert "vlan_id" in output


def test_describe_unknown_tool(capsys):
    _describe_tool({}, "nonexistent")
    output = capsys.readouterr().out
    assert "Unknown tool" in output


# ---------------------------------------------------------------------------
# _call_tool
# ---------------------------------------------------------------------------


def test_call_tool_sync(capsys):
    def add(a: int, b: int) -> dict:
        return {"status": "success", "result": a + b}

    tools = {"add": _make_tool(add)}
    _call_tool(tools, "add", {"a": 2, "b": 3})
    output = capsys.readouterr().out
    assert '"result": 5' in output


def test_call_tool_async(capsys):
    async def async_tool(host: str) -> dict:
        return {"status": "success", "device": host}

    tools = {"async_tool": _make_tool(async_tool)}
    _call_tool(tools, "async_tool", {"host": "spine-01"})
    output = capsys.readouterr().out
    assert '"device": "spine-01"' in output


def test_call_tool_unknown(capsys):
    _call_tool({}, "missing_tool", {})
    output = capsys.readouterr().out
    assert "Unknown tool" in output


def test_call_tool_type_error(capsys):
    def strict(host: str) -> dict:
        return {"status": "success"}

    tools = {"strict": _make_tool(strict)}
    _call_tool(tools, "strict", {"bad_arg": 1})
    output = capsys.readouterr().out
    assert "Error:" in output
    assert "describe strict" in output


def test_call_tool_runtime_error(capsys):
    def failing(host: str) -> dict:
        raise RuntimeError("device unreachable")

    tools = {"failing": _make_tool(failing)}
    _call_tool(tools, "failing", {"host": "spine-01"})
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert "device unreachable" in output


# ---------------------------------------------------------------------------
# run_repl
# ---------------------------------------------------------------------------


class TestRunRepl:
    @patch("network_mcp.playground._get_tools")
    def test_help_command(self, mock_get_tools, capsys, monkeypatch):
        mock_get_tools.return_value = {"t1": _make_tool(lambda: None)}
        monkeypatch.setattr("builtins.input", _make_input(["help", "exit"]))
        code = run_repl()
        assert code == 0
        output = capsys.readouterr().out
        assert "Commands:" in output

    @patch("network_mcp.playground._get_tools")
    def test_question_mark_help(self, mock_get_tools, capsys, monkeypatch):
        mock_get_tools.return_value = {}
        monkeypatch.setattr("builtins.input", _make_input(["?", "quit"]))
        code = run_repl()
        assert code == 0
        output = capsys.readouterr().out
        assert "Commands:" in output

    @patch("network_mcp.playground._get_tools")
    def test_list_command(self, mock_get_tools, capsys, monkeypatch):
        mock_get_tools.return_value = {"tool_a": _make_tool(lambda: None)}
        monkeypatch.setattr("builtins.input", _make_input(["list", "exit"]))
        run_repl()
        output = capsys.readouterr().out
        assert "tool_a" in output

    @patch("network_mcp.playground._get_tools")
    def test_tools_command(self, mock_get_tools, capsys, monkeypatch):
        mock_get_tools.return_value = {"tool_a": _make_tool(lambda: None)}
        monkeypatch.setattr("builtins.input", _make_input(["tools", "exit"]))
        run_repl()
        output = capsys.readouterr().out
        assert "tool_a" in output

    @patch("network_mcp.playground._get_tools")
    def test_describe_command(self, mock_get_tools, capsys, monkeypatch):
        def my_fn(host: str) -> dict:
            pass

        mock_get_tools.return_value = {"my_fn": _make_tool(my_fn, "My description.")}
        monkeypatch.setattr("builtins.input", _make_input(["describe my_fn", "exit"]))
        run_repl()
        output = capsys.readouterr().out
        assert "My description." in output

    @patch("network_mcp.playground._get_tools")
    def test_describe_no_arg(self, mock_get_tools, capsys, monkeypatch):
        mock_get_tools.return_value = {}
        monkeypatch.setattr("builtins.input", _make_input(["describe", "exit"]))
        run_repl()
        output = capsys.readouterr().out
        assert "Usage:" in output

    @patch("network_mcp.playground._get_tools")
    def test_tool_invocation(self, mock_get_tools, capsys, monkeypatch):
        def echo(msg: str) -> dict:
            return {"status": "success", "msg": msg}

        mock_get_tools.return_value = {"echo": _make_tool(echo)}
        monkeypatch.setattr("builtins.input", _make_input(["echo msg=hello", "exit"]))
        run_repl()
        output = capsys.readouterr().out
        assert '"msg": "hello"' in output

    @patch("network_mcp.playground._get_tools")
    def test_empty_line_skipped(self, mock_get_tools, monkeypatch):
        mock_get_tools.return_value = {}
        monkeypatch.setattr("builtins.input", _make_input(["", "exit"]))
        code = run_repl()
        assert code == 0

    @patch("network_mcp.playground._get_tools")
    def test_eof_exits(self, mock_get_tools, capsys, monkeypatch):
        mock_get_tools.return_value = {}
        monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
        code = run_repl()
        assert code == 0
        output = capsys.readouterr().out
        assert "Goodbye!" in output

    @patch("network_mcp.playground._get_tools")
    def test_keyboard_interrupt_exits(self, mock_get_tools, capsys, monkeypatch):
        mock_get_tools.return_value = {}
        monkeypatch.setattr("builtins.input", MagicMock(side_effect=KeyboardInterrupt))
        code = run_repl()
        assert code == 0

    @patch("network_mcp.playground._get_tools")
    def test_parse_error_displayed(self, mock_get_tools, capsys, monkeypatch):
        mock_get_tools.return_value = {"t": _make_tool(lambda: None)}
        monkeypatch.setattr("builtins.input", _make_input(["t badarg", "exit"]))
        run_repl()
        output = capsys.readouterr().out
        assert "Parse error" in output


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


class TestCLIDispatch:
    def test_playground_subcommand(self, monkeypatch):
        """``latticio playground`` dispatches to playground.main()."""
        monkeypatch.setattr("sys.argv", ["latticio", "playground"])
        with patch("network_mcp.playground.main") as mock_main:
            mock_main.side_effect = SystemExit(0)
            with pytest.raises(SystemExit):
                from network_mcp.cli import main

                main()
            mock_main.assert_called_once()

    def test_repl_subcommand(self, monkeypatch):
        """``latticio repl`` dispatches to playground.main()."""
        monkeypatch.setattr("sys.argv", ["latticio", "repl"])
        with patch("network_mcp.playground.main") as mock_main:
            mock_main.side_effect = SystemExit(0)
            with pytest.raises(SystemExit):
                from network_mcp.cli import main

                main()
            mock_main.assert_called_once()


# ---------------------------------------------------------------------------
# _setup_readline
# ---------------------------------------------------------------------------


def test_setup_readline_with_readline():
    """Tab completion is configured when readline is available."""
    from network_mcp.playground import _setup_readline

    tools = {"tool_a": _make_tool(lambda: None), "tool_b": _make_tool(lambda: None)}
    # Should not raise even if readline is unavailable
    _setup_readline(tools)


def test_setup_readline_without_readline():
    """Gracefully handles missing readline."""
    from network_mcp.playground import _setup_readline

    with patch.dict("sys.modules", {"readline": None}):
        # Should not raise
        _setup_readline({})
