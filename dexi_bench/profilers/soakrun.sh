#!/bin/bash
# usage: soakrun.sh <label> <secs> [notes]   (env SOAK_EXTRA adds profiler args)
source /home/dexi/ros2_jazzy/install/setup.bash
source /home/dexi/dexi_ws/install/setup.bash
export ROS_DOMAIN_ID=1 ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
cd /home/dexi/soak
exec python3 -m dexi_bench.profilers.soak --label "$1" --secs "$2" --notes "${3:-}" --out "/home/dexi/soak/runs/$1" $SOAK_EXTRA
