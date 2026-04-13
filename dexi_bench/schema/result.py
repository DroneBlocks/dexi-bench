"""Result schema for dexi_bench runs.

One BenchResult is produced per run and serialized to
`results/<platform_kind>/<yyyy-mm-dd>_<git_sha>_<workload>.json`.

Schema versioned via SCHEMA_VERSION so compare.py can handle future changes.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1


@dataclass
class PlatformInfo:
    kind: str
    arch: str
    os_release: str
    accelerator: Optional[str] = None


@dataclass
class TargetInfo:
    package: str
    engine: str
    version: str
    git_sha: str


@dataclass
class WorkloadInfo:
    name: str
    bag_version: str
    duration_s: float
    warmup_s: float


@dataclass
class Metrics:
    fps_avg: Optional[float] = None
    inference_ms_p50: Optional[float] = None
    inference_ms_p95: Optional[float] = None
    detections_total: Optional[int] = None
    cpu_pct_avg: Optional[float] = None
    cpu_pct_max: Optional[float] = None
    mem_mb_max: Optional[float] = None
    temp_c_max: Optional[float] = None
    power_w_avg: Optional[float] = None
    tokens_per_s: Optional[float] = None
    time_to_first_token_ms: Optional[float] = None


@dataclass
class BenchResult:
    platform: PlatformInfo
    target: TargetInfo
    workload: WorkloadInfo
    metrics: Metrics
    notes: str = ""
    schema_version: int = SCHEMA_VERSION
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "platform": asdict(self.platform),
            "target": asdict(self.target),
            "workload": asdict(self.workload),
            "metrics": {k: v for k, v in asdict(self.metrics).items() if v is not None},
            "notes": self.notes,
        }


def git_sha(short: bool = True) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short" if short else "HEAD", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def write_result(result: BenchResult, results_dir: Path) -> Path:
    """Write a BenchResult to results/<platform_kind>/<date>_<sha>_<workload>.json."""
    date = result.timestamp[:10]
    out_dir = results_dir / result.platform.kind
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{date}_{result.target.git_sha}_{result.workload.name}.json"
    out_path = out_dir / fname
    out_path.write_text(json.dumps(result.to_dict(), indent=2) + "\n")
    return out_path
