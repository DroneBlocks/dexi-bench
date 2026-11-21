#!/bin/bash
# Phase 4: Ryan's dexi_capture_media (dexi-os #102) on top of the full stack.
# Runs ON the board as root, after the sweep (stack in its soak config).
# Launches the node standalone, never through bringup. Removes it afterwards.
# no set -u: ROS setup.bash references unset variables (COLCON_TRACE)
OUT=/home/dexi/soak/runs/p4
mkdir -p $OUT
LOG=$OUT/phase4.log
exec > >(tee -a $LOG) 2>&1
say() { echo "$(date -u +%H:%M:%S)Z $*"; }

source /home/dexi/ros2_jazzy/install/setup.bash
source /home/dexi/dexi_ws/install/setup.bash
export ROS_DOMAIN_ID=1 ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
WS=/home/dexi/dexi_ws
PKG=$WS/src/dexi_capture_media
MEDIA=/home/dexi/dexi_media
NODEPY=$WS/install/dexi_capture_media/lib/python3.12/site-packages/dexi_capture_media/capture_media_node.py
SRV=/dexi/capture_media

call() { timeout 20 ros2 service call $SRV/$1 std_srvs/srv/Trigger 2>&1 | tail -1; }
probe() { ffprobe -v error -show_entries format=duration,size:stream=codec_name,nb_frames -of json "$1" 2>&1; }

launch_node() {  # $@ = extra launch args
  ros2 launch dexi_capture_media capture_media.launch.py "$@" > $OUT/node_$(date +%s).log 2>&1 &
  NODE_LAUNCH=$!
  for i in $(seq 30); do ros2 service list 2>/dev/null | grep -q $SRV/capture_photo && break; sleep 2; done
  say "node up (launch pid $NODE_LAUNCH)"
}
stop_node() {
  kill -INT $NODE_LAUNCH 2>/dev/null; sleep 5
  pkill -9 -f capture_media_node 2>/dev/null; pkill -9 -f "ffmpeg.*dexi_media" 2>/dev/null; true
}

say "== build"
if [ ! -d $PKG ]; then
  sudo -u dexi git clone -q -b feature/capture-media https://github.com/DroneBlocks/dexi_capture_media.git $PKG
fi
git -C $PKG rev-parse HEAD > $OUT/capture_media.rev
which ffmpeg ffprobe
(cd $WS && sudo -u dexi bash -c "source /home/dexi/ros2_jazzy/install/setup.bash && source install/setup.bash && colcon build --packages-select dexi_capture_media" ) | tail -3
source $WS/install/setup.bash
ls $NODEPY || NODEPY=$(find $WS/install/dexi_capture_media -name capture_media_node.py | head -1)
cp $NODEPY $OUT/capture_media_node.orig.py

say "== A: node idle on top of the stack (5 min)"
launch_node
/home/dexi/soak/soakrun.sh p4_idle 300 "capture_media loaded, idle"

say "== B: photo service x5"
for i in 1 2 3 4 5; do /usr/bin/time -f "%e s" bash -c "timeout 20 ros2 service call $SRV/capture_photo std_srvs/srv/Trigger | tail -1" ; sleep 1; done
ls -la $MEDIA | tee $OUT/photos.txt
for f in $MEDIA/photo_*.jpg; do python3 -c "import cv2,sys;im=cv2.imread(sys.argv[1]);print(sys.argv[1], None if im is None else im.shape)" $f; done

say "== C: 10 min recording while profiling (max_video_s 600 default)"
T0=$(date +%s.%N); call start_recording
/home/dexi/soak/soakrun.sh p4_record 630 "capture_media recording (+faststart)"
call stop_recording
T1=$(date +%s.%N)
sleep 10
V=$(ls -t $MEDIA/video_*.mp4 | head -1)
say "video $V wall $(python3 -c "print(round($T1-$T0,1))") s"
probe "$V" | tee $OUT/record10_probe.json
journalctl --since "-12min" --no-pager | grep -i capture_media | tail -20 > $OUT/record10_journal.txt

say "== D: finish time for +faststart (60 s clip)"
call start_recording; sleep 60
S=$(date +%s.%N); call stop_recording; E=$(date +%s.%N)
say "stop_recording took $(python3 -c "print(round($E-$S,2))") s"

say "== E: crash tests with +faststart"
call start_recording; sleep 30
ls -la $MEDIA/*.part
pkill -9 -f capture_media_node; sleep 8
say "after kill -9 node: ffmpeg still running? $(pgrep -af 'ffmpeg' | grep -c dexi_media)"
for p in $MEDIA/*.part; do say "part $p"; ls -la "$p"; probe "$p" | tee $OUT/crash_node_faststart_probe.json; cp "$p" $OUT/crash_node_faststart.part.mp4 2>/dev/null; done
pkill -9 -f "ffmpeg.*dexi_media"; rm -f $MEDIA/*.part; stop_node
launch_node
call start_recording; sleep 30
pkill -9 -f "ffmpeg.*dexi_media"; sleep 5
for p in $MEDIA/*.part; do say "part $p"; ls -la "$p"; probe "$p" | tee $OUT/crash_ffmpeg_faststart_probe.json; done
call stop_recording; sleep 3; rm -f $MEDIA/*.part; stop_node

say "== F: retest with -movflags frag_keyframe+empty_moov"
sed -i "s/'-movflags', '+faststart'/'-movflags', 'frag_keyframe+empty_moov'/" $NODEPY
grep -n movflags $NODEPY
launch_node
call start_recording; sleep 30
pkill -9 -f capture_media_node; sleep 8
for p in $MEDIA/*.part; do say "part $p"; probe "$p" | tee $OUT/crash_node_frag_probe.json; done
pkill -9 -f "ffmpeg.*dexi_media"; rm -f $MEDIA/*.part; stop_node
launch_node
call start_recording; sleep 30
pkill -9 -f "ffmpeg.*dexi_media"; sleep 5
for p in $MEDIA/*.part; do say "part $p"; ls -la "$p"; probe "$p" | tee $OUT/crash_ffmpeg_frag_probe.json; done
call stop_recording; sleep 3; rm -f $MEDIA/*.part
say "== G: frag cost: 5 min recording profiled, file size and finish time"
call start_recording
/home/dexi/soak/soakrun.sh p4_record_frag 300 "capture_media recording (frag_keyframe+empty_moov)"
S=$(date +%s.%N); call stop_recording; E=$(date +%s.%N)
say "frag stop_recording took $(python3 -c "print(round($E-$S,2))") s"
V=$(ls -t $MEDIA/video_*.mp4 | head -1); probe "$V" | tee $OUT/frag5_probe.json
stop_node
say "== reference: 5 min +faststart recording profiled, for a like-for-like size/CPU"
cp $OUT/capture_media_node.orig.py $NODEPY
launch_node
call start_recording
/home/dexi/soak/soakrun.sh p4_record_faststart5 300 "capture_media recording (+faststart), 5 min"
call stop_recording; sleep 5
V=$(ls -t $MEDIA/video_*.mp4 | head -1); probe "$V" | tee $OUT/faststart5_probe.json

say "== H: disk guard (min_free_mb above free space)"
stop_node
FREE=$(df -m --output=avail /home/dexi | tail -1)
launch_node min_free_mb:=$((FREE + 1000))
say "free $FREE MB; photo: $(call capture_photo)"
say "record: $(call start_recording)"
stop_node

say "== cleanup: remove package and media"
ls -la $MEDIA > $OUT/media_final_listing.txt
rm -rf $MEDIA $PKG $WS/build/dexi_capture_media $WS/install/dexi_capture_media
sed -i '/dexi_capture_media/d' $WS/install/.colcon_install_layout 2>/dev/null; true
ls $WS/install | grep -c capture_media
say "phase 4 done"
touch $OUT/DONE
