# -*- coding: utf-8 -*-
"""黑盒拟合工具（任务 12）：扩样本 -> 抽配对 -> 拟合 1D LUT 与参数化公式 -> 与真实 PS 输出比误差。

本脚本只做「拟合与评估」，不改 src/、不改既有真值图。写入范围（任务书）：
    tools/lut_fit.py、tests/test_lut_fit.py、tests/baselines/ps/（新增图 + 子目录清单追加）、
    docs/研究报告/lut-拟合.md

四个子命令
----------
probe   用 tools/ps_probe.py 的 JSX 通路批量出图（复用其 build_jobs/run_jsx/alpha_stats），
        新图命名 ps__拟合A__<模式>__d<深度>_b<亮度>_c<对比度>.png，并**追加**进
        tests/baselines/ps/manifest.json（保留原有 178 条，顶层 manifest 不动）。
explore 抽样诊断：验证文档坐标到纹理坐标的相位、逐点可映射性、亮度/对比度/深度实测曲线。
fit     拟合两种形态并输出三列误差表（LUT / 参数化公式 / 现有经验公式基线），导出 JSON。
all     explore + fit。

关键口径（与任务 03/05 一致，改口径必须整批重出）
--------------------------------------------------
* 画布 100x100、纹理 64x64、scale=100%、画笔 300px/间距 1000%（单笔尖）。
* 纹理图案在画布上按 (0,0) 相位平铺：实测 正片叠底 d100 b0 c0 的 Alpha 与
  texture[y%64, x%64] 逐像素完全相等（10000/10000），减去 d100 b0 c0 等于 255-它。
  因此「输入纹理值 -> 输出 Alpha」可以逐像素配对，不需要估计相位。
* 误差口径与 tools/imgdiff.py 一致：tol=2（吸收量化抖动），
  差异比例 = 差异像素数 / 全部像素数，达标线 2%。
* alpha_unique==1 的图（高度/线性高度 d60/d100 饱和成全 255）无信息量，拟合与评估都过滤。

配对数据的三个来源
------------------
1) 任务 05 的 20 张自动图（标识 自动纹理A，同一张 seed=20261118 纹理）；
2) 本任务新出的自动图（标识 拟合A，同一张纹理）；
3) 历史人工真值图 research/测试结果2（标识 定量测试v2，110 张）。它的输入纹理未知，
   但 正片叠底_d100/b0_c0 在 PS 里是恒等变换（Alpha == 纹理值），可无损反推出该批次
   的纹理图（256 个取值全在），于是也能逐像素配对。该反推图本身是恒等图，评估时剔除。
   research/测试结果（v1，d60/d6）的纹理无法无损反推（乘 d60 反演有量化丢失），
   只做分布级参考，不进像素级误差表（报告里写明）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # 允许以 python tools/lut_fit.py 直接运行
    sys.path.insert(0, str(REPO_ROOT))

BASELINE_DIR = REPO_ROOT / "tests" / "baselines" / "ps"
WORK_DIR = BASELINE_DIR / "_work"
MANIFEST_PATH = BASELINE_DIR / "manifest.json"
TEXTURE_PATH = WORK_DIR / "texture_noise64.png"
RESULT_PATH = WORK_DIR / "lut_fit_result.json"

IDENT_NEW = "拟合A"          # 本任务新图的标识（与任务 05 的 自动纹理A 区分）
TEXTURE_NAME = "bc_texture_noise64"
CANVAS = 100                 # 画布边长
TOL = 2                      # 差异点阈值，与 tools/imgdiff.py 的 DEFAULT_TOL 一致
RATIO_THRESHOLD = 0.02       # 达标线：差异比例 <= 2%

MODES = ["减去", "正片叠底", "高度", "线性高度"]

#: Krita 纹理混合模式枚举号（KisTextureOptionData.h 顺序，见 tools/texture_mask.py）
KRITA_MODE_ID = {"正片叠底": 0, "减去": 1, "高度": 14, "线性高度": 15}

#: 采样网格：4 模式 x >=3 深度 x 亮度{0,±50,±150} x 对比度{0,±50}
BC_ALL = [(0, 0), (50, 0), (-50, 0), (150, 0), (-150, 0), (0, 50), (0, -50), (50, 50), (-50, -50)]
BC_HEIGHT = [(0, 0), (50, 0), (-50, 0), (150, 0), (-150, 0), (0, 50), (0, -50)]
#: 补点（诊断用）：亮度/对比度同时非零的四个象限，以及高度系的中间深度，用来定标
#: 「亮度与对比度的先后顺序」和「深度 A(d)/B(d) 律」。不属于任务书要求的最低覆盖，但缺了
#: 就无法确认参数化公式的交互项。
GRID_EXTRA = [
    ("正片叠底", [100], [(50, -50), (-50, 50), (150, -50), (-150, 50), (150, 50), (-150, -50)]),
    ("高度", [4, 6, 12], [(0, 0)]),
    ("线性高度", [4, 6, 12], [(0, 0)]),
]
GRID = [
    ("减去", [100, 60, 30], BC_ALL),
    ("正片叠底", [100, 60, 30], BC_ALL),
    ("高度", [20, 10, 5, 2], BC_HEIGHT),
    ("线性高度", [20, 10, 5, 2], BC_HEIGHT),
] + GRID_EXTRA

#: 实测会输出「全 0」的组合：高度 d2 的 A = 12*q = 60，b=+150 或 c=-50 会把整张图压到 0。
#: 全 0 图无信息量，且 tools/ps_probe.py --verify 会把「Alpha 全 0」判为不能当基准，
#: 因此不进基准库；这几个组合从采样网格里剔除（其余组合仍满足 4 模式 x >=3 深度 x 亮度/对比度覆盖）。
SKIP_COMBOS = {("高度", 2, 150, 0), ("高度", 2, 0, -50)}


# ===========================================================================
# 一、扩样本（复用任务 05 的 JSX 通路）
# ===========================================================================
def grid_combos() -> list:
    """展开采样网格，返回 [(模式, 深度, 亮度, 对比度)]。"""
    combos = []
    for mode, depths, bcs in GRID:
        for depth in depths:
            for b, c in bcs:
                if (mode, depth, b, c) in SKIP_COMBOS:
                    continue
                combos.append((mode, depth, b, c))
    return combos


def prune_all_zero(verbose: bool = True) -> int:
    """删除本任务新增图里 Alpha 全 0 的那几张，并从子目录清单移除。

    全 0 是真实饱和（高度 d2 的 A=60，b=+150 或 c=-50 时整张被压到 0），但没有信息量，
    而且 tools/ps_probe.py --verify 会把它判成「不能当基准」，所以不留在基准库。
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    images = manifest.get("images", [])
    keep, dropped = [], []
    for entry in images:
        is_new = entry.get("file", "").startswith(f"ps__{IDENT_NEW}__")
        if not is_new:
            keep.append(entry)
            continue
        path = BASELINE_DIR / entry["file"]
        if path.is_file():
            alpha = _alpha(path)
            if not np.any(alpha):
                path.unlink()
                dropped.append(entry["file"])
                continue
        keep.append(entry)
    manifest["images"] = keep
    manifest["count"] = len(keep)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if verbose:
        print(f"[清理] 删除 Alpha 全 0 的新增图 {len(dropped)} 张；清单剩 {len(keep)} 条")
        for name in dropped:
            print(f"   - {name}")
    return 0


def new_file_name(mode: str, depth: int, brightness: int, contrast: int) -> str:
    """按 docs/基准库命名规范.md 生成文件名：<来源>__<标识>__<模式>__<关键参数>.png。"""
    return f"ps__{IDENT_NEW}__{mode}__d{depth}_b{brightness}_c{contrast}.png"


def build_jobs(combos, mode_spec: dict) -> list:
    """按 tools/ps_export.jsx 的 job.json 格式构造任务（字段与任务 05 完全一致）。"""
    jobs = []
    for mode, depth, brightness, contrast in combos:
        kind, value = mode_spec[mode]
        jobs.append(
            {
                "file": new_file_name(mode, depth, brightness, contrast),
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


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def merge_manifest_append(new_entries: list) -> int:
    """把新条目追加进 tests/baselines/ps/manifest.json（保留已有条目与顺序）。

    ps_probe.merge_manifest 会先删掉全部 provenance=auto_jsx 的旧条目再写入，用在这里
    会把任务 05 的 20 张自动图从清单里抹掉；本任务是「追加」，所以自己实现：
    同文件名覆盖、其余原样保留、新文件名追加到末尾。
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    existing = manifest.get("images", [])
    merged = {e["file"]: e for e in existing}
    order = [e["file"] for e in existing]
    for entry in new_entries:
        if entry["file"] not in merged:
            order.append(entry["file"])
        merged[entry["file"]] = entry
    manifest["images"] = [merged[name] for name in order]
    manifest["count"] = len(manifest["images"])
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest["count"]


def probe(limit=None, offset: int = 0, timeout: int = 1800, skip_existing: bool = False) -> int:
    """批量出图并登记清单。返回 0 表示成功率 >= 90%。

    skip_existing=True 时只补缺失组合（已有同名文件且清单里已登记的跳过），
    用于扩充网格后补拍，避免整批重出。
    """
    import tools.ps_probe as pp  # 延迟导入：只有出图时才需要 PS 通路

    pp.ensure_dirs()
    pp.make_texture()
    combos = grid_combos()
    if offset:
        combos = combos[offset:]
    if limit:
        combos = combos[:limit]
    jobs = build_jobs(combos, pp.MODE_SPEC)
    if skip_existing:
        registered = set()
        if MANIFEST_PATH.is_file():
            registered = {e["file"] for e in json.loads(MANIFEST_PATH.read_text(encoding="utf-8")).get("images", [])}
        before = len(jobs)
        jobs = [j for j in jobs if not ((BASELINE_DIR / j["file"]).is_file() and j["file"] in registered)]
        print(f"[扩样本] 只补缺失：{before} 个组合中 {len(jobs)} 张待出图")
        if not jobs:
            print("[扩样本] 没有缺失组合，直接退出")
            return 0
    print(f"[扩样本] 目标 {len(jobs)} 张（4 模式 x 3~4 深度 x 亮度{{0,±50,±150}} x 对比度{{0,±50}}）")
    data = pp.run_jsx(jobs, timeout=timeout)
    results = data.get("results", [])
    ok = [r for r in results if r.get("ok")]
    print(f"[扩样本] PS 侧返回 {len(results)} 条，成功 {len(ok)} 条")
    failed = []
    entries = []
    for job in jobs:
        path = BASELINE_DIR / job["file"]
        res = next((r for r in results if r.get("file") == job["file"]), None)
        if not path.is_file():
            failed.append((job["file"], (res or {}).get("error") or "未产出文件"))
            print(f"[缺失] {job['file']} 未产出：{(res or {}).get('error', '')}")
            continue
        normalized = pp.normalize_rgb_black(path)
        stats = pp.alpha_stats(path)
        entries.append(
            {
                "file": job["file"],
                "source": "ps",
                "size": [stats["width"], stats["height"]],
                "note": (
                    f"任务 12 黑盒拟合新增真值图；Photoshop 2025（26.9.0）Action Manager 自动出图；"
                    f"{job['note']}；画布 {pp.CANVAS['width']}x{pp.CANVAS['height']}@{pp.CANVAS['dpi']}ppi，"
                    f"纹理 {TEXTURE_NAME} 64x64 scale={pp.TEXTURE_SCALE}%，"
                    f"画笔直径 {pp.BRUSH['diameter']}px/硬度 {pp.BRUSH['hardness']}%/间距 {pp.BRUSH['spacing']}%；"
                    f"PS 回读模式={(res or {}).get('mode_read', '?')}；"
                    f"Alpha 非零 {stats['nonzero_pixels']}/{stats['total_pixels']}，均值 {stats['alpha_mean']}，"
                    f"取值种数 {stats['alpha_unique']}；"
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
                "canvas": pp.CANVAS,
                "brush": pp.BRUSH,
                "texture": {"name": TEXTURE_NAME, "scale_percent": pp.TEXTURE_SCALE,
                            "path": TEXTURE_PATH.as_posix()},
                "sha256": sha256_of(path),
                "bytes": path.stat().st_size,
                "provenance": "auto_jsx",
                "generator": "tools/ps_export.jsx via tools/lut_fit.py --probe",
            }
        )
    total = merge_manifest_append(entries)
    print(f"[扩样本] 成功入库 {len(entries)}/{len(jobs)} 张；清单共 {total} 条")
    if failed:
        print(f"[扩样本] 失败 {len(failed)} 张：")
        for name, err in failed[:10]:
            print(f"   - {name}: {err}")
    return 0 if entries and len(entries) >= max(1, int(0.9 * len(jobs))) else 1


# ===========================================================================
# 二、配对数据抽取
# ===========================================================================
def load_texture() -> np.ndarray:
    if not TEXTURE_PATH.is_file():
        raise FileNotFoundError(f"纹理不存在：{TEXTURE_PATH}（先跑 --probe 生成）")
    with Image.open(TEXTURE_PATH) as im:
        return np.asarray(im.convert("L"), dtype=np.uint8)


def tiled_texture(side: int = CANVAS) -> np.ndarray:
    """把 64x64 纹理按 (0,0) 相位铺满 side x side 画布（实测相位，见模块 docstring）。"""
    tex = load_texture()
    yy = np.arange(side) % tex.shape[0]
    xx = np.arange(side) % tex.shape[1]
    return tex[yy][:, xx]


def _alpha(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.uint8)[:, :, 3]


def _group_of(entry: dict) -> str:
    """图片所属批次（文件名第二段：自动纹理A / 拟合A / 定量测试v2 等）。"""
    parts = entry.get("file", "").split("__")
    return parts[1] if len(parts) == 4 else "?"


def build_dataset(include_human: bool = True):
    """构建逐像素配对数据集，返回 (records, notes)。

    每条 record：file/group/mode/depth/b/c/t(100x100 输入纹理值)/a(100x100 输出 Alpha)
                 trivial(恒等参考图，评估剔除)/saturated(alpha_unique==1)
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    images = manifest.get("images", [])
    auto_t = tiled_texture()
    records = []
    notes = []

    # 1) 自动图（任务 05 的 20 张 + 本任务新增）：纹理固定，按 (0,0) 相位平铺
    for entry in images:
        if entry.get("provenance") != "auto_jsx":
            continue
        if (entry.get("texture") or {}).get("name") != TEXTURE_NAME:
            continue
        path = BASELINE_DIR / entry["file"]
        if not path.is_file():
            notes.append(f"缺文件跳过：{entry['file']}")
            continue
        alpha = _alpha(path).astype(np.int16)
        records.append(
            {
                "file": entry["file"],
                "group": "auto",
                "mode": entry["mode"],
                "depth": int(entry["depth"]),
                "b": int(entry["brightness"]),
                "c": int(entry["contrast"]),
                "t": auto_t.astype(np.int16),
                "a": alpha,
                "trivial": False,
                "saturated": entry.get("alpha_stats", {}).get("alpha_unique", 1) == 1,
            }
        )
    notes.append(f"自动图配对：{sum(1 for r in records if r['group'] == 'auto')} 张（纹理已知，相位 (0,0)）")

    # 2) 人工真值图（research/测试结果2）：用 正片叠底_d100/b0_c0 反推该批次纹理
    if include_human:
        human_groups = {}
        for entry in images:
            if entry.get("provenance") == "auto_jsx":
                continue
            origin = entry.get("origin") or ""
            if "测试结果2" not in origin:
                continue
            human_groups.setdefault(_group_of(entry), []).append(entry)
        for group, entries in human_groups.items():
            ref = next(
                (e for e in entries
                 if e["mode"] == "正片叠底" and int(e["depth"]) == 100
                 and int(e["brightness"]) == 0 and int(e["contrast"]) == 0),
                None,
            )
            if ref is None:
                notes.append(f"{group}：找不到 正片叠底_d100_b0_c0 参考图，整组跳过")
                continue
            tmap = _alpha(BASELINE_DIR / ref["file"]).astype(np.int16)
            notes.append(f"{group}：用 {ref['file']} 反推纹理图（恒等图，评估时剔除）")
            for entry in entries:
                path = BASELINE_DIR / entry["file"]
                if not path.is_file():
                    continue
                records.append(
                    {
                        "file": entry["file"],
                        "group": group,
                        "mode": entry["mode"],
                        "depth": int(entry["depth"]),
                        "b": int(entry["brightness"]),
                        "c": int(entry["contrast"]),
                        "t": tmap,
                        "a": _alpha(path).astype(np.int16),
                        "trivial": entry["file"] == ref["file"],
                        "saturated": entry.get("alpha_stats", {}).get("alpha_unique", 1) == 1,
                    }
                )
    return records, notes


def pixel_split(side: int = CANVAS):
    """训练/评估的像素划分：棋盘格奇偶（确定性，两半都覆盖全部纹理值）。"""
    yy, xx = np.mgrid[0:side, 0:side]
    train = ((yy + xx) % 2 == 0).ravel()
    return train, ~train


def mappability(records: list, tol: int = 1) -> dict:
    """可映射性：同一输入纹理值在同一张图里是否总是得到同一 Alpha。"""
    per_image = []
    for rec in records:
        if rec["saturated"] or rec["trivial"]:
            continue
        t = rec["t"].ravel()
        a = rec["a"].ravel()
        spread = 0
        for value in np.unique(t):
            sel = a[t == value]
            if sel.size >= 2:
                spread = max(spread, int(sel.max() - sel.min()))
        per_image.append((rec["file"], spread))
    spreads = [s for _, s in per_image]
    return {
        "checked": len(per_image),
        "pointwise_exact": sum(1 for s in spreads if s == 0),
        "pointwise_le_tol": sum(1 for s in spreads if s <= tol),
        "max_spread": max(spreads) if spreads else -1,
        "worst": sorted(per_image, key=lambda kv: -kv[1])[:5],
    }


# ===========================================================================
# 三、参数化公式（亮度/对比度前置 -> 模式合成 -> 深度）
# ===========================================================================
def contrast_factor(c: float) -> float:
    """PS 纹理对比度因子（现代调整层风格，历史两轮已定量确认）。

    C < 0: 1 + C/100；C >= 0: 1 / (1 - C/100)；C>=100 用极大值逼近二值化。
    """
    if c < 0:
        return 1.0 + c / 100.0
    if c >= 100:
        return 1_000_000.0
    return 1.0 / (1.0 - c / 100.0)


def bc_transform(t, b: float, c: float, params: dict) -> np.ndarray:
    """PS 纹理亮度/对比度前置变换：t(0..255) -> u(0..255)。

    参数化形式（两段、可切换先后顺序，顺序由数据判定）：
        p = 对比度中心（拟合值，约 127）
        k = 对比度因子（每个对比度取值单独拟合，未测值回落到经验公式）
        M1  : v = clip(t + b, 0, 255);        u = clip(p + (v - p) * k)
        M1c : v = t + b（不预截断）;          u = clip(p + (v - p) * k)
        M2  : w = clip(p + (t - p) * k);      u = clip(w + b)
        M2c : w = p + (t - p) * k（不预截断）; u = clip(w + b)
        none: v = clip(t + b, 0, 255);        u = clip(p + (v - p) * k + b_corr)（带交互修正）
    """
    p = float(params.get("pivot", 127.5))
    k = float(params["k_fitted"].get(str(int(c)), contrast_factor(c)))
    order = params.get("order", "M1")
    if order == "sign_c":
        # 实测规则：对比度非负 -> 先亮度后对比度；对比度为负 -> 先对比度后亮度
        order = "M1" if c >= 0 else "M2"
    tt = np.asarray(t, dtype=np.float64)
    if order == "M1":
        v = np.clip(tt + b, 0.0, 255.0)
        u = p + (v - p) * k
    elif order == "M1c":
        u = p + (tt + b - p) * k
    elif order == "M2":
        w = np.clip(p + (tt - p) * k, 0.0, 255.0)
        u = w + b
    elif order == "M2c":
        u = p + (tt - p) * k + b
    else:  # 兜底与 M1 相同
        v = np.clip(tt + b, 0.0, 255.0)
        u = p + (v - p) * k
    return np.clip(u, 0.0, 255.0)


#: 亮度/对比度先后顺序的四个候选（用于数据判定）
BC_ORDERS = ("M1", "M1c", "M2", "M2c")


def predict_bc_order(t, b: float, c: float, p: float, k: float, order: str) -> np.ndarray:
    """给定中心/斜率/顺序，直接算前置变换（拟合阶段用，避免依赖 params 字典）。"""
    tt = np.asarray(t, dtype=np.float64)
    if order == "M1":
        u = p + (np.clip(tt + b, 0.0, 255.0) - p) * k
    elif order == "M1c":
        u = p + (tt + b - p) * k
    elif order == "M2":
        u = np.clip(p + (tt - p) * k, 0.0, 255.0) + b
    else:
        u = p + (tt - p) * k + b
    return np.clip(u, 0.0, 255.0)


#: PS 高度/线性高度 的权重系数（实测 A = 12*q；Krita 同一算子写成 10*s）
HEIGHT_WEIGHT = 12.0


def depth_strength(depth: float):
    """PS 纹理深度的 8bit 量化：深度 percentUnit 存成 round(depth*255/100)。

    实测（b0 c0，输入纹理 t=0 处的输出）：
        高度 d2/d4/d5/d6/d10/d12 -> 60/120/156/180/312/372
    恰好等于 12 * round(depth*2.55)，说明深度先量化成 0..255 的整数 q，再进入合成。
    返回 (q, s)，s = q/255 是 0..1 的强度（与 Krita 的 8bit 强度量化同构）。
    """
    q = int(math.floor(float(depth) * 255.0 / 100.0 + 0.5))
    q = max(0, min(255, q))
    return q, q / 255.0


def mode_predict(u, mode: str, depth: float, params: dict) -> np.ndarray:
    """模式合成 + 深度（PS 实测律，推导见 docs/研究报告/lut-拟合.md）。

        q, s = depth_strength(depth)
        正片叠底   out = clip(255 + (u - 255) * s)
        减去       out = clip(255 - u * s)
        高度       out = clip(W * q - u)                          W = 12
        线性高度   out = clip(max((255 - u) * W*q/255, W*q - u))  W = 12
    """
    w = float(params.get("height_weight", HEIGHT_WEIGHT))
    q, s = depth_strength(depth)
    uu = np.asarray(u, dtype=np.float64)
    if mode == "正片叠底":
        out = 255.0 + (uu - 255.0) * s
    elif mode == "减去":
        out = 255.0 - uu * s
    elif mode == "高度":
        out = w * q - uu
    elif mode == "线性高度":
        md = w * q
        out = np.maximum((255.0 - uu) * md / 255.0, md - uu)
    else:
        raise KeyError(f"未实现的纹理模式：{mode}")
    return np.clip(out, 0.0, 255.0)


def parametric_predict(t, mode: str, depth: int, b: int, c: int, params: dict) -> np.ndarray:
    """参数化公式总入口：先亮度/对比度，再模式合成与深度，最后四舍五入到 8bit。"""
    u = bc_transform(np.asarray(t), b, c, params)
    out = mode_predict(u, mode, depth, params)
    return np.floor(out + 0.5)


def fit_height_weight(records: list, params: dict, verbose: bool = True) -> float:
    """从「高度」的未截断样本拟合权重 W：A = 输出 + u = W*q，取 A/q 的中位数。

    线性高度不用来定 W（它的 max 结构在 W*q>255 时会退化成 W*q-u，信息与高度重复）。
    """
    train, _ = pixel_split()
    ratios = []
    for rec in records:
        if rec["mode"] != "高度" or rec["saturated"] or rec["trivial"]:
            continue
        u = bc_transform(rec["t"], rec["b"], rec["c"], params).ravel()[train]
        a = rec["a"].ravel()[train].astype(np.float64)
        sel = (a > 0.5) & (a < 254.5)
        q, _ = depth_strength(rec["depth"])
        if q <= 0 or sel.sum() < 100:
            continue
        ratios.append(float(np.median((a[sel] + u[sel]) / q)))
    w = float(np.median(ratios)) if ratios else HEIGHT_WEIGHT
    if verbose:
        print(f"[拟合] 高度系权重 W = {w:.4f}（参与样本 {len(ratios)} 张；Krita 同算子为 10）")
    return w


def fit_bc(records: list, verbose: bool = True) -> dict:
    """拟合亮度/对比度前置变换。

    只用「正片叠底 + 深度 100」的图：该组合下 PS 输出 Alpha 就是前置变换后的纹理值 u
    （b0c0 实测逐像素恒等），因此可以直接把 Alpha 当作 u 的观测值。

    两步：
    1) 用 b=0 的样本为每个对比度单独拟合 斜率 k 与中心 p（逐点直线最小二乘，
       只用未被截断的样本点）；p 取各对比度拟合值的中位数作为全局中心。
    2) 用 b≠0 且 c≠0 的样本，比较 BC_ORDERS 里四个候选顺序（先亮度后对比度 /
       先对比度后亮度，各自含「预截断」与「不预截断」两版），按未截断区 MAE 选最优。
    """
    train, _ = pixel_split()
    pooled = {}
    for rec in records:
        if rec["saturated"] or rec["trivial"]:
            continue
        if rec["mode"] != "正片叠底" or rec["depth"] != 100:
            continue
        t = rec["t"].ravel().astype(np.float64)[train]
        a = rec["a"].ravel().astype(np.float64)[train]
        pooled.setdefault((rec["b"], rec["c"]), []).append((t, a))
    if not pooled:
        raise RuntimeError("没有 正片叠底 d100 的样本，无法拟合亮度/对比度前置变换")
    flat = {key: (np.concatenate([x[0] for x in v]), np.concatenate([x[1] for x in v]))
            for key, v in pooled.items()}

    # 1) b=0 的样本：每个对比度单独拟合斜率 k 与中心 p
    k_fit = {}
    pivots = []
    for (b_val, c_val), (tt, uu) in sorted(flat.items()):
        if b_val != 0:
            continue
        if c_val == 0:
            k_fit[0] = 1.0
            continue
        sel = (uu > 1.5) & (uu < 253.5)
        if sel.sum() < 50:
            k_fit[c_val] = contrast_factor(c_val)
            continue
        slope, intercept = np.polyfit(tt[sel], uu[sel], 1)
        if abs(1.0 - slope) > 1e-6:
            pivots.append(float(intercept / (1.0 - slope)))
    p = float(np.median(pivots)) if pivots else 127.5
    # 固定全局中心 p，再按一维最小二乘复核每个对比度的斜率
    for (b_val, c_val), (tt, uu) in flat.items():
        if b_val != 0 or c_val == 0:
            continue
        sel = (uu > 1.5) & (uu < 253.5)
        if sel.sum() < 50:
            continue
        x = tt[sel] - p
        y = uu[sel] - p
        k_fit[c_val] = float(np.sum(x * y) / max(np.sum(x * x), 1e-9))

    # 2) b!=0 且 c!=0 的样本：判定亮度与对比度的先后顺序
    order_scores = {o: [] for o in tuple(BC_ORDERS) + ("sign_c",)}
    per_config_order = {}
    for (b_val, c_val), (tt, uu) in sorted(flat.items()):
        if b_val == 0 or c_val == 0:
            continue
        k_val = k_fit.get(c_val, contrast_factor(c_val))
        cand = {}
        for order in tuple(BC_ORDERS) + ("sign_c",):
            eff = ("M1" if c_val >= 0 else "M2") if order == "sign_c" else order
            pred = predict_bc_order(tt, b_val, c_val, p, k_val, eff)
            sel = (uu > 1.5) & (uu < 253.5) & (pred > 1.5) & (pred < 253.5)
            if sel.sum() < 50:
                continue
            cand[order] = float(np.mean(np.abs(pred[sel] - uu[sel])))
            order_scores[order].append(cand[order])
        if cand:
            per_config_order[f"b{b_val}_c{c_val}"] = min(cand, key=cand.get)
    global_scores = {o: (float(np.mean(v)) if v else float("inf")) for o, v in order_scores.items()}
    finite = {o: s for o, s in global_scores.items() if np.isfinite(s)}
    # 没有「亮度/对比度同时非零」的样本时，不能选顺序，直接回落到数据判定出的符号规则
    best_order = min(finite, key=finite.get) if finite else "sign_c"

    params = {
        "pivot": round(p, 4),
        "k_fitted": {str(c): round(float(k), 6) for c, k in sorted(k_fit.items())},
        "order": best_order,
        "order_scores": {o: round(s, 4) for o, s in global_scores.items()},
        "per_config_order": per_config_order,
    }
    for rec in records:  # 未参与拟合的对比度取值回落到经验公式
        params["k_fitted"].setdefault(str(int(rec["c"])), round(contrast_factor(rec["c"]), 6))
    if verbose:
        print(f"[拟合] 亮度/对比度前置：中心 p = {p:.3f}，顺序（全局最优）= {best_order}，"
              f"各顺序未截断区 MAE = {params['order_scores']}")
        for c_val in sorted(k_fit):
            print(f"   c={c_val:+4d}  k 拟合={params['k_fitted'][str(c_val)]:.4f}  "
                  f"经验公式={contrast_factor(c_val):.4f}")
        if per_config_order:
            print(f"   逐组合最优顺序：{per_config_order}")
    return params


def fit_parametric(records: list, verbose: bool = True) -> dict:
    """拟合完整参数化公式：亮度/对比度前置 + 高度系权重 + 深度量化（其余为固定律）。"""
    params = fit_bc(records, verbose=verbose)
    params["height_weight"] = fit_height_weight(records, params, verbose=verbose)
    params["depth_quant"] = "q = round(depth*255/100)（8bit percentUnit），s = q/255"
    params["version"] = "lut_fit/parametric/v2"
    return params


# ===========================================================================
# 四、1D LUT
# ===========================================================================
def fit_lut(t: np.ndarray, a: np.ndarray) -> np.ndarray:
    """用（输入纹理值 -> 输出 Alpha）配对点拟合 256 点 1D LUT。

    每个输入值取出现像素的中位数（8bit PNG 的取整噪声取中位数比取平均稳），
    训练集没覆盖到的输入值用相邻已知点线性插值补齐。
    """
    table = np.full(256, np.nan, dtype=np.float64)
    for value in range(256):
        sel = a[t == value]
        if sel.size:
            table[value] = float(np.median(sel))
    known = np.flatnonzero(~np.isnan(table))
    if known.size == 0:
        raise RuntimeError("LUT 拟合：训练像素没有任何有效输入值")
    if known.size < 256:
        table = np.interp(np.arange(256), known, table[known])
    return table


def predict_lut(table: np.ndarray, t: np.ndarray) -> np.ndarray:
    return np.rint(table[np.asarray(t, dtype=np.int64)])


# ===========================================================================
# 五、现有经验公式（基线）：src/brush_converter/convert.py 的常量 + Krita 掩码算法
# ===========================================================================
def baseline_krita_params(b: int, c: int):
    """复刻 src/brush_converter/convert.py::_texture_xml 的亮度/对比度经验映射。

    亮度：0.10 - PS亮度/250，夹到 [-1,1]，保留两位小数；
    对比度：C<0 用 1+C/100，C>=0 用 1/(1-C/100)（C>=100 用极大值），夹到 [0,2]，两位小数。
    """
    brightness = 0.10 - b / 250.0
    brightness = max(-1.0, min(1.0, round(brightness, 2)))
    if brightness == 0:
        brightness = 0.0
    if c < 0:
        factor = 1.0 + c / 100.0
    elif c >= 100:
        factor = 1_000_000.0
    else:
        factor = 1.0 / (1.0 - c / 100.0)
    contrast = max(0.0, min(2.0, round(factor, 2)))
    return brightness, contrast


def baseline_predict(t, mode: str, depth: int, b: int, c: int) -> np.ndarray:
    """现有经验公式的最终 Alpha 预测：Krita 掩码算法作用在纹理上，再按 Krita 模式合成。"""
    from tools.texture_mask import compute_mask  # 复用任务 07 的 Krita 复刻

    brightness, contrast = baseline_krita_params(b, c)
    mask_in = (np.asarray(t, dtype=np.float32) / 255.0).astype(np.float32)
    out = compute_mask(mask_in, invert=False, brightness=brightness, contrast=contrast,
                       mode=KRITA_MODE_ID[mode], depth=depth / 100.0)
    return np.floor(out.astype(np.float64) * 255.0 + 0.5)


# ===========================================================================
# 六、误差评估
# ===========================================================================
def metrics(pred: np.ndarray, truth: np.ndarray, tol: int = TOL) -> dict:
    """与 tools/imgdiff.py 同口径：差异点 = |pred-truth| > tol，差异比例分母是全部像素。"""
    delta = np.abs(np.rint(pred).astype(np.int32) - truth.astype(np.int32))
    total = delta.size
    return {
        "pixels": int(total),
        "diff_pixels": int(np.count_nonzero(delta > tol)),
        "diff_ratio": float(np.count_nonzero(delta > tol)) / total,
        "max_delta": int(delta.max()),
        "mae": float(delta.mean()),
    }


def _new_acc() -> dict:
    return {"pixels": 0, "diff_pixels": 0, "max_delta": 0, "abs_sum": 0.0}


def _accumulate(acc: dict, m: dict) -> None:
    acc["pixels"] += m["pixels"]
    acc["diff_pixels"] += m["diff_pixels"]
    acc["max_delta"] = max(acc["max_delta"], m["max_delta"])
    acc["abs_sum"] += m["mae"] * m["pixels"]


def _acc_result(acc: dict) -> dict:
    pixels = max(acc["pixels"], 1)
    return {
        "pixels": acc["pixels"],
        "diff_pixels": acc["diff_pixels"],
        "diff_ratio": acc["diff_pixels"] / pixels,
        "max_delta": acc["max_delta"],
        "mae": acc["abs_sum"] / pixels,
    }


def evaluate(records: list, params: dict, verbose: bool = True) -> dict:
    """三列误差对比：1D LUT / 参数化公式 / 现有经验公式基线。

    拟合只用训练半像素（棋盘格偶数格），误差只在评估半像素（奇数格）上算；
    基线不拟合，同样在评估半像素上算，保证三列比的是同一批像素。
    """
    train, evalmask = pixel_split()
    overall = {"lut": _new_acc(), "param": _new_acc(), "baseline": _new_acc()}
    per_mode = {key: {mode: _new_acc() for mode in MODES} for key in overall}
    per_config = []
    skipped = []
    for rec in records:
        if rec["saturated"] or rec["trivial"]:
            skipped.append(rec["file"])
            continue
        if rec["mode"] not in MODES:
            # 历史人工真值里还有 变暗/线性加深 两个模式，不在本次 4 模式映射范围内，
            # 也不在 ABR->Krita 转换链路上，本任务不拟合，只记录为跳过。
            skipped.append(rec["file"])
            continue
        t = rec["t"].ravel()
        a = rec["a"].ravel().astype(np.int32)
        try:
            table = fit_lut(t[train], a[train])
        except RuntimeError:
            skipped.append(rec["file"])
            continue
        pred = {
            "lut": predict_lut(table, t[evalmask]),
            "param": parametric_predict(t[evalmask], rec["mode"], rec["depth"], rec["b"], rec["c"], params),
            "baseline": baseline_predict(t[evalmask], rec["mode"], rec["depth"], rec["b"], rec["c"]),
        }
        truth = a[evalmask]
        row = {"file": rec["file"], "mode": rec["mode"], "depth": rec["depth"],
               "brightness": rec["b"], "contrast": rec["c"]}
        for key in pred:
            m = metrics(pred[key], truth)
            _accumulate(overall[key], m)
            _accumulate(per_mode[key][rec["mode"]], m)
            row[key] = m
        per_config.append(row)
    result = {
        "samples": len(per_config),
        "skipped": skipped,
        "overall": {key: _acc_result(acc) for key, acc in overall.items()},
        "per_mode": {key: {mode: _acc_result(acc) for mode, acc in per_mode[key].items()}
                     for key in per_mode},
        "per_config": per_config,
        "tol": TOL,
        "ratio_threshold": RATIO_THRESHOLD,
    }
    if verbose:
        print("\n[误差] 三列对比（评估半像素，tol={}，达标线：差异比例<=2%）".format(TOL))
        print("{:<16}{:>20}{:>10}{:>9}{:>12}".format("形态", "差异像素/总像素", "差异比例", "最大差值", "平均绝对差"))
        for key, name in (("lut", "1D LUT"), ("param", "参数化公式"), ("baseline", "现有经验公式")):
            r = result["overall"][key]
            print("{:<16}{:>8}/{:<11}{:>8.2f}%{:>9}{:>12.2f}".format(
                name, r["diff_pixels"], r["pixels"], r["diff_ratio"] * 100, r["max_delta"], r["mae"]))
        print("\n[误差] 分模式差异比例（LUT / 参数化 / 基线）")
        for mode in MODES:
            cells = []
            for key in ("lut", "param", "baseline"):
                r = result["per_mode"][key][mode]
                cells.append(f"{r['diff_ratio']*100:6.2f}% (max {r['max_delta']:>3d})")
            print(f"  {mode:<6} LUT {cells[0]}   参数化 {cells[1]}   基线 {cells[2]}")
    return result


# ===========================================================================
# 七、诊断
# ===========================================================================
def explore(records: list, notes: list) -> None:
    print("[诊断] 数据来源：")
    for note in notes:
        print("   -", note)
    groups = {}
    for rec in records:
        key = (rec["group"], rec["mode"], rec["depth"])
        groups[key] = groups.get(key, 0) + 1
    print(f"[诊断] 配对图共 {len(records)} 张，饱和（alpha_unique==1）{sum(1 for r in records if r['saturated'])} 张")
    for key in sorted(groups, key=str):
        print(f"   {key}: {groups[key]} 张")

    tex = tiled_texture()
    for rec in records:
        if rec["group"] == "auto" and rec["mode"] == "正片叠底" and rec["depth"] == 100 \
                and rec["b"] == 0 and rec["c"] == 0:
            delta = np.abs(rec["a"].astype(np.int32) - tex.astype(np.int32))
            print(f"[诊断] 相位验证 {rec['file']}：与 texture[y%64,x%64] 逐像素一致 "
                  f"{int((delta == 0).sum())}/{delta.size}，最大差 {int(delta.max())}")
            break

    result = mappability(records)
    print(f"[诊断] 可映射性：检查 {result['checked']} 张，最大离散<=1 的 {result['pointwise_le_tol']} 张，"
          f"完全为 0 的 {result['pointwise_exact']} 张，全体最大离散 = {result['max_spread']}")
    print("[诊断] 离散最大的 5 张：", [(Path(f).name, s) for f, s in result["worst"]])

    params = fit_bc(records)
    print("\n[诊断] 亮度/对比度前置实测曲线（自动图 正片叠底 d100 的 输入->输出）")
    for c in sorted({rec["c"] for rec in records}):
        for b in (0, 50, -50, 150, -150):
            rec = next((r for r in records if r["group"] == "auto" and r["mode"] == "正片叠底"
                        and r["depth"] == 100 and r["b"] == b and r["c"] == c), None)
            if rec is None:
                continue
            t = rec["t"].ravel().astype(np.int64)
            a = rec["a"].ravel()
            obs = {int(v): float(np.median(a[t == v])) for v in (0, 32, 128, 224, 255) if np.any(t == v)}
            print(f"   b={b:+4d} c={c:+3d}  " + ", ".join(f"{k}->{v:.0f}" for k, v in obs.items()))


def save_result(params: dict, result: dict, notes: list) -> None:
    payload = {
        "schema": "brushconverter.lut_fit/v1",
        "generated_by": "tools/lut_fit.py --fit",
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "notes": notes,
        "params": params,
        "evaluation": result,
    }
    RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[导出] {RESULT_PATH.relative_to(REPO_ROOT)}（{RESULT_PATH.stat().st_size} 字节）")


def main() -> int:
    parser = argparse.ArgumentParser(description="任务 12 黑盒拟合：扩样本 / 诊断 / 拟合与误差对比")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--probe", action="store_true", help="扩样本（出图 + 追加清单）")
    group.add_argument("--explore", action="store_true", help="诊断：相位/可映射性/实测曲线")
    group.add_argument("--fit", action="store_true", help="拟合并输出三列误差表")
    group.add_argument("--all", action="store_true", help="诊断 + 拟合")
    group.add_argument("--prune", action="store_true", help="删除 Alpha 全 0 的新增图并更新清单")
    parser.add_argument("--limit", type=int, default=None, help="--probe 只跑前 N 个组合")
    parser.add_argument("--offset", type=int, default=0, help="--probe 跳过前 N 个组合")
    parser.add_argument("--timeout", type=int, default=1800, help="--probe 等待 PS 出图的秒数")
    parser.add_argument("--skip-existing", action="store_true", help="--probe 只补缺失组合")
    args = parser.parse_args()

    if args.prune:
        return prune_all_zero()
    if args.probe:
        return probe(limit=args.limit, offset=args.offset, timeout=args.timeout,
                     skip_existing=args.skip_existing)

    records, notes = build_dataset()
    if args.explore:
        explore(records, notes)
        return 0
    if args.fit or args.all:
        if args.all:
            explore(records, notes)
        params = fit_parametric(records)
        result = evaluate(records, params)
        save_result(params, result, notes)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
