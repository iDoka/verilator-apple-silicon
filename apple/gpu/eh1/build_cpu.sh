#!/bin/bash
# Build the VeeR EH1 CoreMark model with the GPU testbench variant (make_tb.py), for the CPU.
# Usage: build_cpu.sh <variant-name> "<extra verilator flags>" "<OPT_FAST and OPT_GLOBAL>"
# Output: apple/gpu/work/eh1/<variant-name>/obj_dir; Verilator output there is also the
# input of the Metal translator.
set -euo pipefail
NAME=$1
VFLAGS=${2:-}
OPT=${3:--O2}
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
export VERILATOR_ROOT=$ROOT
export RV_ROOT=$ROOT/verilator_ext_tests/submodules/Cores-VeeR-EH1
SRC=$ROOT/verilator_ext_tests/obj_vlt/t_cores_veer_eh1_cmark
WORK=$ROOT/apple/gpu/work/eh1/$NAME
rm -rf "$WORK"
mkdir -p "$WORK"
cp -r "$SRC/snapshots" "$SRC/program.hex" "$WORK/"
python3 "$ROOT/apple/gpu/eh1/make_tb.py" "$WORK/tb"
cd "$WORK"
# shellcheck disable=SC2086
"$ROOT/bin/verilator" --cc -CFLAGS -std=c++20 \
    snapshots/default/common_defines.vh "$RV_ROOT/design/include/veer_types.sv" \
    -I"$RV_ROOT/design/include" -I"$RV_ROOT/design/lib" -Isnapshots/default \
    -Wno-UNOPTFLAT -Wno-IMPLICITSTATIC -Wno-WIDTHTRUNC -I"$RV_ROOT/testbench" -f "$RV_ROOT/testbench/flist" \
    tb/tb_top.sv tb/ahb_sif.sv \
    --top-module tb_top --exe test_tb_top.cpp $VFLAGS > verilate.log 2>&1
cp "$RV_ROOT/testbench/test_tb_top.cpp" obj_dir/
make -j8 -C obj_dir -f Vtb_top.mk OPT_FAST="$OPT" OPT_GLOBAL="$OPT" > make.log 2>&1
echo "built $WORK/obj_dir/Vtb_top"
