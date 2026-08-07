# Flight Bag + Camera Video (AprilTag / vibration inspection)

Record a flight rosbag on the drone (camera + AprilTag detections + full PX4 telemetry), then turn the camera stream into an mp4 so you can **visually inspect AprilTag / camera vibration and jitter during flight**. This is the diagnostic companion to the benchmark bags in [BAG_RECORDING.md](BAG_RECORDING.md) — those replay for perf; this one is for *watching* what the camera actually saw.

First produced 2026-06-30 on DEXI-3 `.56`. Sample output: `~/Downloads/flight_video.mp4` (3114 frames, 184.3 s, 16.89 fps).

## 1. Record on the drone

```bash
BAG=~/flight_bags/flight_$(date +%Y%m%d_%H%M%S)
ros2 bag record -o "$BAG" \
  /cam0/image_raw/compressed /cam0/camera_info /apriltag_detections /tf /tf_static \
  /fmu/out/sensor_combined /fmu/out/vehicle_attitude /fmu/out/vehicle_local_position \
  /fmu/out/vehicle_odometry /fmu/out/vehicle_status /fmu/out/vehicle_control_mode \
  /fmu/out/vehicle_land_detected /fmu/out/estimator_status_flags /fmu/out/battery_status_v1 \
  /fmu/in/sensor_optical_flow /fmu/in/distance_sensor
```

- Record the **compressed** camera topic (`/cam0/image_raw/compressed`) — raw is too much bandwidth to disk.
- Pair the camera with `/apriltag_detections` + the `/fmu/*` telemetry so the video can be correlated with attitude / EKF / flow later.
- **Interrupted bag?** Rebuild metadata: `ros2 bag reindex "$BAG" -s mcap`.

## 2. Bag → mp4

Run `dexi_bench/bag_to_video.py` on the drone (ROS2 sourced). It reads the compressed frames, `cv2.imdecode`s them, and `ffmpeg`s an mp4 at the bag's own frame rate:

```bash
python3 dexi_bench/bag_to_video.py            # newest ~/flight_bags/flight_* → /tmp/flight_video.mp4
python3 dexi_bench/bag_to_video.py "$BAG" /tmp/out.mp4
```

Under the hood it's:
```
frames → /tmp/vid_frames/f%05d.jpg
ffmpeg -y -framerate <fps> -i /tmp/vid_frames/f%05d.jpg \
       -c:v libx264 -pix_fmt yuv420p -movflags +faststart flight_video.mp4
```
Then pull it to the Mac: `scp dexi@<ip>:/tmp/flight_video.mp4 ~/Downloads/`.

## Artifacts per flight
- Bag: `~/flight_bags/flight_<ts>` on the drone (mcap).
- ULog: pull over MAVLink (log-download) — e.g. `/tmp/flight.ulg`.
- Video: `flight_video.mp4` (Mac `~/Downloads/`).

## Gotchas
- **Camera wedged (publisher up, no frames)** after a service restart → **reboot** the drone to clear, don't just restart the service.
- Reading FC params over MAVLink (`pymavlink udpout:127.0.0.1:14550`): **PX4 INT params are bit-reinterpreted** — encode via `struct.unpack('<f', struct.pack('<i', v))[0]` or you corrupt them.
