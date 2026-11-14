#!/usr/bin/env python3
"""Rebuild a Verilated model with Clang compiler PGO (profile-guided optimization).

Usage: pgo_build.py [--obj-dir obj_dir] [--opt -O2] [--cwd DIR] -- <training command...>

Run after Verilator has written <obj-dir>/V<top>.mk (verilator --cc/--exe without --build).
Steps: build instrumented, run the training command, merge the profile, rebuild with it.
The training command should be a typical simulation of the model, e.g. one regression test.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def make(obj_dir: Path, mk: Path, opt: str, profile_flag: str) -> None:
    # -B: the flags changed, but make does not track flags, so rebuild everything.
    # Only the fast-path and runtime code is profiled; the slow-path (OPT_SLOW) code
    # runs once, and profiling it at a different optimization level gives hash mismatches.
    flags = f"{opt} {profile_flag}"
    cmd = [
        "make", "-B", f"-j{os.cpu_count()}", "-C",
        str(obj_dir), "-f", mk.name, f"OPT_FAST={flags}", f"OPT_GLOBAL={flags}",
        f"USER_LDFLAGS={profile_flag}"
    ]
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def profdata_tool() -> list[str]:
    if shutil.which("xcrun"):
        return ["xcrun", "llvm-profdata"]
    return ["llvm-profdata"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--obj-dir", type=Path, default=Path("obj_dir"))
    parser.add_argument("--opt", default="-O2", help="OPT_FAST and OPT_GLOBAL (default -O2)")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="training run directory")
    parser.add_argument("training", nargs="+", help="training command")
    args = parser.parse_args()

    obj_dir = args.obj_dir.resolve()
    mks = [p for p in obj_dir.glob("V*.mk") if not p.name.endswith(("_classes.mk", "__ver.d"))]
    if len(mks) != 1:
        sys.exit(f"pgo_build: expected one V<top>.mk in {obj_dir}, found {mks}")
    mk = mks[0]
    prof_dir = obj_dir / "pgo"
    shutil.rmtree(prof_dir, ignore_errors=True)
    prof_dir.mkdir()
    profdata = prof_dir / "default.profdata"

    make(obj_dir, mk, args.opt, "-fprofile-generate")
    env = dict(os.environ, LLVM_PROFILE_FILE=str(prof_dir / "%p.profraw"))
    print("+ " + " ".join(args.training), flush=True)
    subprocess.run(args.training, cwd=args.cwd, env=env, check=True, stdout=subprocess.DEVNULL)
    raws = [str(p) for p in prof_dir.glob("*.profraw")]
    if not raws:
        sys.exit("pgo_build: the training run wrote no profile")
    subprocess.run(profdata_tool() + ["merge", "-output", str(profdata)] + raws, check=True)
    make(obj_dir, mk, args.opt, f"-fprofile-use={profdata}")
    print(f"pgo_build: rebuilt {obj_dir / mk.stem} with {profdata}")


if __name__ == "__main__":
    main()
