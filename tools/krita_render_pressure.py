# -*- coding: utf-8 -*-
"""按压感档位的渲染入口（任务 14）。

**不改原渲染器**：本文件只在 conda 侧调用 `tools/krita_render_runner.py` 的 GUI 真实渲染路线
（真实 Krita 进程 + PyKrita 启动插件，插件把 `Node.paintLine(pressure, pressure)` 的压感传给笔刷）。
Krita 侧压感是否真实注入，由本脚本渲染后自动核验（四档图的 sha256 全相同 = 没生效）。

用法：
    python tools/krita_render_pressure.py \
        --preset "converted/kpp/003_大怪兽-圆头虚_用数位板.kpp" \
        --pressure-profile 0.1,0.25,0.4,0.55,0.7,0.85,1.0 --stroke-length 360 \
        --out-dir .tmp/pressure/krita --curve-out-dir .tmp/pressure/krita

任务 144（v0.17）补强：
- 默认档位改为 7 档 0.1/0.25/0.4/0.55/0.7/0.85/1.0（任务书口径）；
- `--repeat N`：每档渲染 N 次（各自独立 Krita 会话），索引里逐档记 sha256 列表，
  同档两次 sha256 相同 = 渲染确定性；不同档不同 = 压感真实注入；
- `--gradient-end`：额外出一张「单图梯度」——paintLine(pressure=首档, pressure2=末档)
  的双端压力图（插件/runner 已支持 BC_AUTO_PRESSURE2），并把逐列 alpha 剖面与
  「多档阶梯」逐档 alpha 做对照（两种口径写进同一索引）。

产物：每个档位一张 PNG（`feel__<笔刷>__p<压感>__s<尺寸>.png`，repeat>1 时带 `__r<次>`）
+ 一份索引 JSON +（可选）逐档曲线 JSON。

注意：**同一时刻只允许一个 Krita GUI 渲染会话**（批量/全量 pytest 不要与本脚本并发）。
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import krita_render_runner as runner  # noqa: E402
import feel_curve  # noqa: E402


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        digest.update(fh.read())
    return digest.hexdigest()


def safe_component(text):
    value = re.sub(r"^\d+[_-]", "", str(text))
    value = re.sub(r'[\\/:*?"<>|]', "_", value)
    value = re.sub(r"\s+", "_", value.strip())
    return re.sub(r"_+", "_", value).strip("_") or "unknown"


def parse_profile(text):
    grades = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        value = float(part)
        if not (0.0 < value <= 1.0):
            raise ValueError("压感档位必须在 (0, 1]：%r" % part)
        grades.append(value)
    if not grades:
        raise ValueError("--pressure-profile 为空")
    return grades


def simulate_from_base(base_png, out_png, grade):
    """降级方案：从 100% 压感图「模拟」档位（横向缩内容 + Alpha 乘系数）。

    仅在 Krita 侧压感注入失败（四档图完全相同）时使用；**这不是真压感**，
    索引里会标 `simulated=true`，报告与文档也会写明降级事实。
    """
    from PIL import Image

    with Image.open(base_png) as im:
        rgba = im.convert("RGBA")
        width, height = rgba.size
        scale = 0.4 + 0.6 * float(grade)          # 档位越低，笔迹越短
        new_width = max(1, int(round(width * scale)))
        resized = rgba.resize((new_width, height), Image.BILINEAR)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(resized, ((width - new_width) // 2, 0))
        pixels = canvas.load()
        alpha_scale = 0.3 + 0.7 * float(grade)    # 档位越低，越淡
        for y in range(height):
            for x in range(width):
                r, g, b, a = pixels[x, y]
                pixels[x, y] = (r, g, b, int(round(a * alpha_scale)))
        canvas.save(out_png, "PNG")
    return out_png


def _merge_manifest(manifest_path, ns, index, entries):
    """把手感基线条目合并进清单（同笔刷同档位覆盖，其余保留）。"""
    items = []
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                items = json.load(fh).get("items", [])
        except Exception:
            items = []
    keep = [it for it in items if not (it.get("brush") == index["brush"])]
    for entry in entries:
        keep.append({
            "preset": index["preset"],
            "brush": index["brush"],
            "pressure": entry["pressure"],
            "file": entry["file"],
            "curve_file": entry.get("curve_file"),
            "size": entry["size"],
            "stroke_length": entry["stroke_length"],
            "alpha_nonzero": entry["alpha_nonzero"],
            "sha256": entry["sha256"],
            "max_width_px": entry.get("max_width_px"),
            "mean_opacity_01": entry.get("mean_opacity_01"),
            "pressure_injected": index["pressure_injected"],
            "simulated": bool(entry.get("simulated", False)),
        })
    keep.sort(key=lambda it: (str(it.get("brush")), float(it.get("pressure", 0))))
    manifest = {
        "schema_version": 1,
        "task": "14",
        "kind": "feel-baselines",
        "note": "手感基线：3 支笔 × 4 个压感档位；曲线由 tools/feel_curve.py 提取，判定见 tools/feel_compare.py",
        "pressure_injected": index["pressure_injected"],
        "items": keep,
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("清单已合并：%s（现有 %d 条）" % (manifest_path, len(keep)))


def _alpha_image(path):
    """读 PNG → alpha ndarray（0..255）。"""
    from PIL import Image
    import numpy as np
    with Image.open(path) as im:
        return np.array(im.convert("RGBA"))[:, :, 3].astype(np.int16)


def _stroke_value(path):
    """阶梯图的口径：整条笔迹内部 alpha 均值（忽略 alpha=0 与最外 2px 边缘）。"""
    import numpy as np
    a = _alpha_image(path)
    nz = a > 0
    if not nz.any():
        return None
    cols = np.nonzero(nz.any(axis=0))[0]
    xs = slice(cols[0] + 2, cols[-1] - 1)
    sub = a[:, xs]
    vals = sub[sub > 0]
    return round(float(vals.mean()), 3) if vals.size else None


def _column_profile(path):
    """梯度图口径：逐列 alpha 均值（只对该列非零行求均值）。"""
    import numpy as np
    a = _alpha_image(path)
    nz = a > 0
    sums = a.sum(axis=0).astype(float)
    counts = nz.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        means = np.where(counts > 0, sums / np.maximum(counts, 1), 0.0)
    return [round(float(v), 2) for v in means.tolist()], int(counts.sum())


def main():
    parser = argparse.ArgumentParser(description="按压感档位渲染（复用 GUI 真实渲染路线）")
    parser.add_argument("--preset", required=True, help=".kpp 预设路径")
    parser.add_argument("--pressure-profile", default="0.1,0.25,0.4,0.55,0.7,0.85,1.0",
                        help="压感档位，逗号分隔，默认 7 档 0.1,0.25,0.4,0.55,0.7,0.85,1.0")
    parser.add_argument("--repeat", type=int, default=1, help="每档独立渲染次数（默认 1；2 = 复现性校验）")
    parser.add_argument("--gradient-end", type=float, default=None,
                        help="额外出一张双端压力梯度图：起点=首档压力，终点=该值（0–1）")
    parser.add_argument("--stroke-length", type=int, default=120, help="笔划长度（像素），默认 120")
    parser.add_argument("--size", default="200x200", help="画布尺寸，默认 200x200")
    parser.add_argument("--out-dir", default=os.path.join(REPO_ROOT, "tests", "baselines", "feel"), help="PNG 输出目录")
    parser.add_argument("--curve-out-dir", default=None, help="曲线 JSON 输出目录（省略则不提取曲线）")
    parser.add_argument("--index", default=None, help="索引 JSON 路径（默认 <out-dir>/<笔刷>__index.json）")
    parser.add_argument("--timeout", type=float, default=None, help="单档渲染超时秒数（默认 75）")
    parser.add_argument("--simulate-on-failure", action="store_true", help="压感注入失败时用模拟档位降级（会标注 simulated）")
    parser.add_argument("--manifest", default=None, help="手感基线清单 JSON（默认 <out-dir>/manifest.json，按笔刷+档位合并）")
    ns = parser.parse_args()

    size_parts = ns.size.lower().split("x")
    if len(size_parts) != 2:
        parser.error("--size 必须形如 200x200")
    width, height = int(size_parts[0]), int(size_parts[1])
    grades = parse_profile(ns.pressure_profile)
    if not os.path.isfile(ns.preset):
        print("[错误] 预设不存在：%s" % ns.preset, file=sys.stderr)
        return 2
    os.makedirs(ns.out_dir, exist_ok=True)
    if ns.curve_out_dir:
        os.makedirs(ns.curve_out_dir, exist_ok=True)

    brush = safe_component(os.path.splitext(os.path.basename(ns.preset))[0])
    index_path = ns.index or os.path.join(ns.out_dir, "%s__index.json" % brush)
    started_all = time.time()
    entries = []

    repeats = max(1, int(ns.repeat))
    for grade in grades:
        runs = []
        for rep in range(repeats):
            suffix = "" if repeats <= 1 else "__r%d" % rep
            png_name = "feel__%s__p%.2f__s%dx%d%s.png" % (brush, grade, width, height, suffix)
            png_path = os.path.join(ns.out_dir, png_name)
            result = runner.render_one(
                ns.preset, png_path, size=(width, height), stroke=ns.stroke_length,
                pressure=grade, timeout=ns.timeout, route="gui",
            )
            # 外壳会在 PNG 旁写 <png>.result.json 中间结果，这里读完就删，避免污染基线目录
            sidecar = png_path + ".result.json"
            if os.path.isfile(sidecar):
                try:
                    os.remove(sidecar)
                except OSError:
                    pass
            run = {
                "file": os.path.basename(png_path),
                "ok": bool(result.get("ok")),
                "alpha_nonzero": result.get("alpha_nonzero"),
                "elapsed_s": result.get("elapsed_s"),
                "sha256": sha256_of(png_path) if os.path.isfile(png_path) else None,
                "reason": None if result.get("ok") else (result.get("reason") or "")[:300],
            }
            runs.append(run)
        first = runs[0]
        entry = {
            "pressure": grade,
            "file": first["file"],
            "path": os.path.abspath(os.path.join(ns.out_dir, first["file"])),
            "stroke_length": ns.stroke_length,
            "size": "%dx%d" % (width, height),
            "ok": all(r["ok"] for r in runs),
            "alpha_nonzero": first["alpha_nonzero"],
            "elapsed_s": round(sum(r["elapsed_s"] or 0.0 for r in runs), 3),
            "sha256": first["sha256"],
            "reason": first["reason"],
            "runs": runs,
        }
        if repeats > 1:
            hashes = {r["sha256"] for r in runs if r["sha256"]}
            entry["repeat_runs"] = len(runs)
            entry["repeat_identical"] = len(hashes) == 1
        if entry["ok"] and ns.curve_out_dir:
            curve = feel_curve.extract_curves(entry["path"])
            curve_path = os.path.join(ns.curve_out_dir, "%s__p%.2f__curve.json" % (brush, grade))
            feel_curve.save_curve(curve, curve_path)
            entry["curve_file"] = os.path.basename(curve_path)
            entry["max_width_px"] = max(curve["width"])
            entry["mean_opacity_01"] = sum(curve["opacity"]) / len(curve["opacity"])
        entries.append(entry)
        print("[档位 %.2f] ok=%s alpha=%s elapsed=%.2fs repeat=%s" % (
            grade, entry["ok"], entry["alpha_nonzero"], entry["elapsed_s"] or 0.0,
            entry.get("repeat_identical")))

    # 压感是否真实注入：全部成功且各档 sha256 不同 = 不同压力出了不同图
    hashes = [e["sha256"] for e in entries if e["sha256"]]
    all_ok = all(e["ok"] for e in entries) and len(entries) == len(grades)
    pressure_injected = all_ok and len(set(hashes)) > 1
    repeat_stable = all(e.get("repeat_identical", True) for e in entries)
    index = {
        "schema_version": 1,
        "task": "14",
        "kind": "feel-pressure-profile",
        "preset": os.path.basename(ns.preset),
        "brush": brush,
        "stroke_length": ns.stroke_length,
        "size": "%dx%d" % (width, height),
        "pressure_profile": grades,
        "repeat": repeats,
        "repeat_stable": repeat_stable,
        "pressure_injected": pressure_injected,
        "simulated": False,
        "total_elapsed_s": round(time.time() - started_all, 2),
        "entries": entries,
    }

    # ---- 可选：单图梯度（双端压力）与多档阶梯的口径对照（任务 144）----
    if ns.gradient_end is not None and all_ok:
        g0 = grades[0]
        g1 = float(ns.gradient_end)
        gpath = os.path.join(ns.out_dir, "feel__%s__grad_p%.2f_to_p%.2f__s%dx%d.png" % (
            brush, g0, g1, width, height))
        gres = runner.render_one(
            ns.preset, gpath, size=(width, height), stroke=ns.stroke_length,
            pressure=g0, timeout=ns.timeout, route="gui", pressure2=g1,
        )
        sidecar = gpath + ".result.json"
        if os.path.isfile(sidecar):
            try:
                os.remove(sidecar)
            except OSError:
                pass
        grad = {
            "file": os.path.basename(gpath),
            "ok": bool(gres.get("ok")),
            "reason": None if gres.get("ok") else (gres.get("reason") or "")[:300],
            "pressure_start": g0,
            "pressure_end": g1,
            "sha256": sha256_of(gpath) if os.path.isfile(gpath) else None,
            "alpha_nonzero": gres.get("alpha_nonzero"),
        }
        if grad["ok"] and os.path.isfile(gpath):
            prof, _ = _column_profile(gpath)
            x0 = max(1, (width - ns.stroke_length) // 2)
            x1 = min(width - 1, x0 + ns.stroke_length)
            compare = []
            for entry in entries:
                if not entry["ok"]:
                    continue
                stair = _stroke_value(entry["path"])
                t = 0.0 if g1 == g0 else (entry["pressure"] - g0) / (g1 - g0)
                xpos = int(round(x0 + max(0.0, min(1.0, t)) * (x1 - x0)))
                gval = prof[xpos] if 0 <= xpos < len(prof) else None
                compare.append({
                    "pressure": entry["pressure"],
                    "staircase_alpha_mean": stair,
                    "gradient_x": xpos,
                    "gradient_alpha_mean": gval,
                    "abs_diff": (round(abs(stair - gval), 3)
                                 if (stair is not None and gval is not None) else None),
                })
            grad["column_profile"] = prof
            grad["compare"] = compare
        index["gradient"] = grad
        print("[梯度] ok=%s file=%s" % (grad["ok"], grad["file"]))


    if all_ok and not pressure_injected and ns.simulate_on_failure:
        # 降级：用 100% 档位的图模拟其余档位（如实标注 simulated）
        base = entries[-1]["path"]
        for entry in entries[:-1]:
            simulate_from_base(base, entry["path"], entry["pressure"])
            entry["sha256"] = sha256_of(entry["path"])
            entry["simulated"] = True
            entry["alpha_nonzero"] = None
            if ns.curve_out_dir:
                curve = feel_curve.extract_curves(entry["path"])
                curve_path = os.path.join(ns.curve_out_dir, "%s__p%.2f__curve.json" % (brush, entry["pressure"]))
                feel_curve.save_curve(curve, curve_path)
                entry["curve_file"] = os.path.basename(curve_path)
                entry["max_width_px"] = max(curve["width"])
        index["simulated"] = True
        index["note"] = "Krita 侧压感注入失败（四档图 sha256 全同），已按任务书降级为模拟档位：这不是真压感"
        print("[降级] 压感未生效，已生成模拟档位（simulated=true）")
    elif all_ok and not pressure_injected:
        index["note"] = "Krita 侧压感注入失败（四档图 sha256 全同），未启用模拟；本次结果不可当真压感用"
        print("[警告] 压感未生效（四档图完全相同），且未启用 --simulate-on-failure", file=sys.stderr)
    elif all_ok:
        index["note"] = "Krita 侧压感真实注入：不同档位的 Alpha 非零像素数与图像哈希均不同"
        print("[通过] 压感真实注入（四档图哈希不同）")

    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("索引已写：%s" % index_path)

    # 合并到手感基线清单（只在全档成功时写，避免把半成品登记成基线）
    manifest_path = ns.manifest or os.path.join(ns.out_dir, "manifest.json")
    if all_ok:
        _merge_manifest(manifest_path, ns, index, entries)
    print("总计 %d 档 / 成功 %d / 压感真实注入=%s / 同档复现稳定=%s / 总耗时 %.1fs" % (
        len(entries), sum(1 for e in entries if e["ok"]), pressure_injected,
        repeat_stable, index["total_elapsed_s"]))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
