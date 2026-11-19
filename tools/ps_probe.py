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

实验矩阵（PS 侧真值，产物在 tests/baselines/exp/ps/，不影响上面的 --batch）：
    python tools/ps_probe.py --exp-batch     # 矩阵 A(24)+矩阵 B(4)+参考图(1)=29 张
    python tools/ps_probe.py --exp-verify    # 校验实验目录、清单、回读结果
"""
from __future__ import annotations

import argparse
import hashlib
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


# =========================================================================
# 实验（exp）通路：矩阵 A（缩放 x 深度）+ 矩阵 B（亮度/对比度）+ 无纹理参考图，共 29 张
#   * 只写 tests/baselines/exp/ps/（含自身 manifest.json 与 _work/ 取证材料）；
#     不读写 tests/baselines/ps/，与上面的 --batch 完全隔离。
#   * 纹理直接用 research/测试用纹理.png（只读，64x64 RGBA）：与 Krita 侧 preset
#     引用的必须是同一文件字节，所以这里不做任何复制或重采样。
#   * 本段只新增函数，不改动 --batch / --verify 的既有行为。
# =========================================================================
EXP_DIR = REPO_ROOT / "tests" / "baselines" / "exp" / "ps"
EXP_WORK = EXP_DIR / "_work"
EXP_MANIFEST_PATH = EXP_DIR / "manifest.json"
EXP_JOB_PATH = EXP_WORK / "job.json"
EXP_DONE_PATH = EXP_WORK / "done.json"
EXP_BATCH_LOG = EXP_WORK / "batch.log"
EXP_TEXTURE_PATH = REPO_ROOT / "research" / "测试用纹理.png"
EXP_TEXTURE_NAME = "bc_exp_texture"
# 实验矩阵的模式键 -> Action Manager 写法（与 --batch 的 MODE_SPEC 同源，但键名固定为英文 modeKey）
EXP_MODES = {"multiply": ("char", "Mltp"), "linearHeight": ("str", "linearHeight")}
EXP_DEPTHS = (10, 60, 100)
EXP_SCALES = (50, 100, 150, 200)
EXP_BC = ((-50, 0), (50, 0), (0, 50), (0, -50))
# 实验笔刷：只用 tests/定量测试专用.abr 里那支计算笔刷（Dmtr=200、硬度100、间距10），不换笔
EXP_ABR_PATH = REPO_ROOT / "tests" / "定量测试专用.abr"
EXP_BRUSH_NAME = "定量测试用"
# ABR 源纹理是反相的（bp.texture.invert=True），PS 侧显式写 invertTexture=true 与之对齐
EXP_INVERT_TEXTURE = True
# 落笔几何（所有 29 张完全一致）：画布中心 (50,50) -> (50.4,50.4) 的 0.4px 路径，
# 直径 200px 硬笔尖单章覆盖整个 100x100 画布（最远角 70.7px < 半径 100px）
EXP_STROKE_START = (50.0, 50.0)
EXP_STROKE_END = (50.4, 50.4)
# 全画布覆盖判据：Alpha 非零像素占比 < 0.97 要在清单里标注并在报告里说明原因
EXP_COVERAGE_MIN = 0.97


def exp_job(mode: str, depth: int, scale: int, brightness: int, contrast: int) -> dict:
    """构造矩阵 A / B 的一条出图任务（全部启用纹理）。"""
    kind, value = EXP_MODES[mode]
    return {
        "file": f"exp__ps__{mode}__d{depth}_s{scale}_b{brightness}_c{contrast}.png",
        "mode_key": mode,
        "mode_kind": kind,
        "mode_value": value,
        "use_texture": True,
        "invert_texture": EXP_INVERT_TEXTURE,
        "texture_scale": scale,
        "depth": depth,
        "brightness": brightness,
        "contrast": contrast,
        "note": f"模式={mode} 深度={depth} 缩放={scale} 亮度={brightness} 对比度={contrast}",
    }


def exp_reference_job() -> dict:
    """构造无纹理参考图（useTexture=false），几何与其它图完全一致。"""
    return {
        "file": "exp__ps__ref__notex.png",
        "mode_key": "none",
        "mode_kind": "",
        "mode_value": "",
        "use_texture": False,
        "invert_texture": False,
        "texture_scale": 0,
        "depth": 0,
        "brightness": 0,
        "contrast": 0,
        "note": "参考图：useTexture=false（不启用纹理）；画布/画笔/落笔方式与其它图完全一致",
    }


def exp_jobs_all() -> list[dict]:
    """矩阵 A（24）+ 矩阵 B（4）+ 参考图（1）= 29 张，顺序固定（A -> B -> 参考图）。"""
    jobs: list[dict] = []
    for mode in ("multiply", "linearHeight"):
        for depth in EXP_DEPTHS:
            for scale in EXP_SCALES:
                jobs.append(exp_job(mode, depth, scale, 0, 0))
    for brightness, contrast in EXP_BC:
        jobs.append(exp_job("multiply", 100, 100, brightness, contrast))
    jobs.append(exp_reference_job())
    return jobs


def exp_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def exp_write_job_file(jobs: list[dict]) -> None:
    job = {
        "out_dir": EXP_DIR.as_posix(),
        "texture_path": EXP_TEXTURE_PATH.as_posix(),
        "texture_name": EXP_TEXTURE_NAME,
        "texture_scale": 100,
        "abr_path": EXP_ABR_PATH.as_posix(),
        "brush_name": EXP_BRUSH_NAME,
        "canvas": CANVAS,
        "brush": BRUSH,
        "jobs": jobs,
    }
    EXP_JOB_PATH.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def exp_run_jsx(jobs: list[dict], timeout: int = 600):
    """启动 Photoshop 跑 tools/ps_export.jsx（WORK 用环境变量切到实验目录），返回 (done, 耗时, 是否残留 PS)。"""
    EXP_DIR.mkdir(parents=True, exist_ok=True)
    EXP_WORK.mkdir(parents=True, exist_ok=True)
    if not EXP_TEXTURE_PATH.is_file():
        raise FileNotFoundError(f"找不到实验纹理：{EXP_TEXTURE_PATH}")
    # 开跑前确认没有别的 PS 实例在跑；有就停手报告，绝不强杀不是本脚本启动的 Photoshop
    if photoshop_running():
        raise RuntimeError(
            "检测到 Photoshop 已在运行：实验开始前必须没有其它 PS 实例（本脚本不会强杀不是自己启动的 PS）。"
            "请先手动关闭 Photoshop 再重跑。"
        )
    for p in (EXP_DONE_PATH, EXP_BATCH_LOG):
        if p.exists():
            p.unlink()
    exp_write_job_file(jobs)
    env = os.environ.copy()
    env["BRUSHCONVERTER_PS_WORK"] = EXP_WORK.as_posix()
    env["BRUSHCONVERTER_REPO"] = REPO_ROOT.as_posix()
    start = time.time()
    proc = subprocess.Popen(
        [str(PS_EXE), "-r", str(JSX_PATH)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if EXP_DONE_PATH.exists():
            time.sleep(0.5)
            break
        if proc.poll() is not None and not EXP_DONE_PATH.exists():
            time.sleep(2.0)
            if EXP_DONE_PATH.exists():
                break
            raise RuntimeError(f"Photoshop 提前退出（returncode={proc.returncode}）且未写 done.json，见 {EXP_BATCH_LOG}")
        time.sleep(1.0)
    else:
        raise TimeoutError(f"等待 {timeout}s 仍未生成 done.json，见 {EXP_BATCH_LOG}")
    elapsed = time.time() - start
    data = json.loads(EXP_DONE_PATH.read_text(encoding="utf-8"))
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    kill_photoshop()
    time.sleep(1.5)
    leftover = photoshop_running()
    return data, elapsed, leftover


def exp_alpha_stats(path: Path) -> dict:
    """统计一张实验图的 Alpha 与尺寸信息（覆盖率的分子分母都在这里算）。"""
    with Image.open(path) as im:
        image_mode = im.mode
        arr = np.array(im.convert("RGBA"))
    a = arr[:, :, 3]
    nonzero = int(np.count_nonzero(a))
    total = int(a.size)
    hist = np.bincount(a.ravel(), minlength=256)
    return {
        "image_mode": image_mode,
        "width": int(a.shape[1]),
        "height": int(a.shape[0]),
        "nonzero_pixels": nonzero,
        "total_pixels": total,
        "nonzero_ratio": round(nonzero / float(total), 6),
        "alpha_min": int(a.min()),
        "alpha_max": int(a.max()),
        "alpha_mean": round(float(a.mean()), 2),
        "alpha_unique": int(np.count_nonzero(hist)),
        "rgb_nonzero_pixels": int(np.count_nonzero(np.any(arr[:, :, :3] != 0, axis=2))),
    }


def exp_parse_readback(text: str) -> dict:
    out = {}
    for part in str(text or "").split(";"):
        if "=" in part:
            key, _, value = part.partition("=")
            out[key.strip()] = value
    return out


def exp_check_readback(job: dict, readback_text: str) -> bool:
    """Python 侧独立复核 JSX 回读串（不信任 JS 里 verify 的结论，自己再比一遍）。

    校验 7 个字段：useTexture / invertTexture / 图案名 / scale / depth / brightness / contrast /
    blendMode。参考图（useTexture=false）只要求 useTexture 为 false（PS 也可能把键拿掉 → ABSENT）。
    """
    rb = exp_parse_readback(readback_text)
    if not job["use_texture"]:
        return rb.get("useTexture") in ("false", "ABSENT")
    try:
        return (
            rb.get("useTexture") == "true"
            and rb.get("invert") == ("true" if job["invert_texture"] else "false")
            and rb.get("pattern") == EXP_TEXTURE_NAME
            and abs(float(rb["scale"]) - float(job["texture_scale"])) < 1e-6
            and abs(float(rb["depth"]) - float(job["depth"])) < 1e-6
            and int(rb["brightness"]) == int(job["brightness"])
            and int(rb["contrast"]) == int(job["contrast"])
            and rb.get("blend", "").lower() == job["mode_key"].lower()
        )
    except (KeyError, TypeError, ValueError):
        return False


def exp_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def exp_force_rgba(path: Path) -> str:
    """把 PS 导出的「全不透明 RGB PNG」补成 RGBA（Alpha=255），返回后处理说明；空串=未改动。

    背景：PS 2025 对没有任何透明像素的图会导出 RGB PNG（实测参考图就是这种），
    而实验口径要求所有图都是 RGBA（下游按 Alpha 比对）。这里只补 Alpha 通道，
    RGB 值、尺寸、像素内容都不动，并在清单与报告里如实登记。
    """
    with Image.open(path) as im:
        mode = im.mode
        arr = np.array(im.convert("RGBA"))
    if mode == "RGBA":
        return ""
    arr[:, :, 3] = 255
    Image.fromarray(arr, "RGBA").save(path)
    return f"rgb_to_rgba_opaque（PS 导出 {mode}，Python 补 Alpha=255，RGB/尺寸不变）"


def exp_build_manifest(jobs: list[dict], data: dict, elapsed: float, tex_sha: str) -> tuple[dict, list, list, list]:
    """按 29 条任务生成清单条目；返回 (manifest, 缺失文件, 回读失败, 覆盖率不足)。"""
    results = data.get("results", [])
    by_file = {r.get("file"): r for r in results}
    entries = []
    missing: list = []
    readback_fail: list = []
    low_coverage: list = []
    for job in jobs:
        path = EXP_DIR / job["file"]
        if not path.is_file():
            missing.append(job["file"])
            continue
        postprocess = exp_force_rgba(path)
        st = exp_alpha_stats(path)
        res = by_file.get(job["file"], {})
        rb_text = str(res.get("readback", ""))
        rb_ok = bool(res.get("verify")) and exp_check_readback(job, rb_text)
        if not rb_ok:
            readback_fail.append(job["file"])
        below = st["nonzero_ratio"] < EXP_COVERAGE_MIN
        if below:
            low_coverage.append({"file": job["file"], "nonzero_ratio": st["nonzero_ratio"]})
        entries.append(
            {
                "file": job["file"],
                "source": "ps",
                "mode": job["mode_key"],
                "ps_mode": job["mode_key"],
                "ps_depth": job["depth"] if job["use_texture"] else None,
                "ps_scale_pct": job["texture_scale"] if job["use_texture"] else None,
                "ps_brightness": job["brightness"] if job["use_texture"] else None,
                "ps_contrast": job["contrast"] if job["use_texture"] else None,
                "invert_texture": (EXP_INVERT_TEXTURE if job["use_texture"] else None),
                "pattern_name": (EXP_TEXTURE_NAME if job["use_texture"] else None),
                "depth": job["depth"] if job["use_texture"] else None,
                "scale": job["texture_scale"] if job["use_texture"] else None,
                "brightness": job["brightness"] if job["use_texture"] else None,
                "contrast": job["contrast"] if job["use_texture"] else None,
                "use_texture": job["use_texture"],
                "size": [st["width"], st["height"]],
                "image_mode": st["image_mode"],
                "postprocess": postprocess,
                "sha256": exp_sha256(path),
                "alpha_nonzero_ratio": st["nonzero_ratio"],
                "coverage_below_threshold": below,
                "alpha": {
                    "nonzero_pixels": st["nonzero_pixels"],
                    "total_pixels": st["total_pixels"],
                    "min": st["alpha_min"],
                    "max": st["alpha_max"],
                    "mean": st["alpha_mean"],
                    "unique": st["alpha_unique"],
                },
                "rgb_nonzero_pixels": st["rgb_nonzero_pixels"],
                "readback_raw": rb_text,
                "readback_expected": str(res.get("expected", "")),
                "readback_ok": rb_ok,
                "ps_side_verify": bool(res.get("verify")),
                "ps_side_ok": bool(res.get("ok")),
                "ps_side_error": str(res.get("error", "")),
                "note": job["note"],
            }
        )
    manifest = {
        "schema": "brushconverter.baseline.exp.ps/v1",
        "generated_by": "tools/ps_probe.py --exp-batch + tools/ps_export.jsx",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "photoshop": {
            "exe": PS_EXE.name,
            "version": str(data.get("ps_version", "")),
        },
        "canvas": CANVAS,
        "brush": {
            "abr_path": EXP_ABR_PATH.as_posix(),
            "brush_name": EXP_BRUSH_NAME,
            "note": "只用 tests/定量测试专用.abr 里的那支计算笔刷：不换笔、不改笔刷几何（ABR 解析 Dmtr=200 / 硬度100 / 间距10）",
            "ps_readback": data.get("brush", {}),
            "jsx_flags_set": {"opacity": 100, "flow": 100, "useTipDynamics": False,
                              "useScatter": False, "smoothing": False},
        },
        "geometry": {
            "canvas": f"{CANVAS['width']}x{CANVAS['height']}@{CANVAS['dpi']}ppi",
            "stroke_start": list(EXP_STROKE_START),
            "stroke_end": list(EXP_STROKE_END),
            "stroke_width_px": 0.4,
            "stamp": "单笔尖：直径 200px 硬笔尖，圆心约 (50.2,50.2)，画布最远角 70.7px < 半径 100px，整幅都在笔尖内部",
            "dab_center": [50.2, 50.2],
        },
        "texture": {
            "name": EXP_TEXTURE_NAME,
            "path": EXP_TEXTURE_PATH.as_posix(),
            "sha256": tex_sha,
            "md5": exp_md5(EXP_TEXTURE_PATH),
            "size": [64, 64],
            "invert_texture": EXP_INVERT_TEXTURE,
            "note": "PS 侧从 research/测试用纹理.png 的原始字节定义图案（definePattern）后按名字引用；与 Krita 侧 preset 纹理为同一文件字节（同 md5）",
        },
        "matrices": {
            "A": {
                "desc": "缩放 x 深度，brightness=0、contrast=0",
                "modes": ["multiply", "linearHeight"],
                "depths": list(EXP_DEPTHS),
                "scales": list(EXP_SCALES),
                "count": 24,
            },
            "B": {
                "desc": "亮度/对比度，固定 multiply/d100/s100",
                "bc": [list(p) for p in EXP_BC],
                "count": 4,
            },
            "reference": {"desc": "无纹理参考图（useTexture=false，其余几何/笔刷/纹理字节口径不变）", "count": 1},
        },
        "coverage_threshold": EXP_COVERAGE_MIN,
        "run": {
            "elapsed_seconds": round(elapsed, 1),
            "requested": len(jobs),
            "produced": len(entries),
            "photoshop_leftover": None,
        },
        "count": len(entries),
        "images": entries,
    }
    return manifest, missing, readback_fail, low_coverage


def exp_batch() -> int:
    EXP_DIR.mkdir(parents=True, exist_ok=True)
    EXP_WORK.mkdir(parents=True, exist_ok=True)
    # 清掉上一轮实验图，避免旧文件混进清单（只删本目录下 exp__ps__*.png）
    for stale in EXP_DIR.glob("exp__ps__*.png"):
        stale.unlink()
    jobs = exp_jobs_all()
    tex_sha = exp_sha256(EXP_TEXTURE_PATH)
    print(f"[实验] 矩阵 A 24 张 + 矩阵 B 4 张 + 参考图 1 张 = {len(jobs)} 张；"
          f"纹理 {EXP_TEXTURE_PATH.name} sha256={tex_sha[:16]}…；启动 Photoshop …")
    data, elapsed, leftover = exp_run_jsx(jobs)
    manifest, missing, readback_fail, low_coverage = exp_build_manifest(jobs, data, elapsed, tex_sha)
    manifest["run"]["photoshop_leftover"] = leftover
    EXP_MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_count = sum(1 for e in manifest["images"] if e["ps_side_ok"])
    print(f"[实验] 出图完成：PS 侧成功 {ok_count}/{len(jobs)} 张，耗时 {elapsed:.1f}s，"
          f"Photoshop 残留={'有' if leftover else '无'}")
    print(f"[实验] 清单：{EXP_MANIFEST_PATH.relative_to(REPO_ROOT)}（{manifest['count']} 条）")
    if missing:
        print(f"[实验][失败] 缺失 {len(missing)} 张：{missing}")
    if readback_fail:
        print(f"[实验][失败] 回读与期望不一致 {len(readback_fail)} 张：{readback_fail}")
    if low_coverage:
        print(f"[实验][警告] Alpha 非零占比 <{EXP_COVERAGE_MIN} 的图 {len(low_coverage)} 张：{low_coverage}")
    else:
        print(f"[实验] Alpha 非零占比全部 >= {EXP_COVERAGE_MIN}")
    # 至少 3 张回读原文打屏，方便报告直接取证
    for entry in manifest["images"][:1] + manifest["images"][12:13] + manifest["images"][-1:]:
        print(f"[回读] {entry['file']} -> {entry['readback_raw']}")
    if EXP_BATCH_LOG.is_file():
        tail = EXP_BATCH_LOG.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-5:]
        print("[实验] batch.log 末尾：")
        for line in tail:
            print("   ", line)
    all_ok = (not missing) and (not readback_fail) and (not leftover) and len(manifest["images"]) == len(jobs)
    return 0 if all_ok else 1


def exp_verify() -> int:
    problems: list[str] = []
    if not EXP_MANIFEST_PATH.is_file():
        print(f"[失败] 实验清单不存在：{EXP_MANIFEST_PATH}")
        return 1
    manifest = json.loads(EXP_MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = manifest.get("images", [])
    expected = exp_jobs_all()
    expected_files = [j["file"] for j in expected]
    got_files = [e.get("file") for e in entries]
    print(f"[实验清单] {len(entries)} 条；期望 {len(expected_files)} 条")
    if len(entries) != len(expected_files):
        problems.append(f"条目数 {len(entries)} != 期望 {len(expected_files)}")
    missing = [f for f in expected_files if f not in got_files]
    extra = [f for f in got_files if f not in expected_files]
    if missing:
        problems.append(f"清单缺少：{missing}")
    if extra:
        problems.append(f"清单多出：{extra}")
    for entry in entries:
        name = entry.get("file", "")
        path = EXP_DIR / name
        if not path.is_file():
            problems.append(f"文件缺失：{name}")
            continue
        st = exp_alpha_stats(path)
        if st["nonzero_pixels"] == 0:
            problems.append(f"Alpha 全 0：{name}")
        if st["width"] != CANVAS["width"] or st["height"] != CANVAS["height"]:
            problems.append(f"尺寸不是 {CANVAS['width']}x{CANVAS['height']}：{name} -> {st['width']}x{st['height']}")
        if st["image_mode"] != "RGBA":
            problems.append(f"导出不是 RGBA：{name} -> {st['image_mode']}")
        if exp_sha256(path) != entry.get("sha256"):
            problems.append(f"sha256 与清单不一致：{name}")
        if not entry.get("readback_ok"):
            problems.append(f"回读校验未通过：{name}")
        if st["nonzero_ratio"] < EXP_COVERAGE_MIN and not entry.get("coverage_below_threshold"):
            problems.append(f"覆盖率低于阈值却未标注：{name}")
    on_disk = {p.name for p in EXP_DIR.glob("*.png")}
    unregistered = on_disk - set(got_files)
    if unregistered:
        problems.append(f"目录里有未登记的图：{sorted(unregistered)[:5]}")
    low = [e["file"] for e in entries if e.get("coverage_below_threshold")]
    print(f"[覆盖率] 低于 {EXP_COVERAGE_MIN} 的图：{low if low else '无'}")
    if problems:
        print("[失败] 实验校验发现问题：")
        for p in problems:
            print("  -", p)
        return 1
    print("[通过] 实验目录：29 张齐全、命名合规、尺寸/RGBA/sha256/回读/覆盖率标注全部一致")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="驱动 Photoshop 批量出真值图（任务 05）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--com-check", action="store_true", help="只验证 pywin32 COM 通路并打印原始返回值")
    group.add_argument("--batch", action="store_true", help="批量出图并登记清单")
    group.add_argument("--verify", action="store_true", help="校验基准目录与清单")
    group.add_argument("--exp-batch", action="store_true", help="实验矩阵（29 张）出图到 tests/baselines/exp/ps/")
    group.add_argument("--exp-verify", action="store_true", help="校验实验目录与清单")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 个组合（调试用）")
    args = parser.parse_args()
    if args.com_check:
        return com_check()
    if args.verify:
        return verify()
    if args.exp_batch:
        return exp_batch()
    if args.exp_verify:
        return exp_verify()
    return batch(args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
