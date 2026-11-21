# Build and test plan: DEXI-3 CPU/thermal PRs (for 2026-11-22)

Board: dexi-0ceb, 192.168.68.61, `ssh -i C:\Users\denni\.ssh\dexi_bench dexi@192.168.68.61`.
State tonight: restored to the pre-soak config (yolo off, bar_counter on, tag_nav on with the DEXI-5
config, tag size 0.1905), dexi_vision at v0.2.1. Kernel 6.6.74, ondemand governor.
Report and raw data: https://github.com/DroneBlocks/dexi-bench/pull/4

## The PRs, in merge order

| # | PR | What | Measured | Needs before merge |
|---|---|---|---|---|
| 1 | https://github.com/DroneBlocks/dexi_yolo/pull/7 | fixed-schedule rate gate | 1.92 -> 2.00 Hz | review |
| 1 | https://github.com/DroneBlocks/dexi_vision/pull/3 | fixed-schedule rate gate | bars_2 95.8 -> 100% | review |
| 2 | https://github.com/DroneBlocks/dexi_bringup/pull/70 | shared 6 Hz detector stream, drop compressed_2hz + raw_2hz throttles, YOLO 1 thread (cm5) | YOLO 35.6 -> 31.1, bar 11.1 -> 5.6 (% of a core) | #1 merged first |
| 3 | https://github.com/DroneBlocks/dexi_yolo/pull/8 | optional NCNN backend (default stays onnx) | YOLO 35.6 -> 17.0, board -6.9 pts, -3.6 C | `ncnn` in the image; a longer run (AprilTag dipped 8.36 -> 8.09 Hz once) |
| 3 | https://github.com/DroneBlocks/dexi_offboard/pull/27 | EventsExecutor (+ service thread) | offboard 11.8 -> 2.2 | **flight-command test in SITL or on the bench with PX4** |

Not PRs yet: `schedutil` governor in the dexi-os image (-7 C, biggest thermal win; one line:
`cpufreq.default_governor=schedutil` on the kernel cmdline), PX4 DDS topic trim (Dennis's call),
640x360 (needs a 640x360 camera calibration first).

## Rules learned this weekend (read first)

- **Never build C++ with the stack running.** It ran the 2 GB board out of memory for ~40 min.
  `sudo systemctl stop dexi.service`, build with `MAKEFLAGS=-j1`, then start it. dexi_offboard builds in ~60 s this way.
- **Never `apt install linux-perf`** (or anything that pulls `linux-image-rpi-*`): it dragged in a 6.12 kernel
  whose install failed half-way. Reverted; backups of the boot files are in `~/soak/bootfw_backup/`.
- The board's `dexi_bringup` source has 5 uncommitted local edits (cm5 config, launch, three .bak files);
  stash or compare before checking out PR #70 there.
- Python nodes are installed as copies: a source change needs `colcon build --packages-select <pkg>` too.

## 1. Set up the test config (YOLO + bar counter on, targets in view)

```bash
cp ~/.dexi-config.yaml ~/.dexi-config.yaml.orig
# set nodes.yolo.enabled: true, nodes.bar_counter.enabled: true, nodes.tag_nav.enabled: false
sudo systemctl restart dexi.service
```

## 2. Baseline (15 min) with the soak profiler (kept in ~/soak)

```bash
sudo ~/soak/soakrun.sh base_before 900 "before PRs"      # writes ~/soak/runs/base_before/summary.json
```

## 3. Build the PR branches

```bash
cd ~/dexi_ws/src/dexi_yolo   && git fetch origin && git checkout perf/schedule-rate-gate
cd ~/dexi_ws/src/dexi_vision && git fetch origin && git checkout perf/schedule-rate-gate
cd ~/dexi_ws/src/dexi_bringup && git stash -u && git fetch origin && git checkout perf/shared-detector-stream
sudo systemctl stop dexi.service
cd ~/dexi_ws && source ~/ros2_jazzy/install/setup.bash && source install/setup.bash
MAKEFLAGS=-j1 colcon build --packages-select dexi_yolo dexi_vision dexi_bringup
sudo systemctl start dexi.service
sudo ~/soak/soakrun.sh prs_1_2 900 "gate + shared stream"
```
Expect: YOLO 2.00 Hz, bars_2 ~100%, YOLO ~31% and bar ~5.6% of a core, new `image_throttle_detector_node` ~2.7%.
Note PR #70's main also changes the tag size line to 0.1524 (6 in) and has no raw_2hz node; set the tag size for your targets.

## 4. NCNN (PR dexi_yolo#8)

```bash
sudo pip3 install --break-system-packages ncnn     # the node runs as root, so a --user install is not seen
cd ~/dexi_ws/src/dexi_yolo && git checkout perf/ncnn-backend   # or merge it onto the gate branch
sudo systemctl stop dexi.service && (cd ~/dexi_ws && colcon build --packages-select dexi_yolo) && sudo systemctl start dexi.service
# start YOLO with backend:=ncnn (add a launch arg in bringup, or run it by hand):
ros2 launch dexi_yolo yolo_onnx_launch.py backend:=ncnn detection_frequency:=2.0 num_threads:=1
```
Run 30 min against a baseline; check AprilTag Hz holds (it read 8.09 vs 8.36 in one 10 min run).

## 5. EventsExecutor (PR dexi_offboard#27): flight-command test, no props or in SITL

```bash
cd ~/dexi_ws/src/dexi_offboard && git fetch origin && git checkout perf/events-executor
sudo systemctl stop dexi.service && (cd ~/dexi_ws && MAKEFLAGS=-j1 colcon build --packages-select dexi_offboard) && sudo systemctl start dexi.service
```
Check: `/dexi/offboard_manager/status` 5 Hz, `/fmu/out/vehicle_local_position_20hz` 20 Hz, both services listed (all passed on the bench).
Then, in SITL or with Dennis's approval on the bench: takeoff, a move, land through `/dexi/flight/command`, and confirm
status/position keep updating while a blocking command runs. Never arm on the bench without Dennis.

## 6. Put the board back

```bash
for r in dexi_yolo dexi_vision dexi_offboard; do git -C ~/dexi_ws/src/$r checkout main; done
git -C ~/dexi_ws/src/dexi_bringup checkout - && git -C ~/dexi_ws/src/dexi_bringup stash pop
sudo systemctl stop dexi.service && (cd ~/dexi_ws && MAKEFLAGS=-j1 colcon build --packages-select dexi_yolo dexi_vision dexi_offboard dexi_bringup) && sudo systemctl start dexi.service
cp ~/.dexi-config.yaml.orig ~/.dexi-config.yaml
```
The pre-soak originals are also in `~/bench-backup-2026-11-20/`.
