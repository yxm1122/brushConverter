# -*- coding: utf-8 -*-
"""
native_xref.py —— 在「不跑 Ghidra 全量分析」的前提下，为 208 MB 的 Photoshop.exe
取得可复现的函数级交叉引用证据。

为什么不用 Ghidra 全量分析：见 docs/研究报告/ps-逆向-静态.md 第「可行性」节。
本机 Photoshop.exe 的 .text 有 136,037,140 字节，Ghidra 自动分析需要数小时与 8 GB+
堆内存，而分析机空闲物理内存仅约 5.5 GB（页面文件可用 0.55 GB）。因此本脚本用三条
低成本、可解释的证据链代替：

  1. 函数边界：直接读 PE 的 .pdata（异常目录 / RUNTIME_FUNCTION 表）。x64 PE 里每个
     有栈展开信息的函数都有一条 (BeginAddress, EndAddress, UnwindInfoAddress) 记录，
     这就是一份「函数起始地址表」，无需反汇编即可把任意代码地址归到所属函数。
  2. 数据引用：把「字符串的虚拟地址」作为搜索键，扫 .text 里的
     REX + 0x8D/0x8B + ModRM(rip 相对) + disp32 指令，解出目标地址；命中后再用
     capstone 逐条反汇编校验，排除误报。
  3. 调用关系：扫 .text 里的 E8 rel32 直接调用，目标落在候选函数起始地址上的记为
     调用边；再用 capstone 从函数起点线性反汇编到该地址，确认那里确实是一条 call。
     间接调用（call [reg] / vtable 派发）扫不到，报告里如实标注为局限。

另外提供 table 模式：给定名字字符串地址，在数据段里找指向它的 8 字节指针，再把指针
所在的「记录」按 8 字节切片打印，记录里落在 .text 范围内的值就是「该名字对应的处理
函数指针」候选 —— 混合模式注册表通常就是这种结构。

用法：
  # 1) 打印某段字节区间内的可打印字符串（定位名字表用）
  python tools/re_pipeline/native_xref.py dump --exe EXE --start 140129600 --end 140136000

  # 2) 找指向字符串的指针记录
  python tools/re_pipeline/native_xref.py table --exe EXE --va 0x14085CB30 --radius 64

  # 3) 全量交叉引用扫描（字符串 -> 指令 -> 函数），capstone 校验
  python tools/re_pipeline/native_xref.py refs --exe EXE --needles "Linear Height,Hard Mix" --out out.json

  # 4) 给候选函数找直接调用者
  python tools/re_pipeline/native_xref.py callers --exe EXE --funcs 0x140123456,0x140234567
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import struct
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pe_scan import PeImage  # 复用区段/地址换算（同目录，中文注释见 pe_scan.py）

try:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64
    from capstone.x86 import X86_OP_MEM, X86_REG_RIP
except Exception as exc:  # pragma: no cover
    raise SystemExit("需要 capstone：pip install capstone（本次实测 5.0.7）: %s" % exc)


# ----------------------------------------------------------------------------
# 一、.pdata 函数表
# ----------------------------------------------------------------------------
class FunctionTable:
    """.pdata（IMAGE_DIRECTORY_ENTRY_EXCEPTION）解析出的函数边界表。"""

    def __init__(self, pe: PeImage) -> None:
        self.pe = pe
        self.entries: List[Tuple[int, int, int]] = []  # (begin_rva, end_rva, unwind_rva)
        self._begins: List[int] = []
        self._load()

    def _load(self) -> None:
        # .pdata 的位置：优先用可选头里第 4 个数据目录（异常目录）。pe_scan 没解析它，
        # 这里直接按区段名找，等价且更简单；Photoshop.exe 的区段名就叫 .pdata。
        sec = next((s for s in self.pe.sections if s["name"] == ".pdata"), None)
        if sec is None:
            return
        with open(self.pe.path, "rb") as f:
            f.seek(sec["raw_ptr"])
            raw = f.read(sec["raw_size"])
        n = len(raw) // 12
        for i in range(n):
            beg, end, unw = struct.unpack_from("<III", raw, i * 12)
            if beg == 0 and end == 0:
                continue
            self.entries.append((beg, end, unw))
        self.entries.sort()
        self._begins = [e[0] for e in self.entries]

    def containing(self, va: int) -> Optional[Tuple[int, int]]:
        """返回包含该 VA 的函数 (begin_va, end_va)；不在任何函数里返回 None。"""
        rva = va - self.pe.image_base
        i = bisect.bisect_right(self._begins, rva) - 1
        if i < 0:
            return None
        beg, end, _ = self.entries[i]
        if beg <= rva < end:
            return (self.pe.image_base + beg, self.pe.image_base + end)
        return None

    def is_start(self, va: int) -> bool:
        rva = va - self.pe.image_base
        i = bisect.bisect_left(self._begins, rva)
        return i < len(self._begins) and self._begins[i] == rva


# ----------------------------------------------------------------------------
# 二、字节工具
# ----------------------------------------------------------------------------
_MD = Cs(CS_ARCH_X86, CS_MODE_64)
_MD.detail = True


def read_sec(pe: PeImage, name: str) -> Tuple[bytes, int]:
    """读区段原始字节，返回 (data, base_va)。"""
    for s in pe.sections:
        if s["name"] == name:
            with open(pe.path, "rb") as f:
                f.seek(s["raw_ptr"])
                return f.read(s["raw_size"]), pe.image_base + s["va"]
    raise KeyError(name)


def scan_rip_imm_refs(code: bytes, base_va: int, targets: Dict[int, str], limit: int = 100000) -> List[Dict]:
    """单遍扫描：找出所有 rip 相对寻址且目标落在 targets 里的指令（机器码级预筛）。

    x86-64 里引用全局数据的典型形态：48/4C 8D|8B ModRM(rip) disp32，共 7 字节。
    这里先按字节模式预筛，再交给 capstone 校验。
    """
    out: List[Dict] = []
    n = len(code)
    i = 0
    find48 = code.find
    while True:
        j48 = find48(b"\x48", i)
        j4c = find48(b"\x4c", i)
        cands = [x for x in (j48, j4c) if x >= 0]
        if not cands:
            break
        j = min(cands)
        i = j + 1
        if j + 7 > n:
            break
        op = code[j + 1]
        if op not in (0x8D, 0x8B):
            continue
        modrm = code[j + 2]
        # mod=00, rm=101 -> rip 相对；ModRM 取值 0x05/0x0D/0x15/0x1D/0x25/0x2D/0x35/0x3D
        if modrm not in (0x05, 0x0D, 0x15, 0x1D, 0x25, 0x2D, 0x35, 0x3D):
            continue
        disp = struct.unpack_from("<i", code, j + 3)[0]
        insn_va = base_va + j
        tgt = insn_va + 7 + disp
        label = targets.get(tgt)
        if label is None:
            continue
        out.append(
            {
                "insn_va": insn_va,
                "target_va": tgt,
                "target_label": label,
                "opcode": "lea" if op == 0x8D else "mov",
                "raw": code[j : j + 7].hex(),
            }
        )
        if len(out) >= limit:
            break
    return out


def capstone_verify(code: bytes, base_va: int, hit: Dict) -> Optional[Dict]:
    """用 capstone 反汇编该地址，确认确实是一条引用目标的 rip 相对指令。"""
    off = hit["insn_va"] - base_va
    if off < 0 or off + 7 > len(code):
        return None
    for insn in _MD.disasm(code[off : off + 16], hit["insn_va"]):
        if insn.address != hit["insn_va"]:
            return None
        ops = insn.operands
        if len(ops) >= 1 and ops[-1].type == X86_OP_MEM and ops[-1].mem.base == X86_REG_RIP:
            # capstone 已解出绝对地址
            eff = insn.address + insn.size + ops[-1].mem.disp
            if eff == hit["target_va"]:
                return {"text": "%s %s" % (insn.mnemonic, insn.op_str), "size": insn.size, "effective": hex(eff)}
        return None
    return None


def dump_strings(path: str, start: int, end: int, min_len: int = 3) -> List[Dict]:
    with open(path, "rb") as f:
        f.seek(start)
        b = f.read(end - start)
    out = []
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, b):
        out.append({"file_offset": start + m.start(), "text": m.group().decode("latin1")})
    return out


# ----------------------------------------------------------------------------
# 三、指针记录（混合模式注册表）分析
# ----------------------------------------------------------------------------
def pointer_records(pe: PeImage, target_va: int, radius: int = 64) -> List[Dict]:
    """在数据段里找指向 target_va 的 8 字节指针，并把指针附近 radius 字节按 8 字节切片。"""
    needle = struct.pack("<Q", target_va)
    results: List[Dict] = []
    text_lo = pe.image_base
    text_hi = pe.image_base + pe.size_of_image
    for s in pe.sections:
        if s["executable"] or s["raw_size"] == 0:
            continue
        with open(pe.path, "rb") as f:
            f.seek(s["raw_ptr"])
            data = f.read(s["raw_size"])
        start = 0
        while True:
            i = data.find(needle, start)
            if i < 0:
                break
            ptr_va = pe.image_base + s["va"] + i
            lo = max(0, i - radius)
            hi = min(len(data), i + radius + 8)
            # 按 8 字节对齐切片（记录通常按指针宽度对齐）
            al_lo = (lo // 8) * 8
            records = []
            for k in range(al_lo, hi - 8 + 1, 8):
                q = struct.unpack_from("<Q", data, k)[0]
                rec_va = pe.image_base + s["va"] + k
                kind = "?"
                if q == target_va:
                    kind = "TARGET"
                elif text_lo <= q < text_hi:
                    kind = "code_or_data_ptr"
                elif q == 0:
                    kind = "null"
                records.append({"va": hex(rec_va), "qword": hex(q), "kind": kind})
            results.append(
                {
                    "section": s["name"],
                    "ptr_va": hex(ptr_va),
                    "ptr_file_offset": s["raw_ptr"] + i,
                    "records": records,
                }
            )
            start = i + 1
    return results


# ----------------------------------------------------------------------------
# 四、调用者扫描（E8 rel32，capstone 校验）
# ----------------------------------------------------------------------------
def find_direct_callers(pe: PeImage, fns: Sequence[int], ft: FunctionTable) -> Dict[int, List[Dict]]:
    text, text_va = read_sec(pe, ".text")
    want = set(fns)
    raw_sites: Dict[int, List[int]] = {f: [] for f in fns}
    i = 0
    while True:
        i = text.find(b"\xe8", i)
        if i < 0 or i + 5 > len(text):
            break
        rel = struct.unpack_from("<i", text, i + 1)[0]
        site_va = text_va + i
        tgt = site_va + 5 + rel
        if tgt in want:
            raw_sites[tgt].append(site_va)
        i += 1

    out: Dict[int, List[Dict]] = {}
    for f, sites in raw_sites.items():
        verified: List[Dict] = []
        seen_caller = set()
        for site in sites:
            cont = ft.containing(site)
            if cont is None:
                continue  # 不在任何函数体内 -> 视为误报
            caller_begin, caller_end = cont
            if caller_begin in seen_caller and any(v["site_va"] == site for v in verified):
                continue
            seen_caller.add(caller_begin)
            # 从函数起点线性反汇编到该地址，确认那里是一条 call
            off = caller_begin - text_va
            ln = caller_end - caller_begin
            if off < 0 or off + ln > len(text):
                continue
            ok = False
            for insn in _MD.disasm(text[off : off + ln], caller_begin):
                if insn.address == site:
                    ok = insn.mnemonic.startswith("call")
                    break
                if insn.address > site:
                    break
            if not ok:
                continue
            verified.append(
                {
                    "site_va": site,
                    "site_va_hex": hex(site),
                    "caller_begin": hex(caller_begin),
                    "caller_end": hex(caller_end),
                    "caller_size": caller_end - caller_begin,
                }
            )
        out[f] = verified
    return out


# ----------------------------------------------------------------------------
# 四之二、字符串池检索 / 反汇编 / 被调用函数
# ----------------------------------------------------------------------------
def grep_strings(pe: PeImage, pattern: str, min_len: int = 4, limit: int = 4000) -> List[Dict]:
    """在非可执行区段里按正则检索 ASCII 字符串（用于找内部分类器名、UI 关键字）。"""
    rx = re.compile(pattern.encode("latin1"))
    out: List[Dict] = []
    with open(pe.path, "rb") as f:
        for s in pe.sections:
            if s["executable"] or s["raw_size"] == 0:
                continue
            f.seek(s["raw_ptr"])
            data = f.read(s["raw_size"])
            for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data):
                txt = m.group()
                if rx.search(txt):
                    va = pe.image_base + s["va"] + m.start()
                    out.append(
                        {
                            "section": s["name"],
                            "va": hex(va),
                            "file_offset": s["raw_ptr"] + m.start(),
                            "text": txt.decode("latin1"),
                        }
                    )
                    if len(out) >= limit:
                        return out
    return out


def read_at(pe: PeImage, va: int, n: int = 64) -> Optional[bytes]:
    off = pe.va_to_offset(va)
    if off is None:
        return None
    with open(pe.path, "rb") as f:
        f.seek(off)
        return f.read(n)


def cstr_at(pe: PeImage, va: int, maxlen: int = 96) -> Optional[str]:
    b = read_at(pe, va, maxlen)
    if not b:
        return None
    if not (0x20 <= b[0] <= 0x7E):
        return None
    z = b.find(b"\x00")
    if z < 0:
        return None
    s = b[:z]
    if len(s) < 3 or any(c < 0x20 or c > 0x7E for c in s):
        return None
    return s.decode("latin1")


def disasm_function(pe: PeImage, start_va: int, end_va: Optional[int], limit: int = 400) -> List[Dict]:
    """反汇编一个函数体；对 rip 相对内存操作数尝试解析成字符串，便于人工判读。"""
    text, text_va = read_sec(pe, ".text")
    if end_va is None:
        ft = FunctionTable(pe)
        cont = ft.containing(start_va)
        if cont is None:
            raise SystemExit("地址不在 .pdata 函数表内，请用 --end 显式给终点")
        end_va = cont[1]
    off = start_va - text_va
    ln = min(end_va - start_va, len(text) - off)
    rows: List[Dict] = []
    for insn in _MD.disasm(text[off : off + ln], start_va):
        note = ""
        for op in insn.operands:
            if op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP:
                eff = insn.address + insn.size + op.mem.disp
                s = cstr_at(pe, eff)
                if s:
                    note = ' ; "%s" @%s' % (s, hex(eff))
                else:
                    note = " ; -> %s" % hex(eff)
        rows.append({"va": hex(insn.address), "text": "%s %s%s" % (insn.mnemonic, insn.op_str, note)})
        if len(rows) >= limit:
            break
    return rows


def find_callees(pe: PeImage, start_va: int, end_va: Optional[int]) -> List[Dict]:
    """线性反汇编函数体，收集直接 call 目标（E8 rel32 / FF15 rip 间接调用单独标注）。"""
    text, text_va = read_sec(pe, ".text")
    ft = FunctionTable(pe)
    if end_va is None:
        cont = ft.containing(start_va)
        if cont is None:
            raise SystemExit("地址不在 .pdata 函数表内，请用 --end 显式给终点")
        end_va = cont[1]
    off = start_va - text_va
    ln = min(end_va - start_va, len(text) - off)
    out: List[Dict] = []
    for insn in _MD.disasm(text[off : off + ln], start_va):
        if insn.mnemonic.startswith("call"):
            if insn.op_str.startswith("0x"):
                tgt = int(insn.op_str, 16)
                out.append({"site": hex(insn.address), "target": hex(tgt), "kind": "direct", "in_pdata": ft.is_start(tgt)})
            else:
                note = ""
                for op in insn.operands:
                    if op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP:
                        eff = insn.address + insn.size + op.mem.disp
                        s = cstr_at(pe, eff)
                        if s:
                            note = ' -> "%s"' % s
                        else:
                            note = " -> %s" % hex(eff)
                out.append({"site": hex(insn.address), "target": insn.op_str + note, "kind": "indirect", "in_pdata": False})
    return out


# ----------------------------------------------------------------------------
# 五、命令行
# ----------------------------------------------------------------------------
def _hm(v: str) -> int:
    return int(v, 16) if v.lower().startswith("0x") else int(v)


def main() -> int:
    ap = argparse.ArgumentParser(description="低成本函数级交叉引用取证（.pdata + capstone）")
    sub = ap.add_subparsers(dest="mode", required=True)

    p_dump = sub.add_parser("dump", help="打印字节区间内的可打印字符串")
    p_dump.add_argument("--exe", required=True)
    p_dump.add_argument("--start", required=True, type=int)
    p_dump.add_argument("--end", required=True, type=int)

    p_tbl = sub.add_parser("table", help="找指向字符串的指针记录")
    p_tbl.add_argument("--exe", required=True)
    p_tbl.add_argument("--va", required=True, type=lambda x: int(x, 16))
    p_tbl.add_argument("--radius", type=int, default=64)

    p_refs = sub.add_parser("refs", help="字符串 -> 指令 -> 函数 交叉引用扫描")
    p_refs.add_argument("--exe", required=True)
    p_refs.add_argument("--needles", required=True, help="逗号分隔")
    p_refs.add_argument("--out", default="")

    p_call = sub.add_parser("callers", help="找候选函数的直接调用者")
    p_call.add_argument("--exe", required=True)
    p_call.add_argument("--funcs", required=True, help="逗号分隔的 0x 地址")

    p_vt = sub.add_parser("vtable", help="找指向某函数的指针（vtable 槽位）并解析同一张表的其他方法")
    p_vt.add_argument("--exe", required=True)
    p_vt.add_argument("--va", required=True, type=lambda x: int(x, 16))
    p_vt.add_argument("--radius", type=int, default=192)

    p_rng = sub.add_parser("range", help="列出某地址区间内的 .pdata 函数（函数边界台账）")
    p_rng.add_argument("--exe", required=True)
    p_rng.add_argument("--start", required=True, type=lambda x: int(x, 16))
    p_rng.add_argument("--end", required=True, type=lambda x: int(x, 16))

    p_grep = sub.add_parser("grepstr", help="在字符串池里按正则检索")
    p_grep.add_argument("--exe", required=True)
    p_grep.add_argument("--pattern", required=True, help="Python 正则（对 latin1 字符串）")
    p_grep.add_argument("--limit", type=int, default=4000)
    p_grep.add_argument("--out", default="")

    p_dis = sub.add_parser("disasm", help="反汇编一个函数（或显式地址区间）")
    p_dis.add_argument("--exe", required=True)
    p_dis.add_argument("--func", default="", help="函数起始 0x 地址")
    p_dis.add_argument("--start", default="", help="显式起点 0x 地址")
    p_dis.add_argument("--end", default="", help="显式终点 0x 地址")
    p_dis.add_argument("--limit", type=int, default=400)

    p_cal = sub.add_parser("callees", help="列出一个函数的直接/间接被调用目标")
    p_cal.add_argument("--exe", required=True)
    p_cal.add_argument("--func", default="")
    p_cal.add_argument("--start", default="")
    p_cal.add_argument("--end", default="")

    args = ap.parse_args()
    pe = PeImage(args.exe)

    if args.mode == "dump":
        for row in dump_strings(pe.path, args.start, args.end):
            print(row["file_offset"], row["text"])
        return 0

    if args.mode == "table":
        res = pointer_records(pe, args.va, args.radius)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.mode == "refs":
        needles = [x.strip() for x in args.needles.split(",") if x.strip()]
        # 1) 找字符串地址（ASCII）
        text_bytes, _ = None, None
        hits: Dict[int, str] = {}
        with open(pe.path, "rb") as f:
            for s in pe.sections:
                if s["executable"]:
                    continue
                f.seek(s["raw_ptr"])
                data = f.read(s["raw_size"])
                for nd in needles:
                    nb = nd.encode("latin1")
                    st = 0
                    while True:
                        k = data.find(nb, st)
                        if k < 0:
                            break
                        va = pe.image_base + s["va"] + k
                        # 同名多份时只在标签里加序号
                        label = nd if nd not in hits.values() else "%s#%x" % (nd, va & 0xFFFF)
                        hits[va] = label
                        st = k + 1
        # 2) 扫 .text
        text, text_va = read_sec(pe, ".text")
        raw_hits = scan_rip_imm_refs(text, text_va, hits)
        ft = FunctionTable(pe)
        verified = []
        for h in raw_hits:
            v = capstone_verify(text, text_va, h)
            if not v:
                continue
            cont = ft.containing(h["insn_va"])
            h.update(v)
            h["insn_va_hex"] = hex(h["insn_va"])
            h["func_begin"] = hex(cont[0]) if cont else None
            h["func_end"] = hex(cont[1]) if cont else None
            h["func_size"] = (cont[1] - cont[0]) if cont else None
            verified.append(h)

        # 3) 按函数聚合
        by_func: Dict[str, Dict] = {}
        for h in verified:
            k = h["func_begin"] or "none"
            rec = by_func.setdefault(k, {"func_begin": k, "func_size": h["func_size"], "refs": [], "labels": set()})
            rec["refs"].append({"insn_va": h["insn_va_hex"], "label": h["target_label"], "text": h["text"]})
            rec["labels"].add(h["target_label"])
        for rec in by_func.values():
            rec["labels"] = sorted(rec["labels"])
            rec["label_count"] = len(rec["labels"])

        result = {
            "exe": pe.path,
            "needles": needles,
            "string_hits": {hex(k): v for k, v in hits.items()},
            "pdata_function_count": len(ft.entries),
            "raw_ref_sites": len(raw_hits),
            "verified_ref_sites": len(verified),
            "functions": sorted(by_func.values(), key=lambda r: (-r["label_count"], r["func_begin"] or "")),
        }
        text_out = json.dumps(result, ensure_ascii=False, indent=2)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text_out)
            print("[xref] 字符串命中", len(hits), "条；.pdata 函数", len(ft.entries), "个")
            print("[xref] 机器码预筛命中", len(raw_hits), "条；capstone 校验通过", len(verified), "条")
            print("[xref] 涉及函数", len(by_func), "个；已写出", args.out)
            for rec in result["functions"][:15]:
                print("  %s size=%s labels=%s" % (rec["func_begin"], rec["func_size"], ",".join(rec["labels"])))
        else:
            print(text_out)
        return 0

    if args.mode == "callers":
        fns = [_hm(x.strip()) for x in args.funcs.split(",") if x.strip()]
        ft = FunctionTable(pe)
        res = find_direct_callers(pe, fns, ft)
        print(json.dumps({hex(k): v for k, v in res.items()}, ensure_ascii=False, indent=2))
        return 0

    if args.mode == "vtable":
        ft = FunctionTable(pe)
        needle = struct.pack("<Q", args.va)
        for s in pe.sections:
            if s["executable"] or s["raw_size"] == 0:
                continue
            with open(pe.path, "rb") as f:
                f.seek(s["raw_ptr"])
                data = f.read(s["raw_size"])
            st = 0
            while True:
                i = data.find(needle, st)
                if i < 0:
                    break
                st = i + 1
                slot_va = pe.image_base + s["va"] + i
                aligned = (i // 8) * 8
                lo = max(0, i - args.radius)
                lo = (lo // 8) * 8
                hi = min(len(data) - 8, i + args.radius)
                print("=== 引用点 %s（%s，文件偏移 %d）周边槽位 ===" % (hex(slot_va), s["name"], s["raw_ptr"] + i))
                for k in range(lo, hi + 1, 8):
                    q = struct.unpack_from("<Q", data, k)[0]
                    rec_va = pe.image_base + s["va"] + k
                    mark = " <== 目标函数" if q == args.va else ""
                    extra = ""
                    if ft.is_start(q):
                        cont = ft.containing(q)
                        extra = " [.pdata 函数 size=%d]" % (cont[1] - cont[0]) if cont else ""
                    print("  %s +0x%03x -> %s%s%s" % (hex(rec_va), k - aligned, hex(q), extra, mark))
        return 0

    if args.mode == "range":
        ft = FunctionTable(pe)
        n = 0
        for beg, end, _unw in ft.entries:
            va = pe.image_base + beg
            if args.start <= va < args.end:
                print(hex(va), "size=%d" % (end - beg))
                n += 1
        print("[range] 区间内 .pdata 函数", n, "个")
        return 0

    if args.mode == "grepstr":
        rows = grep_strings(pe, args.pattern, limit=args.limit)
        text_out = json.dumps(rows, ensure_ascii=False, indent=2)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text_out)
            print("[grepstr] 命中", len(rows), "条；已写出", args.out)
            for r in rows[:60]:
                print("  %s %s" % (r["va"], r["text"]))
        else:
            print(text_out)
        return 0

    if args.mode == "disasm":
        start = _hm(args.func or args.start)
        end = _hm(args.end) if args.end else None
        for r in disasm_function(pe, start, end, args.limit):
            print(r["va"], r["text"])
        return 0

    if args.mode == "callees":
        start = _hm(args.func or args.start)
        end = _hm(args.end) if args.end else None
        res = find_callees(pe, start, end)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
