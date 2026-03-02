"""Export tool for converting tool output to Markdown, CSV, or JSON reports."""

import csv
import io
import json
from datetime import datetime

from network_mcp.helpers import READ_ONLY
from network_mcp.server import mcp


def _flatten_dict(d: dict, parent_key: str = "", sep: str = ".") -> dict:
    """Recursively flatten a nested dict using dot notation for keys.

    Example: {"a": {"b": 1}} -> {"a.b": 1}
    Lists are converted to JSON strings. Depth is limited to avoid runaway recursion.
    """
    items: list[tuple[str, object]] = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else str(k)
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key, sep).items())
        elif isinstance(v, list):
            items.append((new_key, json.dumps(v, default=str)))
        else:
            items.append((new_key, v))
    return dict(items)


def _format_as_json(data: dict) -> str:
    """Pretty-print data as indented JSON."""
    return json.dumps(data, indent=2, default=str)


def _format_as_csv(data: dict | list) -> str:
    """Convert data to CSV format using flattened keys as headers.

    Handles three data shapes:
    - List of dicts: each dict becomes a row
    - Dict of dicts: each sub-dict becomes a row (outer key added as 'key' column)
    - Flat/nested dict: single row with flattened keys
    """
    output = io.StringIO()

    if isinstance(data, dict) and not data:
        return ""

    rows: list[dict] = []

    if isinstance(data, list):
        # List of dicts
        for item in data:
            if isinstance(item, dict):
                rows.append(_flatten_dict(item))
            else:
                rows.append({"value": item})
    elif isinstance(data, dict):
        values = list(data.values())
        if values and all(isinstance(v, dict) for v in values):
            # Dict of dicts — each value becomes a row, key added as first column
            for key, sub in data.items():
                flat = _flatten_dict(sub) if isinstance(sub, dict) else {"value": sub}
                flat = {"key": key, **flat}
                rows.append(flat)
        else:
            # Single dict — flatten to one row
            rows.append(_flatten_dict(data))

    if not rows:
        return ""

    # Collect all headers preserving order
    headers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for h in row:
            if h not in seen:
                headers.append(h)
                seen.add(h)

    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({h: row.get(h, "") for h in headers})

    return output.getvalue()


def _format_as_markdown(data: dict, title: str) -> str:
    """Convert data to a Markdown report with tables.

    Handles:
    - Flat dicts: single key-value table
    - Dict of dicts: tabular format with sub-dict keys as columns
    - Lists of dicts: tabular format
    - Nested structures: sub-tables per section
    """
    lines: list[str] = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"*Generated: {timestamp}*")
    lines.append("")

    _render_section(data, lines, level=2)

    return "\n".join(lines)


def _render_section(data: object, lines: list[str], level: int = 2, heading: str | None = None) -> None:
    """Recursively render a data structure as Markdown sections and tables."""
    prefix = "#" * min(level, 6)

    if heading:
        lines.append(f"{prefix} {heading}")
        lines.append("")

    if isinstance(data, dict):
        # Separate scalar values from nested structures
        scalars: dict[str, object] = {}
        nested: dict[str, object] = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                nested[k] = v
            else:
                scalars[k] = v

        # Render scalar key-value pairs as a table
        if scalars:
            lines.append("| Key | Value |")
            lines.append("|-----|-------|")
            for k, v in scalars.items():
                lines.append(f"| {k} | {v} |")
            lines.append("")

        # Render nested structures
        for k, v in nested.items():
            if isinstance(v, dict) and v and all(isinstance(sv, dict) for sv in v.values()):
                # Dict of dicts — render as table
                _render_dict_of_dicts(k, v, lines, level)
            elif isinstance(v, list) and v and all(isinstance(item, dict) for item in v):
                # List of dicts — render as table
                _render_list_of_dicts(k, v, lines, level)
            else:
                _render_section(v, lines, level + 1, heading=k)

    elif isinstance(data, list):
        if data and all(isinstance(item, dict) for item in data):
            _render_list_of_dicts(heading or "Items", data, lines, level)
        else:
            for item in data:
                lines.append(f"- {item}")
            lines.append("")
    else:
        lines.append(f"{data}")
        lines.append("")


def _render_dict_of_dicts(heading: str, data: dict, lines: list[str], level: int) -> None:
    """Render a dict-of-dicts as a Markdown table."""
    prefix = "#" * min(level, 6)
    lines.append(f"{prefix} {heading}")
    lines.append("")

    # Collect all sub-keys for column headers
    all_keys: list[str] = []
    seen: set[str] = set()
    for sub in data.values():
        if isinstance(sub, dict):
            for k in sub:
                if k not in seen:
                    all_keys.append(k)
                    seen.add(k)

    # Filter to only scalar columns for the table
    scalar_keys = []
    for k in all_keys:
        if all(not isinstance(sub.get(k), (dict, list)) for sub in data.values() if isinstance(sub, dict)):
            scalar_keys.append(k)

    if scalar_keys:
        header = "| key | " + " | ".join(scalar_keys) + " |"
        sep = "|-----|" + "|".join("-------" for _ in scalar_keys) + "|"
        lines.append(header)
        lines.append(sep)
        for key, sub in data.items():
            if isinstance(sub, dict):
                vals = " | ".join(str(sub.get(k, "")) for k in scalar_keys)
                lines.append(f"| {key} | {vals} |")
        lines.append("")
    else:
        # All sub-values are complex — render each as a sub-section
        for key, sub in data.items():
            _render_section(sub, lines, level + 1, heading=str(key))


def _render_list_of_dicts(heading: str, data: list, lines: list[str], level: int) -> None:
    """Render a list of dicts as a Markdown table."""
    prefix = "#" * min(level, 6)
    lines.append(f"{prefix} {heading}")
    lines.append("")

    # Collect all keys
    all_keys: list[str] = []
    seen: set[str] = set()
    for item in data:
        if isinstance(item, dict):
            for k in item:
                if k not in seen:
                    all_keys.append(k)
                    seen.add(k)

    if not all_keys:
        return

    header = "| " + " | ".join(all_keys) + " |"
    sep = "|" + "|".join("-------" for _ in all_keys) + "|"
    lines.append(header)
    lines.append(sep)
    for item in data:
        if isinstance(item, dict):
            vals = " | ".join(str(item.get(k, "")) for k in all_keys)
            lines.append(f"| {vals} |")
    lines.append("")


@mcp.tool(annotations=READ_ONLY)
def net_export_audit_log(
    start: str | None = None,
    end: str | None = None,
    format: str = "json",  # noqa: A002
) -> dict:
    """Export audit log entries within a time range.

    Args:
        start: Start timestamp (ISO 8601). Default: 24h ago.
        end: End timestamp (ISO 8601). Default: now.
        format: Output format — 'json' or 'csv'.
    """
    from network_mcp.audit import export_audit_log
    from network_mcp.server import settings

    audit_file = settings.audit_file
    if not audit_file:
        return {"status": "error", "error": "No audit file configured (set AUDIT_FILE)"}

    return export_audit_log(audit_file, start=start, end=end, fmt=format)


@mcp.tool(annotations=READ_ONLY)
def eos_export_report(data: dict, output_format: str = "markdown", title: str = "Network Report") -> dict:
    """Export tool output as a formatted report (Markdown, CSV, or JSON).

    When to use: Convert any tool output into a report format for documentation,
    ticketing systems, or management reviews.
    Output: Formatted report string in the specified format.

    Args:
        data: Output from any network-mcp tool (copy the result here).
        output_format: Export format — "markdown", "csv", or "json" (pretty-printed).
        title: Report title (used in markdown header).
    """
    fmt = output_format.lower().strip()

    if fmt == "json":
        report = _format_as_json(data)
    elif fmt == "csv":
        report = _format_as_csv(data)
    elif fmt == "markdown":
        report = _format_as_markdown(data, title)
    else:
        return {
            "status": "error",
            "error": f"Invalid format '{output_format}'. Supported: markdown, csv, json",
        }

    return {
        "status": "success",
        "format": fmt,
        "report": report,
    }
