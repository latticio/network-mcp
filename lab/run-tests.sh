#!/usr/bin/env bash
# lab/run-tests.sh — Automated integration test runner with HTML report
#
# Designed for overnight runs. Executes all integration tests in phases,
# generates an HTML report, and logs everything to a timestamped file.
#
# Usage:
#   ./lab/run-tests.sh                  # Run all phases
#   ./lab/run-tests.sh smoke            # Smoke tests only
#   ./lab/run-tests.sh --loop 6         # Run the full suite 6 times (overnight soak)
#   ./lab/run-tests.sh --loop 6 --interval 600  # 10-minute gap between runs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPORTS_DIR="$PROJECT_ROOT/lab/reports"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="$REPORTS_DIR/run-$TIMESTAMP.log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*" | tee -a "$LOG_FILE"; }
ok()    { echo -e "${GREEN}[PASS]${NC}  $*" | tee -a "$LOG_FILE"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*" | tee -a "$LOG_FILE"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*" | tee -a "$LOG_FILE"; }

mkdir -p "$REPORTS_DIR"

# --- Parse arguments ---

PHASES="all"
LOOP_COUNT=1
LOOP_INTERVAL=60  # seconds between loop iterations
PYTEST_EXTRA_ARGS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        smoke)      PHASES="smoke"; shift ;;
        show)       PHASES="show"; shift ;;
        tools)      PHASES="tools"; shift ;;
        write)      PHASES="write"; shift ;;
        pool)       PHASES="pool"; shift ;;
        clab)       PHASES="clab"; shift ;;
        all)        PHASES="all"; shift ;;
        --loop)     LOOP_COUNT="$2"; shift 2 ;;
        --interval) LOOP_INTERVAL="$2"; shift 2 ;;
        -v|--verbose) PYTEST_EXTRA_ARGS="$PYTEST_EXTRA_ARGS -v"; shift ;;
        *)          PYTEST_EXTRA_ARGS="$PYTEST_EXTRA_ARGS $1"; shift ;;
    esac
done

# --- Pre-flight checks ---

preflight() {
    info "=== Pre-flight Checks ==="

    # Check lab is running
    if ! command -v containerlab &>/dev/null; then
        fail "containerlab not installed"
        exit 1
    fi

    local containers
    containers=$(containerlab inspect --all --format json 2>/dev/null || echo "[]")
    local count
    count=$(python3 -c "
import json, sys
data = json.loads(sys.argv[1])
containers = []
if isinstance(data, list): containers = data
elif isinstance(data, dict):
    if 'containers' in data: containers = data['containers']
    else:
        for v in data.values():
            if isinstance(v, dict) and 'containers' in v: containers.extend(v['containers'])
            elif isinstance(v, list): containers.extend(v)
running = [c for c in containers if isinstance(c, dict) and c.get('state','').lower() in ('running','up')]
print(len(running))
" "$containers" 2>/dev/null || echo "0")

    if [[ "$count" == "0" ]]; then
        fail "No running lab nodes found. Deploy a lab first:"
        echo "  ./lab/setup.sh"
        exit 1
    fi
    ok "$count lab node(s) running"

    # Check pyeapi is installed
    if ! uv run python -c "import pyeapi" 2>/dev/null; then
        warn "pyeapi not importable — running uv sync"
        (cd "$PROJECT_ROOT" && uv sync --extra dev --extra arista)
    fi
    ok "Python dependencies OK"
}

# --- Test execution ---

run_phase() {
    local phase_name="$1"
    local test_path="$2"
    local extra="${3:-}"
    local report_file="$REPORTS_DIR/${phase_name}-${TIMESTAMP}.xml"

    info "--- Phase: $phase_name ---"
    local start_time
    start_time=$(date +%s)

    local exit_code=0
    (cd "$PROJECT_ROOT" && uv run pytest \
        "$test_path" \
        --run-integration \
        -n0 \
        --tb=short \
        --junitxml="$report_file" \
        $extra \
        $PYTEST_EXTRA_ARGS \
        2>&1 | tee -a "$LOG_FILE") || exit_code=$?

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))

    if [[ $exit_code -eq 0 ]]; then
        ok "$phase_name passed (${duration}s)"
    elif [[ $exit_code -eq 5 ]]; then
        warn "$phase_name: no tests collected (${duration}s)"
    else
        fail "$phase_name FAILED (exit $exit_code, ${duration}s)"
    fi

    return $exit_code
}

run_all_phases() {
    local iteration="${1:-1}"
    local total_exit=0

    info "=========================================="
    info "  Integration Test Run #$iteration"
    info "  Started: $(date)"
    info "=========================================="

    case "$PHASES" in
        smoke)
            run_phase "smoke" "tests/integration/test_smoke.py" || total_exit=1
            ;;
        show)
            run_phase "show-commands" "tests/integration/test_show_commands.py" || total_exit=1
            ;;
        tools)
            run_phase "tools-live" "tests/integration/test_tools_live.py" || total_exit=1
            ;;
        write)
            run_phase "write-ops" "tests/integration/test_write_operations.py" || total_exit=1
            ;;
        pool)
            run_phase "connection-pool" "tests/integration/test_connection_pool.py" || total_exit=1
            ;;
        clab)
            run_phase "containerlab" "tests/integration/test_containerlab_integration.py" || total_exit=1
            ;;
        all)
            # Run in dependency order: smoke first, then reads, then writes
            run_phase "smoke" "tests/integration/test_smoke.py" || { fail "Smoke tests failed — aborting"; return 1; }
            run_phase "show-commands" "tests/integration/test_show_commands.py" || total_exit=1
            run_phase "containerlab" "tests/integration/test_containerlab_integration.py" || total_exit=1
            run_phase "tools-live" "tests/integration/test_tools_live.py" || total_exit=1
            run_phase "connection-pool" "tests/integration/test_connection_pool.py" || total_exit=1
            run_phase "write-ops" "tests/integration/test_write_operations.py" || total_exit=1
            ;;
    esac

    echo "" | tee -a "$LOG_FILE"
    if [[ $total_exit -eq 0 ]]; then
        ok "=== Run #$iteration PASSED ==="
    else
        fail "=== Run #$iteration had FAILURES ==="
    fi

    return $total_exit
}

# --- Generate summary report ---

generate_summary() {
    local summary_file="$REPORTS_DIR/summary-$TIMESTAMP.txt"

    {
        echo "============================================"
        echo "  Integration Test Summary"
        echo "  Generated: $(date)"
        echo "  Machine: $(uname -m) / $(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'unknown')"
        echo "  RAM: $(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.0f GB", $1/1073741824}' || echo 'unknown')"
        echo "============================================"
        echo ""

        # Parse JUnit XML reports
        for xml_file in "$REPORTS_DIR"/*-"$TIMESTAMP".xml; do
            if [[ -f "$xml_file" ]]; then
                local phase
                phase=$(basename "$xml_file" | sed "s/-$TIMESTAMP.xml//")
                local tests errors failures
                tests=$(python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('$xml_file')
root = tree.getroot()
ts = root.find('.//testsuite') or root
print(ts.get('tests', '0'))
" 2>/dev/null || echo "?")
                failures=$(python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('$xml_file')
root = tree.getroot()
ts = root.find('.//testsuite') or root
print(ts.get('failures', '0'))
" 2>/dev/null || echo "?")
                errors=$(python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('$xml_file')
root = tree.getroot()
ts = root.find('.//testsuite') or root
print(ts.get('errors', '0'))
" 2>/dev/null || echo "?")
                local status="PASS"
                if [[ "$failures" != "0" || "$errors" != "0" ]]; then
                    status="FAIL"
                fi
                printf "  %-25s  tests=%-4s  fail=%-3s  err=%-3s  [%s]\n" "$phase" "$tests" "$failures" "$errors" "$status"
            fi
        done

        echo ""
        echo "Full log: $LOG_FILE"
        echo "JUnit XML reports: $REPORTS_DIR/*-$TIMESTAMP.xml"
    } > "$summary_file"

    cat "$summary_file" | tee -a "$LOG_FILE"
    info "Summary written to $summary_file"
}

# --- Main ---

preflight

overall_exit=0
for i in $(seq 1 "$LOOP_COUNT"); do
    run_all_phases "$i" || overall_exit=1

    # Sleep between iterations (except after the last one)
    if [[ "$i" -lt "$LOOP_COUNT" ]]; then
        info "Sleeping ${LOOP_INTERVAL}s before next iteration..."
        sleep "$LOOP_INTERVAL"
    fi
done

generate_summary

echo ""
if [[ $overall_exit -eq 0 ]]; then
    ok "All test runs passed"
else
    fail "Some test runs had failures — check reports"
fi

exit $overall_exit
