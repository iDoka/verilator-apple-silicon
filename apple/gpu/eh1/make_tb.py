#!/usr/bin/env python3
"""Make the GPU variant of the VeeR EH1 testbench: no file I/O or strings in the per-cycle path.

Usage: make_tb.py <out_dir>
Changes against Cores-VeeR-EH1/testbench:
- the trace monitor (exec.log, trace_port.csv, dasm strings) and console.log are removed;
- the AXI memory models serve run-time accesses from a fixed 4 KB array instead of an
  associative array; the associative array is kept for $readmemh and the ICCM/DCCM preload,
  which run once on the CPU. Run-time accesses are the boot code at 0x0 and the mailbox;
- the end-of-test monitor also records the result in gpu_status (0xff passed, 1 failed).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TB = ROOT / "verilator_ext_tests/submodules/Cores-VeeR-EH1/testbench"


def sub(text: str, old: str, new: str, count: int = 1) -> str:
    if text.count(old) != count:
        sys.exit(f"make_tb: expected {count} of {old!r}")
    return text.replace(old, new)


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)

    tb = (TB / "tb_top.sv").read_text()
    # Trace monitor: from its comment to the initial block
    tb = re.sub(r"    // trace monitor\n.*?\n    initial begin\n", "    initial begin\n", tb,
                count=1, flags=re.S)
    tb = re.sub(r"        abi_reg\[\d+\] = \"\w+\";\n", "", tb)
    tb = sub(tb, "    string                      abi_reg[32]; // ABI register names\n",
             "    bit [7:0]                   gpu_status /*verilator public_flat*/;\n")
    tb = sub(tb, '            $fwrite(fd,"%c", WriteData[7:0]);\n', "")
    tb = sub(tb, '            $write("%c", WriteData[7:0]);\n', "")
    tb = sub(tb, '            $display("TEST_PASSED");\n',
             '            $display("TEST_PASSED");\n            gpu_status = 8\'hff;\n')
    tb = sub(tb, '            $display("TEST_FAILED");\n',
             '            $display("TEST_FAILED");\n            gpu_status = 8\'h1;\n')
    tb = re.sub(r"        (tp|el|fd) = \$fopen\(.*\n", "", tb)
    tb = re.sub(r"        \$fwrite \(el, .*\n", "", tb)
    tb = sub(tb, "        preload_dccm();\n",
             "        for (int i = 0; i < 4096; i++) begin\n"
             "            lmem.rmem[i] = lmem.mem[i];\n"
             "            imem.rmem[i] = imem.mem[i];\n"
             "        end\n"
             "        preload_dccm();\n")
    tb = sub(tb, '`include "dasm.svi"\n', "")
    (out / "tb_top.sv").write_text(tb)

    sif = (TB / "ahb_sif.sv").read_text()
    # Only the AXI slave (second model in the file) is used with RV_BUILD_AXI4
    head, axi = sif.split("module axi_slv", 1)
    axi = sub(axi, "bit [7:0] mem [bit[31:0]];\n",
              "bit [7:0] mem [bit[31:0]];  // $readmemh and preload only\n"
              "bit [7:0] rmem [0:4095];  // run-time accesses\n")
    axi = re.sub(r"\bmem\[(araddr|awaddr)(\+\d)?\]", r"rmem[12'(\1\2)]", axi)
    (out / "ahb_sif.sv").write_text(head + "module axi_slv" + axi)


if __name__ == "__main__":
    main()
