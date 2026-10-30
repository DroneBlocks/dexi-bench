#!/usr/bin/env python3
"""Build report.html from runs/*.csv (1 Hz logs from tools/remote_logger.sh)."""
import csv
import json
from pathlib import Path

HERE = Path(__file__).parent

# Phase -> the CSVs that make it up (a "_cont" file extends the same condition).
PHASES = [
    ("A", "Stack off", False, ["A1_stack-off_no-fan", "A1b_stack-off_no-fan_cont"]),
    ("A", "Stack off", True, ["A2_stack-off_fan"]),
    ("B", "DEXI-OS", False, ["B1_dexi-os_no-fan", "B1b_dexi-os_no-fan_cont"]),
    ("C", "DEXI-OS + YOLO", False, ["C1_dexi-os+yolo_no-fan"]),
    ("C", "DEXI-OS + YOLO", True, ["C2_dexi-os+yolo_fan"]),
    ("B", "DEXI-OS", True, ["B2_dexi-os_fan"]),
]


def load(name):
    with open(HERE / "runs" / f"{name}.csv") as f:
        return list(csv.DictReader(f))


# The x-axis is logging time: idle gaps between phases (fan changes, the
# operator away) are collapsed to GAP minutes so the chart shows only data.
GAP = 1.0
offset = 0.0
phases = []
for key, stack, fan, files in PHASES:
    rows = [r for name in files for r in load(name)]
    t0 = int(rows[0]["epoch"])
    pts = [
        [
            round(offset + (int(r["epoch"]) - t0) / 60, 3),
            float(r["temp_c"]),
            int(float(r["arm_mhz"])),
            float(r["load1"]),
            int(r["throttled_now"]),
            round((int(r["epoch"]) - t0) / 60, 1),
        ]
        for r in rows
    ]
    offset = pts[-1][0] + GAP
    tail = rows[-30:]
    phases.append({
        "key": key,
        "stack": stack,
        "fan": fan,
        "pts": pts,
        "minutes": round((int(rows[-1]["epoch"]) - int(rows[0]["epoch"])) / 60, 1),
        "settled": round(sum(float(r["temp_c"]) for r in tail) / len(tail), 1),
        "peak": max(float(r["temp_c"]) for r in rows),
        "cpu": round(sum(float(r["cpu_total_pct"]) for r in rows) / len(rows), 1),
        "load": round(sum(float(r["load1"]) for r in rows) / len(rows), 2),
        "throttled": sum(int(r["throttled_now"]) for r in rows),
        "samples": len(rows),
    })

template = (HERE / "report.template.html").read_text()
(HERE / "report.html").write_text(template.replace("/*DATA*/null", json.dumps(phases)))
print("wrote report.html")
