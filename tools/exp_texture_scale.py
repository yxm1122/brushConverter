# -*- coding: utf-8 -*-
"""任务 128：纹理缩放（textureScale）预缩放 A/B 实验（v0.14 · 核心能力 B）

【回答什么问题】
纹理缩放 >100% 的残差（现行 150% 相对残差≈22.2%、200%≈32.4%）能不能靠
「先把纹理 PNG 重采样到目标尺寸、Krita 里写 Scale=1.0」消掉？

【本工具做什么】
1) 准备纹理样本：阶梯纹理（合成对照）+ 项目标准真实纹理 + 真实位图裁片；
2) 生成两条路径的 .kpp：
     raw      : 原纹理 + Texture/Pattern/Scale = s/100（现行转换路径）
     prescale : 预重采样 PNG（与 PS 实测核一致的源对齐周期双线性）+ Scale=1.0
3) PS 侧真值：复用 tools/ps_export.jsx 的 job 格式（每纹理一个 PS 批，不改既有文件）；
4) Krita 侧：复用 tools/krita_render_runner.py 的 GUI 真实渲染路线（单实例，串行）；
5) 逐张比对：Alpha 通道、tol=2、分母=全部像素 -> 差异比例 / max_delta /
   相对残差（与 tools/exp_analyze.py 同口径：R=Alpha(s)-Alpha(100)，
   rel=mean|R_krita-R_ps| / mean|R_ps|），另给体积增量（内嵌 PNG 字节 / .kpp 字节）。

【核模型（本任务从既有 PS 真值反推，见本地报告 §核模型）】
PS 的纹理缩放 = 把图案重采样成 round(边长×s/100) 的周期图案，采样坐标「源对齐」
（目标像素 i 对应源坐标 i/k，无半像素偏移；k=s/100），图案按自身尺寸周期延拓。
Krita 现行路径 = Qt QImage::scaled(SmoothTransformation)（中心对齐双线性）：
源坐标 (i+0.5)/k-0.5，与 PS 差 0.5*(1-1/k) 个源像素 —— 即 >100% 残差根因。
prescale 路径用 numpy 直接实现 PS 核，把结果写成 PNG 内嵌、Scale=1.0。

【用法】
  PY="<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe"
  "$PY" tools/exp_texture_scale.py --build          # 生成纹理 + 两条路径 .kpp
  "$PY" tools/exp_texture_scale.py --render-ps      # PS 真值（自动持 .tmp/ps.lock）
  "$PY" tools/exp_texture_scale.py --render-krita   # Krita 渲染（自动持 .tmp/krita.lock）
  "$PY" tools/exp_texture_scale.py --analyze        # 比对 + 汇总（写 .tmp/128/summary.json）
  "$PY" tools/exp_texture_scale.py --all            # build → ps → krita → analyze

参数：--textures step,r64,tip  --scales 100,150,200,300,400  --paths raw,prescale
      --repeats 2（只对 s>=200 的档做重复；s100 为锚点只渲 1 次）--resume（默认开）

【纪律】
不改 src/、tests/、docs/参数矩阵.*；只写 .tmp/128/ 与 summary；
Krita 单实例 ⇒ 渲染串行；PS 同理；本工具在批内持锁、批结束立即释放。
每条结论只认数字（差异比例 ≤2% 且 max_delta ≤2 才算可行），不认观感。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from brush_converter.abr import AbrFile                       # noqa: E402
from brush_converter.mapping import map_presets               # noqa: E402
from brush_converter.kpp.preset_xml import TextureXml, build_preset_xml  # noqa: E402
from brush_converter.kpp.kpp_writer import build_kpp, render_tip_preview  # noqa: E402
from brush_converter import convert as conv_mod               # noqa: E402
import krita_render_runner as runner                          # noqa: E402

# ------------------------------------------------------------------ 常量

ABR_PATH = REPO / "tests" / "定量测试专用.abr"
TEX_R64 = REPO / "research" / "测试用纹理.png"
TEX_TIP_SRC = (REPO / "extracted" /
               "海怪笔刷-详情展示的勾线笔套装组18支_01_9e263557-527f-11e3-baf5-f373c7f93425.png")
# 默认值用占位符（隐私门）；本机用 BRUSHCONVERTER_PS 覆盖。
PS_EXE = Path(os.environ.get(
    "BRUSHCONVERTER_PS", r"<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe"))
PS_JSX = REPO / "tools" / "ps_export.jsx"
PS_BRUSH_NAME = "定量测试用"

WORK = REPO / ".tmp" / "128"
TEX_DIR = WORK / "tex"
KPP_DIR = WORK / "kpp"
KRITA_DIR = WORK / "krita"
PS_DIR = WORK / "ps"
PSWORK_DIR = WORK / "pswork"
BUILD_JSON = WORK / "build.json"
RENDER_LOG = WORK / "krita_render_log.json"
SUMMARY_JSON = WORK / "summary.json"
PS_LOG = WORK / "ps_render_log.json"

CANVAS = (100, 100)          # 画布（与既有纹理实验同几何，PS 真值可横向对照）
STROKE = 60                  # 单点笔划（定量测试专用.abr 的间距=1000%，只落 1 个 dab）
PRESSURE = 1.0
RENDER_TIMEOUT_S = 75.0
PS_TIMEOUT_S = 600.0

#: 三角色纹理（真实纹理不许用纯色/1×1 占位；step 是对照不是真实纹理）
TEXTURES = {
    "step": {"kind": "step", "label": "阶梯纹理 100×100（合成对照，逐行差 2 灰阶）",
             "public": "阶梯纹理（合成对照）"},
    "r64": {"kind": "file", "path": TEX_R64,
            "label": "项目标准真实纹理 64×64（research/测试用纹理.png）",
            "public": "项目标准真实纹理（64×64）"},
    "tip": {"kind": "crop", "path": TEX_TIP_SRC, "crop": 256,
            "label": "真实笔尖位图中心 256×256 裁片（extracted/…_01_*.png）",
            "public": "真实位图 256×256 裁片"},
}

DEFAULT_SCALES = (100, 150, 200, 300, 400)
DEFAULT_PATHS = ("raw", "prescale")


# ------------------------------------------------------------------ 通用小工具

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + ctype + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))


def _add_text_marker(png: bytes, marker: str) -> bytes:
    """在 IEND 前追加一个 tEXt 块（像素字节不动），使 md5 唯一。

    目的：Krita 5.3.3 遇到「内嵌纹理 md5 == 已安装图案 md5」会静默禁用纹理
    （任务 125 §1.3 实测）。加一个内容相关的元数据块即可稳定加载，同时保留
    原文件全部块与像素数据。
    """
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise SystemExit("不是 PNG 字节，无法追加 tEXt")
    if png[-8:-4] != b"IEND":
        raise SystemExit("PNG 不以 IEND 结尾，无法安全追加 tEXt")
    text = b"brushconverter-exp" + bytes([0]) + marker.encode("utf-8")
    extra = _png_chunk(b"tEXt", text)
    out = png[:-12] + extra + png[-12:]
    if out[:8] != png[:8] or out[-12:] != png[-12:]:
        raise SystemExit("tEXt 追加自检失败")
    return out


def _gray_from_rgba(rgba: np.ndarray) -> np.ndarray:
    """与 KisTextureMaskInfo::recalculateMask() 同式的灰度（0..255）。"""
    r, g, b, a = rgba[..., 0], rgba[..., 1], rgba[..., 2], rgba[..., 3]
    gray = (r * 11.0 + g * 16.0 + b * 5.0) / 32.0
    af = a / 255.0
    return gray * af + (255.0 - gray) * (1.0 - af)


def _rgba_png_from_gray(gray: np.ndarray) -> bytes:
    """灰度 → RGBA PNG（R=G=B=gray，A=255），Krita 的灰度式对它是恒等。"""
    g = np.clip(np.rint(gray), 0, 255).astype(np.uint8)
    rgba = np.dstack([g, g, g, np.full_like(g, 255)])
    import io
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return buf.getvalue()


def _save_gray_rgba(path: Path, gray: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_rgba_png_from_gray(gray))


def _load_gray(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return _gray_from_rgba(np.asarray(im.convert("RGBA"), dtype=np.float64))


def _tile(img: np.ndarray, h: int, w: int) -> np.ndarray:
    ph, pw = img.shape
    return np.tile(img, (h // ph + 1, w // pw + 1))[:h, :w]


def _alpha(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.int16)[:, :, 3]


def _repeats_for(scale: int, repeats: int) -> int:
    """s=100 是锚点，只渲 1 次；其余档按 --repeats（默认 2）。"""
    return 1 if scale == 100 else max(1, int(repeats))


# ------------------------------------------------------------------ PS 核模型

def prescale_gray(gray: np.ndarray, out: int) -> np.ndarray:
    """PS 纹理缩放核：源对齐双线性 + 图案周期延拓（本任务从 PS 真值反推）。

    目标像素 i 的源坐标 = i * in/out（无 +0.5 偏移）；源坐标超出图案边界时
    按图案周期回绕（等价于「先重采样一片 round(in*k) 的周期图案」）。
    out == in 时严格恒等（s=100 锚点）。
    """
    ih, iw = gray.shape
    src_y = np.arange(out) * (ih / float(out))
    src_x = np.arange(out) * (iw / float(out))
    y0 = np.floor(src_y).astype(int)
    x0 = np.floor(src_x).astype(int)
    wy = (src_y - y0)[:, None]
    wx = (src_x - x0)[None, :]
    y0m, y1m = np.mod(y0, ih), np.mod(y0 + 1, ih)
    x0m, x1m = np.mod(x0, iw), np.mod(x0 + 1, iw)
    top = gray[y0m][:, x0m] * (1 - wx) + gray[y0m][:, x1m] * wx
    bot = gray[y1m][:, x0m] * (1 - wx) + gray[y1m][:, x1m] * wx
    return top * (1 - wy) + bot * wy


def predicted_mask(gray: np.ndarray, scale: int, h: int = CANVAS[1], w: int = CANVAS[0]):
    """s×100 的预测掩码（0..255 float）；仅用于 build 自检与报告对照，不参与判据。"""
    out = int(round(gray.shape[0] * scale / 100.0))
    up = prescale_gray(gray, out)
    return _tile(up, h, w)


# ------------------------------------------------------------------ 纹理准备

def texture_source_path(key: str) -> Path:
    """PS 与 raw 路径使用的纹理 PNG 路径（真实纹理保持原始字节不动）。"""
    spec = TEXTURES[key]
    if spec["kind"] == "file":
        src = spec["path"]
        if not src.is_file():
            raise SystemExit("真实纹理不存在：%s（本机只读素材，需本地具备）" % src)
        return src
    dst = TEX_DIR / ("%s.png" % key)
    if dst.is_file():
        return dst
    TEX_DIR.mkdir(parents=True, exist_ok=True)
    if spec["kind"] == "step":
        vals = [min(255, i * 2) for i in range(97)] + [204, 230, 255]
        gray = np.repeat(np.array(vals, dtype=np.uint8)[:, None], 100, axis=1)
        _save_gray_rgba(dst, gray.astype(np.float64))
        return dst
    if spec["kind"] == "crop":
        src = spec["path"]
        if not src.is_file():
            raise SystemExit("真实位图素材不存在：%s（本机只读素材，需本地具备）" % src)
        with Image.open(src) as im:
            arr = np.asarray(im.convert("L"), dtype=np.float64)
        side = int(spec["crop"])
        cy, cx = arr.shape[0] // 2, arr.shape[1] // 2
        half = side // 2
        crop = arr[cy - half:cy + half, cx - half:cx + half]
        if crop.shape != (side, side):
            raise SystemExit("裁片尺寸异常：%r" % (crop.shape,))
        _save_gray_rgba(dst, crop)
        return dst
    raise SystemExit("未知纹理类型：%r" % (spec,))


def embedded_raw_bytes(src_png: Path, tag: str) -> bytes:
    """raw 路径内嵌字节：源 PNG 原块 + 唯一化 tEXt（像素不动）。"""
    src = src_png.read_bytes()
    return _add_text_marker(src, "task128:%s:%s" % (tag, _sha256(src)))


def embedded_prescale_bytes(src_png: Path, scale: int, tag: str) -> tuple[bytes, int]:
    """prescale 路径内嵌字节：PS 同核预重采样结果写 PNG + 唯一化 tEXt。"""
    gray = _load_gray(src_png)
    out = int(round(gray.shape[0] * scale / 100.0))
    up = prescale_gray(gray, out)
    png = _rgba_png_from_gray(up)
    return _add_text_marker(png, "task128:%s:%s" % (tag, _sha256(png))), out


# ------------------------------------------------------------------ .kpp 生成

def load_base():
    """解析 ABR 拿实验基准笔刷（计算笔刷；几何固定，不参与变量）。"""
    if not ABR_PATH.is_file():
        raise SystemExit("ABR 不存在：%s" % ABR_PATH)
    abr = AbrFile.parse(str(ABR_PATH))
    presets = map_presets(abr)
    if len(presets) != 1:
        raise SystemExit("实验前提不成立：%s 解析出 %d 支预设（应为 1）"
                         % (ABR_PATH.name, len(presets)))
    bp = presets[0]
    if bp.texture is None:
        raise SystemExit("实验前提不成立：预设没有纹理设置")
    return bp


def brush_parts(bp):
    """笔刷几何（与纹理无关）：(brush_definition, preview_rgba)。"""
    if bp.tip_gray is None and not bp.is_computed:
        raise SystemExit("笔尖位图缺失且非计算笔刷")
    if bp.is_computed:
        brush_def = conv_mod._computed_brush_def(bp)
        hardness = bp.hardness if bp.hardness is not None else 100.0
        preview = render_tip_preview(conv_mod._circle_mask(hardness, bp.roundness))
    else:
        tip_png = conv_mod._png_bytes(conv_mod._square_tip(bp.tip_gray))
        brush_def = conv_mod._sampled_brush_def("tip", tip_png, bp)
        preview = render_tip_preview(bp.tip_gray)
    return brush_def, preview


def make_kpp_bytes(bp, brush_def: str, preview, tex_bytes: bytes, scale: float) -> bytes:
    """按「现行转换口径 + 指定纹理字节/缩放」组装 .kpp。

    与 convert._texture_xml 的差异只有两处（正是本实验的自变量）：
      * pattern_filename 用内嵌字节 sha256 前 10 位（避免本机图案库撞名）；
      * png_bytes / scale 由调用方给定。
    其余字段全部沿用转换链口径：multiply=0、invert=ABR 原值、b=0、c=1、
    strength=1.0（depth=100）、use_soft_texturing=True（非高度系）。
    """
    res_name = "exp128_tex_" + _sha256(tex_bytes)[:10] + ".png"
    tex = TextureXml(
        pattern_filename=res_name,
        png_bytes=tex_bytes,
        scale=float(scale),
        brightness=0.0,
        contrast=1.0,
        invert=bool(bp.texture.invert),
        texturing_mode=0,
        use_soft_texturing=True,
        strength=1.0,
        strength_curve=None,
        strength_pressure=False,
    )
    xml = build_preset_xml(
        name=bp.name,
        brush_definition=brush_def,
        tip_png=None,
        tip_name=None,
        size_curve=bp.size_curve,
        opacity_curve=bp.opacity_curve,
        flow_curve=bp.flow_curve,
        ratio_curve=bp.ratio_curve,
        rotation_sensor=bp.rotation_sensor,
        rotation_jitter=bp.rotation_jitter,
        scatter=bp.scatter,
        scatter_pressure=bp.scatter_pressure,
        scatter_both_axes=bp.scatter_both_axes,
        scatter_amount=bp.scatter_amount,
        texture=tex,
    )
    return build_kpp(xml, preview)


@dataclass(frozen=True)
class Config:
    tex: str
    path: str
    scale: int
    repeat: int

    @property
    def stem(self) -> str:
        return "k128__%s__%s__s%d__r%d" % (self.tex, self.path, self.scale, self.repeat)

    @property
    def png_name(self) -> str:
        return self.stem + ".png"

    @property
    def kpp_name(self) -> str:
        return self.stem + ".kpp"


def build_configs(textures, scales, paths, repeats) -> list[Config]:
    out = []
    for tex in textures:
        for path in paths:
            for scale in scales:
                if path == "prescale" and scale == 100:
                    continue
                for rep in range(1, _repeats_for(scale, repeats) + 1):
                    out.append(Config(tex, path, int(scale), rep))
    return out


# ------------------------------------------------------------------ 锁（与团队协议一致：单槽 mkdir，陈旧 >60min 清理）

def acquire_lock(name: str, timeout_s: int = 2400, interval_s: float = 10.0) -> bool:
    lock = REPO / ".tmp" / name
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            os.mkdir(lock)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > 3600:
                    shutil.rmtree(lock, ignore_errors=True)
            except OSError:
                pass
            time.sleep(interval_s)
    return False


def release_lock(name: str) -> None:
    shutil.rmtree(REPO / ".tmp" / name, ignore_errors=True)


# ------------------------------------------------------------------ build

def cmd_build(textures, scales, paths, repeats) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    KPP_DIR.mkdir(parents=True, exist_ok=True)
    bp = load_base()
    brush_def, preview = brush_parts(bp)
    configs = build_configs(textures, scales, paths, repeats)
    print("base preset=%r computed=%s invert=%s" % (bp.name, bp.is_computed, bp.texture.invert))
    rows, volumes = [], {}
    for cfg in configs:
        src = texture_source_path(cfg.tex)
        if cfg.path == "raw":
            tex_bytes = embedded_raw_bytes(src, "%s:raw" % cfg.tex)
            written_scale = cfg.scale / 100.0
            out_px = None
        else:
            tex_bytes, out_px = embedded_prescale_bytes(src, cfg.scale, "%s:pre" % cfg.tex)
            written_scale = 1.0
        data = make_kpp_bytes(bp, brush_def, preview, tex_bytes, written_scale)
        (KPP_DIR / cfg.kpp_name).write_bytes(data)
        rows.append({"kpp": cfg.kpp_name, "tex": cfg.tex, "path": cfg.path,
                     "scale": cfg.scale, "repeat": cfg.repeat,
                     "written_scale": written_scale, "prescale_out_px": out_px,
                     "embedded_png_bytes": len(tex_bytes), "kpp_bytes": len(data)})
        volumes[(cfg.tex, cfg.path, cfg.scale)] = {"embedded_png_bytes": len(tex_bytes),
                                                   "kpp_bytes": len(data)}
        print("[build] %-52s png=%7d kpp=%8d scale=%.2f" %
              (cfg.kpp_name, len(tex_bytes), len(data), written_scale))
    # 体积增量（仅对同纹理同档 prescale vs raw 可算；s100 无 prescale）
    vol_rows = []
    for tex in textures:
        for scale in scales:
            a = volumes.get((tex, "raw", scale))
            b = volumes.get((tex, "prescale", scale))
            if not a or not b:
                continue
            vol_rows.append({
                "tex": tex, "scale": scale,
                "raw_png_bytes": a["embedded_png_bytes"], "pre_png_bytes": b["embedded_png_bytes"],
                "png_delta_bytes": b["embedded_png_bytes"] - a["embedded_png_bytes"],
                "raw_kpp_bytes": a["kpp_bytes"], "pre_kpp_bytes": b["kpp_bytes"],
                "kpp_delta_bytes": b["kpp_bytes"] - a["kpp_bytes"],
                "png_ratio": round(b["embedded_png_bytes"] / max(1, a["embedded_png_bytes"]), 3),
            })
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "geometry": {"canvas": list(CANVAS), "stroke": STROKE, "pressure": PRESSURE},
        "textures": {k: {"label": TEXTURES[k]["label"], "public": TEXTURES[k]["public"],
                         "source_png": str(texture_source_path(k))} for k in textures},
        "configs": rows, "volume": vol_rows,
    }
    BUILD_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("build 完成：%d 个 .kpp → %s" % (len(rows), KPP_DIR))
    print("体积增量明细：")
    for v in vol_rows:
        print("  %-4s s%-4d png %6d -> %7d (%+d, %.2fx) | kpp %8d -> %9d (%+d)"
              % (v["tex"], v["scale"], v["raw_png_bytes"], v["pre_png_bytes"],
                 v["png_delta_bytes"], v["png_ratio"], v["raw_kpp_bytes"],
                 v["pre_kpp_bytes"], v["kpp_delta_bytes"]))


# ------------------------------------------------------------------ PS 真值

def ps_truth_name(tex: str, scale: int, repeat: int) -> str:
    return "ps128__%s__d100_s%d_r%d.png" % (tex, scale, repeat)


def _photoshop_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Photoshop.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
    except Exception:
        return False
    return "Photoshop.exe" in out


def _kill_photoshop() -> None:
    subprocess.run(["taskkill", "-F", "-IM", "Photoshop.exe"], capture_output=True, timeout=60)


def _run_one_ps_texture(tex: str, scales, repeats, resume: bool) -> list[dict]:
    tex_path = texture_source_path(tex)
    jobs = []
    for s in scales:
        for rep in range(1, _repeats_for(s, repeats) + 1):
            name = ps_truth_name(tex, s, rep)
            if resume and (PS_DIR / name).is_file():
                continue
            jobs.append({"file": name, "mode_key": "multiply", "mode_kind": "char",
                         "mode_value": "Mltp", "use_texture": True, "invert_texture": True,
                         "texture_scale": int(s), "depth": 100, "brightness": 0,
                         "contrast": 0, "note": "multiply d100 s%d r%d" % (s, rep)})
    if not jobs:
        print("[ps] %s：真值已齐，跳过" % tex)
        return []
    if _photoshop_running():
        raise SystemExit("检测到 Photoshop 已在运行，放弃（不强杀别人的 PS）")
    work = PSWORK_DIR / tex
    work.mkdir(parents=True, exist_ok=True)
    PS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "out_dir": PS_DIR.as_posix(),
        "texture_path": tex_path.as_posix(),
        "texture_name": "bc128_%s" % tex,
        "texture_scale": 100,
        "abr_path": ABR_PATH.as_posix(),
        "brush_name": PS_BRUSH_NAME,
        "canvas": {"width": CANVAS[0], "height": CANVAS[1], "dpi": 72},
        "brush": {"diameter": 300, "hardness": 100, "spacing": 1000},
        "jobs": jobs,
    }
    (work / "job.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for p in (work / "done.json", work / "batch.log"):
        p.unlink(missing_ok=True)
    env = os.environ.copy()
    env["BRUSHCONVERTER_PS_WORK"] = work.as_posix()
    env["BRUSHCONVERTER_REPO"] = REPO.as_posix()
    t0 = time.time()
    proc = subprocess.Popen([str(PS_EXE), "-r", str(PS_JSX)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    deadline = time.time() + PS_TIMEOUT_S
    while time.time() < deadline:
        if (work / "done.json").exists():
            time.sleep(0.5)
            break
        if proc.poll() is not None and not (work / "done.json").exists():
            time.sleep(2.0)
            if (work / "done.json").exists():
                break
            log = work / "batch.log"
            tail = log.read_text(encoding="utf-8", errors="replace")[-2000:] if log.exists() else "<no log>"
            _kill_photoshop()
            raise SystemExit("PS 提前退出 rc=%s：%s" % (proc.returncode, tail))
        time.sleep(1.0)
    else:
        _kill_photoshop()
        raise SystemExit("PS 批超时 %ss" % PS_TIMEOUT_S)
    done = json.loads((work / "done.json").read_text(encoding="utf-8"))
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    _kill_photoshop()
    time.sleep(1.5)
    recs = done.get("results", [])
    ok = sum(1 for r in recs if r.get("ok"))
    print("[ps] %s：%d/%d 张 ok，耗时 %.1fs，PS 残留=%s"
          % (tex, ok, len(recs), time.time() - t0, _photoshop_running()))
    return recs


def cmd_render_ps(textures, scales, repeats, resume: bool) -> None:
    PS_DIR.mkdir(parents=True, exist_ok=True)
    if not acquire_lock("ps.lock"):
        raise SystemExit("等待 .tmp/ps.lock 超时（PS 单槽）")
    log = []
    try:
        for tex in textures:
            log.append({"texture": tex,
                        "results": _run_one_ps_texture(tex, scales, repeats, resume)})
    finally:
        release_lock("ps.lock")
    PS_LOG.write_text(json.dumps({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "batches": log}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("PS 真值批结束，日志：%s" % PS_LOG)


# ------------------------------------------------------------------ Krita 渲染

def cmd_render_krita(resume: bool = True, limit: int | None = None) -> None:
    if not BUILD_JSON.is_file():
        raise SystemExit("先执行 --build（找不到 %s）" % BUILD_JSON)
    payload = json.loads(BUILD_JSON.read_text(encoding="utf-8"))
    configs = [Config(r["tex"], r["path"], int(r["scale"]), int(r["repeat"]))
               for r in payload["configs"]]
    KRITA_DIR.mkdir(parents=True, exist_ok=True)
    log = {}
    if RENDER_LOG.is_file():
        try:
            log = json.loads(RENDER_LOG.read_text(encoding="utf-8"))
        except Exception:
            log = {}
    todo = []
    for cfg in configs:
        out = KRITA_DIR / cfg.png_name
        if resume and out.is_file():
            continue
        kpp = KPP_DIR / cfg.kpp_name
        if not kpp.is_file():
            raise SystemExit("缺少 .kpp：%s（请先 --build）" % kpp)
        todo.append(cfg)
    if limit:
        todo = todo[:limit]
    if not todo:
        print("[krita] 已全部渲染（resume）")
        return
    print("[krita] 待渲染 %d 张，等待 .tmp/krita.lock（单实例，串行）…" % len(todo), flush=True)
    if not acquire_lock("krita.lock"):
        raise SystemExit("等待 .tmp/krita.lock 超时")
    failed = 0
    started = time.time()
    try:
        for idx, cfg in enumerate(todo, 1):
            kpp = KPP_DIR / cfg.kpp_name
            out = KRITA_DIR / cfg.png_name
            t0 = time.time()
            res = runner.render_one(str(kpp), str(out), size=CANVAS, stroke=STROKE,
                                    pressure=PRESSURE, timeout=RENDER_TIMEOUT_S, route="gui")
            side = Path(str(out) + ".result.json")
            if side.is_file():
                side.unlink(missing_ok=True)
            ok = bool(res.get("ok"))
            failed += 0 if ok else 1
            log[cfg.png_name] = {
                "tex": cfg.tex, "path": cfg.path, "scale": cfg.scale, "repeat": cfg.repeat,
                "status": "rendered" if ok else "failed",
                "reason": res.get("reason"), "elapsed_s": round(time.time() - t0, 2),
                "alpha_nonzero": res.get("alpha_nonzero"),
            }
            print("[krita %d/%d] %-40s ok=%s %.1fs %s"
                  % (idx, len(todo), cfg.png_name, ok, time.time() - t0,
                     "" if ok else (res.get("reason") or "")[:120]), flush=True)
            RENDER_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        release_lock("krita.lock")
    print("[krita] 批结束：%d 张，失败 %d，总耗时 %.1fs" % (len(todo), failed, time.time() - started))


# ------------------------------------------------------------------ 分析

def _stats(pred: np.ndarray, ref: np.ndarray, tol: int = 2) -> dict:
    d = np.abs(pred.astype(np.int16) - ref.astype(np.int16))
    return {
        "diff_pixels": int(np.count_nonzero(d > tol)),
        "diff_ratio": float(np.count_nonzero(d > tol)) / float(d.size),
        "max_delta": int(d.max()),
        "mean_abs_delta": float(d.mean()),
        "tol0_ratio": float(np.count_nonzero(d > 0)) / float(d.size),
    }


def _rel_residual(side_s: np.ndarray, side_100: np.ndarray,
                  ps_s: np.ndarray, ps_100: np.ndarray) -> float | None:
    """相对残差 = mean|R_side-R_ps| / mean|R_ps|，R=Alpha(s)-Alpha(100)（exp_analyze 口径）。"""
    r_ps = ps_s.astype(np.float64) - ps_100.astype(np.float64)
    r_sd = side_s.astype(np.float64) - side_100.astype(np.float64)
    amp = float(np.abs(r_ps).mean())
    if amp <= 0:
        return None
    return float(np.abs(r_sd - r_ps).mean()) / amp


def _row_profile(alpha: np.ndarray) -> np.ndarray:
    return alpha.astype(np.float64).mean(axis=1)


def _phase_shift(a: np.ndarray, b: np.ndarray, max_shift: int = 8) -> dict:
    """两条 1D 剖面的最佳平移（行方向，单位 px）及平移前后差异。"""
    n = len(a)
    best = None
    for sh in range(-max_shift, max_shift + 1):
        if sh >= 0:
            aa, bb = a[sh:], b[:n - sh]
        else:
            aa, bb = a[:n + sh], b[-sh:]
        m = float(np.abs(aa - bb).mean())
        if best is None or m < best[0]:
            best = (m, sh)
    mae0 = float(np.abs(a - b).mean())
    return {"best_shift_px": int(best[1]), "mae_at_best": round(best[0], 3),
            "mae_at_shift0": round(mae0, 3)}


def cmd_analyze(textures, scales, paths, repeats) -> None:
    if not BUILD_JSON.is_file():
        raise SystemExit("先执行 --build")
    build = json.loads(BUILD_JSON.read_text(encoding="utf-8"))
    configs = [Config(r["tex"], r["path"], int(r["scale"]), int(r["repeat"]))
               for r in build["configs"]]

    def ps_path(tex: str, scale: int, rep: int) -> tuple[Path, str]:
        """真值解析：本批新渲 > 同档 r1 > 仓库既有真值（仅 r64，任务 12 口径）。"""
        for cand, kind in ((PS_DIR / ps_truth_name(tex, scale, rep), "new"),
                           (PS_DIR / ps_truth_name(tex, scale, 1), "new-r1")):
            if rep > 1 or kind == "new":
                if cand.is_file():
                    return cand, "new" if kind == "new" else "new-r1"
        exp = (REPO / "tests" / "baselines" / "exp" / "ps" /
               ("exp__ps__multiply__d100_s%d_b0_c0.png" % scale))
        if tex == "r64" and exp.is_file():
            return exp, "reuse-exp"
        return PS_DIR / ps_truth_name(tex, scale, rep), "missing"

    def kr_path(cfg: Config) -> Path:
        return KRITA_DIR / cfg.png_name

    rows = []
    for cfg in configs:
        p, truth_source = ps_path(cfg.tex, cfg.scale, cfg.repeat)
        k = kr_path(cfg)
        if not (p.is_file() and k.is_file()):
            rows.append({"tex": cfg.tex, "path": cfg.path, "scale": cfg.scale,
                         "repeat": cfg.repeat, "status": "missing",
                         "missing": [str(x) for x in (p, k) if not x.is_file()]})
            continue
        a_ps, a_kr = _alpha(p), _alpha(k)
        st = _stats(a_kr, a_ps)
        # 相对残差：每侧自比 s100 锚点（prescale 的 100% 与 raw 相同）
        base_ps_path, _base_src = ps_path(cfg.tex, 100, 1)
        base_kr = KRITA_DIR / Config(cfg.tex, "raw", 100, 1).png_name
        rel = None
        if base_ps_path.is_file() and base_kr.is_file():
            rel = _rel_residual(a_kr, _alpha(base_kr), a_ps, _alpha(base_ps_path))
        row = {"tex": cfg.tex, "path": cfg.path, "scale": cfg.scale, "repeat": cfg.repeat,
               "status": "compared", **st, "rel_residual": rel,
               "ps_file": p.name, "truth_source": truth_source, "krita_file": k.name}
        if cfg.tex == "step":
            row["phase"] = _phase_shift(_row_profile(a_kr), _row_profile(a_ps))
        rows.append(row)

    # 重复性（同配置 r1 vs r2 逐像素）
    repro = []
    for cfg in [c for c in configs if c.repeat == 1]:
        p2 = KRITA_DIR / Config(cfg.tex, cfg.path, cfg.scale, 2).png_name
        p1 = kr_path(cfg)
        if p2.is_file() and p1.is_file():
            st = _stats(_alpha(p2), _alpha(p1))
            repro.append({"tex": cfg.tex, "path": cfg.path, "scale": cfg.scale,
                          "repeat_max_delta": st["max_delta"],
                          "repeat_diff_ratio": st["diff_ratio"]})
    # 判据统计
    compared = [r for r in rows if r["status"] == "compared"]
    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "geometry": build["geometry"],
        "criteria": {"pixel_level": "diff_ratio<=2% and max_delta<=2 (tol=2, 分母=全部像素)"},
        "rows": rows, "reproducibility": repro,
    }
    for path in paths:
        rs = [r for r in compared if r["path"] == path]
        summary.setdefault("by_path", {})[path] = {
            "count": len(rs),
            "max_diff_ratio": max((r["diff_ratio"] for r in rs), default=None),
            "max_max_delta": max((r["max_delta"] for r in rs), default=None),
            "pass_count": sum(1 for r in rs if r["diff_ratio"] <= 0.02 and r["max_delta"] <= 2),
        }
    # 既有 repo 真值交叉校验（r64 的 s100/150/200 与 tests/baselines/exp/ps 对照）
    cross = []
    expdir = REPO / "tests" / "baselines" / "exp" / "ps"
    for s in (100, 150, 200):
        exp = expdir / ("exp__ps__multiply__d100_s%d_b0_c0.png" % s)
        cur = PS_DIR / ps_truth_name("r64", s, 1)
        if exp.is_file() and cur.is_file():
            cross.append({"scale": s, "exp_file": exp.name, "current_file": cur.name,
                          **_stats(_alpha(cur), _alpha(exp))})
    summary["crosscheck_against_existing_truth"] = cross
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    # 控制台表
    print("%-4s %-8s %5s %4s %8s %9s %9s %10s" %
          ("tex", "path", "scale", "rep", "diff%", "maxd", "mean|d|", "rel_resid"))
    for r in rows:
        if r["status"] != "compared":
            print("%-4s %-8s %5s %4s  缺失：%s" % (r["tex"], r["path"], r["scale"], r["repeat"],
                                                ",".join(x.split("\\")[-1] for x in r["missing"])))
            continue
        rel = "n/a" if r["rel_residual"] is None else "%.2f%%" % (r["rel_residual"] * 100.0)
        print("%-4s %-8s %5d %4d %7.2f%% %9d %9.2f %10s" %
              (r["tex"], r["path"], r["scale"], r["repeat"], r["diff_ratio"] * 100.0,
               r["max_delta"], r["mean_abs_delta"], rel))
    print("\n路径汇总：")
    for path, s in summary["by_path"].items():
        print("  %-8s n=%2d 最大差异比例=%.2f%% 最大 max_delta=%d 达标(<=2%%且<=2)=%d/%d"
              % (path, s["count"], (s["max_diff_ratio"] or 0) * 100, s["max_max_delta"] or 0,
                 s["pass_count"], s["count"]))
    if cross:
        print("\n与既有真值交叉校验（r64）：")
        for c in cross:
            print("  s%-4d 差异比例=%.2f%% max_delta=%d" % (c["scale"], c["diff_ratio"] * 100, c["max_delta"]))
    print("\n汇总已写：%s" % SUMMARY_JSON)


# ------------------------------------------------------------------ 入口

def _split(text: str) -> tuple:
    return tuple(x.strip() for x in str(text).split(",") if x.strip())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="任务 128 纹理缩放预缩放 A/B 实验")
    ap.add_argument("--textures", default="step,r64,tip")
    ap.add_argument("--scales", default=",".join(str(s) for s in DEFAULT_SCALES))
    ap.add_argument("--paths", default="raw,prescale")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--render-ps", action="store_true")
    ap.add_argument("--render-krita", action="store_true")
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ns = ap.parse_args(argv)
    textures = _split(ns.textures)
    for t in textures:
        if t not in TEXTURES:
            raise SystemExit("未知纹理键：%r（可选 %s）" % (t, ",".join(TEXTURES)))
    scales = tuple(int(s) for s in _split(ns.scales))
    paths = _split(ns.paths)
    for p in paths:
        if p not in ("raw", "prescale"):
            raise SystemExit("未知路径：%r（可选 raw,prescale）" % p)
    resume = not ns.no_resume
    rc = 0
    if ns.build or ns.all:
        cmd_build(textures, scales, paths, ns.repeats)
    if ns.render_ps or ns.all:
        cmd_render_ps(textures, scales, ns.repeats, resume)
    if ns.render_krita or ns.all:
        cmd_render_krita(resume=resume, limit=ns.limit)
    if ns.analyze or ns.all:
        cmd_analyze(textures, scales, paths, ns.repeats)
    if not any((ns.build, ns.render_ps, ns.render_krita, ns.analyze, ns.all)):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
