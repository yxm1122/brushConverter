# -*- coding: utf-8 -*-
"""Photoshop 纹理掩码参考实现（任务 11B）。

本模块把「Photoshop 纹理亮度 / 对比度 / 深度 / 混合模式」写成可执行的 numpy 纯函数，
接口风格对齐 tools/texture_mask.py（float32 输入输出、0..1、纯函数、NaN/Inf 抛错），
但**刻意不调用 tools/texture_mask.py**：那是 Krita 侧语义（深度 weight=10*s、强度
strength=round(depth*255)/255、中心 0.5），而 PS 侧实测是另一套（高度系权重 12*q、
中心 127、对比度因子表、顺序随对比度符号翻转）。两者混用会得到「看起来能跑、
其实两边参数互相抵消」的假结果。

======================================================================
一、依据分层（必须分清，报告里也不许混写）
======================================================================
1) **参数语义层（逆向证实，11A 动态观测）**
   依据：docs/研究报告/ps-逆向-动态.md
   - §3.2 表 A（第 1~12 行）：brightness 存进运行期对象 +0x1c，5 个观测值
     0 / +50 / -50 / +150 / -150 与请求值**逐位相同**：int32、无比例、无偏移、无 clamp；
     contrast 存进 +0x18，5 个观测值 0 / +50 / -50 / +100 / -100 同样逐位相同、无 clamp；
     +0x10（textureScale）恒为 double 100.0。
   - §3.3 表 B（第 19~28 行）：depth 请求 101 → 对象 +0x08 观测为 100（clamp 到 0..100）；
     运行期混合模式号 multiply=7、subtract=39、height=40、linearHeight=41。
   - §4 假设 3：depth = clamp(round(v), 0, 100) 属「实证」（静态指令 + 动态取值双向一致）。
   - §4 假设 5：运行期模式号是继「静态注册表」「Krita 枚举」之后的第三套编号，
     与任务 11 静态报告的 0x2A/0x29 不是同一套，落地时必须用动态这套。
2) **像素层公式（数据拟合，不是反向工程）**
   依据：docs/研究报告/lut-拟合.md 第 4 节（任务 12）。
   11A §5.1 明确记录「像素消费端未判定」（页级内存监控 0 命中），因此
   「参数 → 像素」这一层**没有**反汇编证据，本模块的像素公式来自任务 12 的
   231 张真值图拟合并经 201 张/1,005,000 像素评估：
   1D LUT 差异比例 0.00%、参数化公式 0.01%。**不得**把本模块写成「逆向推出了像素公式」。
   拟合值来源（机器可读）：tests/baselines/ps/_work/lut_fit_result.json 的
   params = {pivot=127.0, k_fitted={...}, order="sign_c", height_weight=12.0}。

======================================================================
二、像素层公式（任务 12 拟合结构，逐行照抄）
======================================================================
    q = round(depth * 255 / 100)                      # 深度先按 8bit percentUnit 量化
    p = 127.0                                          # 对比度中心（拟合值，不是 127.5）
    k = K_TABLE[c] 或 contrast_factor(c)               # 对比度因子表
    C >= 0（先亮度后对比度，顺序规则 sign_c）：
        u = clip(p + (clip(t + b, 0, 255) - p) * k, 0, 255)
    C <  0（先对比度后亮度）：
        u = clip(clip(p + (t - p) * k, 0, 255) + b, 0, 255)
    正片叠底   out = clip(255 + (u - 255) * q/255, 0, 255)
    减去       out = clip(255 - u * q/255, 0, 255)
    高度       out = clip(12*q - u, 0, 255)
    线性高度   out = clip(max((255 - u) * 12*q / 255, 12*q - u), 0, 255)
    最终 Alpha = round(out) / 255                      # PS 输出是 8bit，参考实现同样量化

======================================================================
三、参数语义（本模块对外承诺的行为）
======================================================================
- brightness：接受 [-150, +150]，**原样使用**（不缩放、不偏移、不加权、不 clamp）。
  11A §3.2 逐位证实 PS 内部存的就是这个整数；超出界面范围的值 11A 未观测，
  本模块**拒绝**（ValueError）而不是猜一个「合理」的截断。
- contrast：接受 [-100, +100]，同样原样使用、不 clamp。注意现有 src 的 calibrated
  路径把 C 夹在 [-50, 100]（见 src/brush_converter/kpp/preset_xml.py），那是移植侧的
  表达限制，不是 PS 语义；本参考实现按 11A 观测覆盖到 -100。
- depth：接受任意实数，先 clamp 到 [0, 100]（11A §3.3 观测 101→100），再量化
  q = round(depth * 2.55)。
- mode：同时接受 **PS 运行期号（7/39/40/41）** 与 **Krita 号（0/1/14/15）**，
  显式映射到同一个内部名字；未知号抛 NotImplementedError（消息里带号）。
  没有任何隐式回退，避免把「不认识的模式」静默算成「正片叠底」。
- tex255：0..255 的纹理灰度值（uint8/浮点皆可），越界 clip，NaN/Inf 抛 ValueError。
"""

from __future__ import annotations

import math

import numpy as np

__all__ = [
    "KRITA_MODE_HEIGHT",
    "KRITA_MODE_LINEAR_HEIGHT",
    "KRITA_MODE_MULTIPLY",
    "KRITA_MODE_SUBTRACT",
    "K_TABLE",
    "MODE_ALIASES",
    "MODE_KRITA",
    "MODE_NAMES",
    "MODE_PS_RUNTIME",
    "PS_MODE_HEIGHT",
    "PS_MODE_LINEAR_HEIGHT",
    "PS_MODE_MULTIPLY",
    "PS_MODE_SUBTRACT",
    "PIVOT",
    "SUPPORTED_MODE_IDS",
    "contrast_factor",
    "depth_quantize",
    "ps_mask",
    "resolve_mode",
]

_F64 = np.float64

# ---------------------------------------------------------------------------
# 一、混合模式编号（第三套编号的落地，依据 11A §3.3 表 B / §4 假设 5）
#
# PS 运行期对象 +0x04 里的模式号（动态实测）：multiply=7、subtract=39(0x27)、
# height=40(0x28)、linearHeight=41(0x29)。
# Krita KisTextureOptionData::TexturingMode（tools/texture_mask.py 用的那套）：
# Multiply=0、Subtract=1、Height (Photoshop)=14、Linear Height (Photoshop)=15。
# 两套编号必须显式映射，绝不能用同一张表互相顶替。
# ---------------------------------------------------------------------------
PS_MODE_MULTIPLY = 7
PS_MODE_SUBTRACT = 39
PS_MODE_HEIGHT = 40
PS_MODE_LINEAR_HEIGHT = 41

KRITA_MODE_MULTIPLY = 0
KRITA_MODE_SUBTRACT = 1
KRITA_MODE_HEIGHT = 14
KRITA_MODE_LINEAR_HEIGHT = 15

#: 内部规范名 -> 中文名（报告与真值图文件名共用）
MODE_NAMES = {
    "multiply": "正片叠底",
    "subtract": "减去",
    "height": "高度",
    "linearHeight": "线性高度",
}

#: PS 运行期号 -> 内部规范名
MODE_PS_RUNTIME = {
    PS_MODE_MULTIPLY: "multiply",
    PS_MODE_SUBTRACT: "subtract",
    PS_MODE_HEIGHT: "height",
    PS_MODE_LINEAR_HEIGHT: "linearHeight",
}

#: Krita 枚举号 -> 内部规范名
MODE_KRITA = {
    KRITA_MODE_MULTIPLY: "multiply",
    KRITA_MODE_SUBTRACT: "subtract",
    KRITA_MODE_HEIGHT: "height",
    KRITA_MODE_LINEAR_HEIGHT: "linearHeight",
}

#: 两套编号的并表（唯一合法入参集合）
MODE_ALIASES = {**MODE_PS_RUNTIME, **MODE_KRITA}
SUPPORTED_MODE_IDS = tuple(sorted(MODE_ALIASES))

# ---------------------------------------------------------------------------
# 二、像素层常数（任务 12 拟合结果，见模块 docstring 一.2）
# ---------------------------------------------------------------------------
#: 对比度中心（0..255 尺度）。任务 12 用未截断区直线最小二乘拟合得到 127.000。
#: 注意它不是 127.5 —— 8bit 整数域的中点取整是 127，这个 0.5 的偏差在对比度
#: 非零时会被放大，是旧经验公式之外另一个必须写对的常数。
PIVOT = 127.0

#: 高度/线性高度模式的权重 W（实测 A = W*q，Krita 同一算子写的是 10*s）。
#: 任务 12 用 50 张图的未截断样本拟合得到 12.0000。
HEIGHT_WEIGHT = 12.0

#: PS 对比度 -> 因子 k 的拟合表（tests/baselines/ps/_work/lut_fit_result.json
#: 的 params.k_fitted 原值）。表外取值回落到 contrast_factor() 的经验式，
#: 这是任务 12 的既定做法（拟合网格只覆盖了这些取值）。
K_TABLE = {
    -50: 0.502947,
    -25: 0.753296,
    -10: 0.900000,
    0: 1.000000,
    10: 1.111111,
    25: 1.328681,
    50: 2.000000,
    75: 4.000000,
    100: 1_000_000.0,
}


# ---------------------------------------------------------------------------
# 三、参数校验与映射
# ---------------------------------------------------------------------------
def resolve_mode(mode) -> str:
    """把 PS 运行期号或 Krita 枚举号换成内部规范名。

    参数
    ----
    mode : int
        7/39/40/41（PS 运行期号，11A 动态实测）或 0/1/14/15（Krita 号）。

    返回
    ----
    "multiply" / "subtract" / "height" / "linearHeight"

    异常
    ----
    NotImplementedError
        未知模式号（消息里带原始号，方便定位）。按任务要求**不允许静默回退**：
        任务 11 静态注册表的 0x2A/0x29、Hard Mix 的 0x1E 等都属未知号，
        直接抛错比猜一个模式安全。
    """
    if isinstance(mode, bool):  # bool 是 int 子类，但 True==1 会被误映射成 subtract
        raise NotImplementedError(f"未知纹理混合模式号: {mode!r}（已支持 PS 号 7/39/40/41 与 Krita 号 0/1/14/15）")
    try:
        key = int(mode)
    except (TypeError, ValueError):
        raise NotImplementedError(
            f"未知纹理混合模式号: {mode!r}（已支持 PS 号 7/39/40/41 与 Krita 号 0/1/14/15）"
        ) from None
    if key != mode:  # 7.5 这类非整数
        raise NotImplementedError(
            f"未知纹理混合模式号: {mode!r}（必须为整数；已支持 PS 号 7/39/40/41 与 Krita 号 0/1/14/15）"
        )
    if key not in MODE_ALIASES:
        raise NotImplementedError(
            f"未知纹理混合模式号: {key}（已支持 PS 运行期号 7=正片叠底/39=减去/40=高度/"
            f"41=线性高度，以及 Krita 号 0/1/14/15；两者之外没有可靠映射）"
        )
    return MODE_ALIASES[key]


def _integer_param(name: str, value, lo: int, hi: int) -> int:
    """校验亮度/对比度：必须是 [lo, hi] 内的整数，**不做 clamp**。

    11A §3.2 证实 PS 对象里存的是 int32 且原样不裁。这里只做类型/范围校验：
    - 非整数（如 50.5）→ ValueError：PS 的 int32 存储对小数无观测，不能猜四舍五入；
    - 超出界面范围 → ValueError：PS 界面本身不接受（无法产生真值），
      猜一个截断值会制造「参考实现比 PS 更宽容」的假象。
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是整数，收到布尔值 {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是整数，收到 {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数，收到 {value!r}")
    if number != int(number):
        raise ValueError(f"{name} 必须是整数（PS 运行期对象里是 int32），收到 {value!r}")
    integer = int(number)
    if not (lo <= integer <= hi):
        raise ValueError(
            f"{name} 超出 PS 界面范围 [{lo}, {hi}]：{integer}。"
            f"11A 只观测到范围内取值原样写入，范围外行为未观测，因此拒绝而不是 clamp。"
        )
    return integer


def depth_quantize(depth) -> int:
    """深度 -> 8bit percentUnit 整数 q（0..255）。

    两步（顺序不能反）：
        1) depth = clamp(depth, 0, 100)   —— 11A §3.3 判读 1 动态复现：请求 101 存成 100；
        2) q = round(depth * 255 / 100)   —— 任务 12 §4.2：高度 d2/d4/d5/d6/d10/d12
           在输入 0 处输出 60/120/156/180/312/372 = 12*round(2.55*d)，
           说明深度先量化成 0..255 的整数 q。

    对 101 这类越界值：先 clamp 到 100 再量化得 q=255，与「先量化再 clamp」
    在边界上结果相同（255 是上限），本实现按 11A 观测到的顺序（先 clamp）写。
    """
    number = float(depth)
    if not math.isfinite(number):
        raise ValueError(f"depth 必须是有限数，收到 {depth!r}")
    clamped = min(max(number, 0.0), 100.0)
    q = int(math.floor(clamped * 255.0 / 100.0 + 0.5))  # C++ 的 qRound 对 .5 向上
    return max(0, min(255, q))


def contrast_factor(contrast: int) -> float:
    """对比度 -> 因子 k：优先用任务 12 的拟合表，表外回落到经验式。

    经验式（任务 10/12 两轮一致）：C<0 → 1+C/100；0<=C<100 → 1/(1-C/100)；
    C>=100 → 用 1e6 逼近完全二值化（数学上是无穷大）。
    表内取值与经验式只在 C=+25 有 0.0046 的差（0.0046 = 1.3333-1.3287），
    任务 12 判定为「整数域运算 + 截断」造成的偏差，拟合值优先。
    """
    if contrast in K_TABLE:
        return float(K_TABLE[contrast])
    if contrast < 0:
        return 1.0 + contrast / 100.0
    if contrast >= 100:
        return 1_000_000.0
    return 1.0 / (1.0 - contrast / 100.0)


# ---------------------------------------------------------------------------
# 四、像素层实现（任务 12 拟合结构的逐行 numpy 版）
# ---------------------------------------------------------------------------
def _as_texture(tex255) -> np.ndarray:
    """纹理值 -> float64 ndarray（0..255）。

    越界 clip 到 0..255（PS 纹理来自 8bit 通道，越界是调用方数据错误，
    但沿用 tools/texture_mask.py 的「输入越界一律 clip」约定）；
    NaN/Inf 抛 ValueError，避免把坏数据算成一张「看起来正常」的蒙版。
    """
    arr = np.asarray(tex255, dtype=_F64)
    if not np.all(np.isfinite(arr)):
        raise ValueError("tex255 含 NaN/Inf，无法复刻 PS 纹理掩码")
    return np.clip(arr, 0.0, 255.0)


def _preprocess(tex: np.ndarray, brightness: int, contrast: int) -> np.ndarray:
    """亮度/对比度前置变换：t(0..255) -> u(0..255)。

    逐字复刻 docs/研究报告/lut-拟合.md §4.1 的「中心 127 + 因子表 + 顺序随符号翻转」：
        C >= 0（顺序 M1：先亮度后对比度，亮度步先截断）
            u = p + (clip(t + b, 0, 255) - p) * k
        C <  0（顺序 M2：先对比度后亮度）
            w = clip(p + (t - p) * k, 0, 255)   # k<1 时 w 恒在 0..255 内，此处截断是空操作
            u = w + b
    两个分支最后都 clip 到 0..255。
    任务 12 的四个候选顺序里 sign_c 的未截断区 MAE = 0.1254，其余三个是 24.85~50.13，
    相差两个数量级，所以「按对比度符号翻转」不是可选项而是结构。
    """
    k = contrast_factor(contrast)
    if contrast >= 0:
        shifted = np.clip(tex + brightness, 0.0, 255.0)
        u = PIVOT + (shifted - PIVOT) * k
    else:
        scaled = np.clip(PIVOT + (tex - PIVOT) * k, 0.0, 255.0)
        u = scaled + brightness
    return np.clip(u, 0.0, 255.0)


def _compose(u: np.ndarray, mode_name: str, q: int) -> np.ndarray:
    """按 4 条模式律把前置掩版 u 与深度 q 合成为输出（0..255）。

    公式逐字来自 docs/研究报告/lut-拟合.md §4.3（任务 12 从数据定出的固定结构，
    不需要拟合的部分）：
        正片叠底   out = 255 + (u - 255) * s
        减去       out = 255 - u * s
        高度       out = W*q - u
        线性高度   out = max((255-u) * W*q/255, W*q - u)
    其中 s = q/255，W = 12。
    """
    s = q / 255.0
    if mode_name == "multiply":
        out = 255.0 + (u - 255.0) * s
    elif mode_name == "subtract":
        out = 255.0 - u * s
    elif mode_name == "height":
        out = HEIGHT_WEIGHT * q - u
    elif mode_name == "linearHeight":
        modified = HEIGHT_WEIGHT * q
        out = np.maximum((255.0 - u) * modified / 255.0, modified - u)
    else:  # resolve_mode() 已经拦下未知模式，这里只是防御
        raise NotImplementedError(f"未实现的内部模式名: {mode_name!r}")
    return np.clip(out, 0.0, 255.0)


def ps_mask(tex255, brightness, contrast, mode, depth) -> np.ndarray:
    """PS 纹理掩码：给定纹理值、亮度、对比度、模式、深度，返回 0..1 的 float32 蒙版。

    >>> m = ps_mask(tex255=128, brightness=0, contrast=0, mode=7, depth=100)
    >>> round(float(m), 4)  # 正片叠底 d100 b0 c0 是恒等变换：128/255 = 0.50196
    0.502

    参数
    ----
    tex255 : array_like
        纹理灰度值，0..255（PS 8bit 通道）。越界 clip；NaN/Inf 抛 ValueError。
    brightness : int
        PS 纹理亮度，界面范围 **[-150, +150]**。11A §3.2 表 A 第 2~5 行逐位证实：
        PS 内部把它当 int32 **原样**存放，不缩放、不加偏移、不 clamp。
        本函数同样原样使用；超出范围抛 ValueError（范围外无观测，不猜）。
    contrast : int
        PS 纹理对比度，界面范围 **[-100, +100]**。11A §3.2 表 A 第 6~11 行同样逐位证实
        「原样、无 clamp」；本函数原样使用，超范围抛 ValueError。
    mode : int
        **同时接受两套编号**：PS 运行期号 7=正片叠底 / 39=减去 / 40=高度 /
        41=线性高度（11A §3.3 表 B 动态实测），以及 Krita 号 0/1/14/15
        （tools/texture_mask.py 的枚举）。两套显式映射到同一内部名；
        未知模式号抛 NotImplementedError（带号），无静默回退。
    depth : float
        纹理深度 0..100（%）。先 clamp 到 [0, 100]（11A §3.3：请求 101 观测为 100），
        再量化 q = round(depth*2.55)（任务 12 §4.2）。

    返回
    ----
    numpy.ndarray (float32，0..1)
        与 tex255 同形状；PS 侧最终是 8bit Alpha，本实现同样量化到 8bit 再 /255，
        因此与真值图可以逐像素比较（容差只需吸收 PNG 取整，不需要吸收浮点舍入）。

    依据边界（重要）
    ----------------
    - 参数语义（brightness/contrast/depth/mode 的取值、类型、clamp）：**11A 逆向证实**；
    - 像素公式（中心 127、k 表、符号顺序、W=12、q 量化、4 条模式律）：**任务 12 数据拟合**
      （LUT 0.00%、参数化 0.01%），11A 的像素消费端未定位，**不是**反汇编推导。
    """
    tex = _as_texture(tex255)
    brightness_int = _integer_param("brightness", brightness, -150, 150)
    contrast_int = _integer_param("contrast", contrast, -100, 100)
    q = depth_quantize(depth)
    mode_name = resolve_mode(mode)

    u = _preprocess(tex, brightness_int, contrast_int)
    out = _compose(u, mode_name, q)
    alpha8 = np.floor(out + 0.5)  # PS 输出 8bit Alpha，qRound 对 .5 向上
    return (alpha8 / 255.0).astype(np.float32)


# ---------------------------------------------------------------------------
# 五、真值图批量核验（CLI：python tools/ps_texture_reference.py --eval）
#
# 纯函数 API 到此为止；以下是给报告出「四列误差表」用的核验入口，只在 __main__
# 时才导入 PIL / json / tools.lut_fit / tools.texture_mask / src，不影响上面对外承诺。
# 误差口径与 tools/imgdiff.py::diff_alpha 一致：tol=2、分母 = 全部像素。
#
# 注意：本节里 legacy / src calibrated 两列**按定义**就是 Krita 掩码公式
# （tools/texture_mask.py）的输出——那正是现有转换器写进 .kpp 的东西，必须调用；
# 而 ps_mask() 本体绝不调用 tools/texture_mask.py（PS/Krita 两套语义，见模块开头）。
# ---------------------------------------------------------------------------
#: 核验用例：(模式中文名, 深度, 亮度, 对比度)，覆盖 4 模式 x 多深度 x 亮度/对比度极值。
EVAL_COMBOS = [
    ("正片叠底", 100, 0, 0),        # 恒等参考：Alpha 应逐像素等于纹理值
    ("正片叠底", 100, 50, 50),
    ("正片叠底", 100, -150, 50),
    ("正片叠底", 60, 0, -50),
    ("减去", 100, 0, 0),
    ("减去", 60, 50, 0),
    ("减去", 30, -50, -50),
    ("减去", 100, 0, 50),
    ("高度", 5, 0, 0),
    ("高度", 10, 50, 0),
    ("高度", 2, 0, 50),
    ("高度", 12, 0, 0),
    ("高度", 10, 0, -50),
    ("线性高度", 5, 0, 0),
    ("线性高度", 10, -50, 0),
    ("线性高度", 2, 0, 50),
    ("线性高度", 12, 0, 0),
    ("线性高度", 10, 0, -50),
]

_MODE_CN_TO_PS = {
    "正片叠底": PS_MODE_MULTIPLY,
    "减去": PS_MODE_SUBTRACT,
    "高度": PS_MODE_HEIGHT,
    "线性高度": PS_MODE_LINEAR_HEIGHT,
}


def _load_eval_inputs(repo_root):
    """读取清单 + 纹理 + 真值图配对（只读 tests/baselines/ps/）。"""
    import json

    from PIL import Image

    baseline_dir = repo_root / "tests" / "baselines" / "ps"
    manifest_path = baseline_dir / "manifest.json"
    texture_path = baseline_dir / "_work" / "texture_noise64.png"
    if not manifest_path.is_file() or not texture_path.is_file():
        raise FileNotFoundError(
            f"缺少基准库文件：{manifest_path} 或 {texture_path}（本项需任务 05/12 的产物）"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with Image.open(texture_path) as im:
        tex = np.asarray(im.convert("L"), dtype=np.uint8)
    yy = np.arange(100) % tex.shape[0]
    xx = np.arange(100) % tex.shape[1]
    tiled = tex[yy][:, xx].astype(np.int32)

    index = {(e.get("mode"), int(e.get("depth", -1)), int(e.get("brightness", -999)),
              int(e.get("contrast", -999))): e
             for e in manifest.get("images", [])
             if e.get("provenance") == "auto_jsx"
             and (e.get("texture") or {}).get("name") == "bc_texture_noise64"}
    rows = []
    for mode_cn, depth, brightness, contrast in EVAL_COMBOS:
        entry = index.get((mode_cn, depth, brightness, contrast))
        if entry is None:
            raise KeyError(f"基准库缺图：{mode_cn} d{depth} b{brightness} c{contrast}")
        path = baseline_dir / entry["file"]
        with Image.open(path) as im:
            truth = np.asarray(im.convert("RGBA"), dtype=np.uint8)[:, :, 3].astype(np.int32)
        rows.append({
            "file": entry["file"], "mode_cn": mode_cn, "depth": depth,
            "brightness": brightness, "contrast": contrast,
            "t": tiled, "truth": truth,
        })
    return rows


def _calibrated_predict(t, mode_cn, depth, brightness, contrast):
    """src 现状（任务 13 已转正）的 calibrated 路径，作为第 5 列对照。

    做法：用 src 的 texture_brightness_contrast() 把 PS 亮度/对比度反解成 Krita
    参数，再套 Krita 掩码公式（tools/texture_mask.compute_mask）——这正是转换器
    实际写进 .kpp 的东西，因此它才是「src 现状」的像素级代表。
    """
    from brush_converter.kpp.preset_xml import texture_brightness_contrast
    from tools.lut_fit import KRITA_MODE_ID
    from tools.texture_mask import compute_mask

    k_brightness, k_contrast = texture_brightness_contrast(brightness, contrast)
    mask_in = (np.asarray(t, dtype=np.float32) / 255.0).astype(np.float32)
    out = compute_mask(
        mask_in, invert=False, brightness=k_brightness, contrast=k_contrast,
        mode=KRITA_MODE_ID[mode_cn], depth=depth / 100.0,
    )
    return np.floor(out.astype(np.float64) * 255.0 + 0.5)


def evaluate_baselines(verbose: bool = True) -> dict:
    """用真值图核验：参考实现 / tools/lut_fit 的 LUT / 现有 legacy 公式 / src calibrated。

    四列口径：
    - 参考实现、legacy、calibrated 都是公式，直接在**全图** 10000 像素上算，
      tol=2、分母=全部像素（与 tools/imgdiff.py::diff_alpha 一致）；
    - LUT 是逐图拟合的形态，若在全图上拟合会自洽成 0（自证），因此沿用任务 12
      口径：棋盘格偶数格拟合、奇数格评估（「评估半」）；同时给出「训练半拟合 ->
      全图预测」的数字并注明它偏乐观。
    """
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    if str(repo_root / "src") not in sys.path:
        sys.path.insert(0, str(repo_root / "src"))

    from tools.lut_fit import TOL, baseline_predict, fit_lut, metrics, pixel_split, predict_lut

    rows = _load_eval_inputs(repo_root)
    train_mask, eval_mask = pixel_split(100)
    per_image = []
    for row in rows:
        t = row["t"]
        truth = row["truth"]
        mode_ps = _MODE_CN_TO_PS[row["mode_cn"]]
        ref = np.rint(ps_mask(t, row["brightness"], row["contrast"], mode_ps, row["depth"]) * 255.0).astype(np.int32)
        legacy = np.rint(baseline_predict(t, row["mode_cn"], row["depth"],
                                          row["brightness"], row["contrast"])).astype(np.int32)
        try:
            calibrated = _calibrated_predict(t, row["mode_cn"], row["depth"],
                                             row["brightness"], row["contrast"]).astype(np.int32)
            calibrated_err = None
        except Exception as exc:  # src 导入失败时如实标注，不伪造数字
            calibrated = None
            calibrated_err = f"{type(exc).__name__}: {exc}"
        table = fit_lut(t.ravel()[train_mask], truth.ravel()[train_mask])
        lut_eval = predict_lut(table, t.ravel()[eval_mask]).astype(np.int32)
        lut_full = predict_lut(table, t.ravel()).astype(np.int32)

        per_image.append({
            "file": row["file"], "mode_cn": row["mode_cn"], "depth": row["depth"],
            "brightness": row["brightness"], "contrast": row["contrast"],
            "ref": metrics(ref.ravel(), truth.ravel()),
            "ref_eval": metrics(ref.ravel()[eval_mask], truth.ravel()[eval_mask]),
            "lut_eval": metrics(lut_eval, truth.ravel()[eval_mask]),
            "lut_full": metrics(lut_full, truth.ravel()),
            "legacy": metrics(legacy.ravel(), truth.ravel()),
            "legacy_eval": metrics(legacy.ravel()[eval_mask], truth.ravel()[eval_mask]),
            "calibrated": (metrics(calibrated.ravel(), truth.ravel()) if calibrated is not None else None),
            "calibrated_eval": (
                metrics(calibrated.ravel()[eval_mask], truth.ravel()[eval_mask])
                if calibrated is not None else None
            ),
            "calibrated_error": calibrated_err,
        })

    def _acc(keys):
        acc = {"pixels": 0, "diff_pixels": 0, "max_delta": 0, "abs_sum": 0.0}
        for row in per_image:
            m = row[keys]
            if m is None:
                continue
            acc["pixels"] += m["pixels"]
            acc["diff_pixels"] += m["diff_pixels"]
            acc["max_delta"] = max(acc["max_delta"], m["max_delta"])
            acc["abs_sum"] += m["mae"] * m["pixels"]
        pixels = max(acc["pixels"], 1)
        return {"pixels": acc["pixels"], "diff_pixels": acc["diff_pixels"],
                "diff_ratio": acc["diff_pixels"] / pixels, "max_delta": acc["max_delta"],
                "mae": acc["abs_sum"] / pixels}

    overall = {
        "ref": _acc("ref"),
        "ref_eval": _acc("ref_eval"),
        "lut_eval": _acc("lut_eval"),
        "lut_full": _acc("lut_full"),
        "legacy": _acc("legacy"),
        "legacy_eval": _acc("legacy_eval"),
        "calibrated": _acc("calibrated"),
        "calibrated_eval": _acc("calibrated_eval"),
    }

    if verbose:
        print(f"[核验] 真值图 {len(per_image)} 张（4 模式），纹理 seed=20261118 64x64 按 (0,0) 相位平铺")
        print(f"[口径] 全图 100x100，tol={TOL}，差异比例=差异像素/10000（与 tools/imgdiff.py::diff_alpha 一致）")
        print("")
        print("列：图名 | 参数 | 参考实现(max) | LUT评估半 | legacy | src calibrated —— 均为差异比例")
        for row in per_image:
            cal = (f"{row['calibrated']['diff_ratio']*100:6.2f}%" if row["calibrated"] else "  n/a")
            print(f"{row['file']:<44} | {row['mode_cn']} d{row['depth']} b{row['brightness']} "
                  f"c{row['contrast']} | ref {row['ref']['diff_ratio']*100:6.2f}% "
                  f"(max {row['ref']['max_delta']:>2d}) | LUT {row['lut_eval']['diff_ratio']*100:6.2f}% "
                  f"| legacy {row['legacy']['diff_ratio']*100:6.2f}% | calib {cal}")
        print("")
        print("[汇总] 全图口径（公式列，与 diff_alpha 同口径）")
        print(f"{'形态':<26}{'差异像素/总像素':>22}{'差异比例':>12}{'最大差值':>10}{'平均绝对差':>12}")
        for key, name in (("ref", "参考实现(PS 8bit 公式)"), ("legacy", "现有经验公式 legacy"),
                          ("calibrated", "src 现状 calibrated(任务13)")):
            r = overall[key]
            cells = f"{r['diff_pixels']}/{r['pixels']}"
            print(f"{name:<26}{cells:>22}{r['diff_ratio']*100:>11.2f}%"
                  f"{r['max_delta']:>10}{r['mae']:>12.2f}")
        print("")
        print("[汇总] 评估半口径（任务 12 同口径：棋盘格偶数格拟合 / 奇数格评估）")
        print(f"{'形态':<26}{'差异像素/总像素':>22}{'差异比例':>12}{'最大差值':>10}{'平均绝对差':>12}")
        for key, name in (("ref_eval", "参考实现(PS 8bit 公式)"), ("lut_eval", "tools/lut_fit 的 1D LUT"),
                          ("legacy_eval", "现有经验公式 legacy"), ("calibrated_eval", "src 现状 calibrated(任务13)")):
            r = overall[key]
            cells = f"{r['diff_pixels']}/{r['pixels']}"
            print(f"{name:<26}{cells:>22}{r['diff_ratio']*100:>11.2f}%"
                  f"{r['max_delta']:>10}{r['mae']:>12.2f}")
        print("")
        print(f"[备注] LUT 全图口径（训练半拟合 -> 全图预测，训练半自洽故偏乐观）："
              f"{overall['lut_full']['diff_ratio']*100:.2f}%，最大差值 {overall['lut_full']['max_delta']}")
        failed = [row["file"] for row in per_image if row["calibrated_error"]]
        if failed:
            print(f"[警告] src calibrated 列不可用（{len(failed)} 张）：{failed[0]} -> {per_image[0]['calibrated_error']}")

    return {"samples": len(per_image), "tol": TOL, "per_image": per_image, "overall": overall}


def evaluate_all_records(verbose: bool = True) -> dict:
    """全量核验：在工具 12 的同一批 201 张真值图上算参考实现误差。

    为什么要多这一层：上面的 18 张表覆盖 4 模式多种参数，但没含 C=+100
    （任务 12 的 108 个差异像素全部来自 C=100 的理论二值化边界）。这里用
    tools.lut_fit.build_dataset() 的**全部合格记录**（自动图 + 历史人工真值图，
    共 201 张/1,005,000 像素），按任务 12 的评估半口径（棋盘格偶数格拟合类
    形态不参与，这里只有公式，故两半都可用；为与任务 12 可比，取奇数格评估）
    算三件事：
      1) 参考实现 vs 真值：差异比例 / 最大差值 / 平均绝对差（tol=2）；
      2) 参考实现 vs tools/lut_fit 的参数化公式：逐像素不一致数（应为 0，
         证明本实现与任务 12 拟合结构一致，不是另写了一套）。
    """
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    if str(repo_root / "src") not in sys.path:
        sys.path.insert(0, str(repo_root / "src"))

    from tools.lut_fit import (
        MODES,
        TOL,
        build_dataset,
        fit_parametric,
        metrics,
        parametric_predict,
        pixel_split,
    )

    records, _notes = build_dataset()
    params = fit_parametric(records, verbose=False)
    _train, eval_mask = pixel_split(100)
    acc = {"pixels": 0, "diff_pixels": 0, "max_delta": 0, "abs_sum": 0.0}
    mismatch_pixels = 0
    used, skipped = [], []
    for rec in records:
        if rec["saturated"] or rec["trivial"] or rec["mode"] not in MODES:
            skipped.append(rec["file"])
            continue
        flat_t = rec["t"].ravel()
        truth = rec["a"].ravel()
        pred = np.rint(
            ps_mask(flat_t, rec["b"], rec["c"], _MODE_CN_TO_PS[rec["mode"]], rec["depth"])
            .astype(np.float64) * 255.0
        ).astype(np.int32)
        m = metrics(pred[eval_mask], truth[eval_mask])
        acc["pixels"] += m["pixels"]
        acc["diff_pixels"] += m["diff_pixels"]
        acc["max_delta"] = max(acc["max_delta"], m["max_delta"])
        acc["abs_sum"] += m["mae"] * m["pixels"]
        param = np.floor(
            parametric_predict(flat_t[eval_mask], rec["mode"], rec["depth"], rec["b"], rec["c"], params) + 0.5
        ).astype(np.int32)
        mismatch_pixels += int(np.count_nonzero(pred[eval_mask] != param))
        used.append(rec["file"])
    pixels = max(acc["pixels"], 1)
    overall = {
        "samples": len(used),
        "skipped": len(skipped),
        "pixels": acc["pixels"],
        "diff_pixels": acc["diff_pixels"],
        "diff_ratio": acc["diff_pixels"] / pixels,
        "max_delta": acc["max_delta"],
        "mae": acc["abs_sum"] / pixels,
        "tol": TOL,
        "param_mismatch_pixels": mismatch_pixels,
    }
    if verbose:
        print(f"[全量核验] 参与 {overall['samples']} 张 / 跳过 {overall['skipped']} 张"
              f"（饱和图、恒等参考图、非 4 模式历史图，与任务 12 同口径）")
        print(f"[全量核验] 参考实现 vs 真值：差异像素 {overall['diff_pixels']}/{overall['pixels']}"
              f"（{overall['diff_ratio'] * 100:.2f}%），最大差值 {overall['max_delta']}，"
              f"平均绝对差 {overall['mae']:.2f}（评估半口径，tol={TOL}）")
        print(f"[全量核验] 参考实现 vs 任务 12 参数化公式：不一致像素 {mismatch_pixels}/{overall['pixels']}"
              f"（应为 0，证明实现与拟合结构一致）")
    return overall


def main(argv=None) -> int:
    """CLI：--eval 跑真值图批量核验并打印四列误差表。"""
    import argparse

    parser = argparse.ArgumentParser(description="PS 纹理掩码参考实现（任务 11B）")
    parser.add_argument("--eval", action="store_true", help="用 tests/baselines/ps 真值图做四列误差核验")
    parser.add_argument("--eval-all", action="store_true",
                        help="在任务 12 的全部合格真值图上做全量核验（201 张/1,005,000 像素）")
    args = parser.parse_args(argv)
    if args.eval:
        evaluate_baselines()
        return 0
    if args.eval_all:
        evaluate_all_records()
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
