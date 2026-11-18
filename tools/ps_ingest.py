# -*- coding: utf-8 -*-
"""把已有人工真值图按《基准库命名规范》改名并登记到 tests/baselines/ps/。

背景（任务 05）：Photoshop 2025 的自动化通路实测受限（COM 的 DoJavaScript 报错、
JSX 里画笔纹理的「模式/深度」未在 Action Manager 描述符中暴露），因此本工具承担
降级路线：「人工在 PS 里导出的真值图 → 按规范改名入库 + 核对」。

铁律：research/ 只读，本工具只读取不改写，复制到 tests/baselines/ps/ 后登记清单。
命名格式（见 docs/工作流程/03-像素级比对与基准库规范.md）：
    <来源>__<标识>__<模式>__<关键参数>.png
例：ps__定量测试专用__线性高度__b0_c0.5.png

用法：
    python tools/ps_ingest.py --dry-run        # 只打印计划，不落盘（默认）
    python tools/ps_ingest.py --apply          # 复制图片并写 manifest.json
    python tools/ps_ingest.py --verify         # 校验清单与文件一致性、命名合规
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

# 仓库根目录（本文件位于 <repo>/tools/ 下）
REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = REPO_ROOT / "tests" / "baselines" / "ps"
MANIFEST_PATH = BASELINE_DIR / "manifest.json"
RESEARCH_DIR = REPO_ROOT / "research"

# 允许的模式名（任务书四个组合 + 历史数据里出现过的变暗/线性加深）
KNOWN_MODES = ("减去", "正片叠底", "高度", "线性高度", "变暗", "线性加深", "叠加", "柔光", "强光")

# 历史数据来源 → 标识名（命名规范里的「笔刷或参数标识」字段）
SOURCE_IDENT = {
    "测试结果": "定量测试v1",
    "测试结果2": "定量测试v2",
}


def _parse_int(text: str) -> int | None:
    """严格解析带符号整数（不接受空串、小数、空白）。"""
    if not text:
        return None
    body = text[1:] if text[0] in "+-" else text
    if not body.isdigit():
        return None
    return int(text)


def parse_origin(rel: Path) -> dict | None:
    """把 research/<集合>/<模式_d深度>/<bX_cY>.png 解析成结构化参数。

    不用正则：历史数据的文件名存在 'b0_-25.png' 这类非常规写法（对比度写成负数
    直接接在 b 之后），逐段字符串切分更直观也更好排错。
    """
    parts = rel.parts
    if len(parts) != 3:
        return None
    collection, dirname, filename = parts
    # 目录名：<模式>_d<深度>
    if "_d" not in dirname:
        return None
    mode, sep, depth_text = dirname.rpartition("_d")
    if not sep or not mode or not depth_text.isdigit():
        return None
    if mode not in KNOWN_MODES:
        return None
    # 文件名：b<亮度>_c<对比度>.png（兼容 b0_-25.png 这种把对比度前导符号写全的）
    if not filename.lower().endswith(".png"):
        return None
    stem = filename[:-4]
    if "_" not in stem:
        return None
    b_part, sep2, c_part = stem.rpartition("_")
    if not sep2 or not b_part.startswith("b"):
        return None
    # 常规写法 b-50_c25：c_part 是 "c25"；非常规写法 b0_-25：c_part 是 "-25"
    if c_part.startswith("c"):
        c_text = c_part[1:]
    else:
        c_text = c_part
    brightness = _parse_int(b_part[1:])
    contrast = _parse_int(c_text)
    if brightness is None or contrast is None:
        return None
    return {
        "collection": collection,
        "ident": SOURCE_IDENT.get(collection, collection),
        "mode": mode,
        "depth": int(depth_text),
        "brightness": brightness,
        "contrast": contrast,
    }


def alpha_stats(path: Path) -> dict:
    """统计 Alpha 通道（本项目比对只看 Alpha：PS 导出的真值图 RGB 全黑）。"""
    with Image.open(path) as im:
        arr = np.array(im.convert("RGBA"))
    a = arr[:, :, 3]
    hist = np.bincount(a.ravel(), minlength=256)
    dominant = int(np.argmax(hist))
    return {
        "width": int(a.shape[1]),
        "height": int(a.shape[0]),
        "nonzero_pixels": int(np.count_nonzero(a)),
        "total_pixels": int(a.size),
        "alpha_mean": round(float(a.mean()), 2),
        "alpha_min": int(a.min()),
        "alpha_max": int(a.max()),
        "alpha_unique": int(np.count_nonzero(hist)),
        "alpha_dominant": dominant,
        "rgb_all_black": bool(np.all(arr[:, :, :3] == 0)),
    }


def target_name(meta: dict) -> str:
    """生成符合命名规范的基准图文件名。"""
    params = f"d{meta['depth']}_b{meta['brightness']}_c{meta['contrast']}"
    return f"ps__{meta['ident']}__{meta['mode']}__{params}.png"


def collect() -> list[dict]:
    """扫描 research/ 下两套历史真值图，生成入库计划。"""
    plan: list[dict] = []
    seen: dict[str, str] = {}
    for collection in ("测试结果", "测试结果2"):
        root = RESEARCH_DIR / collection
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/*.png")):
            rel = path.relative_to(RESEARCH_DIR)
            meta = parse_origin(rel)
            if meta is None:
                print(f"[跳过] 命名不符合历史规律：{rel.as_posix()}")
                continue
            name = target_name(meta)
            if name in seen:
                print(f"[冲突] {rel.as_posix()} 与 {seen[name]} 映射到同名 {name}，跳过")
                continue
            seen[name] = rel.as_posix()
            plan.append({"origin": rel.as_posix(), "origin_abs": path, "target": name, "meta": meta})
    return plan


def build_note(item: dict, stats: dict) -> str:
    """清单 note 字段：写清来源、只读引用、Alpha 统计。"""
    m = item["meta"]
    return (
        f"人工在 Photoshop 导出（{m['collection']} 历史真值，只读引用），"
        f"模式={m['mode']} 深度={m['depth']} 亮度={m['brightness']} 对比度={m['contrast']}；"
        f"{stats['width']}x{stats['height']} RGBA；Alpha 非零 {stats['nonzero_pixels']}/{stats['total_pixels']}，"
        f"均值 {stats['alpha_mean']}，取值种数 {stats['alpha_unique']}；原文件 research/{item['origin']}"
    )


def do_ingest(apply: bool) -> int:
    """执行入库：复制文件 + 写 tests/baselines/ps/manifest.json。"""
    plan = collect()
    if not plan:
        print("未找到可入库的历史真值图")
        return 1
    entries = []
    for item in plan:
        stats = alpha_stats(item["origin_abs"])
        entries.append(
            {
                "file": item["target"],
                "source": "ps",
                "size": [stats["width"], stats["height"]],
                "note": build_note(item, stats),
                "mode": item["meta"]["mode"],
                "depth": item["meta"]["depth"],
                "brightness": item["meta"]["brightness"],
                "contrast": item["meta"]["contrast"],
                "alpha_stats": {
                    "nonzero_pixels": stats["nonzero_pixels"],
                    "total_pixels": stats["total_pixels"],
                    "alpha_mean": stats["alpha_mean"],
                    "alpha_min": stats["alpha_min"],
                    "alpha_max": stats["alpha_max"],
                    "alpha_unique": stats["alpha_unique"],
                },
                "origin": f"research/{item['origin']}",
                "provenance": "human_manual",
            }
        )
        if apply:
            BASELINE_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item["origin_abs"], BASELINE_DIR / item["target"])
        else:
            print(f"[计划] research/{item['origin']} -> tests/baselines/ps/{item['target']}")
    if not apply:
        print(f"[dry-run] 共 {len(entries)} 张待入库；加 --apply 才会真正复制并写清单")
        return 0
    manifest = {
        "schema": "brushconverter.baseline.ps/v1",
        "generated_by": "tools/ps_ingest.py",
        "naming": "<来源>__<标识>__<模式>__<关键参数>.png",
        "note": "本清单是 tests/baselines/ps/ 子目录清单；顶层 tests/baselines/manifest.json 由任务 09 合并，本工具不写顶层。",
        "count": len(entries),
        "images": entries,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[完成] 复制 {len(entries)} 张 -> {BASELINE_DIR}；清单 {MANIFEST_PATH}（{len(entries)} 条）")
    return 0


def check_name(name: str) -> bool:
    """结构化校验命名：<来源>__<标识>__<模式>__<参数>.png（四段、双下划线分隔）。"""
    if not name.lower().endswith(".png"):
        return False
    stem = name[:-4]
    fields = stem.split("__")
    if len(fields) != 4:
        return False
    source, ident, mode, params = fields
    return (
        source == "ps"
        and ident != ""
        and mode in KNOWN_MODES
        and params.startswith("d")
        and "_b" in params
        and "_c" in params
    )


def do_verify() -> int:
    """校验：清单存在、文件齐全、命名合规、尺寸一致、Alpha 非全 0。"""
    problems: list[str] = []
    if not MANIFEST_PATH.is_file():
        print(f"[失败] 清单不存在：{MANIFEST_PATH}")
        return 1
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    images = manifest.get("images", [])
    if len(images) < 12:
        problems.append(f"清单条目只有 {len(images)} 张，少于任务要求的 12 张")
    for entry in images:
        name = entry.get("file", "")
        path = BASELINE_DIR / name
        if not check_name(name):
            problems.append(f"命名不合规：{name}")
        if not path.is_file():
            problems.append(f"文件缺失：{name}")
            continue
        stats = alpha_stats(path)
        if stats["nonzero_pixels"] == 0:
            problems.append(f"Alpha 全 0（不能当基准）：{name}")
        if [stats["width"], stats["height"]] != list(entry.get("size", [])):
            problems.append(f"尺寸与清单不一致：{name} 实际 {stats['width']}x{stats['height']}，清单 {entry.get('size')}")
    # 目录里有没有未登记的多余图
    on_disk = {p.name for p in BASELINE_DIR.glob("*.png")}
    registered = {e.get("file") for e in images}
    extra = on_disk - registered
    if extra:
        problems.append(f"目录里有 {len(extra)} 张未登记的图：{sorted(extra)[:5]}")
    if problems:
        print("[失败] 校验发现问题：")
        for p in problems:
            print("  -", p)
        return 1
    print(f"[通过] 清单 {len(images)} 张，文件齐全、命名合规、Alpha 均非全 0；目录中无未登记图片")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把人工真值图按规范改名并登记进 PS 基准库")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="只打印入库计划（默认行为）")
    group.add_argument("--apply", action="store_true", help="真正复制文件并写 manifest.json")
    group.add_argument("--verify", action="store_true", help="校验清单与文件的一致性")
    args = parser.parse_args()
    if args.verify:
        return do_verify()
    return do_ingest(apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
