"""Transient profilers for dexi-bench.

Where runners/ measure *steady-state* package performance and aggregate to a
single BenchResult, profilers/ capture a *time-series* — the shape of a
transient. The first is `thermal`: how fast the SoC sheds heat under a given
cooling scenario (desk fan vs prop-wash from takeoff).
"""
