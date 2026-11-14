#!/usr/bin/env python3
"""Run many simulations of one Verilated model in parallel, e.g. a seed regression, on the CPU,
the GPU, or both from one queue of seeds.

Usage: batch_run.py [--on cpu|gpu|both] [-j K] [--runs N] [--first-seed S] [--pass-regex RE]
                    [--link FILE ...] [--work DIR] [--keep]
                    [--gpu-host "HOST LIB [OPTIONS]"] [--gpu-batch N] [--no-rerun]
                    -- <CPU command with {seed}...>

CPU: each run gets its own directory under --work (the testbench may write logs into its
current directory), with symlinks to the --link files. {seed} in the command is replaced
by the run's seed, e.g. "+verilator+seed+{seed}". A run passes if it exits with 0 and,
with --pass-regex, its output matches. Directories of passing runs are removed unless
--keep. Build the model with --threads 1: on throughput, single-threaded runs on all
cores beat multithreaded runs (see apple/README.md).

GPU: --gpu-host is the host of a model translated by apple/gpu/vl2metal (host and
sim.metallib), which runs one instance per seed and decides pass or fail from the model's
status member. It takes seeds in batches of up to --gpu-batch. The GPU has no $display, so
failing GPU seeds are rerun on the CPU for their log (unless --no-rerun); a seed that fails
on the GPU but passes on the CPU is reported as a MISMATCH.

--on gpu keeps the CPU free: only the GPU host runs, plus CPU reruns of failing seeds.
--on both: CPU processes take single seeds while the GPU takes whole batches. A GPU batch
takes about as long as one run times the GPU's per-instance slowdown (minutes for a CPU
core design), so the GPU takes a batch only when the CPU would not finish the remaining
seeds sooner: a full batch until the first one finishes, then one sized from its time.
"""
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path


class Regression:

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.pass_re = re.compile(args.pass_regex) if args.pass_regex else None
        self.queue = deque(range(args.first_seed, args.first_seed + args.runs))
        self.reruns: deque[int] = deque()  # failing GPU seeds, rerun on the CPU for a log
        self.cond = threading.Condition()
        self.gpu_busy = args.on != "cpu"  # the GPU may still take or return seeds
        self.failed: list[int] = []
        self.mismatch: list[int] = []
        self.done = {"cpu": 0, "gpu": 0}
        self.start = time.perf_counter()
        self.gpu_batch_time = 0.0  # wall time of the last GPU batch
        self.gpu_error = ""

    def prepare_dir(self, name: str) -> Path:
        run_dir = self.args.work / name
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True)
        for f in self.args.link:
            (run_dir / f.name).symlink_to(f)
        return run_dir

    def run_cpu(self, seed: int) -> bool:
        run_dir = self.prepare_dir(f"seed{seed}")
        cmd = [part.replace("{seed}", str(seed)) for part in self.args.command]
        with open(run_dir / "run.log", "w", encoding="utf-8") as log:
            rc = subprocess.run(cmd,
                                cwd=run_dir,
                                stdout=log,
                                stderr=subprocess.STDOUT,
                                check=False).returncode
        passed = rc == 0
        if passed and self.pass_re:
            passed = bool(self.pass_re.search((run_dir / "run.log").read_text(errors="replace")))
        if passed and not self.args.keep:
            shutil.rmtree(run_dir)
        return passed

    def progress(self) -> None:
        print(
            f"\r{self.done['cpu'] + self.done['gpu']}/{self.args.runs} done "
            f"(cpu {self.done['cpu']}, gpu {self.done['gpu']}), {len(self.failed)} failed",
            end="",
            flush=True)

    def cpu_worker(self) -> None:
        while True:
            with self.cond:
                while True:
                    if self.reruns:
                        seed, rerun = self.reruns.popleft(), True
                        break
                    if self.queue and self.args.on != "gpu":
                        seed, rerun = self.queue.popleft(), False
                        break
                    if not self.gpu_busy:
                        return
                    self.cond.wait()
            passed = self.run_cpu(seed)
            with self.cond:
                if rerun:
                    self.failed.append(seed)  # by the GPU result
                    if passed:  # so the GPU model is wrong
                        self.mismatch.append(seed)
                else:
                    self.done["cpu"] += 1
                    if not passed:
                        self.failed.append(seed)
                self.progress()

    def gpu_take(self) -> list[int]:
        """Seeds for the next GPU batch, or [] when the GPU is not worth using any more."""
        remaining = len(self.queue)
        size = min(self.args.gpu_batch, remaining)
        if self.args.on != "both":
            return [self.queue.popleft() for _ in range(size)]
        if not self.gpu_batch_time:
            # Unknown batch time: only a full batch is surely worth it
            size = size if size == self.args.gpu_batch else 0
        elif self.done["cpu"]:
            cpu_rate = self.done["cpu"] / (time.perf_counter() - self.start)
            if remaining / cpu_rate <= self.gpu_batch_time:
                size = 0  # the CPU finishes the rest before a GPU batch would
            else:
                size = min(size, int(remaining - cpu_rate * self.gpu_batch_time))
        return [self.queue.popleft() for _ in range(size)]

    def gpu_worker(self) -> None:
        batch = 0
        while True:
            with self.cond:
                seeds = self.gpu_take()
                if not seeds:
                    self.gpu_busy = False
                    self.cond.notify_all()
                    return
            batch += 1
            run_dir = self.prepare_dir(f"gpu{batch}")
            (run_dir / "seeds.txt").write_text("".join(f"{s}\n" for s in seeds))
            cmd = self.args.gpu_host + [
                str(len(seeds)), "--seeds", "seeds.txt", "--results", "results.txt", "--verify",
                str(self.args.gpu_verify)
            ]
            start = time.perf_counter()
            with open(run_dir / "host.log", "w", encoding="utf-8") as log:
                rc = subprocess.run(cmd,
                                    cwd=run_dir,
                                    stdout=log,
                                    stderr=subprocess.STDOUT,
                                    check=False).returncode
            results = {}
            if (run_dir / "results.txt").exists():
                for line in (run_dir / "results.txt").read_text().splitlines():
                    seed, status, _ = line.split()
                    results[int(seed)] = status
            if rc != 0 or len(results) != len(seeds):
                with self.cond:
                    self.gpu_error = f"GPU batch {batch} failed (exit {rc}), see {run_dir}/host.log"
                    if self.args.on == "both":
                        self.queue.extendleft(reversed(seeds))  # the CPU runs them
                    else:
                        self.queue.clear()
                    self.gpu_busy = False
                    self.cond.notify_all()
                return
            with self.cond:
                self.gpu_batch_time = time.perf_counter() - start
                self.done["gpu"] += len(seeds)
                for seed in seeds:
                    if results[seed] != "PASS":
                        if self.args.rerun:
                            self.reruns.append(seed)
                        else:
                            self.failed.append(seed)
                self.progress()
                self.cond.notify_all()
            if not self.args.keep:
                shutil.rmtree(run_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--on",
                        choices=["cpu", "gpu", "both"],
                        help="where to run (default: both with --gpu-host, else cpu)")
    parser.add_argument("-j",
                        "--jobs",
                        type=int,
                        default=os.cpu_count(),
                        help="concurrent CPU runs (default: all cores)")
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--first-seed", type=int, default=1)
    parser.add_argument("--pass-regex")
    parser.add_argument("--link",
                        type=Path,
                        nargs="*",
                        default=[],
                        help="files to symlink into each run directory")
    parser.add_argument("--work", type=Path, default=Path("batch"))
    parser.add_argument("--keep", action="store_true", help="keep passing run directories")
    parser.add_argument("--gpu-host",
                        type=shlex.split,
                        help='vl2metal host and its library, e.g. "metal/host metal/sim.metallib"')
    parser.add_argument("--gpu-batch",
                        type=int,
                        default=4096,
                        help="seeds per GPU batch (default 4096, the optimum on M4)")
    parser.add_argument("--gpu-verify",
                        type=int,
                        default=1,
                        help="instances per GPU batch checked against the CPU model")
    parser.add_argument("--no-rerun",
                        dest="rerun",
                        action="store_false",
                        help="do not rerun failing GPU seeds on the CPU")
    parser.add_argument("command", nargs="+")
    args = parser.parse_args()
    args.on = args.on or ("both" if args.gpu_host else "cpu")
    if args.on != "cpu" and not args.gpu_host:
        parser.error(f"--on {args.on} needs --gpu-host")
    args.work = args.work.resolve()
    args.link = [f.resolve() for f in args.link]
    # Resolve the executables now, as runs execute in their own directories
    for cmd in (args.command, args.gpu_host or []):
        for i in range(min(2, len(cmd))):
            if os.sep in cmd[i] and Path(cmd[i]).exists():
                cmd[i] = str(Path(cmd[i]).resolve())

    reg = Regression(args)
    threads = [threading.Thread(target=reg.cpu_worker) for _ in range(args.jobs)]
    if args.on != "cpu":
        threads.append(threading.Thread(target=reg.gpu_worker))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - reg.start
    if reg.gpu_error:
        print(f"\n{reg.gpu_error}", file=sys.stderr)
        if args.on == "gpu":
            sys.exit(2)
    print(f"\n{args.runs} runs in {wall:.1f} s: {args.runs / wall:.2f} runs/s "
          f"(cpu {reg.done['cpu']} on {args.jobs} jobs, gpu {reg.done['gpu']})")
    if reg.mismatch:
        print(f"MISMATCH: failed on the GPU but passed on the CPU ({len(reg.mismatch)}): "
              f"{' '.join(map(str, sorted(reg.mismatch)))}")
    if reg.failed:
        print(f"FAILED seeds ({len(reg.failed)}): {' '.join(map(str, sorted(reg.failed)))}")
        if args.rerun or args.on == "cpu":
            print(f"Logs: {args.work}/seed<N>/run.log")
        sys.exit(1)


if __name__ == "__main__":
    main()
