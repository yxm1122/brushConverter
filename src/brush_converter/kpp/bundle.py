"""打包 Krita 资源包（.bundle）。

.bundle 是 ZIP 归档，布局（严格匹配 Krita KoResourceBundle 的写入格式）：
    mimetype                        （首项、stored 不压缩，application/x-krita-resourcebundle）
    META-INF/manifest.xml           （OpenDocument manifest 命名空间）
    meta.xml                        （OpenDocument meta）
    paintoppresets/*.kpp            （笔刷预设，自包含内嵌笔尖）
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

MIMETYPE = "application/x-krita-resourcebundle"
NS_MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
NS_META = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
NS_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
# Dublin Core 的规范 URI 以斜杠结尾，缺斜杠会落到另一个命名空间
NS_DC = "http://purl.org/dc/elements/1.1/"
RES_TYPE = "paintoppresets"


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


def write_bundle(bundle_path: str, kpp_files: list[tuple[str, bytes]]) -> str:
    """把 .kpp 文件打包为 .bundle。

    Parameters
    ----------
    bundle_path : 输出 .bundle 路径
    kpp_files : [(文件名, 文件字节)]，将放入 paintoppresets/ 目录
    """
    name = Path(bundle_path).stem
    entries: list[tuple[str, str]] = []
    for fname, data in kpp_files:
        entries.append((fname, hashlib.md5(data).hexdigest()))

    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/manifest.xml", _manifest_xml(entries))
        zf.writestr("meta.xml", _meta_xml(name))
        for fname, data in kpp_files:
            zf.writestr("paintoppresets/" + fname, data)
    return bundle_path
