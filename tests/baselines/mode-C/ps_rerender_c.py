# -*- coding: utf-8 -*-
"""任务 36（C 批）：补出 5 个「PS 真值失败」模式的真值图（改正枚举写法后重出）。

【为什么需要本脚本】
任务 33 的 PS 真值里有 13 个模式失败，其中 C 批占 5 个（序号 32/33/34/35/36）。
失败原因不是「PS 没有该模式」，而是**喂给 textureBlendMode 的名字/类型写错了**：
  - 32/33/34/35 的 ps_member（SnrB/MnrB/HnrB/ttsD）是 Ghidra 把小端立即数反向渲染的伪名，
    真实 4CC 是 BrnS/BrnM/BrnH/Dstt（证据：docs/数据/mode-ids.json 的 name_corrections，
    以及 mode-ids.modes[i].ps_value_4cc / jsx_readback：burnInS/burnInM/burnInH/desaturate）；
  - 36 的 ps_member 是 4CC "Str "（=saturate），但 mode-align 把它标成 ps_id_kind="string"，
    转换脚本因此用 stringIDToTypeID 喂值 → 被 PS 忽略；正确写法是 charIDToTypeID("Str ")。
【口径】与任务 33 完全一致（同一支笔、同一纹理、同一画布、同一落笔、同一 6 组参数），
只有 mode_value / mode_kind / mode_key 三项按上面改正。
【产物】tests/baselines/mode-C/ps/ps__mode__<序号>_<模式>__d<深度>_b<亮度>_c<对比度>.png
        tests/baselines/mode-C/ps_rerender_result.json（逐张 ok/verify/mode_read/alpha 统计）
【资源】Photoshop 是单实例资源：抢 %TEMP%\\brushconverter-team\\locks\\ps.lock（mkdir 原子锁），
        跑完释放；结束前 taskkill 兜底清理本次启动的 PS。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[3]
MODE_DIR = REPO / "tests" / "baselines" / "mode"
OUT_DIR = REPO / "tests" / "baselines" / "mode-C" / "ps"
WORK = REPO / "tests" / "baselines" / "mode-C" / "_work"
RESULT = REPO / "tests" / "baselines" / "mode-C" / "ps_rerender_result.json"
LOCK_ROOT = Path(os.environ.get("TEMP") or "/tmp") / "brushconverter-team" / "locks"

ABR_PATH = REPO / "tests" / "定量测试专用.abr"
TEXTURE_PATH = REPO / "research" / "测试用纹理.png"
JSX_PATH = REPO / "tools" / "ps_export.jsx"
PS_EXE = Path(os.environ.get("BRUSHCONVERTER_PS",
                             r"D:/program/PS2025/Adobe Photoshop 2025/Photoshop.exe"))

CANVAS = {"width": 100, "height": 100, "dpi": 72}
BRUSH = {"diameter": 200, "hardness": 100, "spacing": 10}
TEXTURE_NAME = "bc_mode_texture"
TEXTURE_SCALE = 100
INVERT_TEXTURE = True
ABR_BRUSH_NAME = "定量测试用"
PS_TIMEOUT = 900

DEPTHS = (10, 60, 100)
BC = ((0, 0), (-50, -50))
GROUPS = tuple((d, b, c) for d in DEPTHS for (b, c) in BC)

# 序号 / 文件名里的模式名 / JSX 的 mode_kind / 真正的枚举值 / 回读期望名
C_MODES = [
    (32, "SnrB", "char", "BrnS", "burnInS"),
    (33, "MnrB", "char", "BrnM", "burnInM"),
    (34, "HnrB", "char", "BrnH", "burnInH"),
    (35, "ttsD", "char", "Dstt", "desaturate"),
    (36, "Str ", "char", "Str ", "saturate"),
]


def json_dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def sanitize(token: str) -> str:
    t = str(token).strip()
    t = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in t)
    return t or "unnamed"


def group_tag(depth: int, brightness: int, contrast: int) -> str:
    return "d%d_b%d_c%d" % (depth, brightness, contrast)


def ps_file_name(order: int, member: str, d: int, b: int, c: int) -> str:
    return "ps__mode__%02d_%s__%s.png" % (order, sanitize(member), group_tag(d, b, c))


def alpha_stats(path: Path) -> dict:
    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        a = np.asarray(rgba, dtype=np.uint8)[:, :, 3]
    import hashlib
    return {
        "width": int(a.shape[1]), "height": int(a.shape[0]),
        "alpha_mean": round(float(a.mean()), 3),
        "alpha_unique": int(len(np.unique(a))),
        "alpha_nonzero_ratio": round(float(np.count_nonzero(a)) / a.size, 4),
        "alpha_sha256": hashlib.sha256(a.tobytes()).hexdigest(),
    }


class ResourceLock:
    """mkdir 原子锁：抢不到就退避轮询，绝不强杀别人的进程。"""

    def __init__(self, name: str, wait_s: float = 1800.0, poll_s: float = 20.0,
                 stale_s: float = 3600.0):
        self.name = name
        self.wait_s = wait_s
        self.poll_s = poll_s
        self.stale_s = stale_s
        self.path = LOCK_ROOT / (name + ".lock")

    def __enter__(self):
        LOCK_ROOT.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        while True:
            try:
                os.mkdir(str(self.path))
                json_dump(self.path / "owner.json", {
                    "task": "36", "resource": self.name, "pid": os.getpid(),
                    "acquired_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                print("[锁] 已获得 %s（等待 %.0fs）" % (self.name, time.time() - t0))
                return self
            except FileExistsError:
                owner = {}
                try:
                    owner = json.loads((self.path / "owner.json").read_text(encoding="utf-8"))
                except Exception:                                  # noqa: BLE001
                    pass
                age = time.time() - self.path.stat().st_mtime
                if age > self.stale_s:
                    print("[锁] %s 锁已超过 %.0fs，判定为残留，清理" % (self.name, age))
                    shutil.rmtree(str(self.path), ignore_errors=True)
                    continue
                if time.time() - t0 > self.wait_s:
                    raise SystemExit("等待 %s 锁超过 %.0fs，放弃（持有者：%r）"
                                     % (self.name, self.wait_s, owner))
                print("[锁] %s 被占用（%r，已 %.0fs），%.0fs 后重试"
                      % (self.name, owner.get("task", "?"), age, self.poll_s))
                time.sleep(self.poll_s)

    def __exit__(self, exc_type, exc, tb):
        shutil.rmtree(str(self.path), ignore_errors=True)
        print("[锁] 已释放 %s" % self.name)
        return False


def process_running(name: str) -> bool:
    out = subprocess.run(["tasklist"], capture_output=True, text=True,
                         errors="ignore").stdout or ""
    return name.lower() in out.lower()


def wait_process_free(name: str, label: str, timeout_s: float) -> None:
    t0 = time.time()
    while process_running(name):
        if time.time() - t0 > timeout_s:
            raise SystemExit("等待 %s 空闲超过 %.0fs，放弃（不强行结束别人的进程）" % (label, timeout_s))
        print("[等待] %s 正在运行，退避轮询…" % label)
        time.sleep(20.0)


def kill_photoshop() -> None:
    subprocess.run(["taskkill", "-F", "-IM", "Photoshop.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def build_jobs() -> list[dict]:
    jobs = []
    for order, member, kind, value, key in C_MODES:
        for (d, b, c) in GROUPS:
            jobs.append({
                "order": order, "ps_member": member,
                "file": ps_file_name(order, member, d, b, c),
                "mode_kind": kind, "mode_value": value, "mode_key": key,
                "use_texture": True, "invert_texture": INVERT_TEXTURE,
                "texture_scale": TEXTURE_SCALE,
                "depth": d, "brightness": b, "contrast": c,
            })
    return jobs


def payload(jobs: list[dict]) -> dict:
    return {
        "out_dir": OUT_DIR.as_posix(),
        "texture_path": TEXTURE_PATH.as_posix(),
        "texture_name": TEXTURE_NAME,
        "texture_scale": TEXTURE_SCALE,
        "abr_path": ABR_PATH.as_posix(),
        "brush_name": ABR_BRUSH_NAME,
        "canvas": CANVAS,
        "brush": BRUSH,
        "jobs": [{k: j[k] for k in ("file", "mode_key", "mode_kind", "mode_value",
                                    "use_texture", "invert_texture", "texture_scale",
                                    "depth", "brightness", "contrast")} for j in jobs],
    }


def run_ps(jobs: list[dict]) -> list[dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    done_path = WORK / "done.json"
    log_path = WORK / "batch.log"
    for p in (done_path, log_path):
        if p.exists():
            p.unlink()
    json_dump(WORK / "job.json", payload(jobs))
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
    print("[PS] done.json 出现，总耗时 %.1fs" % (time.time() - t0))
    data = json.loads(done_path.read_text(encoding="utf-8"))
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    kill_photoshop()
    if isinstance(data, dict):
        data = data.get("results") or []
    return data


def main() -> int:
    if not PS_EXE.is_file():
        raise SystemExit("找不到 Photoshop：%s" % PS_EXE)
    jobs = build_jobs()
    print("[PS] 计划 %d 张（5 模式 × 6 组）" % len(jobs))
    with ResourceLock("ps"):
        wait_process_free("Photoshop.exe", "Photoshop", 1800)
        done = run_ps(jobs)
    by_file = {d.get("file", ""): d for d in done}
    results = {"schema_version": "1.0", "task": "36",
               "purpose": "C 批 5 个失败模式按改正后的枚举写法重出 PS 真值",
               "fixed": [{"order": o, "ps_member": m, "mode_kind": k, "mode_value": v,
                          "mode_key": kk} for o, m, k, v, kk in C_MODES],
               "entries": []}
    for j in jobs:
        d = by_file.get(j["file"], {})
        png = OUT_DIR / j["file"]
        rec = {
            "order": j["order"], "ps_member": j["ps_member"], "depth": j["depth"],
            "brightness": j["brightness"], "contrast": j["contrast"], "file": j["file"],
            "mode_value": j["mode_value"], "mode_key": j["mode_key"],
            "exists": png.is_file(), "ok": bool(d.get("ok")), "verify": bool(d.get("verify")),
            "mode_read": d.get("mode_read", ""), "readback": d.get("readback", ""),
            "error": d.get("error", ""),
        }
        if png.is_file():
            try:
                rec.update(alpha_stats(png))
            except Exception as exc:                              # noqa: BLE001
                rec["alpha_error"] = str(exc)
        results["entries"].append(rec)
        print("%s ok=%s verify=%s mode_read=%-10s u=%s mean=%s"
              % (j["file"], rec["ok"], rec["verify"], rec["mode_read"],
                 rec.get("alpha_unique"), rec.get("alpha_mean")))
    ok = sum(1 for r in results["entries"] if r["ok"] and r["verify"])
    results["counts"] = {"planned": len(jobs), "written": sum(1 for r in results["entries"] if r["exists"]),
                         "ok_verify": ok}
    json_dump(RESULT, results)
    print("[PS] 写图 %d 张，ok&verify %d 张 → %s"
          % (results["counts"]["written"], ok, RESULT))
    return 0 if ok == len(jobs) else 1


if __name__ == "__main__":
    sys.exit(main())
