"""Performance benchmark suite for network-mcp.

Run benchmarks:
    uv run pytest tests/benchmarks/ -v --benchmark-only

Compare against saved baseline:
    uv run pytest tests/benchmarks/ --benchmark-compare

Save results as baseline:
    uv run pytest tests/benchmarks/ --benchmark-save=baseline

Key metric — abstraction overhead:
    The delta between test_benchmark_direct_pyeapi_call and test_benchmark_mcp_tool_call
    measures the cost of run_show_command() (validation, rate limiting, caching, audit,
    sanitization, circuit breaker). Target: <5ms overhead per call. This baseline will
    be compared against the Phase 4a multi-vendor common tool abstraction layer.
"""
