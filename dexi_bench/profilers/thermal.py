"""Thermal cooldown profiler.

Captures a 1 Hz SoC temperature time-series while a cooling scenario plays out,
so different cooling methods can be overlaid on the same time axis. Built to
answer a concrete DEXI question: does prop-wash from takeoff cool the CM5 more
than a desk fan, and does it clear the soft-throttle ceiling?

Unlike the package runners this keeps the *raw* samples (the curve is the
result), but it reuses the same platform detection and samplers so temp comes
from `vcgencmd` on a Pi and `tegrastats` on Jetson with no new code.

Run it ON the device (where the sensors live):

    bench-thermal --scenario takeoff --secs 120

It prints a hot-baseline countdown, then a GO cue — trigger the cooling (fan
on / takeoff) at the cue and let it run. Output:

    results/<platform>/profiles/<date>_<sha>_thermal_<scenario>.csv   # raw 1 Hz
    results/<platform>/profiles/<date>_<sha>_thermal_<scenario>.json  # summary
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..monitors.base import Sample, SystemMonitor
from ..monitors.samplers import pick_sampler
from ..platform import detect

CSV_FIELDS = ["elapsed_s", "temp_c", "cpu_pct", "mem_mb", "power_w"]


@dataclass
class ProfileSummary:
    scenario: str
    platform_kind: str
    git_sha: str
    timestamp: str
    interval_s: float
    duration_s: float
    samples: int
    start_c: Optional[float]
    min_c: Optional[float]
    drop_c: Optional[float]
    notes: str = ""


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "nogit"


def _rows(samples: List[Sample], t0: float) -> List[dict]:
    rows = []
    for s in samples:
        rows.append({
            "elapsed_s": round(s.t - t0, 1),
            "temp_c": s.temp_c,
            "cpu_pct": s.cpu_pct,
            "mem_mb": round(s.mem_mb) if s.mem_mb is not None else None,
            "power_w": s.power_w,
        })
    return rows


def run_profile(scenario: str, secs: int, interval_s: float, results_dir: Path,
                baseline_s: int = 8, notes: str = "") -> ProfileSummary:
    plat = detect()
    sample_fn = pick_sampler(plat.kind, plat.has_jetson)

    mon = SystemMonitor(sample_fn, interval_s=interval_s)
    print(f">> thermal profile: scenario={scenario!r} platform={plat.kind} "
          f"duration={secs}s interval={interval_s}s")
    mon.start()

    # Hold for a hot baseline so the drop is visible, then cue the operator.
    for remaining in range(baseline_s, 0, -1):
        print(f"   baseline… {remaining}s", end="\r", flush=True)
        time.sleep(1)
    print("\n>> GO — trigger cooling now (fan on / takeoff). Logging…")

    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        time.sleep(0.5)
    summary_stats = mon.stop()

    samples = mon.samples
    t0 = mon.start_t
    rows = _rows(samples, t0)
    temps = [r["temp_c"] for r in rows if r["temp_c"] is not None]
    start_c = temps[0] if temps else None
    min_c = min(temps) if temps else None
    drop_c = round(start_c - min_c, 1) if (start_c is not None and min_c is not None) else None

    summary = ProfileSummary(
        scenario=scenario,
        platform_kind=plat.kind,
        git_sha=_git_sha(),
        timestamp=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        interval_s=interval_s,
        duration_s=round(summary_stats.duration_s, 1),
        samples=len(rows),
        start_c=start_c,
        min_c=min_c,
        drop_c=drop_c,
        notes=notes,
    )

    out_dir = results_dir / plat.kind / "profiles"
    out_dir.mkdir(parents=True, exist_ok=True)
    date = summary.timestamp[:10]
    stem = f"{date}_{summary.git_sha}_thermal_{scenario}"
    csv_path = out_dir / f"{stem}.csv"
    json_path = out_dir / f"{stem}.json"

    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    json_path.write_text(json.dumps(asdict(summary), indent=2) + "\n")

    print(f">> {scenario}: {start_c}°C → {min_c}°C  (−{drop_c}°C over {summary.duration_s}s)")
    print(f">> wrote {csv_path}")
    print(f">> wrote {json_path}")
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Capture a 1 Hz SoC thermal cooldown profile.")
    ap.add_argument("--scenario", required=True,
                    help="label for this cooling method, e.g. fan / takeoff / passive")
    ap.add_argument("--secs", type=int, default=120, help="logging duration (default 120)")
    ap.add_argument("--interval", type=float, default=1.0, help="sample interval s (default 1.0)")
    ap.add_argument("--baseline", type=int, default=8,
                    help="hot-baseline hold before the GO cue, s (default 8)")
    ap.add_argument("--results-dir", type=Path, default=Path("results"))
    ap.add_argument("--notes", default="")
    args = ap.parse_args(argv)

    run_profile(args.scenario, args.secs, args.interval, args.results_dir,
                baseline_s=args.baseline, notes=args.notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
