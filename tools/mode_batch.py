# -*- coding: utf-8 -*-
"""任务 33：真值与基线批量生成（PS 真值图 + Krita 渲染基线 + A/B/C 三批分配）。

【本脚本做什么】
1) --plan        读 mode-align.json / mode-ids.json，写 docs/数据/mode-batch.json（A/B/C 三批）
                 与 tests/baselines/mode/README.md（命名规则）、tests/baselines/mode/.gitignore。
2) --ps          在 Photoshop 里按模式批量出真值图（每模式 3 档 depth × 2 组亮度/对比度 = 6 组）。
                 走已跑通的 JSX 通路（tools/ps_export.jsx），分批启动、每批等 done.json。
3) --krita-build 为「有 Krita 对应」的模式生成 .kpp（当前转换器口径：conv）。
4) --krita-render 串行渲染基线（同一时刻只允许一个 Krita GUI 会话，抢 krita.lock）。
5) --manifest    汇总 tests/baselines/mode/manifest.json，并刷新 mode-batch.json 的图路径与状态。
6) --verify-ps / --verify-krita  抽 3 个模式重跑一遍，比 sha256（确定性抽检）。
7) --status      打印当前进度（已出图数/失败数/耗时）。

【口径（写进报告，34/35/36 照此引用）】
- PS 侧：画布 100x100 / 72ppi / RGB 透明底；笔刷 = tests/定量测试专用.abr 的「定量测试用」
  （计算笔刷，Dmtr=200、硬度 100、间距 10 → 只落一个笔尖）；纹理 = research/测试用纹理.png
  （只读、原始字节定义图案），textureScale=100%，invertTexture=true（与 ABR 源预设一致）；
  落笔 = 画布中心 (50,50)→(50.4,50.4) 的 0.4px 路径（整张图只有一个章）。
- Krita 侧：同一支笔（同一 ABR 预设走项目转换链）、同一纹理（同一文件字节 + tEXt 标记避免
  md5 冲突）、同一缩放（Texture/Pattern/Scale=1.0=100%）、画布 100x100、stroke=60、压感 1.0。
  深度 → Texture/Strength/Value（高度系 ×1.2 封顶 1.0，转换链口径）；
  亮度/对比度 → 转换链 calibrated 公式（preset_xml.texture_brightness_contrast）；
  纹理混合模式 → mode-align.json 的 krita_value（同时校验转换链映射一致）。
- 「无对应」模式（Krita TexturingMode 没有该成员）只出 PS 真值，不出 Krita 图。

【六组参数（每组一张 PS + 一张 Krita）】
depth ∈ {10, 60, 100} × (b, c) ∈ {(0, 0), (-50, -50)}  → 6 组，顺序固定。

【命名（确定性）】
  ps__mode__<序号>_<模式名>__d<深度>_b<亮度>_c<对比度>.png
  krita__mode__<序号>_<模式名>__d<深度>_b<亮度>_c<对比度>.png
序号是 mode-align.json ps_members 里的 0 基下标（两位），用来区分同名的 Nrml（0 与 27）。
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
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

import krita_render_runner as runner                        # noqa: E402
from brush_converter.abr import AbrFile                     # noqa: E402
from brush_converter.mapping import map_presets             # noqa: E402
from brush_converter.kpp.kpp_writer import (                # noqa: E402
    build_kpp,
    render_tip_preview,
)
from brush_converter import convert as conv_mod             # noqa: E402

# ---------------------------------------------------------------- 路径

MODE_DIR = REPO / "tests" / "baselines" / "mode"
PS_DIR = MODE_DIR / "ps"
KRITA_DIR = MODE_DIR / "krita"
WORK = MODE_DIR / "_work"
KPP_DIR = WORK / "kpp"
PY_RUN = WORK / "rerun_ps"
KR_RUN = WORK / "rerun_krita"
PS_LOG_DIR = WORK / "ps_logs"

MODE_ALIGN = REPO / "docs" / "数据" / "mode-align.json"
MODE_IDS = REPO / "docs" / "数据" / "mode-ids.json"
BATCH_JSON = REPO / "docs" / "数据" / "mode-batch.json"
PS_RESULTS = WORK / "ps_results.json"
KR_RESULTS = WORK / "krita_results.json"
MANIFEST = MODE_DIR / "manifest.json"

ABR_PATH = REPO / "tests" / "定量测试专用.abr"
TEXTURE_PATH = REPO / "research" / "测试用纹理.png"
JSX_PATH = REPO / "tools" / "ps_export.jsx"
PS_EXE = Path(os.environ.get("BRUSHCONVERTER_PS",
                             r"D:/program/PS2025/Adobe Photoshop 2025/Photoshop.exe"))

# ---------------------------------------------------------------- 口径常量

CANVAS = {"width": 100, "height": 100, "dpi": 72}
# PS 侧 job 的画笔描述（实际用的是 ABR 里那支笔，这些值只作 job.json 记录）
BRUSH = {"diameter": 200, "hardness": 100, "spacing": 10}
TEXTURE_NAME = "bc_mode_texture"       # PS 图案名（每次启动重新定义）
TEXTURE_SCALE = 100                    # PS textureScale=100%
INVERT_TEXTURE = True                  # 与 ABR 源预设的 invert 对齐
ABR_BRUSH_NAME = "定量测试用"

SIZE = (100, 100)                      # Krita 画布
STROKE = 60                            # Krita 笔划长度（单 dab 覆盖整张画布）
PRESSURE = 1.0
GUI_TIMEOUT = 75.0                     # 单张 GUI 渲染超时（实测 11–15 秒）
PS_CHUNK = 24                          # 每次启动 PS 出多少张
PS_TIMEOUT = 900                       # 单批等 done.json 的上限（秒）

DEPTHS = (10, 60, 100)
BC = ((0, 0), (-50, -50))
GROUPS = tuple((d, b, c) for d in DEPTHS for (b, c) in BC)   # 6 组，顺序固定

# PS 纹理模式键 → 转换链 mapping._TEXTURING_MODE 的键（仅「有 Krita 对应」的 14 个）
PS2MAP = {
    "Mltp": "Mul ", "Sbtr": "Sbtr", "Lght": "Lght", "Grdn": "Grdn",
    "Drkn": "Drkn", "Ovrl": "Ovrl", "CDdg": "CDdg", "CBrn": "CBrn",
    "linearDodge": "LDdg", "linearBurn": "LBur", "hardMix": "hardMix",
    "HdMxS": "HdMxS", "height": "height", "linearHeight": "linearHeight",
}
TEX_RESOURCE_PREFIX = "modekrita_tex_"   # 避开本机图案库同名资源（实测会遮蔽内嵌字节）

# ---------------------------------------------------------------- 小工具


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def json_dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def json_load(path: Path, default=None):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def idat_md5(path: Path) -> str:
    """像素数据（IDAT 块）的 md5：与容器元数据无关，是「同参数两次运行像素是否一致」的硬判据。

    为什么需要：PS 保存 PNG 时会写一个 XMP iTXt 块（含创建时间），所以整文件 sha256 每次不同，
    但 IDAT（真正的像素压缩流）两次完全一致。只比 sha256 会得出「PS 不确定」的错误结论。
    """
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return ""
    pos, md5s = 8, []
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        if ctype == b"IDAT":
            md5s.append(hashlib.md5(payload).hexdigest())
        pos += 12 + length
    return hashlib.md5("".join(md5s).encode("ascii")).hexdigest()


def alpha_stats(path: Path) -> dict:
    """Alpha 通道统计（PS/Krita 的笔迹信息都在 Alpha 上）。"""
    with Image.open(path) as im:
        mode = im.mode
        arr = np.array(im.convert("RGBA"))
    a = arr[:, :, 3].astype(np.int32)
    hist = np.bincount(a.ravel(), minlength=256)
    nonzero = int(hist[1:].sum())
    total = int(a.size)
    return {
        "mode": mode,
        "width": int(a.shape[1]),
        "height": int(a.shape[0]),
        "alpha_mean": round(float(a.mean()), 3),
        "alpha_min": int(a.min()),
        "alpha_max": int(a.max()),
        "alpha_unique": int((hist > 0).sum()),
        "alpha_nonzero": nonzero,
        "alpha_nonzero_ratio": round(nonzero / total, 4),
        "sha256": sha256_of(path),
        "idat_md5": idat_md5(path),
        "alpha_sha256": hashlib.sha256(a.astype(np.uint8).tobytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def sanitize(token: str) -> str:
    """文件名里用的模式名：去掉 PS 4CC 的尾部空格，非法字符换下划线。"""
    t = str(token).strip()
    t = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in t)
    return t or "unnamed"


def group_tag(depth: int, brightness: int, contrast: int) -> str:
    return "d%d_b%d_c%d" % (depth, brightness, contrast)


# ---------------------------------------------------------------- 模式清单


def load_modes() -> list[dict]:
    """把 mode-align.json（47 行）与 mode-ids.json（runtime_id）按行号合并。"""
    align = json.loads(MODE_ALIGN.read_text(encoding="utf-8"))
    ids = json.loads(MODE_IDS.read_text(encoding="utf-8"))
    a = align["ps_members"]
    b = ids["modes"]
    if len(a) != len(b):
        raise SystemExit("mode-align 与 mode-ids 行数不一致：%d vs %d" % (len(a), len(b)))
    out = []
    for i, (m, n) in enumerate(zip(a, b)):
        if m["ps_member"] != n["ps_member"]:
            raise SystemExit("第 %d 行模式名不一致：%r vs %r" % (i, m["ps_member"], n["ps_member"]))
        out.append({
            "order": i,
            "ps_member": m["ps_member"],
            "ps_semantics": m.get("ps_semantics", ""),
            "ps_domain": m.get("ps_domain", ""),
            "ps_id_kind": m.get("ps_id_kind", ""),
            "status": m.get("status", ""),                 # 有对应 / 无对应 / 待动态确认
            "krita_value": m.get("krita_value"),
            "krita_member": m.get("krita_member"),
            "runtime_id": n.get("runtime_id"),
            "runtime_status": n.get("status", ""),
            "jsx_readback": n.get("jsx_readback", ""),
            # 任务 31 动态实测里「读取失败」的 3 条（Lght/Grdn/HdMxS）：PS 根本不接受这个枚举值，
            # 回读会停在上一模式。不能拿 mode-ids 的 jsx_readback 当期望值（那本身就是「没变」的读数），
            # 否则会把 height 的图误判成 lightness/gradient 的图（假阳性）。
            "ps_applicable": not str(n.get("status", "")).startswith("读取失败"),
            "note": m.get("note", ""),
        })
    return out


def ps_file_name(mode: dict, depth: int, brightness: int, contrast: int) -> str:
    return "ps__mode__%02d_%s__%s.png" % (
        mode["order"], sanitize(mode["ps_member"]), group_tag(depth, brightness, contrast))


def krita_file_name(mode: dict, depth: int, brightness: int, contrast: int) -> str:
    return "krita__mode__%02d_%s__%s.png" % (
        mode["order"], sanitize(mode["ps_member"]), group_tag(depth, brightness, contrast))


def krita_kpp_name(mode: dict, depth: int, brightness: int, contrast: int) -> str:
    return "kpp__mode__%02d_%s__%s.kpp" % (
        mode["order"], sanitize(mode["ps_member"]), group_tag(depth, brightness, contrast))


def mode_jobs(modes: list[dict]) -> list[dict]:
    """PS 侧任务：每个模式 × 6 组。"""
    jobs = []
    for m in modes:
        for (d, b, c) in GROUPS:
            jobs.append({
                "order": m["order"],
                "ps_member": m["ps_member"],
                "krita_value": m["krita_value"],
                "file": ps_file_name(m, d, b, c),
                "mode_kind": "char" if m["ps_id_kind"] == "4cc" else "str",
                "mode_value": m["ps_member"],
                "mode_key": m["jsx_readback"],
                "ps_applicable": m["ps_applicable"],
                "use_texture": True,
                "invert_texture": INVERT_TEXTURE,
                "texture_scale": TEXTURE_SCALE,
                "depth": d,
                "brightness": b,
                "contrast": c,
            })
    return jobs


def critical_modes(modes: list[dict]) -> list[dict]:
    """需要出 Krita 基线的模式：有 krita_value 的（11 有对应 + 3 待动态确认）。"""
    return [m for m in modes if m.get("krita_value") is not None]


# ---------------------------------------------------------------- 单实例锁


LOCK_ROOT = Path(os.environ.get("TEMP") or "/tmp") / "brushconverter-team" / "locks"


class ResourceLock:
    """mkdir 原子锁：抢不到就退避轮询，绝不强杀别人的进程。"""

    def __init__(self, name: str, wait_s: float = 5400.0, poll_s: float = 20.0,
                 stale_s: float = 5400.0):
        self.name = name
        self.wait_s = wait_s
        self.poll_s = poll_s
        self.stale_s = stale_s
        self.path = LOCK_ROOT / (name + ".lock")
        self.t0 = time.time()

    def __enter__(self):
        LOCK_ROOT.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        while True:
            try:
                os.mkdir(str(self.path))
                json_dump(self.path / "owner.json", {
                    "task": "33", "resource": self.name, "pid": os.getpid(),
                    "acquired_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                print("[锁] 已获得 %s（等待 %.0fs）" % (self.name, time.time() - t0))
                self.t0 = time.time()
                return self
            except FileExistsError:
                owner = json_load(self.path / "owner.json", {}) or {}
                age = time.time() - self.path.stat().st_mtime
                if age > self.stale_s:
                    print("[锁] %s 锁已超过 %.0fs，判定为残留，清理" % (self.name, age))
                    shutil.rmtree(str(self.path), ignore_errors=True)
                    continue
                if time.time() - t0 > self.wait_s:
                    raise SystemExit("等待 %s 锁超过 %.0fs，放弃（持有者：%r）"
                                     % (self.name, self.wait_s, owner))
                print("[锁] %s 被占用中（%r，已 %.0fs），%.0fs 后重试"
                      % (self.name, owner.get("task", "?"), age, self.poll_s))
                time.sleep(self.poll_s)

    def __exit__(self, exc_type, exc, tb):
        shutil.rmtree(str(self.path), ignore_errors=True)
        print("[锁] 已释放 %s（持有 %.0fs）" % (self.name, time.time() - self.t0))
        return False


def process_running(name: str) -> bool:
    out = subprocess.run(["tasklist"], capture_output=True, text=True,
                         errors="ignore").stdout or ""
    return name.lower() in out.lower()


def wait_process_free(name: str, label: str, timeout_s: float) -> None:
    t0 = time.time()
    warned = False
    while process_running(name):
        if time.time() - t0 > timeout_s:
            raise SystemExit("等待 %s 空闲超过 %.0fs，放弃（不强行结束别人的进程）"
                             % (label, timeout_s))
        if not warned:
            print("[等待] 检测到 %s 正在运行（可能被其他成员占用），退避轮询…" % label)
            warned = True
        time.sleep(20.0)


def kill_photoshop() -> None:
    """兜底清理本次由脚本启动的 Photoshop（PS 有时 app.quit() 卡住）。"""
    subprocess.run(["taskkill", "-F", "-IM", "Photoshop.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


# ---------------------------------------------------------------- PS 真值


def ps_job_payload(jobs: list[dict], out_dir: Path | None = None) -> dict:
    return {
        "out_dir": (out_dir or PS_DIR).as_posix(),
        "texture_path": TEXTURE_PATH.as_posix(),
        "texture_name": TEXTURE_NAME,
        "texture_scale": TEXTURE_SCALE,
        "abr_path": ABR_PATH.as_posix(),
        "brush_name": ABR_BRUSH_NAME,
        "canvas": CANVAS,
        "brush": BRUSH,
        "jobs": [
            {k: j[k] for k in ("file", "mode_key", "mode_kind", "mode_value", "use_texture",
                               "invert_texture", "texture_scale", "depth", "brightness",
                               "contrast")}
            for j in jobs
        ],
    }


def run_ps_chunk(jobs: list[dict], chunk_index: int, out_dir: Path | None = None):
    """启动一次 Photoshop，跑一批任务，返回 (done.json 内容, 耗时)。"""
    out_dir = out_dir or PS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    PS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    done_path = WORK / "done.json"
    job_path = WORK / "job.json"
    log_path = WORK / "batch.log"
    for p in (done_path, log_path):
        if p.exists():
            p.unlink()
    json_dump(job_path, ps_job_payload(jobs, out_dir))
    env = os.environ.copy()
    env["BRUSHCONVERTER_PS_WORK"] = WORK.as_posix()
    env["BRUSHCONVERTER_REPO"] = REPO.as_posix()
    t0 = time.time()
    proc = subprocess.Popen([str(PS_EXE), "-r", str(JSX_PATH)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    deadline = time.time() + PS_TIMEOUT
    while time.time() < deadline:
        if done_path.exists():
            time.sleep(0.5)
            break
        if proc.poll() is not None and not done_path.exists():
            time.sleep(2.0)
            if done_path.exists():
                break
            raise RuntimeError("Photoshop 提前退出（returncode=%r）且未写 done.json，见 %s"
                               % (proc.returncode, log_path))
        time.sleep(1.0)
    else:
        kill_photoshop()
        raise TimeoutError("等待 %ds 仍未生成 done.json，见 %s" % (PS_TIMEOUT, log_path))
    elapsed = time.time() - t0
    data = json.loads(done_path.read_text(encoding="utf-8"))
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    kill_photoshop()
    # JSX 的 done.json 形如 {"ps_version":…, "brush":{…}, "results":[…]}；
    # 老版本（无 results 键）直接是数组，这里两种都兼容。
    if isinstance(data, dict):
        data = data.get("results") or []
    if log_path.is_file():
        shutil.copy2(str(log_path), str(PS_LOG_DIR / ("chunk_%02d.log" % chunk_index)))
    print("[PS] 第 %d 批 %d 张完成，耗时 %.1fs（%.1fs/张）"
          % (chunk_index, len(jobs), elapsed, elapsed / max(1, len(jobs))))
    return data, elapsed


def cmd_ps(args) -> int:
    modes = load_modes()
    if args.limit_modes:
        modes = modes[: args.limit_modes]
    jobs = mode_jobs(modes)
    results = json_load(PS_RESULTS, {}) or {}
    todo = [j for j in jobs if not (args.resume and j["file"] in results
                                    and results[j["file"]].get("ok"))]
    if args.limit:
        todo = todo[: args.limit]
    print("[PS] 计划 %d 张，本次待跑 %d 张（chunk=%d）" % (len(jobs), len(todo), args.chunk))
    if not todo:
        print("[PS] 无待跑任务")
        return 0
    if not PS_EXE.is_file():
        raise SystemExit("找不到 Photoshop：%s" % PS_EXE)
    for p in (ABR_PATH, TEXTURE_PATH, JSX_PATH):
        if not p.is_file():
            raise SystemExit("缺少依赖文件：%s" % p)

    rc = 0
    with ResourceLock("ps", wait_s=args.wait_lock):
        t_all = time.time()
        chunk_index = 0
        for start in range(0, len(todo), args.chunk):
            batch = todo[start: start + args.chunk]
            chunk_index += 1
            wait_process_free("Photoshop.exe", "Photoshop", args.wait_ps)
            try:
                done, elapsed = run_ps_chunk(batch, chunk_index)
            except Exception as exc:                      # noqa: BLE001
                print("[PS] 第 %d 批失败：%s" % (chunk_index, exc))
                rc = 2
                kill_photoshop()
                time.sleep(5)
                continue
            by_file = {d.get("file", ""): d for d in done}
            for j in batch:
                d = by_file.get(j["file"], {})
                png = PS_DIR / j["file"]
                exists = png.is_file()
                ok = (bool(d.get("ok")) and bool(d.get("verify")) and exists
                      and j["ps_applicable"])
                rec = {
                    "order": j["order"], "ps_member": j["ps_member"],
                    "depth": j["depth"], "brightness": j["brightness"],
                    "contrast": j["contrast"], "file": j["file"],
                    "ok": ok, "exists": exists,
                    "mode_read": d.get("mode_read", ""),
                    "mode_key_expected": j["mode_key"],
                    "readback": d.get("readback", ""),
                    "expected": d.get("expected", ""),
                    "jsx_verify": bool(d.get("verify")),
                    "jsx_ok": bool(d.get("ok")),
                    "ps_applicable": j["ps_applicable"],
                    "error": d.get("error", ""),
                    "elapsed_s": round(elapsed / max(1, len(batch)), 2),
                }
                if exists:
                    try:
                        rec.update(alpha_stats(png))
                    except Exception as exc:              # noqa: BLE001
                        rec["alpha_error"] = str(exc)
                results[j["file"]] = rec
            json_dump(PS_RESULTS, results)
        print("[PS] 本轮总耗时 %.1fs" % (time.time() - t_all))
    return rc


# ---------------------------------------------------------------- Krita 侧 .kpp


def embedded_texture_bytes() -> tuple[bytes, str]:
    """源纹理字节 + 一个 tEXt 块（像素不动），避免与图案库同 md5 被静默禁用。"""
    src = TEXTURE_PATH.read_bytes()
    sha = hashlib.sha256(src).hexdigest()
    if src[-8:-4] != b"IEND":
        raise SystemExit("源纹理不是以 IEND 结尾的标准 PNG，无法安全追加元数据块")
    payload = b"brushconverter-mode33" + bytes([0]) + b"sha256=" + sha.encode("ascii")
    ctype = b"tEXt"
    chunk = (struct.pack(">I", len(payload)) + ctype + payload
             + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))
    out = src[:-12] + chunk + src[-12:]
    return out, hashlib.md5(out).hexdigest()


def load_base():
    """解析 ABR 取那支计算笔刷（实验口径前提：只有 1 支预设）。"""
    abr = AbrFile.parse(str(ABR_PATH))
    presets = map_presets(abr)
    if len(presets) != 1:
        raise SystemExit("实验前提不成立：%s 解析出 %d 支预设（应为 1 支）"
                         % (ABR_PATH.name, len(presets)))
    bp = presets[0]
    if bp.texture is None:
        raise SystemExit("实验前提不成立：该预设没有纹理设置")
    return bp


def brush_parts(bp):
    if bp.is_computed:
        brush_def = conv_mod._computed_brush_def(bp)
        preview = render_tip_preview(
            conv_mod._circle_mask(bp.hardness if bp.hardness is not None else 100.0,
                                  bp.roundness))
    else:
        from brush_converter import convert as _c
        tip_png = _c._png_bytes(_c._square_tip(bp.tip_gray))
        brush_def = _c._sampled_brush_def("tip", tip_png, bp)
        preview = render_tip_preview(bp.tip_gray)
    return brush_def, preview


def assemble_xml(bp, brush_def: str, tex_xml) -> str:
    from brush_converter.kpp.preset_xml import build_preset_xml
    return build_preset_xml(
        name=bp.name,
        brush_definition=brush_def,
        tip_png=None,
        tip_name=None,
        size_curve=bp.size_curve,
        opacity_curve=bp.opacity_curve,
        flow_curve=bp.flow_curve,
        ratio_curve=bp.ratio_curve,
        rotation_sensor=bp.rotation_sensor,
        rotation_jitter=bp.rotation_jitter,
        scatter=bp.scatter,
        scatter_pressure=bp.scatter_pressure,
        scatter_both_axes=bp.scatter_both_axes,
        scatter_amount=bp.scatter_amount,
        texture=tex_xml,
    )


def build_krita_presets(modes: list[dict] | None = None) -> list[dict]:
    """为每个「有 krita_value」的模式 × 6 组生成 .kpp（当前转换器口径 conv）。"""
    modes = modes or critical_modes(load_modes())
    bp = load_base()
    tex_bytes, tex_md5 = embedded_texture_bytes()
    brush_def, preview = brush_parts(bp)
    KPP_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for m in modes:
        key = PS2MAP.get(m["ps_member"])
        if key is None:
            raise SystemExit("模式 %r 没有转换链映射键，不能生成 Krita 预设" % m["ps_member"])
        for (d, b, c) in GROUPS:
            tex = dataclasses.replace(
                bp.texture, scale=float(TEXTURE_SCALE), depth=float(d),
                brightness=int(b), contrast=int(c), blend_mode=key)
            bp2 = dataclasses.replace(bp, texture=tex)
            tex_xml = conv_mod._texture_xml(bp2)
            if tex_xml is None:
                raise SystemExit("转换链未产出纹理 XML（%s）" % m["ps_member"])
            tex_xml.png_bytes = tex_bytes
            tex_xml.pattern_filename = "%s%s.png" % (TEX_RESOURCE_PREFIX, tex_md5[:8])
            xml = assemble_xml(bp, brush_def, tex_xml)
            data = build_kpp(xml, preview)
            name = krita_kpp_name(m, d, b, c)
            (KPP_DIR / name).write_bytes(data)
            rows.append({
                "order": m["order"], "ps_member": m["ps_member"],
                "krita_value": m["krita_value"], "kpp": name,
                "depth": d, "brightness": b, "contrast": c,
                "expect_texturing_mode": int(m["krita_value"]),
                "got_texturing_mode": int(tex_xml.texturing_mode),
                "krita_strength": tex_xml.strength,
                "krita_brightness": tex_xml.brightness,
                "krita_contrast": tex_xml.contrast,
                "use_soft_texturing": bool(tex_xml.use_soft_texturing),
                "invert": bool(tex_xml.invert),
                "scale": tex_xml.scale,
                "texture_md5": tex_md5,
                "bytes": len(data),
            })
            print("[KPP] %-46s mode=%d strength=%.3f b=%s c=%s"
                  % (name, tex_xml.texturing_mode, tex_xml.strength,
                     tex_xml.brightness, tex_xml.contrast))
    json_dump(WORK / "kpp_table.json", rows)
    bad = [r for r in rows if r["got_texturing_mode"] != r["expect_texturing_mode"]]
    if bad:
        raise SystemExit("转换链映射与 mode-align 的 krita_value 不一致：%r" % bad[:3])
    print("[KPP] 共 %d 个预设 → %s" % (len(rows), KPP_DIR))
    return rows


# ---------------------------------------------------------------- Krita 串行渲染


def cmd_krita_render(args) -> int:
    modes = critical_modes(load_modes())
    if args.limit_modes:
        modes = modes[: args.limit_modes]
    results = json_load(KR_RESULTS, {}) or {}
    todo = []
    for m in modes:
        for (d, b, c) in GROUPS:
            name = krita_file_name(m, d, b, c)
            png = KRITA_DIR / name
            if args.resume and name in results and results[name].get("ok") and png.is_file():
                continue
            todo.append((m, d, b, c, name))
    if args.limit:
        todo = todo[: args.limit]
    print("[Krita] 计划 %d 张，本次待渲染 %d 张（串行）" % (len(modes) * len(GROUPS), len(todo)))
    if not todo:
        return 0
    KRITA_DIR.mkdir(parents=True, exist_ok=True)
    rc = 0
    with ResourceLock("krita", wait_s=args.wait_lock):
        wait_process_free("krita.exe", "Krita", args.wait_krita)
        t_all = time.time()
        for idx, (m, d, b, c, name) in enumerate(todo, 1):
            kpp = KPP_DIR / krita_kpp_name(m, d, b, c)
            png = KRITA_DIR / name
            if not kpp.is_file():
                rc = 2
                results[name] = {"order": m["order"], "ps_member": m["ps_member"],
                                 "krita_value": m["krita_value"], "depth": d,
                                 "brightness": b, "contrast": c, "file": name,
                                 "ok": False, "error": "缺少 .kpp（先跑 --krita-build）"}
                json_dump(KR_RESULTS, results)
                continue
            t0 = time.time()
            res = runner.render_one(str(kpp), str(png), size=SIZE, stroke=STROKE,
                                    pressure=PRESSURE, route="gui", timeout=GUI_TIMEOUT)
            elapsed = time.time() - t0
            ok = bool(res.get("ok")) and png.is_file()
            rec = {
                "order": m["order"], "ps_member": m["ps_member"],
                "krita_value": m["krita_value"], "depth": d,
                "brightness": b, "contrast": c, "file": name, "kpp": kpp.name,
                "ok": ok, "elapsed_s": round(elapsed, 2),
                "reason": res.get("reason", ""),
                "attempts": res.get("attempts", 1),
            }
            if png.is_file():
                try:
                    rec.update(alpha_stats(png))
                except Exception as exc:                  # noqa: BLE001
                    rec["alpha_error"] = str(exc)
                    ok = rec["ok"] = False
            results[name] = rec
            json_dump(KR_RESULTS, results)
            print("[Krita] %3d/%3d %-46s %s %.1fs"
                  % (idx, len(todo), name, "OK" if ok else "FAIL", elapsed))
            if not ok:
                rc = 2
                print("       原因：%s" % str(rec.get("reason"))[:300])
        print("[Krita] 本轮总耗时 %.1fs" % (time.time() - t_all))
    return rc


# ---------------------------------------------------------------- 三批分配 + manifest


BATCH_KEYS = ("A", "B", "C")


def split_batches(modes: list[dict]) -> dict[str, list[dict]]:
    """按顺序三等分（47 → 16/16/15），不重不漏。"""
    n = len(modes)
    base = n // 3
    extra = n - base * 3
    sizes = [base + (1 if i < extra else 0) for i in range(3)]
    out, pos = {}, 0
    for key, size in zip(BATCH_KEYS, sizes):
        out[key] = modes[pos: pos + size]
        pos += size
    return out


def build_batch_doc() -> dict:
    modes = load_modes()
    ps_res = json_load(PS_RESULTS, {}) or {}
    kr_res = json_load(KR_RESULTS, {}) or {}
    batches = split_batches(modes)
    entries_by_batch = {}
    for key in BATCH_KEYS:
        rows = []
        for m in batches[key]:
            ps_imgs, kr_imgs = [], []
            ps_ok = ps_fail = 0
            for (d, b, c) in GROUPS:
                pf = ps_file_name(m, d, b, c)
                rec = ps_res.get(pf, {})
                ps_imgs.append({
                    "file": "tests/baselines/mode/ps/" + pf,
                    "ok": bool(rec.get("ok")),
                    "jsx_verify": bool(rec.get("jsx_verify")),
                    "mode_read": rec.get("mode_read", ""),
                    "error": rec.get("error", ""),
                    "sha256": rec.get("sha256"),
                })
                ps_ok += 1 if rec.get("ok") else 0
                ps_fail += 0 if rec.get("ok") else 1
            if m.get("krita_value") is not None:
                for (d, b, c) in GROUPS:
                    kf = krita_file_name(m, d, b, c)
                    rec = kr_res.get(kf, {})
                    kr_imgs.append({
                        "file": "tests/baselines/mode/krita/" + kf,
                        "ok": bool(rec.get("ok")),
                        "reason": rec.get("reason", ""),
                        "sha256": rec.get("sha256"),
                    })
            judge_kind = ("像素比对（PS vs Krita）" if m.get("krita_value") is not None
                          else "结构判定（Krita 无对应，仅 PS 真值）")
            # PS 真值一句话状态：34/35/36 一眼能看出这个模式有没有 6 组可比图
            if ps_ok == len(GROUPS):
                ps_status = "ok（6/6）"
            elif not m.get("ps_applicable"):
                ps_status = "fail：PS 不接受该枚举值（回读停在上一模式，图无意义）"
            elif any(r.get("error") for r in ps_imgs):
                ps_status = "fail：PS 落笔/保存报错（见 images[].error）"
            else:
                ps_status = "fail：值被 PS 忽略（回读停在上一模式）"
            rows.append({
                "order": m["order"],
                "ps_member": m["ps_member"],
                "ps_semantics": m["ps_semantics"],
                "runtime_id": m["runtime_id"],
                "krita_value": m["krita_value"],
                "krita_member": m["krita_member"],
                "status": m["status"],
                "judge_kind": judge_kind,
                "ps_truth": {"组数": len(GROUPS), "ok": ps_ok, "fail": ps_fail,
                             "status": ps_status, "images": ps_imgs},
                "krita_baseline": {
                    "组数": len(GROUPS) if m.get("krita_value") is not None else 0,
                    "ok": sum(1 for r in kr_imgs if r["ok"]),
                    "images": kr_imgs,
                },
                "ps_note": m.get("note", ""),
            })
        entries_by_batch[key] = rows
    total = sum(len(entries_by_batch[k]) for k in BATCH_KEYS)
    with_krita = len(critical_modes(modes))
    doc = {
        "schema_version": "1.0",
        "task": "33",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": "把 v0.4 要判定的模式清单按顺序三等分给任务 34/35/36；"
                   "每批含 ps_member / runtime_id / krita_value / status 与图路径清单",
        "basis": {
            "ps": {"product": "Adobe Photoshop 2025", "version": "26.9.0.15",
                   "route": "tools/ps_export.jsx（ExtendScript 通路）",
                   "canvas": CANVAS, "texture": "research/测试用纹理.png（原始字节定义图案）",
                   "texture_scale": TEXTURE_SCALE, "invert_texture": INVERT_TEXTURE,
                   "brush": "tests/定量测试专用.abr → 定量测试用"},
            "krita": {"product": "Krita", "version": "5.3.3",
                      "route": "GUI 进程 + PyKrita 启动插件（tools/krita_render_runner.py）",
                      "canvas": [SIZE[0], SIZE[1]], "stroke": STROKE, "pressure": PRESSURE,
                      "preset_basis": "当前转换器口径 conv：strength=depth/100（高度系 ×1.2 封顶 1.0），"
                                       "brightness/contrast 用 calibrated 公式，"
                                       "mode 用 mode-align 的 krita_value"},
            "groups": [{"tag": group_tag(d, b, c), "depth": d,
                        "brightness": b, "contrast": c} for (d, b, c) in GROUPS],
            "naming": "ps__mode__<序号>_<模式>__d<深度>_b<亮度>_c<对比度>.png"
                      " / krita__mode__<同>__…png",
        },
        "counts": {
            "total_modes": total,
            "with_krita_baseline": with_krita,
            "ps_truth_only": total - with_krita,
            "groups_per_mode": len(GROUPS),
            "planned_ps_images": total * len(GROUPS),
            "planned_krita_images": with_krita * len(GROUPS),
            "ps_ok": sum(1 for r in ps_res.values() if r.get("ok")),
            "krita_ok": sum(1 for r in kr_res.values() if r.get("ok")),
            "batches": {k: len(entries_by_batch[k]) for k in BATCH_KEYS},
            "batches_with_krita": {
                k: sum(1 for r in entries_by_batch[k] if r["krita_value"] is not None)
                for k in BATCH_KEYS},
        },
        "batches": [{"batch": k, "members": entries_by_batch[k]} for k in BATCH_KEYS],
    }
    return doc


def write_gitignore() -> None:
    (MODE_DIR / ".gitignore").write_text(
        "# 任务 33：真值图/渲染基线体积大，不入库；只提交 README.md / manifest.json / .gitignore\n"
        "*.png\n*.kpp\n_work/\n", encoding="utf-8")


def write_readme() -> None:
    modes = load_modes()
    crit = critical_modes(modes)
    rows = "\n".join(
        "| %d | %s | %s | %s | %s | %s |" % (
            m["order"], m["ps_member"].strip() or m["ps_member"], m["ps_semantics"],
            "" if m["runtime_id"] is None else m["runtime_id"],
            "" if m["krita_value"] is None else m["krita_value"],
            m["status"])
        for m in crit)
    text = (
        "# tests/baselines/mode —— 任务 33 真值与基线（PS 真值 + Krita 渲染）\n\n"
        "本目录是 v0.4「每个纹理模式下定论」的可比素材库：同一笔、同一纹理、同一缩放，\n"
        "每个模式 6 组参数各出一张 PS 真值与（有对应模式下）一张 Krita 渲染基线。\n\n"
        "**图片不入库**（本目录 .gitignore 已忽略 *.png、*.kpp、_work/）；\n"
        "入库的只有 README.md、manifest.json、.gitignore。\n\n"
        "## 一、命名规则（确定性，同参数跑两遍同名同内容）\n\n"
        "    ps__mode__<序号>_<模式>__d<深度>_b<亮度>_c<对比度>.png      PS 真值\n"
        "    krita__mode__<序号>_<模式>__d<深度>_b<亮度>_c<对比度>.png   Krita 渲染基线\n\n"
        "- <序号>：docs/数据/mode-align.json 里 ps_members 的 0 基下标（两位补零）。\n"
        "  **必须带序号**，因为清单里 Nrml 出现两次（0 与 27）。\n"
        "- <模式>：ps_member 去掉 4CC 尾部空格后的文件名安全形式（Clr 尾部空格 → Clr；H   → H）。\n"
        "- <深度>：PS textureDepth（10 / 60 / 100）。\n"
        "- <亮度>/<对比度>：PS textureBrightness / textureContrast，负号原样保留（如 b-50_c-50）。\n\n"
        "同一组参数在 PS 与 Krita 侧文件名只差前缀，便于 34/35/36 直接配对。\n\n"
        "## 二、6 组参数（顺序固定）\n\n"
        "深度 {10, 60, 100} × 亮度/对比度组 {(0, 0), (-50, -50)} = 6 组：\n\n"
        "| 序号 | depth | brightness | contrast | 说明 |\n"
        "|---|---|---|---|---|\n"
        "| 1 | 10 | 0 | 0 | 低深度、中性 |\n"
        "| 2 | 10 | -50 | -50 | 低深度、双负向 |\n"
        "| 3 | 60 | 0 | 0 | 中深度、中性 |\n"
        "| 4 | 60 | -50 | -50 | 中深度、双负向 |\n"
        "| 5 | 100 | 0 | 0 | 全深度、中性 |\n"
        "| 6 | 100 | -50 | -50 | 全深度、双负向 |\n\n"
        "选 (-50, -50) 而不是 (+50, +50) 的理由：负亮度用来检查「Krita 亮度是减法」这一符号翻转\n"
        "（calibrated 公式里 b_k = center_fix - b/255），负对比度 k=0.5 不在 Krita 上限 2 的截断区，\n"
        "差异不会被饱和吃掉。\n\n"
        "## 三、口径（改口径必须整批重出）\n\n"
        "**PS 真值**：画布 100×100 / 72ppi / RGB 透明底；笔刷 tests/定量测试专用.abr 里的\n"
        "「定量测试用」计算笔刷（Dmtr=200、硬度 100、间距 10）；纹理 research/测试用纹理.png\n"
        "原始字节定义图案（图案名 bc_mode_texture），textureScale=100%、invertTexture=true；\n"
        "落笔 = 画布中心 (50,50)→(50.4,50.4) 的 0.4px 路径（整张图只有一个笔尖章）。\n"
        "导出 PNG 保留 Alpha，RGB 恒为 0，**信息全在 Alpha**。\n\n"
        "**Krita 基线**：同一支笔（同一 ABR 预设走项目转换链）、同一纹理文件字节\n"
        "（内嵌时追加一个 tEXt 块改 md5，避免与图案库同 md5 被 Krita 静默禁用）、\n"
        "Texture/Pattern/Scale=1.0（=100%）、画布 100×100、stroke=60、压感 1.0。\n"
        "深度 → Texture/Strength/Value（=depth/100，高度系 ×1.2 封顶 1.0，转换链口径）；\n"
        "亮度/对比度 → 转换链 calibrated 公式（preset_xml.texture_brightness_contrast）；\n"
        "混合模式 → docs/数据/mode-align.json 的 krita_value（脚本会断言转换链映射与之一致）。\n\n"
        "> 注意：Krita 侧是「当前转换器口径 conv」而不是「PS 数值直传 raw」。这样\n"
        "> PS↔Krita 的逐像素差异就正好等于转换器本身的误差，34/35/36 判「可映射/需校准」时\n"
        "> 读到的就是转换器的真实偏差。\n\n"
        "## 四、哪些模式有 Krita 基线\n\n"
        "判定清单共 @@NMODES@@ 个 PS 模式；其中 **@@NCRIT@@ 个**在 Krita TexturingMode（16 成员）里有对应或\n"
        "待确认对应，出 6 张 Krita 图；其余 **@@NREST@@ 个**是「无对应」（PS 图层/其它传递模式，\n"
        "Krita 纹理混合模式没有该成员），**只出 PS 真值**，判据是结构性的。\n\n"
        "| 序号 | PS 模式 | 语义 | runtime_id | Krita TexturingMode | mode-align 状态 |\n"
        "|---|---|---|---|---|---|\n@@ROWS@@\n\n"
        "## 五、目录\n\n"
        "| 路径 | 内容 | 入库 |\n|---|---|---|\n"
        "| ps/ | PS 真值 PNG（PS 模式 × 6 组） | 否 |\n"
        "| krita/ | Krita 渲染基线 PNG | 否 |\n"
        "| _work/ | job.json / done.json / batch.log / .kpp / 结果 JSON | 否 |\n"
        "| manifest.json | 全部图的登记（尺寸/Alpha 统计/sha256/失败原因） | 是 |\n"
        "| README.md | 本文件 | 是 |\n\n"
        "## 六、复现\n\n"
        "    python tools/mode_batch.py --plan            # 写 docs/数据/mode-batch.json 与本 README\n"
        "    python tools/mode_batch.py --ps --resume     # PS 真值（抢 ps.lock，分批，单批 24 张）\n"
        "    python tools/mode_batch.py --krita-build     # 生成 84 个 .kpp\n"
        "    python tools/mode_batch.py --krita-render --resume   # 串行渲染（抢 krita.lock）\n"
        "    python tools/mode_batch.py --manifest        # 汇总 manifest.json\n"
        "    python tools/mode_batch.py --verify-ps --verify-krita   # 确定性抽检（3 模式比 sha256）\n\n"
        "## 七、判定注意\n\n"
        "1. 只比 Alpha 通道；RGB 在 PS 侧恒黑。\n"
        "2. PS 的 高度/线性高度 在 d60、d100 会饱和成全 255（alpha_unique==1）——\n"
        "   这是 PS 真实行为，但**对拟合没有信息量**，34/35/36 拟合时应按 alpha_unique==1 过滤。\n"
        "3. Krita 渲染的 Alpha 边缘有随机种子/抗锯齿抖动：docs/Krita无头渲染说明.md 实测两次运行\n"
        "   最大差 4–6/255，因此**基线比对要用容差**（tools/imgdiff.py 的默认 tol），不要承诺逐像素零差。\n"
    )
    text = (text.replace("@@NMODES@@", str(len(modes)))
                .replace("@@NCRIT@@", str(len(crit)))
                .replace("@@NREST@@", str(len(modes) - len(crit)))
                .replace("@@ROWS@@", rows))
    (MODE_DIR / "README.md").write_text(text, encoding="utf-8")


def cmd_plan(args) -> int:
    MODE_DIR.mkdir(parents=True, exist_ok=True)
    for d in (PS_DIR, KRITA_DIR, WORK):
        d.mkdir(parents=True, exist_ok=True)
    doc = build_batch_doc()
    json_dump(BATCH_JSON, doc)
    write_readme()
    write_gitignore()
    print("[plan] 写 %s：%d 模式，三批 %s，Krita 基线 %d 模式"
          % (BATCH_JSON.relative_to(REPO), doc["counts"]["total_modes"],
             doc["counts"]["batches"], doc["counts"]["with_krita_baseline"]))
    return 0


def cmd_recheck_ps(args) -> int:
    """按「PS 是否接受该模式」重算已出图记录的 ok（不重跑），并把伪图隔离到 _work/rejected_ps/。

    为什么需要这一步：任务 31 的动态实测里 Lght/Grdn/HdMxS 是「PS 拒绝该枚举值」，回读会停在
    上一模式（height）。如果直接拿 mode-ids 的 jsx_readback（=height）当期望值，JSX 的回读校验会
    假通过，把 height 的图当成 lightness/gradient 的图。这里按 mode-ids 的 status 一票否决，
    并把已经落盘的伪图移出 ps/（不删，留证据）。
    """
    modes = {m["order"]: m for m in load_modes()}
    res = json_load(PS_RESULTS, {}) or {}
    rejected = WORK / "rejected_ps"
    rejected.mkdir(parents=True, exist_ok=True)
    n_ok = n_fail = n_quarantine = 0
    for name, rec in res.items():
        m = modes.get(rec.get("order"))
        if m is None:
            continue
        applicable = bool(m["ps_applicable"])
        rec["ps_applicable"] = applicable
        png = PS_DIR / name
        if not applicable:
            rec["error"] = ("PS 不接受该枚举值（mode-ids 记为「读取失败」）；本次回读=%r，"
                            "停在上一模式，图无意义" % rec.get("mode_read", ""))
            if png.is_file():
                shutil.move(str(png), str(rejected / name))
                rec["quarantined_to"] = "tests/baselines/mode/_work/rejected_ps/" + name
                n_quarantine += 1
            rec["exists"] = False
            rec["ok"] = False
        else:
            ok = (bool(rec.get("jsx_ok")) and bool(rec.get("jsx_verify"))
                  and png.is_file())
            rec["ok"] = ok
            if not ok and png.is_file():
                # 失败但落了盘（例如「值被 PS 忽略、回读停在上一模式」）——图是别的模式的，
                # 留在 ps/ 会被当成该模式真值误用，必须移出（移到 _work/rejected_ps 留证据）。
                shutil.move(str(png), str(rejected / name))
                rec["quarantined_to"] = "tests/baselines/mode/_work/rejected_ps/" + name
                rec["exists"] = False
                n_quarantine += 1
        if rec["ok"]:
            n_ok += 1
        else:
            n_fail += 1
    json_dump(PS_RESULTS, res)
    print("[recheck-ps] 记录 %d：ok %d / fail %d，隔离伪图 %d 张 → %s"
          % (len(res), n_ok, n_fail, n_quarantine, rejected.relative_to(REPO)))
    return 0


def cmd_rehash(args) -> int:
    """对已落盘的图重算 Alpha 统计与规范化哈希（不重新出图）。"""
    touched = 0
    for path, sub in ((PS_RESULTS, "ps"), (KR_RESULTS, "krita")):
        res = json_load(path, {}) or {}
        for name, rec in res.items():
            png = MODE_DIR / sub / name
            if not png.is_file():
                rec["exists"] = False
                continue
            try:
                stats = alpha_stats(png)
            except Exception as exc:                      # noqa: BLE001
                rec["alpha_error"] = str(exc)
                continue
            rec.update(stats)
            rec["exists"] = True
            touched += 1
        json_dump(path, res)
    print("[rehash] 重算 %d 张图的 Alpha 统计与 idat_md5/alpha_sha256" % touched)
    return 0


def cmd_manifest(args) -> int:
    ps_res = json_load(PS_RESULTS, {}) or {}
    kr_res = json_load(KR_RESULTS, {}) or {}
    entries = []
    for res, sub, kind in ((ps_res, "ps", "ps"), (kr_res, "krita", "krita")):
        for name, rec in sorted(res.items(), key=lambda kv: (kv[1].get("order", 0), kv[0])):
            png = MODE_DIR / sub / name
            entries.append({
                "file": "%s/%s" % (sub, name), "kind": kind,
                "order": rec.get("order"), "ps_member": rec.get("ps_member"),
                "krita_value": rec.get("krita_value"),
                "depth": rec.get("depth"), "brightness": rec.get("brightness"),
                "contrast": rec.get("contrast"), "ok": bool(rec.get("ok")),
                "exists": png.is_file(),
                "sha256": rec.get("sha256"),
                "idat_md5": rec.get("idat_md5"),
                "alpha_sha256": rec.get("alpha_sha256"),
                "bytes": rec.get("bytes"),
                "alpha_mean": rec.get("alpha_mean"),
                "alpha_unique": rec.get("alpha_unique"),
                "alpha_nonzero_ratio": rec.get("alpha_nonzero_ratio"),
                "error": rec.get("error", rec.get("reason", "")),
            })
    doc = {
        "schema_version": "1.0",
        "task": "33",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "图不入库；本清单登记参数、Alpha 统计与 sha256。命名规则见同目录 README.md",
        "counts": {
            "ps": sum(1 for e in entries if e["kind"] == "ps"),
            "ps_ok": sum(1 for e in entries if e["kind"] == "ps" and e["ok"]),
            "krita": sum(1 for e in entries if e["kind"] == "krita"),
            "krita_ok": sum(1 for e in entries if e["kind"] == "krita" and e["ok"]),
        },
        "entries": entries,
    }
    json_dump(MANIFEST, doc)
    json_dump(BATCH_JSON, build_batch_doc())
    print("[manifest] PS %d（ok %d）/ Krita %d（ok %d）→ %s"
          % (doc["counts"]["ps"], doc["counts"]["ps_ok"],
             doc["counts"]["krita"], doc["counts"]["krita_ok"],
             MANIFEST.relative_to(REPO)))
    return 0


# ---------------------------------------------------------------- 确定性抽检


DETERMINISM_MODES = (4, 20, 38)      # Mltp（4CC 型）、Sbtr（4CC 型）、height（字符串型）


def cmd_verify_ps(args) -> int:
    modes = [m for m in load_modes() if m["order"] in DETERMINISM_MODES]
    jobs = mode_jobs(modes)
    before = json_load(PS_RESULTS, {}) or {}
    PY_RUN.mkdir(parents=True, exist_ok=True)
    with ResourceLock("ps", wait_s=args.wait_lock):
        wait_process_free("Photoshop.exe", "Photoshop", args.wait_ps)
        done, elapsed = run_ps_chunk(jobs, 900, PY_RUN)
    rows = []
    file_same = pixel_same = 0
    for j in jobs:
        a = PS_DIR / j["file"]
        b = PY_RUN / j["file"]
        ha = sha256_of(a) if a.is_file() else ""
        hb = sha256_of(b) if b.is_file() else ""
        ia = idat_md5(a) if a.is_file() else ""
        ib = idat_md5(b) if b.is_file() else ""
        max_delta = None
        alpha_same = False
        if a.is_file() and b.is_file():
            aa = np.array(Image.open(a).convert("RGBA"))[:, :, 3].astype(np.int32)
            bb = np.array(Image.open(b).convert("RGBA"))[:, :, 3].astype(np.int32)
            max_delta = int(np.abs(aa - bb).max())
            alpha_same = bool(max_delta == 0)
        file_flag = bool(ha and hb and ha == hb)
        pixel_flag = bool(ia and ib and ia == ib and alpha_same)
        file_same += 1 if file_flag else 0
        pixel_same += 1 if pixel_flag else 0
        rows.append({"file": j["file"],
                     "sha256_run1": ha, "sha256_run2": hb, "file_identical": file_flag,
                     "idat_md5_run1": ia, "idat_md5_run2": ib,
                     "alpha_max_delta": max_delta, "pixel_identical": pixel_flag})
    out = {"modes": list(DETERMINISM_MODES), "compared": len(rows),
           "file_identical": file_same, "pixel_identical": pixel_same,
           "elapsed_s": round(elapsed, 2),
           "note": "整文件 sha256 每次不同，原因已定位为 PS 写入的 XMP iTXt 块（含创建时间）；"
                   "IDAT 像素流与 Alpha 通道两次完全一致，故用 idat_md5 + alpha_max_delta 判确定性",
           "rows": rows}
    json_dump(WORK / "determinism_ps.json", out)
    print("[确定性·PS] %d 张：整文件 sha256 相同 %d；像素（IDAT+Alpha）相同 %d"
          % (len(rows), file_same, pixel_same))
    return 0 if pixel_same == len(rows) else 2


def cmd_verify_krita(args) -> int:
    modes = [m for m in load_modes()
             if m["order"] in DETERMINISM_MODES and m.get("krita_value") is not None]
    KR_RUN.mkdir(parents=True, exist_ok=True)
    rows = []
    same = diff = 0
    with ResourceLock("krita", wait_s=args.wait_lock):
        wait_process_free("krita.exe", "Krita", args.wait_krita)
        for m in modes:
            for (d, b, c) in GROUPS:
                kpp = KPP_DIR / krita_kpp_name(m, d, b, c)
                name = krita_file_name(m, d, b, c)
                out = KR_RUN / name
                if not kpp.is_file():
                    rows.append({"file": name, "ok": False, "error": "缺少 .kpp"})
                    continue
                res = runner.render_one(str(kpp), str(out), size=SIZE, stroke=STROKE,
                                        pressure=PRESSURE, route="gui", timeout=GUI_TIMEOUT)
                a = KRITA_DIR / name
                ok = bool(res.get("ok")) and a.is_file() and out.is_file()
                ha = sha256_of(a) if a.is_file() else ""
                hb = sha256_of(out) if out.is_file() else ""
                same_flag = bool(ok and ha and hb and ha == hb)
                same += 1 if same_flag else 0
                diff += 0 if same_flag else 1
                max_delta = None
                if a.is_file() and out.is_file():
                    try:
                        aa = np.array(Image.open(a).convert("RGBA"))[:, :, 3].astype(np.int32)
                        bb = np.array(Image.open(out).convert("RGBA"))[:, :, 3].astype(np.int32)
                        max_delta = int(np.abs(aa - bb).max())
                    except Exception:                     # noqa: BLE001
                        max_delta = None
                rows.append({"file": name, "sha256_run1": ha, "sha256_run2": hb,
                             "identical": same_flag, "alpha_max_delta": max_delta, "ok": ok})
    out = {"modes": list(DETERMINISM_MODES), "compared": len(rows), "same": same,
           "different": diff, "rows": rows}
    json_dump(WORK / "determinism_krita.json", out)
    print("[确定性·Krita] %d 张比对：sha256 相同 %d，不同 %d" % (len(rows), same, diff))
    return 0 if diff == 0 else 2


# ---------------------------------------------------------------- status


def cmd_status(args) -> int:
    ps_res = json_load(PS_RESULTS, {}) or {}
    kr_res = json_load(KR_RESULTS, {}) or {}
    modes = load_modes()
    crit = critical_modes(modes)
    print("模式：%d（有 Krita 基线 %d，仅 PS 真值 %d）"
          % (len(modes), len(crit), len(modes) - len(crit)))
    print("PS  ：已出 %d / 计划 %d，ok %d，失败 %d"
          % (len(ps_res), len(modes) * len(GROUPS),
             sum(1 for r in ps_res.values() if r.get("ok")),
             sum(1 for r in ps_res.values() if not r.get("ok"))))
    print("Krita：已渲染 %d / 计划 %d，ok %d，失败 %d"
          % (len(kr_res), len(crit) * len(GROUPS),
             sum(1 for r in kr_res.values() if r.get("ok")),
             sum(1 for r in kr_res.values() if not r.get("ok"))))
    el = [r.get("elapsed_s") for r in kr_res.values() if r.get("elapsed_s")]
    if el:
        print("Krita 单张耗时：均值 %.1fs，min %.1fs，max %.1fs"
              % (sum(el) / len(el), min(el), max(el)))
    fail_modes = sorted({(r.get("order"), r.get("ps_member")) for r in ps_res.values()
                         if not r.get("ok")})
    if fail_modes:
        print("PS 失败模式：%r" % (fail_modes,))
    return 0


# ---------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="任务 33：真值与基线批量生成")
    ap.add_argument("--plan", action="store_true", help="写 mode-batch.json + README + .gitignore")
    ap.add_argument("--ps", action="store_true", help="PS 真值批量出图")
    ap.add_argument("--krita-build", action="store_true", help="生成 Krita .kpp")
    ap.add_argument("--krita-render", action="store_true", help="串行渲染 Krita 基线")
    ap.add_argument("--manifest", action="store_true", help="汇总 manifest 并刷新 mode-batch.json")
    ap.add_argument("--recheck-ps", action="store_true", help="按 PS 是否接受该模式重算已出图 ok")
    ap.add_argument("--rehash", action="store_true", help="对已落盘的图重算 Alpha 统计（不重出图）")
    ap.add_argument("--verify-ps", action="store_true", help="确定性抽检（PS）")
    ap.add_argument("--verify-krita", action="store_true", help="确定性抽检（Krita）")
    ap.add_argument("--status", action="store_true", help="打印进度")
    ap.add_argument("--resume", action="store_true", help="跳过已成功的图")
    ap.add_argument("--limit", type=int, default=None, help="本次最多处理多少张")
    ap.add_argument("--limit-modes", type=int, default=None, help="只用前 N 个模式（调试）")
    ap.add_argument("--chunk", type=int, default=PS_CHUNK, help="PS 单批张数")
    ap.add_argument("--wait-lock", type=float, default=5400.0, help="等锁上限（秒）")
    ap.add_argument("--wait-ps", type=float, default=5400.0, help="等 Photoshop 空闲上限（秒）")
    ap.add_argument("--wait-krita", type=float, default=1800.0, help="等 Krita 空闲上限（秒）")
    args = ap.parse_args(argv)

    rc = 0
    if args.plan:
        rc |= cmd_plan(args)
    if args.krita_build:
        build_krita_presets()
    if args.ps:
        rc |= cmd_ps(args)
    if args.krita_render:
        rc |= cmd_krita_render(args)
    if args.verify_ps:
        rc |= cmd_verify_ps(args)
    if args.verify_krita:
        rc |= cmd_verify_krita(args)
    if args.recheck_ps:
        rc |= cmd_recheck_ps(args)
    if args.rehash:
        rc |= cmd_rehash(args)
    if args.manifest:
        rc |= cmd_manifest(args)
    if args.status:
        rc |= cmd_status(args)
    if not any([args.plan, args.ps, args.krita_build, args.krita_render, args.manifest,
                args.recheck_ps, args.rehash, args.verify_ps, args.verify_krita,
                args.status]):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
