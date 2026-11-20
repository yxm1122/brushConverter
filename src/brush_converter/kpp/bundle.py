"""打包 Krita 资源包（.bundle）。

.bundle 是 ZIP 归档，布局（严格匹配 Krita KoResourceBundle 的写入格式）：
    mimetype                        （首项、stored 不压缩，application/x-krita-resourcebundle）
    META-INF/manifest.xml           （OpenDocument manifest 命名空间）
    meta.xml                        （OpenDocument meta）
    paintoppresets/*.kpp            （笔刷预设，自包含内嵌笔尖）

v1.3.1 产物加固（使用者批准的 4 条之二/之三/之五）：
1. **字节确定性**：所有条目时间戳固定为 ZIP_DATE_TIME（1980-01-01 00:00:00），
   create_system 固定为 FAT(0)。zip 头里原本带「当前时间 + 宿主平台」，
   同一输入两次转换产出不同字节，导致「产物是否变化」无法用哈希判断。
2. **内容短哈希**：bundle_content_hash8() 只对「逻辑内容」做确定性哈希
   （条目名排序 + 每个条目名/字节 sha256），不受 zip 头时间戳影响，
   用作产物文件名的 hash8 与「同内容复用」判据。
3. **回读校验**：verify_bundle() 写盘后重开产物自查（zip 完整性、mimetype、
   manifest 与条目一致、每个 .kpp 可读出），并记录文件 sha256 与大小。
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from .kpp_writer import read_preset_xml

MIMETYPE = "application/x-krita-resourcebundle"
NS_MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
NS_META = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
NS_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
# Dublin Core 的规范 URI 以斜杠结尾，缺斜杠会落到另一个命名空间
NS_DC = "http://purl.org/dc/elements/1.1/"
RES_TYPE = "paintoppresets"
#: bundle 内预设条目的目录前缀
PREFIX = RES_TYPE + "/"

#: 固定的 zip 条目时间戳：1980-01-01 00:00:00 是 ZIP 格式能表示的最早时间。
#: 写死它才能保证「同一输入两次转换产出字节完全相同」（确定性）。
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

_MANIFEST = "META-INF/manifest.xml"
_META = "meta.xml"
_MIMETYPE_ENTRY = "mimetype"


def _zip_info(name: str, compress_type: int = zipfile.ZIP_DEFLATED) -> zipfile.ZipInfo:
    """构造时间戳/宿主平台都固定的 ZipInfo（确定性写入的唯一入口）。"""
    info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
    info.compress_type = compress_type
    # create_system 默认值随宿主平台变化（Windows=0、POSIX=3），写死 0 后
    # 跨平台也能得到同样的 zip 头字节；external_attr 同理清零。
    info.create_system = 0
    info.external_attr = 0
    return info


def _manifest_xml(entries: list[tuple[str, str]]) -> str:
    """entries: [(filename, md5hex)]"""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<manifest:manifest xmlns:manifest="{NS_MANIFEST}" manifest:version="1.2">',
        '<manifest:file-entry manifest:full-path="/" '
        f'manifest:media-type="{MIMETYPE}"/>',
    ]
    for fname, md5 in entries:
        # 文件名来自 ABR/用户，属性值必须转义（" & < 会破坏 XML）
        full_path = f"{RES_TYPE}/{fname}"
        lines.append(
            f'<manifest:file-entry manifest:media-type="{RES_TYPE}" '
            f'manifest:full-path={quoteattr(full_path)} '
            f'manifest:md5sum={quoteattr(md5)}/>'
        )
    lines.append("</manifest:manifest>")
    return "".join(lines)


def _meta_xml(bundle_name: str) -> str:
    """生成 OpenDocument meta.xml（资源包名称 / 版本 / 描述）。"""
    name = escape(bundle_name)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        # office: 前缀必须在根元素上声明，否则命名空间感知的解析器
        # （Krita 的 QDomDocument、xml.etree）会报 unbound prefix
        f'<office:document-meta xmlns:office="{NS_OFFICE}">\n'
        f'<meta:meta xmlns:meta="{NS_META}" xmlns:dc="{NS_DC}">\n'
        '<meta:generator>brushConverter</meta:generator>\n'
        '<meta:bundle-version>1</meta:bundle-version>\n'
        f'<dc:title>{name}</dc:title>\n'
        '<dc:description>Converted from Photoshop ABR</dc:description>\n'
        '</meta:meta>\n'
        '</office:document-meta>'
    )


def bundle_content_hash8(kpp_files: list[tuple[str, bytes]]) -> str:
    """bundle 的确定性逻辑内容哈希（取 sha256 前 8 位 hex）。

    口径（使用者批准）：bundle 内的预设条目按名字排序，逐个喂入
    「条目名 + 0x00 + sha256(条目字节).hexdigest()」，再对整串取 sha256 前 8 位。

    刻意不用「zip 文件字节的哈希」：zip 头里有时间戳/压缩器差异，
    同一内容换个时间写就会变哈希，既不能做「同内容复用」判据，
    也无法用它判断产物是否随版本变化。
    """
    digest = hashlib.sha256()
    entries = sorted(((PREFIX + fname, data) for fname, data in kpp_files),
                     key=lambda item: item[0])
    for entry_name, data in entries:
        digest.update(entry_name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hashlib.sha256(data).hexdigest().encode("ascii"))
    return digest.hexdigest()[:8]


def bundle_content_hash8_of_file(bundle_path: str | Path) -> str | None:
    """读出已存在 bundle 的逻辑内容哈希；文件不可读/不是合法 bundle 时返回 None。

    用于「目标已存在」时的防呆判断：返回相同 hash8 ⇒ 同内容，可复用不重写；
    返回 None 或不同值 ⇒ 视为不同内容，绝不覆盖（调用方加 _2/_3 后缀）。
    """
    try:
        with zipfile.ZipFile(bundle_path) as zf:
            entries = [
                (info.filename[len(PREFIX):], zf.read(info.filename))
                for info in zf.infolist()
                if info.filename.startswith(PREFIX)
            ]
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        return None
    except Exception:  # noqa: BLE001 - 任何解压/IO 异常都按「不是同内容」处理
        return None
    if not entries:
        return None
    return bundle_content_hash8(entries)


def build_bundle_bytes(kpp_files: list[tuple[str, bytes]], bundle_name: str) -> bytes:
    """按固定布局生成 .bundle 字节（不落盘，供确定性比对/测试用）。"""
    entries: list[tuple[str, str]] = []
    for fname, data in kpp_files:
        entries.append((fname, hashlib.md5(data).hexdigest()))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype 必须是第一个条目且不压缩（Krita 据此识别资源包类型）
        zf.writestr(_zip_info(_MIMETYPE_ENTRY, zipfile.ZIP_STORED), MIMETYPE)
        zf.writestr(_zip_info(_MANIFEST), _manifest_xml(entries))
        zf.writestr(_zip_info(_META), _meta_xml(bundle_name))
        for fname, data in kpp_files:
            zf.writestr(_zip_info(PREFIX + fname), data)
    return buf.getvalue()


def write_bundle(bundle_path: str, kpp_files: list[tuple[str, bytes]]) -> str:
    """把 .kpp 文件打包为 .bundle。

    Parameters
    ----------
    bundle_path : 输出 .bundle 路径（文件名决定 meta.xml 的 dc:title）
    kpp_files : [(文件名, 文件字节)]，将放入 paintoppresets/ 目录

    同输入两次调用产出字节完全相同（条目时间戳固定 + 压缩级别固定）。
    """
    data = build_bundle_bytes(kpp_files, Path(bundle_path).stem)
    with open(bundle_path, "wb") as f:
        f.write(data)
    return bundle_path


@dataclass
class BundleVerifyResult:
    """verify_bundle() 的结论：可直接用于 CLI 打印、GUI 提示与测试断言。"""

    ok: bool = False
    preset_count: int = 0
    size: int = 0
    sha256: str = ""
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """单行摘要（CLI「输出校验：…」用）。"""
        return f"{self.preset_count} 个预设，sha256={self.sha256}，大小={self.size}B"


def _manifest_entries(xml_text: str) -> list[tuple[str, str | None]]:
    """解析 manifest.xml → [(full-path, md5sum)]，跳过 "/" 根条目。"""
    root = ET.fromstring(xml_text)
    resolved: list[tuple[str, str | None]] = []
    for node in root:
        full_path = node.get(f"{{{NS_MANIFEST}}}full-path") or ""
        if full_path in ("", "/"):
            continue
        resolved.append((full_path, node.get(f"{{{NS_MANIFEST}}}md5sum")))
    return resolved


def verify_bundle(path: str | Path) -> BundleVerifyResult:
    """写出后回读校验 .bundle（v1.3.1 加固之五）。

    逐条检查（现状规则）：
      1. zip 能打开；testzip() 无损坏条目；
      2. mimetype 是第一个条目、stored 不压缩、内容等于 MIMETYPE；
      3. manifest 的预设条目数 == zip 里 paintoppresets/ 条目数，
         且每条声明的 md5sum 与实际字节一致、条目确实存在；
      4. 每个 .kpp 能完整读出（PNG 容器 + iTXt 预设 XML，含块 CRC）。
    最后记录整文件 sha256 与字节数。

    不抛异常：任何失败都落进 errors，由调用方决定退出码/提示，
    避免「校验本身崩掉」被误当成通过。
    """
    target = Path(path)
    result = BundleVerifyResult()
    try:
        data = target.read_bytes()
    except OSError as exc:
        result.errors.append(f"无法读取产物：{exc}")
        return result
    result.size = len(data)
    result.sha256 = hashlib.sha256(data).hexdigest()

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        result.errors.append(f"不是有效的 zip 归档：{exc}")
        return result

    with zf:
        infos = zf.infolist()
        names = [info.filename for info in infos]
        kpp_names = [name for name in names if name.startswith(PREFIX)]
        result.preset_count = len(kpp_names)

        # 1) zip 完整性（截断文件可能在 testzip 内部就抛，这里一并兜住）
        try:
            broken = zf.testzip()
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"zip 完整性校验异常：{exc}")
            broken = None
        if broken is not None:
            result.errors.append(f"zip 条目 CRC 校验失败：{broken}")

        # 2) mimetype（Krita 靠「首项 + 不压缩 + 精确内容」识别资源包）
        if not infos:
            result.errors.append("zip 为空，没有任何条目")
        else:
            first = infos[0]
            if first.filename != _MIMETYPE_ENTRY:
                result.errors.append(
                    f"首条目不是 mimetype（实际 {first.filename}）")
            elif first.compress_type != zipfile.ZIP_STORED:
                result.errors.append("mimetype 条目被压缩（Krita 要求 stored）")
            try:
                mimetype = zf.read(_MIMETYPE_ENTRY).decode("utf-8", "replace")
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"读不出 mimetype：{exc}")
            else:
                if mimetype != MIMETYPE:
                    result.errors.append(f"mimetype 内容不正确：{mimetype!r}")

        # 3) manifest 与实际条目一一对应
        if _MANIFEST not in names:
            result.errors.append(f"缺少 {_MANIFEST}")
            declared: list[tuple[str, str | None]] = []
        else:
            try:
                declared = _manifest_entries(
                    zf.read(_MANIFEST).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"manifest 解析失败：{exc}")
                declared = []
        if len(declared) != len(kpp_names):
            result.errors.append(
                f"manifest 条目数（{len(declared)}）与 zip 预设条目数"
                f"（{len(kpp_names)}）不一致")
        for full_path, md5sum in declared:
            if full_path not in names:
                result.errors.append(f"manifest 声明了不存在的条目：{full_path}")
                continue
            try:
                actual = hashlib.md5(zf.read(full_path)).hexdigest()
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"读不出条目 {full_path}：{exc}")
                continue
            if md5sum and actual != md5sum:
                result.errors.append(f"条目 md5 与 manifest 不一致：{full_path}")

        # 4) 每个 .kpp 可读出
        for name in kpp_names:
            try:
                blob = zf.read(name)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"预设 {name} 读不出：{exc}")
                continue
            try:
                read_preset_xml(blob)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"预设 {name} 结构损坏：{exc}")

    result.ok = not result.errors
    return result
