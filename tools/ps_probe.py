# -*- coding: utf-8 -*-
"""驱动 Photoshop 批量出真值图（任务 05 的主脚本，Windows + Photoshop 2025）。

三条通路的分工（细节与实测报错见 docs/PS真值自动化说明.md）：
  1) COM（pywin32）：本脚本 --com-check 验证 Python 能否拿到 Photoshop.Application。
     实测 PS 2025 的 DoJavaScript 会报「非法参数 - 参数 1 - 缺少必需值」，所以 COM 只用来取证，
     真正出图不走 COM。
  2) JSX（ExtendScript）：能跑通，是本次实际采用的通路。脚本用
     `Photoshop.exe -r tools/ps_export.jsx` 启动，PS 侧读 _work/job.json 批量出图，
     写完 _work/done.json 后自行退出；本脚本负责生成清单、等待、校验、登记。
  3) 降级（人工导出图入库）：tools/ps_ingest.py。

用法：
    python tools/ps_probe.py --com-check     # 只做 COM 取证（会启动 Photoshop）
    python tools/ps_probe.py --batch         # 批量出图 + 登记清单（16 张组合）
    python tools/ps_probe.py --batch --limit 4
    python tools/ps_probe.py --verify        # 校验 tests/baselines/ps/ 与 manifest.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = REPO_ROOT / "tests" / "baselines" / "ps"
WORK_DIR = BASELINE_DIR / "_work"
MANIFEST_PATH = BASELINE_DIR / "manifest.json"
JOB_PATH = WORK_DIR / "job.json"
DONE_PATH = WORK_DIR / "done.json"
BATCH_LOG = WORK_DIR / "batch.log"
JSX_PATH = REPO_ROOT / "tools" / "ps_export.jsx"
TEXTURE_PATH = WORK_DIR / "texture_noise64.png"

# 环境变量优先（BRUSHCONVERTER_PS），本机默认值兜底（不含身份信息）
PS_EXE = Path(os.environ.get("BRUSHCONVERTER_PS", r"D:/program/PS2025/Adobe Photoshop 2025/Photoshop.exe"))

# 画布/缩放口径（固定，写进说明文档）：
#   画布 100x100、72 ppi、RGB、透明背景；纹理图案 64x64，textureScale=100%，坐标以画布为基准。
#   画笔实体：直径 300px、硬度 100%、间距 1000%（配合 0.4px 路径 = 只落一个笔尖）。
CANVAS = {"width": 100, "height": 100, "dpi": 72}
BRUSH = {"diameter": 300, "hardness": 100, "spacing": 1000}
TEXTURE_NAME = "bc_texture_noise64"
TEXTURE_SCALE = 100
IDENT = "自动纹理A"

# 中文模式名 → Action Manager 枚举写法（实测可用值）
#   char = charIDToTypeID 四字符 ID；str = stringIDToTypeID 字符串 ID
#   注意：线性高度只有字符串形式 linearHeight 生效，四字符候选（LnHt/LnHg/LinH/LHgt/HgtL…）全部被忽略。
MODE_SPEC = {
    "减去": ("char", "Sbtr"),
    "正片叠底": ("char", "Mltp"),
    "高度": ("char", "Hght"),
    "线性高度": ("str", "linearHeight"),
}

# 默认组合：四模式 × 深度{10,60,100} × 亮度{-50,0,50} × 对比度{-50,0,50} 的代表性子集（20 张）
DEFAULT_COMBOS = [
    ("减去", 100, 0, 0), ("减去", 100, 50, 0), ("减去", 100, -50, 0),
    ("减去", 100, 0, 50), ("减去", 100, 0, -50), ("减去", 60, 0, 0), ("减去", 10, 0, 0),
    ("正片叠底", 100, 0, 0), ("正片叠底", 100, 50, 0), ("正片叠底", 60, 0, 0), ("正片叠底", 10, 0, 0),
    ("高度", 100, 0, 0), ("高度", 60, 0, 0), ("高度", 10, 0, 0), ("高度", 10, 0, 50), ("高度", 10, 0, -50),
    ("线性高度", 100, 0, 0), ("线性高度", 60, 0, 0), ("线性高度", 10, 0, 0), ("线性高度", 10, 0, 50),
]


def ensure_dirs() -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)


def make_texture() -> None:
    """生成确定性的 64x64 灰度噪点纹理（全不透明），作为画笔纹理图案。"""
    rng = np.random.default_rng(20261118)
    tex = rng.integers(0, 256, size=(64, 64), dtype=np.uint8)
    rgba = np.zeros((64, 64, 4), dtype=np.uint8)
    for c in range(3):
        rgba[:, :, c] = tex
    rgba[:, :, 3] = 255
    Image.fromarray(rgba, "RGBA").save(TEXTURE_PATH)
    print(f"[纹理] {TEXTURE_PATH.relative_to(REPO_ROOT)} 64x64 灰度噪点（seed=20261118），"
          f"取值 {int(tex.min())}..{int(tex.max())}，均值 {tex.mean():.2f}")


def kill_photoshop() -> None:
    """强杀 Photoshop（PS 有时 app.quit() 会停在退出流程，出图完成后由 Python 侧兜底清理）。"""
    subprocess.run(["taskkill", "-F", "-IM", "Photoshop.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def photoshop_running() -> bool:
    out = subprocess.run(["tasklist"], capture_output=True, text=True, errors="ignore").stdout
    return "Photoshop.exe" in out


def file_name(mode: str, depth: int, brightness: int, contrast: int) -> str:
    """产出符合命名规范的文件名：<来源>__<标识>__<模式>__<关键参数>.png"""
    return f"ps__{IDENT}__{mode}__d{depth}_b{brightness}_c{contrast}.png"


def alpha_stats(path: Path) -> dict:
    with Image.open(path) as im:
        arr = np.array(im.convert("RGBA"))
    a = arr[:, :, 3]
    hist = np.bincount(a.ravel(), minlength=256)
    return {
        "width": int(a.shape[1]),
        "height": int(a.shape[0]),
        "nonzero_pixels": int(np.count_nonzero(a)),
        "total_pixels": int(a.size),
        "alpha_mean": round(float(a.mean()), 2),
        "alpha_min": int(a.min()),
        "alpha_max": int(a.max()),
        "alpha_unique": int(np.count_nonzero(hist)),
        "rgb_nonzero_pixels": int(np.count_nonzero(np.any(arr[:, :, :3] != 0, axis=2))),
    }


def normalize_rgb_black(path: Path) -> bool:
    """把 RGB 通道清零（Alpha 原样保留）。

    Photoshop 导出 PNG 时，透明像素的 RGB 会写成白色；本项目历史真值图 RGB 全黑，
    且比对只看 Alpha（任务 03 约定）。为让新旧基准图口径一致，这里做一次「只清 RGB、
    不动 Alpha」的规范化，并在清单 note 里记录。
    """
    with Image.open(path) as im:
        arr = np.array(im.convert("RGBA"))
    if not np.any(arr[:, :, :3] != 0):
        return False
    arr[:, :, :3] = 0
    Image.fromarray(arr, "RGBA").save(path)
    return True


def build_jobs(combos) -> list[dict]:
    jobs = []
    for mode, depth, brightness, contrast in combos:
        kind, value = MODE_SPEC[mode]
        jobs.append(
            {
                "file": file_name(mode, depth, brightness, contrast),
                "mode": mode,
                "mode_kind": kind,
                "mode_value": value,
                "depth": depth,
                "brightness": brightness,
                "contrast": contrast,
                "note": f"模式={mode} 深度={depth} 亮度={brightness} 对比度={contrast}",
            }
        )
    return jobs


def write_job_file(jobs: list[dict]) -> None:
    job = {
        "out_dir": BASELINE_DIR.as_posix(),
        "texture_path": TEXTURE_PATH.as_posix(),
        "texture_name": TEXTURE_NAME,
        "texture_scale": TEXTURE_SCALE,
        "canvas": CANVAS,
        "brush": BRUSH,
        "jobs": jobs,
    }
    JOB_PATH.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def run_jsx(jobs: list[dict], timeout: int = 420) -> dict:
    """启动 Photoshop 跑 tools/ps_export.jsx，等 done.json 出现后返回其内容。"""
    for p in (DONE_PATH, BATCH_LOG):
        if p.exists():
            p.unlink()
    write_job_file(jobs)
    kill_photoshop()
    time.sleep(1.0)
    if not PS_EXE.is_file():
        raise FileNotFoundError(f"找不到 Photoshop：{PS_EXE}")
    proc = subprocess.Popen(
        [str(PS_EXE), "-r", str(JSX_PATH)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if DONE_PATH.exists():
            time.sleep(0.5)
            break
        if proc.poll() is not None and not DONE_PATH.exists():
            time.sleep(2.0)
            if DONE_PATH.exists():
                break
            raise RuntimeError(f"Photoshop 提前退出（returncode={proc.returncode}）且未写 done.json，见 {BATCH_LOG}")
        time.sleep(1.0)
    else:
        raise TimeoutError(f"等待 {timeout}s 仍未生成 done.json，见 {BATCH_LOG}")
    data = json.loads(DONE_PATH.read_text(encoding="utf-8"))
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    kill_photoshop()
    return data


def com_check() -> int:
    """通路 A：pywin32 COM 取证。打印原始返回值/报错，不修改任何文件。"""
    try:
        import pythoncom
        import win32com.client
    except ImportError as e:
        print(f"[COM] 未安装 pywin32：{e}")
        return 1
    pythoncom.CoInitialize()
    print("[COM] 尝试 Dispatch('Photoshop.Application') ...")
    try:
        app = win32com.client.Dispatch("Photoshop.Application")
        print("  意外成功：", app.Version)
    except Exception as e:
        print(f"  原始报错：{type(e).__name__}: {e}")
    print("[COM] 尝试 Dispatch('Photoshop.Application.190') ...")
    try:
        app = win32com.client.Dispatch("Photoshop.Application.190")
        print(f"  成功：Name={app.Name!r} Version={app.Version!r} Path={app.Path!r}")
    except Exception as e:
        print(f"  原始报错：{type(e).__name__}: {e}")
        return 1
    print("[COM] 尝试 app.DoJavaScript('1+1', [], 1) ...")
    try:
        print("  返回值：", app.DoJavaScript("1+1", [], 1))
    except Exception as e:
        print(f"  原始报错：{type(e).__name__}: {e}")
    print("[COM] 尝试 app.Open(某个真值 PNG) ...")
    try:
        png = (REPO_ROOT / "research" / "测试结果2" / "减去_d100" / "b0_c0.png").as_posix()
        doc = app.Open(png)
        print(f"  返回值：doc.Name={doc.Name!r}")
    except Exception as e:
        print(f"  原始报错：{type(e).__name__}: {e}")
    kill_photoshop()
    return 0


def merge_manifest(auto_entries: list[dict]) -> int:
    """把自动出图条目合并进 tests/baselines/ps/manifest.json（保留人工入库条目）。"""
    manifest = {}
    if MANIFEST_PATH.is_file():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    images = [e for e in manifest.get("images", []) if e.get("provenance") != "auto_jsx"]
    images.extend(auto_entries)
    manifest.update(
        {
            "schema": "brushconverter.baseline.ps/v1",
            "generated_by": "tools/ps_probe.py + tools/ps_ingest.py",
            "naming": "<来源>__<标识>__<模式>__<关键参数>.png",
            "note": "本清单是 tests/baselines/ps/ 子目录清单；顶层 tests/baselines/manifest.json 由任务 09 合并，本任务不写顶层。",
            "count": len(images),
            "images": images,
        }
    )
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(images)


def batch(limit: int | None) -> int:
    ensure_dirs()
    # 先清掉上一轮自动出图，避免旧文件（例如未生效的线性高度）残留在目录里
    for stale in BASELINE_DIR.glob(f"ps__{IDENT}__*.png"):
        stale.unlink()
    make_texture()
    combos = DEFAULT_COMBOS[:limit] if limit else DEFAULT_COMBOS
    jobs = build_jobs(combos)
    print(f"[批量] 目标 {len(jobs)} 张，启动 Photoshop …")
    data = run_jsx(jobs)
    results = data.get("results", [])
    ok = [r for r in results if r.get("ok")]
    print(f"[批量] PS 侧返回 {len(results)} 条，成功 {len(ok)} 条")
    if BATCH_LOG.is_file():
        tail = BATCH_LOG.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-6:]
        print("[批量] batch.log 末尾：")
        for line in tail:
            print("   ", line)
    auto_entries = []
    for job in jobs:
        path = BASELINE_DIR / job["file"]
        res = next((r for r in results if r.get("file") == job["file"]), None)
        if not path.is_file():
            print(f"[缺失] {job['file']} 未产出")
            continue
        normalized = normalize_rgb_black(path)
        stats = alpha_stats(path)
        auto_entries.append(
            {
                "file": job["file"],
                "source": "ps",
                "size": [stats["width"], stats["height"]],
                "note": (
                    f"Photoshop 2025（26.9.0）Action Manager 自动出图；{job['note']}；"
                    f"画布 {CANVAS['width']}x{CANVAS['height']}@{CANVAS['dpi']}ppi，纹理 {TEXTURE_NAME} 64x64 scale={TEXTURE_SCALE}%，"
                    f"画笔直径 {BRUSH['diameter']}px/硬度 {BRUSH['hardness']}%/间距 {BRUSH['spacing']}%（单笔尖）；"
                    f"PS 回读模式={res.get('mode_read') if res else '?'}；"
                    f"Alpha 非零 {stats['nonzero_pixels']}/{stats['total_pixels']}，均值 {stats['alpha_mean']}，取值种数 {stats['alpha_unique']}；"
                    + ("导出后已把 RGB 清零（Alpha 未改）" if normalized else "RGB 本身全 0")
                ),
                "mode": job["mode"],
                "depth": job["depth"],
                "brightness": job["brightness"],
                "contrast": job["contrast"],
                "mode_read_back": (res or {}).get("mode_read", ""),
                "alpha_stats": {
                    "nonzero_pixels": stats["nonzero_pixels"],
                    "total_pixels": stats["total_pixels"],
                    "alpha_mean": stats["alpha_mean"],
                    "alpha_min": stats["alpha_min"],
                    "alpha_max": stats["alpha_max"],
                    "alpha_unique": stats["alpha_unique"],
                },
                "canvas": CANVAS,
                "brush": BRUSH,
                "texture": {"name": TEXTURE_NAME, "scale_percent": TEXTURE_SCALE, "path": TEXTURE_PATH.as_posix()},
                "provenance": "auto_jsx",
                "generator": "tools/ps_export.jsx via tools/ps_probe.py --batch",
            }
        )
    total = merge_manifest(auto_entries)
    print(f"[完成] 本次登记 {len(auto_entries)} 张自动图；清单共 {total} 条 -> {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    return 0 if len(auto_entries) >= 12 else 1


def verify() -> int:
    problems: list[str] = []
    if not MANIFEST_PATH.is_file():
        print(f"[失败] 清单不存在：{MANIFEST_PATH}")
        return 1
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    images = manifest.get("images", [])
    auto = [e for e in images if e.get("provenance") == "auto_jsx"]
    human = [e for e in images if e.get("provenance") != "auto_jsx"]
    print(f"[清单] 共 {len(images)} 条：自动出图 {len(auto)} 张，人工入库 {len(human)} 张")
    if len(auto) < 12:
        problems.append(f"自动出图只有 {len(auto)} 张，少于任务要求的 12 张")
    for entry in images:
        name = entry.get("file", "")
        path = BASELINE_DIR / name
        stem = name[:-4] if name.endswith(".png") else ""
        fields = stem.split("__")
        if len(fields) != 4 or fields[0] != "ps" or not fields[1] or not fields[3].startswith("d"):
            problems.append(f"命名不合规：{name}")
        if not path.is_file():
            problems.append(f"文件缺失：{name}")
            continue
        stats = alpha_stats(path)
        if stats["nonzero_pixels"] == 0:
            problems.append(f"Alpha 全 0（不能当基准）：{name}")
        if [stats["width"], stats["height"]] != list(entry.get("size", [])):
            problems.append(f"尺寸与清单不一致：{name}")
    on_disk = {p.name for p in BASELINE_DIR.glob("*.png")}
    extra = on_disk - {e.get("file") for e in images}
    if extra:
        problems.append(f"目录里有未登记的图：{sorted(extra)[:5]}")
    if problems:
        print("[失败] 校验发现问题：")
        for p in problems:
            print("  -", p)
        return 1
    print("[通过] 命名合规、文件齐全、Alpha 非全 0、目录无未登记图片")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="驱动 Photoshop 批量出真值图（任务 05）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--com-check", action="store_true", help="只验证 pywin32 COM 通路并打印原始返回值")
    group.add_argument("--batch", action="store_true", help="批量出图并登记清单")
    group.add_argument("--verify", action="store_true", help="校验基准目录与清单")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 个组合（调试用）")
    args = parser.parse_args()
    if args.com_check:
        return com_check()
    if args.verify:
        return verify()
    return batch(args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
