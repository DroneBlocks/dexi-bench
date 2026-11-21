# DEXI-3 CM5 weekend bench soak, 2026-11-21

Open `report.html` (rebuild with `python build_report.py`; text sections live in `extra.json`).

- Board: dexi-0ceb, CM5 Lite 2 GB, image v0.24-rc2, dexi_vision v0.2.1, bench supply + desk fan, props off.
- Targets about 1.1 m away: AprilTag tag36h11 ID 0 (5.75 in), AVR 2026 sheet (square-end 2-bar disc, water barn, toxic barrel).
- `runs.tar.gz` (unpacks to `runs/`; `build_report.py` unpacks it automatically): one folder per run: `system.csv` (1 Hz), `procs.csv.gz` (per-process CPU), `detections.csv`,
  `summary.json`, `change.diff` and `state.txt` (what the run changed), `dexi.journal`.
- `runs/p4/`: capture media (Phase 4) logs and ffprobe results. `runs/video/`: client-side results of the video test.
- `runs/_failed_*`, `cam_nv12`: runs that did not measure anything (kept as evidence).
- `tools/`: Phase 4 script, video test driver and stand-in client.

Profiler and sweep runner: `dexi_bench/profilers/soak.py`, `soak_sweep.py`.
