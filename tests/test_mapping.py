"""阶段3 参数映射的回归测试。

对照用户手动转换的 Krita 参考预设（勾线笔）校准的关键值。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.mapping import map_presets  # noqa: E402

# 测试样本为商用笔刷素材（42MB），不纳入版本库。样本缺失时整体跳过，
# 需跑完整断言时把 .abr 放到 tests/ 目录即可。
ABR = Path(__file__).parent / "海怪笔刷-详情展示的勾线笔套装组18支.abr"

pytestmark = pytest.mark.skipif(
    not ABR.exists(),
    reason="缺少测试样本 .abr（请自备到 tests/ 目录）",
)


def _presets():
    abr = AbrFile.parse(ABR)
    return map_presets(abr)


def test_preset_count():
    # 源文件 20 个预设，去掉 1 个完全重复的「淘宝店」横幅 → 19
    assert len(_presets()) == 19


def test_dedup_banner():
    names = [p.name for p in _presets()]
    assert names.count("淘宝店：大怪兽素材噗收集整理") == 1


def test_scatter_mapping():
    presets = _presets()
    line = next(p for p in presets if "大怪兽-勾线笔" in p.name)
    # 勾线笔 useScatter=True，散布量随压感(bVTy=2)
    assert line.scatter is True
    assert line.scatter_pressure is True
    assert line.scatter_both_axes is False

    # 气泡 useScatter=True 但 bVTy=0（散布不随压感），仍应启用
    bubble = next(p for p in presets if "气泡" in p.name)
    assert bubble.scatter is True
    assert bubble.scatter_pressure is False
    assert bubble.scatter_both_axes is True


def test_scatter_amount():
    presets = _presets()
    # 气泡：scatterDynamics.jitter=145%（顶层 Spcn 是间距，不是散布量）
    bubble = next(p for p in presets if "气泡" in p.name)
    assert bubble.scatter_amount == 145.0
    # 蝴蝶：jitter=85%
    butterfly = next(p for p in presets if "蝴蝶" in p.name)
    assert butterfly.scatter_amount == 85.0
    # 勾线笔：jitter=0（散布启用但量为 0）
    line = next(p for p in presets if "大怪兽-勾线笔" in p.name)
    assert line.scatter_amount == 0.0


def test_line_pen_scale():
    # 大怪兽-勾线笔【用数位板】(index 2) 共用笔尖 87465e27，Dmtr=26
    presets = _presets()
    line = next(p for p in presets if "大怪兽-勾线笔" in p.name)
    assert line.diameter == 26.0
    # 笔尖原图 1895×1901，正方形画布基准取较大边 → scale = 26/1901
    assert abs(line.scale - 26.0 / 1901.0) < 1e-9


def test_line_pen_dynamics():
    presets = _presets()
    line = next(p for p in presets if "大怪兽-勾线笔" in p.name)
    # 流量 min 17%，不透明度 min 80%，宽高比 min 30%
    assert line.flow_curve == "0,0.17;1,1;"
    assert line.opacity_curve == "0,0.8;1,1;"
    assert line.ratio_curve == "0,0.3;1,1;"
    # 旋转随笔迹方向，散布由压感控制
    assert line.rotation_sensor == "drawingangle"
    assert line.rotation_jitter == 6.0
    assert line.scatter is True


def test_computed_brush():
    presets = _presets()
    comp = next(p for p in presets if "圆头虚" in p.name)
    assert comp.is_computed is True
    assert comp.tip_gray is None
    assert comp.diameter == 25.0
    assert comp.hardness == 84.0


def test_auto_brush_angle_radians():
    # 角度：Photoshop Angl 是度，Krita XML 存弧度；负角度 normalize 到 [0,360) 再转弧度
    from brush_converter.kpp.preset_xml import auto_brush_definition
    import math
    xml = auto_brush_definition(25.0, 0.05, -33.0, 100.0, 84.0)
    # -33° → 327° → 弧度
    assert f'angle="{math.radians(-33.0 % 360.0):g}"' in xml


def test_auto_brush_hardness_fade():
    # 硬度 84% → fade 0.84（fade=实心区占比，与硬度同向，不是 (100-h)/100）
    from brush_converter.kpp.preset_xml import auto_brush_definition
    xml = auto_brush_definition(25.0, 0.05, 0.0, 100.0, 84.0)
    assert 'hfade="0.84"' in xml
    assert 'vfade="0.84"' in xml
    # 边界：硬度 100% → 1.0，硬度 0% → 0.0
    assert 'hfade="1"' in auto_brush_definition(25.0, 0.05, 0.0, 100.0, 100.0)
    assert 'hfade="0"' in auto_brush_definition(25.0, 0.05, 0.0, 100.0, 0.0)


def test_sampled_brush_angle_radians():
    # 采样笔刷的 angle 同样要度→弧度 + normalize
    from brush_converter.kpp.preset_xml import sampled_brush_definition
    import math
    xml = sampled_brush_definition("x.png", "abc123", 0.25, -33.0, 1.0)
    assert f'angle="{math.radians(-33.0 % 360.0):g}"' in xml


def test_rotation_jitter_uses_effect_strength_and_180_degree_curves():
    from brush_converter.kpp.preset_xml import build_preset_xml
    xml = build_preset_xml("rotation", "", None, rotation_sensor="drawingangle", rotation_jitter=12.0)
    assert 'RotationValue" type="internal">0.12' in xml
    assert 'RotationUseCurve" type="internal">true' in xml
    # Krita UI 显示 ±180°，但 XML 的曲线坐标是归一化 0..1。
    assert '<ChildSensor id="fuzzy"> <curve>0,0;1,1;</curve>' in xml
    assert '<ChildSensor id="fuzzystroke"> <curve>0,0;1,1;</curve>' in xml


def test_sampled_tip_is_padded_to_square_for_krita():
    import numpy as np
    from brush_converter.convert import _square_tip
    tip = np.full((3, 5), 7, dtype=np.uint8)
    padded = _square_tip(tip)
    assert padded.shape == (5, 5)
    assert np.array_equal(padded[1:4, :], tip)
    assert np.all(padded[0] == 255)
    assert np.all(padded[4] == 255)


def test_generated_sampled_brush_resource_is_square():
    import base64
    import re
    from io import BytesIO
    from PIL import Image
    from brush_converter.convert import _render_preset
    presets = _presets()
    pencil = next(p for p in presets if "大怪兽-软铅" in p.name)
    _, xml, _ = _render_preset(pencil, 0)
    encoded = re.search(r'type="brushes"[^>]*><!\[CDATA\[(.*?)\]\]>', xml, re.S).group(1)
    with Image.open(BytesIO(base64.b64decode(encoded))) as image:
        assert image.width == image.height



def test_texture_mapping():
    presets = _presets()
    textured = [p for p in presets if p.texture is not None]
    # 源文件 9 支笔刷开启纹理（含 1×1 中性纹理的勾线笔×2）
    assert len(textured) == 9

    line = next(p for p in presets if "大怪兽-勾线笔" in p.name)
    t = line.texture
    assert t is not None
    assert t.uuid == "438c2948-d232-11e5-b988-9ff33e1af9cd"
    assert t.name == "R. Melentyev's Art Texture"
    assert t.scale == 50.0
    assert t.depth == 55.0
    assert t.pressure is True
    assert t.invert is False
    assert t.blend_mode == "linearHeight"
    assert t.image is not None and t.image.shape == (1, 1, 3)

    pen = next(p for p in presets if "针管笔" in p.name)
    t2 = pen.texture
    assert t2 is not None
    assert t2.uuid == "69d92381-cf86-a54b-9d04-7f0fc2d9345b"
    assert t2.scale == 73.0
    assert t2.depth == 5.0
    assert t2.invert is True
    assert t2.brightness == -21
    assert t2.contrast == -50
    assert t2.image is not None and t2.image.shape == (1920, 1920, 3)


def test_new_texture_sample_modes():
    sample = Path(__file__).parent / "测试用.abr"
    if not sample.exists():
        pytest.skip("缺少测试用.abr")
    presets = map_presets(AbrFile.parse(sample))
    assert len(presets) == 6
    assert {p.texture.blend_mode for p in presets if p.texture} == {"Hght", "Sbtr", "hardMix"}
    assert all(p.texture is not None for p in presets)
    hard_mix = next(p for p in presets if p.texture and p.texture.blend_mode == "hardMix")
    assert hard_mix.texture is not None
    assert hard_mix.texture.blend_mode == "hardMix"
    _, hard_xml, _ = __import__("brush_converter.convert", fromlist=["_render_preset"])._render_preset(hard_mix, 0)
    assert "Texture/Pattern/TexturingMode\" type=\"internal\">10" in hard_xml

    height = next(p for p in presets if p.texture and p.texture.blend_mode == "Hght")
    _, height_xml, _ = __import__("brush_converter.convert", fromlist=["_render_preset"])._render_preset(height, 0)
    assert "Texture/Pattern/TexturingMode\" type=\"internal\">14" in height_xml


def test_texture_warnings_removed():
    # 纹理主体已映射：不再出现「纹理」警告；只保留其他未映射项
    presets = _presets()
    textured = [p for p in presets if p.texture is not None]
    assert textured
    for p in textured:
        assert not any("纹理" in w for w in p.warnings)


def test_texture_xml_end_to_end():
    # 转换管线端到端：勾线笔的 XML 应含 patterns 资源与纹理参数
    from brush_converter.convert import _render_preset
    presets = _presets()
    line = next(p for p in presets if "大怪兽-勾线笔" in p.name)
    _, xml, _ = _render_preset(line, 0)
    assert 'type="patterns"' in xml
    assert "tex_438c2948.png" in xml
    assert "Texture/Pattern/Enabled\" type=\"internal\">true" in xml
    assert "Texture/Pattern/TexturingMode\" type=\"internal\">15" in xml
    assert "Texture/Pattern/Brightness\" type=\"internal\">0" in xml
    assert "Texture/Pattern/Contrast\" type=\"internal\">1" in xml
    assert "Texture/Strength/Value\" type=\"internal\">0.55" in xml
    assert "PressureTexture/Strength/\" type=\"internal\">true" in xml

