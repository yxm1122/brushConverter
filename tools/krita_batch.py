# -*- coding: utf-8 -*-
"""Krita 批量渲染与夹具落盘（任务 06A）。

【两条数据来源】
1) `--render`（真实渲染）：逐个 .kpp 调 `tools/krita_render_runner.py` 的 **GUI 插件路线**
   （真实 Krita 进程 + PyKrita 启动插件落笔），产物 PNG 落 `tests/baselines/krita/`，
   命名按 `docs/基准库命名规范.md`：`krita__<笔刷名>__plain__s<尺寸>_p<压感>.png`。
   单支失败不中断整批，失败原因取 stderr 尾部 20 行；结果写 `render_attempts.json`。
2) `--register-historical`（降级产物，保留）：把 `research/krita结果` 下 23 张历史真值图
   规范化登记进清单，`status="historical"`（不是本工具渲染的，不许写成 ok）。

`--render` 结束会把「本次渲染条目 + 历史真值图条目」合并写进
`tests/baselines/krita/manifest.json`，供任务 09 汇总。
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import time

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import krita_render_runner as runner  # noqa: E402

HIST_ROOT = os.path.join(REPO_ROOT, "research", "krita结果")
KPP_DIR = os.path.join(REPO_ROOT, "converted", "kpp")
BASELINE_DIR = os.path.join(REPO_ROOT, "tests", "baselines", "krita")
MANIFEST_PATH = os.path.join(BASELINE_DIR, "manifest.json")
ATTEMPTS_PATH = os.path.join(BASELINE_DIR, "render_attempts.json")

# 默认批量范围：converted/kpp 里的 6 支代表笔（覆盖硬笔/软笔/喷枪/纹理笔）
DEFAULT_PRESETS = [
    "001_勾线笔_用数位板.kpp",
    "003_大怪兽-圆头虚_用数位板.kpp",
    "004_大怪兽-圆头实_用数位板.kpp",
    "006_大怪兽-碳铅_用数位板.kpp",
    "012_大怪兽-喷枪_用数位板.kpp",
    "017_砖墙.kpp",
]

BC_RE = re.compile(r"b(?P<brightness>-?\d+(?:\.\d+)?)_c(?P<contrast>\d+(?:\.\d+)?)")


# ---------------------------------------------------------------- 通用


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def alpha_nonzero(png_path):
    with Image.open(png_path) as im:
        rgba = im.convert("RGBA")
        return sum(1 for value in rgba.getchannel("A").getdata() if value > 0)


def image_size(png_path):
    with Image.open(png_path) as im:
        return im.size


def safe_component(text):
    """把笔刷名清洗成文件名安全段：去首部序号、非法字符换下划线、连续下划线合并。"""
    value = re.sub(r"^\d+[_-]", "", str(text))
    value = re.sub(r'[\\/:*?"<>|]', "_", value)
    value = value.replace("【", "_").replace("】", "_").replace("（", "_").replace("）", "_")
    value = re.sub(r"\s+", "_", value.strip())
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def base_name_for_render(kpp_path, preset_name, size=(200, 200), pressure=1.0):
    """按命名规范生成基准图名：krita__<笔刷名>__plain__s200_p1.0.png"""
    brush = safe_component(preset_name or os.path.splitext(os.path.basename(kpp_path))[0])
    return "krita__%s__plain__s%dx%d_p%.1f.png" % (brush, int(size[0]), int(size[1]), float(pressure))


# ---------------------------------------------------------------- 历史真值图登记（保留降级产物）


def split_group(group):
    cleaned = group.replace("（photoshop）", "").replace("(photoshop)", "")
    match = re.match(r"^(?P<mode>.+?)_d(?P<depth>\d+)$", cleaned)
    if match:
        return match.group("mode"), "d" + match.group("depth")
    return cleaned, "none"


def parse_bc_parts(filename):
    match = BC_RE.search(os.path.splitext(os.path.basename(filename))[0])
    if not match:
        return None, None
    return match.group("brightness"), match.group("contrast")


def parse_bc(filename):
    b_text, c_text = parse_bc_parts(filename)
    if b_text is None:
        return None, None
    return float(b_text), float(c_text)


def normalized_name(group, original):
    mode, depth = split_group(group)
    b_text, c_text = parse_bc_parts(original)
    stem, _ext = os.path.splitext(original)
    param = "b%s_c%s" % (b_text, c_text) if (b_text is not None and c_text is not None) else stem
    return "krita__hist_%s__%s__%s.png" % (depth, mode, param)


def register_historical():
    """把 research/krita结果 的历史真值图复制并登记，返回条目列表。"""
    os.makedirs(BASELINE_DIR, exist_ok=True)
    items = []
    used_names = set()
    source_files = []
    for dirpath, _dirnames, filenames in os.walk(HIST_ROOT):
        for filename in sorted(filenames):
            if filename.lower().endswith(".png"):
                source_files.append(os.path.join(dirpath, filename))
    source_files.sort()

    for src in source_files:
        group = os.path.basename(os.path.dirname(src))
        original = os.path.basename(src)
        new_name = normalized_name(group, original)
        if new_name in used_names:
            stem, ext = os.path.splitext(new_name)
            index = 2
            while "%s_%d%s" % (stem, index, ext) in used_names:
                index += 1
            new_name = "%s_%d%s" % (stem, index, ext)
        used_names.add(new_name)
        dst = os.path.join(BASELINE_DIR, new_name)
        shutil.copyfile(src, dst)
        b_value, c_value = parse_bc(original)
        mode, depth = split_group(group)
        width, height = image_size(dst)
        items.append({
            "file": new_name,
            "source": "krita",
            "preset": "none",
            "size": "%dx%d" % (width, height),
            "pressure": None,
            "status": "historical",
            "elapsed_s": None,
            "alpha_nonzero": alpha_nonzero(dst),
            "sha256": sha256_of(dst),
            "bytes": os.path.getsize(dst),
            "mode": mode,
            "brightness": b_value,
            "contrast": c_value,
            "group": group,
            "depth": depth,
            "origin": os.path.relpath(src, REPO_ROOT).replace("\\", "/"),
            "renderer": "krita-gui-historical",
            "note": "历史真值图（Krita 5.3.3 界面人工生成），非本工具渲染",
        })
    return items


# ---------------------------------------------------------------- 真实批量渲染


def collect_presets(list_file=None):
    if list_file:
        with open(list_file, "r", encoding="utf-8") as fh:
            rows = [line.strip() for line in fh if line.strip() and not line.strip().startswith("#")]
        return [row if os.path.isabs(row) else os.path.join(REPO_ROOT, row) for row in rows]
    return [os.path.join(KPP_DIR, name) for name in DEFAULT_PRESETS if os.path.isfile(os.path.join(KPP_DIR, name))]


def render_batch(presets, only_failed=False, timeout=None):
    os.makedirs(BASELINE_DIR, exist_ok=True)

    if only_failed and os.path.isfile(ATTEMPTS_PATH):
        with open(ATTEMPTS_PATH, "r", encoding="utf-8") as fh:
            previous = json.load(fh)
        failed = {item["preset_file"] for item in previous.get("attempts", []) if item.get("status") != "rendered"}
        presets = [path for path in presets if os.path.basename(path) in failed]
        print("[信息] --only-failed：筛出上次失败 %d 支" % len(presets))

    rendered = []
    attempts = []
    ok_count = 0
    started = time.time()
    for preset in presets:
        kpp_name = os.path.basename(preset)
        result = runner.render_one(preset, os.path.join(BASELINE_DIR, "__pending__.png"),
                                   size=(200, 200), stroke=120, pressure=1.0,
                                   timeout=timeout, route="gui")
        preset_name = result.get("preset") or os.path.splitext(kpp_name)[0]
        final_name = base_name_for_render(preset, preset_name)
        final_path = os.path.join(BASELINE_DIR, final_name)
        elapsed = result.get("elapsed_s")
        if result["ok"]:
            pending = os.path.abspath(os.path.join(BASELINE_DIR, "__pending__.png"))
            if os.path.isfile(pending):
                shutil.move(pending, final_path)
            # 清掉外壳写的中间结果文件
            pending_result = pending + ".result.json"
            if os.path.isfile(pending_result):
                os.remove(pending_result)
            alpha = alpha_nonzero(final_path) if os.path.isfile(final_path) else 0
            item = {
                "file": final_name,
                "source": "krita",
                "preset": preset_name,
                "preset_file": kpp_name,
                "size": "200x200",
                "pressure": 1.0,
                "stroke": 120,
                "status": "rendered",
                "elapsed_s": elapsed,
                "alpha_nonzero": alpha,
                "sha256": sha256_of(final_path),
                "bytes": os.path.getsize(final_path),
                "renderer": "krita-gui-plugin",
                "route": "gui",
                "note": "真实渲染：Krita 5.3.3 GUI 进程 + PyKrita 启动插件，透明画布落笔后导出",
            }
            rendered.append(item)
            ok_count += 1
            print("[通过] %s → %s（%.2fs，alpha非零=%d）" % (kpp_name, final_name, elapsed or 0, alpha))
        else:
            pending = os.path.abspath(os.path.join(BASELINE_DIR, "__pending__.png"))
            if os.path.isfile(pending):
                os.remove(pending)
            reason = (result.get("reason") or "")[:400]
            item = {
                "file": None,
                "source": "krita",
                "preset": preset_name,
                "preset_file": kpp_name,
                "size": "200x200",
                "pressure": 1.0,
                "stroke": 120,
                "status": "failed",
                "elapsed_s": elapsed,
                "alpha_nonzero": result.get("alpha_nonzero"),
                "reason": reason,
                "renderer": "krita-gui-plugin",
                "route": "gui",
            }
            rendered.append(item)
            print("[失败] %s（%.2fs）：%s" % (kpp_name, elapsed or 0, reason))
        attempts.append({
            "preset_file": kpp_name,
            "status": "rendered" if result["ok"] else "failed",
            "elapsed_s": elapsed,
            "reason": None if result["ok"] else (result.get("reason") or "")[:400],
            "timeout": result.get("timeout"),
        })

    total_elapsed = time.time() - started
    failed_count = len(attempts) - ok_count
    average = total_elapsed / len(attempts) if attempts else 0.0
    summary = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "route": "gui",
        "total": len(attempts),
        "ok": ok_count,
        "failed": failed_count,
        "average_s": round(average, 3),
        "total_s": round(total_elapsed, 3),
        "attempts": attempts,
    }
    with open(ATTEMPTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("总计 %d 支 / 成功 %d / 失败 %d / 平均 %.2f 秒/支 / 总耗时 %.1f 秒"
          % (len(attempts), ok_count, failed_count, average, total_elapsed))
    if total_elapsed > 20 * 60:
        print("[警告] 总耗时超过 20 分钟，建议用 --list 缩小范围")
    return rendered, summary


# ---------------------------------------------------------------- 清单合并


def write_manifest(rendered_items, historical_items):
    """合并写 manifest.json：真实渲染条目在前，历史真值图条目在后。"""
    items = rendered_items + historical_items
    rendered_ok = [item for item in rendered_items if item["status"] == "rendered"]
    manifest = {
        "schema_version": 1,
        "task": "06A",
        "generated_by": "tools/krita_batch.py",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "note": (
            "items 前段 status=rendered 为 GUI 路线真实渲染（Krita 5.3.3 启动插件，透明画布落笔）；"
            "后段 status=historical 为降级期登记的历史真值图（非本工具渲染）。"
        ),
        "summary": {
            "rendered": len(rendered_ok),
            "failed": len([item for item in rendered_items if item["status"] == "failed"]),
            "historical": len(historical_items),
            "total": len(items),
        },
        "total": len(items),
        "items": items,
    }
    # 清理未被清单引用的旧基准图（只清 krita__ 开头的本目录 PNG，不碰 render/ 子目录）
    keep = {item["file"] for item in items if item.get("file")}
    for name in os.listdir(BASELINE_DIR):
        if name.startswith("krita__") and name.endswith(".png") and name not in keep:
            try:
                os.remove(os.path.join(BASELINE_DIR, name))
            except OSError:
                pass
    with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Krita 批量渲染与夹具落盘（任务 06A）")
    parser.add_argument("--render", action="store_true", help="批量真实渲染（GUI 插件路线）")
    parser.add_argument("--register-historical", action="store_true", help="只登记历史真值图（降级产物）")
    parser.add_argument("--only-failed", action="store_true", help="配合 --render：只重跑上次失败项")
    parser.add_argument("--list", dest="list_file", default=None, help="自定义清单文件（每行一个 .kpp 路径）")
    parser.add_argument("--timeout", type=float, default=None, help="单支渲染超时秒数（默认 180）")
    ns = parser.parse_args()

    if ns.render:
        presets = collect_presets(ns.list_file)
        if not presets:
            print("[错误] 没有可渲染的 .kpp（检查 converted/kpp/ 或 --list 文件）", file=sys.stderr)
            return 2
        rendered, summary = render_batch(presets, only_failed=ns.only_failed, timeout=ns.timeout)
        historical = register_historical()
        manifest = write_manifest(rendered, historical)
        print("清单已写 %s：渲染成功 %d / 失败 %d / 历史真值图 %d（共 %d 条）"
              % (os.path.relpath(MANIFEST_PATH, REPO_ROOT),
                 manifest["summary"]["rendered"], manifest["summary"]["failed"],
                 manifest["summary"]["historical"], manifest["total"]))
        if summary["total"] and summary["ok"] == 0:
            print("[错误] 本批全部失败，详见 render_attempts.json", file=sys.stderr)
            return 1
        return 0

    historical = register_historical()
    manifest = write_manifest([], historical)
    print("已登记历史真值图 %d 张 → %s" % (manifest["summary"]["historical"],
                                        os.path.relpath(MANIFEST_PATH, REPO_ROOT)))
    return 0 if historical else 1


if __name__ == "__main__":
    sys.exit(main())
