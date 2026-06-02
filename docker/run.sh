#!/bin/bash
# Run the baglab container.
#
# Single mount: the entire baglab repo is bind-mounted at /workspace (rw).
# Bags live at ~/baglab/data/ (populated by scripts/sync_orin_bags.sh) and
# generated artifacts land in ~/baglab/outputs/. Both subdirs are gitignored.
#
# Usage:
#   ./docker/run.sh                  # interactive bash
#   ./docker/run.sh --build          # build image first
#   BAGLAB_EXTRA_MOUNT=/path:/extra ./docker/run.sh  # optional extra bind mount

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
IMAGE_NAME="baglab"

if [[ "$1" == "--build" ]]; then
    echo "Building $IMAGE_NAME..."
    docker build \
        -t $IMAGE_NAME \
        -f "$SCRIPT_DIR/Dockerfile" \
        "$REPO_DIR"
    echo "Build complete!"
    shift
fi

mkdir -p "$REPO_DIR/data" "$REPO_DIR/outputs"

EXTRA_MOUNT=()
if [[ -n "$BAGLAB_EXTRA_MOUNT" ]]; then
    EXTRA_MOUNT=(-v "$BAGLAB_EXTRA_MOUNT")
fi

exec docker run -it --rm \
    --network=host \
    -u "$(id -u):$(id -g)" \
    -v "$REPO_DIR":/workspace \
    "${EXTRA_MOUNT[@]}" \
    -w /workspace \
    $IMAGE_NAME \
    "${@:-/bin/bash}"
