# -*- coding: utf-8 -*-
"""任务 129：散布分布实验（v0.14 分歧实验 · 统计量定稳 + 映射 A/B）。

回答的问题
----------
PS scatterDynamics.jitter（0..1000%）→ Krita ScatterValue 能否用**确定映射**做到
「单 dab 偏移的均匀分布半宽 a/D 相对差 ≤10%，且三次重复极差 ≤5%」？

为什么不用 band_height
---------------------
106/107 用的 band_height（落笔垂直方向包围盒高度）是**极值统计量**：每条笔划只有
约 10 个 dab，极值的抽样方差极大（历史复现性 ±7–13%）。124/126 已给出定案：
改用「单 dab 横向偏移 dy 的分布型统计量」。本工具按 124 交接单 S1/F-5 实现，
并把**估计量**也一起定稳（见下）。

统计量（三个估计量同时算，报告选哪个、为什么）
--------------------------------------------
设单 dab 的垂直偏移 dy ~ U(−a, a)（a 即「均匀分布半宽」，换算 a/D）：
  * a_range  = (max−min)/2·(n+1)/(n−1)         —— 主估计量（极差充分统计量 + 小样本修正）
  * a_maxabs = max|dy−center|·(n+1)/n          —— 124/F-5 原口径（center=分布中点）
  * a_q95    = (q97.5−q2.5)/2                   —— 交叉校验（抗离群）
  * band_height                                —— 106/107 旧统计量，已判停用，只作对照
理论抽样方差（n 为 dab 数）：a_range ≈ 1.41a/n；a_maxabs ≈ a/n；a_q95 ≈ 0.44a/√n；
band_height 是 ~10 个 dab 的极值，历史复现性 ±7–13%。主估计量用 a_range 的原因：
a_range 与「分布中心是否零点」无关（maxabs 在低散布档会被 0.2–0.5px 中心漂移带偏），
且 n≈600 时相对标准误 ≈0.24%，三次重复极差远低于 5%。

样本设计（F-5）
--------------
* 笔刷：tests/定量测试专用.abr 的「定量测试用」计算笔刷；D=20px、硬度100、圆度100、角度0
* 间距 500%（dab 步长 = 5×D = 100px）→ 每笔 12000px ⇒ 约 120 个 dab；丢每笔首 dab（PS 首 dab 不散布）
* 每配置 10 次落笔（=10 张 PNG，一张一笔），6 个散布值 0/25/50/100/200/300
* 重复：每配置 10 笔分三组（4/3/3）算「三次重复极差」；另对 j=100 再渲一遍做「两次渲染相对差」
* 每侧：PS 6×10=60 张 + 复渲 10 张；Krita 3 候选 × 6×10=180 张 + 复渲 10 张

三个候选映射
------------
  ① div400：s = j/400（现行实现）
  ② div200：s = j/200（124 闭式结论）
  ③ fit   ：两段线性（2 个参数）：s(j) = j·a100/100 (j≤100)；
             s(j) = a100 + (j−100)·(a300−a100)/200 (j>100)，
             其中 a100/a300 = PS 侧实测 a/D 中位数（由 PS 出图先算出来再生成 Krita 预设）

锁协议（127 门结论：Krita 单实例，所有渲染串行）
---------------------------------------------
  Kritra 锁 .tmp/krita.lock / PS 锁 .tmp/ps.lock：mkdir 抢锁，60 分钟视为陈旧可清，
  10s 重试；拿锁后连渲完本批，finally 释放。

用法
----
    # 生成样本（PS 侧 + Krita 侧，串行、自动抢锁）
    python tools/exp_scatter_stat.py --samples --side both
    # 只出 PS（Krita 之前需要 PS 数字来推 fit 候选）
    python tools/exp_scatter_stat.py --samples --side ps
    python tools/exp_scatter_stat.py --samples --side krita --candidates div200,fit
    # 重新计算全部指标（不渲染）
    python tools/exp_scatter_stat.py --metric
    # 全流程
    python tools/exp_scatter_stat.py --all
    # 冒烟（2 个散布值 × 2 笔，双端各一次，用于验证通路；不写进正式统计）
    python tools/exp_scatter_stat.py --all --quick

产物
----
  .tmp/129/png/{ps,krita,krita_r2,ps_r2}/...   渲染图
  .tmp/129/kpp/*.kpp                           本实验生成的 Krita 预设（只读 src 的生成器，不写 docs/）
  .tmp/129/work/                                PS job.json / done.tsv / 日志
  .tmp/129/metrics.json                         全部统计量（可复算）
  .tmp/129/metrics.md                           报告用 markdown 表

只读边界：不改 src/、tests/、tests/baselines/、docs/参数矩阵；不改既有文档；
不 git commit；本工具只写 .tmp/129/。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
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

from brush_converter.abr import AbrFile                        # noqa: E402
from brush_converter.mapping import map_presets                # noqa: E402
from brush_converter.kpp.preset_xml import build_preset_xml    # noqa: E402
from brush_converter.kpp.kpp_writer import build_kpp, render_tip_preview  # noqa: E402
from brush_converter import convert as conv_mod                # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 默认值一律用占位符（隐私门）；本机用 BRUSHCONVERTER_PS / BRUSHCONVERTER_KRITA 覆盖。
PS_EXE = Path(os.environ.get(
    "BRUSHCONVERTER_PS",
    r"<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe",
))
KRITA_BIN = Path(os.environ.get(
    "BRUSHCONVERTER_KRITA",
    r"<LOCAL_APP_DIR>/Krita (x64)/bin/krita.com",
))
KRITA_LOCK = REPO / ".tmp" / "krita.lock"
PS_LOCK = REPO / ".tmp" / "ps.lock"

ABR_COMPUTED = REPO / "tests" / "定量测试专用.abr"
BRUSH_COMPUTED = "定量测试用"

#: 实验几何（PS 与 Krita 完全一致）
DIAMETER = 20.0
HARDNESS = 100.0
ROUNDNESS = 100.0
ANGLE = 0.0
SPACING_PCT = 500.0          # dab 步长 = 5×D = 100px
STROKE_PX = 12000            # 每笔 12000px ⇒ 约 120 个 dab（单笔重复极差 ≤1%）
CANVAS = (12400, 400)        # 单笔一张图，散布余量 ±31.5px（300%）
ROW_Y = 200                  # 笔划中心线（Krita 插件用 height//2，正好 200）
X0 = (CANVAS[0] - STROKE_PX) // 2
X1 = X0 + STROKE_PX

JITTERS = [0.0, 25.0, 50.0, 100.0, 200.0, 300.0]
STROKES_PER_VALUE = 10
REPEAT_GROUPS = [(0, 1, 2, 3), (4, 5, 6), (7, 8, 9)]   # 三次重复（张索引）
RERENDER_JITTER = 100.0                                 # 「两次渲染」复现性检查点

CANDIDATES = ("div400", "div200", "fit")
CAND_LABEL = {
    "div400": "① ÷400（现行）",
    "div200": "② ÷200（124 闭式）",
    "fit": "③ 两段线性拟合（2 参数）",
}

LOCK_TIMEOUT_S = 1800
LOCK_STALE_S = 3600
GUI_TIMEOUT_S = 300.0


def log(msg: str) -> None:
    print(msg, flush=True)


def fmt_int(v: float) -> str:
    return str(int(round(float(v))))


# ---------------------------------------------------------------------------
# 锁（127 门协议：mkdir 单槽锁，60 分钟陈旧清理，10s 重试）
# ---------------------------------------------------------------------------


def acquire_lock(lock: Path, purpose: str, timeout_s: float = LOCK_TIMEOUT_S) -> float:
    started = time.time()
    while time.time() - started < timeout_s:
        try:
            lock.mkdir()
            waited = time.time() - started
            log("[锁] 拿到 %s 锁（%s，等待 %.0fs）" % (lock.name, purpose, waited))
            return waited
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0.0
            if age > LOCK_STALE_S:
                log("[锁] %s 已陈旧 %.0fs，清理后重试" % (lock.name, age))
                shutil.rmtree(lock, ignore_errors=True)
                continue
            time.sleep(10.0)
    raise SystemExit("[锁] 等待 %s 超时（%.0fs，%s）" % (lock.name, timeout_s, purpose))


def release_lock(lock: Path) -> None:
    try:
        lock.rmdir()
        log("[锁] 释放 %s" % lock.name)
    except OSError:
        # 锁目录可能被别的进程塞了内容（pid 文件等）：整体删掉再确认
        shutil.rmtree(lock, ignore_errors=True)
        if lock.exists():
            log("[锁] 释放 %s 失败（目录仍存在）" % lock.name)
        else:
            log("[锁] 释放 %s（rmtree 兜底）" % lock.name)


def _pids_of(image_name: str) -> set[int]:
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq %s" % image_name, "/FO", "CSV", "/NH"],
            capture_output=True, timeout=20,
        )
    except Exception:
        return set()
    pids: set[int] = set()
    text = (proc.stdout or b"").decode("utf-8", "replace")
    for line in text.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == image_name.lower():
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    return pids


def _wait_no_pid(image_name: str, timeout_s: float = 60.0) -> bool:
    started = time.time()
    while time.time() - started < timeout_s:
        if not _pids_of(image_name):
            return True
        time.sleep(2.0)
    return not _pids_of(image_name)


def _kill_pids(pids) -> None:
    for pid in sorted(pids):
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        except Exception:
            pass


def _krita_pids() -> set[int]:
    """Krita 的 GUI 进程在本机是 krita.com（控制台包装）与/或 krita.exe，两个都要算。"""
    return _pids_of("krita.exe") | _pids_of("krita.com")


def _graceful_close_pids(pids, timeout_s: float = 30.0, image_names=("krita.exe", "krita.com")) -> bool:
    """向指定 pid 的**可见顶层窗口**发 WM_CLOSE（等价于用户点关闭），等它自己退出。

    148 现场教训：Krita 可能停在模态对话框（如 'Sessions - Krita' 会话恢复框），
    此时 tasklist 内存/CPU 都正常但批次不前进。手册禁止强杀 Krita，所以清理路径
    必须先走优雅关闭；只有超时才由调用方按「本批自己的 pid」兜底（绝不按镜像名杀）。
    """
    import ctypes
    from ctypes import wintypes

    pids = {int(p) for p in pids}
    if not pids:
        return True
    user32 = ctypes.windll.user32
    hits = [0]
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            visible = bool(user32.IsWindowVisible(hwnd))
        except Exception:
            visible = False
        if pid.value in pids and visible:
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            log("[清理] WM_CLOSE pid=%s 窗口=%r" % (pid.value, title.value))
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
            hits[0] += 1
        return True

    user32.EnumWindows(proc_type(_cb), 0)
    log("[清理] 优雅关闭：%d 个窗口收到 WM_CLOSE（pid=%r）" % (hits[0], sorted(pids)))
    started = time.time()
    while time.time() - started < timeout_s:
        alive = set()
        for name in image_names:
            alive |= (_pids_of(name) & pids)
        if not alive:
            return True
        time.sleep(1.0)
    alive = set()
    for name in image_names:
        alive |= (_pids_of(name) & pids)
    return not alive


def _krita_temp_dir() -> Path:
    return Path(os.environ.get("TEMP") or tempfile.gettempdir())


def _krita_temp_leftovers() -> list[Path]:
    """%TEMP% 里 Krita 的会话/自动保存残留（正是 'Sessions - Krita' 恢复框的成因）。"""
    d = _krita_temp_dir()
    out: list[Path] = []
    try:
        for p in d.iterdir():
            if not p.is_file():
                continue
            name = p.name
            if name.startswith("KRITA_SWAP_FILE_") or (name.startswith(".krita-")
                                                       and name.endswith("-autosave.kra")):
                out.append(p)
    except OSError:
        return []
    return sorted(out)


def archive_krita_temp_sessions(reason: str = "batch") -> list[str]:
    """把 %TEMP% 的 Krita 会话/自动保存残留**移动到** .tmp/krita-autosave-backup/<ts>/（不删除）。

    148 现场：遗留 KRITA_SWAP_FILE_* 让 Krita 启动时弹「Sessions - Krita」恢复对话框，
    批次卡死不前进。调用前必须确认没有 Krita 进程（本函数不负责等进程）。
    """
    files = _krita_temp_leftovers()
    if not files:
        return []
    dest = REPO / ".tmp" / "krita-autosave-backup" / (time.strftime("%Y%m%d-%H%M%S") + "-" + reason)
    dest.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for p in files:
        try:
            shutil.move(str(p), str(dest / p.name))
            moved.append(p.name)
        except Exception as exc:
            log("[会话残留] 移动失败 %s：%r" % (p.name, exc))
    log("[会话残留] 已归档 %d 个 Krita 临时会话文件 → %s" % (len(moved), dest))
    (dest / "moved.tsv").write_text(chr(10).join(moved) + chr(10), encoding="utf-8")
    return moved


# ---------------------------------------------------------------------------
# Krita 预设（.kpp）生成：只读 src/ 的既有生成器，不写 docs/
# ---------------------------------------------------------------------------


_BASE_CACHE: dict[str, object] = {}


def base_preset():
    if "computed" not in _BASE_CACHE:
        abr = AbrFile.parse(str(ABR_COMPUTED))
        presets = map_presets(abr)
        match = [p for p in presets if p.name == BRUSH_COMPUTED]
        if not match:
            raise SystemExit("基准笔刷找不到：%r" % BRUSH_COMPUTED)
        _BASE_CACHE["computed"] = match[0]
    import copy
    return copy.deepcopy(_BASE_CACHE["computed"])


def build_kpp_file(path: Path, scatter_value: float) -> dict:
    """按给定 Krita ScatterValue 生成 .kpp；返回 XML 关键字段回读。"""
    bp = base_preset()
    bp.size_curve = None
    bp.opacity_curve = None
    bp.flow_curve = None
    bp.ratio_curve = None
    bp.rotation_sensor = None
    bp.rotation_jitter = 0.0
    bp.texture = None
    bp.scatter = scatter_value > 0
    bp.scatter_pressure = False
    bp.scatter_both_axes = False
    # build_preset_xml 内部把 scatter_amount/400 写进 ScatterValue，
    # 故反推 scatter_amount = 400×目标 s，得到任意 s（不改 src/，只借它的 XML 生成器）。
    bp.scatter_amount = 400.0 * float(scatter_value)
    bp.diameter = DIAMETER
    bp.hardness = HARDNESS
    bp.roundness = ROUNDNESS
    bp.angle = ANGLE
    bp.spacing = SPACING_PCT / 100.0

    brush_def = conv_mod._computed_brush_def(bp)
    preview = render_tip_preview(conv_mod._circle_mask(bp.hardness, bp.roundness))
    xml = build_preset_xml(
        name="bc129 %s" % path.stem,
        brush_definition=brush_def,
        tip_png=None,
        tip_name=None,
        size_curve=None, opacity_curve=None, flow_curve=None, ratio_curve=None,
        rotation_sensor=None, rotation_jitter=0.0,
        scatter=bp.scatter,
        scatter_pressure=False,
        scatter_both_axes=False,
        scatter_amount=bp.scatter_amount,
        texture=None,
    )
    data = build_kpp(xml, preview)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    def grab(pat: str) -> str:
        import re
        m = re.search(pat, xml)
        return m.group(1) if m else ""

    return {
        "kpp": path.name,
        "ScatterValue": grab(r'name="ScatterValue" type="internal">([^<]*)<'),
        "PressureScatter": grab(r'name="PressureScatter" type="internal">([^<]*)<'),
        "AxisX": grab(r'name="Scattering/AxisX" type="internal">([^<]*)<'),
        "AxisY": grab(r'name="Scattering/AxisY" type="internal">([^<]*)<'),
        "spacing": grab(r'spacing="([^"]+)"'),
        "diameter": grab(r'diameter="([^"]+)"'),
    }


def candidate_value(cand: str, jitter: float, fit_params: dict | None) -> float:
    if cand == "div400":
        return jitter / 400.0
    if cand == "div200":
        return jitter / 200.0
    if cand == "fit":
        if fit_params is None:
            raise SystemExit("fit 候选需要先算 PS 侧统计量（先跑 --samples --side ps / --metric）")
        a100 = float(fit_params["a100"])
        a300 = float(fit_params["a300"])
        if jitter <= 100.0:
            return jitter * a100 / 100.0
        return a100 + (jitter - 100.0) * (a300 - a100) / 200.0
    raise SystemExit("未知候选：%r" % cand)

# ---------------------------------------------------------------------------
# Photoshop 侧：一个会话里跑完所有 job（每张图 = 一次落笔）
# ---------------------------------------------------------------------------

PS_JSX = r"""// 129：散布出图（长笔划、每张一笔、间距 500%）。由 tools/exp_scatter_stat.py 生成。
var WORK = $.getenv("BC129_WORK");
var jobPath = WORK + "/job.json";
var donePath = WORK + "/done.tsv";
function cid(s) { return charIDToTypeID(s); }
function sid(s) { return stringIDToTypeID(s); }
function appDesc() {
    var r = new ActionReference();
    r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
    return executeActionGet(r);
}
function getOpts() { return appDesc().getObjectValue(sid("currentToolOptions")); }
function setOpts(o) {
    var d = new ActionDescriptor();
    var r = new ActionReference();
    r.putClass(sid("paintbrushTool"));
    d.putReference(cid("null"), r);
    d.putObject(cid("T   "), sid("currentToolOptions"), o);
    executeAction(cid("setd"), d, DialogModes.NO);
}
var logFile = new File(WORK + "/ps.log");
logFile.encoding = "UTF-8";
logFile.open("w");
function L(s) { logFile.writeln(String(s)); }
function numOf(o, n) {
    try { return String(o.getObjectValue(sid("brush")).getUnitDoubleValue(sid(n))); }
    catch (e) { return "ABSENT"; }
}
function jitterOf(o) {
    try { return String(o.getObjectValue(sid("scatterDynamics")).getUnitDoubleValue(sid("jitter"))); }
    catch (e) { return "ABSENT"; }
}
function selectBrush(n) {
    var d = new ActionDescriptor();
    var r = new ActionReference();
    r.putName(cid("Brsh"), n);
    d.putReference(cid("null"), r);
    executeAction(cid("slct"), d, DialogModes.NO);
}
var job = null, doc = null, abrLoaded = "", results = [];
try {
    app.displayDialogs = DialogModes.NO;
    var jf = new File(jobPath);
    jf.encoding = "UTF-8";
    jf.open("r");
    job = eval("(" + jf.read() + ")");
    jf.close();
    L("jobs=" + job.jobs.length);
    var d0 = new ActionDescriptor();
    var r0 = new ActionReference();
    r0.putClass(sid("paintbrushTool"));
    d0.putReference(cid("null"), r0);
    executeAction(cid("slct"), d0, DialogModes.NO);
    var col = new SolidColor();
    col.rgb.red = 0; col.rgb.green = 0; col.rgb.blue = 0;
    app.foregroundColor = col;
    for (var i = 0; i < job.jobs.length; i++) {
        var j = job.jobs[i];
        var res = { file: j.file, ok: false, error: "", diameter: "", spacing: "", jitter: "" };
        try {
            if (doc) { try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e1) {} doc = null; }
            doc = app.documents.add(j.canvas[0], j.canvas[1], 72, "bc129_" + i,
                                    NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
            if (abrLoaded !== j.abr_path) { app.load(new File(j.abr_path)); abrLoaded = j.abr_path; L("LOAD " + j.abr_path); }
            selectBrush(j.brush_name);
            var f = j.fields, o = getOpts(), b = o.getObjectValue(sid("brush"));
            b.putUnitDouble(sid("diameter"), cid("#Pxl"), f.diameter);
            b.putUnitDouble(sid("hardness"), cid("#Prc"), f.hardness);
            b.putUnitDouble(sid("spacing"), cid("#Prc"), f.spacing);
            b.putUnitDouble(sid("roundness"), cid("#Prc"), f.roundness);
            b.putUnitDouble(sid("angle"), cid("#Ang"), f.angle);
            o.putObject(sid("brush"), sid("brush"), b);
            o.putBoolean(sid("useTexture"), false);
            o.putBoolean(sid("useTipDynamics"), false);
            o.putBoolean(sid("smoothing"), false);
            o.putInteger(sid("opacity"), 100);
            o.putInteger(sid("flow"), 100);
            o.putBoolean(sid("useScatter"), true);
            o.putBoolean(sid("bothAxes"), !!j.both_axes);
            setOpts(o);
            var o2 = getOpts();
            var sd = o2.getObjectValue(sid("scatterDynamics"));
            sd.putUnitDouble(sid("jitter"), cid("#Prc"), j.jitter);
            o2.putObject(sid("scatterDynamics"), sid("scatterDynamics"), sd);
            setOpts(o2);
            var o4 = getOpts();
            res.diameter = numOf(o4, "diameter");
            res.spacing = numOf(o4, "spacing");
            res.jitter = jitterOf(o4);
            for (var k = 0; k < j.paths.length; k++) {
                var p = j.paths[k];
                var pts = [];
                var p1 = new PathPointInfo();
                p1.kind = PointKind.CORNERPOINT;
                p1.anchor = [p[0], p[1]];
                p1.leftDirection = p1.anchor;
                p1.rightDirection = p1.anchor;
                pts.push(p1);
                var p2 = new PathPointInfo();
                p2.kind = PointKind.CORNERPOINT;
                p2.anchor = [p[2], p[3]];
                p2.leftDirection = p2.anchor;
                p2.rightDirection = p2.anchor;
                pts.push(p2);
                var sub2 = new SubPathInfo();
                sub2.closed = false;
                sub2.operation = ShapeOperation.SHAPEADD;
                sub2.entireSubPath = pts;
                var pi = doc.pathItems.add("d", [sub2]);
                pi.strokePath(ToolType.BRUSH);
                pi.remove();
            }
            var po = new PNGSaveOptions();
            try { po.transparency = true; } catch (eP) {}
            doc.saveAs(new File(j.out), po, true, Extension.LOWERCASE);
            res.ok = true;
            L("OK " + j.file + " d=" + res.diameter + " sp=" + res.spacing + " jit=" + res.jitter);
        } catch (e) {
            res.error = e.message + " line " + e.line;
            L("FAIL " + j.file + " : " + res.error);
        }
        results.push(res);
    }
} catch (e) {
    L("FATAL " + e.message + " line " + e.line);
    results.push({ file: "", ok: false, error: "FATAL " + e.message });
} finally {
    try { if (doc) doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e2) {}
    try {
        var out = new File(donePath);
        out.encoding = "UTF-8";
        out.open("w");
        out.writeln("ps_version\t" + app.version);
        var keys = ["file", "ok", "error", "diameter", "spacing", "jitter"];
        for (var z = 0; z < results.length; z++) {
            var rr = results[z], row = [];
            for (var m = 0; m < keys.length; m++) {
                var val = String(rr[keys[m]] === undefined ? "" : rr[keys[m]]);
                row.push(val.replace(/[\t\r\n]/g, " ").replace(/"/g, "'"));
            }
            out.writeln(row.join("\t"));
        }
        out.close();
    } catch (e3) {}
    logFile.close();
    try { app.quit(); } catch (e4) {}
}
"""


def ps_jobs(rows: list[dict]) -> list[dict]:
    """rows: [{file, out, jitter, both_axes}] → PS job 列表（一张图 = 一次落笔）。"""
    jobs = []
    for r in rows:
        jobs.append({
            "file": r["file"],
            "out": str(r["out"]),
            "canvas": [CANVAS[0], CANVAS[1]],
            "paths": [[X0, ROW_Y, X1, ROW_Y]],
            "brush_name": BRUSH_COMPUTED,
            "abr_path": str(ABR_COMPUTED),
            "fields": {"diameter": DIAMETER, "hardness": HARDNESS,
                       "spacing": SPACING_PCT, "roundness": ROUNDNESS, "angle": ANGLE},
            "both_axes": bool(r.get("both_axes", False)),
            "jitter": float(r["jitter"]),
        })
    return jobs


#: PS 侧 done.tsv 的列（129 散布用；148 角度用更宽的 AN_ROW_KEYS）
PS_ROW_KEYS = ("file", "ok", "error", "diameter", "spacing", "jitter")


def _run_ps_session(jobs: list[dict], work: Path, label: str, jsx_text: str,
                    row_keys: tuple = PS_ROW_KEYS,
                    env_name: str = "BC129_WORK") -> dict:
    """一个 PS 会话跑完 jobs（每张图一个 job）；调用前须已持有 ps.lock。

    129（散布）与 148（角度）共用这一套「起 PS → 等 done.tsv → 解析」流程；
    差别只在 JSX 文本、环境变量名与 done.tsv 列集合。
    """
    work = Path(work).resolve()          # PS 的 cwd 不是仓库：路径一律绝对化
    work.mkdir(parents=True, exist_ok=True)
    # 抢到锁后再确认没有别的 PS 进程（另一代理可能刚释放锁、进程还在退出）
    waited = _wait_no_pid("Photoshop.exe", timeout_s=180.0)
    if not waited:
        raise SystemExit("已有 Photoshop 进程 %r 且 180s 内未退出" % sorted(_pids_of("Photoshop.exe")))
    job_path = work / "job.json"
    jsx_path = work / "run.jsx"
    done_path = work / "done.tsv"
    for p in (done_path, work / "ps.log"):
        if p.exists():
            p.unlink()
    for job in jobs:
        Path(job["out"]).parent.mkdir(parents=True, exist_ok=True)
    job_path.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False, indent=1), encoding="utf-8")
    jsx_path.write_text(jsx_text, encoding="utf-8-sig")   # ExtendScript 需 BOM 才按 UTF-8 解析

    env = os.environ.copy()
    env[env_name] = str(work)
    before = _pids_of("Photoshop.exe")
    started = time.time()
    log("[PS] 开始 %s：%d 张" % (label, len(jobs)))
    proc = subprocess.Popen([str(PS_EXE), "-r", str(jsx_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    time.sleep(1.0)
    launched = _pids_of("Photoshop.exe") - before   # 只清本会话自己起的 PS，不碰别家
    exited_at = None
    try:
        while time.time() - started < 900:
            if done_path.exists() and done_path.stat().st_size > 0:
                time.sleep(1.5)
                break
            if proc.poll() is not None:
                # -r 可能被转发给已在运行的 PS（启动进程立刻退出、脚本在旧实例里跑），
                # 所以进程退出后还要给 done.tsv 最多 120s
                if exited_at is None:
                    exited_at = time.time()
                elif time.time() - exited_at > 120.0:
                    raise SystemExit("[PS] 进程退出 120s 仍无 done.tsv（见 %s）" % (work / "ps.log"))
            time.sleep(1.0)
        else:
            raise SystemExit("[PS] 等待超时 900s")
    finally:
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            pass
        leftover = (launched & _pids_of("Photoshop.exe")) if launched else set()
        if leftover:
            _kill_pids(leftover)
            time.sleep(1.0)
    elapsed = time.time() - started
    text = done_path.read_text(encoding="utf-8", errors="replace")
    rows_out = []
    ps_version = ""
    for line in text.splitlines():
        parts = line.split("\t")
        if parts[0] == "ps_version" and len(parts) > 1:
            ps_version = parts[1]
            continue
        # 容忍列数不足（旧 JSX 版本少几列）：zip 会自动截断
        if len(parts) >= 3:
            rows_out.append(dict(zip(row_keys, parts)))
    ok_n = sum(1 for r in rows_out if r["ok"] == "true")
    log("[PS] 完成 %s：%d/%d 张，%.1fs，PS=%s" % (label, ok_n, len(jobs), elapsed, ps_version))
    (work / ("done_%s.tsv" % label)).write_text(text, encoding="utf-8")
    return {"rows": rows_out, "ok": ok_n, "total": len(jobs),
            "ps_version": ps_version, "elapsed_s": round(elapsed, 1)}


def run_ps(rows: list[dict], work: Path, label: str = "ps") -> dict:
    """129 散布：一个 PS 会话跑完 rows（每张图 = 一次落笔）。调用前须已持有 ps.lock。"""
    return _run_ps_session(ps_jobs(rows), work, label, PS_JSX)

# ---------------------------------------------------------------------------
# Krita 侧：批量启动插件（一次 Krita 会话连渲多张，满足「单实例、串行」）
# ---------------------------------------------------------------------------

PLUGIN_NAME = "bc129_batch"

PLUGIN_SRC = r'''
# -*- coding: utf-8 -*-
# 任务 129 批量渲染插件：由 tools/exp_scatter_stat.py 运行时复制到 pykrita，
# 会话结束后删除、kritarc 还原（不留持久改动）。一次会话渲染多张 PNG。
import json
import os
import time
import traceback

from PyQt5.QtCore import QPoint, QTimer
from krita import Extension, Krita

LOG = os.environ.get("BC129_LOG", "")


def log(msg):
    if not LOG:
        return
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def write_result(payload):
    path = os.environ.get("BC129_PRESULT")
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
    except Exception as exc:
        log("写结果失败 %r" % (exc,))


class BC129Batch(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.tries = 0
        self.document = None
        QTimer.singleShot(1500, self._tick)

    def setup(self):
        pass

    def createActions(self, window):
        pass

    def _tick(self):
        self.tries += 1
        try:
            inst = Krita.instance()
            win = inst.activeWindow()
            if win is None:
                if self.tries < 240:
                    QTimer.singleShot(500, self._tick)
                    return
                write_result({"ok": False, "stage": "window", "reason": "240 次轮询仍无主窗口"})
                self._finish()
                return
            self._run(inst, win)
        except Exception as exc:
            log("异常 %r" % (exc,))
            log(traceback.format_exc())
            write_result({"ok": False, "stage": "exception", "reason": repr(exc)})
        self._finish()

    def _run(self, inst, win):
        with open(os.environ["BC129_JOB"], "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        jobs = cfg["jobs"]
        presets = inst.resources("preset")
        plist = list(presets.values()) if isinstance(presets, dict) else list(presets)
        pmap = {}
        for preset in plist:
            try:
                pmap[os.path.basename(preset.filename() or "")] = preset
            except Exception:
                continue
        log("预置数=%d 需要=%d 任务=%d" % (len(pmap), len(jobs), len(jobs)))
        results = []
        doc = None
        view = None
        layer = None
        for idx, job in enumerate(jobs):
            res = {"file": job.get("file"), "out": job.get("out"), "ok": False, "error": ""}
            started = time.time()
            try:
                preset = pmap.get(job.get("preset_file", ""))
                if preset is None:
                    raise RuntimeError("预设未找到：" + str(job.get("preset_file")))
                if doc is None:
                    doc = inst.createDocument(job["width"], job["height"], "bc129",
                                              "RGBA", "U8", "", 72.0)
                    for node in list(doc.topLevelNodes()):
                        node.remove()
                    view = win.addView(doc)
                    if view is None:
                        raise RuntimeError("addView 返回空")
                    log("文档 %dx%d 已建" % (job["width"], job["height"]))
                if layer is not None:
                    layer.remove()
                layer = doc.createNode("bc_paint", "paintlayer")
                doc.rootNode().addChildNode(layer, None)
                doc.setActiveNode(layer)
                doc.refreshProjection()
                view.setCurrentBrushPreset(preset)
                y = job["height"] // 2
                x0 = (job["width"] - job["stroke"]) // 2
                x1 = min(job["width"] - 1, x0 + job["stroke"])
                layer.paintLine(QPoint(x0, y), QPoint(x1, y), 1.0, 1.0)
                doc.refreshProjection()
                image = doc.projection(0, 0, job["width"], job["height"])
                out_dir = os.path.dirname(job["out"])
                if out_dir and not os.path.isdir(out_dir):
                    os.makedirs(out_dir, exist_ok=True)
                saved = image.save(job["out"], "PNG")
                res["ok"] = bool(saved)
                res["saved"] = bool(saved)
            except Exception as exc:
                res["error"] = repr(exc)
                log("job %d 失败 %r" % (idx, exc))
            res["elapsed_s"] = round(time.time() - started, 3)
            results.append(res)
            write_result({"ok": True, "stage": "running", "done": len(results), "jobs": results})
            log("job %d/%d %s ok=%s %.2fs" % (idx + 1, len(jobs), res.get("file"), res["ok"], res["elapsed_s"]))
        try:
            if doc is not None:
                doc.setModified(False)
                doc.close()
        except Exception as exc:
            log("关文档失败 %r" % (exc,))
        write_result({"ok": True, "stage": "done", "done": len(results), "jobs": results})
        log("全部完成 done=%d" % len(results))

    def _finish(self):
        QTimer.singleShot(1500, lambda: os._exit(0))
        try:
            action = Krita.instance().action("file_quit")
            if action is not None:
                action.trigger()
        except Exception:
            pass


log("插件模块加载，注册 Extension")
try:
    Krita.instance().addExtension(BC129Batch(Krita.instance()))
    log("Extension 已注册")
except Exception as exc:
    log("注册 Extension 异常 %r" % (exc,))
    log(traceback.format_exc())
'''


def _krita_paths() -> dict:
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    return {
        "pykrita": os.path.join(appdata, "krita", "pykrita"),
        "kritarc": os.path.join(localappdata, "kritarc"),
        "resourcecache": os.path.join(appdata, "krita", "resourcecache.sqlite"),
        "presets": os.path.join(appdata, "krita", "paintoppresets"),
    }


def krita_available() -> bool:
    return KRITA_BIN.is_file() and len(PLUGIN_SRC) > 100


def _install_plugin(paths: dict, name: str = PLUGIN_NAME, src: str = PLUGIN_SRC,
                    title: str = "BC129 Batch") -> tuple[str, str]:
    """把批量渲染插件装进 pykrita（name/src/title 可换：129 与 148 各一套）。"""
    plugin_dir = os.path.join(paths["pykrita"], name)
    os.makedirs(plugin_dir, exist_ok=True)
    with open(os.path.join(plugin_dir, "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write(src)
    desktop = os.path.join(paths["pykrita"], name + ".desktop")
    with open(desktop, "w", encoding="utf-8") as fh:
        fh.write("[Desktop Entry]\nType=Service\nServiceTypes=Krita/PythonPlugin\n"
                 "X-KDE-Library=%s\nX-Python-2-Compatible=false\nName=%s\n" % (name, title))
    return plugin_dir, desktop


def _patch_kritarc(path: str, plugin_name: str = PLUGIN_NAME,
                   session_startup: str = "0") -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        original = fh.read()
    patched = original
    if "[python]" in patched:
        patched = patched.replace("[python]\n", "[python]\nenable_%s=true\n" % plugin_name, 1)
    else:
        patched += "\n[python]\nenable_%s=true\n" % plugin_name
    import re
    # 148 现场：2 = 有未保存会话时弹恢复框（%TEMP% 残留就会触发，批次卡死）。
    # 批处理期间一律写 0（不显示会话管理器），会话结束后由备份还原原值。
    patched = re.sub(r"(?m)^sessionOnStartup=\d+$",
                     "sessionOnStartup=" + session_startup, patched)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(patched)
    return original


def _wait_no_krita(timeout_s: float = 180.0) -> None:
    started = time.time()
    while time.time() - started < timeout_s:
        if not _krita_pids():
            return
        time.sleep(1.0)
    raise SystemExit("仍有 Krita 进程在跑（%r）；单实例下不能起第二个，请稍后再跑"
                     % sorted(_krita_pids()))


def run_krita_launch(jobs: list[dict], work: Path, label: str) -> dict:
    """一次 Krita GUI 会话：渲染 jobs（每张一个文档），返回结果。"""
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    job_path = work / ("job_%s.json" % label)
    result_path = work / ("result_%s.json" % label)
    log_path = work / ("krita_%s.log" % label)
    if result_path.exists():
        result_path.unlink()
    if log_path.exists():
        log_path.unlink()
    job_path.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False, indent=1), encoding="utf-8")

    _wait_no_krita(timeout_s=120.0)
    before = _krita_pids()
    env = os.environ.copy()
    env["BC129_JOB"] = str(job_path)
    env["BC129_PRESULT"] = str(result_path)
    env["BC129_LOG"] = str(log_path)
    started = time.time()
    log("[Krita] 启动 %s：%d 张" % (label, len(jobs)))
    proc = subprocess.Popen([str(KRITA_BIN), "--nosplash"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    timed_out = False
    try:
        while time.time() - started < GUI_TIMEOUT_S:
            if result_path.exists():
                try:
                    payload = json.loads(result_path.read_text(encoding="utf-8"))
                except Exception:
                    payload = None
                if payload and payload.get("stage") in ("done", "window", "exception"):
                    time.sleep(1.0)
                    break
            if proc.poll() is not None:
                # 进程已退出：给结果文件 1.5s 落盘，然后无论 done/running 都结束等待（下面统一判定）
                time.sleep(1.5)
                if result_path.exists():
                    break
                raise SystemExit("[Krita] %s 进程提前退出且无结果（日志 %s）" % (label, log_path))
            time.sleep(1.0)
        else:
            timed_out = True
    finally:
        try:
            proc.wait(timeout=90)
        except subprocess.TimeoutExpired:
            timed_out = True
        extra = _krita_pids() - before
        if extra:
            # 148 教训：先 WM_CLOSE 优雅关，超时才按本批 pid 兜底
            if not _graceful_close_pids(extra, timeout_s=45.0):
                log("[Krita] 优雅关闭超时，兜底结束本批 pid=%r" % (sorted(_krita_pids() & extra),))
                _kill_pids(_krita_pids() & extra)
            time.sleep(1.0)
    elapsed = time.time() - started
    payload = {}
    if result_path.exists():
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    jobs_res = payload.get("jobs") or []
    ok_n = sum(1 for r in jobs_res if r.get("ok"))
    log("[Krita] 完成 %s：%d/%d 张，%.1fs（超时=%s）" % (label, ok_n, len(jobs), elapsed, timed_out))
    if not payload.get("ok"):
        tail = ""
        if log_path.exists():
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
        raise SystemExit("[Krita] %s 会话失败：%r；日志尾部：\n%s" % (label, payload.get("reason"), tail))
    return {"jobs": jobs_res, "ok": ok_n, "total": len(jobs),
            "elapsed_s": round(elapsed, 1), "timeout": timed_out, "payload": payload}


def _wait_no_krita_soft(timeout_s: float = 90.0) -> bool:
    started = time.time()
    while time.time() - started < timeout_s:
        if not _krita_pids():
            return True
        time.sleep(0.5)
    return not _krita_pids()


def _kritarc_hygiene(path: str, set_session_startup: str = "0") -> list[str]:
    """清理 kritarc 里跨代理的 enable_bc* 行，并把 sessionOnStartup 设为 0（lead 裁决口径）。

    返回被删掉的 enable_* 名，便于报告登记「清理了谁的残留」。
    """
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    out: list[str] = []
    removed: list[str] = []
    has_key = False
    for line in lines:
        s = line.strip()
        if s.startswith("enable_bc") and s.endswith("=true"):
            removed.append(s.split("=")[0])
            continue
        if s.startswith("sessionOnStartup="):
            out.append("sessionOnStartup=%s" % set_session_startup)
            has_key = True
            continue
        out.append(line)
    if not has_key:
        out.append("sessionOnStartup=%s" % set_session_startup)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out) + "\n")
    log("[kritarc] 清理 enable_bc*=%r；sessionOnStartup=>%s" % (removed, set_session_startup))
    return removed


def _remove_foreign_plugins(paths: dict) -> list[str]:
    """删除 pykrita 下不属于本批次的 bc_* 插件目录/desktop（调用前必须确认没有 Krita 进程）。"""
    removed: list[str] = []
    base = paths["pykrita"]
    if not os.path.isdir(base):
        return removed
    for name in os.listdir(base):
        low = name.lower()
        if low.startswith("bc") and ("auto_render" in low or low.startswith("bc129")
                                     or low.startswith("bc148") or low.startswith("bc_")):
            p = os.path.join(base, name)
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                removed.append(name)
            except Exception:
                pass
    return removed


def _kritarc_verify(paths: dict) -> dict:
    out = {"enable_lines": [], "sessionOnStartup": [], "pykrita": []}
    p = paths["kritarc"]
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh.read().splitlines():
                s = line.strip()
                if s.startswith("enable_bc"):
                    out["enable_lines"].append(s)
                if s.startswith("sessionOnStartup="):
                    out["sessionOnStartup"].append(s)
    try:
        out["pykrita"] = sorted(os.listdir(paths["pykrita"]))
    except Exception:
        out["pykrita"] = []
    log("[核对] kritarc enable_bc*=%r sessionOnStartup=%r" % (
        out["enable_lines"], out["sessionOnStartup"]))
    log("[核对] pykrita=%r" % (out["pykrita"],))
    return out


def with_krita_session(fn, plugin_name: str = PLUGIN_NAME, plugin_src: str = PLUGIN_SRC,
                       plugin_title: str = "BC129 Batch"):
    """Krita 单实例会话：抢到锁后 → 等无缝 Krita → 清别家残留 → 装本批插件 → 一次启动批量渲完
    → 先杀本批 Krita 并等完全退出 → 再还原 kritarc/resourcecache → 核对输出。

    148 角度实验传入自己的一套插件（plugin_name/plugin_src）复用同一套清理与还原逻辑；
    默认参数保持 129 行为不变。
    """
    paths = _krita_paths()
    if not krita_available():
        raise SystemExit("Krita GUI 路线不可用：%s 或插件模板缺失" % KRITA_BIN)
    _wait_no_krita()
    _wait_no_krita(30.0)
    plugins_removed = _remove_foreign_plugins(paths)
    if plugins_removed:
        log("[清理] 删除别家 pykrita 残留：%r" % (plugins_removed,))
    _kritarc_hygiene(paths["kritarc"], set_session_startup="0")
    backups: dict[str, str] = {}
    tmpdirs: list[str] = []
    plugin_dir = desktop = None
    installed: list[str] = []
    for key in ("kritarc", "resourcecache"):
        src = paths[key]
        if os.path.isfile(src):
            d = tempfile.mkdtemp(prefix="bc129_bak_")
            tmpdirs.append(d)
            dst = os.path.join(d, os.path.basename(src))
            shutil.copy2(src, dst)
            backups[src] = dst
    try:
        if os.path.isfile(paths["kritarc"]):
            _patch_kritarc(paths["kritarc"], plugin_name)
            # 起 Krita 前归档 %TEMP% 的会话/自动保存残留（只移动不删除）：
            # 否则 Krita 可能弹 'Sessions - Krita' 恢复框把批次卡死（148 现场）。
            archive_krita_temp_sessions("pre-batch")
        plugin_dir, desktop = _install_plugin(paths, plugin_name, plugin_src, plugin_title)
        os.makedirs(paths["presets"], exist_ok=True)
        result = fn(paths, installed)
        verify = _kritarc_verify(paths)
        return {"result": result, "verify": verify, "plugins_removed": plugins_removed}
    finally:
        # 1) 先杀本批残留 Krita，并等进程真正退出（Krita 退出时会回写 kritarc）
        extra = _krita_pids()
        if extra:
            # 148 教训：先优雅关（WM_CLOSE），超时才按本批 pid 兜底；绝不按镜像名杀
            if _graceful_close_pids(extra, timeout_s=45.0):
                log("[清理] 本批 Krita 已优雅退出 pid=%r" % (sorted(extra),))
            else:
                left = _krita_pids() & extra
                log("[清理] 优雅关闭超时，兜底结束本批 Krita pid=%r" % (sorted(left),))
                _kill_pids(left)
        if not _wait_no_krita_soft(90.0):
            log("[清理] 警告：90s 内仍有 Krita 进程 %r" % (sorted(_krita_pids()),))
        archive_krita_temp_sessions("post-batch")
        # 2) 删本批预设与插件
        for name in installed:
            try:
                os.remove(os.path.join(paths["presets"], name))
            except Exception:
                pass
        if desktop and os.path.isfile(desktop):
            try:
                os.remove(desktop)
            except Exception:
                pass
        if plugin_dir:
            shutil.rmtree(plugin_dir, ignore_errors=True)
        # 3) 还原 kritarc/resourcecache，再做一次卫生强制（Krita 退出可能写回 enable/2）
        for src, dst in backups.items():
            try:
                shutil.copy2(dst, src)
            except Exception:
                pass
        _kritarc_hygiene(paths["kritarc"], set_session_startup="0")
        for d in tmpdirs:
            shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------------------
# 图像分析：逐 dab 提取偏移 + 分布型统计量
# ---------------------------------------------------------------------------


def load_alpha(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"))[:, :, 3].astype(np.float64)


def extract_image(path: Path) -> dict:
    """一张图（一笔）→ 逐 dab 垂直偏移 dy 与带高（band_height，对照用）。"""
    alpha = load_alpha(path)
    h, w = alpha.shape
    y0 = max(0, ROW_Y - 120)
    y1 = min(h, ROW_Y + 120)
    band = alpha[y0:y1, :]
    colsum = band.sum(axis=0)
    on = colsum > 1.0
    runs: list[tuple[int, int]] = []
    i = 0
    while i < w:
        if not on[i]:
            i += 1
            continue
        k = i
        while k < w and on[k]:
            k += 1
        if k - i >= 4:
            runs.append((i, k - 1))
        i = k
    ys = np.arange(y0, y1, dtype=np.float64)[:, None]
    dabs: list[tuple[float, float, float]] = []
    for (a0, a1) in runs:
        seg = band[:, a0:a1 + 1]
        s = float(seg.sum())
        if s < 50.0:
            continue
        xs = np.arange(a0, a1 + 1, dtype=np.float64)[None, :]
        cy = float((seg * ys).sum() / s)
        cx = float((seg * xs).sum() / s)
        dabs.append((cx, cy, s))
    dabs.sort(key=lambda t: t[0])
    first_skipped = 0
    if dabs and abs(dabs[0][0] - X0) < 40.0:
        dabs = dabs[1:]
        first_skipped = 1
    dy = [cy - ROW_Y for (_cx, cy, _s) in dabs]
    # band_height（107 旧统计量，仅作对照）：笔划区 Alpha>2 的行跨度
    lo, hi = int(X0 + 40), int(X1 - 10)
    sub = alpha[:, lo:hi]
    rows_any = np.where((sub > 2.0).any(axis=1))[0]
    band_height = int(rows_any[-1] - rows_any[0] + 1) if rows_any.size else 0
    return {"file": path.name, "n_dabs": len(dy), "dy": dy,
            "band_height": band_height, "first_dab_skipped": first_skipped}


def uniform_stats(dy: list[float] | np.ndarray) -> dict:
    """均匀分布半宽 a 的估计（各自换算 a/D）。

    主估计量 a_range = (max−min)/2 的 (n+1)/(n−1) 无偏修正：
    均匀分布 [-a,a] 的极差是充分统计量，抽样方差 ≈ 2a²/((n−1)(n+2))，
    比「已知中心的最大绝对偏差」更抗中心漂移（PS/Krita 渲染可能有 0.2–0.5px 系统偏移，
    低散布档 a 只有 2.5px 时单侧 max 估计会被中心漂移带偏）。
    """
    arr = np.asarray(dy, dtype=np.float64)
    n = int(arr.size)
    if n == 0:
        return {"n": 0, "a_range_D": 0.0, "a_maxabs_D": 0.0, "a_q95_D": 0.0,
                "a_mean_D": 0.0, "kurtosis": None, "max_abs_px": 0.0,
                "mean_abs_px": 0.0, "center_px": 0.0, "std_px": 0.0,
                "min_px": 0.0, "max_px": 0.0}
    mn = float(arr.min())
    mx = float(arr.max())
    center = (mn + mx) / 2.0
    a_range = (mx - mn) / 2.0 * ((n + 1) / (n - 1)) if n > 1 else 0.0
    a_maxabs = float(np.abs(arr - center).max()) * (n + 1) / n
    # 均匀分布 [-a,a] 的 95% 质量区间宽 = 2a×0.95 ⇒ 除 0.95 才是半宽
    a_q95 = (float(np.quantile(arr, 0.975)) - float(np.quantile(arr, 0.025))) / 2.0 / 0.95
    a_mean = 2.0 * float(np.abs(arr - center).mean())
    sd = float(arr.std(ddof=1)) if n > 1 else 0.0
    kurt = float((((arr - arr.mean()) / sd) ** 4).mean()) if sd > 0 else None
    return {
        "n": n,
        "a_range_D": a_range / DIAMETER,
        "a_maxabs_D": a_maxabs / DIAMETER,
        "a_q95_D": a_q95 / DIAMETER,
        "a_mean_D": a_mean / DIAMETER,
        "a_range_px": a_range,
        "a_maxabs_px": a_maxabs,
        "a_q95_px": a_q95,
        "a_mean_px": a_mean,
        "center_px": center,
        "std_px": sd,
        "max_abs_px": float(np.abs(arr - center).max()),
        "min_px": mn,
        "max_px": mx,
        "kurtosis": kurt,
    }


def analyze_config(dir_path: Path, jitter: float, images: list[str],
                   group_slices: list[tuple[int, ...]]) -> dict:
    """一个配置（一个散布值、一侧）的全部统计量。"""
    per_image = []
    for name in images:
        path = dir_path / name
        if not path.is_file():
            per_image.append({"file": name, "missing": True})
            continue
        per_image.append(extract_image(path))
    dy_all: list[float] = []
    for item in per_image:
        if "dy" in item:
            dy_all.extend(item["dy"])
    stats = uniform_stats(dy_all)
    bhs = [it["band_height"] for it in per_image if it.get("band_height")]
    stats["jitter"] = jitter
    stats["n_images"] = len(per_image)
    stats["n_images_ok"] = sum(1 for it in per_image if not it.get("missing"))
    stats["band_height_median"] = float(np.median(bhs)) if bhs else 0.0
    stats["images"] = [{k: v for k, v in it.items() if k != "dy"} for it in per_image]
    # 三次重复：按张索引分组池化
    repeats = {}
    for gi, idxs in enumerate(group_slices):
        dy_g: list[float] = []
        for ix in idxs:
            if ix < len(per_image) and "dy" in per_image[ix]:
                dy_g.extend(per_image[ix]["dy"])
        sg = uniform_stats(dy_g)
        bh_g = [per_image[ix]["band_height"] for ix in idxs
                if ix < len(per_image) and per_image[ix].get("band_height")]
        sg["band_height_median"] = float(np.median(bh_g)) if bh_g else 0.0
        repeats["g%d" % (gi + 1)] = sg
    stats["repeats"] = repeats
    # 三次重复极差（相对中位数）
    def _range(key: str) -> float | None:
        vals = [repeats["g%d" % (i + 1)].get(key) for i in range(len(repeats))]
        vals = [float(v) for v in vals if v is not None]
        if len(vals) < 2:
            return None
        mid = float(np.median(vals))
        if mid < 0.01:          # j=0 的 a/D 在噪声地板上（~0），极差没有相对意义
            return None
        return (max(vals) - min(vals)) / mid * 100.0
    stats["repeat_range_pct"] = {
        "a_range_D": _range("a_range_D"),
        "a_maxabs_D": _range("a_maxabs_D"),
        "a_mean_D": _range("a_mean_D"),
        "band_height": _range("band_height_median"),
    }
    # 逐笔散布（笔间离散度，作为另一种稳定性口径）
    per_stroke = [uniform_stats(it["dy"])["a_range_D"] for it in per_image if "dy" in it]
    stats["per_stroke_a_range_D"] = per_stroke
    return stats


def analyze_candidate(work: Path, cand: str, jitters: list[float],
                      images_per_value: int, subdir: str | None = None) -> dict:
    out = {}
    dir_path = work / "png" / "krita" / (subdir or cand)
    for j in jitters:
        names = ["j%s_%02d.png" % (fmt_int(j), i) for i in range(images_per_value)]
        out[fmt_int(j)] = analyze_config(dir_path, j, names,
                                         group_slices_for(images_per_value))
    return out


def group_slices_for(n: int) -> list[tuple[int, ...]]:
    """把 n 张按 4/3/3 分组的通用版本（n<10 时按 2/1/1 等分）。"""
    if n >= 10:
        return [(0, 1, 2, 3), (4, 5, 6), (7, 8, 9)]
    if n >= 3:
        third = max(1, n // 3)
        return [tuple(range(0, third)), tuple(range(third, 2 * third)),
                tuple(range(2 * third, n))]
    return [tuple(range(n))]


GROUP_SLICES = [(0, 1, 2, 3), (4, 5, 6), (7, 8, 9)]

# ---------------------------------------------------------------------------
# 渲染编排
# ---------------------------------------------------------------------------


def kpp_name(cand: str, jitter: float) -> str:
    return "bc129_%s_j%s.kpp" % (cand, fmt_int(jitter))


def ps_configs(work: Path, jitters: list[float], strokes: int) -> dict:
    out = {}
    for j in jitters:
        names = ["j%s_%02d.png" % (fmt_int(j), i) for i in range(strokes)]
        out[fmt_int(j)] = analyze_config(work / "png" / "ps", j, names,
                                         group_slices_for(strokes))
    return out


def fit_params_from_ps(ps: dict) -> dict:
    a100 = float(ps["100"]["a_range_D"])
    a300 = float(ps["300"]["a_range_D"])
    return {"a100": a100, "a300": a300, "params": 2}


def render_ps_side(work: Path, jitters: list[float], strokes: int,
                   force: bool, max_jobs: int = 0) -> dict:
    main_rows, r2_rows = [], []
    for j in jitters:
        for i in range(strokes):
            out = work / "png" / "ps" / ("j%s_%02d.png" % (fmt_int(j), i))
            main_rows.append({"file": out.name, "out": out, "jitter": j})
    if RERENDER_JITTER in jitters:
        for i in range(strokes):
            out = work / "png" / "ps_r2" / ("j%s_%02d.png" % (fmt_int(RERENDER_JITTER), i))
            r2_rows.append({"file": out.name, "out": out, "jitter": RERENDER_JITTER})
    if not force:
        main_rows = [r for r in main_rows if not r["out"].is_file()]
        r2_rows = [r for r in r2_rows if not r["out"].is_file()]
    if max_jobs > 0:
        all_rows = (main_rows + r2_rows)[:max_jobs]
        main_rows = [r for r in all_rows if "ps_r2" not in str(r["out"])]
        r2_rows = [r for r in all_rows if "ps_r2" in str(r["out"])]
    started = time.time()
    acquired = acquire_lock(PS_LOCK, "PS 出图")
    try:
        out = {"wait_s": round(acquired, 1), "sessions": {}}
        # 一个 PS 会话跑完 main + r2（70 张）：避免二次启动/转发失败
        all_rows = main_rows + r2_rows
        if all_rows:
            work.mkdir(parents=True, exist_ok=True)
            out["sessions"]["all"] = run_ps(all_rows, work / "work" / "ps_main", "all")
    finally:
        release_lock(PS_LOCK)
    out["elapsed_s"] = round(time.time() - started, 1)
    return out


def render_krita_side(work: Path, jitters: list[float], strokes: int,
                      candidates: list[str], fit_params: dict | None,
                      force: bool) -> dict:
    plan: dict[str, dict[str, Path]] = {}
    for cand in candidates:
        plan[cand] = {}
        for j in jitters:
            s = candidate_value(cand, j, fit_params)
            path = work / "kpp" / kpp_name(cand, j)
            if force or not path.is_file():
                build_kpp_file(path, s)
            plan[cand][j] = path

    jobs_by_cand: dict[str, list[dict]] = {}
    for cand in candidates:
        jobs = []
        for j in jitters:
            for i in range(strokes):
                out = work / "png" / "krita" / cand / ("j%s_%02d.png" % (fmt_int(j), i))
                jobs.append({"file": out.name, "out": str(out),
                             "preset_file": plan[cand][j].name,
                             "width": CANVAS[0], "height": CANVAS[1], "stroke": STROKE_PX})
        if not force:
            jobs = [job for job in jobs if not Path(job["out"]).is_file()]
        for job in jobs:
            Path(job["out"]).parent.mkdir(parents=True, exist_ok=True)
        jobs_by_cand[cand] = jobs
    r2_jobs = []
    if RERENDER_JITTER in jitters and "div200" in candidates:
        for i in range(strokes):
            out = work / "png" / "krita_r2" / "div200" / ("j%s_%02d.png" % (fmt_int(RERENDER_JITTER), i))
            r2_jobs.append({"file": out.name, "out": str(out),
                            "preset_file": plan["div200"][RERENDER_JITTER].name,
                            "width": CANVAS[0], "height": CANVAS[1], "stroke": STROKE_PX})
        for job in r2_jobs:
            Path(job["out"]).parent.mkdir(parents=True, exist_ok=True)
        if not force:
            r2_jobs = [job for job in r2_jobs if not Path(job["out"]).is_file()]

    started = time.time()
    acquired = acquire_lock(KRITA_LOCK, "Krita 串行渲染")
    launches: dict[str, dict] = {}

    def _run(paths: dict, installed: list[str]) -> dict:
        for cand in candidates:
            for j in jitters:
                name = plan[cand][j].name
                src = str(plan[cand][j])
                dst = os.path.join(paths["presets"], name)
                if not os.path.isfile(dst):
                    shutil.copyfile(src, dst)
                    installed.append(name)
        # lead 裁决：一次 Krita 启动、批量渲完、中途不重启（190 张 ≈ 1–2 分钟）
        all_jobs: list[dict] = []
        for cand in candidates:
            all_jobs.extend(jobs_by_cand[cand])
        all_jobs.extend(r2_jobs)
        result = {}
        if all_jobs:
            result["all"] = run_krita_launch(all_jobs, work / "work" / "krita_all", "all")
        return result

    try:
        payload = with_krita_session(_run)
        launches = {"launches": payload["result"], "verify": payload["verify"],
                    "plugins_removed": payload["plugins_removed"]}
    finally:
        release_lock(KRITA_LOCK)
    return {"wait_s": round(acquired, 1), "launches": launches,
            "elapsed_s": round(time.time() - started, 1)}


# ---------------------------------------------------------------------------
# 指标汇总 + markdown
# ---------------------------------------------------------------------------


def _rel(a: float, b: float) -> float | None:
    if a is None or b is None:
        return None
    if a <= 0:
        return 0.0 if b <= 0 else None
    return (b - a) / a * 100.0


def compare_candidates(ps: dict, krita: dict, candidates: list[str],
                       jitters: list[float]) -> dict:
    out = {}
    for cand in candidates:
        if cand not in krita:
            continue
        rows = {}
        for j in jitters:
            key = fmt_int(j)
            p = ps[key]["a_range_D"]
            k = krita[cand][key]["a_range_D"]
            rel = _rel(p, k)
            if j == 0.0:
                ok = (p <= 0.05 and k <= 0.05)
                rel = None
            else:
                ok = rel is not None and abs(rel) <= 10.0
            rows[key] = {"ps_a_range_D": p, "kt_a_range_D": k,
                         "rel_pct": rel, "pass": ok,
                         "ps_repeat_range": ps[key]["repeat_range_pct"]["a_range_D"],
                         "kt_repeat_range": krita[cand][key]["repeat_range_pct"]["a_range_D"]}
        rels = [abs(r["rel_pct"]) for r in rows.values() if r["rel_pct"] is not None]
        exceed = sum(1 for kk, r in rows.items() if kk != "0" and not r["pass"])
        kt_reps = [r["kt_repeat_range"] for r in rows.values() if r["kt_repeat_range"] is not None]
        out[cand] = {
            "rows": rows,
            "max_abs_rel_pct": max(rels) if rels else None,
            "exceed_count": exceed,
            "nonzero_points": len(rels),
            "max_kt_repeat_range_pct": max(kt_reps) if kt_reps else None,
        }
    return out


def metrics_markdown(m: dict) -> str:
    ps = m["ps"]
    candidates = m["candidates"]
    jitters = m["jitters"]
    lines: list[str] = []
    add = lines.append
    add("## 表 A（主表，统计量 = 单 dab 均匀半宽 a/D，估计量 = (max−min)/2 均匀 MLE）\n")
    add("基准候选：② ÷200（124 闭式结论，本实验复核）。PS 侧为真值参照。\n")
    add("| 散布 j | PS n | PS a/D | Krita n | Krita a/D | 相对差 | 判定(≤10%) | PS 重复极差 | Krita 重复极差 |")
    add("|---:|---:|---:|---:|---:|---:|---|---:|---:|")
    prim = "div200" if "div200" in m["krita"] else candidates[0]
    for j in jitters:
        key = fmt_int(j)
        p = ps[key]
        k = m["krita"][prim][key]
        rel = _rel(p["a_range_D"], k["a_range_D"])
        if j == 0.0:
            judge = "噪声地板 pass" if (p["a_range_D"] <= 0.05 and k["a_range_D"] <= 0.05) else "FAIL"
            rel_s = "—"
        else:
            judge = "PASS" if rel is not None and abs(rel) <= 10.0 else "FAIL"
            rel_s = "%+.2f%%" % rel if rel is not None else "—"
        rr_p = p["repeat_range_pct"]["a_range_D"]
        rr_k = k["repeat_range_pct"]["a_range_D"]
        add("| %d | %d | %.4f | %d | %.4f | %s | %s | %s | %s |" % (
            int(j), p["n"], p["a_range_D"], k["n"], k["a_range_D"], rel_s, judge,
            ("%.2f%%" % rr_p) if rr_p is not None else "—",
            ("%.2f%%" % rr_k) if rr_k is not None else "—"))
    add("")
    add("## 表 B：三个候选映射汇总\n")
    add("| 候选 | 参数个数 | 最大|相对差| | 超标点（非零点） | Krita 重复极差最大值 | 结论 |")
    add("|---|---:|---:|---|---:|---|")
    for cand in candidates:
        if cand not in m["comparison"]:
            continue
        c = m["comparison"][cand]
        note = "过 10%" if c["exceed_count"] == 0 else "未过（%d/%d 点超 10%%）" % (
            c["exceed_count"], c["nonzero_points"])
        add("| %s | %s | %s | %s | %s | %s |" % (
            CAND_LABEL[cand], ("2" if cand == "fit" else "1"),
            ("%.2f%%" % c["max_abs_rel_pct"]) if c["max_abs_rel_pct"] is not None else "—",
            "%d/%d" % (c["exceed_count"], c["nonzero_points"]),
            ("%.2f%%" % c["max_kt_repeat_range_pct"]) if c["max_kt_repeat_range_pct"] is not None else "—",
            note))
    add("")
    add("## 表 C：估计量与旧统计量的复现性对照（候选 ②，j=100）\n")
    add("| 统计量 | PS 值 | Krita 值 | 相对差 | PS 三次重复极差 | Krita 三次重复极差 |")
    add("|---|---:|---:|---:|---:|---:|")
    key = fmt_int(RERENDER_JITTER)
    p = ps[key]
    k = m["krita"][prim][key]
    for label, f in (("band_height（旧，极值统计）", "band_height_median"),
                     ("a_maxabs（124/F-5 原口径 max·(n+1)/n）", "a_maxabs_D"),
                     ("a_q95（2.5%/97.5% 分位半宽，交叉校验）", "a_q95_D"),
                     ("a_range（本实验主统计量 (max−min)/2）", "a_range_D")):
        pv = p.get(f)
        kv = k.get(f)
        rel = _rel(float(pv), float(kv)) if pv is not None and kv is not None else None
        rr_p = p["repeat_range_pct"].get("band_height" if f == "band_height_median" else f)
        rr_k = k["repeat_range_pct"].get("band_height" if f == "band_height_median" else f)
        add("| %s | %.4f | %.4f | %s | %s | %s |" % (
            label, float(pv), float(kv),
            ("%+.2f%%" % rel) if rel is not None else "—",
            ("%.2f%%" % rr_p) if rr_p is not None else "—",
            ("%.2f%%" % rr_k) if rr_k is not None else "—"))
    add("")
    add("## 表 D（对照，126 已判停用）：band_height 6×2\n")
    add("| 散布 j | PS 带高中位数 | Krita 带高中位数 | 相对差 | PS 重复极差 | Krita 重复极差 |")
    add("|---:|---:|---:|---:|---:|---:|")
    for j in jitters:
        key = fmt_int(j)
        pv = ps[key]["band_height_median"]
        kv = m["krita"][prim][key]["band_height_median"]
        rel = _rel(pv, kv)
        rr_p = ps[key]["repeat_range_pct"]["band_height"]
        rr_k = m["krita"][prim][key]["repeat_range_pct"]["band_height"]
        add("| %d | %.1f | %.1f | %s | %s | %s |" % (
            int(j), pv, kv,
            ("%+.2f%%" % rel) if rel is not None else "—",
            ("%.2f%%" % rr_p) if rr_p is not None else "—",
            ("%.2f%%" % rr_k) if rr_k is not None else "—"))
    add("")
    if m.get("fit_params"):
        fp = m["fit_params"]
        add("## 拟合候选 ③ 参数（PS 实测推导）\n")
        add("- a100 = %.6f（PS 侧 j=100 的 a/D 中位口径 95%% 分位估计量）" % fp["a100"])
        add("- a300 = %.6f（PS 侧 j=300 同上）" % fp["a300"])
        add("- 参数个数 = 2；s(j)=j·a100/100（j≤100）；s(j)=a100+(j−100)·(a300−a100)/200（j>100）")
        add("- 换算成 ÷200 的等效系数：a100×200/100 = %.4f×1/200，a300÷300×200 = %.4f×1/200"
            % (fp["a100"] * 2.0, fp["a300"] / 300.0 * 200.0))
    return "\n".join(lines)


def analyze_all(work: Path, jitters: list[float], strokes: int,
                candidates: list[str], fit_params: dict | None) -> dict:
    ps = ps_configs(work, jitters, strokes)
    krita = {}
    for cand in candidates:
        krita[cand] = analyze_candidate(work, cand, jitters, strokes)
    ps_r2 = {}
    krita_r2 = {}
    if RERENDER_JITTER in jitters:
        names = ["j%s_%02d.png" % (fmt_int(RERENDER_JITTER), i) for i in range(strokes)]
        ps_r2["main"] = ps[fmt_int(RERENDER_JITTER)]
        ps_r2["r2"] = analyze_config(work / "png" / "ps_r2", RERENDER_JITTER,
                                     names, group_slices_for(strokes))
        if "div200" in candidates:
            names = ["j%s_%02d.png" % (fmt_int(RERENDER_JITTER), i) for i in range(strokes)]
            krita_r2["main"] = krita["div200"][fmt_int(RERENDER_JITTER)]
            krita_r2["r2"] = analyze_config(work / "png" / "krita_r2" / "div200",
                                            RERENDER_JITTER, names,
                                            group_slices_for(strokes))
    comparison = compare_candidates(ps, krita, candidates, jitters)
    two_render = {}
    for side, pair in (("ps", ps_r2), ("krita", krita_r2)):
        if not pair:
            continue
        a = pair["main"]["a_range_D"]
        b = pair["r2"]["a_range_D"]
        two_render[side] = {
            "main_a_range_D": a, "r2_a_range_D": b,
            "rel_diff_pct": (abs(b - a) / ((a + b) / 2.0) * 100.0) if (a + b) > 0 else 0.0,
            "main_a_maxabs_D": pair["main"]["a_maxabs_D"], "r2_a_maxabs_D": pair["r2"]["a_maxabs_D"],
            "mle_rel_diff_pct": (abs(pair["r2"]["a_maxabs_D"] - pair["main"]["a_maxabs_D"]) /
                                 ((pair["r2"]["a_maxabs_D"] + pair["main"]["a_maxabs_D"]) / 2.0) * 100.0)
            if (pair["main"]["a_maxabs_D"] + pair["r2"]["a_maxabs_D"]) > 0 else 0.0,
            "main_band_height": pair["main"]["band_height_median"],
            "r2_band_height": pair["r2"]["band_height_median"],
        }
        if two_render[side]["main_band_height"] > 0:
            two_render[side]["band_height_rel_diff_pct"] = (
                abs(pair["r2"]["band_height_median"] - pair["main"]["band_height_median"]) /
                ((pair["r2"]["band_height_median"] + pair["main"]["band_height_median"]) / 2.0) * 100.0)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "geometry": {"diameter": DIAMETER, "spacing_pct": SPACING_PCT,
                     "stroke_px": STROKE_PX, "canvas": list(CANVAS),
                     "jitters": jitters, "strokes_per_value": strokes,
                     "statistic": "a_range_D = quantile(|dy-median(dy)|,0.95)/0.95/D"},
        "jitters": jitters,
        "candidates": candidates,
        "fit_params": fit_params,
        "ps": ps,
        "krita": krita,
        "two_render": two_render,
        "comparison": comparison,
    }


# ===========================================================================
# 任务 148/149：角度抖动与笔尖方向动态（F-6 / F-7 / B6 / B8）
# ---------------------------------------------------------------------------
# 统计量（先定义，再量测；方差与已知图校验见 an_validate）
#   单 dab 的模板匹配角 θ_i（mod 360）→ 相对基准角 β 的带符号偏差
#     δ_i = ((θ_i − β + 180) mod 360) − 180 ∈ [−180, 180)
#   主估计量 a_θ = (q97.5(δ) − q2.5(δ)) / 2 / 0.95         [度]
#     —— 均匀分布 [−a, a] 的 5%–95% 覆盖宽度 = 1.9a，除以 1.9 还原「角半跨度」，
#        与任务 129/132 的散布档 a/D 同构；理论值 a_θ = min(180°, 3.6°·j)。
#   交叉校验：σ_θ = a_θ/√3（均匀分布角标准差）、R̄（圆均值合成长度）、
#     D_KS（对整圈均匀分布的 KS 距离；饱和时 → 0）、MAD（中位绝对角偏差）。
#   为什么不用极差：n=40 的极差抽样标准差 ≈1.41a/n，136 A2 已记录不可用；
#     分位差估计量的相对标准误 ≈ 2·sqrt(q(1−q)/n)/1.9 ≈ 2.4%（n=121），
#     an_validate 用已知分布合成图实测该方差。
#   判据（随机档）：两侧 a_θ 的「带高中位数相对差」≤10%；两次独立渲染 ≤5%。
# ---------------------------------------------------------------------------

AN_ABR_DIR = REPO / ".tmp" / "random" / "abr"
AN_ASYM_ABR = AN_ABR_DIR / "bc148_asym.abr"
AN_ASYM_BRUSH = "bc148 asym tip"
AN_D = 100.0                 # 笔尖直径（px）；笔尖位图 100×100 ⇒ 1:1
AN_SPACING_PCT = 500.0       # 孤立 dab 阵列：dab 步长 = 5×D = 500px
AN_GRID = 15                 # 15×15 = 225 个 dab/图（判据只要求 ≥25；
#                              225 个时 a_θ 的两样本复现差标准误 ≈2.2%，稳稳落在 5% 判据内）
AN_PITCH = 240               # 格距 px（D=100 ⇒ 互不重叠）
AN_DAB_EPS = 0.4             # 2 点路径第二点偏移（PS 只落首 dab；124 已实测）
AN_PATCH = 128               # 模板/补丁边长 px
AN_COARSE = 5                # 模板匹配粗搜步长（度）
AN_FINE = 1                  # 精搜步长（度）
AN_TIP_SIZE = 100
AN_ROWS = ("file", "ok", "error", "diameter", "spacing", "jitter",
           "angjitter", "angbvty", "useTipDyn", "n", "angdyn", "szdyn")
AN_JITTERS = [0.0, 25.0, 50.0, 100.0]          # F-6 三档 + j=0 自检/模板
AN_SAT_JITTERS = [50.0, 60.0, 80.0, 100.0]     # 149（B8）饱和判定的四档
AN_REPEATS = 2                                 # 两次独立渲染（复现性口径）
AN_PLUGIN_NAME = "bc148_batch"

#: F-7/B6 的 L 形笔划（两段垂直，方向变化 90°；坐标在 2200×2200 画布内）。
#: 三种起始方向用于区分 bVTy=5 的两种可能语义（固定基准角 0° vs 初始段方向）。
AN_LSHAPES = [
    ("HV", "水平(0°)→垂直(90°)", [(300.0, 400.0), (1800.0, 400.0), (1800.0, 1900.0)]),
    ("VH", "垂直(90°)→水平(0°)", [(400.0, 300.0), (400.0, 1800.0), (1900.0, 1800.0)]),
    ("DD", "对角(45°)→对角(135°)", [(300.0, 300.0), (1150.0, 1150.0), (2000.0, 300.0)]),
]
AN_LSHAPE_CANVAS = (2200, 2200)
#: 计算笔刷的**间距基准实测**（148 诊断）：PS 对椭圆计算笔刷（Rndn=20）的 dab 步长
#: = Spcn% × **短边**（0.2·D），而不是长边 D。实测：Spcn=150 ⇒ 步长 29.9px
#: （=150%×19.9），见 .tmp/random/probe_bvty 与 148 报告 §F-7-0。为得到**孤立 dab**
#: （不靠 spacing 字段假设、可直接连通分离量朝向），PS 侧取 800%（步长 ≈8×20=160px）。
#: Krita 侧 auto_brush 的 spacing 基准是长边 D，需换算成 800%×0.2=160%（见
#: an_lshape_krita_variants 的 spacing_pct），否则两侧 dab 密度不可比。
AN_LSPACING_PCT = 800.0
AN_LSTEP = 0.2 * AN_D * AN_LSPACING_PCT / 100.0     # ≈160px（PS 侧实测步长）
AN_BVTY_ABR = {b: AN_ABR_DIR / ("bc148_bvty%d.abr" % b) for b in (0, 5, 6, 7, 8)}
AN_BVTY_BRUSH = {b: "bc148 bvty%d" % b for b in (0, 5, 6, 7, 8)}


def an_rel(a: float, b: float) -> float | None:
    """相对差（%），分母取中点；任一为 0 时返回 None。"""
    if a is None or b is None:
        return None
    mid = (float(a) + float(b)) / 2.0
    if mid <= 0:
        return None
    return (float(b) - float(a)) / mid * 100.0


def an_grid(n: int = AN_GRID, pitch: int = AN_PITCH):
    """孤立 dab 网格：返回 (画布边长, [(cx, cy), ...])（行优先）。"""
    margin = pitch // 2 + 40
    canvas = 2 * margin + (n - 1) * pitch
    centers = []
    for row in range(n):
        for col in range(n):
            centers.append((float(margin + col * pitch), float(margin + row * pitch)))
    return canvas, centers


def an_dab_paths(centers) -> list[list[list[float]]]:
    """每个 dab 一条 2 点短路径（长度 ≈0.57px，spacing 500% ⇒ 只落首 dab）。"""
    return [[[float(x), float(y)], [float(x) + AN_DAB_EPS, float(y) + AN_DAB_EPS]]
            for (x, y) in centers]


# ---------------------------------------------------------------------------
# PS 侧：一个会话一串 job（job["paths"] = 若干条路径，每条路径是点列表）
# ---------------------------------------------------------------------------

AN_PS_JSX = r"""// 148/149：角度抖动 / 笔尖方向动态出图。由 tools/exp_scatter_stat.py 生成。
// job 字段：file/out/canvas[2]/paths[[[x,y],...],...]/brush_name/abr_path/fields/
//           ang_jitter/ang_bvty/use_tip_dynamics
var WORK = $.getenv("BC129_WORK");
var jobPath = WORK + "/job.json";
var donePath = WORK + "/done.tsv";
function cid(s) { return charIDToTypeID(s); }
function sid(s) { return stringIDToTypeID(s); }
function appDesc() {
    var r = new ActionReference();
    r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
    return executeActionGet(r);
}
function getOpts() { return appDesc().getObjectValue(sid("currentToolOptions")); }
function setOpts(o) {
    var d = new ActionDescriptor();
    var r = new ActionReference();
    r.putClass(sid("paintbrushTool"));
    d.putReference(cid("null"), r);
    d.putObject(cid("T   "), sid("currentToolOptions"), o);
    executeAction(cid("setd"), d, DialogModes.NO);
}
var logFile = new File(WORK + "/ps.log");
logFile.encoding = "UTF-8";
logFile.open("w");
function L(s) { logFile.writeln(String(s)); }
function numOf(o, n) {
    try { return String(o.getObjectValue(sid("brush")).getUnitDoubleValue(sid(n))); }
    catch (e) { return "ABSENT"; }
}
function angJitterOf(o) {
    try { return String(o.getObjectValue(sid("angleDynamics")).getUnitDoubleValue(sid("jitter"))); }
    catch (e) { return "ABSENT"; }
}
function angBvtyOf(o) {
    try {
        var ad = o.getObjectValue(sid("angleDynamics"));
        try { return "int:" + String(ad.getInteger(cid("bVTy"))); } catch (e1) {}
        try { return "int2:" + String(ad.getInteger(sid("bVTy"))); } catch (e2) {}
        try { return "enum:" + keyName(ad.getEnumerationValue(sid("bVTy"))); } catch (e3) {}
        try { return "enum2:" + keyName(ad.getEnumerationValue(cid("bVTy"))); } catch (e4) {}
        return "ABSENT";
    } catch (e0) { return "NOOBJ"; }
}
function tipDynOf(o) {
    try { return String(o.getBoolean(sid("useTipDynamics"))); }
    catch (e) { return "ABSENT"; }
}
// 动态对象的键值转储（控制源 bVTy 在 PS 工具选项里的真实类型/键名，实测取证用）
function keyName(k) {
    try { var s = typeIDToStringID(k); if (s && s.length) { return s; } } catch (e0) {}
    try { return typeIDToCharID(k); } catch (e1) { return "?" + k; }
}
function valOf(ad, k) {
    var t = "?";
    try { t = typeIDToStringID(ad.getType(k)); } catch (e1) {
        try { t = typeIDToCharID(ad.getType(k)); } catch (e2) { t = "?"; }
    }
    try {
        if (t === "long") { return String(ad.getInteger(k)); }
        if (t === "doub") { return String(ad.getDouble(k)); }
        if (t === "UntF") { return String(ad.getUnitDoubleValue(k)) + "u"; }
        if (t === "bool") { return String(ad.getBoolean(k)); }
        if (t === "TEXT") { return String(ad.getString(k)); }
        if (t === "enum") { return keyName(ad.getEnumerationValue(k)); }
    } catch (e3) {}
    // getType 在部分键上不可用：退回「逐个类型试读」（顺序：整数→单位双精度→双精度→布尔→串→枚举）
    try { return "i:" + String(ad.getInteger(k)); } catch (e4) {}
    try { return "u:" + String(ad.getUnitDoubleValue(k)); } catch (e5) {}
    try { return "d:" + String(ad.getDouble(k)); } catch (e6) {}
    try { return "b:" + String(ad.getBoolean(k)); } catch (e7) {}
    try { return "s:" + String(ad.getString(k)); } catch (e8) {}
    try { return "e:" + keyName(ad.getEnumerationValue(k)); } catch (e9) {}
    return "?";
}
function dumpDyn(o, name) {
    var out = [];
    try {
        var ad = o.getObjectValue(sid(name));
        var n = ad.count;
        for (var i = 0; i < n; i++) {
            var k = ad.getKey(i);
            out.push(keyName(k) + "=" + valOf(ad, k));
        }
    } catch (e) { return name + ":ERR " + e.message; }
    return name + "{" + out.join(";") + "}";
}
function selectBrush(n) {
    var d = new ActionDescriptor();
    var r = new ActionReference();
    r.putName(cid("Brsh"), n);
    d.putReference(cid("null"), r);
    executeAction(cid("slct"), d, DialogModes.NO);
}
var job = null, doc = null, abrLoaded = "", results = [];
try {
    app.displayDialogs = DialogModes.NO;
    var jf = new File(jobPath);
    jf.encoding = "UTF-8";
    jf.open("r");
    job = eval("(" + jf.read() + ")");
    jf.close();
    L("jobs=" + job.jobs.length);
    var d0 = new ActionDescriptor();
    var r0 = new ActionReference();
    r0.putClass(sid("paintbrushTool"));
    d0.putReference(cid("null"), r0);
    executeAction(cid("slct"), d0, DialogModes.NO);
    var col = new SolidColor();
    col.rgb.red = 0; col.rgb.green = 0; col.rgb.blue = 0;
    app.foregroundColor = col;
    for (var i = 0; i < job.jobs.length; i++) {
        var j = job.jobs[i];
        var res = { file: j.file, ok: false, error: "", diameter: "", spacing: "",
                    jitter: "", angjitter: "", angbvty: "", useTipDyn: "", n: 0,
                    angdyn: "", szdyn: "" };
        try {
            if (doc) { try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e1) {} doc = null; }
            doc = app.documents.add(j.canvas[0], j.canvas[1], 72, "bc148_" + i,
                                    NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
            if (abrLoaded !== j.abr_path) {
                app.load(new File(j.abr_path));
                abrLoaded = j.abr_path;
                L("LOAD " + j.abr_path);
            }
            selectBrush(j.brush_name);
            var f = j.fields, o = getOpts(), b = o.getObjectValue(sid("brush"));
            b.putUnitDouble(sid("diameter"), cid("#Pxl"), f.diameter);
            if (f.hardness !== null) { b.putUnitDouble(sid("hardness"), cid("#Prc"), f.hardness); }
            if (f.spacing !== null) { b.putUnitDouble(sid("spacing"), cid("#Prc"), f.spacing); }
            if (f.roundness !== null) { b.putUnitDouble(sid("roundness"), cid("#Prc"), f.roundness); }
            if (f.angle !== null) { b.putUnitDouble(sid("angle"), cid("#Ang"), f.angle); }
            o.putObject(sid("brush"), sid("brush"), b);
            o.putBoolean(sid("useTexture"), false);
            o.putBoolean(sid("useTipDynamics"), !!j.use_tip_dynamics);
            o.putBoolean(sid("smoothing"), false);
            o.putInteger(sid("opacity"), 100);
            o.putInteger(sid("flow"), 100);
            o.putBoolean(sid("useScatter"), false);
            setOpts(o);
            if (j.ang_jitter !== null && j.ang_jitter !== undefined) {
                var o2 = getOpts();
                var ad = o2.getObjectValue(sid("angleDynamics"));
                ad.putUnitDouble(sid("jitter"), cid("#Prc"), j.ang_jitter);
                o2.putObject(sid("angleDynamics"), sid("angleDynamics"), ad);
                setOpts(o2);
            }
            if (j.ang_bvty !== null && j.ang_bvty !== undefined) {
                var o3 = getOpts();
                var ad2 = o3.getObjectValue(sid("angleDynamics"));
                ad2.putInteger(sid("bVTy"), j.ang_bvty);
                o3.putObject(sid("angleDynamics"), sid("angleDynamics"), ad2);
                setOpts(o3);
            }
            var o4 = getOpts();
            res.diameter = numOf(o4, "diameter");
            res.spacing = numOf(o4, "spacing");
            res.angjitter = angJitterOf(o4);
            res.angbvty = angBvtyOf(o4);
            res.useTipDyn = tipDynOf(o4);
            res.angdyn = dumpDyn(o4, "angleDynamics");
            res.szdyn = dumpDyn(o4, "szVr");
            for (var k = 0; k < j.paths.length; k++) {
                var p = j.paths[k];
                var pts = [];
                for (var q = 0; q < p.length; q++) {
                    var pt = new PathPointInfo();
                    pt.kind = PointKind.CORNERPOINT;
                    pt.anchor = [p[q][0], p[q][1]];
                    pt.leftDirection = pt.anchor;
                    pt.rightDirection = pt.anchor;
                    pts.push(pt);
                }
                var sub = new SubPathInfo();
                sub.closed = false;
                sub.operation = ShapeOperation.SHAPEADD;
                sub.entireSubPath = pts;
                var pi = doc.pathItems.add("d", [sub]);
                pi.strokePath(ToolType.BRUSH);
                pi.remove();
                res.n = res.n + 1;
            }
            var po = new PNGSaveOptions();
            try { po.transparency = true; } catch (eP) {}
            doc.saveAs(new File(j.out), po, true, Extension.LOWERCASE);
            res.ok = true;
            L("OK " + j.file + " strokes=" + res.n + " d=" + res.diameter +
              " sp=" + res.spacing + " angjit=" + res.angjitter +
              " bvty=" + res.angbvty + " tipdyn=" + res.useTipDyn);
        } catch (e) {
            res.error = e.message + " line " + e.line;
            L("FAIL " + j.file + " : " + res.error);
        }
        results.push(res);
    }
} catch (e) {
    L("FATAL " + e.message + " line " + e.line);
    results.push({ file: "", ok: false, error: "FATAL " + e.message });
} finally {
    try { if (doc) doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e2) {}
    try {
        var out = new File(donePath);
        out.encoding = "UTF-8";
        out.open("w");
        out.writeln("ps_version\t" + app.version);
        var keys = ["file", "ok", "error", "diameter", "spacing", "jitter",
                    "angjitter", "angbvty", "useTipDyn", "n", "angdyn", "szdyn"];
        for (var z = 0; z < results.length; z++) {
            var rr = results[z], row = [];
            for (var m = 0; m < keys.length; m++) {
                var val = String(rr[keys[m]] === undefined ? "" : rr[keys[m]]);
                row.push(val.replace(/[\t\r\n]/g, " ").replace(/"/g, "'"));
            }
            out.writeln(row.join("\t"));
        }
        out.close();
    } catch (e3) { L("WRITE_DONE_FAIL " + e3.message); }
    logFile.close();
    try { app.quit(); } catch (e4) {}
}
"""


def an_abr_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Krita 侧：148 专用批量插件（一次会话连渲多张；每条路径一次 paintLine）
# ---------------------------------------------------------------------------

AN_PLUGIN_SRC = r'''
# -*- coding: utf-8 -*-
# 148/149 批量渲染插件：由 tools/exp_scatter_stat.py 运行时复制到 pykrita，
# 会话结束后删除、kritarc 还原（不留持久改动）。一张图 = 若干条 paintLine 路径。
import json
import os
import time
import traceback

from PyQt5.QtCore import QPoint, QTimer
from krita import Extension, Krita

LOG = os.environ.get("BC129_LOG", "")


def log(msg):
    if not LOG:
        return
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def write_result(payload):
    path = os.environ.get("BC129_PRESULT")
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
    except Exception as exc:
        log("写结果失败 %r" % (exc,))


class BC148Batch(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.tries = 0
        self.closed_titles = []
        QTimer.singleShot(1500, self._tick)

    def setup(self):
        pass

    def createActions(self, window):
        pass

    def _dismiss_blocking_dialogs(self):
        """关掉启动期的模态对话框（实测会卡在 'Sessions - Krita' 会话恢复框）。

        148 现场：%TEMP% 遗留 KRITA_SWAP_FILE_* 会让 Krita 启动弹会话恢复框，
        此时 activeWindow() 一直返回 None，插件轮询空转到超时。这里按标题匹配
        只关对话框（等价用户点关闭），不碰主窗口。
        """
        try:
            from PyQt5.QtWidgets import QApplication
        except Exception as exc:
            log("无法 import QApplication：%r" % (exc,))
            return
        for w in QApplication.topLevelWidgets():
            try:
                if not w.isVisible():
                    continue
                title = w.windowTitle() or ""
                low = title.lower()
                if ("session" in low) or ("会话" in title):
                    log("关闭阻塞对话框：%r" % title)
                    self.closed_titles.append(title)
                    w.close()
            except Exception:
                pass

    def _tick(self):
        self.tries += 1
        try:
            inst = Krita.instance()
            win = inst.activeWindow()
            if win is None:
                if self.tries % 4 == 1:
                    self._dismiss_blocking_dialogs()
                if self.tries < 240:
                    QTimer.singleShot(500, self._tick)
                    return
                write_result({"ok": False, "stage": "window",
                              "reason": "240 次轮询仍无主窗口",
                              "closed_titles": self.closed_titles})
                self._finish()
                return
            self._run(inst, win)
        except Exception as exc:
            log("异常 %r" % (exc,))
            log(traceback.format_exc())
            write_result({"ok": False, "stage": "exception", "reason": repr(exc)})
        self._finish()

    def _run(self, inst, win):
        with open(os.environ["BC129_JOB"], "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        jobs = cfg["jobs"]
        presets = inst.resources("preset")
        plist = list(presets.values()) if isinstance(presets, dict) else list(presets)
        pmap = {}
        for preset in plist:
            try:
                pmap[os.path.basename(preset.filename() or "")] = preset
            except Exception:
                continue
        log("预置数=%d 任务数=%d" % (len(pmap), len(jobs)))
        results = []
        doc = None
        view = None
        for idx, job in enumerate(jobs):
            res = {"file": job.get("file"), "out": job.get("out"),
                   "ok": False, "error": "", "n_paths": 0}
            started = time.time()
            try:
                preset = pmap.get(job.get("preset_file", ""))
                if preset is None:
                    raise RuntimeError("预设未找到：" + str(job.get("preset_file")))
                if doc is None:
                    doc = inst.createDocument(job["width"], job["height"], "bc148",
                                              "RGBA", "U8", "", 72.0)
                    for node in list(doc.topLevelNodes()):
                        node.remove()
                    view = win.addView(doc)
                    if view is None:
                        raise RuntimeError("addView 返回空")
                layer = doc.createNode("bc_paint", "paintlayer")
                doc.rootNode().addChildNode(layer, None)
                doc.setActiveNode(layer)
                doc.refreshProjection()
                view.setCurrentBrushPreset(preset)
                for path in job["paths"]:
                    pts = [(int(round(p[0])), int(round(p[1]))) for p in path]
                    for k in range(len(pts) - 1):
                        a = pts[k]
                        b = pts[k + 1]
                        if a == b:
                            b = (a[0] + 1, a[1] + 1)
                        layer.paintLine(QPoint(a[0], a[1]), QPoint(b[0], b[1]), 1.0, 1.0)
                    res["n_paths"] = res["n_paths"] + 1
                doc.refreshProjection()
                image = doc.projection(0, 0, job["width"], job["height"])
                out_dir = os.path.dirname(job["out"])
                if out_dir and not os.path.isdir(out_dir):
                    os.makedirs(out_dir, exist_ok=True)
                res["ok"] = bool(image.save(job["out"], "PNG"))
                layer.remove()
            except Exception as exc:
                res["error"] = repr(exc)
                log("job %d 失败 %r" % (idx, exc))
            res["elapsed_s"] = round(time.time() - started, 3)
            results.append(res)
            write_result({"ok": True, "stage": "running", "done": len(results), "jobs": results})
            log("job %d/%d %s ok=%s %.2fs" % (idx + 1, len(jobs), res.get("file"),
                                              res["ok"], res["elapsed_s"]))
        try:
            if doc is not None:
                doc.setModified(False)
                doc.close()
        except Exception as exc:
            log("关文档失败 %r" % (exc,))
        write_result({"ok": True, "stage": "done", "done": len(results), "jobs": results})
        log("全部完成 done=%d" % len(results))

    def _finish(self):
        QTimer.singleShot(1500, lambda: os._exit(0))
        try:
            action = Krita.instance().action("file_quit")
            if action is not None:
                action.trigger()
        except Exception:
            pass


log("插件模块加载，注册 Extension")
try:
    Krita.instance().addExtension(BC148Batch(Krita.instance()))
    log("Extension 已注册")
except Exception as exc:
    log("注册 Extension 异常 %r" % (exc,))
    log(traceback.format_exc())
'''


def an_copy_presets(paths: dict, installed: list[str], kpp_paths) -> None:
    """把本批 .kpp 复制进 Krita 的 paintoppresets 目录（会话结束后由会话包装清除）。"""
    for p in kpp_paths:
        dst = os.path.join(paths["presets"], Path(p).name)
        if not os.path.isfile(dst):
            shutil.copyfile(str(p), dst)
            installed.append(Path(p).name)


# ---------------------------------------------------------------------------
# .kpp 生成（只借 src/ 的既有 XML 生成器；不改 src/）
# ---------------------------------------------------------------------------


def an_base_preset(abr_path: Path, brush_name: str):
    abr = AbrFile.parse(str(abr_path))
    presets = map_presets(abr)
    match = [p for p in presets if p.name == brush_name]
    if not match:
        raise SystemExit("角度夹具笔刷找不到：%r（%s）" % (brush_name, abr_path))
    return copy.deepcopy(match[0])


def an_build_kpp(path: Path, abr_path: Path, brush_name: str,
                 rotation_jitter: float | None = None,
                 name: str | None = None,
                 spacing_pct: float | None = None,
                 fuzzy_only: bool = False,
                 flat_fuzzy: bool = False) -> dict:
    """fuzzy_only / flat_fuzzy 是「映射修法候选」的实验开关（不改 src/，只改本批产物）：

    * fuzzy_only：RotationSensor 里**只留 fuzzy**、去掉 fuzzystroke。
      148 实测：现产物两个加法随机源同时启用 ⇒ 随机跨度 = 2×360°×R（j=25 就整圈），
      比 PS 的 360°×R 大一倍；PS 的 angleDynamics 是**逐 dab** 随机，没有「整笔同随机」
      那一层，所以 fuzzystroke 不该出现在这一档。
    * flat_fuzzy：fuzzy/fuzzystroke 曲线压平（0,0;1,0）⇒ 随机贡献 = 0。
      用于「控制源=笔划方向」时把 R 开到 1 但不要随机噪声的修法候选（bVTy=6/7/8）。
    """
    """按 ABR 预设生成 .kpp（隔离其它动态；旋转动态沿用 mapping 的传感器与给定抖动）。"""
    import re

    bp = an_base_preset(abr_path, brush_name)
    bp.size_curve = None
    bp.opacity_curve = None
    bp.flow_curve = None
    bp.ratio_curve = None
    bp.texture = None
    bp.scatter = False
    bp.scatter_pressure = False
    bp.scatter_both_axes = False
    if rotation_jitter is not None:
        bp.rotation_jitter = float(rotation_jitter)
    if spacing_pct is not None:
        bp.spacing = float(spacing_pct) / 100.0

    tip_png = None
    tip_name = None
    if bp.is_computed:
        brush_def = conv_mod._computed_brush_def(bp)
        preview = render_tip_preview(
            conv_mod._circle_mask(bp.hardness if bp.hardness is not None else 100.0,
                                  bp.roundness))
    else:
        tip_png = conv_mod._png_bytes(conv_mod._square_tip(bp.tip_gray))
        tip_name = "bc148_" + hashlib.sha256(tip_png).hexdigest()[:10]
        brush_def = conv_mod._sampled_brush_def(tip_name, tip_png, bp)
        preview = render_tip_preview(bp.tip_gray)

    xml = build_preset_xml(
        name=(name or ("bc148 " + path.stem)),
        brush_definition=brush_def,
        tip_png=tip_png,
        tip_name=tip_name,
        size_curve=None, opacity_curve=None, flow_curve=None, ratio_curve=None,
        rotation_sensor=bp.rotation_sensor,
        rotation_jitter=bp.rotation_jitter,
        scatter=False, scatter_pressure=False, scatter_both_axes=False,
        scatter_amount=None, texture=None,
    )
    if fuzzy_only:
        xml = xml.replace('<ChildSensor id="fuzzystroke"> <curve>0,0;1,1;</curve> </ChildSensor> ', "")
    if flat_fuzzy:
        xml = xml.replace("<curve>0,0;1,1;</curve>", "<curve>0,0;1,0;</curve>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_kpp(xml, preview))

    def grab(pat: str) -> str:
        m = re.search(pat, xml)
        return m.group(1) if m else ""

    sensor_xml = grab(r'name="RotationSensor" type="string"><!\[CDATA\[(.*?)\]\]></param>')
    return {
        "kpp": path.name,
        "brush": brush_name,
        "abr": abr_path.name,
        "rotation_sensor_model": bp.rotation_sensor,
        "rotation_jitter": bp.rotation_jitter,
        "RotationValue": grab(r'name="RotationValue" type="internal">([^<]*)<'),
        "PressureRotation": grab(r'name="PressureRotation" type="internal">([^<]*)<'),
        "RotationSensor_ids": re.findall(r'<ChildSensor id="([a-z]+)"', sensor_xml),
        "diameter": grab(r'diameter="([^"]+)"'),
        "spacing": grab(r'spacing="([^"]+)"'),
        "ratio": grab(r'ratio="([^"]+)"'),
        "hfade": grab(r'hfade="([^"]+)"'),
        "angle_rad": grab(r'angle="([^"]+)"'),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


# ---------------------------------------------------------------------------
# F-6：j=0/25/50/100 的角度抖动（PS + Krita，各两次独立渲染）
# ---------------------------------------------------------------------------


def an_ps_jobs_angle(work: Path, jitters: list[float], repeats: int,
                     subdir: str = "ps", abr: Path = None, brush: str = None) -> list[dict]:
    canvas, centers = an_grid()
    paths = an_dab_paths(centers)
    abr = abr or AN_ASYM_ABR
    brush = brush or AN_ASYM_BRUSH
    jobs = []
    for j in jitters:
        for rep in range(repeats):
            out = work / "png" / subdir / ("j%s_r%d.png" % (fmt_int(j), rep))
            jobs.append({
                "file": out.name, "out": str(out), "rep": rep,
                "canvas": [canvas, canvas], "paths": paths,
                "brush_name": brush, "abr_path": str(abr),
                "fields": {"diameter": AN_D, "hardness": 100.0,
                           "spacing": AN_SPACING_PCT, "roundness": 100.0, "angle": 0.0},
                "ang_jitter": float(j), "ang_bvty": 0, "use_tip_dynamics": True,
            })
    return jobs


def an_render_angle_ps(work: Path, jitters: list[float], repeats: int,
                       force: bool = False) -> dict:
    """F-6 PS 侧：抢 ps.lock → 每个 repeat 一次独立 PS 会话。"""
    jobs = an_ps_jobs_angle(work, jitters, repeats)
    if not force:
        jobs = [j for j in jobs if not Path(j["out"]).is_file()]
    out = {"jobs": len(jobs), "sessions": [], "wait_s": 0.0}
    if not jobs:
        return out
    by_rep: dict[int, list[dict]] = {}
    for job in jobs:
        by_rep.setdefault(job["rep"], []).append(job)
    started = time.time()
    out["wait_s"] = round(acquire_lock(PS_LOCK, "148 角度 PS 出图"), 1)
    try:
        for rep in sorted(by_rep):
            sess = _run_ps_session(by_rep[rep], work / "work" / ("ps_rep%d" % rep),
                                   "rep%d" % rep, AN_PS_JSX, row_keys=AN_ROWS)
            sess["rep"] = rep
            out["sessions"].append(sess)
            if sess["ok"] != sess["total"]:
                raise SystemExit("[PS] 148 角度 rep%d 有失败：%r"
                                 % (rep, [r for r in sess["rows"] if r["ok"] != "true"][:3]))
    finally:
        release_lock(PS_LOCK)
    out["elapsed_s"] = round(time.time() - started, 1)
    return out


def an_krita_jobs_angle(work: Path, jitters: list[float], repeats: int,
                        kpp_map: dict[str, Path]) -> list[dict]:
    canvas, centers = an_grid()
    paths = an_dab_paths(centers)
    jobs = []
    for j in jitters:
        for rep in range(repeats):
            out = work / "png" / "krita" / ("j%s_r%d.png" % (fmt_int(j), rep))
            jobs.append({"file": out.name, "out": str(out), "rep": rep,
                         "preset_file": kpp_map[fmt_int(j)].name,
                         "width": canvas, "height": canvas, "paths": paths})
    return jobs


def an_render_angle_krita(work: Path, jitters: list[float], repeats: int,
                          force: bool = False) -> dict:
    """F-6 Krita 侧：抢 krita.lock → 每个 repeat 一次独立 Krita 会话。"""
    kpp_map: dict[str, Path] = {}
    readbacks = {}
    for j in jitters:
        p = work / "kpp" / ("asym_j%s.kpp" % fmt_int(j))
        readbacks[fmt_int(j)] = an_build_kpp(p, AN_ASYM_ABR, AN_ASYM_BRUSH,
                                             rotation_jitter=float(j))
        kpp_map[fmt_int(j)] = p
    jobs = an_krita_jobs_angle(work, jitters, repeats, kpp_map)
    if not force:
        jobs = [j for j in jobs if not Path(j["out"]).is_file()]
    out = {"jobs": len(jobs), "launches": [], "kpp": readbacks, "wait_s": 0.0}
    if not jobs:
        return out
    started = time.time()
    out["wait_s"] = round(acquire_lock(KRITA_LOCK, "148 角度 Krita 出图"), 1)
    try:
        for rep in range(repeats):
            rep_jobs = [j for j in jobs if j["rep"] == rep]
            if not rep_jobs:
                continue

            def _run(paths: dict, installed: list[str], rep_jobs=rep_jobs,
                     rep=rep) -> dict:
                kpps = [kpp_map[k] for k in sorted(kpp_map, key=lambda s: int(s))]
                an_copy_presets(paths, installed, kpps)
                return run_krita_launch(rep_jobs, work / "work" / ("krita_rep%d" % rep),
                                        "rep%d" % rep)

            payload = with_krita_session(_run, AN_PLUGIN_NAME, AN_PLUGIN_SRC, "BC148 Batch")
            out["launches"].append({"rep": rep, "result": payload["result"],
                                    "verify": payload["verify"],
                                    "plugins_removed": payload["plugins_removed"]})
    finally:
        release_lock(KRITA_LOCK)
    out["elapsed_s"] = round(time.time() - started, 1)
    return out


def an_render_angle(work: Path, side: str, jitters: list[float], repeats: int,
                    force: bool = False) -> dict:
    out: dict = {}
    if side in ("ps", "both"):
        out["ps"] = an_render_angle_ps(work, jitters, repeats, force)
    if side in ("krita", "both"):
        out["krita"] = an_render_angle_krita(work, jitters, repeats, force)
    return out


# ---------------------------------------------------------------------------
# 图像分析：模板匹配求角 + 圆统计量
# ---------------------------------------------------------------------------


def an_load_alpha(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"))[:, :, 3].astype(np.float64)


def an_patch_at(alpha: np.ndarray, cx: float, cy: float, half: int = AN_PATCH // 2):
    """以 (cx,cy) 为初值：窗口内质心细化 → 以质心为中心切 2·half 见方补丁。"""
    h, w = alpha.shape
    x0, y0 = int(round(cx)) - half, int(round(cy)) - half
    win = alpha[max(0, y0):min(h, y0 + 2 * half), max(0, x0):min(w, x0 + 2 * half)]
    s = float(win.sum())
    if s <= 100.0:
        return None
    ys = np.arange(max(0, y0), min(h, y0 + 2 * half), dtype=np.float64)[:, None]
    xs = np.arange(max(0, x0), min(w, x0 + 2 * half), dtype=np.float64)[None, :]
    cy2 = float((win * ys).sum() / s)
    cx2 = float((win * xs).sum() / s)
    iy, ix = int(round(cy2)), int(round(cx2))
    if iy - half < 0 or ix - half < 0 or iy + half >= h or ix + half >= w:
        return None
    return alpha[iy - half:iy + half, ix - half:ix + half]


def an_template_from(alpha: np.ndarray, centers) -> np.ndarray:
    """把 j=0 图的所有 dab 补丁取平均作模板（压力/位置噪声被平均掉）。"""
    patches = [an_patch_at(alpha, x, y) for (x, y) in centers]
    patches = [p for p in patches if p is not None]
    if not patches:
        raise SystemExit("模板补丁为空（j=0 图没找到 dab）")
    return np.mean(np.asarray(patches), axis=0)


def _an_rot_array(tpl_img: Image.Image, th: float, size: int) -> np.ndarray:
    arr = np.asarray(tpl_img.rotate(float(th), resample=Image.BILINEAR), dtype=np.float64)
    return arr.reshape(size * size)


def an_match_angles(patches: np.ndarray, template: np.ndarray,
                    coarse: int = AN_COARSE, fine: int = AN_FINE) -> dict:
    """模板匹配求每个补丁的旋转角（0..360，粗搜 + 精搜 NCC）。

    向量化：粗搜一次性算 n×m 的 NCC 矩阵（m = 360/coarse），再对每个补丁做
    ±(coarse−1) 的细化；返回角度、NCC 与「最优角」的 NCC 余量（次优峰）。
    """
    tpl_img = Image.fromarray(np.uint8(np.clip(template, 0.0, 255.0)))
    n = len(patches)
    half = AN_PATCH // 2
    size = 2 * half
    P = np.asarray(patches, dtype=np.float64).reshape(n, size * size)
    P = P - P.mean(axis=1, keepdims=True)
    Pn = np.linalg.norm(P, axis=1) + 1e-12
    P = P / Pn[:, None]
    coarse_angles = list(range(0, 360, coarse))
    T = np.stack([_an_rot_array(tpl_img, th, size) for th in coarse_angles])
    T = T - T.mean(axis=1, keepdims=True)
    T = T / (np.linalg.norm(T, axis=1, keepdims=True) + 1e-12)
    C = P @ T.T                                    # (n, m)
    best_idx = np.argmax(C, axis=1)
    angles = np.zeros(n)
    nccs = np.zeros(n)
    for i in range(n):
        th0 = coarse_angles[int(best_idx[i])]
        best_th, best_c = float(th0), float(C[i, best_idx[i]])
        for d in range(-coarse + 1, coarse):
            th = (th0 + d * fine / max(1, fine)) % 360
            arr = _an_rot_array(tpl_img, th, size)
            arr = arr - arr.mean()
            c = float((P[i] @ arr) / (np.linalg.norm(arr) + 1e-12))
            if c > best_c:
                best_th, best_c = float(th), c
        angles[i] = best_th % 360.0
        nccs[i] = best_c
    return {"angles": angles, "ncc": nccs}


def an_circular_stats(angles, base_deg: float = 0.0, ncc=None,
                      ncc_min: float = 0.0) -> dict:
    """角度样本的圆统计量（主估计量 a_θ，见本段文件头注释）。"""
    th = np.asarray(angles, dtype=np.float64)
    if ncc is not None:
        keep = np.asarray(ncc, dtype=np.float64) >= ncc_min
        th = th[keep]
    n = int(th.size)
    if n == 0:
        return {"n": 0}
    delta = ((th - base_deg + 180.0) % 360.0) - 180.0
    q025 = float(np.quantile(delta, 0.025))
    q975 = float(np.quantile(delta, 0.975))
    a_theta = (q975 - q025) / 2.0 / 0.95
    rad = np.radians(delta)
    r_bar = float(np.hypot(np.cos(rad).mean(), np.sin(rad).mean()))
    # KS：H0 = 整圈均匀（[-180,180)）
    u = np.sort((delta + 180.0) / 360.0)
    i = np.arange(1, n + 1, dtype=np.float64)
    ks = float(max(np.max(np.abs(i / n - u)), np.max(np.abs(u - (i - 1.0) / n))))
    absd = np.abs(delta)
    return {
        "n": n,
        "a_theta_deg": a_theta,
        "sigma_theta_deg": a_theta / math.sqrt(3.0),
        "q025_deg": q025,
        "q975_deg": q975,
        "min_deg": float(delta.min()),
        "max_deg": float(delta.max()),
        "range_deg": float(delta.max() - delta.min()),
        "mad_deg": float(np.median(absd)),
        "r_bar": r_bar,
        "ks_uniform": ks,
        "ncc_mean": float(np.mean(ncc)) if ncc is not None else None,
        "ncc_min": float(np.min(ncc)) if ncc is not None else None,
        "delta_deg": [round(float(v), 2) for v in delta],
    }


#: 旋转基线（度）：PS 的 angleDynamics 绕基准角（本夹具 Angl=0）随机；
#: Krita 在「旋转动态启用」时按 KisRotationOption 的 value=1-value 约定，
#: 中性点对应 **180°**（148 实测：R=0.25 的 dab 角度集中在 139°..221°，中心 180°）。
#: 所以比较前必须把两侧折算到各自基线，否则 a_θ 会被「180° 偏移」撑成整圈假象。
AN_BASE_PS = 0.0
AN_BASE_KRITA_ROT = 180.0


def an_side_base(side: str, jitter: float) -> float:
    """某侧某档的角度基线：PS=0°；Krita 仅当旋转动态开启（j>0）时=180°，j=0 时=0°。"""
    if side == "ps":
        return AN_BASE_PS
    return AN_BASE_KRITA_ROT if float(jitter) > 0.0 else 0.0


def an_analyze_angle_side(work: Path, side: str, jitters: list[float],
                          repeats: int, ncc_min: float = 0.0) -> dict:
    """一侧（ps/krita）的全部角度统计：j=0 图做模板，逐配置算 a_θ 与复现性。"""
    canvas, centers = an_grid()
    side_dir = work / "png" / ("ps" if side == "ps" else "krita")
    j0_path = side_dir / ("j0_r0.png")
    if not j0_path.is_file():
        raise SystemExit("缺少 j=0 模板图：%s" % j0_path)
    tpl = an_template_from(an_load_alpha(j0_path), centers)
    out: dict = {"template": {"file": j0_path.name, "n_centers": len(centers)},
                 "configs": {}, "repeats": {}}
    per_rep_stats: dict[str, dict[str, dict]] = {}
    for j in jitters:
        key = fmt_int(j)
        per_rep_stats[key] = {}
        pooled_patches = []
        pooled_angles = []
        pooled_ncc = []
        for rep in range(repeats):
            img = side_dir / ("j%s_r%d.png" % (key, rep))
            if not img.is_file():
                per_rep_stats[key]["r%d" % rep] = {"missing": True}
                continue
            alpha = an_load_alpha(img)
            patches = [an_patch_at(alpha, x, y) for (x, y) in centers]
            idx = [i for i, p in enumerate(patches) if p is not None]
            good = [patches[i] for i in idx]
            m = an_match_angles(np.asarray(good), tpl)
            base = an_side_base(side, j)
            st = an_circular_stats(m["angles"], base, m["ncc"], ncc_min)
            st["base_deg"] = base
            st["n_patch"] = len(good)
            st["n_centers"] = len(centers)
            st["file"] = img.name
            per_rep_stats[key]["r%d" % rep] = st
            pooled_patches.extend(good)
            pooled_angles.extend(list(m["angles"]))
            pooled_ncc.extend(list(m["ncc"]))
        if pooled_patches:
            # 池化两个 rep 的匹配结果（不重复匹配：每 rep 的结果已算过）
            st = an_circular_stats(np.asarray(pooled_angles), an_side_base(side, j),
                                   np.asarray(pooled_ncc), ncc_min)
            st["base_deg"] = an_side_base(side, j)
            st["n_patch"] = len(pooled_patches)
        else:
            st = {"n": 0}
        out["configs"][key] = st
        # 两次独立渲染的复现性（用各 rep 的 a_θ）
        reps = [per_rep_stats[key]["r%d" % r] for r in range(repeats)
                if not per_rep_stats[key]["r%d" % r].get("missing")]
        if len(reps) >= 2 and all(r.get("n", 0) > 0 for r in reps):
            a1, a2 = float(reps[0]["a_theta_deg"]), float(reps[1]["a_theta_deg"])
            rel = an_rel(a1, a2)      # j=0 时 a_θ≈0，相对差无定义 → None（不参与判据）
            out["repeats"][key] = {
                "a_theta": [a1, a2],
                "rel_diff_pct": (abs(rel) if rel is not None else None),
            }
    out["per_rep"] = per_rep_stats
    return out


def an_compare_angle(ps: dict, krita: dict, jitters: list[float]) -> dict:
    out = {}
    for j in jitters:
        key = fmt_int(j)
        p = ps["configs"].get(key, {})
        k = krita["configs"].get(key, {})
        if not p or not k or p.get("n", 0) == 0 or k.get("n", 0) == 0:
            continue
        rel = an_rel(float(p["a_theta_deg"]), float(k["a_theta_deg"]))
        sat_val = 180.0
        row = {
            "j": float(j),
            "ps_a_theta": float(p["a_theta_deg"]), "kt_a_theta": float(k["a_theta_deg"]),
            "rel_pct": rel,
            "pass_10pct": (rel is not None and abs(rel) <= 10.0),
            "ps_sigma": float(p["sigma_theta_deg"]), "kt_sigma": float(k["sigma_theta_deg"]),
            "ps_ks": float(p["ks_uniform"]), "kt_ks": float(k["ks_uniform"]),
            "ps_r_bar": float(p["r_bar"]), "kt_r_bar": float(k["r_bar"]),
            "ps_n": int(p["n"]), "kt_n": int(k["n"]),
            "ps_repeat_pct": (ps["repeats"].get(key) or {}).get("rel_diff_pct"),
            "kt_repeat_pct": (krita["repeats"].get(key) or {}).get("rel_diff_pct"),
            "pred_a_theta": min(sat_val, 3.6 * float(j)),
            "sat_fraction": min(1.0, 3.6 * float(j) / 180.0),
        }
        out[key] = row
    return out


# ---------------------------------------------------------------------------
# 已知图校验：模板匹配角度的准确度 + 角半跨度估计量的偏差/方差
# ---------------------------------------------------------------------------


def an_validate(work: Path, seed: int = 1480) -> dict:
    """在「已知角/已知分布」的合成图上校验量测链（不渲染 PS/Krita，纯合成）。

    三组校验（全部写进 manifest，可复算）：
      V1 模板自相关：除 0° 邻域（±10°）外的最大 NCC —— 判「360° 唯一匹配」；
      V2 已知旋转恢复：把模板按已知角旋转后当补丁喂给匹配器，看角度误差；
      V3 已知分布恢复：按 U(−a,a) 的已知角把模板贴到画布上（含位置抖动 + 0.8px
         高斯模糊模拟 PS/Krita 抗锯齿），跑完整「补丁提取 → 匹配 → a_θ」链，
         看 a_θ 相对真值 a 的偏差与两次独立样本的复现差。
    """
    from PIL import ImageFilter

    work.mkdir(parents=True, exist_ok=True)
    tpl_path = work / "png" / "ps" / "j0_r0.png"
    if not tpl_path.is_file():
        raise SystemExit("校验需要 PS 侧 j=0 图（先 --angle-samples --angle-side ps）")
    canvas, centers = an_grid()
    alpha = an_load_alpha(tpl_path)
    tpl = an_template_from(alpha, centers)
    tpl_img = Image.fromarray(np.uint8(np.clip(tpl, 0.0, 255.0)))
    size = AN_PATCH
    out: dict = {"template": {"source": str(tpl_path.relative_to(work)), "size": size}}

    # V1 自相关唯一性
    base = _an_rot_array(tpl_img, 0.0, size)
    base = base - base.mean()
    nb = np.linalg.norm(base)
    scores = []
    for th in range(0, 360):
        arr = _an_rot_array(tpl_img, th, size)
        scores.append(float((base @ (arr - arr.mean())) / (nb * (np.linalg.norm(arr - arr.mean()) + 1e-12))))
    far = [t for t in range(360) if min(t, 360 - t) > 10]
    out["V1_template_autocorr"] = {
        "ncc_0": round(scores[0], 6),
        "ncc_180": round(scores[180], 6),
        "max_ncc_outside_pm10": round(max(scores[t] for t in far), 6),
        "argmax_outside_pm10": int(max(far, key=lambda t: scores[t])),
    }

    # V2 已知旋转恢复（每个角 4 个补丁，±1px 位置抖动）
    rng = np.random.RandomState(seed)
    known = [0.0, 37.0, 90.0, 143.0, 217.0, 301.0, 350.0]
    rec = {}
    for th in known:
        patches = []
        for k in range(4):
            arr = np.asarray(tpl_img.rotate(float(th), resample=Image.BILINEAR,
                                            center=(size / 2.0, size / 2.0)), dtype=np.float64)
            # 位置抖动：整体平移 ±1px（用 PIL 的 affine 更麻烦，这里直接 numpy 平移）
            dy, dx = rng.randint(-1, 2), rng.randint(-1, 2)
            arr = np.roll(arr, (dy, dx), axis=(0, 1))
            patches.append(arr)
        m = an_match_angles(np.asarray(patches), tpl)
        err = [min(abs((a - th + 180) % 360 - 180), abs((a - th - 180) % 360 - 180))
               for a in m["angles"]]
        rec["%g" % th] = {"angles": [round(float(a), 1) for a in m["angles"]],
                          "max_abs_err_deg": round(float(max(err)), 2),
                          "ncc": [round(float(c), 3) for c in m["ncc"]]}
    out["V2_known_rotation"] = rec

    # V3 已知分布恢复（a = 30/90/180；两次独立样本；含模糊）
    def synth(a_true: float, n_dabs: int, rng_seed: int):
        r = np.random.RandomState(rng_seed)
        cv, ctr = an_grid()
        img = np.zeros((cv, cv), dtype=np.float64)
        angles = r.uniform(-a_true, a_true, size=n_dabs)
        for idx, (cx, cy) in enumerate(ctr[:n_dabs]):
            arr = np.asarray(tpl_img.rotate(float(angles[idx]), resample=Image.BILINEAR,
                                            center=(size / 2.0, size / 2.0)), dtype=np.float64)
            iy, ix = int(round(cy)) - size // 2, int(round(cx)) - size // 2
            img[iy:iy + size, ix:ix + size] = arr
        pil = Image.fromarray(np.uint8(np.clip(img, 0, 255))).filter(ImageFilter.GaussianBlur(0.8))
        return np.asarray(pil, dtype=np.float64), angles
    v3 = {}
    for a_true in (30.0, 90.0, 180.0):
        runs = []
        for rseed in (seed + 11, seed + 22):
            cv, ctr = an_grid()
            img, angles_true = synth(a_true, len(ctr), rseed)
            patches = [an_patch_at(img, x, y) for (x, y) in ctr]
            patches = [p for p in patches if p is not None]
            m = an_match_angles(np.asarray(patches), tpl)
            st = an_circular_stats(m["angles"], 0.0, m["ncc"], 0.0)
            truth = an_circular_stats(angles_true, 0.0)
            runs.append({"a_theta": float(st["a_theta_deg"]),
                         "a_theta_true": float(truth["a_theta_deg"]),
                         "bias_pct": an_rel(float(a_true), float(st["a_theta_deg"])),
                         "bias_vs_empirical_pct": an_rel(float(truth["a_theta_deg"]),
                                                         float(st["a_theta_deg"])),
                         "n": int(st["n"]), "ncc_mean": round(float(np.mean(m["ncc"])), 3)})
        r1, r2 = runs
        v3["%g" % a_true] = {
            "a_true": a_true,
            "runs": runs,
            "mean_a_theta": (r1["a_theta"] + r2["a_theta"]) / 2.0,
            "two_sample_rel_pct": abs(an_rel(r1["a_theta"], r2["a_theta"])),
            "max_abs_bias_pct": max(abs(r1["bias_pct"]), abs(r2["bias_pct"])),
            # 相对「同一合成样本的经验真值」的偏差：估计量本身的偏差（不含抽样）
            "max_abs_bias_vs_empirical_pct": max(
                abs(r1["bias_vs_empirical_pct"]), abs(r2["bias_vs_empirical_pct"])),
        }
    out["V3_known_distribution"] = v3
    (work / "validate.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# F-7 / B6：bVTy 5/6/7/8 的 L 形笔划（PS + Krita）
# ---------------------------------------------------------------------------


def an_lshape_ps_variants() -> list[dict]:
    """F-7/B6 的 PS 侧变体：bVTy=0（参照）+ 5/6/7/8（ABR 自带控制源，jitter=0）。"""
    out = []
    for bvty in (0, 5, 6, 7, 8):
        out.append({
            "tag": "ps_bvty%d" % bvty,
            "side": "ps",
            "label": "bVTy=%d（%s）" % (bvty, {0: "关·参照组", 5: "初始方向",
                                             6: "笔划方向", 7: "速度", 8: "旋转"}[bvty]),
            "abr": AN_BVTY_ABR[bvty],
            "brush": AN_BVTY_BRUSH[bvty],
            "ang_bvty": bvty,
            "ang_jitter": 0.0,
            "use_tip_dynamics": True,
        })
    return out


def an_lshape_krita_variants(work: Path) -> list[dict]:
    """Krita 侧变体：converter 现产物（5/6/7/8）+ 「RotationValue=1」修正候选（6/7/8）。"""
    out = []
    kpp_dir = work / "kpp"
    # Krita auto_brush 的 spacing 基准 = 长边 D（PS 对椭圆计算笔刷用短边）：
    # 要复现 PS 的 160px dab 步长，Krita 写 160%（= 800%×0.2）。这条换算同时是
    # 「椭圆计算笔刷 spacing 基准」的实测旁证（B4 归属 exp-geom，本报告只登记）。
    kt_spacing_pct = AN_LSPACING_PCT * 0.2
    for bvty in (0, 5, 6, 7, 8):
        p = kpp_dir / ("lshape_bvty%d.kpp" % bvty)
        rb = an_build_kpp(p, AN_BVTY_ABR[bvty], AN_BVTY_BRUSH[bvty],
                          rotation_jitter=None, name="bc148 lshape bvty%d" % bvty,
                          spacing_pct=kt_spacing_pct)
        out.append({"tag": "kt_bvty%d" % bvty, "side": "krita",
                    "label": "bVTy=%d（converter 产物；spacing 换算 160%%）" % bvty,
                    "kpp": p, "readback": rb, "ang_bvty": bvty})
        if bvty in (6, 7, 8):
            p2 = kpp_dir / ("lshape_bvty%d_rv1.kpp" % bvty)
            rb2 = an_build_kpp(p2, AN_BVTY_ABR[bvty], AN_BVTY_BRUSH[bvty],
                               rotation_jitter=50.0, spacing_pct=kt_spacing_pct,
                               name="bc148 lshape bvty%d rv1" % bvty)
            out.append({"tag": "kt_bvty%d_rv1" % bvty, "side": "krita",
                        "label": "bVTy=%d（只把 RotationValue 开到 1）" % bvty,
                        "kpp": p2, "readback": rb2, "ang_bvty": bvty})
            # 修法候选：控制源 R=1 + 随机源压平（把「方向承载」和「随机抖动」解耦）
            p3 = kpp_dir / ("lshape_bvty%d_fix.kpp" % bvty)
            rb3 = an_build_kpp(p3, AN_BVTY_ABR[bvty], AN_BVTY_BRUSH[bvty],
                               rotation_jitter=50.0, spacing_pct=kt_spacing_pct,
                               flat_fuzzy=True,
                               name="bc148 lshape bvty%d fix" % bvty)
            out.append({"tag": "kt_bvty%d_fix" % bvty, "side": "krita",
                        "label": "bVTy=%d（修法候选：R=1 + fuzzy 压平）" % bvty,
                        "kpp": p3, "readback": rb3, "ang_bvty": bvty})
    return out


def an_lshape_jobs(work: Path, variants: list[dict], subdir: str) -> list[dict]:
    jobs = []
    for v in variants:
        for tag, label, pts in AN_LSHAPES:
            if v["side"] == "ps":
                out = work / "png" / subdir / ("%s_%s.png" % (v["tag"], tag))
                jobs.append({
                    "file": out.name, "out": str(out), "variant": v["tag"], "shape": tag,
                    "canvas": list(AN_LSHAPE_CANVAS),
                    "paths": [list(pts)],
                    "brush_name": v["brush"], "abr_path": str(v["abr"]),
                    "fields": {"diameter": AN_D, "hardness": 100.0,
                               "spacing": AN_LSPACING_PCT, "roundness": 20.0, "angle": 0.0},
                    "ang_jitter": v["ang_jitter"], "ang_bvty": v["ang_bvty"],
                    "use_tip_dynamics": v["use_tip_dynamics"],
                })
            else:
                out = work / "png" / subdir / ("%s_%s.png" % (v["tag"], tag))
                jobs.append({
                    "file": out.name, "out": str(out), "variant": v["tag"], "shape": tag,
                    "preset_file": Path(v["kpp"]).name,
                    "width": AN_LSHAPE_CANVAS[0], "height": AN_LSHAPE_CANVAS[1],
                    "paths": [list(pts[:2]), list(pts[1:])],   # 两段各一次 paintLine
                })
    return jobs


def an_render_lshape(work: Path, force: bool = False) -> dict:
    """F-7/B6 双侧出图：PS 4 个 bVTy 变体 + Krita 7 个 kpp 变体 × 3 种 L 形。"""
    ps_variants = an_lshape_ps_variants()
    kt_variants = an_lshape_krita_variants(work)
    ps_jobs = an_lshape_jobs(work, ps_variants, "ps")
    kt_jobs = an_lshape_jobs(work, kt_variants, "krita")
    if not force:
        ps_jobs = [j for j in ps_jobs if not Path(j["out"]).is_file()]
        kt_jobs = [j for j in kt_jobs if not Path(j["out"]).is_file()]
    def _ser(v: dict) -> dict:
        # manifest 是 JSON：Path 一律转字符串
        return {k: (str(val) if isinstance(val, Path) else val)
                for k, val in v.items() if k in ("tag", "label", "kpp", "abr", "brush",
                                                 "ang_bvty", "ang_jitter", "use_tip_dynamics")}
    out: dict = {"ps_variants": [_ser(v) for v in ps_variants],
                 "krita_variants": [_ser(v) for v in kt_variants],
                 "ps_jobs": len(ps_jobs), "krita_jobs": len(kt_jobs),
                 "readback": {v["tag"]: v.get("readback") for v in kt_variants}}
    started = time.time()
    if ps_jobs:
        waited = acquire_lock(PS_LOCK, "148/149 F-7 B6 PS 出图")
        try:
            out["ps_session"] = _run_ps_session(ps_jobs, work / "work" / "ps_lshape",
                                                "lshape", AN_PS_JSX, row_keys=AN_ROWS)
        finally:
            release_lock(PS_LOCK)
        out["ps_wait_s"] = round(waited, 1)
        sess = out["ps_session"]
        if sess["ok"] != sess["total"]:
            raise SystemExit("[PS] L 形有失败：%r"
                             % [r for r in sess["rows"] if r["ok"] != "true"][:3])
    if kt_jobs:
        waited = acquire_lock(KRITA_LOCK, "148/149 F-7 B6 Krita 出图")
        try:
            kpps = [v["kpp"] for v in kt_variants]

            def _run(paths: dict, installed: list[str]) -> dict:
                an_copy_presets(paths, installed, kpps)
                return run_krita_launch(kt_jobs, work / "work" / "krita_lshape", "lshape")

            payload = with_krita_session(_run, AN_PLUGIN_NAME, AN_PLUGIN_SRC, "BC148 Batch")
        finally:
            release_lock(KRITA_LOCK)
        out["krita_wait_s"] = round(waited, 1)
        out["krita_launch"] = {"result": payload["result"], "verify": payload["verify"],
                               "plugins_removed": payload["plugins_removed"]}
    out["elapsed_s"] = round(time.time() - started, 1)
    return out


def an_circular_mean_mod180(vals) -> dict:
    """mod 180 角度样本的圆统计（倍角法）：返回圆均值（度，[0,180)）与圆标准差（度）。"""
    th = np.asarray(vals, dtype=np.float64)
    n = int(th.size)
    if n == 0:
        return {}
    phi = np.radians(2.0 * th)
    c = float(np.cos(phi).mean())
    s = float(np.sin(phi).mean())
    r_bar = float(math.hypot(c, s))
    mean_deg = (math.degrees(math.atan2(s, c)) / 2.0) % 180.0
    # 圆标准差 = sqrt(-2 ln R̄)（倍角域），再除以 2 回到原角度域
    spread = (math.degrees(math.sqrt(max(0.0, -2.0 * math.log(max(r_bar, 1e-9))))) / 2.0)
    return {"mean_deg": mean_deg, "spread_deg": spread, "r_bar": r_bar, "n": n}


def an_orientation(alpha: np.ndarray, cx: float, cy: float, half: int = 70,
                   ux: float | None = None, uy: float | None = None,
                   half_t: float | None = None, half_n: float | None = None) -> dict | None:
    """单 dab 的二阶矩长轴方向（mod 180，度）与离心率/面积。

    椭圆笔尖（Rndn=20）的朝向可直接由二阶矩得到；mod 180 对 bVTy 5/6 的
    90° 拐角判定足够（两段垂直于 90°，折叠后仍是 90°）。

    148 修正：Krita 的对椭圆 auto_brush 的 dab 步长会随「笔尖在笔划方向上的
    投影」变化（实测同一张图里水平段 160px、垂直段 32px），方形大窗口会把
    相邻 dab 一起框进来、把「叠罗汉方向」误当成笔尖朝向（第一版就测出假 90°）。
    给定 ux/uy（段单位向量）时改用**沿段对齐的矩形窗口**（±half_t, ±half_n）。
    """
    h, w = alpha.shape
    if ux is not None and uy is not None:
        nx, ny = -uy, ux
        ht = max(6, int(round(half_t if half_t is not None else half)))
        hn = max(6, int(round(half_n if half_n is not None else half)))
        ts = np.arange(-ht, ht + 1, dtype=np.float64)
        ns = np.arange(-hn, hn + 1, dtype=np.float64)
        TT, NN = np.meshgrid(ts, ns)
        X = np.rint(cx + ux * TT + nx * NN).astype(int)
        Y = np.rint(cy + uy * TT + ny * NN).astype(int)
        if X.min() < 0 or Y.min() < 0 or X.max() >= w or Y.max() >= h:
            return None
        win = alpha[Y, X].copy()
        xs = X.astype(np.float64)
        ys = Y.astype(np.float64)
    else:
        x0 = int(round(cx)) - half
        y0 = int(round(cy)) - half
        if x0 < 0 or y0 < 0 or x0 + 2 * half >= w or y0 + 2 * half >= h:
            return None
        win = alpha[y0:y0 + 2 * half, x0:x0 + 2 * half].copy()
        ys, xs = np.mgrid[y0:y0 + 2 * half, x0:x0 + 2 * half]
    win[win < 8.0] = 0.0
    s = float(win.sum())
    if s <= 100.0:
        return None
    mx = float((win * xs).sum() / s)
    my = float((win * ys).sum() / s)
    dx = xs - mx
    dy = ys - my
    mu20 = float((win * dx * dx).sum() / s)
    mu02 = float((win * dy * dy).sum() / s)
    mu11 = float((win * dx * dy).sum() / s)
    theta = 0.5 * math.atan2(2.0 * mu11, mu20 - mu02)
    tr = (mu20 + mu02) / 2.0
    disc = math.sqrt(max(0.0, ((mu20 - mu02) / 2.0) ** 2 + mu11 * mu11))
    ev1 = tr + disc
    ev2 = tr - disc
    ecc = (ev1 - ev2) / (ev1 + ev2) if (ev1 + ev2) > 0 else 0.0
    return {"angle_deg": math.degrees(theta) % 180.0, "eccentricity": ecc,
            "area": s, "centroid": (mx, my)}


def an_profile_dabs(alpha: np.ndarray, p0, p1, half_win: int = 90,
                    t_margin: float = 40.0, thr: float = 30.0) -> list[dict]:
    """沿一段笔划的**法向投影剖面**找孤立 dab（不依赖 spacing 字段假设）。

    作透视图：沿段方向 t 逐像素取一条法向剖面（±half_win），把 alpha 求和得到
    t 方向的一维轮廓；轮廓上的连续高值段 = 一个 dab。这样即使 PS 的间距基准与
    ABR 字段不一致（148 实测：椭圆计算笔刷按短边算），也能量到真实的 dab 位置，
    并且能顺便量出实际 dab 步长（报告里的 spacing 基准证据）。
    """
    h, w = alpha.shape
    x0, y0 = float(p0[0]), float(p0[1])
    x1, y1 = float(p1[0]), float(p1[1])
    seg_len = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / seg_len, (y1 - y0) / seg_len
    nx, ny = -uy, ux
    ts = np.arange(t_margin, seg_len - t_margin, 1.0)
    offs = np.arange(-half_win, half_win + 1, 1.0)
    prof = np.empty(ts.size)
    for i in range(ts.size):
        t = ts[i]
        xs = np.rint(x0 + ux * t + nx * offs).astype(int)
        ys = np.rint(y0 + uy * t + ny * offs).astype(int)
        ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        prof[i] = alpha[ys[ok], xs[ok]].sum() if ok.any() else 0.0
    on = prof > thr
    dabs = []
    i = 0
    while i < on.size:
        if not on[i]:
            i += 1
            continue
        k = i
        while k < on.size and on[k]:
            k += 1
        if k - i >= 3:                      # 至少 3px 宽才算一个 dab（去噪）
            sub = prof[i:k]
            tsum = float(sub.sum())
            t_c = float((ts[i:k] * sub).sum() / tsum)
            dabs.append({"t": t_c, "x": x0 + ux * t_c, "y": y0 + uy * t_c,
                         "t_span": float(k - i), "area_prof": tsum})
        i = k
    return dabs


def an_analyze_lshape(work: Path, side: str, variants: list[dict]) -> dict:
    """L 形笔划分析：检出的每个孤立 dab 量二阶矩朝向（mod 180°）→
    每段朝向中位数 → 拐角前后角差（判 bVTy 5/6 行为）。

    148 修正（第一版逐段假设步长，测出假 90°：窗口里落进两个 dab）：
    改为「先按法向剖面检出真实 dab 位置，再逐 dab 量朝向」，并顺带记录
    检出步长（PS/Krita 的实际 dab 间距）作为 spacing 基准的实测证据。
    """
    subdir = "ps" if side == "ps" else "krita"
    out: dict = {"side": side, "variants": {}}
    for v in variants:
        vres = {"label": v["label"]}
        if side == "krita":
            vres["kpp_readback"] = v.get("readback")
        for tag, label, pts in AN_LSHAPES:
            img = work / "png" / subdir / ("%s_%s.png" % (v["tag"], tag))
            if not img.is_file():
                vres[tag] = {"missing": True}
                continue
            alpha = an_load_alpha(img)
            per_seg: dict[int, list[float]] = {}
            steps: dict[int, list[float]] = {}
            spans: dict[int, list[float]] = {}
            eccs: list[float] = []
            for si in range(len(pts) - 1):
                x0, y0 = pts[si]
                x1, y1 = pts[si + 1]
                seg_len = math.hypot(x1 - x0, y1 - y0)
                ux, uy = (x1 - x0) / seg_len, (y1 - y0) / seg_len
                dabs = an_profile_dabs(alpha, pts[si], pts[si + 1])
                if len(dabs) > 1:
                    steps[si] = list(np.diff([d["t"] for d in dabs]))
                spans[si] = [d["t_span"] for d in dabs]
                step_med = float(np.median(steps[si])) if steps.get(si) else None
                for d in dabs:
                    # 丢掉被 t 范围截断的首/尾 dab（质心与 t_span 会被切）
                    if (d["t"] - d["t_span"] / 2.0 < 40.0 - 1 or
                            d["t"] + d["t_span"] / 2.0 > seg_len - 40.0 + 1):
                        continue
                    half_t = (min(0.45 * step_med, d["t_span"] / 2.0 + 3.0)
                              if step_med else 60.0)
                    o = an_orientation(alpha, d["x"], d["y"], ux=ux, uy=uy,
                                       half_t=half_t, half_n=60.0)
                    if o is None or o["eccentricity"] < 0.35:
                        continue
                    per_seg.setdefault(si, []).append(o["angle_deg"])
                    eccs.append(o["eccentricity"])
            seg_stats = {}
            for si in sorted(set(list(per_seg.keys()) + list(spans.keys()))):
                vals = per_seg.get(si, [])
                # 朝向是 mod 180 的量：直接取中位数在 0/180 附近会被「代表值跳变」坑到
                # （实测 bVTy=0 的 dab 全在 179.99x°，中位数/四分位会假性散开）。
                # 用**倍角圆统计**：φ=2θ 求圆均值/圆标准差，再除以 2 回到 mod 180。
                stats180 = an_circular_mean_mod180(vals) if vals else {}
                seg_stats[si] = {
                    "n": len(vals),
                    "n_detected": len(spans.get(si, [])),
                    "mean180": stats180.get("mean_deg"),
                    "spread180": stats180.get("spread_deg"),
                    "r_bar180": stats180.get("r_bar"),
                    "median": float(np.median(vals)) if vals else None,
                    "step_px_median": float(np.median(steps[si])) if steps.get(si) else None,
                    "span_px_median": float(np.median(spans[si])) if spans.get(si) else None,
                }
            jump = None
            s0 = seg_stats.get(0) or {}
            s1 = seg_stats.get(1) or {}
            if s0.get("mean180") is not None and s1.get("mean180") is not None:
                jump = ((s1["mean180"] - s0["mean180"] + 90.0) % 180.0) - 90.0
            vres[tag] = {
                "segments": seg_stats,
                "corner_jump_deg": jump,
                "expected_init_dir_deg": math.degrees(
                    math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0])) % 180.0,
                "ecc_median": float(np.median(eccs)) if eccs else None,
                "n_dabs_detected": sum(x["n_detected"] for x in seg_stats.values()),
            }
        out["variants"][v["tag"]] = vres
    return out



# ---------------------------------------------------------------------------
# 148/149：manifest、汇总指标与 markdown
# ---------------------------------------------------------------------------


def an_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def an_write_manifest(work: Path, task: str, payload: dict,
                      inputs: dict[str, Path] | None = None,
                      outputs_glob: str = "**/*.png") -> dict:
    """证据落盘：任务/时间/命令/输入 sha256/输出 sha256/统计量。"""
    work.mkdir(parents=True, exist_ok=True)
    inputs = inputs or {}
    out_rows = {}
    out_dir = work / "png"
    if out_dir.is_dir():
        for p in sorted(out_dir.glob(outputs_glob)):
            out_rows[str(p.relative_to(work)).replace("\\", "/")] = {
                "sha256": an_sha(p), "bytes": p.stat().st_size}
    man = {
        "task": task,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": " ".join(sys.argv),
        "repo_head": _git_head(),
        "inputs": {str(Path(p).relative_to(REPO)).replace("\\", "/"): an_sha(Path(p))
                   for p in inputs.values() if Path(p).is_file()},
        "input_labels": {k: str(Path(v).relative_to(REPO)).replace("\\", "/")
                         for k, v in inputs.items() if Path(v).is_file()},
        "outputs": out_rows,
        "n_outputs": len(out_rows),
        "payload": payload,
    }
    path = work / "manifest.json"
    path.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    log("[manifest] %s（%d 个输出、%d 个输入）" % (path, len(out_rows), len(man["inputs"])))
    return man


def _git_head() -> str:
    try:
        proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO),
                              capture_output=True, timeout=20)
        return (proc.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:
        return ""


def an_write_artifacts_manifest(work: Path, task: str, payload: dict) -> dict:
    """把分析产物（metrics/validate/fix_candidate 等）的 sha256 也写进 manifest。"""
    rows = {}
    for p in sorted(work.glob("*")):
        if p.is_file() and p.suffix in (".json", ".md"):
            rows[p.name] = {"sha256": an_sha(p), "bytes": p.stat().st_size}
    man = {"task": task,
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "command": " ".join(sys.argv),
           "repo_head": _git_head(),
           "artifacts": rows,
           "payload": payload}
    path = work / ("manifest_%s.json" % task)
    path.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    log("[manifest] %s（%d 个分析产物）" % (path, len(rows)))
    return man


def an_angle_markdown(m: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("## 表 A：角度抖动（F-6 口径：主统计量 a_θ = 角半跨度，度）\n")
    add("理论值 a_θ = min(180°, 3.6°×j)（124 B 级：跨度 ≈ 7.2°/1%，j≥50 整圈饱和）；"
        "σ_θ = a_θ/√3。判据：两侧相对差 ≤10%、两次独立渲染相对差 ≤5%。\n")
    add("| j(%) | PS n | PS a_θ | Krita n | Krita a_θ | 相对差 | 判定(≤10%) | "
        "PS 复现(≤5%) | Krita 复现(≤5%) | PS KS | Krita KS | 理论 a_θ |")
    add("|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|")
    comp = m.get("comparison") or {}
    for key in sorted(comp, key=lambda s: int(s)):
        r = comp[key]
        if float(r["j"]) == 0.0:
            judge = "自检" if (r["ps_a_theta"] <= 5.0 and r["kt_a_theta"] <= 5.0) else "FAIL"
        else:
            judge = "PASS" if r["pass_10pct"] else "FAIL"
        add("| %s | %d | %.2f | %d | %.2f | %s | %s | %s | %s | %.3f | %.3f | %.1f |" % (
            key, r["ps_n"], r["ps_a_theta"], r["kt_n"], r["kt_a_theta"],
            ("%+.2f%%" % r["rel_pct"]) if r["rel_pct"] is not None else "—", judge,
            ("%.2f%%" % r["ps_repeat_pct"]) if r["ps_repeat_pct"] is not None else "—",
            ("%.2f%%" % r["kt_repeat_pct"]) if r["kt_repeat_pct"] is not None else "—",
            r["ps_ks"], r["kt_ks"], r["pred_a_theta"]))
    add("")
    add("## 表 B：逐配置圆统计量（σ_θ / R̄ / MAD / 极差，交叉校验用）\n")
    add("| 侧 | j | n | a_θ(°) | σ_θ(°) | R̄ | MAD(°) | 极差(°) | KS | NCC 均值/最小 |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for side in ("ps", "krita"):
        s = m.get(side) or {}
        for key in sorted(s.get("configs") or {}, key=lambda x: int(x)):
            r = s["configs"][key]
            if r.get("n", 0) == 0:
                continue
            add("| %s | %s | %d | %.2f | %.2f | %.4f | %.2f | %.2f | %.4f | %.3f/%.3f |" % (
                side.upper(), key, r["n"], r["a_theta_deg"], r["sigma_theta_deg"],
                r["r_bar"], r["mad_deg"], r["range_deg"], r["ks_uniform"],
                r["ncc_mean"] or 0.0, r["ncc_min"] or 0.0))
    add("")
    if m.get("saturation"):
        s = m["saturation"]
        add("## 表 C：B8 旋转饱和判定（149）\n")
        add("| j(%) | a_θ(°) | a_θ/180 | KS(整圈均匀) | 复现(%) |")
        add("|---:|---:|---:|---:|---:|")
        for key in sorted(s["rows"], key=lambda x: int(x)):
            r = s["rows"][key]
            add("| %s | %.2f | %.4f | %.4f | %s |" % (
                key, r["a_theta"], r["a_over_180"], r["ks_uniform"],
                ("%.2f%%" % r["repeat_pct"]) if r["repeat_pct"] is not None else "—"))
        add("")
        add("- 四档 a_θ 中位数 = %.2f°；最大相对差 = %.2f%%（判据 ≤10%%）" % (
            s["median_a_theta"], s["max_abs_rel_pct"]))
        add("- 判读：%s" % s["verdict"])
        add("")
    if m.get("fix_candidate"):
        fc = m["fix_candidate"]
        add("## 表 D：F-6 映射修法候选（RotationSensor 只留 fuzzy，去掉 fuzzystroke）\n")
        add("| 档 | PS a_θ(°) | 修法候选 Krita a_θ(°) | 相对差 | 判定(≤10%) | 修法前 Krita a_θ(°) | 修法前相对差 |")
        add("|---|---:|---:|---:|---|---:|---:|")
        before = (m.get("comparison") or {})
        rows_fc = fc.get("fix_rows") or {}
        ordered = (list(rows_fc.values()) if isinstance(rows_fc, list)
                   else [rows_fc[k] for k in sorted(rows_fc, key=lambda s: float(rows_fc[s]["j"]))])
        for row in ordered:
            j = row["j"]
            b = before.get(fmt_int(j)) or {}
            add("| j=%g | %s | %.2f | %s | %s | %s | %s |" % (
                j,
                ("%.2f" % row["ps_a_theta"]) if row.get("ps_a_theta") is not None else "—",
                row["krita_fix_a_theta"],
                ("%+.2f%%" % row["rel_pct"]) if row.get("rel_pct") is not None else "—",
                ("—（无 PS 对照）" if row.get("ps_a_theta") is None
                 else ("PASS" if row.get("pass_10pct") else "FAIL")),
                ("%.2f" % b["kt_a_theta"]) if b else "—",
                ("%+.2f%%" % b["rel_pct"]) if b.get("rel_pct") is not None else "—"))
        add("")
        add("- 口径：%s" % fc.get("note", ""))
        add("")
    return chr(10).join(lines)


def an_saturation_metrics(ps: dict, jitters: list[float]) -> dict:
    """149（B8）：PS 侧 j∈(50,100] 四档的 a_θ 是否饱和（彼此相同 + 整圈均匀）。"""
    rows = {}
    for j in jitters:
        key = fmt_int(j)
        st = (ps.get("configs") or {}).get(key)
        if not st or st.get("n", 0) == 0:
            continue
        rows[key] = {
            "a_theta": float(st["a_theta_deg"]),
            "a_over_180": float(st["a_theta_deg"]) / 180.0,
            "ks_uniform": float(st["ks_uniform"]),
            "r_bar": float(st["r_bar"]),
            "sigma": float(st["sigma_theta_deg"]),
            "n": int(st["n"]),
            "repeat_pct": (ps.get("repeats", {}).get(key) or {}).get("rel_diff_pct"),
        }
    if not rows:
        return {}
    vals = [r["a_theta"] for r in rows.values()]
    med = float(np.median(vals))
    max_rel = max(abs((v - med) / med * 100.0) for v in vals) if med > 0 else None
    saturated = bool(max_rel is not None and max_rel <= 10.0
                     and all(r["a_over_180"] >= 0.90 for r in rows.values())
                     and all(r["ks_uniform"] <= 0.12 for r in rows.values()))
    if saturated:
        verdict = ("饱和：四档 a_θ 互相最大相对差 %.2f%%（≤10%%）、均 ≥0.90×180°、"
                   "对整圈均匀的 KS ≤0.12 ⇒ j≥50 已铺满整圈，"
                   "Krita 维持 RotationValue=min(1,j/50) 成立" % max_rel)
    else:
        verdict = ("未达饱和判据：a_θ 相对差 %.2f%% / 最小 a_θ 占比 %.3f / 最大 KS %.3f"
                   "（判据：相对差≤10%%、占比≥0.90、KS≤0.12）"
                   % (max_rel if max_rel is not None else -1.0,
                      min(r["a_over_180"] for r in rows.values()),
                      max(r["ks_uniform"] for r in rows.values())))
    return {"rows": rows, "median_a_theta": med, "max_abs_rel_pct": max_rel,
            "saturated": saturated, "verdict": verdict}


def an_lshape_verdict(vres: dict) -> dict:
    """单变体的 F-7 判读：拐角前/后角差 + 首段绝对朝向 vs 初始段方向/0°。"""
    jumps = []
    seg0 = []
    init_dirs = []
    for tag in ("HV", "VH", "DD"):
        r = (vres or {}).get(tag) or {}
        if r.get("missing") or not r.get("segments"):
            continue
        segs = r["segments"]
        # segments 的键在内存里是 int 0/1，JSON 往返后变字符串 → 两种都认
        s0d = segs.get(0) if segs.get(0) is not None else segs.get("0")
        s1d = segs.get(1) if segs.get(1) is not None else segs.get("1")
        n0 = (s0d or {}).get("n", 0) or 0
        n1 = (s1d or {}).get("n", 0) or 0
        if min(n0, n1) < 5:
            # 段内有效 dab 不足 5 个（Krita 侧 DD 段与相邻段在走廊投影上粘连）→ 不采信
            continue
        if r.get("corner_jump_deg") is not None:
            jumps.append(float(r["corner_jump_deg"]))
        segs2 = r.get("segments") or {}
        s0 = segs2.get(0) if segs2.get(0) is not None else (segs2.get("0") or {})
        if s0.get("mean180") is not None:
            seg0.append(float(s0["mean180"]))
        if r.get("expected_init_dir_deg") is not None:
            init_dirs.append(float(r["expected_init_dir_deg"]))
    out = {
        "n_shapes": min(len(jumps), len(seg0)),
        "corner_jumps_deg": jumps,
        "max_abs_jump_deg": max((abs(v) for v in jumps), default=None),
        "all_jump_gt_45": bool(jumps) and all(abs(v) > 45.0 for v in jumps),
        "all_jump_le_5": bool(jumps) and all(abs(v) <= 5.0 for v in jumps),
        "seg0_medians_deg": seg0,
        "init_dirs_deg": init_dirs,
        "seg0_vs_init_deg": [(((s - d + 90.0) % 180.0) - 90.0) for s, d in zip(seg0, init_dirs)],
        "seg0_vs_zero_deg": [(((s + 90.0) % 180.0) - 90.0) for s in seg0],
    }
    if out["all_jump_gt_45"]:
        out["verdict"] = "每段跟随当前笔划方向（拐角前后角差 >45°）"
    elif out["all_jump_le_5"]:
        near_init = all(abs(v) <= 5.0 for v in out["seg0_vs_init_deg"]) if out["seg0_vs_init_deg"] else False
        near_zero = all(abs(v) <= 5.0 for v in out["seg0_vs_zero_deg"]) if out["seg0_vs_zero_deg"] else False
        if near_init and not near_zero:
            out["verdict"] = "整笔固定 = 初始段方向（Initial Direction 语义成立）"
        elif near_zero and not near_init:
            out["verdict"] = "整笔固定 = 基准角 0°（不随初始段方向）"
        elif near_init and near_zero:
            out["verdict"] = "整笔固定（三种起始方向都≈基准角，无法区分初始方向）"
        else:
            out["verdict"] = "整笔固定（角差 ≤5°）"
    elif out["max_abs_jump_deg"] is not None:
        out["verdict"] = "拐角前后角差 %s°（既非 >45° 也非 ≤5°）" % (
            "/".join("%.1f" % v for v in out["corner_jumps_deg"]))
    else:
        out["verdict"] = "样本不足"
    return out


def an_lshape_markdown(m: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("## 表 A：bVTy 5/6/7/8 的 L 形笔划（每段 dab 朝向中位数，mod 180°）\n")
    add("判据（126 F-7）：拐角前后角差 >45° ⇒ 6=当前笔划方向；≤5° ⇒ 整笔固定。\n")
    add("| 侧/变体 | 形状 | 段0 朝向(° mod180) | 段1 朝向(° mod180) | 拐角角差 | 段内圆标准差(段0/段1) | dab 数 |")
    add("|---|---|---:|---:|---:|---|---:|")
    for side in ("ps", "krita"):
        s = m.get(side) or {}
        for tag, vres in (s.get("variants") or {}).items():
            for shape in ("HV", "VH", "DD"):
                r = (vres or {}).get(shape) or {}
                if r.get("missing") or not r.get("segments"):
                    continue
                seg0 = (r["segments"].get(0) or {})
                seg1 = (r["segments"].get(1) or {})
                add("| %s | %s | %s | %s | %s | %s/%s | %d/%d |" % (
                    tag, shape,
                    ("%.1f" % seg0.get("mean180")) if seg0.get("mean180") is not None else "—",
                    ("%.1f" % seg1.get("mean180")) if seg1.get("mean180") is not None else "—",
                    ("%+.1f" % r["corner_jump_deg"]) if r.get("corner_jump_deg") is not None else "—",
                    ("%.1f" % seg0.get("spread180")) if seg0.get("spread180") is not None else "—",
                    ("%.1f" % seg1.get("spread180")) if seg1.get("spread180") is not None else "—",
                    seg0.get("n", 0), seg1.get("n", 0)))
    add("")
    add("## 表 B：判读（自动阈值：>45° = 跟随方向；≤5° = 整笔固定）\n")
    add("| 侧/变体 | 判读 | 拐角角差(°) | 段0 中位 vs 初始段方向(°) | PS 对照 |")
    add("|---|---|---|---|---|")
    ps_j = m.get("ps_judge") or {}
    for side in ("ps", "krita"):
        s = m.get("judge", {}).get(side) or {}
        for tag, verdict in s.items():
            vres = ((m.get(side) or {}).get("variants") or {}).get(tag) or {}
            v = verdict
            ps_tag = tag.replace("kt_", "ps_") if side == "krita" else tag
            ps_row = ps_j.get(ps_tag) or {}
            add("| %s | %s | %s | %s | %s |" % (
                tag, v.get("verdict", "—"),
                "/".join("%.1f" % x for x in v.get("corner_jumps_deg", [])) or "—",
                "/".join("%.1f" % x for x in v.get("seg0_vs_init_deg", [])) or "—",
                ps_row.get("verdict", "—")))
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 148/149 编排
# ---------------------------------------------------------------------------


def do_angle(work: Path, side: str, jitters: list[float], repeats: int,
             force: bool) -> dict:
    """F-6/149：出图（按 side）→ 落 manifest。"""
    render = an_render_angle(work, side, jitters, repeats, force)
    inputs = {"asym_abr": AN_ASYM_ABR}
    for key in sorted((work / "kpp").glob("*.kpp")) if (work / "kpp").is_dir() else []:
        inputs["kpp_" + key.name] = key
    task = "149-B8" if jitters and min(jitters) >= 50.0 else "148-F6"
    an_write_manifest(work, task, {"render": render}, inputs)
    return render


def do_angle_metric(work: Path, jitters: list[float], repeats: int,
                    tag: str = "148-F6") -> dict:
    ps = an_analyze_angle_side(work, "ps", jitters, repeats)
    krita = None
    if (work / "png" / "krita" / "j0_r0.png").is_file():
        krita = an_analyze_angle_side(work, "krita", jitters, repeats)
    m = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tag": tag,
        "geometry": {"diameter": AN_D, "spacing_pct": AN_SPACING_PCT,
                     "grid": AN_GRID, "pitch": AN_PITCH, "patch": AN_PATCH,
                     "coarse_deg": AN_COARSE, "fine_deg": AN_FINE},
        "jitters": jitters, "repeats": repeats,
        "statistic": ("a_theta = (q97.5(delta)-q2.5(delta))/2/0.95；"
                      "delta=((theta-base+180) mod 360)-180"),
        "ps": ps,
        "krita": krita or {"configs": {}},
    }
    if krita is not None:
        m["comparison"] = an_compare_angle(ps, krita, jitters)
    fix_path = work / "fix_candidate.json"
    if fix_path.is_file():
        # F-6 映射修法候选（fuzzy_only）的实测对照行，由 fix_candidate_analysis 生成
        try:
            m["fix_candidate"] = json.loads(fix_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log("[fix_candidate] 读取失败：%r" % (exc,))
    if min(jitters) >= 50.0:
        m["saturation"] = an_saturation_metrics(ps, jitters)
    (work / "angle_metrics.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    md = an_angle_markdown(m)
    (work / "angle_metrics.md").write_text(md, encoding="utf-8")
    print(md)
    an_write_artifacts_manifest(work, tag, {
        "comparison": m.get("comparison"),
        "saturation": m.get("saturation"),
        "statistic": m.get("statistic"),
    })
    return m


def do_lshape(work: Path, force: bool) -> dict:
    render = an_render_lshape(work, force)
    inputs = {"asym_abr": AN_ASYM_ABR}
    for b in (5, 6, 7, 8):
        inputs["bvty%d_abr" % b] = AN_BVTY_ABR[b]
    for p in sorted((work / "kpp").glob("*.kpp")) if (work / "kpp").is_dir() else []:
        inputs["kpp_" + p.name] = p
    def _clean(o):
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(v) for v in o]
        return o
    an_write_manifest(work, "148-F7-B6", _clean({"render": render}), inputs)
    return render


def do_lshape_metric(work: Path) -> dict:
    ps_v = an_lshape_ps_variants()
    kt_v = an_lshape_krita_variants(work)
    ps = an_analyze_lshape(work, "ps", ps_v)
    kt = an_analyze_lshape(work, "krita", kt_v)
    m = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tag": "148-F7-B6",
        "lshapes": [{"tag": t, "label": bl, "points": p} for t, bl, p in AN_LSHAPES],
        "dab_step_px": AN_LSTEP,
        "ps": ps, "krita": kt,
    }
    m["ps_judge"] = {tag: an_lshape_verdict(v) for tag, v in ps["variants"].items()}
    m["judge"] = {"ps": m["ps_judge"],
                  "krita": {tag: an_lshape_verdict(v) for tag, v in kt["variants"].items()}}
    (work / "lshape_metrics.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    md = an_lshape_markdown(m)
    (work / "lshape_metrics.md").write_text(md, encoding="utf-8")
    print(md)
    an_write_artifacts_manifest(work, "148-F7-B6", {"judge": m.get("judge")})
    return m


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def do_samples(work: Path, side: str, candidates: list[str], quick: bool,
               force: bool, max_jobs: int = 0) -> dict:
    jitters = [0.0, 100.0] if quick else list(JITTERS)
    strokes = 2 if quick else STROKES_PER_VALUE
    if quick and "fit" in candidates:
        candidates = [c for c in candidates if c != "fit"]
    result: dict = {"jitters": jitters, "strokes": strokes, "candidates": candidates}
    fit_params = None
    if side in ("ps", "both"):
        need_files = [(work / "png" / "ps" / ("j%s_%02d.png" % (fmt_int(j), i)))
                      for j in jitters for i in range(strokes)]
        if RERENDER_JITTER in jitters:
            need_files += [(work / "png" / "ps_r2" / ("j%s_%02d.png" % (fmt_int(RERENDER_JITTER), i)))
                           for i in range(strokes)]
        ps_metrics = ps_configs(work, jitters, strokes) if all(p.is_file() for p in need_files) else None
        # 先出 PS（fit 候选需要 PS 数字）
        if not ps_metrics:
            result["ps_render"] = render_ps_side(work, jitters, strokes, force, max_jobs)
        ps_metrics = ps_configs(work, jitters, strokes)
        if all(fmt_int(j) in ps_metrics for j in jitters) and "100" in ps_metrics and "300" in ps_metrics:
            fit_params = fit_params_from_ps(ps_metrics)
        result["ps_configs"] = {k: {kk: vv for kk, vv in v.items()
                                    if kk in ("a_range_D", "a_maxabs_D", "a_mean_D", "n")}
                                for k, v in ps_metrics.items()}
    if side in ("krita", "both"):
        if fit_params is None and "fit" in candidates:
            # fit 候选的 a100/a300 必须由 PS 实测推出来（先 --side ps 出图）
            # fit 只需要 j=100 / j=300 两个锚点（a100/a300），其余点由拟合式插值
            need_ps = [(work / "png" / "ps" / ("j%s_%02d.png" % (fmt_int(j), i)))
                       for j in (100.0, 300.0) for i in range(strokes)]
            if not all(p.is_file() for p in need_ps):
                raise SystemExit("fit 候选需要 PS 侧 j=100/300 实测数字（先 --samples --side ps）")
            fit_params = fit_params_from_ps(ps_configs(work, jitters, strokes))
        result["krita_render"] = render_krita_side(work, jitters, strokes, candidates,
                                                   fit_params, force)
        result["krita_presets"] = {}
        for cand in candidates:
            for j in jitters:
                p = work / "kpp" / kpp_name(cand, j)
                if p.is_file():
                    result["krita_presets"]["%s@%d" % (cand, int(j))] = (
                        candidate_value(cand, j, fit_params))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="任务 129：散布分布实验（统计量 + 映射 A/B）")
    parser.add_argument("--samples", action="store_true", help="生成 PS/Krita 样本（串行 + 锁）")
    parser.add_argument("--metric", action="store_true", help="重新计算并打印全部指标")
    parser.add_argument("--all", action="store_true", help="= --samples + --metric")
    parser.add_argument("--side", choices=["ps", "krita", "both"], default="both",
                        help="--samples 只跑哪一侧（默认 both）")
    parser.add_argument("--candidates", default="div400,div200,fit",
                        help="逗号分隔的候选映射（默认 div400,div200,fit）")
    parser.add_argument("--quick", action="store_true", help="冒烟：2 个散布值 × 2 笔，不写进正式结论")
    parser.add_argument("--force", action="store_true", help="已存在也重渲")
    parser.add_argument("--max-jobs", type=int, default=0,
                        help="单次 PS 会话最多出多少张（0=全部；用于被外部打断时小步恢复）")
    parser.add_argument("--workdir", default=str(REPO / ".tmp" / "129"))
    # ---- 任务 148/149：角度抖动（F-6）、方向动态（F-7/B6）、旋转饱和（B8）----
    parser.add_argument("--angle-samples", action="store_true",
                        help="148/149：角度抖动出图（孤立 dab 网格，PS/Krita 串行 + 锁）")
    parser.add_argument("--angle-metric", action="store_true",
                        help="148/149：重算角度统计量（模板匹配 + 圆统计）")
    parser.add_argument("--angle-all", action="store_true", help="= --angle-samples + --angle-metric")
    parser.add_argument("--angle-validate", action="store_true",
                        help="在已知角/已知分布合成图上校验量测链（写 validate.json）")
    parser.add_argument("--angle-out", default=str(REPO / ".tmp" / "random" / "148"),
                        help="角度实验证据目录（148 用 .tmp/random/148；149 用 .tmp/random/149）")
    parser.add_argument("--angle-jitters", default="0,25,50,100",
                        help="角度抖动档位（148: 0,25,50,100；149: 50,60,80,100）")
    parser.add_argument("--angle-repeats", type=int, default=AN_REPEATS,
                        help="两次独立渲染的重复数（默认 2）")
    parser.add_argument("--angle-side", choices=["ps", "krita", "both"], default="both",
                        help="角度实验出图只跑哪一侧")
    parser.add_argument("--lshape-samples", action="store_true",
                        help="148 F-7/B6：bVTy=5/6/7/8 的 L 形笔划出图（PS + Krita）")
    parser.add_argument("--lshape-metric", action="store_true",
                        help="148 F-7/B6：重算 L 形笔划的逐段朝向与拐角角差")
    parser.add_argument("--lshape-all", action="store_true", help="= --lshape-samples + --lshape-metric")
    parser.add_argument("--lshape-out", default=str(REPO / ".tmp" / "random" / "148"),
                        help="F-7/B6 证据目录")
    ns = parser.parse_args(argv)
    work = Path(ns.workdir)
    work.mkdir(parents=True, exist_ok=True)

    # ---- 148/149 入口（与 129 的散布流程完全分开；不传这些开关时行为不变）----
    angle_mode = bool(ns.angle_samples or ns.angle_metric or ns.angle_all or ns.angle_validate)
    lshape_mode = bool(ns.lshape_samples or ns.lshape_metric or ns.lshape_all)
    if angle_mode or lshape_mode:
        rc = 0
        if angle_mode:
            aw = Path(ns.angle_out).resolve()      # 必须绝对路径：PS 的 cwd 不是仓库
            aw.mkdir(parents=True, exist_ok=True)
            jitters = [float(x) for x in str(ns.angle_jitters).split(",") if x.strip()]
            log("[角度] 目录=%s 档位=%r 重复=%d 侧=%s"
                % (aw, jitters, ns.angle_repeats, ns.angle_side))
            if ns.angle_all or ns.angle_samples:
                do_angle(aw, ns.angle_side, jitters, ns.angle_repeats, ns.force)
            if ns.angle_all or ns.angle_metric:
                tag = "149-B8" if (jitters and min(jitters) >= 50.0) else "148-F6"
                do_angle_metric(aw, jitters, ns.angle_repeats, tag=tag)
            if ns.angle_validate:
                v = an_validate(aw)
                print(json.dumps({"V1": v["V1_template_autocorr"],
                                  "V2": {k: r["max_abs_err_deg"] for k, r in v["V2_known_rotation"].items()},
                                  "V3": {k: r["max_abs_bias_pct"] for k, r in v["V3_known_distribution"].items()}},
                                 ensure_ascii=False, indent=1))
        if lshape_mode:
            lw = Path(ns.lshape_out).resolve()     # 必须绝对路径：PS 的 cwd 不是仓库
            lw.mkdir(parents=True, exist_ok=True)
            log("[F-7/B6] 目录=%s" % lw)
            if ns.lshape_all or ns.lshape_samples:
                do_lshape(lw, ns.force)
            if ns.lshape_all or ns.lshape_metric:
                do_lshape_metric(lw)
        return rc
    candidates = [c.strip() for c in ns.candidates.split(",") if c.strip() in CANDIDATES]
    if not candidates:
        raise SystemExit("--candidates 至少给一个：%r" % (CANDIDATES,))
    rc = 0
    if ns.all or ns.samples:
        payload = do_samples(work, ns.side, candidates, ns.quick, ns.force, ns.max_jobs)
        (work / "samples.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
        log("[样本] 完成：%s" % json.dumps({k: v for k, v in payload.items()
                                           if k in ("jitters", "strokes", "candidates")},
                                          ensure_ascii=False))
    if ns.all or ns.metric or not (ns.samples or ns.all):
        if ns.quick:
            candidates = [c for c in candidates if c != "fit"]
        jitters = [0.0, 100.0] if ns.quick else list(JITTERS)
        strokes = 2 if ns.quick else STROKES_PER_VALUE
        ps = ps_configs(work, jitters, strokes)
        fit_params = None
        if "100" in ps and "300" in ps:
            fit_params = fit_params_from_ps(ps)
        m = analyze_all(work, jitters, strokes, candidates, fit_params)
        (work / "metrics.json").write_text(json.dumps(m, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
        md = metrics_markdown(m)
        (work / "metrics.md").write_text(md, encoding="utf-8")
        print(md)
        if m["two_render"]:
            print("\n两次渲染相对差：")
            for side, t in m["two_render"].items():
                print("  %-5s a_range: %.4f vs %.4f → %.2f%%；a_maxabs: %.2f%%；band_height: %.2f%%"
                      % (side, t["main_a_range_D"], t["r2_a_range_D"], t["rel_diff_pct"],
                         t["mle_rel_diff_pct"], t.get("band_height_rel_diff_pct", float("nan"))))
        for cand in candidates:
            c = m["comparison"].get(cand)
            if not c:
                continue
            print("候选 %-7s：最大|相对差|=%s 超标=%d/%d Krita重复极差max=%s"
                  % (cand,
                     ("%.2f%%" % c["max_abs_rel_pct"]) if c["max_abs_rel_pct"] is not None else "—",
                     c["exceed_count"], c["nonzero_points"],
                     ("%.2f%%" % c["max_kt_repeat_range_pct"]) if c["max_kt_repeat_range_pct"] is not None else "—"))
        if ns.metric and not (ns.samples or ns.all):
            rc = 0
        log("[指标] 已写 %s 与 %s" % (work / "metrics.json", work / "metrics.md"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
