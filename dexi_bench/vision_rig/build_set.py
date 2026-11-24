"""Build the vision test set shown on the iPad: images plus a manifest of what each
DEXI detector should report for each image.

    python -m dexi_bench.vision_rig.build_set --assets C:/Users/denni/_dev/avr-yolo/assets

Groups:
  bars_rounded   AVR 2026 bridge discs from avr-yolo (1/2/3 lines + blackout)
  bars_square    square-end thin-bar discs (Team 1350A style), drawn here, 0-3 bars
  yolo           AVR 2026 barrels and barns from avr-yolo
  apriltag       tag36h11 IDs 0-6 from avr-yolo (6 in tag on a 7 in target)
  negative       plain white / grey / RVR colour squares: nothing should fire

Expectations use the detectors' own labels:
  yolo  -> dexi_yolo avr_2026 class names (wheat_barrel, water_barrel, gasoline,
           toxic_fluid, wheat_barn, water_barn, bridge_1line/2line/3line, blackout)
  bars  -> dexi_vision bar counter labels (bars_0..bars_3)
  tag   -> tag36h11 id
None means "this detector should report nothing".
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
OUT = HERE / "images"
CANVAS = 1200  # square, white; the page scales it to fit the iPad screen


def _on_canvas(src: Path, dst: Path, fill: float = 0.85) -> None:
    """Centre an artwork image on a white square canvas (so every item frames the same)."""
    im = Image.open(src).convert("RGB")
    side = int(CANVAS * fill)
    im.thumbnail((side, side), Image.LANCZOS)
    c = Image.new("RGB", (CANVAS, CANVAS), "white")
    c.paste(im, ((CANVAS - im.width) // 2, (CANVAS - im.height) // 2))
    c.save(dst)


def _square_disc(n_bars: int, dst: Path) -> None:
    """Black disc with n thin square-end white bars (width/length ~0.13, as the printed art)."""
    c = Image.new("RGB", (CANVAS, CANVAS), "white")
    d = ImageDraw.Draw(c)
    r = int(CANVAS * 0.42)
    cx = cy = CANVAS // 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill="black")
    length = int(2 * r * 0.46)
    width = int(length * 0.13)
    gap = int(width * 1.6)
    total = n_bars * width + (n_bars - 1) * gap if n_bars else 0
    x0 = cx - total // 2
    for i in range(n_bars):
        x = x0 + i * (width + gap)
        d.rectangle((x, cy - length // 2, x + width, cy + length // 2), fill="white")
    c.save(dst)


def build(assets: Path) -> list:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    items = []

    def add(name, group, yolo=None, bars=None, tag=None, note=""):
        # v0.25 behaviour: YOLO never publishes blackout (profile exclude list) and the
        # bar counter does not publish bars_0 (publish_bars_0 off), so both stay silent.
        if yolo == "blackout" or (yolo or "").startswith("bridge_"):
            yolo = None   # avr_2026 excludes blackout and bridges (the bar counter owns bridges)
        if bars == "bars_0":
            bars = None
        items.append({"id": len(items), "file": name, "group": group,
                      "expect": {"yolo": yolo, "bars": bars, "tag": tag}, "note": note})

    for f, yolo, bars in (("bridge_red_1line_3in.png", "bridge_1line", "bars_1"),
                          ("bridge_green_2line_3in.png", "bridge_2line", "bars_2"),
                          ("bridge_blue_3line_3in.png", "bridge_3line", "bars_3"),
                          ("bridge_blackout_3in.png", "blackout", "bars_0")):
        name = "bars_rounded_" + f.replace("_3in", "")
        _on_canvas(assets / "bridge_images" / f, OUT / name)
        add(name, "bars_rounded", yolo=yolo, bars=bars)

    for n in range(4):
        name = f"bars_square_{n}.png"
        _square_disc(n, OUT / name)
        add(name, "bars_square", yolo=(f"bridge_{n}line" if n else "blackout"), bars=f"bars_{n}",
            note="YOLO was trained on rounded art; its square-end result is informational")

    for f, yolo in (("barrel_water_2.625in.png", "water_barrel"), ("barrel_wheat_2.625in.png", "wheat_barrel"),
                    ("barrel_gasoline_2.625in.png", "gasoline"), ("barrel_toxic_2.625in.png", "toxic_fluid"),
                    ("barrel_blackout_2.625in.png", "blackout"), ("barn_water_4in.png", "water_barn"),
                    ("barn_wheat_4in.png", "wheat_barn"), ("barn_blackout_4in.png", "blackout")):
        name = "yolo_" + f.split("_2.625in")[0].split("_4in")[0] + ".png"
        _on_canvas(assets / ("barrel_images" if "barrel" in f else "barn_images") / f, OUT / name)
        add(name, "yolo", yolo=yolo)

    for i in range(7):
        f = f"tag36h11_id{i}_6in_on_7in_target.png"
        name = f"apriltag_id{i}.png"
        _on_canvas(assets / "apriltags" / f, OUT / name, fill=0.95)
        add(name, "apriltag", tag=i, note="tag fills ~81% of the 7 in target; set the detector size to the on-screen black square")

    for colour in ("white", "#808080"):
        name = f"negative_{colour.strip('#')}.png"
        Image.new("RGB", (CANVAS, CANVAS), colour).save(OUT / name)
        add(name, "negative")
    for f in sorted((assets / "rvr_squares").glob("*.png")):
        name = "negative_" + f.name.replace("_8in", "")
        _on_canvas(f, OUT / name)
        add(name, "negative", note="RVR floor square: not a DEXI detector class")

    (HERE / "manifest.json").write_text(json.dumps(items, indent=2))
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", type=Path, required=True, help="avr-yolo/assets")
    items = build(ap.parse_args().assets)
    groups = {}
    for it in items:
        groups[it["group"]] = groups.get(it["group"], 0) + 1
    print(f"{len(items)} images -> {OUT}: {groups}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
