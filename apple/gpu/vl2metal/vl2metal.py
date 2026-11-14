#!/usr/bin/env python3
"""Translate a single-threaded Verilated model into a Metal compute kernel, one model instance
per GPU thread (the RTLflow approach), for running many simulations of one design at once.

Usage: vl2metal.py <obj_dir> <prefix> <out_dir> --status <member> [--lanes 32] [--clock clk]

Reads the Verilator C++ output (--cc, --threads 1) and writes to <out_dir>:
  sim.metal     the per-cycle code of the model as MSL, plus the kernel that runs it
  gpu_root.h    the same state layout for the host, and functions that copy one model instance
                between the CPU model and the GPU buffer

State layout ("array of structures of arrays"): instances are grouped in tiles of --lanes,
one tile per Root struct. Each scalar member of the model becomes an array over the lanes of
a tile, so the threads of one SIMD-group access consecutive addresses. Large unpacked arrays
(memories) are indexed [element][lane], so lanes reading the same address also coalesce.
The translation is text-based: member accesses vlSelfRef.X are rewritten to X[L] or X[i][L].

Supported subset: what the per-cycle code of the model uses must be in verilated_metal.h.
Initial blocks, $readmem and the settle region run on the CPU model before the state is
copied to the GPU. $display/$write are dropped, $finish sets the __Vgpu_finish member.
$random/$urandom draw from a per-instance generator in __Vgpu_rng0/1, which the host seeds
with the state of the CPU model's generator after initialization.
"""
import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent

SCALAR_SIZES = {"CData": 1, "SData": 2, "IData": 4, "QData": 8}
PORT_TYPES = {"8": "CData", "16": "SData", "": "IData", "64": "QData"}
# Arrays up to this many bytes per instance stay whole per lane (e.g. trigger vectors, which
# the code passes by reference); larger ones are laid out [element][lane].
SMALL_ARRAY_BYTES = 64
# Eval loop entry points, see VerilatedEvalLoop::evalImpl in include/verilated.cpp
ENTRIES = [
    "eval_sample", "eval_ico", "eval_act", "eval_inact", "eval_nba", "eval_obs", "eval_react",
    "eval_postponed"
]
REMOVED_CALLS = ["VL_DEBUG_IF", "VL_WRITEF_NX", "VL_FWRITEF_NX"]


def die(msg: str) -> None:
    sys.exit(f"vl2metal: {msg}")


def type_size(t: str) -> int:
    if t in SCALAR_SIZES:
        return SCALAR_SIZES[t]
    m = re.fullmatch(r"VlWide<(\d+)>", t)
    if m:
        return 4 * int(m.group(1))
    m = re.fullmatch(r"VlUnpacked<(.+), (\d+)>", t)
    if m:
        return type_size(m.group(1)) * int(m.group(2))
    die(f"unsupported member type {t}")
    return 0


@dataclass
class Member:
    name: str
    ctype: str  # without :: and comments, e.g. VlUnpacked<QData, 16384>
    cpu_path: str  # access path in the CPU root class
    elem: str = ""  # element type of a large array
    depth: int = 0  # element count of a large array

    @property
    def big(self) -> bool:
        return self.depth > 0

    def decl(self, lanes: int) -> str:
        if self.big:
            return f"    {self.elem} {self.name}[{self.depth}][{lanes}];"
        return f"    {self.ctype} {self.name}[{lanes}];"


def parse_header(path: Path) -> tuple[list[Member], set[str]]:
    members: list[Member] = []
    skipped: set[str] = set()
    for line in path.read_text().splitlines():
        line = re.sub(r"/\*.*?\*/", "", line)
        m = re.match(r"\s*VL_(?:IN|OUT)(8|16|64|)\((\w+),", line)
        if m:
            members.append(
                Member(m.group(2), PORT_TYPES[m.group(1)], "__VprimaryPorts." + m.group(2)))
            continue
        if re.match(r"\s*VL_(?:IN|OUT)W\(", line):
            die("wide ports are not supported")
        m = re.match(r"\s*(::[\w:<>, ]+?)\s+(\w+);\s*$", line)
        if not m:
            continue
        ctype = m.group(1).replace("::", "")
        name = m.group(2)
        if not re.fullmatch(r"(CData|SData|IData|QData|VlWide<\d+>|VlUnpacked<.+>)", ctype) \
                or "std" in ctype or "Assoc" in ctype:
            skipped.add(name)
            continue
        mem = Member(name, ctype, name)
        am = re.fullmatch(r"VlUnpacked<(.+), (\d+)>", ctype)
        if am and type_size(ctype) > SMALL_ARRAY_BYTES:
            if am.group(1).startswith("VlUnpacked"):
                die(f"multi-dimensional array {name} is not supported")
            mem.elem, mem.depth = am.group(1), int(am.group(2))
        members.append(mem)
    return members, skipped


def parse_functions(files: list[Path], prefix: str) -> dict[str, tuple[str, str]]:
    """Return {name: (signature, body)} of the root class functions."""
    sig_re = re.compile(r"^(?:VL_ATTR_COLD\s+)?(\w[\w:]*(?:/\*.*?\*/)?)\s+(" + prefix +
                        r"___024root___\w+)\((.*)\)\s*\{\s*$")
    funcs: dict[str, tuple[str, str]] = {}
    for f in files:
        lines = f.read_text().splitlines()
        i = 0
        while i < len(lines):
            m = sig_re.match(lines[i])
            if not m:
                i += 1
                continue
            j = i + 1
            while lines[j] != "}":
                j += 1
            body = "\n".join(lines[i + 1:j])
            body = re.sub(r"#ifdef VL_DEBUG\n.*?#endif[^\n]*", "", body, flags=re.S)
            funcs[m.group(2)] = (f"{m.group(1)} {m.group(2)}({m.group(3)})", body)
            i = j + 1
    return funcs


def reachable(funcs: dict[str, tuple[str, str]], prefix: str) -> list[str]:
    call_re = re.compile(r"\b(" + prefix + r"___024root___\w+)\s*\(")
    todo = [f"{prefix}___024root___{e}" for e in ENTRIES]
    seen: list[str] = []
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        if name not in funcs:
            die(f"function {name} not found")
        seen.append(name)
        todo += call_re.findall(funcs[name][1])
    return seen


def matching(text: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index just past the bracket matching the one at text[start]."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
    die("unbalanced brackets")
    return 0


def remove_calls(text: str, names: list[str]) -> str:
    for name in names:
        while True:
            m = re.search(r"\b" + name + r"\(", text)
            if not m:
                break
            end = matching(text, m.end() - 1, "(", ")")
            semi = re.match(r"\s*;", text[end:])
            text = text[:m.start()] + text[end + (semi.end() if semi else 0):]
    return text


def rewrite_members(text: str, members: dict[str, Member], skipped: set[str]) -> str:
    out = []
    pos = 0
    for m in re.finditer(r"\bvlSelfRef\.(\w+)", text):
        if m.start() < pos:
            continue  # inside an index already copied
        name = m.group(1)
        if name in skipped:
            die(f"per-cycle code uses unsupported member {name}")
        mem = members.get(name)
        if mem is None:
            die(f"unknown member {name}")
        if not mem.big:
            out.append(text[pos:m.end()] + "[L]")
            pos = m.end()
            continue
        k = m.end()
        while text[k].isspace():
            k += 1
        if text[k] != "[":
            die(f"array {name} is used whole")
        end = matching(text, k, "[", "]")
        # The index may itself access members
        index = rewrite_members(text[k + 1:end - 1], members, skipped)
        out.append(text[pos:m.end()] + "[" + index + "][L]")
        pos = end
    out.append(text[pos:])
    return "".join(out)


def translate_function(sig: str, body: str, prefix: str, members: dict[str, Member],
                       skipped: set[str]) -> tuple[str, str]:
    sig = sig.replace(f"({prefix}___024root* vlSelf", "(device Root* vlSelf, uint L")
    sig = sig.replace("const ::VlUnpacked<", "const device ::VlUnpacked<")
    sig = re.sub(r"([(,]\s*)::VlUnpacked<", r"\1device ::VlUnpacked<", sig)
    body = remove_calls(body, REMOVED_CALLS)
    body = re.sub(r"VL_FINISH_MT\([^;]*\);", "vlSelfRef.__Vgpu_finish = 1;", body)
    body = re.sub(r".*vlSymsp VL_ATTR_UNUSED = vlSelf->vlSymsp;\n", "", body)
    body = body.replace("auto& vlSelfRef = std::ref(*vlSelf).get();",
                        "device Root& vlSelfRef = *vlSelf;")
    body = body.replace(".__VprimaryPorts.", ".")
    if "__Vnontrivs" in body or "vlSymsp" in body:
        die(f"{sig}: uses non-trivial members or the symbol table")
    body = rewrite_members(body, members, skipped)
    body = re.sub(r"(\b" + prefix + r"___024root___\w+\()vlSelf\b", r"\1vlSelf, L", body)
    # Random numbers come from the instance's generator, see verilated_metal.h
    body = re.sub(r"\b(VL_U?RANDOM_\w+)\(\s*\)", r"\1(VL_GPU_RNG)", body)
    body = re.sub(r"\b(VL_U?RANDOM_\w+)\((?!VL_GPU_RNG)", r"\1(VL_GPU_RNG, ", body)
    body = re.sub(r"\b(0x[0-9a-fA-F]+|\d+)ULL\b", r"\1UL", body)
    return sig, body


def parse_constpool(files: list[Path], used: set[str]) -> list[str]:
    defs = []
    for f in files:
        for m in re.finditer(r"^extern VL_CONSTINIT_CXX20 const (.+?) (\w+)\{\{(.*?)\}\};",
                             f.read_text(), re.M | re.S):
            if m.group(2) in used:
                init = re.sub(r"\b(0x[0-9a-fA-F]+|\d+)ULL\b", r"\1UL", m.group(3))
                defs.append(f"constant {m.group(1)} {m.group(2)} = {{{{{init}}}}};")
    return defs


def emit_kernel(prefix: str, lanes: int, clock: str) -> str:
    p = f"{prefix}___024root___"
    return f"""
#define VL2METAL_CONVERGE_LIMIT 100

// VerilatedEvalLoop::evalImpl after initialization; a region that does not converge sets
// __Vgpu_finish to 2
static void vl2metal_eval(device Root* vlSelf, uint L) {{
    {p}eval_sample(vlSelf, L);
    uint ico = 0;
    do {{
        if (++ico > VL2METAL_CONVERGE_LIMIT) {{ vlSelf->__Vgpu_finish[L] = 2; return; }}
    }} while ({p}eval_ico(vlSelf, L, ico == 1));
    uint react = 0;
    do {{
        uint obs = 0;
        do {{
            uint nba = 0;
            do {{
                uint inact = 0;
                do {{
                    uint act = 0;
                    do {{
                        if (++act > VL2METAL_CONVERGE_LIMIT) {{ vlSelf->__Vgpu_finish[L] = 2; return; }}
                    }} while ({p}eval_act(vlSelf, L));
                    if (++inact > VL2METAL_CONVERGE_LIMIT) {{ vlSelf->__Vgpu_finish[L] = 2; return; }}
                }} while ({p}eval_inact(vlSelf, L));
                if (++nba > VL2METAL_CONVERGE_LIMIT) {{ vlSelf->__Vgpu_finish[L] = 2; return; }}
            }} while ({p}eval_nba(vlSelf, L));
            if (++obs > VL2METAL_CONVERGE_LIMIT) {{ vlSelf->__Vgpu_finish[L] = 2; return; }}
        }} while ({p}eval_obs(vlSelf, L));
        if (++react > VL2METAL_CONVERGE_LIMIT) {{ vlSelf->__Vgpu_finish[L] = 2; return; }}
    }} while ({p}eval_react(vlSelf, L));
    {p}eval_postponed(vlSelf, L);
}}

struct Vl2MetalArgs {{
    uint halfCycles;  // evaluations per dispatch, the clock toggles before each
    uint instances;
}};

// One thread per model instance; threadgroups must be a multiple of {lanes} threads
kernel void vl2metal_run(device Root* tiles [[buffer(0)]], constant Vl2MetalArgs& args [[buffer(1)]],
                         uint gid [[thread_position_in_grid]]) {{
    if (gid >= args.instances) return;
    const uint L = gid % {lanes};
    device Root* vlSelf = &tiles[gid / {lanes}];
    for (uint i = 0; i < args.halfCycles; ++i) {{
        if (vlSelf->__Vgpu_finish[L]) return;
        vlSelf->{clock}[L] = !vlSelf->{clock}[L];
        vl2metal_eval(vlSelf, L);
        ++vlSelf->__Vgpu_evals[L];
    }}
}}
"""


def members_names(members: list[Member]) -> set[str]:
    return {m.name for m in members}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("obj_dir", type=Path)
    parser.add_argument("prefix")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--lanes", type=int, default=32)
    parser.add_argument("--clock", default="clk", help="clock input, toggled before each eval")
    parser.add_argument("--status",
                        required=True,
                        help="member holding the test result, returned by vl2metal_status()")
    parser.add_argument("--noinline",
                        action="store_true",
                        help="do not inline the model's per-cycle functions into the kernel")
    args = parser.parse_args()
    prefix = args.prefix
    lanes = args.lanes

    member_list, skipped = parse_header(args.obj_dir / f"{prefix}___024root.h")
    member_list.append(Member("__Vgpu_finish", "CData", ""))
    member_list.append(Member("__Vgpu_evals", "IData", ""))
    member_list.append(Member("__Vgpu_rng0", "QData", ""))
    member_list.append(Member("__Vgpu_rng1", "QData", ""))
    if args.status not in members_names(member_list):
        die(f"status member {args.status} not found")
    members = {m.name: m for m in member_list}
    fast = sorted(args.obj_dir.glob(f"{prefix}___024root__[0-9]*.cpp"))
    fast = [f for f in fast if "Slow" not in f.name]
    funcs = parse_functions(fast, prefix)
    names = reachable(funcs, prefix)

    protos, bodies = [], []
    for name in sorted(names):
        sig, body = translate_function(*funcs[name], prefix, members, skipped)
        if args.noinline and re.search(r"___(nba|eval_body)", name):
            # Keeps the Metal backend from compiling the whole model as one function
            sig = "__attribute__((noinline)) " + sig
        protos.append(sig + ";")
        bodies.append(sig + " {\n" + body + "\n}\n")
    code = "\n".join(bodies)
    used_consts = set(re.findall(r"\b" + prefix + r"__ConstPool__\w+", code))
    consts = parse_constpool(sorted(args.obj_dir.glob(f"{prefix}__ConstPool__*.cpp")), used_consts)
    if len(consts) != len(used_consts):
        die("missing constant pool entries")

    # Small members first, so the per-cycle working set is dense; memories last
    member_list.sort(key=lambda m: m.big)
    struct = "struct Root {\n" + "\n".join(m.decl(lanes) for m in member_list) + "\n};\n"
    args.out_dir.mkdir(parents=True, exist_ok=True)

    layout = "\n".join(f"    out[{i}] = (ulong)((device char*)&r->{m.name} - (device char*)r);"
                       for i, m in enumerate(member_list))
    metal = (f'// Generated by vl2metal.py from {args.obj_dir}\n'
             f'#include "verilated_metal.h"\n\n{struct}\n' + "\n".join(consts) + "\n\n" +
             "\n".join(protos) + "\n\n" + code + emit_kernel(prefix, lanes, args.clock) +
             "\n// Member offsets, to check the host copy of the layout\n"
             "kernel void vl2metal_layout(device ulong* out [[buffer(0)]], "
             "device Root* r [[buffer(1)]]) {\n" + layout +
             f"\n    out[{len(member_list)}] = sizeof(Root);\n}}\n")
    (args.out_dir / "sim.metal").write_text(metal)

    pack, unpack, compare, offsets = [], [], [], []
    for m in member_list:
        offsets.append(f"        offsetof(Root, {m.name}),")
        if not m.cpu_path:
            continue
        if m.big:
            pack.append(f"    for (size_t i = 0; i < {m.depth}; ++i) "
                        f"std::memcpy(&t.{m.name}[i][l], &s.{m.cpu_path}.m_storage[i], "
                        f"sizeof(t.{m.name}[i][l]));")
            unpack.append(f"    for (size_t i = 0; i < {m.depth}; ++i) "
                          f"std::memcpy(&s.{m.cpu_path}.m_storage[i], &t.{m.name}[i][l], "
                          f"sizeof(t.{m.name}[i][l]));")
            compare.append(
                f"    for (size_t i = 0; i < {m.depth}; ++i) "
                f"if (std::memcmp(&s.{m.cpu_path}.m_storage[i], &t.{m.name}[i][l], "
                f"sizeof(t.{m.name}[i][l]))) {{ diffs.push_back(\"{m.name}\"); break; }}")
        else:
            pack.append(
                f"    std::memcpy(&t.{m.name}[l], &s.{m.cpu_path}, sizeof(t.{m.name}[l]));")
            unpack.append(
                f"    std::memcpy(&s.{m.cpu_path}, &t.{m.name}[l], sizeof(t.{m.name}[l]));")
            compare.append(f"    if (std::memcmp(&s.{m.cpu_path}, &t.{m.name}[l], "
                           f"sizeof(t.{m.name}[l]))) diffs.push_back(\"{m.name}\");")
    root = f"{prefix}___024root"
    host = f"""// Generated by vl2metal.py from {args.obj_dir}
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

#include "{prefix}.h"
#include "{root}.h"

using Vl2MetalModel = {prefix};
#define VL2METAL_CLOCK {args.clock}

namespace vl2metal {{
typedef uint8_t CData;
typedef uint16_t SData;
typedef uint32_t IData;
typedef uint64_t QData;
template <int N> struct VlWide {{ uint32_t m_storage[N]; }};
template <class T, int N> struct VlUnpacked {{ T m_storage[N]; }};
constexpr size_t kLanes = {lanes};
{struct}
// Offsets of the members in declaration order, then sizeof(Root); vl2metal_layout writes
// the same from the GPU
inline std::vector<uint64_t> layout() {{
    return {{
{chr(10).join(offsets)}
        sizeof(Root)}};
}}
}}  // namespace vl2metal

inline void vl2metal_pack(vl2metal::Root* tiles, size_t lane, const {root}& s) {{
    vl2metal::Root& t = tiles[lane / vl2metal::kLanes];
    const size_t l = lane % vl2metal::kLanes;
{chr(10).join(pack)}
    t.__Vgpu_finish[l] = 0;
    t.__Vgpu_evals[l] = 0;
}}

inline void vl2metal_unpack(const vl2metal::Root* tiles, size_t lane, {root}& s) {{
    const vl2metal::Root& t = tiles[lane / vl2metal::kLanes];
    const size_t l = lane % vl2metal::kLanes;
{chr(10).join(unpack)}
}}

// Names of the members that differ between the CPU model and one GPU instance
inline std::vector<std::string> vl2metal_compare(const vl2metal::Root* tiles, size_t lane,
                                                 const {root}& s) {{
    const vl2metal::Root& t = tiles[lane / vl2metal::kLanes];
    const size_t l = lane % vl2metal::kLanes;
    std::vector<std::string> diffs;
{chr(10).join(compare)}
    return diffs;
}}

inline uint64_t vl2metal_status(const vl2metal::Root& t, size_t l) {{ return t.{args.status}[l]; }}

inline uint8_t vl2metal_finish(const vl2metal::Root* tiles, size_t lane) {{
    return tiles[lane / vl2metal::kLanes].__Vgpu_finish[lane % vl2metal::kLanes];
}}
"""
    (args.out_dir / "gpu_root.h").write_text(host)
    print(f"vl2metal: {len(names)} functions, {len(member_list)} members, "
          f"{len(consts)} constants, Root {sum(type_size(m.ctype) for m in member_list) * lanes} "
          f"bytes per {lanes} lanes")


if __name__ == "__main__":
    main()
