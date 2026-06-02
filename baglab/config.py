"""Configuration for bronze-tier quality checks.

All thresholds live here with sensible defaults. A config can be loaded from a
JSON file and/or overridden by CLI flags (see baglab.cli).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path

from baglab.constants import ARM_JOINTS, DEFAULT_EXPECTED_TOPICS


@dataclass
class CheckConfig:
    # --- Data integrity (priority 1) ---
    expected_topics: list[str] = field(
        default_factory=lambda: list(DEFAULT_EXPECTED_TOPICS)
    )
    # A gap is flagged when an inter-message interval exceeds this multiple of
    # the topic's median interval AND exceeds gap_floor_s. The absolute floor
    # suppresses sub-100 ms record-time jitter on high-rate topics (/lowstate,
    # /dex3) that would otherwise produce dozens of false "gaps."
    gap_factor: float = 5.0
    gap_floor_s: float = 0.1
    # Topics whose median rate falls below this are flagged as suspiciously slow.
    min_rate_hz: float = 0.0  # 0 disables the rate floor check

    # --- Trajectory smoothness (priority 4) ---
    # Velocity/acceleration computed by finite-differencing /lowstate positions.
    vel_threshold: float = 6.0    # rad/s  (matches G1 joint velocity safety limit)
    accel_threshold: float = 60.0  # rad/s^2
    # Finite-difference window (seconds), shared by smoothness + static checks.
    # Larger than the sample period to low-pass sensor/quantization noise that
    # otherwise dominates derivatives of ~1 kHz /lowstate positions.
    deriv_window_s: float = 0.02
    # Robust spike factor: also flag samples whose |value| exceeds this multiple
    # of the per-joint median-absolute-deviation. 0 disables MAD spike detection.
    spike_mad_factor: float = 0.0
    # Cap on individual violation events recorded in the report (counts are exact).
    max_events: int = 200

    # --- Static-frame scrubbing (priority 5) ---
    # A sample is "inactive" when max |velocity| over static_joints is below this.
    static_vel_threshold: float = 0.02  # rad/s
    # Contiguous inactive spans at/above this duration are reported as drop ranges.
    min_static_duration: float = 1.0    # seconds
    # Joint indices considered for inactivity (default: both arms).
    static_joints: list[int] = field(default_factory=lambda: list(ARM_JOINTS))

    # --- Reader ---
    # Bound /lowstate samples held in RAM. ~600k = 10 min at 1 kHz x 29 joints,
    # which lands around ~280 MB of float64. Decimation is uniform stride, and
    # the report surfaces a `decimated` flag so the user knows.
    max_lowstate_samples: int = 600_000

    @classmethod
    def from_file(cls, path: str | Path) -> "CheckConfig":
        """Load config from a JSON file, falling back to defaults for any
        unspecified field. Unknown keys raise a clear error."""
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "CheckConfig":
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(
                f"Unknown config keys: {sorted(unknown)}. Valid keys: {sorted(known)}"
            )
        return cls(**{k: v for k, v in raw.items() if k in known})

    def apply_overrides(self, **overrides) -> "CheckConfig":
        """Return a copy with any non-None overrides applied (used for CLI flags)."""
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        for key, value in overrides.items():
            if value is not None:
                data[key] = value
        return CheckConfig(**data)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}
