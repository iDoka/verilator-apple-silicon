#!/usr/bin/env python3
"""Time built simulator variants: median wall time of N runs, checking TEST_PASSED.

Usage: run.py <core> <variant> [<variant> ...] [--runs N] [--prefix "taskpolicy -c utility"]
Variants are interleaved run by run, so slow drifts in machine state hit all of them alike.
"""
import argparse
import shlex
import statistics
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_once(workdir: Path, prefix: list[str]) -> float:
    start = time.perf_counter()
    proc = subprocess.run(prefix + ["./obj_dir/Vtb_top"], cwd=workdir, capture_output=True,
                          text=True, check=False)
    elapsed = time.perf_counter() - start
    if "TEST_PASSED" not in proc.stdout:
        raise RuntimeError(f"{workdir}: FAILED\n{proc.stdout[-500:]}{proc.stderr[-500:]}")
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("core")
    parser.add_argument("variants", nargs="+")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--prefix", default="")
    args = parser.parse_args()
    prefix = shlex.split(args.prefix)
    times: dict[str, list[float]] = {v: [] for v in args.variants}
    for _ in range(args.runs):
        for variant in args.variants:
            times[variant].append(run_once(ROOT / "apple/bench/work" / args.core / variant, prefix))
    base = statistics.median(times[args.variants[0]])
    for variant, ts in times.items():
        med = statistics.median(ts)
        print(f"{args.core}/{variant:24s} median={med:7.3f}s  vs_first={base / med:5.2f}x  "
              f"min={min(ts):.3f} max={max(ts):.3f}")


if __name__ == "__main__":
    main()
