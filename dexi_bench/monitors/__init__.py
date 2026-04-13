"""System monitor package.

Usage:
    from dexi_bench.monitors import make_monitor

    mon = make_monitor()
    mon.start()
    # ... run workload ...
    summary = mon.stop()
"""

from __future__ import annotations

from ..platform import detect
from .base import MonitorSummary, Sample, SystemMonitor
from .samplers import pick_sampler

__all__ = ["MonitorSummary", "Sample", "SystemMonitor", "make_monitor"]


def make_monitor(interval_s: float = 1.0) -> SystemMonitor:
    p = detect()
    return SystemMonitor(pick_sampler(p.kind, p.has_jetson), interval_s=interval_s)
