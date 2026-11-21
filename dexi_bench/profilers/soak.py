"""Live-stack soak profiler.

Measures a running DEXI stack as it is (no rosbag): system load and thermals,
per-process CPU, and how often each perception pipeline publishes and finds
its target. Built for the bench soak where a drone sits in front of fixed
targets (AprilTag, YOLO art, bar disc) and one setting changes per run.

Run it ON the device, with ROS sourced and the stack's ROS_DOMAIN_ID:

    sudo bash -c 'source .../setup.bash && export ROS_DOMAIN_ID=1 && \
        python3 -m dexi_bench.profilers.soak --label baseline --secs 1800'

Per second it records total CPU, SoC temp, ARM clock, live-throttle flag,
MemAvailable, swap, and message counts per pipeline. Per process it records
CPU % (100 = one core), keyed by ROS node name when the process has one.

Output (in --out, default results/<platform>/soak/<label>/):

    system.csv      1 Hz system + pipeline counts
    procs.csv       per-process CPU, long format (elapsed_s, proc, cpu_pct, rss_mb)
    detections.csv  every detection message (elapsed_s, topic, label, conf)
    summary.json    means / p95 / rates / hit rates

Hit rate = share of a pipeline's messages that contain its target. Targets are
command-line options; YOLO classes not listed in --yolo-classes are ignored.

To avoid adding load it never subscribes to an image topic: camera rate is read
from /cam0/camera_info, which the camera publishes once per frame.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import psutil

from ..monitors.samplers import vcgencmd_clock_mhz, vcgencmd_temp
from ..platform import detect

_NODE_RE = re.compile(r"__node:=(\S+)")


def _throttled_raw() -> Optional[int]:
    import subprocess
    try:
        out = subprocess.check_output(["vcgencmd", "get_throttled"], timeout=1).decode()
        return int(out.strip().split("=0x")[1], 16)
    except Exception:
        return None


def _meminfo() -> Dict[str, int]:
    out = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            out[k] = int(v.split()[0])
    return out


def _proc_name(p: psutil.Process) -> str:
    try:
        cmd = " ".join(p.cmdline())
    except (psutil.Error, OSError):
        cmd = ""
    m = _NODE_RE.search(cmd)
    if m:
        return m.group(1)
    try:
        name = p.name()
    except psutil.Error:
        return "?"
    if name.startswith("kworker"):
        return "kworker"
    if name.startswith("python") and cmd:
        parts = [os.path.basename(a) for a in cmd.split() if a.endswith(".py") or "/" in a]
        if len(parts) > 1:
            return parts[1]
    return name


class _ProcSampler:
    def __init__(self) -> None:
        self._procs: Dict[int, psutil.Process] = {}

    def sample(self) -> Dict[str, List[float]]:
        """name -> [cpu_pct, rss_mb] summed over processes with that name."""
        seen = set()
        agg: Dict[str, List[float]] = defaultdict(lambda: [0.0, 0.0])
        for p in psutil.process_iter():
            seen.add(p.pid)
            if p.pid not in self._procs:
                self._procs[p.pid] = p
                try:
                    p.cpu_percent(None)  # prime
                except psutil.Error:
                    pass
                continue
            p = self._procs[p.pid]
            try:
                cpu = p.cpu_percent(None)
                rss = p.memory_info().rss / 1048576
            except psutil.Error:
                continue
            a = agg[_proc_name(p)]
            a[0] += cpu
            a[1] += rss
        for pid in list(self._procs):
            if pid not in seen:
                del self._procs[pid]
        return agg


class _Pipelines:
    """ROS subscriptions that count messages and target hits."""

    def __init__(self, tag_id: int, yolo_classes: List[str], bar_label: str,
                 yolo_ignore: List[str]) -> None:
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import qos_profile_sensor_data
        from apriltag_msgs.msg import AprilTagDetectionArray
        from dexi_interfaces.msg import YoloDetectionArray
        from sensor_msgs.msg import CameraInfo

        self.tag_id = tag_id
        self.yolo_classes = yolo_classes
        self.yolo_ignore = set(yolo_ignore)
        self.bar_label = bar_label
        self.lock = threading.Lock()
        self.t0 = time.monotonic()
        self.counts: Counter = Counter()      # per-interval, reset by take()
        self.totals: Counter = Counter()      # whole run
        self.events: List[tuple] = []

        rclpy.init()
        self.node = Node("dexi_bench_soak")
        n = self.node
        n.create_subscription(AprilTagDetectionArray, "/apriltag_detections", self._tag, 10)
        n.create_subscription(YoloDetectionArray, "/yolo_detections", self._yolo, 10)
        n.create_subscription(YoloDetectionArray, "/bar_detections", self._bar, 10)
        n.create_subscription(CameraInfo, "/cam0/camera_info", self._cam, qos_profile_sensor_data)
        self._exec_thread = threading.Thread(target=rclpy.spin, args=(n,), daemon=True)
        self._exec_thread.start()

    def _bump(self, *keys: str) -> None:
        for k in keys:
            self.counts[k] += 1
            self.totals[k] += 1

    def _ev(self, topic: str, label: str, conf: float) -> None:
        self.events.append((round(time.monotonic() - self.t0, 2), topic, label, round(conf, 3)))

    def _cam(self, _msg) -> None:
        with self.lock:
            self._bump("cam")

    def _tag(self, msg) -> None:
        with self.lock:
            self._bump("tag")
            ids = [d.id for d in msg.detections]
            if self.tag_id in ids:
                self._bump("tag_hit")
            for d in msg.detections:
                self._ev("tag", str(d.id), float(d.decision_margin))

    def _yolo(self, msg) -> None:
        with self.lock:
            self._bump("yolo")
            names = set()
            for d in msg.detections:
                if d.class_name in self.yolo_ignore:
                    continue
                names.add(d.class_name)
                self._ev("yolo", d.class_name, float(d.confidence))
            for c in self.yolo_classes:
                if c in names:
                    self._bump(f"yolo_hit_{c}")
            if any(n not in self.yolo_classes for n in names):
                self._bump("yolo_other")

    def _bar(self, msg) -> None:
        with self.lock:
            self._bump("bar")
            labels = [d.class_name for d in msg.detections]
            if not labels:
                self._bump("bar_abstain")
            elif self.bar_label in labels:
                self._bump("bar_hit")
            else:
                self._bump("bar_wrong")
            for d in msg.detections:
                self._ev("bar", d.class_name, float(d.confidence))

    def take(self) -> Counter:
        with self.lock:
            c = self.counts
            self.counts = Counter()
            return c

    def take_events(self) -> List[tuple]:
        with self.lock:
            e = self.events
            self.events = []
            return e

    def stop(self) -> None:
        import rclpy
        try:
            self.node.destroy_node()
            rclpy.shutdown()
        except Exception:
            pass


def _stats(xs: List[float]) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {}
    s = sorted(xs)
    return {
        "mean": round(statistics.fmean(s), 2),
        "p95": round(s[min(len(s) - 1, int(0.95 * len(s)))], 2),
        "max": round(s[-1], 2),
        "min": round(s[0], 2),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--label", required=True)
    ap.add_argument("--secs", type=float, default=900)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--notes", default="")
    ap.add_argument("--tag-id", type=int, default=0)
    ap.add_argument("--yolo-classes", default="water_barn,toxic_fluid")
    ap.add_argument("--yolo-ignore", default="bridge_1line,bridge_2line,bridge_3line,blackout",
                    help="YOLO classes left out of the analysis entirely")
    ap.add_argument("--bar-label", default="bars_2")
    ap.add_argument("--bar-hz", type=float, default=2.0,
                    help="bar_counter detection_frequency; it publishes nothing when it abstains, "
                         "so abstain rate = 1 - msgs / (bar_hz * duration)")
    ap.add_argument("--no-ros", action="store_true", help="system metrics only")
    args = ap.parse_args(argv)

    plat = detect()
    kind = getattr(plat, "kind", "unknown")
    out = args.out or Path("results") / kind / "soak" / args.label
    out.mkdir(parents=True, exist_ok=True)
    yolo_classes = [c for c in args.yolo_classes.split(",") if c]

    pipes = None if args.no_ros else _Pipelines(
        args.tag_id, yolo_classes, args.bar_label,
        [c for c in args.yolo_ignore.split(",") if c])
    procs = _ProcSampler()
    psutil.cpu_percent(None)
    procs.sample()

    count_keys = ["cam", "tag", "tag_hit", "yolo", "yolo_other", "bar", "bar_hit",
                  "bar_wrong", "bar_abstain"] + [f"yolo_hit_{c}" for c in yolo_classes]
    sys_fields = ["elapsed_s", "cpu_pct", "load1", "temp_c", "clock_mhz", "throttled_raw",
                  "mem_available_mb", "swap_used_mb"] + count_keys
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    rows: List[dict] = []
    proc_cpu: Dict[str, List[float]] = defaultdict(list)
    proc_rss: Dict[str, List[float]] = defaultdict(list)
    if pipes:
        pipes.take()
        pipes.take_events()

    fs = open(out / "system.csv", "w", newline="")
    fp = open(out / "procs.csv", "w", newline="")
    fd = open(out / "detections.csv", "w", newline="")
    ws = csv.DictWriter(fs, fieldnames=sys_fields)
    ws.writeheader()
    wp = csv.writer(fp)
    wp.writerow(["elapsed_s", "proc", "cpu_pct", "rss_mb"])
    wd = csv.writer(fd)
    wd.writerow(["elapsed_s", "topic", "label", "conf"])

    next_t = t0 + args.interval
    n = 0
    try:
        while time.monotonic() - t0 < args.secs:
            time.sleep(max(0.0, next_t - time.monotonic()))
            next_t += args.interval
            el = round(time.monotonic() - t0, 1)
            mi = _meminfo()
            row = {
                "elapsed_s": el,
                "cpu_pct": psutil.cpu_percent(None),
                "load1": round(os.getloadavg()[0], 2),
                "temp_c": vcgencmd_temp(),
                "clock_mhz": vcgencmd_clock_mhz(),
                "throttled_raw": _throttled_raw(),
                "mem_available_mb": round(mi["MemAvailable"] / 1024),
                "swap_used_mb": round((mi.get("SwapTotal", 0) - mi.get("SwapFree", 0)) / 1024),
            }
            c = pipes.take() if pipes else Counter()
            for k in count_keys:
                row[k] = c.get(k, 0)
            ws.writerow(row)
            rows.append(row)
            for name, (cpu, rss) in procs.sample().items():
                if cpu >= 0.05 or name in proc_cpu:
                    wp.writerow([el, name, round(cpu, 1), round(rss, 1)])
                proc_cpu[name].append(cpu)
                proc_rss[name].append(rss)
            if pipes:
                wd.writerows(pipes.take_events())
            n += 1
            if n % 30 == 0:
                fs.flush(); fp.flush(); fd.flush()
    except KeyboardInterrupt:
        pass
    finally:
        fs.close(); fp.close(); fd.close()
        if pipes:
            pipes.stop()

    dur = (rows[-1]["elapsed_s"] if rows else 0.0) or 1.0
    tot = Counter()
    for r in rows:
        for k in count_keys:
            tot[k] += r[k]

    def rate(k):
        return round(tot[k] / dur, 2)

    def share(k, base):
        return round(tot[k] / tot[base], 3) if tot[base] else None

    pipelines = {
        "camera": {"hz": rate("cam")},
        "apriltag": {"hz": rate("tag"), "hit_rate": share("tag_hit", "tag"),
                     "hit_hz": rate("tag_hit")},
        "yolo": {"hz": rate("yolo"),
                 "hit_rate": {c: share(f"yolo_hit_{c}", "yolo") for c in yolo_classes},
                 "other_class_rate": share("yolo_other", "yolo")},
        "bar_counter": {"hz": rate("bar"), "msgs": tot["bar"],
                        "expected_msgs": round(args.bar_hz * dur),
                        # shares of the expected publish slots (silence = abstain)
                        "read_rate": round(tot["bar_hit"] / max(1, args.bar_hz * dur), 3),
                        "wrong_rate": round(tot["bar_wrong"] / max(1, args.bar_hz * dur), 3),
                        "abstain_rate": round(max(0.0, 1 - (tot["bar_hit"] + tot["bar_wrong"])
                                                  / max(1, args.bar_hz * dur)), 3),
                        "wrong_labels": {}},
    }
    # wrong bar labels, from the detections file
    try:
        with open(out / "detections.csv") as f:
            wl = Counter(r["label"] for r in csv.DictReader(f)
                         if r["topic"] == "bar" and r["label"] != args.bar_label)
        pipelines["bar_counter"]["wrong_labels"] = dict(wl)
    except OSError:
        pass
    top = sorted(((statistics.fmean(v), k) for k, v in proc_cpu.items() if v), reverse=True)
    summary = {
        "label": args.label,
        "notes": args.notes,
        "platform_kind": kind,
        "started_utc": started.isoformat(timespec="seconds"),
        "duration_s": dur,
        "samples": len(rows),
        "cpu_pct": _stats([r["cpu_pct"] for r in rows]),
        "temp_c": _stats([r["temp_c"] for r in rows]),
        "clock_mhz": _stats([r["clock_mhz"] for r in rows]),
        "throttled_now_share": round(sum(1 for r in rows if (r["throttled_raw"] or 0) & 0x4)
                                     / max(1, len(rows)), 3),
        "soft_temp_limit_now_share": round(sum(1 for r in rows if (r["throttled_raw"] or 0) & 0x8)
                                           / max(1, len(rows)), 3),
        "throttled_raw_last": rows[-1]["throttled_raw"] if rows else None,
        "mem_available_mb": _stats([r["mem_available_mb"] for r in rows]),
        "swap_used_mb": _stats([r["swap_used_mb"] for r in rows]),
        "pipelines": pipelines,
        "procs_cpu_pct_mean": {k: round(v, 1) for v, k in top[:25]},
        "procs_rss_mb_mean": {k: round(statistics.fmean(proc_rss[k]), 1) for _, k in top[:25]},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: summary[k] for k in ("label", "cpu_pct", "temp_c", "pipelines")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
