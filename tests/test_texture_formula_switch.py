# -*- coding: utf-8 -*-
"""任务 13：纹理亮度/对比度公式开关（legacy / calibrated）的单元测试与 A/B 表生成器。

【两条路径】（v1.2.0 起默认 calibrated；legacy 是历史方案，可用环境变量回退）
- calibrated（默认）：任务 12 拟合出的 PS 纹理前置变换的精确逆（闭式推导见
  src/brush_converter/kpp/preset_xml.py::_texture_brightness_contrast_calibrated）：

      k   = 1+C/100（C<0）或 1/(1-C/100)（C>=0）
      b_k = (1-k)/(510k) - PS亮度/(255k)   （C<0：PS 顺序是「先对比度后亮度」）
      b_k = (1-k)/(510k) - PS亮度/255      （C>=0：PS 顺序是「先亮度后对比度」）
      c_k = k

- legacy（历史方案，v1.1.2；回退命令 BRUSHCONVERTER_TEXTURE_FORMULA=legacy）：
      亮度 b_k = round(0.10 - PS亮度/250, 2)   clamp [-1,1]
      对比度 c_k = PS 中心因子（C<0: 1+C/100；C>=0: 1/(1-C/100)）round 两位 clamp [0,2]

【A/B 判据（lead 已定）】
任务 10 实测「PS 图 vs Krita 图整图 <=2%」跨引擎结构性不可达（最小 22.36%），
故 A/B 改在**掩码空间**做：同一纹理 0..255 全取值、同一（亮度, 对比度）参数组，
比较「PS 拟合掩码」与「Krita 复刻掩码」的 8-bit 差异比例（tol=2）与最大差值。
- PS 侧真值：任务 12 的参数化公式（tools/lut_fit.bc_transform，pivot=127、order=sign_c）
- Krita 侧真值：tools/texture_mask.apply_three（KisTextureMaskInfo 的精确复刻）

【直接运行本文件可打印完整 A/B 表】
    python tests/test_texture_formula_switch.py
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.convert import _texture_xml  # noqa: E402
from brush_converter.kpp.preset_xml import (  # noqa: E402
    _TEXTURE_FORMULA_ENV,
    _texture_contrast_factor,
    _texture_formula,
    texture_brightness_contrast,
)
from brush_converter.mapping import BrushPreset, TextureSettings, map_presets  # noqa: E402

pytestmark = pytest.mark.verify

# ---------------------------------------------------------------------------
# A/B 判据口径与 PS/Krita 两侧的参考模型
# ---------------------------------------------------------------------------
TOL = 2            # 与 tools/imgdiff.py / 任务 12 一致：|差| > 2 才算差异
RATIO_LIMIT = 0.02  # 达标线：差异比例 <= 2%

#: 任务 12 拟合结果（tests/baselines/ps/_work/lut_fit_result.json 的 params）
PS_PARAMS = {"pivot": 127.0, "k_fitted": {}, "order": "sign_c"}

#: ABR 纹理混合模式 → 任务 12 参数化公式里的模式名
PS_MODE = {
    "Mul ": "正片叠底",
    "Sbtr": "减去",
    "Hght": "高度",
    "height": "高度",
    "HghtPS": "高度",
    "linearHeight": "线性高度",
    "linearHeightPhotoshop": "线性高度",
    "linearHeightPS": "线性高度",
}

#: ABR 纹理混合模式 → Krita TexturingMode 枚举号（与 mapping._TEXTURING_MODE 一致）
KRITA_MODE = {
    "Mul ": 0,
    "Sbtr": 1,
    "Hght": 14,
    "height": 14,
    "HghtPS": 14,
    "linearHeight": 15,
    "linearHeightPhotoshop": 15,
    "linearHeightPS": 15,
}

#: A/B 参数网格：任务 12 的亮度/对比度覆盖 + 真实笔刷会出现的取值
GRID_B = (-150, -100, -50, -25, 0, 25, 50, 100, 150)
GRID_C = (-50, -25, -10, 0, 10, 25, 50, 75, 100)

_TEX_255 = np.arange(256, dtype=np.float64)


def _ps_mask_values(brightness: int, contrast: int) -> np.ndarray:
    """PS 侧前置掩码（0..255，整数）：任务 12 参数化公式。"""
    from tools.lut_fit import bc_transform

    u = bc_transform(_TEX_255, brightness, contrast, PS_PARAMS)
    return np.floor(u + 0.5).astype(np.int64)


def _krita_mask_values(brightness: float, contrast: float) -> np.ndarray:
    """Krita 侧前置掩码（0..255，整数）：KisTextureMaskInfo 的精确复刻。"""
    from tools.texture_mask import apply_three

    mask = apply_three((_TEX_255 / 255.0).astype(np.float32), False,
                       brightness, contrast)
    return np.floor(mask.astype(np.float64) * 255.0 + 0.5).astype(np.int64)


def _metrics(pred: np.ndarray, truth: np.ndarray, tol: int = TOL) -> dict:
    """与 tools/imgdiff.py 同口径：差异点 = |差| > tol，分母为全部取值点。"""
    delta = np.abs(np.rint(pred).astype(np.int64) - truth.astype(np.int64))
    return {
        "points": int(delta.size),
        "diff_points": int(np.count_nonzero(delta > tol)),
        "diff_ratio": float(np.count_nonzero(delta > tol)) / delta.size,
        "max_delta": int(delta.max()),
        "mae": float(delta.mean()),
    }


def _pair_metrics(ps_b: int, ps_c: int, formula: str) -> dict:
    """把一条路径的 Krita 参数与 PS 前置掩码对比。"""
    old = os.environ.get(_TEXTURE_FORMULA_ENV)
    os.environ[_TEXTURE_FORMULA_ENV] = formula
    try:
        b, c = texture_brightness_contrast(ps_b, ps_c)
    finally:
        if old is None:
            os.environ.pop(_TEXTURE_FORMULA_ENV, None)
        else:
            os.environ[_TEXTURE_FORMULA_ENV] = old
    return _metrics(_krita_mask_values(b, c), _ps_mask_values(ps_b, ps_c))


def _expressible(ps_b: int, ps_c: int) -> bool:
    """参数组是否在 Krita 可表达范围内（否则属结构性不可达，不计入公式优劣）。

    - C > 50 时 k > 2，超出 Krita Contrast 上限 2；
    - 反解出的亮度超出 Krita 亮度滑杆 [-1,1]（仅 C<0 且 PS 亮度很负时出现）。
    """
    import sys as _sys

    from brush_converter.kpp import preset_xml as _px

    k = _texture_contrast_factor(ps_c)
    k_eff = max(0.0, min(2.0, k))
    if k_eff <= 0.0:
        return False
    if ps_c > 50:
        return False
    c = max(-50.0, min(100.0, ps_c))
    center = (1.0 - k_eff) / (510.0 * k_eff)
    b_k = center - (ps_b / (255.0 * k_eff) if c < 0 else ps_b / 255.0)
    return -1.0 <= b_k <= 1.0


def _expressible_combos() -> list[tuple[int, int]]:
    return [(b, c) for b in GRID_B for c in GRID_C if _expressible(b, c)]


# ---------------------------------------------------------------------------
# 单元测试 1：默认值、旧公式逐值、非法值回退
# ---------------------------------------------------------------------------
def test_default_formula_is_calibrated(monkeypatch):
    """v1.2.0 起默认必须是 calibrated（任务 13 转正）；显式 legacy 仍可回退。"""
    monkeypatch.delenv(_TEXTURE_FORMULA_ENV, raising=False)
    assert _texture_formula() == "calibrated"
    # calibrated：PS(0, 0) 是恒等 → Krita 亮度 0、对比度 1.0
    assert texture_brightness_contrast(0, 0) == (0.0, 1.0)
    # 显式 legacy 回退仍是 v1.1.2 的 0.10 / 1.0
    monkeypatch.setenv(_TEXTURE_FORMULA_ENV, "legacy")
    assert texture_brightness_contrast(0, 0) == (0.1, 1.0)


@pytest.mark.parametrize("ps_b,ps_c,expected", [
    (0, 0, (0.1, 1.0)),          # 中性：旧基线 0.10
    (-27, -17, (0.21, 0.83)),    # tests/test_texture_xml.py 已锁定的数值
    (-100, 100, (0.5, 2.0)),     # 亮度 0.5；+100 对比度用 2.0 逼近二值化
    (30, 25, (-0.02, 1.33)),     # 0.10-30/250 = -0.02（两位小数，负号保留）
])
def test_legacy_values(monkeypatch, ps_b, ps_c, expected):
    """legacy 路径的具体数值（含 clamp），与 v1.1.2 实现逐值一致。"""
    monkeypatch.setenv(_TEXTURE_FORMULA_ENV, "legacy")
    assert texture_brightness_contrast(ps_b, ps_c) == expected


def test_legacy_rounding_edge(monkeypatch):
    """0.10 - 30/250 = -0.02 的两位小数舍入与负零处理不能变。"""
    monkeypatch.setenv(_TEXTURE_FORMULA_ENV, "legacy")
    assert texture_brightness_contrast(30, 25) == (-0.02, 1.33)
    assert texture_brightness_contrast(0, 75) == (0.1, 2.0)   # 4.0 → clamp 2
    assert texture_brightness_contrast(0, -100) == (0.1, 0.5)  # C=-100 → clamp -50 → 0.5


def test_default_equals_calibrated_and_legacy_equals_v112(monkeypatch):
    """不设变量 == calibrated；显式 legacy == v1.1.2 旧公式（逐字段）。"""
    values = [(b, c) for b in GRID_B for c in GRID_C]
    monkeypatch.delenv(_TEXTURE_FORMULA_ENV, raising=False)
    default = [texture_brightness_contrast(b, c) for b, c in values]
    monkeypatch.setenv(_TEXTURE_FORMULA_ENV, "calibrated")
    explicit = [texture_brightness_contrast(b, c) for b, c in values]
    assert default == explicit
    # 显式 legacy 必须与 v1.1.2 旧公式逐字段一致（旧路径不许失守）
    monkeypatch.setenv(_TEXTURE_FORMULA_ENV, "legacy")
    legacy = [texture_brightness_contrast(b, c) for b, c in values]
    expected = []
    for b, c in values:
        brightness = max(-1.0, min(1.0, round(0.10 - b / 250.0, 2)))
        if brightness == 0:
            brightness = 0.0
        cc = max(-50.0, min(100.0, c))
        if cc < 0:
            factor = 1.0 + cc / 100.0
        elif cc >= 100:
            factor = 1e6
        else:
            factor = 1.0 / (1.0 - cc / 100.0)
        expected.append((brightness, max(0.0, min(2.0, round(factor, 2)))))
    assert legacy == expected


# ---------------------------------------------------------------------------
# 单元测试 2：calibrated 的具体数值（闭式）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ps_b,ps_c,expected", [
    (0, 0, (0.0, 1.0)),           # 中性 = 恒等（旧公式的 0.10 基线是多余偏移）
    (-27, -17, (0.128, 0.83)),    # (1-0.83)/(510*0.83) + 27/(255*0.83)
    (30, 25, (-0.118, 1.333)),    # C>=0：亮度项不除以 k
    (150, 50, (-0.589, 2.0)),     # (1-2)/(510*2) - 150/255
    (-150, -50, (1.0, 0.5)),      # 反解 1.178 → 超 Krita 亮度上限，截断 1.0
    (-100, 100, (0.391, 2.0)),    # C=+100：k 巨大 → 对比度截断 2
])
def test_calibrated_values(monkeypatch, ps_b, ps_c, expected):
    """calibrated 路径的具体数值，防止公式或精度被静默改动。"""
    monkeypatch.setenv(_TEXTURE_FORMULA_ENV, "calibrated")
    assert texture_brightness_contrast(ps_b, ps_c) == expected


def test_switch_changes_written_xml(monkeypatch):
    """同一笔刷在两种公式下产出的 Krita 亮度/对比度不同，且能写进 XML。"""
    from brush_converter.kpp.preset_xml import build_preset_xml

    img = np.zeros((4, 4, 3), dtype=np.uint8)
    ts = TextureSettings(name="x", uuid="12345678-0000-0000-0000-000000000000",
                         brightness=-27, contrast=-17, image=img)
    bp = BrushPreset(name="x", texture=ts)

    monkeypatch.setenv(_TEXTURE_FORMULA_ENV, "legacy")
    legacy = _texture_xml(bp)
    assert legacy is not None
    xml_legacy = build_preset_xml("x", "", None, texture=legacy)

    monkeypatch.setenv(_TEXTURE_FORMULA_ENV, "calibrated")
    calib = _texture_xml(bp)
    assert calib is not None
    xml_calib = build_preset_xml("x", "", None, texture=calib)

    assert (legacy.brightness, legacy.contrast) == (0.21, 0.83)
    assert (calib.brightness, calib.contrast) == (0.128, 0.83)
    assert 'Texture/Pattern/Brightness" type="internal">0.21' in xml_legacy
    assert 'Texture/Pattern/Brightness" type="internal">0.128' in xml_calib


# ---------------------------------------------------------------------------
# 单元测试 3：非法环境变量回退 legacy 且只警告一次
# ---------------------------------------------------------------------------
def test_illegal_value_falls_back_to_default_and_warns_once(monkeypatch, capsys):
    from brush_converter.kpp import preset_xml as px

    monkeypatch.setenv(_TEXTURE_FORMULA_ENV, "abc")
    px._TEXTURE_FORMULA_WARNED.clear()
    # 默认公式是 calibrated → 非法值回退 calibrated（不是 legacy）
    assert _texture_formula() == "calibrated"
    assert texture_brightness_contrast(0, 0) == (0.0, 1.0)
    # 同一个非法值只警告一次；再调用不再刷屏
    assert _texture_formula() == "calibrated"
    err = capsys.readouterr().err
    assert err.count(_TEXTURE_FORMULA_ENV) == 1
    assert "回退默认公式 calibrated" in err


def test_blank_and_case_insensitive(monkeypatch):
    monkeypatch.setenv(_TEXTURE_FORMULA_ENV, "  Calibrated ")
    assert _texture_formula() == "calibrated"
    monkeypatch.setenv(_TEXTURE_FORMULA_ENV, "LEGACY")
    assert _texture_formula() == "legacy"
    # 空字符串视为非法 → 回退默认 calibrated（会警告一次，这里只断言返回值）
    monkeypatch.setenv(_TEXTURE_FORMULA_ENV, "")
    assert _texture_formula() == "calibrated"


# ---------------------------------------------------------------------------
# 单元测试 4：A/B 判据（掩码空间等价）
# ---------------------------------------------------------------------------
def test_calibrated_mask_equivalent_on_expressible_grid(monkeypatch):
    """可表达参数组：Krita 掩码 vs PS 拟合掩码 <= 2%（期望 0.00%，最大差 <= 2）。"""
    monkeypatch.delenv(_TEXTURE_FORMULA_ENV, raising=False)
    combos = _expressible_combos()
    assert combos, "可表达参数组不应为空"
    total = 0
    diff = 0
    worst = 0
    for ps_b, ps_c in combos:
        m = _pair_metrics(ps_b, ps_c, "calibrated")
        total += m["points"]
        diff += m["diff_points"]
        worst = max(worst, m["max_delta"])
        assert m["diff_ratio"] <= RATIO_LIMIT, f"({ps_b},{ps_c}) 差异比例 {m['diff_ratio']:.2%}"
    assert diff / total == 0.0
    assert worst <= 2


def test_legacy_mask_much_worse_than_calibrated(monkeypatch):
    """同一批参数组上 legacy 的掩码差异必须显著大于 calibrated。"""
    monkeypatch.delenv(_TEXTURE_FORMULA_ENV, raising=False)
    combos = _expressible_combos()
    total = 0
    legacy_diff = 0
    calib_diff = 0
    for ps_b, ps_c in combos:
        lg = _pair_metrics(ps_b, ps_c, "legacy")
        cb = _pair_metrics(ps_b, ps_c, "calibrated")
        total += lg["points"]
        legacy_diff += lg["diff_points"]
        calib_diff += cb["diff_points"]
    legacy_ratio = legacy_diff / total
    calib_ratio = calib_diff / total
    assert calib_ratio == 0.0, calib_ratio
    assert legacy_ratio > 0.30, legacy_ratio  # 实测约 0.60


def test_structural_limits_are_flagged():
    """C>50 与反解超范围必须被判为「结构性不可表达」，不能假装达标。"""
    assert not _expressible(0, 75)      # Krita Contrast 上限 2
    assert not _expressible(0, 100)
    assert not _expressible(-150, -50)  # 反解 1.178 超 Krita 亮度上限
    assert not _expressible(150, -50)
    assert _expressible(0, 50)          # 边界：k=2 恰好可表达
    assert _expressible(-27, -17)


# ---------------------------------------------------------------------------
# A/B 表（直接运行本文件时打印，供任务报告引用）
# ---------------------------------------------------------------------------
def _real_brushes():
    """真实样本里所有带纹理的预设：[(文件, 名称, 模式, 深度, PS亮度, PS对比度)]。"""
    rows = []
    abr_files = [
        _REPO_ROOT / "tests" / "测试用.abr",
        _REPO_ROOT / "tests" / "海怪笔刷-详情展示的勾线笔套装组18支.abr",
    ]
    for path in abr_files:
        if not path.is_file():
            continue
        abr = AbrFile.parse(str(path))
        for bp in map_presets(abr):
            tex = bp.texture
            if tex is None:
                continue
            rows.append({
                "file": path.name,
                "name": bp.name,
                "mode": tex.blend_mode,
                "depth": float(tex.depth),
                "b": int(tex.brightness),
                "c": int(tex.contrast),
            })
    return rows


def _table_grid() -> str:
    lines = [
        "### A/B-1 参数网格（纹理 0..255 全取值，掩码空间，tol=2）",
        "",
        "| PS亮度 | PS对比度 | leg b_k | leg c_k | legacy 差异比例 | legacy 最大差 | cal b_k | cal c_k | calibrated 差异比例 | calibrated 最大差 | 可表达 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|",
    ]
    for ps_b in GRID_B:
        for ps_c in GRID_C:
            old = os.environ.get(_TEXTURE_FORMULA_ENV)
            os.environ[_TEXTURE_FORMULA_ENV] = "legacy"
            lb, lc = texture_brightness_contrast(ps_b, ps_c)
            os.environ[_TEXTURE_FORMULA_ENV] = "calibrated"
            cb, cc = texture_brightness_contrast(ps_b, ps_c)
            if old is None:
                os.environ.pop(_TEXTURE_FORMULA_ENV, None)
            else:
                os.environ[_TEXTURE_FORMULA_ENV] = old
            lm = _pair_metrics(ps_b, ps_c, "legacy")
            cm = _pair_metrics(ps_b, ps_c, "calibrated")
            lines.append(
                f"| {ps_b} | {ps_c} | {lb:g} | {lc:g} | {lm['diff_ratio']:.2%} | {lm['max_delta']} "
                f"| {cb:g} | {cc:g} | {cm['diff_ratio']:.2%} | {cm['max_delta']} "
                f"| {'是' if _expressible(ps_b, ps_c) else '否'} |"
            )
    return "\n".join(lines)


def _table_brushes() -> str:
    lines = [
        "### A/B-2 真实笔刷（tests/测试用.abr 6 支 + 海怪…18支.abr 19 支，仅含纹理的 15 支）",
        "",
        "| 文件 | 笔刷 | 模式 | 深度 | PS b | PS c | legacy 差异比例 | legacy 最大差 | calibrated 差异比例 | calibrated 最大差 | 可表达 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|:--:|",
    ]
    for row in _real_brushes():
        lm = _pair_metrics(row["b"], row["c"], "legacy")
        cm = _pair_metrics(row["b"], row["c"], "calibrated")
        ok = "是" if _expressible(row["b"], row["c"]) else "否"
        lines.append(
            f"| {row['file']} | {row['name']} | {row['mode']} | {row['depth']:g} | {row['b']} | {row['c']} "
            f"| {lm['diff_ratio']:.2%} | {lm['max_delta']} | {cm['diff_ratio']:.2%} | {cm['max_delta']} | {ok} |"
        )
    return "\n".join(lines)


def _table_convert() -> str:
    """真实转换两遍（内存内），对比 .kpp 里的亮度/对比度字段。"""
    import tempfile
    from brush_converter.convert import convert

    lines = [
        "### A/B-3 真实转换（同一批笔刷分别用两种公式产出 .kpp，抽取字段对照）",
        "",
        "| 笔刷 | legacy 亮度/对比度 | calibrated 亮度/对比度 |",
        "|---|---|---|",
    ]
    path = _REPO_ROOT / "tests" / "测试用.abr"
    if not path.is_file():
        return "\n".join(lines + ["（样本缺失，跳过）"])
    out = Path(tempfile.mkdtemp(prefix="bc_formula_ab_"))
    old = os.environ.get(_TEXTURE_FORMULA_ENV)
    results = {}
    for formula in ("legacy", "calibrated"):
        os.environ[_TEXTURE_FORMULA_ENV] = formula
        try:
            r = convert(str(path), str(out / formula), make_bundle=False)
            # 在环境变量仍指向本路径时读回纹理字段（开关是每次调用时读取的）
            rows = [(bp.name, _texture_xml(bp)) for bp in r.presets]
        finally:
            if old is None:
                os.environ.pop(_TEXTURE_FORMULA_ENV, None)
            else:
                os.environ[_TEXTURE_FORMULA_ENV] = old
        results[formula] = rows
    for (name, lt), (_, ct) in zip(results["legacy"], results["calibrated"]):
        if lt is None or ct is None:
            continue
        lines.append(f"| {name} | {lt.brightness:g} / {lt.contrast:g} | {ct.brightness:g} / {ct.contrast:g} |")
    return "\n".join(lines)


def _path_params(path: str, ps_b: int, ps_c: int) -> tuple[float, float]:
    """在指定公式路径下取 Krita 亮度/对比度（读完恢复环境变量）。"""
    old = os.environ.get(_TEXTURE_FORMULA_ENV)
    os.environ[_TEXTURE_FORMULA_ENV] = path
    try:
        return texture_brightness_contrast(ps_b, ps_c)
    finally:
        if old is None:
            os.environ.pop(_TEXTURE_FORMULA_ENV, None)
        else:
            os.environ[_TEXTURE_FORMULA_ENV] = old


def _table_truth() -> str:
    """真实 PS 真值图（d100 正片叠底/减去）上的掩码空间 A/B。

    PS 深度 100 时：正片叠底输出 Alpha 就是前置掩码 u；减去输出是 255-u。
    因此这两组图可以直接当「PS 前置掩码真值」，不依赖拟合公式。
    """
    from tools.lut_fit import build_dataset
    from tools.texture_mask import apply_three

    lines = [
        "### A/B-4 真实 PS 真值图（d100 正片叠底/减去；Alpha 即 PS 前置掩码）",
        "",
    ]
    try:
        records, _ = build_dataset(include_human=False)
    except Exception as exc:  # 基准库缺失时如实说明，不伪造数字
        return "\n".join(lines + [f"（基准库不可用，跳过：{exc}）"])
    sel = [r for r in records
           if r["depth"] == 100 and r["mode"] in ("正片叠底", "减去") and not r["saturated"]]
    acc = {"legacy": [0, 0, 0], "calibrated": [0, 0, 0]}
    acc_ok = {"legacy": [0, 0, 0], "calibrated": [0, 0, 0]}
    worst = []
    for r in sel:
        x = np.clip(r["t"].astype(np.float64).ravel(), 0, 255) / 255.0
        alpha = r["a"].astype(np.int64).ravel()
        truth = alpha if r["mode"] == "正片叠底" else 255 - alpha
        ok = _expressible(int(r["b"]), int(r["c"]))
        for path in ("legacy", "calibrated"):
            b, c = _path_params(path, int(r["b"]), int(r["c"]))
            pred = np.floor(apply_three(x.astype(np.float32), False, b, c)
                            .astype(np.float64) * 255.0 + 0.5)
            d = np.abs(pred.astype(np.int64) - truth)
            nb = int(np.count_nonzero(d > TOL))
            for target in (acc[path], acc_ok[path] if ok else None):
                if target is None:
                    continue
                target[0] += d.size
                target[1] += nb
                target[2] = max(target[2], int(d.max()))
            if path == "calibrated":
                worst.append((float(nb) / d.size, int(d.max()), r["file"], ok))
    lines.append(f"- 参与比对真值图：{len(sel)} 张（每张 100×100 像素；可表达 {sum(1 for r in sel if _expressible(int(r['b']), int(r['c'])))} 张）")
    for path in ("legacy", "calibrated"):
        n, diff, mx = acc[path]
        lines.append(f"- {path}（全部）：差异比例 {diff / n:.3%}，最大差值 {mx}")
        n, diff, mx = acc_ok[path]
        lines.append(f"- {path}（仅可表达参数）：差异比例 {diff / n:.3%}，最大差值 {mx}")
    lines.append("")
    lines.append("calibrated 最差 5 张（差异比例 / 最大差 / 是否可表达）：")
    for ratio, mx, name, ok in sorted(worst, reverse=True)[:5]:
        lines.append(f"  - {ratio:.2%} / {mx} / {'是' if ok else '否（结构性）'} / {name}")
    return "\n".join(lines)


def _summary() -> str:
    """汇总最关键的两个数：可表达参数组上 legacy 与 calibrated 的整体差异比例。"""
    total = 0
    legacy_diff = 0
    calib_diff = 0
    all_total = 0
    all_legacy = 0
    all_calib = 0
    for ps_b in GRID_B:
        for ps_c in GRID_C:
            lm = _pair_metrics(ps_b, ps_c, "legacy")
            cm = _pair_metrics(ps_b, ps_c, "calibrated")
            all_total += lm["points"]
            all_legacy += lm["diff_points"]
            all_calib += cm["diff_points"]
            if _expressible(ps_b, ps_c):
                total += lm["points"]
                legacy_diff += lm["diff_points"]
                calib_diff += cm["diff_points"]
    return (
        "### A/B-0 汇总结论\n\n"
        f"- 可表达参数组（{len(_expressible_combos())}/{len(GRID_B) * len(GRID_C)} 组，纹理 0..255 全取值）："
        f"legacy 差异比例 {legacy_diff / total:.2%}，calibrated {calib_diff / total:.2%}（达标线 <=2%）\n"
        f"- 全部参数组（含 C>50 / 反解超亮度范围的不可表达组）："
        f"legacy {all_legacy / all_total:.2%}，calibrated {all_calib / all_total:.2%}"
        "（calibrated 的残余差异全部来自结构性不可表达组，不是公式误差）"
    )


def _table_full_pipeline() -> str:
    """完整掩码（含模式/深度）对比：说明跨引擎整图判据结构性不可达，本任务只校准前置掩码。"""
    from tools.lut_fit import bc_transform, mode_predict
    from tools.texture_mask import compute_mask

    lines = [
        "### A/B-5 参考：完整掩码（含模式/深度）对比（说明为什么判据只能是掩码空间）",
        "",
        "| 模式 | 深度 | legacy 差异比例 | calibrated 差异比例 |",
        "|---|---:|---:|---:|",
    ]
    ps_modes = ("正片叠底", "减去", "高度", "线性高度")
    k_mode = {"正片叠底": 0, "减去": 1, "高度": 14, "线性高度": 15}
    for ps_mode in ps_modes:
        for depth in (30, 60, 100):
            for path in ("legacy", "calibrated"):
                total = 0
                diff = 0
                for ps_b in (-50, 0, 50):
                    for ps_c in (0, 25):
                        old = os.environ.get(_TEXTURE_FORMULA_ENV)
                        os.environ[_TEXTURE_FORMULA_ENV] = path
                        try:
                            b, c = texture_brightness_contrast(ps_b, ps_c)
                        finally:
                            if old is None:
                                os.environ.pop(_TEXTURE_FORMULA_ENV, None)
                            else:
                                os.environ[_TEXTURE_FORMULA_ENV] = old
                        u = bc_transform(_TEX_255, ps_b, ps_c, PS_PARAMS)
                        truth = np.floor(mode_predict(u, ps_mode, depth, PS_PARAMS) + 0.5)
                        pred = np.floor(compute_mask(
                            (_TEX_255 / 255.0).astype(np.float32), False, b, c,
                            k_mode[ps_mode], depth / 100.0).astype(np.float64) * 255.0 + 0.5)
                        d = np.abs(pred.astype(np.int64) - truth.astype(np.int64))
                        total += d.size
                        diff += int(np.count_nonzero(d > TOL))
                if path == "legacy":
                    legacy_ratio = diff / total
                else:
                    lines.append(
                        f"| {ps_mode} | {depth} | {legacy_ratio:.2%} | {diff / total:.2%} |")
    return "\n".join(lines)


def print_ab_tables() -> None:
    """打印任务报告引用的全部 A/B 表。"""
    print("## 纹理亮度/对比度公式 A/B（掩码空间，tol=2，达标线 <=2%）")
    print()
    print(_summary())
    print()
    print(_table_grid())
    print()
    print(_table_brushes())
    print()
    print(_table_convert())
    print()
    print(_table_truth())
    print()
    print(_table_full_pipeline())


if __name__ == "__main__":
    print_ab_tables()
