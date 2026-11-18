"""像素级比对：按 Alpha 通道逐像素比较两张同尺寸 PNG（任务 03）。

口径（写死，不要随手改）
------------------------
* 只看 Alpha 通道：Photoshop 纹理测试导出的 PNG 是 RGBA，RGB 三通道全黑，
  纹理信息主要落在 Alpha 上；本版不比较 RGB，避免把「黑底差异」当成笔刷差异。
  将来若要扩展到 RGB，另开参数/新函数，不要在 diff_alpha 里偷偷加。
* 差异点：abs(A - B) > tol 的像素；tol 默认 2/255，用来吸收 PNG 量化与
  抗锯齿造成的 ±1~2 抖动（任务书默认值）。
* diff_ratio 的分母是 width*height（**全部像素**），不是「非零像素数」，
  也不是「有笔迹的像素数」；否则空白区占多数的图会把差异比例放大到失真。
* 尺寸不一致直接抛 ValueError（本版不做缩放/对齐，对齐是任务 08 的事），
  异常文本里同时给出两个尺寸，方便定位拿错了哪张图。
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# 差异点阈值默认 2/255：吸收无意义的量化抖动，又不至于漏掉真实偏移
DEFAULT_TOL = 2

# 判定「通过」的默认差异比例阈值（2%）：任务 03 文档口径，任务 08/09 沿用
DEFAULT_RATIO_THRESHOLD = 0.02


def _load_alpha(path) -> np.ndarray:
    """读一张图，只取 Alpha 通道（uint8，二维）。

    用 convert("RGBA") 而不是 convert("RGB")：后者会把 Alpha 丢掉，
    导致两张图怎么比都是 0（README 里列过的经典坑）。
    """
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        return np.asarray(rgba, dtype=np.uint8)[:, :, 3]


def diff_alpha(path_a, path_b, tol: int = DEFAULT_TOL) -> dict:
    """按 Alpha 通道比较两张同尺寸 PNG。

    返回 {"width", "height", "diff_pixels", "diff_ratio", "max_delta"}：
    diff_ratio = diff_pixels / (width*height)（分母是全部像素，见模块 docstring）；
    max_delta 为 Alpha 差值的最大绝对值（0..255）。

    两张图尺寸不同时抛 ValueError("尺寸不一致：A=HxW, B=HxW")。
    """
    alpha_a = _load_alpha(path_a)
    alpha_b = _load_alpha(path_b)
    if alpha_a.shape != alpha_b.shape:
        a_h, a_w = alpha_a.shape
        b_h, b_w = alpha_b.shape
        raise ValueError(f"尺寸不一致：A={a_h}x{a_w}, B={b_h}x{b_w}")

    height, width = alpha_a.shape
    total = width * height
    if total == 0:
        raise ValueError("图像尺寸为 0，无法比对")

    # 先转 int16 再相减：uint8 直接相减会回绕（0-255 变成 1）
    delta = np.abs(alpha_a.astype(np.int16) - alpha_b.astype(np.int16))
    diff_pixels = int(np.count_nonzero(delta > tol))
    return {
        "width": int(width),
        "height": int(height),
        "diff_pixels": diff_pixels,
        "diff_ratio": diff_pixels / total,
        "max_delta": int(delta.max()),
    }


def passes(result: dict, threshold: float = DEFAULT_RATIO_THRESHOLD) -> bool:
    """按差异比例阈值判定是否通过（默认 2%）。

    只做「差异比例」这一条判据；是否再叠加 max_delta/局部区域判据由任务 08/09 决定。
    """
    return result["diff_ratio"] <= threshold
