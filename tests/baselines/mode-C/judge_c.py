# -*- coding: utf-8 -*-
"""任务 36（C 批）：逐模式判读脚本（PS 真值 / Krita 渲染 / 掩码复刻 三方对齐）。

【判据（任务书 + 01-通用规范，禁止放宽）】
  达标 = 掩码空间差异比例 <= 2% 且 max_delta <= 2。
  * 「差异比例」= 差异像素数 / 全部像素数（分母是 W*H，与 tools/imgdiff.py 同口径）；
  * 差异像素 = |Δalpha| > 2（吸收 PNG 量化与抗锯齿抖动，与 imgdiff 默认 tol 一致）；
  * 饱和过滤：alpha_unique == 1（整片 255）的图无信息量，单列标记、不参与判定。

【三方是什么】
  1) PS 真值   tests/baselines/mode/ps/ps__mode__<序号>_<模式>__<组>.png（任务 33）
     + tests/baselines/mode-C/ps/（本任务补出：序号 32/33/34/35/36，改正枚举写法后重出）
  2) Krita 渲染 tests/baselines/mode/krita/krita__mode__<序号>_<模式>__<组>.png（任务 33）。
     只有 mode-align 里 krita_value 非空的 14 个模式有这张图。
  3) 掩码复刻 tools/texture_mask.py（KisTextureMaskInfo + KisMaskingBrushCompositeOp 的 numpy 复刻），
     参数取转换链当前口径：strength = convert._texture_strength(depth, blend)，
     brightness/contrast = preset_xml.texture_brightness_contrast(PS 值)，
     mode = mapping._TEXTURING_MODE[模式键]。base 取 1（单笔尖铺满画布）。

【为什么「PS 真值 vs 某个 Krita 模式的渲染」也要算】
  「无对应」模式在 Krita 里没有自己的枚举成员。判「能不能映射」的唯一办法是：
  把它的 PS 真值同组图的像素，与「若把它映射成 Krita 模式 X」会得到的真实 Krita
  渲染图逐像素比（X 取任务 33 已渲染的 14 个模式）。差异 <= 判据门槛 ⇒ 可映射；
  全都超门槛 ⇒ 不可映射（并附 Krita 枚举缺失的硬证据）。

用法：
    python tests/baselines/mode-C/judge_c.py            # 打印表格 + 写 judge-result.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from brush_converter.convert import _texture_strength          # noqa: E402
from brush_converter.kpp.preset_xml import texture_brightness_contrast  # noqa: E402
from brush_converter.mapping import _TEXTURING_MODE            # noqa: E402
from texture_mask import (compute_mask, texture_mask_from_rgba,  # noqa: E402
                          SUPPORTED_MODES)

MODE_DIR = REPO / "tests" / "baselines" / "mode"
PS_DIR = MODE_DIR / "ps"
KR_DIR = MODE_DIR / "krita"
C_PS_DIR = REPO / "tests" / "baselines" / "mode-C" / "ps"
BATCH_JSON = REPO / "docs" / "数据" / "mode-batch.json"
TEXTURE_PATH = REPO / "research" / "测试用纹理.png"
OUT_JSON = REPO / "tests" / "baselines" / "mode-C" / "judge-result.json"

TOL = 2                      # 差异像素阈值（与 imgdiff 默认一致）
RATIO_MAX = 0.02             # 差异比例门槛 2%
MAXDELTA_MAX = 2             # 最大差值门槛

GROUPS = ["d10_b0_c0", "d10_b-50_c-50", "d60_b0_c0", "d60_b-50_c-50",
          "d100_b0_c0", "d100_b-50_c-50"]
GROUP_PARAMS = {"d10_b0_c0": (10, 0, 0), "d10_b-50_c-50": (10, -50, -50),
                "d60_b0_c0": (60, 0, 0), "d60_b-50_c-50": (60, -50, -50),
                "d100_b0_c0": (100, 0, 0), "d100_b-50_c-50": (100, -50, -50)}


def sanitize(token: str) -> str:
    t = str(token).strip()
    t = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in t)
    return t or "unnamed"


def load_alpha(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.uint8)[:, :, 3]


def diff_arrays(a: np.ndarray, b: np.ndarray) -> dict:
    """与 tools/imgdiff.py 同口径的数组级比较（tol=2，分母 W*H）。"""
    if a.shape != b.shape:
        raise ValueError("尺寸不一致：%s vs %s" % (a.shape, b.shape))
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    total = a.size
    px = int(np.count_nonzero(d > TOL))
    return {"width": int(a.shape[1]), "height": int(a.shape[0]),
            "diff_pixels": px, "diff_ratio": px / total, "max_delta": int(d.max()),
            "mean_abs": float(d.mean())}


def verdict(r: dict) -> bool:
    return r["diff_ratio"] <= RATIO_MAX and r["max_delta"] <= MAXDELTA_MAX


def texture_mask_tiled(canvas: tuple[int, int] = (100, 100)) -> np.ndarray:
    """纹理位图 → 0..1 灰阶掩码，并按图案相位 (0,0) 平铺到画布尺寸。

    Krita/PS 都是「图案左上角对齐画布左上角、按图案尺寸平铺」，本实验
    Scale=1.0（100%）、Offset=(0,0)，因此直接 np.tile 后裁到画布大小。
    掩码公式见 tools/texture_mask.texture_mask_from_rgba（不在此处反相，
    反相由 apply_three 按源码顺序在亮度/对比度之后执行）。
    """
    with Image.open(TEXTURE_PATH) as im:
        rgba = np.asarray(im.convert("RGBA"), dtype=np.uint8)
    mask = texture_mask_from_rgba(rgba, invert=False)
    th, tw = mask.shape
    ch, cw = canvas
    reps = (ch // th + 1, cw // tw + 1)
    return np.ascontiguousarray(np.tile(mask, reps)[:ch, :cw])


def replica_alpha(mode_value: int, depth: int, brightness: int, contrast: int,
                  tex_mask: np.ndarray) -> np.ndarray | None:
    """按转换链当前口径算出「Krita 应有的 alpha」（0..255 uint8）。"""
    if mode_value not in SUPPORTED_MODES:
        return None
    b_k, c_k = texture_brightness_contrast(brightness, contrast)
    strength = _texture_strength(depth, "height" if mode_value in (14, 15) else "Mul ")
    mask = compute_mask(tex_mask, invert=True, brightness=b_k, contrast=c_k,
                        mode=mode_value, depth=strength, base=None)
    return np.clip(np.rint(mask * 255.0), 0, 255).astype(np.uint8)


def ps_path(order: int, member: str, g: str) -> Path | None:
    name = "ps__mode__%02d_%s__%s.png" % (order, sanitize(member), g)
    for base in (PS_DIR, C_PS_DIR):
        p = base / name
        if p.is_file():
            return p
    return None


def kr_path(order: int, member: str, g: str) -> Path | None:
    p = KR_DIR / ("krita__mode__%02d_%s__%s.png" % (order, sanitize(member), g))
    return p if p.is_file() else None


def kr_ref_paths(g: str) -> list[tuple[int, str, Path]]:
    """任务 33 已渲染的全部 Krita 基线（14 模式 × 该组），作为「若映射成 X」的候选。"""
    out = []
    for p in sorted(KR_DIR.glob("krita__mode__*__%s.png" % g)):
        stem = p.name[:-4]                            # krita__mode__<序号>_<模式>__<组>
        parts = stem.split("__")
        order_s, member = parts[2].split("_", 1)      # <序号>_<模式>
        out.append((int(order_s), member, p))
    return out


def main() -> int:
    batch = json.loads(BATCH_JSON.read_text(encoding="utf-8"))
    memb = next(b for b in batch["batches"] if b["batch"] == "C")["members"]
    tex = texture_mask_tiled()
    result = {"schema_version": "1.0", "task": "36", "batch": "C",
              "judge": {"tol": TOL, "ratio_max": RATIO_MAX, "max_delta_max": MAXDELTA_MAX,
                        "note": "差异比例分母 = W*H；alpha_unique<=1 记饱和、不参与判定"},
              "modes": []}
    for m in memb:
        order = m["order"]
        member = m["ps_member"]
        kv = m["krita_value"]
        rec = {"order": order, "ps_member": member, "runtime_id": m["runtime_id"],
               "krita_value": kv, "status": m["status"], "groups": {}}
        for g in GROUPS:
            d, b, c = GROUP_PARAMS[g]
            ps = ps_path(order, member, g)
            kr = kr_path(order, member, g)
            g_rec = {"depth": d, "brightness": b, "contrast": c,
                     "ps": None, "krita": None, "replica": None,
                     "ps_vs_krita": None, "ps_vs_replica": None, "krita_vs_replica": None,
                     "best_ref": None}
            pa = load_alpha(ps) if ps else None
            ka = load_alpha(kr) if kr else None
            if pa is not None:
                g_rec["ps"] = {"file": ps.relative_to(REPO).as_posix(),
                               "alpha_unique": int(len(np.unique(pa))),
                               "alpha_mean": round(float(pa.mean()), 3),
                               "saturated": bool(len(np.unique(pa)) <= 1)}
            if ka is not None:
                g_rec["krita"] = {"file": kr.relative_to(REPO).as_posix(),
                                  "alpha_unique": int(len(np.unique(ka))),
                                  "alpha_mean": round(float(ka.mean()), 3),
                                  "saturated": bool(len(np.unique(ka)) <= 1)}
            if pa is not None and ka is not None:
                r = diff_arrays(pa, ka)
                r["pass"] = verdict(r)
                g_rec["ps_vs_krita"] = r
            if kv is not None:
                ra = replica_alpha(int(kv), d, b, c, tex)
                if ra is not None:
                    g_rec["replica"] = {"mode": int(kv),
                                        "strength": round(_texture_strength(
                                            d, "height" if kv in (14, 15) else "Mul "), 4),
                                        "bc": list(texture_brightness_contrast(b, c))}
                    if pa is not None:
                        r = diff_arrays(pa, ra)
                        r["pass"] = verdict(r)
                        g_rec["ps_vs_replica"] = r
                    if ka is not None:
                        r = diff_arrays(ka, ra)
                        r["pass"] = verdict(r)
                        g_rec["krita_vs_replica"] = r
            if pa is not None:
                best = None
                for ref_order, ref_member, ref_path in kr_ref_paths(g):
                    ka2 = load_alpha(ref_path)
                    r = diff_arrays(pa, ka2)
                    cand = {"krita_order": ref_order, "krita_member": ref_member.strip(),
                            "diff_ratio": r["diff_ratio"], "max_delta": r["max_delta"],
                            "diff_pixels": r["diff_pixels"], "mean_abs": r["mean_abs"],
                            "pass": verdict(r)}
                    if best is None or (cand["diff_ratio"], cand["max_delta"]) < \
                            (best["diff_ratio"], best["max_delta"]):
                        best = cand
                g_rec["best_ref"] = best
            rec["groups"][g] = g_rec
        result["modes"].append(rec)

    # ---------- 打印表格 ----------
    def pf(v):
        return "  --  " if v is None else "%5.2f%%" % (v * 100)

    print("=" * 118)
    print("C 批 15 模式逐组判读（PS 真值 vs Krita 渲染 vs 掩码复刻；饱和=alpha_unique<=1）")
    print("=" * 118)
    for rec in result["modes"]:
        print()
        print("### 序号 %02d  PS=%r  krita_value=%s  status=%s"
              % (rec["order"], rec["ps_member"], rec["krita_value"], rec["status"]))
        print("  %-16s %-22s %-22s %-22s %-26s" %
              ("组", "PS(u/mean)", "Krita(u/mean)", "PS vs Krita", "PS vs 最佳 Krita 参考")
        )
        for g in GROUPS:
            r = rec["groups"][g]
            pss = "--" if not r["ps"] else "u=%d m=%.0f%s" % (
                r["ps"]["alpha_unique"], r["ps"]["alpha_mean"], "(饱和)" if r["ps"]["saturated"] else "")
            kss = "--" if not r["krita"] else "u=%d m=%.0f%s" % (
                r["krita"]["alpha_unique"], r["krita"]["alpha_mean"],
                "(饱和)" if r["krita"]["saturated"] else "")
            pk = r["ps_vs_krita"]
            pks = "%s max=%s %s" % (pf(pk["diff_ratio"]) if pk else "--",
                                    pk["max_delta"] if pk else "-",
                                    ("达标" if pk and pk["pass"] else "超阈") if pk else "") if pk else "--"
            br = r["best_ref"]
            brs = ("%s#%02d%s max=%s %s" % (pf(br["diff_ratio"]), br["krita_order"],
                                            br["krita_member"], br["max_delta"],
                                            "达标" if br["pass"] else "超阈")) if br else "--"
            print("  %-16s %-22s %-22s %-22s %-26s" % (g, pss, kss, pks, brs))
        print("  掩码复刻: " + " | ".join(
            "%s PS复刻=%s" % (g.replace("d", "d", 1),
                              ("%s max=%d" % (pf(rec["groups"][g]["ps_vs_replica"]["diff_ratio"]),
                                              rec["groups"][g]["ps_vs_replica"]["max_delta"]))
                              if rec["groups"][g]["ps_vs_replica"] else "--")
            for g in GROUPS))
        print("  复刻保真: " + " | ".join(
            "%s KR复刻=%s" % (g, ("%s max=%d" % (pf(rec["groups"][g]["krita_vs_replica"]["diff_ratio"]),
                                                  rec["groups"][g]["krita_vs_replica"]["max_delta"]))
                              if rec["groups"][g]["krita_vs_replica"] else "--")
            for g in GROUPS))

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print("[写出] %s" % OUT_JSON.relative_to(REPO).as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
