"""Tests for the export report tool."""

import csv
import io
import json

from network_mcp.tools.export import (
    _flatten_dict,
    _format_as_csv,
    _format_as_json,
    _format_as_markdown,
    eos_export_report,
)
from tests.conftest import MOCK_BGP_SUMMARY, MOCK_SHOW_VERSION


class TestFlattenDict:
    def test_flat_dict_unchanged(self):
        data = {"a": 1, "b": "hello"}
        assert _flatten_dict(data) == {"a": 1, "b": "hello"}

    def test_nested_dict(self):
        data = {"a": {"b": 1, "c": {"d": 2}}}
        result = _flatten_dict(data)
        assert result == {"a.b": 1, "a.c.d": 2}

    def test_list_values_become_json(self):
        data = {"tags": [1, 2, 3]}
        result = _flatten_dict(data)
        assert result["tags"] == "[1, 2, 3]"

    def test_empty_dict(self):
        assert _flatten_dict({}) == {}

    def test_custom_separator(self):
        data = {"a": {"b": 1}}
        result = _flatten_dict(data, sep="/")
        assert result == {"a/b": 1}


class TestFormatAsJson:
    def test_pretty_printed(self):
        data = {"key": "value"}
        result = _format_as_json(data)
        parsed = json.loads(result)
        assert parsed == data
        assert "\n" in result  # indented

    def test_handles_non_serializable(self):
        """default=str handles types like datetime."""
        from datetime import datetime

        data = {"ts": datetime(2024, 1, 1)}
        result = _format_as_json(data)
        parsed = json.loads(result)
        assert "2024" in parsed["ts"]

    def test_mock_show_version(self):
        result = _format_as_json(MOCK_SHOW_VERSION)
        parsed = json.loads(result)
        assert parsed["hostname"] == "test-switch"
        assert parsed["version"] == "4.32.1F"


class TestFormatAsCsv:
    def test_flat_dict_single_row(self):
        data = {"name": "spine-01", "version": "4.32.1F"}
        result = _format_as_csv(data)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["name"] == "spine-01"

    def test_dict_of_dicts(self):
        data = {
            "Ethernet1": {"status": "up", "speed": "1G"},
            "Ethernet2": {"status": "down", "speed": "10G"},
        }
        result = _format_as_csv(data)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["key"] == "Ethernet1"
        assert rows[0]["status"] == "up"
        assert rows[1]["key"] == "Ethernet2"

    def test_nested_dict_flattened(self):
        """A dict-of-dicts is treated as rows with a 'key' column."""
        data = {"device": {"info": {"hostname": "sw1", "version": "4.32"}}}
        result = _format_as_csv(data)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["key"] == "device"
        assert rows[0]["info.hostname"] == "sw1"

    def test_mock_show_version_csv(self):
        result = _format_as_csv(MOCK_SHOW_VERSION)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["hostname"] == "test-switch"

    def test_empty_dict(self):
        result = _format_as_csv({})
        assert result == ""

    def test_list_data_as_csv(self):
        """The tool wraps list data, but _format_as_csv handles lists directly."""
        data = [
            {"name": "spine-01", "role": "spine"},
            {"name": "leaf-01", "role": "leaf"},
        ]
        result = _format_as_csv(data)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["name"] == "spine-01"
        assert rows[1]["role"] == "leaf"


class TestFormatAsMarkdown:
    def test_has_title_and_timestamp(self):
        result = _format_as_markdown({"a": 1}, "Test Report")
        assert "# Test Report" in result
        assert "*Generated:" in result

    def test_flat_dict_table(self):
        data = {"hostname": "spine-01", "version": "4.32.1F"}
        result = _format_as_markdown(data, "Device Info")
        assert "| Key | Value |" in result
        assert "| hostname | spine-01 |" in result
        assert "| version | 4.32.1F |" in result

    def test_mock_show_version_markdown(self):
        result = _format_as_markdown(MOCK_SHOW_VERSION, "Show Version")
        assert "# Show Version" in result
        assert "| hostname | test-switch |" in result
        assert "| version | 4.32.1F |" in result

    def test_nested_dict_subtables(self):
        data = MOCK_BGP_SUMMARY
        result = _format_as_markdown(data, "BGP Summary")
        assert "# BGP Summary" in result
        # Should render vrfs > default > peers as nested sections
        assert "vrfs" in result

    def test_dict_of_dicts_as_table(self):
        data = {
            "interfaces": {
                "Ethernet1": {"status": "up", "speed": "1G"},
                "Ethernet2": {"status": "down", "speed": "10G"},
            }
        }
        result = _format_as_markdown(data, "Interfaces")
        assert "| key |" in result
        assert "Ethernet1" in result
        assert "Ethernet2" in result

    def test_list_of_dicts_as_table(self):
        data = {
            "items": [
                {"name": "spine-01", "role": "spine"},
                {"name": "leaf-01", "role": "leaf"},
            ]
        }
        result = _format_as_markdown(data, "Inventory")
        assert "| name | role |" in result
        assert "spine-01" in result
        assert "leaf-01" in result

    def test_empty_data(self):
        result = _format_as_markdown({}, "Empty Report")
        assert "# Empty Report" in result


class TestEosExportReport:
    def test_json_format(self):
        data = {"hostname": "sw1", "version": "4.32"}
        result = eos_export_report(data, output_format="json")
        assert result["status"] == "success"
        assert result["format"] == "json"
        parsed = json.loads(result["report"])
        assert parsed["hostname"] == "sw1"

    def test_csv_format(self):
        data = {"hostname": "sw1", "version": "4.32"}
        result = eos_export_report(data, output_format="csv")
        assert result["status"] == "success"
        assert result["format"] == "csv"
        reader = csv.DictReader(io.StringIO(result["report"]))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["hostname"] == "sw1"

    def test_markdown_format(self):
        data = {"hostname": "sw1", "version": "4.32"}
        result = eos_export_report(data, output_format="markdown", title="Device Report")
        assert result["status"] == "success"
        assert result["format"] == "markdown"
        assert "# Device Report" in result["report"]
        assert "| hostname | sw1 |" in result["report"]

    def test_invalid_format_returns_error(self):
        result = eos_export_report({"a": 1}, output_format="xml")
        assert result["status"] == "error"
        assert "Invalid format 'xml'" in result["error"]
        assert "markdown, csv, json" in result["error"]

    def test_format_case_insensitive(self):
        result = eos_export_report({"a": 1}, output_format="JSON")
        assert result["status"] == "success"
        assert result["format"] == "json"

    def test_format_with_whitespace(self):
        result = eos_export_report({"a": 1}, output_format="  csv  ")
        assert result["status"] == "success"
        assert result["format"] == "csv"

    def test_empty_data_json(self):
        result = eos_export_report({}, output_format="json")
        assert result["status"] == "success"
        assert json.loads(result["report"]) == {}

    def test_empty_data_markdown(self):
        result = eos_export_report({}, output_format="markdown")
        assert result["status"] == "success"
        assert "# Network Report" in result["report"]

    def test_empty_data_csv(self):
        result = eos_export_report({}, output_format="csv")
        assert result["status"] == "success"

    def test_mock_show_version_json(self):
        result = eos_export_report(MOCK_SHOW_VERSION, output_format="json", title="Version")
        assert result["status"] == "success"
        parsed = json.loads(result["report"])
        assert parsed["hostname"] == "test-switch"

    def test_mock_show_version_csv(self):
        result = eos_export_report(MOCK_SHOW_VERSION, output_format="csv")
        assert result["status"] == "success"
        reader = csv.DictReader(io.StringIO(result["report"]))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["hostname"] == "test-switch"

    def test_mock_show_version_markdown(self):
        result = eos_export_report(MOCK_SHOW_VERSION, output_format="markdown", title="Show Version Report")
        assert result["status"] == "success"
        assert "# Show Version Report" in result["report"]
        assert "test-switch" in result["report"]

    def test_mock_bgp_summary_markdown(self):
        result = eos_export_report(MOCK_BGP_SUMMARY, output_format="markdown", title="BGP Report")
        assert result["status"] == "success"
        report = result["report"]
        assert "# BGP Report" in report
        assert "vrfs" in report

    def test_mock_bgp_summary_csv(self):
        result = eos_export_report(MOCK_BGP_SUMMARY, output_format="csv")
        assert result["status"] == "success"
        # CSV should have flattened dot-notation keys
        assert "vrfs.default" in result["report"] or "key" in result["report"]

    def test_default_format_is_markdown(self):
        result = eos_export_report({"a": 1})
        assert result["status"] == "success"
        assert result["format"] == "markdown"

    def test_default_title(self):
        result = eos_export_report({"a": 1})
        assert "# Network Report" in result["report"]

    def test_list_of_dicts_data(self):
        """Test that when data contains a list of dicts, tables are generated."""
        data = {
            "devices": [
                {"hostname": "spine-01", "model": "DCS-7280SR", "version": "4.32.1F"},
                {"hostname": "leaf-01", "model": "DCS-7050TX", "version": "4.31.2F"},
            ]
        }
        result = eos_export_report(data, output_format="markdown", title="Inventory")
        assert result["status"] == "success"
        assert "spine-01" in result["report"]
        assert "leaf-01" in result["report"]
        assert "| hostname |" in result["report"] or "hostname" in result["report"]

    def test_nested_csv_dot_notation(self):
        """Verify deeply nested keys get dot-notation in CSV."""
        data = {"level1": {"level2": {"level3": "deep_value"}}}
        result = eos_export_report(data, output_format="csv")
        assert result["status"] == "success"
        # Dict-of-dicts: outer key becomes 'key' column, inner keys are flattened
        assert "level2.level3" in result["report"]
        assert "deep_value" in result["report"]
