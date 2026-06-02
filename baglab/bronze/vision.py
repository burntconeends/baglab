"""Priorities 2 & 3 — DEFERRED vision-based checks (scaffolding only).

These require vision models and are intentionally NOT implemented in the bronze
timeseries pipeline. The interfaces are sketched here so a later iteration can
fill them in without reshaping the report model.

  Priority 2 — gripper / hand vision tracking:
      Track hand/gripper pose from /zed/image_raw/compressed (and optionally the
      Dex3 hand joint states) to verify the grasp aligns with the commanded
      action. TODO: integrate HaMeR (hand mesh recovery).

  Priority 3 — object tracking / size:
      Detect and track the manipulated object across the episode, estimating
      bounding box / size and trajectory. TODO: integrate Grounded-SAM
      (open-vocabulary detection + segmentation).

Both add heavy ML/vision dependencies (torch, model weights) that the bronze
tier deliberately avoids. Keep them out of the default `baglab check` path.
"""
from __future__ import annotations

from baglab.config import CheckConfig
from baglab.reader import BagData


def check_gripper_tracking(bag: BagData, config: CheckConfig):
    """TODO(priority 2): hand/gripper tracking via HaMeR. Not implemented."""
    raise NotImplementedError(
        "gripper/hand vision tracking (priority 2) is deferred — requires HaMeR"
    )


def check_object_tracking(bag: BagData, config: CheckConfig):
    """TODO(priority 3): object detection/tracking/size via Grounded-SAM."""
    raise NotImplementedError(
        "object tracking/size (priority 3) is deferred — requires Grounded-SAM"
    )
