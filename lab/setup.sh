#!/usr/bin/env bash
# lab/setup.sh — Bootstrap script for containerlab integration testing
#
# This script:
#   1. Checks prerequisites (Docker, containerlab, cEOS image)
#   2. Deploys the chosen topology
#   3. Waits for all nodes to become eAPI-reachable
#   4. Generates a devices.yaml inventory from the running lab
#
# Usage:
#   ./lab/setup.sh                    # Deploy 2-node lab (default)
#   ./lab/setup.sh spine-leaf         # Deploy 6-node spine-leaf lab
#   ./lab/setup.sh destroy            # Tear down the running lab
#   ./lab/setup.sh status             # Show lab status

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TOPO_DIR="$SCRIPT_DIR/topologies"
INVENTORY_FILE="$PROJECT_ROOT/lab/devices.yaml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# --- Prerequisite checks ---

check_docker() {
    if ! command -v docker &>/dev/null; then
        error "Docker not found. Install OrbStack (recommended) or Docker Desktop:"
        echo "  brew install orbstack"
        echo "  # or: brew install --cask docker"
        return 1
    fi
    if ! docker info &>/dev/null 2>&1; then
        error "Docker daemon not running. Start OrbStack or Docker Desktop first."
        return 1
    fi
    ok "Docker is running ($(docker --version | head -1))"
}

check_containerlab() {
    if ! command -v containerlab &>/dev/null; then
        error "containerlab not found. Install it:"
        echo "  brew install containerlab"
        echo "  # or: bash -c \"\$(curl -sL https://get.containerlab.dev)\""
        return 1
    fi
    ok "containerlab installed ($(containerlab version 2>/dev/null | head -1 || echo 'unknown version'))"
}

check_ceos_image() {
    # Check for any ceos image tag
    local images
    images=$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -i ceos || true)
    if [[ -z "$images" ]]; then
        error "No cEOS image found. Download cEOS-lab from arista.com and import it:"
        echo ""
        echo "  Steps:"
        echo "  1. Register (free) at https://www.arista.com/en/login"
        echo "  2. Go to: Software Downloads > cEOS-lab"
        echo "  3. Download the ARM64 (aarch64) .tar.xz file for Apple Silicon"
        echo "  4. Import it:"
        echo "     docker import cEOS64-lab-<version>.tar ceos:<version>"
        echo "     docker tag ceos:<version> ceos:latest"
        echo ""
        return 1
    fi
    ok "cEOS image(s) found:"
    echo "$images" | while read -r img; do echo "       $img"; done

    # Ensure ceos:latest tag exists
    if ! docker images --format '{{.Repository}}:{{.Tag}}' | grep -q '^ceos:latest$'; then
        warn "No 'ceos:latest' tag found. The topologies reference ceos:latest."
        echo "       Tag an existing image:  docker tag ceos:<version> ceos:latest"
        local first_tag
        first_tag=$(echo "$images" | head -1)
        echo "       Example:  docker tag $first_tag ceos:latest"
        return 1
    fi
}

# --- Lab operations ---

get_lab_name() {
    local topo="$1"
    case "$topo" in
        2-node|2node|small|"")     echo "netmcp-2node" ;;
        spine-leaf|fabric|medium)  echo "netmcp-fabric" ;;
        *)                         echo "netmcp-$topo" ;;
    esac
}

get_topo_file() {
    local topo="$1"
    case "$topo" in
        2-node|2node|small|"")     echo "$TOPO_DIR/2-node.clab.yaml" ;;
        spine-leaf|fabric|medium)  echo "$TOPO_DIR/spine-leaf.clab.yaml" ;;
        *)
            if [[ -f "$TOPO_DIR/$topo.clab.yaml" ]]; then
                echo "$TOPO_DIR/$topo.clab.yaml"
            else
                error "Unknown topology: $topo"
                echo ""
            fi
            ;;
    esac
}

wait_for_eapi() {
    local host="$1"
    local name="$2"
    local max_wait="${3:-180}"
    local interval=5
    local elapsed=0

    info "Waiting for eAPI on $name ($host)..."
    while (( elapsed < max_wait )); do
        # Try HTTPS first, fall back to HTTP
        if curl -sk --connect-timeout 3 "https://$host/command-api" \
            -u admin:admin \
            -d '{"jsonrpc":"2.0","method":"runCmds","params":{"version":1,"cmds":["show version"],"format":"json"},"id":1}' \
            -H 'Content-Type: application/json' 2>/dev/null | grep -q '"modelName"'; then
            ok "$name eAPI reachable (HTTPS, ${elapsed}s)"
            return 0
        fi
        if curl -sk --connect-timeout 3 "http://$host/command-api" \
            -u admin:admin \
            -d '{"jsonrpc":"2.0","method":"runCmds","params":{"version":1,"cmds":["show version"],"format":"json"},"id":1}' \
            -H 'Content-Type: application/json' 2>/dev/null | grep -q '"modelName"'; then
            ok "$name eAPI reachable (HTTP, ${elapsed}s)"
            return 0
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done

    error "$name eAPI not reachable after ${max_wait}s"
    return 1
}

generate_inventory() {
    info "Generating inventory at $INVENTORY_FILE..."

    # Use containerlab inspect to get node IPs
    local inspect_json
    inspect_json=$(containerlab inspect --all --format json 2>/dev/null || echo "[]")

    cat > "$INVENTORY_FILE" <<'HEADER'
# Auto-generated by lab/setup.sh from running containerlab topology
# Do NOT commit this file — it contains lab-specific IPs
devices:
HEADER

    # Parse containerlab inspect output and generate device entries
    # containerlab inspect JSON can vary by version, so we use Python for reliable parsing
    python3 - "$inspect_json" >> "$INVENTORY_FILE" <<'PYEOF'
import json
import sys

try:
    data = json.loads(sys.argv[1])
except (json.JSONDecodeError, IndexError):
    sys.exit(0)

# Normalize: could be list, {"containers": [...]}, or {"labname": {"containers": [...]}}
containers = []
if isinstance(data, list):
    containers = data
elif isinstance(data, dict):
    if "containers" in data:
        containers = data["containers"]
    else:
        for lab_data in data.values():
            if isinstance(lab_data, dict) and "containers" in lab_data:
                containers.extend(lab_data["containers"])
            elif isinstance(lab_data, list):
                containers.extend(lab_data)

for c in containers:
    if not isinstance(c, dict):
        continue
    state = c.get("state", "").lower()
    if state not in ("running", "up"):
        continue
    name = c.get("name", "")
    lab = c.get("lab_name", "")
    ip = c.get("ipv4_address", c.get("mgmt_ipv4", ""))
    if "/" in ip:
        ip = ip.split("/")[0]
    if not name or not ip:
        continue
    # Extract short node name from clab-<lab>-<node>
    prefix = f"clab-{lab}-"
    short_name = name[len(prefix):] if name.startswith(prefix) else name
    print(f"  {short_name}:")
    print(f"    host: {ip}")
    print(f"    platform: eos")
    print(f"    username: admin")
    print(f"    password: admin")
    print(f"    transport: https")
    print(f"    port: 443")
    print(f"    tags: [containerlab, lab:{lab}]")
    print()
PYEOF

    ok "Inventory written to $INVENTORY_FILE"
}

deploy_lab() {
    local topo="${1:-}"
    local topo_file
    topo_file=$(get_topo_file "$topo")
    if [[ -z "$topo_file" ]]; then
        return 1
    fi

    info "=== Prerequisite Checks ==="
    check_docker
    check_containerlab
    check_ceos_image

    echo ""
    info "=== Deploying Lab ==="
    info "Topology: $topo_file"
    echo ""

    sudo containerlab deploy -t "$topo_file" --reconfigure

    echo ""
    info "=== Waiting for Nodes ==="

    # Get all node IPs from inspect
    sleep 5  # Brief pause for containers to get IPs

    local inspect_json
    inspect_json=$(containerlab inspect --all --format json 2>/dev/null || echo "[]")

    # Extract IPs and wait for eAPI on each
    python3 - "$inspect_json" <<'PYEOF' | while read -r line; do
import json, sys
try:
    data = json.loads(sys.argv[1])
except (json.JSONDecodeError, IndexError):
    sys.exit(0)
containers = []
if isinstance(data, list):
    containers = data
elif isinstance(data, dict):
    if "containers" in data:
        containers = data["containers"]
    else:
        for ld in data.values():
            if isinstance(ld, dict) and "containers" in ld:
                containers.extend(ld["containers"])
            elif isinstance(ld, list):
                containers.extend(ld)
for c in containers:
    if not isinstance(c, dict):
        continue
    if c.get("state", "").lower() not in ("running", "up"):
        continue
    name = c.get("name", "")
    lab = c.get("lab_name", "")
    ip = c.get("ipv4_address", c.get("mgmt_ipv4", ""))
    if "/" in ip:
        ip = ip.split("/")[0]
    if name and ip:
        prefix = f"clab-{lab}-"
        short = name[len(prefix):] if name.startswith(prefix) else name
        print(f"{ip} {short}")
PYEOF
        local ip name
        ip=$(echo "$line" | awk '{print $1}')
        name=$(echo "$line" | awk '{print $2}')
        wait_for_eapi "$ip" "$name" 180
    done

    echo ""
    info "=== Generating Inventory ==="
    generate_inventory

    echo ""
    info "=== Lab Ready ==="
    ok "Lab deployed and all nodes responding to eAPI"
    echo ""
    echo "  Next steps:"
    echo "    1. Run smoke tests:     uv run pytest tests/integration/ -v -k smoke"
    echo "    2. Run all integration: uv run pytest tests/integration/ -v"
    echo "    3. Run overnight suite: ./lab/run-tests.sh"
    echo ""
    echo "  Environment for manual testing:"
    echo "    NET_INVENTORY_FILE=$INVENTORY_FILE NET_READ_ONLY=false NET_DEMO_MODE=false uv run network-mcp"
    echo ""
}

destroy_lab() {
    info "Discovering running labs..."
    local labs
    labs=$(containerlab inspect --all --format json 2>/dev/null || echo "")

    if [[ -z "$labs" || "$labs" == "[]" ]]; then
        warn "No running labs found."
        return 0
    fi

    # Destroy all netmcp labs
    for lab_name in netmcp-2node netmcp-fabric; do
        if echo "$labs" | grep -q "$lab_name"; then
            info "Destroying $lab_name..."
            sudo containerlab destroy --name "$lab_name" --cleanup 2>/dev/null || true
        fi
    done

    # Clean up generated inventory
    if [[ -f "$INVENTORY_FILE" ]]; then
        rm "$INVENTORY_FILE"
        info "Removed generated inventory"
    fi

    ok "Labs destroyed"
}

show_status() {
    info "Lab status:"
    containerlab inspect --all 2>/dev/null || warn "No running labs or containerlab not available"
}

# --- Main ---

case "${1:-}" in
    destroy|down|teardown)
        destroy_lab
        ;;
    status|inspect)
        show_status
        ;;
    help|-h|--help)
        echo "Usage: $0 [topology|command]"
        echo ""
        echo "  Topologies:"
        echo "    (empty), 2-node, small  — 2-node minimal lab (~3 GB RAM)"
        echo "    spine-leaf, fabric      — 6-node spine-leaf (~10 GB RAM)"
        echo ""
        echo "  Commands:"
        echo "    destroy   — Tear down all running netmcp labs"
        echo "    status    — Show running lab status"
        echo "    help      — This message"
        ;;
    *)
        deploy_lab "${1:-}"
        ;;
esac
