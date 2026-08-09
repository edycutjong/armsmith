"""Armsmith — profile → diagnose → patch → prove → PR, for AI repos on Arm.

Phase-1 core (hardware-free): benchstats engine, 13-rule pack with replay
probes, reproduce gate, signed reports, evidence renderer, replay CLI.
Live instruments (Graviton, perf, Performix, llama-bench, cosign-in-CI) are
S1+ items and are marked TODO(S1) — this codebase never fabricates hardware
results.
"""

__version__ = "1.2.0"
