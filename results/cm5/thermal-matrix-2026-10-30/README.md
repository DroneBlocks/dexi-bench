# CM5 bench thermal matrix — 2026-10-30

One DEXI-3 (CM5 Lite, DEXI-OS) on the bench, motors off, 1 Hz logs of SoC temp,
ARM clock, throttle flags, CPU % and load. Three loads (stack off, DEXI-OS,
DEXI-OS + YOLO avr_2026 at 2 Hz / 2 threads), each with and without a desk fan.

Open `report.html` for the results table and timeline.

- `runs/` — raw CSVs, one per phase (`*_cont` extends the phase before it)
- `tools/phase.sh` — logs one phase: `DEXI_HOST=dexi-xxxx.local tools/phase.sh <label> [seconds]`
- `tools/remote_logger.sh` — the drone-side 1 Hz logger
- `build_report.py` — rebuilds `report.html` from `runs/` and `report.template.html`
