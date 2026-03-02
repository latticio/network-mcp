"""Tests for Arista EOS configuration checkpoint tools.

Covers:
- eos_create_checkpoint: success, empty name, read-only blocked
- eos_list_checkpoints: with checkpoints, empty
- eos_rollback_to_checkpoint: success, not found, read-only blocked
- eos_delete_config_checkpoint: success, not found, read-only blocked
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.server import conn_mgr
from network_mcp.tools.checkpoints import (
    eos_create_checkpoint,
    eos_delete_config_checkpoint,
    eos_list_checkpoints,
    eos_rollback_to_checkpoint,
    validate_checkpoint_name,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_conn_mgr_rw(mock_node):
    """Patch conn_mgr for read-write operations with read-only mode disabled.

    Patches both get_driver and acquire (used by run_session_command).
    """

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield mock_node

    with (
        patch.object(conn_mgr, "get_driver", return_value=mock_node),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        patch("network_mcp.helpers._get_settings") as mock_settings,
    ):
        settings = MagicMock()
        settings.net_read_only = False
        settings.net_change_mgmt_enabled = False
        settings.config_retry_enabled = False
        settings.net_distributed_backend = "local"
        settings.net_redis_url = ""
        mock_settings.return_value = settings
        yield mock_node


@pytest.fixture
def mock_conn_mgr_ro(mock_node):
    """Patch conn_mgr for read-only mode (writes blocked)."""

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield mock_node

    with (
        patch.object(conn_mgr, "get_driver", return_value=mock_node),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        patch("network_mcp.helpers._get_settings") as mock_settings,
    ):
        settings = MagicMock()
        settings.net_read_only = True
        mock_settings.return_value = settings
        yield mock_node


# ===========================================================================
# validate_checkpoint_name tests
# ===========================================================================


class TestValidateCheckpointName:
    """Tests for checkpoint name validation."""

    def test_valid_names(self):
        """Valid checkpoint names should pass validation."""
        assert validate_checkpoint_name("before-bgp-change") is None
        assert validate_checkpoint_name("pre-upgrade-2024") is None
        assert validate_checkpoint_name("checkpoint1") is None
        assert validate_checkpoint_name("a") is None

    def test_empty_name(self):
        """Empty checkpoint name should fail validation."""
        assert validate_checkpoint_name("") is not None
        assert "required" in validate_checkpoint_name("").lower() or "empty" in validate_checkpoint_name("").lower()

    def test_whitespace_name(self):
        """Whitespace-only checkpoint name should fail validation."""
        result = validate_checkpoint_name("   ")
        assert result is not None

    def test_name_too_long(self):
        """Checkpoint name exceeding 50 characters should fail."""
        long_name = "a" * 51
        result = validate_checkpoint_name(long_name)
        assert result is not None
        assert "50" in result

    def test_name_exactly_50_chars(self):
        """Checkpoint name of exactly 50 characters should pass."""
        name = "a" * 50
        assert validate_checkpoint_name(name) is None

    def test_invalid_characters(self):
        """Names with special characters should fail."""
        assert validate_checkpoint_name("my checkpoint") is not None  # space
        assert validate_checkpoint_name("my_checkpoint") is not None  # underscore (not in pattern)
        assert validate_checkpoint_name("my.checkpoint") is not None  # dot
        assert validate_checkpoint_name("my/checkpoint") is not None  # slash


# ===========================================================================
# eos_create_checkpoint tests
# ===========================================================================


class TestCreateCheckpoint:
    """Tests for eos_create_checkpoint."""

    def test_create_success(self, mock_conn_mgr_rw):
        """Successfully create a checkpoint."""
        mock_conn_mgr_rw.run_commands.return_value = [{}]

        result = eos_create_checkpoint("switch-01", "pre-upgrade")

        assert result["status"] == "success"
        assert result["action"] == "create_checkpoint"
        assert result["data"]["checkpoint_name"] == "pre-upgrade"
        assert "pre-upgrade" in result["message"]

    def test_create_empty_name_returns_error(self, mock_conn_mgr_rw):
        """Creating a checkpoint with empty name should return error."""
        result = eos_create_checkpoint("switch-01", "")

        assert result["status"] == "error"
        assert "required" in result["error"].lower() or "empty" in result["error"].lower()

    def test_create_invalid_name_returns_error(self, mock_conn_mgr_rw):
        """Creating a checkpoint with invalid characters should return error."""
        result = eos_create_checkpoint("switch-01", "bad name!")

        assert result["status"] == "error"
        assert "invalid" in result["error"].lower() or "must contain" in result["error"].lower()

    def test_create_blocked_in_read_only(self, mock_conn_mgr_ro):
        """Creating a checkpoint in read-only mode should return error."""
        result = eos_create_checkpoint("switch-01", "pre-upgrade")

        assert result["status"] == "error"
        assert "read" in result["error"].lower() or "write" in result["error"].lower()

    def test_create_with_device_error(self, mock_conn_mgr_rw):
        """Creating a checkpoint when device returns error should propagate."""
        mock_conn_mgr_rw.run_commands.side_effect = ConnectionError("Device unreachable")

        result = eos_create_checkpoint("switch-01", "pre-upgrade")

        assert result["status"] == "error"
        assert "switch-01" in str(result.get("device", ""))


# ===========================================================================
# eos_list_checkpoints tests
# ===========================================================================


class TestListCheckpoints:
    """Tests for eos_list_checkpoints."""

    def test_list_with_checkpoints(self, mock_conn_mgr):
        """Should return checkpoint list from device output."""
        mock_node = mock_conn_mgr.return_value
        mock_node.run_commands.return_value = [{"output": "pre-upgrade\npost-upgrade\nbefore-bgp-change\n"}]

        result = eos_list_checkpoints("switch-01")

        assert result["status"] == "success"
        assert "checkpoints" in result["data"]
        assert "pre-upgrade" in result["data"]["checkpoints"]
        assert "post-upgrade" in result["data"]["checkpoints"]
        assert "before-bgp-change" in result["data"]["checkpoints"]

    def test_list_empty(self, mock_conn_mgr):
        """Should handle empty checkpoint list."""
        mock_node = mock_conn_mgr.return_value
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_list_checkpoints("switch-01")

        assert result["status"] == "success"
        assert result["data"]["checkpoints"] == ""

    def test_list_not_blocked_in_read_only(self, mock_conn_mgr):
        """Listing checkpoints should work in read-only mode (it's a read operation)."""
        mock_node = mock_conn_mgr.return_value
        mock_node.run_commands.return_value = [{"output": "checkpoint-1\n"}]

        result = eos_list_checkpoints("switch-01")

        assert result["status"] == "success"

    def test_list_with_connection_error(self, mock_conn_mgr):
        """Should handle connection errors gracefully."""
        mock_node = mock_conn_mgr.return_value
        mock_node.run_commands.side_effect = ConnectionError("Device unreachable")

        result = eos_list_checkpoints("switch-01")

        assert result["status"] == "error"


# ===========================================================================
# eos_rollback_to_checkpoint tests
# ===========================================================================


class TestRollbackToCheckpoint:
    """Tests for eos_rollback_to_checkpoint."""

    def test_rollback_success(self, mock_conn_mgr_rw):
        """Successfully roll back to a checkpoint."""
        mock_conn_mgr_rw.run_commands.return_value = [{}]

        result = eos_rollback_to_checkpoint("switch-01", "pre-upgrade")

        assert result["status"] == "success"
        assert result["action"] == "rollback_to_checkpoint"
        assert result["data"]["checkpoint_name"] == "pre-upgrade"
        assert "pre-upgrade" in result["message"]

    def test_rollback_not_found(self, mock_conn_mgr_rw):
        """Rollback to non-existent checkpoint should propagate device error."""
        import pyeapi.eapilib

        mock_conn_mgr_rw.run_commands.side_effect = pyeapi.eapilib.CommandError(
            1000, "Could not find checkpoint 'nonexistent'"
        )

        result = eos_rollback_to_checkpoint("switch-01", "nonexistent")

        assert result["status"] == "error"

    def test_rollback_blocked_in_read_only(self, mock_conn_mgr_ro):
        """Rollback in read-only mode should return error."""
        result = eos_rollback_to_checkpoint("switch-01", "pre-upgrade")

        assert result["status"] == "error"
        assert "read" in result["error"].lower() or "write" in result["error"].lower()

    def test_rollback_empty_name_returns_error(self, mock_conn_mgr_rw):
        """Rollback with empty checkpoint name should return error."""
        result = eos_rollback_to_checkpoint("switch-01", "")

        assert result["status"] == "error"
        assert "required" in result["error"].lower() or "empty" in result["error"].lower()

    def test_rollback_invalid_name_returns_error(self, mock_conn_mgr_rw):
        """Rollback with invalid checkpoint name should return error."""
        result = eos_rollback_to_checkpoint("switch-01", "bad/name")

        assert result["status"] == "error"

    def test_rollback_with_connection_error(self, mock_conn_mgr_rw):
        """Rollback when device is unreachable should return error."""
        mock_conn_mgr_rw.run_commands.side_effect = ConnectionError("Device unreachable")

        result = eos_rollback_to_checkpoint("switch-01", "pre-upgrade")

        assert result["status"] == "error"


# ===========================================================================
# eos_delete_config_checkpoint tests
# ===========================================================================


class TestDeleteConfigCheckpoint:
    """Tests for eos_delete_config_checkpoint."""

    def test_delete_success(self, mock_conn_mgr_rw):
        """Successfully delete a checkpoint."""
        mock_conn_mgr_rw.run_commands.return_value = [{}]

        result = eos_delete_config_checkpoint("switch-01", "old-checkpoint")

        assert result["status"] == "success"
        assert result["action"] == "delete_config_checkpoint"
        assert result["data"]["checkpoint_name"] == "old-checkpoint"
        assert "old-checkpoint" in result["message"]

    def test_delete_not_found(self, mock_conn_mgr_rw):
        """Deleting a non-existent checkpoint should propagate device error."""
        import pyeapi.eapilib

        mock_conn_mgr_rw.run_commands.side_effect = pyeapi.eapilib.CommandError(
            1000, "No such file or directory: 'checkpoint:nonexistent'"
        )

        result = eos_delete_config_checkpoint("switch-01", "nonexistent")

        assert result["status"] == "error"

    def test_delete_blocked_in_read_only(self, mock_conn_mgr_ro):
        """Deleting a checkpoint in read-only mode should return error."""
        result = eos_delete_config_checkpoint("switch-01", "old-checkpoint")

        assert result["status"] == "error"
        assert "read" in result["error"].lower() or "write" in result["error"].lower()

    def test_delete_empty_name_returns_error(self, mock_conn_mgr_rw):
        """Delete with empty checkpoint name should return error."""
        result = eos_delete_config_checkpoint("switch-01", "")

        assert result["status"] == "error"

    def test_delete_invalid_name_returns_error(self, mock_conn_mgr_rw):
        """Delete with invalid checkpoint name should return error."""
        result = eos_delete_config_checkpoint("switch-01", "bad name!")

        assert result["status"] == "error"

    def test_delete_with_connection_error(self, mock_conn_mgr_rw):
        """Delete when device is unreachable should return error."""
        mock_conn_mgr_rw.run_commands.side_effect = ConnectionError("Device unreachable")

        result = eos_delete_config_checkpoint("switch-01", "old-checkpoint")

        assert result["status"] == "error"
