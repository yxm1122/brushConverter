#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全量自动验收与归档（任务 15）。

一条命令依次跑三层比对（**严格串行**：同一时刻只启动一个 pytest 进程；手感层不启动 Krita GUI，
但本项目里任何会启动 Krita 的用例（tests/test_krita_render.py）都不在本次三层内，避免并发渲染互相干扰）：

    参数层  pytest -m verify_params
    像素层  pytest -m verify_pixels
    手感层  pytest tests/test_feel_curve.py（并读 feel 基线做自检指标）

汇总输出：
    层 / 通过 / 失败 / 跳过 / 关键指标 表格 + **复用任务 01 的摘要行格式**：
    「通过 N 项 ｜ 失败 M 项 ｜ 跳过 K 项」（有跳过时末尾追加「（含跳过，未全验）」）。

用法：
    python tools/verify_all.py                 # 只跑并打印
    python tools/verify_all.py --archive       # 跑完写 docs/验收报告/归档/verify-<时间戳>.md
    python tools/verify_all.py --merge-manifest  # 把 feel 子清单合并进顶层 tests/baselines/manifest.json

退出码（与任务 01 一致）：有失败或 pytest 异常 -> 1；全通过（含跳过）-> 0；三层一条用例都没收集到 -> 2。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import verify_entry  # noqa: E402  复用任务 01 的摘要契约（格式/解析/退出码）
import feel_compare  # noqa: E402
import feel_curve  # noqa: E402

ARCHIVE_DIR = REPO_ROOT / "docs" / "验收报告" / "归档"
FEEL_DIR = REPO_ROOT / "tests" / "baselines" / "feel"
FEEL_MANIFEST = FEEL_DIR / "manifest.json"
TOP_MANIFEST = REPO_ROOT / "tests" / "baselines" / "manifest.json"

def _env_candidates(env_name: str) -> list[Path]:
    """环境变量优先：用 os.pathsep 分隔多个候选路径；未设置时返回空列表。"""
    raw = os.environ.get(env_name, "")
    return [Path(item) for item in raw.split(os.pathsep) if item.strip()]


# 候选路径：环境变量优先（BRUSHCONVERTER_KRITA_CANDIDATES / BRUSHCONVERTER_PS_CANDIDATES），
# 本机默认值兜底（不含身份信息，脚本需要它才能在本机直接跑）
KRITA_CANDIDATES = _env_candidates("BRUSHCONVERTER_KRITA_CANDIDATES") + [
    Path(r"D:/program/Krita (x64)/bin/krita.com"),
    Path(r"D:/program/Krita (x64)/bin/krita.exe"),
]
PS_CANDIDATES = _env_candidates("BRUSHCONVERTER_PS_CANDIDATES") + [
    Path(r"D:/program/PS2025/Adobe Photoshop 2025/Photoshop.exe"),
]

# 三层定义：key 用于机器、name 用于表格；args 是传给 pytest 的参数
LAYERS = [
    {"key": "params", "name": "参数层", "args": ["-m", "verify_params"]},
    {"key": "pixels", "name": "像素层", "args": ["-m", "verify_pixels"]},
    {"key": "feel", "name": "手感层", "args": [str(Path("tests") / "test_feel_curve.py")]},
]


# ==========================================================================
# 一、单层执行
# ==========================================================================

def _decode(data) -> str:
    return verify_entry._decode_stdout(data)


def _metric_for(layer_key, result, feel_summary):
    """每层的关键指标（给人看的短句）。"""
    total = result.get("total", 0)
    if layer_key == "feel":
        if feel_summary is None:
            return "手感基线缺失（如实 skip）"
        return "基线 %d 条，自比 max dev=%.3f，压感真实注入=%s" % (
            feel_summary["items"],
            feel_summary["max_self_dev"],
            "是" if feel_summary["pressure_injected"] else "否",
        )
    return "用例 %d 项（通过 %d）" % (total, result.get("passed", 0))


def feel_baseline_summary():
    """读 feel 基线并做自比，返回指标；基线缺失返回 None（上层如实 skip）。"""
    if not FEEL_MANIFEST.is_file():
        return None
    with open(FEEL_MANIFEST, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    items = manifest.get("items") or []
    max_dev = 0.0
    checked = 0
    for item in items:
        curve_file = FEEL_DIR / str(item.get("curve_file") or "")
        if not curve_file.is_file():
            continue
        curve = feel_curve.load_curve(str(curve_file))
        result = feel_compare.compare_curves(curve, curve)
        max_dev = max(max_dev, result["width_dev"], result["opacity_dev"])
        checked += 1
    return {
        "items": len(items),
        "curves_checked": checked,
        "max_self_dev": max_dev,
        "pressure_injected": bool(manifest.get("pressure_injected")),
    }


def run_layer(layer, timeout=900):
    """用子进程跑一层 pytest（串行），返回结构化结果。"""
    junit_dir = tempfile.mkdtemp(prefix="verify_all_%s_" % layer["key"])
    junit_path = Path(junit_dir) / "junit.xml"
    cmd = [
        sys.executable, "-m", "pytest", "-q",
        "--junitxml=" + str(junit_path),
        *[str(a) for a in layer["args"]],
    ]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            cmd, cwd=str(REPO_ROOT), capture_output=True, env=env,
            timeout=timeout, check=False,
        )
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        proc = exc
        timed_out = True

    result = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "skips": [], "failures": []}
    if not timed_out and junit_path.is_file():
        result = verify_entry.parse_junit(junit_path)

    stdout_text = _decode(getattr(proc, "stdout", b"")) if not timed_out else ""
    stderr_text = _decode(getattr(proc, "stderr", b"")) if not timed_out else ""
    return {
        "key": layer["key"],
        "name": layer["name"],
        "total": result["total"],
        "passed": result["passed"],
        "failed": result["failed"],
        "skipped": result["skipped"],
        "skips": result["skips"],
        "failures": result["failures"],
        "timed_out": timed_out,
        "exit_code": None if timed_out else proc.returncode,
        "stdout_tail": "\n".join(stdout_text.strip().splitlines()[-20:]),
        "stderr_tail": "\n".join(stderr_text.strip().splitlines()[-20:]),
        "metric": "",
    }


def run_all(layers=None, timeout=900, layer_runner=None, feel_summary_getter=None):
    """串行跑三层并汇总。layers / layer_runner / feel_summary_getter 供单测注入。"""
    chosen = list(layers if layers is not None else LAYERS)
    runner = layer_runner or run_layer
    get_feel = feel_summary_getter or feel_baseline_summary
    feel_summary = get_feel() if any(layer["key"] == "feel" for layer in chosen) else None
    results = []
    for layer in chosen:
        result = runner(layer, timeout=timeout)
        result["metric"] = _metric_for(layer["key"], result, feel_summary if layer["key"] == "feel" else None)
        results.append(result)
    return results


# ==========================================================================
# 二、汇总与摘要（复用任务 01 契约）
# ==========================================================================

def aggregate(results):
    total = {"passed": 0, "failed": 0, "skipped": 0, "total": 0}
    for item in results:
        total["passed"] += item.get("passed", 0)
        total["failed"] += item.get("failed", 0)
        total["skipped"] += item.get("skipped", 0)
        total["total"] += item.get("total", 0)
    return total


def build_overall_summary(results):
    """生成与任务 01 一字不差的总览行。"""
    return verify_entry.build_summary(aggregate(results))


def decide_overall_exit_code(results):
    """与任务 01 一致的退出码换算（0 / 1 / 2）。"""
    total = aggregate(results)
    any_error = any(item.get("timed_out") or (item.get("exit_code") not in (0, 1, None)) for item in results)
    pytest_code = 0 if not any_error else 3
    return verify_entry.decide_exit_code(pytest_code, total)


def render_table(results):
    """渲染「层 / 通过 / 失败 / 跳过 / 关键指标」表格（纯文本，窄终端也友好）。"""
    headers = ["层", "通过", "失败", "跳过", "关键指标"]
    rows = [[item["name"], str(item["passed"]), str(item["failed"]), str(item["skipped"]), item["metric"]]
            for item in results]
    widths = [max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
              for i, header in enumerate(headers)]
    # 中文按 2 列宽估算，单纯 len 对齐会略歪，这里用固定分隔符的简单表格
    def fmt_row(cells):
        return " | ".join(cell.ljust(width) for cell, width in zip(cells, widths))
    lines = [fmt_row(headers), "-+-".join("-" * width for width in widths)]
    lines.extend(fmt_row(row) for row in rows)
    return "\n".join(lines)


def skip_details(results):
    rows = []
    for item in results:
        for file_name, case_name, reason in item.get("skips", []):
            rows.append((item["name"], file_name, case_name, reason))
    return rows


def failure_details(results):
    rows = []
    for item in results:
        for file_name, case_name in item.get("failures", []):
            rows.append((item["name"], file_name, case_name))
    return rows


def print_overview(results):
    """打印：表格 -> 失败明细 -> 跳过明细 -> 分隔线 -> 总览摘要行（最后一行最醒目）。"""
    print(render_table(results))
    failures = failure_details(results)
    if failures:
        print("\n失败明细：")
        for layer_name, file_name, case_name in failures:
            print("  - [%s] %s::%s" % (layer_name, file_name, case_name))
    skips = skip_details(results)
    if skips:
        print("\n跳过明细：")
        for layer_name, file_name, case_name, reason in skips:
            print("  - [%s] %s::%s — %s" % (layer_name, file_name, case_name, reason))
    print("\n" + "-" * 72)
    print(build_overall_summary(results))


# ==========================================================================
# 三、环境版本采集（自动，不许手写）
# ==========================================================================

def _first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def collect_python_version():
    try:
        import platform
        return "Python %s %s" % (platform.python_version(), platform.architecture()[0])
    except Exception:
        return "未检测到"


def collect_krita_version():
    for path in KRITA_CANDIDATES:
        if not path.is_file():
            continue
        try:
            proc = subprocess.run([str(path), "-v"], capture_output=True, timeout=60, check=False)
            text = _first_line(_decode(proc.stdout)) or _first_line(_decode(proc.stderr))
            if text:
                return text
        except Exception:
            continue
    return "未检测到"


def _powershell_file_version(path):
    cmd = [
        "powershell", "-NoProfile", "-Command",
        "(Get-Item '%s').VersionInfo.FileVersion" % str(path).replace("/", "\\"),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        text = _first_line(_decode(proc.stdout))
        if text:
            return text
    except Exception:
        pass
    return ""


def collect_photoshop_version():
    for path in PS_CANDIDATES:
        if not path.is_file():
            continue
        version = _powershell_file_version(path)
        if version:
            return version
    return "未检测到"


def collect_environment():
    """返回环境三行（采集不到写「未检测到」）。"""
    return {
        "Python": collect_python_version(),
        "Krita": collect_krita_version(),
        "Photoshop": collect_photoshop_version(),
    }


# ==========================================================================
# 四、归档
# ==========================================================================

def now_stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def archive_path(stamp=None):
    """生成归档路径：verify-<YYYYMMDD-HHMMSS>.md；同名已存在则加 -2、-3…（不覆盖）。"""
    stamp = stamp or now_stamp()
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    base = "verify-%s" % stamp
    candidate = ARCHIVE_DIR / (base + ".md")
    index = 2
    while candidate.exists():
        candidate = ARCHIVE_DIR / ("%s-%d.md" % (base, index))
        index += 1
    return candidate


def render_archive(results, env, exit_code, command="python tools/verify_all.py --archive"):
    """渲染归档 Markdown 内容。"""
    total = aggregate(results)
    lines = [
        "# 全量验收归档（verify_all）",
        "",
        "- 归档时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- 命令：%s" % command,
        "- 退出码：%d（0=全通过；1=有失败/异常；2=一条用例都没收集到）" % exit_code,
        "- 摘要行（与任务 01 契约一致，脚本可直接解析）：",
        "",
        "~~~text",
        build_overall_summary(results),
        "~~~",
        "",
        "## 一、总览",
        "",
        "| 层 | 通过 | 失败 | 跳过 | 关键指标 |",
        "|---|---|---|---|---|",
    ]
    for item in results:
        lines.append("| %s | %d | %d | %d | %s |" % (
            item["name"], item["passed"], item["failed"], item["skipped"], item["metric"]))
    lines.append("| **合计** | **%d** | **%d** | **%d** | 共 %d 项用例 |" % (
        total["passed"], total["failed"], total["skipped"], total["total"]))
    lines.extend(["", "## 二、失败明细", ""])
    failures = failure_details(results)
    if not failures:
        lines.append("无（本层无失败用例）。")
    else:
        for layer_name, file_name, case_name in failures:
            lines.append("- [%s] %s::%s" % (layer_name, file_name, case_name))
    lines.extend(["", "## 三、跳过原因", ""])
    skips = skip_details(results)
    if not skips:
        lines.append("无（本次 0 跳过）。")
    else:
        for layer_name, file_name, case_name, reason in skips:
            lines.append("- [%s] %s::%s — %s" % (layer_name, file_name, case_name, reason))
    lines.extend(["", "## 四、运行环境（自动采集）", ""])
    for name in ("Python", "Krita", "Photoshop"):
        lines.append("- %s：%s" % (name, env.get(name, "未检测到")))
    lines.append("")
    return "\n".join(lines)


def write_archive(results, env, exit_code, command="python tools/verify_all.py --archive"):
    path = archive_path()
    path.write_text(render_archive(results, env, exit_code, command), encoding="utf-8")
    return path


# ==========================================================================
# 五、清单合并（feel 子清单 -> 顶层 manifest）
# ==========================================================================

FIELD_ORDER = ["file", "source", "size", "pressure", "mode", "depth",
               "brightness", "contrast", "status", "alpha_nonzero", "note"]


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _alpha_nonzero(path):
    from PIL import Image
    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        # Pillow 14 起 Image.getdata() 移除（DeprecationWarning）；tobytes() 逐字节等价。
        alpha = rgba.getchannel("A").tobytes()
        return len(alpha) - alpha.count(0)


def merge_feel_manifest(top_manifest_path=None, feel_manifest_path=None):
    """把 feel 子清单合并进顶层清单（幂等：已存在的 file 不重复添加）。

    合并口径沿用任务 09：file 用相对 tests/baselines 的路径；字段固定 11 个（缺 null）；
    alpha_nonzero 当场读 PNG 实测；条数对账 = 合并前 + 新增。
    """
    top_path = Path(top_manifest_path or TOP_MANIFEST)
    feel_path = Path(feel_manifest_path or FEEL_MANIFEST)
    with open(top_path, "r", encoding="utf-8") as fh:
        top = json.load(fh)
    with open(feel_path, "r", encoding="utf-8") as fh:
        feel = json.load(fh)

    items = top.get("items") or []
    existing_files = {str(item.get("file")) for item in items}
    before_total = len(items)
    added = 0
    duplicates = 0
    for entry in feel.get("items") or []:
        rel = "feel/" + str(entry.get("file"))
        if rel in existing_files:
            duplicates += 1
            continue
        png = top_path.parent / rel
        if not png.is_file():
            continue
        items.append({
            "file": rel,
            "source": "krita",
            "size": entry.get("size"),
            "pressure": entry.get("pressure"),
            "mode": None,
            "depth": None,
            "brightness": None,
            "contrast": None,
            "status": "rendered",
            "alpha_nonzero": _alpha_nonzero(png),
            "note": "任务14手感基线（笔刷=%s，压感=%s，曲线=%s）" % (
                entry.get("brush"), entry.get("pressure"), entry.get("curve_file")),
        })
        existing_files.add(rel)
        added += 1

    counts_by_source = {"ps": 0, "krita": 0, "handmade": 0}
    for item in items:
        source = item.get("source")
        if source in counts_by_source:
            counts_by_source[source] += 1
    after_total = len(items)
    merged_at = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
    top["merged_at"] = merged_at
    top["merged_by"] = "任务 15（全量自动验收与归档），执行代理 krita-line"
    top["counts"] = {
        "total": after_total,
        **counts_by_source,
        "reconciliation": "%d = %d + %d + %d" % (
            after_total, counts_by_source["ps"], counts_by_source["krita"], counts_by_source["handmade"]),
    }
    sources = top.setdefault("sources", {})
    sources.setdefault("ps", {})
    sources.setdefault("handmade", {})
    sources["krita"] = {
        "manifest": "tests/baselines/krita/manifest.json",
        "list_key": "items",
        "count": counts_by_source["krita"],
        "note": "含任务14合入的手感基线 12 张（来源子清单 tests/baselines/feel/manifest.json）",
    }
    sources["feel"] = {
        "manifest": "tests/baselines/feel/manifest.json",
        "list_key": "items",
        "count": len(feel.get("items") or []),
        "merged_as_source": "krita",
    }
    history = top.setdefault("merge_history", [])
    history.append({
        "at": merged_at,
        "by": "task-15",
        "before_total": before_total,
        "feel_items": len(feel.get("items") or []),
        "added": added,
        "duplicates_skipped": duplicates,
        "after_total": after_total,
    })
    top["note"] = "顶层清单由任务 09 合并、任务 15 再次合并（含 feel 手感基线）；以本文件为唯一权威。"
    with open(top_path, "w", encoding="utf-8") as fh:
        json.dump(top, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return {
        "merged_at": merged_at,
        "before_total": before_total,
        "feel_items": len(feel.get("items") or []),
        "added": added,
        "duplicates_skipped": duplicates,
        "after_total": after_total,
        "counts": top["counts"],
        "reconciliation": "%d = 合并前 %d + 新增 %d" % (after_total, before_total, added),
    }


# ==========================================================================
# 六、主流程
# ==========================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(description="全量自动验收与归档（三层串行 + 总览 + 归档 + 清单合并）")
    parser.add_argument("--archive", action="store_true", help="把本次结果写成带时间戳的归档报告")
    parser.add_argument("--merge-manifest", action="store_true", help="把 feel 子清单合并进顶层 manifest")
    parser.add_argument("--timeout", type=int, default=900, help="单层 pytest 超时秒数，默认 900")
    parser.add_argument("--json", default=None, help="把本次结构化结果另存为 JSON（可选）")
    parser.add_argument("--selftest-failure", action="store_true",
                        help="自检：追加一个临时失败用例层（文件建在系统临时目录，跑完即删），用于验证失败明细与退出码")
    ns = parser.parse_args(argv)

    verify_entry._force_utf8_stdout()
    print("全量验收：依次跑 参数层 -> 像素层 -> 手感层（串行，同一时刻只启动一个 pytest）")
    selftest_dir = None
    layers = list(LAYERS)
    if ns.selftest_failure:
        selftest_dir = tempfile.mkdtemp(prefix="verify_all_selftest_")
        test_file = Path(selftest_dir) / "test_verify_all_selftest.py"
        test_file.write_text(
            "def test_intentional_failure():\n"
            "    assert 1 == 2, \"verify_all 自检：故意失败，用于验证失败明细与退出码\"\n",
            encoding="utf-8",
        )
        layers.append({"key": "selftest", "name": "自检层（临时失败用例）", "args": [str(test_file)]})
        print("[自检] 已追加一个临时失败用例层：%s" % test_file)
    try:
        results = run_all(timeout=ns.timeout, layers=layers)
    finally:
        if selftest_dir:
            import shutil
            shutil.rmtree(selftest_dir, ignore_errors=True)
    print_overview(results)
    exit_code = decide_overall_exit_code(results)

    env = collect_environment()
    print("\n环境：Python=%s ｜ Krita=%s ｜ Photoshop=%s" % (
        env["Python"], env["Krita"], env["Photoshop"]))

    archive_file = None
    if ns.archive:
        archive_file = write_archive(results, env, exit_code)
        print("归档已写：%s" % archive_file.relative_to(REPO_ROOT).as_posix())

    merge_result = None
    if ns.merge_manifest:
        merge_result = merge_feel_manifest()
        print("清单合并：%s（%s）" % (merge_result["reconciliation"], merge_result["merged_at"]))
        print("  顶层 counts：%s" % json.dumps(merge_result["counts"], ensure_ascii=False))

    if ns.json:
        Path(ns.json).write_text(json.dumps({
            "results": results,
            "summary": build_overall_summary(results),
            "exit_code": exit_code,
            "environment": env,
            "archive": str(archive_file) if archive_file else None,
            "merge": merge_result,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print("结构化结果已写：%s" % ns.json)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
