"""Platform detection for dexi_bench.

Identifies which hardware the harness is running on so monitors and runners
can pick the right shims (tegrastats on Jetson, vcgencmd on Pi, etc.).
"""

from __future__ import annotations

import os
import platform as _platform
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Platform:
    kind: str              # pi5, cm4, cm5, orin_nano, orin_nx, unknown
    arch: str              # aarch64, x86_64
    os_release: str        # e.g. "Ubuntu 22.04"
    has_hailo: bool
    has_jetson: bool


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def detect() -> Platform:
    arch = _platform.machine()
    model = _read("/proc/device-tree/model").replace("\x00", "")
    os_release = _read("/etc/os-release")

    kind = "unknown"
    if "Raspberry Pi 5" in model:
        kind = "pi5"
    elif "Compute Module 5" in model:
        kind = "cm5"
    elif "Compute Module 4" in model:
        kind = "cm4"
    elif "Orin Nano" in model:
        kind = "orin_nano"
    elif "Orin NX" in model:
        kind = "orin_nx"

    has_jetson = "Orin" in model or Path("/etc/nv_tegra_release").exists()
    has_hailo = Path("/dev/hailo0").exists() or os.path.exists("/usr/bin/hailortcli")

    pretty = ""
    for line in os_release.splitlines():
        if line.startswith("PRETTY_NAME="):
            pretty = line.split("=", 1)[1].strip('"')
            break

    return Platform(
        kind=kind,
        arch=arch,
        os_release=pretty,
        has_hailo=has_hailo,
        has_jetson=has_jetson,
    )
