#!/bin/bash
# Video-to-browser comparison, run from a PC on the drone's network (rogbeast).
# For each mode: start the soak profiler on the board (detached), run the stand-in client here
# for the same window, then collect. Stack is the soak config; nothing on it is changed except
# that web_video_server is started standalone for its own run and stopped after.
HOST=192.168.68.61
KEY=/c/Users/denni/.ssh/dexi_bench
SECS=${SECS:-600}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$HERE/../runs/video
mkdir -p "$OUT"
S() { timeout 120 ssh -o ConnectTimeout=30 -i $KEY dexi@$HOST "$@"; }

prof() {  # label notes
  S "sudo setsid nohup /home/dexi/soak/soakrun.sh $1 $((SECS + 5)) '$2' > /dev/null 2>&1 < /dev/null & disown"
  sleep 3
}
wait_summary() {
  for i in $(seq 60); do S "test -f /home/dexi/soak/runs/$1/summary.json" && return 0; sleep 15; done; return 1
}

echo "== v_none: no video client"
prof v_none "no video client"
sleep $((SECS + 10)); wait_summary v_none

echo "== v_rosbridge30: rosbridge, CBOR, throttle 33 ms (as CameraFeed.vue)"
prof v_rosbridge30 "rosbridge video client, CBOR, throttle_rate 33 (CameraFeed.vue)"
python "$HERE/video_client.py" rosbridge --host $HOST --secs $SECS --throttle-ms 33 --out "$OUT/client_rosbridge30.json"
wait_summary v_rosbridge30

echo "== v_rosbridge15: rosbridge, CBOR, throttle 66 ms"
prof v_rosbridge15 "rosbridge video client, CBOR, throttle_rate 66"
python "$HERE/video_client.py" rosbridge --host $HOST --secs $SECS --throttle-ms 66 --out "$OUT/client_rosbridge15.json"
wait_summary v_rosbridge15

echo "== v_wvs: web_video_server, type=ros_compressed (JPEG passthrough)"
S "sudo setsid nohup bash -c 'source /home/dexi/ros2_jazzy/install/setup.bash && source /home/dexi/dexi_ws/install/setup.bash && export ROS_DOMAIN_ID=1 ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET && exec ros2 run web_video_server web_video_server --ros-args -p port:=8080' > /home/dexi/soak/wvs.log 2>&1 < /dev/null & disown"
sleep 15
prof v_wvs "web_video_server MJPEG client, type=ros_compressed"
python "$HERE/video_client.py" mjpeg --url "http://$HOST:8080/stream?topic=/cam0/image_raw&type=ros_compressed" --secs $SECS --out "$OUT/client_wvs.json"
wait_summary v_wvs
S "sudo pkill -f web_video_server_node; sudo pkill -f 'lib/web_video_server/web_video_server'; true"
S "tail -20 /home/dexi/soak/wvs.log" > "$OUT/wvs.log"
echo "video test done"
