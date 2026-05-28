#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/sync_orin_bags.sh [DEST_DIR]

Mirrors rosbag recordings from the G1 Orin into a local gitignored directory.

Defaults:
  Remote:      unitree@192.168.123.164
  Remote dir:  /home/unitree/bags
  Local dest:  <repo>/data/orin_bags

Environment overrides:
  ORIN_BAG_REMOTE      SSH remote, e.g. unitree@192.168.123.164
  ORIN_BAG_REMOTE_DIR  Remote bag directory, e.g. /home/unitree/bags
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REMOTE="${ORIN_BAG_REMOTE:-unitree@192.168.123.164}"
REMOTE_DIR="${ORIN_BAG_REMOTE_DIR:-/home/unitree/bags}"
DEST_DIR="${1:-${BAGLAB_BAGS_ROOT:-${REPO_ROOT}/data}/orin_bags}"

mkdir -p "${DEST_DIR}"

echo "[sync_orin_bags] Remote: ${REMOTE}:${REMOTE_DIR%/}/"
echo "[sync_orin_bags] Local:  ${DEST_DIR%/}/"
echo "[sync_orin_bags] Starting rsync. Existing files are skipped or resumed."

rsync -avh --info=progress2 --partial \
    "${REMOTE}:${REMOTE_DIR%/}/" \
    "${DEST_DIR%/}/"
