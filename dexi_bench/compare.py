"""Compare dexi_bench results and render markdown tables.

Reads results/**/*.json, groups by (platform_kind, package, workload, engine),
emits a markdown comparison of the latest run per group, and flags
regressions against the most recent previous result in each group.

Usage:
    bench-compare
    bench-compare --results-dir PATH
    bench-compare --package dexi_yolo --workload bench_yolo_sparse
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, List, Optional

REGRESSION_RULES = {
    "fps_avg": ("higher_better", 0.10),
    "inference_ms_p95": ("lower_better", 0.15),
    "cpu_pct_avg": ("lower_better_abs", 10.0),
}


def _load(results_dir: Path) -> List[dict]:
    out: List[dict] = []
    for p in sorted(results_dir.rglob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception as e:
            print(f"skip {p}: {e}", file=sys.stderr)
    return out


def _group_key(r: dict) -> tuple:
    return (
        r["platform"]["kind"],
        r["target"]["package"],
        r["workload"]["name"],
        r["target"]["engine"],
    )


def _fmt(v, fmt: str = "{:.1f}") -> str:
    return fmt.format(v) if v is not None else "—"


def _render_table(
    results: List[dict],
    filter_pkg: Optional[str],
    filter_wl: Optional[str],
) -> str:
    filtered = [
        r for r in results
        if (filter_pkg is None or r["target"]["package"] == filter_pkg)
        and (filter_wl is None or r["workload"]["name"] == filter_wl)
    ]
    if not filtered:
        return "_no matching results_\n"

    latest: dict = {}
    for r in sorted(filtered, key=lambda r: r["timestamp"]):
        latest[_group_key(r)] = r

    lines = [
        "| Workload | Platform | Engine | FPS | p50 ms | p95 ms | CPU % | Temp °C | Power W | SHA | Date |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(
        latest.values(),
        key=lambda r: (r["workload"]["name"], r["platform"]["kind"], r["target"]["engine"]),
    ):
        m = r.get("metrics", {})
        lines.append(
            "| {wl} | {plat} | {eng} | {fps} | {p50} | {p95} | {cpu} | {temp} | {pwr} | `{sha}` | {date} |".format(
                wl=r["workload"]["name"],
                plat=r["platform"]["kind"],
                eng=r["target"]["engine"],
                fps=_fmt(m.get("fps_avg")),
                p50=_fmt(m.get("inference_ms_p50")),
                p95=_fmt(m.get("inference_ms_p95")),
                cpu=_fmt(m.get("cpu_pct_avg")),
                temp=_fmt(m.get("temp_c_max")),
                pwr=_fmt(m.get("power_w_avg"), "{:.2f}"),
                sha=r["target"]["git_sha"],
                date=r["timestamp"][:10],
            )
        )
    return "\n".join(lines) + "\n"


def _check_regressions(results: List[dict]) -> List[str]:
    by_group: dict = defaultdict(list)
    for r in results:
        by_group[_group_key(r)].append(r)

    flags: List[str] = []
    for key, group in by_group.items():
        group.sort(key=lambda r: r["timestamp"])
        if len(group) < 2:
            continue
        prev, curr = group[-2], group[-1]
        prev_m, curr_m = prev.get("metrics", {}), curr.get("metrics", {})
        label = "/".join(key)
        for metric, (direction, threshold) in REGRESSION_RULES.items():
            pv, cv = prev_m.get(metric), curr_m.get(metric)
            if pv is None or cv is None:
                continue
            if direction == "higher_better":
                if pv > 0 and (pv - cv) / pv > threshold:
                    flags.append(f"{label}: {metric} {pv:.1f} → {cv:.1f} ({(pv-cv)/pv*100:.1f}% drop)")
            elif direction == "lower_better":
                if pv > 0 and (cv - pv) / pv > threshold:
                    flags.append(f"{label}: {metric} {pv:.1f} → {cv:.1f} ({(cv-pv)/pv*100:.1f}% rise)")
            elif direction == "lower_better_abs":
                if (cv - pv) > threshold:
                    flags.append(f"{label}: {metric} {pv:.1f} → {cv:.1f} (+{cv-pv:.1f} abs)")
    return flags


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="bench-compare")
    p.add_argument("--results-dir", type=Path, default=Path("results"))
    p.add_argument("--package")
    p.add_argument("--workload")
    args = p.parse_args(list(argv) if argv is not None else None)

    if not args.results_dir.exists():
        print(f"results dir not found: {args.results_dir}", file=sys.stderr)
        return 2

    results = _load(args.results_dir)
    if not results:
        print("_no results found_")
        return 0

    print(_render_table(results, args.package, args.workload))

    flags = _check_regressions(results)
    if flags:
        print("\n## Regressions\n")
        for f in flags:
            print(f"- {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
