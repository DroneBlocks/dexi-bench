"""AprilTag benchmark runner.

Benchmarks AprilTag detection throughput. Important: the package that
actually runs detection is upstream `apriltag_ros`, launched on DEXI via
`dexi_bringup`. `dexi_apriltag` is a *consumer* of /apriltag_detections
that produces PX4 visual odometry, and its detection callback is
throttled to 10 Hz (src/apriltag_odometry.cpp:119). The benchmark
subscribes to /apriltag_detections directly to measure the detector's
real throughput, bypassing dexi_apriltag's throttle.

Run with ROS2 sourced:
    source /opt/ros/jazzy/setup.bash
    source ~/dexi_ws/install/setup.bash
    bench-apriltag \\
      --bag ~/bags/bench_apriltag_sparse_v1 \\
      --target-cmd "ros2 launch dexi_bringup apriltag.launch.py" \\
      --duration 60

If you already have apriltag_ros running (e.g. via a full dexi_bringup),
pass --no-launch-target to skip launching and only run the subscriber.
"""

from __future__ import annotations

import argparse
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from ..monitors import make_monitor
from ..platform import detect
from ..schema.result import (
    BenchResult,
    Metrics,
    PlatformInfo,
    TargetInfo,
    WorkloadInfo,
    git_sha,
    write_result,
)

DEFAULT_TOPIC = "/apriltag_detections"
DEFAULT_TARGET_CMD = "ros2 launch dexi_bringup apriltag.launch.py"


class _DetectionListener:
    """Times inter-message arrivals on the AprilTag detections topic."""

    def __init__(self, topic: str) -> None:
        import rclpy
        from rclpy.node import Node

        try:
            from apriltag_msgs.msg import AprilTagDetectionArray
        except ImportError as e:
            raise RuntimeError(
                "apriltag_msgs not on PYTHONPATH — install ros-<distro>-apriltag-msgs "
                "and source your ROS/workspace setup"
            ) from e

        self._rclpy = rclpy
        rclpy.init()
        self._node: Node = rclpy.create_node("dexi_bench_apriltag_listener")
        self._arrivals: List[float] = []
        self._detections_total = 0
        self._empty_msgs = 0
        self._lock = threading.Lock()
        self._measuring = False

        def cb(msg) -> None:
            now = time.monotonic()
            with self._lock:
                if self._measuring:
                    self._arrivals.append(now)
                    n = len(getattr(msg, "detections", []) or [])
                    self._detections_total += n
                    if n == 0:
                        self._empty_msgs += 1

        self._node.create_subscription(AprilTagDetectionArray, topic, cb, 10)
        self._spin_stop = False
        self._spin_thread = threading.Thread(target=self._spin, daemon=True)
        self._spin_thread.start()

    def _spin(self) -> None:
        while not self._spin_stop and self._rclpy.ok():
            try:
                self._rclpy.spin_once(self._node, timeout_sec=0.1)
            except Exception:
                break

    def start_measuring(self) -> None:
        with self._lock:
            self._arrivals.clear()
            self._detections_total = 0
            self._empty_msgs = 0
            self._measuring = True

    def stop_measuring(self) -> None:
        with self._lock:
            self._measuring = False

    def summary(self) -> dict:
        with self._lock:
            arrivals = list(self._arrivals)
            det_total = self._detections_total
            empty = self._empty_msgs
        if len(arrivals) < 2:
            return {
                "fps_avg": None,
                "inference_ms_p50": None,
                "inference_ms_p95": None,
                "detections_total": det_total,
                "frames": len(arrivals),
                "empty_msgs": empty,
            }
        intervals_ms = sorted(
            (arrivals[i] - arrivals[i - 1]) * 1000.0 for i in range(1, len(arrivals))
        )
        n = len(intervals_ms)
        p50 = intervals_ms[n // 2]
        p95 = intervals_ms[min(n - 1, int(n * 0.95))]
        total_s = arrivals[-1] - arrivals[0]
        fps = (len(arrivals) - 1) / total_s if total_s > 0 else None
        return {
            "fps_avg": fps,
            "inference_ms_p50": p50,
            "inference_ms_p95": p95,
            "detections_total": det_total,
            "frames": len(arrivals),
            "empty_msgs": empty,
        }

    def shutdown(self) -> None:
        self._spin_stop = True
        try:
            self._node.destroy_node()
        except Exception:
            pass
        try:
            self._rclpy.shutdown()
        except Exception:
            pass


def _launch_target(cmd: str) -> subprocess.Popen:
    parts = shlex.split(cmd)
    print(f"[bench-apriltag] launching target: {cmd}", flush=True)
    return subprocess.Popen(
        parts, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )


def _play_bag(bag: Path, rate: float, loop: bool) -> subprocess.Popen:
    cmd = ["ros2", "bag", "play", str(bag), "--rate", str(rate)]
    if loop:
        cmd.append("--loop")
    print(f"[bench-apriltag] playing bag: {' '.join(cmd)}", flush=True)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _stop_process(proc: subprocess.Popen, grace_s: float = 5.0) -> str:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            out, _ = proc.communicate(timeout=grace_s)
            return out or ""
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                out, _ = proc.communicate(timeout=grace_s)
                return out or ""
            except subprocess.TimeoutExpired:
                proc.kill()
                return ""
    out, _ = proc.communicate()
    return out or ""


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="bench-apriltag",
        description="Benchmark AprilTag detection throughput (apriltag_ros upstream)",
    )
    p.add_argument("--bag", type=Path, required=True, help="Path to rosbag2 directory")
    p.add_argument("--bag-version", default="v1")
    p.add_argument("--workload", default="bench_apriltag_sparse")
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--warmup", type=float, default=5.0)
    p.add_argument("--bag-rate", type=float, default=1.0)
    p.add_argument("--bag-loop", action="store_true", help="Loop bag during measurement")
    p.add_argument("--topic", default=DEFAULT_TOPIC)
    p.add_argument(
        "--target-cmd",
        default=DEFAULT_TARGET_CMD,
        help="Shell command to launch the apriltag detector (default: dexi_bringup apriltag launch)",
    )
    p.add_argument(
        "--no-launch-target",
        action="store_true",
        help="Skip launching the target; assume apriltag_ros is already running",
    )
    p.add_argument("--node-startup-s", type=float, default=5.0)
    p.add_argument("--results-dir", type=Path, default=Path("results"))
    p.add_argument("--version", default="0.0.1", help="apriltag target package version")
    p.add_argument(
        "--package",
        default="apriltag_ros",
        help="Target package label for the result file (default: apriltag_ros, since "
        "detection is done by upstream apriltag_ros, not dexi_apriltag)",
    )
    p.add_argument("--notes", default="")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    if not args.bag.exists():
        print(f"[bench-apriltag] bag not found: {args.bag}", file=sys.stderr)
        return 2

    platform = detect()
    print(f"[bench-apriltag] platform: {platform}", flush=True)

    target_proc: Optional[subprocess.Popen] = None
    if not args.no_launch_target:
        target_proc = _launch_target(args.target_cmd)

    listener: Optional[_DetectionListener] = None
    bag_proc: Optional[subprocess.Popen] = None
    monitor = make_monitor(interval_s=1.0)
    target_stdout = ""
    mon_summary = None
    topic_summary: dict = {}

    try:
        if target_proc is not None:
            print(f"[bench-apriltag] waiting {args.node_startup_s}s for target startup...", flush=True)
            time.sleep(args.node_startup_s)
            if target_proc.poll() is not None:
                out, _ = target_proc.communicate()
                print(f"[bench-apriltag] target exited during startup:\n{out}", file=sys.stderr)
                return 3

        listener = _DetectionListener(args.topic)
        bag_proc = _play_bag(args.bag, args.bag_rate, args.bag_loop)
        monitor.start()

        print(f"[bench-apriltag] warmup {args.warmup}s", flush=True)
        time.sleep(args.warmup)

        listener.start_measuring()
        print(f"[bench-apriltag] measuring {args.duration}s", flush=True)
        time.sleep(args.duration)
        listener.stop_measuring()

        mon_summary = monitor.stop()
        topic_summary = listener.summary()

    finally:
        if bag_proc is not None:
            _stop_process(bag_proc, grace_s=3.0)
        if target_proc is not None:
            target_stdout = _stop_process(target_proc, grace_s=8.0)
        if listener is not None:
            listener.shutdown()

    print(f"[bench-apriltag] topic: {topic_summary}", flush=True)
    print(f"[bench-apriltag] monitor: {mon_summary}", flush=True)

    if target_stdout:
        tail = "\n".join(target_stdout.splitlines()[-30:])
        print(f"[bench-apriltag] target stdout tail:\n{tail}", flush=True)

    notes = args.notes
    if topic_summary.get("empty_msgs"):
        notes = (notes + " " if notes else "") + f"empty_msgs={topic_summary['empty_msgs']}"

    result = BenchResult(
        platform=PlatformInfo(
            kind=platform.kind,
            arch=platform.arch,
            os_release=platform.os_release,
            accelerator=None,
        ),
        target=TargetInfo(
            package=args.package,
            engine="cpu",
            version=args.version,
            git_sha=git_sha(),
        ),
        workload=WorkloadInfo(
            name=args.workload,
            bag_version=args.bag_version,
            duration_s=args.duration,
            warmup_s=args.warmup,
        ),
        metrics=Metrics(
            fps_avg=topic_summary.get("fps_avg"),
            inference_ms_p50=topic_summary.get("inference_ms_p50"),
            inference_ms_p95=topic_summary.get("inference_ms_p95"),
            detections_total=topic_summary.get("detections_total"),
            cpu_pct_avg=mon_summary.cpu_pct_avg if mon_summary else None,
            cpu_pct_max=mon_summary.cpu_pct_max if mon_summary else None,
            mem_mb_max=mon_summary.mem_mb_max if mon_summary else None,
            temp_c_max=mon_summary.temp_c_max if mon_summary else None,
            power_w_avg=mon_summary.power_w_avg if mon_summary else None,
        ),
        notes=notes,
    )
    out = write_result(result, args.results_dir)
    print(f"[bench-apriltag] wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
