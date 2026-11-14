#!/bin/bash
# Build Verilator from this branch and install it with the Apple silicon tools.
# Usage: apple/install.sh <prefix>     e.g. apple/install.sh ~/opt/verilator-apple-gpu
set -euo pipefail
PREFIX=$1
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
autoconf
./configure --prefix="$PREFIX"
make -j6
make install
BIN=$PREFIX/bin
install -m 755 apple/tools/pgo_build.py "$BIN/verilator_pgo_build"
install -m 755 apple/tools/batch_run.py "$BIN/verilator_batch_run"
install -m 755 apple/bench/energy.py "$BIN/verilator_energy"
# vl2metal/build.sh finds the runtime headers three levels up: share/verilator/include
GPU=$PREFIX/share/verilator/apple/gpu/vl2metal
mkdir -p "$GPU"
cp apple/gpu/vl2metal/{build.sh,host.mm,verilated_metal.h,vl2metal.py} "$GPU/"
printf '#!/bin/sh\nexec "%s/build.sh" "$@"\n' "$GPU" > "$BIN/verilator_metal_build"
chmod 755 "$BIN/verilator_metal_build"
"$BIN/verilator" --version
