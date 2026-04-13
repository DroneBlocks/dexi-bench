# Benchmark Bag Recording Guide

Canonical rosbags are the **input side** of every dexi-bench run. They must be recorded once, named with a version suffix, and then replayed byte-identically across all hardware platforms. Re-recording is an explicit version bump (`v1` → `v2`), never a silent refresh.

## Bag set (v1)

| Bag | Purpose | Scene | Motion | Duration |
|---|---|---|---|---|
| `bench_yolo_sparse_v1` | Realistic YOLO throughput | All 6 trained classes spread on floor like the drone challenge field | Slow single pass with downward-facing camera on a cart/boom | 60–90s |
| `bench_yolo_dense_v1` | YOLO perf ceiling | All 6 classes clustered in one downward frame | Tripod, no motion | 30s |
| `bench_apriltag_sparse_v1` | Realistic AprilTag throughput | 4 printed tags spread on floor at varied positions | Slow single pass, downward-facing | 60s |
| `bench_apriltag_dense_v1` | AprilTag perf ceiling | 4 tags on a wall at measured distances (1m, 2m, 3m, 2m-off-angle) | Tripod, no motion | 60s |

Record on the **Pi .63 setup** (Hailo hat + Arducam USB). Same camera across all bags — camera variance is a separate axis we do not want mixed in.

## Topics to record

```bash
# Common across all bags
/camera/image_raw          # or /camera/image_raw/compressed if bandwidth is an issue
/camera/camera_info

# Not recorded (these are what we measure, not replay):
#   /yolo_detections
#   /apriltag/detections
```

Explicit command:

```bash
ros2 bag record \
  --output bench_yolo_sparse_v1 \
  --storage mcap \
  /camera/image_raw /camera/camera_info
```

Use **mcap** storage (faster, smaller, the ROS2 default going forward).

## Recording procedure

### 1. Fix the camera settings

Before recording, lock exposure/gain/white balance if your camera driver supports it. Auto-exposure shifts frame content between runs and muddles comparisons. For the Arducam, set explicit params in the camera launch file.

Also note the resolution and framerate you chose — record them in the `bag_metadata.yaml` described below. Start with **1280×720 @ 30 FPS** unless you have reason to go higher.

### 2. Stage the scene

Walk through the scene before starting:
- No humans in frame (unless a human-shaped target is intentional)
- Consistent overhead lighting, no windows with direct sun
- Targets taped or weighted so they can't shift
- For `_dense` bags, all targets fit inside a single frame from the chosen camera distance
- For `_sparse` bags, lay out the targets along the path the cart will travel

### 3. Record

```bash
# ssh to Pi .63, bring up the camera
ros2 launch dexi_camera camera.launch.py   # or whatever the actual launch is

# in a second terminal
cd ~/bags
ros2 bag record --output bench_yolo_sparse_v1 --storage mcap \
  /camera/image_raw /camera/camera_info
```

Let the camera run idle for ~5 seconds before starting the pass (warmup is cheap in the bag, expensive to re-record). Start the cart/tripod pass, hit Ctrl+C when done.

### 4. Sidecar metadata

Write `bench_yolo_sparse_v1/bag_metadata.yaml` next to the bag so the benchmark result can be interpreted forever:

```yaml
bag_version: v1
workload: bench_yolo_sparse
recorded: 2026-04-13
recording_host: pi63
camera: arducam_imx708
resolution: 1280x720
fps: 30
exposure_mode: locked
duration_s: 75
scene_description: "6 dexi_yolo_training classes (ball, bottle, chair, laptop, cup, book) spread across 4m indoor floor, overhead LED, downward camera at ~1.5m"
motion: "slow single pass, cart-mounted, ~0.3 m/s"
ground_truth:
  class_counts:  # optional — how many of each class are in frame at peak
    ball: 1
    bottle: 1
    chair: 1
    laptop: 1
    cup: 1
    book: 1
```

### 5. Verify the bag

Before calling it canonical:

```bash
ros2 bag info bench_yolo_sparse_v1
```

Check:
- Total duration matches expected (±2s)
- Message count on `/camera/image_raw` ≈ `fps × duration` (drops should be <1%)
- File size is sane (720p30 mcap runs ~5–15 MB/s; 75s ≈ 400–1100 MB)

Sanity-check by replaying locally with dexi_yolo running:

```bash
ros2 run dexi_yolo dexi_yolo_node_hailo.py &
ros2 bag play bench_yolo_sparse_v1
# confirm /yolo_detections populates and detections look right
```

## Publishing canonical bags

Bags are too large to commit. Distribution flow:

1. Upload to a GitHub Release on the `dexi-bench-data` repo (or `dexi-sim-ftw` releases, tagged `bench-data-v1`).
2. `dexi_bench/scripts/fetch_bags.sh` (future) downloads by version.
3. Version bumps are additive: `v2` is a new release, `v1` stays forever so old result files remain interpretable.

Do **not** edit a published bag in place — always bump the version, even for a trivial re-record.

## Re-recording (version bump)

If a target moves, the scene changes, or you switch cameras, create `_v2` and note the difference in its `bag_metadata.yaml`. Old `_v1` results remain valid for their era; new results against `_v2` start a fresh baseline. `compare.py` groups by `workload.name + bag_version`, so the two generations will not be mixed in regression checks.
