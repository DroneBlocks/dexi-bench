"""Overlay thermal profiles onto one comparison chart.

Reads results/**/profiles/*_thermal_*.csv and aligns every run on elapsed_s so
cooling scenarios (fan vs takeoff vs passive) sit on the same time axis. Writes
a self-contained HTML chart with a per-run temperature-drop card.

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


def _load(path: Path) -> List[dict]:
    pts = []
    with path.open() as f:
        for r in csv.DictReader(f):
            if r.get("temp_c") in (None, ""):
                continue
            pts.append({"x": float(r["elapsed_s"]), "y": float(r["temp_c"])})
    return pts


def build(results_dir: Path, platform: Optional[str], out: Path) -> Path:
    root = results_dir / platform if platform else results_dir
    csvs = sorted(root.rglob("*_thermal_*.csv"))
    if not csvs:
        raise SystemExit(f"No thermal profiles under {root}. Capture one: "
                         f"bench-thermal --scenario fan")

    series = []
    for i, path in enumerate(csvs):
        pts = _load(path)
        if not pts:
            continue
        temps = [p["y"] for p in pts]
        start, lo = temps[0], min(temps)
        series.append({
            "label": _scenario_of(path),
            "color": COLORS[i % len(COLORS)],
            "points": pts,
            "start": start, "min": lo, "drop": round(start - lo, 1),
            "secs": round(pts[-1]["x"]),
        })

    cards = "".join(
        f'<div class="card"><div class="k">{s["label"]}</div>'
        f'<div class="v" style="color:{s["color"]}">&minus;{s["drop"]}&deg;C</div>'
        f'<div class="d">{s["start"]:.1f}&deg; &rarr; {s["min"]:.1f}&deg; over {s["secs"]}s</div></div>'
        for s in series
    )
    datasets = json.dumps([
        {"label": s["label"], "data": s["points"], "borderColor": s["color"],
         "backgroundColor": s["color"], "borderWidth": 2, "pointRadius": 0,
         "tension": 0.25}
        for s in series
    ])

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dexi-bench — Thermal Cooldown</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{{--bg:#0d1117;--card:#161b22;--line:#30363d;--txt:#e6edf3;--mut:#8b949e}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:28px}}
h1{{font-size:22px;margin:0 0 4px}}
.sub{{color:var(--mut);margin:0 0 22px;font-size:13px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:26px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}
.card .k{{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.5px}}
.card .v{{font-size:26px;font-weight:650;margin-top:6px}}
.card .d{{font-size:12px;color:var(--mut);margin-top:4px}}
.chart-wrap{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}}
canvas{{max-height:440px}}
</style></head><body>
<h1>dexi-bench &mdash; SoC Thermal Cooldown</h1>
<p class="sub">1 Hz SoC temperature, aligned on elapsed seconds. Each run starts hot, then cooling is triggered.</p>
<div class="cards">{cards}</div>
<div class="chart-wrap"><canvas id="c"></canvas></div>
<script>
new Chart(document.getElementById('c'),{{
  type:'line',
  data:{{datasets:{datasets}}},
  options:{{responsive:true,interaction:{{mode:'index',intersect:false}},
    scales:{{x:{{type:'linear',title:{{display:true,text:'elapsed (s)',color:'#8b949e'}},
              ticks:{{color:'#8b949e'}},grid:{{color:'#30363d'}}}},
            y:{{title:{{display:true,text:'SoC °C',color:'#8b949e'}},
              ticks:{{color:'#8b949e'}},grid:{{color:'#30363d'}}}}}},
    plugins:{{legend:{{labels:{{color:'#e6edf3'}}}}}}}}
}});
</script></body></html>"""

    out.write_text(html)
    print(f"Wrote {out}  ({len(series)} run(s): {', '.join(s['label'] for s in series)})")
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
