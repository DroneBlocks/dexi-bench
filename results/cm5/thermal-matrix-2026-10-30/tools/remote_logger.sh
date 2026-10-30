#!/usr/bin/env bash
# Drone-side 1 Hz thermal/CPU logger. Prints pure CSV to stdout. Arg1 = sample count.
N=${1:-70}
echo "elapsed_s,epoch,temp_c,arm_mhz,throttled_hex,throttled_now,cpu_total_pct,cpu0_pct,cpu1_pct,cpu2_pct,cpu3_pct,load1"
declare -a prev_total prev_idle cur_total cur_idle
read_stat(){
  while read -r name a b c d e f g h rest; do
    case "$name" in cpu) idx=0;; cpu0) idx=1;; cpu1) idx=2;; cpu2) idx=3;; cpu3) idx=4;; *) continue;; esac
    cur_idle[$idx]=$((d+e)); cur_total[$idx]=$((a+b+c+d+e+f+g+h))
  done < /proc/stat
}
read_stat
for i in 0 1 2 3 4; do prev_total[$i]=${cur_total[$i]}; prev_idle[$i]=${cur_idle[$i]}; done
start=$(date +%s)
for s in $(seq 1 "$N"); do
  sleep 1
  read_stat
  declare -a pcts
  for i in 0 1 2 3 4; do
    dt=$((cur_total[i]-prev_total[i])); di=$((cur_idle[i]-prev_idle[i]))
    if [ "$dt" -gt 0 ]; then pcts[$i]=$(awk "BEGIN{printf \"%.1f\",100*($dt-$di)/$dt}"); else pcts[$i]=0.0; fi
    prev_total[$i]=${cur_total[$i]}; prev_idle[$i]=${cur_idle[$i]}
  done
  epoch=$(date +%s); elapsed=$((epoch-start))
  tc=$(awk '{printf "%.1f",$1/1000}' /sys/class/thermal/thermal_zone0/temp)
  fr=$(vcgencmd measure_clock arm | sed 's/.*=//'); mhz=$(awk "BEGIN{printf \"%.0f\",$fr/1000000}")
  th=$(vcgencmd get_throttled | sed 's/.*=//')
  thnow=$(( (16#${th#0x} & 4) != 0 ? 1 : 0 ))
  load1=$(cut -d' ' -f1 /proc/loadavg)
  echo "$elapsed,$epoch,$tc,$mhz,$th,$thnow,${pcts[0]},${pcts[1]},${pcts[2]},${pcts[3]},${pcts[4]},$load1"
done
