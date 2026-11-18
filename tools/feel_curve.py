# -*- coding: utf-8 -*-
"""手感曲线提取（任务 14）。

从一张「沿水平方向画的一笔」的 RGBA PNG 里提取两条曲线：

1. **宽度曲线 width**：沿笔画方向（x 轴）逐列统计 Alpha > 阈值的像素个数（单位：像素）。
   阈值默认 10（0..255 尺度）：笔迹边缘有抗锯齿的半透明像素，不滤掉会让宽度虚高且抖动；
   只把 Alpha>10 的像素算作「笔迹」，见 ALPHA_THRESHOLD。
2. **不透明度曲线 opacity**：逐列统计「笔迹像素」的 Alpha 均值再除 255，单位 **0..1**。
   只对 Alpha>阈值的像素求均值，这样宽度变化不会稀释浓度（宽度与浓度解耦）。

坐标口径：
- positions 为 **0..1 的画布归一化横坐标**（按整张画布宽度归一化，而不是按笔迹包围盒），
  这样两张图上笔迹长度/位置不同时也能对齐比较；
- width 保留像素单位（便于用 max(|Δw|/max(w,1)) 做相对偏差，见 feel_compare.py）；
- opacity 为 0..1（与 width 的像素单位刻意区分，避免阈值口径混用）。
"""

import json
import os

import numpy as np
from PIL import Image

# Alpha 阈值（0..255）：低于它的像素视为抗锯齿/背景，不计入宽度与浓度
ALPHA_THRESHOLD = 10
DEFAULT_POINTS = 64


def load_alpha(png_path):
    """读取 PNG 的 Alpha 通道为 float ndarray（0..255）。"""
    with Image.open(png_path) as im:
        rgba = im.convert("RGBA")
        return np.asarray(rgba, dtype=np.float64)[:, :, 3]


def extract_curves(png_path, points=DEFAULT_POINTS, alpha_threshold=ALPHA_THRESHOLD):
    """提取宽度/不透明度曲线，并线性重采样到points 个点。

    返回 dict：positions（0..1）、width（px）、opacity（0..1）、meta。
    """
    alpha = load_alpha(png_path)
    height, width = alpha.shape
    painted = alpha > float(alpha_threshold)

    # 逐列（x）统计：宽度 = 笔迹像素个数；浓度 = 笔迹像素 Alpha 均值 / 255
    widths = painted.sum(axis=0).astype(np.float64)  # (width,)
    opacities = np.zeros(width, dtype=np.float64)
    for x in range(width):
        column = alpha[:, x]
        mask = column > float(alpha_threshold)
        if mask.any():
            opacities[x] = column[mask].mean() / 255.0

    # 画布归一化坐标（用列中心，避免 0 与 1 端点采样偏差）
    canvas_positions = (np.arange(width) + 0.5) / float(width)
    grid = (np.arange(points) + 0.5) / float(points)
    width_curve = np.interp(grid, canvas_positions, widths)
    opacity_curve = np.interp(grid, canvas_positions, opacities)

    return {
        "positions": grid.tolist(),
        "width": width_curve.tolist(),
        "opacity": opacity_curve.tolist(),
        "meta": {
            "file": os.path.basename(png_path),
            "path": os.path.abspath(png_path),
            "canvas": [int(width), int(height)],
            "points": int(points),
            "alpha_threshold": int(alpha_threshold),
            "width_unit": "px",
            "opacity_unit": "0..1",
            "positions_unit": "0..1（按画布宽度归一化）",
        },
    }


def save_curve(curve, json_path):
    """把曲线写成 JSON（UTF-8）。"""
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(curve, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def load_curve(json_path):
    with open(json_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _main():
    import argparse

    parser = argparse.ArgumentParser(description="从渲染图提取手感曲线（宽度/不透明度）")
    parser.add_argument("--image", required=True, help="渲染图 PNG 路径")
    parser.add_argument("--out", default=None, help="曲线 JSON 输出路径（省略则打印到 stdout）")
    parser.add_argument("--points", type=int, default=DEFAULT_POINTS, help="重采样点数，默认 64")
    parser.add_argument("--alpha-threshold", type=int, default=ALPHA_THRESHOLD, help="Alpha 阈值，默认 10")
    ns = parser.parse_args()
    curve = extract_curves(ns.image, points=ns.points, alpha_threshold=ns.alpha_threshold)
    if ns.out:
        save_curve(curve, ns.out)
        print("已写曲线：%s（points=%d，canvas=%s）" % (ns.out, ns.points, curve["meta"]["canvas"]))
    else:
        print(json.dumps(curve, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    _main()
