#!/usr/bin/env python3
"""Batch throughput: run K copies of a variant concurrently, report simulations per second.

Usage: throughput.py <core> <variant> <K> [<K> ...] [--jobs M]
A queue of M simulations is drained by K concurrent workers, like a regression farm.
Each worker runs in its own directory, because the testbench writes its logs to the cwd.
"""
import argparse
import queue
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("core")
    parser.add_argument("variant")
    parser.add_argument("ks", nargs="+", type=int)
    parser.add_argument("--jobs", type=int, default=40)
    args = parser.parse_args()
    src = ROOT / "apple/bench/work" / args.core / args.variant
    copies = ROOT / "apple/bench/work" / args.core / f"{args.variant}.copies"
    shutil.rmtree(copies, ignore_errors=True)
    dirs = []
    for i in range(max(args.ks)):
        d = copies / str(i)
        d.mkdir(parents=True)
        shutil.copy(src / "program.hex", d)
        dirs.append(d)
    exe = src / "obj_dir/Vtb_top"
    for k in args.ks:

        free_dirs: queue.Queue[Path] = queue.Queue()
        for d in dirs[:k]:
            free_dirs.put(d)

        def job(_: int, k: int = k, free_dirs: queue.Queue[Path] = free_dirs) -> None:
            d = free_dirs.get()
            out = subprocess.run([exe], cwd=d, capture_output=True, text=True,
                                 check=False).stdout
            free_dirs.put(d)
            if "TEST_PASSED" not in out:
                raise RuntimeError(f"k={k}: a simulation failed")

        start = time.perf_counter()
        with ThreadPoolExecutor(k) as pool:
            list(pool.map(job, range(args.jobs)))
        wall = time.perf_counter() - start
        print(f"{args.core}/{args.variant} k={k:2d} jobs={args.jobs} wall={wall:6.2f}s  "
              f"sims/s={args.jobs / wall:5.2f}")


if __name__ == "__main__":
    main()
