"""Priority 4 — trajectory smoothness.

Computes per-joint velocity and acceleration from /lowstate positions using
windowed central differences (see bronze/kinematics.py), then flags:
  - velocity spikes:     |v| > vel_threshold
  - acceleration spikes: |a| > accel_threshold  (a discontinuity in velocity)
  - optionally, robust MAD-based spikes when spike_mad_factor > 0.

Reports per-joint stats (max |v|, max |a|, violation counts) plus a list of
individual violation events (timestamped), capped at config.max_events.
"""
from __future__ import annotations

import numpy as np

from baglab.bronze.kinematics import central_derivatives
from baglab.config import CheckConfig
from baglab.constants import G1_JOINT_NAMES
from baglab.reader import BagData
from baglab.report import JointSmoothness, SmoothnessResult, SmoothnessViolation


def _mad(x: np.ndarray) -> float:
    """Median absolute deviation (robust spread)."""
    med = np.median(x)
    return float(np.median(np.abs(x - med)))


def check_smoothness(bag: BagData, config: CheckConfig) -> SmoothnessResult:
    ls = bag.lowstate
    if ls is None or ls.timestamps_ns.size < 3:
        return SmoothnessResult(
            n_samples=0 if ls is None else int(ls.timestamps_ns.size),
            vel_threshold=config.vel_threshold,
            accel_threshold=config.accel_threshold,
            per_joint=[], violations=[], n_violations_total=0,
            violations_truncated=False, passed=True,
            note="insufficient /lowstate samples for smoothness analysis",
        )

    q = ls.q                                        # (N, J)
    n, n_joints = q.shape
    start_s = bag.start_ns / 1e9

    # Windowed central derivatives (see bronze/kinematics.py) — low-passes the
    # noise that dominates sample-to-sample derivatives of ~1 kHz positions.
    deriv_t, vel, accel = central_derivatives(
        ls.timestamps_ns, q, config.deriv_window_s)
    if vel.shape[0] == 0:
        return SmoothnessResult(
            n_samples=n, vel_threshold=config.vel_threshold,
            accel_threshold=config.accel_threshold, per_joint=[], violations=[],
            n_violations_total=0, violations_truncated=False, passed=True,
            note="insufficient /lowstate samples for the derivative window",
        )
    vel_t = deriv_t
    accel_t = deriv_t

    # Optional robust per-joint thresholds (take the stricter of abs / MAD).
    vel_thr = np.full(n_joints, config.vel_threshold, dtype=np.float64)
    accel_thr = np.full(n_joints, config.accel_threshold, dtype=np.float64)
    if config.spike_mad_factor > 0:
        for j in range(n_joints):
            vj = vel[:, j][np.isfinite(vel[:, j])]
            aj = accel[:, j][np.isfinite(accel[:, j])]
            if vj.size:
                vel_thr[j] = min(vel_thr[j],
                                 config.spike_mad_factor * (_mad(vj) or np.inf))
            if aj.size:
                accel_thr[j] = min(accel_thr[j],
                                   config.spike_mad_factor * (_mad(aj) or np.inf))

    per_joint: list[JointSmoothness] = []
    violations: list[SmoothnessViolation] = []
    n_total = 0

    abs_vel = np.abs(vel)
    abs_accel = np.abs(accel)

    for j in range(n_joints):
        name = G1_JOINT_NAMES[j] if j < len(G1_JOINT_NAMES) else f"joint_{j}"
        vmask = abs_vel[:, j] > vel_thr[j]
        amask = abs_accel[:, j] > accel_thr[j]
        n_v = int(np.nansum(vmask))
        n_a = int(np.nansum(amask))
        n_total += n_v + n_a

        max_v = float(np.nanmax(abs_vel[:, j])) if abs_vel.shape[0] else 0.0
        max_a = float(np.nanmax(abs_accel[:, j])) if abs_accel.shape[0] else 0.0
        per_joint.append(JointSmoothness(
            joint_index=j, joint_name=name,
            max_abs_vel=max_v, max_abs_accel=max_a,
            n_vel_violations=n_v, n_accel_violations=n_a,
        ))

        for i in np.nonzero(vmask)[0]:
            violations.append(SmoothnessViolation(
                joint_index=j, joint_name=name,
                time_s=float(vel_t[i] - start_s), kind="velocity",
                value=float(vel[i, j]), threshold=float(vel_thr[j]),
            ))
        for i in np.nonzero(amask)[0]:
            violations.append(SmoothnessViolation(
                joint_index=j, joint_name=name,
                time_s=float(accel_t[i] - start_s), kind="acceleration",
                value=float(accel[i, j]), threshold=float(accel_thr[j]),
            ))

    violations.sort(key=lambda v: v.time_s)
    truncated = len(violations) > config.max_events
    if truncated:
        violations = violations[: config.max_events]

    return SmoothnessResult(
        n_samples=n,
        vel_threshold=config.vel_threshold,
        accel_threshold=config.accel_threshold,
        per_joint=per_joint,
        violations=violations,
        n_violations_total=n_total,
        violations_truncated=truncated,
        passed=(n_total == 0),
    )
