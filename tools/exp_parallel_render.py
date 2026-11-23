# -*- coding: utf-8 -*-
r"""任务 127：Krita 并行渲染门（可复跑）。

【本工具回答的问题】
两个独立 Krita 进程能否同时渲染同一批「计算笔刷」（auto_brush）并且逐像素等价？
判据（任务书写死）：10/10 张差异比例 0、max_delta 0、0 崩溃、0 假失败 → 可行；
否则记录失败现场（哪张、差多少、日志尾部、退出码）。

【三个阶段】
1) serial  ：串行逐张渲染 10 张（基线 A）。
2) parallel：两个独立 Krita 进程并发，每进程渲染 5 张（待测 B）。
             每个进程有独立输出目录；进程各自串行渲染自己的 5 张。
3) cross   ：再串行渲染一次同样的 10 张（交叉复算 C），用于区分
             「并行差异」与「Krita 自身渲染非确定」。

【比对口径】
只认 PNG 的像素（解码后的 IDAT/RGBA），不认文件 sha256（iTXt/时间戳会变）。
RGB 与 Alpha 分别给差异像素数、差异比例、max_delta；同时记录
IDAT 哈希（原始压缩流）与像素哈希（解码后 RGBA）两种指纹。

【profile 说明（重要）】
Krita 5.3.3（Windows）只有一个用户级 profile：
- kritarc 固定在 %LOCALAPPDATA%\kritarc，CLI 没有重定向入口；
- --resource-location 能重定向 resources，但实测会禁用 PyKrita 插件加载（见 --mode both 的隔离探针）；
- APPDATA/LOCALAPPDATA 环境变量对 Krita 无效（Qt 用 Known Folder API）。
因此本工具按「本机事实」实现：临时改写共享 kritarc + 共享 pykrita 插件 + 临时安装 10 张预设，
结束一律还原（finally）。并在结果 JSON 里记录隔离探针结论。

【用法】
    python tools/exp_parallel_render.py --mode both            # 完整门（默认）
    python tools/exp_parallel_render.py --mode serial          # 只跑串行基线 + 交叉复算
    python tools/exp_parallel_render.py --mode parallel        # 只跑并行尝试（需已有基线）
    python tools/exp_parallel_render.py --mode both --workdir .tmp/127

内部：--worker <0|1> 由父进程调用，不要手工用。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "tools")
sys.path.insert(0, TOOLS)

# 默认值一律用占位符（隐私门：公开仓库不写本机安装路径）；本机运行时用环境变量覆盖。
KRITA_BIN = os.environ.get(
    "BRUSHCONVERTER_KRITA", r"<LOCAL_APP_DIR>/Krita (x64)/bin/krita.com"
)
KRITARUNNER = os.environ.get(
    "BRUSHCONVERTER_KRITARUNNER", r"<LOCAL_APP_DIR>/Krita (x64)/bin/kritarunner.exe"
)
PLUGIN_SRC = os.path.join(TOOLS, "krita_render_gui_plugin.py")
ABR_DIR = os.path.join(REPO, "tests", "abr-generated")
PLUGIN_NAME = "bc_auto_render"

APPDATA = os.environ.get("APPDATA", "")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
KRITARC = os.path.join(LOCALAPPDATA, "kritarc")
REAL_PYKRITA = os.path.join(APPDATA, "krita", "pykrita")
REAL_PRESETS = os.path.join(APPDATA, "krita", "paintoppresets")
RESOURCE_CACHE = os.path.join(APPDATA, "krita", "resourcecache.sqlite")

# 10 张计算笔刷（来自 tests/abr-generated 的 20 支测试 ABR；全部 kind=computed/auto_brush）
# 排除 scatter 族：其散布动态带随机数，不适合做「逐像素等价」门样本（噪声会掩盖真实差异）。
DEFAULT_SAMPLES = [
    "angle-000",
    "angle-045",
    "angle-090",
    "hardness-000",
    "hardness-100",
    "size-020",
    "size-100",
    "size-300",
    "scale-100",
    "hardness-050",
]

CANVAS_SIZE = (200, 200)
STROKE = 120
PRESSURE = 1.0
DEFAULT_TIMEOUT = 60.0
DEFAULT_PARALLEL_TIMEOUT = 30.0


# ------------------------------------------------------------------ PNG 工具


def _png_chunks(data):
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        yield ctype, payload
        pos += 12 + length


def png_idat_sha256(path):
    """PNG 原始 IDAT 压缩流哈希（编码器层指纹，不用于判等）。"""
    with open(path, "rb") as fh:
        data = fh.read()
    digest = hashlib.sha256()
    for ctype, payload in _png_chunks(data):
        if ctype == b"IDAT":
            digest.update(payload)
    return digest.hexdigest()


def png_pixel_sha256(path):
    """解码后 RGBA 像素哈希（像素层指纹）。"""
    from PIL import Image
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        return hashlib.sha256(rgba.tobytes()).hexdigest()


def kpp_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def kpp_brush_definition(path):
    """从 .kpp（PNG，iTXt keyword=preset）里取出 brush_definition 文本。"""
    with open(path, "rb") as fh:
        data = fh.read()
    preset = b""
    for ctype, payload in _png_chunks(data):
        if ctype == b"iTXt":
            kw_end = payload.index(b"\0")
            keyword = payload[:kw_end].decode("latin-1")
            if keyword != "preset":
                continue
            rest = payload[kw_end + 1:]
            compressed = rest[0]
            rest2 = rest[2:]
            lang_end = rest2.index(b"\0")
            rest3 = rest2[lang_end + 1:]
            trans_end = rest3.index(b"\0")
            text = rest3[trans_end + 1:]
            preset = zlib.decompress(text) if compressed else text
    if not preset:
        return ""
    xml = preset.decode("utf-8", "replace")
    match = re.search(r'<param name="brush_definition"[^>]*>(.*?)</param>', xml, re.S)
    return match.group(1) if match else ""


def kpp_is_computed(path):
    definition = kpp_brush_definition(path)
    return 'type="auto_brush"' in definition


def image_stats(path):
    """解码后的 Alpha 统计 + 指纹。"""
    from PIL import Image
    import numpy as np
    with Image.open(path) as image:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    alpha = rgba[:, :, 3]
    return {
        "width": int(rgba.shape[1]),
        "height": int(rgba.shape[0]),
        "alpha_nonzero": int(np.count_nonzero(alpha)),
        "alpha_max": int(alpha.max()),
        "alpha_sum": int(alpha.sum()),
        "pixel_sha256": hashlib.sha256(rgba.tobytes()).hexdigest(),
        "idat_sha256": png_idat_sha256(path),
    }


def compare_rgba(path_a, path_b):
    """RGB 与 Alpha 分别比对（差异像素分母 = 全部像素；max_delta 取最大绝对差）。"""
    from PIL import Image
    import numpy as np
    with Image.open(path_a) as image:
        a = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    with Image.open(path_b) as image:
        b = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    if a.shape != b.shape:
        return {"error": "尺寸不一致：%r vs %r" % (a.shape, b.shape)}
    height, width = a.shape[0], a.shape[1]
    total = height * width
    rgb_delta = np.abs(a[:, :, :3].astype(np.int16) - b[:, :, :3].astype(np.int16))
    alpha_delta = np.abs(a[:, :, 3].astype(np.int16) - b[:, :, 3].astype(np.int16))
    rgb_diff = int(np.count_nonzero(rgb_delta))
    alpha_diff = int(np.count_nonzero(alpha_delta))
    return {
        "width": int(width),
        "height": int(height),
        "rgb_diff_pixels": rgb_diff,
        "rgb_diff_ratio": rgb_diff / total,
        "rgb_max_delta": int(rgb_delta.max()),
        "alpha_diff_pixels": alpha_diff,
        "alpha_diff_ratio": alpha_diff / total,
        "alpha_max_delta": int(alpha_delta.max()),
    }


# ------------------------------------------------------------------ 进程 / profile


def _tasklist_krita():
    """返回当前 krita* 进程 [{"pid","name"}]。"""
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=20
        )
    except Exception:
        return []
    rows = []
    for line in (proc.stdout or b"").decode("utf-8", "replace").splitlines():
        if '"krita' not in line.lower():
            continue
        parts = [part.strip('"') for part in line.split('","')]
        if len(parts) >= 2:
            rows.append({"pid": parts[1], "name": parts[0]})
    return rows


def _kill_tree(pid):
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=20)
    except Exception:
        pass


def kill_krita(name_filter="krita"):
    for row in _tasklist_krita():
        if name_filter in row["name"].lower():
            _kill_tree(row["pid"])


def _patch_kritarc(text):
    text = re.sub(r"(?m)^enable_%s=.*\r?$" % PLUGIN_NAME, "", text)
    if "[python]\n" in text:
        text = text.replace("[python]\n",
                            "[python]\nenable_%s=true\n" % PLUGIN_NAME, 1)
    else:
        text += "\n[python]\nenable_%s=true\n" % PLUGIN_NAME
    return re.sub(r"(?m)^sessionOnStartup=\d+\r?$", "sessionOnStartup=2", text)


def _install_plugin():
    os.makedirs(os.path.join(REAL_PYKRITA, PLUGIN_NAME), exist_ok=True)
    shutil.copyfile(PLUGIN_SRC,
                    os.path.join(REAL_PYKRITA, PLUGIN_NAME, "__init__.py"))
    desktop = os.path.join(REAL_PYKRITA, PLUGIN_NAME + ".desktop")
    with open(desktop, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("[Desktop Entry]\nType=Service\nServiceTypes=Krita/PythonPlugin\n"
                 "X-KDE-Library=%s\nX-Python-2-Compatible=false\nName=BC Auto Render\n"
                 % PLUGIN_NAME)
    return desktop


def _remove_plugin(desktop_path):
    shutil.rmtree(os.path.join(REAL_PYKRITA, PLUGIN_NAME), ignore_errors=True)
    if desktop_path and os.path.isfile(desktop_path):
        os.remove(desktop_path)


class SharedProfile(object):
    """临时改写共享 profile（kritarc + pykrita + paintoppresets + resourcecache 备份）。"""

    def __init__(self, workdir, kpps):
        self.workdir = workdir
        self.kpps = kpps
        self.backup_dir = os.path.join(workdir, "profile_backup")
        self.kritarc_original = None
        self.installed = []
        self.desktop = None
        self.cache_backup = None

    def __enter__(self):
        os.makedirs(self.backup_dir, exist_ok=True)
        if not os.path.isfile(KRITARC):
            raise RuntimeError("找不到 kritarc：%s" % KRITARC)
        with open(KRITARC, "rb") as fh:
            self.kritarc_original = fh.read()
        text = self.kritarc_original.decode("utf-8", "replace")
        patched = _patch_kritarc(text)
        if "enable_%s=true" % PLUGIN_NAME not in patched:
            raise RuntimeError("kritarc 补丁失败")
        with open(KRITARC, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(patched)

        self.desktop = _install_plugin()
        os.makedirs(REAL_PRESETS, exist_ok=True)
        for kpp in self.kpps:
            dst = os.path.join(REAL_PRESETS, os.path.basename(kpp))
            existed = os.path.isfile(dst)
            shutil.copyfile(kpp, dst)
            self.installed.append((dst, existed))

        if os.path.isfile(RESOURCE_CACHE):
            self.cache_backup = os.path.join(self.backup_dir, "resourcecache.sqlite")
            try:
                shutil.copy2(RESOURCE_CACHE, self.cache_backup)
            except Exception:
                self.cache_backup = None
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            with open(KRITARC, "wb") as fh:
                fh.write(self.kritarc_original)
        except Exception:
            pass
        _remove_plugin(self.desktop)
        for dst, existed in self.installed:
            if not existed and os.path.isfile(dst):
                try:
                    os.remove(dst)
                except Exception:
                    pass
        if self.cache_backup and os.path.isfile(self.cache_backup):
            try:
                shutil.copy2(self.cache_backup, RESOURCE_CACHE)
            except Exception:
                pass
        return False


def _count_log_warnings(text):
    if not text:
        return 0
    pattern = re.compile(r"警告|错误|异常|失败|Traceback|ERROR|WARNING|CRITICAL", re.I)
    return sum(1 for line in text.splitlines() if pattern.search(line))


def render_one(sample, out_png, timeout, workdir):
    """单次真实渲染：启动一个 Krita 进程 → 插件落笔导出 → 解析结果。"""
    out_png = os.path.abspath(out_png)
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    result_json = out_png + ".result.json"
    gui_log = out_png + ".gui.log"
    for path in (out_png, result_json, gui_log):
        if os.path.isfile(path):
            os.remove(path)
    env = os.environ.copy()
    env.update({
        "BC_AUTO_LOG": gui_log,
        "BC_AUTO_OUT": out_png,
        "BC_AUTO_PRESULT": result_json,
        "BC_AUTO_PRESET_FILE": os.path.basename(sample["kpp"]),
        "BC_AUTO_SIZE": "%dx%d" % CANVAS_SIZE,
        "BC_AUTO_STROKE": str(STROKE),
        "BC_AUTO_PRESSURE": str(PRESSURE),
    })
    started = time.time()
    timed_out = False
    proc = subprocess.Popen([KRITA_BIN, "--nosplash"], env=env, cwd=REPO,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc.pid)
        try:
            stdout, stderr = proc.communicate(timeout=20)
        except Exception:
            stdout = stderr = b""
        exit_code = None
    elapsed = round(time.time() - started, 2)
    stderr_text = (stderr or b"").decode("utf-8", "replace")
    gui_text = ""
    if os.path.isfile(gui_log):
        with open(gui_log, "r", encoding="utf-8", errors="replace") as fh:
            gui_text = fh.read()
    payload = None
    if os.path.isfile(result_json):
        try:
            with open(result_json, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            payload = None

    ok = bool(payload and payload.get("ok") and os.path.isfile(out_png))
    entry = {
        "index": sample["index"],
        "name": sample["name"],
        "abr": sample["abr"],
        "kpp": os.path.basename(sample["kpp"]),
        "kpp_sha256": sample.get("kpp_sha256", ""),
        "out_png": os.path.relpath(out_png, REPO).replace("\\", "/"),
        "ok": ok,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "elapsed_s": elapsed,
        "alpha_nonzero": payload.get("alpha_nonzero") if payload else None,
        "log_tail": "\n".join([line for line in gui_text.splitlines() if line.strip()][-20:]),
        "log_warn_lines": _count_log_warnings(gui_text),
        "stderr_tail": "\n".join([line for line in stderr_text.splitlines() if line.strip()][-20:]),
        "stderr_warn_lines": _count_log_warnings(stderr_text),
    }
    if ok:
        stats = image_stats(out_png)
        entry.update({
            "width": stats["width"],
            "height": stats["height"],
            "alpha_nonzero": stats["alpha_nonzero"],
            "alpha_max": stats["alpha_max"],
            "alpha_sum": stats["alpha_sum"],
            "pixel_sha256": stats["pixel_sha256"],
            "idat_sha256": stats["idat_sha256"],
        })
    else:
        # 分类：无产物且进程 0 退出 = 单实例转发（假失败）；进程异常退出/超时 = 崩溃/挂起
        if exit_code == 0 and not payload and elapsed < 12:
            entry["failure_kind"] = "single_instance_forwarded"
        elif timed_out:
            entry["failure_kind"] = "timeout_hang"
        elif payload and not payload.get("ok"):
            entry["failure_kind"] = "plugin_reported_failure"
        else:
            entry["failure_kind"] = "unknown"
    return entry


# ------------------------------------------------------------------ 样本准备


def sample_kpp_name(stem):
    return "000_BC-v140-%s.kpp" % stem


def ensure_samples(workdir, stems, skip_convert=False):
    kpp_dir = os.path.join(workdir, "kpp", "kpp")
    os.makedirs(kpp_dir, exist_ok=True)
    samples = []
    for index, stem in enumerate(stems, start=1):
        abr = os.path.join(ABR_DIR, stem + ".abr")
        kpp = os.path.join(kpp_dir, sample_kpp_name(stem))
        if not os.path.isfile(kpp):
            if skip_convert:
                raise RuntimeError("缺少 .kpp：%s（--skip-convert 下不转换）" % kpp)
            cmd = [sys.executable, os.path.join(REPO, "cli.py"), "convert", abr,
                   "-o", os.path.join(workdir, "kpp"), "--no-bundle"]
            proc = subprocess.run(cmd, cwd=REPO, capture_output=True)
            if proc.returncode != 0:
                raise RuntimeError("转换失败 %s：%s" % (
                    stem, (proc.stderr or b"").decode("utf-8", "replace")[-800:]))
            if not os.path.isfile(kpp):
                raise RuntimeError("转换后仍缺少 .kpp：%s" % kpp)
        if not kpp_is_computed(kpp):
            raise RuntimeError("样本不是计算笔刷（不是 auto_brush）：%s" % kpp)
        samples.append({
            "index": index,
            "name": stem,
            "abr": os.path.basename(abr),
            "kpp": kpp,
            "kpp_sha256": kpp_sha256(kpp),
        })
    return samples


# ------------------------------------------------------------------ 阶段执行


def phase_serial(samples, workdir, timeout, tag):
    out_dir = os.path.join(workdir, tag)
    results = []
    for sample in samples:
        entry = render_one(sample, os.path.join(out_dir, "%02d_%s.png" % (sample["index"], sample["name"])),
                           timeout, workdir)
        entry["phase"] = tag
        results.append(entry)
        print("[%s] %02d %-16s ok=%s alpha=%s %.2fs %s" % (
            tag, sample["index"], sample["name"], entry["ok"],
            entry.get("alpha_nonzero"), entry["elapsed_s"],
            entry.get("failure_kind", "")), flush=True)
    return results


def phase_parallel_attempt(samples, workdir, timeout):
    """父进程：起两个 worker 子进程并发渲染；同时采样 krita 并发数。"""
    samples_json = os.path.join(workdir, "samples.json")
    with open(samples_json, "w", encoding="utf-8") as fh:
        json.dump(samples, fh, ensure_ascii=False, indent=1)
    cmd = [sys.executable, os.path.abspath(__file__), "--worker", "0",
           "--workdir", workdir, "--timeout", str(timeout),
           "--samples-json", samples_json]
    cmd2 = [sys.executable, os.path.abspath(__file__), "--worker", "1",
            "--workdir", workdir, "--timeout", str(timeout),
            "--samples-json", samples_json]
    t0 = time.time()
    p1 = subprocess.Popen(cmd, cwd=REPO)
    p2 = subprocess.Popen(cmd2, cwd=REPO)
    max_concurrent = 0
    samples_of_concurrency = []
    while p1.poll() is None or p2.poll() is None:
        count = len(_tasklist_krita())
        max_concurrent = max(max_concurrent, count)
        samples_of_concurrency.append(count)
        time.sleep(0.5)
    elapsed = round(time.time() - t0, 2)
    workers = {}
    for idx in (0, 1):
        path = os.path.join(workdir, "worker%d.json" % idx)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                workers[str(idx)] = json.load(fh)
    all_results = []
    for idx in ("0", "1"):
        all_results.extend(workers.get(idx, {}).get("results", []))
    all_results.sort(key=lambda item: item.get("index", 0))
    return {
        "elapsed_s": elapsed,
        "returncodes": [p1.returncode, p2.returncode],
        "max_concurrent_krita": max_concurrent,
        "concurrency_samples": samples_of_concurrency,
        "workers": workers,
        "results": all_results,
    }


def phase_single_instance_probe(timeout=20.0):
    """探测：第二个 Krita 进程能否与第一个并存（QtSingleApplication）。"""
    kill_krita()
    env = os.environ.copy()
    started = time.time()
    first = subprocess.Popen([KRITA_BIN, "--nosplash"], env=env, cwd=REPO,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6.0)
    first_alive = first.poll() is None
    second = subprocess.Popen([KRITA_BIN, "--nosplash"], env=env, cwd=REPO,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    second_rc = None
    second_exit_s = None
    max_concurrent = len(_tasklist_krita())
    while time.time() - started < timeout:
        count = len(_tasklist_krita())
        max_concurrent = max(max_concurrent, count)
        if second.poll() is not None:
            second_rc = second.returncode
            second_exit_s = round(time.time() - (started + 6.0), 2)
            break
        time.sleep(0.5)
    first_alive_after = first.poll() is None
    kill_krita()
    if (second_rc == 0 and second_exit_s is not None and second_exit_s < 10
            and first_alive_after):
        conclusion = "single_instance_forwarded"
    elif second_rc is None and first_alive_after:
        conclusion = "multi_instance_possible"
    else:
        conclusion = "unexpected"
    return {
        "first_started": True,
        "first_alive_after_6s": first_alive,
        "second_exit_code": second_rc,
        "second_exit_after_s": second_exit_s,
        "transient_overlap_seen": max_concurrent > 1,
        "max_concurrent_krita": max_concurrent,
        "first_still_alive_after": first_alive_after,
        "conclusion": conclusion,
    }


def phase_isolation_probe(workdir):
    """探测 --resource-location 对 PyKrita 插件加载的影响（A/B）。"""
    diag_src = os.path.join(workdir, "diag_plugin.py")
    with open(diag_src, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "# -*- coding: utf-8 -*-\n"
            "import os\n"
            "LOG = os.environ.get('BC_AUTO_LOG', '')\n"
            "if LOG:\n"
            "    open(LOG, 'a', encoding='utf-8').write('[diag] import ok\\n')\n"
        )
    res_dir = os.path.join(workdir, "probe_profile", "resources")
    os.makedirs(os.path.join(res_dir, "pykrita"), exist_ok=True)
    for target in (os.path.join(REAL_PYKRITA, PLUGIN_NAME),
                   os.path.join(res_dir, "pykrita", PLUGIN_NAME)):
        os.makedirs(target, exist_ok=True)
        shutil.copyfile(diag_src, os.path.join(target, "__init__.py"))
    desktop_text = ("[Desktop Entry]\nType=Service\nServiceTypes=Krita/PythonPlugin\n"
                    "X-KDE-Library=%s\nX-Python-2-Compatible=false\nName=Diag\n" % PLUGIN_NAME)
    for target in (REAL_PYKRITA, os.path.join(res_dir, "pykrita")):
        with open(os.path.join(target, PLUGIN_NAME + ".desktop"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(desktop_text)
    report = {}
    try:
        for label, extra in (("with_resource_location", ["--resource-location", res_dir]),
                             ("without_resource_location", [])):
            kill_krita()
            log = os.path.join(workdir, "probe_profile", label + ".log")
            if os.path.isfile(log):
                os.remove(log)
            env = os.environ.copy()
            env["BC_AUTO_LOG"] = log
            proc = subprocess.Popen([KRITA_BIN, "--nosplash"] + extra, env=env, cwd=REPO,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.time() + 15
            while time.time() < deadline and not os.path.isfile(log):
                time.sleep(0.3)
            kill_krita()
            report[label] = {"plugin_loaded": os.path.isfile(log)}
    finally:
        for target in (os.path.join(REAL_PYKRITA, PLUGIN_NAME),
                       os.path.join(res_dir, "pykrita", PLUGIN_NAME)):
            shutil.rmtree(target, ignore_errors=True)
        for target in (REAL_PYKRITA, os.path.join(res_dir, "pykrita")):
            path = os.path.join(target, PLUGIN_NAME + ".desktop")
            if os.path.isfile(path):
                os.remove(path)
        kill_krita()
    return report


# ------------------------------------------------------------------ 比对与判定


def compare_phase(base_results, other_results):
    base_by_index = {item["index"]: item for item in base_results}
    pairs = []
    for entry in other_results:
        base = base_by_index.get(entry["index"])
        if not base:
            continue
        row = {"index": entry["index"], "name": entry["name"],
               "base_ok": base["ok"], "other_ok": entry["ok"]}
        if base["ok"] and entry["ok"]:
            row["compare"] = compare_rgba(
                os.path.join(REPO, base["out_png"]),
                os.path.join(REPO, entry["out_png"]))
        else:
            row["compare"] = {"error": "缺少产物（base_ok=%s other_ok=%s）"
                                       % (base["ok"], entry["ok"])}
        pairs.append(row)
    return pairs


def judge(samples, baseline, parallel, cross, probe_single, isolation_probe):
    parallel_by_index = {item["index"]: item for item in parallel.get("results", [])}
    comparisons = compare_phase(baseline, parallel.get("results", []))
    baseline_by_index = {item["index"]: item for item in baseline}

    missing = [sample["index"] for sample in samples
               if sample["index"] not in parallel_by_index or not parallel_by_index[sample["index"]]["ok"]]
    per_sample = []
    for sample in samples:
        entry = parallel_by_index.get(sample["index"])
        compare = next((row["compare"] for row in comparisons if row["index"] == sample["index"]), None)
        per_sample.append({
            "index": sample["index"],
            "name": sample["name"],
            "kpp": os.path.basename(sample["kpp"]),
            "kpp_sha256": sample["kpp_sha256"],
            "baseline_ok": baseline_by_index.get(sample["index"], {}).get("ok", False),
            "parallel_ok": bool(entry and entry.get("ok")),
            "parallel_elapsed_s": entry.get("elapsed_s") if entry else None,
            "parallel_failure_kind": entry.get("failure_kind") if entry else "no_result",
            "parallel_exit_code": entry.get("exit_code") if entry else None,
            "compare": compare,
        })
    diff_zero = [row for row in per_sample
                 if row["compare"] and "error" not in row["compare"]
                 and row["compare"]["rgb_diff_pixels"] == 0
                 and row["compare"]["alpha_diff_pixels"] == 0]
    crash_count = sum(1 for entry in parallel.get("results", [])
                      if entry.get("failure_kind") in ("timeout_hang", "crashed")
                      or (entry.get("exit_code") not in (0, None)))
    false_failure_count = sum(1 for entry in parallel.get("results", [])
                              if entry.get("failure_kind") == "single_instance_forwarded")
    cross_pairs = compare_phase(baseline, cross)
    cross_identical = sum(1 for row in cross_pairs
                          if "error" not in row["compare"]
                          and row["compare"]["rgb_diff_pixels"] == 0
                          and row["compare"]["alpha_diff_pixels"] == 0)
    pass_gate = (len(diff_zero) == len(samples) and crash_count == 0
                 and false_failure_count == 0)
    return {
        "samples": per_sample,
        "summary": {
            "total": len(samples),
            "parallel_ok": len(samples) - len(missing),
            "exact_match": len(diff_zero),
            "crash_count": crash_count,
            "false_failure_count": false_failure_count,
            "missing_indexes": missing,
            "cross_serial_identical": cross_identical,
            "cross_serial_total": len(cross_pairs),
            "verdict": "可行" if pass_gate else "不可行",
        },
        "cross_check": cross_pairs,
        "single_instance_probe": probe_single,
        "isolation_probe": isolation_probe,
        "parallel_meta": {key: parallel.get(key) for key in
                          ("elapsed_s", "returncodes", "max_concurrent_krita",
                           "concurrency_samples")},
    }


# ------------------------------------------------------------------ worker / main


def run_worker(index, workdir, timeout, samples_json):
    with open(samples_json, "r", encoding="utf-8") as fh:
        samples = json.load(fh)
    half = len(samples) // 2
    my_samples = samples[:half] if index == 0 else samples[half:]
    out_dir = os.path.join(workdir, "parallel", "worker%d" % index)
    results = []
    for sample in my_samples:
        entry = render_one(sample, os.path.join(out_dir, "%02d_%s.png" % (sample["index"], sample["name"])),
                           timeout, workdir)
        entry["worker"] = index
        entry["phase"] = "parallel"
        results.append(entry)
        print("[worker%d] %02d %-16s ok=%s alpha=%s %.2fs %s" % (
            index, sample["index"], sample["name"], entry["ok"],
            entry.get("alpha_nonzero"), entry["elapsed_s"],
            entry.get("failure_kind", "")), flush=True)
    path = os.path.join(workdir, "worker%d.json" % index)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"worker": index, "results": results}, fh, ensure_ascii=False, indent=1)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Krita 并行渲染门（任务 127）")
    parser.add_argument("--mode", choices=["serial", "parallel", "both"], default="both")
    parser.add_argument("--workdir", default=os.path.join(REPO, ".tmp", "127"))
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--parallel-timeout", type=float, default=DEFAULT_PARALLEL_TIMEOUT)
    parser.add_argument("--samples", nargs="*", default=None,
                        help="覆盖默认 10 张样本（test ABR 文件名主干）")
    parser.add_argument("--skip-convert", action="store_true")
    parser.add_argument("--worker", type=int, choices=[0, 1], default=None)
    parser.add_argument("--samples-json", default=None)
    parser.add_argument("--no-probe", action="store_true")
    args = parser.parse_args()

    workdir = os.path.abspath(args.workdir)
    os.makedirs(workdir, exist_ok=True)

    if args.worker is not None:
        if not args.samples_json:
            parser.error("--worker 需要 --samples-json")
        return run_worker(args.worker, workdir, args.parallel_timeout, args.samples_json)

    if not os.path.isfile(KRITA_BIN):
        print("Krita 不存在：%s" % KRITA_BIN)
        return 2
    if not os.path.isfile(PLUGIN_SRC):
        print("插件源码不存在：%s" % PLUGIN_SRC)
        return 2
    existing = _tasklist_krita()
    if existing:
        print("已有 Krita 进程在运行，先退出后方可跑门：%r" % existing)
        return 2

    stems = args.samples if args.samples else DEFAULT_SAMPLES
    samples = ensure_samples(workdir, stems, skip_convert=args.skip_convert)
    print("样本：%d 张（全部 auto_brush）" % len(samples), flush=True)

    result = {
        "task": "127 并行渲染门",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "krita_bin": KRITA_BIN,
        "krita_version": "",
        "mode": args.mode,
        "samples": samples,
        "baseline": [],
        "cross": [],
        "parallel": {},
        "probe_single_instance": None,
        "probe_isolation": None,
    }
    try:
        ver = subprocess.run([KRITA_BIN, "--version"], capture_output=True, timeout=30)
        result["krita_version"] = (ver.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:
        pass

    if not args.no_probe:
        print("隔离探针：单实例 / --resource-location ...", flush=True)
        result["probe_single_instance"] = phase_single_instance_probe()
        print("  single-instance:", result["probe_single_instance"]["conclusion"], flush=True)
        time.sleep(2.0)
        kill_krita()

    with SharedProfile(workdir, [sample["kpp"] for sample in samples]):
        if not args.no_probe:
            result["probe_isolation"] = phase_isolation_probe(workdir)
            print("  isolation:", result["probe_isolation"], flush=True)
            # 隔离探针会临时替换/删除真实插件，这里恢复正式插件
            _install_plugin()

        if args.mode in ("serial", "both"):
            print("阶段 serial（串行基线）...", flush=True)
            result["baseline"] = phase_serial(samples, workdir, args.timeout, "serial")
            with open(os.path.join(workdir, "baseline.json"), "w", encoding="utf-8") as fh:
                json.dump(result["baseline"], fh, ensure_ascii=False, indent=1)

        if args.mode == "parallel" and not result["baseline"]:
            with open(os.path.join(workdir, "baseline.json"), "r", encoding="utf-8") as fh:
                result["baseline"] = json.load(fh)

        if args.mode in ("parallel", "both"):
            print("阶段 parallel（两个独立 Krita 进程并发）...", flush=True)
            result["parallel"] = phase_parallel_attempt(
                samples, workdir, args.parallel_timeout)
            with open(os.path.join(workdir, "parallel.json"), "w", encoding="utf-8") as fh:
                json.dump(result["parallel"], fh, ensure_ascii=False, indent=1)

        if args.mode in ("serial", "both"):
            print("阶段 cross（串行交叉复算）...", flush=True)
            result["cross"] = phase_serial(samples, workdir, args.timeout, "cross")
            with open(os.path.join(workdir, "cross.json"), "w", encoding="utf-8") as fh:
                json.dump(result["cross"], fh, ensure_ascii=False, indent=1)

    result["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    result["judgement"] = judge(samples, result["baseline"],
                                result["parallel"], result["cross"],
                                result["probe_single_instance"],
                                result["probe_isolation"])
    with open(os.path.join(workdir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)

    summary = result["judgement"]["summary"]
    print("\n=== 门结论：%s ===" % summary["verdict"])
    print("逐像素一致：%d/%d；崩溃：%d；假失败：%d；交叉复算串行一致：%d/%d" % (
        summary["exact_match"], summary["total"], summary["crash_count"],
        summary["false_failure_count"], summary["cross_serial_identical"],
        summary["cross_serial_total"]))
    print("结果 JSON：%s" % os.path.join(workdir, "result.json"))
    return 0 if summary["verdict"] == "可行" else 1


if __name__ == "__main__":
    sys.exit(main())
