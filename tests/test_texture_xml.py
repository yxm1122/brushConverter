"""Krita 纹理参数与内嵌资源生成测试（无需真实样本）。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from brush_converter.convert import _texture_xml  # noqa: E402
from brush_converter.kpp.preset_xml import (  # noqa: E402
    DEFAULT_PARAMS,
    TextureXml,
    build_preset_xml,
)
from brush_converter.mapping import BrushPreset, TextureSettings  # noqa: E402


def _xml(texture: TextureXml | None = None) -> str:
    return build_preset_xml(
        name="测试纹理",
        brush_definition='<Brush type="auto_brush" spacing="0.1"/>',
        tip_png=None,
        texture=texture,
    )


def test_texture_params_written():
    tex = TextureXml(
        pattern_filename="tex_abc12345.png",
        png_bytes=b"fake-png-bytes",
        scale=0.5,
        brightness=-21,
        contrast=-49,
        invert=True,
        texturing_mode=4,
        strength=0.55,
        strength_curve="0,0;1,1;",
        strength_pressure=True,
    )
    xml = _xml(tex)
    md5 = hashlib.md5(b"fake-png-bytes").hexdigest()
    assert 'type="patterns"' in xml
    assert "tex_abc12345.png" in xml
    assert f"PatternMD5Sum\" type=\"string\"><![CDATA[{md5}]]>" in xml
    assert "Texture/Pattern/Enabled\" type=\"internal\">true" in xml
    assert "Texture/Pattern/Scale\" type=\"internal\">0.5" in xml
    assert "Texture/Pattern/Brightness\" type=\"internal\">-21" in xml
    assert "Texture/Pattern/Contrast\" type=\"internal\">-49" in xml
    assert "Texture/Pattern/Invert\" type=\"internal\">true" in xml
    assert "Texture/Pattern/TexturingMode\" type=\"internal\">4" in xml
    assert "Texture/Strength/Value\" type=\"internal\">0.55" in xml
    assert "Texture/Strength/commonCurve\" type=\"string\"><![CDATA[0,0;1,1;]]>" in xml
    assert "PressureTexture/Strength/\" type=\"internal\">true" in xml


def test_texture_disabled_by_default():
    xml = _xml(None)
    assert 'type="patterns"' not in xml
    assert "Texture/Pattern/Enabled\" type=\"internal\">false" in xml
    assert DEFAULT_PARAMS["Texture/Pattern/Enabled"] == ("internal", "false")
    assert DEFAULT_PARAMS["Texture/Pattern/TexturingMode"] == ("internal", "0")


def test_convert_texture_xml_formulas():
    # 亮/对比度/缩放换算 + 钳位；未知混合模式回退 Multiply(0)
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    ts = TextureSettings(
        name="x",
        uuid="12345678-0000-0000-0000-000000000000",
        scale=2000.0,   # 2000% → clamp 10.0
        invert=True,
        brightness=-100,
        contrast=100,
        depth=55.0,
        depth_min=0.0,
        pressure=True,
        blend_mode="Ovld",  # 不在映射表 → 0
        image=img,
    )
    tex = _texture_xml(BrushPreset(name="x", texture=ts))
    assert tex is not None
    assert tex.scale == 10.0
    assert tex.brightness == -255
    assert tex.contrast == 255
    assert tex.texturing_mode == 0
    assert abs(tex.strength - 0.55) < 1e-9
    assert tex.strength_pressure is True
    assert tex.strength_curve == "0,0;1,1;"


def test_convert_texture_filename_from_uuid():
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    ts = TextureSettings(name="x", uuid="66e2987f-d47b-fa49-9e36-3ffa8b78533a",
                         image=img)
    tex = _texture_xml(BrushPreset(name="x", texture=ts))
    assert tex is not None
    assert tex.pattern_filename == "tex_66e2987f.png"
