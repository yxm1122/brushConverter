# -*- coding: utf-8 -*-
"""任务168：可编辑转换分支（三项开关的受控替代路径）定向测试。

验收目标（逐条对应任务书）：
1. 开启且命中时沿用现行实现；默认（未传选项状态）的产物与本轮开工基线
   逐字节一致，默认未命中不发生无意义烘焙/预缩放。
2. 计算笔刷关闭位图化时走**独立的生产级原生分支**（auto_brush + 结构自检），
   明确硬度几何代价；不得把研究用的 _computed_brush_def 当成已验证生产路径。
3. 纹理预缩放关闭时保留源纹理字节与原生 Scale；亮度越界烘焙关闭时保留
   Krita 可调字段并明写截断/外观误差，不偷偷换成另一种烘焙链。
4. 三项开关的开启/关闭/不适用正反例与组合；关闭位图化时重新核对
   full_coverage 与 hardMix 偏置，不改随机源修复、不改采样笔尖补方/边框 255。

测试样本固定落在工作区 .tmp/168/tests/（不使用系统临时目录）；样本 ABR 只读。
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from brush_converter.convert import (  # noqa: E402
    DEFAULT_CONVERSION_SWITCHES,
    ConversionSwitches,
    _build_kpp_bytes,
    _native_computed_brush_def,
    _native_computed_preview_mask,
    _png_bytes,
    _prescale_texture_image,
    _ps_texture_mask_8bit,
    _render_preset,
    _square_tip,
    _texture_png_array,
    _texture_xml,
    _tip_is_full_coverage,
    _verify_native_computed_brush,
    convert,
    convert_presets,
    plan_preset,
)
from brush_converter.export_options import (  # noqa: E402
    ACTUAL_ALTERNATIVE,
    ACTUAL_APPLIED,
    ACTUAL_NOT_APPLICABLE,
    SUMMARY_EVIDENCE_CONVERSION,
    SWITCH_BAKE_TEXTURE_BC,
    SWITCH_IDS,
    SWITCH_PRESCALE_TEXTURE,
    SWITCH_RASTERIZE_COMPUTED,
    ExportOptionState,
    source_digest_of_bytes,
)
from brush_converter.kpp.kpp_writer import render_tip_preview  # noqa: E402
from brush_converter.kpp.preset_xml import (  # noqa: E402
    texture_brightness_contrast,
    texture_brightness_truncation,
)
from brush_converter.mapping import BrushPreset, TextureSettings  # noqa: E402

pytestmark = [pytest.mark.verify, pytest.mark.verify_params]

TASK_TMP = REPO_ROOT / ".tmp" / "168"
TESTS_TMP = TASK_TMP / "tests"

#: 本轮开工基线（改动前，HEAD=83b002f）用同一组固定夹具记录的值。
#: 默认路径必须逐字节不变；改动若动到 PNG/编码/XML 任一环，这里会先失败。
GOLDEN_SAMPLED_XML = "e41d59d0ba87d6fd1111c3379c7b60ea6f36ca0b3875d5fdd0011f741a49fe86"
GOLDEN_SAMPLED_KPP = "a6960ff45d549c13f08eba10793f22ba2ed7be09df19fee1a72054f353089cf0"
GOLDEN_COMPUTED_XML = "4b0673c4e95fea09ef1a7d608f1a978a9c4746f42c79c32366cd31c9d7255f22"
GOLDEN_COMPUTED_KPP = "87bab40bbc760a191745b96dfabffb12875a67b3f26c7e071960d6c3cbaeb66e"


def _fresh_dir(name: str) -> Path:
    """在工作区任务目录下准备一个空目录（只删除本任务自己创建的路径）。"""
    target = TESTS_TMP / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _gray(seed: int = 168, shape: tuple[int, ...] = (24, 24)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=shape, dtype=np.uint8)


def _texture(**kwargs) -> TextureSettings:
    base = dict(name="t168", uuid="u168", scale=200.0, brightness=-150,
                contrast=-50, invert=True, depth=100.0, blend_mode="Mltp",
                image=_gray(168, (24, 24)))
    base.update(kwargs)
    return TextureSettings(**base)


def _sampled() -> BrushPreset:
    """固定采样笔尖夹具（与开工基线脚本完全一致）。"""
    return BrushPreset(name="bc168 sampled", spacing=0.25, angle=30.0, roundness=80.0,
                       diameter=40.0, tip_gray=_gray(1, (8, 10)), scale=2.5,
                       spacing_basis=0.8)


def _computed_texture() -> BrushPreset:
    """固定计算笔刷 + 越界纹理夹具（三项开关同时适用）。"""
    return BrushPreset(name="bc168 computed", spacing=0.1, angle=-15.0, roundness=70.0,
                       diameter=120.0, hardness=35.0, is_computed=True,
                       texture=_texture())


def _computed(**kwargs) -> BrushPreset:
    data = dict(name="bc168 computed", spacing=0.1, angle=-15.0, roundness=70.0,
                diameter=120.0, hardness=35.0, is_computed=True)
    data.update(kwargs)
    return BrushPreset(**data)


def _switches(**kwargs) -> ConversionSwitches:
    return ConversionSwitches(**kwargs)


def _sha(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _param(xml: str, name: str) -> str:
    m = re.search(
        r'<param name="' + re.escape(name) + r'" type="(?:internal|string)">(.*?)</param>',
        xml, re.S)
    assert m is not None, f"XML 缺少参数 {name}"
    raw = m.group(1)
    if raw.startswith("<![CDATA[") and raw.endswith("]]>"):
        raw = raw[len("<![CDATA["):-len("]]>")]
    return raw


def _brush_def(xml: str) -> ET.Element:
    return ET.fromstring(_param(xml, "brush_definition"))


def _decoded_png(png: bytes, mode: str = "L") -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert(mode))


def _kpp_bytes(bp: BrushPreset, plan=None) -> bytes:
    _fname, xml, preview = _render_preset(bp, 0, plan=plan)
    return _build_kpp_bytes(xml, preview)


def _state(*presets: BrushPreset) -> ExportOptionState:
    return ExportOptionState.create(list(presets), source_digest_of_bytes(b"task168 source"))


def _disable_all(state: ExportOptionState) -> None:
    for preset_id in state.preset_ids:
        for switch_id in SWITCH_IDS:
            if state.applicability(preset_id, switch_id).applicable:
                state.set_override(preset_id, switch_id, False)


# ---------------------------------------------------------------------------
# 1) 默认与基线：三开关全开 = 现行实现，逐字节不变
# ---------------------------------------------------------------------------
def test_default_switches_mirror_current_behavior():
    assert DEFAULT_CONVERSION_SWITCHES == ConversionSwitches(True, True, True)
    plan = plan_preset(_computed_texture())
    assert plan.rasterize_effective and plan.prescale_effective and plan.bake_effective
    assert plan.native_tip is False
    for switch_id in SWITCH_IDS:
        outcome = plan.outcome(switch_id)
        assert outcome.applicable and outcome.enabled
        assert outcome.actual == ACTUAL_APPLIED
        assert outcome.limited is False


def test_default_output_matches_frozen_baseline_hashes():
    """默认产物必须与改动前（HEAD=83b002f）记录的哈希逐字节一致。"""
    sampled = _sampled()
    _fname, xml, preview = _render_preset(sampled, 0)
    assert _sha(xml) == GOLDEN_SAMPLED_XML
    assert _sha(_build_kpp_bytes(xml, preview)) == GOLDEN_SAMPLED_KPP

    computed = _computed_texture()
    _fname, xml, preview = _render_preset(computed, 0)
    assert _sha(xml) == GOLDEN_COMPUTED_XML
    assert _sha(_build_kpp_bytes(xml, preview)) == GOLDEN_COMPUTED_KPP


def test_explicit_all_on_plan_is_byte_identical_to_default():
    """显式 plan（三开关全开）不能改变任何字节：开启且命中时沿用现行实现。"""
    for bp in (_sampled(), _computed_texture()):
        fname_a, xml_a, preview_a = _render_preset(bp, 0)
        fname_b, xml_b, preview_b = _render_preset(bp, 0, plan=plan_preset(bp))
        assert fname_a == fname_b
        assert xml_a == xml_b
        assert np.array_equal(preview_a, preview_b)


def test_default_does_not_bake_or_prescale_when_not_applicable(monkeypatch):
    """默认未命中时不得发生无意义烘焙：不适用项不应调用像素函数。"""
    bp = BrushPreset(name="bc168 in-range", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8),
                     texture=_texture(scale=100.0, brightness=0, contrast=0,
                                      invert=False, image=_gray(2, (16, 16))))

    def _boom(*_args, **_kwargs):
        raise AssertionError("不适用项不应进入像素处理")

    monkeypatch.setattr("brush_converter.convert._prescale_texture_image", _boom)
    monkeypatch.setattr("brush_converter.convert._ps_texture_mask_8bit", _boom)
    plan = plan_preset(bp)
    for switch_id in SWITCH_IDS:
        assert plan.outcome(switch_id).actual == ACTUAL_NOT_APPLICABLE
    _fname, xml, _preview = _render_preset(bp, 0, plan=plan)
    assert _param(xml, "Texture/Pattern/Scale") == "1"
    assert _param(xml, "Texture/Pattern/Brightness") == "0"
    assert _param(xml, "Texture/Pattern/Contrast") == "1"


def test_texture_switches_not_applicable_without_pattern_image():
    bp = BrushPreset(name="bc168 no texture", spacing=0.25,
                     tip_gray=np.zeros((4, 4), dtype=np.uint8),
                     texture=_texture(image=None))
    plan = plan_preset(bp)
    for switch_id in (SWITCH_PRESCALE_TEXTURE, SWITCH_BAKE_TEXTURE_BC):
        assert plan.outcome(switch_id).actual == ACTUAL_NOT_APPLICABLE
        assert plan.outcome(switch_id).enabled is False


# ---------------------------------------------------------------------------
# 2) 计算笔刷位图化：开启=现行采样位图；关闭=生产级原生 auto_brush
# ---------------------------------------------------------------------------
def test_rasterize_on_renders_png_brush_with_embedded_tip():
    bp = _computed()
    fname, xml, preview = _render_preset(bp, 0)
    assert fname.endswith(".kpp")
    brush = _brush_def(xml)
    assert brush.get("type") == "png_brush"
    assert 'type="brushes"' in xml
    assert 'type="auto_brush"' not in xml
    assert preview.ndim == 3


def test_rasterize_off_renders_native_auto_brush_without_tip_resource():
    bp = _computed_texture()
    plan = plan_preset(bp, _switches(rasterize_computed=False))
    _fname, xml, _preview = _render_preset(bp, 0, plan=plan)
    brush = _brush_def(xml)
    assert brush.get("type") == "auto_brush"
    assert 'type="png_brush"' not in xml
    assert 'type="brushes"' not in xml  # 不内嵌笔尖位图
    assert 'type="patterns"' in xml     # 纹理仍然内嵌
    mask = brush.find("MaskGenerator")
    assert mask is not None and mask.get("type") == "circle"
    assert float(mask.get("diameter")) == pytest.approx(120.0)
    assert float(mask.get("ratio")) == pytest.approx(0.7)
    assert float(mask.get("hfade")) == pytest.approx(0.35)
    assert float(mask.get("vfade")) == pytest.approx(0.35)
    assert float(brush.get("spacing")) == pytest.approx(0.1)
    # 负角度先 normalize 到 [0,360)，再按弧度写
    assert float(brush.get("angle")) == pytest.approx(np.deg2rad(345.0), abs=1e-5)
    assert plan.native_tip is True


def test_native_brush_def_defaults_and_validator():
    bp = _computed(hardness=None, diameter=None, roundness=100.0, angle=450.0)
    definition = _native_computed_brush_def(bp)
    root = ET.fromstring(definition)
    mask = root.find("MaskGenerator")
    assert mask is not None
    assert float(mask.get("diameter")) == pytest.approx(30.0)
    assert float(mask.get("hfade")) == pytest.approx(0.5)  # 缺省硬度按 50
    assert float(root.get("angle")) == pytest.approx(np.deg2rad(90.0), abs=1e-5)
    # 篡改后的定义必须被结构自检拒绝（不能静默产出错误分支）
    with pytest.raises(ValueError):
        _verify_native_computed_brush(definition.replace('type="auto_brush"', 'type="png_brush"'), bp)
    with pytest.raises(ValueError):
        _verify_native_computed_brush(definition.replace('diameter="30"', 'diameter="31"'), bp)


def test_native_branch_preview_is_circle_not_rasterized_tip():
    bp = _computed()
    plan = plan_preset(bp, _switches(rasterize_computed=False))
    _fname, _xml, preview = _render_preset(bp, 0, plan=plan)
    expected = render_tip_preview(_native_computed_preview_mask(bp))
    assert np.array_equal(preview, expected)
    _fname, _xml, rasterized = _render_preset(bp, 0)
    assert not np.array_equal(preview, rasterized)


def test_native_branch_hardmix_has_no_full_coverage_bias():
    """关闭位图化后 full_coverage 必然 False：hardMix 偏置不下发，Strength 保留深度。"""
    tex = _texture(scale=100.0, brightness=0, contrast=0, invert=False,
                   depth=10.0, blend_mode="hardMix")
    bp = _computed(texture=tex)
    plan = plan_preset(bp, _switches(rasterize_computed=False))
    _fname, xml, _preview = _render_preset(bp, 0, plan=plan)
    assert _param(xml, "Texture/Pattern/TexturingMode") == "10"
    assert _param(xml, "Texture/Strength/Value") == "0.1"
    assert _param(xml, "Texture/Pattern/Brightness") == "0"
    assert _param(xml, "Texture/Pattern/Invert") == "false"
    # 位图化路径下生成的位图也不是满覆盖（AA 边），同样不应有偏置
    _fname, xml_on, _preview = _render_preset(bp, 0)
    assert _param(xml_on, "Texture/Strength/Value") == "0.1"
    assert _param(xml_on, "Texture/Pattern/Brightness") == "0"


def test_sampled_full_coverage_hardmix_bias_is_unchanged():
    """对照：采样满覆盖笔尖的 hardMix 偏置保持 135-2 行为（本任务未改随机/偏置逻辑）。"""
    tex = _texture(scale=100.0, brightness=0, contrast=0, invert=False,
                   depth=10.0, blend_mode="hardMix")
    bp = BrushPreset(name="bc168 full", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8), texture=tex)
    assert _tip_is_full_coverage(bp.tip_gray) is True
    _fname, xml, _preview = _render_preset(bp, 0)
    assert _param(xml, "Texture/Strength/Value") == "1"
    assert float(_param(xml, "Texture/Pattern/Brightness")) == pytest.approx(-1.0 / 255.0)


def test_rasterize_switch_does_not_touch_rotation_random_source():
    bp = _computed(rotation_sensor="drawingangle", rotation_jitter=25.0)
    plan_off = plan_preset(bp, _switches(rasterize_computed=False))
    _fname, xml_on, _preview = _render_preset(bp, 0)
    _fname, xml_off, _preview = _render_preset(bp, 0, plan=plan_off)
    for name in ("RotationSensor", "RotationValue", "PressureRotation"):
        assert _param(xml_on, name) == _param(xml_off, name)
    assert "fuzzystroke" not in xml_off
    assert xml_off.count("ChildSensor id=\"fuzzy\"") == 1  # 单随机源修复保持不变


def test_sampled_tip_square_padding_is_unchanged():
    bp = _sampled()
    _fname, xml, _preview = _render_preset(bp, 0)
    assert 'type="png_brush"' in xml
    b64 = re.search(r'<resource [^>]*type="brushes"[^>]*><!\[CDATA\[([A-Za-z0-9+/=]+)\]\]></resource>', xml)
    assert b64 is not None
    import base64
    assert base64.b64decode(b64.group(1)) == _png_bytes(_square_tip(bp.tip_gray))


# ---------------------------------------------------------------------------
# 3) 纹理预缩放：开启=PS 同核预缩放 + Scale=1；关闭=源字节 + 原生 Scale
# ---------------------------------------------------------------------------
def test_prescale_off_keeps_source_texture_and_native_scale():
    img = _gray(7, (24, 24))
    bp = BrushPreset(name="bc168 prescale", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8),
                     texture=_texture(scale=200.0, brightness=0, contrast=0,
                                      invert=False, image=img))
    plan = plan_preset(bp, _switches(prescale_texture=False))
    assert plan.prescale_effective is False
    tex = _texture_xml(bp, plan=plan)
    assert tex is not None
    raw_png = _texture_png_array(img)
    assert tex.png_bytes == raw_png
    assert tex.pattern_filename == "tex_" + _sha(raw_png)[:12] + ".png"
    assert tex.scale == pytest.approx(2.0)
    _fname, xml, _preview = _render_preset(bp, 0, plan=plan)
    assert _param(xml, "Texture/Pattern/Scale") == "2"
    assert _decoded_png(tex.png_bytes).shape == (24, 24)


def test_prescale_off_never_calls_prescaler(monkeypatch):
    bp = BrushPreset(name="bc168 prescale", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8),
                     texture=_texture(scale=200.0, brightness=0, contrast=0,
                                      invert=False))

    def _boom(*_args, **_kwargs):
        raise AssertionError("关闭预缩放后不应调用 _prescale_texture_image")

    monkeypatch.setattr("brush_converter.convert._prescale_texture_image", _boom)
    plan = plan_preset(bp, _switches(prescale_texture=False))
    tex = _texture_xml(bp, plan=plan)
    assert tex is not None and tex.scale == pytest.approx(2.0)


def test_prescale_on_still_uses_ps_kernel_and_scale_one():
    img = _gray(7, (24, 24))
    bp = BrushPreset(name="bc168 prescale", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8),
                     texture=_texture(scale=200.0, brightness=0, contrast=0,
                                      invert=False, image=img))
    tex = _texture_xml(bp)
    assert tex is not None
    assert tex.scale == pytest.approx(1.0)
    assert tex.png_bytes == _texture_png_array(_prescale_texture_image(img, 200.0))
    assert _decoded_png(tex.png_bytes).shape == (48, 48)


def test_prescale_inapplicable_when_round_hits_same_size(monkeypatch):
    img = np.zeros((16, 16), dtype=np.uint8)
    bp = BrushPreset(name="bc168 boundary", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8),
                     texture=_texture(scale=103.125, brightness=0, contrast=0,
                                      invert=False, image=img))

    def _boom(*_args, **_kwargs):
        raise AssertionError("不适用时不应调用预缩放")

    monkeypatch.setattr("brush_converter.convert._prescale_texture_image", _boom)
    plan = plan_preset(bp)
    assert plan.outcome(SWITCH_PRESCALE_TEXTURE).actual == ACTUAL_NOT_APPLICABLE
    tex = _texture_xml(bp, plan=plan)
    assert tex is not None
    assert tex.png_bytes == _texture_png_array(img)
    assert tex.scale == pytest.approx(1.03125)


def test_prescale_off_marks_scale_above_ten_as_limited():
    bp = BrushPreset(name="bc168 big scale", spacing=0.25,
                     tip_gray=np.zeros((4, 4), dtype=np.uint8),
                     texture=_texture(scale=2000.0, brightness=0, contrast=0,
                                      invert=False, image=np.zeros((4, 4), dtype=np.uint8)))
    plan = plan_preset(bp, _switches(prescale_texture=False))
    outcome = plan.outcome(SWITCH_PRESCALE_TEXTURE)
    assert outcome.actual == ACTUAL_ALTERNATIVE
    assert outcome.limited is True
    assert "待 187" in outcome.actual_reason
    tex = _texture_xml(bp, plan=plan)
    assert tex is not None and tex.scale == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# 4) 亮度越界烘焙：开启=烘像素+中性字段；关闭=保留字段并明写截断
# ---------------------------------------------------------------------------
def test_bake_off_keeps_native_fields_and_raw_pixels():
    img = _gray(11, (16, 16))
    bp = BrushPreset(name="bc168 bake", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8),
                     texture=_texture(scale=100.0, brightness=-150, contrast=-50,
                                      invert=True, image=img))
    plan = plan_preset(bp, _switches(bake_texture_bc=False))
    assert plan.bake_effective is False
    tex = _texture_xml(bp, plan=plan)
    assert tex is not None
    expected_b, expected_c = texture_brightness_contrast(-150, -50)
    assert tex.brightness == pytest.approx(expected_b)
    assert tex.contrast == pytest.approx(expected_c)
    assert tex.invert is True
    assert tex.png_bytes == _texture_png_array(img)  # 未烘焙，像素一字未动
    assert tex.pattern_filename == "tex_" + _sha(tex.png_bytes)[:12] + ".png"
    _fname, xml, _preview = _render_preset(bp, 0, plan=plan)
    assert float(_param(xml, "Texture/Pattern/Brightness")) == pytest.approx(expected_b)
    assert float(_param(xml, "Texture/Pattern/Contrast")) == pytest.approx(expected_c)
    assert _param(xml, "Texture/Pattern/Invert") == "true"


def test_bake_off_reports_truncation_and_marks_limited():
    bp = BrushPreset(name="bc168 bake", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8),
                     texture=_texture(scale=100.0, brightness=-150, contrast=-50,
                                      invert=True))
    outcome = plan_preset(bp, _switches(bake_texture_bc=False)).outcome(SWITCH_BAKE_TEXTURE_BC)
    info = texture_brightness_truncation(-150, -50)
    assert info["truncated"] is True
    assert outcome.limited is True
    assert f"反解 {float(info['raw']):.4f}" in outcome.actual_reason
    assert f"写 {float(info['written']):.4f}" in outcome.actual_reason
    assert "不烘焙" in outcome.actual_reason


def test_bake_off_never_calls_bake(monkeypatch):
    bp = BrushPreset(name="bc168 bake", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8),
                     texture=_texture(scale=100.0, brightness=-150, contrast=-50,
                                      invert=True))

    def _boom(*_args, **_kwargs):
        raise AssertionError("关闭烘焙后不应调用 _ps_texture_mask_8bit")

    monkeypatch.setattr("brush_converter.convert._ps_texture_mask_8bit", _boom)
    plan = plan_preset(bp, _switches(bake_texture_bc=False))
    tex = _texture_xml(bp, plan=plan)
    assert tex is not None and tex.brightness == pytest.approx(1.0)


def test_bake_on_still_writes_neutral_fields_and_baked_pixels():
    img = _gray(11, (16, 16))
    bp = BrushPreset(name="bc168 bake", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8),
                     texture=_texture(scale=100.0, brightness=-150, contrast=-50,
                                      invert=True, image=img))
    tex = _texture_xml(bp)
    assert tex is not None
    assert tex.brightness == 0.0 and tex.contrast == 1.0 and tex.invert is False
    assert np.array_equal(_decoded_png(tex.png_bytes), _ps_texture_mask_8bit(img, -150, -50, True))
    assert tex.scale == pytest.approx(1.0)


def test_bake_inapplicable_in_range_keeps_fields():
    bp = BrushPreset(name="bc168 in-range", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8),
                     texture=_texture(scale=100.0, brightness=0, contrast=50,
                                      invert=True, image=_gray(12, (16, 16))))
    plan = plan_preset(bp)
    assert plan.outcome(SWITCH_BAKE_TEXTURE_BC).actual == ACTUAL_NOT_APPLICABLE
    tex = _texture_xml(bp, plan=plan)
    assert tex is not None
    # 不适用时保持 calibrated 原生字段（不是中性值，也不是烘焙后字段）
    exp_b, exp_c = texture_brightness_contrast(0, 50)
    assert tex.brightness == pytest.approx(exp_b)
    assert tex.contrast == pytest.approx(exp_c)
    assert tex.invert is True
    assert tex.png_bytes == _texture_png_array(bp.texture.image)


def test_texture_brightness_truncation_boundary():
    for brightness, expect in ((-127, False), (127, False), (-128, True), (128, True)):
        info = texture_brightness_truncation(brightness, -50)
        assert info["truncated"] is expect
    raw = texture_brightness_truncation(150, 0)["raw"]
    assert abs(float(raw)) <= 1.0  # 正对比度侧反解恒在域内（B10 只命中 C=-50 区）


# ---------------------------------------------------------------------------
# 5) 组合：三项开关的 8 种组合都必须真实改变对应产物
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rasterize", [True, False])
@pytest.mark.parametrize("prescale", [True, False])
@pytest.mark.parametrize("bake", [True, False])
def test_switch_combinations_change_expected_fields(rasterize, prescale, bake):
    bp = _computed_texture()
    plan = plan_preset(bp, _switches(rasterize_computed=rasterize,
                                     prescale_texture=prescale,
                                     bake_texture_bc=bake))
    _fname, xml, _preview = _render_preset(bp, 0, plan=plan)
    brush = _brush_def(xml)
    assert brush.get("type") == ("png_brush" if rasterize else "auto_brush")
    assert ('type="brushes"' in xml) is rasterize
    assert _param(xml, "Texture/Pattern/Scale") == ("1" if prescale else "2")
    if bake:
        assert _param(xml, "Texture/Pattern/Brightness") == "0"
        assert _param(xml, "Texture/Pattern/Contrast") == "1"
        assert _param(xml, "Texture/Pattern/Invert") == "false"
    else:
        assert float(_param(xml, "Texture/Pattern/Brightness")) == pytest.approx(1.0)
        assert float(_param(xml, "Texture/Pattern/Contrast")) == pytest.approx(0.5)
        assert _param(xml, "Texture/Pattern/Invert") == "true"
    # 不适用项在这支笔上不存在：三项开关都真实适用
    assert plan.prescale_effective is prescale
    assert plan.bake_effective is bake
    assert plan.rasterize_effective is rasterize


def test_all_eight_combinations_have_unique_kpp_bytes():
    bp = _computed_texture()
    seen: dict[str, tuple[bool, bool, bool]] = {}
    for rasterize in (True, False):
        for prescale in (True, False):
            for bake in (True, False):
                plan = plan_preset(bp, _switches(rasterize_computed=rasterize,
                                                 prescale_texture=prescale,
                                                 bake_texture_bc=bake))
                digest = _sha(_kpp_bytes(bp, plan))
                assert digest not in seen, f"组合 {rasterize,prescale,bake} 与 {seen.get(digest)} 产物相同"
                seen[digest] = (rasterize, prescale, bake)
    assert len(seen) == 8


def test_prescale_off_bake_on_applies_bake_to_source_pixels():
    """组合语义：预缩放关闭时烘焙作用在源尺寸；Scale 仍写原生百分比。"""
    img = _gray(13, (16, 16))
    bp = BrushPreset(name="bc168 combo", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8),
                     texture=_texture(scale=200.0, brightness=-150, contrast=-50,
                                      invert=True, image=img))
    plan = plan_preset(bp, _switches(prescale_texture=False, bake_texture_bc=True))
    tex = _texture_xml(bp, plan=plan)
    assert tex is not None
    assert tex.scale == pytest.approx(2.0)
    assert _decoded_png(tex.png_bytes).shape == (16, 16)
    assert np.array_equal(_decoded_png(tex.png_bytes),
                          _ps_texture_mask_8bit(img, -150, -50, True))


def test_prescale_on_bake_off_keeps_prescaled_pixels_and_native_fields():
    img = _gray(14, (16, 16))
    bp = BrushPreset(name="bc168 combo", spacing=0.25,
                     tip_gray=np.zeros((8, 8), dtype=np.uint8),
                     texture=_texture(scale=200.0, brightness=-150, contrast=-50,
                                      invert=True, image=img))
    plan = plan_preset(bp, _switches(bake_texture_bc=False))
    tex = _texture_xml(bp, plan=plan)
    assert tex is not None
    assert tex.scale == pytest.approx(1.0)
    assert _decoded_png(tex.png_bytes).shape == (32, 32)
    assert tex.brightness == pytest.approx(1.0)
    assert tex.contrast == pytest.approx(0.5)
    assert tex.invert is True


# ---------------------------------------------------------------------------
# 6) 真实运行摘要：actual 来自执行结果，evidence=conversion-run
# ---------------------------------------------------------------------------
def test_convert_presets_records_alternative_actuals_and_costs():
    bp = _computed_texture()
    state = _state(bp)
    _disable_all(state)
    out = _fresh_dir("summary_off")
    result = convert_presets(None, [bp], str(out), make_bundle=False,
                             make_standalone=True, option_state=state)
    summary = result.options_summary
    assert summary is not None
    assert summary.evidence == SUMMARY_EVIDENCE_CONVERSION
    counts = summary.counts()
    assert counts["total_presets"] == 1
    assert counts["alternative"] == 3
    assert counts["applied"] == 0
    for switch_id in SWITCH_IDS:
        assert summary.items[0].switches[switch_id].actual == ACTUAL_ALTERNATIVE
        assert summary.items[0].switches[switch_id].pending_alternative is False
    lines = result.option_summary_lines()
    text = "\n".join(lines)
    assert "可编辑替代 3 项" in text
    assert "实际烘焙 0 项，未烘焙（保留原生字段） 1 项" in text
    assert "实际位图化 0 项，原生可编辑 1 项" in text
    assert "实际预缩放 0 项，保留源纹理 1 项" in text
    assert "标受限" in text
    assert "（未做人工效果确认）" in "".join(result.warnings)
    # 产物确实与默认不同（同源、两种开关状态）
    _fname, xml, preview = _render_preset(bp, 0)
    default_kpp = _build_kpp_bytes(xml, preview)
    assert result.kpp_files[0][1] != default_kpp
    assert (out / "kpp" / result.kpp_files[0][0]).exists()


def test_convert_presets_default_state_is_byte_identical_to_no_state():
    bp = _computed_texture()
    state = _state(bp)  # 条件默认：三项全开
    out_a = _fresh_dir("state_default")
    out_b = _fresh_dir("state_none")
    with_state = convert_presets(None, [bp], str(out_a), make_bundle=False,
                                 make_standalone=True, option_state=state)
    without = convert_presets(None, [bp], str(out_b), make_bundle=False,
                              make_standalone=True)
    assert with_state.kpp_files == without.kpp_files
    assert with_state.options_summary is not None
    assert with_state.options_summary.counts()["applied"] == 3
    assert without.options_summary is None
    assert without.option_summary_lines() == []


def test_convert_presets_rejects_preset_outside_state():
    state = _state(_sampled())
    foreign = _computed_texture()
    out = _fresh_dir("foreign")
    with pytest.raises(ValueError, match="不在导出选项状态"):
        convert_presets(None, [foreign], str(out), make_bundle=False, option_state=state)


def test_convert_checks_source_digest_before_writing():
    abr = REPO_ROOT / "tests" / "abr-generated" / "hardness-050.abr"
    if not abr.exists():
        pytest.skip("缺少 hardness-050.abr")
    state = ExportOptionState.create(
        [BrushPreset(name="x", is_computed=True, diameter=200.0, hardness=50.0)],
        source_digest_of_bytes(b"another source"))
    out = _fresh_dir("digest_mismatch")
    with pytest.raises(ValueError, match="另一份源文件"):
        convert(str(abr), str(out), make_bundle=False, option_state=state)
    assert not (out / "kpp").exists()


def test_subset_uses_object_identity_for_options():
    first = _sampled()
    second = _computed_texture()
    state = _state(first, second)
    _disable_all(state)
    # 只转第二支：对象身份必须命中第二支的 ID，而不是按列表下标错位
    out = _fresh_dir("subset")
    result = convert_presets(None, [second], str(out), make_bundle=False,
                             option_state=state)
    pid = state.preset_ids[1]
    assert pid in result.option_plans
    assert result.options_summary is not None
    assert result.options_summary.items[0].identity.preset_id == pid


# ---------------------------------------------------------------------------
# 7) 不变量：只在显式关闭时走替代；开关不改变补方/边框/随机源修复
# ---------------------------------------------------------------------------
def test_alternative_paths_require_explicit_off():
    bp = _computed_texture()
    on = plan_preset(bp)
    off = plan_preset(bp, _switches(rasterize_computed=False,
                                    prescale_texture=False,
                                    bake_texture_bc=False))
    assert on.native_tip is False and off.native_tip is True
    assert on.limited_switches == ()
    assert set(off.limited_switches) == {SWITCH_RASTERIZE_COMPUTED, SWITCH_BAKE_TEXTURE_BC}
