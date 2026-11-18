# -*- coding: utf-8 -*-
"""手感曲线偏差判定（任务 14）。

两条曲线：
- width：像素单位；偏差用相对值 width_dev = max(|w_a - w_b| / max(w_b, 1))，无量纲（0..1）；
- opacity：**0..1 尺度**；偏差 opacity_dev = max(|o_a - o_b|)，单位就是 0..1。

阈值（写死，单位在常量名里标明）：
- WIDTH_DEV_THRESHOLD = 0.05   → 宽度相对偏差 < 5%
- OPACITY_DEV_THRESHOLD_01 = 0.05 → 不透明度偏差 < 0.05（0..1 尺度，即满量程 5%）
  换算到 0..255 尺度：0.05 × 255 = 12.75；任务书里「按 0..255 尺度则 <5/255≈2%」的口径写成
  「0..255 尺度下 5 个灰阶 ≈ 1.96%」，本实现按用户需求「<5%」取 5% 满量程，并在文档与本模块常量名里
  明确写清是 0..1 尺度（避免两处阈值含义打架）。

判定：两条偏差同时达标 → 合格；否则不合格。
"""

import json
import os

from PIL import Image

WIDTH_DEV_THRESHOLD = 0.05        # 宽度相对偏差上限（无量纲，0..1；×100 即百分比）
OPACITY_DEV_THRESHOLD_01 = 0.05   # 不透明度偏差上限（0..1 尺度；= 12.75/255）
OPACITY_DEV_THRESHOLD_255 = OPACITY_DEV_THRESHOLD_01 * 255.0  # 仅用于打印换算，不参与判定


def width_deviation(curve_a, curve_b):
    """宽度相对偏差：max(|w_a-w_b| / max(w_b,1))，单位无量纲。"""
    a = curve_a["width"]
    b = curve_b["width"]
    if len(a) != len(b):
        raise ValueError("两条宽度曲线点数不一致：%d vs %d" % (len(a), len(b)))
    worst = 0.0
    for wa, wb in zip(a, b):
        dev = abs(float(wa) - float(wb)) / max(float(wb), 1.0)
        worst = max(worst, dev)
    return worst


def opacity_deviation(curve_a, curve_b):
    """不透明度偏差：max(|o_a-o_b|)，单位 0..1（不是 0..255）。"""
    a = curve_a["opacity"]
    b = curve_b["opacity"]
    if len(a) != len(b):
        raise ValueError("两条不透明度曲线点数不一致：%d vs %d" % (len(a), len(b)))
    return max((abs(float(oa) - float(ob)) for oa, ob in zip(a, b)), default=0.0)


def compare_curves(curve_a, curve_b,
                   width_threshold=WIDTH_DEV_THRESHOLD,
                   opacity_threshold=OPACITY_DEV_THRESHOLD_01):
    """比较两条曲线，返回判决与各项偏差（含百分比/0..255 换算，便于报告）。"""
    wdev = width_deviation(curve_a, curve_b)
    odev = opacity_deviation(curve_a, curve_b)
    width_ok = wdev < float(width_threshold)
    opacity_ok = odev < float(opacity_threshold)
    return {
        "width_dev": wdev,
        "width_dev_percent": wdev * 100.0,
        "opacity_dev": odev,
        "opacity_dev_0_255": odev * 255.0,
        "width_pass": width_ok,
        "opacity_pass": opacity_ok,
        "verdict": "合格" if (width_ok and opacity_ok) else "不合格",
        "thresholds": {
            "width_dev": float(width_threshold),
            "opacity_dev_01": float(opacity_threshold),
            "opacity_dev_0_255_equivalent": float(opacity_threshold) * 255.0,
        },
        "meta_a": curve_a.get("meta", {}),
        "meta_b": curve_b.get("meta", {}),
    }


def stretch_image_horizontally(src_path, dst_path, factor=1.10):
    """把图片内容横向拉伸 factor 倍，再居中裁回原画布尺寸。

    为什么裁回原尺寸：手感曲线按「画布归一化横坐标」采样；若只把画布变宽，笔迹占画布的比例不变，
    曲线会完全重合，检测不出横向形变。裁回原尺寸后，笔迹在同一坐标网格里变长，宽度曲线会错位，
    因此能如实判出「形变不合格」。本函数只用于验收演示与单测，不参与正常基线生成。
    """
    with Image.open(src_path) as im:
        rgba = im.convert("RGBA")
        width, height = rgba.size
        stretched = rgba.resize((max(1, int(round(width * float(factor)))), height), Image.BILINEAR)
        left = (stretched.width - width) // 2
        cropped = stretched.crop((left, 0, left + width, height))
        cropped.save(dst_path, "PNG")
    return dst_path


def _main():
    import argparse

    parser = argparse.ArgumentParser(description="手感曲线比对（宽度相对偏差 + 不透明度偏差）")
    parser.add_argument("--a", required=True, help="曲线 JSON A（被测）")
    parser.add_argument("--b", required=True, help="曲线 JSON B（基线）")
    parser.add_argument("--width-threshold", type=float, default=WIDTH_DEV_THRESHOLD, help="宽度偏差阈值，默认 0.05")
    parser.add_argument("--opacity-threshold", type=float, default=OPACITY_DEV_THRESHOLD_01, help="不透明度偏差阈值（0..1 尺度），默认 0.05")
    ns = parser.parse_args()
    with open(ns.a, "r", encoding="utf-8") as fh:
        curve_a = json.load(fh)
    with open(ns.b, "r", encoding="utf-8") as fh:
        curve_b = json.load(fh)
    result = compare_curves(curve_a, curve_b,
                            width_threshold=ns.width_threshold,
                            opacity_threshold=ns.opacity_threshold)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["verdict"] == "合格" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
