"""Tests for multi-tenancy device isolation via TenantDeviceFilter."""

from network_mcp.rbac import TenantDeviceFilter


class TestTenantDeviceFilter:
    """Unit tests for TenantDeviceFilter.is_allowed()."""

    def _make_inventory(self, device_tenants: dict[str, list[str]]) -> dict[str, dict]:
        """Helper: build a minimal inventory dict from {device_name: tenants_list}."""
        return {name: {"tenants": tenants} for name, tenants in device_tenants.items()}

    def test_tenant_allowed_on_assigned_device(self):
        """Tenant A can access a device with tenants: ['A']."""
        inv = self._make_inventory({"spine-01": ["A"]})
        f = TenantDeviceFilter(inv)
        assert f.is_allowed("spine-01", "A") is True

    def test_tenant_denied_on_other_device(self):
        """Tenant A cannot access a device with tenants: ['B']."""
        inv = self._make_inventory({"spine-01": ["B"]})
        f = TenantDeviceFilter(inv)
        assert f.is_allowed("spine-01", "A") is False

    def test_tenant_allowed_on_shared_device(self):
        """Tenant A can access a device with tenants: ['A', 'B']."""
        inv = self._make_inventory({"leaf-01": ["A", "B"]})
        f = TenantDeviceFilter(inv)
        assert f.is_allowed("leaf-01", "A") is True

    def test_tenant_allowed_on_unrestricted_device(self):
        """Tenant A can access a device with tenants: [] (unrestricted)."""
        inv = self._make_inventory({"core-01": []})
        f = TenantDeviceFilter(inv)
        assert f.is_allowed("core-01", "A") is True

    def test_no_tenant_id_allows_full_access(self):
        """No tenant_id (None) → full access to all devices (backward compat)."""
        inv = self._make_inventory({"spine-01": ["B"]})
        f = TenantDeviceFilter(inv)
        assert f.is_allowed("spine-01", None) is True

    def test_device_not_in_inventory_allowed(self):
        """Device not in inventory → allow (don't block dynamic hosts)."""
        inv = self._make_inventory({"spine-01": ["A"]})
        f = TenantDeviceFilter(inv)
        assert f.is_allowed("unknown-device", "A") is True

    def test_empty_inventory(self):
        """Empty inventory → all devices allowed."""
        f = TenantDeviceFilter({})
        assert f.is_allowed("any-device", "A") is True

    def test_multiple_devices_mixed_tenants(self):
        """Multiple devices with different tenant assignments."""
        inv = self._make_inventory(
            {
                "spine-01": ["acme-corp"],
                "leaf-01": ["acme-corp", "beta-inc"],
                "core-01": [],
            }
        )
        f = TenantDeviceFilter(inv)

        # acme-corp
        assert f.is_allowed("spine-01", "acme-corp") is True
        assert f.is_allowed("leaf-01", "acme-corp") is True
        assert f.is_allowed("core-01", "acme-corp") is True

        # beta-inc
        assert f.is_allowed("spine-01", "beta-inc") is False
        assert f.is_allowed("leaf-01", "beta-inc") is True
        assert f.is_allowed("core-01", "beta-inc") is True

    def test_filter_with_device_credentials_objects(self):
        """TenantDeviceFilter works with DeviceCredentials model objects."""
        from pydantic import SecretStr

        from network_mcp.connection import DeviceCredentials

        inv = {
            "spine-01": DeviceCredentials(
                host="10.0.0.1",
                password=SecretStr(""),
                tenants=["acme-corp"],
            ),
            "leaf-01": DeviceCredentials(
                host="10.0.0.2",
                password=SecretStr(""),
                tenants=[],
            ),
        }
        f = TenantDeviceFilter(inv)
        assert f.is_allowed("spine-01", "acme-corp") is True
        assert f.is_allowed("spine-01", "other") is False
        assert f.is_allowed("leaf-01", "other") is True

    def test_missing_tenants_key_in_dict(self):
        """Device dict without 'tenants' key treated as unrestricted."""
        inv = {"spine-01": {"host": "10.0.0.1"}}
        f = TenantDeviceFilter(inv)
        assert f.is_allowed("spine-01", "any-tenant") is True

    def test_empty_string_tenant_id_treated_as_no_tenant(self):
        """Empty string tenant_id treated as no tenant (backward compat)."""
        inv = self._make_inventory({"spine-01": ["A"]})
        f = TenantDeviceFilter(inv)
        assert f.is_allowed("spine-01", "") is True


class TestTenantIsolationConfig:
    """Tests for tenant isolation configuration."""

    def test_tenant_isolation_disabled_by_default(self):
        """TENANT_ISOLATION_ENABLED defaults to False."""
        from network_mcp.config import NetworkSettings

        s = NetworkSettings(net_password="")
        assert s.tenant_isolation_enabled is False

    def test_tenant_isolation_enabled_via_env(self, monkeypatch):
        """TENANT_ISOLATION_ENABLED=true enables tenant isolation."""
        monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "true")
        from network_mcp.config import NetworkSettings

        s = NetworkSettings(net_password="")
        assert s.tenant_isolation_enabled is True


class TestExtractTenantIdIntegration:
    """Tests for extract_tenant_id used alongside TenantDeviceFilter."""

    def test_extract_then_filter(self):
        """End-to-end: extract tenant from JWT claims and check device access."""
        from network_mcp.rbac import extract_tenant_id

        inv = {"spine-01": {"tenants": ["acme.com"]}}
        f = TenantDeviceFilter(inv)

        # Email-based sub claim → domain = acme.com
        claims = {"sub": "user@acme.com"}
        tenant_id = extract_tenant_id(claims)
        assert tenant_id == "acme.com"
        assert f.is_allowed("spine-01", tenant_id) is True

        # Different domain → denied
        claims_other = {"sub": "user@other.com"}
        tenant_id_other = extract_tenant_id(claims_other)
        assert f.is_allowed("spine-01", tenant_id_other) is False

    def test_explicit_tenant_id_claim(self):
        """Explicit tenant_id JWT claim used for filtering."""
        from network_mcp.rbac import extract_tenant_id

        inv = {"leaf-01": {"tenants": ["beta-inc"]}}
        f = TenantDeviceFilter(inv)

        claims = {"tenant_id": "beta-inc"}
        tenant_id = extract_tenant_id(claims)
        assert f.is_allowed("leaf-01", tenant_id) is True

    def test_org_id_claim(self):
        """Auth0 org_id JWT claim used for filtering."""
        from network_mcp.rbac import extract_tenant_id

        inv = {"core-01": {"tenants": ["org_abc123"]}}
        f = TenantDeviceFilter(inv)

        claims = {"org_id": "org_abc123"}
        tenant_id = extract_tenant_id(claims)
        assert f.is_allowed("core-01", tenant_id) is True
