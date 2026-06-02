"""Priority 5 — static-frame scrubbing.

Detects windows where the robot is effectively still: max |velocity| over the
configured joints (default: both arms) stays below `static_vel_threshold` for at
least `min_static_duration` seconds. Velocity comes from the same windowed
central differences as the smoothness check (see bronze/kinematics.py). The bag
is NOT modified — the time ranges to drop are reported so a later (silver) stage
can act on them.
"""
from __future__ import annotations

import numpy as np

from baglab.bronze.kinematics import central_derivatives
from baglab.config import CheckConfig
from baglab.reader import BagData
from baglab.report import StaticResult, StaticWindow


def check_static_frames(bag: BagData, config: CheckConfig) -> StaticResult:
    ls = bag.lowstate
    joints = list(config.static_joints)
    if ls is None or ls.timestamps_ns.size < 2:
        return StaticResult(
            static_vel_threshold=config.static_vel_threshold,
            min_static_duration=config.min_static_duration,
            static_joints=joints, windows=[], total_static_s=0.0,
            fraction_static=0.0, passed=True,
            note="insufficient /lowstate samples for static-frame analysis",
        )

    q = ls.q
    n_joints = q.shape[1]
    joints = [j for j in joints if 0 <= j < n_joints]
    start_s = bag.start_ns / 1e9

    vel_t, vel, _ = central_derivatives(
        ls.timestamps_ns, q[:, joints], config.deriv_window_s)
    if vel.shape[0] == 0:
        return StaticResult(
            static_vel_threshold=config.static_vel_threshold,
            min_static_duration=config.min_static_duration,
            static_joints=joints, windows=[], total_static_s=0.0,
            fraction_static=0.0, passed=True,
            note="insufficient /lowstate samples for the derivative window",
        )
    speed = np.max(np.abs(vel), axis=1)                # (M,)

    inactive = speed < config.static_vel_threshold

    windows: list[StaticWindow] = []
    total_static = 0.0
    i = 0
    m = inactive.size
    while i < m:
        if not inactive[i]:
            i += 1
            continue
        j = i
        while j + 1 < m and inactive[j + 1]:
            j += 1
        # Inactive run spans velocity-sample indices i..j, i.e. position
        # timestamps vel_t[i]..vel_t[j].
        win_start = float(vel_t[i] - start_s)
        win_end = float(vel_t[j] - start_s)
        duration = win_end - win_start
        if duration >= config.min_static_duration:
            windows.append(StaticWindow(
                start_s=win_start, end_s=win_end, duration_s=duration,
            ))
            total_static += duration
        i = j + 1

    bag_dur = bag.duration_s
    fraction = (total_static / bag_dur) if bag_dur > 0 else 0.0
    return StaticResult(
        static_vel_threshold=config.static_vel_threshold,
        min_static_duration=config.min_static_duration,
        static_joints=joints,
        windows=windows,
        total_static_s=total_static,
        fraction_static=fraction,
        passed=True,  # advisory — reports ranges to drop, never a hard failure
    )
