# -*- coding: utf-8 -*-
"""亚像素对齐（只做平移）：把「整体差 1~2 像素」从差异比例里剥离出去（任务 08）。

本模块的定位
------------
两张图直接逐像素相减，会被整体偏移放大成一片红（见 docs/比对与定位说明.md）。
本模块在比对之前先求一个**整数像素的平移量**，把 b 平移回 a 的位置，再交给
tools/imgdiff.py 的差异口径去算差异比例。

口径（写死，不要随手改）
------------------------
* **只做平移**：不缩放、不旋转、不做互相关穷举搜索、不做亚像素插值。
  这是使用者明确拍板的边界（任务书「硬性要求 1」）：任何「为了凑分数」的
  几何变换都会把真问题对齐掉。平移量由外接框中心（默认）或质心求出，
  取整后应用；原始浮点值放在 meta["dx_raw"]/meta["dy_raw"] 里备查。
* **位置统计先滤噪**：求外接框/质心前先把 Alpha <= noise_threshold（默认 10）
  的像素当 0。半透明噪声与抗锯齿边缘会把外接框撑大，导致对齐量偏离
  （任务书「常见问题」第 1 条：先按 Alpha>10 过滤再做外接框）。
* **画布外一律填 0**：平移后落到画布外的部分不存在，按 Alpha=0 填充，
  并把「原本非零、被推出画布」的像素数记进 meta["clipped_pixels"]。
* **差异口径复用 tools/imgdiff.py**：只比 Alpha 通道、tol 默认 2、
  分母为 width*height（全部像素）。本模块不重新定义差异口径；
  数组输入无法直接调用 diff_alpha()（它吃文件路径），因此在这里按同样
  三行公式复刻一份 diff_alpha_arrays()，并由 tests/test_align.py 用
  diff_alpha() 做交叉验证，防止两套口径漂移。

返回约定
--------
    b_aligned, (dx, dy), meta = align_translate(a, b, tol=2, method="bbox")

* b_aligned：uint8 二维 ndarray（Alpha 通道），已按 (dx, dy) 平移、画布外填 0；
* (dx, dy)：本次实际应用的整数平移量（把 b 平移 (dx, dy) 后与 a 对齐）；
* meta：至少含 dx、dy、method、clipped_pixels、aligned_ratio 五个键（任务书要求），
  另有 dx_raw/dy_raw、diff_ratio_before、max_delta_*、diff_bbox_* 等辅助字段。
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image

# 差异点阈值统一取自 tools/imgdiff.py（任务 09 合并重复实现：原先这里又写了一个 2）
from tools.imgdiff import DEFAULT_TOL

# 位置统计（外接框/质心）的噪声过滤阈值：Alpha <= 该值的像素不参与定位。
# 取 10 而不是 0：PNG 量化与抗锯齿会在边缘留下 1~9 的淡影，会把外接框撑大。
DEFAULT_NOISE_THRESHOLD = 10

# 支持的对齐方法：外接框中心（默认）/ Alpha 加权质心
SUPPORTED_METHODS = ("bbox", "centroid")


# ---------------------------------------------------------------------------
# 一、输入归一化
# ---------------------------------------------------------------------------

def to_alpha(source) -> np.ndarray:
    """把「文件路径 / PIL.Image / ndarray」统一转成 uint8 二维 Alpha 数组。

    - 路径：按 RGBA 读，只取第 4 通道（Alpha）。用 convert("RGBA") 而不是
      convert("RGB")，否则 Alpha 被丢掉，两张图怎么比都是 0。
    - ndarray：支持 (H, W) 二维 Alpha，或 (H, W, 4) 的 RGBA（取第 4 通道）；
      三通道 RGB 没有 Alpha 可比，直接报错，避免偷偷拿 RGB 通道顶替。
    """
    if isinstance(source, (str, os.PathLike)):
        with Image.open(source) as image:
            rgba = image.convert("RGBA")
            return np.asarray(rgba, dtype=np.uint8)[:, :, 3]
    if isinstance(source, Image.Image):
        return np.asarray(source.convert("RGBA"), dtype=np.uint8)[:, :, 3]

    arr = np.asarray(source)
    if arr.ndim == 3:
        if arr.shape[2] != 4:
            raise ValueError(
                f"只接受 Alpha 二维数组或 RGBA(H,W,4) 数组，收到形状 {arr.shape}；"
                "RGB 没有 Alpha，不能顶替"
            )
        arr = arr[:, :, 3]
    if arr.ndim != 2:
        raise ValueError(f"Alpha 数组必须是二维，收到形状 {arr.shape}")
    if arr.dtype != np.uint8:
        # 非 uint8 输入（例如 float 0..1 或 int16）按四舍五入并截断到 0..255，
        # 避免「uint8 以外直接 astype」把 1.0 变成 1 这种静默错误。
        arr = np.clip(np.rint(np.asarray(arr, dtype=np.float64)), 0, 255).astype(np.uint8)
    return arr


# ---------------------------------------------------------------------------
# 二、位置统计：外接框中心 / 质心
# ---------------------------------------------------------------------------

def _position(alpha: np.ndarray, method: str, noise_threshold: int) -> tuple[float, float] | None:
    """求图像「在哪」：返回 (cx, cy) 浮点坐标；整张图都不超过噪声阈值时返回 None。

    bbox：Alpha > 阈值的像素的外接框中心（min+max)/2 —— 对外接框而言，
          「像素坐标」与「像素中心坐标」两种约定给出的差值相同，对齐量不受影响；
    centroid：Alpha > 阈值的像素按 Alpha 值加权的质心（形状更依赖质量分布，
          对细长/带渐变笔迹通常比外接框稳）。
    """
    if method not in SUPPORTED_METHODS:
        raise ValueError(f"不支持的对齐方法 {method!r}，只允许 {SUPPORTED_METHODS}")
    ys, xs = np.nonzero(alpha > noise_threshold)
    if ys.size == 0:
        return None
    if method == "bbox":
        cx = (float(xs.min()) + float(xs.max())) / 2.0
        cy = (float(ys.min()) + float(ys.max())) / 2.0
        return cx, cy
    weights = alpha[ys, xs].astype(np.float64)
    total = float(weights.sum())
    if total <= 0.0:  # 理论上不可达（已过滤 > 阈值），保险起见退化成外接框
        return _position(alpha, "bbox", noise_threshold)
    cx = float((xs.astype(np.float64) * weights).sum() / total)
    cy = float((ys.astype(np.float64) * weights).sum() / total)
    return cx, cy


# ---------------------------------------------------------------------------
# 三、平移与差异
# ---------------------------------------------------------------------------

def shift_alpha(alpha: np.ndarray, dx: int, dy: int) -> tuple[np.ndarray, int]:
    """把 Alpha 按 (dx, dy) 平移（正 dx 向右、正 dy 向下），画布外填 0。

    返回 (平移后的数组, clipped_pixels)：
    clipped_pixels = 原本 Alpha>0、但平移后落到画布外因而被丢掉的像素数。
    （Alpha=0 的像素被丢掉没有信息损失，不计入。）
    """
    src = np.asarray(alpha, dtype=np.uint8)
    height, width = src.shape
    out = np.zeros((height, width), dtype=np.uint8)

    dst_x0, dst_x1 = max(0, dx), min(width, width + dx)
    dst_y0, dst_y1 = max(0, dy), min(height, height + dy)
    src_x0, src_x1 = max(0, -dx), min(width, width - dx)
    src_y0, src_y1 = max(0, -dy), min(height, height - dy)

    total_nonzero = int(np.count_nonzero(src))
    if dst_x1 <= dst_x0 or dst_y1 <= dst_y0:
        # 位移量不小于画布尺寸：整张图都被推出去，等价于全 0
        return out, total_nonzero

    region = src[src_y0:src_y1, src_x0:src_x1]
    out[dst_y0:dst_y1, dst_x0:dst_x1] = region
    kept_nonzero = int(np.count_nonzero(region))
    return out, total_nonzero - kept_nonzero


def diff_bbox(alpha_a: np.ndarray, alpha_b: np.ndarray, tol: int) -> dict | None:
    """差异像素的外接框（用于「差异落在哪一块」的定位），无差异时返回 None。"""
    delta = np.abs(alpha_a.astype(np.int16) - alpha_b.astype(np.int16))
    mask = delta > tol
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return None
    return {
        "x": int(xs.min()),
        "y": int(ys.min()),
        "width": int(xs.max() - xs.min() + 1),
        "height": int(ys.max() - ys.min() + 1),
    }


def diff_alpha_arrays(alpha_a: np.ndarray, alpha_b: np.ndarray, tol: int = DEFAULT_TOL) -> dict:
    """按 tools/imgdiff.diff_alpha() 的口径比较两个 Alpha 数组（不落盘）。

    口径与 imgdiff 完全一致：差异点 = abs(A-B) > tol；分母 = width*height
    （全部像素，不是非零像素数）；尺寸不一致抛 ValueError 并同时给出两个尺寸。
    tests/test_align.py 里用 diff_alpha() 对同一对图做交叉验证。
    """
    a = np.asarray(alpha_a, dtype=np.uint8)
    b = np.asarray(alpha_b, dtype=np.uint8)
    if a.shape != b.shape:
        raise ValueError(f"尺寸不一致：A={a.shape[0]}x{a.shape[1]}, B={b.shape[0]}x{b.shape[1]}")
    height, width = a.shape
    total = width * height
    if total == 0:
        raise ValueError("图像尺寸为 0，无法比对")
    delta = np.abs(a.astype(np.int16) - b.astype(np.int16))
    diff_pixels = int(np.count_nonzero(delta > tol))
    return {
        "width": int(width),
        "height": int(height),
        "diff_pixels": diff_pixels,
        "diff_ratio": diff_pixels / total,
        "max_delta": int(delta.max()),
    }


# ---------------------------------------------------------------------------
# 四、主函数：对齐
# ---------------------------------------------------------------------------

def align_translate(
    a,
    b,
    tol: int = DEFAULT_TOL,
    method: str = "bbox",
    *,
    noise_threshold: int = DEFAULT_NOISE_THRESHOLD,
    max_shift: int | None = None,
):
    """把 b 平移回 a 的位置，返回 (b_aligned, (dx, dy), meta)。

    参数
    ----
    a, b : 路径 / PIL.Image / ndarray —— 要对齐的两张图（只比 Alpha）。
    tol  : 差异点阈值，透传给差异统计（默认 2，与 imgdiff 一致）。
    method : "bbox"（外接框中心，默认）或 "centroid"（Alpha 加权质心）。
    noise_threshold : 位置统计前的噪声过滤阈值（默认 10）。
    max_shift : 允许的最大平移像素数；None 表示不限制。超出上限时**不做平移**
                （dx=dy=0）并在 meta["shift_limited"] 标记，理由是任务书
                「常见问题」第 3 条：放开无限平移会把无关图也拉到相似位置，
                造成过度收敛。限制的是「救不回来就别硬救」，不是缩放或旋转。

    返回
    ----
    (b_aligned, (dx, dy), meta)
      b_aligned : uint8 二维数组，已平移、画布外填 0；
      (dx, dy)  : 实际应用的整数平移量（把 b 平移 (dx,dy) 即与 a 对齐）；
      meta      : 见模块 docstring（含 dx/dy/method/clipped_pixels/aligned_ratio）。
    """
    alpha_a = to_alpha(a)
    alpha_b = to_alpha(b)
    if alpha_a.shape != alpha_b.shape:
        raise ValueError(
            f"尺寸不一致：A={alpha_a.shape[0]}x{alpha_a.shape[1]}, "
            f"B={alpha_b.shape[0]}x{alpha_b.shape[1]}（本模块只做平移，不做缩放）"
        )

    pos_a = _position(alpha_a, method, noise_threshold)
    pos_b = _position(alpha_b, method, noise_threshold)

    # 有一张图整张都低于噪声阈值：无从判断位置，按「不平移」处理并如实标记
    if pos_a is None or pos_b is None:
        dx_raw = dy_raw = 0.0
        empty_side = "a" if pos_a is None else "b"
        note = f"图像 {empty_side} 全图 Alpha <= {noise_threshold}，无法定位，按不平移处理"
    else:
        dx_raw = pos_a[0] - pos_b[0]
        dy_raw = pos_a[1] - pos_b[1]
        note = ""

    dx = int(round(dx_raw))
    dy = int(round(dy_raw))
    shift_limited = False
    if max_shift is not None and (abs(dx) > max_shift or abs(dy) > max_shift):
        shift_limited = True
        note = (
            f"计算出的平移 ({dx}, {dy}) 超出上限 max_shift={max_shift}，"
            "为避免把无关图强行拉到相似位置，本次不做平移"
        )
        dx = dy = 0

    b_aligned, clipped = shift_alpha(alpha_b, dx, dy)

    before = diff_alpha_arrays(alpha_a, alpha_b, tol)
    after = diff_alpha_arrays(alpha_a, b_aligned, tol)

    meta = {
        # 任务书要求的五个键
        "dx": dx,
        "dy": dy,
        "method": method,
        "clipped_pixels": int(clipped),
        "aligned_ratio": after["diff_ratio"],
        # 辅助字段（便于定位与复盘）
        "dx_raw": dx_raw,
        "dy_raw": dy_raw,
        "noise_threshold": int(noise_threshold),
        "max_shift": max_shift,
        "shift_limited": shift_limited,
        "note": note,
        "width": after["width"],
        "height": after["height"],
        "diff_pixels_before": before["diff_pixels"],
        "diff_ratio_before": before["diff_ratio"],
        "max_delta_before": before["max_delta"],
        "diff_pixels_after": after["diff_pixels"],
        "max_delta_after": after["max_delta"],
        "diff_bbox_before": diff_bbox(alpha_a, alpha_b, tol),
        "diff_bbox_after": diff_bbox(alpha_a, b_aligned, tol),
    }
    return b_aligned, (dx, dy), meta
