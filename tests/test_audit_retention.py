"""Tests for audit log retention, archival, mandatory signing, and key versioning."""

import gzip
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.audit import (
    AuditLogArchiver,
    AuditLogger,
    _sign_entry,
    verify_entry,
    verify_entry_versioned,
)

# --- AuditLogArchiver tests ---


class TestAuditLogArchiver:
    def _write_entries(self, log_dir: str, entries: list[dict], filename: str = "audit.jsonl") -> str:
        """Helper: write JSONL entries to a file."""
        path = os.path.join(log_dir, filename)
        with open(path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        return path

    def test_archive_old_entries(self, tmp_path):
        """Entries older than retention_days are moved to archive."""
        log_dir = str(tmp_path / "logs")
        os.makedirs(log_dir)

        now = datetime.now(tz=UTC)
        old_ts = (now - timedelta(days=100)).isoformat()
        recent_ts = (now - timedelta(days=1)).isoformat()

        entries = [
            {"timestamp": old_ts, "event": "tool_invocation", "tool": "old_tool"},
            {"timestamp": recent_ts, "event": "tool_invocation", "tool": "recent_tool"},
        ]
        self._write_entries(log_dir, entries)

        archiver = AuditLogArchiver(retention_days=30)
        result = archiver.archive_old_entries(log_dir)

        assert result["status"] == "success"
        assert result["archived_count"] == 1
        assert result["archive_path"] is not None
        assert result["archive_path"].endswith(".jsonl.gz")

        # Verify the source file only has the recent entry
        with open(os.path.join(log_dir, "audit.jsonl")) as f:
            remaining = [json.loads(line) for line in f if line.strip()]
        assert len(remaining) == 1
        assert remaining[0]["tool"] == "recent_tool"

    def test_archive_creates_gzip_compressed_file(self, tmp_path):
        """Archived entries are written as gzip-compressed JSONL."""
        log_dir = str(tmp_path / "logs")
        archive_dir = str(tmp_path / "archive")
        os.makedirs(log_dir)

        now = datetime.now(tz=UTC)
        old_ts = (now - timedelta(days=100)).isoformat()
        entries = [
            {"timestamp": old_ts, "event": "tool_invocation", "tool": "archived_tool"},
        ]
        self._write_entries(log_dir, entries)

        archiver = AuditLogArchiver(retention_days=30, archive_dir=archive_dir)
        result = archiver.archive_old_entries(log_dir)

        assert result["status"] == "success"
        assert result["archived_count"] == 1

        # Verify it's a valid gzip file with correct content
        archive_path = result["archive_path"]
        assert os.path.exists(archive_path)
        with gzip.open(archive_path, "rt") as gz:
            lines = gz.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool"] == "archived_tool"

    def test_archive_no_old_entries(self, tmp_path):
        """When no entries are older than retention_days, nothing is archived."""
        log_dir = str(tmp_path / "logs")
        os.makedirs(log_dir)

        now = datetime.now(tz=UTC)
        recent_ts = (now - timedelta(days=1)).isoformat()
        entries = [{"timestamp": recent_ts, "event": "tool_invocation", "tool": "recent"}]
        self._write_entries(log_dir, entries)

        archiver = AuditLogArchiver(retention_days=30)
        result = archiver.archive_old_entries(log_dir)

        assert result["status"] == "success"
        assert result["archived_count"] == 0
        assert result["archive_path"] is None

    def test_archive_filename_contains_date_range(self, tmp_path):
        """Archive filename includes the date range of archived entries."""
        log_dir = str(tmp_path / "logs")
        archive_dir = str(tmp_path / "archive")
        os.makedirs(log_dir)

        now = datetime.now(tz=UTC)
        ts1 = (now - timedelta(days=200)).isoformat()
        ts2 = (now - timedelta(days=100)).isoformat()

        entries = [
            {"timestamp": ts1, "event": "tool_invocation", "tool": "old1"},
            {"timestamp": ts2, "event": "tool_invocation", "tool": "old2"},
        ]
        self._write_entries(log_dir, entries)

        archiver = AuditLogArchiver(retention_days=30, archive_dir=archive_dir)
        result = archiver.archive_old_entries(log_dir)

        archive_name = os.path.basename(result["archive_path"])
        date1 = (now - timedelta(days=200)).strftime("%Y-%m-%d")
        date2 = (now - timedelta(days=100)).strftime("%Y-%m-%d")
        assert archive_name == f"audit_{date1}_to_{date2}.jsonl.gz"

    def test_upload_to_s3_missing_boto3(self, tmp_path):
        """upload_to_s3 returns error when boto3 is not installed."""
        archiver = AuditLogArchiver(retention_days=30)
        fake_archive = str(tmp_path / "test_archive.jsonl.gz")

        with patch.dict(sys.modules, {"boto3": None}):
            # Force reimport failure by removing from sys.modules
            saved = sys.modules.pop("boto3", None)
            try:
                # Patch builtins.__import__ to raise ImportError for boto3
                original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

                def mock_import(name, *args, **kwargs):
                    if name == "boto3":
                        raise ImportError("No module named 'boto3'")
                    return original_import(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=mock_import):
                    result = archiver.upload_to_s3(fake_archive)
            finally:
                if saved is not None:
                    sys.modules["boto3"] = saved

        assert result["status"] == "error"
        assert "boto3 is required" in result["error"]

    def test_upload_to_s3_success(self, tmp_path):
        """upload_to_s3 succeeds with mocked boto3 client."""
        archive_file = tmp_path / "test_archive.jsonl.gz"
        archive_file.write_text("test content")

        mock_boto3 = MagicMock()
        mock_s3_client = MagicMock()
        mock_boto3.client.return_value = mock_s3_client

        archiver = AuditLogArchiver(
            retention_days=30,
            s3_config={"bucket": "my-audit-bucket", "prefix": "logs/", "region": "us-east-1"},
        )

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            result = archiver.upload_to_s3(str(archive_file))

        assert result["status"] == "success"
        assert result["bucket"] == "my-audit-bucket"
        assert result["key"] == "logs/test_archive.jsonl.gz"
        mock_s3_client.upload_file.assert_called_once_with(
            str(archive_file), "my-audit-bucket", "logs/test_archive.jsonl.gz"
        )

    def test_upload_to_s3_missing_bucket(self, tmp_path):
        """upload_to_s3 returns error when bucket is not configured."""
        archiver = AuditLogArchiver(retention_days=30, s3_config={})
        mock_boto3 = MagicMock()
        fake_archive = str(tmp_path / "test.jsonl.gz")

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            result = archiver.upload_to_s3(fake_archive)

        assert result["status"] == "error"
        assert "bucket" in result["error"]


# --- Mandatory signing enforcement tests ---


class TestMandatorySigning:
    def test_require_signing_raises_without_key(self):
        """When require_signing=True and no key provided, ValueError is raised."""
        with pytest.raises(ValueError, match="AUDIT_SIGNING_KEY is required when audit signing is mandatory"):
            AuditLogger(
                logger_name="test-mandatory-1",
                signing_key=None,
                require_signing=True,
            )

    def test_require_signing_passes_with_key(self):
        """When require_signing=True and key is provided, logger initializes normally."""
        logger = AuditLogger(
            logger_name="test-mandatory-2",
            signing_key=b"test-key-123",
            require_signing=True,
        )
        assert logger._signing_key == b"test-key-123"
        assert logger._require_signing is True

    def test_emit_raises_when_signing_mandatory_but_key_missing(self):
        """If require_signing is set after init somehow without key, _emit raises."""
        logger = AuditLogger(logger_name="test-mandatory-3", signing_key=b"key")
        # Simulate key being cleared after init
        logger._signing_key = None
        logger._require_signing = True

        with pytest.raises(ValueError, match="Audit signing is mandatory"):
            logger._emit({"event": "test"}, "test message")


# --- Key versioning tests ---


class TestKeyVersioning:
    def test_sign_entry_includes_key_version(self):
        """sign_entry with key_version includes it in the signed output."""
        entry = {"event": "tool_invocation", "tool": "test"}
        hmac_sig = _sign_entry(entry, b"test-key", key_version=2)
        assert isinstance(hmac_sig, str)
        assert len(hmac_sig) == 64  # SHA-256 hex digest

    def test_sign_entry_different_versions_produce_different_signatures(self):
        """Different key versions produce different HMAC signatures."""
        entry = {"event": "tool_invocation", "tool": "test"}
        sig_v1 = _sign_entry(entry, b"test-key", key_version=1)
        sig_v2 = _sign_entry(entry, b"test-key", key_version=2)
        assert sig_v1 != sig_v2

    def test_verify_entry_with_key_version(self):
        """verify_entry validates entries that include key_version."""
        entry = {"event": "tool_invocation", "tool": "test"}
        sig = _sign_entry(entry, b"my-key", key_version=3)
        signed_entry = {**entry, "key_version": 3, "hmac": sig}

        assert verify_entry(signed_entry, b"my-key") is True

    def test_verify_entry_fails_with_wrong_key(self):
        """verify_entry fails when wrong key is used."""
        entry = {"event": "tool_invocation", "tool": "test"}
        sig = _sign_entry(entry, b"correct-key", key_version=1)
        signed_entry = {**entry, "key_version": 1, "hmac": sig}

        assert verify_entry(signed_entry, b"wrong-key") is False

    def test_verify_entry_fails_with_tampered_data(self):
        """verify_entry fails when entry data has been tampered with."""
        entry = {"event": "tool_invocation", "tool": "test"}
        sig = _sign_entry(entry, b"my-key", key_version=1)
        tampered_entry = {**entry, "tool": "tampered", "key_version": 1, "hmac": sig}

        assert verify_entry(tampered_entry, b"my-key") is False

    def test_verify_entry_versioned_correct_key(self):
        """verify_entry_versioned succeeds with correct key version from history."""
        entry = {"event": "tool_invocation", "tool": "test"}
        sig = _sign_entry(entry, b"key-v2", key_version=2)
        signed_entry = {**entry, "key_version": 2, "hmac": sig}

        key_history = {1: "key-v1", 2: "key-v2", 3: "key-v3"}
        assert verify_entry_versioned(signed_entry, key_history) is True

    def test_verify_entry_versioned_wrong_version(self):
        """verify_entry_versioned fails when key_version is not in history."""
        entry = {"event": "tool_invocation", "tool": "test"}
        sig = _sign_entry(entry, b"key-v2", key_version=2)
        signed_entry = {**entry, "key_version": 2, "hmac": sig}

        key_history = {1: "key-v1", 3: "key-v3"}  # version 2 missing
        assert verify_entry_versioned(signed_entry, key_history) is False

    def test_verify_entry_versioned_tampered_entry(self):
        """verify_entry_versioned fails with tampered entry."""
        entry = {"event": "tool_invocation", "tool": "test"}
        sig = _sign_entry(entry, b"key-v1", key_version=1)
        tampered = {**entry, "tool": "hacked", "key_version": 1, "hmac": sig}

        key_history = {1: "key-v1"}
        assert verify_entry_versioned(tampered, key_history) is False

    def test_verify_entry_versioned_no_hmac(self):
        """verify_entry_versioned fails when entry has no hmac field."""
        entry = {"event": "tool_invocation", "key_version": 1}
        assert verify_entry_versioned(entry, {1: "key"}) is False

    def test_verify_entry_versioned_no_key_version(self):
        """verify_entry_versioned fails when entry has no key_version field."""
        entry = {"event": "tool_invocation", "hmac": "abc123"}
        assert verify_entry_versioned(entry, {1: "key"}) is False

    def test_audit_logger_emits_key_version(self, capfd):
        """AuditLogger includes key_version in signed entries."""
        logger = AuditLogger(
            logger_name="test-kv-emit",
            signing_key=b"test-key",
            key_version=5,
        )
        logger.log_tool_call(
            tool_name="test_tool",
            device="spine-01",
            parameters={"host": "spine-01"},
            result_status="success",
            duration_ms=10.0,
        )
        captured = capfd.readouterr()
        log_line = captured.err.strip()
        data = json.loads(log_line)
        assert data["key_version"] == 5
        assert "hmac" in data


# --- Settings defaults tests ---


class TestSettingsDefaults:
    def test_default_retention_days(self):
        """NET_AUDIT_RETENTION_DAYS defaults to 2555 (7 years)."""
        from network_mcp.config import NetworkSettings

        settings = NetworkSettings()
        assert settings.net_audit_retention_days == 2555

    def test_default_require_audit_signing(self):
        """NET_REQUIRE_AUDIT_SIGNING defaults to False."""
        from network_mcp.config import NetworkSettings

        settings = NetworkSettings()
        assert settings.net_require_audit_signing is False

    def test_default_archive_dir(self):
        """NET_AUDIT_ARCHIVE_DIR defaults to None."""
        from network_mcp.config import NetworkSettings

        settings = NetworkSettings()
        assert settings.net_audit_archive_dir is None

    def test_default_signing_key_version(self):
        """AUDIT_SIGNING_KEY_VERSION defaults to 1."""
        from network_mcp.config import NetworkSettings

        settings = NetworkSettings()
        assert settings.audit_signing_key_version == 1
