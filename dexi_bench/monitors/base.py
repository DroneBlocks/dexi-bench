"""System monitor: background sampler that collects CPU/mem/temp/power.

The monitor runs a thread that calls a platform-specific `sample()` function
at `interval_s` and aggregates into summary stats on stop().

Sample() returns a Sample dataclass with whatever fields the platform can
fill. Unavailable fields are left None; aggregation skips them.
"""

from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class Sample:
    t: float
    cpu_pct: Optional[float] = None
    mem_mb: Optional[float] = None
    temp_c: Optional[float] = None
    power_w: Optional[float] = None


@dataclass
class MonitorSummary:
    samples: int
    duration_s: float
    cpu_pct_avg: Optional[float] = None
    cpu_pct_max: Optional[float] = None
    mem_mb_max: Optional[float] = None
    temp_c_max: Optional[float] = None
    power_w_avg: Optional[float] = None


SampleFn = Callable[[], Sample]


class SystemMonitor:
    def __init__(self, sample_fn: SampleFn, interval_s: float = 1.0) -> None:
        self._sample_fn = sample_fn
        self._interval = interval_s
        self._samples: List[Sample] = []
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._start_t: float = 0.0

    def start(self) -> None:
        self._samples.clear()
        self._stop.clear()
        self._start_t = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> MonitorSummary:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval * 3)
        return self._summarize()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._samples.append(self._sample_fn())
            except Exception:
                pass
            self._stop.wait(self._interval)

    def _summarize(self) -> MonitorSummary:
        def col(field: str) -> list:
            return [getattr(s, field) for s in self._samples if getattr(s, field) is not None]

        cpu = col("cpu_pct")
        mem = col("mem_mb")
        temp = col("temp_c")
        power = col("power_w")
        return MonitorSummary(
            samples=len(self._samples),
            duration_s=time.monotonic() - self._start_t,
            cpu_pct_avg=statistics.mean(cpu) if cpu else None,
            cpu_pct_max=max(cpu) if cpu else None,
            mem_mb_max=max(mem) if mem else None,
            temp_c_max=max(temp) if temp else None,
            power_w_avg=statistics.mean(power) if power else None,
        )
