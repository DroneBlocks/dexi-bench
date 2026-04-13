# dexi-bench

**Cross-platform performance benchmarking harness for DEXI drone packages.**

Runs identical workloads — canned rosbags, fixed prompt sets, deterministic SITL scenarios — against the most CPU- and accelerator-intensive nodes in the DEXI stack, then records FPS, latency, CPU, memory, temperature, and power to versioned JSON results so performance can be tracked **over time, across hardware, and across releases**.

*Naming note: the project, GitHub repo, and PyPI distribution are `dexi-bench` (hyphenated). The Python import name is `dexi_bench` (underscored), because that's required to be a valid Python identifier. So `pip install dexi-bench` ships a package you use as `from dexi_bench.schema.result import BenchResult`. This mirrors the convention used by packages like `scikit-learn` / `sklearn` and `flask-sqlalchemy` / `flask_sqlalchemy`.*

## Why

DEXI ships with **AprilTag detection enabled by default** — it's the most important perception package on the drone, powering corridor navigation, odometry, and landing pad alignment out of the box. Any CPU budget left over goes to secondary perception like YOLO. That ordering matters: if an `dexi_apriltag` update regresses on a shipping drone, customers notice immediately; a YOLO regression is a feature-flag issue. So AprilTag is the first-class benchmark target, and everything else orbits it.

DEXI also started life on a Raspberry Pi CM4, where YOLO object detection ran at roughly **2 FPS** — enough to compete in our drone challenge, but a clear ceiling once we started asking more of the vision stack. That baseline drove the Hailo 8L integration on Pi 5, the push toward Hailo 10H and Jetson Orin Nano, and the broader question of *which SoM actually belongs on a DEXI drone for a given workload*. The long-term goal is both **AprilTag and YOLO running concurrently out of the box** — which needs both packages benchmarked, on every platform, every release.

The DEXI stack now runs on a widening matrix of hardware — CM4, CM5, Pi 5, Pi 5 + Hailo 8L, (planned) Pi 5 + Hailo 10H, Jetson Orin Nano — and every new model or package update shifts numbers somewhere on that matrix. Without a consistent benchmark harness, questions like:

- "Did the last `dexi_apriltag` update regress detection latency on Pi 5?"
- "Can this platform run `dexi_apriltag` + `dexi_yolo` concurrently without dropping AprilTag frames?"
- "Does `dexi_llm` actually run faster on Orin Nano than on Pi 5?"
- "How much CPU headroom does `dexi_apriltag` leave for offboard control?"
- "Which platform should we recommend to a community member running our challenge?"

…are answered today with anecdotes and one-off screenshots. `dexi-bench` replaces that with committed JSON result files, historical comparison, and automated regression detection on every package update.

## Benchmark targets

The targets are the packages that dominate CPU, accelerator time, memory, or real-time deadlines — the places a regression actually hurts a flight. Ordered by priority: the packages at the top are the ones DEXI ships with enabled by default.

| Package | Repo | Workload | Key metrics |
|---|---|---|---|
| `dexi_apriltag` | [DroneBlocks/dexi_apriltag](https://github.com/DroneBlocks/dexi_apriltag) | Canonical rosbag (sparse + dense), known tag positions | detections/sec, detection latency, odometry drift, CPU % |
| `dexi_yolo` | [DroneBlocks/dexi_yolo](https://github.com/DroneBlocks/dexi_yolo) | Canonical rosbag (sparse + dense), 6 trained classes from `dexi_yolo_training` | FPS, inference p50/p95, CPU %, temp, power |
| `dexi_llm` | [DroneBlocks/dexi_llm](https://github.com/DroneBlocks/dexi_llm) | Fixed prompt set, drone-command intents | tokens/sec, time-to-first-token, agent iterations |
| `dexi_camera` | [DroneBlocks/dexi_camera](https://github.com/DroneBlocks/dexi_camera) | Raw 1080p30 capture, 60s sustained | dropped frames, CPU %, temperature |
| `dexi_offboard` | [DroneBlocks/dexi_offboard](https://github.com/DroneBlocks/dexi_offboard) | SITL takeoff + 60s setpoint loop | setpoint rate jitter, CPU %, RT latency |

`dexi_apriltag` is the first-class benchmark target because it ships enabled by default on every DEXI drone. `dexi_yolo` is second because the long-term roadmap has AprilTag and YOLO running concurrently on every platform. The remaining targets land in priority order after those two are stable.

A **combined `bench_apriltag_plus_yolo` workload** is planned — replay the same bag while running both pipelines concurrently and measure whether either pipeline drops frames under contention. This is the test that actually answers "can this platform ship with both enabled?"

Runners land one at a time. `bench-yolo` is the first concrete implementation (simpler to get right; the runner architecture is target-agnostic). `bench-apriltag` is next and will follow the same pattern — see `docs/DESIGN.md`.

## Hardware matrix

| Platform | Vision accelerator | LLM capable | Status |
|---|---|---|---|
| Raspberry Pi CM4 | — (ONNX CPU) | ✗ | Original DEXI reference — YOLO ~2 FPS, motivating the move to accelerated compute |
| Raspberry Pi CM5 | — (ONNX CPU) | ✗ | Planned — mid-tier CPU-only comparison |
| Raspberry Pi 5 | — (ONNX CPU) | ~3 tok/s (CPU) | Supported — CPU baseline |
| Raspberry Pi 5 | Hailo 8L (13 TOPS) | ✗ | Supported — current drone reference (Pi .60, .63) |
| Jetson Orin Nano | Ampere GPU (40 TOPS) | ✓ (up to ~7B) | In progress — [dexi-os jetson branch](https://github.com/DroneBlocks/dexi-os/tree/feature/jetson-docker-support) |
| Raspberry Pi 5 | Hailo 10H (40 TOPS) | ✓ (small LLMs) | Planned |

Every run records the detected platform so results are always attributable to specific hardware. See `dexi_bench/platform.py` for the detection logic.

## Design principles

1. **Black-box.** dexi-bench observes target nodes via ROS2 topic subscription and stdout parsing. It does **not** import from or modify `dexi_yolo`, `dexi_llm`, `apriltag_ros`, or any target package. Adding a new target doesn't require changes to that target.
2. **Reproducible.** Canonical rosbags and prompt sets are recorded once, versioned (`v1`, `v2`), and replayed byte-identically across all platforms. Only hardware and software under test vary between runs.
3. **Historical.** Every run writes a JSON result file committed to `results/<platform>/<date>_<sha>_<workload>.json`. `bench-compare` renders markdown comparison tables and flags regressions against the most recent prior result in the same group.
4. **Portable.** Platform-specific system monitors (`tegrastats` on Jetson, `vcgencmd` on Pi, `psutil` everywhere) are shims behind a common interface; the same harness runs on any supported platform.
5. **Not a ROS package.** `dexi-bench` is a plain Python project — `pyproject.toml`, `pip install -e .`, entry-point CLIs. It uses `rclpy` for topic subscription and `ros2 bag play` via subprocess for bag playback, but it has no `package.xml` and doesn't need `colcon build`. Works wherever ROS2 is sourced.

## Install

Requirements:
- A system with ROS2 sourced (Jazzy recommended, Humble should work)
- The `dexi_ws` workspace built and sourced (for `dexi_yolo`, `dexi_interfaces`, etc.)
- Python ≥ 3.10, `pip`

```bash
# on the Pi (or any target platform)
source /opt/ros/jazzy/setup.bash
source ~/dexi_ws/install/setup.bash

cd ~/_dev
git clone https://github.com/DroneBlocks/dexi-bench.git
cd dexi-bench
pip install -e . --break-system-packages
```

`--break-system-packages` is needed on Debian Bookworm-based distros (Raspberry Pi OS, DEXI-OS). On a venv or conda environment, omit it.

## Usage

### Run a single benchmark

```bash
bench-yolo \
  --engine hailo \
  --bag ~/bags/bench_yolo_sparse_v1 \
  --workload bench_yolo_sparse \
  --duration 60 \
  --warmup 5 \
  --results-dir ~/dexi_bench_results
```

What happens:
1. Detects platform (`pi5`, `orin_nano`, ...), picks the appropriate system-monitor shim.
2. Launches the target node via `ros2 run dexi_yolo dexi_yolo_node_hailo.py`.
3. Waits for node startup, then begins rosbag playback and topic subscription.
4. Runs a warmup window, resets timers, then times the measurement window.
5. On completion, cleanly shuts down the target, aggregates metrics, writes result JSON.

### Compare historical results

```bash
bench-compare --results-dir ~/dexi_bench_results
```

Renders a markdown table of the latest run per `(platform, package, workload, engine)` group, and flags any group whose most recent run regressed:

- FPS dropped > 10% vs previous
- Inference p95 rose > 15% vs previous
- CPU average rose > 10 percentage points (absolute) vs previous

Filter to a specific target:

```bash
bench-compare --package dexi_yolo --workload bench_yolo_sparse
```

## Canonical workloads (v1)

| Workload | Target | Bag / input |
|---|---|---|
| `bench_apriltag_sparse` | `dexi_apriltag` | `bench_apriltag_sparse_v1` — 4 tags spread on floor, slow single pass |
| `bench_apriltag_dense` | `dexi_apriltag` | `bench_apriltag_dense_v1` — 4 tags on wall at measured distances |
| `bench_yolo_sparse` | `dexi_yolo` | `bench_yolo_sparse_v1` — 6 trained classes spread on floor, slow single pass |
| `bench_yolo_dense` | `dexi_yolo` | `bench_yolo_dense_v1` — all 6 classes clustered in one frame, tripod static |
| `bench_apriltag_plus_yolo` | `dexi_apriltag` + `dexi_yolo` | Combined rosbag with both tags and classes, both pipelines running concurrently |
| `bench_camera_1080p30` | `dexi_camera` | Live capture, 60s |
| `bench_llm_intents` | `dexi_llm` | Fixed prompt set (drone-command intents) |
| `bench_offboard_sitl` | `dexi_offboard` | PX4 SITL, 60s setpoint loop |

Recording procedure is in [`docs/BAG_RECORDING.md`](docs/BAG_RECORDING.md). Full architecture and open questions are in [`docs/DESIGN.md`](docs/DESIGN.md).

## Result schema

Every run produces a JSON file like:

```json
{
  "schema_version": 1,
  "timestamp": "2026-04-12T18:23:42Z",
  "platform": {
    "kind": "pi5",
    "arch": "aarch64",
    "os_release": "Debian GNU/Linux 12 (bookworm)",
    "accelerator": "hailo_8l"
  },
  "target": {
    "package": "dexi_yolo",
    "engine": "hailo",
    "version": "0.2.1",
    "git_sha": "396ccdb"
  },
  "workload": {
    "name": "bench_yolo_sparse",
    "bag_version": "v1",
    "duration_s": 60,
    "warmup_s": 5
  },
  "metrics": {
    "fps_avg": 108.4,
    "inference_ms_p50": 8.9,
    "inference_ms_p95": 11.2,
    "detections_total": 1820,
    "cpu_pct_avg": 7.3,
    "cpu_pct_max": 14.1,
    "mem_mb_max": 412,
    "temp_c_max": 58.5,
    "power_w_avg": 5.2
  },
  "notes": ""
}
```

## Status

| Component | Status |
|---|---|
| Scaffolding, pyproject, platform detection | ✅ |
| Result schema + JSON writer | ✅ |
| System monitor (psutil, vcgencmd, tegrastats) | ✅ |
| `bench-apriltag` runner | ✅ (subscribes to upstream `apriltag_ros`, bypasses `dexi_apriltag`'s 10 Hz odometry throttle) |
| `bench-yolo` runner | ✅ |
| `bench-compare` + regression detection | ✅ |
| Canonical bag recording procedure | ✅ documented, bags not yet recorded |
| `bench-apriltag-plus-yolo` concurrent runner | 🔜 |
| `bench-llm` runner | 🔜 |
| `bench-camera` runner | 🔜 |
| `bench-offboard` runner | 🔜 |
| Hailo NPU utilization via `hailortcli` | 🔜 |
| Published `dexi-bench-data` release | 🔜 |

## Layout

```
dexi-bench/                     # repo root (hyphenated)
├── pyproject.toml              # name = "dexi-bench" (hyphen)
├── README.md
├── docs/
│   ├── DESIGN.md               # full architecture, open questions
│   └── BAG_RECORDING.md        # how to record canonical bags
├── dexi_bench/                 # Python import package (underscore — required)
│   ├── platform.py             # pi5 / cm4 / cm5 / orin_nano / hailo / jetson detection
│   ├── runners/
│   │   ├── yolo_runner.py      # bench-yolo entry point
│   │   └── apriltag_runner.py  # bench-apriltag entry point
│   ├── monitors/
│   │   ├── base.py             # SystemMonitor + Sample + MonitorSummary
│   │   └── samplers.py         # psutil / vcgencmd / tegrastats shims
│   ├── schema/
│   │   └── result.py           # BenchResult dataclasses + write_result()
│   └── compare.py              # bench-compare entry point
├── config/                     # per-workload YAML configs (future)
└── results/                    # committed JSON results, per-platform subdirs
```

## Related repos

- [DroneBlocks/dexi_apriltag](https://github.com/DroneBlocks/dexi_apriltag) — AprilTag detection + corridor navigation + odometry (ships enabled by default)
- [DroneBlocks/dexi_yolo](https://github.com/DroneBlocks/dexi_yolo) — YOLO node with PyTorch/ONNX/Hailo backends
- [DroneBlocks/dexi_llm](https://github.com/DroneBlocks/dexi_llm) — Local LLM for natural-language drone commands
- [DroneBlocks/dexi_camera](https://github.com/DroneBlocks/dexi_camera) — Pi 5 + Arducam camera node
- [DroneBlocks/dexi_offboard](https://github.com/DroneBlocks/dexi_offboard) — PX4 offboard control manager
- [DroneBlocks/dexi-os](https://github.com/DroneBlocks/dexi-os) — Base OS images for Pi 5, CM4/5, Jetson
- [DroneBlocks/dexi_yolo_training](https://github.com/DroneBlocks/dexi_yolo_training) — Training data + printed targets for the drone challenge

## License

MIT
