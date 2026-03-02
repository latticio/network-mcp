"""Tests for RFC 5424 structured syslog formatter."""

import logging
import re

import pytest

from network_mcp.syslog_formatter import RFC5424Formatter


@pytest.fixture
def formatter():
    return RFC5424Formatter(app_name="network-mcp")


def _make_record(
    level: int = logging.INFO,
    msg: str = "Test message",
    audit_data: dict | None = None,
) -> logging.LogRecord:
    """Create a log record with optional audit_data."""
    record = logging.LogRecord(
        name="network-mcp.audit",
        level=level,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if audit_data is not None:
        record.audit_data = audit_data  # type: ignore[attr-defined]
    return record


class TestRFC5424Format:
    """RFC 5424 output format validation."""

    def test_basic_format(self, formatter):
        record = _make_record()
        result = formatter.format(record)
        # Should match: <PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID SD MSG
        pattern = r"<\d+>1 \S+ \S+ network-mcp \d+ - .+ Test message"
        assert re.match(pattern, result), f"Output does not match RFC 5424 format: {result}"

    def test_starts_with_pri_and_version(self, formatter):
        record = _make_record()
        result = formatter.format(record)
        assert result.startswith("<"), "Should start with PRI"
        # Extract PRI value
        pri_match = re.match(r"<(\d+)>1", result)
        assert pri_match, "Should have <PRI>1 format"

    def test_version_is_1(self, formatter):
        record = _make_record()
        result = formatter.format(record)
        pri_end = result.index(">")
        assert result[pri_end + 1 : pri_end + 2] == "1", "VERSION should be 1"


class TestSeverityMapping:
    """Python log levels map to correct syslog severity codes."""

    @pytest.mark.parametrize(
        ("level", "expected_severity"),
        [
            (logging.DEBUG, 7),
            (logging.INFO, 6),
            (logging.WARNING, 4),
            (logging.ERROR, 3),
            (logging.CRITICAL, 2),
        ],
    )
    def test_severity_mapping(self, formatter, level, expected_severity):
        record = _make_record(level=level)
        result = formatter.format(record)
        pri_match = re.match(r"<(\d+)>", result)
        assert pri_match
        pri = int(pri_match.group(1))
        # PRI = FACILITY * 8 + SEVERITY
        severity = pri % 8
        facility = pri // 8
        assert severity == expected_severity
        assert facility == 16  # local0


class TestStructuredData:
    """Structured data elements include tool, host, user when present."""

    def test_meta_element_with_tool_and_host(self, formatter):
        record = _make_record(
            audit_data={
                "event": "tool_invocation",
                "tool": "eos_get_vlans",
                "device": "spine-01",
                "result_status": "success",
                "duration_ms": 42.5,
            }
        )
        result = formatter.format(record)
        assert "[meta@48577 " in result
        assert 'tool="eos_get_vlans"' in result
        assert 'host="spine-01"' in result

    def test_meta_element_with_user_and_tenant(self, formatter):
        record = _make_record(
            audit_data={
                "event": "tool_invocation",
                "tool": "eos_get_vlans",
                "device": "spine-01",
                "user": "admin@example.com",
                "tenant_id": "tenant-42",
                "result_status": "success",
                "duration_ms": 10.0,
            }
        )
        result = formatter.format(record)
        assert 'user="admin@example.com"' in result
        assert 'tenant_id="tenant-42"' in result

    def test_outcome_element_with_status_and_duration(self, formatter):
        record = _make_record(
            audit_data={
                "tool": "eos_get_vlans",
                "device": "spine-01",
                "result_status": "success",
                "duration_ms": 123.45,
            }
        )
        result = formatter.format(record)
        assert "[outcome@48577 " in result
        assert 'status="success"' in result
        assert 'duration_ms="123.45"' in result

    def test_outcome_element_with_error(self, formatter):
        record = _make_record(
            audit_data={
                "tool": "eos_get_vlans",
                "device": "spine-01",
                "result_status": "error",
                "duration_ms": 5.0,
                "error": "Connection refused",
            }
        )
        result = formatter.format(record)
        assert 'error="Connection refused"' in result

    def test_no_audit_data_produces_nil_sd(self, formatter):
        record = _make_record()
        result = formatter.format(record)
        # SD should be "-" when no audit_data
        parts = result.split(" ", 7)  # Split up to SD field
        # The 7th field (index 6) should be "-" for NILVALUE SD
        assert parts[6] == "-"


class TestSpecialCharacterEscaping:
    """Special characters in structured data values are properly escaped."""

    def test_escape_double_quote(self, formatter):
        record = _make_record(
            audit_data={
                "tool": 'tool_with_"quotes"',
                "device": "spine-01",
            }
        )
        result = formatter.format(record)
        assert r'tool="tool_with_\"quotes\""' in result

    def test_escape_backslash(self, formatter):
        record = _make_record(
            audit_data={
                "tool": "tool\\path",
                "device": "spine-01",
            }
        )
        result = formatter.format(record)
        assert r'tool="tool\\path"' in result

    def test_escape_closing_bracket(self, formatter):
        record = _make_record(
            audit_data={
                "tool": "tool]bracket",
                "device": "spine-01",
            }
        )
        result = formatter.format(record)
        assert r'tool="tool\]bracket"' in result

    def test_escape_combined(self, formatter):
        raw = r'a"b\c]d'
        escaped = RFC5424Formatter._escape_sd_value(raw)
        assert escaped == r"a\"b\\c\]d"


class TestTimestamp:
    """Timestamp is ISO 8601 with timezone."""

    def test_timestamp_is_iso8601(self, formatter):
        record = _make_record()
        result = formatter.format(record)
        # Extract timestamp (second field after PRI>1)
        parts = result.split(" ", 3)
        timestamp = parts[1]
        # Should be a valid ISO 8601 string with timezone
        assert "T" in timestamp, "Timestamp should be ISO 8601 format"
        assert "+" in timestamp or "Z" in timestamp or timestamp.endswith("+00:00"), (
            f"Timestamp should include timezone: {timestamp}"
        )


class TestGracefulDegradation:
    """Missing extra fields produce valid output."""

    def test_empty_audit_data(self, formatter):
        record = _make_record(audit_data={})
        result = formatter.format(record)
        # Should still produce valid output with NILVALUE SD
        assert re.match(r"<\d+>1 ", result)
        parts = result.split(" ", 7)
        assert parts[6] == "-"

    def test_partial_audit_data_tool_only(self, formatter):
        record = _make_record(audit_data={"tool": "eos_get_vlans"})
        result = formatter.format(record)
        assert 'tool="eos_get_vlans"' in result
        # No outcome element since no status/duration
        assert "outcome@" not in result

    def test_partial_audit_data_status_only(self, formatter):
        record = _make_record(audit_data={"result_status": "success"})
        result = formatter.format(record)
        # No meta element since no tool/device/user
        assert "meta@" not in result
        # Outcome element should be present
        assert 'status="success"' in result


class TestAppName:
    """App name is configurable."""

    def test_custom_app_name(self):
        formatter = RFC5424Formatter(app_name="custom-app")
        record = _make_record()
        result = formatter.format(record)
        assert " custom-app " in result
