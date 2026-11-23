#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""任务 130：硬度剖面实验（F-8 主项）——auto_brush + fade vs 生成采样笔尖位图。

回答：PS 的硬度剖面（Hrdn h=0/50/100 的径向 alpha）能不能「烘成采样笔尖位图」，
再由 Krita 的 png_brush 精确复现？若可以，落地时代价是什么？

两条路径（各 h=0/50/100 三点，共 6 组；每组都对照同一张 PS 真值图）：
  A（现状）：auto_brush（计算笔尖）+ hfade/vfade = Hrdn/100；
  B（候选）：从 PS 真值图提取径向剖面 -> 生成正方形 PNG 位图（255 = 墨）
            -> 内嵌到 .kpp 的 png_brush（scale = 1.0，渲染足印 = 位图边长）。
B 另出一个对照变体 B200（把同一剖面压进 200px 位图 = 保持 Krita 尺寸字段
仍为 PS 标称 200），用来量化「不改尺寸语义」的代价。

PS 真值复用入库图（不启动 Photoshop）：
  docs/数据/偏差/ps/hardness__computed__v{0,50,100}.png
  由 tools/measure_deviation.py --param hardness 生成（画布 400x400、单点、
  定量测试用 Dmtr=200 / Rndn=100 / Spcn=500%）。

判据（任务书 §四 / 126 F-8）：几何级 过渡带宽绝对差 <= 2px 或相对差 <= 2%。

【Krita png_brush 的灰度极性（本任务实测新增，写进报告 §三.1）】
Krita 5.3.3 对内嵌 8-bit 灰度 PNG 的笔尖按 **255 - 灰度值** 当不透明度：
实测「全 255 位图渲染为全透明、全 0 位图渲染为全墨」（.tmp/130/polarity.log），
且本次 B 组渲染的 alpha 与位图灰度逐半径满足 α_渲染 + 灰度 = 255。
所以路径 B 的位图必须按「反相」烘：α=0 的笔尖外围写成 255（这同时满足任务书
「边框 255」的字面要求），α=255 的墨芯写成 0。用 --tip-polarity direct 可关掉反相复现旧口径。

复跑（先按团队协议持有 Krita 单实例锁 .tmp/krita.lock，再 --render）：
    "<conda>/python.exe" tools/exp_hardness_profile.py --path both --hardness 0,50,100 --render
    "<conda>/python.exe" tools/exp_hardness_profile.py --path both --hardness 0,50,100   # 只重新分析已有渲染
    "<conda>/python.exe" tools/exp_hardness_profile.py --profiles                        # 只打印剖面
产物：.tmp/130/{tips,render,kpp,ps_profiles.json,results.json,summary.md}
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from brush_converter import convert as conv_mod                    # noqa: E402
from brush_converter.kpp import preset_xml                        # noqa: E402
from brush_converter.kpp.kpp_writer import (                      # noqa: E402
    build_kpp, read_preset_xml, render_tip_preview,
)
import measure_deviation as md                                    # noqa: E402

WORK = REPO / ".tmp" / "130"
TIP_DIR = WORK / "tips"
KPP_DIR = WORK / "kpp"
RENDER_DIR = WORK / "render"
PS_DIR = REPO / "docs" / "数据" / "偏差" / "ps"

#: PS 真值口径（与 tools/measure_deviation.py 的 hardness 点一致）
CANVAS = (400, 400)
STROKE = 1
PRESSURE = 1.0
DIAMETER = 200.0
SPACING_PCT = 500.0
ROUNDNESS = 100.0
ANGLE = 0.0

#: 判据（任务书 §四：几何级 <=2px 或 <=2%）
ABS_PX_TOL = 2.0
REL_TOL = 0.02
BBOX_THR = 2

PROFILE_STEP = 0.5
PROFILE_MIN_COUNT = 8

#: Krita 5.3.3 png_brush 的灰度极性：inverted = 255 - 灰度 当不透明度（实测，见模块 docstring）。
TIP_POLARITY_DEFAULT = "inverted"


def log(msg: str) -> None:
    print(msg, flush=True)


def fmt_h(h: float) -> str:
    return "%g" % float(h)


def ps_truth(h: float) -> Path:
    return PS_DIR / ("hardness__computed__v%s.png" % fmt_h(h))


# --------------------------------------------------------------------------- #
# 一、从 PS 真值图提取径向 alpha 剖面（只读入库图）
# --------------------------------------------------------------------------- #


def radial_profile(alpha: np.ndarray) -> dict:
    """按质心做径向 alpha 中位数采样，返回单调不增的剖面。

    只取 alpha>0 的像素：PS 真值图的画布是 400x400，空区不参与。
    step=0.5px、每 bin 至少 8 个像素才采信（外圈像素少时靠中位数抗噪）。
    """
    mask = alpha > 0
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        raise ValueError("PS 真值图全透明，无法提取剖面")
    w = alpha[ys, xs].astype(np.float64)
    cy = float((ys * w).sum() / w.sum())
    cx = float((xs * w).sum() / w.sum())
    yy, xx = np.mgrid[0:alpha.shape[0], 0:alpha.shape[1]]
    r = np.hypot(xx - cx, yy - cy)[mask]
    a = alpha[mask].astype(np.float64)
    bins = np.arange(0.0, float(max(alpha.shape)) + PROFILE_STEP, PROFILE_STEP)
    idx = np.digitize(r, bins)
    rs: list[float] = []
    av: list[float] = []
    for i, b in enumerate(bins):
        m = idx == i
        if int(m.sum()) >= PROFILE_MIN_COUNT:
            rs.append(float(b) + PROFILE_STEP / 2.0)
            av.append(float(np.median(a[m])))
    if len(rs) < 4:
        raise ValueError("径向剖面有效点不足（%d）" % len(rs))
    # 单调不增：从中心向外逐步取「到此为止的最小值」，避免外圈噪声抬升
    for i in range(1, len(av)):
        av[i] = min(av[i], av[i - 1])
    return {"cx": cx, "cy": cy, "r": rs, "a": av}


def profile_at(prof: dict, r: float) -> float:
    return float(np.interp([r], prof["r"], prof["a"], left=prof["a"][0], right=0.0)[0])


def tip_geometry(prof: dict, alpha: np.ndarray) -> dict:
    """定「可见半径」与 B 主variant 的位图边长。

    r_vis = 包围盒（alpha>2）最长边的一半 —— 与 106/123 的「PS 软边外扩」同口径；
    edge = 2*ceil(r_vis)+2（偶数、且四周各留 1px 的 0 边，保证位图边长恰好等于
    渲染足印、边缘 alpha 自然衰减到 0，不需要任何 255 补白）。
    """
    x0, y0, x1, y1 = md.bbox_alpha(alpha, thr=BBOX_THR)
    r_vis = max(x1 - x0 + 1, y1 - y0 + 1) / 2.0
    edge = int(2 * math.ceil(r_vis) + 2)
    return {"bbox": [x0, y0, x1, y1], "bbox_w": int(x1 - x0 + 1),
            "bbox_h": int(y1 - y0 + 1), "r_vis": r_vis, "edge": edge}


# --------------------------------------------------------------------------- #
# 二、生成采样笔尖位图（正方形；边框不触发 255 补白）
# --------------------------------------------------------------------------- #


def build_tip(prof: dict, edge: int, name_scale: float,
             polarity: str = TIP_POLARITY_DEFAULT) -> np.ndarray:
    """按径向剖面生成 edge×edge 灰度位图。

    name_scale：像素半径 -> 原始（PS 图）半径的换算系数。
      - B 主variant（足印保真）：1.0，位图 1px 对应 PS 图 1px；
      - B200（尺寸保真）：r_vis / (edge/2)，把同一剖面径向压缩进 200px 位图。

    polarity：
      - "inverted"（默认，符合 Krita 5.3.3 实测）：存 255 - α，即墨芯=0、外围=255；
      - "direct"：存 α 本身（墨芯=255、外围=0），用于复现「按文档白=墨」的旧口径。
    """
    c = (edge - 1) / 2.0
    yy, xx = np.mgrid[0:edge, 0:edge].astype(np.float64)
    pr = np.hypot(xx - c, yy - c)
    rn = pr * name_scale
    a = np.interp(rn, prof["r"], prof["a"], left=prof["a"][0], right=0.0)
    # 最外 1px 环强制归零：裁掉的是 PS 自身 α<=2 的不可见尾巴（dab 质量 <0.5%），
    # 目的是让「反相后边框=255」整圈成立（任务书硬性要求 2：正方形 + 边框 255）。
    a = np.where(pr >= (edge / 2.0) - 1.0, 0.0, a)
    if polarity == "inverted":
        a = 255.0 - a
    elif polarity != "direct":
        raise SystemExit("未知 tip polarity：%r" % polarity)
    return np.clip(np.rint(a), 0, 255).astype(np.uint8)


def verify_tip(tip: np.ndarray, label: str, polarity: str = TIP_POLARITY_DEFAULT,
               expected_border: int | None = None) -> dict:
    """校验「正方形 + 补平方行为未被绕过 + 边框取值符合约定」。

    边框约定随极性：inverted 口径下外围=255（任务书「边框 255」的字面形态）、
    direct 口径下外围=0；两种都必须整圈一致（不允许裁切/漏填）。
    """
    h, w = tip.shape
    square = bool(h == w)
    straightened = conv_mod._square_tip(tip)
    no_pad = bool(straightened.shape == tip.shape and np.array_equal(straightened, tip))
    border = np.concatenate([tip[0, :], tip[-1, :], tip[:, 0], tip[:, -1]])
    corners = [int(tip[0, 0]), int(tip[0, -1]), int(tip[-1, 0]), int(tip[-1, -1])]
    if expected_border is None:
        expected_border = 255 if polarity == "inverted" else 0
    border_ok = bool(np.all(border == expected_border))
    if not (square and no_pad and border_ok):
        raise SystemExit(
            "位图校验失败[%s]：square=%s no_pad=%s border!=%d(uniform=%s)" % (
                label, square, no_pad, expected_border, border_ok))
    return {"label": label, "polarity": polarity, "shape": [int(h), int(w)],
            "square": square, "square_tip_noop": no_pad,
            "corners": corners, "border_value": int(expected_border),
            "border_uniform": border_ok,
            "nonzero_ratio": round(float(np.count_nonzero(tip) / tip.size), 4)}


# --------------------------------------------------------------------------- #
# 三、生成 .kpp（A = auto_brush；B = png_brush 内嵌位图）
# --------------------------------------------------------------------------- #


def make_computed_kpp(h: float, path: Path) -> dict:
    """路径 A：与发行版同一套 auto_brush 生成器（preset_xml.auto_brush_definition）。"""
    brush_def = preset_xml.auto_brush_definition(
        DIAMETER, SPACING_PCT / 100.0, ANGLE, ROUNDNESS, h)
    xml = preset_xml.build_preset_xml(
        name="bc130 A hardness %s" % fmt_h(h),
        brush_definition=brush_def,
        tip_png=None,
    )
    preview = render_tip_preview(conv_mod._circle_mask(h, ROUNDNESS))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_kpp(xml, preview))
    rb = read_preset_xml(path.read_bytes())
    import re
    def grab(pat: str) -> str:
        m = re.search(pat, rb)
        return m.group(1) if m else ""
    return {"kpp": str(path.relative_to(REPO)), "kpp_bytes": path.stat().st_size,
            "brush_type": grab(r'<Brush type="([^"]+)"'),
            "hfade": grab(r'hfade="([^"]+)"'),
            "diameter": grab(r'diameter="([^"]+)"'),
            "spacing": grab(r'spacing="([^"]+)"'),
            "embedded_png_bytes": 0}


def make_sampled_kpp(tip: np.ndarray, tag: str, path: Path,
                     polarity: str = TIP_POLARITY_DEFAULT) -> dict:
    """路径 B：png_brush + 内嵌位图（走发行版同一套 _square_tip/_png_bytes/XML 生成器）。

    scale = 1.0：位图原生 1:1 渲染 => 渲染足印（Krita 里的笔刷尺寸）= 位图边长。
    """
    tip_png = conv_mod._png_bytes(conv_mod._square_tip(tip))
    tip_name = "bc130_" + hashlib.sha256(tip_png).hexdigest()[:10]
    md5 = hashlib.md5(tip_png).hexdigest()
    brush_def = preset_xml.sampled_brush_definition(
        tip_name + ".png", md5, SPACING_PCT / 100.0, ANGLE, 1.0)
    xml = preset_xml.build_preset_xml(
        name="bc130 B %s" % tag,
        brush_definition=brush_def,
        tip_png=tip_png,
        tip_name=tip_name,
    )
    # 预览图始终是「白笔刷在透明底」（render_tip_preview 以 255 为墨）；反转极性时先把位图反回来
    preview_gray = (255 - tip) if polarity == "inverted" else tip
    preview = render_tip_preview(preview_gray)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_kpp(xml, preview))
    rb = read_preset_xml(path.read_bytes())
    import re
    def grab(pat: str) -> str:
        m = re.search(pat, rb)
        return m.group(1) if m else ""
    return {"kpp": str(path.relative_to(REPO)), "kpp_bytes": path.stat().st_size,
            "brush_type": grab(r'<Brush type="([^"]+)"'),
            "scale": grab(r'scale="([^"]+)"'),
            "spacing": grab(r'spacing="([^"]+)"'),
            "embedded_png_bytes": len(tip_png),
            "tip_png_sha256": hashlib.sha256(tip_png).hexdigest(),
            "tip_name": tip_name}


# --------------------------------------------------------------------------- #
# 四、渲染（串行：由调用方保证持有 .tmp/krita.lock）
# --------------------------------------------------------------------------- #


def krita_running() -> list:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq krita.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, timeout=20).stdout.decode("utf-8", "replace")
        return [ln for ln in out.splitlines() if ln.strip() and "krita.exe" in ln.lower()]
    except Exception:
        return []


def require_lock() -> None:
    lock = REPO / ".tmp" / "krita.lock"
    if not lock.is_dir():
        raise SystemExit("未持有 Krita 锁：%s 不存在（先按团队协议 mkdir 抢锁）" % lock)


def render_stable(kpp: Path, outbase: Path, max_tries: int = 3) -> dict:
    """渲染并取「IDAT 一致」的一组（任务书 §五：采样笔刷非确定时多渲一次取一致组）。

    返回 {png, idat, tries, elapsed_s, alpha_nonzero, idats:[...], mode}
    """
    outbase.parent.mkdir(parents=True, exist_ok=True)
    cands = []
    for i in range(1, max_tries + 1):
        out = outbase.with_name("%s__rep%d.png" % (outbase.stem, i))
        res = None
        for attempt in range(1, 4):
            res = md.runner.render_one(str(kpp), str(out), size=CANVAS, stroke=STROKE,
                                       pressure=PRESSURE, timeout=120.0)
            if res.get("ok"):
                break
            # 已知偶发：上一轮 Krita 未退干净时启动失败（"未写结果文件"/日志空）→ 等干净再试
            log("  [retry %d] %s 渲染失败：%s" % (attempt, out.name, res.get("reason")))
            time.sleep(4.0)
        if not res or not res.get("ok"):
            raise RuntimeError("Krita 渲染失败：%s（%s）日志尾部：%s" % (
                out.name, (res or {}).get("reason"), ((res or {}).get("log_tail") or "")[-600:]))
        sha = md.idat_sha256(out)
        cands.append({"png": out, "idat": sha, "alpha_nonzero": res.get("alpha_nonzero"),
                      "elapsed_s": res.get("elapsed_s")})
        if i >= 2 and cands[0]["idat"] == cands[1]["idat"]:
            break
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            if cands[i]["idat"] == cands[j]["idat"]:
                return {"png": cands[i]["png"], "idat": cands[i]["idat"],
                        "tries": len(cands), "mode": "identical-pair",
                        "idats": [c["idat"] for c in cands],
                        "alpha_nonzero": cands[i]["alpha_nonzero"],
                        "elapsed_s": cands[i]["elapsed_s"]}
    return {"png": cands[0]["png"], "idat": cands[0]["idat"], "tries": len(cands),
            "mode": "all-differ", "idats": [c["idat"] for c in cands],
            "alpha_nonzero": cands[0]["alpha_nonzero"],
            "elapsed_s": cands[0]["elapsed_s"]}


def render_group(item: dict) -> dict:
    kpp = KPP_DIR / item["kpp"]
    outbase = RENDER_DIR / item["key"]
    started = time.time()
    res = render_stable(kpp, outbase, max_tries=3 if item["path"] == "b" else 2)
    res["elapsed_s"] = round(time.time() - started, 2)
    log("[render] %-10s -> %s（%s，%.1fs，alpha=%s）" % (
        item["key"], res["png"].name, res["mode"], res["elapsed_s"], res["alpha_nonzero"]))
    return res


# --------------------------------------------------------------------------- #
# 五、量测与分析
# --------------------------------------------------------------------------- #


def measure_one(ps_file: Path, kt_file: Path) -> dict:
    ps = md.load_alpha(ps_file)
    kt = md.load_alpha(kt_file)
    if ps.shape != kt.shape:
        raise ValueError("画布不一致：PS %s vs Krita %s" % (ps.shape, kt.shape))
    ps_edge = md.measure_edge(ps)
    kt_edge = md.measure_edge(kt)
    ps_w = md.measure_width(ps)
    kt_w = md.measure_width(kt)
    pixel = md.metrics_alpha(ps, kt)
    ps_bbox = md.bbox_alpha(ps, thr=BBOX_THR)
    kt_bbox = md.bbox_alpha(kt, thr=BBOX_THR)
    out = {"ps": {"edge_width": ps_edge.get("edge_width"),
                  "width50": ps_w.get("width50"),
                  "bbox": list(ps_bbox),
                  "bbox_w": int(ps_bbox[2] - ps_bbox[0] + 1) if ps_bbox[0] >= 0 else None,
                  "bbox_h": int(ps_bbox[3] - ps_bbox[1] + 1) if ps_bbox[0] >= 0 else None,
                  "alpha0_radius": (max(ps_bbox[2] - ps_bbox[0] + 1, ps_bbox[3] - ps_bbox[1] + 1) / 2.0
                                    if ps_bbox[0] >= 0 else None)},
           "kt": {"edge_width": kt_edge.get("edge_width"),
                  "width50": kt_w.get("width50"),
                  "bbox": list(kt_bbox),
                  "bbox_w": int(kt_bbox[2] - kt_bbox[0] + 1) if kt_bbox[0] >= 0 else None,
                  "bbox_h": int(kt_bbox[3] - kt_bbox[1] + 1) if kt_bbox[0] >= 0 else None,
                  "alpha0_radius": (max(kt_bbox[2] - kt_bbox[0] + 1, kt_bbox[3] - kt_bbox[1] + 1) / 2.0
                                    if kt_bbox[0] >= 0 else None)},
           "pixel": pixel}
    e_ps, e_kt = out["ps"]["edge_width"], out["kt"]["edge_width"]
    if e_ps and e_kt is not None:
        out["edge_abs_diff_px"] = round(abs(e_kt - e_ps), 3)
        out["edge_rel_diff"] = round(abs(e_kt - e_ps) / e_ps, 5)
        out["edge_pass_abs"] = bool(out["edge_abs_diff_px"] <= ABS_PX_TOL)
        out["edge_pass_rel"] = bool(out["edge_rel_diff"] <= REL_TOL)
        out["edge_pass"] = bool(out["edge_pass_abs"] or out["edge_pass_rel"])
    w_ps, w_kt = out["ps"]["width50"], out["kt"]["width50"]
    if w_ps and w_kt is not None:
        out["width50_abs_diff_px"] = round(abs(w_kt - w_ps), 3)
        out["width50_rel_diff"] = round(abs(w_kt - w_ps) / w_ps, 5)
    a_ps, a_kt = out["ps"]["alpha0_radius"], out["kt"]["alpha0_radius"]
    if a_ps and a_kt:
        out["alpha0_abs_diff_px"] = round(abs(a_kt - a_ps), 3)
        out["alpha0_rel_diff"] = round(abs(a_kt - a_ps) / a_ps, 5)
    return out


# --------------------------------------------------------------------------- #
# 六、主流程
# --------------------------------------------------------------------------- #


def plan_groups(hardness: list[float], paths: str, variants: list[str]) -> list[dict]:
    items: list[dict] = []
    for h in hardness:
        if paths in ("a", "both"):
            items.append({"key": "A_h%s" % fmt_h(h), "path": "a", "variant": "computed",
                          "h": h, "kpp": "bc130_A_h%s.kpp" % fmt_h(h)})
        if paths in ("b", "both"):
            for v in variants:
                items.append({"key": "B%s_h%s" % ("" if v == "main" else "size200", fmt_h(h)),
                              "path": "b", "variant": v, "h": h,
                              "kpp": "bc130_B%s_h%s.kpp" % ("" if v == "main" else "size200", fmt_h(h))})
    return items


def build_all(items: list[dict], polarity: str = TIP_POLARITY_DEFAULT) -> dict:
    """提取剖面、生成位图与 .kpp，打印校验信息。"""
    TIP_DIR.mkdir(parents=True, exist_ok=True)
    KPP_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)

    profiles: dict = {}
    for h in sorted({it["h"] for it in items}):
        p = ps_truth(h)
        if not p.is_file():
            raise SystemExit("PS 真值图缺失：%s（先跑 tools/measure_deviation.py --param hardness）" % p)
        alpha = md.load_alpha(p)
        prof = radial_profile(alpha)
        geo = tip_geometry(prof, alpha)
        profiles[fmt_h(h)] = {
            "ps_file": str(p.relative_to(REPO)),
            "centroid": [round(prof["cx"], 2), round(prof["cy"], 2)],
            "r": [round(v, 2) for v in prof["r"]],
            "a": [round(v, 1) for v in prof["a"]],
            "alpha_at": {("r%g" % (rr * 100.0)): round(profile_at(prof, rr * 100.0), 1)
                         for rr in (0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4)},
            "geometry": geo,
        }
    (WORK / "ps_profiles.json").write_text(
        json.dumps(profiles, ensure_ascii=False, indent=1), encoding="utf-8")

    tips: dict = {}
    kpps: dict = {}
    build_t0 = time.time()
    for it in items:
        h = it["h"]
        prof = radial_profile(md.load_alpha(ps_truth(h)))
        geo = profiles[fmt_h(h)]["geometry"]
        if it["path"] == "a":
            info = make_computed_kpp(h, KPP_DIR / it["kpp"])
            kpps[it["key"]] = info
            continue
        if it["variant"] == "main":
            edge = geo["edge"]
            scale_native = 1.0
        elif it["variant"] == "size200":
            edge = int(DIAMETER)
            scale_native = geo["r_vis"] / (edge / 2.0)
        else:
            raise SystemExit("未知 variant：%r" % it["variant"])
        tip = build_tip(prof, edge, scale_native, polarity)
        tip_path = TIP_DIR / ("%s_edge%d.png" % (it["key"], edge))
        Image.fromarray(tip, mode="L").save(tip_path)
        tips[it["key"]] = dict(verify_tip(tip, it["key"], polarity), edge=edge,
                               radial_scale=round(scale_native, 5),
                               png_bytes=tip_path.stat().st_size,
                               png=str(tip_path.relative_to(REPO)))
        kpps[it["key"]] = make_sampled_kpp(tip, "%s h%s" % (it["variant"], fmt_h(h)),
                                          KPP_DIR / it["kpp"], polarity)
    return {"profiles": profiles, "tips": tips, "kpps": kpps,
            "build_elapsed_s": round(time.time() - build_t0, 3)}


def do_render(items: list[dict], skip_existing: bool = False) -> dict:
    require_lock()
    running = krita_running()
    if running:
        raise SystemExit("检测到已在运行的 krita.exe（%d 个），拒绝渲染以免被单实例转发：%r"
                         % (len(running), running[:2]))
    out: dict = {}
    t0 = time.time()
    for it in items:
        if skip_existing:
            reps = sorted(RENDER_DIR.glob("%s__rep*.png" % it["key"]))
            if len(reps) >= 2 and md.idat_sha256(reps[0]) == md.idat_sha256(reps[1]):
                out[it["key"]] = {"png": reps[0], "idat": md.idat_sha256(reps[0]),
                                  "tries": len(reps), "mode": "skip-existing",
                                  "idats": [md.idat_sha256(p) for p in reps],
                                  "alpha_nonzero": None, "elapsed_s": 0.0}
                log("[render] %-10s 已有 IDAT 一致的两份，跳过" % it["key"])
                continue
        out[it["key"]] = render_group(it)
    log("[render] 全部完成：%d 组，%.1fs" % (len(items), time.time() - t0))
    return {"groups": {k: {kk: (str(vv) if isinstance(vv, Path) else vv)
                          for kk, vv in v.items()} for k, v in out.items()},
            "elapsed_s": round(time.time() - t0, 2)}


def analyze(items: list[dict], built: dict, render_info: dict) -> dict:
    rows = []
    for it in items:
        h = it["h"]
        kt = render_info["groups"].get(it["key"], {}).get("png")
        if not kt:
            continue
        kt_path = Path(kt)
        m = measure_one(ps_truth(h), kt_path)
        row = {"key": it["key"], "path": it["path"], "variant": it["variant"], "h": h,
               "kpp": it["kpp"], "krita_png": kt,
               "idats": render_info["groups"][it["key"]].get("idats"),
               "determinism": render_info["groups"][it["key"]].get("mode"),
               "render_elapsed_s": render_info["groups"][it["key"]].get("elapsed_s"),
               **m}
        if it["key"] in built["tips"]:
            tp = built["tips"][it["key"]]
            row["tip_edge"] = tp["edge"]
            row["tip_png_bytes"] = tp["png_bytes"]
            row["kpp_bytes"] = built["kpps"][it["key"]]["kpp_bytes"]
        else:
            row["kpp_bytes"] = built["kpps"][it["key"]]["kpp_bytes"]
        rows.append(row)
    return {"rows": rows}


def write_summary(result: dict) -> Path:
    rows = result["analysis"]["rows"]
    prof = result["build"]["profiles"]
    tips = result["build"]["tips"]
    lines: list[str] = []
    lines.append("# 130 硬度剖面实验 · 机器输出汇总（.tmp/130/summary.md）\n")
    lines.append("> 由 tools/exp_hardness_profile.py 自动生成；判据 几何级 <=2px 或 <=2%。\n")
    lines.append("\n## 1. PS 径向剖面（从入库真值图提取）\n")
    lines.append("| h | PS 包围盒 | r_vis(px) | α@0.2R | @0.4R | @0.6R | @0.8R | @1.0R | @1.2R | @1.4R | 位图边长 B主 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for h in sorted(prof, key=float):
        p = prof[h]
        g = p["geometry"]
        at = p["alpha_at"]
        lines.append("| %s | %d×%d | %.1f | %s | %s | %s | %s | %s | %s | %s | %d |" % (
            h, g["bbox_w"], g["bbox_h"], g["r_vis"],
            at["r20"], at["r40"], at["r60"], at["r80"], at["r100"], at["r120"], at["r140"],
            g["edge"]))
    lines.append("\n## 2. 六组主表（A = auto_brush+fade；B主 = 足印保真采样笔尖）\n")
    lines.append("| 组 | PS 过渡宽 | Krita 过渡宽 | Δpx | Δ% | PS 宽50 | KT 宽50 | 宽Δpx | 差异比例 | max_delta | 判定 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if r["key"].startswith("Bsize200"):
            continue
        lines.append("| %s | %.2f | %.2f | %+.2f | %+.2f%% | %s | %s | %s | %.2f%% | %d | %s |" % (
            r["key"], r["ps"]["edge_width"], r["kt"]["edge_width"],
            (r["kt"]["edge_width"] - r["ps"]["edge_width"]),
            100.0 * (r["kt"]["edge_width"] - r["ps"]["edge_width"]) / r["ps"]["edge_width"],
            _f(r["ps"]["width50"]), _f(r["kt"]["width50"]), _f(r.get("width50_abs_diff_px")),
            100.0 * r["pixel"]["diff_ratio"], r["pixel"]["max_delta"],
            "PASS" if r.get("edge_pass") else "FAIL"))
    lines.append("\n## 3. B 变体 size200（保持 Krita 尺寸字段 200 的代价）\n")
    lines.append("| 组 | PS 过渡宽 | Krita 过渡宽 | Δpx | Δ% | 判定 |")
    lines.append("|---|---|---|---|---|---|")
    for r in rows:
        if r["key"].startswith("Bsize200"):
            lines.append("| %s | %.2f | %.2f | %+.2f | %+.2f%% | %s |" % (
                r["key"], r["ps"]["edge_width"], r["kt"]["edge_width"],
                r["kt"]["edge_width"] - r["ps"]["edge_width"],
                100.0 * (r["kt"]["edge_width"] - r["ps"]["edge_width"]) / r["ps"]["edge_width"],
                "PASS" if r.get("edge_pass") else "FAIL"))
    lines.append("\n## 4. 位图与体积\n")
    lines.append("| 组 | 位图边长 | PNG 字节 | .kpp 字节 | IDAT 复现 |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        if "tip_edge" in r:
            lines.append("| %s | %d | %d | %d | %s |" % (
                r["key"], r["tip_edge"], r["tip_png_bytes"], r["kpp_bytes"], r["determinism"]))
    lines.append("\n## 5. α=0 半径（各自包围盒归一 + 绝对 px）\n")
    lines.append("| 组 | PS α0 半径 | KT α0 半径 | Δpx | Δ% |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        if r["key"].startswith("Bsize200"):
            continue
        lines.append("| %s | %.1f | %.1f | %+.2f | %+.2f%% |" % (
            r["key"], r["ps"]["alpha0_radius"], r["kt"]["alpha0_radius"],
            r["kt"]["alpha0_radius"] - r["ps"]["alpha0_radius"],
            100.0 * (r["kt"]["alpha0_radius"] - r["ps"]["alpha0_radius"]) / r["ps"]["alpha0_radius"]))
    path = WORK / "summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path



# --------------------------------------------------------------------------- #
# 七、生产路径实验（任务 150 B2/B3 + 151 N7）：PS 真值 vs 当前实现（v1.5.0 生产链）
# --------------------------------------------------------------------------- #
# 130（上文）的 A/B 双路径是「旧 auto_brush 对照 + 实验期手写位图生成器」；
# v1.5.0（133）落地后**生产路径**是 convert._render_preset 的 png_brush 分支。
# 本节的实验一律直接调用生产函数生成 .kpp，量的是「当前实现」而不是实验复刻。
#
# PS 真值由 .tmp/geom/ps_truth_geom.py 用 measure_deviation 的出图链一次出（串行），
# 命名 bcgeom__<exp>__D<直径>__h<硬度>__r<圆度>.png（画布为正方形）。

PROD_GRIDS: dict = {
    "b2": {"diameters": [200.0], "hardness": [25.0, 50.0, 75.0],
           "roundness": [50.0, 80.0], "canvas": 400, "shape": "ellipse",
           "note": "中间硬度（0/50/100 之间插值）× 椭圆（Rndn=50/80）剖面组合"},
    "b3": {"diameters": [19.0, 30.0, 50.0], "hardness": [0.0],
           "roundness": [100.0], "canvas": 160, "shape": "circle",
           "note": "小直径 h0 足印量化（D=19 公式 30px vs 理论 27.4px）"},
    "n7": {"diameters": [600.0, 800.0], "hardness": [95.0, 100.0],
           "roundness": [100.0], "canvas": 1000, "shape": "circle",
           "note": "h≥95 大直径过渡带（B1 过渡带模型 (D/200)×1.9−1.9 的失效边界）"},
}


def prod_names(exp: str, d: float, h: float, r: float) -> tuple[str, str]:
    stem = "bcgeom__%s__D%g__h%g__r%g" % (exp, d, h, r)
    return stem + ".png", stem + ".kpp"


def prod_items(experiments: list[str]) -> list[dict]:
    items: list[dict] = []
    for exp in experiments:
        grid = PROD_GRIDS.get(exp)
        if not grid:
            raise SystemExit("未知生产路径实验：%r（可选 %r）" % (exp, list(PROD_GRIDS)))
        for d in grid["diameters"]:
            for h in grid["hardness"]:
                for r in grid["roundness"]:
                    ps_name, kpp_name = prod_names(exp, d, h, r)
                    items.append({"exp": exp, "d": float(d), "h": float(h), "r": float(r),
                                  "canvas": int(grid["canvas"]), "shape": grid["shape"],
                                  "ps_file": ps_name, "kpp": kpp_name,
                                  "key": "%s_D%g_h%g_r%g" % (exp, d, h, r)})
    return items


def build_prod_kpp(item: dict, kpp_dir: Path) -> dict:
    """按 v1.5.0 生产链生成 .kpp：convert._render_preset 的 png_brush 分支。"""
    bp = copy.deepcopy(md.base_preset("computed"))
    # 与 130/131 同口径隔离：去掉与本次测量无关的动态/纹理/散布
    bp.size_curve = None
    bp.opacity_curve = None
    bp.flow_curve = None
    bp.ratio_curve = None
    bp.rotation_sensor = None
    bp.rotation_jitter = 0.0
    bp.texture = None
    bp.scatter = False
    bp.scatter_pressure = False
    bp.scatter_both_axes = False
    bp.scatter_amount = None
    bp.angle = 0.0
    bp.spacing = SPACING_PCT / 100.0
    bp.diameter = item["d"]
    bp.hardness = item["h"]
    bp.roundness = item["r"]
    fname, xml, preview = conv_mod._render_preset(bp, 0)
    data = build_kpp(xml, preview)
    kpp_dir.mkdir(parents=True, exist_ok=True)
    path = kpp_dir / item["kpp"]
    path.write_bytes(data)
    rb = read_preset_xml(path.read_bytes())

    def grab(pat: str) -> str:
        m = re.search(pat, rb)
        return m.group(1) if m else ""

    return {"kpp": str(path.relative_to(REPO)), "kpp_bytes": len(data),
            "preset_filename": fname,
            "brush_type": grab(r'<Brush type="([^"]+)"'),
            "scale_field": grab(r'<Brush[^>]*scale="([^"]+)"'),
            "spacing_field": grab(r'<Brush[^>]*spacing="([^"]+)"'),
            "preset_diameter": grab(r'name="diameter"[^>]*>([^<]*)<'),
            "embedded_png_bytes": len(data)}


def prod_render_one(item: dict, kpp_path: Path, out_path: Path,
                    determinism: bool = False) -> dict:
    """渲染一张（可选重复一次做 IDAT 一致性检查）；调用方须持 .tmp/krita.lock。"""
    reps = []
    for i in range(1, (2 if determinism else 1) + 1):
        out = out_path if i == 1 else out_path.with_name(
            out_path.stem + "__rep2" + out_path.suffix)
        res = None
        for attempt in range(1, 4):
            res = md.runner.render_one(str(kpp_path), str(out),
                                       size=(item["canvas"], item["canvas"]),
                                       stroke=STROKE, pressure=PRESSURE, timeout=180.0)
            if res.get("ok"):
                break
            log("  [retry %d] %s 渲染失败：%s" % (attempt, out.name, res.get("reason")))
            time.sleep(4.0)
        if not res or not res.get("ok"):
            raise RuntimeError("Krita 渲染失败：%s（%s）日志尾部：%s" % (
                out.name, (res or {}).get("reason"),
                ((res or {}).get("log_tail") or "")[-600:]))
        reps.append({"png": out, "idat": md.idat_sha256(out),
                     "alpha_nonzero": res.get("alpha_nonzero"),
                     "elapsed_s": res.get("elapsed_s")})
    info = {"png": reps[0]["png"], "idat": reps[0]["idat"],
            "alpha_nonzero": reps[0]["alpha_nonzero"],
            "elapsed_s": reps[0]["elapsed_s"]}
    if determinism:
        info["idat_rep2"] = reps[1]["idat"]
        info["deterministic"] = reps[0]["idat"] == reps[1]["idat"]
    return info


def _axis_metrics(alpha: np.ndarray, shape: str) -> dict:
    """主/短轴 10%→90% 过渡宽 + 包围盒尺寸。

    主轴 = 水平（angle=0、Rndn<100 时 PS 长轴水平）；短轴 = 转置后同口径量测。
    圆度=100 时两轴同值，仍并列给出作对照。
    """
    edge_major = md.measure_edge(alpha).get("edge_width")
    edge_minor = md.measure_edge(alpha.T).get("edge_width")
    x0, y0, x1, y1 = md.bbox_alpha(alpha, thr=BBOX_THR)
    return {
        "edge_major_px": None if edge_major is None else round(edge_major, 3),
        "edge_minor_px": None if edge_minor is None else round(edge_minor, 3),
        "bbox_w": int(x1 - x0 + 1) if x0 >= 0 else None,
        "bbox_h": int(y1 - y0 + 1) if y0 >= 0 else None,
        "shape": shape,
    }


def _geom_judge(ps_v, kt_v) -> dict | None:
    if ps_v in (None, 0) or kt_v is None:
        return None
    absd = abs(float(kt_v) - float(ps_v))
    rel = absd / float(ps_v)
    return {"abs_px": round(absd, 3), "rel_pct": round(100.0 * rel, 2),
            "pass_abs": bool(absd <= ABS_PX_TOL),
            "pass_rel": bool(rel <= REL_TOL),
            "pass": bool(absd <= ABS_PX_TOL or rel <= REL_TOL)}


def run_production_experiments(experiments: list[str], render: bool,
                               prod_workdir: Path, ps_dir: Path) -> int:
    """B2/B3/N7：生产链 .kpp + PS 真值 → 主/短轴过渡宽、包围盒、像素差。"""
    items = prod_items(experiments)
    kpp_dir = prod_workdir / "kpp"
    render_dir = prod_workdir / "render"
    kpp_dir.mkdir(parents=True, exist_ok=True)
    render_dir.mkdir(parents=True, exist_ok=True)
    log("=== 生产路径实验：%d 组（%s）；PS 真值目录 %s ===" % (
        len(items), ",".join(experiments), ps_dir.relative_to(REPO)))
    info: dict = {}
    for it in items:
        it["ps_path"] = ps_dir / it["ps_file"]
        if not it["ps_path"].is_file():
            raise SystemExit("PS 真值缺失：%s（先跑 .tmp/geom/ps_truth_geom.py）" % it["ps_path"])
        b = build_prod_kpp(it, kpp_dir)
        it["kpp_info"] = b
        log("  [build] %-18s brush=%s scale=%s spacing=%s 位图=%sB" % (
            it["key"], b["brush_type"], b["scale_field"], b["spacing_field"],
            b["embedded_png_bytes"]))
    if render:
        require_lock()
        running = krita_running()
        if running:
            raise SystemExit("检测到已在运行的 krita.exe（%d 个），拒绝渲染：%r"
                             % (len(running), running[:2]))
        seen_det: set = set()
        for it in items:
            out = render_dir / ("%s__prod.png" % it["key"])
            det = it["exp"] not in seen_det
            r = prod_render_one(it, kpp_dir / it["kpp"], out, determinism=det)
            seen_det.add(it["exp"])
            r["png"] = str(r["png"])
            info[it["key"]] = r
            log("[render] %-18s -> %s（%.1fs，alpha=%s%s）" % (
                it["key"], Path(r["png"]).name, r["elapsed_s"], r["alpha_nonzero"],
                "，重复 IDAT=%s" % ("一致" if r.get("deterministic") else "不一致")
                if det else ""))
    else:
        for it in items:
            cands = sorted(render_dir.glob("%s__prod*.png" % it["key"]))
            if cands:
                info[it["key"]] = {"png": str(cands[0]), "idat": md.idat_sha256(cands[0]),
                                   "alpha_nonzero": None, "elapsed_s": 0.0}
        log("[render] 未加 --render：复用 %d/%d 组已有渲染" % (len(info), len(items)))

    rows = []
    for it in items:
        r = info.get(it["key"])
        if not r:
            continue
        ps = md.load_alpha(it["ps_path"])
        kt = md.load_alpha(Path(r["png"]))
        if ps.shape != kt.shape:
            raise ValueError("画布不一致：PS %s vs Krita %s" % (ps.shape, kt.shape))
        m_ps = _axis_metrics(ps, it["shape"])
        m_kt = _axis_metrics(kt, it["shape"])
        row = {"key": it["key"], "exp": it["exp"], "diameter": it["d"], "hardness": it["h"],
               "roundness": it["r"], "canvas": it["canvas"], "shape": it["shape"],
               "ps_file": str(it["ps_path"].relative_to(REPO)),
               "krita_png": r["png"], "idat": r.get("idat"),
               "deterministic": r.get("deterministic"),
               "kpp": it["kpp_info"], "ps": m_ps, "kt": m_kt,
               "pixel": md.metrics_alpha(ps, kt)}
        row["major"] = _geom_judge(m_ps["edge_major_px"], m_kt["edge_major_px"])
        row["minor"] = _geom_judge(m_ps["edge_minor_px"], m_kt["edge_minor_px"])
        if m_ps["bbox_w"] and m_kt["bbox_w"]:
            row["bbox_w_diff_px"] = int(m_kt["bbox_w"] - m_ps["bbox_w"])
            row["bbox_h_diff_px"] = int(m_kt["bbox_h"] - m_ps["bbox_h"])
        if m_ps["bbox_w"] and m_ps["bbox_h"]:
            row["ps_axis_ratio"] = round(m_ps["bbox_w"] / m_ps["bbox_h"], 4)
            row["kt_axis_ratio"] = round(m_kt["bbox_w"] / m_kt["bbox_h"], 4)
            row["axis_ratio_diff_pct"] = round(
                100.0 * abs(row["kt_axis_ratio"] - row["ps_axis_ratio"]) / row["ps_axis_ratio"], 2)
        # B3 专用：生产公式 edge vs 理论 PS 足印（h0 的 r_vis/R=1.44）
        if it["exp"] == "b3":
            import brush_converter.hardness_tip as ht
            edge = ht.tip_edge(it["h"], it["d"])
            theory = ht.footprint_ratio(it["h"]) * it["d"]
            row["formula_edge_px"] = int(edge)
            row["theoretical_footprint_px"] = round(theory, 2)
            row["formula_vs_theory_pct"] = round(100.0 * (edge / theory - 1.0), 2)
            row["ps_bbox_max_px"] = max(m_ps["bbox_w"], m_ps["bbox_h"])
            row["ps_bbox_vs_theory_pct"] = round(
                100.0 * (row["ps_bbox_max_px"] / theory - 1.0), 2)
        rows.append(row)
        log("  %-18s PS 主/短 %s/%s  KT 主/短 %s/%s  主Δ%+.2fpx 短Δ%+.2fpx  判定=%s" % (
            it["key"], m_ps["edge_major_px"], m_ps["edge_minor_px"],
            m_kt["edge_major_px"], m_kt["edge_minor_px"],
            row["major"]["abs_px"] if row["major"] else float("nan"),
            row["minor"]["abs_px"] if row["minor"] else float("nan"),
            "PASS" if (row["major"] or {}).get("pass") else "FAIL"))

    result = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "argv": sys.argv[1:], "experiments": experiments,
              "ps_dir": str(ps_dir.relative_to(REPO)),
              "grids": {e: PROD_GRIDS[e] for e in experiments},
              "render": info, "rows": rows,
              "abs_px_tol": ABS_PX_TOL, "rel_tol": REL_TOL}
    path = prod_workdir / "results_hardness.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    md_path = prod_workdir / "summary_hardness.md"
    lines = ["# 150 B2/B3 + 151 N7 生产路径实验（.tmp/geom）\n",
             "> 生产链 = convert._render_preset 的 png_brush 分支；判据 几何级：绝对差 ≤2px 或相对差 ≤2%。\n",
             "\n| 组 | D | h | Rndn | 画布 | PS 主轴 | KT 主轴 | 主轴Δpx | 主轴Δ% | PS 短轴 | KT 短轴 | 短轴Δpx | 短轴Δ% | bboxΔw/h | 像素差% / max | 判定 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"]
    for r in rows:
        maj, mnr = r.get("major") or {}, r.get("minor") or {}
        lines.append("| %s | %g | %g | %g | %d | %s | %s | %s | %s | %s | %s | %s | %s | %+d/%+d | %.2f%% / %d | %s |" % (
            r["key"], r["diameter"], r["hardness"], r["roundness"], r["canvas"],
            r["ps"]["edge_major_px"], r["kt"]["edge_major_px"],
            maj.get("abs_px"), maj.get("rel_pct"),
            r["ps"]["edge_minor_px"], r["kt"]["edge_minor_px"],
            mnr.get("abs_px"), mnr.get("rel_pct"),
            r.get("bbox_w_diff_px"), r.get("bbox_h_diff_px"),
            100.0 * r["pixel"]["diff_ratio"], r["pixel"]["max_delta"],
            "PASS" if (maj.get("pass") and mnr.get("pass")) else "FAIL"))
    lines.append("\n## B3 公式核对（生产 tip_edge vs PS 理论足印）\n")
    lines.append("| D | h | 公式 edge | 理论足印 | 公式超理论 | PS bbox 最长边 | PS bbox vs 理论 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows:
        if r["exp"] != "b3":
            continue
        lines.append("| %g | %g | %d | %.2f | %+.2f%% | %d | %+.2f%% |" % (
            r["diameter"], r["hardness"], r["formula_edge_px"],
            r["theoretical_footprint_px"], r["formula_vs_theory_pct"],
            r["ps_bbox_max_px"], r["ps_bbox_vs_theory_pct"]))
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("[done] 结果 %s / 汇总 %s" % (path.relative_to(REPO), md_path.relative_to(REPO)))
    return 0


def _f(v) -> str:
    return "—" if v is None else ("%.2f" % float(v))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="任务130 硬度剖面实验（A/B）")
    ap.add_argument("--hardness", default="0,50,100", help="硬度档，逗号分隔")
    ap.add_argument("--path", default="both", choices=["a", "b", "both"], help="路径")
    ap.add_argument("--variant", default="main", choices=["main", "size200", "main,size200"],
                    help="B 路径变体（main = 足印保真；size200 = 保持 200 尺寸）")
    ap.add_argument("--render", action="store_true", help="真的启动 Krita 渲染（需持有 .tmp/krita.lock）")
    ap.add_argument("--skip-existing", action="store_true", help="已有 IDAT 一致两份的组跳过渲染")
    ap.add_argument("--profiles", action="store_true", help="只打印 PS 径向剖面后退出")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--tip-polarity", default="inverted", choices=["inverted", "direct"],
                    help="位图灰度极性：inverted=255-α（Krita 5.3.3 实测口径，默认）；direct=α")
    ap.add_argument("--experiment", default="",
                    help="生产路径实验（任务 150/151）：b2,b3,n7（逗号分隔）；跑 PS 真值 vs 当前实现")
    ap.add_argument("--prod-workdir", default="", help="生产路径实验产物目录（默认 .tmp/geom）")
    ap.add_argument("--ps-dir", default="", help="生产路径实验的 PS 真值目录（默认 <prod-workdir>/ps）")
    args = ap.parse_args(argv)

    global WORK, TIP_DIR, KPP_DIR, RENDER_DIR, TIP_POLARITY_DEFAULT
    WORK = Path(args.workdir).resolve() if args.workdir else (REPO / ".tmp" / "130")
    TIP_POLARITY_DEFAULT = args.tip_polarity
    TIP_DIR = WORK / "tips" / args.tip_polarity
    KPP_DIR = WORK / "kpp" / args.tip_polarity
    RENDER_DIR = WORK / "render" / args.tip_polarity

    if args.experiment:
        prod_workdir = (Path(args.prod_workdir).resolve() if args.prod_workdir
                        else (REPO / ".tmp" / "geom"))
        ps_dir = (Path(args.ps_dir).resolve() if args.ps_dir
                  else (prod_workdir / "ps"))
        experiments = [e.strip() for e in args.experiment.split(",") if e.strip()]
        return run_production_experiments(experiments, args.render, prod_workdir, ps_dir)

    hardness = [float(v) for v in args.hardness.split(",") if v.strip()]
    variants = [v.strip() for v in args.variant.split(",") if v.strip()]
    items = plan_groups(hardness, args.path, variants)

    log("=== 任务130 硬度剖面实验：%d 组（path=%s，variant=%s）===" % (len(items), args.path, variants))
    built = build_all(items, TIP_POLARITY_DEFAULT)
    log("[build] 位图 %d 张、.kpp %d 个，耗时 %.2fs" % (
        len(built["tips"]), len(built["kpps"]), built["build_elapsed_s"]))
    for key, tp in built["tips"].items():
        log("  位图 %-12s shape=%s png=%d B 校验=%s" % (
            key, tp["shape"], tp["png_bytes"],
            "OK(square,no-pad,border%d)" % tp["border_value"] if tp["square"] and tp["square_tip_noop"] else "FAIL"))
    if args.profiles:
        print(json.dumps(built["profiles"], ensure_ascii=False, indent=1))
        return 0

    render_info = {"groups": {}, "elapsed_s": 0.0}
    if args.render:
        render_info = do_render(items, skip_existing=args.skip_existing)
    else:
        # 复用已有渲染：按 group key 找最近一次 rep1（或首张）
        for it in items:
            if it["key"].startswith("B") and it["variant"] == "main":
                pat = "%s__rep" % it["key"]
            else:
                pat = "%s__rep" % it["key"]
            cands = sorted(RENDER_DIR.glob(pat + "*.png"))
            if cands:
                ids = [md.idat_sha256(p) for p in cands]
                render_info["groups"][it["key"]] = {
                    "png": str(cands[0]), "idat": ids[0], "idats": ids,
                    "mode": "reused", "alpha_nonzero": None, "elapsed_s": 0.0,
                }
        log("[render] 未加 --render：复用 %d 组已有渲染" % len(render_info["groups"]))

    analysis = analyze(items, built, render_info)
    result = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "argv": sys.argv[1:], "canvas": list(CANVAS), "diameter": DIAMETER,
              "spacing_pct": SPACING_PCT, "build": built,
              "render": render_info, "analysis": analysis}
    (WORK / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    summary = write_summary(result)
    log("[done] 结果 %s / 汇总 %s" % (WORK / "results.json", summary))
    for r in analysis["rows"]:
        log("  %-12s PS %.2f  KT %.2f  Δ%+.2fpx (%+.2f%%)  判定=%s" % (
            r["key"], r["ps"]["edge_width"], r["kt"]["edge_width"],
            r["kt"]["edge_width"] - r["ps"]["edge_width"],
            100.0 * (r["kt"]["edge_width"] - r["ps"]["edge_width"]) / r["ps"]["edge_width"],
            "PASS" if r.get("edge_pass") else "FAIL"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
