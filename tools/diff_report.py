# -*- coding: utf-8 -*-
"""差异定位报告：把「一对图 + 已知参数」变成一条可读的定位记录（任务 08）。

职责
----
1. 对齐：调用 tools/align.py 的 align_translate()，只做平移；
2. 比对：复用 tools/imgdiff.py 的 diff_alpha()（只比 Alpha、tol=2、分母 W*H）；
3. 判定：按写死的三档阈值给 verdict（ok / suspect / fail）；
4. 定位：把差异归到「哪支笔 / 哪个模式 / 哪个深度 / 哪组亮度对比度」，
   并给出 PS 图与 Krita 图的配对（四元组：模式 + 深度 + 亮度 + 对比度）。

判定阈值（写死，不许私下改）
----------------------------
* after <= 2%        -> "ok"
* 2% < after <= 10%  -> "suspect"
* after > 10%        -> "fail"
阈值常量 THRESHOLD_OK / THRESHOLD_SUSPECT 是后续任务 09/15 的判据，
要改必须先改常量并在报告里写清理由。

定位记录字段（固定，RECORD_FIELDS 是唯一权威）
----------------------------------------------
任务书要求的 12 个字段：
    pair, brush, mode, depth, brightness, contrast,
    dx, dy, diff_ratio_before, diff_ratio_after, max_delta, verdict
其中 brightness/contrast 记的是 **Krita 侧参数**（这是「设了这组 Krita 参数后
和 PS 真值差多少」的定位口径）；PS 侧原始亮度/对比度另记在
ps_brightness / ps_contrast 两个附加字段里，避免把两套单位混进一个字段。

附加字段（同样固定，不随调用变化）：
    ps_brightness, ps_contrast, clipped_pixels, method,
    max_delta_before, diff_bbox_after, shift_limited

命令行用法
----------
    python tools/diff_report.py --pairs                # 列出全部可配对组与配对明细
    python tools/diff_report.py --markdown             # 生成定位表（Markdown）
    python tools/diff_report.py --json                 # 生成定位记录（JSON）
    python tools/diff_report.py --markdown --method centroid   # 换质心法
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # 允许直接用 python tools/diff_report.py 跑
    sys.path.insert(0, str(REPO_ROOT))

from tools.align import align_translate, diff_alpha_arrays, to_alpha  # noqa: E402
from tools.imgdiff import DEFAULT_RATIO_THRESHOLD, DEFAULT_TOL, diff_alpha  # noqa: E402
from tools.kpp_params import close as values_close  # noqa: E402

# ---------------------------------------------------------------------------
# 一、判据常量（写死）
# ---------------------------------------------------------------------------

#: 对齐后差异比例 <= 2% 判 ok（阈值统一取自 tools/imgdiff.py，不再重复写死 0.02）
THRESHOLD_OK = DEFAULT_RATIO_THRESHOLD
#: 对齐后差异比例 <= 10% 判 suspect（超过 10% 判 fail）
THRESHOLD_SUSPECT = 0.10

#: 默认最多允许平移多少像素（防止「过度收敛」把无关图拉到相似位置）
DEFAULT_MAX_SHIFT = 10
#: 默认对齐方法（任务书默认 bbox）
DEFAULT_METHOD = "bbox"

#: 定位记录必须包含的 12 个字段（顺序即输出顺序）
REQUIRED_FIELDS = (
    "pair",
    "brush",
    "mode",
    "depth",
    "brightness",
    "contrast",
    "dx",
    "dy",
    "diff_ratio_before",
    "diff_ratio_after",
    "max_delta",
    "verdict",
)

#: 附加字段（固定集合；含义见模块 docstring）
EXTRA_FIELDS = (
    "ps_brightness",
    "ps_contrast",
    "clipped_pixels",
    "method",
    "max_delta_before",
    "diff_bbox_after",
    "shift_limited",
)

#: 一条定位记录的完整字段表
RECORD_FIELDS = REQUIRED_FIELDS + EXTRA_FIELDS

VERDICT_CN = {"ok": "合格", "suspect": "可疑", "fail": "不合格"}


def classify_verdict(diff_ratio_after: float) -> str:
    """按写死的三档阈值判定：ok / suspect / fail（判的是**对齐后**的比例）。"""
    ratio = float(diff_ratio_after)
    if ratio <= THRESHOLD_OK:
        return "ok"
    if ratio <= THRESHOLD_SUSPECT:
        return "suspect"
    return "fail"


def validate_record(record: dict) -> dict:
    """校验一条记录字段齐全，缺字段直接抛错（防止下游拿到半条记录）。"""
    missing = [name for name in RECORD_FIELDS if name not in record]
    if missing:
        raise ValueError(f"定位记录缺字段：{missing}")
    return record


# ---------------------------------------------------------------------------
# 二、PS 参数 -> Krita 参数（与 src/brush_converter/convert.py 的换算一致）
# ---------------------------------------------------------------------------

def ps_to_krita_params(ps_brightness: float, ps_contrast: float) -> tuple[float, float]:
    """把 PS 纹理亮度/对比度换算成 Krita 侧参数（只用于**配对**，不改 src/）。

    本函数逐行复刻 src/brush_converter/convert.py::_texture_xml 的换算
    （PS 亮度 -150..150、对比度 -50..100；Krita 亮度 -1..1、对比度 0..2）：

        Brightness = round(0.10 - ps_b / 250, 2)      # clamp [-1, 1]
        Contrast   = round(factor, 2)                 # clamp [0, 2]
        factor = 1 + ps_c/100           (ps_c < 0)
               = 1 / (1 - ps_c/100)     (0 <= ps_c < 100)
               = 1e6                    (ps_c >= 100，逼近二值化后夹到 2)

    为什么要在本文件里留一份：本任务不许改 src/，也不该依赖一个带
    BrushPreset 入参的私有函数；若 src 的公式变了，配对表会立刻对不上
    （见 docs/比对与定位说明.md 的遗留第 3 条）。
    """
    brightness = 0.10 - float(ps_brightness) / 250.0
    brightness = max(-1.0, min(1.0, round(brightness, 2)))
    if brightness == 0:  # 避免 -0.0 出现在记录里
        brightness = 0.0
    c = max(-50.0, min(100.0, float(ps_contrast)))
    if c < 0.0:
        factor = 1.0 + c / 100.0
    elif c >= 100.0:
        factor = 1_000_000.0
    else:
        factor = 1.0 / (1.0 - c / 100.0)
    contrast = max(0.0, min(2.0, round(factor, 2)))
    return brightness, contrast


# ---------------------------------------------------------------------------
# 三、文件名解析与基准清单配对（四元组：模式 + 深度 + 亮度 + 对比度）
# ---------------------------------------------------------------------------

# 关键参数段里的 d/b/c 三个数值（PS: d100_b0_c50；Krita: b0_c0.5）
_PARAM_RE = re.compile(r"(d|b|c)(-?\d+(?:\.\d+)?)")
# 标识段里的深度（Krita: hist_d100）
_IDENT_DEPTH_RE = re.compile(r"d(\d+)")


def parse_baseline_name(filename: str) -> dict | None:
    """从基准图文件名解析四元组；不符合命名规范（不是 4 段）时返回 None。

    命名规范见 docs/基准库命名规范.md：
        <来源>__<标识>__<模式>__<关键参数>.png
    PS 的深度写在关键参数段里（d100_b0_c50），Krita 的深度写在标识段里
    （hist_d100）；两种写法这里都支持。
    """
    stem = filename[:-4] if filename.lower().endswith(".png") else filename
    parts = stem.split("__")
    if len(parts) != 4:
        return None
    source, ident, mode, params = parts
    values = {key: float(num) for key, num in _PARAM_RE.findall(params)}
    depth = None
    if "d" in values:
        depth = int(round(values["d"]))
    else:
        match = _IDENT_DEPTH_RE.search(ident)
        if match:
            depth = int(match.group(1))
    if depth is None or "b" not in values or "c" not in values:
        return None
    return {
        "source": source,
        "ident": ident,
        "mode": mode,
        "depth": depth,
        "brightness": values["b"],
        "contrast": values["c"],
    }


def _normalize_depth(value) -> int:
    """把 manifest 里的深度统一成整数。

    PS 子清单存的是 int（60/100），Krita 子清单存的是字符串 "d100"（历史登记口径），
    两种写法都要认，否则配对时 int("d100") 会直接崩。
    """
    if isinstance(value, str):
        match = _IDENT_DEPTH_RE.search(value)
        if not match:
            raise ValueError(f"无法从深度字段解析出数字：{value!r}")
        return int(match.group(1))
    return int(value)


def load_baseline_entries(manifest_path) -> dict:
    """读一份子目录 manifest，返回 {"entries": [...], "skipped": [...]}。

    每条 entry：{file, path, source, ident, mode, depth, brightness, contrast, parsed}
    manifest 字段优先，文件名解析值放 parsed 里供一致性核对。

    **容错是硬要求**：tests/baselines/ 由多个并行任务持续写入（任务 06 的真实渲染
    结果没有纹理亮度/对比度，文件名是 krita__<笔刷>__plain__s200x200_p1.0.png，
    不是四元组；登记过程中也可能短暂出现 file=null 或图片文件还没落盘的条目）。
    这些条目一律进 skipped 并写明原因，**不许让配对流程崩掉**，也不许猜参数。
    """
    manifest_path = Path(manifest_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw = data.get("images") or data.get("items") or []
    directory = manifest_path.parent
    entries = []
    skipped = []
    for item in raw:
        if not isinstance(item, dict):
            skipped.append({"file": None, "reason": "登记项不是对象"})
            continue
        filename = item.get("file")
        if not isinstance(filename, str) or not filename:
            skipped.append({"file": None, "reason": "登记项缺 file 字段（可能正在被并行任务写入）"})
            continue
        parsed = parse_baseline_name(filename)
        if parsed is None:
            skipped.append({"file": filename, "reason": "文件名不是「来源__标识__模式__参数」四段"})
            continue
        path = directory / filename
        if not path.exists():
            skipped.append({"file": filename, "reason": "图片文件不在磁盘上"})
            continue
        entries.append(
            {
                "file": filename,
                "path": path,
                "source": parsed["source"],
                "ident": parsed["ident"],
                "mode": item.get("mode") or parsed["mode"],
                "depth": _normalize_depth(item.get("depth", parsed["depth"])),
                "brightness": float(item.get("brightness", parsed["brightness"])),
                "contrast": float(item.get("contrast", parsed["contrast"])),
                "parsed": parsed,
            }
        )
    return {"entries": entries, "skipped": skipped}


def check_name_manifest_consistency(
    entries: list[dict], skipped: list[dict] | None = None
) -> list[str]:
    """核对「文件名解析出的四元组」与 manifest 登记字段是否一致，返回不一致描述列表。

    skipped 里的条目（非四元组命名、缺文件等）也会列出来，但标注为「跳过」而不是
    「不一致」——它们本来就不参与纹理参数比对（例如任务 06 的真实渲染结果）。
    """
    problems = []
    for item in skipped or []:
        problems.append(f"[跳过] {item['file']}：{item['reason']}")
    problems = []
    for entry in entries:
        parsed = entry["parsed"]
        if parsed["mode"] != entry["mode"]:
            problems.append(f"{entry['file']}：模式 文件名={parsed['mode']} manifest={entry['mode']}")
        if parsed["depth"] != entry["depth"]:
            problems.append(
                f"{entry['file']}：深度 文件名={parsed['depth']} manifest={entry['depth']}"
            )
        if not values_close(parsed["brightness"], entry["brightness"], 1e-6):
            problems.append(
                f"{entry['file']}：亮度 文件名={parsed['brightness']} manifest={entry['brightness']}"
            )
        if not values_close(parsed["contrast"], entry["contrast"], 1e-6):
            problems.append(
                f"{entry['file']}：对比度 文件名={parsed['contrast']} manifest={entry['contrast']}"
            )
    return problems


#: 允许参与 PS<->Krita 配对的 PS 标识：必须与 Krita 真值图用同一张纹理。
#: 依据 docs/PS真值自动化说明.md 第 185 条：自动纹理A 用的是另一张 64×64 噪点纹理，
#: 与旧人工真值图逐像素比对没有意义；而 research/测试结果2 与 research/krita结果
#: 都用 research/测试用纹理.png（见 docs/纹理掩码复刻说明.md L117-119）。
PREFERRED_PS_IDENTS = ("定量测试v1", "定量测试v2")


def pair_baselines(
    ps_manifest,
    krita_manifest,
    *,
    tol_brightness: float = 0.005,
    tol_contrast: float = 0.005,
    preferred_idents: tuple[str, ...] = PREFERRED_PS_IDENTS,
) -> dict:
    """按「模式 + 深度 + 亮度 + 对比度」四元组配对 PS 与 Krita 基准图。

    配对规则（写死）：
    1. 模式名相同、深度相同；
    2. 把 PS 的亮度/对比度按 ps_to_krita_params() 换算到 Krita 域后，
       与 Krita 侧的亮度/对比度相等（容差 0.005，远小于网格间距）；
    3. PS 侧只允许 PREFERRED_PS_IDENTS 里的标识（同源纹理），否则会拿两张
       不同纹理的图去比，差异全是噪声（会污染定位结论）。

    返回 {"pairs": [...], "groups": {...}, "unmatched": [...]}。
    """
    ps_loaded = load_baseline_entries(ps_manifest)
    krita_loaded = load_baseline_entries(krita_manifest)
    ps_entries = ps_loaded["entries"]
    krita_entries = krita_loaded["entries"]
    skipped = [{"manifest": str(ps_manifest), **item} for item in ps_loaded["skipped"]] + [
        {"manifest": str(krita_manifest), **item} for item in krita_loaded["skipped"]
    ]

    groups: dict[tuple[str, int], dict] = {}

    def _slot(mode: str, depth: int) -> dict:
        return groups.setdefault(
            (mode, depth), {"ps_count": 0, "krita_count": 0, "pair_count": 0}
        )

    for entry in ps_entries:
        _slot(entry["mode"], entry["depth"])["ps_count"] += 1
    for entry in krita_entries:
        _slot(entry["mode"], entry["depth"])["krita_count"] += 1

    pairs = []
    unmatched = []
    for krita in sorted(krita_entries, key=lambda item: item["file"]):
        candidates = []
        for ps in ps_entries:
            if ps["mode"] != krita["mode"] or ps["depth"] != krita["depth"]:
                continue
            if ps["ident"] not in preferred_idents:
                continue
            kb, kc = ps_to_krita_params(ps["brightness"], ps["contrast"])
            if values_close(kb, krita["brightness"], tol_brightness) and values_close(
                kc, krita["contrast"], tol_contrast
            ):
                candidates.append((ps, kb, kc))
        if not candidates:
            unmatched.append(
                {
                    "krita": krita["file"],
                    "group": f"{krita['mode']}_d{krita['depth']}",
                    "reason": "同模式同深度下没有换算后参数相等的 PS 图",
                }
            )
            continue
        # 多个候选时取「换算后与 Krita 值偏差最小」的一张（通常是未触发 clamp 的那张）
        candidates.sort(
            key=lambda item: abs(item[1] - krita["brightness"]) + abs(item[2] - krita["contrast"])
        )
        ps, kb, kc = candidates[0]
        pairs.append(
            {
                "ps": ps,
                "krita": krita,
                "mode": krita["mode"],
                "depth": krita["depth"],
                "ps_brightness": ps["brightness"],
                "ps_contrast": ps["contrast"],
                "krita_brightness": krita["brightness"],
                "krita_contrast": krita["contrast"],
                "brush": ps["ident"],
                "group": f"{krita['mode']}_d{krita['depth']}",
                "krita_to_ps": (kb, kc),
            }
        )
        _slot(krita["mode"], krita["depth"])["pair_count"] += 1

    return {"pairs": pairs, "groups": groups, "unmatched": unmatched, "skipped": skipped}


# ---------------------------------------------------------------------------
# 四、构建定位记录
# ---------------------------------------------------------------------------

def _write_aligned_png(alpha: np.ndarray, directory: Path, name: str) -> Path:
    """把对齐后的 Alpha 存成 RGBA PNG（RGB 全 0），供 imgdiff.diff_alpha 读取。"""
    data = np.zeros((*alpha.shape, 4), dtype=np.uint8)
    data[:, :, 3] = alpha
    path = directory / name
    Image.fromarray(data, mode="RGBA").save(path, format="PNG")
    return path


def build_record(
    path_a,
    path_b,
    *,
    brush: str = "none",
    mode: str = "none",
    depth=0,
    brightness=0.0,
    contrast=0.0,
    method: str = DEFAULT_METHOD,
    tol: int = DEFAULT_TOL,
    max_shift: int | None = DEFAULT_MAX_SHIFT,
    ps_brightness=None,
    ps_contrast=None,
    pair_label: str | None = None,
) -> dict:
    """对齐 -> 比对 -> 判定，返回一条固定字段的定位记录。

    * path_a：参考图（PS 真值），path_b：待对齐图（Krita 侧）；
    * brush/mode/depth/brightness/contrast：定位用的已知参数
      （brightness/contrast 记 Krita 侧参数，PS 侧原始值放 ps_brightness/ps_contrast）；
    * 差异比例 before/after 都走 imgdiff.diff_alpha()，字面复用任务 03 的口径；
      after 用「对齐后的 b」临时落盘成 PNG 再比，确保和被验收的比对器同口径。
    """
    path_a = Path(path_a)
    path_b = Path(path_b)
    before = diff_alpha(path_a, path_b, tol=tol)
    b_aligned, (dx, dy), meta = align_translate(
        path_a, path_b, tol=tol, method=method, max_shift=max_shift
    )
    with tempfile.TemporaryDirectory(prefix="brushconv_align_") as tmpdir:
        aligned_path = _write_aligned_png(b_aligned, Path(tmpdir), "aligned.png")
        after = diff_alpha(path_a, aligned_path, tol=tol)
        # 交叉校验：数组口径与落盘口径必须完全一致，否则说明两套口径漂移了
        alpha_a = to_alpha(path_a)  # 统一走 align.to_alpha，不再内联一份 RGBA→Alpha
        array_after = diff_alpha_arrays(alpha_a, b_aligned, tol=tol)
        if (
            array_after["diff_pixels"] != after["diff_pixels"]
            or array_after["max_delta"] != after["max_delta"]
        ):
            raise AssertionError(
                "对齐后差异在「数组口径」与「imgdiff 落盘口径」下不一致："
                f"数组={array_after}，imgdiff={after}"
            )

    record = {
        "pair": pair_label if pair_label is not None else f"{path_a.name} ↔ {path_b.name}",
        "brush": brush,
        "mode": mode,
        "depth": int(depth),
        "brightness": float(brightness),
        "contrast": float(contrast),
        "dx": int(dx),
        "dy": int(dy),
        "diff_ratio_before": float(before["diff_ratio"]),
        "diff_ratio_after": float(after["diff_ratio"]),
        "max_delta": int(after["max_delta"]),
        "verdict": classify_verdict(after["diff_ratio"]),
        "ps_brightness": None if ps_brightness is None else float(ps_brightness),
        "ps_contrast": None if ps_contrast is None else float(ps_contrast),
        "clipped_pixels": int(meta["clipped_pixels"]),
        "method": method,
        "max_delta_before": int(before["max_delta"]),
        "diff_bbox_after": meta["diff_bbox_after"],
        "shift_limited": bool(meta["shift_limited"]),
    }
    return validate_record(record)


def build_records_for_pairs(
    pair_result: dict,
    *,
    method: str = DEFAULT_METHOD,
    tol: int = DEFAULT_TOL,
    max_shift: int | None = DEFAULT_MAX_SHIFT,
) -> list[dict]:
    """把 pair_baselines() 的配对结果逐对跑成定位记录。"""
    records = []
    for pair in pair_result["pairs"]:
        records.append(
            build_record(
                pair["ps"]["path"],
                pair["krita"]["path"],
                brush=pair["brush"],
                mode=pair["mode"],
                depth=pair["depth"],
                brightness=pair["krita_brightness"],
                contrast=pair["krita_contrast"],
                method=method,
                tol=tol,
                max_shift=max_shift,
                ps_brightness=pair["ps_brightness"],
                ps_contrast=pair["ps_contrast"],
            )
        )
    return records


# ---------------------------------------------------------------------------
# 五、渲染（Markdown）
# ---------------------------------------------------------------------------

def _fmt_ratio(value: float) -> str:
    """差异比例统一显示成百分比两位小数（0.2236 -> 22.36%）。"""
    return f"{value * 100:.2f}%"


def _fmt_num(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def render_pair_table(pair_result: dict) -> str:
    """渲染「配对规则 + 全部可配对组 + 配对明细」的 Markdown。"""
    lines = []
    lines.append("### 配对规则")
    lines.append("")
    lines.append(
        "四元组 = 模式 + 深度 + 亮度 + 对比度；PS 参数先按 ps_to_krita_params() 换算到 Krita 域"
        "（与 src/brush_converter/convert.py 一致），与 Krita 侧参数相等（容差 0.005）才算一对。"
    )
    lines.append("")
    lines.append("### 可配对组（同模式 + 同深度，两侧都有图）")
    lines.append("")
    lines.append("| 组（模式_深度） | PS 图数 | Krita 图数 | 配成对数 |")
    lines.append("|---|---|---|---|")
    for (mode, depth), stats in sorted(pair_result["groups"].items()):
        if stats["ps_count"] == 0 or stats["krita_count"] == 0:
            continue
        lines.append(
            f"| {mode}_d{depth} | {stats['ps_count']} | {stats['krita_count']} | {stats['pair_count']} |"
        )
    lines.append("")
    lines.append("### 配对明细")
    lines.append("")
    lines.append("| 组 | PS 文件（b, c） | Krita 文件（B, C） | 换算后 Kb, Kc |")
    lines.append("|---|---|---|---|")
    for pair in pair_result["pairs"]:
        kb, kc = pair["krita_to_ps"]
        lines.append(
            f"| {pair['group']} | {pair['ps']['file']}（{_fmt_num(pair['ps_brightness'])}, "
            f"{_fmt_num(pair['ps_contrast'])}） | {pair['krita']['file']}（"
            f"{_fmt_num(pair['krita_brightness'])}, {_fmt_num(pair['krita_contrast'])}） | "
            f"{kb:g}, {kc:g} |"
        )
    lines.append("")
    lines.append("### 未配到 PS 图的 Krita 图")
    lines.append("")
    if pair_result["unmatched"]:
        lines.append("| Krita 文件 | 组 | 原因 |")
        lines.append("|---|---|---|")
        for item in pair_result["unmatched"]:
            lines.append(f"| {item['krita']} | {item['group']} | {item['reason']} |")
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("### 未参与配对的登记项（非四元组命名 / 缺文件，按规则跳过）")
    lines.append("")
    skipped = pair_result.get("skipped") or []
    if skipped:
        lines.append("| manifest | 登记文件 | 跳过原因 |")
        lines.append("|---|---|---|")
        for item in skipped:
            manifest_name = Path(item["manifest"]).parent.name
            lines.append(f"| {manifest_name} | {item['file']} | {item['reason']} |")
    else:
        lines.append("（无）")
    lines.append("")
    return "\n".join(lines)


def render_records_markdown(records: list[dict]) -> str:
    """渲染定位表：每行同时给对齐前/后差异比例、最大差值、verdict。"""
    lines = []
    lines.append(
        "| # | 组 | 笔刷/标识 | Krita（B, C） | PS（b, c） | 平移 (dx,dy) | 越界像素 | "
        "对齐前差异 | 对齐后差异 | 最大差值 | 差异外接框 | verdict |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for index, record in enumerate(records, start=1):
        bbox = record["diff_bbox_after"]
        bbox_text = "—" if bbox is None else f"{bbox['x']},{bbox['y']} {bbox['width']}×{bbox['height']}"
        shift = f"({record['dx']},{record['dy']})"
        if record["shift_limited"]:
            shift += "（超上限未平）"
        lines.append(
            f"| {index} | {record['mode']}_d{record['depth']} | {record['brush']} | "
            f"({_fmt_num(record['brightness'])}, {_fmt_num(record['contrast'])}) | "
            f"({_fmt_num(record['ps_brightness'])}, {_fmt_num(record['ps_contrast'])}) | "
            f"{shift} | {record['clipped_pixels']} | "
            f"{_fmt_ratio(record['diff_ratio_before'])} | {_fmt_ratio(record['diff_ratio_after'])} | "
            f"{record['max_delta']} | {bbox_text} | {record['verdict']} |"
        )
    return "\n".join(lines)


def render_summary(records: list[dict]) -> str:
    """按 verdict 汇总，给出合格/可疑/不合格张数（供报告直接引用）。"""
    counts = {"ok": 0, "suspect": 0, "fail": 0}
    for record in records:
        counts[record["verdict"]] += 1
    before_best = min((record["diff_ratio_before"] for record in records), default=0.0)
    after_best = min((record["diff_ratio_after"] for record in records), default=0.0)
    improved = sum(
        1 for record in records if record["diff_ratio_after"] < record["diff_ratio_before"]
    )
    return (
        f"共 {len(records)} 对：ok {counts['ok']} / suspect {counts['suspect']} / "
        f"fail {counts['fail']}；对齐让差异变小的 {improved} 对；"
        f"最小差异比例 对齐前 {_fmt_ratio(before_best)} → 对齐后 {_fmt_ratio(after_best)}"
    )


# ---------------------------------------------------------------------------
# 六、命令行
# ---------------------------------------------------------------------------

PS_MANIFEST = REPO_ROOT / "tests" / "baselines" / "ps" / "manifest.json"
KRITA_MANIFEST = REPO_ROOT / "tests" / "baselines" / "krita" / "manifest.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="任务 08：亚像素对齐（只平移）+ 差异定位报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pairs", action="store_true", help="只打印配对表（不跑比对）")
    parser.add_argument("--markdown", action="store_true", help="打印定位表（Markdown）")
    parser.add_argument("--json", action="store_true", help="打印定位记录（JSON）")
    parser.add_argument(
        "--method",
        default=DEFAULT_METHOD,
        choices=("bbox", "centroid"),
        help="对齐方法：bbox=外接框中心（默认）、centroid=质心",
    )
    parser.add_argument(
        "--max-shift",
        type=int,
        default=DEFAULT_MAX_SHIFT,
        help=f"允许的最大平移像素数（默认 {DEFAULT_MAX_SHIFT}；传 -1 表示不限制）",
    )
    parser.add_argument("--tol", type=int, default=DEFAULT_TOL, help="差异点阈值（默认 2）")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 对（0=全部）")
    parser.add_argument("--ps-manifest", default=str(PS_MANIFEST), help="PS 子目录 manifest 路径")
    parser.add_argument(
        "--krita-manifest", default=str(KRITA_MANIFEST), help="Krita 子目录 manifest 路径"
    )
    parser.add_argument(
        "--check-names",
        action="store_true",
        help="只核对「文件名解析出的四元组」与 manifest 字段是否一致",
    )
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    if args.check_names:
        for manifest in (args.ps_manifest, args.krita_manifest):
            loaded = load_baseline_entries(manifest)
            entries = loaded["entries"]
            skipped = loaded["skipped"]
            problems = check_name_manifest_consistency(entries, skipped)
            mismatches = [item for item in problems if not item.startswith("[跳过]")]
            status = "四元组全部一致" if not mismatches else f"不一致 {len(mismatches)} 条"
            print(
                f"{manifest}：可配对登记 {len(entries)} 条，{status}；"
                f"跳过 {len(skipped)} 条（非四元组/缺文件）"
            )
            for problem in problems[:50]:
                print("  - " + problem)
        return 0

    pair_result = pair_baselines(args.ps_manifest, args.krita_manifest)
    if not (args.markdown or args.json):
        # 默认（以及 --pairs）都打印配对表：配对是定位的前提，先看配对再看数字
        print(render_pair_table(pair_result))
        if not args.pairs:
            print("提示：加 --markdown 生成定位表，加 --json 生成定位记录。")
        return 0

    records = build_records_for_pairs(
        pair_result,
        method=args.method,
        tol=args.tol,
        max_shift=None if args.max_shift < 0 else args.max_shift,
    )
    if args.limit > 0:
        records = records[: args.limit]

    if args.markdown:
        print(render_pair_table(pair_result))
        print()
        print(render_records_markdown(records))
        print()
        print(render_summary(records))
    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
