"""v1.3.1 产物加固回归测试（使用者批准的 4 条加固）。

覆盖：
  ① 同输入两次转换：bundle 字节相同、文件名 hash8 相同；
  ② 内容变化 → hash8 与产物名变化；
  ③ 覆盖防呆：同内容不重复写并复用；不同内容自动加 _2/_3 后缀且原文件不动；
  ④ verify_bundle 对截断/损坏/非法 zip 返回失败（不抛异常）；
  ⑤ 纹理计数：1x1 占位图案不算真实纹理；005-011 类真实纹理样本命中。

真实样本（tests/海怪笔刷-*.abr）被 .gitignore 排除（商用大素材），
缺失时 skip —— skip 不等于通过。
"""

from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from brush_converter import __version__  # noqa: E402
from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.convert import (  # noqa: E402
    ConvertResult,
    convert_presets,
    count_real_textures,
    kpp_has_real_texture,
)
from brush_converter.kpp.bundle import (  # noqa: E402
    build_bundle_bytes,
    bundle_content_hash8_of_file,
    verify_bundle,
    write_bundle,
)
from brush_converter.kpp.kpp_writer import build_kpp  # noqa: E402
from brush_converter.kpp.preset_xml import TextureXml, build_preset_xml  # noqa: E402
from brush_converter.mapping import BrushPreset, map_presets  # noqa: E402

#: 使用者报障文件（19 支套装，005-011 带真实纹理），不入库
SAMPLE_ABR = Path(__file__).resolve().parent / "海怪笔刷-详情展示的勾线笔套装组18支.abr"


class _FakeAbr:
    """convert_presets 只用 abr.name 作为产物基名，这里给个最小替身。

    这样确定性/防呆用例不依赖任何真实 .abr，任何人克隆仓库都能跑。
    """

    name = "样本笔刷"


def _presets(count: int = 3) -> list[BrushPreset]:
    """构造 count 个采样笔刷（灰度蒙版取值不同 → 字节确定但不相同）。"""
    return [
        BrushPreset(name=f"笔刷{i}",
                    tip_gray=np.full((16, 16), 200 + i, dtype=np.uint8))
        for i in range(count)
    ]


def _convert(out_dir: Path, presets: list[BrushPreset] | None = None) -> ConvertResult:
    return convert_presets(_FakeAbr(), presets or _presets(), str(out_dir))


def _png(array: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(array, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _kpp_with_texture(name: str, pattern: np.ndarray | None) -> bytes:
    """造一个 .kpp：pattern 为 None 表示完全不开纹理。"""
    texture = None
    if pattern is not None:
        texture = TextureXml(pattern_filename=f"tex_{name}.png",
                             png_bytes=_png(pattern))
    xml = build_preset_xml(name, "", None, texture=texture)
    return build_kpp(xml, np.zeros((200, 200, 4), dtype=np.uint8))


def _bundle_of(kpp_files: list[tuple[str, bytes]], tmp_path: Path) -> Path:
    path = tmp_path / "样例.bundle"
    write_bundle(str(path), kpp_files)
    return path


# ---------------------------------------------------------------- ① 确定性


def test_same_input_produces_identical_bundle_bytes(tmp_path):
    """同输入两次转换（不同目录）→ 产物字节完全相同。"""
    first = _convert(tmp_path / "a")
    second = _convert(tmp_path / "b")
    assert first.bundle_path and second.bundle_path
    assert first.content_hash8 == second.content_hash8
    assert Path(first.bundle_path).name == Path(second.bundle_path).name
    assert Path(first.bundle_path).read_bytes() == Path(second.bundle_path).read_bytes()


def test_bundle_name_carries_version_and_hash8(tmp_path):
    """产物名 = stem_v<__version__>_<content_hash8>.bundle。"""
    result = _convert(tmp_path)
    name = Path(result.bundle_path).name
    assert name == f"样本笔刷_v{__version__}_{result.content_hash8}.bundle"
    assert len(result.content_hash8) == 8
    int(result.content_hash8, 16)  # 必须是合法 hex


def test_hash8_is_logical_not_zip_bytes(tmp_path):
    """hash8 取逻辑内容（条目名+条目字节），不随 zip 头/meta.xml 标题变化。"""
    kpps = [(f"{i:03d}_a.kpp", _kpp_with_texture(f"a{i}", None)) for i in range(2)]
    one = build_bundle_bytes(kpps, "甲")
    two = build_bundle_bytes(kpps, "乙")
    assert one != two, "bundle 名不同则 meta.xml 的 dc:title 不同，zip 字节必然不同"
    first = tmp_path / "甲.bundle"
    second = tmp_path / "乙.bundle"
    first.write_bytes(one)
    second.write_bytes(two)
    assert bundle_content_hash8_of_file(first) == bundle_content_hash8_of_file(second)


# ------------------------------------------------------------- ② 内容变→hash 变


def test_content_change_changes_hash8(tmp_path):
    base = _convert(tmp_path / "p")
    changed_presets = _presets()
    changed_presets[1].tip_gray = np.full((16, 16), 17, dtype=np.uint8)
    changed = _convert(tmp_path / "q", changed_presets)
    assert base.content_hash8 != changed.content_hash8
    assert Path(base.bundle_path).name != Path(changed.bundle_path).name


# ------------------------------------------------------------- ③ 覆盖防呆


def test_existing_same_content_is_reused_without_rewrite(tmp_path):
    first = _convert(tmp_path)
    target = Path(first.bundle_path)
    mtime = target.stat().st_mtime_ns
    size = target.stat().st_size

    second = _convert(tmp_path)
    assert second.bundle_reused is True
    assert second.bundle_path == first.bundle_path
    assert target.stat().st_mtime_ns == mtime, "同内容产物不应被重新写入"
    assert target.stat().st_size == size
    assert any("已存在同内容产物" in w for w in second.warnings)


def test_existing_different_content_gets_suffix_never_overwritten(tmp_path):
    first = _convert(tmp_path)
    target = Path(first.bundle_path)
    occupied = b"not a bundle but occupies the target path"
    target.write_bytes(occupied)

    second = _convert(tmp_path)
    assert second.bundle_renamed is True
    assert Path(second.bundle_path).name == f"{target.stem}_2{target.suffix}"
    assert target.read_bytes() == occupied, "已存在的同名文件必须一字不动"
    assert Path(second.bundle_path).exists()
    assert second.content_hash8 in Path(second.bundle_path).name
    assert any("已重命名" in w for w in second.warnings)

    # 再占一个 → 后缀递增到 _3（绝不覆盖、也不复用别人的占位文件）
    Path(second.bundle_path).write_bytes(b"another occupied path")
    third = _convert(tmp_path)
    assert Path(third.bundle_path).name == f"{target.stem}_3{target.suffix}"


def test_legacy_stem_bundle_is_reported_not_overwritten(tmp_path):
    """旧版命名的 stem.bundle 只提示不覆盖（使用者报障的现场就是新旧产物混放）。"""
    legacy = tmp_path / "样本笔刷.bundle"
    legacy.write_bytes(b"v1.3.0 legacy artifact")
    result = _convert(tmp_path)
    assert legacy.read_bytes() == b"v1.3.0 legacy artifact"
    assert any("旧版命名产物" in w for w in result.warnings)
    assert Path(result.bundle_path).name != legacy.name


# ------------------------------------------------------------- ④ 回读校验


def test_verify_bundle_accepts_fresh_output(tmp_path):
    result = _convert(tmp_path)
    verified = result.verify
    assert verified is not None and verified.ok, verified.errors
    data = Path(result.bundle_path).read_bytes()
    assert verified.preset_count == len(result.kpp_files) == 3
    assert verified.size == len(data)
    assert verified.sha256 == hashlib.sha256(data).hexdigest()


def test_verify_bundle_rejects_truncated_zip(tmp_path):
    path = _bundle_of([("000_a.kpp", _kpp_with_texture("a", None))], tmp_path)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    verified = verify_bundle(path)
    assert verified.ok is False
    assert verified.errors


def test_verify_bundle_rejects_corrupted_payload(tmp_path):
    path = _bundle_of([("000_a.kpp", _kpp_with_texture("a", None))], tmp_path)
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF  # 破坏中部字节：解压/CRC 必失败
    path.write_bytes(bytes(data))
    verified = verify_bundle(path)
    assert verified.ok is False, "损坏的字节不能被当成校验通过"
    assert verified.errors


def test_verify_bundle_rejects_non_zip(tmp_path):
    path = tmp_path / "x.bundle"
    payload = b"plain text, not a zip"
    path.write_bytes(payload)
    verified = verify_bundle(path)
    assert verified.ok is False
    assert verified.size == len(payload)
    assert verified.errors


# ------------------------------------------------------------- ⑤ 纹理计数


def test_texture_count_ignores_1x1_placeholder():
    """1x1 占位图案（Enabled=true 但没有真实图像）不算「含真实纹理」。"""
    assert kpp_has_real_texture(
        _kpp_with_texture("a", np.zeros((1, 1), dtype=np.uint8))) is False
    assert kpp_has_real_texture(
        _kpp_with_texture("b", np.zeros((64, 64), dtype=np.uint8))) is True
    assert kpp_has_real_texture(_kpp_with_texture("c", None)) is False
    assert count_real_textures([
        ("000_a.kpp", _kpp_with_texture("a", np.zeros((1, 1), dtype=np.uint8))),
        ("001_b.kpp", _kpp_with_texture("b", np.zeros((16, 16), dtype=np.uint8))),
        ("002_c.kpp", _kpp_with_texture("c", None)),
    ]) == 1


def test_texture_count_hits_005_to_011_samples(tmp_path):
    """真实样本：000-004 无真实纹理，005-011 共 7 支命中。"""
    if not SAMPLE_ABR.exists():
        pytest.skip(f"缺少真实样本 {SAMPLE_ABR.name}"
                    "（.gitignore 排除商用大素材；skip 不等于通过）")
    abr = AbrFile.parse(str(SAMPLE_ABR))
    presets = map_presets(abr)
    if len(presets) < 12:
        pytest.skip(f"样本预设数不足 12（实际 {len(presets)}）")
    result = convert_presets(abr, presets[:12], str(tmp_path), make_bundle=True)
    assert len(result.kpp_files) == 12, "000-011 都应有笔尖，不能出现跳过"
    hits = [index for index, (_name, blob) in enumerate(result.kpp_files)
            if kpp_has_real_texture(blob)]
    assert hits == [5, 6, 7, 8, 9, 10, 11], f"真实纹理预设应为 005-011，实际 {hits}"
    assert result.texture_count == 7

# ------------------------------------------------- ⑤b CLI 回读校验的退出码

def test_cli_prints_verification_ok_and_exits_zero(tmp_path, monkeypatch, capsys):
    """成功路径：打印「输出校验：OK（N 个预设，sha256=…，大小=…B）」并返回 0。"""
    import cli as cli_module

    result = _convert(tmp_path)
    monkeypatch.setattr(cli_module, "convert", lambda *a, **k: result)
    code = cli_module.main(["convert", "sample.abr", "-o", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 0
    assert "输出校验：OK" in captured.out
    assert result.verify is not None
    assert result.verify.sha256 in captured.out
    assert f"大小={result.verify.size}B" in captured.out
    assert "含真实纹理" in captured.out


def test_cli_exits_nonzero_when_verification_fails(tmp_path, monkeypatch, capsys):
    """失败路径：产物坏了必须非 0 退出（返回 3）且把原因打到 stderr。"""
    import cli as cli_module

    broken = tmp_path / "broken.bundle"
    broken.write_bytes(b"not a zip at all")
    fake = ConvertResult(kpp_files=[("000_a.kpp", b"x")],
                         bundle_path=str(broken), out_dir=str(tmp_path))
    monkeypatch.setattr(cli_module, "convert", lambda *a, **k: fake)
    code = cli_module.main(["convert", "sample.abr", "-o", str(tmp_path)])
    captured = capsys.readouterr()
    assert code != 0, "回读校验失败时 CLI 不能返回 0"
    assert code == 3
    assert "输出校验：失败" in captured.err
    assert "校验" in captured.err

