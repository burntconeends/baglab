"""Visualize a converted G1 .mcap bag in Rerun.

Handles current bag contents plus PC-internal topics (/G1Env/env_state_act,
/ControlPolicy/*) that will appear once the laptop<->Orin DDS bridge is fixed.
Topics with zero messages are silently skipped so the same script works pre-
and post-bridge.

The PC-internal topics are published as std_msgs/String containing serialized
Python dicts. We try msgpack first, then JSON, then leave the string alone
if neither parses.

Usage:
    python3 bag_to_rerun.py <bag.mcap>
    python3 bag_to_rerun.py <bag.mcap> --subsample 20
    python3 bag_to_rerun.py <bag.mcap> --no-images
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from datetime import datetime
import rerun as rr
from rosbags.highlevel import AnyReader

try:
    import msgpack
    HAS_MSGPACK = True
except ImportError:
    HAS_MSGPACK = False


# G1 body joint layout (29 joints, /lowstate order)
G1_JOINT_NAMES = [
    "left_hip_pitch", "left_hip_roll", "left_hip_yaw",
    "left_knee", "left_ankle_pitch", "left_ankle_roll",
    "right_hip_pitch", "right_hip_roll", "right_hip_yaw",
    "right_knee", "right_ankle_pitch", "right_ankle_roll",
    "waist_yaw", "waist_roll", "waist_pitch",
    "left_shoulder_pitch", "left_shoulder_roll", "left_shoulder_yaw",
    "left_elbow", "left_wrist_roll", "left_wrist_pitch", "left_wrist_yaw",
    "right_shoulder_pitch", "right_shoulder_roll", "right_shoulder_yaw",
    "right_elbow", "right_wrist_roll", "right_wrist_pitch", "right_wrist_yaw",
]


def try_decode_dict(raw_bytes):
    """Decode a std_msgs/String payload as msgpack or JSON. Returns dict or None."""
    if HAS_MSGPACK:
        try:
            return msgpack.unpackb(raw_bytes, raw=False)
        except Exception:
            pass
    try:
        return json.loads(raw_bytes)
    except Exception:
        pass
    try:
        return json.loads(raw_bytes.decode('utf-8'))
    except Exception:
        return None


def log_vec(prefix, vec, names):
    """Log a numeric vector as N scalar timeseries under prefix/name."""
    for i, name in enumerate(names):
        if i >= len(vec):
            break
        rr.log(f"{prefix}/{name}", rr.Scalars(float(vec[i])))


def log_g1env_dict(data: dict):
    """Log fields from a /G1Env/env_state_act dict payload."""
    # observation.state — joint positions and velocities (43-vec total, body+hands)
    # Field names confirmed via run_g1_control_loop.py: env.observe() returns obs with these.
    if "q" in data and hasattr(data["q"], "__len__"):
        q = data["q"]
        for i, name in enumerate(G1_JOINT_NAMES):
            if i >= len(q): break
            rr.log(f"observation/state/q/{name}", rr.Scalars(float(q[i])))
        # Hand joints follow body in the 43-vec (indices 29..35 left, 36..42 right)
        for i in range(29, min(36, len(q))):
            rr.log(f"observation/state/hand_left/q/{i-29}", rr.Scalars(float(q[i])))
        for i in range(36, min(43, len(q))):
            rr.log(f"observation/state/hand_right/q/{i-36}", rr.Scalars(float(q[i])))

    if "dq" in data and hasattr(data["dq"], "__len__"):
        dq = data["dq"]
        for i, name in enumerate(G1_JOINT_NAMES):
            if i >= len(dq): break
            rr.log(f"observation/state/dq/{name}", rr.Scalars(float(dq[i])))

    # observation.eef_state — FK-computed wrist poses
    # ASSUMPTION: env.observe() includes "wrist_pose" (14-vec) — verify from G1Env.observe()
    if "wrist_pose" in data:
        wp = data["wrist_pose"]
        if hasattr(wp, "__len__") and len(wp) >= 14:
            log_vec("observation/eef_state/left/pos", wp[0:3], ["x", "y", "z"])
            log_vec("observation/eef_state/left/quat", wp[3:7], ["qw", "qx", "qy", "qz"])
            log_vec("observation/eef_state/right/pos", wp[7:10], ["x", "y", "z"])
            log_vec("observation/eef_state/right/quat", wp[10:14], ["qw", "qx", "qy", "qz"])

    # action — WBC joint commands (43-vec)
    # Confirmed: msg["action"] = wbc_action["q"]
    if "action" in data and hasattr(data["action"], "__len__"):
        a = data["action"]
        for i, name in enumerate(G1_JOINT_NAMES):
            if i >= len(a): break
            rr.log(f"action/q_cmd/{name}", rr.Scalars(float(a[i])))

    # action.eef — teleop wrist target (14-vec)
    # Confirmed: msg["action.eef"] = last_teleop_cmd.get("wrist_pose")
    if "action.eef" in data:
        ae = data["action.eef"]
        if hasattr(ae, "__len__") and len(ae) >= 14:
            log_vec("action/eef/left/pos", ae[0:3], ["x", "y", "z"])
            log_vec("action/eef/left/quat", ae[3:7], ["qw", "qx", "qy", "qz"])
            log_vec("action/eef/right/pos", ae[7:10], ["x", "y", "z"])
            log_vec("action/eef/right/quat", ae[10:14], ["qw", "qx", "qy", "qz"])

    # teleop.navigate_command (3-vec)
    # Confirmed: msg["navigate_command"] = last_teleop_cmd.get("navigate_cmd")
    if "navigate_command" in data:
        nc = data["navigate_command"]
        if hasattr(nc, "__len__") and len(nc) >= 3:
            log_vec("teleop/navigate", nc, ["vx", "vy", "wz"])

    # teleop.base_height_command (scalar)
    # Confirmed: msg["base_height_command"]
    if "base_height_command" in data:
        bh = data["base_height_command"]
        if hasattr(bh, "__len__"):
            bh = bh[0]
        rr.log("teleop/base_height", rr.Scalars(float(bh)))

    # Timestamps for sync-quality diagnostics
    # Confirmed: msg["timestamps"] = {"main_loop": ..., "proprio": ...}
    if "timestamps" in data and isinstance(data["timestamps"], dict):
        ts = data["timestamps"]
        if "main_loop" in ts:
            rr.log("diagnostics/timestamps/main_loop", rr.Scalars(float(ts["main_loop"])))
        if "proprio" in ts:
            rr.log("diagnostics/timestamps/proprio", rr.Scalars(float(ts["proprio"])))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bag", type=Path, help="Path to .mcap file or bag directory")
    parser.add_argument("--subsample", type=int, default=20,
                        help="Log every Nth scalar message (default 20: 1 kHz -> 50 Hz)")
    parser.add_argument("--no-images", action="store_true", help="Skip image decoding")
    args = parser.parse_args()

    if not args.bag.exists():
        print(f"ERROR: {args.bag} not found", file=sys.stderr)
        sys.exit(1)

    if not HAS_MSGPACK:
        print("Note: msgpack not installed. Will try JSON-only for PC-internal topics.")
        print("      Install with: pip install msgpack")

    rr.init("g1_bag")
    rr.save("/tmp/g1_bag.rrd")

    counters, logged = {}, {}
    def inc(t, topic):
        t[topic] = t.get(topic, 0) + 1

    with AnyReader([args.bag]) as reader:
        all_topics = {conn.topic: conn.msgtype for conn in reader.connections}
        print(f"Bag: {args.bag.name}")
        print(f"Duration: {reader.duration / 1e9:.2f}s, Messages: {reader.message_count}")
        print(f"Streaming to Rerun viewer...")
        print()

        for conn, timestamp, rawdata in reader.messages():
            topic, msg_type = conn.topic, conn.msgtype
            inc(counters, topic)
            rr.set_time("ros_time", timestamp=np.datetime64(timestamp, "ns"))

            # === ZED stereo camera (compressed JPEG, full rate ~23 FPS) ===
            if topic == "/zed/image_raw/compressed" and not args.no_images:
                msg = reader.deserialize(rawdata, msg_type)
                rr.log("camera/zed/stereo",
                       rr.EncodedImage(contents=bytes(msg.data), media_type="image/jpeg"))
                inc(logged, topic)

            # === Body proprioception (subsampled) ===
            elif topic == "/lowstate":
                if counters[topic] % args.subsample != 0: continue
                msg = reader.deserialize(rawdata, msg_type)
                for i, name in enumerate(G1_JOINT_NAMES):
                    if i >= len(msg.motor_state): break
                    rr.log(f"observation/state/q/{name}", rr.Scalars(msg.motor_state[i].q))
                    rr.log(f"observation/state/dq/{name}", rr.Scalars(msg.motor_state[i].dq))
                rr.log("imu/body/accel/x", rr.Scalars(msg.imu_state.accelerometer[0]))
                rr.log("imu/body/accel/y", rr.Scalars(msg.imu_state.accelerometer[1]))
                rr.log("imu/body/accel/z", rr.Scalars(msg.imu_state.accelerometer[2]))
                rr.log("imu/body/gyro/x", rr.Scalars(msg.imu_state.gyroscope[0]))
                rr.log("imu/body/gyro/y", rr.Scalars(msg.imu_state.gyroscope[1]))
                rr.log("imu/body/gyro/z", rr.Scalars(msg.imu_state.gyroscope[2]))
                inc(logged, topic)

            # === Body commands (subsampled) ===
            elif topic == "/lowcmd":
                if counters[topic] % args.subsample != 0: continue
                msg = reader.deserialize(rawdata, msg_type)
                for i, name in enumerate(G1_JOINT_NAMES):
                    if i >= len(msg.motor_cmd): break
                    rr.log(f"action/q_cmd/{name}", rr.Scalars(msg.motor_cmd[i].q))
                inc(logged, topic)

            # === Hand state (subsampled) ===
            elif topic in ("/dex3/left/state", "/dex3/right/state"):
                if counters[topic] % args.subsample != 0: continue
                side = "left" if "left" in topic else "right"
                msg = reader.deserialize(rawdata, msg_type)
                for i, motor in enumerate(msg.motor_state):
                    rr.log(f"observation/state/hand_{side}/q/{i}", rr.Scalars(motor.q))
                    rr.log(f"observation/state/hand_{side}/dq/{i}", rr.Scalars(motor.dq))
                inc(logged, topic)

            # === Hand commands (populated during teleop) ===
            elif topic in ("/dex3/left/cmd", "/dex3/right/cmd"):
                if counters[topic] % args.subsample != 0: continue
                side = "left" if "left" in topic else "right"
                msg = reader.deserialize(rawdata, msg_type)
                for i, motor in enumerate(msg.motor_cmd):
                    rr.log(f"action/hand_{side}/q_cmd/{i}", rr.Scalars(motor.q))
                inc(logged, topic)

            # === Livox IMU (full rate, 200 Hz) ===
            elif topic == "/utlidar/imu_livox_mid360":
                msg = reader.deserialize(rawdata, msg_type)
                rr.log("imu/livox/accel/x", rr.Scalars(msg.linear_acceleration.x))
                rr.log("imu/livox/accel/y", rr.Scalars(msg.linear_acceleration.y))
                rr.log("imu/livox/accel/z", rr.Scalars(msg.linear_acceleration.z))
                rr.log("imu/livox/gyro/x", rr.Scalars(msg.angular_velocity.x))
                rr.log("imu/livox/gyro/y", rr.Scalars(msg.angular_velocity.y))
                rr.log("imu/livox/gyro/z", rr.Scalars(msg.angular_velocity.z))
                inc(logged, topic)

            # === PICO controller (populated during teleop) ===
            elif topic == "/wirelesscontroller":
                msg = reader.deserialize(rawdata, msg_type)
                rr.log("teleop/controller/lx", rr.Scalars(msg.lx))
                rr.log("teleop/controller/ly", rr.Scalars(msg.ly))
                rr.log("teleop/controller/rx", rr.Scalars(msg.rx))
                rr.log("teleop/controller/ry", rr.Scalars(msg.ry))
                rr.log("teleop/controller/keys", rr.Scalars(float(msg.keys)))
                inc(logged, topic)

            # === PC-internal topics (require DDS bridge fix to appear in bag) ===
            elif topic == "/G1Env/env_state_act":
                # Published by run_g1_control_loop.py as a serialized dict over std_msgs/String.
                msg = reader.deserialize(rawdata, msg_type)
                payload = getattr(msg, "data", None)
                if payload is None: continue
                if isinstance(payload, str): payload = payload.encode("utf-8")
                data = try_decode_dict(payload)
                if data is None: continue
                log_g1env_dict(data)
                inc(logged, topic)

            elif topic == "/ControlPolicy/lower_body_policy_status":
                # Published as: {"use_policy_action": bool, "timestamp": float}
                msg = reader.deserialize(rawdata, msg_type)
                payload = getattr(msg, "data", None)
                if payload is None: continue
                if isinstance(payload, str): payload = payload.encode("utf-8")
                data = try_decode_dict(payload)
                if data is None: continue
                if "use_policy_action" in data:
                    rr.log("policy/use_policy_action", rr.Scalars(float(bool(data["use_policy_action"]))))
                inc(logged, topic)

            elif topic == "/ControlPolicy/joint_safety_status":
                # Published as: {"joint_safety_ok": bool, "timestamp": float}
                msg = reader.deserialize(rawdata, msg_type)
                payload = getattr(msg, "data", None)
                if payload is None: continue
                if isinstance(payload, str): payload = payload.encode("utf-8")
                data = try_decode_dict(payload)
                if data is None: continue
                if "joint_safety_ok" in data:
                    rr.log("policy/joint_safety_ok", rr.Scalars(float(bool(data["joint_safety_ok"]))))
                inc(logged, topic)

    # Summary
    print()
    print("=" * 80)
    print(f"{'Topic':<42} {'Type':<28} {'Read':>8} {'Logged':>8}")
    print("=" * 80)
    for topic in sorted(all_topics):
        msgtype = all_topics[topic].replace("/msg/", "/")
        read_n = counters.get(topic, 0)
        logged_n = logged.get(topic, 0)
        flag = "" if read_n > 0 else "  [empty]"
        print(f"  {topic:<40} {msgtype[:28]:<28} {read_n:>8} {logged_n:>8}{flag}")
    print()
    print("Rerun viewer should be open. Close it to exit.")


if __name__ == "__main__":
    main()
