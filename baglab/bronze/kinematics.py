"""Shared kinematics helpers for bronze checks.

Derivatives of high-rate joint positions are dominated by sensor/quantization
noise when taken sample-to-sample (the 1/dt and 1/dt^2 factors explode at ~1 kHz
/lowstate rates, and bag *record* timestamps add sub-ms jitter on top). We
instead use central differences over a fixed *time window*, which low-passes
that noise while still surfacing genuine velocity/acceleration spikes.
"""
from __future__ import annotations

import numpy as np


def median_dt(timestamps_ns) -> float:
    """Median positive sampling interval (seconds). Falls back to 500 Hz."""
    t = np.asarray(timestamps_ns, dtype=np.float64) / 1e9
    iv = np.diff(t)
    pos = iv[iv > 0]
    return float(np.median(pos)) if pos.size else 0.002


def central_derivatives(timestamps_ns, q, window_s: float):
    """Central 1st/2nd derivatives of q over a ~window_s time window.

    Returns (t_s, vel, accel) evaluated at the interior sample times t[W:-W],
    where W = round(window_s / median_dt). q may be (N,) or (N, J); vel/accel
    keep the trailing shape. Uses actual elapsed time across the window (jitter
    averages out over 2W samples) for the divisor.
    """
    t = np.asarray(timestamps_ns, dtype=np.float64) / 1e9
    q = np.asarray(q, dtype=np.float64)
    n = q.shape[0]
    dt = median_dt(timestamps_ns)
    w = max(1, int(round(window_s / dt)))

    if n <= 2 * w:
        empty = np.empty((0,) + q.shape[1:], dtype=np.float64)
        return t[:0], empty, empty

    q_lo, q_mid, q_hi = q[:-2 * w], q[w:-w], q[2 * w:]
    t_lo, t_hi = t[:-2 * w], t[2 * w:]
    # Half-window elapsed time (~w*dt), floored so jitter can't shrink it to ~0.
    half = np.maximum((t_hi - t_lo) / 2.0, 0.5 * w * dt)

    if q.ndim == 2:
        half = half[:, None]
    vel = (q_hi - q_lo) / (2.0 * half)
    accel = (q_hi - 2.0 * q_mid + q_lo) / (half ** 2)
    return t[w:-w], vel, accel
