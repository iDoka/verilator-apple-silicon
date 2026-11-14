#!/bin/bash
# Build the rng_tb test for the CPU and for the GPU.
# Usage: build.sh <out_dir> [extra verilator flags, e.g. +define+RESEED]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
OUT=$1
shift
mkdir -p "$OUT"
cd "$OUT"
VERILATOR_ROOT=$ROOT "$ROOT/bin/verilator" --cc --exe --build -j 4 --output-split-cfuncs 1000 \
    -CFLAGS -std=c++20 "$@" "$HERE/rng_tb.sv" "$HERE/rng_main.cpp" > verilate.log
"$HERE/../vl2metal/build.sh" obj_dir Vrng_tb clk rng_tb__DOT__gpu_status metal --noinline \
    > metal.log 2>&1
echo "built $OUT/obj_dir/Vrng_tb and $OUT/metal/host"
