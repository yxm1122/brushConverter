"""ABR patt 区段（纹理图案）解析测试。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from brush_converter.abr.patterns import (  # noqa: E402
    PatternParseError,
    _rle_decode_u16,
    parse_patterns,
)

ABR = Path(__file__).parent / "海怪笔刷-详情展示的勾线笔套装组18支.abr"

needs_sample = pytest.mark.skipif(
    not ABR.exists(),
    reason="缺少测试样本 .abr（请自备到 tests/ 目录）",
)


# ---------- RLE / 错误处理（无需样本） ----------


def test_rle_decode_u16():
    # 3 行 × 4 像素：全 0 / 1,2,3,4 / 全 5
    buf = (
        b"\x00\x02\x00\x05\x00\x02"      # 行长度表：2, 5, 2
        b"\xfd\x00"                       # 行0：重复 0 ×4（256-3=253）
        b"\x03\x01\x02\x03\x04"          # 行1：4 个字面量
        b"\xfd\x05"                        # 行2：重复 5 ×4
    )
    out = _rle_decode_u16(buf, 12, 3)
    assert out == bytes([0, 0, 0, 0, 1, 2, 3, 4, 5, 5, 5, 5])


def test_rle_decode_truncated():
    with pytest.raises(PatternParseError):
        _rle_decode_u16(b"\x00\x02\x00\x05", 12, 3)


def test_parse_patterns_empty():
    assert parse_patterns(b"") == {}


def test_parse_patterns_rejects_garbage():
    # 记录长度 16、体内全零 → version=0 → 报错
    with pytest.raises(PatternParseError):
        parse_patterns(b"\x00\x00\x00\x10" + b"\x00" * 16)


def test_parse_patterns_rejects_negative_length():
    with pytest.raises(PatternParseError):
        parse_patterns(b"\xff\xff\xff\xff" + b"\x00" * 16)


# ---------- 真实样本（需自备 .abr） ----------


@needs_sample
def _patterns():
    from brush_converter.abr import AbrFile
    return AbrFile.parse(ABR).patterns


@needs_sample
def test_pattern_count_and_ids():
    pats = _patterns()
    assert len(pats) == 4
    for uid in (
        "438c2948-d232-11e5-b988-9ff33e1af9cd",
        "66e2987f-d47b-fa49-9e36-3ffa8b78533a",
        "f648c44c-5189-b14e-8ef2-926477d0bbe7",
        "69d92381-cf86-a54b-9d04-7f0fc2d9345b",
    ):
        assert uid in pats


@needs_sample
def test_pattern_names():
    pats = _patterns()
    assert pats["438c2948-d232-11e5-b988-9ff33e1af9cd"].name == "R. Melentyev's Art Texture"
    for uid in (
        "66e2987f-d47b-fa49-9e36-3ffa8b78533a",
        "f648c44c-5189-b14e-8ef2-926477d0bbe7",
        "69d92381-cf86-a54b-9d04-7f0fc2d9345b",
    ):
        assert pats[uid].name == "Shape 2.png"


@needs_sample
def test_pattern_sizes_and_pixels():
    pats = _patterns()
    neutral = pats["438c2948-d232-11e5-b988-9ff33e1af9cd"]
    assert neutral.image.shape == (1, 1, 3)
    assert tuple(neutral.image[0, 0]) == (236, 236, 236)  # 中性浅灰
    for uid in (
        "66e2987f-d47b-fa49-9e36-3ffa8b78533a",
        "f648c44c-5189-b14e-8ef2-926477d0bbe7",
        "69d92381-cf86-a54b-9d04-7f0fc2d9345b",
    ):
        assert pats[uid].image.shape == (1920, 1920, 3)


@needs_sample
def test_pattern_pixel_md5s():
    pats = _patterns()
    expect = {
        "438c2948-d232-11e5-b988-9ff33e1af9cd": "3b39ce997847e012384588e55daed2bf",
        "66e2987f-d47b-fa49-9e36-3ffa8b78533a": "a2c8769ab6cb0e700cd19a3e98d808ed",
        "f648c44c-5189-b14e-8ef2-926477d0bbe7": "bddbd59f275f40e4c8c92c80eb731f75",
        "69d92381-cf86-a54b-9d04-7f0fc2d9345b": "e6d90cf300cac503ef87dcbb3e3e0afd",
    }
    for uid, md5 in expect.items():
        assert hashlib.md5(pats[uid].image.tobytes()).hexdigest() == md5
