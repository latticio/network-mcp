#!/usr/bin/env python3
"""Generate a markdown summary from pytest-benchmark JSON output.

Usage:
    # Save benchmark results as JSON:
    uv run pytest tests/benchmarks/ --benchmark-only --benchmark-json=benchmark_results.json

    # Generate markdown report:
    uv run python scripts/benchmark_report.py benchmark_results.json

    # Output to file:
    uv run python scripts/benchmark_report.py benchmark_results.json -o benchmark_report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def _format_time(seconds: float) -> str:
    """Format time in human-readable units."""
    if seconds < 1e-6:
        return f"{seconds * 1e9:.1f} ns"
    if seconds < 1e-3:
        return f"{seconds * 1e6:.1f} us"
    if seconds < 1.0:
        return f"{seconds * 1e3:.2f} ms"
    return f"{seconds:.3f} s"


def _format_ops(ops_per_sec: float) -> str:
    """Format operations per second."""
    if ops_per_sec >= 1e6:
        return f"{ops_per_sec / 1e6:.1f}M ops/s"
    if ops_per_sec >= 1e3:
        return f"{ops_per_sec / 1e3:.1f}K ops/s"
    return f"{ops_per_sec:.0f} ops/s"


def generate_report(data: dict) -> str:
    """Generate a markdown report from pytest-benchmark JSON data."""
    lines: list[str] = []

    # Header
    machine = data.get("machine_info", {})
    commit = data.get("commit_info", {})
    lines.append("# Benchmark Results")
    lines.append("")

    # Environment info
    lines.append("## Environment")
    lines.append("")
    if machine:
        lines.append(f"- **Machine**: {machine.get('node', 'unknown')}")
        lines.append(f"- **CPU**: {machine.get('processor', 'unknown')}")
        lines.append(f"- **Python**: {machine.get('python_version', 'unknown')}")
        lines.append(f"- **Platform**: {machine.get('platform', 'unknown')}")
    if commit and commit.get("id"):
        lines.append(f"- **Commit**: `{commit['id'][:8]}`")
    lines.append("")

    benchmarks = data.get("benchmarks", [])
    if not benchmarks:
        lines.append("*No benchmark results found.*")
        return "\n".join(lines)

    # Group benchmarks
    groups: dict[str, list[dict]] = defaultdict(list)
    for bench in benchmarks:
        group = bench.get("group", "ungrouped")
        groups[group].append(bench)

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Benchmark | Mean | Min | Max | StdDev | Rounds | Ops/s |")
    lines.append("|-----------|------|-----|-----|--------|--------|-------|")
    for bench in sorted(benchmarks, key=lambda b: b["stats"]["mean"]):
        stats = bench["stats"]
        name = bench.get("name", "unknown").split("::")[-1]
        lines.append(
            f"| {name} "
            f"| {_format_time(stats['mean'])} "
            f"| {_format_time(stats['min'])} "
            f"| {_format_time(stats['max'])} "
            f"| {_format_time(stats['stddev'])} "
            f"| {stats['rounds']} "
            f"| {_format_ops(stats['ops'])} |"
        )
    lines.append("")

    # Per-group breakdown
    lines.append("## By Group")
    lines.append("")
    for group_name in sorted(groups):
        group_benches = groups[group_name]
        lines.append(f"### {group_name}")
        lines.append("")
        lines.append("| Benchmark | Mean | Min | Median | Ops/s |")
        lines.append("|-----------|------|-----|--------|-------|")
        for bench in sorted(group_benches, key=lambda b: b["stats"]["mean"]):
            stats = bench["stats"]
            name = bench.get("name", "unknown").split("::")[-1]
            lines.append(
                f"| {name} "
                f"| {_format_time(stats['mean'])} "
                f"| {_format_time(stats['min'])} "
                f"| {_format_time(stats['median'])} "
                f"| {_format_ops(stats['ops'])} |"
            )
        lines.append("")

    # Performance highlights
    fastest = min(benchmarks, key=lambda b: b["stats"]["mean"])
    slowest = max(benchmarks, key=lambda b: b["stats"]["mean"])
    lines.append("## Highlights")
    lines.append("")
    lines.append(
        f"- **Fastest**: {fastest['name'].split('::')[-1]} "
        f"({_format_time(fastest['stats']['mean'])})"
    )
    lines.append(
        f"- **Slowest**: {slowest['name'].split('::')[-1]} "
        f"({_format_time(slowest['stats']['mean'])})"
    )
    lines.append(f"- **Total benchmarks**: {len(benchmarks)}")
    lines.append(f"- **Groups**: {len(groups)}")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate markdown from pytest-benchmark JSON")
    parser.add_argument("input", type=Path, help="Path to pytest-benchmark JSON output file")
    parser.add_argument("-o", "--output", type=Path, help="Output markdown file (default: stdout)")
    args = parser.parse_args()

    if not args.input.exists():
        sys.stderr.write(f"Error: {args.input} not found\n")
        return 1

    with args.input.open() as f:
        data = json.load(f)

    report = generate_report(data)

    if args.output:
        args.output.write_text(report)
        sys.stderr.write(f"Report written to {args.output}\n")
    else:
        sys.stdout.write(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
