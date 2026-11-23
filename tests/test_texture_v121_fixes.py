"""v1.2.1 两处修复的回归测试（挂 verify 标记）。

修复 1：高度系（Hght / height / HghtPS / linearHeight / linearHeightPhotoshop /
        linearHeightPS）纹理强度 = min(1.0, 1.2 × depth/100)，非高度系保持 depth/100。
        依据 docs/验收报告/实验-归因-报告.md §4.2/§4.3 与
        docs/验收报告/实验-Krita侧-报告.md §17（2026-11-19 fixA 实测：PS 高度系
        权重 W=12 vs Krita W=10，d10/s100 mean|Δ| 43.31 → 1.39、max 52 → 2、
        超阈像素 95.36% → 0.00%）。

修复 2：内嵌纹理 PNG 追加只含元数据的 tEXt 块（keyword=brushconverter，
        内容=sha256=<像素数组 sha256>），像素逐字节不变；资源名改为由内嵌字节
        的 sha256 前 12 位派生（tex_<hex12>.png）。
        依据 docs/验收报告/实验-Krita侧-报告.md §8.2（Krita 5.3.3 两个静默坑：
        内嵌纹理与图案库同 md5 → 纹理被静默禁用（Alpha 恒 255）；
        资源名与库内图案同名 → 串用别人的图案）。
"""

from __future__ import annotations

import hashlib
import io
import re
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from brush_converter.convert import (  # noqa: E402
    HEIGHT_STRENGTH_GAIN,
    _render_preset,
    _texture_png,
    _texture_strength,
    _texture_strength_min,
    _texture_xml,
)
from brush_converter.kpp.preset_xml import build_preset_xml  # noqa: E402
from brush_converter.mapping import BrushPreset, TextureSettings  # noqa: E402

pytestmark = pytest.mark.verify

# 高度系全部 6 个别名（PS 4CC 与描述符成员名都会出现）
HEIGHT_MODES = [
    "Hght", "height", "HghtPS",
    "linearHeight", "linearHeightPhotoshop", "linearHeightPS",
]
# 非高度系抽查：multiply（"Mul "）与 subtract（"Sbtr"）；hardMix 由既有用例覆盖
NON_HEIGHT_MODES = ["Mul ", "Sbtr"]

# depth → 实际写进 XML 的 Texture/Strength/Value 文本（逐值断言，不用容差放宽）
DEPTHS = [0, 10, 60, 83, 100]
EXPECTED_HEIGHT = {0: "0", 10: "0.12", 60: "0.72", 83: "0.996", 100: "1"}
EXPECTED_NON_HEIGHT = {0: "0", 10: "0.1", 60: "0.6", 83: "0.83", 100: "1"}


def _image_rgb(seed: int = 7, size: int = 8) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(size, size, 3), dtype=np.uint8)


def _texture(depth: float = 10.0, blend_mode: str = "linearHeight",
             depth_min: float = 0.0, pressure: bool = False,
             image: np.ndarray | None = None) -> TextureSettings:
    return TextureSettings(
        name="测试纹理",
        uuid="66e2987f-d47b-fa49-9e36-3ffa8b78533a",
        depth=float(depth),
        depth_min=float(depth_min),
        pressure=pressure,
        blend_mode=blend_mode,
        image=_image_rgb() if image is None else image,
    )


def _texture_xml_of(texture: TextureSettings):
    tex = _texture_xml(BrushPreset(name="测试", texture=texture))
    assert tex is not None, "带位图的纹理必须产出 TextureXml"
    return tex


def _xml_of(texture: TextureSettings) -> str:
    """把纹理组装成真实预设 XML（走生产同一个 build_preset_xml）。"""
    return build_preset_xml(
        name="测试", brush_definition='<Brush type="auto_brush" spacing="0.1"/>',
        tip_png=None, texture=_texture_xml_of(texture),
    )


def _param(xml: str, name: str) -> str:
    """取 XML 里某个 <param> 的原始内容（internal 转义值或 string 的 CDATA 原文）。"""
    m = re.search(
        r'<param name="' + re.escape(name) + r'" type="(?:internal|string)">'
        r"(.*?)</param>",
        xml,
        re.S,
    )
    assert m is not None, f"XML 里缺少参数 {name}"
    return m.group(1)


def _param_value(xml: str, name: str) -> str:
    """取参数值：string 类型剥掉 CDATA 包裹。"""
    raw = _param(xml, name)
    if raw.startswith("<![CDATA[") and raw.endswith("]]>"):
        return raw[len("<![CDATA["):-len("]]>")]
    return raw


# ---------------------------------------------------------------------------
# 1) 恒定强度：高度系 ×1.2 封顶 1，非高度系 = depth/100（逐值断言 XML 实际值）
# ---------------------------------------------------------------------------


def test_height_strength_gain_constant_is_1_2():
    """常数集中定义（禁止魔法数散落），且数值就是 PS W=12 / Krita W=10。"""
    assert HEIGHT_STRENGTH_GAIN == 1.2
    assert _texture_strength(10.0, "linearHeight") == pytest.approx(0.12)
    assert _texture_strength(10.0, "Mul ") == pytest.approx(0.10)
    assert _texture_strength_min(10.0, "linearHeight") == pytest.approx(0.12)
    assert _texture_strength_min(10.0, "Sbtr") == pytest.approx(0.10)


@pytest.mark.parametrize("blend_mode", HEIGHT_MODES)
@pytest.mark.parametrize("depth", DEPTHS)
def test_height_modes_strength_x1_2_with_cap(blend_mode, depth):
    """高度系：min(1, 1.2×depth/100)，d83 未封顶（0.996）、d100 封顶 1。"""
    tex = _texture_xml_of(_texture(depth=depth, blend_mode=blend_mode))
    assert tex.strength == pytest.approx(min(1.0, 1.2 * depth / 100.0), abs=1e-12)
    xml = _xml_of(_texture(depth=depth, blend_mode=blend_mode))
    assert _param_value(xml, "Texture/Strength/Value") == EXPECTED_HEIGHT[depth]


@pytest.mark.parametrize("blend_mode", NON_HEIGHT_MODES)
@pytest.mark.parametrize("depth", DEPTHS)
def test_non_height_modes_strength_unchanged(blend_mode, depth):
    """非高度系：必须仍是 depth/100（multiply 实测已 0.00%，不许被 ×1.2 污染）。"""
    tex = _texture_xml_of(_texture(depth=depth, blend_mode=blend_mode))
    assert tex.strength == pytest.approx(depth / 100.0, abs=1e-12)
    xml = _xml_of(_texture(depth=depth, blend_mode=blend_mode))
    assert _param_value(xml, "Texture/Strength/Value") == EXPECTED_NON_HEIGHT[depth]





# ---------------------------------------------------------------------------
# 2) 压感曲线路径：Texture/Strength/commonCurve 的 0 端点（v1.5.0 任务 135-5 改比值）
#
# 旧基准错在哪：v1.2.1 的这组用例要求 0 端点写绝对强度 s_m（如 depth=10、
# Mnm=10 → 0.12）。Krita 的强度是乘性的（strength = qBound(0,1, Value×curve)），
# 低压力端会被再乘一次 Value：d=50 差 2×、d=10 差 10×、d=100 才无误差。
# 任务 135-5 按 126 C-9 改为写比值 s_m/s_d；旧断言把已知错误语义钉成基准，故改写。
# 高度系 ×1.2 在比值里约掉（s_m/s_d = depth_min/depth），所以高度系与非高度系
# 在同深度下得到同一比值。PS 侧作用点未量测（128A F-3）⇒ 只落比值、不写常数项。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("blend_mode", "ps_depth", "depth_min", "expected_curve", "expected_value"),
    [
        ("linearHeight", 50, 10, "0,0.2;1,1;", "0.6"),   # s_m/s_d = 0.12/0.6
        ("Hght", 50, 10, "0,0.2;1,1;", "0.6"),
        ("linearHeightPS", 10, 10, "0,1;1,1;", "0.12"),  # 端点相等 → 全开
        ("linearHeight", 100, 100, "0,1;1,1;", "1"),
        ("Mul ", 50, 10, "0,0.2;1,1;", "0.5"),
        ("Sbtr", 50, 10, "0,0.2;1,1;", "0.5"),
        ("Sbtr", 10, 10, "0,1;1,1;", "0.1"),
        ("Mul ", 50, 100, "0,1;1,1;", "0.5"),           # s_m>s_d → 夹到曲线域 1
        ("Mul ", 0, 10, "0,1;1,1;", "0"),                # s_d=0：Value=0，端点取 1
    ],
)
def test_pressure_curve_endpoints_write_ratio(
        blend_mode, ps_depth, depth_min, expected_curve, expected_value):
    """压感路径的曲线 0 端点 = s_m/s_d（Krita 乘性语义；126 C-9 / 135-5）。"""
    texture = _texture(depth=float(ps_depth), blend_mode=blend_mode,
                       depth_min=float(depth_min), pressure=True)
    tex = _texture_xml_of(texture)
    assert tex.strength_curve == expected_curve
    xml = _xml_of(texture)
    assert _param_value(xml, "Texture/Strength/commonCurve") == expected_curve
    assert _param_value(xml, "Texture/Strength/UseCurve") == "true"
    assert _param_value(xml, "Texture/Strength/Value") == expected_value


def test_pressure_curve_capped_at_one_for_height():
    """高度系曲线 0 端点同样受曲线域 1.0 上限约束（比值 >=1 时夹到 1，不会写出 1.2）。"""
    tex = _texture_xml_of(_texture(depth=100.0, depth_min=100.0,
                                   pressure=True, blend_mode="linearHeight"))
    assert tex.strength_curve == "0,1;1,1;"
    assert "0,1;1,1;" in _xml_of(_texture(depth=100.0, depth_min=100.0,
                                          pressure=True, blend_mode="linearHeight"))





# ---------------------------------------------------------------------------
# 3) 内嵌 PNG：像素逐字节不变 + tEXt 标记 + md5 与源文件不同（防撞车证据）
# ---------------------------------------------------------------------------


def _png_chunks(png: bytes):
    """按 PNG 规范拆块（签名 + 长度/类型/数据/CRC）。"""
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "不是合法 PNG"
    pos = 8
    while pos < len(png):
        length = int.from_bytes(png[pos:pos + 4], "big")
        ctype = png[pos + 4:pos + 8]
        data = png[pos + 8:pos + 8 + length]
        yield ctype, data
        pos += 12 + length


def _text_chunks(png: bytes) -> dict[str, str]:
    """解析全部 tEXt 块 → {keyword: text}。"""
    out: dict[str, str] = {}
    for ctype, data in _png_chunks(png):
        if ctype == b"tEXt":
            keyword, _, text = data.partition(b"\x00")
            out[keyword.decode("latin-1")] = text.decode("latin-1")
    return out


def _plain_png(image: np.ndarray) -> bytes:
    """按旧实现方式（无任何元数据）编码源 PNG，作为「源 PNG 文件」字节。"""
    buf = io.BytesIO()
    Image.fromarray(image, mode="RGB" if image.ndim == 3 else "L").save(
        buf, format="PNG")
    return buf.getvalue()


def _pixel_sha(image: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


@pytest.mark.parametrize("shape", ["rgb", "gray"])
def test_embedded_png_pixels_byte_identical_and_marked(shape, tmp_path):
    """① 解码像素与源数组逐像素一致；② 带 brushconverter tEXt 且值为像素 sha256。"""
    if shape == "rgb":
        image = _image_rgb()
    else:
        image = np.arange(64, dtype=np.uint8).reshape(8, 8)
    embedded = _texture_png(TextureSettings(name="x", uuid="66e2987f",
                                            image=image))
    decoded = np.asarray(Image.open(io.BytesIO(embedded)))
    assert decoded.shape == image.shape
    assert np.array_equal(decoded, image), "内嵌 PNG 的像素数据被改动了"

    texts = _text_chunks(embedded)
    assert "brushconverter" in texts, "缺少 keyword=brushconverter 的 tEXt 块"
    assert texts["brushconverter"] == f"sha256={_pixel_sha(image)}"

    # 元数据块只加在 IEND 前，像素块逐字节等于无元数据的源 PNG 的 IDAT
    source_idat = [d for t, d in _png_chunks(_plain_png(image)) if t == b"IDAT"]
    embedded_idat = [d for t, d in _png_chunks(embedded) if t == b"IDAT"]
    assert embedded_idat == source_idat, "像素压缩数据（IDAT）必须逐字节不变"


def test_embedded_png_md5_differs_from_source_file(tmp_path):
    """③ 内嵌字节 md5 ≠ 源 PNG 文件 md5：这是避开 Krita「同 md5 静默禁用」的直接证据。"""
    image = _image_rgb(seed=11)
    embedded = _texture_png(TextureSettings(name="x", uuid="66e2987f",
                                            image=image))
    source_path = tmp_path / "源纹理.png"
    source_path.write_bytes(_plain_png(image))
    source_md5 = hashlib.md5(source_path.read_bytes()).hexdigest()
    embedded_md5 = hashlib.md5(embedded).hexdigest()
    assert embedded_md5 != source_md5


# ---------------------------------------------------------------------------
# 4) 资源名：内容哈希形状 / 确定性 / 不同内容不同名
# ---------------------------------------------------------------------------


def test_resource_name_matches_content_hash_shape():
    tex = _texture_xml_of(_texture(image=_image_rgb(seed=3)))
    assert re.fullmatch(r"tex_[0-9a-f]{12}\.png", tex.pattern_filename)
    assert tex.pattern_filename == (
        f"tex_{hashlib.sha256(tex.png_bytes).hexdigest()[:12]}.png")
    # XML 里的 Texture/Pattern/Name 与 PatternFileName 必须同名
    xml = _xml_of(_texture(image=_image_rgb(seed=3)))
    assert _param_value(xml, "Texture/Pattern/PatternFileName") == tex.pattern_filename
    assert _param_value(xml, "Texture/Pattern/Name") == tex.pattern_filename


def test_same_input_same_name_and_bytes_different_input_different_name():
    """同输入两次转换名字/字节完全相同；不同纹理必须得到不同名字。"""
    image_a = _image_rgb(seed=5)
    first = _texture_xml_of(_texture(image=image_a))
    second = _texture_xml_of(_texture(image=image_a))
    assert first.pattern_filename == second.pattern_filename
    assert first.png_bytes == second.png_bytes

    other = _texture_xml_of(_texture(image=_image_rgb(seed=6)))
    assert other.pattern_filename != first.pattern_filename
    assert other.png_bytes != first.png_bytes


def test_full_preset_conversion_is_deterministic():
    """整支预设连转两次：文件名与 XML（含 base64 资源）完全一致。"""
    preset = BrushPreset(
        name="确定性笔", spacing=0.2,
        tip_gray=np.full((8, 8), 255, dtype=np.uint8),
        texture=_texture(depth=10.0, blend_mode="linearHeight"),
    )
    file1, xml1, _ = _render_preset(preset, 0)
    file2, xml2, _ = _render_preset(preset, 0)
    assert file1 == file2
    assert xml1 == xml2
    # 资源名在整支预设里就是内容哈希名，且与 PatternFileName 一致
    name = _param_value(xml1, "Texture/Pattern/PatternFileName")
    assert re.fullmatch(r"tex_[0-9a-f]{12}\.png", name)
    assert f'filename="{name}"' in xml1
