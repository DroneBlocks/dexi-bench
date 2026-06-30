"""Platform-specific sample functions.

Each returns a Sample. psutil_sample is the portable baseline; the
platform-specific variants layer on temp and (where available) power.

Jetson: tegrastats provides temp + power. Started lazily on first call
and the latest line is parsed.
Pi: vcgencmd measure_temp for SoC temperature. No power without extra HW.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from typing import Optional

import psutil

from .base import Sample


def psutil_sample() -> Sample:
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory().used / (1024 * 1024)
    temp_c: Optional[float] = None
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for readings in temps.values():
                if readings:
                    temp_c = max(r.current for r in readings)
                    break
    except (AttributeError, OSError):
        pass
    return Sample(t=time.monotonic(), cpu_pct=cpu, mem_mb=mem, temp_c=temp_c)


def vcgencmd_temp() -> Optional[float]:
    if not shutil.which("vcgencmd"):
        return None
    try:
        out = subprocess.check_output(
            ["vcgencmd", "measure_temp"], stderr=subprocess.DEVNULL, timeout=1
        ).decode()
        m = re.search(r"temp=([\d.]+)", out)
        return float(m.group(1)) if m else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def pi_sample() -> Sample:
    s = psutil_sample()
    if s.temp_c is None:
        s.temp_c = vcgencmd_temp()
    return s


def vcgencmd_clock_mhz() -> Optional[float]:
    """ARM core clock in MHz. Drops to ~1500 when the SoC soft-throttles."""
    if not shutil.which("vcgencmd"):
        return None
    try:
        out = subprocess.check_output(
            ["vcgencmd", "measure_clock", "arm"], stderr=subprocess.DEVNULL, timeout=1
        ).decode()
        m = re.search(r"=(\d+)", out)
        return round(int(m.group(1)) / 1_000_000) if m else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def vcgencmd_throttled_now() -> Optional[int]:
    """1 if currently throttled (get_throttled bit 2), else 0; None if no vcgencmd."""
    if not shutil.which("vcgencmd"):
        return None
    try:
        out = subprocess.check_output(
            ["vcgencmd", "get_throttled"], stderr=subprocess.DEVNULL, timeout=1
        ).decode()
        m = re.search(r"=0x([0-9a-fA-F]+)", out)
        return (int(m.group(1), 16) & 0x4) and 1 or 0 if m else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def pi_sample_full() -> Sample:
    """pi_sample plus ARM clock + live-throttle flag (for thermal profiling)."""
    s = pi_sample()
    s.clock_mhz = vcgencmd_clock_mhz()
    s.throttled_now = vcgencmd_throttled_now()
    return s


class _TegrastatsReader:
    """Reads tegrastats output in a background thread, exposes latest line."""

    _TEMP_RE = re.compile(r"(?:CPU|GPU|AO|thermal)@([\d.]+)C")
    _POWER_RE = re.compile(r"VDD_IN\s+(\d+)mW|POM_5V_IN\s+(\d+)/")

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._latest: Optional[str] = None
        self._lock = threading.Lock()
        self._started = False

    def _ensure_started(self) -> None:
        if self._started:
            return
        if not shutil.which("tegrastats"):
            self._started = True
            return
        try:
            self._proc = subprocess.Popen(
                ["tegrastats", "--interval", "1000"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            threading.Thread(target=self._pump, daemon=True).start()
        except FileNotFoundError:
            self._proc = None
        self._started = True

    def _pump(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            with self._lock:
                self._latest = line.strip()

    def read(self) -> tuple[Optional[float], Optional[float]]:
        self._ensure_started()
        with self._lock:
            line = self._latest
        if not line:
            return None, None
        temps = [float(m) for m in re.findall(r"@([\d.]+)C", line)]
        temp_c = max(temps) if temps else None
        power_w: Optional[float] = None
        m = self._POWER_RE.search(line)
        if m:
            mw = m.group(1) or m.group(2)
            if mw:
                power_w = int(mw) / 1000.0
        return temp_c, power_w

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()


_tegrastats = _TegrastatsReader()


def jetson_sample() -> Sample:
    s = psutil_sample()
    temp_c, power_w = _tegrastats.read()
    if temp_c is not None:
        s.temp_c = temp_c
    s.power_w = power_w
    return s


def pick_sampler(platform_kind: str, has_jetson: bool):
    if has_jetson:
        return jetson_sample
    if platform_kind in ("pi5", "cm4", "cm5"):
        return pi_sample
    return psutil_sample


class FcTempReader:
    """Background MAVLink reader for flight-controller board temperature.

    PX4 doesn't expose the STM32 die temp, but it streams the on-board IMU
    temperature in HIGHRES_IMU (and baro temp in SCALED_PRESSURE) — the
    standard proxy for 'FC temp'. We keep the latest value; the sampler reads
    it without blocking. Best-effort: if pymavlink is missing or the endpoint
    is unreachable, latest stays None and callers just see a blank column.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._latest: Optional[float] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            from pymavlink import mavutil
        except ImportError:
            return
        try:
            conn = mavutil.mavlink_connection(self._url)
        except Exception:
            return
        while not self._stop.is_set():
            msg = conn.recv_match(
                type=["HIGHRES_IMU", "SCALED_PRESSURE"], blocking=True, timeout=2
            )
            if msg is None:
                continue
            # HIGHRES_IMU.temperature is degC (float); SCALED_PRESSURE is cdegC.
            t = msg.temperature
            if msg.get_type() == "SCALED_PRESSURE":
                t = t / 100.0
            if t:
                with self._lock:
                    self._latest = round(float(t), 1)

    def read(self) -> Optional[float]:
        with self._lock:
            return self._latest

    def stop(self) -> None:
        self._stop.set()


def with_fc_temp(base_fn, reader: FcTempReader):
    """Wrap a sample fn so each Sample also carries the latest FC board temp."""
    def _fn() -> Sample:
        s = base_fn()
        s.fc_temp_c = reader.read()
        return s
    return _fn
