# -*- coding: utf-8 -*-
"""任务 34（A 批 16 模式）：逐模式判读脚本（PS 真值 / Krita 真机渲染 / 掩码复刻 三方对齐）。

【判据（任务书 + 01-通用规范，禁止放宽）】
  达标 = 掩码空间差异比例 <= 2% 且 max_delta <= 2。
  * 「差异比例」= 差异像素数 / 全部像素数（分母 W*H，与 tools/imgdiff.py 同口径）；
  * 差异像素 = |Δalpha| > 2（与 imgdiff 默认 tol=2 一致）；
  * 饱和过滤：alpha_unique <= 1 的真值图无信息量 → 单列标记，**不参与判定**
   （任务 33 README §七.2 的既定口径）。

【为什么用「已渲染的 14 个 Krita 模式」当候选】
  A 批 16 个模式里只有 3 个在 mode-align 里有 krita_value；其余 13 个是「Krita 枚举无对应」。
  判「能不能映射」的唯一硬办法：把它的 PS 真值同组像素，与「若映射成 Krita 模式 X」的真实
  渲染图逐像素比（X 遍历任务 33 已渲染的 14 个模式）。全部超门槛 ⇒ 不可映射（附枚举缺失证据）。
  这样候选侧全部是**真机像素**，不依赖任何公式建模。

【附带的公式族交叉验证】
  tools/texture_mask.py 只实现了 5 个算子、且没有 useSoftTexturing 分支，而本批 kpp 全部
  use_soft_texturing=true。因此本脚本另外用「已渲染真机图反核过」的软纹理算子族
  （soft: out = dst + (op(dst,src)-dst)*s，s=round(strength*255)/255）做一次复算，
  用于把「算子名」和「像素」对起来；判定仍以真机渲染候选为准。

用法：
    "$PY" tests/baselines/mode-A/judge_a.py            # 打印表格 + 写 judge-result.json
"""
from __future__ import annotations

import json
import math
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
from texture_mask import compute_mask, SUPPORTED_MODES         # noqa: E402

MODE_DIR = REPO / "tests" / "baselines" / "mode"
PS_DIR = MODE_DIR / "ps"
KR_DIR = MODE_DIR / "krita"
BATCH_JSON = REPO / "docs" / "数据" / "mode-batch.json"
MANIFEST = MODE_DIR / "manifest.json"
TEXTURE_PATH = REPO / "research" / "测试用纹理.png"
OUT_JSON = REPO / "tests" / "baselines" / "mode-A" / "judge-result.json"

TOL = 2
RATIO_MAX = 0.02
MAXDELTA_MAX = 2

GROUPS = ["d10_b0_c0", "d10_b-50_c-50", "d60_b0_c0", "d60_b-50_c-50",
          "d100_b0_c0", "d100_b-50_c-50"]
GROUP_PARAMS = {"d10_b0_c0": (10, 0, 0), "d10_b-50_c-50": (10, -50, -50),
                "d60_b0_c0": (60, 0, 0), "d60_b-50_c-50": (60, -50, -50),
                "d100_b0_c0": (100, 0, 0), "d100_b-50-c-50": (100, -50, -50),
                "d100_b-50_c-50": (100, -50, -50)}


def sanitize(token: str) -> str:
    t = str(token).strip()
    t = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in t)
    return t or "unnamed"


def load_alpha(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.uint8)[:, :, 3]


def diff_arrays(a: np.ndarray, b: np.ndarray) -> dict:
    if a.shape != b.shape:
        raise ValueError("尺寸不一致：%s vs %s" % (a.shape, b.shape))
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    px = int(np.count_nonzero(d > TOL))
    return {"width": int(a.shape[1]), "height": int(a.shape[0]),
            "diff_pixels": px, "diff_ratio": px / a.size,
            "max_delta": int(d.max()), "mean_abs": float(d.mean())}


def verdict(r: dict) -> bool:
    return r["diff_ratio"] <= RATIO_MAX and r["max_delta"] <= MAXDELTA_MAX


def tile_to(img: np.ndarray, h: int, w: int) -> np.ndarray:
    ih, iw = img.shape[:2]
    return img[np.ix_(np.arange(h) % ih, np.arange(w) % iw)]


def krita_texture_gray() -> np.ndarray:
    """纹理按 Krita 语义折成 0..255（KisTextureMaskInfo.cpp L184-190 的加权灰度）。"""
    with Image.open(TEXTURE_PATH) as im:
        a = np.asarray(im.convert("RGBA"), dtype=np.int64)
    gray = (a[..., 0] * 11 + a[..., 1] * 16 + a[..., 2] * 5) // 32
    al = a[..., 3].astype(np.float32) / 255.0
    return np.clip((gray.astype(np.float32) / 255.0) * al + (1 - al), 0, 1).astype(np.float32) * 255.0


def krita_mask(T: np.ndarray, brightness: float, contrast: float, invert: bool = True) -> np.ndarray:
    """Krita 掩码三步（亮→对比→反相），与 tools/texture_mask.apply_three 同语义（float32）。"""
    m = T.astype(np.float32) / np.float32(255.0)
    m = m - np.float32(brightness)
    m = (m - np.float32(0.5)) * np.float32(contrast) + np.float32(0.5)
    m = np.clip(m, 0.0, 1.0)
    if invert:
        m = np.float32(1.0) - m
    return np.clip(m, 0.0, 1.0).astype(np.float32)


def strength8(s: float) -> float:
    return math.floor(min(max(float(s), 0.0), 1.0) * 255.0 + 0.5) / 255.0


OPERATORS = {0: "MULTIPLY", 1: "SUBTRACT", 4: "DARKEN", 5: "OVERLAY",
             10: "HARD_MIX_PS", 14: "HEIGHT_PS", 15: "LIN_HEIGHT_PS"}


def soft_blend(dst: np.ndarray, src: np.ndarray, mode: int, strength: float) -> np.ndarray:
    """Krita KisMaskingBrushCompositeOp 的 use_soft_texturing=true 分支（已用真机渲染反核）。"""
    s = np.float32(strength8(strength))
    if mode == 0:
        comp = src * dst
    elif mode == 1:
        comp = np.maximum(0.0, dst - src)
    elif mode == 4:
        comp = np.minimum(dst, src)
    elif mode == 5:
        comp = np.where(dst <= 0.5, 2 * dst * src, 1 - 2 * (1 - dst) * (1 - src))
    elif mode == 10:
        comp = np.where(src + dst * s > 1, 1.0, 0.0)
    elif mode == 14:
        comp = np.clip(dst * (10 * s) - src, 0, 1)
    elif mode == 15:
        md = dst * (10 * s)
        comp = np.clip(np.maximum((1 - src) * md, md - src), 0, 1)
    else:
        raise ValueError(mode)
    return np.clip(dst + (comp - dst) * s, 0, 1).astype(np.float32)


def kr_ref_paths(g: str) -> list[tuple[int, str, int, Path]]:
    """任务 33 已渲染的全部 Krita 基线（14 模式 × 该组）= 候选算子集合。"""
    out = []
    for p in sorted(KR_DIR.glob("krita__mode__*__%s.png" % g)):
        parts = p.name[:-4].split("__")            # krita / mode / <序号>_<模式> / <组>
        if len(parts) != 4:
            continue
        order_s, member = parts[2].split("_", 1)
        out.append((int(order_s), member, 0, p))
    return out


def main() -> int:
    batch = json.loads(BATCH_JSON.read_text(encoding="utf-8"))
    memb = next(b for b in batch["batches"] if b["batch"] == "A")["members"]
    tex = krita_texture_gray()
    with Image.open(TEXTURE_PATH) as _im:
        tex_rgba = np.asarray(_im.convert("RGBA"), dtype=np.uint8)
    man = {(e["kind"], e["file"].split("/")[-1]): e
           for e in json.loads(MANIFEST.read_text(encoding="utf-8"))["entries"]}

    result = {"schema_version": "1.0", "task": "34", "batch": "A",
              "judge": {"tol": TOL, "ratio_max": RATIO_MAX, "max_delta_max": MAXDELTA_MAX,
                        "note": "差异比例分母 = W*H；alpha_unique<=1 记饱和、不参与判定；"
                                "候选 = 任务 33 已渲染的 14 个 Krita 模式真机图（同组参数）"},
              "modes": []}

    for m in memb:
        order, member, kv = m["order"], m["ps_member"], m["krita_value"]
        rec = {"order": order, "ps_member": member, "runtime_id": m["runtime_id"],
               "krita_value": kv, "status": m["status"], "groups": {},
               "ps_alpha_sha256": {}, "judgeable_groups": 0}
        for g in GROUPS:
            d, b, c = GROUP_PARAMS[g]
            name = "ps__mode__%02d_%s__%s.png" % (order, sanitize(member), g)
            ps_path = PS_DIR / name
            pa = load_alpha(ps_path)
            uniq = int(len(np.unique(pa)))
            rec["ps_alpha_sha256"][g] = man[("ps", name)]["alpha_sha256"]
            g_rec = {"depth": d, "brightness": b, "contrast": c,
                     "ps": {"file": ps_path.relative_to(REPO).as_posix(),
                            "alpha_unique": uniq, "alpha_mean": round(float(pa.mean()), 3),
                            "saturated": bool(uniq <= 1)},
                     "own_krita": None, "best_ref": None, "refs": {}}
            if not g_rec["ps"]["saturated"]:
                rec["judgeable_groups"] += 1

            # 1) 自有 Krita 渲染（只有 3 个模式有）
            own = KR_DIR / ("krita__mode__%02d_%s__%s.png" % (order, sanitize(member), g))
            if own.is_file():
                ka = load_alpha(own)
                r = diff_arrays(pa, ka)
                r["pass"] = verdict(r)
                r["file"] = own.relative_to(REPO).as_posix()
                g_rec["own_krita"] = r

            # 2) 候选：14 个已渲染模式（同组）
            best = None
            for ref_order, ref_member, _, ref_path in kr_ref_paths(g):
                ka2 = load_alpha(ref_path)
                r = diff_arrays(pa, ka2)
                cand = {"krita_order": ref_order, "krita_member": ref_member.strip(),
                        "diff_ratio": r["diff_ratio"], "max_delta": r["max_delta"],
                        "diff_pixels": r["diff_pixels"], "pass": verdict(r)}
                g_rec["refs"][cand["krita_member"]] = cand
                if best is None or (cand["diff_ratio"], cand["max_delta"]) < \
                        (best["diff_ratio"], best["max_delta"]):
                    best = cand
            g_rec["best_ref"] = best

            # 3) 掩码复刻（仅 3 个有 krita_value 的模式可复刻）
            #    hard = tools/texture_mask.py 原样（不含 useSoftTexturing 分支）
            #    soft = 本脚本的软纹理算子族（已用真机图反核，8/8 张 0.0000%）
            if kv is not None and int(kv) in SUPPORTED_MODES:
                from texture_mask import texture_mask_from_rgba
                b_k, c_k = texture_brightness_contrast(b, c)
                st = _texture_strength(d, "height" if int(kv) in (14, 15) else "Mul ")
                rgba100 = tile_to(tex_rgba, pa.shape[0], pa.shape[1])
                mask0 = texture_mask_from_rgba(rgba100, invert=False)
                m_hard = compute_mask(mask0, invert=True, brightness=b_k, contrast=c_k,
                                      mode=int(kv), depth=st, base=None)
                r = diff_arrays(pa, np.clip(np.rint(m_hard * 255.0), 0, 255).astype(np.uint8))
                r["pass"] = verdict(r)
                g_rec["replica_hard"] = r
                src_soft = krita_mask(tile_to(tex, pa.shape[0], pa.shape[1]), b_k, c_k, True)
                m_soft = soft_blend(np.ones_like(src_soft), src_soft, int(kv), st)
                r = diff_arrays(pa, np.clip(np.rint(m_soft * 255.0), 0, 255).astype(np.uint8))
                r["pass"] = verdict(r)
                g_rec["replica_soft"] = r
            rec["groups"][g] = g_rec
        result["modes"].append(rec)

    # ---- 逐模式汇总：找「全组达标」的候选 ----
    print("=" * 120)
    print("A 批 16 模式判读（PS 真值 vs Krita 真机候选；饱和组不参与判定；tol=2 / 门槛 2% & maxΔ<=2）")
    print("=" * 120)
    for rec in result["modes"]:
        names = sorted(rec["groups"][GROUPS[0]]["refs"])
        winners = []
        for nm in names:
            ok = True
            any_judged = False
            for g in GROUPS:
                gr = rec["groups"][g]
                if gr["ps"]["saturated"]:
                    continue
                any_judged = True
                if not gr["refs"][nm]["pass"]:
                    ok = False
                    break
            if ok and any_judged:
                winners.append(nm)
        rec["full_pass_candidates"] = winners
        print()
        print("### 序号 %02d  PS=%-6r krita_value=%-4s 可判组=%d/6  全组达标候选=%s"
              % (rec["order"], rec["ps_member"], rec["krita_value"],
                 rec["judgeable_groups"], winners if winners else "无"))
        for g in GROUPS:
            gr = rec["groups"][g]
            ps = gr["ps"]
            tag = "饱和(不判)" if ps["saturated"] else "u=%d m=%.0f" % (ps["alpha_unique"], ps["alpha_mean"])
            own = gr["own_krita"]
            own_s = ("%.2f%%/max%d" % (own["diff_ratio"] * 100, own["max_delta"])) if own else "--"
            br = gr["best_ref"]
            br_s = "%s#%02d %.2f%%/max%d" % (br["krita_member"], br["krita_order"],
                                             br["diff_ratio"] * 100, br["max_delta"])
            print("  %-16s PS[%-14s] 自有Krita=%-14s 最佳候选=%-26s" % (g, tag, own_s, br_s))

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print("[写出] %s" % OUT_JSON.relative_to(REPO).as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())