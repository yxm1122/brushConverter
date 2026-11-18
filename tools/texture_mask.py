# -*- coding: utf-8 -*-
"""Krita 纹理掩码算法复刻（numpy 纯函数）。

本模块把 Krita 的两个源码片段逐行翻译成 numpy：
1) 纹理掩码预处理（亮度 / 对比度 / 反相 / 灰度换算 / 中性点）
   -> research/krita_texture/KisTextureMaskInfo.cpp（仓库内只读副本）
2) 掩码与笔尖 alpha 的混合（混合模式 + 深度/强度）
   -> Krita 上游 libs/ui/tool/strokes/KisMaskingBrushCompositeOp.h
      与 plugins/paintops/libpaintop/kis_texture_option.cpp（仓库内只读副本）
   （KisMaskingBrushCompositeOp.h 不在 research/ 副本里，来源 URL 见
     docs/纹理掩码复刻说明.md 的「来源」一节；这里只抄公式，不改动任何第三方文件。）

【输入输出约定】（任务 07 硬性要求第 3 条）
- 所有 mask/base/tex 均为 float32 ndarray，数值范围 0..1（越界一律 clip，
  NaN/Inf 直接报 ValueError，避免把坏数据算成「看起来正常」的图）。
- 所有函数都是纯函数：不读文件、不依赖 Krita 进程、不修改入参。
- 返回值为新的 float32 ndarray，范围仍在 0..1。

【和源码的偏差（有意为之，均有注释说明）】
- KisTextureMaskInfo.cpp 只在对比度之后统一 clamp（源码 L196-197）；本模块的
  公开单步函数按任务约定「越界一律 clip」，而 apply_three() 内部使用未截断的
  中间量，保证串联顺序与源码完全一致（亮度步提前截断会在 contrast<1 时改变结果）。
- 源码用 8-bit 整数通道运算，本模块用 float32 连续值；只有「深度/强度」按源码
  做了 8-bit 量化（quint8 强度 = round(depth*255)），因为这一步在实测里能被观察到
  （见 docs/纹理掩码复刻说明.md 的实测表：线性高度 d10 的实测值只与 260/255 吻合）。
"""

from __future__ import annotations

import math

import numpy as np

# ---------------------------------------------------------------------------
# Krita 纹理混合模式枚举（KisTextureOptionData.h 的 TexturingMode 顺序）
# 本模块只实现 ABR 转换实际会出现的 5 个模式，其余一律显式抛错。
# ---------------------------------------------------------------------------
MODE_MULTIPLY = 0          # Multiply（源码枚举 MULTIPLY）
MODE_SUBTRACT = 1          # Subtract（源码枚举 SUBTRACT）
MODE_HARD_MIX_PHOTOSHOP = 10      # Hard Mix (Photoshop)
MODE_HEIGHT_PHOTOSHOP = 14        # Height (Photoshop)
MODE_LINEAR_HEIGHT_PHOTOSHOP = 15  # Linear Height (Photoshop)

#: 已实现的模式号 -> 名称（测试与文档共用，未知模式号不在其中）
SUPPORTED_MODES = {
    MODE_MULTIPLY: "Multiply",
    MODE_SUBTRACT: "Subtract",
    MODE_HARD_MIX_PHOTOSHOP: "Hard Mix (Photoshop)",
    MODE_HEIGHT_PHOTOSHOP: "Height (Photoshop)",
    MODE_LINEAR_HEIGHT_PHOTOSHOP: "Linear Height (Photoshop)",
}

_F32 = np.float32


def _as_mask(value, name: str = "mask") -> np.ndarray:
    """把输入转成 float32、0..1 的数组（越界 clip，NaN/Inf 报错）。"""
    arr = np.asarray(value, dtype=_F32)
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} 含 NaN/Inf，无法复刻 Krita 掩码")
    return np.clip(arr, _F32(0.0), _F32(1.0))


def _strength_8bit(strength: float) -> float:
    """把 0..1 的强度按 Krita 8-bit 通道量化。

    源码 KisMaskingBrushCompositeOp.h 的 StrengthCompositeFunctionBase：
        strength(KoColorSpaceMaths<qreal, channels_type>::scaleToA(strength))
    通道类型为 quint8 时即 round(strength * 255)，C++ 的 qRound 对 .5 向上取整，
    因此这里用 floor(x + 0.5) 模拟，不能用 Python 的银行家舍入 round()。
    """
    s = min(max(float(strength), 0.0), 1.0)
    return math.floor(s * 255.0 + 0.5) / 255.0


def texture_mask_from_rgba(image, invert: bool = False) -> np.ndarray:
    """把纹理位图（H×W×4 uint8，RGBA）按源码公式折成 0..1 掩码。

    对应 KisTextureMaskInfo.cpp L184-190：
        const int grayValue = (red * 11 + green * 16 + blue * 5) / 32;
        float maskValue = (grayValue / 255.0) * alpha + (1 - alpha);
    C++ 的整数除法对非负数等价于向下取整，因此这里用 // 32。

    参数
    ----
    image : array_like
        (H, W, 4) 的 uint8/整型数组（RGBA），或 (H, W) 的灰度数组。
    invert : bool
        为 True 时对结果做 1-x（与源码 L199-201 同一语义）。
    """
    arr = np.asarray(image)
    if arr.ndim == 2:
        gray = arr.astype(np.int64)
        alpha = np.full(gray.shape, 255, dtype=np.int64)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        a_i64 = arr.astype(np.int64)
        red, green, blue, alpha = a_i64[..., 0], a_i64[..., 1], a_i64[..., 2], a_i64[..., 3]
        gray = (red * 11 + green * 16 + blue * 5) // 32
    else:
        raise ValueError(f"纹理数组形状应为 (H, W, 4) 或 (H, W)，实际 {arr.shape}")
    alpha_f = alpha.astype(_F32) / _F32(255.0)
    mask = (gray.astype(_F32) / _F32(255.0)) * alpha_f + (_F32(1.0) - alpha_f)
    mask = np.clip(mask, _F32(0.0), _F32(1.0)).astype(_F32)
    if invert:
        mask = (_F32(1.0) - mask).astype(_F32)
    return mask


def apply_invert(mask, enabled: bool) -> np.ndarray:
    """反相：enabled 为 True 时输出 1-mask。

    对应 KisTextureMaskInfo.cpp L199-201：
        if (m_invert) { maskValue = 1 - maskValue; }
    源码在反相之后还有一次 qBound（L203），本函数同样做 0..1 clip。
    """
    m = _as_mask(mask)
    if not enabled:
        return m
    return np.clip(_F32(1.0) - m, _F32(0.0), _F32(1.0)).astype(_F32)


def _brightness_raw(mask: np.ndarray, brightness: float) -> np.ndarray:
    """亮度步的未截断中间量（源码 L192：maskValue = maskValue - m_brightness）。"""
    return (mask - _F32(brightness)).astype(_F32)


def _contrast_raw(mask: np.ndarray, contrast: float) -> np.ndarray:
    """对比度步的未截断中间量。

    对应源码 L194：maskValue = ((maskValue - 0.5) * m_contrast) + 0.5
    以 0.5 为中心，contrast>1 拉开、contrast<1 压平（方向不要弄反）。
    """
    return ((mask - _F32(0.5)) * _F32(contrast) + _F32(0.5)).astype(_F32)


def apply_brightness(mask, brightness: float) -> np.ndarray:
    """亮度：输出 clip(mask - brightness, 0, 1)。

    对应 KisTextureMaskInfo.cpp L192：
        maskValue = maskValue - m_brightness;
    注意方向：brightness 越大蒙版越暗（源码是减法，不是加法）。
    源码在 L192 不立即截断，而是在对比度之后 L196-197 统一截断；本函数按任务
    「越界一律 clip」的约定在出口截断，若要与源码严格等价请用 apply_three()。
    """
    m = _brightness_raw(_as_mask(mask), brightness)
    return np.clip(m, _F32(0.0), _F32(1.0)).astype(_F32)


def apply_contrast(mask, contrast: float) -> np.ndarray:
    """对比度：输出 clip((mask-0.5)*contrast+0.5, 0, 1)。

    对应 KisTextureMaskInfo.cpp L194-197（乘法 + 统一截断）。
    contrast=1 为恒等；contrast=2 时以 0.5 为中心拉开：
        0.4 -> 0.3，0.6 -> 0.7。
    """
    m = _contrast_raw(_as_mask(mask), contrast)
    return np.clip(m, _F32(0.0), _F32(1.0)).astype(_F32)


def apply_three(mask, invert: bool, brightness: float, contrast: float) -> np.ndarray:
    """按源码顺序串联：亮度 -> 对比度 -> 截断 -> 反相 -> qBound。

    严格对应 KisTextureMaskInfo.cpp 的以下连续语句（L192-203）：
        L192  maskValue = maskValue - m_brightness;
        L194  maskValue = ((maskValue - 0.5) * m_contrast) + 0.5;
        L196-197  大于 1 取 1、小于 0 取 0；
        L199-201  if (m_invert) maskValue = 1 - maskValue;
        L203  maskValue = qBound(0.0f, maskValue, 1.0f);
    中间量刻意不提前截断，保证与源码逐步等价（提前截断会在 contrast<1 时改变结果）。
    注意此处不含 neutralPoint（默认 0.5 时为恒等）与 cutoffPolicy（默认 0 时不裁剪），
    这两段不在本任务范围内，见 docs/纹理掩码复刻说明.md「未覆盖」一节。
    """
    m = _as_mask(mask)
    m = _brightness_raw(m, brightness)
    m = _contrast_raw(m, contrast)
    m = np.clip(m, _F32(0.0), _F32(1.0))
    if invert:
        m = _F32(1.0) - m
    return np.clip(m, _F32(0.0), _F32(1.0)).astype(_F32)


def blend(base, tex, mode: int, strength: float = 1.0) -> np.ndarray:
    """把纹理掩码 tex 按 Krita 混合模式混到笔尖 alpha（base）上。

    参数
    ----
    base : array_like
        笔尖涂抹后的 alpha（0..1）。复刻「单张蒙版」时传全 1（不透明笔尖）。
    tex : array_like
        经 apply_three() 处理后的纹理掩码（0..1），即合成算子里的 src。
    mode : int
        Krita KisTextureOptionData::TexturingMode 枚举号，支持
        0=Multiply、1=Subtract、10=Hard Mix (Photoshop)、
        14=Height (Photoshop)、15=Linear Height (Photoshop)。
        其它枚举号显式抛 NotImplementedError（带枚举号），绝不静默回退。
    strength : float
        纹理深度（即源码的 strength = Texture/Strength/Value，0..1）。

    源码依据
    --------
    模式选择：plugins/paintops/libpaintop/kis_texture_option.cpp L335-354
        （switch 把枚举号换成 compositeOpId，再 createForAlphaSrc(..., strength, soft)）。
    算子公式：libs/ui/tool/strokes/KisMaskingBrushCompositeOp.h 中
        CompositeFunction<..., use_strength=true, use_soft_texturing=false> 分支。
    其中 s = round(strength*255)/255 为 8-bit 量化强度，inv(x)=1-x：
        0  Multiply              : mul(src, dst, s)          = src*dst*s
        1  Subtract              : max(0, dst - (src + (1-s)))
        10 Hard Mix (Photoshop)  : (src + dst*s > 1) ? 1 : 0
        14 Height (Photoshop)    : clamp(dst*(10s) - src, 0, 1)
        15 Linear Height (PS)    : md=dst*(10s);
                                   clamp(max((1-src)*md, md-src), 0, 1)
    归一化说明：源码在 0..255 通道上算，除以 unitValue 两次即等价于上面的 0..1 公式。
    本模块不含 useSoftTexturing 分支（KPP 默认 false），也不含 Height/Linear Height
    非 Photoshop 的 12/13 号模式（ABR 映射用不到，按任务要求显式抛错）。
    """
    b = _as_mask(base, "base")
    t = _as_mask(tex, "tex")
    if mode not in SUPPORTED_MODES:
        raise NotImplementedError(
            f"未实现的 Krita 纹理混合模式枚举号: {mode}（已实现: "
            f"{sorted(SUPPORTED_MODES)}）"
        )
    s = _F32(_strength_8bit(strength))

    if mode == MODE_MULTIPLY:  # src*dst*s
        out = t * b * s
    elif mode == MODE_SUBTRACT:  # max(0, dst-(src+(1-s)))
        out = b - (t + (_F32(1.0) - s))
    elif mode == MODE_HARD_MIX_PHOTOSHOP:  # src + dst*s > 1 -> 1 else 0
        out = np.where(t + b * s > _F32(1.0), _F32(1.0), _F32(0.0))
    elif mode == MODE_HEIGHT_PHOTOSHOP:  # clamp(dst*(10s)-src, 0, 1)
        out = b * (_F32(10.0) * s) - t
    else:  # MODE_LINEAR_HEIGHT_PHOTOSHOP
        modified_dst = b * (_F32(10.0) * s)
        multiply = (_F32(1.0) - t) * modified_dst
        height = modified_dst - t
        out = np.maximum(multiply, height)

    return np.clip(out, _F32(0.0), _F32(1.0)).astype(_F32)


def apply_depth(mask, depth: float) -> np.ndarray:
    """把纹理深度（0..1）按源码的强度语义作用到掩码上。

    源码里「深度」就是合成算子的 strength 参数
    （kis_texture_option.cpp L312: qreal strength = m_strengthOption.apply(info)；
    L352-354 传给 createForAlphaSrc），在 Multiply / Darken 这类线性模式中
    等价于给掩码乘上 8-bit 量化强度：mask * round(depth*255)/255。

    注意：Height / Linear Height 模式的强度不是简单乘在掩码上
    （见 KisMaskingBrushCompositeOp.h 的 weight=10*strength 分支），必须走
    blend(..., strength=depth)；compute_mask() 已按源码把 depth 传进 blend。
    """
    m = _as_mask(mask)
    return np.clip(m * _F32(_strength_8bit(depth)), _F32(0.0), _F32(1.0)).astype(_F32)


def compute_mask(tex, invert: bool, brightness: float, contrast: float,
                 mode: int, depth: float, base=None) -> np.ndarray:
    """完整流程：纹理 -> 亮度/对比度/反相 -> 混合模式 + 深度 -> 蒙版。

    参数与 kis_texture_option.cpp 的调用链一一对应：
        recalculateMask()           -> apply_three()
        m_strengthOption.apply()    -> depth（strength）
        createForAlphaSrc(...strength) + composite() -> blend()

    base 默认全 1（不透明笔尖的 alpha）。真实笔画里 base 是笔尖覆盖度，
    「单张蒙版」评估时用默认值即可；做整笔像素比对时由调用方传入笔尖 alpha。
    输出为 float32、0..1、无 NaN。
    """
    m = _as_mask(tex, "tex")
    proc = apply_three(m, invert=invert, brightness=brightness, contrast=contrast)
    if base is None:
        b = np.ones_like(proc, dtype=_F32)
    else:
        b = _as_mask(base, "base")
    return blend(b, proc, mode, strength=depth)
