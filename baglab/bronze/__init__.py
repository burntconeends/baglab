"""Bronze tier — automated quality checks on raw G1 rosbags.

Implemented (pure timeseries, no ML/vision deps):
  - integrity      (priority 1): topic presence, jitter, dropped frames
  - smoothness     (priority 4): joint velocity/acceleration outliers
  - static_frames  (priority 5): inactivity windows to drop

Deferred (scaffolding only — see vision.py):
  - gripper/hand tracking (priority 2): needs HaMeR
  - object tracking/size  (priority 3): needs Grounded-SAM
"""
from baglab.bronze.integrity import check_integrity
from baglab.bronze.smoothness import check_smoothness
from baglab.bronze.static_frames import check_static_frames

__all__ = ["check_integrity", "check_smoothness", "check_static_frames"]
