"""Tests for audit hardening: HMAC signing, log rotation, syslog, export, auth failure logging."""

import json
import logging
import logging.handlers
import os
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from network_mcp.audit import (
    AuditLogger,
    _entries_to_csv,
    _parse_audit_file,
    _sign_entry,
    export_audit_log,
    verify_entry,
)

# ---------------------------------------------------------------------------
# 1. HMAC signing
# ---------------------------------------------------------------------------


class TestHMACSigning:
    """HMAC-SHA256 signing and verification."""

    def test_sign_and_verify_with_same_key(self):
        key = b"test-secret-key"
        entry = {"event": "tool_invocation", "tool": "get_vlans", "device": "spine-01"}
        sig = _sign_entry(entry, key)
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex digest length

        signed_entry = {**entry, "hmac": sig}
        assert verify_entry(signed_entry, key) is True

    def test_verify_fails_with_different_key(self):
        key1 = b"key-one"
        key2 = b"key-two"
        entry = {"event": "tool_invocation", "tool": "get_vlans", "device": "leaf-01"}
        sig = _sign_entry(entry, key1)
        signed_entry = {**entry, "hmac": sig}
        assert verify_entry(signed_entry, key2) is False

    def test_tampered_entry_fails_verification(self):
        key = b"signing-key"
        entry = {"event": "tool_invocation", "tool": "get_vlans", "device": "spine-01"}
        sig = _sign_entry(entry, key)
        tampered = {**entry, "device": "hacked-device", "hmac": sig}
        assert verify_entry(tampered, key) is False

    def test_verify_missing_hmac(self):
        key = b"signing-key"
        entry = {"event": "tool_invocation", "tool": "get_vlans"}
        assert verify_entry(entry, key) is False

    def test_audit_logger_signs_entries(self, capfd):
        key = b"audit-key"
        logger = AuditLogger(logger_name="test-hmac-sign", signing_key=key)
        logger.log_tool_call(
            tool_name="get_vlans",
            device="spine-01",
            parameters={"host": "spine-01"},
            result_status="success",
            duration_ms=10.0,
        )
        captured = capfd.readouterr()
        log_line = captured.err.strip()
        data = json.loads(log_line)
        assert "hmac" in data
        # Verify the HMAC is valid
        payload = {k: v for k, v in data.items() if k not in ("hmac", "timestamp", "level", "logger", "message")}
        assert verify_entry({**payload, "hmac": data["hmac"]}, key)

    def test_audit_logger_no_hmac_without_key(self, capfd):
        logger = AuditLogger(logger_name="test-no-hmac")
        logger.log_tool_call(
            tool_name="get_vlans",
            device="spine-01",
            parameters={},
            result_status="success",
            duration_ms=5.0,
        )
        captured = capfd.readouterr()
        data = json.loads(captured.err.strip())
        assert "hmac" not in data


# ---------------------------------------------------------------------------
# 2. Log rotation
# ---------------------------------------------------------------------------


class TestLogRotation:
    """RotatingFileHandler configuration."""

    def test_file_handler_configured_with_correct_settings(self):
        logger = AuditLogger(logger_name="test-rotation-config")
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            audit_file = f.name

        try:
            logger.configure_file_handler(
                audit_file=audit_file,
                max_bytes=1024,
                backup_count=3,
            )
            # Find the RotatingFileHandler
            handlers = [h for h in logger._logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
            assert len(handlers) == 1
            handler = handlers[0]
            assert handler.maxBytes == 1024
            assert handler.backupCount == 3
        finally:
            os.unlink(audit_file)

    def test_rotation_happens_at_size_limit(self):
        logger = AuditLogger(logger_name="test-rotation-trigger")
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = os.path.join(tmpdir, "audit.log")
            # Very small max_bytes to trigger rotation quickly
            logger.configure_file_handler(
                audit_file=audit_file,
                max_bytes=200,
                backup_count=2,
            )
            # Write enough entries to trigger rotation
            for i in range(20):
                logger.log_tool_call(
                    tool_name=f"tool_{i}",
                    device="test",
                    parameters={},
                    result_status="success",
                    duration_ms=1.0,
                )
            # Check that backup files were created
            files = os.listdir(tmpdir)
            backup_files = [f for f in files if f.startswith("audit.log.")]
            assert len(backup_files) > 0, f"Expected rotation backup files, got: {files}"


# ---------------------------------------------------------------------------
# 3. Syslog forwarding
# ---------------------------------------------------------------------------


class TestSyslogForwarding:
    """SysLogHandler configuration."""

    def test_syslog_handler_added_when_configured(self):
        logger = AuditLogger(logger_name="test-syslog-add")
        initial_count = len(logger._logger.handlers)
        logger.configure_syslog_handler("127.0.0.1", 1514)
        syslog_handlers = [h for h in logger._logger.handlers if isinstance(h, logging.handlers.SysLogHandler)]
        assert len(syslog_handlers) == 1
        assert len(logger._logger.handlers) == initial_count + 1

    def test_no_syslog_handler_by_default(self):
        logger = AuditLogger(logger_name="test-syslog-default")
        syslog_handlers = [h for h in logger._logger.handlers if isinstance(h, logging.handlers.SysLogHandler)]
        assert len(syslog_handlers) == 0

    def test_syslog_handler_uses_local6_facility(self):
        logger = AuditLogger(logger_name="test-syslog-facility")
        logger.configure_syslog_handler("127.0.0.1", 1514)
        syslog_handlers = [h for h in logger._logger.handlers if isinstance(h, logging.handlers.SysLogHandler)]
        assert syslog_handlers[0].facility == logging.handlers.SysLogHandler.LOG_LOCAL6


# ---------------------------------------------------------------------------
# 4. Audit log export
# ---------------------------------------------------------------------------


class TestAuditExport:
    """Audit log export (time range, JSON, CSV)."""

    @pytest.fixture()
    def audit_file(self):
        """Create a temp audit file with sample entries."""
        now = datetime.now(tz=UTC)
        entries = [
            {
                "timestamp": (now - timedelta(hours=2)).isoformat(),
                "event": "tool_invocation",
                "tool": "get_vlans",
                "device": "spine-01",
                "result_status": "success",
            },
            {
                "timestamp": (now - timedelta(hours=1)).isoformat(),
                "event": "tool_invocation",
                "tool": "get_bgp",
                "device": "leaf-01",
                "result_status": "success",
            },
            {
                "timestamp": (now - timedelta(days=2)).isoformat(),
                "event": "tool_invocation",
                "tool": "get_version",
                "device": "spine-02",
                "result_status": "success",
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            path = f.name
        yield path, now
        os.unlink(path)

    def test_export_with_time_range_returns_entries(self, audit_file):
        path, now = audit_file
        start = (now - timedelta(hours=3)).isoformat()
        end = now.isoformat()
        result = export_audit_log(path, start=start, end=end, fmt="json")
        assert result["status"] == "success"
        assert result["count"] == 2  # Only the two recent entries

    def test_export_json_format(self, audit_file):
        path, now = audit_file
        start = (now - timedelta(hours=3)).isoformat()
        result = export_audit_log(path, start=start, fmt="json")
        assert result["format"] == "json"
        assert isinstance(result["data"], list)
        for entry in result["data"]:
            assert isinstance(entry, dict)
            assert "timestamp" in entry

    def test_export_csv_format(self, audit_file):
        path, now = audit_file
        start = (now - timedelta(hours=3)).isoformat()
        result = export_audit_log(path, start=start, fmt="csv")
        assert result["format"] == "csv"
        assert isinstance(result["data"], str)
        lines = result["data"].strip().split("\n")
        assert len(lines) == 3  # header + 2 data rows
        assert "timestamp" in lines[0]

    def test_export_no_entries_in_range(self, audit_file):
        path, now = audit_file
        # Range in the future — no entries match
        start = (now + timedelta(hours=1)).isoformat()
        end = (now + timedelta(hours=2)).isoformat()
        result = export_audit_log(path, start=start, end=end, fmt="json")
        assert result["status"] == "success"
        assert result["count"] == 0
        assert result["data"] == []

    def test_export_default_time_range(self, audit_file):
        path, _ = audit_file
        result = export_audit_log(path, fmt="json")
        assert result["status"] == "success"
        # Default is last 24h — should include 2 entries (not the 2-day-old one)
        assert result["count"] == 2

    def test_export_missing_file(self):
        result = export_audit_log("/nonexistent/audit.log", fmt="json")
        assert result["status"] == "success"
        assert result["count"] == 0

    def test_export_csv_empty(self):
        result = export_audit_log("/nonexistent/audit.log", fmt="csv")
        assert result["format"] == "csv"
        assert result["data"] == ""


class TestEntriesCsv:
    """CSV conversion helpers."""

    def test_entries_to_csv_empty(self):
        assert _entries_to_csv([]) == ""

    def test_entries_to_csv_with_nested(self):
        entries = [
            {"timestamp": "2025-01-01T00:00:00Z", "params": {"host": "a"}, "tool": "x"},
        ]
        csv_str = _entries_to_csv(entries)
        assert "timestamp" in csv_str
        assert "params" in csv_str
        assert "tool" in csv_str


class TestParseAuditFile:
    """Test JSONL parsing with timestamp filtering."""

    def test_skips_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("not json\n")
            f.write(json.dumps({"timestamp": datetime.now(tz=UTC).isoformat(), "event": "ok"}) + "\n")
            path = f.name
        try:
            start = datetime.now(tz=UTC) - timedelta(hours=1)
            end = datetime.now(tz=UTC) + timedelta(hours=1)
            entries = _parse_audit_file(path, start, end)
            assert len(entries) == 1
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 5. Auth failure logging
# ---------------------------------------------------------------------------


class TestAuthFailureLogging:
    """Authentication/RBAC denial audit logging."""

    def test_log_auth_failure_emits_event(self, capfd):
        logger = AuditLogger(logger_name="test-auth-fail-1")
        logger.log_auth_failure(
            tool_name="eos_delete_vlan",
            reason="Missing scope network:admin",
            user="user@example.com",
            source_ip="10.0.0.1",
            attempted_scope="network:admin",
            device="spine-01",
        )
        captured = capfd.readouterr()
        data = json.loads(captured.err.strip())
        assert data["event"] == "auth_failure"
        assert data["tool"] == "eos_delete_vlan"
        assert data["reason"] == "Missing scope network:admin"
        assert data["user"] == "user@example.com"
        assert data["source_ip"] == "10.0.0.1"
        assert data["attempted_scope"] == "network:admin"
        assert data["device"] == "spine-01"

    def test_log_auth_failure_with_hmac(self, capfd):
        key = b"auth-audit-key"
        logger = AuditLogger(logger_name="test-auth-fail-hmac", signing_key=key)
        logger.log_auth_failure(
            tool_name="eos_create_vlan",
            reason="Insufficient scope",
            user="op@corp.com",
        )
        captured = capfd.readouterr()
        data = json.loads(captured.err.strip())
        assert "hmac" in data
        assert data["event"] == "auth_failure"

    def test_log_auth_failure_minimal_fields(self, capfd):
        logger = AuditLogger(logger_name="test-auth-fail-minimal")
        logger.log_auth_failure(
            tool_name="eos_get_vlans",
            reason="Token expired",
        )
        captured = capfd.readouterr()
        data = json.loads(captured.err.strip())
        assert data["event"] == "auth_failure"
        assert data["tool"] == "eos_get_vlans"
        assert data["reason"] == "Token expired"
        # Optional fields should not be present
        assert "source_ip" not in data
        assert "attempted_scope" not in data
        assert "device" not in data

    def test_rbac_denial_logs_to_audit(self, capfd):
        """Verify log_auth_failure produces a complete audit record for RBAC denials."""
        logger = AuditLogger(logger_name="test-rbac-denial")
        reason = "Forbidden: tool 'eos_delete_vlan' requires scope 'network:admin'. Your scopes: ['network:read']"
        logger.log_auth_failure(
            tool_name="eos_delete_vlan",
            reason=reason,
            user="client-123",
            attempted_scope="network:admin",
            device="spine-01",
        )
        captured = capfd.readouterr()
        data = json.loads(captured.err.strip())
        assert data["event"] == "auth_failure"
        assert data["tool"] == "eos_delete_vlan"
        assert data["user"] == "client-123"
        assert data["attempted_scope"] == "network:admin"
        assert data["device"] == "spine-01"
        assert "network:admin" in data["reason"]


# ---------------------------------------------------------------------------
# 6. Export tool registration
# ---------------------------------------------------------------------------


class TestExportToolRegistration:
    """Verify the net_export_audit_log tool is registered."""

    def test_export_tool_no_audit_file(self):
        from network_mcp.server import settings
        from network_mcp.tools.export import net_export_audit_log

        with patch.object(settings, "audit_file", None):
            result = net_export_audit_log()
            assert result["status"] == "error"
            assert "AUDIT_FILE" in result["error"]

    def test_export_tool_with_audit_file(self):
        now = datetime.now(tz=UTC)
        entry = {
            "timestamp": (now - timedelta(minutes=10)).isoformat(),
            "event": "tool_invocation",
            "tool": "get_vlans",
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(entry) + "\n")
            path = f.name
        try:
            from network_mcp.server import settings
            from network_mcp.tools.export import net_export_audit_log

            with patch.object(settings, "audit_file", path):
                result = net_export_audit_log()
                assert result["status"] == "success"
                assert result["count"] == 1
        finally:
            os.unlink(path)
