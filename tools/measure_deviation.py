# -*- coding: utf-8 -*-
"""任务 106：六参数（间距/缩放/尺寸/硬度/散布/角度）PS↔Krita 偏差测量入口。

本脚本只测量与记录，**不改任何映射代码、不改 tests/**、不改参数矩阵。
设计要点
--------
1. 同一支笔、同一画布、同一笔划口径：PS 用 Action Manager 改当前工具选项的
   brush 对象并落笔；Krita 用项目转换链（mapping + preset_xml + build_kpp）
   生成对应 .kpp，再走 tools/krita_render_runner.py 的 GUI 真实渲染路线。
   Krita 渲染全程串行，同一时刻只有一个 Krita 会话。
2. 六参数的取值点（任务书规定）：
   间距 10/50/100（%）、缩放 50/100/200（%）、尺寸 20/100/300（px）、
   硬度 0/50/100（%）、散布 0/100/300（%）、角度 0/45/90（°）。
   间距另做「方形采样笔尖 vs 非方形采样笔尖」对照，用来回答使用者提出的
   「间距偏差是否与笔尖补平方有关」这一待验证假设。
3. 判据（沿用项目口径，不许改）：
   - 像素类：差异比例（|Δalpha|>2 的像素 / 画布总像素）≤2% 且 max_delta≤2 算一致；
   - 几何类：相对误差 ≤2% 算一致；角度差按 2%×180°=3.6° 折算；
   - 绝对差 ≤2px 的点（如硬边过渡宽度）按「与 max_delta≤2 同量级」算一致。
4. 判像素只认 IDAT 哈希（PNG 的 iTXt/XMP 会变，文件 sha256 不可比）。
5. 输出 docs/数据/偏差/manifest.json：每张图登记侧（ps/krita）、参数、取值、
   文件、尺寸、idat_sha256、时间、复现命令，以及 PS/Krita 两侧的参数回读值。
   同样的输入/路径重复运行，manifest 除时间戳外确定（按固定键排序）。

命令行
------
    python tools/measure_deviation.py --all
    python tools/measure_deviation.py --param spacing --values 10,50,100
    python tools/measure_deviation.py --analyze                 # 只重算数字
    python tools/measure_deviation.py --probe                   # 只重跑 PS 字段探针
    python tools/measure_deviation.py --param size --skip-krita # 只出 PS 图（调试用）

注意
----
* 本脚本启动 Photoshop 前会检查是否已有实例：有则通过 `Photoshop.exe -r` 复用
  （不退出用户进程），没有则自己启动并在结束时退出；绝不并行开两个 PS。
* PS 侧只改「当前工具选项」的瞬时值，不保存文档、不保存笔刷预设；每次运行结束
  把计算笔刷的五个字段写回初始值。
* Krita 侧 .kpp 生成在 docs/数据/偏差/kpp/，渲染前由 runner 临时装进
  %APPDATA%/krita/paintoppresets 并在结束后删除、还原 kritarc 与资源缓存。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from brush_converter.abr import AbrFile                      # noqa: E402
from brush_converter.mapping import map_presets               # noqa: E402
from brush_converter.kpp.preset_xml import build_preset_xml   # noqa: E402
from brush_converter.kpp.kpp_writer import build_kpp, render_tip_preview  # noqa: E402
from brush_converter import convert as conv_mod               # noqa: E402
import imgdiff                                               # noqa: E402
import krita_render_runner as runner                          # noqa: E402

# ------------------------------------------------------------------ 常量

PS_EXE = Path(os.environ.get("BRUSHCONVERTER_PS", r"D:/program/PS2025/Adobe Photoshop 2025/Photoshop.exe"))
ABR_COMPUTED = REPO / "tests" / "定量测试专用.abr"
ABR_SAMPLED = REPO / "tests" / "测试用.abr"

OUT_DIR = REPO / "docs" / "数据" / "偏差"
PS_DIR = OUT_DIR / "ps"
KT_DIR = OUT_DIR / "krita"
KPP_DIR = OUT_DIR / "kpp"
WORK_DIR = OUT_DIR / "work"
MANIFEST = OUT_DIR / "manifest.json"

#: 计算笔刷（定量测试专用.abr 里唯一一支）：Dmtr=200 / Hrdn=100 / Rndn=100 / Spcn=1000
BRUSH_COMPUTED = "定量测试用"
#: 方形对照：采样笔尖 86x84（长宽比 1.02，几乎 1:1，补白只在上下各 1 行）
BRUSH_SQUARE = "Kyle 的终极粉彩派对"
#: 非方形对照：采样笔尖 183x143（长宽比 1.28，补白上下各 20 行）
BRUSH_NONSQUARE = "KYLE 额外厚实炭笔"

#: 任务书规定的六参数取值点
PARAM_VALUES = {
    "spacing": [10.0, 50.0, 100.0],
    "scale": [50.0, 100.0, 200.0],
    "size": [20.0, 100.0, 300.0],
    "hardness": [0.0, 50.0, 100.0],
    "scatter": [0.0, 100.0, 300.0],
    "angle": [0.0, 45.0, 90.0],
}

PARAM_ORDER = ["scale", "spacing", "size", "hardness", "scatter", "angle"]

#: 画布与笔划（PS 与 Krita 完全一致；Krita 插件落笔 y=高//2、x=(宽-stroke)//2 起）
CANVAS_LINE = (640, 320)      # 直线笔划：间距/尺寸
CANVAS_DAB = (400, 400)       # 单点：硬度/角度
CANVAS_SCATTER = (640, 560)   # 笔划 + 垂直散布余量
CANVAS_SIZE = (640, 400)      # 尺寸 300px 需要更高画布
STROKE_LINE = 480
STROKE_DAB = 1

DIFF_TOL = imgdiff.DEFAULT_TOL          # 2
RATIO_THRESHOLD = 0.02                  # 2%
GEOM_REL_THRESHOLD = 0.02               # 几何类 2%
ANGLE_TOL_DEG = 0.02 * 180.0            # 3.6°
ABS_PX_THRESHOLD = 2.0                  # 绝对差 2px（与 max_delta≤2 同量级）

#: exp 数据（缩放参数复用，见 docs/研究报告/缩放深度-归因实验.md）
EXP_PS = REPO / "tests" / "baselines" / "exp" / "ps"
EXP_KT = REPO / "tests" / "baselines" / "exp" / "krita"


# ------------------------------------------------------------------ 通用工具


def log(msg: str) -> None:
    print(msg, flush=True)


def fmt(v: float) -> str:
    """取值转文件名片段：10.0 → '10'，45.5 → '45.5'。"""
    return ("%g" % float(v))


def idat_sha256(path: Path) -> str:
    """只哈希 PNG 的 IDAT 块（判像素只认这个，文件 sha256 会被 iTXt/XMP 影响）。"""
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG：%s" % path)
    pos = 8
    digest = hashlib.sha256()
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        if ctype == b"IDAT":
            digest.update(payload)
        pos += 12 + length
        if ctype == b"IEND":
            break
    return digest.hexdigest()


def load_alpha(path: Path) -> np.ndarray:
    """读 PNG 的 Alpha 通道（uint8 二维）。"""
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.uint8)[:, :, 3]


def bbox_alpha(alpha: np.ndarray, thr: int = 2) -> tuple[int, int, int, int]:
    """返回 (x0, y0, x1, y1)，含端点；全空返回 (-1,-1,-1,-1)。"""
    mask = alpha > thr
    if not mask.any():
        return (-1, -1, -1, -1)
    cols = np.where(mask.any(axis=0))[0]
    rows = np.where(mask.any(axis=1))[0]
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])


def moving_average(x: np.ndarray, win: int) -> np.ndarray:
    win = max(1, int(win))
    if win <= 1 or win >= len(x):
        return np.full_like(x, x.mean())
    kernel = np.ones(win) / win
    return np.convolve(x, kernel, mode="same")


def fft_period(sig: np.ndarray, pmin: float, pmax: float) -> dict:
    """在 [pmin,pmax] 像素周期内找 FFT 主峰，返回 {period, conf}。

    conf = 峰值 / 该频段中位数；<2 视为没有明显周期。
    先去均值 + Hann 窗，避免端点不连续抬高低频。
    """
    n = len(sig)
    x = sig.astype(np.float64) - float(np.mean(sig))
    if n < 16 or not np.any(x):
        return {"period": None, "conf": 0.0}
    x = x * np.hanning(n)
    spec = np.abs(np.fft.rfft(x))
    kmin = max(1, int(math.floor(n / pmax)))
    kmax = min(len(spec) - 2, int(math.ceil(n / pmin)))
    if kmax <= kmin + 1:
        return {"period": None, "conf": 0.0}
    seg = spec[kmin:kmax + 1]
    i = int(np.argmax(seg)) + kmin
    if i <= 0 or i >= len(spec) - 1:
        return {"period": None, "conf": 0.0}
    y0, y1, y2 = (math.log(spec[i - 1] + 1e-9), math.log(spec[i] + 1e-9),
                  math.log(spec[i + 1] + 1e-9))
    den = y0 - 2 * y1 + y2
    delta = 0.5 * (y0 - y2) / den if abs(den) > 1e-12 else 0.0
    k = i + max(-0.5, min(0.5, delta))
    med = float(np.median(seg))
    conf = float(spec[i] / (med + 1e-9))
    return {"period": float(n / k), "conf": conf}


def acf_period(sig: np.ndarray, pmin: float, pmax: float) -> dict:
    """自相关法找周期（与 FFT 交叉验证）。"""
    x = sig.astype(np.float64) - float(np.mean(sig))
    if len(x) < 16 or not np.any(x):
        return {"period": None, "peak": 0.0}
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    if ac[0] <= 0:
        return {"period": None, "peak": 0.0}
    ac = ac / ac[0]
    lo = max(2, int(math.floor(pmin)))
    hi = min(len(ac) - 2, int(math.ceil(pmax)))
    if hi <= lo + 2:
        return {"period": None, "peak": 0.0}
    lags = np.arange(lo, hi + 1)
    vals = ac[lo:hi + 1]
    best = int(lags[int(np.argmax(vals))])
    if 0 < best < len(ac) - 1:
        y0, y1, y2 = ac[best - 1], ac[best], ac[best + 1]
        den = y0 - 2 * y1 + y2
        d = 0.5 * (y0 - y2) / den if abs(den) > 1e-12 else 0.0
        best = best + max(-0.5, min(0.5, d))
    return {"period": float(best), "peak": float(ac[int(round(best))] if best < len(ac) else 0.0)}


# ------------------------------------------------------------------ 测量计划


def _line_spec(canvas, stroke: int) -> dict:
    w, h = canvas
    return {"canvas": [w, h], "stroke": stroke, "x0": (w - stroke) // 2, "y0": h // 2,
            "x1": min(w - 1, (w - stroke) // 2 + stroke), "y1": h // 2,
            "mode": "line"}


def _dab_spec(canvas) -> dict:
    w, h = canvas
    return {"canvas": [w, h], "stroke": STROKE_DAB, "x0": w // 2, "y0": h // 2,
            "x1": w // 2 + 0.3, "y1": h // 2, "mode": "dab"}


def plan_param(param: str, values: list[float]) -> list[dict]:
    """给出一个参数的全部测量点（PS 字段 + KPP 字段 + 几何口径）。"""
    points: list[dict] = []
    if param == "scale":
        # 缩放复用 exp 已有 PS/Krita 图（同一支纹理笔刷、同一画布），不重新渲染；
        # 数据与归因见 docs/研究报告/缩放深度-归因实验.md。
        for v in values:
            stem = "multiply__d100_s%s_b0_c0" % fmt(v)
            points.append({
                "param": param, "group": "exp", "value": float(v),
                "ps_file": EXP_PS / ("exp__ps__" + stem + ".png"),
                "kt_file": EXP_KT / ("exp__krita__conv__" + stem + ".png"),
                "render": False,
                "geom": {"mode": "image"},
            })
        return points

    if param == "spacing":
        # 三组：计算笔刷（无补白）+ 采样方形对照 + 采样非方形对照
        for v in values:
            points.append({
                "param": param, "group": "computed", "value": float(v),
                "brush": "computed",
                "ps_file": PS_DIR / ("spacing__computed__v%s.png" % fmt(v)),
                "kt_file": KT_DIR / ("spacing__computed__v%s.png" % fmt(v)),
                "geom": {"mode": "line", "stroke": _line_spec(CANVAS_LINE, STROKE_LINE),
                         "nominal_diameter": 200.0},
                "ps_fields": {"diameter": 200.0, "hardness": 50.0, "spacing": float(v),
                              "roundness": 100.0, "angle": 0.0,
                              "scatter": None, "jitter": None},
                "kpp_fields": {"diameter": 200.0, "hardness": 50.0, "spacing": float(v),
                               "roundness": 100.0, "angle": 0.0,
                               "scatter": None, "jitter": None},
            })
        for group, brush_name in (("square", BRUSH_SQUARE), ("nonsquare", BRUSH_NONSQUARE)):
            for v in values:
                points.append({
                    "param": param, "group": group, "value": float(v),
                    "brush": brush_name,
                    "ps_file": PS_DIR / ("spacing__%s__v%s.png" % (group, fmt(v))),
                    "kt_file": KT_DIR / ("spacing__%s__v%s.png" % (group, fmt(v))),
                    "geom": {"mode": "line", "stroke": _line_spec(CANVAS_LINE, STROKE_LINE),
                             "nominal_diameter": 60.0 if group == "square" else 112.0},
                    "ps_fields": {"spacing": float(v), "angle": 0.0,
                                  "scatter": False, "jitter": None},
                    "kpp_fields": {"spacing": float(v), "angle": 0.0,
                                   "scatter": False, "jitter": None},
                })
        return points

    if param == "size":
        for v in values:
            points.append({
                "param": param, "group": "computed", "value": float(v),
                "brush": "computed",
                "ps_file": PS_DIR / ("size__computed__v%s.png" % fmt(v)),
                "kt_file": KT_DIR / ("size__computed__v%s.png" % fmt(v)),
                "geom": {"mode": "line", "stroke": _line_spec(CANVAS_SIZE, STROKE_LINE),
                         "nominal_diameter": float(v)},
                "ps_fields": {"diameter": float(v), "hardness": 100.0, "spacing": 100.0,
                              "roundness": 100.0, "angle": 0.0,
                              "scatter": None, "jitter": None},
                "kpp_fields": {"diameter": float(v), "hardness": 100.0, "spacing": 100.0,
                               "roundness": 100.0, "angle": 0.0,
                               "scatter": None, "jitter": None},
            })
        return points

    if param == "hardness":
        for v in values:
            points.append({
                "param": param, "group": "computed", "value": float(v),
                "brush": "computed",
                "ps_file": PS_DIR / ("hardness__computed__v%s.png" % fmt(v)),
                "kt_file": KT_DIR / ("hardness__computed__v%s.png" % fmt(v)),
                "geom": {"mode": "dab", "stroke": _dab_spec(CANVAS_DAB),
                         "nominal_diameter": 200.0},
                "ps_fields": {"diameter": 200.0, "hardness": float(v), "spacing": 500.0,
                              "roundness": 100.0, "angle": 0.0,
                              "scatter": None, "jitter": None},
                "kpp_fields": {"diameter": 200.0, "hardness": float(v), "spacing": 500.0,
                               "roundness": 100.0, "angle": 0.0,
                               "scatter": None, "jitter": None},
            })
        return points

    if param == "scatter":
        for v in values:
            points.append({
                "param": param, "group": "computed", "value": float(v),
                "brush": "computed",
                "ps_file": PS_DIR / ("scatter__computed__v%s.png" % fmt(v)),
                "kt_file": KT_DIR / ("scatter__computed__v%s.png" % fmt(v)),
                "geom": {"mode": "line", "stroke": _line_spec(CANVAS_SCATTER, STROKE_LINE),
                         "nominal_diameter": 100.0},
                "ps_fields": {"diameter": 100.0, "hardness": 100.0, "spacing": 50.0,
                              "roundness": 100.0, "angle": 0.0,
                              "scatter": True, "jitter": float(v)},
                "kpp_fields": {"diameter": 100.0, "hardness": 100.0, "spacing": 50.0,
                               "roundness": 100.0, "angle": 0.0,
                               "scatter": True, "jitter": float(v)},
            })
        return points

    if param == "angle":
        for v in values:
            points.append({
                "param": param, "group": "computed", "value": float(v),
                "brush": "computed",
                "ps_file": PS_DIR / ("angle__computed__v%s.png" % fmt(v)),
                "kt_file": KT_DIR / ("angle__computed__v%s.png" % fmt(v)),
                "geom": {"mode": "dab", "stroke": _dab_spec(CANVAS_DAB),
                         "nominal_diameter": 200.0},
                "ps_fields": {"diameter": 200.0, "hardness": 100.0, "spacing": 500.0,
                              "roundness": 30.0, "angle": float(v),
                              "scatter": None, "jitter": None},
                "kpp_fields": {"diameter": 200.0, "hardness": 100.0, "spacing": 500.0,
                               "roundness": 30.0, "angle": float(v),
                               "scatter": None, "jitter": None},
            })
        return points

    raise SystemExit("未知参数：%r" % param)


# ------------------------------------------------------------------ Krita 预设生成


_BASE_CACHE: dict[str, object] = {}


def base_preset(brush: str):
    """取基准预设（只读 .abr，不修改）。brush='computed' 或采样笔刷名。"""
    if brush in _BASE_CACHE:
        return _BASE_CACHE[brush]
    if brush == "computed":
        abr = AbrFile.parse(str(ABR_COMPUTED))
        presets = map_presets(abr)
        match = [p for p in presets if p.name == BRUSH_COMPUTED]
    else:
        abr = AbrFile.parse(str(ABR_SAMPLED))
        presets = map_presets(abr)
        match = [p for p in presets if p.name == brush]
    if not match:
        raise SystemExit("基准笔刷找不到：%r" % brush)
    _BASE_CACHE[brush] = match[0]
    return match[0]


def make_kpp(point: dict) -> tuple[Path, dict]:
    """按测量点生成 .kpp（用项目转换链的同一套 XML 生成器，只覆盖被测量的字段）。"""
    bp = copy.deepcopy(base_preset(point["brush"]))
    fields = dict(point["kpp_fields"])
    # 隔离测量：去掉与本次测量无关的动态（压感/旋转抖动/纹理/散布开关），
    # PS 侧用 useTipDynamics=false、useTexture=false 做同样隔离。
    bp.size_curve = None
    bp.opacity_curve = None
    bp.flow_curve = None
    bp.ratio_curve = None
    bp.rotation_sensor = None
    bp.rotation_jitter = 0.0
    bp.texture = None
    bp.scatter = bool(fields.get("scatter"))
    bp.scatter_pressure = False
    bp.scatter_both_axes = False
    bp.scatter_amount = float(fields["jitter"]) if fields.get("jitter") is not None else None

    if "diameter" in fields:
        bp.diameter = float(fields["diameter"])
    if "hardness" in fields:
        bp.hardness = float(fields["hardness"])
    if "roundness" in fields:
        bp.roundness = float(fields["roundness"])
    if "angle" in fields:
        bp.angle = float(fields["angle"])
    if "spacing" in fields:
        bp.spacing = float(fields["spacing"]) / 100.0

    tip_png = None
    tip_name = None
    if bp.is_computed:
        brush_def = conv_mod._computed_brush_def(bp)
        preview = render_tip_preview(
            conv_mod._circle_mask(bp.hardness if bp.hardness is not None else 100.0, bp.roundness))
    else:
        tip_png = conv_mod._png_bytes(conv_mod._square_tip(bp.tip_gray))
        tip_name = "bc106_" + hashlib.sha256(tip_png).hexdigest()[:10]
        brush_def = conv_mod._sampled_brush_def(tip_name, tip_png, bp)
        preview = render_tip_preview(bp.tip_gray)

    preset_name = "bc106 %s %s v%s" % (point["param"], point["group"], fmt(point["value"]))
    xml = build_preset_xml(
        name=preset_name,
        brush_definition=brush_def,
        tip_png=tip_png,
        tip_name=tip_name,
        size_curve=None,
        opacity_curve=None,
        flow_curve=None,
        ratio_curve=None,
        rotation_sensor=None,
        rotation_jitter=0.0,
        scatter=bp.scatter,
        scatter_pressure=False,
        scatter_both_axes=False,
        scatter_amount=bp.scatter_amount,
        texture=None,
    )
    data = build_kpp(xml, preview)
    KPP_DIR.mkdir(parents=True, exist_ok=True)
    path = KPP_DIR / ("%s__%s__v%s.kpp" % (point["param"], point["group"], fmt(point["value"])))
    path.write_bytes(data)

    # 回读 KPP 里的关键字段（写进 manifest，作为「Krita 侧实际用的值」证据）
    def _grab(pattern: str) -> str:
        import re
        m = re.search(pattern, xml)
        return m.group(1) if m else ""

    readback = {
        "spacing": _grab(r'spacing="([^"]+)"'),
        "angle": _grab(r'angle="([^"]+)"'),
        "diameter": _grab(r'diameter="([^"]+)"'),
        "ratio": _grab(r'ratio="([^"]+)"'),
        "hfade": _grab(r'hfade="([^"]+)"'),
        "ScatterValue": _grab(r'name="ScatterValue" type="internal">([^<]*)<'),
        "PressureScatter": _grab(r'name="PressureScatter" type="internal">([^<]*)<'),
        "AxisX": _grab(r'name="Scattering/AxisX" type="internal">([^<]*)<'),
        "AxisY": _grab(r'name="Scattering/AxisY" type="internal">([^<]*)<'),
        "xml_bytes": str(len(xml)),
    }
    return path, readback


# ------------------------------------------------------------------ Photoshop 侧


PS_JSX = r"""
// 任务 106 临时 JSX：由 tools/measure_deviation.py 生成到系统临时目录。
// 读 job.json → 逐条设置 currentToolOptions 的 brush 对象 → 落笔 → 导出 PNG。
// 结果写 done.tsv（制表符分隔，ExtendScript 没有 JSON.stringify）。
#target photoshop
(function () {
    // 工作目录：优先读 %TEMP%/bc_measure_106_work.txt（复用已在运行的 PS 时，
    // 新进程的环境变量不会进入旧实例；标记文件对任何实例都可读），再退回环境变量。
    var WORK = "";
    try {
        var wf = new File(Folder.temp.fsName + "/bc_measure_106_work.txt");
        if (wf.exists) { wf.encoding = "UTF-8"; wf.open("r"); WORK = wf.read(); wf.close(); }
    } catch (eW) {}
    if (!WORK) { WORK = $.getenv("BC_MEASURE_WORK"); }
    var jobPath = WORK + "/job.json";
    var donePath = WORK + "/done.tsv";
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function appDesc() {
        var r = new ActionReference(); r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
        return executeActionGet(r);
    }
    function getOpts() { return appDesc().getObjectValue(sid("currentToolOptions")); }
    function setOpts(o) {
        var d = new ActionDescriptor(); var r = new ActionReference();
        r.putClass(sid("paintbrushTool"));
        d.putReference(cid("null"), r);
        d.putObject(cid("T   "), sid("currentToolOptions"), o);
        executeAction(cid("setd"), d, DialogModes.NO);
    }
    var logFile = new File(WORK + "/ps.log");
    logFile.encoding = "UTF-8";
    logFile.open("w");
    function L(s) { logFile.writeln(String(s)); }
    function numOf(o, name) {
        try { return String(o.getObjectValue(sid("brush")).getUnitDoubleValue(sid(name))); }
        catch (e) { return "ABSENT"; }
    }
    function jitterOf(o) {
        try { return String(o.getObjectValue(sid("scatterDynamics")).getUnitDoubleValue(sid("jitter"))); }
        catch (e) { return "ABSENT"; }
    }
    function boolOf(o, name) {
        try { return String(o.getBoolean(sid(name))); } catch (e) { return "ABSENT"; }
    }
    function selectBrush(name) {
        var d = new ActionDescriptor(); var r = new ActionReference();
        r.putName(cid("Brsh"), name);
        d.putReference(cid("null"), r);
        executeAction(cid("slct"), d, DialogModes.NO);
    }
    var job = null, doc = null, brushName = "";
    var results = [];
    try {
        app.displayDialogs = DialogModes.NO;
        var jf = new File(jobPath); jf.encoding = "UTF-8"; jf.open("r");
        job = eval("(" + jf.read() + ")"); jf.close();
        L("job loaded, count=" + job.jobs.length + ", quit=" + job.quit);

        // 画笔工具 + 载入 ABR + 按名选笔
        var d0 = new ActionDescriptor(); var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool"));
        d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        if (job.abr_path) { app.load(new File(job.abr_path)); L("LOAD ABR OK " + job.abr_path); }
        var col = new SolidColor(); col.rgb.red = 0; col.rgb.green = 0; col.rgb.blue = 0;
        app.foregroundColor = col;

        function applyOne(j) {
            if (brushName !== j.brush_name) {
                selectBrush(j.brush_name);
                brushName = j.brush_name;
                L("SELECT " + brushName);
            }
            var f = j.fields;
            var o = getOpts();
            var b = o.getObjectValue(sid("brush"));
            if (f.diameter !== null) { b.putUnitDouble(sid("diameter"), cid("#Pxl"), f.diameter); }
            if (f.hardness !== null) { b.putUnitDouble(sid("hardness"), cid("#Prc"), f.hardness); }
            if (f.spacing !== null) { b.putUnitDouble(sid("spacing"), cid("#Prc"), f.spacing); }
            if (f.roundness !== null) { b.putUnitDouble(sid("roundness"), cid("#Prc"), f.roundness); }
            if (f.angle !== null) { b.putUnitDouble(sid("angle"), cid("#Ang"), f.angle); }
            o.putObject(sid("brush"), sid("brush"), b);
            o.putBoolean(sid("useTexture"), false);
            o.putBoolean(sid("useTipDynamics"), false);
            o.putBoolean(sid("smoothing"), false);
            o.putInteger(sid("opacity"), 100);
            o.putInteger(sid("flow"), 100);
            if (f.scatter) {
                o.putBoolean(sid("useScatter"), true);
                o.putBoolean(sid("bothAxes"), false);
            } else {
                o.putBoolean(sid("useScatter"), false);
            }
            setOpts(o);
            if (f.scatter) {
                // 散布量在 useScatter=true 之后才出现，二阶段写
                var o2 = getOpts();
                var sd = o2.getObjectValue(sid("scatterDynamics"));
                sd.putUnitDouble(sid("jitter"), cid("#Prc"), f.jitter);
                o2.putObject(sid("scatterDynamics"), sid("scatterDynamics"), sd);
                setOpts(o2);
            }
            var o3 = getOpts();
            return {
                diameter: numOf(o3, "diameter"), hardness: numOf(o3, "hardness"),
                spacing: numOf(o3, "spacing"), roundness: numOf(o3, "roundness"),
                angle: numOf(o3, "angle"), jitter: jitterOf(o3),
                useScatter: boolOf(o3, "useScatter"), useTexture: boolOf(o3, "useTexture"),
                useTipDynamics: boolOf(o3, "useTipDynamics")
            };
        }
        function paint(st) {
            var pts = [];
            var p1 = new PathPointInfo(); p1.kind = PointKind.CORNERPOINT;
            p1.anchor = [st.x0, st.y0]; p1.leftDirection = p1.anchor; p1.rightDirection = p1.anchor;
            pts.push(p1);
            var p2 = new PathPointInfo(); p2.kind = PointKind.CORNERPOINT;
            p2.anchor = [st.x1, st.y1]; p2.leftDirection = p2.anchor; p2.rightDirection = p2.anchor;
            pts.push(p2);
            var sub = new SubPathInfo(); sub.closed = false;
            sub.operation = ShapeOperation.SHAPEADD; sub.entireSubPath = pts;
            var pi = doc.pathItems.add("bc_line", [sub]);
            pi.strokePath(ToolType.BRUSH);
        }
        function exportPng(path) {
            var po = new PNGSaveOptions();
            try { po.transparency = true; } catch (e) {}
            doc.saveAs(new File(path), po, true, Extension.LOWERCASE);
        }

        for (var i = 0; i < job.jobs.length; i++) {
            var j = job.jobs[i];
            var res = { file: j.file, ok: false, error: "", diameter: "", hardness: "",
                        spacing: "", roundness: "", angle: "", jitter: "", useScatter: "",
                        useTexture: "", useTipDynamics: "", brush: j.brush_name };
            try {
                if (doc) { try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e1) {} doc = null; }
                doc = app.documents.add(j.canvas[0], j.canvas[1], 72, "bc106_" + i,
                                        NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
                var rb = applyOne(j);
                res.diameter = rb.diameter; res.hardness = rb.hardness;
                res.spacing = rb.spacing; res.roundness = rb.roundness; res.angle = rb.angle;
                res.jitter = rb.jitter; res.useScatter = rb.useScatter;
                res.useTexture = rb.useTexture; res.useTipDynamics = rb.useTipDynamics;
                paint(j.stroke);
                exportPng(j.out);
                res.ok = true;
                L("OK " + j.file + " spacing=" + rb.spacing + " diameter=" + rb.diameter +
                  " hardness=" + rb.hardness + " roundness=" + rb.roundness +
                  " angle=" + rb.angle + " jitter=" + rb.jitter +
                  " useScatter=" + rb.useScatter);
            } catch (e) {
                res.error = e.message + " line " + e.line;
                L("FAIL " + j.file + " : " + res.error.replace(/\n/g, " "));
            }
            results.push(res);
        }
    } catch (e) {
        L("FATAL " + e.message + " line " + e.line);
        results.push({ file: "", ok: false, error: "FATAL " + e.message });
    } finally {
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try {
            var out = new File(donePath);
            out.encoding = "UTF-8";
            out.open("w");
            out.writeln("ps_version\t" + app.version);
            var keys = ["file", "ok", "error", "brush", "diameter", "hardness", "spacing",
                        "roundness", "angle", "jitter", "useScatter", "useTexture", "useTipDynamics"];
            for (var k = 0; k < results.length; k++) {
                var r = results[k];
                var row = [];
                for (var m = 0; m < keys.length; m++) {
                    var val = String(r[keys[m]] === undefined ? "" : r[keys[m]]);
                    row.push(val.replace(/[\t\r\n]/g, " ").replace(/"/g, "'"));
                }
                out.writeln(row.join("\t"));
            }
            out.close();
        } catch (e3) { L("WRITE_DONE_FAIL " + e3.message); }
        logFile.close();
        if (job && job.quit) { try { app.quit(); } catch (e4) {} }
    }
})();
"""


def _tasklist_photoshop() -> list[str]:
    """列出 Photoshop.exe 进程（直接调 tasklist，不经 shell，避开路径转换）。"""
    try:
        proc = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Photoshop.exe", "/FO", "CSV", "/NH"],
                              capture_output=True, timeout=30)
    except Exception:
        return []
    text = (proc.stdout or b"").decode("utf-8", "replace")
    return [line for line in text.splitlines() if line.upper().startswith(b"\"PHOTOSHOP.EXE\"".decode("ascii").upper())]


def photoshop_running() -> bool:
    return bool(_tasklist_photoshop())


def kill_photoshop() -> None:
    """只在本脚本自己启动 PS 的路径上调用（启动前已确认没有别的实例）。"""
    try:
        subprocess.run(["taskkill", "/F", "/IM", "Photoshop.exe"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except Exception:
        pass


def ps_job(points: list[dict]) -> list[dict]:
    """把测量点转成 PS 侧 job 列表（按参数分组）。"""
    jobs = []
    for p in points:
        st = dict(p["geom"]["stroke"])
        fields = {k: p["ps_fields"].get(k) for k in
                  ("diameter", "hardness", "spacing", "roundness", "angle")}
        fields["scatter"] = bool(p["ps_fields"].get("scatter"))
        fields["jitter"] = p["ps_fields"].get("jitter")
        brush_name = BRUSH_COMPUTED if p["brush"] == "computed" else p["brush"]
        jobs.append({
            "file": p["ps_file"].name,
            "out": str(p["ps_file"]),
            "canvas": st["canvas"],
            "stroke": st,
            "brush_name": brush_name,
            "abr_path": str(ABR_COMPUTED if p["brush"] == "computed" else ABR_SAMPLED),
            "fields": fields,
        })
    return jobs


def ps_work_dir() -> Path:
    """PS 侧通信目录（job.json / done.tsv / ps.log）。

    必须用纯 ASCII 路径：ExtendScript 的 File() 在中文路径上不可靠；
    运行结束后把这三个文件原样复制到 docs/数据/偏差/work/ 作为证据。
    """
    path = Path(tempfile.gettempdir()) / "bc_measure_106_work"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_ps(points: list[dict], timeout: float = 900.0) -> dict:
    """一个 PS 会话跑完全部 PS 侧出图；返回 {file: 回读行}。"""
    if not PS_EXE.is_file():
        raise SystemExit("Photoshop 不存在：%s" % PS_EXE)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    for p in points:
        p["ps_file"].parent.mkdir(parents=True, exist_ok=True)
    jobs = ps_job(points)
    preexisting = photoshop_running()
    job = {
        "quit": not preexisting,
        "abr_path": str(ABR_COMPUTED),
        "jobs": jobs,
    }
    work = ps_work_dir()
    (Path(tempfile.gettempdir()) / "bc_measure_106_work.txt").write_text(
        str(work), encoding="utf-8")
    job_path = work / "job.json"
    done_path = work / "done.tsv"
    log_path = work / "ps.log"
    for path in (done_path, log_path):
        if path.exists():
            path.unlink()
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")

    jsx_path = Path(tempfile.gettempdir()) / "bc_measure_106.jsx"
    # 带 BOM 写 UTF-8：ExtendScript 只有见到 BOM 才按 UTF-8 解析源码，
    # 否则中文注释/中文标识按 ANSI 读，可能整段脚本解析失败。
    jsx_path.write_text(PS_JSX, encoding="utf-8-sig")

    log("PS: 已有实例=%s，开始出图 %d 张（串行，一个会话）" % (preexisting, len(jobs)))
    env = os.environ.copy()
    env["BC_MEASURE_WORK"] = str(work)
    env["BRUSHCONVERTER_REPO"] = str(REPO)
    started = time.time()
    proc = subprocess.Popen([str(PS_EXE), "-r", str(jsx_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    while time.time() - started < timeout:
        if done_path.exists() and done_path.stat().st_size > 0:
            time.sleep(1.0)
            break
        # 复用已有实例时，`-r` 的启动进程会立刻退出（脚本被转发到运行中的 PS），
        # 不能把它当成失败；只有「自己启动的实例」提前退出才是异常。
        if proc.poll() is not None and not preexisting:
            time.sleep(2.0)
            if done_path.exists() and done_path.stat().st_size > 0:
                break
            raise RuntimeError("Photoshop 提前退出且未写 done.tsv，见 %s" % log_path)
        time.sleep(1.0)
    else:
        raise TimeoutError("等待 %ss 仍未完成 PS 出图，见 %s" % (timeout, log_path))

    rows: dict[str, dict] = {}
    ps_version = ""
    text = done_path.read_text(encoding="utf-8", errors="replace")
    keys = ["file", "ok", "error", "brush", "diameter", "hardness", "spacing",
            "roundness", "angle", "jitter", "useScatter", "useTexture", "useTipDynamics"]
    for line in text.splitlines():
        parts = line.split("\t")
        if parts[0] == "ps_version" and len(parts) > 1:
            ps_version = parts[1]
            continue
        if len(parts) < len(keys):
            continue
        rows[parts[0]] = dict(zip(keys, parts))

    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    if not preexisting and photoshop_running():
        # 只清理本脚本启动的实例；启动前已确认没有别的 PS
        kill_photoshop()
        time.sleep(1.0)
    # 把通信文件原样留档到仓库（job/done/日志）
    for src in (job_path, done_path, log_path):
        if src.exists():
            (WORK_DIR / src.name).write_bytes(src.read_bytes())
    elapsed = time.time() - started
    log("PS: 完成 %d/%d 张，用时 %.1fs，版本 %s" % (sum(1 for r in rows.values() if r["ok"] == "true"),
                                                len(jobs), elapsed, ps_version))
    return {"rows": rows, "ps_version": ps_version, "elapsed_s": round(elapsed, 1),
            "preexisting": preexisting, "log": str(log_path)}


# ------------------------------------------------------------------ Krita 侧


def render_krita(points: list[dict], force: bool = False) -> dict:
    """串行渲染全部 Krita 侧图（同一时刻一个 Krita）。"""
    if not runner.gui_available():
        raise SystemExit("Krita GUI 渲染路线不可用（缺 krita.com 或插件）：%s" % runner.PLUGIN_SRC)
    info: dict[str, dict] = {}
    started = time.time()
    for idx, p in enumerate(points, 1):
        kpp_path, readback = make_kpp(p)
        p["kpp_path"] = str(kpp_path)
        p["kpp_readback"] = readback
        out = p["kt_file"]
        out.parent.mkdir(parents=True, exist_ok=True)
        geom = p["geom"]["stroke"]
        size = (geom["canvas"][0], geom["canvas"][1])
        stroke = int(geom["stroke"])
        if out.exists() and not force:
            info[str(out)] = {"ok": True, "skipped": True, "elapsed_s": 0.0}
            log("Krita [%d/%d] 已存在，跳过 %s" % (idx, len(points), out.name))
            continue
        log("Krita [%d/%d] 渲染 %s（%dx%d, stroke=%d）" % (
            idx, len(points), out.name, size[0], size[1], stroke))
        res = runner.render_one(str(kpp_path), str(out), size=size, stroke=stroke,
                                pressure=1.0, timeout=90.0)
        info[str(out)] = res
        if not res.get("ok"):
            raise RuntimeError("Krita 渲染失败：%s；原因：%s；日志尾部：%s" % (
                out.name, res.get("reason"), (res.get("log_tail") or "")[-800:]))
        log("Krita [%d/%d] 完成 %.1fs alpha_nonzero=%s" % (
            idx, len(points), res.get("elapsed_s", 0.0), res.get("alpha_nonzero")))
    log("Krita: 全部完成，用时 %.1fs" % (time.time() - started))
    return {"renders": info, "elapsed_s": round(time.time() - started, 1)}


# ------------------------------------------------------------------ 分析


def metrics_alpha(a: np.ndarray, b: np.ndarray) -> dict:
    """像素口径：差异比例（|Δ|>2 的像素/画布总像素）+ max_delta + mean|Δ|。"""
    if a.shape != b.shape:
        raise ValueError("尺寸不一致：%s vs %s" % (a.shape, b.shape))
    delta = np.abs(a.astype(np.int16) - b.astype(np.int16))
    total = int(delta.size)
    diff_pixels = int(np.count_nonzero(delta > DIFF_TOL))
    return {
        "diff_pixels": diff_pixels,
        "diff_ratio": diff_pixels / total,
        "max_delta": int(delta.max()),
        "mean_abs_delta": float(delta.mean()),
        "pass": bool(diff_pixels / total <= RATIO_THRESHOLD and int(delta.max()) <= DIFF_TOL),
    }


def column_profile(alpha: np.ndarray, y0: int, y1: int) -> np.ndarray:
    """笔划区域内按列求 Alpha 质量（沿 x 的 1D 信号）。"""
    band = alpha[max(0, y0):y1 + 1, :].astype(np.float64)
    return band.sum(axis=0)


def row_profile(alpha: np.ndarray, x0: int, x1: int) -> np.ndarray:
    band = alpha[:, max(0, x0):x1 + 1].astype(np.float64)
    return band.sum(axis=1)


def _moving_avg(x: np.ndarray, win: int) -> np.ndarray:
    win = max(1, int(win))
    if win <= 1 or win >= len(x):
        return np.full_like(x, x.mean())
    return np.convolve(x, np.ones(win) / win, mode="same")


def _scallop_step(sig: np.ndarray, nominal_step: float) -> dict | None:
    """扇贝极值法：平滑后找主极大，取相邻主极大间距的中位数。

    窗口取 0.2×标称步长：把「台阶式量化条纹」并成每个笔迹一个主极大；
    过高阈值 0.25×幅度避免把纹理噪声当笔迹。
    """
    w = max(3, int(round(0.2 * nominal_step)))
    s = _moving_avg(sig, w)
    n = len(s)
    lo = max(2, int(round(0.5 * nominal_step)))
    hi = n - max(2, int(round(0.5 * nominal_step)))
    if hi - lo < 2 * nominal_step:
        return None
    seg = s[lo:hi]
    med = float(np.median(seg))
    top = float(seg.max())
    if top - med <= 1e-9:
        return None
    thr = med + 0.25 * (top - med)
    peaks = [(i + lo, float(seg[i])) for i in range(1, len(seg) - 1)
             if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] >= thr]
    merged: list[tuple[int, float]] = []
    for x, v in peaks:
        if merged and x - merged[-1][0] < 0.5 * nominal_step:
            if v > merged[-1][1]:
                merged[-1] = (x, v)
        else:
            merged.append((x, v))
    if len(merged) < 3:
        return None
    diffs = np.diff([m[0] for m in merged])
    m = float(np.median(diffs))
    diffs = [float(d) for d in diffs if d <= 2.0 * m]
    if len(diffs) < 2:
        return None
    return {"step": float(np.median(diffs)), "n_peaks": len(merged),
            "diffs": [int(d) for d in diffs[:12]]}


def _dft_scan(sig: np.ndarray, pmin: float, pmax: float, nfreq: int = 400) -> dict | None:
    """Hann 窗 + 细网格直接算 |DFT|（不靠 FFT 峰值插值，避开短信号插值偏差）。"""
    x = np.asarray(sig, dtype=np.float64)
    x = x - x.mean()
    if not np.any(x):
        return None
    w = x * np.hanning(len(x))
    t = np.arange(len(w))
    freqs = np.linspace(1.0 / pmax, 1.0 / pmin, nfreq)
    mags = np.empty(nfreq)
    for i, f in enumerate(freqs):
        mags[i] = math.hypot(float(np.dot(w, np.cos(2 * math.pi * f * t))),
                             float(np.dot(w, np.sin(2 * math.pi * f * t))))
    i = int(np.argmax(mags))
    if i == 0 or i == nfreq - 1:
        return None
    y0, y1, y2 = mags[i - 1], mags[i], mags[i + 1]
    den = y0 - 2 * y1 + y2
    d = 0.5 * (y0 - y2) / den if abs(den) > 1e-12 else 0.0
    f = freqs[i] + max(-1.0, min(1.0, d)) * (freqs[1] - freqs[0])
    return {"step": float(1.0 / f), "conf": float(mags[i] / (np.median(mags) + 1e-9))}


def measure_step(alpha: np.ndarray, diameter: float, value: float) -> dict:
    """测相邻笔迹中心距（步长）：扇贝极值 + 细网格 DFT，两路多信号投票。

    为什么换掉「FFT 主峰 + 自相关」：实测（18 张间距图）发现
    (1) 原搜索窗 [0.25D,2.2D] 在 10% 间距（步长 0.1D）时下界把真周期挡在外面；
    (2) 未去趋势的 FFT 主峰被笔划两端的包络（周期≈划长）抢走；
    (3) 平滑包络（计算笔刷）的自相关在 1/2、1/4 周期上有同级假峰。
    现口径：搜索窗 [0.5,2.5]×标称步长（能发现 ±2 倍偏差，同时排除包络），
    候选来自 mass/cnt8/cnt64/cnt160 四个信号的「扇贝极值」与「DFT 扫描」共 9 路，
    按 5% 聚类取票数最多的簇的中位数；少于 2 票 = 不可判读（step_px=None）。
    """
    x0, y0, x1, y1 = bbox_alpha(alpha, thr=2)
    if x0 < 0:
        return {"step_px": None, "note": "空图"}
    nominal_step = value / 100.0 * float(diameter)
    if nominal_step <= 0:
        return {"step_px": None, "note": "标称步长为 0"}
    band = alpha[y0:y1 + 1, x0:x1 + 1].astype(np.float64)
    sigs = {
        "mass": band.sum(axis=0),
        "cnt8": (band > 8).sum(axis=0).astype(np.float64),
        "cnt64": (band > 64).sum(axis=0).astype(np.float64),
        "cnt160": (band > 160).sum(axis=0).astype(np.float64),
    }
    cands = []
    ripples = {}
    for name, sig in sigs.items():
        # 只看笔划中段（两端各去 1/4）：圆头笔划两端本来就没有调制，
        # 用全长算调制幅度会被「起止端的包络」抬成假高值。
        q = max(2, len(sig) // 4)
        core = sig[q:len(sig) - q] if len(sig) - 2 * q > 8 else sig
        base = float(np.median(core)) if len(core) else 0.0
        ripples[name] = 0.0 if base <= 0 else float(
            (np.percentile(core, 95) - np.percentile(core, 5)) / base)
    for name, sig in sigs.items():
        r = _scallop_step(sig, nominal_step)
        if r:
            cands.append({"m": "scallop/" + name, "step": r["step"], "w": float(r["n_peaks"]),
                          "sig": name})
    for name, sig in sigs.items():
        for tag, s in (("raw", sig),
                       ("hp", sig - _moving_avg(sig, max(3, int(round(2.5 * nominal_step)))))):
            r = _dft_scan(s, 0.5 * nominal_step, 2.5 * nominal_step)
            if r:
                cands.append({"m": "dft/%s/%s" % (name, tag), "step": r["step"],
                              "w": min(r["conf"], 20.0), "sig": name})
    clusters: list[dict] = []
    for c in sorted(cands, key=lambda z: z["step"]):
        for cl in clusters:
            if abs(c["step"] - cl["center"]) <= 0.05 * cl["center"]:
                cl["items"].append(c)
                cl["center"] = float(np.median([t["step"] for t in cl["items"]]))
                break
        else:
            clusters.append({"center": c["step"], "items": [c]})
    best = None
    if clusters:
        best = max(clusters, key=lambda cl: (len(cl["items"]),
                                             sum(t["w"] for t in cl["items"])))
    vals = [t["step"] for t in best["items"]] if best else []
    out = {
        "step_px": None,
        "nominal_step_px": nominal_step,
        "band_height": int(y1 - y0 + 1),
        "stroke_length": int(x1 - x0 + 1),
        "profile_len": int(len(sigs["mass"])),
        "votes": len(vals),
        "candidates": len(cands),
        "candidate_steps": [round(c["step"], 2) for c in cands],
        "candidate_methods": [c["m"] for c in cands],
        "ripple_pct": {k: round(v * 100, 2) for k, v in ripples.items()},
    }
    # 调制幅度门：若投票簇里没有任何一路信号的轮廓调制 ≥0.5%，
    # 说明图上根本没有「笔迹扇贝」，周期只能来自包络伪峰 → 判不可读。
    # （本机实例：Krita 计算笔刷 10% 间距，cnt8 恒为 198，DFT 却给出 48.2。）
    max_ripple = max((ripples.get(c["sig"], 0.0) for c in best["items"]), default=0.0) if best else 0.0
    if len(vals) >= 2 and max_ripple >= 0.005:
        out["step_px"] = round(float(np.median(vals)), 2)
        out["spread_pct"] = round(100 * (max(vals) - min(vals)) / float(np.median(vals)), 2)
        out["cluster_methods"] = [t["m"] for t in best["items"]]
    elif len(vals) >= 2:
        out["note"] = "不可判读：最大轮廓调制 %.2f%% < 0.5%%，周期候选来自包络伪峰" % (max_ripple * 100)
    else:
        out["note"] = "不可判读：周期候选不足 2 票（信号无稳定周期性）"
    return out


def measure_footprint(alpha: np.ndarray, step_px: float | None) -> dict:
    """笔尖足迹：笔划包围盒高（=笔尖渲染高）+ 首个笔迹的半宽估计。

    笔迹足迹宽用「首峰位置 - 包围盒左缘」×2 估计（假设笔尖左右近似对称）；
    仅供「中心距 ÷ 笔尖宽」这条对照口径使用，误差在报告里注明。
    """
    x0, y0, x1, y1 = bbox_alpha(alpha, thr=2)
    if x0 < 0:
        return {"height": 0, "width_est": None}
    h = (alpha[y0:y1 + 1, :] > 8).sum(axis=0).astype(np.float64)
    out = {"height": int(y1 - y0 + 1), "width_est": None}
    if step_px:
        xs = x0
        xe = min(x1, x0 + int(round(1.2 * step_px)))
        seg = h[xs:xe + 1]
        if len(seg) >= 3:
            peak = int(np.argmax(seg)) + xs
            out["width_est"] = float(2 * (peak - x0))
            out["first_peak_x"] = peak
    return out


def _col_width50(col: np.ndarray, thr: float = 127.5) -> float | None:
    """单列 50% 阈值宽度（含上下边界亚像素插值）；该列无达标像素返回 None。"""
    col = col.astype(np.float64)
    idx = np.where(col >= thr)[0]
    if len(idx) == 0:
        return None
    runs = np.split(idx, np.where(np.diff(idx) > 1)[0] + 1)
    run = max(runs, key=len)
    top, bot = int(run[0]), int(run[-1])
    w = float(bot - top + 1)
    if top > 0:
        v_in, v_out = col[top], col[top - 1]
        if v_in != v_out:
            w -= (v_in - thr) / (v_in - v_out)
    if bot < len(col) - 1:
        v_in, v_out = col[bot], col[bot + 1]
        if v_in != v_out:
            w -= (v_in - thr) / (v_in - v_out)
    return float(w)


def measure_width(alpha: np.ndarray) -> dict:
    """笔划宽：逐列 50% 阈值宽度，取「顶 10% 列」的中位数（抗笔迹间隙）。

    旧口径只看笔划正中一列：间距=100% 时中列可能正好落在相邻笔迹的切点上
    （本机 Krita size=20 实测中列 alpha 全 0，宽度被记成 0）。改为全列统计后，
    取到的是笔迹最宽处（=笔尖渲染直径），对连续线与断续线都成立。
    """
    x0, y0, x1, y1 = bbox_alpha(alpha, thr=2)
    if x0 < 0:
        return {"width50": None, "bbox_height": 0}
    widths = []
    for cx in range(x0, x1 + 1):
        w = _col_width50(alpha[:, cx])
        if w is not None:
            widths.append(w)
    if not widths:
        return {"width50": None, "bbox_height": int(y1 - y0 + 1)}
    arr = np.sort(np.asarray(widths, dtype=np.float64))
    k = max(1, int(round(0.10 * len(arr))))
    top10 = arr[-k:]
    near = arr[arr >= 0.95 * arr[-1]]
    return {"width50": float(np.median(near)), "width50_max": float(arr[-1]),
            "width50_top10": float(np.median(top10)),
            "width50_median_all": float(np.median(arr)),
            "n_columns": int(len(arr)), "bbox_height": int(y1 - y0 + 1),
            "thr": 127.5}


def measure_edge(alpha: np.ndarray) -> dict:
    """单点：10%→90% 过渡宽度（沿过质心的水平行，左右两侧取平均）。"""
    x0, y0, x1, y1 = bbox_alpha(alpha, thr=2)
    if x0 < 0:
        return {"edge_width": None}
    rows = np.arange(alpha.shape[0]).astype(np.float64)
    mass = alpha.astype(np.float64).sum(axis=1)
    cy = int(np.argmax(mass))
    row = alpha[cy, :].astype(np.float64)
    peak = float(row.max())
    if peak <= 0:
        return {"edge_width": None}
    hi, lo = 0.9 * peak, 0.1 * peak

    def cross(x_from: int, x_to: int, level: float):
        """从 x_from 走向 x_to，找首个跌破 level 的位置（线性插值）。

        扫描上界要越过包围盒边界 1–2 像素：包围盒用 alpha>2 定义，
        硬边笔尖的 90%/10% 落差正好落在 x1+1 处（旧实现少扫一格 → 恒返回 None）。
        """
        step = 1 if x_to > x_from else -1
        prev = row[x_from]
        last = len(row) - 2 if step > 0 else 1
        x_end = min(x_to + 2, last) if step > 0 else max(x_to - 2, last)
        for x in range(x_from, x_end, step):
            cur = row[x + step]
            if (prev >= level) and (cur < level):
                frac = (prev - level) / (prev - cur) if prev != cur else 0.0
                return x + step * frac
            prev = cur
        return None

    c = (x0 + x1) // 2
    right10 = cross(c, x1, lo)
    right90 = cross(c, x1, hi)
    left10 = cross(c, x0, lo)
    left90 = cross(c, x0, hi)
    widths = []
    if right10 is not None and right90 is not None:
        widths.append(abs(right10 - right90))
    if left10 is not None and left90 is not None:
        widths.append(abs(left10 - left90))
    tw = float(np.mean(widths)) if widths else None
    return {"edge_width": tw, "peak_alpha": peak, "centroid_row": cy,
            "right_10_90": None if right10 is None or right90 is None else abs(right10 - right90)}


def measure_angle(alpha: np.ndarray) -> dict:
    """单点：Alpha 加权二阶矩求主轴方向（度，mod 180）+ 长短轴比。"""
    ys, xs = np.nonzero(alpha > 2)
    if len(xs) < 10:
        return {"angle_deg": None}
    w = alpha[ys, xs].astype(np.float64)
    mx = float((xs * w).sum() / w.sum())
    my = float((ys * w).sum() / w.sum())
    dx = xs - mx
    dy = ys - my
    mxx = float((w * dx * dx).sum() / w.sum())
    myy = float((w * dy * dy).sum() / w.sum())
    mxy = float((w * dx * dy).sum() / w.sum())
    theta = 0.5 * math.degrees(math.atan2(2 * mxy, mxx - myy))
    theta = theta % 180.0
    cov = np.array([[mxx, mxy], [mxy, myy]])
    evals = np.linalg.eigvalsh(cov)
    evals = np.clip(evals, 1e-9, None)
    return {"angle_deg": theta, "moment_xx": mxx, "moment_yy": myy, "moment_xy": mxy,
            "axis_ratio": float(math.sqrt(evals[1] / evals[0])),
            "major_px": float(4 * math.sqrt(evals[1])),
            "minor_px": float(4 * math.sqrt(evals[0])),
            "centroid": [round(mx, 2), round(my, 2)]}


def measure_scatter(alpha: np.ndarray) -> dict:
    """直线笔划：散布的空间离散度（包围盒高度 + Alpha 加权重心的行标准差）。"""
    x0, y0, x1, y1 = bbox_alpha(alpha, thr=2)
    if x0 < 0:
        return {"band_height": 0, "y_std": None}
    # 中间 50% 列，逐列求 Alpha 加权平均行与方差，再对所有列取平均
    lo = x0 + (x1 - x0) // 4
    hi = x1 - (x1 - x0) // 4
    band = alpha[y0:y1 + 1, lo:hi + 1].astype(np.float64)
    rows = np.arange(y0, y1 + 1, dtype=np.float64)[:, None]
    col_sum = band.sum(axis=0)
    valid = col_sum > 0
    means = (rows * band).sum(axis=0)[valid] / col_sum[valid]
    var = ((rows - means[None, :]) ** 2 * band[:, valid]).sum(axis=0) / col_sum[valid]
    return {
        "band_height": int(y1 - y0 + 1),
        "y_std": float(np.sqrt(np.mean(var))),
        "y_mean_spread": float(np.std(means)),
        "x_range": [int(lo), int(hi)],
    }


def _rel(path) -> str:
    """仓库内相对路径（正斜杠）；隐私门要求提交物不出现盘符与家目录。"""
    try:
        return str(Path(path).resolve().relative_to(REPO)).replace("\\", "/")
    except Exception:
        return Path(path).name


def analyze_point(p: dict) -> dict:
    """单个测量点的指标。"""
    geom = p["geom"]
    mode = geom["mode"]
    ps_path, kt_path = Path(p["ps_file"]), Path(p["kt_file"])
    entry = {"param": p["param"], "group": p["group"], "value": p["value"],
             "ps_file": _rel(ps_path), "kt_file": _rel(kt_path)}
    if not ps_path.is_file() or not kt_path.is_file():
        entry["error"] = "缺图：ps=%s kt=%s" % (ps_path.is_file(), kt_path.is_file())
        entry["consistent"] = None
        return entry
    a_ps, a_kt = load_alpha(ps_path), load_alpha(kt_path)
    entry["pixel"] = metrics_alpha(a_ps, a_kt)
    entry["ps_idat_sha256"] = idat_sha256(ps_path)
    entry["kt_idat_sha256"] = idat_sha256(kt_path)

    if mode == "image":
        # 缩放：图像级差异 + 响应曲线（相对 s=100 锚点）
        entry["metric"] = {"kind": "image_diff"}
        entry["consistent"] = entry["pixel"]["pass"]
        return entry

    if p["param"] == "spacing":
        d = float(geom["nominal_diameter"])
        v = float(p["value"])
        m_ps = measure_step(a_ps, d, v)
        m_kt = measure_step(a_kt, d, v)
        f_ps = measure_footprint(a_ps, m_ps.get("step_px"))
        f_kt = measure_footprint(a_kt, m_kt.get("step_px"))
        m = {"ps": dict(m_ps, footprint=f_ps), "kt": dict(m_kt, footprint=f_kt)}
        entry["metric"] = m
        if m_ps.get("step_px") and m_kt.get("step_px"):
            band_ps = max(1.0, float(m_ps["band_height"]))
            band_kt = max(1.0, float(m_kt["band_height"]))
            m["step_over_diameter_ps"] = m_ps["step_px"] / d
            m["step_over_diameter_kt"] = m_kt["step_px"] / d
            m["rel_err_step_over_diameter"] = abs(m_ps["step_px"] - m_kt["step_px"]) / m_ps["step_px"]
            m["rel_err_step"] = abs(m_ps["step_px"] - m_kt["step_px"]) / m_ps["step_px"]
            m["step_ratio_kt_over_ps"] = m_kt["step_px"] / m_ps["step_px"]
            m["step_over_band_ps"] = m_ps["step_px"] / band_ps
            m["step_over_band_kt"] = m_kt["step_px"] / band_kt
            m["rel_err_step_over_band"] = abs(m_ps["step_px"] / band_ps - m_kt["step_px"] / band_kt) / (m_ps["step_px"] / band_ps)
            m["band_height_ratio_kt_over_ps"] = band_kt / band_ps
            m["nominal_step_px"] = v / 100.0 * d
            m["ps_step_vs_nominal_rel"] = abs(m_ps["step_px"] - m["nominal_step_px"]) / m["nominal_step_px"]
            m["kt_step_vs_nominal_rel"] = abs(m_kt["step_px"] - m["nominal_step_px"]) / m["nominal_step_px"]
            entry["consistent"] = bool(m["rel_err_step"] <= GEOM_REL_THRESHOLD)
        else:
            entry["consistent"] = None
            entry["unmeasurable"] = {
                "ps": m_ps.get("note") or ("step=%s" % m_ps.get("step_px")),
                "kt": m_kt.get("note") or ("step=%s" % m_kt.get("step_px")),
            }
        return entry

    if p["param"] == "size":
        m_ps = measure_width(a_ps)
        m_kt = measure_width(a_kt)
        m = {"ps": m_ps, "kt": m_kt}
        if m_ps.get("width50") and m_kt.get("width50"):
            m["rel_err_width"] = abs(m_ps["width50"] - m_kt["width50"]) / m_ps["width50"]
            m["width_ratio_kt_over_ps"] = m_kt["width50"] / m_ps["width50"]
            m["abs_diff_px"] = abs(m_ps["width50"] - m_kt["width50"])
            m["nominal_diameter"] = float(geom["nominal_diameter"])
            m["ps_width_vs_nominal_rel"] = abs(m_ps["width50"] - float(geom["nominal_diameter"])) / float(geom["nominal_diameter"])
            m["kt_width_vs_nominal_rel"] = abs(m_kt["width50"] - float(geom["nominal_diameter"])) / float(geom["nominal_diameter"])
            # 判据：相对误差 ≤2%，或绝对差 ≤2px（与 max_delta≤2 同量级；小直径下
            # 亚像素栅格差会被相对误差放大——20px 笔尖差 0.5px 就有 2.5%）
            m["consistent_by_rel"] = bool(m["rel_err_width"] <= GEOM_REL_THRESHOLD)
            m["consistent_by_abs"] = bool(m["abs_diff_px"] <= ABS_PX_THRESHOLD)
            entry["consistent"] = bool(m["consistent_by_rel"] or m["consistent_by_abs"])
        else:
            entry["consistent"] = None
        entry["metric"] = m
        return entry

    if p["param"] == "hardness":
        m_ps = measure_edge(a_ps)
        m_kt = measure_edge(a_kt)
        m = {"ps": m_ps, "kt": m_kt}
        if m_ps.get("edge_width") is not None and m_kt.get("edge_width") is not None:
            m["abs_diff_px"] = abs(m_ps["edge_width"] - m_kt["edge_width"])
            m["rel_err"] = m["abs_diff_px"] / max(1e-6, m_ps["edge_width"])
            m["consistent_by_rel"] = bool(m["rel_err"] <= GEOM_REL_THRESHOLD)
            m["consistent_by_abs"] = bool(m["abs_diff_px"] <= ABS_PX_THRESHOLD)
            entry["consistent"] = bool(m["consistent_by_rel"] or m["consistent_by_abs"])
        else:
            entry["consistent"] = None
        entry["metric"] = m
        return entry

    if p["param"] == "scatter":
        m_ps = measure_scatter(a_ps)
        m_kt = measure_scatter(a_kt)
        m = {"ps": m_ps, "kt": m_kt}
        entry["metric"] = m
        entry["consistent"] = None  # 散布是「零值锚点 + 增量」口径，整体在参数级判
        return entry

    if p["param"] == "angle":
        m_ps = measure_angle(a_ps)
        m_kt = measure_angle(a_kt)
        m = {"ps": m_ps, "kt": m_kt}
        if m_ps.get("angle_deg") is not None and m_kt.get("angle_deg") is not None:
            diff = abs(m_ps["angle_deg"] - m_kt["angle_deg"])
            diff = min(diff, 180.0 - diff)
            m["angle_diff_deg"] = diff
            m["axis_ratio_ps"] = m_ps.get("axis_ratio")
            m["axis_ratio_kt"] = m_kt.get("axis_ratio")
            entry["consistent"] = bool(diff <= ANGLE_TOL_DEG)
        else:
            entry["consistent"] = None
        entry["metric"] = m
        return entry

    entry["consistent"] = None
    return entry


def analyze_scale(points: list[dict]) -> dict:
    """缩放：图像级差异 + 响应曲线 R(s)=A(s)-A(100) 的两侧残差。"""
    rows = []
    base_ps = base_kt = None
    for p in points:
        r = analyze_point(p)
        if abs(p["value"] - 100.0) < 1e-9:
            base_ps = load_alpha(Path(p["ps_file"])) if Path(p["ps_file"]).is_file() else None
            base_kt = load_alpha(Path(p["kt_file"])) if Path(p["kt_file"]).is_file() else None
        rows.append(r)
    for r, p in zip(rows, points):
        if base_ps is None or base_kt is None:
            r["metric"]["curve"] = {"error": "缺 s=100 锚点图"}
            continue
        if not (Path(p["ps_file"]).is_file() and Path(p["kt_file"]).is_file()):
            continue
        a_ps, a_kt = load_alpha(Path(p["ps_file"])), load_alpha(Path(p["kt_file"]))
        r_ps = a_ps.astype(np.int16) - base_ps.astype(np.int16)
        r_kt = a_kt.astype(np.int16) - base_kt.astype(np.int16)
        d = np.abs(r_ps.astype(np.int32) - r_kt.astype(np.int32))
        mean_ps = float(np.abs(r_ps).mean())
        r["metric"]["curve"] = {
            "residual_mean_abs": float(d.mean()),
            "residual_max_abs": int(d.max()),
            "mean_abs_R_ps": mean_ps,
            "rel_residual": (float(d.mean()) / mean_ps) if mean_ps > 0 else None,
            "note": "R(s)=A(s)-A(100)，s=100 为锚点（R≡0 属定义）",
        }
    return {"param": "scale", "group": "exp", "points": rows}


def conclude(param: str, points: list[dict], results: dict) -> dict:
    """按判据给参数结论：真偏 / 不偏 / 证不出来（只允许三选一）。"""
    pts = results.get("points") or []
    ok_flags = [r.get("consistent") for r in pts]
    measured = [f for f in ok_flags if f is not None]
    unmeasured = [r for r in pts if r.get("consistent") is None]

    if param == "spacing":
        # 三组一起看：计算笔刷（无补白）+ 近方形采样 + 非方形采样
        rows = [r for r in pts if r.get("consistent") is not None]
        if not rows:
            return {"conclusion": "证不出来", "reason": "全部取值点缺测或步长不可判读"}
        errs = []
        per_group: dict[str, list[float]] = {}
        for r in rows:
            e = r["metric"].get("rel_err_step")
            if e is not None:
                errs.append((r["group"], r["value"], e))
                per_group.setdefault(r["group"], []).append(e)
        bad = [x for x in errs if x[2] > GEOM_REL_THRESHOLD]
        unmeas = [r for r in pts if r.get("consistent") is None]
        summary = "；".join("%s 最大 %.2f%%" % (g, max(v) * 100) for g, v in
                            (("computed", per_group.get("computed", [])),
                             ("square", per_group.get("square", [])),
                             ("nonsquare", per_group.get("nonsquare", []))) if v)
        tail = ("；%d 点不可判读：%s" % (
            len(unmeas), "、".join("%s v%s" % (r["group"], fmt(r["value"])) for r in unmeas))
            if unmeas else "")
        if bad:
            parts = "、".join("%s v%s=%.1f%%" % (g, fmt(v), e * 100) for g, v, e in bad)
            return {"conclusion": "真偏",
                    "reason": "步长相对误差超 2%% 的点：%s（共 %d/%d 点超阈）；分组：%s%s"
                              % (parts, len(bad), len(errs), summary, tail)}
        return {"conclusion": "不偏",
                "reason": "已判读 %d 点步长相对误差均 ≤2%%；分组：%s%s"
                          % (len(errs), summary, tail)}

    if param == "scale":
        pixels = [r["pixel"] for r in pts if "pixel" in r]
        bad = [r for r in pts if r.get("consistent") is False]
        if bad:
            vals = "、".join(fmt(r["value"]) + "%" for r in bad)
            return {"conclusion": "真偏",
                    "reason": "缩放 %s 处整图差异比例 >2%% 或 max_delta>2（s=100 锚点一致）" % vals}
        if not pixels:
            return {"conclusion": "证不出来", "reason": "缺 exp 图"}
        return {"conclusion": "不偏", "reason": "三点整图差异比例 ≤2% 且 max_delta≤2"}

    if param == "scatter":
        # 零值锚点 + 增量口径：band_height(v)-band_height(0)
        base = next((r for r in pts if abs(r["value"]) < 1e-9), None)
        if base is None or "metric" not in base:
            return {"conclusion": "证不出来", "reason": "缺 0% 锚点"}
        b_ps = base["metric"]["ps"]["band_height"]
        b_kt = base["metric"]["kt"]["band_height"]
        if not b_ps:
            return {"conclusion": "证不出来", "reason": "0% 锚点 PS 图空"}
        rows = []
        for r in pts:
            if r["value"] == 0 or "metric" not in r:
                continue
            g_ps = r["metric"]["ps"]["band_height"] - b_ps
            g_kt = r["metric"]["kt"]["band_height"] - b_kt
            rel = abs(g_ps - g_kt) / g_ps if g_ps else None
            rows.append({"value": r["value"], "grow_ps": g_ps, "grow_kt": g_kt,
                         "rel_err": rel})
        results["scatter_growth"] = rows
        bad = [x for x in rows if x["rel_err"] is not None and x["rel_err"] > GEOM_REL_THRESHOLD]
        if bad:
            return {"conclusion": "真偏",
                    "reason": "散布增量（band_height(v)-band_height(0)）相对误差最大 %.1f%%（PS 增量 vs Krita 增量）"
                              % (max(x["rel_err"] for x in bad) * 100)}
        return {"conclusion": "不偏", "reason": "散布增量相对误差均 ≤2%%"}

    # 通用：尺寸/硬度/角度
    if not measured:
        return {"conclusion": "证不出来", "reason": "全部取值点缺测"}
    bad = [r for r in pts if r.get("consistent") is False]
    if bad:
        detail = "；".join("%s=%s" % (fmt(r["value"]), r.get("metric")) for r in bad)
        return {"conclusion": "真偏",
                "reason": "至少一点超阈（%d/%d 点超阈）" % (len(bad), len(measured)),
                "detail": detail}
    return {"conclusion": "不偏", "reason": "全部已测取值点均在阈值内"}


def conclude_padding(spacing_results: dict) -> dict:
    """间距的「方形 vs 非方形」对照结论（与补平方有关/无关/证据不足）。

    主对照口径 = 绝对步长相对误差（rel_err_step，PS 为分母）；
    辅口径 = 中心距/笔迹高（rel_err_step_over_band）与足迹比，一并给出。
    """
    pts = spacing_results["points"]
    groups = {"computed": [], "square": [], "nonsquare": []}
    for r in pts:
        g = r["group"]
        if g in groups and "metric" in r:
            e = r["metric"].get("rel_err_step")
            b = r["metric"].get("rel_err_step_over_band")
            if e is not None:
                groups[g].append({"value": r["value"], "rel_err_step": e,
                                  "rel_err_step_over_band": b,
                                  "step_ps": r["metric"]["ps"].get("step_px"),
                                  "step_kt": r["metric"]["kt"].get("step_px"),
                                  "band_ps": r["metric"]["ps"].get("band_height"),
                                  "band_kt": r["metric"]["kt"].get("band_height"),
                                  "band_height_ratio": r["metric"].get("band_height_ratio_kt_over_ps")})
    def worst(rows):
        return max((x["rel_err_step"] for x in rows), default=None)
    w_sq, w_ns, w_c = worst(groups["square"]), worst(groups["nonsquare"]), worst(groups["computed"])
    out = {"worst_rel_err_step": {"computed": w_c, "square": w_sq, "nonsquare": w_ns},
           "points": groups}
    if w_sq is None or w_ns is None:
        out["conclusion"] = "证据不足"
        out["reason"] = "方形或非方形对照缺测"
        return out
    if w_ns <= GEOM_REL_THRESHOLD and w_sq <= GEOM_REL_THRESHOLD:
        out["conclusion"] = "与补平方无关"
        out["reason"] = ("方形对照最大步长相对误差 %.2f%%、非方形 %.2f%%，两者都 ≤2%%；"
                         "计算笔刷（无补白）最大 %s"
                         % (w_sq * 100, w_ns * 100,
                            "缺测" if w_c is None else "%.2f%%" % (w_c * 100)))
    elif w_ns > GEOM_REL_THRESHOLD and w_ns >= 2 * max(w_sq, 1e-9):
        out["conclusion"] = "与补平方有关"
        out["reason"] = ("非方形最大步长相对误差 %.2f%% 超阈且 ≥2×方形 %.2f%%；"
                         "计算笔刷（无补白）最大 %s"
                         % (w_ns * 100, w_sq * 100,
                            "缺测" if w_c is None else "%.2f%%" % (w_c * 100)))
    else:
        out["conclusion"] = "证据不足"
        out["reason"] = ("方形 %.2f%%、非方形 %.2f%%：没有形成「只在非方形出现」的分化"
                         % (w_sq * 100, w_ns * 100))
    return out


# ------------------------------------------------------------------ manifest


def kpp_fields(path: Path) -> dict:
    """从 .kpp（PNG + preset 文本块）回读关键字段；文件不存在返回 {}。"""
    import re
    from PIL import Image as _Image
    try:
        with _Image.open(path) as im:
            raw = im.info.get("preset")
    except Exception:
        return {}
    if raw is None:
        return {}
    xml = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)

    def grab(pattern: str) -> str:
        m = re.search(pattern, xml)
        return m.group(1) if m else ""

    return {
        "spacing": grab(r'spacing="([^"]+)"'),
        "angle": grab(r'angle="([^"]+)"'),
        "diameter": grab(r'diameter="([^"]+)"'),
        "ratio": grab(r'ratio="([^"]+)"'),
        "hfade": grab(r'hfade="([^"]+)"'),
        "ScatterValue": grab(r'name="ScatterValue" type="internal">([^<]*)<'),
        "PressureScatter": grab(r'name="PressureScatter" type="internal">([^<]*)<'),
        "AxisX": grab(r'name="Scattering/AxisX" type="internal">([^<]*)<'),
        "AxisY": grab(r'name="Scattering/AxisY" type="internal">([^<]*)<'),
        "xml_bytes": str(len(xml)),
    }


def manifest_entry(p: dict, side: str, ps_info: dict | None = None) -> dict:
    path = Path(p["ps_file"] if side == "ps" else p["kt_file"])
    entry = {
        "side": side, "param": p["param"], "group": p["group"], "value": p["value"],
        "file": _rel(path),
        "exists": path.is_file(),
        # 复现命令用相对写法：提交物里不许出现本机 Python 绝对路径
        "cmd": "python tools/measure_deviation.py --param %s --values %s --analyze"
               % (p["param"], fmt(p["value"])),
    }
    if path.is_file():
        alpha = load_alpha(path)
        entry["width"], entry["height"] = int(alpha.shape[1]), int(alpha.shape[0])
        entry["idat_sha256"] = idat_sha256(path)
        entry["mtime"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
        entry["bytes"] = int(path.stat().st_size)
    if side == "ps" and ps_info:
        row = ps_info["rows"].get(path.name)
        if row:
            entry["ps_readback"] = {k: row[k] for k in
                                    ("brush", "diameter", "hardness", "spacing", "roundness",
                                     "angle", "jitter", "useScatter", "useTexture", "useTipDynamics")}
            entry["ps_ok"] = row["ok"] == "true"
            if row.get("error"):
                entry["ps_error"] = row["error"]
    if side == "krita":
        kpp_path = p.get("kpp_path")
        if not kpp_path:
            cand = KPP_DIR / ("%s__%s__v%s.kpp" % (p["param"], p["group"], fmt(p["value"])))
            kpp_path = str(cand) if cand.is_file() else ""
        if kpp_path:
            entry["krita_preset"] = _rel(kpp_path)
            entry["krita_readback"] = p.get("kpp_readback") or kpp_fields(Path(kpp_path))
    return entry


def update_manifest(points: list[dict], results: dict, ps_info: dict | None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    old = {}
    if MANIFEST.is_file():
        try:
            old = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    entries = {(e["param"], e["group"], float(e["value"]), e["side"]): e
               for e in old.get("images", [])}
    for p in points:
        for side in ("ps", "krita"):
            e = manifest_entry(p, side, ps_info)
            key = (e["param"], e["group"], float(e["value"]), side)
            prev = entries.get(key) or {}
            # --analyze 不跑 PS 时没有回读；保留上一次真实出图的回读证据
            for k in ("ps_readback", "ps_ok", "ps_error", "krita_readback", "krita_preset"):
                if k not in e and k in prev:
                    e[k] = prev[k]
            entries[key] = e
    images = [entries[k] for k in sorted(entries, key=lambda k: (k[0], k[1], k[2], k[3]))]
    old_ps = old.get("ps") or {}
    ps_meta = {"exe": "Photoshop.exe",
               "version": (ps_info or {}).get("ps_version") or old_ps.get("version", ""),
               "session": "一个 PS 会话串行出图；已有实例时复用且不退出",
               "elapsed_s": (ps_info or {}).get("elapsed_s") if ps_info else old_ps.get("elapsed_s")}
    data = {
        "schema": "brushconverter/deviation-106@1",
        "criteria": {
            "pixel": "差异点=|Δalpha|>2，分母=宽×高；一致=差异比例≤2% 且 max_delta≤2",
            "geometry": "相对误差≤2% 视为一致；角度按 2%×180°=3.6°；绝对差≤2px 亦算一致",
            "idat": "判像素只认 PNG IDAT 块的 sha256（文件 sha256 会被 iTXt/XMP 影响）",
        },
        "brushes": {
            "computed": {"abr": "tests/定量测试专用.abr", "name": BRUSH_COMPUTED,
                         "note": "唯一一支计算笔刷 Dmtr=200/Hrdn=100/Rndn=100，笔尖无位图、无补白"},
            "square": {"abr": "tests/测试用.abr", "name": BRUSH_SQUARE,
                       "note": "采样笔尖 86x84（长宽比 1.02），补白后 86x86"},
            "nonsquare": {"abr": "tests/测试用.abr", "name": BRUSH_NONSQUARE,
                          "note": "采样笔尖 183x143（长宽比 1.28），补白后 183x183"},
        },
        "ps": ps_meta,
        "krita": {"exe": "krita.com", "route": "GUI 真实渲染（krita_render_runner.py）",
                  "serial": True},
        "images": images,
        "results": {k: v for k, v in results.items() if k != "points"},
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    log("manifest → %s（%d 张图）" % (MANIFEST, len(images)))


# ------------------------------------------------------------------ 主流程


def run(params: list[str], values_map: dict[str, list[float]] | None,
        skip_ps: bool, skip_krita: bool, force: bool) -> dict:
    points: list[dict] = []
    for param in params:
        vals = (values_map or {}).get(param) or PARAM_VALUES[param]
        pts = plan_param(param, vals)
        points.extend(pts)
    render_points = [p for p in points if p.get("render", True)]
    ps_info = None
    if render_points and not skip_ps:
        ps_info = run_ps(render_points)
    elif render_points:
        log("PS: 按 --skip-ps 跳过")
    if render_points and not skip_krita:
        render_krita(render_points, force=force)
    elif render_points:
        log("Krita: 按 --skip-krita 跳过")

    results = {}
    for param in params:
        pts = [p for p in points if p["param"] == param]
        if param == "scale":
            res = analyze_scale(pts)
        else:
            rows = [analyze_point(p) for p in pts]
            res = {"param": param, "group": "多组" if param == "spacing" else "computed",
                   "points": rows}
        res["conclusion_detail"] = conclude(param, pts, res)
        if param == "spacing":
            res["padding_conclusion"] = conclude_padding(res)
        results[param] = res
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / ("results_%s.json" % param)).write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        log("== %s：%s（%s）" % (param, res["conclusion_detail"]["conclusion"],
                                res["conclusion_detail"]["reason"]))
        if param == "spacing":
            log("   补平方对照：%s（%s）" % (res["padding_conclusion"]["conclusion"],
                                          res["padding_conclusion"]["reason"]))
        for r in res["points"]:
            if r.get("error"):
                log("   [缺] %s/%s v%s：%s" % (r["param"], r["group"], fmt(r["value"]), r["error"]))
                continue
            m = r.get("metric", {})
            if param == "spacing":
                fp = m.get("ps", {}).get("footprint", {})
                fk = m.get("kt", {}).get("footprint", {})
                log("   %s v%s：PS步长=%s px（票 %s/%s）Krita步长=%s px（票 %s/%s）"
                    "相对误差=%s 足迹高 PS=%s Krita=%s 步长/足迹高 PS=%s Krita=%s 判定=%s"
                    % (r["group"], fmt(r["value"]),
                       _r(m.get("ps", {}).get("step_px")), m.get("ps", {}).get("votes"),
                       m.get("ps", {}).get("candidates"),
                       _r(m.get("kt", {}).get("step_px")), m.get("kt", {}).get("votes"),
                       m.get("kt", {}).get("candidates"),
                       ("%.2f%%" % (m.get("rel_err_step") * 100))
                       if m.get("rel_err_step") is not None else "不可判读",
                       fp.get("height"), fk.get("height"),
                       _r(m.get("step_over_band_ps")), _r(m.get("step_over_band_kt")),
                       r.get("consistent")))
            elif param == "size":
                log("   %s v%s：PS宽=%s Krita宽=%s 误差=%.2f%%" % (
                    r["group"], fmt(r["value"]), _r(m.get("ps", {}).get("width50")),
                    _r(m.get("kt", {}).get("width50")), (m.get("rel_err_width") or 0) * 100))
            elif param == "hardness":
                log("   %s v%s：PS过渡宽=%s px Krita过渡宽=%s px Δ=%s px" % (
                    r["group"], fmt(r["value"]), _r(m.get("ps", {}).get("edge_width")),
                    _r(m.get("kt", {}).get("edge_width")), _r(m.get("abs_diff_px"))))
            elif param == "scatter":
                log("   %s v%s：PS带高=%s Krita带高=%s PS ystd=%s Krita ystd=%s" % (
                    r["group"], fmt(r["value"]), m.get("ps", {}).get("band_height"),
                    m.get("kt", {}).get("band_height"), _r(m.get("ps", {}).get("y_std")),
                    _r(m.get("kt", {}).get("y_std"))))
            elif param == "angle":
                log("   %s v%s：PS角=%s° Krita角=%s° Δ=%s° 轴比 PS=%s Krita=%s" % (
                    r["group"], fmt(r["value"]), _r(m.get("ps", {}).get("angle_deg")),
                    _r(m.get("kt", {}).get("angle_deg")), _r(m.get("angle_diff_deg")),
                    _r(m.get("ps", {}).get("axis_ratio")), _r(m.get("kt", {}).get("axis_ratio"))))
            elif param == "scale":
                pix = r.get("pixel", {})
                curve = m.get("curve", {})
                log("   %s v%s：差异比例=%.2f%% max=%s mean|ΔR|=%s 相对残差=%s" % (
                    r["group"], fmt(r["value"]), (pix.get("diff_ratio") or 0) * 100,
                    pix.get("max_delta"), _r(curve.get("residual_mean_abs")),
                    _r(curve.get("rel_residual"))))
    update_manifest(points, results, ps_info)
    return results


def _r(v, nd=2):
    if v is None:
        return "None"
    try:
        return ("%.*f" % (nd, float(v))).rstrip("0").rstrip(".")
    except Exception:
        return str(v)


# ------------------------------------------------------------------ PS 字段探针


PROBE_JSX = r"""
// 任务 106 临时探针：探明 currentToolOptions.brush 与 scatterDynamics 的可写字段
#target photoshop
(function () {
    // 工作目录：优先读 %TEMP%/bc_measure_106_work.txt（复用已在运行的 PS 时，
    // 新进程的环境变量不会进入旧实例；标记文件对任何实例都可读），再退回环境变量。
    var WORK = "";
    try {
        var wf = new File(Folder.temp.fsName + "/bc_measure_106_work.txt");
        if (wf.exists) { wf.encoding = "UTF-8"; wf.open("r"); WORK = wf.read(); wf.close(); }
    } catch (eW) {}
    if (!WORK) { WORK = $.getenv("BC_MEASURE_WORK"); }
    var REPO = $.getenv("BRUSHCONVERTER_REPO");
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function appDesc() {
        var r = new ActionReference(); r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
        return executeActionGet(r);
    }
    function getOpts() { return appDesc().getObjectValue(sid("currentToolOptions")); }
    function setOpts(o) {
        var d = new ActionDescriptor(); var r = new ActionReference();
        r.putClass(sid("paintbrushTool"));
        d.putReference(cid("null"), r);
        d.putObject(cid("T   "), sid("currentToolOptions"), o);
        executeAction(cid("setd"), d, DialogModes.NO);
    }
    var logFile = new File(WORK + "/probe.log");
    logFile.encoding = "UTF-8"; logFile.open("w");
    function L(s) { logFile.writeln(String(s)); }
    function vstr(o, id) {
        try {
            var t = o.getType(id);
            if (t === DescValueType.UNITDOUBLE) { return "UNITDOUBLE:" + o.getUnitDoubleValue(id); }
            if (t === DescValueType.INTEGER) { return "INTEGER:" + o.getInteger(id); }
            if (t === DescValueType.BOOLEAN) { return "BOOLEAN:" + o.getBoolean(id); }
            if (t === DescValueType.STRING) { return "STRING:" + o.getString(id); }
            return "OTHER";
        } catch (e) { return "ABSENT"; }
    }
    function enumerateDesc(o, label) {
        var rows = [];
        try {
            for (var i = 0; i < o.count; i++) {
                var k = o.getKey(i), nm = "";
                try { nm = typeIDToStringID(k); } catch (e) {}
                if (!nm) { try { nm = typeIDToCharID(k); } catch (e2) { nm = "?" + k; } }
                rows.push(nm);
            }
        } catch (e3) { rows.push("DUMP_ERR:" + e3.message); }
        return label + "\t" + rows.join(",");
    }
    function selectBrush(name) {
        var d = new ActionDescriptor(); var r = new ActionReference();
        r.putName(cid("Brsh"), name); d.putReference(cid("null"), r);
        executeAction(cid("slct"), d, DialogModes.NO);
    }
    function writeBrush(key, unit, value) {
        var res = "before=;after=;ok=false;err=";
        try {
            var o = getOpts(); var b = o.getObjectValue(sid("brush"));
            var before = vstr(b, sid(key));
            b.putUnitDouble(sid(key), cid(unit), value);
            o.putObject(sid("brush"), sid("brush"), b); setOpts(o);
            var o2 = getOpts(); var b2 = o2.getObjectValue(sid("brush"));
            res = "before=" + before + ";after=" + vstr(b2, sid(key)) + ";ok=true;err=";
        } catch (e) { res = "before=;after=;ok=false;err=" + String(e.message).replace(/[\t\r\n]/g, " "); }
        return "WRITE\t" + key + "\t" + unit + "\t" + value + "\t" + res;
    }
    try {
        app.displayDialogs = DialogModes.NO;
        var d0 = new ActionDescriptor(); var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool")); d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        app.load(new File(REPO + "/tests/定量测试专用.abr"));
        selectBrush("定量测试用");
        L("PS_VERSION\t" + app.version);
        var o = getOpts();
        L(enumerateDesc(o, "OPTIONS_KEYS"));
        L(enumerateDesc(o.getObjectValue(sid("brush")), "BRUSH_KEYS_COMPUTED"));
        L(writeBrush("diameter", "#Pxl", 150));
        L(writeBrush("hardness", "#Prc", 40));
        L(writeBrush("spacing", "#Prc", 33));
        L(writeBrush("roundness", "#Prc", 70));
        L(writeBrush("angle", "#Ang", 45));
        // 散布
        try {
            var o1 = getOpts(); o1.putBoolean(sid("useScatter"), true); setOpts(o1);
            L("WRITE\tuseScatter\tBOOL\tTrue\tok=true");
            var sd = getOpts().getObjectValue(sid("scatterDynamics"));
            L(enumerateDesc(sd, "SCATTER_KEYS"));
            var o2 = getOpts(); var sd2 = o2.getObjectValue(sid("scatterDynamics"));
            sd2.putUnitDouble(sid("jitter"), cid("#Prc"), 300);
            o2.putObject(sid("scatterDynamics"), sid("scatterDynamics"), sd2); setOpts(o2);
            L("WRITE\tjitter\t#Prc\t300\tbefore=UNITDOUBLE:30;after=" +
              vstr(getOpts().getObjectValue(sid("scatterDynamics")), sid("jitter")) + ";ok=true;err=");
            var o3 = getOpts(); o3.putBoolean(sid("useScatter"), false); setOpts(o3);
        } catch (e4) { L("SCATTER_ERR\t" + String(e4.message).replace(/[\t\r\n]/g, " ")); }
        // 采样笔刷
        app.load(new File(REPO + "/tests/测试用.abr"));
        var names = ["Kyle 的终极粉彩派对", "KYLE 额外厚实炭笔", "Kyle 的橡皮擦 - 自然边缘"];
        for (var i = 0; i < names.length; i++) {
            try { selectBrush(names[i]); } catch (e5) { L("SELECT_FAIL\t" + names[i] + "\t" + e5.message); }
            var ob = getOpts();
            L(enumerateDesc(ob.getObjectValue(sid("brush")), "BRUSH_KEYS\t" + names[i]));
            L("WRITE_SAMPLED\t" + names[i] + "\t" + writeBrush("spacing", "#Prc", 37).substring(6));
            L("WRITE_SAMPLED\t" + names[i] + "\t" + writeBrush("diameter", "#Pxl", 77).substring(6));
        }
        // 复原计算笔刷几何
        try {
            var oo = getOpts();
            var bb = oo.getObjectValue(sid("brush"));
            bb.putUnitDouble(sid("diameter"), cid("#Pxl"), 200);
            bb.putUnitDouble(sid("hardness"), cid("#Prc"), 100);
            bb.putUnitDouble(sid("spacing"), cid("#Prc"), 1000);
            bb.putUnitDouble(sid("roundness"), cid("#Prc"), 100);
            bb.putUnitDouble(sid("angle"), cid("#Ang"), 0);
            oo.putObject(sid("brush"), sid("brush"), bb); setOpts(oo);
        } catch (e6) {}
    } catch (e) { L("FATAL\t" + String(e.message).replace(/[\t\r\n]/g, " ") + "\tline " + e.line); }
    logFile.close();
    try { app.quit(); } catch (e7) {}
})();
"""


def run_probe() -> dict:
    """重跑 PS 字段探针，结果写 docs/数据/偏差/ps_brush_fields.json。"""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    jsx_path = Path(tempfile.gettempdir()) / "bc_measure_106_probe.jsx"
    jsx_path.write_text(PROBE_JSX, encoding="utf-8-sig")
    work = ps_work_dir()
    (Path(tempfile.gettempdir()) / "bc_measure_106_work.txt").write_text(
        str(work), encoding="utf-8")
    probe_log = work / "probe.log"
    if probe_log.exists():
        probe_log.unlink()
    preexisting = photoshop_running()
    if preexisting:
        raise SystemExit("检测到 Photoshop 已在运行：字段探针会退出 PS，为避免关掉你的会话，请先手动关闭 Photoshop")
    env = os.environ.copy()
    env["BC_MEASURE_WORK"] = str(work)
    env["BRUSHCONVERTER_REPO"] = str(REPO)
    log("PS 字段探针：启动 Photoshop…")
    proc = subprocess.Popen([str(PS_EXE), "-r", str(jsx_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    started = time.time()
    while time.time() - started < 300:
        if probe_log.exists() and probe_log.stat().st_size > 0 and "FATAL" in probe_log.read_text(encoding="utf-8", errors="replace"):
            break
        if proc.poll() is not None:
            break
        time.sleep(1.0)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        kill_photoshop()
    text = probe_log.read_text(encoding="utf-8", errors="replace") if probe_log.exists() else ""
    if probe_log.exists():
        (WORK_DIR / "probe.log").write_bytes(probe_log.read_bytes())
    data = {"schema": "brushconverter/ps-brush-fields@1",
            "source": str((WORK_DIR / "probe.log").relative_to(REPO)).replace("\\", "/"),
            "lines": [ln for ln in text.splitlines() if ln.strip()]}
    (OUT_DIR / "ps_brush_fields.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    log(text)
    log("探针结果 → %s" % (OUT_DIR / "ps_brush_fields.json"))
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="任务 106：六参数 PS↔Krita 偏差测量（只测不改）")
    ap.add_argument("--param", action="append", default=[],
                    choices=PARAM_ORDER, help="参数名，可重复；不给则配合 --all")
    ap.add_argument("--values", default="", help="逗号分隔的取值，仅单参数时生效")
    ap.add_argument("--all", action="store_true", help="六个参数全跑（串行）")
    ap.add_argument("--analyze", action="store_true", help="只读已有图，重算数字")
    ap.add_argument("--probe", action="store_true", help="只重跑 PS 字段探针")
    ap.add_argument("--skip-ps", action="store_true", help="跳过 PS 出图（调试）")
    ap.add_argument("--skip-krita", action="store_true", help="跳过 Krita 渲染（调试）")
    ap.add_argument("--force", action="store_true", help="Krita 图已存在也重渲")
    ns = ap.parse_args(argv)

    if ns.probe:
        run_probe()
        return 0

    if ns.all:
        params = list(PARAM_ORDER)
    elif ns.param:
        params = list(dict.fromkeys(ns.param))
    else:
        ap.error("需要 --param 或 --all（或用 --probe）")

    values_map = {}
    if ns.values:
        if len(params) != 1:
            ap.error("--values 只能与单个 --param 一起用")
        vals = [float(x) for x in ns.values.split(",") if x.strip()]
        if len(vals) < 3:
            log("警告：%s 只给了 %d 个取值点（任务要求每参数 ≥3 点）" % (params[0], len(vals)))
        values_map[params[0]] = vals

    if ns.analyze:
        # 只分析：不启动 PS / Krita
        results = run(params, values_map, skip_ps=True, skip_krita=True, force=False)
    else:
        results = run(params, values_map, skip_ps=ns.skip_ps, skip_krita=ns.skip_krita,
                      force=ns.force)

    counts = {"真偏": 0, "不偏": 0, "证不出来": 0}
    for param in params:
        counts[results[param]["conclusion_detail"]["conclusion"]] += 1
    log("六参数结论计数：真偏 %d / 不偏 %d / 证不出来 %d（本次跑了 %d 个参数）" % (
        counts["真偏"], counts["不偏"], counts["证不出来"], len(params)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
