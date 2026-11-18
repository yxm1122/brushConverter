# -*- coding: utf-8 -*-
"""任务 08：亚像素对齐（只平移）与差异定位的行为锁定。

覆盖口径（对应任务书「验收标准」逐条）：
* align_translate() 返回的 (dx, dy) 与已知平移一致（bbox 与 centroid 两法）；
* 平移越界时 clipped_pixels > 0，且仍能给出差异比例（不崩）；
* 两张完全无关的图必须判 fail（防止对齐算法「过度收敛」）；
* verdict 三档阈值边界（2% / 10%）固定；
* 定位记录字段齐全（固定字段表）；
* PS<->Krita 基准配对至少 6 对，且每对尺寸一致；
* 对真实基准图内容做平移复原演示（先清 6 像素边框，避免平移丢内容）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.align import (  # noqa: E402
    DEFAULT_NOISE_THRESHOLD,
    align_translate,
    diff_alpha_arrays,
    shift_alpha,
    to_alpha,
)
from tools.diff_report import (  # noqa: E402
    RECORD_FIELDS,
    REQUIRED_FIELDS,
    build_record,
    classify_verdict,
    pair_baselines,
    parse_baseline_name,
    ps_to_krita_params,
)
from tools.imgdiff import diff_alpha  # noqa: E402

pytestmark = [pytest.mark.verify, pytest.mark.verify_pixels]

BASELINES = REPO_ROOT / "tests" / "baselines"
PS_MANIFEST = BASELINES / "ps" / "manifest.json"
KRITA_MANIFEST = BASELINES / "krita" / "manifest.json"
KRITA_DIR = BASELINES / "krita"


# ---------------------------------------------------------------------------
# 工具函数：造图 / 落盘
# ---------------------------------------------------------------------------

def _save_alpha(tmp_path: Path, name: str, alpha: np.ndarray) -> Path:
    """把二维 Alpha 存成 RGBA PNG（RGB 全 0），与真实基准图的存储方式一致。"""
    data = np.zeros((*alpha.shape, 4), dtype=np.uint8)
    data[:, :, 3] = np.asarray(alpha, dtype=np.uint8)
    path = tmp_path / name
    Image.fromarray(data, mode="RGBA").save(path, format="PNG")
    return path


def _make_shape(size: int = 64) -> np.ndarray:
    """造一张不对称、离四边至少 8 像素的测试图（平移不会丢内容）。"""
    alpha = np.zeros((size, size), dtype=np.uint8)
    alpha[8:40, 10:30] = 255            # 左侧竖条
    alpha[18:28, 30:52] = 180           # 中间横条（不对称）
    alpha[44:56, 40:58] = 240           # 右下角块
    alpha[8:40, 10:18] = 90             # 竖条内部的弱边（让质心不是几何对称）
    return alpha


# ---------------------------------------------------------------------------
# 一、平移复原（任务书验收标准 1）
# ---------------------------------------------------------------------------

def test_translate_shift_recovered_bbox(tmp_path):
    """把 a 平移 (3, -2) 得到副本 b，对齐应还原 (-3, +2) 且对齐后差异 0。

    约定：align_translate(a, b) 返回的是「把 b 平移回 a 需要的量」，
    所以副本被平移 (3, -2) 时，还原量就是 (-3, +2)。
    """
    a = _make_shape()
    b, clipped_when_copy = shift_alpha(a, 3, -2)
    assert clipped_when_copy == 0, "造副本时不该丢内容（测试图离边 8 像素以上）"

    path_a = _save_alpha(tmp_path, "a.png", a)
    path_b = _save_alpha(tmp_path, "b.png", b)
    before = diff_alpha(path_a, path_b)
    assert before["diff_ratio"] > 0.0, "平移过的两张图，未对齐前差异比例必须大于 0"

    aligned, (dx, dy), meta = align_translate(path_a, path_b, method="bbox")
    assert (dx, dy) == (-3, 2), f"bbox 法还原的平移量不对：({dx}, {dy})"
    assert np.array_equal(aligned, a), "bbox 法对齐后的像素与 a 不逐像素相等"

    after = diff_alpha(path_a, _save_alpha(tmp_path, "aligned_bbox.png", aligned))
    assert after["diff_ratio"] == 0.0
    assert meta["aligned_ratio"] == 0.0
    assert meta["clipped_pixels"] == 0
    assert meta["method"] == "bbox"
    # 任务书要求 meta 至少含这五个键
    for key in ("dx", "dy", "method", "clipped_pixels", "aligned_ratio"):
        assert key in meta, f"meta 缺字段 {key}"


def test_translate_shift_recovered_centroid(tmp_path):
    """同一对图换质心法，也必须还原 (-3, +2) 且对齐后差异 0。"""
    a = _make_shape()
    b, _ = shift_alpha(a, 3, -2)
    path_a = _save_alpha(tmp_path, "a.png", a)
    path_b = _save_alpha(tmp_path, "b.png", b)

    aligned, (dx, dy), meta = align_translate(path_a, path_b, method="centroid")
    assert (dx, dy) == (-3, 2), f"质心法还原的平移量不对：({dx}, {dy})"
    assert np.array_equal(aligned, a)
    assert meta["method"] == "centroid"
    assert meta["aligned_ratio"] == 0.0
    assert meta["diff_ratio_before"] > 0.0


def test_reverse_direction_with_negative_shift(tmp_path):
    """反向平移 (-4, +5)：还原量必须是 (+4, -5)，防止符号写反被单侧用例掩盖。"""
    a = _make_shape()
    b, _ = shift_alpha(a, -4, 5)
    path_a = _save_alpha(tmp_path, "a.png", a)
    path_b = _save_alpha(tmp_path, "b.png", b)
    _, (dx, dy), _ = align_translate(path_a, path_b)
    assert (dx, dy) == (4, -5)


# ---------------------------------------------------------------------------
# 二、越界不崩、clipped_pixels 有数值（任务书验收标准 4）
# ---------------------------------------------------------------------------

def test_out_of_canvas_clipped_and_still_reports_ratio(tmp_path):
    """两张内容位置差异很大、b 从画布左边缘开始时：clipped_pixels > 0 且不崩。

    构造：a 是小方块（中心 x=10），b 是横跨左侧的大块（中心 x=25）。
    外接框中心差 = -15，于是 b 会被向左推 15 像素，左侧内容被推出画布。
    """
    a = np.zeros((64, 64), dtype=np.uint8)
    a[20:30, 5:16] = 255
    b = np.zeros((64, 64), dtype=np.uint8)
    b[10:40, 0:51] = 160

    path_a = _save_alpha(tmp_path, "a.png", a)
    path_b = _save_alpha(tmp_path, "b.png", b)
    aligned, (dx, dy), meta = align_translate(path_a, path_b, method="bbox", max_shift=None)

    assert dx < 0, f"本用例预期向左推，实际 dx={dx}"
    assert meta["clipped_pixels"] > 0, "越界平移必须记录 clipped_pixels > 0"
    assert 0.0 <= meta["aligned_ratio"] <= 1.0
    assert aligned.shape == a.shape
    # 同样的用例走 diff_report：必须有完整记录且 verdict 合法（不许抛异常）
    record = build_record(
        path_a,
        path_b,
        brush="合成图",
        mode="none",
        depth=0,
        brightness=0.0,
        contrast=1.0,
        max_shift=None,
    )
    assert record["clipped_pixels"] > 0
    assert record["verdict"] in {"ok", "suspect", "fail"}
    for name in RECORD_FIELDS:
        assert name in record, f"定位记录缺字段 {name}"


def test_shift_larger_than_canvas_does_not_crash():
    """平移量不小于画布时：整张图被推出，返回全 0、不抛异常。"""
    alpha = np.full((8, 8), 200, dtype=np.uint8)
    shifted, clipped = shift_alpha(alpha, 8, 0)
    assert shifted.shape == (8, 8)
    assert int(shifted.max()) == 0
    assert clipped == 64


# ---------------------------------------------------------------------------
# 三、无关图必须 fail（任务书验收标准 3，硬性要求 3）
# ---------------------------------------------------------------------------

def test_unrelated_noise_images_verdict_fail(tmp_path):
    """两张互不相关的随机噪点图：任何平移都救不回来，verdict 必须是 fail。"""
    rng = np.random.default_rng(20261118)
    a = rng.integers(0, 256, size=(64, 64), dtype=np.uint8)
    b = rng.integers(0, 256, size=(64, 64), dtype=np.uint8)
    path_a = _save_alpha(tmp_path, "noise_a.png", a)
    path_b = _save_alpha(tmp_path, "noise_b.png", b)

    # 默认有最大位移限制
    record = build_record(path_a, path_b, brush="无关图", mode="none", depth=0)
    assert record["verdict"] == "fail", (
        f"无关图被判成了 {record['verdict']}（差异 {record['diff_ratio_after']:.4f}），"
        "说明对齐过度收敛"
    )
    # 即使完全放开位移上限，也必须 fail：证明不是靠 max_shift 兜住的
    record_unlimited = build_record(
        path_a, path_b, brush="无关图", mode="none", depth=0, max_shift=None
    )
    assert record_unlimited["verdict"] == "fail", "放开位移上限后无关图竟被判合格"


def test_unrelated_structured_images_verdict_fail(tmp_path):
    """结构完全不同的两张图（斜条纹 vs 棋盘格）同样必须 fail。"""
    size = 64
    yy, xx = np.mgrid[0:size, 0:size]
    stripes = np.where((xx + yy) % 12 < 6, 230, 20).astype(np.uint8)
    checker = np.where(((xx // 8) + (yy // 8)) % 2 == 0, 240, 10).astype(np.uint8)
    path_a = _save_alpha(tmp_path, "stripes.png", stripes)
    path_b = _save_alpha(tmp_path, "checker.png", checker)
    record = build_record(path_a, path_b, brush="无关图2", mode="none", depth=0, max_shift=None)
    assert record["verdict"] == "fail"
    assert record["diff_ratio_after"] > 0.10


# ---------------------------------------------------------------------------
# 四、判定阈值与记录字段（固定口径）
# ---------------------------------------------------------------------------

def test_verdict_thresholds_boundary():
    """三档阈值边界写死：<=2% ok；2%~10% suspect；>10% fail。"""
    assert classify_verdict(0.0) == "ok"
    assert classify_verdict(0.02) == "ok"
    assert classify_verdict(0.0200001) == "suspect"
    assert classify_verdict(0.10) == "suspect"
    assert classify_verdict(0.1000001) == "fail"
    assert classify_verdict(1.0) == "fail"


def test_required_fields_are_the_task_fields():
    """任务书要求的 12 个字段必须一字不差地在固定字段表里。"""
    assert REQUIRED_FIELDS == (
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
    assert len(RECORD_FIELDS) == len(set(RECORD_FIELDS))


def test_record_reports_before_and_after(tmp_path):
    """必须同时给出对齐前与对齐后两个差异比例（只给后者等于隐藏了本来差多少）。"""
    a = _make_shape()
    b, _ = shift_alpha(a, 2, 1)
    path_a = _save_alpha(tmp_path, "a.png", a)
    path_b = _save_alpha(tmp_path, "b.png", b)
    record = build_record(path_a, path_b, brush="合成图", mode="none", depth=0)
    assert record["diff_ratio_before"] > 0.0
    assert record["diff_ratio_after"] == 0.0
    assert record["diff_ratio_after"] < record["diff_ratio_before"]
    assert record["max_delta"] == 0
    assert record["verdict"] == "ok"


# ---------------------------------------------------------------------------
# 五、口径交叉验证：数组差异 == imgdiff 落盘差异
# ---------------------------------------------------------------------------

def test_array_diff_matches_imgdiff(tmp_path):
    """align.diff_alpha_arrays 与 imgdiff.diff_alpha 对同一对图必须给出同样数字。"""
    rng = np.random.default_rng(7)
    a = rng.integers(0, 256, size=(32, 32), dtype=np.uint8)
    b = a.copy()
    b[0:5, 0:5] = 0
    b[10, 10] = 255
    path_a = _save_alpha(tmp_path, "a.png", a)
    path_b = _save_alpha(tmp_path, "b.png", b)

    from_file = diff_alpha(path_a, path_b, tol=2)
    from_array = diff_alpha_arrays(a, b, tol=2)
    assert from_array["diff_pixels"] == from_file["diff_pixels"]
    assert from_array["max_delta"] == from_file["max_delta"]
    assert from_array["diff_ratio"] == pytest.approx(from_file["diff_ratio"])


# ---------------------------------------------------------------------------
# 六、噪声过滤：Alpha <= 阈值的淡影不该撑大外接框
# ---------------------------------------------------------------------------

def test_noise_pixels_do_not_dilate_bbox(tmp_path):
    """在角上加 Alpha=5 的淡影（<= 噪声阈值 10），对齐量必须仍等于真实平移。"""
    a = _make_shape()
    b, _ = shift_alpha(a, 4, 3)
    b[0, 0] = DEFAULT_NOISE_THRESHOLD          # 恰好等于阈值 -> 不算有效像素
    b[0, 63] = DEFAULT_NOISE_THRESHOLD - 1     # 低于阈值 -> 不算
    path_a = _save_alpha(tmp_path, "a.png", a)
    path_b = _save_alpha(tmp_path, "b.png", b)
    _, (dx, dy), _ = align_translate(path_a, path_b, method="bbox")
    assert (dx, dy) == (-4, -3), f"淡影把外接框撑大了，对齐量变成 ({dx}, {dy})"


def test_max_shift_guard_blocks_huge_shift(tmp_path):
    """位移超过 max_shift 时不做平移并标记 shift_limited（防止过度收敛）。"""
    a = _make_shape()
    b, _ = shift_alpha(a, 30, 0)
    path_a = _save_alpha(tmp_path, "a.png", a)
    path_b = _save_alpha(tmp_path, "b.png", b)
    _, (dx, dy), meta = align_translate(path_a, path_b, max_shift=5)
    assert (dx, dy) == (0, 0)
    assert meta["shift_limited"] is True
    assert meta["clipped_pixels"] == 0


# ---------------------------------------------------------------------------
# 七、基准清单配对（四元组）
# ---------------------------------------------------------------------------

def test_parse_baseline_name_both_styles():
    """PS 与 Krita 两种文件名写法都要能解析出四元组。"""
    ps = parse_baseline_name("ps__定量测试v2__正片叠底__d100_b0_c50.png")
    assert ps is not None
    assert (ps["source"], ps["mode"], ps["depth"]) == ("ps", "正片叠底", 100)
    assert (ps["brightness"], ps["contrast"]) == (0.0, 50.0)

    krita = parse_baseline_name("krita__hist_d100__正片叠底__b0.10_c1.png")
    assert krita is not None
    assert (krita["source"], krita["mode"], krita["depth"]) == ("krita", "正片叠底", 100)
    assert krita["brightness"] == pytest.approx(0.10)
    assert krita["contrast"] == pytest.approx(1.0)

    # 不符合 4 段命名规范的必须返回 None，而不是猜
    assert parse_baseline_name("随便一张图.png") is None


def test_ps_to_krita_params_known_values():
    """换算的关键格点（与 src/brush_converter/convert.py 一致的抽样值）。"""
    assert ps_to_krita_params(0, 0) == (0.1, 1.0)
    assert ps_to_krita_params(50, 0) == (-0.1, 1.0)
    assert ps_to_krita_params(-25, 0) == pytest.approx((0.2, 1.0))
    assert ps_to_krita_params(0, -50) == pytest.approx((0.1, 0.5))
    assert ps_to_krita_params(0, 50) == pytest.approx((0.1, 2.0))
    assert ps_to_krita_params(0, 100)[1] == 2.0  # 夹到 Krita 上限


def test_pair_baselines_finds_at_least_six_pairs():
    """两份子清单必须能配出 >= 6 对，且每对同模式同深度、尺寸一致。"""
    if not (PS_MANIFEST.exists() and KRITA_MANIFEST.exists()):
        pytest.skip("缺少子目录 manifest（tests/baselines/ps 或 krita 未就绪）")
    pair_result = pair_baselines(PS_MANIFEST, KRITA_MANIFEST)
    pairs = pair_result["pairs"]
    assert len(pairs) >= 6, f"只配出 {len(pairs)} 对，少于任务书要求的 6 对"
    for pair in pairs:
        assert pair["ps"]["mode"] == pair["krita"]["mode"]
        assert pair["ps"]["depth"] == pair["krita"]["depth"]
        with Image.open(pair["ps"]["path"]) as image_a, Image.open(pair["krita"]["path"]) as image_b:
            assert image_a.size == image_b.size, f"{pair['ps']['file']} 与 {pair['krita']['file']} 尺寸不一致"


def test_baseline_pair_record_fields_complete():
    """真实基准对上跑一条定位记录：字段齐全、verdict 合法、before/after 都是比例。"""
    if not (PS_MANIFEST.exists() and KRITA_MANIFEST.exists()):
        pytest.skip("缺少子目录 manifest")
    pairs = pair_baselines(PS_MANIFEST, KRITA_MANIFEST)["pairs"]
    if not pairs:
        pytest.skip("没有可配对的基准对")
    pair = pairs[0]
    record = build_record(
        pair["ps"]["path"],
        pair["krita"]["path"],
        brush=pair["brush"],
        mode=pair["mode"],
        depth=pair["depth"],
        brightness=pair["krita_brightness"],
        contrast=pair["krita_contrast"],
        ps_brightness=pair["ps_brightness"],
        ps_contrast=pair["ps_contrast"],
    )
    for name in RECORD_FIELDS:
        assert name in record
    assert record["verdict"] in {"ok", "suspect", "fail"}
    assert 0.0 <= record["diff_ratio_before"] <= 1.0
    assert 0.0 <= record["diff_ratio_after"] <= 1.0
    assert isinstance(record["dx"], int) and isinstance(record["dy"], int)


# ---------------------------------------------------------------------------
# 八、真实基准图上的平移复原演示（证明对齐在真实纹理内容上有效）
# ---------------------------------------------------------------------------

def test_real_baseline_translation_demo(tmp_path, capsys=None):
    """取 6 张真实 Krita 基准图，人工平移后复原（演示对齐在真实纹理上确实能归零）。

    做法：先清 6 像素边框（避免人为平移时把内容推出画布），再按 (3,-2)/(-2,3)/(4,1)
    循环平移；对齐必须还原出相反的平移量，且对齐后差异 0.00%。
    派生图只写临时目录，**不落仓库、不改 tests/baselines/**。
    """
    candidates = [
        KRITA_DIR / "krita__hist_d100__正片叠底__b0_c0.png",
        KRITA_DIR / "krita__hist_d100__正片叠底__b0_c1.33.png",
        KRITA_DIR / "krita__hist_d100__正片叠底__b-0.10_c1.png",
        KRITA_DIR / "krita__hist_d10__线性高度__b0_c0.png",
        KRITA_DIR / "krita__hist_d10__线性高度__b0.3_c1.png",
        KRITA_DIR / "krita__hist_d10__线性高度__b0_c2.png",
    ]
    existing = [path for path in candidates if path.exists()]
    if not existing:
        pytest.skip("缺少 tests/baselines/krita 真实基准图")

    shifts = [(3, -2), (-2, 3), (4, 1)]
    rows = []
    for index, source in enumerate(existing):
        alpha = to_alpha(source).copy()
        alpha[:6, :] = 0
        alpha[-6:, :] = 0
        alpha[:, :6] = 0
        alpha[:, -6:] = 0
        base_path = _save_alpha(tmp_path, f"base_{index}_{source.stem}.png", alpha)
        want_shift = shifts[index % len(shifts)]
        shifted, clipped = shift_alpha(alpha, *want_shift)
        assert clipped == 0
        shifted_path = _save_alpha(tmp_path, f"shifted_{index}_{source.stem}.png", shifted)

        record = build_record(base_path, shifted_path, brush="平移复原演示", mode="none", depth=0)
        assert (record["dx"], record["dy"]) == (-want_shift[0], -want_shift[1]), (
            f"真实基准图的平移复原量不对：人为平移 {want_shift}，"
            f"还原 {(record['dx'], record['dy'])}"
        )
        assert record["diff_ratio_before"] > 0.0
        assert record["diff_ratio_after"] == 0.0
        assert record["verdict"] == "ok"
        rows.append(
            f"| {source.name} | {want_shift} | {record['diff_ratio_before'] * 100:.2f}% | "
            f"{record['diff_ratio_after'] * 100:.2f}% | ({record['dx']},{record['dy']}) | "
            f"{record['max_delta']} | {record['verdict']} |"
        )

    print("真实基准图平移复原演示（人为平移后对齐）：")
    print("| 图 | 人为平移 | 对齐前差异 | 对齐后差异 | 还原平移 | 最大差值 | verdict |")
    print("|---|---|---|---|---|---|---|")
    for row in rows:
        print(row)
    assert len(rows) >= 6
