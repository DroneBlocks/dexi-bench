#!/usr/bin/env bash
# Run one 1 Hz thermal phase on the bench drone: ./phase.sh <label> [seconds] [host]
set -euo pipefail
LABEL=${1:?label}; SECS=${2:-300}; HOST=${3:-${DEXI_HOST:?set DEXI_HOST to the drone .local name}}
DIR="$(cd "$(dirname "$0")" && pwd)"; OUT="$DIR/../runs/$LABEL.csv"
sshpass -p "${DEXI_PASS:-dexi}" scp -q -o StrictHostKeyChecking=no "$DIR/remote_logger.sh" dexi@$HOST:/tmp/remote_logger.sh
sshpass -p "${DEXI_PASS:-dexi}" ssh -o StrictHostKeyChecking=no dexi@$HOST "bash /tmp/remote_logger.sh $SECS" > "$OUT"
python3 - "$OUT" <<'PY'
import csv,sys
r=list(csv.DictReader(open(sys.argv[1]))); t=[float(x['temp_c']) for x in r]; m=[float(x['arm_mhz']) for x in r]; c=[float(x['cpu_total_pct']) for x in r]
thr=sum(x['throttled_now']=='1' for x in r)
print(f"{sys.argv[1].split('/')[-1]}: start {t[0]} end {t[-1]} max {max(t)} | clock min {min(m):.0f} | cpu avg {sum(c)/len(c):.0f}% | throttled {thr}/{len(r)} s")
PY
