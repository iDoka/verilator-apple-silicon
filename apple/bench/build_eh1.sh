#!/bin/bash
# Build one VeeR EH1 CoreMark simulator variant.
# Usage: build_eh1.sh <variant-name> "<extra verilator flags>" "<OPT_FAST and OPT_GLOBAL>" ["<USER_LDFLAGS>"]
# Needs the snapshot and program.hex made by verilator_ext_tests/t/t_cores_veer_eh1_cmark.py.
set -euo pipefail
NAME=$1
VFLAGS=${2:-}
OPT_FAST=${3:--Os}
LDFLAGS_USER=${4:-}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
export VERILATOR_ROOT=$ROOT
export RV_ROOT=$ROOT/verilator_ext_tests/submodules/Cores-VeeR-EH1
SRC=$ROOT/verilator_ext_tests/obj_vlt/t_cores_veer_eh1_cmark
WORK=$ROOT/apple/bench/work/eh1/$NAME
rm -rf "$WORK"
mkdir -p "$WORK"
cp -r "$SRC/snapshots" "$SRC/program.hex" "$WORK/"
cd "$WORK"
# shellcheck disable=SC2086
"$ROOT/bin/verilator" --cc -CFLAGS -std=c++20 \
    snapshots/default/common_defines.vh "$RV_ROOT/design/include/veer_types.sv" \
    -I"$RV_ROOT/design/include" -I"$RV_ROOT/design/lib" -Isnapshots/default \
    -Wno-UNOPTFLAT -Wno-IMPLICITSTATIC -I"$RV_ROOT/testbench" -f "$RV_ROOT/testbench/flist" \
    "$RV_ROOT/testbench/tb_top.sv" "$RV_ROOT/testbench/ahb_sif.sv" \
    --top-module tb_top --exe test_tb_top.cpp $VFLAGS > verilate.log 2>&1
cp "$RV_ROOT/testbench/test_tb_top.cpp" obj_dir/
make -j8 -C obj_dir -f Vtb_top.mk OPT_FAST="$OPT_FAST" OPT_GLOBAL="$OPT_FAST" \
    USER_LDFLAGS="$LDFLAGS_USER" > make.log 2>&1
echo "built $WORK/obj_dir/Vtb_top"
