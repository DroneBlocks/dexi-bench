"""Build report.html from runs/ (soak profiler output pulled off the board).

    python build_report.py            # writes report.html next to this file

Ranking rule: every experiment is compared with the mean of the baseline runs
taken just before and just after it (room and board temperature drift over a
night, so a single early baseline would flatter or punish later runs).
CPU is whole-board busy % (100 = all 4 cores). Temperature is the mean of the
last 5 min of each run. Detection effect is the worst relative change in
target hits per second across AprilTag, YOLO (water_barn, toxic_fluid) and the
bar counter.
"""

from __future__ import annotations

import csv
import gzip
import html
import json
import statistics
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
RUNS = HERE / "runs"
EXTRA = HERE / "extra.json"     # hand-written findings / phase-4 results

BLUE = "#2a78d6"
ORANGE = "#eb6834"
GRAY = "#8a8985"


def load_runs():
    runs = {}
    for d in sorted(RUNS.iterdir()):
        sj = d / "summary.json"
        if d.name.startswith("_"):
            continue
        if not sj.exists() or (d / "error.txt").exists():
            if (d / "error.txt").exists():
                runs[d.name] = {"error": (d / "error.txt").read_text()[:600],
                                "desc": (d / "experiment.txt").read_text().strip()
                                if (d / "experiment.txt").exists() else d.name}
            continue
        s = json.loads(sj.read_text())
        if s["pipelines"]["camera"]["hz"] < 1:
            runs[d.name] = {"error": "camera published nothing for the whole run (stack came up without a camera stream). "
                            + ((d / "dexi.journal").read_text()[-400:] if (d / "dexi.journal").exists() else ""),
                            "desc": (d / "experiment.txt").read_text().strip() if (d / "experiment.txt").exists() else d.name}
            continue
        rows = list(csv.DictReader(open(d / "system.csv")))
        s["rows"] = rows
        s["desc"] = (d / "experiment.txt").read_text().strip() if (d / "experiment.txt").exists() else s.get("notes", "")
        s["t0"] = datetime.fromisoformat(s["started_utc"])
        dur = s["duration_s"]
        tail = [float(r["temp_c"]) for r in rows if r["temp_c"] and float(r["elapsed_s"]) >= dur - 300]
        s["temp_tail"] = statistics.fmean(tail) if tail else None
        p = s["pipelines"]
        tot = lambda k: sum(int(r[k]) for r in rows)
        s["hits"] = {
            "AprilTag ID 0": tot("tag_hit") / dur,
            "YOLO water_barn": tot("yolo_hit_water_barn") / dur,
            "YOLO toxic_fluid": tot("yolo_hit_toxic_fluid") / dur,
            "Bar counter bars_2": tot("bar_hit") / dur,
        }
        s["bar_wrong_hz"] = tot("bar_wrong") / dur
        runs[d.name] = s
    return runs


def is_base(name):
    return name == "baseline" or name.startswith("baseline@")


REGIME_B = set()   # runs made while ondemand had kernel-default tunables (filled from extra.json)


def regime(n):
    return "B" if n in REGIME_B else "A"


def bracket(runs, name):
    """Mean of the nearest same-state baseline before and after `name` (by start time)."""
    t = runs[name]["t0"]
    bases = sorted((r["t0"], n) for n, r in runs.items() if is_base(n) and "t0" in r
                   and n != "baseline@p1" and regime(n) == regime(name))
    before = [n for tt, n in bases if tt < t]
    after = [n for tt, n in bases if tt > t]
    pick = ([before[-1]] if before else []) + ([after[0]] if after else [])
    return pick


def mean_of(runs, names, f):
    xs = [f(runs[n]) for n in names]
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def compare(runs):
    out = []
    for n, r in runs.items():
        if "error" in r or is_base(n) or "__" in n or n.startswith(("p4", "v_")):
            continue
        b = bracket(runs, n)
        if not b:
            continue
        bc = mean_of(runs, b, lambda x: x["cpu_pct"]["mean"])
        bt = mean_of(runs, b, lambda x: x["temp_tail"])
        eff = {}
        for k in r["hits"]:
            bh = mean_of(runs, b, lambda x: x["hits"][k])
            eff[k] = None if not bh else (r["hits"][k] - bh) / bh
        worst = min((v for v in eff.values() if v is not None), default=0.0)
        out.append({
            "name": n, "desc": r["desc"], "bases": b,
            "cpu": r["cpu_pct"]["mean"], "cpu_saved": bc - r["cpu_pct"]["mean"],
            "temp": r["temp_tail"], "temp_delta": (r["temp_tail"] - bt) if (r["temp_tail"] and bt) else None,
            "effect": eff, "worst": worst,
            "bar_wrong_hz": r["bar_wrong_hz"],
            "throttled": r["throttled_now_share"], "soft": r["soft_temp_limit_now_share"],
            "procs": r["procs_cpu_pct_mean"],
        })
    # free (< 5 % loss on every pipeline) first by CPU saved, then the rest by CPU saved per 10 % loss
    def key(c):
        loss = max(0.0, -c["worst"])
        if loss < 0.05:
            return (0, -c["cpu_saved"])
        return (1, -c["cpu_saved"] / (loss / 0.10))
    return sorted(out, key=key)


# ---- svg helpers ------------------------------------------------------------

def line_chart(series, title, unit, w=760, h=220, color=BLUE, y0=None, y1=None):
    """series: list of (x_seconds, y). One series, one axis."""
    pts = [(x, y) for x, y in series if y is not None]
    if not pts:
        return ""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    lo = y0 if y0 is not None else min(ys)
    hi = y1 if y1 is not None else max(ys)
    if hi - lo < 1e-6:
        hi = lo + 1
    pad = (hi - lo) * 0.08
    if y0 is None:
        lo -= pad
    if y1 is None:
        hi += pad
    L, R, T, B = 48, 12, 12, 28
    X = lambda x: L + (x - xs[0]) / max(1, xs[-1] - xs[0]) * (w - L - R)
    Y = lambda y: T + (hi - y) / (hi - lo) * (h - T - B)
    # 15 s means keep the line readable
    step = 15
    agg = []
    for i in range(0, len(pts), step):
        chunk = pts[i:i + step]
        agg.append((chunk[0][0], statistics.fmean(c[1] for c in chunk)))
    d = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(agg))
    grid = ""
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        grid += (f'<line x1="{L}" x2="{w-R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#e8e7e3"/>'
                 f'<text x="{L-6}" y="{Y(v)+4:.1f}" text-anchor="end" class="ax">{v:.0f}</text>')
    xt = ""
    span = xs[-1] - xs[0]
    tick = 300 if span > 1200 else 120
    t = 0
    while t <= span:
        xt += f'<text x="{X(xs[0]+t):.1f}" y="{h-8}" text-anchor="middle" class="ax">{int(t/60)}m</text>'
        t += tick
    hov = "".join(
        f'<rect x="{X(x)-4:.1f}" y="{T}" width="8" height="{h-T-B}" fill="transparent">'
        f'<title>{int(x//60)}m{int(x%60):02d}s: {y:.1f} {unit}</title></rect>' for x, y in agg)
    return (f'<figure><figcaption>{html.escape(title)}</figcaption>'
            f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{html.escape(title)}">'
            f'{grid}{xt}<path d="{d}" fill="none" stroke="{color}" stroke-width="2"/>{hov}</svg></figure>')


def bar_chart(items, title, unit, w=760):
    """items: list of (label, value, tooltip). Horizontal bars from a zero line."""
    if not items:
        return ""
    row = 22
    h = row * len(items) + 30
    L, R = 230, 60
    vmax = max(abs(v) for _, v, _ in items) or 1
    vmin = min(0, min(v for _, v, _ in items))
    vtop = max(0, max(v for _, v, _ in items))
    X = lambda v: L + (v - vmin) / ((vtop - vmin) or 1) * (w - L - R)
    out = f'<line x1="{X(0):.1f}" x2="{X(0):.1f}" y1="4" y2="{h-24}" stroke="#bdbcb7"/>'
    for i, (lab, v, tip) in enumerate(items):
        y = 6 + i * row
        x0, x1 = sorted((X(0), X(v)))
        col = BLUE if v >= 0 else GRAY
        out += (f'<g><title>{html.escape(tip)}</title>'
                f'<rect x="0" y="{y-2}" width="{w}" height="{row}" fill="transparent"/>'
                f'<text x="{L-8}" y="{y+12}" text-anchor="end" class="lab">{html.escape(lab)}</text>'
                f'<rect x="{x0:.1f}" y="{y+2}" width="{max(1.5, x1-x0):.1f}" height="{row-8}" rx="3" fill="{col}"/>'
                f'<text x="{(x1 + 6) if v >= 0 else (X(0) + 6):.1f}" y="{y+12}" '
                f'text-anchor="start" class="val">{v:+.1f}</text></g>')
    out += f'<text x="{X(0):.1f}" y="{h-8}" text-anchor="middle" class="ax">0 {unit}</text>'
    return (f'<figure><figcaption>{html.escape(title)}</figcaption>'
            f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{html.escape(title)}">{out}</svg></figure>')


def pct(v, digits=0):
    return "n/a" if v is None else f"{v*100:+.{digits}f}%"


def fmt(v, d=1):
    return "n/a" if v is None else f"{v:.{d}f}"


CSS = """
body{font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0b0b0b;background:#fff;max-width:980px;margin:32px auto;padding:0 20px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:36px 0 8px;border-bottom:1px solid #e8e7e3;padding-bottom:4px}
h3{font-size:16px;margin:20px 0 6px}.sub{color:#52514e;margin:0 0 20px}
table{border-collapse:collapse;width:100%;margin:8px 0 16px;font-size:13.5px}
th,td{border-bottom:1px solid #eeede9;padding:5px 8px;text-align:right;vertical-align:top}
th{background:#f6f6f4;font-weight:600}td:first-child,th:first-child{text-align:left}
td.l{text-align:left}.good{color:#0b6b2e;font-weight:600}.bad{color:#a3231f;font-weight:600}
figure{margin:12px 0 20px}figcaption{font-weight:600;font-size:14px;margin-bottom:4px}
svg .ax{font-size:11px;fill:#6b6a66}svg .lab{font-size:12px;fill:#2b2b2a}svg .val{font-size:11.5px;fill:#2b2b2a}
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}.kpi{border:1px solid #e8e7e3;border-radius:8px;padding:10px 14px;min-width:150px}
.kpi b{display:block;font-size:22px}.kpi span{color:#52514e;font-size:12.5px}
.note{background:#f7f9fc;border-left:3px solid #2a78d6;padding:8px 12px;margin:10px 0}
code{background:#f3f3f1;padding:1px 4px;border-radius:3px;font-size:12.5px}
"""


def build():
    runs = load_runs()
    extra = json.loads(EXTRA.read_text()) if EXTRA.exists() else {}
    REGIME_B.update(extra.get("regime_b", []))
    cmp_ = compare(runs)
    base = runs.get("baseline")
    bases = [n for n in runs if is_base(n) and "error" not in runs[n] and n != "baseline@p1" and regime(n) == "A"]
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>DEXI-3 CM5 weekend soak</title><style>{CSS}</style></head><body>"]
    parts.append("<h1>DEXI-3 CM5 weekend bench soak</h1>")
    parts.append(f"<p class='sub'>dexi-0ceb, CM5 Lite 2 GB, image v0.24-rc2, dexi_vision v0.2.1. Bench supply, desk fan, props off. "
                 f"{html.escape(extra.get('dates', ''))}</p>")
    if extra.get("headline"):
        parts.append("<div class='note'>" + extra["headline"] + "</div>")

    # ---- ranked gains
    parts.append("<h2>1. Ranked gains</h2>")
    noise = None
    if len(bases) >= 2:
        cs = [runs[n]["cpu_pct"]["mean"] for n in bases]
        ts = [runs[n]["temp_tail"] for n in bases if runs[n]["temp_tail"]]
        noise = (max(cs) - min(cs), (max(ts) - min(ts)) if ts else None)
        parts.append(f"<p>Each change ran 15 min after a 3 min warm-up, compared with the baseline runs on either side of it. "
                     f"Baseline-to-baseline spread across the night: <b>{noise[0]:.1f} pts CPU</b>, "
                     f"<b>{fmt(noise[1])} °C</b>. Gains smaller than that are noise.</p>")
    parts.append(bar_chart([(c["name"], c["cpu_saved"],
                             f"{c['desc']}: CPU {c['cpu']:.1f}% (saved {c['cpu_saved']:+.1f} pts), "
                             f"temp {fmt(c['temp_delta'])} °C, worst detection change {pct(c['worst'])}")
                            for c in sorted(cmp_, key=lambda c: -c["cpu_saved"])],
                           "Board CPU saved vs bracketing baselines (points of 400% / 4 cores = 100%)", "pts"))
    parts.append("<table><tr><th>#</th><th>Change</th><th>CPU %</th><th>CPU saved (pts)</th><th>Temp Δ °C</th>"
                 "<th>AprilTag</th><th>water_barn</th><th>toxic_fluid</th><th>bars_2</th><th>Verdict</th></tr>")
    for i, c in enumerate(cmp_, 1):
        e = c["effect"]
        loss = max(0.0, -c["worst"])
        verdict = extra.get("verdicts", {}).get(c["name"], "free" if loss < 0.05 else f"costs {loss*100:.0f}% detections")
        cell = lambda v: f"<td class='{'bad' if v is not None and v < -0.05 else ''}'>{pct(v)}</td>"
        parts.append(f"<tr><td>{i}</td><td class='l'><b>{html.escape(c['name'])}</b><br><span style='color:#52514e'>{html.escape(c['desc'])}</span></td>"
                     f"<td>{c['cpu']:.1f}</td><td class='{'good' if noise and c['cpu_saved'] > noise[0] else ''}'>{c['cpu_saved']:+.1f}</td>"
                     f"<td>{fmt(c['temp_delta'])}</td>{cell(e['AprilTag ID 0'])}{cell(e['YOLO water_barn'])}"
                     f"{cell(e['YOLO toxic_fluid'])}{cell(e['Bar counter bars_2'])}<td class='l'>{html.escape(verdict)}</td></tr>")
    parts.append("</table><p style='color:#52514e;font-size:13px'>Order: changes that cost under 5% on every pipeline first, by CPU saved; "
                 "then the rest by CPU saved per 10% of detections lost. Detection columns are the change in target hits per second.</p>")
    errs = {n: r for n, r in runs.items() if "error" in r}
    if errs:
        parts.append("<h3>Changes that did not run</h3><table><tr><th>Change</th><th>What happened</th></tr>")
        for n, r in errs.items():
            parts.append(f"<tr><td>{html.escape(n)}</td><td class='l'><code>{html.escape(r['error'][:300])}</code></td></tr>")
        parts.append("</table>")
    for k in ("combined_html",):
        if extra.get(k):
            parts.append(extra[k])

    # ---- baseline
    if base:
        parts.append("<h2>2. Baseline (30 min, everything on)</h2>")
        p = base["pipelines"]
        parts.append("<div class='kpis'>"
                     f"<div class='kpi'><b>{base['cpu_pct']['mean']:.0f}%</b><span>board CPU, mean (p95 {base['cpu_pct']['p95']:.0f}%)</span></div>"
                     f"<div class='kpi'><b>{base['temp_c']['mean']:.1f} °C</b><span>SoC temp, mean (max {base['temp_c']['max']:.1f})</span></div>"
                     f"<div class='kpi'><b>{base['clock_mhz']['min']:.0f}–{base['clock_mhz']['max']:.0f}</b><span>ARM MHz range</span></div>"
                     f"<div class='kpi'><b>{base['mem_available_mb']['min']:.0f} MB</b><span>MemAvailable, min</span></div>"
                     f"<div class='kpi'><b>{base['swap_used_mb']['max']:.0f} MB</b><span>swap used, max</span></div>"
                     "</div>")
        parts.append(f"<p>Throttled now in {base['throttled_now_share']*100:.0f}% of samples; soft temperature limit active in "
                     f"{base['soft_temp_limit_now_share']*100:.0f}%. Last <code>get_throttled</code>: 0x{(base['throttled_raw_last'] or 0):x}.</p>")
        parts.append("<table><tr><th>Pipeline</th><th>Rate (Hz)</th><th>Target hit rate</th></tr>"
                     f"<tr><td>Camera</td><td>{p['camera']['hz']:.1f}</td><td>n/a</td></tr>"
                     f"<tr><td>AprilTag (ID 0)</td><td>{p['apriltag']['hz']:.2f}</td><td>{p['apriltag']['hit_rate']*100:.1f}%</td></tr>"
                     f"<tr><td>YOLO</td><td>{p['yolo']['hz']:.2f}</td><td>water_barn {p['yolo']['hit_rate']['water_barn']*100:.1f}%, toxic_fluid {p['yolo']['hit_rate']['toxic_fluid']*100:.1f}%</td></tr>"
                     f"<tr><td>Bar counter</td><td>{p['bar_counter']['hz']:.2f}</td><td>bars_2 {p['bar_counter']['read_rate']*100:.1f}%, wrong {p['bar_counter']['wrong_rate']*100:.1f}%, abstain {p['bar_counter']['abstain_rate']*100:.1f}%</td></tr></table>")
        rows = base["rows"]
        parts.append(line_chart([(float(r["elapsed_s"]), float(r["cpu_pct"])) for r in rows], "Board CPU % over the baseline (15 s means)", "%", y0=0, y1=100))
        parts.append(line_chart([(float(r["elapsed_s"]), float(r["temp_c"]) if r["temp_c"] else None) for r in rows], "SoC temperature °C over the baseline", "°C", color=ORANGE))
        parts.append("<h3>CPU by process (mean, 100% = one core)</h3><table><tr><th>Process / node</th><th>CPU %</th><th>RSS MB</th></tr>")
        for k, v in list(base["procs_cpu_pct_mean"].items())[:16]:
            k_show = "python3 (this soak profiler)" if k == "python3" else k
            parts.append(f"<tr><td>{html.escape(k_show)}</td><td>{v:.1f}</td><td>{base['procs_rss_mb_mean'].get(k, 0):.0f}</td></tr>")
        parts.append("</table>")
    # drift
    if bases:
        parts.append("<h3>Baseline repeats</h3><table><tr><th>Run</th><th>Start (UTC)</th><th>CPU %</th><th>Temp last 5 min °C</th><th>AprilTag Hz</th><th>bars_2 read</th></tr>")
        for n in sorted(bases, key=lambda n: runs[n]["t0"]):
            r = runs[n]
            parts.append(f"<tr><td>{n}</td><td>{r['started_utc'][11:16]}</td><td>{r['cpu_pct']['mean']:.1f}</td><td>{fmt(r['temp_tail'])}</td>"
                         f"<td>{r['pipelines']['apriltag']['hz']:.2f}</td><td>{r['pipelines']['bar_counter']['read_rate']*100:.1f}%</td></tr>")
        parts.append("</table>")

    for sec in ("bar_html", "capture_html", "video_html", "findings_html", "restore_html", "method_html"):
        if extra.get(sec):
            parts.append(extra[sec])
    parts.append("</body></html>")
    (HERE / "report.html").write_text("\n".join(parts), encoding="utf-8")
    print("wrote report.html;", len(cmp_), "experiments ranked")


if __name__ == "__main__":
    build()
