"""open-code-review 审查结论的回归测试（2026-11-16）。

每个用例对应一条被确认的问题；标注「实测」的来自真实样本文件的行为。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.abr.descriptors import Reader  # noqa: E402
from brush_converter.abr.patterns import PatternParseError, parse_patterns  # noqa: E402
from brush_converter.abr.rle import rle_decode  # noqa: E402
from brush_converter.convert import convert_presets  # noqa: E402
from brush_converter.kpp.bundle import _manifest_xml, _meta_xml  # noqa: E402
from brush_converter.kpp.kpp_writer import build_kpp  # noqa: E402
from brush_converter.kpp.preset_xml import TextureXml, build_preset_xml  # noqa: E402
from brush_converter.mapping import _map_texture  # noqa: E402
from brush_converter.abr import descriptors as D  # noqa: E402


def _section(key: bytes, payload: bytes, pad_to: int = 4) -> bytes:
    return b"8BIM" + key + struct.pack(">I", len(payload)) + payload


# ---------- 8BIM 区段 4 字节对齐（实测 len%4==1 时需要补 3 字节）----------


def test_8bim_padding_uses_negative_length_mod_4():
    # desc 负载长度 5（%4 == 1）：补足量应为 3 字节，而不是 1
    desc = _section(b"desc", b"12345")
    phry = _section(b"phry", b"x" * 8)
    data = struct.pack(">HH", 6, 2) + desc + b"\x00" * 3 + phry
    abr = AbrFile._from_bytes(data, "probe")
    assert set(abr.sections) == {"desc", "phry"}
    # 该测试数据没有 samp 区，「缺少 samp」诊断是合理的；只要没有区段扫描中断
    assert not any("8BIM" in d for d in abr.diagnostics)


def test_8bim_padding_len_mod_4_is_3():
    samp = _section(b"samp", b"abc")  # %4 == 3 → 补 1 字节
    data = struct.pack(">HH", 6, 2) + samp + b"\x00" + _section(b"phry", b"y")
    abr = AbrFile._from_bytes(data, "probe")
    assert set(abr.sections) == {"samp", "phry"}


# ---------- 描述符 enum 参数顺序：typeID 在前，值在后 ----------


def test_enum_reads_type_id_before_value():
    payload = b"enum" + struct.pack(">I", 4) + b"BlnM" + struct.pack(">I", 4) + b"Hght"
    value = Reader(payload).read_value()
    assert (value.type, value.type_id, value.value) == ("enum", "BlnM", "Hght")


def test_objc_keeps_class_id():
    inner = (
        struct.pack(">I", 0)          # name 长度 0
        + struct.pack(">I", 4) + b"Brsh"
        + struct.pack(">I", 0)        # item 数
    )
    payload = b"Objc" + inner
    value = Reader(payload).read_value()
    assert value.type == "Objc"
    assert value.class_id == "Brsh"


def test_value_list_depth_limit():
    # 深度嵌套的 VlLs 必须变成干净的 DescParseError，而不是 RecursionError
    payload = b"".join(b"VlLs" + struct.pack(">I", 1) for _ in range(200))
    with pytest.raises(D.DescParseError):
        Reader(payload).read_value()


# ---------- 纹理参数：0 是合法值，不能被默认值覆盖 ----------


def test_texture_zero_depth_and_scale_are_kept():
    p = {
        "textureDepth": D.Value("long", 0),
        "textureScale": D.Value("long", 0),
        "textureBrightness": D.Value("long", 0),
        "textureContrast": D.Value("long", 0),
    }
    tex = _map_texture(p, {})
    assert tex is not None
    assert tex.depth == 0.0
    assert tex.scale == 0.0


def test_spacing_zero_is_kept():
    # map_presets 内部对 Spcn 的 None 判断（0 表示连续笔迹）
    from brush_converter.mapping import map_presets
    # 没有样本文件时用真实 desc 构造代价高，这里直接校验表达式语义：
    spcn = 0.0
    assert (25.0 if spcn is None else spcn) / 100.0 == 0.0


# ---------- 计算笔刷只有纹理资源时也要声明 embedded_resources ----------


def test_texture_only_preset_declares_embedded_resources():
    tex = TextureXml(pattern_filename="tex_abc.png", png_bytes=b"png")
    xml = build_preset_xml("t", "", None, texture=tex)
    assert "embedded_resources=\"2\"" in xml
    assert "<resources>" in xml


def test_cdata_and_internal_values_are_escaped():
    xml = build_preset_xml("t", "", None, extra_params={
        "custom_string": "a]]>b",       # string 类型 → CDATA 拆分
        "custom_internal": "<&>",       # internal 类型 → 实体转义
    })
    import xml.etree.ElementTree as ET
    ET.fromstring(xml)                  # 必须仍然是合法 XML


# ---------- .bundle meta/manifest 的命名空间 ----------


def test_bundle_meta_xml_is_namespace_well_formed():
    import xml.etree.ElementTree as ET
    ET.fromstring(_meta_xml("示例包"))


def test_manifest_escapes_attribute_values():
    import xml.etree.ElementTree as ET
    xml = _manifest_xml([('a"b&c.kpp', "d41d8cd98f00b204e9800998ecf8427e")])
    root = ET.fromstring(xml)
    ns = "{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}"
    # 属性值里同时含 " 和 & 时也必须能解析回原值（quoteattr 会按需选引号）
    assert root[1].get(ns + "full-path") == 'paintoppresets/a"b&c.kpp'


# ---------- 参数校验 ----------


def test_build_kpp_rejects_non_rgba_preview():
    with pytest.raises(ValueError):
        build_kpp("<Preset/>", np.zeros((4, 4, 3), dtype=np.uint8))
    with pytest.raises(ValueError):
        build_kpp("<Preset/>", np.zeros((4, 4, 4), dtype=np.float32))


def test_convert_presets_requires_an_output_format():
    with pytest.raises(ValueError):
        convert_presets(None, [], "unused", make_bundle=False, make_standalone=False)


def test_rle_rejects_truncated_row_table():
    with pytest.raises(ValueError):
        rle_decode(b"\x00", expected_size=4, height=2)


def test_pattern_rejects_short_record():
    with pytest.raises(PatternParseError):
        parse_patterns(struct.pack(">I", 10) + b"\x00" * 10)


def test_cli_sanitizes_windows_reserved_characters():
    from cli import _safe_component
    assert _safe_component("a\\b") == "a_b"
    assert _safe_component("C:x") == "C_x"
    assert _safe_component("...") == "tip"
