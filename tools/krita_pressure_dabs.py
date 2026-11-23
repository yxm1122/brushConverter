# -*- coding: utf-8 -*-
"""Krita 侧多档压感阶梯装置（任务 143 配套 / 145-147 双端对照用）。

做法：一次 Krita GUI 会话里，用 Node.paintLine 在同一张画布上按不同 y 行各画一条
等长笔迹，每行一个压力值（BC_AUTO_STROKES，插件/runner 已支持，任务 144 新增）。
这样 N 档压力从 N 次会话压到 1 次（单张 11–14s），且各行几何完全一致、便于逐行量测。

背景：**Krita 侧「压力」是脚本注入 paintLine(pressure, pressure2)，不是真实数位板**
（口径写进 144/145 报告）。

判读口径（与 PS 侧 tools/ps_pressure_ramp.py 的 dab 统计同构）：
- 纹理笔（棋盘纹理 + multiply）：暗块 alpha p5 → depth_eff ≈ (255 − p5)/2.55；
- 不透明度动态：行内 dab 中心区 alpha 中位数 = 255·（opacity 动态值）；
- hardMix：中心区 alpha=255 像素占比。
本工具只负责渲染 + 逐行 dab 统计并写 index（PS↔Krita 对照在报告脚本里按同一口径合并）。

渲染锁：本工具自抢 .tmp/krita.lock（mkdir；已存在且 mtime<60 分钟则等待，>60 分钟视为
陈旧可删；结束 rmdir）。**持锁期间不主动强杀 Krita**（手册 §二.3：不许强杀）。

用法：
    python tools/krita_pressure_dabs.py --preset .tmp/pressure/krita/kpp/kpp/000_bc_p_tex_min0.kpp \
        --pressures 0.17,0.35,0.53,0.70,0.88,0.97 --out-dir .tmp/pressure/krita/dabs
    python tools/krita_pressure_dabs.py --preset a.kpp --preset b.kpp --pressures ... --repeat
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import krita_render_runner as runner  # noqa: E402

LOCK_DIR = os.path.join(REPO_ROOT, ".tmp", "krita.lock")
LOCK_STALE_SECONDS = 3600
DEFAULT_ROW_STEP = 100


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_component(text):
    value = re.sub(r"^\d+[_-]", "", str(text))
    value = re.sub(r'[\\/:*?"<>|]', "_", value)
    value = re.sub(r"\s+", "_", value.strip())
    return re.sub(r"_+", "_", value).strip("_") or "unknown"


def acquire_lock(wait_timeout=1800, poll=20):
    started = time.time()
    while True:
        try:
            os.makedirs(LOCK_DIR)
            with open(os.path.join(LOCK_DIR, "owner.txt"), "w", encoding="utf-8") as fh:
                fh.write("task-6/krita_pressure_dabs pid=%d at=%s\n"
                         % (os.getpid(), time.strftime("%Y-%m-%dT%H:%M:%S")))
            return True
        except FileExistsError:
            try:
                age = time.time() - os.stat(LOCK_DIR).st_mtime
            except FileNotFoundError:
                continue
            if age > LOCK_STALE_SECONDS:
                print("[锁] .tmp/krita.lock 已存在 %.0f 分钟（>60 分钟视为陈旧），删除后重抢" % (age / 60.0))
                shutil.rmtree(LOCK_DIR, ignore_errors=True)
                continue
            if time.time() - started > wait_timeout:
                raise TimeoutError("等待 .tmp/krita.lock 超过 %ss（别人正在渲染）" % wait_timeout)
            print("[锁] .tmp/krita.lock 被占用（%.0f 秒前），%ss 后重试" % (age, poll))
            time.sleep(poll)


def release_lock():
    shutil.rmtree(LOCK_DIR, ignore_errors=True)


def parse_pressures(text):
    out = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        v = float(part)
        if not (0.0 < v <= 1.0):
            raise ValueError("压力档必须在 (0,1]：%r" % part)
        out.append(v)
    if not out:
        raise ValueError("--pressures 为空")
    return out


def analyze_rows(png_path, rows, row_step, stroke, patch_half=16):
    """逐行 dab 统计：每行（该压力）取所有 dab 的中心区统计量并汇总。"""
    import numpy as np
    from PIL import Image
    with Image.open(png_path) as im:
        alpha = np.array(im.convert("RGBA"))[:, :, 3].astype(np.int16)
    out = []
    for row in rows:
        y = int(row["y"])
        band = alpha[max(0, y - 40):y + 41, :]
        cols = (band > 0).sum(axis=0)
        runs = []
        inr = False
        s = None
        for x, w in enumerate(cols):
            if w > 1 and not inr:
                inr = True
                s = x
            elif w <= 1 and inr:
                inr = False
                runs.append((s, x - 1))
        if inr:
            runs.append((s, len(cols) - 1))
        vals = []
        for (a, b) in runs:
            if b - a < 20:
                continue
            c = (a + b) // 2
            patch = alpha[max(0, y - patch_half):y + patch_half + 1,
                          max(0, c - patch_half):c + patch_half + 1]
            if patch.size == 0:
                continue
            vals.append({
                "x": int(c),
                "dark_p5": float(np.percentile(patch, 5)),
                "light_p95": float(np.percentile(patch, 95)),
                "median": float(np.median(patch)),
                "frac255": round(float((patch >= 254).mean()), 4),
                "frac0": round(float((patch == 0).mean()), 4),
            })
        agg = None
        if vals:
            agg = {
                "n_dabs": len(vals),
                "dark_p5": round(float(np.mean([v["dark_p5"] for v in vals])), 2),
                "light_p95": round(float(np.mean([v["light_p95"] for v in vals])), 2),
                "median": round(float(np.mean([v["median"] for v in vals])), 2),
                "frac255": round(float(np.mean([v["frac255"] for v in vals])), 4),
                "frac0": round(float(np.mean([v["frac0"] for v in vals])), 4),
                "dabs": vals,
            }
        out.append({"pressure": row["pressure"], "pressure2": row.get("pressure2"),
                    "y": y, "runs": [[int(a), int(b)] for a, b in runs], "stats": agg})
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Krita 多档压感阶梯（一行一档）")
    parser.add_argument("--preset", action="append", required=True, help=".kpp 路径（可多次）")
    parser.add_argument("--pressures", default="0.17,0.35,0.53,0.70,0.88,0.97", help="压力档，逗号分隔")
    parser.add_argument("--stroke", type=int, default=1400, help="笔划长度 px（默认 1400）")
    parser.add_argument("--row-step", type=int, default=DEFAULT_ROW_STEP, help="行距 px（默认 100）")
    parser.add_argument("--margin", type=int, default=None, help="首行 y（默认 row_step//2）")
    parser.add_argument("--out-dir", default=os.path.join(REPO_ROOT, ".tmp", "pressure", "krita", "dabs"))
    parser.add_argument("--index", default=None, help="索引 JSON（默认 <out-dir>/index.json）")
    parser.add_argument("--timeout", type=float, default=120.0, help="单张渲染超时秒数（多行同样 120）")
    parser.add_argument("--gradient-end", type=float, default=None,
                        help="追加一行双端梯度：pressure=首档，pressure2=该值")
    parser.add_argument("--no-lock", action="store_true", help="调用方已持锁（不再抢/放）")
    ns = parser.parse_args(argv)

    pressures = parse_pressures(ns.pressures)
    margin = ns.margin if ns.margin is not None else ns.row_step // 2
    width = ns.stroke + 200
    n_rows = len(pressures) + (1 if ns.gradient_end is not None else 0)
    height = margin * 2 + ns.row_step * (n_rows - 1)
    size = (width, height)
    rows = []
    for i, p in enumerate(pressures):
        rows.append({"pressure": p, "y": margin + i * ns.row_step})
    if ns.gradient_end is not None:
        rows.append({"pressure": pressures[0], "pressure2": float(ns.gradient_end),
                     "y": margin + len(pressures) * ns.row_step})
    os.makedirs(ns.out_dir, exist_ok=True)
    index_path = ns.index or os.path.join(ns.out_dir, "index.json")
    took_lock = False
    if not ns.no_lock:
        acquire_lock()
        took_lock = True
    entries = []
    try:
        for preset in ns.preset:
            base = safe_component(os.path.splitext(os.path.basename(preset))[0])
            png = os.path.join(ns.out_dir, "%s__stage.png" % base)
            if os.path.isfile(png):
                os.remove(png)
            result = runner.render_one(
                preset, png, size=size, stroke=ns.stroke, pressure=pressures[0],
                timeout=ns.timeout, route="gui", strokes=rows,
            )
            sidecar = png + ".result.json"
            if os.path.isfile(sidecar):
                try:
                    os.remove(sidecar)
                except OSError:
                    pass
            entry = {
                "preset": os.path.basename(preset),
                "file": os.path.basename(png),
                "path": os.path.abspath(png),
                "ok": bool(result.get("ok")),
                "reason": None if result.get("ok") else (result.get("reason") or "")[:300],
                "size": "%dx%d" % (width, height),
                "stroke": ns.stroke,
                "row_step": ns.row_step,
                "rows": rows,
                "sha256": sha256_of(png) if os.path.isfile(png) else None,
                "alpha_nonzero": result.get("alpha_nonzero"),
                "elapsed_s": result.get("elapsed_s"),
            }
            if entry["ok"]:
                entry["analysis"] = analyze_rows(png, rows, ns.row_step, ns.stroke)
            entries.append(entry)
            print("[%s] ok=%s alpha=%s sha=%s" % (
                entry["preset"], entry["ok"], entry["alpha_nonzero"],
                (entry["sha256"] or "")[:12]))
            if entry.get("analysis"):
                for row in entry["analysis"]:
                    st = row["stats"]
                    print("   p=%.2f y=%3d %s" % (
                        row["pressure"], row["y"],
                        json.dumps(st, ensure_ascii=False)[:170] if st else "NO DABS"))
        index = {
            "schema_version": 1,
            "task": "143/145/146/147",
            "kind": "krita-pressure-stage",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "note": "Krita 侧压力为脚本注入 paintLine(pressure, pressure2)，非真实数位板",
            "canvas": {"width": width, "height": height},
            "pressures": pressures,
            "gradient_end": ns.gradient_end,
            "entries": entries,
        }
        with open(index_path, "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        print("索引已写：%s" % index_path)
        return 0 if all(e["ok"] for e in entries) else 1
    finally:
        if took_lock:
            release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
