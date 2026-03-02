"""Structured audit logging for MCP tool invocations.

Supports HMAC-SHA256 signing for tamper detection, log rotation via
RotatingFileHandler, syslog forwarding (RFC 5424), audit log archival
with gzip compression, mandatory signing enforcement, key versioning,
and an MCP tool for exporting audit entries by time range.
"""

import csv
import gzip
import hashlib
import hmac
import io
import json
import logging
import logging.handlers
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_logger = logging.getLogger("network-mcp.audit.setup")


def _sign_entry(entry: dict, key: bytes, *, key_version: int | None = None) -> str:
    """Generate HMAC-SHA256 signature for an audit log entry.

    Args:
        entry: The audit entry dict (without hmac/key_version fields).
        key: HMAC signing key bytes.
        key_version: Optional key version to include in the signed data.
    """
    payload = dict(entry)
    if key_version is not None:
        payload["key_version"] = key_version
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()


def verify_entry(entry: dict, key: bytes) -> bool:
    """Verify the HMAC signature on an audit log entry using the provided key.

    Returns True if the signature matches, False otherwise.
    """
    stored_hmac = entry.get("hmac")
    if not stored_hmac:
        return False
    payload = {k: v for k, v in entry.items() if k not in ("hmac",)}
    expected = _sign_entry(
        {k: v for k, v in payload.items() if k != "key_version"},
        key,
        key_version=payload.get("key_version"),
    )
    return hmac.compare_digest(stored_hmac, expected)


def verify_entry_versioned(entry: dict, key_history: dict[int, str]) -> bool:
    """Verify the HMAC signature using a versioned key history.

    Reads key_version from the entry, looks up the corresponding key
    in key_history, and verifies the signature.

    Args:
        entry: Audit log entry dict containing hmac and key_version fields.
        key_history: Mapping of key version (int) to signing key (str).

    Returns:
        True if valid, False if invalid or key version not found.
    """
    stored_hmac = entry.get("hmac")
    if not stored_hmac:
        return False
    kv = entry.get("key_version")
    if kv is None:
        return False
    key_str = key_history.get(kv)
    if key_str is None:
        return False
    return verify_entry(entry, key_str.encode())


class JSONFormatter(logging.Formatter):
    """Format log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "audit_data"):
            log_data.update(record.audit_data)
        return json.dumps(log_data, default=str)


class AuditLogger:
    """Structured audit logging for MCP tool invocations.

    Logs every tool invocation as a JSON line to stderr and optionally to a
    rotating file and syslog.  Supports HMAC-SHA256 signing for tamper detection.
    """

    def __init__(
        self,
        logger_name: str = "network-mcp.audit",
        signing_key: bytes | None = None,
        *,
        require_signing: bool = False,
        key_version: int = 1,
    ):
        if require_signing and not signing_key:
            raise ValueError("AUDIT_SIGNING_KEY is required when audit signing is mandatory")

        self._logger = logging.getLogger(logger_name)
        self._logger.setLevel(logging.INFO)
        self._signing_key = signing_key
        self._require_signing = require_signing
        self._key_version = key_version

        # JSON handler to stderr (MCP-safe — stdout is JSON-RPC)
        if not self._logger.handlers:
            handler = logging.StreamHandler(stream=sys.stderr)
            handler.setFormatter(JSONFormatter())
            self._logger.addHandler(handler)
        self._logger.propagate = False

    def configure_file_handler(
        self,
        audit_file: str,
        max_bytes: int = 10_485_760,
        backup_count: int = 5,
    ) -> None:
        """Add a RotatingFileHandler for audit log persistence."""
        file_handler = logging.handlers.RotatingFileHandler(
            audit_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
        )
        file_handler.setFormatter(JSONFormatter())
        self._logger.addHandler(file_handler)

    def configure_syslog_handler(self, host: str, port: int = 514, *, use_rfc5424: bool = False) -> None:
        """Add a SysLogHandler for remote syslog forwarding.

        Args:
            host: Syslog server hostname or IP.
            port: Syslog server port (default 514).
            use_rfc5424: When True, use RFC 5424 structured format with SIEM-ready
                structured data elements. Otherwise use default format.
        """
        syslog_handler = logging.handlers.SysLogHandler(
            address=(host, port),
            facility=logging.handlers.SysLogHandler.LOG_LOCAL6,
        )
        if use_rfc5424:
            from network_mcp.syslog_formatter import RFC5424Formatter

            syslog_handler.setFormatter(RFC5424Formatter())
        self._logger.addHandler(syslog_handler)

    def log_tool_call(
        self,
        tool_name: str,
        device: str,
        parameters: dict,
        result_status: str,
        duration_ms: float,
        error: str | None = None,
        user: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Log a tool invocation."""
        # Import here to avoid circular imports (middleware imports from auth, not audit)
        from network_mcp.middleware import get_correlation_id, get_current_tenant_id, get_current_user

        if user is None:
            user = get_current_user()

        if tenant_id is None:
            tenant_id = get_current_tenant_id()

        correlation_id = get_correlation_id()

        audit_data = {
            "event": "tool_invocation",
            "tool": tool_name,
            "device": device,
            "user": user,
            "tenant_id": tenant_id,
            "request_id": correlation_id,
            "correlation_id": correlation_id,
            "parameters": self._redact_sensitive(parameters),
            "result_status": result_status,
            "duration_ms": round(duration_ms, 2),
        }
        if error:
            audit_data["error"] = error

        self._emit(audit_data, f"Tool call: {tool_name} on {device}")

    def log_auth_failure(
        self,
        tool_name: str,
        reason: str,
        *,
        user: str | None = None,
        source_ip: str | None = None,
        attempted_scope: str | None = None,
        device: str | None = None,
    ) -> None:
        """Log an authentication or RBAC denial to the audit trail."""
        from network_mcp.middleware import get_correlation_id, get_current_user

        correlation_id = get_correlation_id()
        audit_data: dict = {
            "event": "auth_failure",
            "tool": tool_name,
            "user": user or get_current_user(),
            "request_id": correlation_id,
            "correlation_id": correlation_id,
            "reason": reason,
        }
        if source_ip:
            audit_data["source_ip"] = source_ip
        if attempted_scope:
            audit_data["attempted_scope"] = attempted_scope
        if device:
            audit_data["device"] = device

        self._emit(audit_data, f"Auth failure: {tool_name} — {reason}", level=logging.WARNING)

    def log_config_reload(
        self,
        changes: dict[str, dict[str, object]],
        *,
        user: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Log a configuration reload event to the audit trail.

        Each changed setting is logged as a separate audit entry. Secret values
        (passwords, tokens, keys) are redacted to '***'.

        Args:
            changes: Dict of changed settings from reload_settings():
                     {"setting_name": {"old": old_val, "new": new_val}}.
            user: Authenticated user who triggered the reload.
            tenant_id: Tenant ID from request context.
        """
        from network_mcp.config import _SECRET_FIELDS
        from network_mcp.middleware import get_correlation_id, get_current_tenant_id, get_current_user

        if user is None:
            user = get_current_user()
        if tenant_id is None:
            tenant_id = get_current_tenant_id()
        correlation_id = get_correlation_id()

        for setting_name, diff in changes.items():
            old_val = diff["old"]
            new_val = diff["new"]

            # Redact secret values (should never appear in reloadable settings,
            # but defense-in-depth)
            if setting_name in _SECRET_FIELDS:
                old_val = "***"
                new_val = "***"

            audit_data: dict = {
                "event": "config_reload",
                "action": "config_reload",
                "setting_name": setting_name,
                "old_value": old_val,
                "new_value": new_val,
                "user": user,
                "tenant_id": tenant_id,
                "request_id": correlation_id,
                "correlation_id": correlation_id,
            }
            self._emit(audit_data, f"Config reload: {setting_name} changed")

    def _emit(self, audit_data: dict, message: str, *, level: int = logging.INFO) -> None:
        """Build a log record, optionally sign it, and emit."""
        if self._signing_key:
            payload = {k: v for k, v in audit_data.items() if k not in ("hmac", "key_version")}
            audit_data["key_version"] = self._key_version
            audit_data["hmac"] = _sign_entry(payload, self._signing_key, key_version=self._key_version)
        elif self._require_signing:
            raise ValueError("Audit signing is mandatory but no signing key is available")

        record = self._logger.makeRecord(
            name=self._logger.name,
            level=level,
            fn="",
            lno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        record.audit_data = audit_data  # type: ignore[attr-defined]
        self._logger.handle(record)

    def _redact_sensitive(self, params: dict) -> dict:
        """Redact sensitive fields from parameters before logging."""
        redacted: dict[str, object] = {}
        sensitive_keys = {"password", "secret", "token", "community", "key"}
        for k, v in params.items():
            if any(s in k.lower() for s in sensitive_keys):
                redacted[k] = "***REDACTED***"
            elif isinstance(v, list) and k == "commands":
                redacted[k] = [self._redact_command(cmd) for cmd in v]
            else:
                redacted[k] = v
        return redacted

    @staticmethod
    def _redact_command(cmd: str) -> str:
        """Redact potential secrets in config commands."""
        cmd = re.sub(r"(secret|password)\s+\d+\s+\S+", r"\1 *** ***REDACTED***", cmd)
        cmd = re.sub(r"(community)\s+\S+", r"\1 ***REDACTED***", cmd)
        return cmd


class AuditLogArchiver:
    """Archives audit log entries older than retention_days."""

    def __init__(
        self,
        retention_days: int,
        archive_dir: str | None = None,
        s3_config: dict | None = None,
    ):
        self._retention_days = retention_days
        self._archive_dir = archive_dir
        self._s3_config = s3_config or {}

    def archive_old_entries(self, log_dir: str) -> dict:
        """Move entries older than retention_days to archive_dir.

        Scans all .jsonl files in log_dir, separates entries into
        retained (recent) and archived (old), writes archived entries
        to a gzip-compressed JSONL file, and rewrites the source files
        with only retained entries.

        Returns:
            {"status": "success", "archived_count": N, "archive_path": "..."}
        """
        cutoff = datetime.now(tz=UTC) - timedelta(days=self._retention_days)
        archive_base = self._archive_dir or os.path.join(log_dir, "archive")
        os.makedirs(archive_base, exist_ok=True)

        archived_entries: list[str] = []
        log_path = Path(log_dir)

        for log_file in sorted(log_path.glob("*.jsonl")):
            retained_lines: list[str] = []
            with open(log_file) as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        entry = json.loads(stripped)
                    except json.JSONDecodeError:
                        retained_lines.append(stripped)
                        continue
                    ts_str = entry.get("timestamp")
                    if not ts_str:
                        retained_lines.append(stripped)
                        continue
                    try:
                        ts = datetime.fromisoformat(ts_str)
                    except (ValueError, TypeError):
                        retained_lines.append(stripped)
                        continue
                    if ts_str and ts < cutoff:
                        archived_entries.append(stripped)
                    else:
                        retained_lines.append(stripped)

            # Rewrite source file with only retained entries
            with open(log_file, "w") as f:
                for retained_line in retained_lines:
                    f.write(retained_line + "\n")

        if not archived_entries:
            return {"status": "success", "archived_count": 0, "archive_path": None}

        # Determine date range for archive filename
        dates: list[datetime] = []
        for line in archived_entries:
            try:
                entry = json.loads(line)
                ts = datetime.fromisoformat(entry["timestamp"])
                dates.append(ts)
            except (json.JSONDecodeError, KeyError, ValueError):
                pass

        if dates:
            min_date = min(dates).strftime("%Y-%m-%d")
            max_date = max(dates).strftime("%Y-%m-%d")
        else:
            today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
            min_date = max_date = today

        archive_name = f"audit_{min_date}_to_{max_date}.jsonl.gz"
        archive_path = os.path.join(archive_base, archive_name)

        with gzip.open(archive_path, "wt", encoding="utf-8") as gz:
            for entry_line in archived_entries:
                gz.write(entry_line + "\n")

        return {
            "status": "success",
            "archived_count": len(archived_entries),
            "archive_path": archive_path,
        }

    def upload_to_s3(self, archive_path: str) -> dict:
        """Upload archived log file to S3-compatible storage (requires boto3).

        Returns:
            {"status": "success"|"error", ...}
        """
        try:
            import boto3  # noqa: F811
        except ImportError:
            return {"status": "error", "error": "boto3 is required for S3 upload. Install with: pip install boto3"}

        bucket = self._s3_config.get("bucket")
        if not bucket:
            return {"status": "error", "error": "s3_config['bucket'] is required"}

        prefix = self._s3_config.get("prefix", "audit-logs/")
        region = self._s3_config.get("region", None)
        endpoint_url = self._s3_config.get("endpoint_url", None)

        filename = os.path.basename(archive_path)
        s3_key = f"{prefix}{filename}"

        try:
            client_kwargs: dict = {}
            if region:
                client_kwargs["region_name"] = region
            if endpoint_url:
                client_kwargs["endpoint_url"] = endpoint_url

            s3 = boto3.client("s3", **client_kwargs)
            s3.upload_file(archive_path, bucket, s3_key)
            return {
                "status": "success",
                "bucket": bucket,
                "key": s3_key,
                "size_bytes": os.path.getsize(archive_path),
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}


def _resolve_signing_key() -> bytes | None:
    """Resolve the HMAC signing key from settings.

    Priority: AUDIT_SIGNING_KEY > AUTH_SECRET_KEY > None (signing disabled).
    Logs warnings if keys are configured but empty or unavailable.
    """
    try:
        from network_mcp.config import NetworkSettings

        settings = NetworkSettings()
    except Exception:
        _logger.warning("Could not load NetworkSettings for audit HMAC key resolution; HMAC signing disabled")
        return None

    # Try dedicated audit signing key first
    if settings.audit_signing_key:
        value = settings.audit_signing_key.get_secret_value()
        if value:
            return value.encode()
        _logger.warning("AUDIT_SIGNING_KEY is set but empty; falling back to AUTH_SECRET_KEY")

    # Fall back to auth secret key
    if settings.auth_secret_key:
        value = settings.auth_secret_key.get_secret_value()
        if value:
            return value.encode()
        _logger.warning("AUTH_SECRET_KEY is set but empty; HMAC audit signing disabled")

    return None


def _build_audit_logger() -> AuditLogger:
    """Build the global AuditLogger with file rotation and syslog from settings."""
    signing_key = _resolve_signing_key()

    # Load settings for require_signing and key_version
    require_signing = False
    key_version = 1
    try:
        from network_mcp.config import NetworkSettings

        settings = NetworkSettings()
        require_signing = settings.net_require_audit_signing
        key_version = settings.audit_signing_key_version
    except Exception:
        _logger.warning("Could not load NetworkSettings for audit logger configuration")
        settings = None

    audit = AuditLogger(
        signing_key=signing_key,
        require_signing=require_signing,
        key_version=key_version,
    )

    if signing_key:
        _logger.info("Audit HMAC signing enabled (key_version=%d)", key_version)
    else:
        _logger.info("Audit HMAC signing disabled (no signing key configured)")

    if settings is None:
        return audit

    # File rotation
    if settings.audit_file:
        audit.configure_file_handler(
            audit_file=settings.audit_file,
            max_bytes=settings.audit_max_bytes,
            backup_count=settings.audit_backup_count,
        )
        _logger.info(
            "Audit file handler configured: %s (max_bytes=%d, backups=%d)",
            settings.audit_file,
            settings.audit_max_bytes,
            settings.audit_backup_count,
        )

    # Syslog forwarding (RFC 5424 structured format when NET_AUDIT_SYSLOG=true)
    if settings.net_audit_syslog or settings.audit_syslog_host != "localhost":
        audit.configure_syslog_handler(
            settings.audit_syslog_host,
            settings.audit_syslog_port,
            use_rfc5424=settings.net_audit_syslog,
        )
        fmt_label = "RFC 5424" if settings.net_audit_syslog else "default"
        _logger.info(
            "Audit syslog forwarding enabled: %s:%d (format=%s)",
            settings.audit_syslog_host,
            settings.audit_syslog_port,
            fmt_label,
        )

    return audit


# Global instance — used by helpers.py
audit_logger = _build_audit_logger()


def log_tool_invocation(tool_name: str, host: str, params: dict, result: dict, duration_ms: float) -> None:
    """Convenience function to log a tool call from helpers.

    Automatically includes the authenticated user from the current request context.
    """
    status = result.get("status", "unknown") if isinstance(result, dict) else "success"
    error = result.get("error") if isinstance(result, dict) and status == "error" else None
    audit_logger.log_tool_call(
        tool_name=tool_name,
        device=host,
        parameters=params,
        result_status=status,
        duration_ms=duration_ms,
        error=error,
    )


# --- Audit log export ---


def _parse_audit_file(file_path: str, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Read a JSONL audit file and return entries within the time range."""
    entries: list[dict] = []
    try:
        with open(file_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = entry.get("timestamp")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str)
                except (ValueError, TypeError):
                    continue
                if start_dt <= ts <= end_dt:
                    entries.append(entry)
    except FileNotFoundError:
        pass
    return entries


def _entries_to_csv(entries: list[dict]) -> str:
    """Convert audit entries to CSV format."""
    if not entries:
        return ""
    all_keys: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        for k in entry:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=all_keys, extrasaction="ignore")
    writer.writeheader()
    for entry in entries:
        # Flatten nested dicts/lists to JSON strings for CSV cells
        row = {}
        for k in all_keys:
            v = entry.get(k)
            if isinstance(v, (dict, list)):
                row[k] = json.dumps(v, default=str)
            else:
                row[k] = v
        writer.writerow(row)
    return output.getvalue()


def export_audit_log(
    audit_file: str,
    start: str | None = None,
    end: str | None = None,
    fmt: str = "json",
) -> dict:
    """Export audit log entries within a time range.

    Args:
        audit_file: Path to the JSONL audit log file.
        start: Start timestamp (ISO 8601). Default: 24h ago.
        end: End timestamp (ISO 8601). Default: now.
        fmt: Output format — 'json' or 'csv'.

    Returns:
        Dict with status, count, and data.
    """
    now = datetime.now(tz=UTC)
    start_dt = datetime.fromisoformat(start) if start else now - timedelta(hours=24)
    end_dt = datetime.fromisoformat(end) if end else now

    # Ensure timezone-aware
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=UTC)

    entries = _parse_audit_file(audit_file, start_dt, end_dt)

    if fmt == "csv":
        data = _entries_to_csv(entries)
    else:
        data = entries

    return {
        "status": "success",
        "count": len(entries),
        "format": fmt,
        "data": data,
    }
