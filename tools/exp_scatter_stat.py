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


def run_ps(rows: list[dict], work: Path, label: str = "ps") -> dict:
    """一个 PS 会话跑完 rows；要求此时没有别的 Photoshop 进程（持 ps.lock）。"""
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
    jobs = ps_jobs(rows)
    for row in rows:
        Path(row["out"]).parent.mkdir(parents=True, exist_ok=True)
    job_path.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False, indent=1), encoding="utf-8")
    jsx_path.write_text(PS_JSX, encoding="utf-8-sig")   # ExtendScript 需 BOM 才按 UTF-8 解析

    env = os.environ.copy()
    env["BC129_WORK"] = str(work)
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
        if len(parts) >= 6:
            rows_out.append(dict(zip(("file", "ok", "error", "diameter", "spacing", "jitter"), parts)))
    ok_n = sum(1 for r in rows_out if r["ok"] == "true")
    log("[PS] 完成 %s：%d/%d 张，%.1fs，PS=%s" % (label, ok_n, len(jobs), elapsed, ps_version))
    (work / ("done_%s.tsv" % label)).write_text(text, encoding="utf-8")
    return {"rows": rows_out, "ok": ok_n, "total": len(jobs),
            "ps_version": ps_version, "elapsed_s": round(elapsed, 1)}

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


def _install_plugin(paths: dict) -> tuple[str, str]:
    plugin_dir = os.path.join(paths["pykrita"], PLUGIN_NAME)
    os.makedirs(plugin_dir, exist_ok=True)
    with open(os.path.join(plugin_dir, "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write(PLUGIN_SRC)
    desktop = os.path.join(paths["pykrita"], PLUGIN_NAME + ".desktop")
    with open(desktop, "w", encoding="utf-8") as fh:
        fh.write("[Desktop Entry]\nType=Service\nServiceTypes=Krita/PythonPlugin\n"
                 "X-KDE-Library=%s\nX-Python-2-Compatible=false\nName=BC129 Batch\n" % PLUGIN_NAME)
    return plugin_dir, desktop


def _patch_kritarc(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        original = fh.read()
    patched = original
    if "[python]" in patched:
        patched = patched.replace("[python]\n", "[python]\nenable_%s=true\n" % PLUGIN_NAME, 1)
    else:
        patched += "\n[python]\nenable_%s=true\n" % PLUGIN_NAME
    import re
    patched = re.sub(r"(?m)^sessionOnStartup=\d+$", "sessionOnStartup=2", patched)
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
            _kill_pids(extra)
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
        if low.startswith("bc") and ("auto_render" in low or low.startswith("bc129") or low.startswith("bc_")):
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


def with_krita_session(fn):
    """Krita 单实例会话：抢到锁后 → 等无缝 Krita → 清别家残留 → 装本批插件 → 一次启动批量渲完
    → 先杀本批 Krita 并等完全退出 → 再还原 kritarc/resourcecache → 核对输出。
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
            _patch_kritarc(paths["kritarc"])
        plugin_dir, desktop = _install_plugin(paths)
        os.makedirs(paths["presets"], exist_ok=True)
        result = fn(paths, installed)
        verify = _kritarc_verify(paths)
        return {"result": result, "verify": verify, "plugins_removed": plugins_removed}
    finally:
        # 1) 先杀本批残留 Krita，并等进程真正退出（Krita 退出时会回写 kritarc）
        extra = _krita_pids()
        if extra:
            log("[清理] 杀掉本批残留 Krita pid=%r" % (sorted(extra),))
            _kill_pids(extra)
        if not _wait_no_krita_soft(90.0):
            log("[清理] 警告：90s 内仍有 Krita 进程 %r" % (sorted(_krita_pids()),))
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
    ns = parser.parse_args(argv)
    work = Path(ns.workdir)
    work.mkdir(parents=True, exist_ok=True)
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
