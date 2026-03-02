"""Tests for config version tracking (optimistic concurrency).

Validates that:
- net_get_config_version returns SHA-256 hash
- Same config produces same hash (deterministic)
- Different config produces different hash
- net_push_config with matching expected_version succeeds
- net_push_config with mismatching expected_version returns concurrent change error
- net_push_config without expected_version skips version check
- Config version updated after successful push
"""

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.server import conn_mgr

MOCK_RUNNING_CONFIG_A = """\
hostname spine-01
interface Ethernet1
   description link-to-leaf-01
   no shutdown
end
"""

MOCK_RUNNING_CONFIG_B = """\
hostname spine-01
interface Ethernet1
   description link-to-leaf-01-UPDATED
   no shutdown
interface Ethernet2
   description new-link
end
"""


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_config.return_value = {
        "running": MOCK_RUNNING_CONFIG_A,
        "startup": MOCK_RUNNING_CONFIG_A,
    }
    driver.run_config.return_value = []
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return mock driver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


@pytest.fixture(autouse=True)
def _reset_config_versions():
    """Clear config versions cache before each test."""
    from network_mcp.tools.common import config

    config._config_versions.clear()
    yield
    config._config_versions.clear()


class TestNetGetConfigVersion:
    """Tests for net_get_config_version tool."""

    def test_returns_sha256_hash(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_config_version

        result = net_get_config_version("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["data"]["algorithm"] == "sha256"
        # Verify it's a valid SHA-256 hex string (64 chars)
        version = result["data"]["version"]
        assert len(version) == 64
        assert all(c in "0123456789abcdef" for c in version)

    def test_correct_hash_value(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_config_version

        result = net_get_config_version("spine-01")
        expected_hash = hashlib.sha256(MOCK_RUNNING_CONFIG_A.encode("utf-8")).hexdigest()
        assert result["data"]["version"] == expected_hash

    def test_same_config_same_hash(self, mock_common_conn_mgr, mock_driver):
        """Deterministic: same config always produces same hash."""
        from network_mcp.tools.common.config import net_get_config_version

        result1 = net_get_config_version("spine-01")
        result2 = net_get_config_version("spine-01")
        assert result1["data"]["version"] == result2["data"]["version"]

    def test_different_config_different_hash(self, mock_common_conn_mgr, mock_driver):
        """Different configs must produce different hashes."""
        from network_mcp.tools.common.config import net_get_config_version

        result1 = net_get_config_version("spine-01")

        # Change the config
        mock_driver.get_config.return_value = {
            "running": MOCK_RUNNING_CONFIG_B,
            "startup": MOCK_RUNNING_CONFIG_B,
        }
        result2 = net_get_config_version("spine-01")

        assert result1["data"]["version"] != result2["data"]["version"]

    def test_stores_version_in_cache(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import _config_versions, net_get_config_version

        assert "spine-01" not in _config_versions
        net_get_config_version("spine-01")
        assert "spine-01" in _config_versions

    def test_response_includes_vendor_platform(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_config_version

        result = net_get_config_version("spine-01")
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"

    def test_not_supported_error(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.drivers.base import NotSupportedError
        from network_mcp.tools.common.config import net_get_config_version

        mock_driver.get_config.side_effect = NotSupportedError("not supported")
        result = net_get_config_version("spine-01")
        assert result["status"] == "not_supported"


class TestNetPushConfigOptimisticConcurrency:
    """Tests for optimistic concurrency in net_push_config."""

    def test_matching_version_succeeds(self, mock_common_conn_mgr, mock_driver):
        """Push succeeds when expected_version matches current config."""
        expected = hashlib.sha256(MOCK_RUNNING_CONFIG_A.encode("utf-8")).hexdigest()

        with patch("network_mcp.tools.common.config.check_read_only", return_value=None):
            from network_mcp.tools.common.config import net_push_config

            result = net_push_config("spine-01", ["vlan 100", "name SERVERS"], expected_version=expected)

        assert result["status"] == "success"
        mock_driver.run_config.assert_called_once_with(["vlan 100", "name SERVERS"])

    def test_mismatching_version_returns_error(self, mock_common_conn_mgr, mock_driver):
        """Push fails when expected_version doesn't match current config."""
        wrong_version = "0" * 64  # Invalid hash

        with patch("network_mcp.tools.common.config.check_read_only", return_value=None):
            from network_mcp.tools.common.config import net_push_config

            result = net_push_config("spine-01", ["vlan 100", "name SERVERS"], expected_version=wrong_version)

        assert result["status"] == "error"
        assert "Concurrent config change detected" in result["error"]
        assert result["details"]["expected_version"] == wrong_version
        assert len(result["details"]["current_version"]) == 64
        # Should NOT have applied commands
        mock_driver.run_config.assert_not_called()

    def test_no_expected_version_skips_check(self, mock_common_conn_mgr, mock_driver):
        """Push proceeds without version check when expected_version is None."""
        with patch("network_mcp.tools.common.config.check_read_only", return_value=None):
            from network_mcp.tools.common.config import net_push_config

            result = net_push_config("spine-01", ["vlan 100", "name SERVERS"])

        assert result["status"] == "success"
        mock_driver.run_config.assert_called_once()
        # get_config should still be called (for post-push version update) but NOT for pre-check
        # First call is post-push version update
        assert mock_driver.get_config.call_count >= 1

    def test_version_updated_after_push(self, mock_common_conn_mgr, mock_driver):
        """Config version cache is updated after successful push."""
        from network_mcp.tools.common.config import _config_versions

        assert "spine-01" not in _config_versions

        with patch("network_mcp.tools.common.config.check_read_only", return_value=None):
            from network_mcp.tools.common.config import net_push_config

            result = net_push_config("spine-01", ["vlan 100", "name SERVERS"])

        assert result["status"] == "success"
        assert "spine-01" in _config_versions
        expected_hash = hashlib.sha256(MOCK_RUNNING_CONFIG_A.encode("utf-8")).hexdigest()
        assert _config_versions["spine-01"] == expected_hash

    def test_version_updated_to_new_config_after_push(self, mock_common_conn_mgr, mock_driver):
        """After push, the version reflects the new config, not the old one."""
        call_count = 0

        def get_config_side_effect(sanitized=False):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                # Pre-check: return original config
                return {"running": MOCK_RUNNING_CONFIG_A, "startup": MOCK_RUNNING_CONFIG_A}
            else:
                # Post-push: return modified config
                return {"running": MOCK_RUNNING_CONFIG_B, "startup": MOCK_RUNNING_CONFIG_B}

        mock_driver.get_config.side_effect = get_config_side_effect
        expected = hashlib.sha256(MOCK_RUNNING_CONFIG_A.encode("utf-8")).hexdigest()

        with patch("network_mcp.tools.common.config.check_read_only", return_value=None):
            from network_mcp.tools.common.config import _config_versions, net_push_config

            result = net_push_config("spine-01", ["vlan 100"], expected_version=expected)

        assert result["status"] == "success"
        new_hash = hashlib.sha256(MOCK_RUNNING_CONFIG_B.encode("utf-8")).hexdigest()
        assert _config_versions["spine-01"] == new_hash

    def test_concurrent_change_detection_end_to_end(self, mock_common_conn_mgr, mock_driver):
        """End-to-end: get version, config changes, push fails."""
        from network_mcp.tools.common.config import net_get_config_version

        # Get version
        version_result = net_get_config_version("spine-01")
        version = version_result["data"]["version"]

        # Simulate config change on device
        mock_driver.get_config.return_value = {
            "running": MOCK_RUNNING_CONFIG_B,
            "startup": MOCK_RUNNING_CONFIG_B,
        }

        # Try to push with old version — should fail
        with patch("network_mcp.tools.common.config.check_read_only", return_value=None):
            from network_mcp.tools.common.config import net_push_config

            result = net_push_config("spine-01", ["vlan 200"], expected_version=version)

        assert result["status"] == "error"
        assert "Concurrent config change" in result["error"]
