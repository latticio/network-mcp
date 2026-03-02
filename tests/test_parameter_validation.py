"""Tests for parameter validation functions added in helpers.py."""

from network_mcp.helpers import (
    validate_acl_name,
    validate_bgp_asn,
    validate_community_string,
    validate_description,
    validate_vlan_name,
    validate_vrf_name,
)

# ---------------------------------------------------------------------------
# validate_vlan_name
# ---------------------------------------------------------------------------


class TestValidateVlanName:
    def test_valid_simple_name(self):
        assert validate_vlan_name("SERVERS") is None

    def test_valid_with_hyphens(self):
        assert validate_vlan_name("web-servers") is None

    def test_valid_with_underscores(self):
        assert validate_vlan_name("vlan_100") is None

    def test_valid_with_spaces(self):
        assert validate_vlan_name("web servers") is None

    def test_valid_mixed(self):
        assert validate_vlan_name("VLAN-100_prod") is None

    def test_valid_numeric(self):
        assert validate_vlan_name("100") is None

    def test_valid_max_length(self):
        assert validate_vlan_name("a" * 32) is None

    def test_empty_fails(self):
        err = validate_vlan_name("")
        assert err is not None
        assert "empty" in err.lower()

    def test_whitespace_only_fails(self):
        err = validate_vlan_name("   ")
        assert err is not None
        assert "empty" in err.lower()

    def test_too_long_fails(self):
        err = validate_vlan_name("a" * 33)
        assert err is not None
        assert "32" in err

    def test_special_chars_fail(self):
        assert validate_vlan_name("vlan@100") is not None
        assert validate_vlan_name("vlan#100") is not None
        assert validate_vlan_name("vlan;drop") is not None
        assert validate_vlan_name("vlan$100") is not None

    def test_newline_fails(self):
        assert validate_vlan_name("vlan\n100") is not None

    def test_semicolon_fails(self):
        assert validate_vlan_name("vlan;100") is not None


# ---------------------------------------------------------------------------
# validate_description
# ---------------------------------------------------------------------------


class TestValidateDescription:
    def test_valid_simple(self):
        assert validate_description("Uplink to spine-01") is None

    def test_valid_with_special_chars(self):
        assert validate_description("Port for server-rack #3 (primary)") is None

    def test_valid_max_length(self):
        assert validate_description("x" * 240) is None

    def test_empty_fails(self):
        err = validate_description("")
        assert err is not None
        assert "empty" in err.lower()

    def test_too_long_fails(self):
        err = validate_description("x" * 241)
        assert err is not None
        assert "240" in err

    def test_control_chars_fail(self):
        assert validate_description("test\x00desc") is not None
        assert validate_description("test\x01desc") is not None
        assert validate_description("test\x1fdesc") is not None

    def test_tab_fails(self):
        # Tab is a control character (\x09)
        assert validate_description("test\tdesc") is not None

    def test_newline_fails(self):
        # Newline is a control character (\x0a)
        assert validate_description("test\ndesc") is not None


# ---------------------------------------------------------------------------
# validate_vrf_name
# ---------------------------------------------------------------------------


class TestValidateVrfName:
    def test_valid_simple(self):
        assert validate_vrf_name("MGMT") is None

    def test_valid_with_hyphens(self):
        assert validate_vrf_name("vrf-production") is None

    def test_valid_with_underscores(self):
        assert validate_vrf_name("vrf_mgmt") is None

    def test_valid_with_colons(self):
        assert validate_vrf_name("tenant:prod") is None

    def test_valid_max_length(self):
        assert validate_vrf_name("a" * 32) is None

    def test_empty_fails(self):
        err = validate_vrf_name("")
        assert err is not None
        assert "empty" in err.lower()

    def test_whitespace_only_fails(self):
        err = validate_vrf_name("   ")
        assert err is not None
        assert "empty" in err.lower()

    def test_spaces_fail(self):
        err = validate_vrf_name("vrf name")
        assert err is not None
        assert "no spaces" in err.lower()

    def test_too_long_fails(self):
        err = validate_vrf_name("a" * 33)
        assert err is not None
        assert "32" in err

    def test_special_chars_fail(self):
        assert validate_vrf_name("vrf@prod") is not None
        assert validate_vrf_name("vrf;drop") is not None


# ---------------------------------------------------------------------------
# validate_acl_name
# ---------------------------------------------------------------------------


class TestValidateAclName:
    def test_valid_simple(self):
        assert validate_acl_name("PERMIT_ALL") is None

    def test_valid_with_hyphens(self):
        assert validate_acl_name("deny-list") is None

    def test_valid_numeric(self):
        assert validate_acl_name("100") is None

    def test_valid_max_length(self):
        assert validate_acl_name("a" * 100) is None

    def test_empty_fails(self):
        err = validate_acl_name("")
        assert err is not None
        assert "empty" in err.lower()

    def test_spaces_fail(self):
        err = validate_acl_name("my acl")
        assert err is not None
        assert "no spaces" in err.lower()

    def test_too_long_fails(self):
        err = validate_acl_name("a" * 101)
        assert err is not None
        assert "100" in err

    def test_special_chars_fail(self):
        assert validate_acl_name("acl@1") is not None
        assert validate_acl_name("acl;drop") is not None
        assert validate_acl_name("acl#1") is not None


# ---------------------------------------------------------------------------
# validate_bgp_asn
# ---------------------------------------------------------------------------


class TestValidateBgpAsn:
    def test_valid_min(self):
        assert validate_bgp_asn(1) is None

    def test_valid_private_range(self):
        assert validate_bgp_asn(65535) is None

    def test_valid_32bit_max(self):
        assert validate_bgp_asn(4294967295) is None

    def test_valid_4byte(self):
        assert validate_bgp_asn(65536) is None

    def test_zero_fails(self):
        err = validate_bgp_asn(0)
        assert err is not None
        assert "between 1 and 4294967295" in err

    def test_negative_fails(self):
        err = validate_bgp_asn(-1)
        assert err is not None

    def test_over_max_fails(self):
        err = validate_bgp_asn(4294967296)
        assert err is not None

    def test_bool_fails(self):
        err = validate_bgp_asn(True)
        assert err is not None
        assert "integer" in err.lower()


# ---------------------------------------------------------------------------
# validate_community_string
# ---------------------------------------------------------------------------


class TestValidateCommunityString:
    def test_valid_simple(self):
        assert validate_community_string("public") is None

    def test_valid_complex(self):
        assert validate_community_string("Pr1v@te!") is None

    def test_valid_max_length(self):
        assert validate_community_string("a" * 32) is None

    def test_empty_fails(self):
        err = validate_community_string("")
        assert err is not None
        assert "empty" in err.lower()

    def test_spaces_fail(self):
        err = validate_community_string("my community")
        assert err is not None
        assert "space" in err.lower()

    def test_too_long_fails(self):
        err = validate_community_string("a" * 33)
        assert err is not None
        assert "32" in err

    def test_non_printable_fails(self):
        err = validate_community_string("test\x01string")
        assert err is not None
        assert "printable" in err.lower()

    def test_tab_fails(self):
        err = validate_community_string("test\tstring")
        assert err is not None
