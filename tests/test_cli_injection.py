"""Tests for CLI injection prevention: $(), ${}, and existing dangerous chars."""

from network_mcp.helpers import validate_cli_param


class TestDollarSubstitutionBlocked:
    """$(...) and ${...} command substitution must be rejected."""

    def test_reject_dollar_paren_substitution(self):
        result = validate_cli_param("$(cat /etc/passwd)")
        assert result is not None
        assert "invalid characters" in result

    def test_reject_dollar_brace_substitution(self):
        result = validate_cli_param("${IFS}")
        assert result is not None
        assert "invalid characters" in result

    def test_reject_dollar_paren_in_middle(self):
        result = validate_cli_param("vlan$(id)name")
        assert result is not None

    def test_reject_dollar_brace_in_middle(self):
        result = validate_cli_param("prefix${HOME}suffix")
        assert result is not None

    def test_reject_nested_dollar_paren(self):
        result = validate_cli_param("$($(whoami))")
        assert result is not None

    def test_allow_normal_dollar_in_description(self):
        """$100 in a VLAN name description is OK — not followed by ( or {."""
        result = validate_cli_param("cost-$100")
        assert result is None

    def test_allow_plain_dollar_sign(self):
        """A lone $ at end of string is OK."""
        result = validate_cli_param("price$")
        assert result is None

    def test_allow_dollar_with_letter(self):
        """$VAR (without braces) is OK — only $( and ${ are dangerous."""
        result = validate_cli_param("$VAR")
        assert result is None


class TestExistingDangerousCharsStillBlocked:
    """Existing injection vectors remain blocked after the regex update."""

    def test_semicolon(self):
        assert validate_cli_param("cmd;evil") is not None

    def test_pipe(self):
        assert validate_cli_param("cmd|pipe") is not None

    def test_backtick(self):
        assert validate_cli_param("cmd`tick`") is not None

    def test_exclamation(self):
        assert validate_cli_param("cmd!bang") is not None

    def test_newline(self):
        assert validate_cli_param("cmd\nevil") is not None

    def test_carriage_return(self):
        assert validate_cli_param("cmd\revil") is not None

    def test_null_byte(self):
        assert validate_cli_param("cmd\x00evil") is not None


class TestSafeInputsAllowed:
    """Normal CLI parameters pass validation."""

    def test_simple_interface(self):
        assert validate_cli_param("Ethernet1") is None

    def test_ip_address(self):
        assert validate_cli_param("10.0.0.1") is None

    def test_vlan_name(self):
        assert validate_cli_param("SERVERS-100") is None

    def test_description_with_spaces(self):
        assert validate_cli_param("to spine-01 uplink") is None

    def test_bgp_community(self):
        assert validate_cli_param("65000:100") is None
