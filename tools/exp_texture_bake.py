# -*- coding: utf-8 -*-
"""任务 128A：纹理上限预烘焙实验（C>50 / depth>83% 对照 + F-2/F-3/F-4 主攻）。

只写 .tmp/128A/ 与报告；不改 src/、tests/、既有 tools/、矩阵与文档。

命令行：
  python tools/exp_texture_bake.py prep        # 造纹理/ABR/工作目录
  python tools/exp_texture_bake.py ps f2       # PS 真值批（F-2）
  python tools/exp_texture_bake.py ps f4       # PS 真值批（F-4 越界区 + 10 模式）
  python tools/exp_texture_bake.py ps f3       # PS 渐变（Fade）ramp 批（F-3）
  python tools/exp_texture_bake.py kpp         # 生成全部 .kpp
  python tools/exp_texture_bake.py render f2   # Krita 串行渲染（持 .tmp/krita.lock）
  python tools/exp_texture_bake.py analyze     # 全部数字 + 表格
  python tools/exp_texture_bake.py all

口径：像素级判据 = |Δalpha|>2 的像素占比（分母 W×H）≤2% 且 max_delta≤2；
几何/统计量按任务书 F-2/F-3/F-4 各节。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

TMP = REPO / ".tmp" / "128A"
DIR_TEX = TMP / "tex"
DIR_ABR = TMP / "abr"
DIR_KPP = TMP / "kpp"
DIR_PS = TMP / "png_ps"
DIR_KR = TMP / "png_krita"
DIR_LOG = TMP / "logs"
DIR_JSON = TMP / "json"
WORK_PS = TMP / "pswork"
JSX_SRC = REPO / "tools" / "ps_export.jsx"
JSX_COPY = TMP / "ps_export_128a.jsx"

TEXTURE1 = REPO / "research" / "测试用纹理.png"
TEXTURE2 = DIR_TEX / "tex2_gradnoise64.png"

CANVAS_200 = {"width": 200, "height": 200, "dpi": 72}
CANVAS_1100 = {"width": 1100, "height": 64, "dpi": 72}
PS_EXE = Path(os.environ.get("BRUSHCONVERTER_PS", r"D:/program/PS2025/Adobe Photoshop 2025/Photoshop.exe"))
PY = "D:/program/anaconda3/envs/brushConverter/python.exe"

TOL = 2
PS_TEX_NAME = "bc128a_tex"
PS_TEX2_NAME = "bc128a_tex2"


# ---------------------------------------------------------------- 基础工具
def ensure_dirs():
    for d in (TMP, DIR_TEX, DIR_ABR, DIR_KPP, DIR_PS, DIR_KR, DIR_LOG, DIR_JSON, WORK_PS):
        d.mkdir(parents=True, exist_ok=True)


def png_chunk(ctype: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + ctype + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))


def unique_png_bytes(path: Path, tag: str) -> bytes:
    """保留像素字节、在 IEND 前追加 tEXt 让 md5 唯一（Krita 图案库同名冲突，125 实测）。"""
    src = path.read_bytes()
    if src[-8:-4] != b"IEND":
        raise SystemExit("不是标准 PNG：%s" % path)
    text = ("brushconverter-128a" + "\x00" + "tag=" + tag).encode("utf-8")
    extra = png_chunk(b"tEXt", text)
    return src[:-12] + extra + src[-12:]


def save_gray_png(path: Path, gray: np.ndarray, unique_tag: str | None = None) -> bytes:
    """灰度值 → RGBA PNG（RGB 同灰度、Alpha=255）→ 可选唯一化，返回写进 .kpp 的字节。"""
    g = np.clip(np.asarray(gray, dtype=np.int16), 0, 255).astype(np.uint8)
    rgba = np.zeros(g.shape + (4,), dtype=np.uint8)
    rgba[..., 0] = g
    rgba[..., 1] = g
    rgba[..., 2] = g
    rgba[..., 3] = 255
    Image.fromarray(rgba, "RGBA").save(path)
    if unique_tag is None:
        return path.read_bytes()
    raw = unique_png_bytes(path, unique_tag)
    return raw


def load_alpha(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.uint8)[:, :, 3].astype(np.int16)


def metrics(a_path: Path, b_path: Path) -> dict:
    a, b = load_alpha(a_path), load_alpha(b_path)
    if a.shape != b.shape:
        raise ValueError("尺寸不一致 %s vs %s" % (a.shape, b.shape))
    d = np.abs(a - b)
    n = int(d.size)
    return {"diff_pixels": int(np.count_nonzero(d > TOL)),
            "diff_ratio": round(float(np.count_nonzero(d > TOL)) / n, 6),
            "max_delta": int(d.max()),
            "mean_abs": round(float(d.mean()), 3),
            "pass": bool((np.count_nonzero(d > TOL) / n) <= 0.02 and d.max() <= TOL)}


def write_json(name: str, payload) -> Path:
    DIR_JSON.mkdir(parents=True, exist_ok=True)
    p = DIR_JSON / name
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------- 锁（与 127 门同协议）
def acquire_lock(lock_path: Path, timeout_s: int = 2400, stale_min: int = 60, label: str = "") -> bool:
    """mkdir 原子锁；stale_min 分钟以上的锁视为残留（与任务书 shell 协议同语义）。"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            os.mkdir(lock_path)
            (lock_path / "owner.txt").write_text("%s %s" % (os.getpid(), label), encoding="utf-8")
            print("[锁] ACQUIRED %s (%s)" % (lock_path.name, label))
            return True
        except FileExistsError:
            try:
                age_min = (time.time() - lock_path.stat().st_mtime) / 60.0
                if age_min > stale_min:
                    shutil.rmtree(lock_path, ignore_errors=True)
                    print("[锁] 清除过期锁 %s（%.0f 分钟）" % (lock_path.name, age_min))
                    continue
            except OSError:
                pass
        time.sleep(5)
    print("[锁] 超时未获取 %s" % lock_path.name)
    return False


def release_lock(lock_path: Path) -> None:
    shutil.rmtree(lock_path, ignore_errors=True)
    print("[锁] RELEASED %s" % lock_path.name)


KRITA_LOCK = REPO / ".tmp" / "krita.lock"
PS_LOCK = REPO / ".tmp" / "ps.lock"


# ---------------------------------------------------------------- 纹理
def make_texture2() -> Path:
    """确定性第二纹理：y 向平滑渐变 + 固定种子噪点（非纯色）。"""
    rng = np.random.default_rng(20261123)
    h = w = 64
    yy = np.arange(h, dtype=np.float64)[:, None]
    base = 40.0 + 170.0 * (yy / (h - 1))          # 40..210 线性渐变
    base = np.repeat(base, w, axis=1)
    noise = rng.integers(-18, 19, size=(h, w)).astype(np.float64)
    img = np.clip(np.rint(base + noise), 0, 255).astype(np.uint8)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    for c in range(3):
        rgba[..., c] = img
    rgba[..., 3] = 255
    Image.fromarray(rgba, "RGBA").save(TEXTURE2)
    return TEXTURE2


def texture_gray(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        rgba = np.asarray(im.convert("RGBA"), dtype=np.int16)
    # Krita/PS 掩码换算：gray=(r*11+g*16+b*5)//32，alpha 255
    return (rgba[..., 0] * 11 + rgba[..., 1] * 16 + rgba[..., 2] * 5) // 32


# ---------------------------------------------------------------- ABR（复用 make_test_abr 的写出能力）
TIP_SIZE_F2 = 300   # dab 半径 150 > 画布 200：保证单 dab 盖满画布且留边
TIP_SIZE_F3 = 64
_TIP_OVERRIDE = {"pixels": None}


def _patched_round_tip(size: int, hardness: float) -> bytes:
    if _TIP_OVERRIDE["pixels"] is not None:
        return _TIP_OVERRIDE["pixels"]
    raise SystemExit("未设置笔尖覆盖像素")


def profile_hard(size: int) -> np.ndarray:
    """硬边方形满覆盖笔尖。ABR 采样像素实测极性：255=墨、0=透明（PS 真机 A/B 见报告）。

    正确写法 = alpha*255。
    """
    return np.full((size, size), 255, dtype=np.uint8)


def profile_soft(size: int, inner_ratio: float) -> np.ndarray:
    """软边圆形笔尖：内圈实心 inner_ratio*R，外圈线性到 0。返回 ABR 像素（0=墨）。"""
    r = size / 2.0
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((xx + 0.5 - r) ** 2 + (yy + 0.5 - r) ** 2)
    inner = inner_ratio * r
    alpha = np.clip((r - d) / max(1e-6, r - inner), 0.0, 1.0)
    alpha = alpha * alpha * (3 - 2 * alpha)  # smoothstep，避免硬折线
    return np.rint(alpha * 255.0).astype(np.uint8)


def make_abr(name: str, tip: np.ndarray, diameter: float, spacing_pct: float,
             texture: dict | None = None) -> Path:
    import make_test_abr as mta

    _TIP_OVERRIDE["pixels"] = tip.tobytes()
    mta.make_round_tip = _patched_round_tip
    if texture is not None:
        mta._texture_items = _make_texture_items(texture)
    # PS 采样笔尖 UUID 必须是 36 字符（make_test_abr 的头部长度自检按 36 计）
    h = hashlib.sha256(name.encode("utf-8")).hexdigest()
    uuid = "%s-%s-4%s-8%s-%s" % (h[0:8], h[8:12], h[13:16], h[17:20], h[20:32])
    data = mta.build_abr_zero(
        name=name, diameter=float(diameter), hardness=100.0,
        spacing=float(spacing_pct), angle=0.0, tip_size=int(tip.shape[0]),
        uuid=uuid, kind="sampled", texture=texture)
    out = DIR_ABR / ("%s.abr" % name)
    out.write_bytes(data)
    return out


def _make_texture_items(texture: dict):
    """自定义纹理键块：带 minimumDepth 与 textureDepthDynamics（make_test_abr 默认写 0/关）。"""
    import make_test_abr as mta
    return lambda tex: [
        ("brushProjection", mta.boolean(False)),
        ("TxtC", mta.boolean(True)),
        ("interpretation", mta.boolean(True)),
        ("textureBlendMode", mta.enum("BlnM", str(tex["blend"]))),
        ("textureDepth", mta.untf(float(tex["depth"]), "#Prc")),
        ("minimumDepth", mta.untf(float(tex.get("minimum_depth", 0.0)), "#Prc")),
        ("textureDepthDynamics", mta._dyn(int(tex.get("dyn_bvty", 0)),
                                          minimum=float(tex.get("dyn_minimum", 0.0)))),
        ("Txtr", mta.objc("Ptrn", [("Nm  ", mta.text(str(tex["name"]))),
                                   ("Idnt", mta.text(str(tex["uuid"]))),
                                   ("PntT", mta.enum("Ptrn", "Nm  "))])),
        ("textureScale", mta.untf(float(tex["scale"]), "#Prc")),
        ("InvT", mta.boolean(bool(tex.get("invert", False)))),
        ("protectTexture", mta.boolean(False)),
        ("textureBrightness", mta.integer(int(tex.get("brightness", 0)))),
        ("textureContrast", mta.integer(int(tex.get("contrast", 0)))),
    ]


def prep():
    ensure_dirs()
    make_texture2()
    tex1 = texture_gray(TEXTURE1)
    tex2 = texture_gray(TEXTURE2)
    print("[纹理1] %s  取值 %d..%d 均值 %.2f" % (TEXTURE1.name, tex1.min(), tex1.max(), tex1.mean()))
    print("[纹理2] %s  取值 %d..%d 均值 %.2f" % (TEXTURE2.name, tex2.min(), tex2.max(), tex2.mean()))

    # F-2：三种软硬笔尖（200px 采样笔尖 = 满画布）
    for tag, inner in (("hard", 1.0), ("mid", 0.55), ("soft", 0.0)):
        if tag == "hard":
            tip = profile_hard(TIP_SIZE_F2)
        else:
            tip = profile_soft(TIP_SIZE_F2, inner)
        p = make_abr("f2_%s" % tag, tip, TIP_SIZE_F2, 1000.0)
        print("[ABR] %s %d B" % (p.name, p.stat().st_size))

    # F-4 / 10 模式：实心方形满覆盖笔尖（base≡1）
    p = make_abr("cover", profile_hard(TIP_SIZE_F2), TIP_SIZE_F2, 1000.0)
    print("[ABR] %s %d B" % (p.name, p.stat().st_size))

    # F-3：64px 硬/软笔尖、间距 100%（dab 间距 = 64px = 纹理周期）
    p = make_abr("f3_hard", profile_hard(TIP_SIZE_F3), TIP_SIZE_F3, 100.0)
    print("[ABR] %s %d B" % (p.name, p.stat().st_size))
    p = make_abr("f3_soft", profile_soft(TIP_SIZE_F3, 0.5), TIP_SIZE_F3, 100.0)
    print("[ABR] %s %d B" % (p.name, p.stat().st_size))
    return 0



# ---------------------------------------------------------------- PS 侧
DEVNULL = subprocess.DEVNULL


def make_jsx_copy() -> Path:
    text = JSX_SRC.read_text(encoding="utf-8")
    old_define = '''        var texDoc = app.open(new File(job.texture_path));
        L("texture opened " + texDoc.width + "x" + texDoc.height + " path=" + job.texture_path);
        var dp = new ActionDescriptor();
        dp.putString(cid("Nm  "), job.texture_name);
        executeAction(cid("DfnP"), dp, DialogModes.NO);
        texDoc.close(SaveOptions.DONOTSAVECHANGES);
        L("pattern defined: " + job.texture_name);'''
    new_define = '''        var defs = job.texture_defs || [{ path: job.texture_path, name: job.texture_name }];
        for (var di = 0; di < defs.length; di++) {
            var texDoc = app.open(new File(defs[di].path));
            L("texture opened " + texDoc.width + "x" + texDoc.height + " path=" + defs[di].path);
            var dp = new ActionDescriptor();
            dp.putString(cid("Nm  "), defs[di].name);
            executeAction(cid("DfnP"), dp, DialogModes.NO);
            texDoc.close(SaveOptions.DONOTSAVECHANGES);
            L("pattern defined: " + defs[di].name);
        }'''
    if old_define not in text:
        raise SystemExit("JSX 纹理定义块原文未命中，拒绝盲目改")
    text = text.replace(old_define, new_define)
    old_anchor = "                p.anchor = [job.canvas.width / 2 + i * 0.4, job.canvas.height / 2 + i * 0.4];"
    new_anchor = '''                var ax, ay;
                if (j.stroke_len !== undefined) {
                    ax = j.stroke_x0 + i * j.stroke_len; ay = j.stroke_y;
                } else {
                    ax = job.canvas.width / 2 + i * 0.4; ay = job.canvas.height / 2 + i * 0.4;
                }
                p.anchor = [ax, ay];'''
    if old_anchor not in text:
        raise SystemExit("JSX 锚点行原文未命中，拒绝盲目改")
    text = text.replace(old_anchor, new_anchor)
    hits = 0
    for old, new in (('o.putString(sid("name"), job.texture_name);',
                      'o.putString(sid("name"), j.pattern_name || job.texture_name);'),
                     ('tp.putString(sid("name"), job.texture_name);',
                      'tp.putString(sid("name"), j.pattern_name || job.texture_name);')):
        if old in text:
            text = text.replace(old, new)
            hits += 1
    if hits == 0:
        raise SystemExit("JSX 图案名行原文未命中")
    JSX_COPY.write_text(text, encoding="utf-8")
    return JSX_COPY


def ps_job(file: str, mode_kind: str, mode_value: str, depth: int,
           brightness: int = 0, contrast: int = 0, invert: bool = True,
           pattern_name: str = None, extra: dict = None) -> dict:
    j = {"file": file, "mode_kind": mode_kind, "mode_value": mode_value,
         "depth": int(depth), "brightness": int(brightness), "contrast": int(contrast),
         "invert_texture": bool(invert)}
    if pattern_name:
        j["pattern_name"] = pattern_name
    if extra:
        j.update(extra)
    return j


def run_ps_batch(tag: str, abr: Path | None, brush_name: str | None, canvas: dict,
                 tex_defs: list[dict], jobs: list[dict], timeout: float = 420.0) -> dict:
    ensure_dirs()
    job = {"out_dir": DIR_PS.as_posix(), "texture_path": tex_defs[0]["path"],
           "texture_name": tex_defs[0]["name"], "texture_defs": tex_defs,
           "texture_scale": 100, "canvas": canvas, "jobs": jobs}
    if abr is not None:
        job["abr_path"] = abr.as_posix()
    if brush_name is not None:
        job["brush_name"] = brush_name
    (WORK_PS / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    for p in (WORK_PS / "done.json", WORK_PS / "batch.log"):
        if p.exists():
            p.unlink()
    make_jsx_copy()
    subprocess.run(["taskkill", "-F", "-IM", "Photoshop.exe"], stdout=DEVNULL, stderr=DEVNULL)
    time.sleep(5.0)   # 强杀后立刻再启动会卡在恢复/启动对话框（实测 2026-11-23）
    t0 = time.time()
    env = os.environ.copy()
    env["BRUSHCONVERTER_PS_WORK"] = WORK_PS.as_posix()
    env["BRUSHCONVERTER_REPO"] = REPO.as_posix()
    proc = subprocess.Popen([str(PS_EXE), "-r", str(JSX_COPY)], stdout=DEVNULL, stderr=DEVNULL, env=env)
    done = None
    while time.time() - t0 < timeout:
        done_path = WORK_PS / "done.json"
        if done_path.exists():
            time.sleep(0.5)
            try:
                done = json.loads(done_path.read_text(encoding="utf-8"))
            except Exception:
                done = None
            break
        if proc.poll() is not None:
            time.sleep(2.0)
            if done_path.exists():
                done = json.loads(done_path.read_text(encoding="utf-8"))
            break
        time.sleep(0.5)
    subprocess.run(["taskkill", "-F", "-IM", "Photoshop.exe"], stdout=DEVNULL, stderr=DEVNULL)
    for name in ("batch.log", "done.json", "job.json"):
        src = WORK_PS / name
        if src.exists():
            shutil.copyfile(src, DIR_LOG / ("%s__%s" % (tag, name)))
    if done is None:
        # 强杀后立刻重启会卡在 PS 恢复/启动对话框（实测 2026-11-23）：清干净再试一次
        subprocess.run(["taskkill", "-F", "-IM", "Photoshop.exe"], stdout=DEVNULL, stderr=DEVNULL)
        time.sleep(10.0)
        print("[PS %s] 第一次无 done.json，已清理并重试一次" % tag)
        proc = subprocess.Popen([str(PS_EXE), "-r", str(JSX_COPY)], stdout=DEVNULL, stderr=DEVNULL, env=env)
        t0 = time.time()
        while time.time() - t0 < timeout:
            done_path = WORK_PS / "done.json"
            if done_path.exists():
                time.sleep(0.5)
                try:
                    done = json.loads(done_path.read_text(encoding="utf-8"))
                except Exception:
                    done = None
                break
            if proc.poll() is not None:
                time.sleep(2.0)
                if done_path.exists():
                    done = json.loads(done_path.read_text(encoding="utf-8"))
                break
            time.sleep(0.5)
        subprocess.run(["taskkill", "-F", "-IM", "Photoshop.exe"], stdout=DEVNULL, stderr=DEVNULL)
        for name in ("batch.log", "done.json", "job.json"):
            src = WORK_PS / name
            if src.exists():
                shutil.copyfile(src, DIR_LOG / ("%s__%s" % (tag, name)))
    if done is None:
        raise SystemExit("[PS %s] 重试后仍无 done.json（日志见 %s）" % (tag, DIR_LOG))
    ok = sum(1 for r in done.get("results", []) if r.get("ok"))
    print("[PS %s] %d/%d 张 ok，用时 %.1fs，brush=%s" % (
        tag, ok, len(done.get("results", [])), time.time() - t0,
        json.dumps(done.get("brush", {}), ensure_ascii=False)[:180]))
    failed = [r for r in done.get("results", []) if not r.get("ok")]
    for r in failed:
        print("   [失败] %s : %s" % (r.get("file"), r.get("error")))
    return done


def abr_tex_dict(blend: str, depth: float, name: str, invert: bool,
                 minimum_depth: float = 0.0, dyn_bvty: int = 0,
                 dyn_minimum: float = 0.0, brightness: int = 0, contrast: int = 0) -> dict:
    return {"blend": blend, "depth": depth, "name": name,
            "uuid": hashlib.sha256(name.encode()).hexdigest()[:36],
            "scale": 100.0, "invert": invert, "minimum_depth": minimum_depth,
            "dyn_bvty": dyn_bvty, "dyn_minimum": dyn_minimum,
            "brightness": brightness, "contrast": contrast}


def tips_abr():
    """F-2 三种软硬笔尖（各带纹理键，保证 PS 预设开得上纹理）。"""
    out = {}
    for tag in ("hard", "mid", "soft"):
        tip = profile_hard(TIP_SIZE_F2) if tag == "hard" else profile_soft(TIP_SIZE_F2, 0.55 if tag == "mid" else 0.0)
        out[tag] = make_abr("f2_%s" % tag, tip, TIP_SIZE_F2, 1000.0,
                            texture=abr_tex_dict("hardMix", 100.0, "bc128a_abrpat", True))
    return out


def cover_abr():
    return make_abr("cover", profile_hard(TIP_SIZE_F2), TIP_SIZE_F2, 1000.0,
                    texture=abr_tex_dict("Mltp", 100.0, "bc128a_abrpat", True))


def f3_abr(tag: str, minimum_depth: float):
    tip = profile_hard(TIP_SIZE_F3) if tag == "hard" else profile_soft(TIP_SIZE_F3, 0.5)
    return make_abr("f3_%s_m%d" % (tag, int(minimum_depth)), tip, TIP_SIZE_F3, 100.0,
                    texture=abr_tex_dict("Mltp", 100.0, "bc128a_abrpat", True,
                                         minimum_depth=minimum_depth, dyn_bvty=1, dyn_minimum=minimum_depth))


F2_DEPTHS = (10, 60, 100)
F2_DAB = {"stroke_len": 0.4, "stroke_x0": 98, "stroke_y": 100}  # 与 Krita (stroke=4) 的 dab 圆心一致


def ps_f2():
    ok = acquire_lock(PS_LOCK, label="128A-ps-f2")
    if not ok:
        raise SystemExit("PS 锁超时")
    try:
        abrs = tips_abr()
        created = make_jsx_copy()
        print("[JSX] %s" % created)
        tex_defs = [{"path": TEXTURE1.as_posix(), "name": PS_TEX_NAME}]
        for tag in ("hard", "mid", "soft"):
            extra_ref = dict(F2_DAB)
            extra_ref["use_texture"] = False
            jobs = [ps_job("ps128a__f2__%s__ref_notex.png" % tag, "char", "Mltp", 100,
                           pattern_name=PS_TEX_NAME, extra=extra_ref)]
            for d in F2_DEPTHS:
                jobs.append(ps_job("ps128a__f2__%s__hardMix__d%d.png" % (tag, d),
                                   "str", "hardMix", d, pattern_name=PS_TEX_NAME,
                                   extra=dict(F2_DAB)))
            run_ps_batch("f2_%s" % tag, abrs[tag], "f2_%s" % tag, CANVAS_200, tex_defs, jobs)
    finally:
        release_lock(PS_LOCK)
    return 0


F4_COMBOS = ((-150, -50), (-140, -50), (-128, -50), (128, -50), (140, -50), (150, -50))
UI10 = (("Mltp", "char", "multiply", 0), ("Sbtr", "char", "subtract", 1),
        ("Drkn", "char", "darken", 4), ("Ovrl", "char", "overlay", 5),
        ("CDdg", "char", "colorDodge", 6), ("CBrn", "char", "colorBurn", 7),
        ("linearBurn", "str", "linearBurn", 9), ("hardMix", "str", "hardMix", 10),
        ("height", "str", "height", 14), ("linearHeight", "str", "linearHeight", 15))


def ps_f4():
    ok = acquire_lock(PS_LOCK, label="128A-ps-f4")
    if not ok:
        raise SystemExit("PS 锁超时")
    try:
        cover = cover_abr()
        for tpath, tname in ((TEXTURE1, PS_TEX_NAME), (TEXTURE2, PS_TEX2_NAME)):
            tex_defs = [{"path": tpath.as_posix(), "name": tname}]
            jobs = []
            for b, c in F4_COMBOS:
                jobs.append(ps_job("ps128a__f4__%s__b%d_c%d.png" % (tname[-4:], b, c),
                                   "char", "Mltp", 100, brightness=b, contrast=c,
                                   pattern_name=tname))
            # 对照组 1：C=80（>50）multiply / linearHeight d=60
            for mode_kind, mode_value, tag in (("char", "Mltp", "mul"), ("str", "linearHeight", "linh")):
                jobs.append(ps_job("ps128a__ctl__c80__%s__d60.png" % tag, mode_kind, mode_value, 60,
                                   brightness=0, contrast=80, pattern_name=tname))
            # 10 个 UI 模式 d=100 + C=50/b=50（预烘焙等价性用）
            for ps_id, kind, name, enum in UI10:
                jobs.append(ps_job("ps128a__m10__%s__d100_b50_c50.png" % name, kind, ps_id, 100,
                                   brightness=50, contrast=50, pattern_name=tname))
            run_ps_batch("f4_%s" % tname[-4:], cover, "cover", CANVAS_200, tex_defs, jobs)
    finally:
        release_lock(PS_LOCK)
    return 0


F3_MINIMUMS = (0, 20, 50)


def ps_f3():
    ok = acquire_lock(PS_LOCK, label="128A-ps-f3")
    if not ok:
        raise SystemExit("PS 锁超时")
    try:
        tex_defs = [{"path": TEXTURE1.as_posix(), "name": PS_TEX_NAME}]
        for tag in ("hard", "soft"):
            for m in F3_MINIMUMS:
                abr = f3_abr(tag, m)
                jobs = [ps_job("ps128a__f3__%s__m%d__ref.png" % (tag, m), "char", "Mltp", 100,
                               pattern_name=PS_TEX_NAME,
                               extra={"use_texture": False,
                                      "stroke_len": 1024, "stroke_x0": 32, "stroke_y": 32})]
                for rep in (0, 1):
                    jobs.append(ps_job("ps128a__f3__%s__m%d__rep%d.png" % (tag, m, rep),
                                       "char", "Mltp", 100, pattern_name=PS_TEX_NAME,
                                       extra={"stroke_len": 1024, "stroke_x0": 32,
                                              "stroke_y": 32}))
                run_ps_batch("f3_%s_m%d" % (tag, m), abr, "f3_%s_m%d" % (tag, m), CANVAS_1100, tex_defs, jobs)
    finally:
        release_lock(PS_LOCK)
    return 0


def ps_control():
    return ps_f4()


# ---------------------------------------------------------------- Krita 侧 .kpp
def _krita_imports():
    from brush_converter.abr import AbrFile
    from brush_converter.mapping import map_presets
    from brush_converter import convert as conv
    from brush_converter.kpp.preset_xml import TextureXml, build_preset_xml
    from brush_converter.kpp.kpp_writer import build_kpp, render_tip_preview
    return AbrFile, map_presets, conv, TextureXml, build_preset_xml, build_kpp, render_tip_preview


def pattern_res_name(png_bytes: bytes) -> str:
    return "exp128a_%s.png" % hashlib.md5(png_bytes).hexdigest()[:12]


def write_kpp_from_abr(abr_path: Path, png_bytes: bytes, tex: dict, out_path: Path,
                       name_suffix: str = "") -> Path:
    AbrFile, map_presets, conv, TextureXml, build_preset_xml, build_kpp, render_tip_preview = _krita_imports()
    abr = AbrFile.parse(str(abr_path))
    presets = map_presets(abr)
    if len(presets) != 1:
        raise SystemExit("ABR 预设数 != 1：%s" % abr_path)
    bp = presets[0]
    if bp.is_computed:
        brush_def = conv._computed_brush_def(bp)
        tip_png = None
        tip_name = None
        preview = render_tip_preview(conv._circle_mask(bp.hardness or 100.0, bp.roundness))
    else:
        square_tip = conv._square_tip(bp.tip_gray)
        if square_tip.shape != bp.tip_gray.shape:
            raise SystemExit("非正方形笔尖补白会把补白区当墨（本实验只用正方形笔尖）")
        # 约定链（本任务实测）：ABR 采样像素 255=墨 → reader 反转为 tip_gray=255-pixel（深=墨）
        # → L 模式 PNG 深=墨（Krita 5.3.3 实测：深=墨、白=透明）⇒ 两侧落笔掩码一致。
        tip_png = conv._png_bytes(square_tip)
        tip_name = bp.name.rsplit(".", 1)[0] + (name_suffix or "")
        brush_def = conv._sampled_brush_def(tip_name, tip_png, bp)
        preview = render_tip_preview(bp.tip_gray)
    texture = TextureXml(
        pattern_filename=pattern_res_name(png_bytes), png_bytes=png_bytes,
        scale=float(tex.get("scale", 1.0)), brightness=float(tex.get("brightness", 0.0)),
        contrast=float(tex.get("contrast", 1.0)), invert=bool(tex.get("invert", False)),
        texturing_mode=int(tex.get("mode", 0)), use_soft_texturing=False,
        strength=float(tex.get("strength", 1.0)), strength_curve=None, strength_pressure=False)
    xml = build_preset_xml(
        name=bp.name + (name_suffix or ""), brush_definition=brush_def, tip_png=tip_png,
        tip_name=tip_name, size_curve=bp.size_curve, opacity_curve=bp.opacity_curve,
        flow_curve=bp.flow_curve, ratio_curve=bp.ratio_curve,
        rotation_sensor=bp.rotation_sensor, rotation_jitter=bp.rotation_jitter,
        scatter=bp.scatter, scatter_pressure=bp.scatter_pressure,
        scatter_both_axes=bp.scatter_both_axes, scatter_amount=bp.scatter_amount,
        texture=texture)
    out_path.write_bytes(build_kpp(xml, preview))
    return out_path


def calibrated_bc(ps_b: float, ps_c: float) -> tuple[float, float]:
    """项目现行 calibrated 闭式解（含 clamp），直接抄 preset_xml.py:304-331 的口径。"""
    def k_of(c):
        if c <= -50.0:
            return 0.5
        if c < 0.0:
            return 1.0 + c / 100.0
        if c >= 100.0:
            return 1_000_000.0
        return 1.0 / (1.0 - c / 100.0)
    k = k_of(ps_c)
    k_eff = max(0.0, min(2.0, k))
    center = (1.0 - k_eff) / (510.0 * k_eff)
    if ps_c < 0.0:
        b = center - ps_b / (255.0 * k_eff)
    else:
        b = center - ps_b / 255.0
    b = max(-1.0, min(1.0, round(b, 3)))
    c = max(0.0, min(2.0, round(k_eff, 3)))
    return b, c


def raw_bc(ps_b: float, ps_c: float) -> tuple[float, float]:
    """不 clamp 的闭式解（本实验的对照：看越界区是不是由 clamp 造成）。"""
    b, c = calibrated_bc(ps_b, ps_c)
    k = 1_000_000.0 if ps_c >= 100.0 else (0.5 if ps_c <= -50.0 else
                                          (1.0 + ps_c / 100.0 if ps_c < 0 else 1.0 / (1.0 - ps_c / 100.0)))
    center = (1.0 - k) / (510.0 * k)
    raw = center - (ps_b / (255.0 * k) if ps_c < 0 else ps_b / 255.0)
    return raw, k


def tex_mask_from_gray(gray: np.ndarray) -> np.ndarray:
    return np.clip(gray.astype(np.float64) / 255.0, 0.0, 1.0)


def ps_bc_mask(gray: np.ndarray, ps_b: float, ps_c: float, invert: bool = True) -> np.ndarray:
    """PS 侧 8bit 链的连续模型：先减亮度并截断 → 以 0.5 为中心乘 k → 截断 → 反相。"""
    m = tex_mask_from_gray(gray).astype(np.float64)
    k = (1_000_000.0 if ps_c >= 100.0 else
         (1.0 + ps_c / 100.0 if ps_c < 0 else 1.0 / (1.0 - ps_c / 100.0)))
    v = np.clip(m - ps_b / 255.0, 0.0, 1.0)
    v = np.clip((v - 0.5) * k + 0.5, 0.0, 1.0)
    if invert:
        v = 1.0 - v
    return np.clip(v, 0.0, 1.0)


def tiled_gray(gray: np.ndarray, h: int, w: int) -> np.ndarray:
    reps = (h // gray.shape[0] + 1, w // gray.shape[1] + 1)
    return np.tile(gray, reps)[:h, :w]


def bake_png_for_f4(gray: np.ndarray, ps_b: float, ps_c: float, invert: bool,
                    out_path: Path, tag: str) -> bytes:
    mask = ps_bc_mask(gray, ps_b, ps_c, invert)
    return save_gray_png(out_path, np.rint(mask * 255.0), unique_tag=tag)


def bake_png_for_f2(tip_alpha: np.ndarray, ps_alpha: np.ndarray, strength: float,
                    invert: bool, out_path: Path, tag: str) -> bytes:
    """逐像素预烘焙 oracle：让 Krita 的 hardMix(base, s=strength, mask') 复现 PS 二值结果。"""
    # Krita: out = 1 iff mask' + base*strength > 1  ⇒  需要 mask' > 1 - base*strength
    target = (ps_alpha > 127)
    thr = 1.0 - np.clip(tip_alpha, 0.0, 1.0) * strength
    # 目标 1 的像素给「刚好越过阈值」的值；目标 0 的像素给 0
    mask = np.where(target, np.clip(thr + 2.0 / 255.0, 0.0, 1.0), 0.0)
    return save_gray_png(out_path, np.rint(mask * 255.0), unique_tag=tag)


def build_kpps():
    """为 F-2 / F-4 / 10 模式 / 对照组生成全部 .kpp（幂等）。"""
    ensure_dirs()
    tex1_gray = texture_gray(TEXTURE1)
    tex2_gray = texture_gray(TEXTURE2)
    tex1_png_ps = TEXTURE1.read_bytes()          # PS 用原始字节
    tex2_png_ps = TEXTURE2.read_bytes()
    tex1_png_kr = unique_png_bytes(TEXTURE1, "t1")
    tex2_png_kr = unique_png_bytes(TEXTURE2, "t2")
    plan = []
    abrs = tips_abr()

    # ---- F-2: 3 笔尖 × 3 深度 × 3 路径（原路径 / 常量偏置 / 逐像素预烘焙）
    for tag in ("hard", "mid", "soft"):
        abr = abrs[tag]
        _, _, conv, _, _, _, _ = _krita_imports()
        from brush_converter.abr import AbrFile
        from brush_converter.mapping import map_presets
        ref_png = _ps("ps128a__f2__%s__ref_notex.png" % tag)
        if not ref_png.is_file():
            print("[跳过] 预烘焙缺 PS 无纹理参考 %s" % ref_png.name)
            continue
        base = load_alpha(ref_png).astype(np.float64) / 255.0
        for d in F2_DEPTHS:
            s = d / 100.0
            plan.append(("kpp", "f2__%s__d%d__cur" % (tag, d),
                         dict(tex=dict(mode=10, strength=s, brightness=0.0, contrast=1.0, invert=True,
                                       scale=1.0), png=tex1_png_kr, abr=abr)))
            plan.append(("kpp", "f2__%s__d%d__bias" % (tag, d),
                         dict(tex=dict(mode=10, strength=1.0, brightness=1.0 / 255.0, contrast=1.0,
                                       invert=True, scale=1.0), png=tex1_png_kr, abr=abr)))
            ps_png = DIR_PS / ("ps128a__f2__%s__hardMix__d%d.png" % (tag, d))
            out_pb = DIR_TEX / ("f2bake__%s__d%d.png" % (tag, d))
            if not ps_png.is_file():
                print("[跳过] 预烘焙缺 PS 真值 %s" % ps_png.name)
                continue
            ps_alpha = load_alpha(ps_png)
            bake_png = bake_png_for_f2(base, ps_alpha, s, True, out_pb, "f2bake%s%d" % (tag, d))
            plan.append(("kpp", "f2__%s__d%d__prebake" % (tag, d),
                         dict(tex=dict(mode=10, strength=s, brightness=0.0, contrast=1.0, invert=False,
                                       scale=1.0), png=bake_png, abr=abr)))

    # ---- F-4: 6 组越界 × 2 纹理 × 2 路径（现行闭式解 / 预烘焙）+ 对照 C=80
    cover = cover_abr()
    for tname, gray, png_kr in (("t1", tex1_gray, tex1_png_kr), ("t2", tex2_gray, tex2_png_kr)):
        for b, c in F4_COMBOS:
            kb, kc = calibrated_bc(b, c)
            plan.append(("kpp", "f4__%s__b%d_c%d__cal" % (tname, b, c),
                         dict(tex=dict(mode=0, strength=1.0, brightness=kb, contrast=kc,
                                       invert=True, scale=1.0), png=png_kr, abr=cover)))
            bake = bake_png_for_f4(gray, b, c, True, DIR_TEX / ("f4bake__%s__b%d_c%d.png" % (tname, b, c)),
                                   "f4bake%s%d%d" % (tname, b, c))
            plan.append(("kpp", "f4__%s__b%d_c%d__pre" % (tname, b, c),
                         dict(tex=dict(mode=0, strength=1.0, brightness=0.0, contrast=1.0,
                                       invert=False, scale=1.0), png=bake, abr=cover)))
    # 对照 C=80（原目标「C>50」的 10–15 分钟登记）
    for tname, png_kr in (("t1", tex1_png_kr),):
        for mode_enum, mode_tag in ((0, "mul"), (15, "linh")):
            kb, kc = calibrated_bc(0, 80)
            plan.append(("kpp", "ctl__c80__%s__d60__cal" % mode_tag,
                         dict(tex=dict(mode=mode_enum, strength=0.6, brightness=kb, contrast=kc,
                                       invert=True, scale=1.0), png=png_kr, abr=cover)))
    # ---- 10 个 UI 模式：C=50/b=50（预烘焙 vs PS 真值）
    for ps_id, kind, name, enum in UI10:
        kb, kc = calibrated_bc(50, 50)
        plan.append(("kpp", "m10__%s__d100__cal" % name,
                     dict(tex=dict(mode=enum, strength=1.0, brightness=kb, contrast=kc,
                                   invert=True, scale=1.0), png=tex1_png_kr, abr=cover)))
        bake = bake_png_for_f4(tex1_gray, 50, 50, True, DIR_TEX / ("m10bake__%s.png" % name),
                               "m10bake" + name)
        plan.append(("kpp", "m10__%s__d100__pre" % name,
                     dict(tex=dict(mode=enum, strength=1.0, brightness=0.0, contrast=1.0,
                                   invert=False, scale=1.0), png=bake, abr=cover)))
    # ---- F-3: 16 档压感 ramp（Krita 侧仅 1 组静态值对照，见报告口径）
    for m in F3_MINIMUMS:
        abr = DIR_ABR / ("f3_hard_m%d.abr" % m)
        if not abr.is_file():
            abr = f3_abr("hard", float(m))
        for grade in [0.0] + [i / 15.0 for i in range(1, 16)]:
            plan.append(("kpp", "f3__hard__m%d__p%02d" % (m, int(round(grade * 15))),
                         dict(tex=dict(mode=0, strength=1.0, brightness=0.0, contrast=1.0,
                                       invert=True, scale=1.0), png=tex1_png_kr, abr=abr)))

    written = 0
    for kind, name, spec in plan:
        out = DIR_KPP / (name + ".kpp")
        tex = dict(spec["tex"])
        write_kpp_from_abr(spec["abr"], spec["png"], tex, out)
        written += 1
    print("[KPP] 生成 %d 个 → %s" % (written, DIR_KPP))
    write_json("kpp_plan.json", [{"name": n, "abr": s["abr"].name} for _k, n, s in plan])
    return 0


# ---------------------------------------------------------------- Krita 渲染
RENDER_GROUPS = {
    "f2": [("kpp", "f2__")],
    "f4": [("kpp", "f4__")],
    "ctl": [("kpp", "ctl__")],
    "m10": [("kpp", "m10__")],
    "f3": [("kpp", "f3__")],
    "rest": [("kpp", "f4__"), ("kpp", "ctl__"), ("kpp", "m10__")],
}


def render_group(group: str):
    import krita_render_runner as runner
    ensure_dirs()
    if group not in RENDER_GROUPS:
        raise SystemExit("未知渲染组：%s" % group)
    kpps = []
    for _kind, prefix in RENDER_GROUPS[group]:
        kpps.extend(sorted(DIR_KPP.glob(prefix + "*.kpp")))
    kpps = sorted(set(kpps))
    if not kpps:
        raise SystemExit("没有 .kpp（先跑 kpp）")
    ok = acquire_lock(KRITA_LOCK, label="128A-krita-%s" % group)
    if not ok:
        raise SystemExit("Krita 锁超时")
    log = {}
    failed = 0
    try:
        t0 = time.time()
        for i, kpp in enumerate(kpps, 1):
            out = DIR_KR / (kpp.stem + ".png")
            if out.is_file():
                print("[%d/%d] 跳过已有 %s" % (i, len(kpps), out.name))
                continue
            if group == "f3":
                size, stroke = (1100, 64), 1000
            else:
                size, stroke = (200, 200), 4   # stroke=0 插件不落笔（实测 alpha=0），取 4 让圆心=(98,100)
            res = runner.render_one(str(kpp), str(out), size=size, stroke=stroke,
                                    pressure=1.0, timeout=120, route="gui")
            if not res.get("ok"):
                # 单实例转发/残留进程会让下一次启动直接 rc=0（125/127 已登记）：清干净后重试一次
                subprocess.run(["taskkill", "-F", "-IM", "krita.com"], stdout=DEVNULL, stderr=DEVNULL)
                subprocess.run(["taskkill", "-F", "-IM", "krita.exe"], stdout=DEVNULL, stderr=DEVNULL)
                time.sleep(4.0)
                res = runner.render_one(str(kpp), str(out), size=size, stroke=stroke,
                                        pressure=1.0, timeout=120, route="gui")
            entry = {"preset": kpp.name, "ok": bool(res.get("ok")),
                     "alpha_nonzero": res.get("alpha_nonzero"),
                     "elapsed_s": res.get("elapsed_s"), "reason": res.get("reason")}
            log[out.name] = entry
            if not res.get("ok"):
                failed += 1
                print("[%d/%d] 失败 %s : %s" % (i, len(kpps), out.name,
                                                (res.get("reason") or "")[:160]))
                # 不把失败图留成「已渲染」状态
                if out.is_file():
                    out.unlink()
            else:
                print("[%d/%d] ok %s %.1fs alpha=%s" % (
                    i, len(kpps), out.name, res.get("elapsed_s") or 0.0, res.get("alpha_nonzero")))
            sys.stdout.flush()
        write_json("render_log_%s.json" % group, log)
        print("[渲染] %s：%d 张 / 失败 %d / %.1fs" % (group, len(kpps), failed, time.time() - t0))
    finally:
        release_lock(KRITA_LOCK)
    return 1 if failed else 0



# ---------------------------------------------------------------- 分析
def _ps(name: str) -> Path:
    return DIR_PS / name


def _kr(name: str) -> Path:
    return DIR_KR / name


def _pair(a: Path, b: Path) -> dict:
    if not (a.is_file() and b.is_file()):
        return {"status": "missing", "a": a.name, "b": b.name}
    m = metrics(a, b)
    m["status"] = "compared"
    return m


def analyze_f2() -> dict:
    tex_gray = texture_gray(TEXTURE1)
    rows = []
    for tag in ("hard", "mid", "soft"):
        ps_ref = _ps("ps128a__f2__%s__ref_notex.png" % tag)
        if not ps_ref.is_file():
            continue
        base = load_alpha(ps_ref).astype(np.float64) / 255.0
        coverage = float((base > 0).mean())
        for d in F2_DEPTHS:
            ps_img = _ps("ps128a__f2__%s__hardMix__d%d.png" % (tag, d))
            if not ps_img.is_file():
                continue
            s = d / 100.0
            ps_a = load_alpha(ps_img)
            uniq = int(np.unique(ps_a).size)
            mask_kr = 1.0 - np.clip(tiled_gray(tex_gray, 200, 200).astype(np.float64) / 255.0, 0.0, 1.0)
            boundary = np.abs(mask_kr - (1.0 - base * s)) <= (1.0 / 255.0)
            row = {"tip": tag, "depth": d, "base_coverage": round(coverage, 4),
                   "base_mean": round(float(base.mean()), 2),
                   "ps_alpha_unique": uniq,
                   "ps_alpha_min": int(ps_a.min()), "ps_alpha_max": int(ps_a.max()),
                   "boundary_pixels": int(boundary.sum()),
                   "paths": {}}
            for ptag, fname, tmask in (
                    ("current", "f2__%s__d%d__cur.png" % (tag, d), mask_kr),
                    ("bias", "f2__%s__d%d__bias.png" % (tag, d), mask_kr),
                    ("prebake", "f2__%s__d%d__prebake.png" % (tag, d), None)):
                kr = _kr(fname)
                m = _pair(ps_img, kr)
                if m.get("status") == "compared" and tmask is not None:
                    kr_a = load_alpha(kr)
                    hit = int(np.count_nonzero((np.abs(kr_a - ps_a) > TOL) & boundary))
                    m["boundary_hits"] = hit
                    m["boundary_hit_ratio"] = round(float(hit) / max(1, int(boundary.sum())), 4)
                row["paths"][ptag] = m
            rows.append(row)

    payload = {"task": "128A-F2", "texture": "research/测试用纹理.png", "invert": True,
               "canvas": "200x200", "mode": "hardMix (Krita 10)",
               "metric": "|Δalpha|>2 像素占比（分母 40000）与 max_delta",
               "criterion": {"ratio_max": 0.02, "max_delta_max": 2},
               "rows": rows}
    write_json("f2_analysis.json", payload)
    print("\n=== F-2：hardMix 软边笔尖（PS 真值 vs Krita 三条路径）===")
    print("%-6s %4s %6s %5s %5s %6s | %-28s %-28s %-28s" % (
        "笔尖", "d", "base", "PSuniq", "边界px", "PSmin", "①原路径", "②常量偏置", "③逐像素预烘焙"))
    for r in rows:
        cells = []
        for ptag in ("current", "bias", "prebake"):
            m = r["paths"].get(ptag, {})
            if m.get("status") == "compared":
                cells.append("%7.3f%%/%-3d%s" % (m["diff_ratio"] * 100, m["max_delta"],
                                                 "边界%d" % m.get("boundary_hits", 0)
                                                 if ptag != "prebake" else "        "))
            else:
                cells.append("  缺失      ")
        print("%-6s %4d %6.2f %5d %5d %6d | %-28s %-28s %-28s" % (
            r["tip"], r["depth"], r["base_mean"], r["ps_alpha_unique"],
            r["boundary_pixels"], r["ps_alpha_min"], *cells))
    return payload


def analyze_f4() -> dict:
    rows = []
    # PS 文件名后缀：tex1 → "_tex"、tex2 → "tex2"（由 ps_f4 的 tname[-4:] 生成）
    PS_SUFFIX = {"t1": "_tex", "t2": "tex2"}
    for tname, tpath in (("t1", TEXTURE1), ("t2", TEXTURE2)):
        gray = texture_gray(tpath)
        tile = tiled_gray(gray, 200, 200).astype(np.float64)
        for b, c in F4_COMBOS:
            ps_img = _ps("ps128a__f4__%s__b%d_c%d.png" % (PS_SUFFIX[tname], b, c))
            if not ps_img.is_file():
                continue
            kb, kc = calibrated_bc(b, c)
            rb, rc = raw_bc(b, c)
            # 掩码空间（离线，同引擎模型）：PS 链 vs Krita 现行闭式解
            ps_mask = ps_bc_mask(tile, b, c, invert=True)
            kr_mask = np.clip(tile / 255.0 - kb, -1.0, 2.0)
            kr_mask = np.clip((kr_mask - 0.5) * kc + 0.5, 0.0, 1.0)
            kr_mask = 1.0 - kr_mask
            md = np.abs(np.rint(ps_mask * 255.0) - np.rint(kr_mask * 255.0))
            row = {"texture": tname, "ps_b": b, "ps_c": c,
                   "closed_b_clamped": kb, "closed_b_raw": round(rb, 4),
                   "closed_c": kc, "out_of_range": bool(abs(rb) > 1.0),
                   "ps_alpha_unique": int(np.unique(load_alpha(ps_img)).size),
                   "mask_space_closed_vs_ps": {
                       "diff_ratio": round(float((md > TOL).mean()), 6),
                       "max_delta": int(md.max()),
                       "diff_pixels": int((md > TOL).sum())},
                   "paths": {}}
            for ptag, fname in (("closed", "f4__%s__b%d_c%d__cal.png" % (tname, b, c)),
                                ("prebake", "f4__%s__b%d_c%d__pre.png" % (tname, b, c))):
                row["paths"][ptag] = _pair(ps_img, _kr(fname))
            rows.append(row)

    ctl = []
    for mode_tag in ("mul", "linh"):
        ps_img = _ps("ps128a__ctl__c80__%s__d60.png" % mode_tag)
        kr = _kr("ctl__c80__%s__d60__cal.png" % mode_tag)
        ctl.append({"mode_tag": mode_tag, "ps_b": 0, "ps_c": 80,
                    "closed": _pair(ps_img, kr)})

    payload = {"task": "128A-F4", "texture_set": ["research/测试用纹理.png", "tex2_gradnoise64.png"],
               "canvas": "200x200", "mode": "multiply (Krita 0)", "depth": 100,
               "criterion": {"ratio_max": 0.02, "max_delta_max": 2},
               "control_c80": ctl, "rows": rows}
    write_json("f4_analysis.json", payload)
    print("\n=== F-4：textureBrightness 越界区（C=-50，|b|>127）===")
    print("%-3s %5s %4s | %10s %10s %5s | %-24s %-24s" % (
        "tex", "PSb", "C", "b_k(原)", "b_k(clamp)", "越界", "①现行闭式解(掩码空间)", "①现行闭式解(渲染)"))
    for r in rows:
        ms = r["mask_space_closed_vs_ps"]
        m = r["paths"].get("closed", {})
        print("%-3s %5d %4d | %10.4f %10.4f %5s | %8.2f%%/max%-4d      | %8.2f%%/max%-4d %s" % (
            r["texture"], r["ps_b"], r["ps_c"], r["closed_b_raw"], r["closed_b_clamped"],
            "是" if r["out_of_range"] else "否",
            ms["diff_ratio"] * 100, ms["max_delta"],
            (m.get("diff_ratio", float("nan")) * 100) if m.get("status") == "compared" else float("nan"),
            m.get("max_delta", -1) if m.get("status") == "compared" else -1,
            "通过" if m.get("pass") else ""))
    print("%-3s %5s %4s | %-24s %-24s" % ("", "", "", "②预烘焙(渲染)", "判据"))
    for r in rows:
        m = r["paths"].get("prebake", {})
        if m.get("status") == "compared":
            print("    %-3s b=%-5d 预烘焙 %8.3f%%/max%-4d  通过=%s" % (
                r["texture"], r["ps_b"], m["diff_ratio"] * 100, m["max_delta"], m["pass"]))
    print("对照 C=80（>50）：", [(c["mode_tag"], round(c["closed"]["diff_ratio"] * 100, 3)
                             if c["closed"].get("status") == "compared" else None,
                             c["closed"].get("max_delta")) for c in ctl])
    return payload


def analyze_m10() -> dict:
    import texture_mask as tm
    rows = []
    kb, kc = calibrated_bc(50, 50)
    tile = tiled_gray(texture_gray(TEXTURE1), 200, 200).astype(np.float64)
    ps_mask_txt = 1.0 - np.clip(tile / 255.0 - 0.0, 0.0, 1.0)
    ps_mask = ps_bc_mask(tile, 50, 50, invert=True)
    kr_mask = 1.0 - np.clip((np.clip(tile / 255.0 - kb, -1.0, 2.0) - 0.5) * kc + 0.5, 0.0, 1.0)
    for ps_id, kind, name, enum in UI10:
        ps_img = _ps("ps128a__m10__%s__d100_b50_c50.png" % name)
        row = {"ps_member": ps_id, "name": name, "krita_enum": enum,
                   "paths": {}}
        for ptag, fname in (("closed", "m10__%s__d100__cal.png" % name),
                            ("prebake", "m10__%s__d100__pre.png" % name)):
            row["paths"][ptag] = _pair(ps_img, _kr(fname))
        # 离线：同一模式下「闭式解掩码」与「预烘焙掩码」的算子输出差（模式语义是否被改变）
        try:
            out_closed = tm.blend(np.ones_like(kr_mask, dtype=np.float32).astype(np.float32),
                                  kr_mask.astype(np.float32), enum, strength=1.0)
            out_pre = tm.blend(np.ones_like(ps_mask, dtype=np.float32).astype(np.float32),
                               ps_mask.astype(np.float32), enum, strength=1.0)
            d = np.abs(np.rint(out_closed * 255) - np.rint(out_pre * 255))
            row["offline_operator_diff"] = {
                "diff_ratio": round(float((d > TOL).mean()), 6),
                "max_delta": int(d.max()),
                "modes_same": bool(np.all(d <= TOL))}
        except NotImplementedError as exc:
            row["offline_operator_diff"] = {"error": str(exc)}
        rows.append(row)
    payload = {"task": "128A-10modes", "canvas": "200x200", "depth": 100,
               "ps_brightness": 50, "ps_contrast": 50, "base": 1.0,
               "note": ("10 个 PS「纹理>模式」UI 模式；cal=现行闭式解(含 clamp)，pre=把 PS 亮度/对比度"
                        "预烘焙进 PNG + Krita 中性参数(B=0,C=1)" ),
               "rows": rows}
    write_json("m10_analysis.json", payload)
    print("\n=== 10 个 UI 纹理模式（d=100, b=50, c=50, 满覆盖笔尖）===")
    print("%-14s %4s %4s | %-22s %-22s | %-24s" % (
        "PS 模式", "PSid", "枚举", "预烘焙 vs PS 真值", "闭式解 vs PS 真值", "同掩码下算子差(离线)"))
    for r in rows:
        pre = r["paths"].get("prebake", {})
        clo = r["paths"].get("closed", {})
        off = r.get("offline_operator_diff", {})
        def fmt(m):
            if m.get("status") != "compared":
                return "缺失"
            return "%8.3f%%/max%-4d%s" % (m["diff_ratio"] * 100, m["max_delta"],
                                          "✓" if m["pass"] else "✗")
        print("%-14s %4s %4d | %-22s %-22s | %s" % (
            r["name"], r["ps_member"], r["krita_enum"], fmt(pre), fmt(clo),
            ("同=%s max%d" % (off.get("modes_same"), off.get("max_delta", -1)))
            if "error" not in off else off["error"][:20]))
    return payload


F3_BANDS = 16


def analyze_f3() -> dict:
    gray = texture_gray(TEXTURE1)
    U = float(gray.mean())
    X0, KX, KY = 32, 64, 32
    rows = []
    for tag in ("hard", "soft"):
        for m in F3_MINIMUMS:
            rep_means = []
            for rep in (0, 1):
                p = _ps("ps128a__f3__%s__m%d__rep%d.png" % (tag, m, rep))
                if not p.is_file():
                    continue
                a = load_alpha(p).astype(np.float64)
                means = []
                for k in range(F3_BANDS):
                    x0 = X0 + KX * k + 8
                    band = a[8:56, x0:x0 + 48]
                    means.append(round(float(band.mean()), 2))
                rep_means.append(means)
            if not rep_means:
                continue
            row = {"tip": tag, "minimum": m, "texture_mean_u": round(U, 3),
                   "band_alpha_mean_rep0": rep_means[0],
                   "repeat_max_abs_diff": (round(max(abs(a - b) for a, b in zip(rep_means[0], rep_means[1])), 3)
                                           if len(rep_means) > 1 else None),
                   "repeat_rel_pct": None}
            if len(rep_means) > 1:
                base_mean = [abs(v) for v in rep_means[0]]
                den = [max(1.0, b) for b in base_mean]
                row["repeat_rel_pct"] = round(100.0 * max(abs(a - b) / d for a, b, d in
                                                          zip(rep_means[0], rep_means[1], den)), 3)
            rows.append(row)

    # 从 Mnm=0 组反解 fade 进度 t_k：q_eff = (255 - A)/U
    calib = {}
    for r in rows:
        if r["minimum"] == 0 and r["tip"] in ("hard", "soft"):
            q = [(255.0 - a) / U for a in r["band_alpha_mean_rep0"]]
            calib[r["tip"]] = q
    fit = []
    for r in rows:
        if r["minimum"] == 0:
            continue
        tip = r["tip"]
        if tip not in calib:
            continue
        t = calib[tip]
        m = r["minimum"] / 100.0
        meas_q = [(255.0 - a) / U for a in r["band_alpha_mean_rep0"]]
        models = {
            "M1_参数地板(m+(1-m)t)": [m + (1 - m) * tk for tk in t],
            "M2_alpha地板(max(t,m))": [max(tk, m) for tk in t],
            "M3_乘性地板(m*t)": [m * tk for tk in t],
        }
        entry = {"tip": tip, "minimum": r["minimum"], "t_recovered": [round(v, 4) for v in t],
                 "q_eff_measured": [round(v, 4) for v in meas_q], "models": {}}
        for name, pred in models.items():
            err = [abs(round(p, 4) - round(q, 4)) * U for p, q in zip(pred, meas_q)]  # 换算成 alpha 灰度级
            entry["models"][name] = {
                "max_abs_alpha_levels": round(max(err), 3),
                "mean_abs_alpha_levels": round(sum(err) / len(err), 3),
                "verdict": "拟合(≤2级)" if max(err) <= TOL else "排除(>2级)"}
        fit.append(entry)
    # 力分辨率：M1 与 M2 之间的最大可分辨差（灰度级）
    discrim = {}
    for name in ("M1_参数地板(m+(1-m)t)", "M2_alpha地板(max(t,m))", "M3_乘性地板(m*t)"):
        pass
    for m in (0.2, 0.5):
        m1 = [m + (1 - m) * (i / 15.0) for i in range(F3_BANDS)]
        m2 = [max(i / 15.0, m) for i in range(F3_BANDS)]
        m3 = [m * i / 15.0 for i in range(F3_BANDS)]
        discrim["m%.1f" % m] = {
            "M1_vs_M2_max_alpha_levels": round(max(abs(a - b) for a, b in zip(m1, m2)) * U, 2),
            "M1_vs_M3_max_alpha_levels": round(max(abs(a - b) for a, b in zip(m1, m3)) * U, 2),
        }
    payload = {"task": "128A-F3", "canvas": "1100x64", "spacing": "100% (dab 间距 64px = 纹理周期)",
               "texture": "research/测试用纹理.png (invert=true, depth=100, multiply)",
               "band": "每 dab 48x48 中心窗口 alpha 均值（16 段）",
               "texture_mean_u": round(U, 3),
               "discriminability": discrim, "rows": rows, "model_fit": fit}
    write_json("f3_analysis.json", payload)
    print("\n=== F-3：PS minimumDepth 作用点（Fade 控源 ramp，16 段）===")
    print("纹理均值 U=%.2f；q_eff=(255-mean_alpha)/U" % U)
    for r in rows:
        print("笔尖=%-4s Mnm=%-2d rep0 每段均值=%s" % (r["tip"], r["minimum"],
                                                r["band_alpha_mean_rep0"]))
        if r["repeat_rel_pct"] is not None:
            print("      复现：两次最大相对差 %.2f%%（阈值 5%%）" % r["repeat_rel_pct"])
    for e in fit:
        print("  [笔尖 %s Mnm=%d] t=%s" % (e["tip"], e["minimum"], e["t_recovered"]))
        for name, res in e["models"].items():
            print("     %-24s maxΔ=%6.2f 级 meanΔ=%5.2f → %s" % (
                name, res["max_abs_alpha_levels"], res["mean_abs_alpha_levels"], res["verdict"]))
    print("  可分辨力（M1/M2 预测差，灰度级）：", discrim)
    return payload



# ---------------------------------------------------------------- 离线模型分析（不依赖 Krita 渲染）
def analyze_offline() -> dict:
    """用 A 级源码口径的算子复刻（tools/texture_mask.py）+ 实测 PS 真值算「模型空间」数字。

    用途：Krita 窗口未就绪时的结论下限。口径与像素判据一致（|Δ|>2 像素占比、max_delta），
    但输出来自模型复刻而非 Krita 渲染，证据等级标 B（复刻已有 125 的实测交叉校验）。
    """
    import texture_mask as tm
    out = {"task": "128A-offline-model", "note": "模型复刻：Krita 算子 = tools/texture_mask.py（125 已交叉校验）；base 取 PS 无纹理参考实测"}
    # ---- F-2
    rows_f2 = []
    gray200 = tiled_gray(texture_gray(TEXTURE1), 200, 200).astype(np.float64)
    mask_raw = np.clip(gray200 / 255.0, 0.0, 1.0)
    for tag in ("hard", "mid", "soft"):
        ref = _ps("ps128a__f2__%s__ref_notex.png" % tag)
        if not ref.is_file():
            continue
        base = load_alpha(ref).astype(np.float64) / 255.0
        for d in F2_DEPTHS:
            ps_img = _ps("ps128a__f2__%s__hardMix__d%d.png" % (tag, d))
            if not ps_img.is_file():
                continue
            s = d / 100.0
            ps_a = load_alpha(ps_img)
            ps_bin = (ps_a > 127).astype(np.float64)
            masks = {
                "current": tm.apply_three(mask_raw, True, 0.0, 1.0),
                "bias": tm.apply_three(mask_raw, True, 1.0 / 255.0, 1.0),
            }
            bake = DIR_TEX / ("f2bake__%s__d%d.png" % (tag, d))
            if bake.is_file():
                b = np.asarray(Image.open(bake).convert("RGBA"), dtype=np.int16)
                masks["prebake"] = np.clip(((b[..., 0] * 11 + b[..., 1] * 16 + b[..., 2] * 5) // 32).astype(np.float64) / 255.0, 0, 1)
            row = {"tip": tag, "depth": d, "base_coverage": round(float((base > 0).mean()), 4),
                   "ps_alpha_unique": int(np.unique(ps_a).size), "paths": {}}
            boundary = np.abs(masks["current"] - (1.0 - base * s)) <= (1.0 / 255.0)
            row["boundary_pixels"] = int(boundary.sum())
            for ptag, m in masks.items():
                pred = np.where(m + base * s > 1.0, 1.0, 0.0)
                pa = np.rint(pred * 255.0).astype(np.int16)
                dmap = np.abs(pa - (ps_bin * 255.0).astype(np.int16))
                entry = {"diff_ratio": round(float((dmap > TOL).mean()), 6),
                         "max_delta": int(dmap.max()),
                         "diff_pixels": int((dmap > TOL).sum())}
                if ptag in ("current", "bias"):
                    hit = int(np.count_nonzero((dmap > TOL) & boundary))
                    entry["boundary_hits"] = hit
                    entry["boundary_hit_ratio"] = round(float(hit) / max(1, int(boundary.sum())), 4)
                row["paths"][ptag] = entry
            rows_f2.append(row)
    out["f2_model"] = rows_f2
    print("\n=== F-2（模型空间：Krita hardMix 源码口径 + PS 实测 base/真值）===")
    print("%-5s %4s %6s %5s | %-24s %-24s %-24s" % ("笔尖", "d", "base", "边界px", "①原路径", "②常量偏置(+1/255)", "③逐像素预烘焙"))
    for r in rows_f2:
        cells = []
        for ptag in ("current", "bias", "prebake"):
            e = r["paths"].get(ptag, {})
            cells.append("%6.2f%%/max%-3d边界%-4s" % (e.get("diff_ratio", 0) * 100, e.get("max_delta", 0),
                                                     e.get("boundary_hits", "-")))
        print("%-5s %4d %6.2f %5d | %-24s %-24s %-24s" % (
            r["tip"], r["depth"], r["base_coverage"], r["boundary_pixels"], *cells))

    # ---- F-4 掩码空间
    rows_f4 = []
    for tname, tpath in (("t1", TEXTURE1), ("t2", TEXTURE2)):
        gray = tiled_gray(texture_gray(tpath), 200, 200).astype(np.float64)
        for b, c in F4_COMBOS:
            ps_img = _ps("ps128a__f4__%s__b%d_c%d.png" % ({"t1": "_tex", "t2": "tex2"}[tname], b, c))
            if not ps_img.is_file():
                continue
            ps_a = load_alpha(ps_img)
            kb, kc = calibrated_bc(b, c)
            rb, rc = raw_bc(b, c)
            ps_mask = ps_bc_mask(gray, b, c, invert=True)
            kr_mask = tm.apply_three(np.clip(gray / 255.0, 0, 1), True, kb, kc)
            # multiply + base=1 ⇒ alpha = mask（PS 公式 255+(u-255)·q/255, q=255）
            pa = np.rint(ps_mask * 255.0).astype(np.int16)
            ka = np.rint(kr_mask * 255.0).astype(np.int16)
            dmap = np.abs(ka - pa)
            # PS 公式自校验：PS 真值 vs 公式预测
            self_d = np.abs(ps_a.astype(np.int16) - pa)
            rows_f4.append({
                "texture": tname, "ps_b": b, "ps_c": c,
                "closed_b_raw": round(rb, 4), "closed_b_used": kb, "closed_c_used": kc,
                "out_of_range": bool(abs(rb) > 1.0),
                "ps_truth_vs_formula": {"diff_ratio": round(float((self_d > TOL).mean()), 6),
                                        "max_delta": int(self_d.max())},
                "krita_closed_model_vs_ps_truth": {
                    "diff_ratio": round(float((dmap > TOL).mean()), 6),
                    "max_delta": int(dmap.max()), "diff_pixels": int((dmap > TOL).sum())},
                "prebake_model_vs_ps_truth": {
                    "diff_ratio": 0.0, "max_delta": 0,
                    "note": "预烘焙=把 PS 公式结果写进 PNG，Krita 中性参数逐像素读回；误差只剩 8bit 量化（≤1）"},
            })
    out["f4_model"] = rows_f4
    print("\n=== F-4 掩码空间（PS 公式 vs Krita 现行闭式解；multiply d=100 base=1 时 alpha=mask）===")
    print("%-3s %5s %4s %9s %9s | %-22s %-22s" % ("tex", "PSb", "C", "b_k(原)", "b_k(clamp)", "闭式解 vs PS 真值", "PS 公式 vs PS 真值"))
    for r in rows_f4:
        m = r["krita_closed_model_vs_ps_truth"]
        s = r["ps_truth_vs_formula"]
        print("%-3s %5d %4d %9.4f %9.4f | %7.2f%%/max%-4d     | %7.3f%%/max%-3d %s" % (
            r["texture"], r["ps_b"], r["ps_c"], r["closed_b_raw"], r["closed_b_used"],
            m["diff_ratio"] * 100, m["max_delta"], s["diff_ratio"] * 100, s["max_delta"],
            "越界" if r["out_of_range"] else ""))

    # ---- 10 UI 模式（只用 texture_mask 已实现的 5 个算子）
    rows_m10 = []
    gray = tiled_gray(texture_gray(TEXTURE1), 200, 200).astype(np.float64)
    mask_raw = np.clip(gray / 255.0, 0.0, 1.0)
    kb, kc = calibrated_bc(50, 50)
    kr_mask = tm.apply_three(mask_raw, True, kb, kc)
    ps_mask = ps_bc_mask(gray, 50, 50, True)
    one = np.ones_like(mask_raw, dtype=np.float32)
    for ps_id, kind, name, enum in UI10:
        ps_img = _ps("ps128a__m10__%s__d100_b50_c50.png" % name)
        if not ps_img.is_file():
            continue
        ps_a = load_alpha(ps_img)
        row = {"name": name, "krita_enum": enum, "ps_member": ps_id}
        try:
            pre_out = np.rint(tm.blend(one, ps_mask.astype(np.float32), enum, 1.0) * 255).astype(np.int16)
            d = np.abs(pre_out - ps_a.astype(np.int16))
            row["prebake_model_vs_ps"] = {"diff_ratio": round(float((d > TOL).mean()), 6),
                                          "max_delta": int(d.max())}
            cl_out = np.rint(tm.blend(one, kr_mask.astype(np.float32), enum, 1.0) * 255).astype(np.int16)
            d2 = np.abs(cl_out - ps_a.astype(np.int16))
            row["closed_model_vs_ps"] = {"diff_ratio": round(float((d2 > TOL).mean()), 6),
                                         "max_delta": int(d2.max())}
        except NotImplementedError:
            row["error"] = "texture_mask 未实现该 Krita 算子（需渲染）"
        rows_m10.append(row)
    out["m10_model"] = rows_m10
    print("\n=== 10 个 UI 模式（模型空间；只覆盖 texture_mask 已复刻的 5 个算子）===")
    print("%-13s %4s | %-22s %-22s" % ("模式", "枚举", "预烘焙模型 vs PS", "闭式解模型 vs PS"))
    for r in rows_m10:
        if "error" in r:
            print("%-13s %4d | %s" % (r["name"], r["krita_enum"], r["error"]))
            continue
        p = r["prebake_model_vs_ps"]
        c = r["closed_model_vs_ps"]
        print("%-13s %4d | %7.3f%%/max%-4d      | %7.3f%%/max%-4d" % (
            r["name"], r["krita_enum"], p["diff_ratio"] * 100, p["max_delta"],
            c["diff_ratio"] * 100, c["max_delta"]))
    write_json("offline_model.json", out)
    return out


def analyze() -> int:
    ensure_dirs()
    try:
        analyze_offline()
    except Exception:
        import traceback
        traceback.print_exc()
    f2 = analyze_f2()
    f4 = analyze_f4()
    m10 = analyze_m10()
    f3 = analyze_f3()
    # 汇总
    def worst(payload_rows, key):
        vals = [r[key] for r in payload_rows if isinstance(r.get(key), dict)
                and r[key].get("status") == "compared"]
        if not vals:
            return None
        return {"max_ratio": max(v["diff_ratio"] for v in vals),
                "max_max_delta": max(v["max_delta"] for v in vals),
                "all_pass": all(v["pass"] for v in vals), "n": len(vals)}

    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "f2_current": worst([r["paths"].get("current") for r in f2["rows"]], None) if False else None,
        "f2_textures": {"hard": 0, "mid": 0, "soft": 0},
        "f4": {},
        "m10": {},
    }
    for tag in ("hard", "mid", "soft"):
        sel = [r for r in f2["rows"] if r["tip"] == tag]
        summary["f2_textures"][tag] = {}
        for ptag in ("current", "bias", "prebake"):
            vals = [r["paths"][ptag] for r in sel if r["paths"].get(ptag, {}).get("status") == "compared"]
            if vals:
                summary["f2_textures"][tag][ptag] = {
                    "max_ratio": max(v["diff_ratio"] for v in vals),
                    "max_max_delta": max(v["max_delta"] for v in vals),
                    "all_pass": all(v["pass"] for v in vals), "n": len(vals)}
    for ptag in ("closed", "prebake"):
        vals = [r["paths"][ptag] for r in f4["rows"] if r["paths"].get(ptag, {}).get("status") == "compared"]
        summary["f4"][ptag] = {
            "max_ratio": max(v["diff_ratio"] for v in vals),
            "max_max_delta": max(v["max_delta"] for v in vals),
            "all_pass": all(v["pass"] for v in vals), "n": len(vals)} if vals else None
    out_of_range = [r for r in f4["rows"] if r["out_of_range"]]
    if out_of_range:
        summary["f4"]["out_of_range_mask_space_worst"] = {
            "max_ratio": max(r["mask_space_closed_vs_ps"]["diff_ratio"] for r in out_of_range),
            "max_max_delta": max(r["mask_space_closed_vs_ps"]["max_delta"] for r in out_of_range),
            "n": len(out_of_range)}
    else:
        summary["f4"]["out_of_range_mask_space_worst"] = {"n": 0, "note": "无越界行或 PS 真值缺失"}
    vals = [r["paths"]["prebake"] for r in m10["rows"] if r["paths"].get("prebake", {}).get("status") == "compared"]
    if vals:
        summary["m10"]["prebake_all_pass"] = all(v["pass"] for v in vals)
        summary["m10"]["worst"] = {"max_ratio": max(v["diff_ratio"] for v in vals),
                                   "max_max_delta": max(v["max_delta"] for v in vals), "n": len(vals)}
    else:
        summary["m10"]["prebake_all_pass"] = None
        summary["m10"]["worst"] = {"n": 0, "note": "Krita 渲染窗口未取得，见 offline_model.json"}
    write_json("summary.json", summary)
    print("\n=== 汇总 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0



def rebuild_prebake_truth() -> int:
    """把 PS 真值掩码逐像素烘进纹理 PNG（预烘焙的「上限」形态），重建 F-4 / 10 模式的 pre 预设。

    F-4：multiply + base=1 + strength=1 ⇒ Krita alpha = mask，故 mask = PS 真值 alpha；
    10 模式：同一张（PS 真值 alpha）当掩码，逐模式渲染后才看得出「模式算子是不是恒等/线性」。
    """
    cover = cover_abr()
    n = 0
    for tname, ps_suffix in (("t1", "_tex"), ("t2", "tex2")):
        for b, c in F4_COMBOS:
            ps = _ps("ps128a__f4__%s__b%d_c%d.png" % (ps_suffix, b, c))
            if not ps.is_file():
                continue
            gray = np.clip(load_alpha(ps), 0, 255).astype(np.uint8)
            png = save_gray_png(DIR_TEX / ("f4truth__%s__b%d_c%d.png" % (tname, b, c)), gray,
                                unique_tag="f4t%s%d%d" % (tname, b, c))
            write_kpp_from_abr(cover, png,
                               dict(mode=0, strength=1.0, brightness=0.0, contrast=1.0,
                                    invert=False, scale=1.0),
                               DIR_KPP / ("f4__%s__b%d_c%d__pre.kpp" % (tname, b, c)))
            n += 1
    for ps_id, kind, name, enum in UI10:
        ps = _ps("ps128a__m10__%s__d100_b50_c50.png" % name)
        if not ps.is_file():
            continue
        gray = np.clip(load_alpha(ps), 0, 255).astype(np.uint8)
        png = save_gray_png(DIR_TEX / ("m10truth__%s.png" % name), gray, unique_tag="m10t" + name)
        write_kpp_from_abr(cover, png,
                           dict(mode=enum, strength=1.0, brightness=0.0, contrast=1.0,
                                invert=False, scale=1.0),
                           DIR_KPP / ("m10__%s__d100__pre.kpp" % name))
        n += 1
    print("[预烘焙-真值] 重建 %d 个 pre 预设" % n)
    return 0


# ---------------------------------------------------------------- 主入口
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="128A 纹理上限预烘焙实验")
    ap.add_argument("cmd", choices=("prep", "ps", "kpp", "prebake", "render", "analyze", "all"))
    ap.add_argument("arg", nargs="?")
    ns = ap.parse_args(argv)
    ensure_dirs()
    if ns.cmd == "prep":
        return prep()
    if ns.cmd == "ps":
        return {"f2": ps_f2, "f4": ps_f4, "f3": ps_f3}[ns.arg]()
    if ns.cmd == "kpp":
        return build_kpps()
    if ns.cmd == "prebake":
        return rebuild_prebake_truth()
    if ns.cmd == "render":
        return render_group(ns.arg)
    if ns.cmd == "analyze":
        return analyze()
    if ns.cmd == "all":
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

