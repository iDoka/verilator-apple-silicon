#!/usr/bin/env python3
"""Run many simulations of one Verilated model in parallel, e.g. a seed regression.

Usage: batch_run.py [-j K] [--runs N] [--first-seed S] [--pass-regex RE]
                    [--link FILE ...] [--work DIR] [--keep] -- <command with {seed}...>

Each run gets its own directory under --work (the testbench may write logs into its
current directory), with symlinks to the --link files. {seed} in the command is replaced
by the run's seed, e.g. "+verilator+seed+{seed}". A run passes if it exits with 0 and,
with --pass-regex, its output matches. Directories of passing runs are removed unless
--keep. Build the model with --threads 1: on throughput, single-threaded runs on all
cores beat multithreaded runs (see apple/README.md).
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def run_one(seed: int, args: argparse.Namespace, pass_re: re.Pattern[str] | None) -> bool:
    run_dir = args.work / f"seed{seed}"
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    for f in args.link:
        (run_dir / f.name).symlink_to(f)
    cmd = [part.replace("{seed}", str(seed)) for part in args.command]
    with open(run_dir / "run.log", "w", encoding="utf-8") as log:
        rc = subprocess.run(cmd, cwd=run_dir, stdout=log, stderr=subprocess.STDOUT,
                            check=False).returncode
    passed = rc == 0
    if passed and pass_re:
        passed = bool(pass_re.search((run_dir / "run.log").read_text(errors="replace")))
    if passed and not args.keep:
        shutil.rmtree(run_dir)
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count(),
                        help="concurrent runs (default: all cores)")
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--first-seed", type=int, default=1)
    parser.add_argument("--pass-regex")
    parser.add_argument("--link", type=Path, nargs="*", default=[],
                        help="files to symlink into each run directory")
    parser.add_argument("--work", type=Path, default=Path("batch"))
    parser.add_argument("--keep", action="store_true", help="keep passing run directories")
    parser.add_argument("command", nargs="+")
    args = parser.parse_args()
    args.work = args.work.resolve()
    args.link = [f.resolve() for f in args.link]
    # Resolve the executable now, as runs execute in their own directories
    if os.sep in args.command[0]:
        args.command[0] = str(Path(args.command[0]).resolve())
    pass_re = re.compile(args.pass_regex) if args.pass_regex else None

    seeds = range(args.first_seed, args.first_seed + args.runs)
    failed = []
    start = time.perf_counter()
    with ThreadPoolExecutor(args.jobs) as pool:
        futures = {pool.submit(run_one, seed, args, pass_re): seed for seed in seeds}
        for done, future in enumerate(as_completed(futures), 1):
            if not future.result():
                failed.append(futures[future])
            print(f"\r{done}/{args.runs} done, {len(failed)} failed", end="", flush=True)
    wall = time.perf_counter() - start
    print(f"\n{args.runs} runs on {args.jobs} jobs in {wall:.1f} s: "
          f"{args.runs / wall:.2f} runs/s")
    if failed:
        print(f"FAILED seeds ({len(failed)}): {' '.join(map(str, sorted(failed)))}")
        print(f"Logs: {args.work}/seed<N>/run.log")
        sys.exit(1)


if __name__ == "__main__":
    main()
