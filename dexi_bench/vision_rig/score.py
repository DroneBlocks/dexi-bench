"""Step through the vision test set on the iPad and score each DEXI detector against it.

    python -m dexi_bench.vision_rig.score --drone 192.168.68.63 [--settle 3 --dwell 8 --groups bars_square,yolo]

Needs the rig server running (server.py) and the iPad showing its page in front of
the drone camera. Starts drone_listener.py on the drone over ssh, then for each item:
show it, wait --settle seconds (camera, vote windows), record --dwell seconds.

Per item and detector:
  yolo  pass if the expected class is in >= 50% of YOLO messages and no other class
        (except the ignored ones) is in more than 20%. Expectation None: pass if no
        class is in more than 20% of messages.
  bars  the bar counter publishes only when its vote agrees, so: pass if >= 1 reading
        and >= 90% of readings are the expected label. None expected: pass if silent.
  tag   pass if the expected id is in >= 50% of AprilTag messages; None: no id in > 20%.
Writes results/vision_rig/<stamp>/{results.json, results.csv, summary.md}.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
REMOTE = "/tmp/vision_rig_listener.py"
ENV = ("source /home/dexi/ros2_jazzy/install/setup.bash && source /home/dexi/dexi_ws/install/setup.bash && "
       "export ROS_DOMAIN_ID=1 ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET && "
       "sudo -E env PATH=$PATH PYTHONPATH=$PYTHONPATH LD_LIBRARY_PATH=$LD_LIBRARY_PATH "
       "AMENT_PREFIX_PATH=$AMENT_PREFIX_PATH ROS_DOMAIN_ID=1 ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET "
       f"python3 -u {REMOTE}")


def api(base, path, method="POST"):
    req = urllib.request.Request(base + path, method=method)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


class Listener:
    def __init__(self, ssh):
        subprocess.run(ssh + [f"cat > {REMOTE}"], input=(HERE / "drone_listener.py").read_bytes(), check=True)
        self.p = subprocess.Popen(ssh + [ENV], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.msgs = []
        self.ready = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m["topic"] == "ready":
                self.ready.set()
                continue
            self.msgs.append((time.time(), m))

    def window(self, t0, t1):
        return [m for t, m in list(self.msgs) if t0 <= t < t1]

    def stop(self):
        self.p.terminate()


def judge(item, msgs, ignore_yolo):
    exp = item["expect"]
    res = {}
    y = [m for m in msgs if m["topic"] == "yolo"]
    share = collections.Counter()
    for m in y:
        for lab in set(m["labels"]) - ignore_yolo:
            share[lab] += 1
    shares = {k: round(v / len(y), 2) for k, v in share.items()} if y else {}
    if not y:
        # dexi_yolo publishes only when it detects something, so silence is a real answer
        ok = exp["yolo"] is None
    elif exp["yolo"]:
        others = [v for k, v in shares.items() if k != exp["yolo"]]
        ok = shares.get(exp["yolo"], 0) >= 0.5 and all(v <= 0.2 for v in others)
    else:
        ok = all(v <= 0.2 for v in shares.values())
    res["yolo"] = {"ok": ok, "msgs": len(y), "shares": shares}

    b = [lab for m in msgs if m["topic"] == "bars" for lab in m["labels"]]
    bc = collections.Counter(b)
    if exp["bars"]:
        ok = len(b) >= 1 and bc[exp["bars"]] / len(b) >= 0.9
    else:
        ok = len(b) == 0
    res["bars"] = {"ok": ok, "readings": len(b), "labels": dict(bc)}

    t = [m for m in msgs if m["topic"] == "tag"]
    tc = collections.Counter(i for m in t for i in set(m["labels"]))
    tshare = {k: round(v / len(t), 2) for k, v in tc.items()} if t else {}
    if not t:
        ok = None
    elif exp["tag"] is not None:
        ok = tshare.get(exp["tag"], 0) >= 0.5
    else:
        ok = all(v <= 0.2 for v in tshare.values())
    res["tag"] = {"ok": ok, "msgs": len(t), "ids": tshare}
    return res


def loop(a, items, lis):
    """Repeat passes until --hours elapses. Appends every item result to log.csv and
    rewrites summary.md (per-image pass rates across all passes) after every pass."""
    out = a.out / ("loop_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    log = out / "log.csv"
    with open(log, "w", newline="") as f:
        csv.writer(f).writerow(["utc", "pass", "id", "file", "group", "yolo_ok", "yolo_shares",
                                "bars_ok", "bar_labels", "tag_ok", "tag_ids"])
    stats = collections.defaultdict(lambda: collections.Counter())
    end = time.time() + a.hours * 3600
    n = 0
    valid_n = 0
    while time.time() < end:
        n += 1
        if lis.p.poll() is not None:
            raise SystemExit("drone listener exited (drone rebooted or ssh dropped)")
        results = []
        for it in items:
            api(a.server, f"/api/show/{it['id']}")
            time.sleep(a.settle)
            t0 = time.time()
            time.sleep(a.dwell)
            results.append((it, judge(it, lis.window(t0, time.time()), set(x for x in a.ignore_yolo.split(",") if x))))
        # Display check: if the AprilTags were not all seen, the iPad was asleep, dark or
        # moved; drop the pass instead of scoring a blank screen.
        tag_items = [r for it, r in results if it["group"] == "apriltag"]
        valid = bool(tag_items) and all(r["tag"]["ok"] for r in tag_items)
        with open(out / "passes.csv", "a", newline="") as f:
            csv.writer(f).writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), n, valid,
                                    sum(1 for r in tag_items if r["tag"]["ok"]), len(tag_items)])
        if not valid:
            print(f"pass {n} INVALID (display check: {sum(1 for r in tag_items if r['tag']['ok'])}/{len(tag_items)} tags seen)", flush=True)
            continue
        valid_n += 1
        for it, r in results:
            with open(log, "a", newline="") as f:
                csv.writer(f).writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), n, it["id"], it["file"],
                                        it["group"], r["yolo"]["ok"], json.dumps(r["yolo"]["shares"]),
                                        r["bars"]["ok"], json.dumps(r["bars"]["labels"]), r["tag"]["ok"],
                                        json.dumps(r["tag"]["ids"])])
            for k in ("yolo", "bars", "tag"):
                stats[(it["id"], it["file"])][(k, r[k]["ok"])] += 1
        lines = [f"# Vision rig loop {out.name}", "", f"{valid_n} valid passes of {n}, {len(items)} images per pass, "
                 f"updated {datetime.now(timezone.utc).isoformat(timespec='seconds')}Z", "",
                 "| # | image | YOLO pass | bars pass | tag pass |", "|---|---|---|---|---|"]
        for (i, fn), c in sorted(stats.items()):
            cell = []
            for k in ("yolo", "bars", "tag"):
                p_, f_ = c[(k, True)], c[(k, False)]
                cell.append("n/a" if p_ + f_ == 0 else (f"{p_}/{p_ + f_}" + (" **" if f_ else "")))
            lines.append(f"| {i} | {fn} | {cell[0]} | {cell[1]} | {cell[2]} |")
        (out / "summary.md").write_text(chr(10).join(lines) + chr(10))
        print(f"pass {n} done -> {out}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drone", default="192.168.68.63")
    ap.add_argument("--key", default=r"C:\Users\denni\.ssh\dexi_bench")
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--dwell", type=float, default=8.0)
    ap.add_argument("--groups", default="", help="comma list; empty = all")
    ap.add_argument("--ignore-yolo", default="", help="YOLO classes to leave out of scoring")
    ap.add_argument("--out", type=Path, default=Path("results/vision_rig"))
    ap.add_argument("--hours", type=float, default=0.0,
                    help="loop mode: repeat passes for this long, appending to <out>/loop_<stamp>/")
    a = ap.parse_args()

    manifest = api(a.server, "/api/manifest", "GET")
    groups = set(g for g in a.groups.split(",") if g)
    items = [it for it in manifest if not groups or it["group"] in groups]
    ssh = ["ssh", "-o", "BatchMode=yes", "-i", a.key, f"dexi@{a.drone}"]
    # No need to stop the loop: every show command holds the screen (server default
    # hold 30 s), and the server goes back to looping once this script stops.
    lis = Listener(ssh)
    if not lis.ready.wait(30):
        raise SystemExit("drone listener did not start (ssh / ROS environment?)")
    time.sleep(2)

    if a.hours > 0:
        loop(a, items, lis)
        lis.stop()
        return

    rows = []
    for it in items:
        api(a.server, f"/api/show/{it['id']}")
        time.sleep(a.settle)
        t0 = time.time()
        time.sleep(a.dwell)
        r = judge(it, lis.window(t0, time.time()), set(x for x in a.ignore_yolo.split(",") if x))
        rows.append({"item": it, "result": r})
        flag = lambda v: "-" if v is None else ("PASS" if v else "FAIL")
        print(f"{it['id']:>2} {it['file']:<34} yolo {flag(r['yolo']['ok']):<4} {r['yolo']['shares']}  "
              f"bars {flag(r['bars']['ok']):<4} {r['bars']['labels']}  tag {flag(r['tag']['ok']):<4} {r['tag']['ids']}",
              flush=True)
    lis.stop()

    out = a.out / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"args": vars(a) | {"out": str(a.out)}, "rows": rows}, indent=2))
    with open(out / "results.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "file", "group", "exp_yolo", "yolo_ok", "yolo_shares", "exp_bars", "bars_ok",
                    "bar_labels", "exp_tag", "tag_ok", "tag_ids"])
        for r in rows:
            it, res = r["item"], r["result"]
            w.writerow([it["id"], it["file"], it["group"], it["expect"]["yolo"], res["yolo"]["ok"],
                        json.dumps(res["yolo"]["shares"]), it["expect"]["bars"], res["bars"]["ok"],
                        json.dumps(res["bars"]["labels"]), it["expect"]["tag"], res["tag"]["ok"],
                        json.dumps(res["tag"]["ids"])])
    lines = ["| # | image | YOLO | bars | tag |", "|---|---|---|---|---|"]
    tally = collections.Counter()
    for r in rows:
        it, res = r["item"], r["result"]
        cell = []
        for k in ("yolo", "bars", "tag"):
            v = res[k]["ok"]
            tally[(k, v)] += 1
            cell.append("n/a" if v is None else ("pass" if v else "**FAIL**"))
        lines.append(f"| {it['id']} | {it['file']} | {cell[0]} | {cell[1]} | {cell[2]} |")
    summ = "  ".join(f"{k}: {tally[(k, True)]} pass / {tally[(k, False)]} fail"
                     for k in ("yolo", "bars", "tag"))
    (out / "summary.md").write_text(f"# Vision rig run {out.name}\n\n{summ}\n\n" + "\n".join(lines) + "\n")
    print(summ)
    print(f"results: {out}")


if __name__ == "__main__":
    main()
