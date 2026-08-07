#!/usr/bin/env python3
"""bag_to_video.py — turn a flight rosbag's camera stream into an mp4.

Reads /cam0/image_raw/compressed from a rosbag2 (mcap), decodes the JPEG frames,
and ffmpegs an mp4 at the bag's own frame rate — for visually inspecting AprilTag
/ camera vibration & jitter during flight. See docs/FLIGHT_BAG_AND_VIDEO.md.

Run on the drone with ROS2 sourced:
    python3 dexi_bench/bag_to_video.py [BAG_DIR] [OUT_MP4]
Defaults: newest ~/flight_bags/flight_* -> /tmp/flight_video.mp4
"""
import sys, os, glob, subprocess
import numpy as np
import cv2
from rosbag2_py import SequentialReader, StorageOptions, ConverterOptions
from rclpy.serialization import deserialize_message
from sensor_msgs.msg import CompressedImage

TOPIC = "/cam0/image_raw/compressed"
FRAME_DIR = "/tmp/vid_frames"


def main():
    bag = sys.argv[1] if len(sys.argv) > 1 else \
        sorted(glob.glob(os.path.expanduser("~/flight_bags/flight_*")))[-1]
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/flight_video.mp4"

    os.makedirs(FRAME_DIR, exist_ok=True)
    for f in glob.glob(f"{FRAME_DIR}/*.jpg"):
        os.remove(f)

    reader = SequentialReader()
    reader.open(StorageOptions(uri=bag, storage_id="mcap"), ConverterOptions("", ""))

    n = 0
    t0 = t_last = None
    while reader.has_next():
        topic, data, ts = reader.read_next()
        if topic != TOPIC:
            continue
        msg = deserialize_message(data, CompressedImage)
        img = cv2.imdecode(np.frombuffer(msg.data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        cv2.imwrite(f"{FRAME_DIR}/f{n:05d}.jpg", img)
        if t0 is None:
            t0 = ts
        t_last = ts
        n += 1

    if n == 0:
        sys.exit(f"No frames on {TOPIC} in {bag}")

    dur = (t_last - t0) / 1e9 if t0 is not None else 0.0
    fps = (n / dur) if dur > 0 else 30.0
    print(f"bag={bag}\nframes={n} duration={dur:.1f}s fps={fps:.2f}")

    subprocess.run([
        "ffmpeg", "-y", "-framerate", f"{fps:.2f}",
        "-i", f"{FRAME_DIR}/f%05d.jpg",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out,
    ], check=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
