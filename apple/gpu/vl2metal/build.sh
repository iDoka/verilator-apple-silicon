#!/bin/bash
# Translate a Verilated model with vl2metal.py, compile the Metal library and the host.
# Usage: build.sh <obj_dir> <prefix> <clock> <status member> <out_dir>
# The obj_dir must be built for the CPU first (its objects are linked into the host).
set -euo pipefail
OBJ=$(cd "$1" && pwd)
PREFIX=$2
CLOCK=$3
STATUS=$4
OUT=$5
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
python3 "$HERE/vl2metal.py" "$OBJ" "$PREFIX" "$OUT" --clock "$CLOCK" --status "$STATUS"
cp "$HERE/verilated_metal.h" "$OUT/"
cd "$OUT"
start=$(date +%s)
xcrun metal -O2 -c sim.metal -o sim.air
xcrun metallib sim.air -o sim.metallib
echo "metal: $(( $(date +%s) - start )) s"
OBJS=$(ls "$OBJ"/*.o | grep -v test_tb_top.o)
clang++ -O2 -std=c++20 -fobjc-arc -DVL_TIME_CONTEXT -I"$OBJ" -I"$OUT" -I"$ROOT/include" -I"$ROOT/include/vltstd" \
    "$HERE/host.mm" $OBJS -framework Metal -framework Foundation -o host
echo "built $OUT/host"
