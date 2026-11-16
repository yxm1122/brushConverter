"""ABR 读取器的「零笔刷」诊断回归测试（对应 GitHub issue #1）。

背景：用户报告某个 PS 能正常导入的 .abr，在转换器里显示「共 0 支」、
无预览、无法转换。根因是 reader 在结构异常时静默返回空表，
用户只能看到空列表而拿不到任何原因。

这些用例锁定：凡解析成功但读不到笔刷，必须留下可解释的 diagnostics。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from brush_converter.abr import AbrFile  # noqa: E402


def _header(version: int, subversion: int) -> bytes:
    return struct.pack(">HH", version, subversion)


def _section(key: bytes, payload: bytes) -> bytes:
    return b"8BIM" + key + struct.pack(">I", len(payload)) + payload


def _parse(data: bytes) -> AbrFile:
    return AbrFile._from_bytes(data, "probe")


def test_empty_samp_reports_diagnostic():
    """samp 区为空：0 支，但必须说明 samp 为空。"""
    abr = _parse(_header(6, 2) + _section(b"samp", b""))
    assert abr.tips == []
    assert any("samp" in d for d in abr.diagnostics)


def test_missing_samp_reports_diagnostic():
    """只有 patt 没有 samp：必须说明缺少 samp 区。"""
    abr = _parse(_header(6, 2) + _section(b"patt", b""))
    assert abr.tips == []
    assert any("samp" in d for d in abr.diagnostics)


def test_non_8bim_signature_reports_offset():
    """区段签名不是 8BIM：记录中断偏移，而不是静默丢弃剩余字节。"""
    data = _header(6, 2) + b"XXXX" + b"samp" + struct.pack(">I", 0)
    abr = _parse(data)
    assert abr.sections == {}
    assert abr.tips == []
    assert any("8BIM" in d and "偏移" in d for d in abr.diagnostics)


def test_unknown_major_version_reports_diagnostic():
    """未知主版本（如 v7）：提示版本不受支持。"""
    abr = _parse(_header(7, 1) + _section(b"desc", b""))
    assert abr.sections == {}
    assert any("版本" in d for d in abr.diagnostics)


def test_header_only_file_reports_diagnostic():
    """只有头部、无任何区段：仍要给出原因。"""
    abr = _parse(_header(6, 2))
    assert abr.tips == []
    assert abr.diagnostics


def test_truncated_section_length_reports_diagnostic():
    """区段声明长度超过文件：按剩余截断并记录。"""
    payload = b"abcd"
    # 用未知区段名 'zzzz'，避免触发 patt/samp 各自的解析逻辑，
    # 专测「声明长度超出文件」这一条诊断。
    data = _header(6, 2) + b"8BIM" + b"zzzz" + struct.pack(">I", 9999) + payload
    abr = _parse(data)
    assert "zzzz" in abr.sections
    assert abr.sections["zzzz"].length == len(payload)
    assert any("超出文件" in d for d in abr.diagnostics)


def test_looks_unsupported_flag():
    """解析成功但无笔刷且无区段 → 标记为可疑结构。"""
    abr = _parse(_header(7, 1))
    assert abr.looks_unsupported is True


def test_summary_surfaces_diagnostics():
    """summary() 必须把诊断信息带给 CLI 用户。"""
    abr = _parse(_header(6, 2))
    text = abr.summary()
    assert "未读取到任何笔刷" in text
    assert "诊断:" in text


def test_valid_section_has_no_spurious_diagnostics():
    """正常文件不应产生诊断噪音（空 patt 且无 samp 除外）。"""
    abr = _parse(_header(6, 2) + _section(b"patt", b""))
    # 只应有「缺少 samp」这一条，不应有签名中断类诊断
    assert not any("8BIM" in d for d in abr.diagnostics)