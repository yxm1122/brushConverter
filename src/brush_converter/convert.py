"""转换管线：.abr → Krita 预设（.kpp）与资源包（.bundle）。"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, PngImagePlugin

from . import __version__
from .abr import AbrFile
from .kpp import TextureXml, build_preset_xml, write_bundle, write_kpp
from .kpp.bundle import (
    BundleVerifyResult,
    bundle_content_hash8,
    bundle_content_hash8_of_file,
    verify_bundle,
)
from .kpp.kpp_writer import read_preset_xml, render_tip_preview
from .kpp.preset_xml import texture_brightness_contrast
from .hardness_tip import build_tip, verify_generated_tip
from .mapping import _TEXTURING_MODE, BrushPreset, TextureSettings, map_presets


def _png_bytes(gray: np.ndarray) -> bytes:
    """灰度蒙版（255=墨）→ PNG 字节（8bit L 通道）。"""
    buf = io.BytesIO()
    Image.fromarray(gray, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _square_tip(gray: np.ndarray) -> np.ndarray:
    """将采样笔尖居中填充到正方形画布，避免 Krita 非正方形尺寸显示偏差。"""
    if gray.ndim != 2:
        raise ValueError("笔尖蒙版必须是二维灰度数组")
    h, w = gray.shape
    side = max(h, w)
    if h == side and w == side:
        return gray
    # ABR/Krita 笔尖蒙版约定 255=白色笔迹（墨），0=透明；
    # 用白色填充是刻意的：Krita 对采样笔尖按 PNG 尺寸计算预设大小，
    # 透明边框会让「预设编辑器显示尺寸」与「实际绘图区大小」不一致。
    # 2026-08-26 与用户在 Krita 实测确认（白色边框 + 居中），不要改成 0。
    canvas = np.full((side, side), 255, dtype=gray.dtype)
    y = (side - h) // 2
    x = (side - w) // 2
    canvas[y:y + h, x:x + w] = gray
    return canvas


# ---------------------------------------------------------------------------
# 高度系纹理强度增益（v1.2.1）：PS 权重 12 vs Krita 权重 10，归一化后固定相差 2q。
# 高度系强度乘 12/10=1.2 再封顶 1.0（d10 实测 mean|Δ| 43.31→1.39、超阈 95.36%→0.00%）；
# 非高度系（multiply/subtract/hardMix）已 0.00%，一律不加增益（动了会引入 2q 正偏差）。
# 推导、实测与封顶 1.0 的结构性限制见 docs/parameter-mapping.md §7.1（第 8 条）。
_HEIGHT_BLEND_MODES = frozenset({
    "Hght", "height", "HghtPS",
    "linearHeight", "linearHeightPhotoshop", "linearHeightPS",
})

#: 高度系强度增益 = PS 权重 12 / Krita 权重 10；推导见 parameter-mapping §7.1。
HEIGHT_STRENGTH_GAIN = 1.2

#: 内嵌纹理 PNG 的 tEXt 元数据关键字（Krita 5.3.3 md5 去重坑的规避标记）。
_TEXTURE_TEXT_KEYWORD = "brushconverter"


def _texture_strength(depth: float, blend_mode: str) -> float:
    """PS 纹理深度（0..100）→ Krita Texture/Strength/Value。

    高度系（见 _HEIGHT_BLEND_MODES）先乘 HEIGHT_STRENGTH_GAIN 再封顶 1.0，
    用于抵消 PS W=12 vs Krita W=10 的权重差（实测数字见上方注释）；
    其余模式原样返回 depth/100，不做任何改动。
    """
    d = max(0.0, min(100.0, float(depth)))
    strength = d / 100.0
    if blend_mode in _HEIGHT_BLEND_MODES:
        return min(1.0, HEIGHT_STRENGTH_GAIN * strength)
    return strength


def _texture_strength_min(depth_min: float, blend_mode: str) -> float:
    """PS 压感→深度曲线最小值（textureDepthDynamics.Mnm）→ 低压力端**绝对强度**。

    与恒定路径同一套换算（含高度系 ×1.2 与封顶），保证曲线两端口径一致。
    v1.5.0（任务 135-5）起它只作为比值的分子：Krita 的强度是乘性的
    （strength = qBound(0, 1, Value × curve)），低压力端会被再乘一次 Value，
    所以曲线 0 端点必须写 s_m/s_d 而不是 s_m（见 _texture_xml）。
    """
    return _texture_strength(depth_min, blend_mode)


def _texture_png_array(img: np.ndarray) -> bytes:
    """蒙版数组 → 内嵌 PNG 字节（RGB 或灰度，像素数据逐字节不变）。

    保存时追加一个只含元数据的 tEXt 块（keyword=brushconverter，
    内容=sha256=<像素数组 sha256 hex>），像素数据一字不动。两个实测坑：
    1) md5 撞车：Krita 5.3.3 会按 md5 把内嵌图案与已安装图案库去重，内嵌纹理
       与库内文件同 md5 时该纹理解析失败 → 纹理被**静默禁用**（预设加载正常、
       Enabled=true、字段回读全对，但落笔 Alpha 恒 255，无任何报错）。追加元数据
       块让内嵌字节 md5 与源文件必然不同，实测即恢复正常（§8.2）。
    2) 文件名撞车（见 _texture_xml 的命名注释）：只改字节名不改文件名仍会串用
       库内同名图案，所以两件事必须一起做。
    为什么每次都加、而不是只在「撞车时」加：转换器不知道接收方 Krita 图案库里
    有什么文件，无法提前判断是否会撞；固定加标记才能保证产物可复现、
    「重名 ⇒ 同内容」，也便于事后用 tEXt 里的像素哈希核对同一纹理。
    v1.5.0（任务 135-1）：预缩放后的位图同样走这里，标记与内容哈希都按**新字节**派生。
    """
    if img is None:
        raise ValueError("纹理位图不能为空")
    pixels = np.ascontiguousarray(img)
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text(
        _TEXTURE_TEXT_KEYWORD,
        f"sha256={hashlib.sha256(pixels.tobytes()).hexdigest()}",
    )
    buf = io.BytesIO()
    if img.ndim == 3 and img.shape[2] == 3:
        Image.fromarray(img, mode="RGB").save(buf, format="PNG", pnginfo=png_info)
    else:
        Image.fromarray(img, mode="L").save(buf, format="PNG", pnginfo=png_info)
    return buf.getvalue()


def _texture_png(texture: TextureSettings) -> bytes:
    """纹理设置 → 内嵌 PNG 字节（既有回归用例沿用的入口，等价于 _texture_png_array）。"""
    img = texture.image
    if img is None:
        raise ValueError("纹理位图不能为空")
    return _texture_png_array(img)


def _prescale_texture_image(img: np.ndarray, scale_pct: float) -> np.ndarray | None:
    """PS 纹理缩放核：源对齐周期双线性预缩放到 round(边长 × s/100)（任务 135-1）。

    依据 128 实验 §2.2 / §4：PS 的重采样是「源坐标 = 目标 i × 输入/输出、无 0.5
    相位偏移、越界按图案周期回绕」，Krita 的 Qt 缩放是中心对齐 —— 两者差
    0.5(k-1) 画布 px 的相位，真实高频纹理上造成 90%+ 超阈像素。转换侧用 PS 同核
    预缩放后写 Texture/Pattern/Scale=1.0，Krita 不再重采样 ⇒ 24/24 = 0.00%/max≤1。
    本函数是 tools/exp_texture_scale.py::prescale_gray 的移植（约 20 行 numpy）：

    - 目标边长 = round(输入边长 × s/100)（高/宽各算各的，方纹理即 128 的口径）；
    - 目标像素 i 的源坐标 = i × in/out（**不加 0.5**，这是与 Qt 的关键差别）；
    - 负坐标/越界按图案尺寸取模回绕（等价「先重采样一片周期图案」）；
    - 双线性后 8-bit 四舍五入；RGB 逐通道同核（线性核与灰度换算可交换）。
    s≤100 不走这里（128 §6.1 的 <100% 核未测，保持原路径）。目标尺寸恰好等于
    输入尺寸时返回 None（调用方保持原路径，避免无意义地改字节）。
    """
    if img.ndim not in (2, 3):
        raise ValueError(f"纹理位图必须是二维灰度或三维 RGB，实际 shape={img.shape}")
    factor = float(scale_pct) / 100.0
    if factor <= 1.0:
        # s<=100 不预缩放：128 §6.1 的 <100% 核未测，保持原路径（由调用方判 scale>100）
        return None
    ih, iw = img.shape[:2]
    oh = max(1, int(round(ih * factor)))
    ow = max(1, int(round(iw * factor)))
    if (oh, ow) == (ih, iw):
        return None
    src_y = np.arange(oh, dtype=np.float64) * (ih / float(oh))
    src_x = np.arange(ow, dtype=np.float64) * (iw / float(ow))
    y0 = np.floor(src_y).astype(np.int64)
    x0 = np.floor(src_x).astype(np.int64)
    wy = (src_y - y0)[:, None]
    wx = (src_x - x0)[None, :]
    y0m, y1m = np.mod(y0, ih), np.mod(y0 + 1, ih)
    x0m, x1m = np.mod(x0, iw), np.mod(x0 + 1, iw)

    def _resample_plane(plane: np.ndarray) -> np.ndarray:
        p = plane.astype(np.float64)
        top = p[y0m][:, x0m] * (1.0 - wx) + p[y0m][:, x1m] * wx
        bot = p[y1m][:, x0m] * (1.0 - wx) + p[y1m][:, x1m] * wx
        return top * (1.0 - wy) + bot * wy

    if img.ndim == 2:
        out = _resample_plane(img)
    else:
        out = np.stack([_resample_plane(img[:, :, c]) for c in range(img.shape[2])],
                       axis=2)
    return np.clip(np.rint(out), 0.0, 255.0).astype(np.uint8)


def _tip_is_full_coverage(tip: np.ndarray | None) -> bool:
    """满覆盖笔尖判定（任务 135-2）：base≡1 ⇔ 笔尖位图**每个像素都是墨**。

    极性（130 §三实测，勿猜）：Krita 5.3.3 的 L 模式 png_brush「深=墨、白 255=透明」；
    转换链里 samples.py 的采样蒙版与 hardness_tip 生成的 stored 位图同为此约定
    （0=墨、255=透明），所以「满覆盖」= 位图全 0。
    128A §一 的 hard 档就是这种笔尖：PS 无纹理参考图 40000/40000 px 全 255（base≡1），
    常量偏置（Strength=1.0 + 亮度 ±1/255）在它上面 3/3 = 0.00%/max 0。
    只要存在软边/抗锯齿（1..254 的中间值）或透明区（255），就不算满覆盖：
    128A 实测这类 base<1 笔尖偏置后仍 10%–40% 超阈，属登记残差、不下发偏置。
    """
    if tip is None or tip.size == 0:
        return False
    return bool(np.all(tip == 0))


def _texture_xml(bp: BrushPreset, full_coverage: bool | None = None) -> TextureXml | None:
    """把 Photoshop 纹理参数映射为 Krita 纹理选项（无位图时返回 None）。

    映射目标是 Krita 模式合成后的最终效果；校准常数来自
    research/测试结果2 与 research/krita结果，而不是 Photoshop 中间纹理值。

    v1.2.1 两处修复（依据见 docs/parameter-mapping.md §7.1）：
    - 高度系强度 ×1.2（抵消 PS W=12 vs Krita W=10，见 _texture_strength()）；
    - 图案资源名由内嵌字节的 sha256 派生 + 内嵌 PNG 追加 tEXt 标记
      （Krita 5.3.3 的「同名串用」与「同 md5 静默禁用」两个静默坑）。

    v1.5.0（任务 135）本函数承载五条落地：
    - 135-1：scale>100 先用 PS 同核预缩放（_prescale_texture_image）再内嵌，
      写 Scale=1.0；s≤100 保持原路径 Scale=s/100；两处 Scale 截断全部解除。
    - 135-2：hardMix + 满覆盖笔尖（base≡1）+ 恒定深度 → Strength=1.0 且
      Brightness 偏置 ±1/255（符号随 Invert）；base<1 不下发，登记 10%–40% 残差。
    - 135-3：亮度越界区（C=-50 且 |PS 亮度|>127）只登记残差、不改运行时路径。
    - 135-4：对比度 k 原值直写（不再 min(2.0) 截断，见 preset_xml）。
    - 135-5：minimumDepth 的曲线 0 端点写比值 s_m/s_d（Krita 强度是乘性的）。
    """
    tex = bp.texture
    if tex is None or tex.image is None:
        return None
    if full_coverage is None:
        # 直接调用（既有回归用例）时按笔尖位图就地判定；生产路径由 _render_preset
        # 传入**实际内嵌**的那张位图（计算笔刷是 hardness_tip 生成的 stored）。
        full_coverage = _tip_is_full_coverage(bp.tip_gray)
    # --- 135-1：scale>100 → 预缩放内嵌 PNG + Scale=1.0（128 实测 24/24 达标）---
    scale_pct = float(tex.scale)
    image = tex.image
    preset_scale = scale_pct / 100.0
    if scale_pct > 100.0:
        prescaled = _prescale_texture_image(image, scale_pct)
        if prescaled is not None:
            image = prescaled
            preset_scale = 1.0
    # 图案资源名由「内嵌字节的 sha256」派生，而不是 PS 的 uuid：
    # Krita 5.3.3 遇到与图案库同名的资源时会**串用别人的图案**（实测：
    # tex_<uuid8>.png 与库内同名图案撞车，不同字节渲染出同一张错图，
    # 见实验-Krita侧-报告 §8.2）。用内容哈希命名可以保证「重名 ⇒ 同内容」，
    # 且同一输入两次转换得到同一名字与同一字节（确定性）。
    # 为什么不能只改名字：md5 去重坑仍然存在（同 md5 内嵌纹理被静默禁用），
    # 必须同时给内嵌字节加 tEXt 标记，见 _texture_png_array()。
    # 135-1 的预缩放字节同样走这条派生规则：哈希与资源名都按**新字节**变，
    # 绝不写死旧值（否则不同内容会共用同一个 tex_<sha12> 名）。
    png_bytes = _texture_png_array(image)
    filename = f"tex_{hashlib.sha256(png_bytes).hexdigest()[:12]}.png"
    # Krita 源码的 KisTextureMaskInfo 直接执行：
    #   maskValue -= brightness
    #   maskValue = ((maskValue - 0.5) * contrast) + 0.5
    # 亮度/对比度只有 calibrated 一条路径（v1.5.0 删除 legacy 开关，任务 135-6）；
    # 公式、推导与限制见 docs/parameter-mapping.md 第 7 节。
    brightness, contrast = texture_brightness_contrast(tex.brightness, tex.contrast)
    texturing_mode = _TEXTURING_MODE.get(tex.blend_mode, 0)
    strength = _texture_strength(tex.depth, tex.blend_mode)
    # --- 135-2：hardMix + 满覆盖 + 恒定深度：Strength=1.0 + 亮度 ±1/255 ---
    # 依据 128A §二：Krita hardMix 是 (src + dst*s > 1)，PS 在 base≡1 时对 8-bit
    # 边界用 >=（实测 PS alpha 全 255）；s=1 时阈值降到 0，±1/255 把严格大于补成
    # 等价于 >= ⇒ 3/3 = 0.00%/max 0（原路径 95.573%/52.377%/0.975%）。
    # 符号随 Invert：Krita 的反相在亮度之后（src=1-(m-b)），要抬高最终掩码
    # 1 个 8-bit LSB，Invert=true 时 b 取 +1/255、false 时取 -1/255。
    # 压感路径（Strength 随压力变）下偏置不再成立（阈值随 s 变），本轮不下发。
    if texturing_mode == 10 and full_coverage and not tex.pressure:
        strength = 1.0
        brightness = brightness + (1.0 / 255.0 if tex.invert else -1.0 / 255.0)
    strength_curve = None
    if tex.pressure:
        # --- 135-5：0 端点写比值 s_m/s_d（Krita 乘性：strength = Value × curve）---
        # 旧实现写绝对值 s_m，低压力端会被再乘一次 Value：d=50 差 2×、d=10 差 10×、
        # d=100 无误差。PS 侧作用点未量测（128A F-3），所以本轮只落比值、
        # **不写死乘/减模式的常数项**，也不宣称「已达标」。
        s_d = _texture_strength(tex.depth, tex.blend_mode)
        s_m = _texture_strength_min(tex.depth_min, tex.blend_mode)
        ratio = 1.0 if s_d <= 0.0 else min(1.0, max(0.0, s_m / s_d))
        strength_curve = f"0,{ratio:g};1,1;"
    return TextureXml(
        pattern_filename=filename,
        png_bytes=png_bytes,
        scale=preset_scale,
        brightness=brightness,
        contrast=contrast,
        invert=tex.invert,
        texturing_mode=texturing_mode,
        # Krita 文档明确指出：除 Height/Linear Height 外，Soft Texturing
        # 更接近 Photoshop；PS 专用 Height 模式本身不启用该优化。
        use_soft_texturing=(tex.blend_mode not in _HEIGHT_BLEND_MODES),
        # v1.2.1：高度系 ×1.2 且封顶 1.0；非高度系保持 depth/100 不动。
        # 135-2 的满覆盖 hardMix 例外（Strength 强制 1.0，见上）。
        strength=strength,
        strength_curve=strength_curve,
        strength_pressure=tex.pressure,
    )


def _safe_filename(name: str, index: int) -> str:
    """把中文名清洗为安全的 .kpp 文件名（前缀 3 位序号）。"""
    cleaned = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_")
    cleaned = cleaned or "brush"
    return f"{index:03d}_{cleaned}.kpp"


def _circle_mask(hardness: float, roundness: float,
                 size: int = 256) -> np.ndarray:
    """按硬度/圆度生成一个圆形灰度蒙版（预览/实验夹具用）。

    v1.5.0 起计算笔刷的预设与预览都改用 `hardness_tip.build_tip` 生成的 PS 剖面位图，
    本函数保留给 130 实验的路径 A 对照与既有回归用例（不是生产路径、也不是开关）。

    预览始终把笔尖铺满画布的 90%，与 PS 主直径无关：真实尺寸由预设里
    auto_brush 的 diameter 决定，缩略图只反映形状与边缘软硬。
    """
    s = size
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float64)
    cy = cx = (s - 1) / 2.0
    radius = (s - 1) / 2.0 * 0.9  # 占画布 90%
    # 圆度可以合法为 0（线状笔尖），除数必须夹住，否则 dist 变成 inf/nan
    ratio = max(roundness / 100.0, 1e-3)
    dx = (xx - cx) / radius
    dy = (yy - cy) / (radius * ratio)
    dist = np.sqrt(dx * dx + dy * dy)
    # 硬度 → 边缘过渡带占半径的比例（0.02..1）：硬度 100 = 几乎硬边，
    # 0 = 从中心线性衰减。注意这里不能用 (1-falloff) 之类的混合：
    # 硬笔刷的 falloff 远大于 1，会把整个圆盘压成一个小点。
    soft = max(0.02, (100.0 - hardness) / 100.0)
    mask = np.clip((1.0 - dist) / soft, 0.0, 1.0)
    return (mask * 255.0).astype(np.uint8)


@dataclass
class ConvertResult:
    kpp_files: list[tuple[str, bytes]] = field(default_factory=list)
    bundle_path: str | None = None
    out_dir: str = ""
    presets: list[BrushPreset] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    # --- v1.3.1 产物加固 ---
    content_hash8: str = ""            # 产物名的逻辑内容短哈希（只在有 bundle 时有值）
    texture_count: int = 0             # 含真实纹理的预设数（Enabled=true 且非 1x1）
    bundle_reused: bool = False        # 命中「已存在同内容产物」→ 未重复写入
    bundle_renamed: bool = False       # 目标同名但内容不同 → 自动加 _2/_3 后缀
    warnings: list[str] = field(default_factory=list)  # 需展示给用户的提示（防呆等）
    verify: BundleVerifyResult | None = None  # 写出后回读校验结论



# ---------------------------------------------------------------------------
# v1.3.1 产物加固：内容短哈希命名 / 覆盖防呆 / 回读校验 / 纹理计数
# 依据见 docs/developer-guide.md §5.4（v1.3.1 产物加固，使用者批准的 4 条）。
# ---------------------------------------------------------------------------


def _png_size(png: bytes) -> tuple[int, int]:
    """只读 PNG 的 IHDR 取宽高（不解码像素：3MB 纹理也能瞬间判尺寸）。"""
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("不是 PNG 字节（缺少 PNG 签名）")
    if len(png) < 24 or png[12:16] != b"IHDR":
        raise ValueError("PNG 缺少 IHDR 块")
    return struct.unpack(">II", png[16:24])


def kpp_has_real_texture(kpp_bytes: bytes) -> bool:
    """判定单个 .kpp 是否带「真实纹理」。

    口径（使用者批准）：Texture/Pattern/Enabled=true，且内嵌 patterns 资源
    不是 1x1 占位图。1x1 是 ABR 里「纹理参数开着但图案缺内容」的产物，
    Krita 加载后视觉上等于没有纹理，不能计入「含真实纹理」。
    """
    root = ET.fromstring(read_preset_xml(kpp_bytes))
    enabled = False
    for param in root.iter("param"):
        if param.get("name") == "Texture/Pattern/Enabled":
            enabled = (param.text or "").strip().lower() == "true"
            break
    if not enabled:
        return False
    for res in root.iter("resource"):
        if res.get("type") != "patterns":
            continue
        payload = (res.text or "").strip()
        if not payload:
            continue
        try:
            png = base64.b64decode(payload)
        except (ValueError, TypeError):
            continue
        try:
            width, height = _png_size(png)
        except ValueError:
            continue
        if width > 1 or height > 1:
            return True
    return False


def count_real_textures(kpp_files: list[tuple[str, bytes]]) -> int:
    """统计产物里「含真实纹理」的预设个数（GUI 摘要与回归测试共用同一口径）。"""
    return sum(1 for _, data in kpp_files if kpp_has_real_texture(data))


def _resolve_bundle_path(target: Path, content_hash8: str) -> tuple[Path, str]:
    """决定 .bundle 落盘路径，绝不静默覆盖。

    返回 (路径, 状态)，状态取 "new" / "reused" / "renamed"：
    - 目标不存在 → 直接写；
    - 目标已存在且逻辑内容哈希相同 → 复用（不重复写，避免白写几十 MB）；
    - 目标已存在但内容不同（旧产物 / 手工放的坏文件 / 哈希前缀撞车）
      → 依次试 名字_2、名字_3…，并对调用方发出「已重命名」警告。
    """
    if not target.exists():
        return target, "new"
    if bundle_content_hash8_of_file(target) == content_hash8:
        return target, "reused"
    for index in range(2, 1000):
        candidate = target.with_name(f"{target.stem}_{index}{target.suffix}")
        if not candidate.exists():
            return candidate, "renamed"
    raise OSError(f"同名产物过多，无法为 {target.name} 找到可写文件名")


def convert(abr_path: str, out_dir: str, make_bundle: bool = True,
            make_standalone: bool = True) -> ConvertResult:
    """CLI 便捷入口：解析单个 .abr 文件并转换全部预设。

    返回 ConvertResult；GUI 走 convert_presets() 以支持子集转换。
    """
    abr = AbrFile.parse(abr_path)
    presets = map_presets(abr)
    return convert_presets(abr, presets, out_dir, make_bundle=make_bundle,
                           make_standalone=make_standalone)


def _render_preset(bp: BrushPreset, index: int) -> tuple[str, str, np.ndarray]:
    """渲染单个预设 → (文件名, 预设 XML, 预览 RGBA)。"""
    if bp.tip_gray is None and not bp.is_computed:
        raise ValueError(f"{bp.name}: 无笔尖位图")
    # 135-2：满覆盖判定必须基于**实际内嵌**的那张笔尖位图（见 _tip_is_full_coverage）。
    full_coverage = False

    if bp.is_computed:
        # v1.5.0（任务 133，132 落地清单 133-1/133-2/133-3）：计算笔刷由 auto_brush
        # 改成「按 PS 硬度剖面生成的采样笔尖」。依据 130 实验：Krita 的 hfade/vfade 只
        # 作用在 1.00R 以内，复刻不了 PS「软边伸出标称半径」的 h0/h50（−15.60px/−19.81%、
        # +7.33px/+22.63%）；把剖面烘成位图、并让 Krita 尺寸字段 = PS 实际足印
        # （h0/50/100 → 1.45/1.25/1.02 × 标称直径）后，过渡带变成 −0.90px/−1.14%、
        # +0.04px/+0.12%。代价（发布说明必写）：fade/硬度在 Krita 里不可再调、
        # 尺寸显示值 = PS 足印（200 → 290/250/204）、每支笔多 3–12 KB 位图。
        generated = build_tip(bp.hardness, bp.diameter or 30.0, bp.roundness)
        # 自检（130 verify_tip 口径）：正方形 + 外圈整圈 255，失败即报错，
        # 绝不静默产出「极性写反/带透明边框」的位图（130 首轮 5 组作废的教训）。
        verify_generated_tip(generated)
        full_coverage = _tip_is_full_coverage(generated.stored)
        tip_png = _png_bytes(generated.stored)
        res_name = _safe_filename(bp.name, index).rsplit(".", 1)[0]
        # scale=1.0 → 预设尺寸字段 = 位图边长 = PS 足印；间距按「标称基准 / 位图边长」
        # 乘回（否则 h0 的步长会被放大到 1.45 倍，见 hardness_tip.spacing_basis）。
        brush_def = _sampled_brush_def(res_name, tip_png, bp,
                                       spacing_basis=generated.spacing_basis,
                                       scale=1.0)
        preview = render_tip_preview(generated.mask)
    else:
        assert bp.tip_gray is not None  # 非计算笔刷必有笔尖（函数开头已校验）
        # Krita 对非正方形 png_brush 的编辑器尺寸显示存在偏差；
        # 与映射时的 max(width,height) 基准保持一致，内嵌正方形笔尖。
        # 间距基准补偿在 _sampled_brush_def 里做（spacing × 短边/长边）。
        square_tip = _square_tip(bp.tip_gray)
        full_coverage = _tip_is_full_coverage(square_tip)
        tip_png = _png_bytes(square_tip)
        res_name = _safe_filename(bp.name, index).rsplit(".", 1)[0]
        brush_def = _sampled_brush_def(res_name, tip_png, bp)
        preview = render_tip_preview(bp.tip_gray)

    xml = build_preset_xml(
        name=bp.name,
        brush_definition=brush_def,
        tip_png=tip_png,
        tip_name=_safe_filename(bp.name, index).rsplit(".", 1)[0] if tip_png else None,
        size_curve=bp.size_curve,
        opacity_curve=bp.opacity_curve,
        flow_curve=bp.flow_curve,
        ratio_curve=bp.ratio_curve,
        rotation_sensor=bp.rotation_sensor,
        rotation_jitter=bp.rotation_jitter,
        scatter=bp.scatter,
        scatter_pressure=bp.scatter_pressure,
        scatter_both_axes=bp.scatter_both_axes,
        scatter_amount=bp.scatter_amount,
        texture=_texture_xml(bp, full_coverage=full_coverage),
    )
    return _safe_filename(bp.name, index), xml, preview


def convert_presets(abr: AbrFile, presets: list[BrushPreset], out_dir: str,
                    make_bundle: bool = True, make_standalone: bool = True) -> ConvertResult:
    """把给定预设子集转成 .kpp / .bundle。

    make_bundle / make_standalone 至少一个为 True，对应「.bundle」与「.kpp」两种产物；
    两者都为 False 时不写任何文件，必须显式报错而不是假装转换成功。
    """
    if not make_bundle and not make_standalone:
        raise ValueError("make_bundle 与 make_standalone 至少需要一个为 True")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    result = ConvertResult(out_dir=str(out), presets=presets)
    kpp_dir = out / "kpp"
    if make_standalone:
        kpp_dir.mkdir(parents=True, exist_ok=True)

    for i, bp in enumerate(presets):
        if bp.tip_gray is None and not bp.is_computed:
            result.skipped.append(f"{bp.name}: 无笔尖位图")
            continue
        fname, xml, preview = _render_preset(bp, i)
        result.kpp_files.append((fname, _build_kpp_bytes(xml, preview)))
        if make_standalone:
            write_kpp(str(kpp_dir / fname), xml, preview)

    result.texture_count = count_real_textures(result.kpp_files)

    if make_bundle and result.kpp_files:
        stem = getattr(abr, "name", None) or "converted"
        # v1.3.1：产物名带版本号 + 逻辑内容短哈希，让「同名 ⇒ 同内容」成立。
        # 版本号让不同版本的产物不会互相覆盖，hash8 让同内容可被识别复用。
        result.content_hash8 = bundle_content_hash8(result.kpp_files)
        base = out / f"{stem}_v{__version__}_{result.content_hash8}.bundle"
        target, state = _resolve_bundle_path(base, result.content_hash8)
        result.bundle_reused = state == "reused"
        if result.bundle_reused:
            # 同内容产物已存在：直接复用，绝不再写一遍（写盘会改 mtime，
            # 也会让「未重复写入」这句提示变成假话）。
            result.bundle_path = str(target)
        else:
            result.bundle_path = write_bundle(str(target), result.kpp_files)
        result.bundle_renamed = state == "renamed"
        if state == "reused":
            result.warnings.append("已存在同内容产物，未重复写入")
        elif state == "renamed":
            result.warnings.append(
                f"目标同名文件内容不同，已重命名为 {target.name}（原文件未覆盖）")
        # 旧版命名（stem.bundle）留在同目录时只提示，不覆盖也不删：
        # 「主要针对旧版 stem.bundle」的防呆落在这里最安全。
        legacy = out / f"{stem}.bundle"
        if legacy.exists():
            result.warnings.append(
                f"检测到旧版命名产物 {legacy.name}：v1.3.1 不覆盖旧文件，"
                f"新产物为 {target.name}")
        # 写出后回读校验（verify_bundle 不抛异常，失败结论进 result.verify）
        result.verify = verify_bundle(result.bundle_path)
    return result


def _sampled_brush_def(res_name: str, tip_png: bytes, bp: BrushPreset,
                        spacing_basis: float | None = None,
                        scale: float | None = None) -> str:
    """生成采样笔尖的 png_brush <Brush> 定义（filename + md5sum 引用内嵌 PNG）。

    spacing 在这里乘「间距基准补偿」：Krita 的 png_brush 尺寸/间距基准是内嵌位图的
    边长，而 PS 的基准是笔尖蒙版的**短边**。两类笔刷的补偿系数不同：

    - 采样笔尖（ABR 自带位图）：用 `bp.spacing_basis` = 原始笔尖短边/长边（任务 107）。
      补白把 Krita 的基准由短边换成长边，任务 106 §4 实测 Krita/PS 步长比 ≈ 长宽比
      （非方形 1.253/1.281/1.325，长宽比 1.280；方形 1.013–1.020）。严格正方形比值为 1
      → 输出与改动前逐字节一致；近方形按实际短/长边微调（如 84/86 = 0.977）。
    - 计算笔刷（v1.5.0 任务 133）：由调用方传 `spacing_basis` = Dmtr×短/长 ÷ 位图边长
      （`hardness_tip.spacing_basis`）。PS 对计算笔刷的 100% 间距基准是标称直径
      （131 表 3：b_PS=200.00=Dmtr），改采样后 Krita 基准变成 PS 足印（h0 是 1.45×Dmtr）。

    scale=1.0 时预设尺寸字段 = 位图边长（计算笔刷路径显式传 1.0，让尺寸恰好等于足印）。
    """
    from .kpp.preset_xml import sampled_brush_definition
    md5 = hashlib.md5(tip_png).hexdigest()
    basis = bp.spacing_basis if spacing_basis is None else spacing_basis
    basis = basis if basis else 1.0
    scale = bp.scale if scale is None else scale
    return sampled_brush_definition(res_name + ".png", md5, bp.spacing * basis,
                                    bp.angle, scale)


def _computed_brush_def(bp: BrushPreset) -> str:
    """生成计算笔刷的 auto_brush <Brush> 定义（程序化圆形笔尖）。

    **v1.5.0 起生产路径不再使用**：计算笔刷改由 `hardness_tip.build_tip` 生成采样笔尖
    （任务 133，130 实验判「采样等价路线可行」）。本函数保留给两处不改的调用方——
    130 实验复跑（`tools/exp_hardness_profile.py` 的路径 A 对照）与 107 的回归用例
    （`tests/test_param_calibration_v140.py`）——不是功能开关，也不能被重新接回生产。
    """
    from .kpp.preset_xml import auto_brush_definition
    return auto_brush_definition(bp.diameter or 30.0, bp.spacing, bp.angle,
                                 bp.roundness, bp.hardness)


def _build_kpp_bytes(xml: str, preview_rgba: np.ndarray) -> bytes:
    """把预设 XML + 预览 RGBA 编码为完整 .kpp 字节。"""
    from .kpp.kpp_writer import build_kpp
    return build_kpp(xml, preview_rgba)
