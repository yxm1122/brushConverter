# -*- coding: utf-8 -*-
"""ABR 描述符键扫描器（只读）。

任务 22 的执行工具：扫给定目录下所有 .abr，统计 desc 区段里每个键
（含嵌套动态对象里的键）的出现次数、取值类型与取值样本，并判断仓库
解析器（src/brush_converter）是否已识别该键。

用法（一条命令同时产出 JSON 与「三个数字」摘要行）：

    python tools/scan_abr_keys.py tests "%TEMP%\brushconverter-team\abr-samples" \
        --ext-label "%TEMP%\brushconverter-team\abr-samples" \
        -o docs/数据/abr_keys.json --krita-caps docs/数据/krita_caps.json

只读保证：脚本只读取 .abr 与 src/ 源码，不写除 --out 指定的 JSON 之外的
任何文件；不修改 src/、tests/、research/。

隐私：JSON 里只记录「仓库内相对路径」或「外部样本的文件名」，不记录
绝对路径；外部样本目录在 JSON 里统一记为 --ext-label（默认 external）。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import struct
import sys
from collections import Counter, OrderedDict
from pathlib import Path

# Windows 控制台默认是 GBK，中文摘要行会乱码；显式改成 UTF-8（写文件不受影响）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# 允许直接运行：把仓库 src/ 加进 sys.path（只读导入，不修改源码）
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from brush_converter.abr import AbrFile          # noqa: E402
from brush_converter.abr import descriptors as D  # noqa: E402

# 值类型的固定展示顺序（便于 diff 稳定）
TYPE_ORDER = ["long", "doub", "bool", "TEXT", "enum", "UntF",
              "Objc", "VlLs", "tdta", "alis", "comp", "GlbO", "Glbc", "obj "]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_repo_literals(src_root: Path) -> dict[str, list[str]]:
    """扫描 src/ 下所有 .py 的字符串常量（ast，精确匹配含尾空格）。

    in_repo_parser 的判据：该键是否作为字符串字面量出现在仓库解析/映射
    代码里。用 ast 而不是正则，避免把注释、变量名误判成「已识别」。
    """
    table: dict[str, list[str]] = {}
    for py in sorted(src_root.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = py.relative_to(_REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                table.setdefault(node.value, []).append(f"{rel}:{node.lineno}")
    return table


def fmt_value(v: D.Value) -> str:
    """把一个描述符值格式化成可读样本（统一为字符串，便于下游直接展示）。"""
    if v.type == "long":
        return str(int(v.value))
    if v.type == "doub":
        f = float(v.value)
        # 保留足够精度又不丢信息：整数就写整数
        return str(int(f)) if f.is_integer() else repr(f)
    if v.type == "bool":
        return "true" if v.value else "false"
    if v.type == "TEXT":
        s = str(v.value)
        return s if len(s) <= 120 else s[:117] + "..."
    if v.type == "enum":
        return f"{v.type_id}:{v.value}"
    if v.type == "UntF":
        f = float(v.value)
        num = str(int(f)) if f.is_integer() else repr(f)
        return f"{num} {v.unit}"
    if v.type == "Objc":
        n = len(v.value) if isinstance(v.value, dict) else 0
        return f"<Objc {v.class_id} n={n}>"
    if v.type == "VlLs":
        n = len(v.value) if isinstance(v.value, list) else 0
        return f"<VlLs n={n}>"
    if v.type in ("tdta", "alis"):
        n = len(v.data or b"")
        return f"<{v.type} {n} bytes>"
    return f"<{v.type}>"


def walk_descriptor(desc: dict[str, D.Value], prefix: str, depth: int,
                    acc: list[tuple[str, str, int, D.Value]]) -> None:
    """递归遍历描述符，收集 (键, 路径模板, 深度, 值)。

    路径模板去掉数组下标，只保留结构：
        Brsh[]                  → 顶层笔刷列表
        Brsh[].opVr[]           → 预设内的动态对象
        Brsh[].Txtr[]           → 预设内的纹理对象
    """
    for key, val in desc.items():
        path = f"{prefix}{key}"
        acc.append((key, path, depth, val))
        if val.type == "Objc" and isinstance(val.value, dict):
            walk_descriptor(val.value, path + ".", depth + 1, acc)
        elif val.type == "VlLs" and isinstance(val.value, list):
            for item in val.value:
                if item.type == "Objc" and isinstance(item.value, dict):
                    walk_descriptor(item.value, path + "[].", depth + 1, acc)


def scan_one(path: Path, name: str, source: str,
             literals: dict[str, list[str]]) -> tuple[dict, list[tuple[str, str, int, D.Value]]]:
    """扫描单个 .abr，返回（文件元信息, 键出现列表）。"""
    info: dict = {
        "name": name,
        "source": source,          # repo / external
        "sha256": sha256_of(path),
        "bytes": path.stat().st_size,
        "version": None,
        "subversion": None,
        "presets": 0,
        "desc_bytes": 0,
        "keys": 0,
        "occurrences": 0,
        "parse_ok": False,
        "error": None,
        "diagnostics": [],
    }
    acc: list[tuple[str, str, int, D.Value]] = []
    try:
        abr = AbrFile.parse(str(path))
        info["version"] = abr.version
        info["subversion"] = abr.subversion
        info["diagnostics"] = list(abr.diagnostics)
        desc = abr.sections.get("desc")
        if desc is None:
            info["error"] = "无 desc 区段（v1/v2 旧格式或非预设型 ABR）"
            return info, acc
        info["desc_bytes"] = desc.length
        top = D.parse_descriptor_block(desc.data)
        # 顶层描述符本身（含 Brsh 列表）
        walk_descriptor(top, "", 0, acc)
        presets = D.iter_brush_presets(top)
        info["presets"] = len(presets)
        info["parse_ok"] = True
    except Exception as e:  # 解析失败照样记录，不中断整批扫描
        info["error"] = f"{type(e).__name__}: {e}"
        info["diagnostics"] = list(info.get("diagnostics") or [])
    info["occurrences"] = len(acc)
    info["keys"] = len({k for k, _p, _d, _v in acc})
    return info, acc


def merge_keys(per_file: list[tuple[dict, list]], literals: dict[str, list[str]],
               max_samples: int) -> list[dict]:
    """把多文件的键出现合并成每个键一条记录。"""
    counts: Counter = Counter()
    types: dict[str, Counter] = {}
    samples: dict[str, list[str]] = {}
    paths: dict[str, Counter] = {}
    depths: dict[str, set[int]] = {}
    files: dict[str, set[str]] = {}
    ranges: dict[str, list[float]] = {}
    units: dict[str, Counter] = {}
    # 枚举/数值键的实际取值分布（取值去重后 <=25 个才记录），
    # 供任务 23/31 核对「ABR 里真实出现过哪些枚举成员、各多少次」。
    value_counts: dict[str, Counter] = {}

    for info, acc in per_file:
        for key, path, depth, val in acc:
            counts[key] += 1
            types.setdefault(key, Counter())[val.type] += 1
            paths.setdefault(key, Counter())[path] += 1
            depths.setdefault(key, set()).add(depth)
            files.setdefault(key, set()).add(info["name"])
            lst = samples.setdefault(key, [])
            s = fmt_value(val)
            if s not in lst and len(lst) < max_samples:
                lst.append(s)
            if val.type in ("long", "doub", "UntF"):
                try:
                    ranges.setdefault(key, []).append(float(val.value))
                except (TypeError, ValueError):
                    pass
            if val.type in ("long", "doub", "enum"):
                value_counts.setdefault(key, Counter())[s] += 1
            if val.type == "UntF":
                units.setdefault(key, Counter())[str(val.unit)] += 1

    out = []
    for key in sorted(counts):
        tl = [t for t, _n in sorted(types[key].items(),
                                    key=lambda kv: (-kv[1], TYPE_ORDER.index(kv[0])
                                                    if kv[0] in TYPE_ORDER else 99))]
        rec: dict = {
            "key": key,
            "count": counts[key],
            # type：出现过的值类型（多个时用 | 连接；完整列表见 types）
            "type": "|".join(tl),
            "types": tl,
            "samples": samples[key],
            "in_repo_parser": key in literals,
            "repo_refs": literals.get(key, [])[:5],
            "count_files": len(files[key]),
            "paths": [f"{p} (\u00d7{n})" for p, n in sorted(paths[key].items(),
                                                            key=lambda kv: (-kv[1], kv[0]))[:10]],
            "depths": sorted(depths[key]),
        }
        if key in ranges and ranges[key]:
            rec["value_range"] = {"min": min(ranges[key]), "max": max(ranges[key])}
        if key in units:
            rec["units"] = [u for u, _n in units[key].most_common()]
        if key in value_counts and len(value_counts[key]) <= 25:
            rec["value_counts"] = {
                v: n for v, n in sorted(value_counts[key].items(), key=lambda kv: (-kv[1], kv[0]))}
        elif key in value_counts:
            rec["distinct_values"] = len(value_counts[key])
        out.append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ABR 描述符键扫描（只读）")
    ap.add_argument("dirs", nargs="+", help="要扫描的目录（递归找 *.abr）")
    ap.add_argument("-o", "--out", default="docs/数据/abr_keys.json",
                    help="输出 JSON（默认 docs/数据/abr_keys.json）")
    ap.add_argument("--repo-src", default="src/brush_converter",
                    help="仓库解析器源码目录（算 in_repo_parser，默认 src/brush_converter）")
    ap.add_argument("--krita-caps", default="docs/数据/krita_caps.json",
                    help="Krita 能力表 JSON（只为摘要里的第三个数字）")
    ap.add_argument("--ext-label", default="external",
                    help="外部样本目录在 JSON 里的别名（避免写入本机绝对路径）")
    ap.add_argument("--max-samples", type=int, default=8,
                    help="每个键最多保留多少个取值样本（默认 8）")
    args = ap.parse_args(argv)

    cwd = Path.cwd().resolve()
    literals = collect_repo_literals((cwd / args.repo_src).resolve())

    targets: list[tuple[Path, str, str]] = []
    for d in args.dirs:
        dp = Path(d).resolve()
        if not dp.is_dir():
            print(f"警告：目录不存在，跳过 {d}", file=sys.stderr)
            continue
        for p in sorted(dp.rglob("*.abr")):
            rp = p.resolve()
            try:
                rel = rp.relative_to(cwd).as_posix()
                targets.append((p, rel, "repo"))
            except ValueError:
                # 不在仓库内的样本：只记文件名，路径用 --ext-label 归一化
                targets.append((p, p.name, "external"))
    if not targets:
        print("错误：没有找到任何 .abr", file=sys.stderr)
        return 2

    per_file = []
    for p, name, source in targets:
        info, acc = scan_one(p, name, source, literals)
        per_file.append((info, acc))
        flag = "OK " if info["parse_ok"] else "FAIL"
        print(f"[{flag}] {name}  v{info['version']}.{info['subversion']}  "
              f"预设 {info['presets']}  键 {info['keys']}  出现 {info['occurrences']}"
              + ("" if info["parse_ok"] else f"  错误：{info['error']}"))

    keys = merge_keys(per_file, literals, args.max_samples)
    files_meta = [info for info, _acc in per_file]
    keys_total = len(keys)
    keys_known = sum(1 for k in keys if k["in_repo_parser"])
    occurrences = sum(k["count"] for k in keys)

    payload = OrderedDict()
    payload["schema_version"] = "1.0"
    payload["tool"] = "tools/scan_abr_keys.py"
    payload["description"] = "ABR desc 键实测集合（任务 22）。每个键含 key/count/type/samples/in_repo_parser。"
    payload["in_repo_parser_rule"] = (
        "该键是否作为字符串字面量出现在 src/brush_converter/**/*.py（ast 精确匹配，"
        "含尾空格；如 'Mnm '）。命中位置见该键的 repo_refs。")
    payload["inputs"] = {
        "dirs": [Path(d).name if Path(d).resolve().is_relative_to(cwd) else args.ext_label
                 for d in args.dirs],
        "external_label": args.ext_label if any(f["source"] == "external" for f in files_meta) else None,
        "repo_src": args.repo_src,
        "max_samples": args.max_samples,
    }
    payload["totals"] = {
        "files": len(files_meta),
        "files_repo": sum(1 for f in files_meta if f["source"] == "repo"),
        "files_external": sum(1 for f in files_meta if f["source"] == "external"),
        "files_ok": sum(1 for f in files_meta if f["parse_ok"]),
        "files_failed": sum(1 for f in files_meta if not f["parse_ok"]),
        "presets": sum(f["presets"] for f in files_meta),
        "keys_total": keys_total,
        "keys_in_repo_parser": keys_known,
        "keys_not_in_repo_parser": keys_total - keys_known,
        "occurrences": occurrences,
    }
    payload["files"] = files_meta
    payload["keys"] = keys

    out_path = (cwd / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")

    krita_n = None
    kp = (cwd / args.krita_caps).resolve()
    if kp.is_file():
        try:
            krita_n = len(json.loads(kp.read_text(encoding="utf-8"))["params"])
        except (KeyError, ValueError, TypeError):
            krita_n = None

    print()
    print(f"输出：{args.out}（{out_path.stat().st_size} 字节）")
    print(f"文件 {len(files_meta)} 个（repo {payload['totals']['files_repo']} / "
          f"external {payload['totals']['files_external']}），解析成功 "
          f"{payload['totals']['files_ok']}，预设 {payload['totals']['presets']} 个")
    print(f"ABR 实测键总数 {keys_total}（共出现 {occurrences} 次），"
          f"仓库解析器已识别 {keys_known}，未识别 {keys_total - keys_known}")
    if krita_n is None:
        print(f"Krita 能力表条目数：未读到 {args.krita_caps}")
    else:
        print(f"Krita 能力表条目数 {krita_n}")
    print(f"=== 三个数字：ABR实测键总数={keys_total}；"
          f"仓库解析器已识别={keys_known}；"
          f"Krita能力表条目数={'NA' if krita_n is None else krita_n} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
