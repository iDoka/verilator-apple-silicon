#!/usr/bin/env python3
"""Measure the energy of a command on Apple silicon with macmon (no root needed).

Usage: energy.py [--idle SEC] [--interval MS] [--log FILE] -- <command...>

Samples macmon (brew install macmon) while the command runs and prints the energy of the
whole Mac (sys_power, the SMC system power sensor) and of the SoC parts (CPU, GPU, RAM).
With --idle, it first samples the idle machine for that long, and also prints the energy
above idle: what the command itself costs. Each macmon sample is the average power over its
interval, so energy is the sum of power times interval.
"""
import argparse
import json
import subprocess
import sys
import threading
import time

FIELDS = ["sys_power", "cpu_power", "gpu_power", "ram_power"]


class Sampler:

    def __init__(self, interval_ms: int, log):
        self.proc = subprocess.Popen(["macmon", "pipe", "-i", str(interval_ms)],
                                     stdout=subprocess.PIPE,
                                     text=True)
        self.samples: list[tuple[float, dict]] = []  # (arrival time, sample)
        self.log = log
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self) -> None:
        for line in self.proc.stdout:
            sample = json.loads(line)
            self.samples.append((time.monotonic(), sample))
            if self.log:
                self.log.write(line)

    def energy(self, start: float, end: float) -> tuple[dict, float]:
        """Joules per field over the samples that arrived in [start, end], and their span."""
        window = [(t, s) for t, s in self.samples if start < t <= end]
        joules = {f: 0.0 for f in FIELDS}
        prev = start
        for t, s in window:
            for f in FIELDS:
                joules[f] += s[f] * (t - prev)
            prev = t
        return joules, prev - start

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.wait()


def report(name: str, joules: dict, span: float) -> None:
    parts = ", ".join(f"{f.split('_')[0]} {joules[f]:.0f} J ({joules[f] / span:.2f} W)"
                      for f in FIELDS[1:])
    print(f"{name}: {span:.1f} s, whole Mac {joules['sys_power']:.0f} J "
          f"({joules['sys_power'] / span:.2f} W); {parts}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--idle", type=float, default=0, help="seconds of idle baseline")
    parser.add_argument("--interval", type=int, default=1000, help="sample interval, ms")
    parser.add_argument("--log", type=argparse.FileType("w"), help="write raw samples")
    parser.add_argument("command", nargs="+")
    args = parser.parse_args()

    sampler = Sampler(args.interval, args.log)
    time.sleep(2 * args.interval / 1000)  # first samples
    idle_power = None
    if args.idle:
        start = time.monotonic()
        time.sleep(args.idle)
        joules, span = sampler.energy(start, time.monotonic())
        report("idle", joules, span)
        idle_power = {f: joules[f] / span for f in FIELDS}
    start = time.monotonic()
    rc = subprocess.run(args.command, check=False).returncode
    end = time.monotonic()
    time.sleep(1.5 * args.interval / 1000)  # the sample covering the end
    sampler.stop()
    joules, span = sampler.energy(start, end + args.interval / 1000)
    print(f"command exit {rc}, wall {end - start:.1f} s")
    report("command", joules, span)
    if idle_power:
        above = {f: joules[f] - idle_power[f] * span for f in FIELDS}
        report("above idle", above, span)
    sys.exit(rc)


if __name__ == "__main__":
    main()
