# -*- coding: utf-8 -*-
"""按压感档位的渲染入口（任务 14）。

**不改原渲染器**：本文件只在 conda 侧调用 `tools/krita_render_runner.py` 的 GUI 真实渲染路线
（真实 Krita 进程 + PyKrita 启动插件，插件把 `Node.paintLine(pressure, pressure)` 的压感传给笔刷）。
Krita 侧压感是否真实注入，由本脚本渲染后自动核验（四档图的 sha256 全相同 = 没生效）。

用法：
    python tools/krita_render_pressure.py \
        --preset "converted/kpp/003_大怪兽-圆头虚_用数位板.kpp" \
        --pressure-profile 0.2,0.5,0.8,1.0 --stroke-length 120 \
        --out-dir tests/baselines/feel --curve-out-dir tests/baselines/feel

产物：每个档位一张 PNG（`feel__<笔刷>__p<压感>__s<尺寸>.png`）+ 一份索引 JSON +（可选）逐档曲线 JSON。

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


def main():
    parser = argparse.ArgumentParser(description="按压感档位渲染（复用 GUI 真实渲染路线）")
    parser.add_argument("--preset", required=True, help=".kpp 预设路径")
    parser.add_argument("--pressure-profile", default="0.2,0.5,0.8,1.0", help="压感档位，逗号分隔，默认 0.2,0.5,0.8,1.0")
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

    for grade in grades:
        png_name = "feel__%s__p%.2f__s%dx%d.png" % (brush, grade, width, height)
        png_path = os.path.join(ns.out_dir, png_name)
        result = runner.render_one(
            ns.preset, png_path, size=(width, height), stroke=ns.stroke_length,
            pressure=grade, timeout=ns.timeout, route="gui",
        )
        entry = {
            "pressure": grade,
            "file": os.path.basename(png_path),
            "path": os.path.abspath(png_path),
            "stroke_length": ns.stroke_length,
            "size": "%dx%d" % (width, height),
            "ok": bool(result.get("ok")),
            "alpha_nonzero": result.get("alpha_nonzero"),
            "elapsed_s": result.get("elapsed_s"),
            "sha256": sha256_of(png_path) if os.path.isfile(png_path) else None,
            "reason": None if result.get("ok") else (result.get("reason") or "")[:300],
        }
        # 外壳会在 PNG 旁写 <png>.result.json 中间结果，这里读完就删，避免污染基线目录
        sidecar = png_path + ".result.json"
        if os.path.isfile(sidecar):
            try:
                os.remove(sidecar)
            except OSError:
                pass
        if result.get("ok") and ns.curve_out_dir:
            curve = feel_curve.extract_curves(png_path)
            curve_path = os.path.join(ns.curve_out_dir, "%s__p%.2f__curve.json" % (brush, grade))
            feel_curve.save_curve(curve, curve_path)
            entry["curve_file"] = os.path.basename(curve_path)
            entry["max_width_px"] = max(curve["width"])
            entry["mean_opacity_01"] = sum(curve["opacity"]) / len(curve["opacity"])
        entries.append(entry)
        print("[档位 %.2f] ok=%s alpha=%s elapsed=%.2fs" % (
            grade, entry["ok"], entry["alpha_nonzero"], entry["elapsed_s"] or 0.0))

    # 压感是否真实注入：全部成功但四张图 sha256 完全相同 → 压感没生效
    hashes = [e["sha256"] for e in entries if e["sha256"]]
    all_ok = all(e["ok"] for e in entries) and len(entries) == len(grades)
    pressure_injected = all_ok and len(set(hashes)) > 1
    index = {
        "schema_version": 1,
        "task": "14",
        "kind": "feel-pressure-profile",
        "preset": os.path.basename(ns.preset),
        "brush": brush,
        "stroke_length": ns.stroke_length,
        "size": "%dx%d" % (width, height),
        "pressure_profile": grades,
        "pressure_injected": pressure_injected,
        "simulated": False,
        "total_elapsed_s": round(time.time() - started_all, 2),
        "entries": entries,
    }

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
    print("总计 %d 档 / 成功 %d / 压感真实注入=%s / 总耗时 %.1fs" % (
        len(entries), sum(1 for e in entries if e["ok"]), pressure_injected, index["total_elapsed_s"]))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
