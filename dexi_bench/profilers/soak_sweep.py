"""One-change-at-a-time optimization sweep for a live DEXI stack.

Runs ON the device as root (it edits ~/.dexi-config.yaml and the installed
bringup launch file, and restarts dexi.service). For each experiment:

    restore pristine config -> apply one change -> restart dexi.service ->
    warm up -> soak profiler for N seconds -> restore pristine -> next

Resumable: an experiment whose run folder already has summary.json is skipped,
so after a crash or reboot just start it again. Pristine copies are taken once,
on the first start, into <runs>/_pristine/ and every experiment restores from
them, so a half-applied change never leaks into the next run.

    sudo python3 -m dexi_bench.profilers.soak_sweep --plan plan.txt --runs ~/soak/runs

The plan file is one experiment name per line (see EXPERIMENTS below); a line
"name secs" overrides the soak length. Lines starting with # are skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Tuple

HOME = Path("/home/dexi")
CFG = HOME / ".dexi-config.yaml"
LAUNCH = HOME / "dexi_ws/install/dexi_bringup/share/dexi_bringup/launch/dexi_bringup_cm5.launch.py"
DROPIN_DIR = Path("/etc/systemd/system/dexi.service.d")
DROPIN = DROPIN_DIR / "zz-soak.conf"
SOAKRUN = HOME / "soak/soakrun.sh"

LOG = None


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}Z {msg}"
    print(line, flush=True)
    if LOG:
        with open(LOG, "a") as f:
            f.write(line + "\n")


def sh(cmd: str, check: bool = True) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd}: {r.stderr.strip()}")
    return r.stdout


# ---- edit helpers -----------------------------------------------------------

def cfg_set(path: List[str], value) -> None:
    import yaml
    d = yaml.safe_load(CFG.read_text()) or {}
    cur = d
    for k in path[:-1]:
        cur = cur.setdefault(k, {})
    cur[path[-1]] = value
    CFG.write_text(yaml.safe_dump(d, sort_keys=False))
    sh(f"chown dexi:dexi {CFG}")


def launch_sub(old: str, new: str) -> None:
    s = LAUNCH.read_text()
    if old not in s:
        raise RuntimeError(f"launch edit: anchor not found: {old!r}")
    LAUNCH.write_text(s.replace(old, new, 1))


CAM_PARAMS_ANCHOR = "'camera_name': 'cam0'"
TAG_PARAMS_ANCHOR = "'family': '36h11',  # Standard AprilTag family"
BAR_NODE_ANCHOR = "executable='bar_counter_node.py',\n        name='bar_counter_node',"


def _shared_stream(hz: float) -> None:
    topic = f"/cam0/image_raw/compressed_{hz:g}hz"
    NL = chr(10)
    launch_sub("from launch_ros.actions import Node" + NL,
               "from launch_ros.actions import Node, SetRemap" + NL + "from launch.actions import GroupAction" + NL)
    launch_sub("    ld.add_action(image_throttle_apriltag_node)" + NL,
               "    ld.add_action(image_throttle_apriltag_node)" + NL
               + "    ld.add_action(Node(package='topic_tools', executable='throttle', name='image_throttle_shared_node'," + NL
               + f"        arguments=['messages', '/cam0/image_raw/compressed', '{hz:g}', '{topic}']," + NL
               + "        condition=IfCondition(camera)))" + NL)
    launch_sub("    ld.add_action(yolo_launch)" + NL,
               f"    ld.add_action(GroupAction([SetRemap('/cam0/image_raw/compressed', '{topic}'), yolo_launch]))" + NL)
    launch_sub(BAR_NODE_ANCHOR, BAR_NODE_ANCHOR + NL + f"        remappings=[('/cam0/image_raw/compressed', '{topic}')],")


def dropin(text: str) -> None:
    DROPIN_DIR.mkdir(parents=True, exist_ok=True)
    DROPIN.write_text("[Service]\n" + text + "\n")
    sh("systemctl daemon-reload")


# ---- experiments: name -> (description, apply fn, env for the profiler) -----

Exp = Tuple[str, Callable[[], None], Dict[str, str]]


def _nothing() -> None:
    pass


EXPERIMENTS: Dict[str, Exp] = {
    "baseline": ("Everything on, as configured for the soak", _nothing, {}),
    # camera
    "cam_yuyv": ("Camera format YUYV instead of XRGB8888",
                 lambda: cfg_set(["nodes", "camera", "format"], "YUYV"), {}),
    "cam_nv12": ("Camera format NV12 instead of XRGB8888",
                 lambda: cfg_set(["nodes", "camera", "format"], "NV12"), {}),
    "cam_jpeg40": ("JPEG quality 40 (from 60)",
                   lambda: cfg_set(["nodes", "camera", "jpeg_quality"], 40), {}),
    "cam_640x360": ("Camera 640x360 (from 768x432)",
                    lambda: (cfg_set(["nodes", "camera", "width"], 640),
                             cfg_set(["nodes", "camera", "height"], 360)), {}),
    "cam_15fps": ("Camera 15 fps (FrameDurationLimits 66.7 ms, from 30 fps)",
                  lambda: launch_sub(CAM_PARAMS_ANCHOR,
                                     CAM_PARAMS_ANCHOR + ",\n            'FrameDurationLimits': [66666, 66666]"),
                  {}),
    # throttles
    "no_throttle_2hz": ("Remove the compressed_2hz throttle node",
                        # the node already carries condition=IfCondition(camera); replace it, don't add a second
                        lambda: launch_sub("'/cam0/image_raw/compressed_2hz'],\n        condition=IfCondition(camera)",
                                           "'/cam0/image_raw/compressed_2hz'],\n        condition=IfCondition('false')"),
                        {}),
    "tag_5hz": ("AprilTag input throttle 5 Hz (from 10)",
                lambda: launch_sub("'/cam0/image_raw/compressed', '10.0', '/cam0/image_raw/compressed_apriltag'",
                                   "'/cam0/image_raw/compressed', '5.0', '/cam0/image_raw/compressed_apriltag'"),
                {}),
    # apriltag detector
    "tag_decimate3": ("AprilTag detector.decimate 3 (from 2)",
                      lambda: launch_sub(TAG_PARAMS_ANCHOR, TAG_PARAMS_ANCHOR + "\n            'detector.decimate': 3.0,"),
                      {}),
    "tag_threads2": ("AprilTag detector.threads 2 (from 1)",
                     lambda: launch_sub(TAG_PARAMS_ANCHOR, TAG_PARAMS_ANCHOR + "\n            'detector.threads': 2,"),
                     {}),
    # yolo
    "yolo_1hz": ("YOLO detection_frequency 1 Hz (from 2)",
                 lambda: cfg_set(["nodes", "yolo", "detection_frequency"], 1.0), {}),
    "yolo_threads1": ("YOLO onnxruntime num_threads 1 (from 2)",
                      lambda: cfg_set(["nodes", "yolo", "num_threads"], 1), {}),
    "yolo_threads4": ("YOLO onnxruntime num_threads 4 (from 2)",
                      lambda: cfg_set(["nodes", "yolo", "num_threads"], 4), {}),
    # shared lower-rate stream for the 2 Hz Python consumers (launch-only change)
    "shared_6hz": ("YOLO and bar counter read one shared 6 Hz throttled stream instead of the 30 Hz stream "
                   "(one extra C++ throttle; launch remaps only)", lambda: _shared_stream(6.0), {}),
    # bar counter
    "bar_1hz": ("Bar counter detection_frequency 1 Hz (from 2)",
                lambda: launch_sub(BAR_NODE_ANCHOR, BAR_NODE_ANCHOR + "\n        parameters=[{'detection_frequency': 1.0}],"),
                {"SOAK_BAR_HZ": "1.0"}),
    "bar_on_2hz_stream": ("Bar counter reads compressed_2hz instead of the 30 Hz stream (remap only)",
                          lambda: launch_sub(BAR_NODE_ANCHOR, BAR_NODE_ANCHOR +
                                             "\n        remappings=[('/cam0/image_raw/compressed', '/cam0/image_raw/compressed_2hz')],"),
                          {}),
    # services
    "no_rosbridge": ("rosbridge off (no web clients connected during the soak)",
                     lambda: cfg_set(["nodes", "rosbridge", "enabled"], False), {}),
    "no_telemetry": ("telemetry node off",
                     lambda: cfg_set(["nodes", "telemetry", "enabled"], False), {}),
    "no_containers": ("Docker containers stopped (dexi-node-red, dexi-droneblocks)",
                      lambda: sh("docker stop dexi-node-red dexi-droneblocks"), {}),
    "no_extras": ("code-server, lttng-sessiond, mavlink2rest stopped",
                  lambda: sh("systemctl stop code-server lttng-sessiond mavlink2rest"), {}),
    # system
    "gov_schedutil": ("CPU governor schedutil (from ondemand)",
                      lambda: sh("for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo schedutil > $g; done"), {}),
    "gov_performance": ("CPU governor performance (from ondemand)",
                        lambda: sh("for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance > $g; done"), {}),
    "ondemand_kernel_defaults": ("ondemand with kernel-default tunables (up_threshold 95, sampling_rate 10000, "
                                 "sampling_down_factor 1) instead of the Pi OS boot values (50 / 100000 / 50)",
                                 lambda: _ondemand_set({"up_threshold": "95", "sampling_rate": "10000",
                                                        "sampling_down_factor": "1"}), {}),
    "dds_cyclone": ("RMW Cyclone DDS instead of Fast DDS",
                    lambda: dropin("Environment=RMW_IMPLEMENTATION=rmw_cyclonedds_cpp"),
                    {"RMW_IMPLEMENTATION": "rmw_cyclonedds_cpp"}),
    "fastdds_shm_off": ("Fast DDS with shared-memory transport off (UDP only)",
                        lambda: dropin("Environment=FASTDDS_BUILTIN_TRANSPORTS=UDPv4"),
                        {"FASTDDS_BUILTIN_TRANSPORTS": "UDPv4"}),
    "journal_warning": ("journald MaxLevelStore=warning (from debug)",
                        lambda: (Path("/etc/systemd/journald.conf.d").mkdir(exist_ok=True),
                                 Path("/etc/systemd/journald.conf.d/zz-soak.conf").write_text(
                                     "[Journal]\nMaxLevelStore=warning\n"),
                                 sh("systemctl restart systemd-journald")), {}),
}


ONDEMAND = Path("/sys/devices/system/cpu/cpufreq/ondemand")
ONDEMAND_KEYS = ("up_threshold", "sampling_rate", "sampling_down_factor", "io_is_busy", "powersave_bias")


def _ondemand_set(vals: Dict[str, str]) -> None:
    for k, v in vals.items():
        (ONDEMAND / k).write_text(str(v))


# Code overlays: experiment name -> [(patched file under OVERLAY_DIR/<name>/, installed target)].
# The original target is backed up once into <runs>/_pristine/overlay/ and restore() always
# copies it back, so a code change never outlives its run.
OVERLAY_DIR = HOME / "soak/overlays"
INSTALL = HOME / "dexi_ws/install"
OVERLAYS: Dict[str, List[Tuple[str, Path]]] = {
    "code_raw_subs": [
        ("dexi_yolo_node_onnx.py", INSTALL / "dexi_yolo/lib/dexi_yolo/dexi_yolo_node_onnx.py"),
        ("bar_counter_node.py", INSTALL / "dexi_vision/lib/dexi_vision/bar_counter_node.py"),
    ],
    "code_gate": [
        ("dexi_yolo_node_onnx.py", INSTALL / "dexi_yolo/lib/dexi_yolo/dexi_yolo_node_onnx.py"),
        ("bar_counter_node.py", INSTALL / "dexi_vision/lib/dexi_vision/bar_counter_node.py"),
    ],
    "code_offboard_2threads": [
        ("px4_offboard_manager", INSTALL / "dexi_offboard/lib/dexi_offboard/px4_offboard_manager"),
    ],
}
PDIR: List[Path] = []


def _swap_in(src: Path, target: Path) -> None:
    """Copy src over target via a temp file and rename, so it works while target is a running
    binary (writing in place fails with ETXTBSY)."""
    tmp = target.with_name(target.name + ".soak-tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, target)


def _overlay(name: str) -> None:
    bdir = PDIR[0] / "overlay"
    bdir.mkdir(parents=True, exist_ok=True)
    for fname, target in OVERLAYS[name]:
        backup = bdir / (target.parent.parent.name + "__" + target.name)
        if not backup.exists():
            shutil.copy2(target, backup)
        _swap_in(OVERLAY_DIR / name / fname, target)
        sh(f"chown dexi:dexi {target}")


for _n in OVERLAYS:
    EXPERIMENTS[_n] = (f"Code overlay {_n}: " + ", ".join(f for f, _ in OVERLAYS[_n]),
                       (lambda n=_n: _overlay(n)), {})


def combo(*names: str) -> Exp:
    def apply() -> None:
        for n in names:
            EXPERIMENTS[n][1]()
    env: Dict[str, str] = {}
    for n in names:
        env.update(EXPERIMENTS[n][2])
    return ("Combined: " + " + ".join(names), apply, env)


# ---- restore ----------------------------------------------------------------

def take_pristine(pdir: Path) -> None:
    if (pdir / "done").exists():
        return
    pdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CFG, pdir / "dexi-config.yaml")
    shutil.copy2(LAUNCH, pdir / "launch.py")
    gov = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").read_text().strip()
    (pdir / "governor").write_text(gov)
    if ONDEMAND.exists():
        (pdir / "ondemand.json").write_text(json.dumps({k: (ONDEMAND / k).read_text().strip() for k in ONDEMAND_KEYS}))
    (pdir / "done").write_text(datetime.now(timezone.utc).isoformat())
    log(f"pristine copies taken in {pdir} (governor {gov})")


def restore(pdir: Path) -> None:
    bdir = pdir / "overlay"
    for files in OVERLAYS.values():
        for _, target in files:
            backup = bdir / (target.parent.parent.name + "__" + target.name)
            if backup.exists():
                _swap_in(backup, target)
                sh(f"chown dexi:dexi {target}")
    shutil.copy2(pdir / "dexi-config.yaml", CFG)
    sh(f"chown dexi:dexi {CFG}")
    shutil.copy2(pdir / "launch.py", LAUNCH)
    sh(f"chown dexi:dexi {LAUNCH}")
    gov = (pdir / "governor").read_text().strip()
    sh(f"for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo {gov} > $g; done")
    # Re-selecting a governor resets its tunables to kernel defaults; put the boot values back.
    if gov == "ondemand" and (pdir / "ondemand.json").exists() and ONDEMAND.exists():
        _ondemand_set(json.loads((pdir / "ondemand.json").read_text()))
    if DROPIN.exists():
        DROPIN.unlink()
        sh("systemctl daemon-reload")
    jd = Path("/etc/systemd/journald.conf.d/zz-soak.conf")
    if jd.exists():
        jd.unlink()
        sh("systemctl restart systemd-journald")
    sh("docker start dexi-node-red dexi-droneblocks", check=False)
    sh("systemctl start code-server lttng-sessiond mavlink2rest", check=False)


def restart_stack() -> None:
    sh("systemctl restart dexi.service")


def snapshot(rdir: Path, desc: str, pdir: Path) -> None:
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "experiment.txt").write_text(desc + "\n")
    diff = sh(f"diff -u {pdir/'dexi-config.yaml'} {CFG}; diff -u {pdir/'launch.py'} {LAUNCH}", check=False)
    for files in OVERLAYS.values():
        for _, target in files:
            backup = pdir / "overlay" / (target.parent.parent.name + "__" + target.name)
            if backup.exists():
                diff += sh(f"diff -u {backup} {target}", check=False)
    (rdir / "change.diff").write_text(diff)
    extra = sh("cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor; "
               "systemctl cat dexi.service | grep -i environment; docker ps --format '{{.Names}}'", check=False)
    (rdir / "state.txt").write_text(extra)


def run_one(name: str, exp: Exp, secs: int, warmup: int, runs: Path, pdir: Path) -> None:
    rdir = runs / name
    if (rdir / "summary.json").exists():
        log(f"skip {name} (done)")
        return
    desc, apply, env = exp
    log(f"=== {name}: {desc} ({secs}s, warmup {warmup}s)")
    restore(pdir)
    try:
        apply()
    except Exception as e:  # record and move on
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "error.txt").write_text(f"apply failed: {e}\n")
        log(f"!!! {name} apply failed: {e}")
        restore(pdir)
        restart_stack()
        return
    snapshot(rdir, desc, pdir)
    restart_stack()
    time.sleep(warmup)
    if sh("systemctl is-active dexi.service", check=False).strip() != "active":
        (rdir / "error.txt").write_text("dexi.service not active after warmup\n" +
                                        sh("journalctl -u dexi.service -n 80 --no-pager", check=False))
        log(f"!!! {name}: dexi.service not active; not profiling")
        return
    envs = " ".join(f"{k}={v}" for k, v in env.items())
    bar_hz = env.get("SOAK_BAR_HZ", "2.0")
    cmd = (f"env {envs} SOAK_EXTRA='--bar-hz {bar_hz}' {SOAKRUN} {name} {secs} \"{desc}\" "
           f"> {rdir}/profiler.log 2>&1")
    r = subprocess.run(cmd, shell=True)
    if r.returncode != 0:
        log(f"!!! {name}: profiler exit {r.returncode}")
    sh(f"journalctl -u dexi.service --since '-{secs + warmup + 30}s' --no-pager | tail -200 > {rdir}/dexi.journal", check=False)
    log(f"--- {name} done")


def main(argv=None) -> int:
    global LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--runs", type=Path, default=HOME / "soak/runs")
    ap.add_argument("--secs", type=int, default=900)
    ap.add_argument("--warmup", type=int, default=180)
    args = ap.parse_args(argv)
    if os.geteuid() != 0:
        print("run as root", file=sys.stderr)
        return 2
    args.runs.mkdir(parents=True, exist_ok=True)
    LOG = args.runs / "sweep.log"
    pdir = args.runs / "_pristine"
    take_pristine(pdir)
    PDIR.append(pdir)
    for line in args.plan.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        name, secs = parts[0], int(parts[1]) if len(parts) > 1 else args.secs
        warmup = int(parts[2]) if len(parts) > 2 else args.warmup
        if "+" in name:
            exp = combo(*name.split("+"))
        else:
            base = name.split("@")[0]  # baseline@2 = a repeat
            if base not in EXPERIMENTS:
                log(f"!!! unknown experiment {name}")
                continue
            exp = EXPERIMENTS[base]
        run_one(name.replace("+", "__"), exp, secs, warmup, args.runs, pdir)
    restore(pdir)
    restart_stack()
    log("plan complete; pristine config restored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
