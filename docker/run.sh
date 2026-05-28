#!/bin/bash
# Run the baglab container.
# Mounts the baglab repo (read-only) and a bag data directory (read-write).
#
# Usage:
#   ./docker/run.sh                  # interactive bash
#   ./docker/run.sh --build          # build image first
#
# Bag data directory (host) defaults to the GR00T-WholeBodyControl outputs,
# override with BAGLAB_BAGS_ROOT:
#   BAGLAB_BAGS_ROOT=/path/to/bags ./docker/run.sh

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
IMAGE_NAME="baglab"

# Host directory holding the bags (orin_bags/, *.mcap, etc.)
BAGS_ROOT="${BAGLAB_BAGS_ROOT:-$HOME/GR00T/GR00T-WholeBodyControl/outputs}"

if [[ "$1" == "--build" ]]; then
    echo "Building $IMAGE_NAME..."
    docker build \
        -t $IMAGE_NAME \
        -f "$SCRIPT_DIR/Dockerfile" \
        "$REPO_DIR"
    echo "Build complete!"
    shift
fi

mkdir -p "$BAGS_ROOT"

exec docker run -it --rm \
    --network=host \
    -u 0:0 \
    -v "$REPO_DIR":/workspace:ro \
    -v "$BAGS_ROOT":/workspace/data \
    -w /workspace \
    $IMAGE_NAME \
    "${@:-/bin/bash}"
