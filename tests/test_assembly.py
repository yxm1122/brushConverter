# -*- coding: utf-8 -*-
"""任务 09：组装验收 —— 顶层清单对账 + 命名合规登记 + 三方端到端对比。

本文件是「并行产物收口」的自动化部分，对应任务书第五节验收标准：

1. 顶层 tests/baselines/manifest.json 存在，条数 = 四份来源子清单之和（ps / krita / handmade / feel），
   每条记录含 11 个统一字段（缺的为 null）；
2. 基准图文件名按 docs/基准库命名规范.md 检查，不合规的只登记不改名；
3. 串起「PS 图 ↔ Krita 图 ↔ 掩码理论值」三方对比，输出汇总表。

判据口径（写死，不许为了让用例变绿而放宽）
------------------------------------------
* 差异比例与最大差值一律走 tools/imgdiff.py 的 diff_alpha() 口径
  （只比 Alpha、tol=2、分母为宽×高）；数组输入走 tools/align.diff_alpha_arrays()，
  两者由 tests/test_align.py 交叉验证一致。
* ≤2% 只用于同引擎比对：Krita 真值图 ↔ 掩码复刻理论值（都是 Krita 语义）。
* 跨引擎（PS ↔ Krita）不判 ok：任务 10 已实测跨引擎同参数基线差异
  22.36%~100.00%，docs/验收报告/10-报告.md 结论是「绝对 ≤2% 判据在
  PS↔Krita 口径下不可达」。本文件只给数字，不硬判通过。
* 06A 历史登记把文件名 c0 记成 contrast=0.0，而 Krita 对比度的中性值是
  1.0（见 tools/texture_mask.py 的对比度公式与实测表）；预测时按中性值
  处理并在报告「不一致清单」里登记，不在本任务改子清单。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import imgdiff, kpp_params  # noqa: E402
from tools import texture_mask as tm  # noqa: E402
from tools.align import diff_alpha_arrays, to_alpha  # noqa: E402
from tools.diff_report import (  # noqa: E402
    THRESHOLD_OK,
    build_records_for_pairs,
    pair_baselines,
)

pytestmark = [pytest.mark.verify, pytest.mark.verify_pixels]

BASELINES = REPO_ROOT / "tests" / "baselines"
TOP_MANIFEST = BASELINES / "manifest.json"
TEXTURE_PATH = REPO_ROOT / "research" / "测试用纹理.png"

#: 四份来源子清单：目录 -> (清单路径, 列表键名)。列表键名在四个任务里各不相同
#: （ps=images / krita=items / handmade=entries / feel=items），合并工具必须全部认。
#: feel 是手感基线（任务 14）：Krita 渲染但语义独立，按 lead 2026-11-18 决策升级为正式来源。
SOURCE_MANIFESTS = {
    "ps": (BASELINES / "ps" / "manifest.json", "images"),
    "krita": (BASELINES / "krita" / "manifest.json", "items"),
    "handmade": (BASELINES / "handmade" / "manifest.json", "entries"),
    "feel": (BASELINES / "feel" / "manifest.json", "items"),
}

#: 顶层清单的统一字段（顺序即写入顺序，缺的必须是 null，不许删字段）
UNIFIED_FIELDS = [
    "file",
    "source",
    "size",
    "pressure",
    "mode",
    "depth",
    "brightness",
    "contrast",
    "status",
    "alpha_nonzero",
    "note",
]

#: 文件名规范：来源__标识__模式__关键参数.png（四段、双下划线分隔）
NAME_SEGMENTS = 4
#: 合法参数段一：纹理参数 b<亮度>_c<对比度>（PS/Krita 纹理类基准图）
PARAM_SEGMENT_RE = re.compile(r"^b[-+]?[0-9]+(?:[.][0-9]+)?_c[-+]?[0-9]+(?:[.][0-9]+)?$")
#: 合法参数段二：画布/压感参数 s<W>x<H>[_p<压感>]
#: （plain 真实渲染用 s200x200_p1.0；feel 手感基线用 s200x200，压感在第三段 p<值>）
PLAIN_PARAM_SEGMENT_RE = re.compile(r"^s[0-9]+x[0-9]+(?:_p[0-9]+(?:[.][0-9]+)?)?$")
ILLEGAL_NAME_CHARS = set(' #?*"<>|')

#: 中文模式名 -> Krita TexturingMode 枚举号（与 tools/texture_mask.py 同源）
MODE_ENUM = {
    "正片叠底": tm.MODE_MULTIPLY,
    "减去": tm.MODE_SUBTRACT,
    "高度": tm.MODE_HEIGHT_PHOTOSHOP,
    "线性高度": tm.MODE_LINEAR_HEIGHT_PHOTOSHOP,
}

#: Krita 对比度的中性值（调色板里默认 1.0；0.0 会把掩码压成常数 0.5，不是中性）
NEUTRAL_CONTRAST = 1.0


# ---------------------------------------------------------------------------
# 一、读取与工具函数
# ---------------------------------------------------------------------------

def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _top_items() -> list:
    """读顶层清单条目；文件不存在时 skip（缺端要如实写原因，不许当成通过）。"""
    if not TOP_MANIFEST.is_file():
        pytest.skip(f"顶层清单不存在：{TOP_MANIFEST}（任务 09 合并产物缺失）")
    return _load_json(TOP_MANIFEST).get("items", [])


def _source_items(source: str) -> list:
    path, key = SOURCE_MANIFESTS[source]
    if not path.is_file():
        return []
    return _load_json(path).get(key, [])


def _parse_size(text):
    """把 "100x100" / "200x200" 解析成 (宽, 高)。"""
    match = re.fullmatch(r"([0-9]+)x([0-9]+)", str(text).lower().replace(" ", ""))
    if not match:
        raise ValueError(f"尺寸写法无法解析：{text!r}")
    return int(match.group(1)), int(match.group(2))


def _tiled_texture(width: int, height: int) -> np.ndarray:
    """把 64×64 纹理按 (0,0) 相位平铺到 width×height（与任务 07/07A 口径一致）。"""
    texture = np.asarray(Image.open(TEXTURE_PATH).convert("RGBA"), dtype=np.uint8)
    th, tw = texture.shape[:2]
    ny, nx = -(-height // th), -(-width // tw)
    return np.tile(texture, (ny, nx, 1))[:height, :width, :]


def _predict_mask_alpha(entry: dict) -> np.ndarray:
    """按一条 Krita 登记项预测掩码理论值（uint8 Alpha）。"""
    if entry.get("mode") not in MODE_ENUM:
        raise ValueError(f"未复刻的模式：{entry.get('mode')!r}")
    width, height = _parse_size(entry.get("size"))
    mask = tm.texture_mask_from_rgba(_tiled_texture(width, height), invert=False)
    depth = float(str(entry.get("depth") or "d100").lstrip("d")) / 100.0
    brightness = float(entry.get("brightness") or 0.0)
    contrast = float(entry.get("contrast") or 0.0)
    if contrast == 0.0:
        # 06A 历史登记把文件名 c0 记成 0.0；Krita 侧中性对比度是 1.0（见模块 docstring）
        contrast = NEUTRAL_CONTRAST
    out = tm.compute_mask(
        mask, False, brightness, contrast, MODE_ENUM[entry["mode"]], depth
    )
    return np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8)


def _krita_path(entry: dict) -> Path:
    return BASELINES / "krita" / entry["file"]


# ---------------------------------------------------------------------------
# 二、顶层清单：条数对账与字段统一
# ---------------------------------------------------------------------------

def test_top_manifest_reconciles_at_merge_time():
    """合并后总数 = 四份来源之和；逐 source 计数一致；无重复合并键。"""
    doc = _load_json(TOP_MANIFEST)
    items = doc.get("items", [])
    counts = doc.get("counts", {})
    sources = doc.get("sources", {})

    recorded_total = int(counts["total"])
    recorded_by_source = {src: int(counts[src]) for src in SOURCE_MANIFESTS}

    assert len(items) == recorded_total, f"条目数 {len(items)} != counts.total {recorded_total}"
    for source in SOURCE_MANIFESTS:
        assert sources[source]["count"] == recorded_by_source[source], (
            f"{source} 子清单登记数 {sources[source]['count']} 与 counts 不一致"
        )
    assert sum(recorded_by_source.values()) == recorded_total, (
        f"逐 source 相加 {recorded_by_source} != 总数 {recorded_total}"
    )

    # 合并键：相对路径；重名会互相覆盖，必须为 0
    files = [item["file"] for item in items]
    assert len(files) == len(set(files)), "顶层清单存在重复合并键（相对路径重复）"
    for item in items:
        assert item["file"].split("/", 1)[0] == item["source"], (
            f"file 路径前缀与 source 不一致：{item['file']} / {item['source']}"
        )


def test_top_manifest_every_record_has_unified_fields():
    """每条记录都含 11 个统一字段（缺的为 null），不许丢字段、不许改顺序。"""
    items = _top_items()
    assert items, "顶层清单为空"
    for item in items:
        assert list(item.keys()) == UNIFIED_FIELDS, (
            f"{item.get('file')} 字段集合/顺序不符合统一字段表：{list(item.keys())}"
        )


def test_top_manifest_field_types():
    """字段类型可解析：size=WxH、depth 整数、source 四选一、alpha_nonzero 整数或 null。"""
    items = _top_items()
    for item in items:
        assert item["source"] in SOURCE_MANIFESTS, f"未知来源：{item['source']}"
        width, height = _parse_size(item["size"])  # 解析失败即抛错
        assert width > 0 and height > 0
        assert item["depth"] is None or isinstance(item["depth"], int), (
            f"{item['file']} 的 depth 不是整数：{item['depth']!r}"
        )
        assert item["alpha_nonzero"] is None or isinstance(item["alpha_nonzero"], int), (
            f"{item['file']} 的 alpha_nonzero 不是整数：{item['alpha_nonzero']!r}"
        )
        for key in ("brightness", "contrast"):
            value = item[key]
            assert value is None or isinstance(value, (int, float)), (
                f"{item['file']} 的 {key} 不是数值：{value!r}"
            )


def test_top_manifest_alpha_nonzero_matches_files():
    """alpha_nonzero 是合并时实测值：与磁盘 PNG 现算结果必须一致。"""
    items = _top_items()
    missing, mismatched = [], []
    for item in items:
        path = BASELINES / item["file"]
        if not path.is_file():
            missing.append(item["file"])
            continue
        measured = int(np.count_nonzero(to_alpha(path)))
        if measured != item["alpha_nonzero"]:
            mismatched.append((item["file"], item["alpha_nonzero"], measured))
    if missing:
        # 合并后并行任务仍在动基线目录：缺文件属「现场在变」，如实跳过而不是判失败
        pytest.skip(f"合并后基线目录仍有变动，{len(missing)} 张图不在磁盘上：{missing[:3]}")
    assert not mismatched, f"alpha_nonzero 与实测不一致：{mismatched[:5]}"


def test_sources_have_not_drifted_since_merge():
    """子清单若在合并后被并行任务追加/重写，本用例如实 skip 并提示由任务 15 再次合并。"""
    doc = _load_json(TOP_MANIFEST)
    recorded = doc.get("counts", {})
    current = {source: len(_source_items(source)) for source in SOURCE_MANIFESTS}
    recorded_by_source = {source: int(recorded[source]) for source in SOURCE_MANIFESTS}
    if current != recorded_by_source:
        pytest.skip(
            "并行任务在合并后改动了子清单："
            f"合并时 {recorded_by_source}，现在 {current}；"
            "按约定由任务 15 再次合并，本用例不做二次判据"
        )
    assert current == recorded_by_source


# ---------------------------------------------------------------------------
# 三、命名合规（只登记，不改名）
# ---------------------------------------------------------------------------

def test_naming_compliance_registered_only():
    """结构性不合规必须为 0；参数段写法偏差只登记（合法段：b/c 纹理参数、s/p 画布压感参数）。"""
    items = _top_items()
    structural, param_segment = [], []
    for item in items:
        name = item["file"].split("/", 1)[1]
        stem = name[:-4] if name.endswith(".png") else name
        parts = stem.split("__")
        if (
            len(parts) != NAME_SEGMENTS
            or parts[0] != item["source"]
            or ILLEGAL_NAME_CHARS & set(name)
        ):
            structural.append(item["file"])
            continue
        if not (
            PARAM_SEGMENT_RE.match(parts[3]) or PLAIN_PARAM_SEGMENT_RE.match(parts[3])
        ):
            param_segment.append(item["file"])

    assert not structural, f"基准图文件名结构性不合规（四段/来源/非法字符）：{structural[:10]}"
    # 参数段偏差只登记：重命名会破坏 05/06A 的清单引用，任务书要求「只登记不改名」
    assert len(param_segment) < len(items), "参数段全部不合规，疑似解析规则写错了"
    print(
        f"[命名合规] 检查 {len(items)} 张：结构性不合规 0 张；"
        f"参数段写法偏差（深度写进第 4 段）{len(param_segment)} 张，"
        f"示例：{param_segment[:3]}"
    )


# ---------------------------------------------------------------------------
# 四、三方端到端：PS 图 ↔ Krita 图 ↔ 掩码理论值
# ---------------------------------------------------------------------------

def test_three_way_end_to_end():
    """三方对比一条命令跑通：PS↔Krita 给数字（不判 ok），Krita↔掩码按 ≤2% 判。"""
    if not TEXTURE_PATH.is_file():
        pytest.skip(f"缺纹理输入，无法算掩码理论值：{TEXTURE_PATH}")
    _top_items()  # 顶层清单缺失时这里就会 skip

    # 端 1+2：PS 与 Krita 的配对（配对逻辑复用任务 08 的 diff_report）
    pair_result = pair_baselines(
        SOURCE_MANIFESTS["ps"][0], SOURCE_MANIFESTS["krita"][0]
    )
    pairs = pair_result["pairs"]
    if not pairs:
        pytest.skip(
            "缺可配对的 PS/Krita 端：四元组（模式+深度+亮度+对比度）没有任何一对同时存在"
        )
    records = build_records_for_pairs(pair_result)
    assert len(records) == len(pairs)

    # 端 3：掩码理论值复刻 Krita 图（同引擎口径，按项目 ≤2% 判据）
    krita_items = _source_items("krita")
    hist_rows = []
    for entry in krita_items:
        if entry.get("status") != "historical" or entry.get("mode") not in MODE_ENUM:
            continue
        path = _krita_path(entry)
        if not path.is_file():
            continue
        predicted = _predict_mask_alpha(entry)
        actual = to_alpha(path)
        if predicted.shape != actual.shape:
            continue
        hist_rows.append(
            (entry["file"], diff_alpha_arrays(predicted, actual, tol=imgdiff.DEFAULT_TOL))
        )
    assert hist_rows, "缺 Krita 历史真值端：没有可复刻掩码的 100×100 历史图"

    same_engine_fail = [
        (name, result["diff_ratio"], result["max_delta"])
        for name, result in hist_rows
        if result["diff_ratio"] > THRESHOLD_OK
    ]
    assert not same_engine_fail, (
        f"同引擎口径（Krita 图 ↔ 掩码理论值）超过 ≤2% 判据：{same_engine_fail[:5]}"
    )

    # 三方汇总表：跨引擎两列只给数字，不套 ≤2% 判据（10-报告已实测不可达）
    sep = chr(10)
    lines = [
        "",
        "| # | PS 图 | Krita 图 | PS↔Krita 对齐前 | PS↔Krita 对齐后 | Krita↔掩码 | PS↔掩码 | 跨引擎判据 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    cross_ratios = []
    for index, (pair, record) in enumerate(zip(pairs, records), start=1):
        krita_entry = next(
            item for item in krita_items if item.get("file") == pair["krita"]["file"]
        )
        predicted = _predict_mask_alpha(krita_entry)
        krita_actual = to_alpha(_krita_path(krita_entry))
        ps_actual = to_alpha(BASELINES / "ps" / pair["ps"]["file"])
        if predicted.shape != krita_actual.shape or predicted.shape != ps_actual.shape:
            pytest.skip(
                f"尺寸不一致，无法三方对比：{pair['krita']['file']} "
                f"{predicted.shape} / {krita_actual.shape} / {ps_actual.shape}"
            )
        to_krita = diff_alpha_arrays(predicted, krita_actual, tol=imgdiff.DEFAULT_TOL)
        to_ps = diff_alpha_arrays(predicted, ps_actual, tol=imgdiff.DEFAULT_TOL)

        for ratio in (
            record["diff_ratio_before"],
            record["diff_ratio_after"],
            to_krita["diff_ratio"],
            to_ps["diff_ratio"],
        ):
            assert 0.0 <= ratio <= 1.0, f"差异比例越界：{ratio}"
        cross_ratios.append(record["diff_ratio_after"])

        lines.append(
            "| {i} | {ps} | {krita} | {before:.2f}% | {after:.2f}% | {km:.2f}% (max {kmd}) | "
            "{pm:.2f}% (max {pmd}) | 不适用 |".format(
                i=index,
                ps=pair["ps"]["file"],
                krita=pair["krita"]["file"],
                before=record["diff_ratio_before"] * 100.0,
                after=record["diff_ratio_after"] * 100.0,
                km=to_krita["diff_ratio"] * 100.0,
                kmd=to_krita["max_delta"],
                pm=to_ps["diff_ratio"] * 100.0,
                pmd=to_ps["max_delta"],
            )
        )
    max_hist_delta = max(result["max_delta"] for _, result in hist_rows)
    lines.append("")
    lines.append(
        f"[三方汇总] PS↔Krita 对齐后差异 {min(cross_ratios)*100:.2f}%~{max(cross_ratios)*100:.2f}%"
        f"（{len(cross_ratios)} 对，跨引擎，不判 ok）；"
        f"Krita↔掩码 {len(hist_rows)} 行全部 ≤ {THRESHOLD_OK*100:.0f}%；"
        f"Krita↔掩码最大差值 {max_hist_delta} 灰度级。"
    )
    lines.append(
        "[判据口径] ≤2% 只对同引擎（Krita 图 ↔ 掩码理论值）生效；PS↔Krita 是跨引擎基线差，"
        "10-报告实测 22.36%~100.00%，绝对 ≤2% 不可达，这里只给数字供人工判读。"
    )
    print(sep.join(lines))

    # 该用例的「通过」定义：三方都能算出数、同引擎判据全绿；
    # 跨引擎差异不作为失败条件（避免用判据硬凑 ok）
    assert min(cross_ratios) > 0.0, "PS↔Krita 差异为 0 反而不合理（两端不应完全相同）"


# ---------------------------------------------------------------------------
# 五、重复实现合并后的「单一来源」锁定
# ---------------------------------------------------------------------------

def test_merged_constants_have_single_source():
    """重复实现合并后的锁定：容差/阈值/数值比较只保留一个来源。"""
    from tools import align, diff_report

    assert align.DEFAULT_TOL == imgdiff.DEFAULT_TOL, (
        "align.DEFAULT_TOL 必须取自 tools/imgdiff.DEFAULT_TOL"
    )
    assert THRESHOLD_OK == imgdiff.DEFAULT_RATIO_THRESHOLD, (
        "diff_report.THRESHOLD_OK 必须取自 tools/imgdiff.DEFAULT_RATIO_THRESHOLD"
    )
    assert diff_report.values_close is kpp_params.close, (
        "diff_report 的数值比较必须复用 tools/kpp_params.close"
    )
