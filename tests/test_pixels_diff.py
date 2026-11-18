"""任务 03：像素级比对（tools/imgdiff.py）的行为锁定。

覆盖口径：
* 只看 Alpha 通道；
* diff_ratio 分母是 width*height（全部像素）；
* 两张图尺寸不一致 -> ValueError，异常文本里同时出现两个尺寸；
* 真实基准图（research/krita结果）与自身比 -> 差异 0；
* tests/baselines/handmade 的两张手工基准与 manifest 登记一致。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.imgdiff import (  # noqa: E402
    DEFAULT_RATIO_THRESHOLD,
    DEFAULT_TOL,
    diff_alpha,
    passes,
)

pytestmark = [pytest.mark.verify, pytest.mark.verify_pixels]

HERE = Path(__file__).parent
BASELINES = HERE / "baselines"
HANDMADE = BASELINES / "handmade"
HANDMADE_MANIFEST = HANDMADE / "manifest.json"

# 任务书指定的真实基准图（research/ 只读；缺了就跳过，不许拿别的图顶替）
RESEARCH_IMAGE = REPO_ROOT / "research" / "krita结果" / "正片叠底_d100" / "b0_c0.png"


def _save_alpha(tmp_path: Path, name: str, alpha: np.ndarray) -> Path:
    """把二维 Alpha 存成 RGBA PNG（RGB 全黑），返回路径。"""
    data = np.zeros((*alpha.shape, 4), dtype=np.uint8)
    data[:, :, 3] = alpha
    path = tmp_path / name
    Image.fromarray(data, mode="RGBA").save(path, format="PNG")
    return path


def test_identical_images_zero_diff(tmp_path):
    """两张完全相同的图：差异 0 像素、比例 0、最大差值 0。"""
    alpha = np.tile(np.arange(8, dtype=np.uint8) * 30, (6, 1))
    a = _save_alpha(tmp_path, "a.png", alpha)
    b = _save_alpha(tmp_path, "b.png", alpha.copy())
    result = diff_alpha(a, b)
    assert result["width"] == 8 and result["height"] == 6
    assert result["diff_pixels"] == 0
    assert result["diff_ratio"] == 0.0
    assert result["max_delta"] == 0


def test_all_pixels_differ_ratio_one(tmp_path):
    """全像素差 255：比例必须是 1.0（不是 255 或别的数）。"""
    a = _save_alpha(tmp_path, "a.png", np.zeros((3, 2), dtype=np.uint8))
    b = _save_alpha(tmp_path, "b.png", np.full((3, 2), 255, dtype=np.uint8))
    result = diff_alpha(a, b)
    assert result["diff_pixels"] == 6
    assert result["diff_ratio"] == 1.0
    assert result["max_delta"] == 255


def test_single_pixel_diff_ratio_uses_full_pixel_count(tmp_path):
    """仅 1 个像素差：比例 = 1/(W*H)，分母必须是全部像素而不是非零像素数。"""
    alpha = np.zeros((4, 4), dtype=np.uint8)
    alpha[2, 3] = 200
    a = _save_alpha(tmp_path, "a.png", np.zeros((4, 4), dtype=np.uint8))
    b = _save_alpha(tmp_path, "b.png", alpha)
    result = diff_alpha(a, b)
    assert result["diff_pixels"] == 1
    assert result["diff_ratio"] == pytest.approx(1 / 16)
    assert result["max_delta"] == 200


def test_size_mismatch_raises_with_both_sizes(tmp_path):
    """尺寸不一致：抛 ValueError，异常文本里同时出现两个尺寸。"""
    a = _save_alpha(tmp_path, "a.png", np.zeros((4, 3), dtype=np.uint8))
    b = _save_alpha(tmp_path, "b.png", np.zeros((2, 5), dtype=np.uint8))
    with pytest.raises(ValueError) as excinfo:
        diff_alpha(a, b)
    message = str(excinfo.value)
    assert "尺寸不一致" in message, f"报错文本没说明尺寸不一致：{message}"
    assert "4x3" in message, f"报错文本缺 A 的尺寸：{message}"
    assert "2x5" in message, f"报错文本缺 B 的尺寸：{message}"


def test_tolerance_boundary(tmp_path):
    """差值 <= tol 不算差异，> tol 才算；默认 tol=2。"""
    a = _save_alpha(tmp_path, "a.png", np.zeros((3, 3), dtype=np.uint8))
    alpha_b = np.zeros((3, 3), dtype=np.uint8)
    alpha_b[0, 0] = DEFAULT_TOL          # 恰好等于容差 -> 不算差异
    alpha_b[1, 1] = DEFAULT_TOL + 1      # 超过容差 -> 算差异
    b = _save_alpha(tmp_path, "b.png", alpha_b)

    result = diff_alpha(a, b)
    assert result["diff_pixels"] == 1, "容差边界处理错误：<=tol 不应计入差异"
    assert result["max_delta"] == DEFAULT_TOL + 1

    strict = diff_alpha(a, b, tol=0)
    assert strict["diff_pixels"] == 2, "tol=0 时两个非零差值都该计入"


def test_research_baseline_self_diff_is_zero():
    """真实基准图与自身比：差异必须为 0（证明读图链路没丢 Alpha）。"""
    if not RESEARCH_IMAGE.exists():
        pytest.skip(f"缺少基准图 {RESEARCH_IMAGE}（research/ 只读，缺样本时跳过而非伪造）")
    result = diff_alpha(RESEARCH_IMAGE, RESEARCH_IMAGE)
    # 打通时打印一条人能读的结论（加 -s 可见），满足「屏幕上能看到差异 0 像素」的验收要求
    print(
        f"像素级比对：{RESEARCH_IMAGE.name} 与自身比 → 尺寸 "
        f"{result['width']}x{result['height']}、差异 {result['diff_pixels']} 像素、"
        f"比例 {result['diff_ratio']:.6f}、最大差值 {result['max_delta']}"
    )
    assert result["diff_pixels"] == 0
    assert result["diff_ratio"] == 0.0
    assert result["max_delta"] == 0
    assert result["width"] > 0 and result["height"] > 0


def test_passes_threshold():
    """默认 2% 阈值：等于阈值算通过，超过算不通过。"""
    assert DEFAULT_RATIO_THRESHOLD == pytest.approx(0.02)
    assert passes({"diff_ratio": 0.02}) is True
    assert passes({"diff_ratio": 0.0}) is True
    assert passes({"diff_ratio": 0.0201}) is False
    assert passes({"diff_ratio": 0.05}, threshold=0.1) is True


def test_handmade_baselines_registered_and_identical(tmp_path):
    """handmade 两张基准：manifest 与文件一一对应，且与自身比差异为 0。"""
    manifest = json.loads(HANDMADE_MANIFEST.read_text(encoding="utf-8"))
    entries = manifest["entries"]
    assert len(entries) == 2, f"handmade 基准应恰好 2 条登记，实际 {len(entries)}"

    files_on_disk = sorted(p.name for p in HANDMADE.glob("*.png"))
    registered = sorted(entry["file"] for entry in entries)
    assert files_on_disk == registered, f"manifest 与磁盘文件不一致：磁盘={files_on_disk} 登记={registered}"

    for entry in entries:
        path = HANDMADE / entry["file"]
        assert entry["source"] == "handmade", f"{entry['file']} 的 source 不是 handmade"
        # 命名规范：<来源>__<标识>__<模式>__<关键参数>.png
        assert entry["file"].count("__") >= 3, f"{entry['file']} 不符合命名规范（缺 __ 分段）"
        assert entry["file"].startswith("handmade__"), f"{entry['file']} 来源段不是 handmade"
        with Image.open(path) as image:
            assert image.size == (64, 64), f"{entry['file']} 尺寸不是 64x64：{image.size}"
        assert path.stat().st_size <= 2 * 1024 * 1024, f"{entry['file']} 超过 2MB 禁入限制"
        assert entry["size"] == "64x64", f"{entry['file']} 的 size 字段与实际不符"
        # sha256 校验：manifest 记的哈希必须等于当前文件（图被换过要立刻发现）
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == entry["sha256"], f"{entry['file']} 的 sha256 与 manifest 不符"
        result = diff_alpha(path, path)
        assert result["diff_ratio"] == 0.0
        assert result["max_delta"] == 0
