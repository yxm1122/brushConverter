# -*- coding: utf-8 -*-
"""
pe_scan.py —— Photoshop.exe 等 PE 文件的「免反汇编」低成本取证脚本。

用途（任务 11「逆向定位候选代码路径」）：
  1. 解析 PE 头：镜像基址、区段表、导出表大小，给出「文件偏移 <-> RVA <-> VA」互转；
  2. 全文件扫描指定字符串（ASCII / UTF-16LE 两种编码），输出命中位置及其所属区段；
  3. 指针表扫描：在数据区段里找「8 字节绝对 VA 指向目标字符串」的指针（vtable / 名字表）；
  4. 指令扫描：在可执行区段里找 RIP 相对寻址指令（lea/mov reg,[rip+disp32]）中
     disp32 正好指向目标字符串的机器码位置 —— 这是「代码引用了该字符串」的硬证据。
     命中会顺带校验收尾字节是不是合法的 ModRM，降低误报率。

设计约束：
  * 只读打开目标文件，绝不写回（任务 11 硬性要求 1）；
  * 不依赖 Ghidra / 反汇编库，纯标准库 + 大文件分块，208 MB 也能在几十秒内跑完；
  * 输出 JSON（UTF-8，中文说明），便于后续 filter_candidates.py 与报告引用。

用法示例：
  python tools/re_pipeline/pe_scan.py \
      --exe "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe" \
      --needles "Linear Height,Hard Mix,Linear Dodge,Texture,Brightness,Contrast" \
      --out docs/研究报告/附件/ps_string_hits.json
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from typing import Dict, List, Optional, Tuple

# Windows 控制台默认 GBK，强制 UTF-8 输出，避免中文报错
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ----------------------------------------------------------------------------
# 一、PE 结构解析
# ----------------------------------------------------------------------------
class PeImage:
    """极简 PE32+ 解析器：只取定位所需的字段（基址、区段、导出表大小）。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.file_size = os.path.getsize(path)
        with open(path, "rb") as f:
            self.dos = f.read(0x40)
            if self.dos[:2] != b"MZ":
                raise ValueError("不是 MZ/PE 文件")
            e_lfanew = struct.unpack_from("<I", self.dos, 0x3C)[0]
            f.seek(e_lfanew)
            self.nt = f.read(0x108)
            if self.nt[:4] != b"PE\x00\x00":
                raise ValueError("缺少 PE 签名")
            coff = self.nt[4:24]
            (
                self.machine,
                self.num_sections,
                self.time_date_stamp,
                _sym_ptr,
                _num_sym,
                self.size_optional_header,
                self.characteristics,
            ) = struct.unpack("<HHIIIHH", coff)
            opt = self.nt[24 : 24 + self.size_optional_header]
            self.magic = struct.unpack_from("<H", opt, 0)[0]  # 0x20B = PE32+
            self.image_base = struct.unpack_from("<Q", opt, 24)[0] if self.magic == 0x20B else struct.unpack_from("<I", opt, 28)[0]
            self.size_of_image = struct.unpack_from("<I", opt, 56)[0]
            self.entry_point_rva = struct.unpack_from("<I", opt, 16)[0]
            # 导出表目录项（可选头偏移 112 起为 16 个 DataDirectory）
            dd_off = 112 if self.magic == 0x20B else 96
            self.export_rva, self.export_size = struct.unpack_from("<II", opt, dd_off)
            self.reloc_rva, self.reloc_size = struct.unpack_from("<II", opt, dd_off + 5 * 8)

            # 区段表
            sec_off = 24 + self.size_optional_header
            f.seek(e_lfanew + 4 + 20 + self.size_optional_header)
            sec_raw = f.read(self.num_sections * 40)
            self.sections: List[Dict] = []
            for i in range(self.num_sections):
                raw = sec_raw[i * 40 : (i + 1) * 40]
                name = raw[:8].rstrip(b"\x00").decode("latin1")
                vsize, vaddr, rsize, roff = struct.unpack_from("<IIII", raw, 8)
                chars = struct.unpack_from("<I", raw, 36)[0]
                self.sections.append(
                    {
                        "name": name,
                        "va": vaddr,
                        "vsize": vsize,
                        "raw_ptr": roff,
                        "raw_size": rsize,
                        "characteristics": chars,
                        "executable": bool(chars & 0x20000000),
                        "readable": bool(chars & 0x40000000),
                    }
                )

    # 文件偏移 -> 区段
    def section_of_offset(self, off: int) -> Optional[Dict]:
        for s in self.sections:
            if s["raw_ptr"] <= off < s["raw_ptr"] + s["raw_size"]:
                return s
        return None

    # 文件偏移 -> VA
    def offset_to_va(self, off: int) -> Optional[int]:
        s = self.section_of_offset(off)
        if s is None:
            return None
        return self.image_base + s["va"] + (off - s["raw_ptr"])

    # VA -> 文件偏移
    def va_to_offset(self, va: int) -> Optional[int]:
        rva = va - self.image_base
        for s in self.sections:
            if s["va"] <= rva < s["va"] + max(s["vsize"], s["raw_size"]):
                d = rva - s["va"]
                if d < s["raw_size"]:
                    return s["raw_ptr"] + d
                return None
        return None

    def summary(self) -> Dict:
        return {
            "path": self.path,
            "file_size": self.file_size,
            "image_base": hex(self.image_base),
            "pe_magic": hex(self.magic),
            "machine": hex(self.machine),
            "num_sections": self.num_sections,
            "size_of_image": self.size_of_image,
            "entry_point_rva": hex(self.entry_point_rva),
            "export_rva": hex(self.export_rva),
            "export_size": self.export_size,
            "sections": [
                {
                    "name": s["name"],
                    "va": hex(s["va"]),
                    "vsize": s["vsize"],
                    "raw_ptr": s["raw_ptr"],
                    "raw_size": s["raw_size"],
                    "executable": s["executable"],
                }
                for s in self.sections
            ],
        }


# ----------------------------------------------------------------------------
# 二、字符串扫描（ASCII / UTF-16LE）
# ----------------------------------------------------------------------------
def scan_needles(pe: PeImage, needles: List[str], chunk: int = 8 << 20) -> Dict[str, List[Dict]]:
    """在整个文件里以固定块扫描 needle；块之间留重叠避免跨块漏检。"""
    # 预编译各 needle 的两种编码
    pats: List[Tuple[str, str, bytes]] = []
    for n in needles:
        pats.append((n, "ascii", n.encode("latin1")))
        pats.append((n, "utf16le", n.encode("utf-16-le")))

    hits: Dict[str, List[Dict]] = {n: [] for n in needles}
    max_pat = max(len(p) for _, _, p in pats)
    overlap = max_pat + 8

    with open(pe.path, "rb") as f:
        off = 0
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            for name, enc, pat in pats:
                start = 0
                while True:
                    i = buf.find(pat, start)
                    if i < 0:
                        break
                    abs_off = off + i
                    sec = pe.section_of_offset(abs_off)
                    va = pe.offset_to_va(abs_off)
                    hits[name].append(
                        {
                            "encoding": enc,
                            "file_offset": abs_off,
                            "file_offset_hex": hex(abs_off),
                            "rva": hex(va - pe.image_base) if va is not None else None,
                            "va": hex(va) if va is not None else None,
                            "section": sec["name"] if sec else None,
                        }
                    )
                    start = i + 1
            if len(buf) < chunk:
                break
            f.seek(off + chunk - overlap)
            off = off + chunk - overlap
    return hits


# ----------------------------------------------------------------------------
# 三、数据段指针扫描：找 8 字节绝对 VA 指向目标
# ----------------------------------------------------------------------------
def scan_pointer_refs(pe: PeImage, target_va: int) -> List[Dict]:
    out: List[Dict] = []
    needle = struct.pack("<Q", target_va)
    for s in pe.sections:
        if s["raw_size"] == 0 or s["executable"]:
            continue  # 只看数据段
        with open(pe.path, "rb") as f:
            f.seek(s["raw_ptr"])
            data = f.read(s["raw_size"])
        start = 0
        while True:
            i = data.find(needle, start)
            if i < 0:
                break
            sec2 = pe.section_of_offset(s["raw_ptr"] + i)
            out.append(
                {
                    "kind": "pointer_qword",
                    "section": sec2["name"] if sec2 else s["name"],
                    "file_offset": s["raw_ptr"] + i,
                    "file_offset_hex": hex(s["raw_ptr"] + i),
                    "va": hex(pe.image_base + s["va"] + i),
                }
            )
            start = i + 1
    return out


# ----------------------------------------------------------------------------
# 四、代码段 RIP 相对寻址扫描（lea/mov reg,[rip+disp32]）
# ----------------------------------------------------------------------------
# x86-64：REX.W(0x48/0x4C) + 0x8D(lea) 或 0x8B(mov) + ModRM(0x05/0x0D/0x15/0x1D/0x25/0x2D/0x35/0x3D) + disp32
_REX_OPCODE = {0x48: "lea", 0x4C: "lea-movsxd?", 0x8B: "mov"}
_MODRM_OK = {0x05, 0x0D, 0x15, 0x1D, 0x25, 0x2D, 0x35, 0x3D}
# 指令长度：REX + opcode + ModRM + disp32 = 7 字节
_INSN_LEN = 7
_DISP_IDX = 3  # disp32 在指令内的起始偏移


def scan_rip_refs(pe: PeImage, target_va: int, max_hits: int = 200) -> List[Dict]:
    """在可执行区段里找 disp32 指向 target_va 的指令。

    对每条 7 字节指令，disp32 = target_va - (instr_va + 7)。
    我们把 disp32 当作 4 字节小端整型在代码段里全文搜索，
    每命中一次就回退 3 字节检查 REX+opcode+ModRM 是否合法。
    """
    out: List[Dict] = []
    for s in pe.sections:
        if not s["executable"] or s["raw_size"] == 0:
            continue
        with open(pe.path, "rb") as f:
            f.seek(s["raw_ptr"])
            data = f.read(s["raw_size"])
        lo = pe.image_base + s["va"]
        # 只可能从 [s.va, s.va+vsize-7] 起始
        span = len(data)
        # disp32 取值随指令地址变化，逐个候选地址生成代价高；
        # 改用「反解」：遍历所有 7 字节窗口的起始位置，直接校验 disp32。
        # 为了速度，先用 numpy 化的方式不行（无第三方依赖），改用分块 python 循环 + 快速预筛：
        #   预筛条件：data[i]=='0x48'/'0x4C' 且 data[i+1]=='0x8D'/'0x8B'
        # 这样枚举量从 100MB 降到「所有 48 8D/48 8B/4C 8D/4C 8B 位置」。
        buf = data
        n = span
        i = 0
        while True:
            # 找下一个 REX 前缀
            j48 = buf.find(b"\x48", i)
            j4c = buf.find(b"\x4c", i)
            if j48 < 0 and j4c < 0:
                break
            j = min(x for x in (j48, j4c) if x >= 0)
            i = j + 1
            if j + _INSN_LEN > n:
                break
            op = buf[j + 1]
            if op not in (0x8D, 0x8B):
                continue
            modrm = buf[j + 2]
            if modrm not in _MODRM_OK:
                continue
            disp = struct.unpack_from("<i", buf, j + 3)[0]
            insn_va = lo + j
            if insn_va + _INSN_LEN + disp == target_va:
                out.append(
                    {
                        "kind": "rip_relative",
                        "mnemonic_guess": ("lea" if op == 0x8D else "mov"),
                        "section": s["name"],
                        "file_offset": s["raw_ptr"] + j,
                        "file_offset_hex": hex(s["raw_ptr"] + j),
                        "insn_va": hex(insn_va),
                        "ref_site_rva": hex(insn_va - pe.image_base),
                        "raw_bytes": buf[j : j + _INSN_LEN].hex(),
                    }
                )
                if len(out) >= max_hits:
                    return out
    return out


# ----------------------------------------------------------------------------
# 五、命令行入口
# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="PE 低成本取证：区段表 + 字符串命中 + 指针/指令交叉引用")
    ap.add_argument("--exe", required=True, help="目标 PE 文件（只读）")
    ap.add_argument("--needles", default="", help="逗号分隔的检索字符串")
    ap.add_argument("--out", default="", help="输出 JSON 路径；留空则打印到 stdout")
    ap.add_argument("--xref", action="store_true", help="对每个命中做指针表 + RIP 相对寻址扫描（慢一些）")
    ap.add_argument("--max-xref", type=int, default=40, help="单个命中最多返回多少条交叉引用")
    args = ap.parse_args()

    pe = PeImage(args.exe)
    result: Dict = {"pe": pe.summary()}

    needles = [x for x in (n.strip() for n in args.needles.split(",")) if x]
    if needles:
        result["string_hits"] = scan_needles(pe, needles)

    if args.xref and needles:
        xrefs: Dict[str, List[Dict]] = {}
        for n in needles:
            rows: List[Dict] = []
            for h in result["string_hits"].get(n, []):
                if h["va"] is None:
                    continue
                va = int(h["va"], 16)
                rows.append({"target_va": hex(va), "encoding": h["encoding"], "refs": scan_pointer_refs(pe, va) + scan_rip_refs(pe, va, args.max_xref)})
            xrefs[n] = rows
        result["xrefs"] = xrefs

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        # 控制台只打摘要，避免刷屏
        print("[pe_scan] 目标:", args.exe)
        print("[pe_scan] 大小:", pe.file_size, "字节")
        print("[pe_scan] 区段数:", pe.num_sections, "镜像基址:", hex(pe.image_base))
        for n in needles:
            print(f"[pe_scan] 字符串 {n!r} 命中 {len(result['string_hits'].get(n, []))} 次")
        if "xrefs" in result:
            for n, rows in result["xrefs"].items():
                tot = sum(len(r["refs"]) for r in rows)
                print(f"[pe_scan] {n!r} 交叉引用合计 {tot} 条")
        print("[pe_scan] 已写出:", args.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
