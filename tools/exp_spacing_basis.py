#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""任务 131：间距基准实验（Spcn 100% 点 3.53% 残差归因 + 短边补偿去留）。

假设（分开验）
--------------
H1：PS 的间距基准是笔尖最大边，107 的「短边/长边」补偿方向错。
H2：基准没错，残差来自 PS 自身 −3.4% 的少周期量测偏差。

做法
----
3 类笔刷（computed / square / nonsquare）× 3 个间距（10/50/100%）× 2 次重复
= 18 组，每组 PS 与 Krita 各 1 张「长直线」落笔（≥12 个笔迹周期），
用 alpha 投影的「自相关 + 细网格 DFT + 峰检测」自动提取相邻笔迹中心距。

只写 .tmp/131/；不改 src/ tests/ 矩阵与文档；PS/Krita 均串行（锁协议）。

用法
----
    python tools/exp_spacing_basis.py --analyze            # 只重算（读 .tmp/131）
    python tools/exp_spacing_basis.py --render             # 渲染缺失的图后分析
    python tools/exp_spacing_basis.py --brush computed --spacing 100 --render --force
    python tools/exp_spacing_basis.py --legacy             # 顺带复算 106 旧图（640px）

产物：.tmp/131/results.json、.tmp/131/kpp/*.kpp、.tmp/131/{ps,krita}/*.png
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from brush_converter.abr import AbrFile                              # noqa: E402
from brush_converter.mapping import map_presets                       # noqa: E402
from brush_converter.kpp.preset_xml import build_preset_xml           # noqa: E402
from brush_converter.kpp.kpp_writer import build_kpp, render_tip_preview  # noqa: E402
from brush_converter import convert as conv_mod                       # noqa: E402
import krita_render_runner as runner                                  # noqa: E402
import measure_deviation as md                                        # noqa: E402

OUT = REPO / ".tmp" / "131"
PS_DIR = OUT / "ps"
KT_DIR = OUT / "krita"
KPP_DIR = OUT / "kpp"
WORK = OUT / "ps_work"
RESULTS = OUT / "results.json"

# 默认值用占位符（隐私门）；本机用 BRUSHCONVERTER_PS 覆盖。
PS_EXE = Path(os.environ.get("BRUSHCONVERTER_PS",
                             r"<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe"))
ABR_COMPUTED = REPO / "tests" / "定量测试专用.abr"
ABR_SAMPLED = REPO / "tests" / "测试用.abr"
BRUSH_COMPUTED = "定量测试用"
BRUSH_SQUARE = "Kyle 的终极粉彩派对"
BRUSH_NONSQUARE = "KYLE 额外厚实炭笔"

# ------------------------------------------------------------------ 实验配置
#: 画布/笔划长度按「100% 间距下 ≥12 周期」选取（见报告 §3.2）
CONFIGS: dict[str, dict] = {
    "computed": {
        "cls": "计算笔刷（无补白）", "preset": BRUSH_COMPUTED, "abr": ABR_COMPUTED,
        "dmtr": 200.0, "basis": 1.0, "hardness": 100.0, "roundness": 100.0,
        "canvas": (2800, 420), "stroke": 2600,
    },
    "square": {
        "cls": "方形采样笔刷（86×84）", "preset": BRUSH_SQUARE, "abr": ABR_SAMPLED,
        "dmtr": 60.0, "basis": 84.0 / 86.0, "hardness": None, "roundness": None,
        "canvas": (1000, 200), "stroke": 900,
    },
    "nonsquare": {
        "cls": "非方形采样笔刷（183×143）", "preset": BRUSH_NONSQUARE, "abr": ABR_SAMPLED,
        "dmtr": 112.0, "basis": 143.0 / 183.0, "hardness": None, "roundness": None,
        "canvas": (1400, 260), "stroke": 1300,
    },
}
SPACINGS = [10.0, 50.0, 100.0]
GROUPS = list(CONFIGS)
CRITERION_RATIO = 0.02          # 使用者判据：中心距比 ≤2%
FEW_CYCLE_THRESHOLD = 8.0       # < 8 周期标「少周期，仅供参考」
REPEATS = 2

LOCK_PS = REPO / ".tmp" / "ps.lock"
LOCK_KRITA = REPO / ".tmp" / "krita.lock"
LOCK_STALE_S = 3600.0           # 60 分钟（与 lead 的 find -mmin +60 等价）


def log(msg: str) -> None:
    print(msg, flush=True)


def fmt(v: float) -> str:
    return "%g" % float(v)


def expected_step(group: str, spacing: float) -> float:
    """标称中心距 = Dmtr × (短边/长边) × Spcn/100（两侧同口径）。"""
    cfg = CONFIGS[group]
    return cfg["dmtr"] * cfg["basis"] * spacing / 100.0


def _line_spec(canvas, stroke: int) -> dict:
    w, h = canvas
    return {"canvas": [w, h], "stroke": int(stroke), "x0": (w - stroke) // 2, "y0": h // 2,
            "x1": min(w - 1, (w - stroke) // 2 + stroke), "y1": h // 2, "mode": "line"}


# ------------------------------------------------------------------ 锁（与 lead 协议等价）
def acquire_lock(lock: Path, timeout_s: float = 1800.0) -> tuple[bool, float]:
    started = time.time()
    while time.time() - started < timeout_s:
        try:
            lock.mkdir(parents=True)
            return True, time.time() - started
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
                if age > LOCK_STALE_S:
                    shutil.rmtree(lock, ignore_errors=True)
                    continue
            except FileNotFoundError:
                continue
            log("  等锁 %s（已 %.0fs）…" % (lock.name, time.time() - started))
            time.sleep(10)
    return False, time.time() - started


def release_lock(lock: Path) -> None:
    shutil.rmtree(lock, ignore_errors=True)


# ------------------------------------------------------------------ KPP 生成
_BASE_CACHE: dict[str, object] = {}


def base_preset(group: str):
    cfg = CONFIGS[group]
    if group in _BASE_CACHE:
        return _BASE_CACHE[group]
    abr = AbrFile.parse(str(cfg["abr"]))
    presets = map_presets(abr)
    match = [p for p in presets if p.name == cfg["preset"]]
    if not match:
        raise SystemExit("笔刷找不到：%r" % cfg["preset"])
    _BASE_CACHE[group] = match[0]
    return match[0]


def build_kpp_for(group: str, spacing: float) -> tuple[Path, dict]:
    """用项目发行版转换链生成 .kpp（含 107 短边补偿）；隔离动态/纹理/散布。"""
    cfg = CONFIGS[group]
    bp = copy.deepcopy(base_preset(group))
    bp.size_curve = None
    bp.opacity_curve = None
    bp.flow_curve = None
    bp.ratio_curve = None
    bp.rotation_sensor = None
    bp.rotation_jitter = 0.0
    bp.texture = None
    bp.scatter = False
    bp.scatter_pressure = False
    bp.scatter_both_axes = False
    bp.scatter_amount = None
    bp.angle = 0.0
    bp.spacing = float(spacing) / 100.0
    if cfg["hardness"] is not None:
        bp.hardness = float(cfg["hardness"])
    if cfg["roundness"] is not None:
        bp.roundness = float(cfg["roundness"])

    tip_png = None
    tip_name = None
    if bp.is_computed:
        brush_def = conv_mod._computed_brush_def(bp)
        preview = render_tip_preview(
            conv_mod._circle_mask(bp.hardness if bp.hardness is not None else 100.0, bp.roundness))
    else:
        tip_png = conv_mod._png_bytes(conv_mod._square_tip(bp.tip_gray))
        tip_name = "bc131_" + hashlib.sha256(tip_png).hexdigest()[:10]
        brush_def = conv_mod._sampled_brush_def(tip_name, tip_png, bp)
        preview = render_tip_preview(bp.tip_gray)

    preset_name = "bc131 %s v%s" % (group, fmt(spacing))
    xml = build_preset_xml(
        name=preset_name, brush_definition=brush_def, tip_png=tip_png, tip_name=tip_name,
        size_curve=None, opacity_curve=None, flow_curve=None, ratio_curve=None,
        rotation_sensor=None, rotation_jitter=0.0, scatter=False, scatter_pressure=False,
        scatter_both_axes=False, scatter_amount=None, texture=None)
    data = build_kpp(xml, preview)
    KPP_DIR.mkdir(parents=True, exist_ok=True)
    path = KPP_DIR / ("bc131__%s__v%s.kpp" % (group, fmt(spacing)))
    path.write_bytes(data)

    def _grab(pat: str) -> str:
        m = re.search(pat, xml)
        return m.group(1) if m else ""
    readback = {
        "preset_name": preset_name,
        "spacing": _grab(r'spacing="([^"]+)"'),
        "brush_diameter": _grab(r'diameter="([^"]+)"'),
        "ratio": _grab(r'ratio="([^"]+)"'),
        "hfade": _grab(r'hfade="([^"]+)"'),
        "kpp_file": path.name, "xml_bytes": len(xml),
    }
    return path, readback


# ------------------------------------------------------------------ Photoshop 侧
JSX = md.PS_JSX.replace("bc_measure_106_work", "bc_exp131_work")
_OLD_LOAD = '        if (job.abr_path) { app.load(new File(job.abr_path)); L("LOAD ABR OK " + job.abr_path); }'
_NEW_LOAD = ('        if (job.abr_path) {\n'
             '            var _abrList = (job.abr_path instanceof Array) ? job.abr_path : [job.abr_path];\n'
             '            for (var _ai = 0; _ai < _abrList.length; _ai++) {\n'
             '                app.load(new File(_abrList[_ai])); L("LOAD ABR OK " + _abrList[_ai]);\n'
             '            }\n'
             '        }')
assert _OLD_LOAD in JSX, "PS_JSX 结构与预期不同，无法注入多 ABR 加载"
JSX = JSX.replace(_OLD_LOAD, _NEW_LOAD)


def ps_work_dir() -> Path:
    work = Path(tempfile.gettempdir()) / "bc_exp131_work"
    work.mkdir(parents=True, exist_ok=True)
    (Path(tempfile.gettempdir()) / "bc_exp131_work.txt").write_text(str(work), encoding="utf-8")
    return work


def ps_job(group: str, spacing: float, repeat: int) -> dict:
    cfg = CONFIGS[group]
    st = _line_spec(cfg["canvas"], cfg["stroke"])
    fields = {
        "diameter": cfg["hardness"] is not None and cfg["dmtr"] or None,
        "hardness": cfg["hardness"],
        "spacing": float(spacing),
        "roundness": cfg["roundness"],
        "angle": 0.0,
        "scatter": False,
        "jitter": None,
    }
    out = PS_DIR / ("bc131__%s__v%s__r%d.png" % (group, fmt(spacing), repeat))
    return {
        "file": out.name, "out": str(out), "canvas": st["canvas"], "stroke": st,
        "brush_name": cfg["preset"], "abr_path": str(cfg["abr"]), "fields": fields,
    }


def _ps_attempt(jsx_path: Path, done_path: Path, log_path: Path, env: dict,
                timeout: float):
    """一次「启动 PS 并等待 done.tsv」尝试。返回 (ok, proc)。

    PS 的 `-r` 启动进程在「已有 PS 实例」时会把脚本转发给运行中的实例后立刻退出，
    这不算失败；只要 done.tsv 在宽限期（90s）内出现就算成功。启动进程退出且 90s
    内仍无 done.tsv → 本次尝试失败（多半是第二实例被拒/PS 崩溃），交给外层重试。
    """
    for p in (done_path, log_path):
        if p.exists():
            p.unlink()
    proc = subprocess.Popen([str(PS_EXE), "-r", str(jsx_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    started = time.time()
    early_exit_at = None
    while time.time() - started < timeout:
        if done_path.exists() and done_path.stat().st_size > 0:
            time.sleep(1.0)
            return True, proc
        if proc.poll() is not None and early_exit_at is None:
            early_exit_at = time.time()
        if early_exit_at is not None and time.time() - early_exit_at > 90.0:
            return False, proc
        time.sleep(1.0)
    return False, proc


def run_ps_session(abr_paths: list[Path], jobs: list[dict], timeout: float = 900.0,
                    reuse: bool = False) -> dict:
    work = ps_work_dir()
    job_path = work / "job.json"
    done_path = work / "done.tsv"
    log_path = work / "ps.log"
    job = {"quit": False, "abr_path": [str(p) for p in abr_paths], "jobs": jobs}
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    jsx_path = Path(tempfile.gettempdir()) / "bc_exp131.jsx"
    jsx_path.write_text(JSX, encoding="utf-8-sig")
    env = os.environ.copy()
    env["BC_MEASURE_WORK"] = str(work)
    env["BRUSHCONVERTER_REPO"] = str(REPO)
    started = time.time()
    proc = None
    ok = False
    for attempt in range(1, 4):
        log("PS: 会话 abr=%s，%d 张（串行，第 %d 次启动，reuse=%s）" % (
            ",".join(Path(p).name for p in abr_paths), len(jobs), attempt, reuse))
        ok, proc = _ps_attempt(jsx_path, done_path, log_path, env, timeout)
        if ok:
            break
        log("PS: 第 %d 次启动未拿到 done.tsv；2s 后重试（当前 PS 运行中=%s）" % (
            attempt, md.photoshop_running()))
        time.sleep(2.0)
    if not ok:
        raise RuntimeError("Photoshop 三次启动均未写 done.tsv（见 %s）" % log_path)

    rows = {}
    text = done_path.read_text(encoding="utf-8", errors="replace")
    keys = ["file", "ok", "error", "brush", "diameter", "hardness", "spacing",
            "roundness", "angle", "jitter", "useScatter", "useTexture", "useTipDynamics"]
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= len(keys):
            rows[parts[0]] = dict(zip(keys, parts))
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        pass
    WORK.mkdir(parents=True, exist_ok=True)
    tag = "multi" if len(abr_paths) > 1 else Path(abr_paths[0]).stem
    for src in (job_path, done_path, log_path):
        if src.exists():
            (WORK / ("%s_%s" % (tag, src.name))).write_bytes(src.read_bytes())
    alive = proc.poll() is None
    log("PS: 完成 %d/%d 张，用时 %.1fs（启动进程存活=%s）" % (
        sum(1 for r in rows.values() if r.get("ok") == "true"), len(jobs),
        time.time() - started, alive))
    return {"rows": rows, "elapsed_s": round(time.time() - started, 1), "alive": alive}


def phase_ps(todo: list[tuple[str, float, int]], force: bool) -> None:
    jobs_needed = []
    for group, spacing, repeat in todo:
        out = PS_DIR / ("bc131__%s__v%s__r%d.png" % (group, fmt(spacing), repeat))
        if out.exists() and not force:
            log("PS: 已存在，跳过 %s" % out.name)
            continue
        jobs_needed.append((group, spacing, repeat))
    if not jobs_needed:
        return
    PS_DIR.mkdir(parents=True, exist_ok=True)
    acquired, waited = acquire_lock(LOCK_PS)
    if not acquired:
        raise SystemExit("PS 锁 30 分钟未拿到，放弃渲染（已写分析可稍后 --analyze）")
    log("PS 锁已拿到（等待 %.0fs）" % waited)
    try:
        reuse = md.photoshop_running()
        jobs = [ps_job(g, s, r) for (g, s, r) in jobs_needed]
        abrs = sorted({CONFIGS[g]["abr"] for (g, s, r) in jobs_needed})
        info = run_ps_session(abrs, jobs, reuse=reuse)
        # 只有「启动进程仍然存活」才说明这个 PS 实例是本次会话自己掌管的后代，
        # 才允许在批结束时关闭；转发到已有实例时不动别人的 PS。
        if info.get("alive") and md.photoshop_running():
            md.kill_photoshop()
            time.sleep(1.0)
    finally:
        release_lock(LOCK_PS)
        log("PS 锁已释放")


# ------------------------------------------------------------------ Krita 侧
def phase_krita(todo: list[tuple[str, float, int]], force: bool) -> None:
    need = []
    for group, spacing, repeat in todo:
        out = KT_DIR / ("bc131__%s__v%s__r%d.png" % (group, fmt(spacing), repeat))
        if out.exists() and not force:
            log("Krita: 已存在，跳过 %s" % out.name)
            continue
        need.append((group, spacing, repeat))
    if not need:
        return
    KT_DIR.mkdir(parents=True, exist_ok=True)
    acquired, waited = acquire_lock(LOCK_KRITA)
    if not acquired:
        raise SystemExit("Krita 锁 30 分钟未拿到，放弃渲染")
    log("Krita 锁已拿到（等待 %.0fs）" % waited)
    try:
        # Krita 5.3.3 是单实例（QtSingleApplication）：任何残留实例都会把新实例的
        # 启动请求转发走并立刻退出 → 插件不加载、PNG 不写、静默失败（107 已记录）。
        # 拿锁后先等残留自然退出（最多 30s），仍有则强杀（锁在我们手里，属孤儿）。
        for _ in range(10):
            pids = set()
            for name in ("krita.exe", "krita.com"):
                pids |= runner._pids_of(name)
            if not pids:
                break
            log("Krita: 发现残留实例 %r，等待 3s …" % sorted(pids))
            time.sleep(3)
        pids = set()
        for name in ("krita.exe", "krita.com"):
            pids |= runner._pids_of(name)
        if pids:
            runner._kill_pids(sorted(pids))
            log("Krita: 清理残留实例 %r" % sorted(pids))
            time.sleep(3)
        for idx, (group, spacing, repeat) in enumerate(need, 1):
            cfg = CONFIGS[group]
            kpp, _rb = build_kpp_for(group, spacing)
            out = KT_DIR / ("bc131__%s__v%s__r%d.png" % (group, fmt(spacing), repeat))
            w, h = cfg["canvas"]
            log("Krita [%d/%d] %s（%dx%d, stroke=%d）" % (
                idx, len(need), out.name, w, h, cfg["stroke"]))
            res = runner.render_one(str(kpp), str(out), size=(w, h),
                                    stroke=cfg["stroke"], pressure=1.0, timeout=150.0)
            if not res.get("ok"):
                raise RuntimeError("Krita 渲染失败：%s；原因：%s；日志：%s" % (
                    out.name, res.get("reason"), (res.get("log_tail") or "")[-600:]))
            log("Krita [%d/%d] 完成 %.1fs alpha_nonzero=%s" % (
                idx, len(need), res.get("elapsed_s", 0), res.get("alpha_nonzero")))
    finally:
        release_lock(LOCK_KRITA)
        log("Krita 锁已释放")


# ------------------------------------------------------------------ 量测
def load_alpha(path: Path) -> np.ndarray:
    a = np.array(Image.open(path))
    if a.ndim == 3 and a.shape[2] == 4:
        return a[:, :, 3].astype(np.float64)
    if a.ndim == 3:
        return a[:, :, 0].astype(np.float64)
    return a.astype(np.float64)


def bbox_alpha(alpha: np.ndarray, thr: int = 2):
    ys, xs = np.nonzero(alpha > thr)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _movavg(x: np.ndarray, win: int) -> np.ndarray:
    win = max(1, int(win))
    if win <= 1 or win >= len(x):
        return np.full_like(x, float(np.mean(x)))
    return np.convolve(x, np.ones(win) / win, mode="same")


def _detrend(sig: np.ndarray, nominal: float) -> np.ndarray:
    x = np.asarray(sig, dtype=np.float64)
    return x - _movavg(x, max(3, int(round(2.5 * nominal))))


def _dft_step(sig: np.ndarray, nominal: float) -> dict | None:
    """细网格 DFT（Hann 窗）：在 [0.8,1.25]×标称步长内找周期，返回周期与调制幅度。"""
    x = np.asarray(sig, dtype=np.float64)
    x = x - x.mean()
    if not np.any(x):
        return None
    n = len(x)
    q = n // 20
    if n - 2 * q > 16:
        x = x[q:n - q]
    win = np.hanning(len(x))
    w = x * win
    t = np.arange(len(w), dtype=np.float64)
    en = float(np.dot(w, w)) + 1e-12
    freqs = np.linspace(1.0 / (1.25 * nominal), 1.0 / (0.8 * nominal), 600)
    best = None
    for f in freqs:
        c = float(np.dot(w, np.cos(2 * math.pi * f * t)))
        s = float(np.dot(w, np.sin(2 * math.pi * f * t)))
        m = (c * c + s * s) / en
        if best is None or m > best[0]:
            best = (m, f)
    f = best[1]
    c = float(np.dot(w, np.cos(2 * math.pi * f * t)))
    s = float(np.dot(w, np.sin(2 * math.pi * f * t)))
    amp = 2.0 * math.hypot(c, s) / max(1e-9, float(np.sum(win)))
    return {"step": 1.0 / f, "fit": best[0], "amp": amp}


def _acf_step(sig: np.ndarray, nominal: float) -> dict | None:
    x = _detrend(sig, nominal)
    x = x - x.mean()
    n = len(x)
    if n < 8:
        return None
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    ac = ac / (ac[0] + 1e-12)
    lo = max(2, int(round(0.8 * nominal)))
    hi = min(n - 2, int(round(1.25 * nominal)))
    if hi - lo < 3:
        return None
    seg = ac[lo:hi]
    i = int(np.argmax(seg))
    d = 0.0
    if 0 < i < len(seg) - 1:
        y0, y1, y2 = seg[i - 1], seg[i], seg[i + 1]
        den = y0 - 2 * y1 + y2
        if abs(den) > 1e-12:
            d = float(np.clip(0.5 * (y0 - y2) / den, -1.0, 1.0))
    return {"step": float(lo + i + d), "acf": float(seg[i])}


def _peak_step(sig: np.ndarray, nominal: float) -> dict | None:
    s = _movavg(sig, max(3, int(round(0.12 * nominal))))
    n = len(s)
    lo = max(2, int(round(0.5 * nominal)))
    hi = n - max(2, int(round(0.5 * nominal)))
    if hi - lo < 2 * nominal:
        return None
    seg = s[lo:hi]
    med = float(np.median(seg))
    p95 = float(np.percentile(seg, 95))
    if p95 - med <= 1e-9:
        return None
    thr = med + 0.20 * (p95 - med)
    peaks = [lo + i for i in range(1, len(seg) - 1)
             if seg[i] >= seg[i - 1] and seg[i] > seg[i + 1] and seg[i] >= thr]
    merged = []
    for x in peaks:
        if merged and x - merged[-1] < 0.55 * nominal:
            continue
        merged.append(x)
    if len(merged) < 4:
        return None
    diffs = np.diff(merged)
    m = float(np.median(diffs))
    keep = [float(d) for d in diffs if 0.8 * m <= d <= 1.25 * m]
    if len(keep) < 3:
        return None
    return {"step": float(np.median(keep)), "n_peaks": len(merged)}


def estimate_step(alpha: np.ndarray, nominal: float) -> dict:
    """自动量中心距：4 条带 × 3 路（DFT/ACF/峰检测）聚类投票。"""
    bb = bbox_alpha(alpha)
    if not bb:
        return {"step_px": None, "note": "空图"}
    x0, y0, x1, y1 = bb
    h = y1 - y0 + 1
    strips = {
        "mass": (y0, y1),
        "top": (y0, y0 + max(3, int(0.12 * h))),
        "bot": (y1 - max(3, int(0.12 * h)), y1),
        "mid": (y0 + h // 3, y1 - h // 3),
    }
    cands = []
    strip_info = {}
    for name, (ya, yb) in strips.items():
        if yb <= ya:
            continue
        sig = alpha[ya + 1:yb, x0:x1 + 1].sum(axis=0)
        base = float(np.median(sig)) if len(sig) else 0.0
        core = sig[len(sig) // 4:len(sig) - len(sig) // 4] if len(sig) > 8 else sig
        ripple = 0.0 if base <= 0 else float(
            (np.percentile(core, 95) - np.percentile(core, 5)) / base)
        dft = _dft_step(_detrend(sig, nominal), nominal)
        acf = _acf_step(sig, nominal)
        pk = _peak_step(sig, nominal)
        strip_info[name] = {"ripple_pct": round(ripple * 100, 3),
                            "dft_step": round(dft["step"], 3) if dft else None,
                            "dft_amp": round(dft["amp"], 4) if dft else None,
                            "acf_step": round(acf["step"], 3) if acf else None,
                            "acf_r": round(acf["acf"], 4) if acf else None,
                            "peak_step": round(pk["step"], 3) if pk else None,
                            "peak_n": pk["n_peaks"] if pk else None}
        if dft:
            cands.append({"sig": name, "m": "dft", "step": dft["step"],
                          "weight": float(dft["fit"])})
        if acf and acf["acf"] > 0.05:
            cands.append({"sig": name, "m": "acf", "step": acf["step"],
                          "weight": float(acf["acf"]) * 0.5})
        if pk:
            cands.append({"sig": name, "m": "peak", "step": pk["step"], "weight": 0.15,
                          "peak_n": pk["n_peaks"]})

    # 3% 聚类，取权重和最大的一簇；簇内中位数 = 中心距
    clusters: list[dict] = []
    for c in sorted(cands, key=lambda z: z["step"]):
        for cl in clusters:
            if abs(c["step"] - cl["center"]) <= 0.03 * cl["center"]:
                cl["items"].append(c)
                cl["center"] = float(np.median([t["step"] for t in cl["items"]]))
                break
        else:
            clusters.append({"center": c["step"], "items": [c]})
    best = max(clusters, key=lambda cl: sum(t["weight"] for t in cl["items"])) if clusters else None
    out = {"bbox": [x0, y0, x1, y1], "band_h": h, "strips": strip_info,
           "n_candidates": len(cands)}
    # 主口径：细网格 DFT 的亚像素周期（峰检测受像素量化限制：5.86px 的步长
    # 峰间距只能是 6px，误差 2.4%）。取调制幅度 ≥20% 最大值的 DFT 带，
    # 相互 1.5% 内一致时取中位数；不足 2 带才退回聚类投票。
    dft_cands = [c for c in cands if c["m"] == "dft"]
    primary = None
    if dft_cands:
        amax = max(c["weight"] for c in dft_cands)
        dft_sel = [c["step"] for c in dft_cands
                   if c["weight"] >= 0.2 * amax and c["weight"] > 0]
        if len(dft_sel) >= 2:
            med = float(np.median(dft_sel))
            keep = [v for v in dft_sel if abs(v - med) <= 0.015 * med]
            if len(keep) >= 2:
                primary = float(np.median(keep))
                out["dft_consensus"] = {"n": len(keep), "steps": [round(v, 3) for v in keep],
                                        "spread_pct": round(100 * (max(keep) - min(keep)) / primary, 3)}
    if best is None:
        out.update({"step_px": None, "note": "无周期候选"})
        return out
    vals = [t["step"] for t in best["items"]]
    steps_med = primary if primary else float(np.median(vals))
    cycles = (x1 - x0 + 1) / steps_med if steps_med > 0 else 0.0
    peak_ns = [t["peak_n"] for t in best["items"] if "peak_n" in t]
    out.update({
        "step_px": round(steps_med, 2),
        "method": "dft_subpixel" if primary else "cluster",
        "votes": len(vals),
        "vote_methods": [t["m"] + "/" + t["sig"] for t in best["items"]],
        "spread_pct": round(100.0 * (max(vals) - min(vals)) / steps_med, 2),
        "cycles": round(cycles, 1),
        "few_cycles": bool(cycles < FEW_CYCLE_THRESHOLD),
        "peak_count": max(peak_ns) if peak_ns else None,
        "ripple_max_pct": round(max(s["ripple_pct"] for s in strip_info.values()), 3),
    })
    return out


# ------------------------------------------------------------------ 分析
def analyze_one(path: Path, nominal: float, md_diameter: float, spacing: float) -> dict:
    if not path.exists():
        return {"error": "missing", "path": str(path)}
    alpha = load_alpha(path)
    est = estimate_step(alpha, nominal)
    cross = md.measure_step(alpha, md_diameter, spacing)
    out = {"path": str(path.relative_to(REPO)),
           "nominal_step_px": round(nominal, 3),
           "step_px": est.get("step_px"),
           "cycles": est.get("cycles"),
           "few_cycles": est.get("few_cycles"),
           "votes": est.get("votes"), "vote_methods": est.get("vote_methods"),
           "spread_pct": est.get("spread_pct"),
           "peak_count": est.get("peak_count"),
           "ripple_max_pct": est.get("ripple_max_pct"),
           "strips": est.get("strips"),
           "md_measure_step": {k: cross.get(k) for k in
                               ("step_px", "votes", "spread_pct", "note")
                               if k in cross}}
    return out


def analyze_all(legacy: bool) -> dict:
    result = {"meta": {
        "task": "131 间距基准实验",
        "criterion_ratio_pct": CRITERION_RATIO * 100,
        "groups": GROUPS, "spacings": SPACINGS, "repeats": REPEATS,
        "canvas_stroke": {g: {"canvas": CONFIGS[g]["canvas"], "stroke": CONFIGS[g]["stroke"],
                              "dmtr": CONFIGS[g]["dmtr"], "basis": CONFIGS[g]["basis"]}
                          for g in GROUPS},
    }, "cells": {}, "legacy_106": {}}

    for group in GROUPS:
        for spacing in SPACINGS:
            nominal = expected_step(group, spacing)
            cell = {"group": group, "cls": CONFIGS[group]["cls"],
                    "spacing": spacing, "expected_step_px": round(nominal, 3),
                    "ps": [], "krita": []}
            cfg = CONFIGS[group]
            md_diameter = cfg["dmtr"] * cfg["basis"]
            for repeat in range(1, REPEATS + 1):
                cell["ps"].append(analyze_one(
                    PS_DIR / ("bc131__%s__v%s__r%d.png" % (group, fmt(spacing), repeat)),
                    nominal, md_diameter, spacing))
                cell["krita"].append(analyze_one(
                    KT_DIR / ("bc131__%s__v%s__r%d.png" % (group, fmt(spacing), repeat)),
                    nominal, md_diameter, spacing))
            ps_steps = [p["step_px"] for p in cell["ps"] if p.get("step_px")]
            kt_steps = [k["step_px"] for k in cell["krita"] if k.get("step_px")]
            ps_cycles = [p.get("cycles") for p in cell["ps"] if p.get("cycles")]
            kt_cycles = [k.get("cycles") for k in cell["krita"] if k.get("cycles")]
            cell["ps_step_px"] = round(float(np.mean(ps_steps)), 2) if ps_steps else None
            cell["krita_step_px"] = round(float(np.mean(kt_steps)), 2) if kt_steps else None
            cell["ps_spread_px"] = round(max(ps_steps) - min(ps_steps), 3) if len(ps_steps) > 1 else 0.0
            cell["krita_spread_px"] = round(max(kt_steps) - min(kt_steps), 3) if len(kt_steps) > 1 else 0.0
            cell["cycles_ps"] = round(min(ps_cycles), 1) if ps_cycles else None
            cell["cycles_krita"] = round(min(kt_cycles), 1) if kt_cycles else None
            if cell["ps_step_px"] and cell["krita_step_px"]:
                ratio = (cell["krita_step_px"] - cell["ps_step_px"]) / cell["ps_step_px"]
                cell["abs_diff_px"] = round(cell["krita_step_px"] - cell["ps_step_px"], 2)
                cell["ratio_pct"] = round(ratio * 100.0, 2)
                cell["pass"] = bool(abs(ratio) <= CRITERION_RATIO)
                cell["few_cycles"] = bool(
                    (cell["cycles_ps"] or 0) < FEW_CYCLE_THRESHOLD
                    or (cell["cycles_krita"] or 0) < FEW_CYCLE_THRESHOLD)
                cell["d_ps_vs_model_pct"] = round(
                    (cell["ps_step_px"] / nominal - 1.0) * 100.0, 2)
                cell["d_krita_vs_model_pct"] = round(
                    (cell["krita_step_px"] / nominal - 1.0) * 100.0, 2)
                # 若撤回短边补偿：Krita 步长 = Dmtr×Spcn/100（Krita 基准 = 补白后长边）
                uncomp = cfg["dmtr"] * spacing / 100.0
                cell["no_compensation_krita_step_px"] = round(uncomp, 2)
                cell["no_compensation_ratio_pct"] = round(
                    (uncomp - cell["ps_step_px"]) / cell["ps_step_px"] * 100.0, 2)
            else:
                cell["ratio_pct"] = None
                cell["pass"] = False
                cell["note"] = "缺一侧量测"
            result["cells"]["%s v%s" % (group, fmt(spacing))] = cell

    if legacy:
        legacy_base = REPO / "docs" / "数据" / "偏差"
        for group in GROUPS:
            for spacing in SPACINGS:
                nominal = expected_step(group, spacing)
                cfg = CONFIGS[group]
                md_diameter = cfg["dmtr"] * cfg["basis"]
                pair = {}
                for side, sub in (("ps", "ps"), ("krita", "krita")):
                    p = legacy_base / sub / ("spacing__%s__v%s.png" % (group, fmt(spacing)))
                    pair[side] = analyze_one(p, nominal, md_diameter, spacing)
                ps_step = pair["ps"].get("step_px")
                kt_step = pair["krita"].get("step_px")
                item = {"expected_step_px": round(nominal, 3),
                        "ps_step_px": ps_step, "krita_step_px": kt_step,
                        "ps_md_step_px": pair["ps"].get("md_measure_step", {}).get("step_px"),
                        "krita_md_step_px": pair["krita"].get("md_measure_step", {}).get("step_px"),
                        "ps_cycles": pair["ps"].get("cycles"),
                        "krita_cycles": pair["krita"].get("cycles"),
                        "ps_ripple": pair["ps"].get("ripple_max_pct"),
                        "krita_ripple": pair["krita"].get("ripple_max_pct")}
                if ps_step and kt_step:
                    item["ratio_pct"] = round((kt_step - ps_step) / ps_step * 100.0, 2)
                result["legacy_106"]["%s v%s" % (group, fmt(spacing))] = item
    return result


def print_summary(result: dict) -> None:
    log("")
    log("| 类别 | 间距 | 周期数(PS/K) | PS 中心距 | Krita 中心距 | 比值 | 绝对差 | 判定 |")
    log("|---|---|---|---|---|---|---|---|")
    for group in GROUPS:
        for spacing in SPACINGS:
            c = result["cells"]["%s v%s" % (group, fmt(spacing))]
            ratio = "—" if c.get("ratio_pct") is None else "%+.2f%%" % c["ratio_pct"]
            absd = "—" if c.get("abs_diff_px") is None else "%.2f px" % c["abs_diff_px"]
            verdict = "达标" if c.get("pass") else "超阈"
            if c.get("few_cycles"):
                verdict += "（少周期）"
            log("| %s | %g%% | %s/%s | %s | %s | %s | %s | %s |" % (
                c["cls"], spacing,
                c.get("cycles_ps"), c.get("cycles_krita"),
                c.get("ps_step_px"), c.get("krita_step_px"), ratio, absd, verdict))
    log("")


# ------------------------------------------------------------------ 主流程
def parse_list(text: str | None, default: list) -> list:
    if not text:
        return list(default)
    return [t.strip() for t in text.split(",") if t.strip()]


def main(argv: list[str] | None = None) -> int:
    global REPEATS
    ap = argparse.ArgumentParser(description="任务 131：间距基准实验")
    ap.add_argument("--brush", default=None, help="computed,square,nonsquare（缺省全部）")
    ap.add_argument("--spacing", default=None, help="10,50,100（缺省全部）")
    ap.add_argument("--repeat", type=int, default=REPEATS, help="重复次数（缺省 2）")
    ap.add_argument("--render", action="store_true", help="渲染缺失的图（PS/Krita 串行）")
    ap.add_argument("--force", action="store_true", help="强制重渲已有图")
    ap.add_argument("--analyze-only", action="store_true", help="只分析，不渲染")
    ap.add_argument("--legacy", action="store_true", help="顺带复算 106 的 640px 旧图")
    args = ap.parse_args(argv)

    if args.repeat:
        REPEATS = int(args.repeat)
    OUT.mkdir(parents=True, exist_ok=True)

    brushes = parse_list(args.brush, GROUPS)
    bad = [b for b in brushes if b not in CONFIGS]
    if bad:
        raise SystemExit("未知笔刷类：%r（可选 %r）" % (bad, GROUPS))
    spacings = [float(s) for s in parse_list(args.spacing, SPACINGS)]
    todo = [(g, s, r) for g in brushes for s in spacings for r in range(1, REPEATS + 1)]

    if args.render and not args.analyze_only:
        if not PS_EXE.is_file():
            raise SystemExit("Photoshop 不存在：%s" % PS_EXE)
        phase_ps(todo, force=args.force)
        phase_krita(todo, force=args.force)

    result = analyze_all(legacy=args.legacy)
    RESULTS.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print_summary(result)
    log("结果已写：%s" % RESULTS.relative_to(REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
