"""Overlay thermal profiles onto comparison charts.

Reads results/**/profiles/*_thermal_*.csv and aligns every run on elapsed_s so
cooling scenarios (fan vs takeoff vs passive) sit on the same time axis. Writes
a self-contained HTML page:
  - SoC temperature overlay (solid), with flight-controller board temp dashed
    when a run captured it (--fc-url);
  - ARM clock overlay (second panel) when any run has clock data — shows the
    soft-throttle cap lifting as the SoC cools.

    bench-thermal-compare
    bench-thermal-compare --results-dir results --platform cm5 --out thermal.html
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import List, Optional

COLORS = ["#ff6b6b", "#4dabf7", "#51cf66", "#ffd43b", "#cc5de8", "#ff922b"]


def _scenario_of(path: Path) -> str:
    # <date>_<sha>_thermal_<scenario>.csv
    stem = path.stem
    return stem.split("_thermal_", 1)[1] if "_thermal_" in stem else stem


def _col(rows: List[dict], field: str) -> List[dict]:
    pts = []
    for r in rows:
        v = r.get(field)
        if v in (None, ""):
            continue
        pts.append({"x": float(r["elapsed_s"]), "y": float(v)})
    return pts


def _load(path: Path) -> List[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def build(results_dir: Path, platform: Optional[str], out: Path) -> Path:
    root = results_dir / platform if platform else results_dir
    csvs = sorted(root.rglob("*_thermal_*.csv"))
    if not csvs:
        raise SystemExit(f"No thermal profiles under {root}. Capture one: "
                         f"bench-thermal --scenario fan")

    series = []
    for i, path in enumerate(csvs):
        rows = _load(path)
        temp = _col(rows, "temp_c")
        if not temp:
            continue
        ys = [p["y"] for p in temp]
        series.append({
            "label": _scenario_of(path),
            "color": COLORS[i % len(COLORS)],
            "temp": temp,
            "fc": _col(rows, "fc_temp_c"),
            "clock": _col(rows, "clock_mhz"),
            "start": ys[0], "min": min(ys), "drop": round(ys[0] - min(ys), 1),
            "secs": round(temp[-1]["x"]),
        })

    has_clock = any(s["clock"] for s in series)
    has_fc = any(s["fc"] for s in series)

    cards = "".join(
        f'<div class="card"><div class="k">{s["label"]}</div>'
        f'<div class="v" style="color:{s["color"]}">&minus;{s["drop"]}&deg;C</div>'
        f'<div class="d">{s["start"]:.1f}&deg; &rarr; {s["min"]:.1f}&deg; over {s["secs"]}s</div></div>'
        for s in series
    )

    temp_ds = []
    for s in series:
        temp_ds.append({"label": s["label"], "data": s["temp"], "borderColor": s["color"],
                        "backgroundColor": s["color"], "borderWidth": 2, "pointRadius": 0,
                        "tension": 0.25})
        if s["fc"]:
            temp_ds.append({"label": f'{s["label"]} (FC)', "data": s["fc"], "borderColor": s["color"],
                            "backgroundColor": s["color"], "borderWidth": 1.5, "pointRadius": 0,
                            "borderDash": [6, 4], "tension": 0.25})
    clock_ds = [
        {"label": s["label"], "data": s["clock"], "borderColor": s["color"],
         "backgroundColor": s["color"], "borderWidth": 2, "pointRadius": 0, "stepped": True}
        for s in series if s["clock"]
    ]

    fc_note = ('<p class="note">Dashed lines = flight-controller board temp (IMU sensor) over MAVLink.</p>'
               if has_fc else "")
    clock_panel = ("""
<div class="chart-wrap"><h2>ARM clock (MHz)</h2><canvas id="clk"></canvas>
<p class="note">Soft-throttle pins the core at ~1500 MHz when hot; it snaps back to full speed once the SoC cools below the limit.</p></div>"""
                   if has_clock else "")
    clock_js = (f"""
new Chart(document.getElementById('clk'),{{type:'line',data:{{datasets:{json.dumps(clock_ds)}}},
  options:{{responsive:true,interaction:{{mode:'index',intersect:false}},
    scales:{{x:{{type:'linear',title:{{display:true,text:'elapsed (s)',color:'#8b949e'}},ticks:{{color:'#8b949e'}},grid:{{color:'#30363d'}}}},
            y:{{title:{{display:true,text:'MHz',color:'#8b949e'}},ticks:{{color:'#8b949e'}},grid:{{color:'#30363d'}}}}}},
    plugins:{{legend:{{labels:{{color:'#e6edf3'}}}}}}}}}});"""
                if has_clock else "")

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dexi-bench — Thermal Cooldown</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{{--bg:#0d1117;--card:#161b22;--line:#30363d;--txt:#e6edf3;--mut:#8b949e}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:28px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:15px;margin:0 0 12px;font-weight:600}}
.sub{{color:var(--mut);margin:0 0 22px;font-size:13px}}
.note{{color:var(--mut);font-size:12px;margin:8px 0 0}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:26px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}
.card .k{{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.5px}}
.card .v{{font-size:26px;font-weight:650;margin-top:6px}}
.card .d{{font-size:12px;color:var(--mut);margin-top:4px}}
.chart-wrap{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:22px}}
canvas{{max-height:440px}}
</style></head><body>
<h1>dexi-bench &mdash; SoC Thermal Cooldown</h1>
<p class="sub">1 Hz, aligned on elapsed seconds. Each run starts hot, then cooling is triggered.</p>
<div class="cards">{cards}</div>
<div class="chart-wrap"><h2>SoC temperature (&deg;C)</h2><canvas id="temp"></canvas>{fc_note}</div>{clock_panel}
<script>
new Chart(document.getElementById('temp'),{{type:'line',data:{{datasets:{json.dumps(temp_ds)}}},
  options:{{responsive:true,interaction:{{mode:'index',intersect:false}},
    scales:{{x:{{type:'linear',title:{{display:true,text:'elapsed (s)',color:'#8b949e'}},ticks:{{color:'#8b949e'}},grid:{{color:'#30363d'}}}},
            y:{{title:{{display:true,text:'°C',color:'#8b949e'}},ticks:{{color:'#8b949e'}},grid:{{color:'#30363d'}}}}}},
    plugins:{{legend:{{labels:{{color:'#e6edf3'}}}}}}}}}});{clock_js}
</script></body></html>"""

    out.write_text(html)
    extras = []
    if has_clock:
        extras.append("clock")
    if has_fc:
        extras.append("fc-temp")
    suffix = f" [+{', '.join(extras)}]" if extras else ""
    print(f"Wrote {out}  ({len(series)} run(s): {', '.join(s['label'] for s in series)}){suffix}")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Overlay thermal cooldown profiles into one chart.")
    ap.add_argument("--results-dir", type=Path, default=Path("results"))
    ap.add_argument("--platform", default=None, help="restrict to one platform kind, e.g. cm5")
    ap.add_argument("--out", type=Path, default=Path("thermal_compare.html"))
    args = ap.parse_args(argv)
    build(args.results_dir, args.platform, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
