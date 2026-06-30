# dexi-bench Design

## Goal

Give the DEXI project and community a single source of truth for **how each package performs on each supported hardware platform**, tracked over time so regressions are visible at release time.

## Non-goals

- Not a replacement for integration tests — dexi-bench measures performance, not correctness beyond basic sanity.
- Not a CI-blocking gate (at least initially) — results are informational, flagged in PRs for review.

## Architecture

```
               +-------------------------+
               |   bench_<target> CLI    |
               +-----------+-------------+
                           |
              +------------+------------+
              |                         |
     +--------v--------+        +-------v--------+
     |  target node    |        | system monitor |
     |  (dexi_yolo,    |        |  (cpu/mem/     |
     |   dexi_llm, ...)|        |   temp/power)  |
     +--------+--------+        +-------+--------+
              |                         |
              | ROS2 topics             | samples
              | + stdout                |
              |                         |
     +--------v-------------------------v---+
     |         runner (orchestrator)         |
     |  - play bag / feed prompts            |
     |  - warmup window                      |
     |  - timed measurement window           |
     |  - aggregate metrics                  |
     |  - write JSON result                  |
     +----------------+----------------------+
                      |
                      v
              results/<platform>/
                <date>_<sha>_<workload>.json
```

## Black-box observation

dexi-bench does **not** import from or modify target packages. Metrics are gathered two ways:

1. **Topic subscription.** Runner subscribes to the target's output topic (e.g. `/yolo_detections`) and times inter-message intervals externally → FPS, p50/p95.
2. **Stdout parsing.** Target nodes already log aggregated stats on shutdown (see `dexi_yolo_node_hailo.py:254`, `dexi_yolo_node_onnx.py:428`). The runner captures stdout and regexes final numbers.

A future optional enhancement: add a `/dexi_<pkg>/stats` topic to each target package for richer structured metrics. Strictly additive, only if log parsing proves fragile.

## Result schema (v1)

```json
{
  "schema_version": 1,
  "timestamp": "2026-04-12T14:30:00Z",
  "platform": {
    "kind": "pi5",
    "arch": "aarch64",
    "os_release": "Debian GNU/Linux 12 (bookworm)",
    "accelerator": "hailo_8l"
  },
  "target": {
    "package": "dexi_yolo",
    "engine": "hailo",
    "version": "0.2.1",
    "git_sha": "396ccdb"
  },
  "workload": {
    "name": "bench_yolo_sparse",
    "bag_version": "v1",
    "duration_s": 60,
    "warmup_s": 5
  },
  "metrics": {
    "fps_avg": 108.4,
    "inference_ms_p50": 8.9,
    "inference_ms_p95": 11.2,
    "detections_total": 1820,
    "cpu_pct_avg": 7.3,
    "cpu_pct_max": 14.1,
    "mem_mb_max": 412,
    "temp_c_max": 58.5,
    "power_w_avg": 5.2
  },
  "notes": ""
}
```

## Runner lifecycle

1. Detect platform (`platform.py`).
2. Resolve workload config (YAML in `config/`).
3. Start system monitor in background thread.
4. Launch target node via subprocess with stdout capture.
5. Play bag / feed prompts after configurable warmup.
6. Run for `duration_s`, subscribing to output topic.
7. Stop bag, SIGINT target node, wait for clean shutdown.
8. Parse target's final stats from stdout.
9. Stop monitor, aggregate samples.
10. Merge into result schema, write JSON to `results/<platform>/`.

## Workload configs

One YAML per canonical workload:

```yaml
# config/yolo_sparse.yaml
name: bench_yolo_sparse
target: dexi_yolo
bag: bench_yolo_sparse_v1.bag
bag_topics:
  - /camera/image_raw
  - /camera/camera_info
output_topic: /yolo_detections
duration_s: 60
warmup_s: 5
engines: [onnx, hailo, tensorrt]  # subset picked per platform
```

## Historical storage

- `results/<platform_kind>/<yyyy-mm-dd>_<git_sha>_<workload>.json`
- Committed to the repo (JSON files, small, diffable)
- `compare.py` reads all files and renders `docs/BENCHMARKS.md`

## Regression rule (v1, tunable)

A run is flagged regressed if, on the same `platform.kind + target.package + workload.name`, any of:
- `fps_avg` drops > 10% from previous
- `inference_ms_p95` rises > 15%
- `cpu_pct_avg` rises > 10 percentage points

Flags are informational, not blocking, until we trust the rules.

## Open questions

- Power measurement: Jetson has `tegrastats`, Pi has `vcgencmd measure_volts` but no amperage without extra hardware. Do we accept "power not available" for Pi, or add a USB power meter recommendation?
- Where do the canonical bags live? Separate `dexi-bench-data` repo/release vs embedded in dexi-bench? (Leaning: separate release artifact, `fetch_bags.sh` downloads.)
- Should `compare.py` post a comment on PRs that modify a target package? (Post-MVP.)

## Profilers (transient measurements)

Runners answer "what's the steady-state number for this package on this
platform?" and collapse a run to one `BenchResult`. A second class of
question — "what does this transient *look like* over time?" — has no package
under test and needs the raw curve, not an aggregate. Those live in
`dexi_bench/profilers/` and write a different result shape:

```
results/<platform_kind>/profiles/<date>_<sha>_<profile>_<scenario>.csv   # raw time-series
results/<platform_kind>/profiles/<date>_<sha>_<profile>_<scenario>.json  # small summary
```

Profilers deliberately **reuse** the steady-state machinery — `platform.detect()`
and `monitors/samplers.pick_sampler()` are shared, so a profiler gets the right
temp/power shim for free. The only addition to the core was exposing the raw
samples on `SystemMonitor` (the runners never needed them).

First profiler: **thermal** (`bench-thermal`) — SoC cooldown under a cooling
scenario (fan vs takeoff prop-wash), overlaid by `bench-thermal-compare`. This
is what answers "does the drone cool itself better in the air than on the
bench, and does it clear the throttle ceiling?" — previously an anecdote.
