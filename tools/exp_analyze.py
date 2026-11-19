# -*- coding: utf-8 -*-
"""实验归因分析：PS/Krita 对照图的 2×2 归因 + 缩放/深度判读（只读实验产物）。

本工具是「实验-归因」任务的分析入口，**只读**，不写任何文件：

    PYTHONIOENCODING=utf-8 "<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/exp_analyze.py
    ... --json       # 额外把机读结果打到 stdout（放在人读报告之后）
    ... --section 2  # 只跑第 2 节（1=自检/对齐 2=2×2 归因 3=缩放 4=深度 5=结构残差 6=修法预测）

输入（全部只读）：
    tests/baselines/exp/ps/manifest.json      + 29 张 PNG（PS 真值，2025/26.9.0.15）
    tests/baselines/exp/krita/manifest.json   + 53 张 PNG（Krita 5.3.3，conv/raw/ref）
    research/测试用纹理.png                    64×64 RGBA 源纹理（两侧同一份字节）

口径（与既有工具一致，不另立一套）：
* 差异点 = |Δ| > 2（tools/imgdiff.DEFAULT_TOL），比例分母 = 全部像素（10000）；
* 对齐只用 tools/align.align_translate 的整数平移（不缩放、不旋转）；
* 语义层分别调用 tools/ps_texture_reference.ps_mask（PS 数值语义）与
  tools/texture_mask.compute_mask（Krita conv 数值语义）；两套语义**不得互相顶替**；
* scale≠100% 时 Krita 对图案做 Qt 平滑重采样（见实验-Krita侧-报告 §9），
  texture_mask 的「整数平铺」预测器**不覆盖**这种情况，因此缩放/深度判读一律
  图像↔图像比，不用预测器下结论。

判档（任务书口径，写死）：
    mean|ΔR| ≤ 5 灰度级 → 准；5 < mean|ΔR| ≤ 25 → 部分；> 25 → 不准。
其中 R 为「响应曲线」：R_side(S) = Alpha_side(S) − Alpha_side(S=100)，有符号 int16。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.align import align_translate                      # noqa: E402
from tools.imgdiff import DEFAULT_TOL                        # noqa: E402
from tools.ps_texture_reference import (                     # noqa: E402
    PS_MODE_LINEAR_HEIGHT,
    PS_MODE_MULTIPLY,
    ps_mask,
)
from tools.texture_mask import (                             # noqa: E402
    MODE_LINEAR_HEIGHT_PHOTOSHOP,
    MODE_MULTIPLY,
    compute_mask,
)

EXP_DIR = REPO_ROOT / "tests" / "baselines" / "exp"
PS_DIR = EXP_DIR / "ps"
KRITA_DIR = EXP_DIR / "krita"
TEXTURE_PATH = REPO_ROOT / "research" / "测试用纹理.png"

TOL = DEFAULT_TOL                    # 2
CANVAS = 100                         # 画布 100×100
TEX = 64                             # 纹理 64×64
ZERO_LEVEL = 255.0                   # PS 的 alpha 合成以 255（不透明）为透明端基准

#: 模式名 -> (PS 运行期号, Krita 枚举号)
MODES = {
    "multiply": (PS_MODE_MULTIPLY, MODE_MULTIPLY),
    "linearHeight": (PS_MODE_LINEAR_HEIGHT, MODE_LINEAR_HEIGHT_PHOTOSHOP),
}

#: 判档阈值（灰度级）
VERDICT_OK = 5.0
VERDICT_PARTIAL = 25.0

#: |Δ| 直方图分箱（灰度级）
HIST_BINS = [(0, 0), (1, 2), (3, 5), (6, 10), (11, 25), (26, 50), (51, 100), (101, 255)]


# ===========================================================================
# 零、基础工具
# ===========================================================================
def load_alpha(path: Path) -> np.ndarray:
    """读 PNG 只取 Alpha（uint8 二维）。与 tools/imgdiff._load_alpha 同口径。"""
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"), dtype=np.uint8)[:, :, 3]


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


_ALPHA_CACHE: dict = {}


def alpha_of(side: str, file_name: str) -> np.ndarray:
    key = (side, file_name)
    if key not in _ALPHA_CACHE:
        base = PS_DIR if side == "ps" else KRITA_DIR
        _ALPHA_CACHE[key] = load_alpha(base / file_name)
    return _ALPHA_CACHE[key]


def verdict(mean_abs_delta: float) -> str:
    """按任务书阈值判档。"""
    if mean_abs_delta <= VERDICT_OK:
        return "准"
    if mean_abs_delta <= VERDICT_PARTIAL:
        return "部分"
    return "不准"


def pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    """皮尔逊相关系数（任一数组方差为 0 时返回 None，避免除以 0 得到 nan）。"""
    x = np.asarray(a, dtype=np.float64).ravel()
    y = np.asarray(b, dtype=np.float64).ravel()
    if x.std() == 0.0 or y.std() == 0.0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def metrics(a, b, tol: int = TOL) -> dict:
    """按 imgdiff 口径比较两个同尺寸 Alpha 数组，并给直方图要点。

    返回：diff_pixels / diff_ratio（|Δ|>tol、分母=全部像素）、mean_abs、max_abs、
    signed_mean（Δ 的带符号均值，用来识别「整体抬升/压暗」）、hist（各档像素数与占比）。
    """
    aa = np.asarray(a, dtype=np.int16)
    bb = np.asarray(b, dtype=np.int16)
    if aa.shape != bb.shape:
        raise ValueError(f"尺寸不一致：{aa.shape} vs {bb.shape}")
    delta = aa - bb
    abs_delta = np.abs(delta)
    total = int(abs_delta.size)
    diff_pixels = int(np.count_nonzero(abs_delta > tol))
    hist = {}
    for low, high in HIST_BINS:
        count = int(np.count_nonzero((abs_delta >= low) & (abs_delta <= high)))
        hist[f"{low}-{high}"] = {"count": count, "ratio": count / total}
    return {
        "total_pixels": total,
        "diff_pixels": diff_pixels,
        "diff_ratio": diff_pixels / total,
        "mean_abs": float(abs_delta.mean()),
        "max_abs": int(abs_delta.max()),
        "signed_mean": float(delta.mean()),
        "hist": hist,
    }


def fill_alpha(alpha: np.ndarray) -> bool:
    """整张图是否恒为 255（饱和，无信息量）。"""
    return bool(np.all(alpha == 255))


def tiled_texture_gray() -> np.ndarray:
    """64×64 纹理按 tools/texture_mask 的灰度公式折成 0..255 灰度，并平铺到 100×100。

    gray = (r*11 + g*16 + b*5) // 32（KisTextureMaskInfo.cpp L184-190 的整数除法）。
    平铺锚点取画布 (0,0)、原始像素尺寸（PS 侧 §5 与 Krita 侧 §9 双向证实的相位）。
    """
    with Image.open(TEXTURE_PATH) as image:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.int64)
    red, green, blue = rgba[..., 0], rgba[..., 1], rgba[..., 2]
    gray = (red * 11 + green * 16 + blue * 5) // 32
    yy = np.arange(CANVAS) % TEX
    xx = np.arange(CANVAS) % TEX
    return gray[yy][:, xx]


# ===========================================================================
# 一、清单索引与数据自检
# ===========================================================================
class Data:
    """两侧清单的索引（键 = 模式/深度/缩放/亮度/对比度）。"""

    def __init__(self) -> None:
        ps_manifest = load_manifest(PS_DIR / "manifest.json")
        krita_manifest = load_manifest(KRITA_DIR / "manifest.json")
        # 参考图（useTexture=false / variant=ref）不进参数索引，单独按文件名取
        self.ps_images = {}
        for entry in ps_manifest["images"]:
            if entry.get("use_texture") is False or entry.get("ps_depth") is None:
                continue
            key = (entry["ps_mode"], int(entry["ps_depth"]), int(entry["ps_scale_pct"]),
                   int(entry["ps_brightness"]), int(entry["ps_contrast"]))
            self.ps_images[key] = entry["file"]
        self.krita_images = {}
        for entry in krita_manifest["items"]:
            if entry.get("variant") == "ref" or entry.get("depth") is None:
                continue
            key = (entry["variant"], entry["mode"], int(entry["depth"]), int(entry["scale"]),
                   int(entry["brightness"]), int(entry["contrast"]))
            self.krita_images[key] = entry["file"]
        self.ps_manifest = ps_manifest
        self.krita_manifest = krita_manifest
        self.ref_ps = "exp__ps__ref__notex.png"
        self.ref_krita = "exp__krita__ref__notex.png"

    def ps(self, mode: str, depth: int, scale: int = 100, b: int = 0, c: int = 0) -> np.ndarray:
        return alpha_of("ps", self.ps_images[(mode, depth, scale, b, c)])

    def krita(self, variant: str, mode: str, depth: int, scale: int = 100,
              b: int = 0, c: int = 0) -> np.ndarray:
        return alpha_of("krita", self.krita_images[(variant, mode, depth, scale, b, c)])

    def ref(self, side: str) -> np.ndarray:
        return alpha_of(side, self.ref_ps if side == "ps" else self.ref_krita)


def section_selfcheck(data: Data) -> dict:
    """数据自检：张数、尺寸、两侧参考图、饱和图集合。"""
    out = {
        "ps_count": len(data.ps_images),
        "ps_total_with_ref": len(data.ps_images) + 1,
        "krita_count": len(data.krita_images),
        "krita_total_with_ref": len(data.krita_images) + 1,
        "ps_ref_all255": fill_alpha(data.ref("ps")),
        "krita_ref_all255": fill_alpha(data.ref("krita")),
        "ps_ref_min_max": [int(data.ref("ps").min()), int(data.ref("ps").max())],
        "krita_ref_min_max": [int(data.ref("krita").min()), int(data.ref("krita").max())],
        "ref_cross_diff": metrics(data.ref("ps"), data.ref("krita")),
        "ps_saturated": [],
        "krita_saturated": [],
    }
    for (mode, depth, scale, b, c) in sorted(data.ps_images):
        if fill_alpha(data.ps(mode, depth, scale, b, c)):
            out["ps_saturated"].append(f"{mode} d{depth} s{scale} b{b} c{c}")
    for (variant, mode, depth, scale, b, c) in sorted(data.krita_images):
        if fill_alpha(data.krita(variant, mode, depth, scale, b, c)):
            out["krita_saturated"].append(
                f"{variant} {mode} d{depth} s{scale} b{b} c{c}")
    return out


def section_align(data: Data) -> dict:
    """对齐核验：两侧 dab 圆心不同但几何等价，用 align_translate 实测 dx=dy=0。

    取三对样例：参考图（全 255）、multiply/d100/s100、multiply/d10/s50。
    同时打印对齐前后的差异比例，证明「平移不能且不需要」改善。
    """
    pairs = [
        ("参考图 ref__notex", data.ref("ps"), data.ref("krita")),
        ("multiply d100 s100 conv", data.ps("multiply", 100, 100),
         data.krita("conv", "multiply", 100, 100)),
        ("multiply d10 s50 conv", data.ps("multiply", 10, 50),
         data.krita("conv", "multiply", 10, 50)),
        # 放大档：用来判断 150/200 的残差里有没有「图案整体平移」成分
        ("multiply d100 s150 conv", data.ps("multiply", 100, 150),
         data.krita("conv", "multiply", 100, 150)),
        ("multiply d100 s200 conv", data.ps("multiply", 100, 200),
         data.krita("conv", "multiply", 100, 200)),
        ("linearHeight d10 s100 conv", data.ps("linearHeight", 10, 100),
         data.krita("conv", "linearHeight", 10, 100)),
    ]
    rows = []
    for name, ps_alpha, k_alpha in pairs:
        aligned, (dx, dy), meta = align_translate(ps_alpha, k_alpha, tol=TOL)
        before = metrics(ps_alpha, k_alpha)
        after = metrics(ps_alpha, aligned)
        rows.append({
            "pair": name,
            "dx": int(dx), "dy": int(dy),
            "method": meta.get("method"),
            "clipped_pixels": int(meta.get("clipped_pixels", -1)),
            "diff_ratio_before": before["diff_ratio"],
            "diff_ratio_after": after["diff_ratio"],
            "max_abs_before": before["max_abs"],
            "max_abs_after": after["max_abs"],
        })
    return {"rows": rows,
            "all_zero_shift": all(r["dx"] == 0 and r["dy"] == 0 for r in rows)}


# ===========================================================================
# 二、表 1：2×2 归因（固定 depth=100、scale=100、b=c=0）
# ===========================================================================
def _gray64() -> np.ndarray:
    """64×64 纹理的 0..255 灰度（未反相），用于语义层直接比较。"""
    texture64 = np.asarray(Image.open(TEXTURE_PATH).convert("RGBA"), dtype=np.int64)
    return ((texture64[..., 0] * 11 + texture64[..., 1] * 16 + texture64[..., 2] * 5)
            // 32).astype(np.float64)


def _semantic(mode: str, depth: int) -> dict:
    """语义层（d）：在 64×64 纹理值上直接比较两侧掩码公式。

    不经 100×100 平铺、不经重采样，因此与缩放无关，是「引擎语义差」的最干净切片。
    PS 侧 ps_mask 的输入是「反相后的纹理值」（invertTexture=true 在 PS 里作用于纹理）；
    Krita 侧把 invert=True 交给 compute_mask 内部处理。两侧都取 8bit 量化后的输出。
    """
    ps_mode, k_mode = MODES[mode]
    tex255 = _gray64()
    ps_sem = np.floor(ps_mask(255.0 - tex255, 0, 0, ps_mode, depth) * 255.0 + 0.5)
    k_sem = np.floor(compute_mask(
        (tex255 / 255.0).astype(np.float32), invert=True,
        brightness=0.0, contrast=1.0, mode=k_mode, depth=depth / 100.0,
    ).astype(np.float64) * 255.0 + 0.5)
    out = metrics(ps_sem, k_sem)
    out["ps_unique"] = int(np.unique(ps_sem).size)
    out["krita_unique"] = int(np.unique(k_sem).size)
    return out


def _attribution_row(data: Data, mode: str, depth: int, scale: int = 100,
                     b: int = 0, c: int = 0, supplementary: bool = False) -> dict:
    """一行 2×2 归因：(a) PS−Kraw、(b) Kraw−Kconv、(c) PS−Kconv、(d) 语义层。"""
    ps_alpha = data.ps(mode, depth, scale, b, c)
    kraw = data.krita("raw", mode, depth, scale, b, c)
    kconv = data.krita("conv", mode, depth, scale, b, c)
    q = int(np.floor(depth * 255.0 / 100.0 + 0.5))
    soft_const = 255.0 * (1.0 - q / 255.0)
    return {
        "mode": mode, "depth": depth, "scale": scale, "b": b, "c": c,
        "supplementary": supplementary,
        "q8": q, "soft_const_255_1ms": soft_const,
        "ps_saturated": fill_alpha(ps_alpha),
        "krita_saturated": fill_alpha(kraw),
        "ps_alpha_min_max": [int(ps_alpha.min()), int(ps_alpha.max())],
        "kraw_alpha_min_max": [int(kraw.min()), int(kraw.max())],
        "kconv_alpha_min_max": [int(kconv.min()), int(kconv.max())],
        "a_ps_vs_kraw": metrics(ps_alpha, kraw),
        "b_kraw_vs_kconv": metrics(kraw, kconv),
        "c_ps_vs_kconv": metrics(ps_alpha, kconv),
        "d_semantic_64": _semantic(mode, depth),
    }


def section_table1(data: Data) -> dict:
    """表 1：固定 depth=100、scale=100、b=c=0，两个模式各一行（任务书要求）。

    另外给「补充行」：multiply 的 d10/d60 与 linearHeight 的 d10 —— 因为
    * multiply 在 d100 时 s=1 ⇒ 255(1−s)=0，(a)(b)(c) 全为 0 是**结构性必然**，
      单看 d100 看不出两侧机制差（要 d<100 才会暴露 255(1−s)）；
    * linearHeight 在 d100 时**两侧都饱和成 255**，四格全 0 属「不可判读」，
      真正可比的非饱和点是 d10。
    因此补充行不是加分项，是让归因结论成立的必需品。
    """
    rows = []
    for mode in MODES:
        rows.append(_attribution_row(data, mode, 100, supplementary=False))
        if mode == "multiply":
            rows.append(_attribution_row(data, mode, 10, supplementary=True))
            rows.append(_attribution_row(data, mode, 60, supplementary=True))
        else:
            rows.append(_attribution_row(data, mode, 10, supplementary=True))
    tex255 = _gray64()
    return {"rows": rows,
            "texture_gray_64": {"min": int(tex255.min()), "max": int(tex255.max()),
                                "mean": float(tex255.mean()),
                                "unique": int(np.unique(tex255).size)}}


# ===========================================================================
# 三、表 2：缩放判读（响应曲线 R(S)=Alpha(S)−Alpha(100)）
# ===========================================================================
SCALES = (50, 100, 150, 200)


def _response(alpha_map: dict, scale: int) -> np.ndarray:
    """有符号 int16 响应曲线：Alpha(S) − Alpha(100)。"""
    return (alpha_map[scale].astype(np.int16) - alpha_map[100].astype(np.int16))


def _curve_row(label: str, ps_map: dict, k_map: dict, scale: int,
               ps_saturated_scales: set) -> dict:
    """单点判读行：响应曲线残差 + 幅度比 + 相关系数 + 图像级绝对差。"""
    r_ps = _response(ps_map, scale)
    r_k = _response(k_map, scale)
    resid = metrics(r_ps, r_k)                  # 残差 = R_ps − R_k（口径同 imgdiff）
    abs_diff = metrics(ps_map[scale], k_map[scale])
    amp_ps = float(np.abs(r_ps).mean())
    amp_k = float(np.abs(r_k).mean())
    unreadable = scale in ps_saturated_scales
    return {
        "label": label,
        "scale": scale,
        "readable": not unreadable,
        "mean_abs_R_diff": resid["mean_abs"],
        "max_abs_R_diff": resid["max_abs"],
        "R_diff_ratio": resid["diff_ratio"],
        "mean_abs_R_ps": amp_ps,
        "mean_abs_R_k": amp_k,
        "amplitude_ratio": (amp_ps / amp_k) if amp_k > 0 else None,
        # 相对残差 = mean|ΔR| / mean|R_ps|：若它随深度不变、只随缩放变化，
        # 说明误差正比于「图案自身的变化幅度」，即误差出在缩放后的图案本身（重采样），
        # 而不是出在深度/强度映射上（那会给近似恒定的绝对偏移）。
        "rel_residual": (resid["mean_abs"] / amp_ps) if amp_ps > 0 else None,
        "corr_R": pearson(r_ps, r_k),
        "abs_diff_ratio_at_S": abs_diff["diff_ratio"],
        "abs_mean_at_S": abs_diff["mean_abs"],
        "abs_max_at_S": abs_diff["max_abs"],
        "verdict": "不可判读" if unreadable else verdict(resid["mean_abs"]),
        "sign_same_ratio": float(np.mean(np.sign(r_ps) == np.sign(r_k))) if scale != 100 else None,
        "grad_corr": _gradient_corr(ps_map[scale], np.abs(r_ps - r_k)),
    }


def _gradient_corr(image: np.ndarray, residual_abs: np.ndarray) -> float | None:
    """残差 |ΔR| 与 PS 图像局部梯度幅值的相关系数。

    用途：区分「重采样差异」与「机制错」。
    - 若残差集中在纹理边缘（梯度大处），说明两边图案位置/插值不同 → 重采样问题；
    - 若残差与梯度无关（铺满全图），说明是整体数值变换 → 机制/映射问题。
    """
    img = np.asarray(image, dtype=np.float64)
    gx, gy = np.gradient(img)
    grad = np.hypot(gx, gy)
    return pearson(grad, np.asarray(residual_abs, dtype=np.float64))


def section_table2(data: Data) -> dict:
    """对每个 (mode, depth) 比较 S∈{50,150,200} 的响应曲线；分 50 与 150/200 判档。"""
    cases = []
    for mode in MODES:
        for depth in (10, 60, 100):
            for variant in ("conv", "raw"):
                ps_map = {s: data.ps(mode, depth, s) for s in SCALES}
                k_map = {s: data.krita(variant, mode, depth, s) for s in SCALES}
                ps_sat = {s for s in SCALES if fill_alpha(ps_map[s])}
                k_sat = {s for s in SCALES if fill_alpha(k_map[s])}
                rows = [_curve_row(f"{mode} d{depth} {variant}", ps_map, k_map, s, ps_sat)
                        for s in (50, 150, 200)]
                # conv 与 raw 的响应曲线是否等价（软纹理是常量平移 → 理论上 R 应相同）
                raw_rows = {r["scale"]: r for r in rows}
                if variant == "conv":
                    k_raw = {s: data.krita("raw", mode, depth, s) for s in SCALES}
                    for row in rows:
                        s = row["scale"]
                        r_conv = _response(k_map, s)
                        r_raw = _response(k_raw, s)
                        row["conv_vs_raw_R_max_abs"] = int(
                            np.abs(r_conv.astype(np.int32) - r_raw.astype(np.int32)).max())
                # 分档汇总：缩小（50%）与放大（150/200）分开判
                tail = [r for r in rows if r["scale"] in (150, 200)]
                one = [r for r in rows if r["scale"] == 50]
                cases.append({
                    "mode": mode, "depth": depth, "variant": variant,
                    "ps_saturated_scales": sorted(ps_sat),
                    "krita_saturated_scales": sorted(k_sat),
                    "rows": rows,
                    "agg_shrink_S50": {
                        "mean_abs_R_diff": one[0]["mean_abs_R_diff"],
                        "verdict": one[0]["verdict"],
                    },
                    "agg_zoom_S150_200": {
                        "mean_abs_R_diff": float(np.mean([r["mean_abs_R_diff"] for r in tail])),
                        "verdict": ("不可判读" if all(r["verdict"] == "不可判读" for r in tail)
                                    else verdict(float(np.mean([r["mean_abs_R_diff"] for r in tail])))),
                    },
                })
    return {"cases": cases, "scales": list(SCALES)}


# ===========================================================================
# 四、表 3：深度判读（响应曲线 R(D)=Alpha(D)−Alpha(100)）
# ===========================================================================
def section_table3(data: Data) -> dict:
    """深度判读：multiply d10/60/100 + linearHeight d10。

    基准选 Alpha(100) 的理由：
    * multiply 在 depth=100 时 PS 公式退化为恒等（out=u），是唯一「无衰减」的天然锚点，
      且两侧都不饱和（PS min=0/max=255，Krita 同为 0..255），可比；
    * linearHeight 的 d60/d100 在**两侧都饱和成 255**（PS 8 张与参考图同 sha；Krita 12 张
      预测饱和），所以 linearHeight 的深度响应在 PS 侧只有 d10 一个非饱和点，
      判读只能落到「d10 vs 饱和端」这一条，已如实标注「单点、不可作曲线判读」。
    """
    cases = []
    for mode, depths in (("multiply", (10, 60, 100)), ("linearHeight", (10,))):
        for depth in depths:
            for variant in ("conv", "raw"):
                ps_map = {d: data.ps(mode, d, 100) for d in (10, 60, 100)}
                k_map = {d: data.krita(variant, mode, d, 100) for d in (10, 60, 100)}
                r_ps = ps_map[depth].astype(np.int16) - ps_map[100].astype(np.int16)
                r_k = k_map[depth].astype(np.int16) - k_map[100].astype(np.int16)
                resid = metrics(r_ps, r_k)
                abs_diff = metrics(ps_map[depth], k_map[depth])
                # 曲线可读性：深度点或基准点任一侧饱和 → 该点没有响应信息
                curve_readable = not (fill_alpha(ps_map[depth]) or fill_alpha(ps_map[100]))
                amp_ps = float(np.abs(r_ps).mean())
                amp_k = float(np.abs(r_k).mean())
                q = int(np.floor(min(max(depth, 0.0), 100.0) * 255.0 / 100.0 + 0.5))
                cases.append({
                    "mode": mode, "depth": depth, "variant": variant,
                    "depth_q8": q,
                    "krita_strength_q8": int(np.floor((depth / 100.0) * 255.0 + 0.5)),
                    "ps_saturated_here": fill_alpha(ps_map[depth]),
                    "ps_saturated_at_100": fill_alpha(ps_map[100]),
                    "krita_saturated_here": fill_alpha(k_map[depth]),
                    "curve_readable": curve_readable and depth != 100,
                    "note": ("d=100 是基准点，R≡0 属定义" if depth == 100 else
                             "" if curve_readable else "PS 该点或基准点饱和，深度响应不可判读"),
                    "mean_abs_R_diff": resid["mean_abs"],
                    "max_abs_R_diff": resid["max_abs"],
                    "R_diff_ratio": resid["diff_ratio"],
                    "mean_abs_R_ps": amp_ps,
                    "mean_abs_R_k": amp_k,
                    "amplitude_ratio": (amp_ps / amp_k) if amp_k > 0 else None,
                    "corr_R": pearson(r_ps, r_k),
                    "abs_diff_ratio_at_D": abs_diff["diff_ratio"],
                    "abs_mean_at_D": abs_diff["mean_abs"],
                    "abs_max_at_D": abs_diff["max_abs"],
                    "verdict": (verdict(resid["mean_abs"])
                                if (curve_readable and depth != 100) else "不可判读"),
                    "verdict_note": ("" if (curve_readable and depth != 100) else
                                     "不可判读（饱和/基准点，非「准」）"),
                })
    return {"cases": cases}


# ===========================================================================
# 五、结构残差核验：把「机制」变成可算的常数
# ===========================================================================
def section_residual(data: Data) -> dict:
    """验证两条结构假设（scale=100，避免重采样污染）：

    假设 1（multiply）：PS 的 alpha 合成以 255 为透明端：
        Alpha_ps = 255 + (u−255)·s = u·s + 255·(1−s)
        Krita 硬合成：Alpha_kraw = u·s
        ⇒ PS − Kraw ≡ 255·(1−s)（常数，s=q/255）
        Krita 软纹理：Alpha_soft = Alpha_hard + 255·(1−s)（实验-Krita侧-报告 §10.2）
        ⇒ PS − Kconv ≡ 0（量化级）
    假设 2（linearHeight）：PS 权重 W=12、Krita 权重 W=10：
        Alpha_ps = max((255−u)·12q/255, 12q−u)
        Alpha_k  = max((255−u)·10q/255, 10q−u)
        ⇒ 低深度时两者常数分离 ~2q
    """
    tex = tiled_texture_gray().astype(np.float64)
    inv = 255.0 - tex                               # invertTexture=true
    rows = []
    for mode in MODES:
        ps_mode, k_mode = MODES[mode]
        for depth in (10, 60, 100):
            q = int(np.floor(depth * 255.0 / 100.0 + 0.5))
            s = q / 255.0
            const = 255.0 * (1.0 - s)
            ps_alpha = data.ps(mode, depth, 100)
            kraw = data.krita("raw", mode, depth, 100)
            kconv = data.krita("conv", mode, depth, 100)
            # 测得的 PS−Kraw，与常数模型比
            measured_const = (ps_alpha.astype(np.float64) - kraw.astype(np.float64))
            resid_const = measured_const - const
            row = {
                "mode": mode, "depth": depth, "q8": q, "soft_const_255_1ms": const,
                "ps_minus_kraw": {
                    "mean": float(measured_const.mean()),
                    "std": float(measured_const.std()),
                    "max_abs_dev_from_const": float(np.abs(resid_const).max()),
                    "diff_ratio_vs_raw": metrics(ps_alpha, kraw)["diff_ratio"],
                },
                "ps_minus_kconv": metrics(ps_alpha, kconv),
                "soft_model_conv_minus_raw": metrics(
                    np.clip(kraw.astype(np.float64) + const, 0, 255).round(), kconv),
            }
            # 预测器单向核对：ps_mask / compute_mask 对同一张纹理的差
            pred_ps = np.floor(ps_mask(inv, 0, 0, ps_mode, depth) * 255.0 + 0.5)
            # Krita 侧 invert=True 交给 compute_mask 内部处理（不是把已反相的纹理再传一遍）
            pred_k = np.floor(compute_mask(
                (tex / 255.0).astype(np.float32), invert=True, brightness=0.0,
                contrast=1.0, mode=k_mode, depth=depth / 100.0,
            ).astype(np.float64) * 255.0 + 0.5)
            row["semantic_pred_ps_vs_krita"] = metrics(pred_ps, pred_k)
            row["pred_ps_vs_ps_image"] = metrics(pred_ps, ps_alpha)
            row["pred_krita_vs_kraw_image"] = metrics(pred_k, kraw)
            rows.append(row)
    return {"rows": rows}


# ===========================================================================
# 六、修法收益预测：W=12 vs 10 的 ×1.2 强度补偿
# ===========================================================================
def section_fix(data: Data) -> dict:
    """最小改动收益预测（scale=100、b=c=0，可用预测器的地方才用）。

    修法 A（Height/LinearHeight 系）：把写进 .kpp 的 strength 从 q/255 改成
        q' = min(255, round(1.2·q))，使 Krita 的 10·q' ≈ PS 的 12·q。
    修法 B（multiply）：现状已用 UseSoftTexturing=true 抵消 255·(1−s)，本工具量化其收益。
    修法 C（scale≠100）：Krita 内部 Qt 重采样，转换侧无参数可改，只能改「重采样口径」
        （预缩放内嵌图案或改走图章/自定义笔尖），本工具不给预测。
    """
    tex = tiled_texture_gray().astype(np.float64)
    rows = []
    for mode in MODES:
        ps_mode, k_mode = MODES[mode]
        is_height_family = mode != "multiply"
        for depth in (10, 60, 100):
            ps_alpha = data.ps(mode, depth, 100)
            kraw = data.krita("raw", mode, depth, 100)
            kconv = data.krita("conv", mode, depth, 100)
            q = int(np.floor(depth * 255.0 / 100.0 + 0.5))
            # 修法 A（只对 Height/LinearHeight 系）：strength ×1.2，把 Krita 的 10·q
            # 抬到 PS 的 12·q；超出 1.0 时按上限 255 处理（d>83.3% 后与现状相同）。
            q_fix = min(255, int(np.floor(1.2 * q + 0.5))) if is_height_family else q
            strength_fix = q_fix / 255.0
            pred_fix = np.floor(compute_mask(
                (tex / 255.0).astype(np.float32), invert=True, brightness=0.0,
                contrast=1.0, mode=k_mode, depth=strength_fix,
            ).astype(np.float64) * 255.0 + 0.5)
            # 修法 B（只对 multiply）：软纹理常量，使 Krita 与 PS 的 255 透明端对齐
            pred_soft = np.clip(pred_fix.astype(np.float64)
                                + 255.0 * (1.0 - strength_fix), 0, 255)
            rows.append({
                "mode": mode, "depth": depth,
                "q8_now": q, "q8_fix": q_fix,
                "fix_scope": "height" if is_height_family else "multiply(现状不变)",
                "current_conv_vs_ps": metrics(ps_alpha, kconv),
                "fixA_conv_vs_ps": metrics(ps_alpha, pred_fix if is_height_family else kconv),
                "fixA_plus_soft_vs_ps": metrics(ps_alpha, pred_soft),
                "observed_raw_vs_ps": metrics(ps_alpha, kraw),
            })
    return {"rows": rows}


# ===========================================================================
# 七、打印
# ===========================================================================
def fmt_metric(m: dict, with_hist: bool = False) -> str:
    text = (f"差异比例={m['diff_ratio'] * 100:.2f}% mean|Δ|={m['mean_abs']:.2f} "
            f"max|Δ|={m['max_abs']}")
    if with_hist:
        tips = []
        for key, value in m["hist"].items():
            if value["count"]:
                tips.append(f"{key}:{value['count']}({value['ratio'] * 100:.1f}%)")
        text += " 直方图[" + " ".join(tips) + "]"
    return text


def print_report(result: dict) -> None:
    print("=" * 96)
    print("实验归因分析（只读）：PS 真值 vs Krita 5.3.3，2×2 归因 + 缩放/深度判读")
    print("口径：差异点 |Δ|>2、分母=全部像素(10000)；判档 mean|ΔR| ≤5 准 / ≤25 部分 / >25 不准")
    print("=" * 96)

    sc = result["selfcheck"]
    print("\n[一] 数据自检")
    print(f"  PS {sc['ps_total_with_ref']} 张（参数图 {sc['ps_count']} + 参考 1）/ "
          f"Krita {sc['krita_total_with_ref']} 张（参数图 {sc['krita_count']} + 参考 1）")
    print(f"  参考图：PS all255={sc['ps_ref_all255']} {sc['ps_ref_min_max']}；"
          f"Krita all255={sc['krita_ref_all255']} {sc['krita_ref_min_max']}")
    print(f"  参考图跨侧：{fmt_metric(sc['ref_cross_diff'])}")
    print(f"  PS 饱和图 {len(sc['ps_saturated'])} 张：{sc['ps_saturated']}")
    print(f"  Krita 饱和图 {len(sc['krita_saturated'])} 张：{sc['krita_saturated']}")

    al = result["align"]
    print("\n[二] 对齐核验（align_translate，bbox，tol=2）")
    for row in al["rows"]:
        print(f"  {row['pair']:<32} dx={row['dx']} dy={row['dy']} "
              f"clipped={row['clipped_pixels']} "
              f"差异比例 前={row['diff_ratio_before'] * 100:.2f}% "
              f"后={row['diff_ratio_after'] * 100:.2f}%  max={row['max_abs_before']}")
    print(f"  → 所有样例平移量均为 0：{al['all_zero_shift']}")

    t1 = result["table1"]
    print("\n[三] 表 1：2×2 归因（depth=100、scale=100、b=c=0）")
    print(f"  纹理 64×64 灰度：{t1['texture_gray_64']}")
    for row in t1["rows"]:
        tag = "补充" if row["supplementary"] else "规定"
        print(f"\n  ── [{tag}] {row['mode']} d{row['depth']}（PS min/max={row['ps_alpha_min_max']}，"
              f"Kraw min/max={row['kraw_alpha_min_max']}）PS 饱和={row['ps_saturated']} "
              f"q8={row['q8']} 255(1−s)={row['soft_const_255_1ms']:.0f}")
        print(f"    (a) PS  vs Kraw : {fmt_metric(row['a_ps_vs_kraw'])} "
              f"(Δ均值={row['a_ps_vs_kraw']['signed_mean']:+.2f})")
        print(f"        {fmt_metric(row['a_ps_vs_kraw'], with_hist=True)}")
        print(f"    (b) Kraw vs Kconv: {fmt_metric(row['b_kraw_vs_kconv'])} "
              f"(Δ均值={row['b_kraw_vs_kconv']['signed_mean']:+.2f})")
        print(f"    (c) PS  vs Kconv : {fmt_metric(row['c_ps_vs_kconv'])}")
        print(f"        {fmt_metric(row['c_ps_vs_kconv'], with_hist=True)}")
        print(f"    (d) 语义层 64×64 : {fmt_metric(row['d_semantic_64'])}")

    t2 = result["table2"]
    print("\n[四] 表 2：缩放判读（R(S)=Alpha(S)−Alpha(100)，有符号 int16）")
    for case in t2["cases"]:
        if case["variant"] != "conv":
            continue
        print(f"\n  ── {case['mode']} d{case['depth']}（conv）"
              f" PS饱和档={case['ps_saturated_scales']}")
        for row in case["rows"]:
            ratio = "n/a" if row["amplitude_ratio"] is None else f"{row['amplitude_ratio']:.3f}"
            corr = "n/a" if row["corr_R"] is None else f"{row['corr_R']:.4f}"
            grad = "n/a" if row["grad_corr"] is None else f"{row['grad_corr']:.3f}"
            conv_raw = row.get("conv_vs_raw_R_max_abs", "n/a")
            rel = "n/a" if row["rel_residual"] is None else f"{row['rel_residual'] * 100:.1f}%"
            print(f"    S={row['scale']:>3}%: mean|ΔR|={row['mean_abs_R_diff']:.2f} "
                  f"max|ΔR|={row['max_abs_R_diff']:>3} 相对残差={rel} 幅度比={ratio} r={corr} "
                  f"梯度相关={grad} 图像级差异比例={row['abs_diff_ratio_at_S'] * 100:.2f}% "
                  f"|R_conv−R_raw|max={conv_raw} → {row['verdict']}")
        print(f"    小结：缩小 S50 mean|ΔR|={case['agg_shrink_S50']['mean_abs_R_diff']:.2f}"
              f"（{case['agg_shrink_S50']['verdict']}）；"
              f"放大 150/200 mean|ΔR|={case['agg_zoom_S150_200']['mean_abs_R_diff']:.2f}"
              f"（{case['agg_zoom_S150_200']['verdict']}）")

    t3 = result["table3"]
    print("\n[五] 表 3：深度判读（R(D)=Alpha(D)−Alpha(100)）")
    for case in t3["cases"]:
        if case["variant"] != "conv":
            continue
        ratio = "n/a" if case["amplitude_ratio"] is None else f"{case['amplitude_ratio']:.3f}"
        corr = "n/a" if case["corr_R"] is None else f"{case['corr_R']:.4f}"
        print(f"  {case['mode']:<13} d{case['depth']:>3} q8={case['depth_q8']:>3} "
              f"(Krita strength 8bit={case['krita_strength_q8']:>3}) "
              f"mean|ΔR|={case['mean_abs_R_diff']:.2f} max|ΔR|={case['max_abs_R_diff']:>3} "
              f"幅度比={ratio} r={corr} 图像级差异比例={case['abs_diff_ratio_at_D'] * 100:.2f}% "
              f"PS点饱和={case['ps_saturated_here']} 基准饱和={case['ps_saturated_at_100']} "
              f"曲线可读={case['curve_readable']} → {case['verdict']}")

    res = result["residual"]
    print("\n[六] 结构残差核验（scale=100）")
    for row in res["rows"]:
        print(f"  {row['mode']:<13} d{row['depth']:>3} q8={row['q8']:>3} "
              f"常数 255(1−s)={row['soft_const_255_1ms']:.2f}")
        pk = row["ps_minus_kraw"]
        print(f"    PS−Kraw 实测均值={pk['mean']:.2f} std={pk['std']:.2f} "
              f"偏离常数最大值={pk['max_abs_dev_from_const']:.2f} "
              f"差异比例={pk['diff_ratio_vs_raw'] * 100:.1f}%")
        print(f"    PS−Kconv（软纹理后）: {fmt_metric(row['ps_minus_kconv'])}")
        print(f"    软纹理模型 (Kraw+常数) vs Kconv: "
              f"{fmt_metric(row['soft_model_conv_minus_raw'])}")
        print(f"    语义预测 ps_mask−compute_mask: "
              f"{fmt_metric(row['semantic_pred_ps_vs_krita'])}")

    fx = result["fix"]
    print("\n[七] 修法收益预测（scale=100）")
    for row in fx["rows"]:
        print(f"  {row['mode']:<13} d{row['depth']:>3} q8 {row['q8_now']}→{row['q8_fix']} "
              f"（{row['fix_scope']}）")
        print(f"    现状 conv vs PS   : {fmt_metric(row['current_conv_vs_ps'])}")
        print(f"    修法A vs PS       : {fmt_metric(row['fixA_conv_vs_ps'])}")
        print(f"    修法A+软纹理 vs PS: {fmt_metric(row['fixA_plus_soft_vs_ps'])}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PS/Krita 对照图归因与缩放/深度判读（只读）")
    parser.add_argument("--json", action="store_true", help="在人读报告后追加机读 JSON")
    parser.add_argument("--section", type=int, default=0,
                        help="只跑某一节（1=自检 2=表1 3=表2 4=表3 5=残差 6=修法；0=全部）")
    args = parser.parse_args(argv)

    data = Data()
    result = {}
    sections = {
        1: ("selfcheck", section_selfcheck),
        2: ("table1", section_table1),
        3: ("table2", section_table2),
        4: ("table3", section_table3),
        5: ("residual", section_residual),
        6: ("fix", section_fix),
    }
    # 对齐核验总是做（便宜且是几何前提）
    result["align"] = section_align(data)
    for index, (name, func) in sections.items():
        if args.section and index != args.section:
            continue
        result[name] = func(data)

    if not args.section:
        print_report(result)
    if args.json:
        print("\n---JSON---")
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
