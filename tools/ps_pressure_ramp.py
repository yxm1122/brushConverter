# -*- coding: utf-8 -*-
"""PS 压感 ramp 装置（任务 143）：造「沿笔迹压力变化」的 PS 真值图。

**为什么是这个路线**（recon + 实测依据，详见 docs/验收报告/143-报告.md）：
- Photoshop Scripting 官方文档 PathItem.strokePath(ToolType tool, bool simulatePressure)
  有第二个布尔参数（「模拟压力」），旧 ps_export.jsx 只传了 tool；
- PS 2025 二进制里的 Action Manager 描述符规范含
  /stroke ... /pressure [($$$/Actions/Key/Stroke/SimulatePressure=simulate pressure) /boolean]
  即键名是 stringID "pressure"（不是 simulatePressure），作为对照路线验证。

本脚本负责：
1) 造压力夹具 ABR（计算笔刷 + bVTy=2 的尺寸/不透明度/纹理深度动态）：
   - p_size   尺寸动态随压感（用于标定「沿路径压力剖面」p(x)，宽度 = D·p）
   - p_opac   不透明度动态随压感（F-9 用）
   - p_tex    纹理深度动态随压感（F-3/B12 用；可选带 minimumDepth / Mnm）
   - 纹理是确定性 8px 棋盘（0/255 两值块），便于把「深度→alpha」逐块读出来
2) 以 job/done 协议启动 Photoshop（tools/ps_pressure_probe.jsx 先验证，正式批用
   tools/ps_pressure_ramp.jsx），等待 done.json（**判成败看 done.json，不看退出码**：
   PS 已有实例时 -r 会转发秒退）；
3) 分析 PNG：沿 x 的宽度/alpha 剖面、孤立 dab 的棋盘块 alpha、两次复现 sha256；
4) 落 .tmp/pressure/ps/（manifest.json 记任务/时间/命令/输入输出 sha256/统计量）。

渲染锁：本脚本自抢 .tmp/ps.lock（mkdir 粒度=一次批处理），已存在且目录 mtime<60 分钟
则等待（20s 重试），>60 分钟视为陈旧可删；结束时 rmdir。**持锁期间**才允许 taskkill
Photoshop.exe 清场（照 142 手册 §二.3）。

用法：
    python tools/ps_pressure_ramp.py abrs            # 造夹具 ABR
    python tools/ps_pressure_ramp.py probe           # 最小验证（路线接受性 + 压感是否生效）
    python tools/ps_pressure_ramp.py render --job ramp   # 正式压力 ramp 批（需先 abrs）
    python tools/ps_pressure_ramp.py analyze --dir <png 目录>
"""

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import make_test_abr as M  # noqa: E402

PS_EXE = Path(os.environ.get("BRUSHCONVERTER_PS", r"D:/program/PS2025/Adobe Photoshop 2025/Photoshop.exe"))
PROBE_JSX = HERE / "ps_pressure_probe.jsx"
RAMP_JSX = HERE / "ps_pressure_ramp.jsx"
PRESSURE_DIR = REPO_ROOT / ".tmp" / "pressure" / "ps"
WORK_DIR = PRESSURE_DIR / "_work"
ABR_DIR = PRESSURE_DIR / "abr"
LOCK_DIR = REPO_ROOT / ".tmp" / "ps.lock"
LOCK_STALE_SECONDS = 3600

# 夹具纹理：64x64、8px 棋盘（0 / 255 两值块）
TEXTURE_NAME = "bc_pressure_checker8"
TEXTURE_UUID = "11112222-3333-4444-5555-666677778888"
TEXTURE_SIZE = 64
TEXTURE_BLOCK = 8
CANVAS_PROBE = {"width": 900, "height": 300, "dpi": 72}
CANVAS_RAMP = {"width": 1600, "height": 400, "dpi": 72}


# ---------------------------------------------------------------- 通用工具


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def checker_pixels(size=TEXTURE_SIZE, block=TEXTURE_BLOCK):
    """64x64 灰度棋盘：((x//b + y//b) % 2 == 0) → 0，否则 255。"""
    out = bytearray(size * size)
    for y in range(size):
        for x in range(size):
            out[y * size + x] = 0 if ((x // block + y // block) % 2 == 0) else 255
    return bytes(out)


TEXTURE_PNG = PRESSURE_DIR / "texture" / "checker8.png"


def make_checker_png(path=TEXTURE_PNG, size=TEXTURE_SIZE, block=TEXTURE_BLOCK):
    """把棋盘写成 64x64 RGBA PNG（A=255）；PS 侧用「定义图案」注入，名称同 ABR 内嵌图案。"""
    import numpy as np
    from PIL import Image
    px = checker_pixels(size, block)
    rgb = np.frombuffer(px, dtype=np.uint8).reshape(size, size)
    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    for c in range(3):
        rgba[:, :, c] = rgb
    rgba[:, :, 3] = 255
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path)
    return path


def build_abr(path, *, name, diameter, hardness, spacing, angle=0.0, roundness=100.0,
              size_dyn=None, opac_dyn=None, tex=None, use_tip=False, use_paint=False):
    """从零拼一个「计算笔刷」ABR（可带压力动态）。

    size_dyn / opac_dyn: {"bvty": 2, "mnm": 0}（Mnm 写进动态对象）
    tex: {"depth":100, "minimum":0, "blend":"Mltp", "scale":100, "invert":False,
          "depth_bvty":2, "depth_mnm":0}
    """
    texture = None
    if tex is not None:
        texture = {
            "name": TEXTURE_NAME, "uuid": TEXTURE_UUID,
            "scale": float(tex.get("scale", 100.0)), "depth": float(tex.get("depth", 100.0)),
            "blend": tex.get("blend", "Mltp"), "invert": bool(tex.get("invert", False)),
        }
    preset = M.build_computed_preset(name, diameter, hardness, spacing, angle,
                                     roundness=roundness, texture=texture)
    items = preset.items
    # 动态对象：bVTy=2 = 压感；Mnm 是该动态的最小值（%）
    if size_dyn is not None:
        M._set_key(items, "szVr", M._dyn(bvty=size_dyn.get("bvty", 2), minimum=size_dyn.get("mnm", 0.0)))
        M._set_key(items, "minimumDiameter", M.untf(float(size_dyn.get("mnm", 0.0)), "#Prc"))
        M._set_key(items, "useTipDynamics", M.boolean(bool(use_tip)))
    if opac_dyn is not None:
        M._set_key(items, "opVr", M._dyn(bvty=opac_dyn.get("bvty", 2), minimum=opac_dyn.get("mnm", 0.0)))
        M._set_key(items, "usePaintDynamics", M.boolean(bool(use_paint)))
    if tex is not None:
        M._set_key(items, "textureDepthDynamics",
                   M._dyn(bvty=tex.get("depth_bvty", 2), minimum=tex.get("depth_mnm", 0.0)))
        M._set_key(items, "minimumDepth", M.untf(float(tex.get("minimum", 0.0)), "#Prc"))

    sections = [M.encode_section("samp", b"")]
    if tex is not None:
        record = M.encode_pattern_record(TEXTURE_NAME, TEXTURE_UUID,
                                         TEXTURE_SIZE, TEXTURE_SIZE, checker_pixels())
        sections.append(M.encode_section("patt", record))
    desc = M.encode_desc_section([("Brsh", M.vlls([preset]))])
    phry = M.build_phry(M.DEFAULT_GROUP_NAME, M.DEFAULT_GROUP_UUID, 1)
    sections.extend([M.encode_section("desc", desc), M.encode_section("phry", phry)])
    data = struct.pack(">HH", 6, 2) + b"".join(sections)
    path.write_bytes(data)
    return path


def readback_abr(path):
    """用本项目解析器回读关键字段（写进 manifest 的输入证据）。"""
    from brush_converter.abr import reader as R, descriptors as D
    af = R.AbrFile.parse(path)
    top = D.parse_descriptor_block(af.sections["desc"].data)
    p = D.iter_brush_presets(top)[0]
    br = D.get_obj(p, "Brsh")

    def dyn(key):
        obj = D.get_obj(p, key)
        if obj is None:
            return None
        out = {}
        for k, v in obj.items():
            out[k] = getattr(v, "value", None)
        return out

    info = {
        "name": D.get_text(p, "Nm  "),
        "diameter": D.get_number(br, "Dmtr"),
        "hardness": D.get_number(br, "Hrdn"),
        "spacing": D.get_number(br, "Spcn"),
        "useTexture": D.get_bool(p, "useTexture"),
        "textureDepth": D.get_number(p, "textureDepth"),
        "minimumDepth": D.get_number(p, "minimumDepth"),
        "textureScale": D.get_number(p, "textureScale"),
        "textureBlendMode": (p.get("textureBlendMode").value if p.get("textureBlendMode") else None),
        "size_dyn": dyn("szVr"),
        "opacity_dyn": dyn("opVr"),
        "texture_depth_dyn": dyn("textureDepthDynamics"),
        "minimumDiameter": D.get_number(p, "minimumDiameter"),
        "useTipDynamics": D.get_bool(p, "useTipDynamics"),
        "usePaintDynamics": D.get_bool(p, "usePaintDynamics"),
    }
    return info


# ---------------------------------------------------------------- 渲染锁


def _lock_age_seconds():
    try:
        return time.time() - LOCK_DIR.stat().st_mtime
    except FileNotFoundError:
        return None


_LOCK_HELD = False


def acquire_lock(wait_timeout=1200, poll=20):
    """抢 .tmp/ps.lock；陈旧（>60 分钟）可删。返回 True=持锁（本进程真的创建了该目录）。"""
    global _LOCK_HELD
    started = time.time()
    while True:
        try:
            LOCK_DIR.mkdir(parents=True)
            (LOCK_DIR / "owner.txt").write_text(
                "task-5/143 ps_pressure_ramp pid=%d at=%s\n" % (os.getpid(), time.strftime("%Y-%m-%dT%H:%M:%S")),
                encoding="utf-8")
            _LOCK_HELD = True
            return True
        except FileExistsError:
            age = _lock_age_seconds()
            if age is not None and age > LOCK_STALE_SECONDS:
                print("[锁] .tmp/ps.lock 已存在 %.0f 分钟（>60 分钟视为陈旧），删除后重抢" % (age / 60.0))
                shutil.rmtree(LOCK_DIR, ignore_errors=True)
                continue
            if time.time() - started > wait_timeout:
                raise TimeoutError("等待 .tmp/ps.lock 超过 %ss" % wait_timeout)
            print("[锁] .tmp/ps.lock 被占用（%.0f 秒前），%ss 后重试" % (age or 0, poll))
            time.sleep(poll)


def release_lock():
    """只释放自己抢到的锁（别人持锁时等待超时，绝不能删他人目录）。"""
    global _LOCK_HELD
    if not _LOCK_HELD:
        return
    shutil.rmtree(LOCK_DIR, ignore_errors=True)
    _LOCK_HELD = False


def photoshop_running():
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True, errors="ignore", timeout=30).stdout
    except Exception:
        return False
    return "Photoshop.exe" in out


def kill_photoshop():
    subprocess.run(["taskkill", "-F", "-IM", "Photoshop.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def snapshot_tasklist(tag):
    """跑前/跑后 tasklist 留证（手册 §二.3）。"""
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True, errors="ignore", timeout=30).stdout
    except Exception as exc:
        out = "tasklist 调用失败：%r" % (exc,)
    rows = [line for line in out.splitlines()
            if any(k in line.lower() for k in ("photoshop", "krita", "kritarunner"))]
    path = PRESSURE_DIR / ("tasklist_%s.txt" % tag)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print("[tasklist:%s] %s" % (tag, " / ".join(rows) if rows else "<无 PS/Krita 进程>"))
    return rows


def run_jsx(jsx, job, work_dir, log_name, timeout=420, kill_existing=True):
    """写 job.json → 启动 PS -r jsx → 等 done.json。返回 (done_json, 元信息)。

    判成败只看 done.json（PS 已有实例时 -r 会转发秒退）。
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    for name in ("done.json", "batch.log"):
        p = work_dir / name
        if p.exists():
            p.unlink()
    (work_dir / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    if kill_existing:
        kill_photoshop()
        time.sleep(6.0)  # 强杀后立刻重启会卡在恢复/启动对话框（exp_texture_bake 实测 2026-11-23）
    if not PS_EXE.is_file():
        raise FileNotFoundError("找不到 Photoshop：%s" % PS_EXE)
    env = os.environ.copy()
    env["BRUSHCONVERTER_REPO"] = REPO_ROOT.as_posix()
    env["BRUSHCONVERTER_PS_WORK"] = work_dir.as_posix()
    env["PYTHONIOENCODING"] = "utf-8"
    snapshot_tasklist(log_name + "_before")
    started = time.time()
    done_path = work_dir / "done.json"
    attempts = 2 if kill_existing else 1
    proc = None
    for attempt in range(attempts):
        proc = subprocess.Popen([str(PS_EXE), "-r", str(jsx)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if done_path.exists():
                time.sleep(0.8)
                break
            if proc.poll() is not None and not done_path.exists():
                time.sleep(2.0)
                break
            time.sleep(1.0)
        if done_path.exists():
            break
        if attempt + 1 < attempts:
            print("[PS] 第 %d 次启动未产出 done.json（可能卡在恢复/启动对话框），清理后重试" % (attempt + 1))
            kill_photoshop()
            time.sleep(10.0)
        else:
            kill_photoshop()
            raise TimeoutError("等待 %ss 仍未生成 done.json，见 %s" % (timeout, work_dir / "batch.log"))
    elapsed = round(time.time() - started, 2)
    done = json.loads(done_path.read_text(encoding="utf-8"))
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    if kill_existing:
        kill_photoshop()
        time.sleep(1.5)
    snapshot_tasklist(log_name + "_after")
    meta = {"jsx": jsx.name, "work_dir": work_dir.as_posix(), "elapsed_s": elapsed,
            "job_sha256": sha256_file(work_dir / "job.json"),
            "done_sha256": sha256_file(done_path)}
    return done, meta


# ---------------------------------------------------------------- 夹具 ABR


def build_fixture_abrs():
    ABR_DIR.mkdir(parents=True, exist_ok=True)
    fixtures = []

    p = ABR_DIR / "p_size.abr"
    build_abr(p, name="bc_p_size", diameter=80, hardness=100, spacing=2.0,
              size_dyn={"bvty": 2, "mnm": 0.0}, use_tip=True)
    fixtures.append(p)

    p = ABR_DIR / "p_size_min50.abr"
    build_abr(p, name="bc_p_size_min50", diameter=80, hardness=100, spacing=2.0,
              size_dyn={"bvty": 2, "mnm": 50.0}, use_tip=True)
    fixtures.append(p)

    # 孤立 dab 组：间距 150%（80px 直径 → 120px 步长），沿路径压力在 dab 中心为 11 档
    for fname, bname, mnm in (("p_opac.abr", "bc_p_opac", 0.0),
                              ("p_opac_min20.abr", "bc_p_opac_min20", 20.0),
                              ("p_opac_min50.abr", "bc_p_opac_min50", 50.0)):
        p = ABR_DIR / fname
        build_abr(p, name=bname, diameter=80, hardness=100, spacing=150.0,
                  opac_dyn={"bvty": 2, "mnm": mnm}, use_paint=True)
        fixtures.append(p)

    for fname, bname, mnm in (("p_tex_min0.abr", "bc_p_tex_min0", 0.0),
                              ("p_tex_min20.abr", "bc_p_tex_min20", 20.0),
                              ("p_tex_min50.abr", "bc_p_tex_min50", 50.0)):
        p = ABR_DIR / fname
        build_abr(p, name=bname, diameter=80, hardness=100, spacing=150.0,
                  tex={"depth": 100, "minimum": mnm, "blend": "Mltp", "depth_mnm": mnm})
        fixtures.append(p)

    # 静态深度对照组：验证 minimumDepth 对「无动态」的静态深度是否夹参数/夹 alpha
    for fname, bname, dep, md in (("p_static_d0_md50.abr", "bc_p_static_d0_md50", 0.0, 50.0),
                                  ("p_static_d100_md50.abr", "bc_p_static_d100_md50", 100.0, 50.0),
                                  ("p_static_d50_md50.abr", "bc_p_static_d50_md50", 50.0, 50.0),
                                  ("p_static_d50_md0.abr", "bc_p_static_d50_md0", 50.0, 0.0)):
        p = ABR_DIR / fname
        build_abr(p, name=bname, diameter=80, hardness=100, spacing=150.0,
                  tex={"depth": dep, "minimum": md, "blend": "Mltp",
                       "depth_mnm": md, "depth_bvty": 0})
        fixtures.append(p)

    for fname, bname, mnm in (("p_hardmix_min0.abr", "bc_p_hardmix_min0", 0.0),
                              ("p_hardmix_min50.abr", "bc_p_hardmix_min50", 50.0)):
        p = ABR_DIR / fname
        build_abr(p, name=bname, diameter=80, hardness=100, spacing=150.0,
                  tex={"depth": 100, "minimum": mnm, "blend": "hardMix", "depth_mnm": mnm})
        fixtures.append(p)

    p = ABR_DIR / "p_tex_cont.abr"
    build_abr(p, name="bc_p_tex_cont", diameter=80, hardness=100, spacing=2.0,
              tex={"depth": 100, "minimum": 0, "blend": "Mltp", "depth_mnm": 0.0})
    fixtures.append(p)

    png = make_checker_png()
    manifest = {"task": "143", "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "texture": {"name": TEXTURE_NAME, "uuid": TEXTURE_UUID,
                            "size": TEXTURE_SIZE, "block": TEXTURE_BLOCK,
                            "pixels_sha256": sha256_text(checker_pixels().hex()),
                            "png": png.as_posix(), "png_sha256": sha256_file(png)},
                "abrs": []}
    for path in fixtures:
        info = readback_abr(path)
        info["file"] = path.name
        info["bytes"] = path.stat().st_size
        info["sha256"] = sha256_file(path)
        manifest["abrs"].append(info)
        print("[ABR] %-18s %6d B  %s" % (path.name, path.stat().st_size, info["name"]))
    (ABR_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    print("夹具清单已写：%s" % (ABR_DIR / "manifest.json"))
    return manifest


# ---------------------------------------------------------------- 分析


def load_alpha(path):
    import numpy as np
    from PIL import Image
    with Image.open(path) as im:
        arr = np.array(im.convert("RGBA"))
    return arr[:, :, 3]


def profile_columns(alpha):
    """沿 x 的列剖面：宽度（alpha>0 的行数）、alpha 均值（仅非零）、alpha 最大。"""
    import numpy as np
    nz = alpha > 0
    widths = nz.sum(axis=0)
    sums = alpha.sum(axis=0).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        means = np.where(widths > 0, sums / np.maximum(widths, 1), 0.0)
    return {
        "width": widths.tolist(),
        "mean_alpha": [round(float(v), 2) for v in means.tolist()],
        "max_alpha": alpha.max(axis=0).tolist(),
        "nonempty_cols": int((widths > 0).sum()),
    }


def summarize_profile(prof, min_width=1):
    """把列剖面压成若干可报数字：端点/中点宽度、宽度极值、alpha 极值。"""
    import numpy as np
    w = np.asarray(prof["width"], dtype=float)
    m = np.asarray(prof["mean_alpha"], dtype=float)
    cols = np.nonzero(w >= min_width)[0]
    if cols.size == 0:
        return {"ok": False, "reason": "无有效列"}
    x0, x1 = int(cols[0]), int(cols[-1])
    span = np.arange(x0, x1 + 1)
    half = x0 + (x1 - x0) // 2
    return {
        "ok": True,
        "x_first": x0, "x_last": x1,
        "width_at_x0": float(w[x0]), "width_at_mid": float(w[half]), "width_at_x1": float(w[x1]),
        "width_max": float(w[span].max()), "width_argmax_x": int(span[int(w[span].argmax())]),
        "mean_alpha_max": round(float(m[span].max()), 2),
        "mean_alpha_at_x0": round(float(m[x0]), 2),
        "mean_alpha_at_x1": round(float(m[x1]), 2),
        "width_profile_sha256": sha256_text(json.dumps([round(float(v), 3) for v in w[span].tolist()])),
    }


def block_analysis(alpha, texture_block=TEXTURE_BLOCK, phase=(0, 0)):
    """按「纹理棋盘块」聚合 alpha：区分 mask=0 块与 mask=255 块。"""
    import numpy as np
    h, w = alpha.shape
    b = texture_block
    px, py = phase
    yy, xx = np.mgrid[0:h, 0:w]
    is_dark = (((xx - px) // b + (yy - py) // b) % 2 == 0)
    nz = alpha > 0
    inner = nz.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            shifted = np.zeros_like(nz)
            ys = slice(max(0, dy), h + min(0, dy))
            xs = slice(max(0, dx), w + min(0, dx))
            ays = slice(max(0, -dy), h + min(0, -dy))
            axs = slice(max(0, -dx), w + min(0, -dx))
            shifted[ays, axs] = nz[ys, xs]
            inner &= shifted
    bx = (xx - px) % b
    by = (yy - py) % b
    block_inner = (bx > 0) & (bx < b - 1) & (by > 0) & (by < b - 1)
    sel = inner & block_inner
    out = {"block": b, "phase": [int(px), int(py)], "inner_pixels": int(sel.sum())}
    for tag, mask in (("dark", is_dark), ("light", ~is_dark)):
        vals = alpha[sel & mask]
        if vals.size == 0:
            out[tag] = None
            continue
        out[tag] = {
            "n": int(vals.size),
            "mean": round(float(vals.mean()), 2),
            "min": int(vals.min()), "max": int(vals.max()),
            "p10": int(np.percentile(vals, 10)), "p50": int(np.percentile(vals, 50)),
            "p90": int(np.percentile(vals, 90)),
        }
    return out


def detect_phase(alpha, texture_block=TEXTURE_BLOCK, min_alpha=250):
    """从图里回读棋盘相位：alpha≈255（light 块）的像素落在 mask=255 块。"""
    import numpy as np
    h, w = alpha.shape
    b = texture_block
    yy, xx = np.mgrid[0:h, 0:w]
    bright = alpha >= min_alpha
    best = None
    for py in (0, b // 2):
        for px in (0, b // 2):
            light = (((xx - px) // b + (yy - py) // b) % 2 == 1)
            hit = int((bright & light).sum())
            miss = int((bright & ~light).sum())
            score = hit - miss
            if best is None or score > best[0]:
                best = (score, (px, py), hit, miss)
    return {"phase": best[1], "score": best[0], "hit": best[2], "miss": best[3]}


def analyze_pngs(png_paths, out_json, texture_block=TEXTURE_BLOCK):
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "images": []}
    for path in png_paths:
        path = Path(path)
        alpha = load_alpha(path)
        prof = profile_columns(alpha)
        entry = {
            "file": path.name,
            "sha256": sha256_file(path),
            "shape": list(alpha.shape),
            "alpha_nonzero": int((alpha > 0).sum()),
            "alpha_mean_nonzero": round(float(alpha[alpha > 0].mean()), 2) if (alpha > 0).any() else 0.0,
            "profile_summary": summarize_profile(prof),
            "profile": prof,
        }
        try:
            phase = detect_phase(alpha)
            entry["phase"] = phase
            entry["blocks"] = block_analysis(alpha, texture_block, phase["phase"])
        except Exception as exc:  # noqa: BLE001
            entry["block_error"] = repr(exc)
        report["images"].append(entry)
    out_json = Path(out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("分析已写：%s（%d 张）" % (out_json, len(report["images"])))
    return report


# ---------------------------------------------------------------- 子命令


def cmd_abrs(_ns):
    build_fixture_abrs()
    return 0


def cmd_probe(ns):
    """最小验证：不带 ABR 的默认画笔 + p_size 夹具，跑 4 个变体。"""
    acquire_lock()
    try:
        results = {}
        work_a = WORK_DIR / "probe_default"
        out_a = PRESSURE_DIR / "probe_default"
        out_a.mkdir(parents=True, exist_ok=True)
        job_a = {"out_dir": out_a.as_posix(),
                 "canvas": CANVAS_PROBE,
                 "path": {"n": 2, "x0": 70, "y0": 150, "x1": 830, "y1": 150},
                 "variants": ["plain", "false", "true", "am_pressure"]}
        done_a, meta_a = run_jsx(PROBE_JSX, job_a, work_a, "probe_default")
        results["default_brush"] = {"done": done_a, "meta": meta_a}
        print("[probe default] ps=%s abr=%s" % (done_a.get("ps_version"), done_a.get("abr")))
        for r in done_a.get("results", []):
            print("   %-12s ok=%s err=%s" % (r["variant"], r["ok"], r["error"][:120]))

        abr_size = ABR_DIR / "p_size.abr"
        if not abr_size.is_file():
            build_fixture_abrs()
        work_b = WORK_DIR / "probe_size"
        out_b = PRESSURE_DIR / "probe_size"
        out_b.mkdir(parents=True, exist_ok=True)
        job_b = {"out_dir": out_b.as_posix(),
                 "canvas": CANVAS_PROBE,
                 "abr_path": abr_size.as_posix(),
                 "brush_name": "bc_p_size",
                 "path": {"n": 2, "x0": 70, "y0": 150, "x1": 830, "y1": 150},
                 "variants": ["plain", "true"]}
        done_b, meta_b = run_jsx(PROBE_JSX, job_b, work_b, "probe_size")
        results["size_brush"] = {"done": done_b, "meta": meta_b}
        print("[probe size] abr=%s brush=%s" % (done_b.get("abr"), done_b.get("brush")))
        for r in done_b.get("results", []):
            print("   %-12s ok=%s err=%s rb=%s" % (r["variant"], r["ok"], r["error"][:100],
                                                    r["readback"][:200]))

        done_b2, meta_b2 = run_jsx(PROBE_JSX, job_b, work_b, "probe_size_repeat")
        results["size_brush_repeat"] = {"done": done_b2, "meta": meta_b2}

        pngs = sorted(out_a.glob("*.png")) + sorted(out_b.glob("*.png"))
        analysis = analyze_pngs(pngs, PRESSURE_DIR / "probe_analysis.json")
        rep = {}
        for name in ("plain.png", "true.png"):
            p = out_b / name
            rep[name] = sha256_file(p) if p.is_file() else None
        results["repeat_sha256_second_pass"] = rep
        manifest = {
            "task": "143", "kind": "ps-pressure-probe",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "commands": ["python tools/ps_pressure_ramp.py abrs",
                         "python tools/ps_pressure_ramp.py probe"],
            "inputs": {"probe_jsx_sha256": sha256_file(PROBE_JSX),
                       "abr_manifest": json.loads((ABR_DIR / "manifest.json").read_text(encoding="utf-8"))
                       if (ABR_DIR / "manifest.json").is_file() else None},
            "outputs": {str(p.relative_to(REPO_ROOT)).replace("\\", "/"): sha256_file(p)
                        for p in pngs},
            "runs": results,
            "analysis_file": "probe_analysis.json",
        }
        (PRESSURE_DIR / "manifest_probe.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        print("manifest 已写：%s" % (PRESSURE_DIR / "manifest_probe.json"))
        for img in analysis["images"]:
            s = img["profile_summary"]
            if not s.get("ok"):
                print("  %-14s 空图" % img["file"])
                continue
            print("  %-14s x=%d..%d 宽 x0/mid/x1=%.1f/%.1f/%.1f max=%.1f 均值alpha max=%s"
                  % (img["file"], s["x_first"], s["x_last"], s["width_at_x0"], s["width_at_mid"],
                     s["width_at_x1"], s["width_max"], s["mean_alpha_max"]))
        return 0
    finally:
        release_lock()


# ---------------------------------------------------------------- 正式 ramp 批


def column_runs(widths, min_width=2):
    """把列剖面切成连续段（孤立 dab 或连续笔迹块），返回 [(start,end)]。"""
    runs = []
    in_run = False
    start = None
    for x, w in enumerate(widths):
        if w >= min_width and not in_run:
            in_run = True
            start = x
        elif w < min_width and in_run:
            in_run = False
            runs.append((start, x - 1))
    if in_run:
        runs.append((start, len(widths) - 1))
    return runs


def dab_stats(alpha, x0, x1, y_center, half=20):
    """孤立 dab 的中心区统计：alpha 中位数/p10/p90/255 占比。

    p10 用作「棋盘暗块（mask=0）的 alpha」→ multiply 律下 = 255·(1−depth_eff)；
    中位数用于无纹理的不透明度动态（整块同值）。
    """
    import numpy as np
    h = alpha.shape[0]
    ys = max(0, y_center - half)
    ye = min(h, y_center + half + 1)
    xs = max(0, x0)
    xe = min(alpha.shape[1], x1 + 1)
    patch = alpha[ys:ye, xs:xe].ravel()
    if patch.size == 0:
        return None
    return {
        "x0": int(x0), "x1": int(x1), "x_center": round((x0 + x1) / 2.0, 1),
        "n": int(patch.size),
        "median": float(np.median(patch)),
        "mean": round(float(patch.mean()), 2),
        "p10": float(np.percentile(patch, 10)),
        "p25": float(np.percentile(patch, 25)),
        "p90": float(np.percentile(patch, 90)),
        "frac255": round(float((patch >= 254).mean()), 4),
        "frac0": round(float((patch == 0).mean()), 4),
    }


def pressure_curve_from_size(entry, diameter=80.0):
    """由尺寸动态笔迹的列宽剖面反演压力曲线 p(x)。

    口径：p = (width − w_min) / (w_max − w_min)。w_min 取笔迹首列宽度（压力≈0 端），
    w_max 取全程最大列宽；尺寸动态 szVr.bVTy=2 的线性度见 143 报告 §证据。
    """
    import numpy as np
    w = np.asarray(entry["profile"]["width"], dtype=float)
    s = entry["profile_summary"]
    if not s.get("ok"):
        return None
    x0, x1 = s["x_first"], s["x_last"]
    span = w[x0:x1 + 1]
    w_min = float(np.median(span[:5])) if span.size >= 5 else float(span.min())
    w_max = float(span.max())
    if w_max <= w_min:
        return None
    p = np.clip((w - w_min) / (w_max - w_min), 0.0, 1.0)
    return {"x0": int(x0), "x1": int(x1), "w_min": w_min, "w_max": w_max,
            "diameter": diameter,
            "p": [round(float(v), 4) for v in p.tolist()]}


def pressure_at(curve, x):
    """取压力曲线在 x 处的值；超出范围用端点值。"""
    if curve is None:
        return None
    p = curve["p"]
    x0, x1 = curve["x0"], curve["x1"]
    if x < x0:
        return p[x0]
    if x > x1:
        return p[x1]
    return p[int(round(x))]


def _dab_series(alpha, y_center, min_run=20, patch_half=16, patch_half_w=16):
    """逐 dab 提取 (中心x, dark_p5, light_p95, median, frac255, frac0)。"""
    import numpy as np
    cols = (alpha > 0).sum(axis=0)
    runs = column_runs(cols, min_width=2)
    out = []
    for (s, e) in runs:
        if e - s < min_run:
            continue
        c = (s + e) // 2
        patch = alpha[max(0, y_center - patch_half):y_center + patch_half + 1,
                      max(0, c - patch_half_w):c + patch_half_w + 1]
        if patch.size == 0:
            continue
        out.append({
            "x": int(c),
            "dark_p5": float(np.percentile(patch, 5)),
            "light_p95": float(np.percentile(patch, 95)),
            "median": float(np.median(patch)),
            "frac255": round(float((patch >= 254).mean()), 4),
            "frac0": round(float((patch == 0).mean()), 4),
        })
    return out


def analyze_ramp(dir_path, out_json, y_center=None, pressure_curve=None):
    """分析 ramp 批：尺寸剖面 → p(x)；逐 dab → alpha/纹理深度；两模型残差。"""
    import numpy as np
    dir_path = Path(dir_path)
    pngs = sorted(dir_path.glob("*.png"))
    base = analyze_pngs(pngs, out_json)
    by_file = {e["file"]: e for e in base["images"]}
    if y_center is None:
        y_center = CANVAS_RAMP["height"] // 2
    curve = pressure_curve
    if curve is None and "ramp_size_true.png" in by_file:
        curve = pressure_curve_from_size(by_file["ramp_size_true.png"], 80.0)
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "dir": dir_path.as_posix(), "pressure_curve": curve,
              "dabs": {}, "depth_model": {}, "images": {}}
    for name in sorted(by_file):
        entry = by_file[name]
        alpha = load_alpha(dir_path / name)
        dabs = _dab_series(alpha, y_center)
        for d in dabs:
            d["pressure"] = pressure_at(curve, d["x"])
        report["dabs"][name] = dabs
        report["images"][name] = {
            "sha256": entry["sha256"], "alpha_nonzero": entry["alpha_nonzero"],
            "profile_summary": entry["profile_summary"],
            "profile": entry["profile"],  # 列宽/列均值原始序列（报告取数用）
            "phase": entry.get("phase"),
            "blocks_global": entry.get("blocks"),
        }
    # —— 纹理深度：dark alpha → depth_eff = (255 − dark)/2.55（multiply 律，143 §证据）——
    def depth_eff(dark):
        return round((255.0 - dark) / 2.55, 1)

    model = {}
    for name in ("ramp_tex_m0.png", "ramp_tex_m20.png", "ramp_tex_m50.png"):
        if name not in report["dabs"]:
            continue
        ds = [d for d in report["dabs"][name] if d.get("pressure") is not None]
        m = {"mnm": int(name.split("_m")[-1].split(".")[0]), "points": []}
        for d in ds:
            m["points"].append({"x": d["x"], "p": d["pressure"],
                                "dark": d["dark_p5"], "depth_eff": depth_eff(d["dark_p5"])})
        model[name] = m
    # 拟合 D_eff(p) = t + (D − t)(1 − m/100)(1 − p)（143 实测形态，t=minimumDepth, D=textureDepth）
    fits = {}
    for name, m in model.items():
        if not m["points"]:
            continue
        mm = m["mnm"] / 100.0
        pts = [(q["p"], q["depth_eff"]) for q in m["points"]]
        D, t = 100.0, float(m["mnm"])
        preds = [t + (D - t) * (1 - mm) * (1 - p) for p, _ in pts]
        resid = [round(q[1] - pr, 1) for q, pr in zip(pts, preds)]
        fits[name] = {"D": D, "t": t, "mnm": m["mnm"],
                      "residual_abs_max": max(abs(r) for r in resid) if resid else None,
                      "residual_mean": round(float(np.mean([abs(r) for r in resid])), 2) if resid else None,
                      "residuals": resid}
    report["depth_model"] = fits
    # —— 跨会话/同会话复现：pixel diff（tol=2 口径）——
    report["compare"] = {}
    out_json = Path(out_json)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ramp 分析已写：%s" % out_json)
    return report


def pixel_diff(path_a, path_b, tol=2):
    """两图 alpha 平面的像素差：差异像素比例、max_delta、平均绝对差。"""
    import numpy as np
    a = load_alpha(path_a).astype(np.int16)
    b = load_alpha(path_b).astype(np.int16)
    if a.shape != b.shape:
        return {"shape_mismatch": [list(a.shape), list(b.shape)]}
    d = np.abs(a - b)
    total = d.size
    return {"diff_pixels": int((d > tol).sum()), "total": int(total),
            "diff_ratio_pct": round(100.0 * float((d > tol).sum()) / total, 4),
            "max_delta": int(d.max()), "mean_abs_delta": round(float(d.mean()), 4),
            "pixels_any_diff": int((d > 0).sum()),
            "sha256_equal": sha256_file(path_a) == sha256_file(path_b)}


RAMP_ITEMS = [
    # —— 143 装置本体：压力剖面 + 相邻档差异 + 同档复现 ——
    ("ramp_size_true.png", "bc_p_size", "true", {}),
    ("ramp_size_true_dup.png", "bc_p_size", "true", {}),
    ("ramp_size_plain.png", "bc_p_size", "plain", {}),
    ("ramp_size_min50_true.png", "bc_p_size_min50", "true", {}),
    ("ramp_opac_m0.png", "bc_p_opac", "true", {}),
    ("ramp_opac_m0_dup.png", "bc_p_opac", "true", {}),
    # —— 146 F-9：不透明度动态 Mnm（0/20/50）+ 基准不透明度 50 的量纲判定 ——
    ("ramp_opac_m20.png", "bc_p_opac_min20", "true", {}),
    ("ramp_opac_m50.png", "bc_p_opac_min50", "true", {}),
    ("ramp_opac_m0_base50.png", "bc_p_opac", "true", {"opacity": 50}),
    ("ramp_opac_m50_base50.png", "bc_p_opac_min50", "true", {"opacity": 50}),
    # —— 145 F-3：纹理深度动态 Mnm（0/20/50）+ 静态对照组 ——
    ("ramp_tex_m0.png", "bc_p_tex_min0", "true", {"use_texture": True}),
    ("ramp_tex_m0_dup.png", "bc_p_tex_min0", "true", {"use_texture": True}),
    ("ramp_tex_m20.png", "bc_p_tex_min20", "true", {"use_texture": True}),
    ("ramp_tex_m50.png", "bc_p_tex_min50", "true", {"use_texture": True}),
    ("ramp_static_d0_md50.png", "bc_p_static_d0_md50", "true", {"use_texture": True}),
    ("ramp_static_d100_md50.png", "bc_p_static_d100_md50", "true", {"use_texture": True}),
    ("ramp_static_d50_md50.png", "bc_p_static_d50_md50", "true", {"use_texture": True}),
    ("ramp_static_d50_md0.png", "bc_p_static_d50_md0", "true", {"use_texture": True}),
    # —— 147 B12：hardMix + 压感（Mnm 0/50）——
    ("ramp_hd_m0.png", "bc_p_hardmix_min0", "true", {"use_texture": True}),
    ("ramp_hd_m0_dup.png", "bc_p_hardmix_min0", "true", {"use_texture": True}),
    ("ramp_hd_m50.png", "bc_p_hardmix_min50", "true", {"use_texture": True}),
]

# 跨会话复现只跑这几张（时间/会话启动成本）
REPEAT_FILES = ("ramp_size_true.png", "ramp_opac_m0.png", "ramp_tex_m0.png", "ramp_hd_m0.png")


def build_ramp_job(out_dir, only=None):
    items = []
    for file, brush, stroke, opts in RAMP_ITEMS:
        if only is not None and file not in only:
            continue
        item = {"file": file, "brush_name": brush, "stroke": stroke, "opts": dict(opts)}
        items.append(item)
    return {
        "out_dir": out_dir.as_posix(),
        "canvas": CANVAS_RAMP,
        "texture_path": make_checker_png().as_posix(),
        "texture_name": TEXTURE_NAME,
        "abr_path": (ABR_DIR / "p_size.abr").as_posix(),
        "abr_paths": sorted(p.as_posix() for p in ABR_DIR.glob("p_*.abr")),
        "path": {"n": 2, "x0": 100, "y0": CANVAS_RAMP["height"] // 2,
                 "x1": 1500, "y1": CANVAS_RAMP["height"] // 2},
        "items": items,
    }


def cmd_ramp(ns):
    acquire_lock()
    try:
        if not (ABR_DIR / "manifest.json").is_file():
            build_fixture_abrs()
        out_dir = PRESSURE_DIR / "ramp"
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in out_dir.glob("*.png"):
            p.unlink()
        job = build_ramp_job(out_dir)
        work = WORK_DIR / "ramp"
        done, meta = run_jsx(RAMP_JSX, job, work, "ramp", timeout=480)
        okn = sum(1 for r in done.get("results", []) if r["ok"])
        print("[ramp] ps=%s items=%d ok=%d abr=%s" % (
            done.get("ps_version"), len(done.get("results", [])), okn, done.get("abr")))
        for r in done.get("results", []):
            if not r["ok"]:
                print("  FAIL", r["file"], r["error"][:200])
        # 跨会话复现：只跑 REPEAT_FILES 到独立目录
        out_dir2 = PRESSURE_DIR / "ramp_repeat"
        out_dir2.mkdir(parents=True, exist_ok=True)
        for p in out_dir2.glob("*.png"):
            p.unlink()
        job2 = build_ramp_job(out_dir2, only=set(REPEAT_FILES))
        done2, meta2 = run_jsx(RAMP_JSX, job2, WORK_DIR / "ramp_repeat", "ramp_repeat", timeout=300)
        print("[ramp repeat] ok=%d" % sum(1 for r in done2.get("results", []) if r["ok"]))
        # 分析
        report = analyze_ramp(out_dir, PRESSURE_DIR / "ramp_analysis.json")
        report2 = analyze_ramp(out_dir2, PRESSURE_DIR / "ramp_repeat_analysis.json",
                              pressure_curve=report["pressure_curve"])
        # 同会话重复项（*_dup）逐像素差
        within = {}
        for name in sorted(report["images"]):
            if "_dup" not in name:
                continue
            orig = name.replace("_dup", "")
            if (out_dir / orig).is_file() and (out_dir / name).is_file():
                within[orig + " vs " + name] = pixel_diff(out_dir / orig, out_dir / name)
        # 跨会话像素差
        cross = {}
        for name in REPEAT_FILES:
            a = out_dir / name
            b = out_dir2 / name
            if a.is_file() and b.is_file():
                cross[name] = pixel_diff(a, b)
        # 关键曲线抽取（报告数字）
        def series(name, key):
            return [(d["x"], d.get("pressure"), d[key]) for d in report["dabs"].get(name, [])]

        summary = {
            "task": "143", "kind": "ps-pressure-ramp",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "commands": ["python tools/ps_pressure_ramp.py abrs",
                         "python tools/ps_pressure_ramp.py ramp"],
            "inputs": {"ramp_jsx_sha256": sha256_file(RAMP_JSX),
                       "probe_jsx_sha256": sha256_file(PROBE_JSX),
                       "abr_manifest": json.loads((ABR_DIR / "manifest.json").read_text(encoding="utf-8")),
                       "texture_png_sha256": sha256_file(make_checker_png())},
            "runs": {"ramp": {"done": done, "meta": meta},
                     "ramp_repeat": {"done": done2, "meta": meta2}},
            "calibration": report["pressure_curve"],
            "depth_model_fits": report["depth_model"],
            "series": {
                "size_width": series("ramp_size_true.png", "median"),
                "opacity_alpha": series("ramp_opac_m0.png", "median"),
                "opacity_alpha_m20": series("ramp_opac_m20.png", "median"),
                "opacity_alpha_m50": series("ramp_opac_m50.png", "median"),
                "opacity_alpha_m0_base50": series("ramp_opac_m0_base50.png", "median"),
                "opacity_alpha_m50_base50": series("ramp_opac_m50_base50.png", "median"),
                "tex_dark_m0": series("ramp_tex_m0.png", "dark_p5"),
                "tex_dark_m20": series("ramp_tex_m20.png", "dark_p5"),
                "tex_dark_m50": series("ramp_tex_m50.png", "dark_p5"),
                "hardmix_frac255_m0": series("ramp_hd_m0.png", "frac255"),
                "hardmix_frac255_m50": series("ramp_hd_m50.png", "frac255"),
                "static_d0_md50": series("ramp_static_d0_md50.png", "dark_p5"),
                "static_d100_md50": series("ramp_static_d100_md50.png", "dark_p5"),
                "static_d50_md50": series("ramp_static_d50_md50.png", "dark_p5"),
                "static_d50_md0": series("ramp_static_d50_md0.png", "dark_p5"),
            },
            "within_session_pixel_diff": within,
            "cross_session_pixel_diff": cross,
            "image_sha256": {n: report["images"][n]["sha256"] for n in report["images"]},
        }
        (PRESSURE_DIR / "manifest_ramp.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        # —— 控制台摘要（报告数字直接抄）——
        cal = report["pressure_curve"]
        if cal:
            p = cal["p"]
            print("压力曲线：x=%d..%d w_min=%.1f w_max=%.1f" % (cal["x0"], cal["x1"], cal["w_min"], cal["w_max"]))
            for px in range(100, 1501, 100):
                print("   x=%4d p=%.3f" % (px, p[px]))
        for name in ("ramp_opac_m0.png", "ramp_opac_m20.png", "ramp_opac_m50.png",
                     "ramp_opac_m0_base50.png", "ramp_opac_m50_base50.png"):
            print(name, [(round(d.get("pressure") or -1, 2), round(d["median"], 1))
                         for d in report["dabs"].get(name, [])])
        for name in ("ramp_tex_m0.png", "ramp_tex_m20.png", "ramp_tex_m50.png"):
            print(name, [(round(d.get("pressure") or -1, 2), round(d["dark_p5"], 1))
                         for d in report["dabs"].get(name, [])])
        for name in ("ramp_hd_m0.png", "ramp_hd_m50.png"):
            print(name, [(round(d.get("pressure") or -1, 2), round(d["frac255"], 3))
                         for d in report["dabs"].get(name, [])])
        for name in ("ramp_static_d0_md50.png", "ramp_static_d100_md50.png",
                     "ramp_static_d50_md50.png", "ramp_static_d50_md0.png"):
            print(name, [round(d["dark_p5"], 1) for d in report["dabs"].get(name, [])])
        print("depth 拟合：", json.dumps(report["depth_model"], ensure_ascii=False))
        print("同会话复现：", json.dumps(within, ensure_ascii=False))
        print("跨会话复现：", json.dumps(cross, ensure_ascii=False))
        print("manifest 已写：%s" % (PRESSURE_DIR / "manifest_ramp.json"))
        return 0
    finally:
        release_lock()


def main(argv=None):
    parser = argparse.ArgumentParser(description="PS 压感 ramp 装置（任务 143）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("abrs", help="造压力夹具 ABR")
    sub.add_parser("probe", help="最小验证（路线接受性 + 压感是否生效）")
    sub.add_parser("ramp", help="正式压力 ramp 批（尺寸/不透明度/纹理深度/hardMix）")
    pa = sub.add_parser("analyze", help="只重跑分析（不渲染）")
    pa.add_argument("--dir", default=(PRESSURE_DIR / "ramp").as_posix(), help="PNG 目录")
    pa.add_argument("--out", default=(PRESSURE_DIR / "ramp_analysis.json").as_posix(), help="输出 JSON")
    ns = parser.parse_args(argv)
    PRESSURE_DIR.mkdir(parents=True, exist_ok=True)
    if ns.cmd == "abrs":
        return cmd_abrs(ns)
    if ns.cmd == "probe":
        return cmd_probe(ns)
    if ns.cmd == "ramp":
        return cmd_ramp(ns)
    if ns.cmd == "analyze":
        analyze_ramp(Path(ns.dir), Path(ns.out))
        return 0
    parser.error("未知子命令")


if __name__ == "__main__":
    sys.exit(main())