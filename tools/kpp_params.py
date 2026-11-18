"""读取 .kpp 预设并抽出「参数级比对」关心的字段（任务 02）。

.kpp 不是 ZIP：它是 PNG（200×200 预览），预设 XML 存在 iTXt 块
（keyword="preset"，zlib 压缩，个别旧文件可能是 zTXt）里。
本模块只依赖标准库，供 tests/test_params_golden.py 与 tools 使用。
"""

from __future__ import annotations

import base64
import math
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

# ---------------------------------------------------------------------------
# 数值容差
# ---------------------------------------------------------------------------
# 统一容差 1e-3，理由：
# 1) spacing：PS Spcn 是整数百分比，映射为 Spcn/100，Krita XML 用 %g 输出
#    （最多 6 位有效数字），最大量化误差远小于 1e-3；
# 2) angle：度 → 弧度 → 度 往返只有 ~1e-13 的浮点误差，1e-3 度足够宽松；
# 3) scale：Dmtr/max(宽,高) 是两个整数的商，%g 有 6 位有效数字，1e-3 够；
# 4) scatter_value：jitter/400，jitter 是 0..1000 的整数，结果步长 0.0025，
#    因此 1e-3 足以区分「相邻合法值」而不会误判相邻取值。
# 布尔/枚举/字符串不走容差，一律严格相等（见 close）。
DEFAULT_TOL = 1e-3


def close(a, b, tol: float = DEFAULT_TOL) -> bool:
    """数值比较：浮点用容差，布尔/枚举/字符串用严格相等。

    容差口径见模块顶部 DEFAULT_TOL 的注释（统一 1e-3）。
    """
    # bool 是 int 的子类，必须先判：True 不能等于 1.0 这种「差不多」
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or (isinstance(a, bool) and isinstance(b, bool) and a == b)
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    both_number = isinstance(a, (int, float)) and isinstance(b, (int, float))
    if both_number:
        return abs(float(a) - float(b)) <= tol
    return a == b


# ---------------------------------------------------------------------------
# PNG 块解析：从 .kpp 里取出预设 XML
# ---------------------------------------------------------------------------

def _iter_png_chunks(data: bytes):
    """遍历 PNG 块，产出 (类型, 负载)。"""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("不是 PNG 文件（.kpp 必须是 PNG 容器）：" + repr(data[:8]))
    pos = 8
    total = len(data)
    while pos + 8 <= total:
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        yield ctype, payload


def _parse_itxt(payload: bytes) -> tuple[str, bytes]:
    """拆 iTXt 块 → (keyword, 解压后的文本字节)。"""
    keyword, rest = payload.split(b"\x00", 1)
    compression_flag = rest[0]
    # rest[1] 是 compression method，只支持 0（zlib）
    rest = rest[2:]
    _language, rest = rest.split(b"\x00", 1)
    _translated, text = rest.split(b"\x00", 1)
    if compression_flag == 1:
        text = zlib.decompress(text)
    return keyword.decode("latin1"), text


def _parse_ztxt(payload: bytes) -> tuple[str, bytes]:
    """拆 zTXt 块 → (keyword, 解压后的文本字节)。"""
    keyword, rest = payload.split(b"\x00", 1)
    # rest[0] 是 compression method（固定 0）
    return keyword.decode("latin1"), zlib.decompress(rest[1:])


def read_kpp_xml(path) -> str:
    """读取 .kpp，返回里面那份预设 XML 文本。

    优先 iTXt（Krita 5.x 实际写法），兼容 zTXt；找不到就明确报错，
    不要返回空字符串让上层误以为「参数都是默认值」。
    """
    for ctype, payload in _iter_png_chunks(Path(path).read_bytes()):
        if ctype == b"iTXt":
            keyword, text = _parse_itxt(payload)
            if keyword == "preset":
                return text.decode("utf-8")
        elif ctype == b"zTXt":
            keyword, text = _parse_ztxt(payload)
            if keyword == "preset":
                return text.decode("utf-8")
    raise ValueError("这个 .kpp 里没有 keyword=preset 的 iTXt/zTXt 块：" + str(path))


# ---------------------------------------------------------------------------
# 资源与笔尖尺寸
# ---------------------------------------------------------------------------

def _png_size(png: bytes) -> tuple[int, int]:
    """从 PNG 字节读 IHDR 里的 (宽, 高)。"""
    if not png.startswith(b"\x89PNG\r\n\x1a\n") or png[12:16] != b"IHDR":
        raise ValueError("内嵌资源不是合法 PNG")
    width, height = struct.unpack(">II", png[16:24])
    return width, height


def _embedded_resources(root: ET.Element) -> dict[str, bytes]:
    """取出 <resources><resource filename="..." type="...">base64</resource> 的字节。"""
    found: dict[str, bytes] = {}
    resources = root.find("resources")
    if resources is None:
        return found
    for res in resources.findall("resource"):
        filename = res.get("filename")
        if not filename:
            continue
        try:
            found[filename] = base64.b64decode((res.text or "").strip())
        except (ValueError, TypeError):
            continue
    return found


# ---------------------------------------------------------------------------
# 参数抽取
# ---------------------------------------------------------------------------

def _as_bool(text: str) -> bool:
    """Krita internal 参数的布尔写法。"""
    return (text or "").strip().lower() == "true"


def extract_params(xml: str) -> dict:
    """把预设 XML 抽成扁平字典，键名与 tests/golden/params_expected.json 一致。

    返回字段：name / brush_type / spacing / angle_deg / angle_rad / scale /
    size / scatter_enabled / scatter_value / scatter_use_curve / axis_x / axis_y。
    """
    root = ET.fromstring(xml)
    result: dict = {"name": root.get("name") or ""}

    params: dict[str, str] = {}
    for node in root.findall("param"):
        key = node.get("name")
        if key is not None:
            params[key] = node.text or ""

    brush_def = params.get("brush_definition", "")
    if not brush_def.strip():
        raise ValueError(f"预设 {result['name']!r} 缺少 brush_definition")
    brush = ET.fromstring(brush_def)
    brush_type = brush.get("type") or ""
    result["brush_type"] = brush_type
    result["spacing"] = float(brush.get("spacing") or 0.0)
    angle_rad = float(brush.get("angle") or 0.0)
    result["angle_rad"] = angle_rad
    result["angle_deg"] = math.degrees(angle_rad) % 360.0

    resources = _embedded_resources(root)
    if brush_type == "auto_brush":
        mask = brush.find("MaskGenerator")
        if mask is None:
            raise ValueError(f"auto_brush 预设 {result['name']!r} 缺少 MaskGenerator")
        result["scale"] = 1.0
        result["size"] = float(mask.get("diameter") or 0.0)
    else:
        scale = float(brush.get("scale") or 0.0)
        result["scale"] = scale
        # 采样笔尖：size = scale × 内嵌笔尖 PNG 的边长（正方形化后的 max(w,h)）
        filename = brush.get("filename") or ""
        if filename not in resources:
            raise ValueError(f"预设 {result['name']!r} 的笔尖资源 {filename!r} 未内嵌")
        width, height = _png_size(resources[filename])
        result["size"] = scale * max(width, height)

    result["scatter_enabled"] = _as_bool(params.get("PressureScatter", "false"))
    result["scatter_value"] = float(params.get("ScatterValue") or 0.0)
    result["scatter_use_curve"] = _as_bool(params.get("ScatterUseCurve", "false"))
    result["axis_x"] = _as_bool(params.get("Scattering/AxisX", "false"))
    result["axis_y"] = _as_bool(params.get("Scattering/AxisY", "true"))
    return result
