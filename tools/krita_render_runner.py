# -*- coding: utf-8 -*-
"""Krita 单笔渲染的 conda 侧外壳（任务 06）。

【两条路线】
1) GUI 路线（默认，**真实渲染**）：临时安装一个 PyKrita 启动插件到
   `%APPDATA%/krita/pykrita/bc_auto_render/`，并用临时修改的 kritarc 让 Krita 启动时加载它；
   真实 Krita 进程启动 → 插件建透明画布 → 落笔 → 导出 PNG → 关闭文档退出。
   会话结束**必定**还原 kritarc、还原 resourcecache.sqlite、删除插件与临时 .kpp（finally 保证）。
2) kritarunner 路线（保留作证据/回退）：`kritarunner -s krita_render`，无主窗口 →
   `Window.addView` 触发 0xC0000005（任务 04 门结论），只用于复现失败，不作为默认。

判定成败：只看结果 JSON（GUI 路线由插件写）或 `KRITA_RENDER_RESULT` 标记（kritarunner 路线）
+ PNG 存在 + Alpha 非零像素数 > 0；**不看进程退出码**（kritarunner 实测不透传，GUI 进程可能被强杀）。

命令行：
    python tools/krita_render_runner.py --preset "converted/kpp/003_大怪兽-圆头虚_用数位板.kpp" --out /tmp/one.png
    python tools/krita_render_runner.py --route headless --preset ... --out ...   # 复现 kritarunner 失败
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

# ---------------------------------------------------------------- 基础常量

KRITARUNNER = os.environ.get(
    "BRUSHCONVERTER_KRITARUNNER",
    r"D:\program\Krita (x64)\bin\kritarunner.exe",
)
KRITA_BIN = os.environ.get(
    "BRUSHCONVERTER_KRITA",
    r"D:\program\Krita (x64)\bin\krita.com",
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_MODULE = "krita_render"
RESULT_PREFIX = "KRITA_RENDER_RESULT "
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_GUI_TIMEOUT_S = 75.0  # 实测单次约 11–14 秒；75 秒足够，且能尽快暴露卡死
STDERR_TAIL_LINES = 20

PLUGIN_SRC = os.path.join(SCRIPT_DIR, "krita_render_gui_plugin.py")
PLUGIN_NAME = "bc_auto_render"

APPDATA = os.environ.get("APPDATA", "")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
PYKRITA_DIR = os.path.join(APPDATA, "krita", "pykrita")
KRITARC_PATH = os.path.join(LOCALAPPDATA, "kritarc")
RESOURCE_CACHE = os.path.join(APPDATA, "krita", "resourcecache.sqlite")
PAINTOPPRESETS_DIR = os.path.join(APPDATA, "krita", "paintoppresets")


# ---------------------------------------------------------------- 通用工具


def kritarunner_available():
    return os.path.isfile(KRITARUNNER)


def gui_available():
    return os.path.isfile(KRITA_BIN) and os.path.isfile(PLUGIN_SRC)


def _tail(text, lines=STDERR_TAIL_LINES):
    if not text:
        return ""
    rows = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rows = [row for row in rows if row.strip()]
    return "\n".join(rows[-lines:])


def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _alpha_nonzero(png_path):
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(png_path) as im:
            rgba = im.convert("RGBA")
            # Pillow 14 起 Image.getdata() 移除；tobytes() 逐字节等价。
            alpha = rgba.getchannel("A").tobytes()
            return len(alpha) - alpha.count(0)
    except Exception:
        return None


def _pids_of(image_name):
    """列出指定进程名的 PID（用于超时后只杀我们新起的 Krita 进程）。"""
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq %s" % image_name, "/FO", "CSV", "/NH"],
            capture_output=True, timeout=20,
        )
    except Exception:
        return set()
    pids = set()
    for line in (proc.stdout or b"").decode("utf-8", "replace").splitlines():
        parts = [part.strip('"') for part in line.split('","')]
        if parts and parts[0].lower() == image_name.lower() and len(parts) > 1:
            try:
                pids.add(int(parts[1]))
            except ValueError:
                continue
    return pids


def _kill_pids(pids):
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=20)
        except Exception:
            continue


# ---------------------------------------------------------------- kritarunner 路线（证据/回退）


def _extract_marker(stdout):
    result = None
    for line in stdout.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if line.startswith(RESULT_PREFIX):
            try:
                result = json.loads(line[len(RESULT_PREFIX):])
            except json.JSONDecodeError:
                result = None
    return result


def _run(cmd, cwd, env, timeout):
    return subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout, capture_output=True)


def invoke(args_list, timeout=DEFAULT_TIMEOUT_S):
    """调 kritarunner 执行 krita_render 模块（无头路线，预期失败）。"""
    started = time.time()
    base = {
        "ok": False, "route": "kritarunner", "exit_code": None, "stderr": "",
        "stdout_tail": "", "out_path": None, "elapsed_s": 0.0, "reason": "",
        "timeout": False, "marker": None,
    }
    if not kritarunner_available():
        base["reason"] = "kritarunner 不存在：%s" % KRITARUNNER
        base["elapsed_s"] = round(time.time() - started, 3)
        return base

    cmd = [KRITARUNNER, "-s", SCRIPT_MODULE]
    env = os.environ.copy()
    # 183A：去掉从 pytest 进程继承的 offscreen 平台，避免无头/真实 Krita 被串扰。
    env.pop("QT_QPA_PLATFORM", None)
    env["KRITA_RENDER_ARGS"] = json.dumps(list(args_list), ensure_ascii=False)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = _run(cmd, SCRIPT_DIR, env, timeout)
    except subprocess.TimeoutExpired as exc:
        base["timeout"] = True
        base["elapsed_s"] = round(time.time() - started, 3)
        base["reason"] = "timeout：kritarunner 超过 %ss 未返回；stderr 尾部：%s" % (
            timeout, _tail((exc.stderr or b"").decode("utf-8", "replace")))
        return base

    stdout = (proc.stdout or b"").decode("utf-8", "replace")
    stderr = (proc.stderr or b"").decode("utf-8", "replace")
    base["exit_code"] = proc.returncode
    base["stderr"] = _tail(stderr)
    base["stdout_tail"] = _tail(stdout)
    base["elapsed_s"] = round(time.time() - started, 3)

    marker = _extract_marker(stdout)
    base["marker"] = marker
    if marker is not None:
        base["out_path"] = marker.get("out_path")
    if marker is None:
        crash_hint = ""
        if proc.returncode not in (0, None):
            crash_hint = "；进程退出码=%s（0xC0000005=访问违例）" % proc.returncode
        base["reason"] = "脚本未输出结果标记（kritarunner 未执行脚本或中途崩溃）" + crash_hint
        return base
    if not marker.get("ok"):
        base["reason"] = "脚本返回 ok=false：%s" % marker.get("reason", "<未给出原因>")
        return base
    out_path = marker.get("out_path")
    if not out_path or not os.path.isfile(out_path):
        base["reason"] = "脚本声称成功但 PNG 不存在：%r" % (out_path,)
        return base
    alpha = marker.get("alpha_nonzero")
    if alpha is None:
        alpha = _alpha_nonzero(out_path)
    base["alpha_nonzero"] = alpha
    if not alpha:
        base["reason"] = "PNG 存在但 Alpha 非零像素数=%r，视为落笔失败" % (alpha,)
        return base
    base["ok"] = True
    base["reason"] = "ok"
    return base


def render_one_headless(preset, out, size=(200, 200), stroke=120, pressure=1.0, timeout=DEFAULT_TIMEOUT_S):
    """kritarunner 路线（预期失败，保留作门结论证据）。"""
    args = [
        "--preset", os.path.abspath(str(preset)),
        "--size", "%dx%d" % (int(size[0]), int(size[1])),
        "--stroke", str(int(stroke)),
        "--pressure", str(float(pressure)),
        "--out", os.path.abspath(str(out)),
    ]
    result = invoke(args, timeout=timeout)
    result["preset"] = str(preset)
    result["width"] = int(size[0])
    result["height"] = int(size[1])
    result["pressure"] = float(pressure)
    return result


# ---------------------------------------------------------------- GUI 路线（真实渲染）


def _install_plugin():
    """临时安装 PyKrita 插件，返回 (plugin_dir, desktop_path)。"""
    plugin_dir = os.path.join(PYKRITA_DIR, PLUGIN_NAME)
    os.makedirs(plugin_dir, exist_ok=True)
    shutil.copyfile(PLUGIN_SRC, os.path.join(plugin_dir, "__init__.py"))
    desktop_path = os.path.join(PYKRITA_DIR, PLUGIN_NAME + ".desktop")
    _write_text(
        desktop_path,
        "[Desktop Entry]\nType=Service\nServiceTypes=Krita/PythonPlugin\n"
        "X-KDE-Library=%s\nX-Python-2-Compatible=false\nName=BC Auto Render\n" % PLUGIN_NAME,
    )
    return plugin_dir, desktop_path


def _patch_kritarc():
    """临时启用插件并把启动会话改成「新会话」（避免会话管理器对话框阻塞主窗口）。"""
    original = _read_text(KRITARC_PATH)
    patched = original
    if "[python]" in patched:
        patched = patched.replace("[python]\n", "[python]\nenable_%s=true\n" % PLUGIN_NAME, 1)
    else:
        patched += "\n[python]\nenable_%s=true\n" % PLUGIN_NAME
    import re
    patched = re.sub(r"(?m)^sessionOnStartup=\d+$", "sessionOnStartup=2", patched)
    _write_text(KRITARC_PATH, patched)
    return original


# 进程级「会话开始时就已存在的 krita.exe」基线：只有基线之外的才可能是我们起的。
# 注意不能用「每次调用前快照」——上一轮残留会被误当成用户进程，导致新实例转发到残留实例后秒退。
_BASELINE_KRITA_PIDS = None


def _baseline_pids():
    global _BASELINE_KRITA_PIDS
    if _BASELINE_KRITA_PIDS is None:
        _BASELINE_KRITA_PIDS = _pids_of("krita.exe")
    return _BASELINE_KRITA_PIDS


def _wait_extra_krita_gone(timeout=25.0):
    """等待并（超时后）清理「基线之外」的 krita.exe。返回 (是否干净, 被清理的 PID 列表)。"""
    baseline = _baseline_pids()
    started = time.time()
    while time.time() - started < timeout:
        extra = _pids_of("krita.exe") - baseline
        if not extra:
            return True, []
        time.sleep(0.4)
    extra = _pids_of("krita.exe") - baseline
    if extra:
        _kill_pids(extra)
        time.sleep(1.0)
    return False, sorted(extra)


def render_one_gui(preset, out, size=(200, 200), stroke=120, pressure=1.0, timeout=DEFAULT_GUI_TIMEOUT_S,
                   pressure2=None, strokes=None):
    """GUI 路线入口：失败时最多重试一次（首次失败常见原因是上一轮 Krita 未退干净）。"""
    result = _render_one_gui_once(preset, out, size=size, stroke=stroke,
                                  pressure=pressure, timeout=timeout, pressure2=pressure2,
                                  strokes=strokes)
    if result["ok"]:
        return result
    reason_text = result.get("reason") or ""
    if ("未写结果文件" in reason_text) or ("timeout" in reason_text):
        time.sleep(2.0)
        retry = _render_one_gui_once(preset, out, size=size, stroke=stroke,
                                     pressure=pressure, timeout=timeout, pressure2=pressure2,
                                     strokes=strokes)
        retry["retried"] = True
        if not retry["ok"]:
            retry["first_reason"] = result["reason"]
        return retry
    return result


def _render_one_gui_once(preset, out, size=(200, 200), stroke=120, pressure=1.0, timeout=DEFAULT_GUI_TIMEOUT_S,
                         pressure2=None, strokes=None):
    """GUI 路线单次执行：真实 Krita 进程 + 启动插件完成「落笔 → 导出 PNG」。

    参数 preset 是 .kpp 文件路径；函数会把它临时装进 Krita 资源目录
    （`%APPDATA%/krita/paintoppresets/`），按文件名匹配资源库预设，结束后删除并还原 resourcecache。
    """
    started = time.time()
    result = {
        "ok": False, "route": "gui", "exit_code": None, "stderr": "",
        "stdout_tail": "", "out_path": None, "elapsed_s": 0.0, "reason": "",
        "timeout": False, "marker": None,
        "preset": str(preset), "width": int(size[0]), "height": int(size[1]),
        "pressure": float(pressure),
    }
    if not gui_available():
        result["reason"] = "GUI 路线不可用（缺少 krita.com 或 %s）" % PLUGIN_SRC
        result["elapsed_s"] = round(time.time() - started, 3)
        return result

    preset_path = os.path.abspath(str(preset))
    out_path = os.path.abspath(str(out))
    if not os.path.isfile(preset_path):
        result["reason"] = "预设文件不存在：%s" % preset_path
        result["elapsed_s"] = round(time.time() - started, 3)
        return result

    # 上一轮 Krita 若还在退出中，等它彻底退干净（超时则清理残留）再启动，
    # 否则新实例可能把请求转发给残留实例后立即退出，插件根本不会加载。
    waited_clean, killed = _wait_extra_krita_gone(timeout=25.0)
    tmpdir = tempfile.mkdtemp(prefix="bc_krita_gui_")
    backups = {}
    plugin_dir = None
    desktop_path = None
    installed_kpp = None
    before_pids = _pids_of("krita.exe")
    if killed:
        log_killed = "清理残留 krita.exe PID=%r" % (killed,)
    else:
        log_killed = ""
    try:
        # 1) 备份 kritarc / resourcecache.sqlite
        for path in (KRITARC_PATH, RESOURCE_CACHE):
            if os.path.isfile(path):
                dst = os.path.join(tmpdir, os.path.basename(path))
                shutil.copy2(path, dst)
                backups[path] = dst
        # 2) 临时启用插件 + 新会话启动
        if os.path.isfile(KRITARC_PATH):
            _patch_kritarc()
        # 3) 临时装入 .kpp
        os.makedirs(PAINTOPPRESETS_DIR, exist_ok=True)
        installed_kpp = os.path.join(PAINTOPPRESETS_DIR, os.path.basename(preset_path))
        shutil.copyfile(preset_path, installed_kpp)
        plugin_dir, desktop_path = _install_plugin()

        # 4) 启动 Krita
        result_json = out_path + ".result.json"
        if os.path.isfile(result_json):
            os.remove(result_json)
        if os.path.isfile(out_path):
            os.remove(out_path)
        log_path = os.path.join(tmpdir, "gui_log.txt")
        env = os.environ.copy()
        # 183A：Krita 子进程必须使用真实平台；只移除子进程 env，pytest 自身仍可 offscreen。
        env.pop("QT_QPA_PLATFORM", None)
        env["BC_AUTO_LOG"] = log_path
        env["BC_AUTO_OUT"] = out_path
        env["BC_AUTO_PRESULT"] = result_json
        env["BC_AUTO_PRESET_FILE"] = os.path.basename(preset_path)
        env["BC_AUTO_SIZE"] = "%dx%d" % (int(size[0]), int(size[1]))
        env["BC_AUTO_STROKE"] = str(int(stroke))
        env["BC_AUTO_PRESSURE"] = str(float(pressure))
        if pressure2 is not None:
            env["BC_AUTO_PRESSURE2"] = str(float(pressure2))
        if strokes is not None:
            import json as _json
            env["BC_AUTO_STROKES"] = _json.dumps(list(strokes), ensure_ascii=False)
        timed_out = False
        try:
            proc = subprocess.run([KRITA_BIN, "--nosplash"], env=env, timeout=timeout, capture_output=True)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            proc = exc
            new_pids = _pids_of("krita.exe") - before_pids
            _kill_pids(new_pids)

        result["timeout"] = timed_out
        result["elapsed_s"] = round(time.time() - started, 3)
        if not timed_out:
            result["exit_code"] = proc.returncode
            result["stdout_tail"] = _tail((proc.stdout or b"").decode("utf-8", "replace"))
            result["stderr"] = _tail((proc.stderr or b"").decode("utf-8", "replace"))
        result["log_tail"] = _tail(_read_text(log_path), 40) if os.path.isfile(log_path) else ""

        # 5) 读插件写的结果
        if os.path.isfile(result_json):
            with open(result_json, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            result["marker"] = payload
            result["out_path"] = payload.get("out_path")
            result["alpha_nonzero"] = payload.get("alpha_nonzero")
            result["preset"] = payload.get("preset", result["preset"])
            if payload.get("ok") and payload.get("out_path") and os.path.isfile(payload["out_path"]):
                alpha = payload.get("alpha_nonzero")
                if alpha is None:
                    alpha = _alpha_nonzero(payload["out_path"])
                result["alpha_nonzero"] = alpha
                if alpha:
                    result["ok"] = True
                    result["reason"] = "ok"
                else:
                    result["reason"] = "PNG 存在但 Alpha 非零像素=0，视为落笔失败"
            else:
                result["reason"] = "插件返回 ok=false：%s" % payload.get("reason", "<无原因>")
        elif timed_out:
            result["reason"] = "timeout：Krita GUI 进程超过 %ss 未退出（已强杀本次新起的 krita.exe）" % timeout
        else:
            result["reason"] = "插件未写结果文件（可能未加载/启动失败）；日志尾部：%s" % (result.get("log_tail") or "<空>")
        return result
    finally:
        # 6) 还原与清理（无论成败都执行）
        for path, backup in backups.items():
            try:
                shutil.copy2(backup, path)
            except Exception:
                pass
        if installed_kpp and os.path.isfile(installed_kpp):
            try:
                os.remove(installed_kpp)
            except Exception:
                pass
        if desktop_path and os.path.isfile(desktop_path):
            try:
                os.remove(desktop_path)
            except Exception:
                pass
        if plugin_dir:
            shutil.rmtree(plugin_dir, ignore_errors=True)
        leftover = _pids_of("krita.exe") - before_pids
        if leftover:
            _kill_pids(leftover)
        shutil.rmtree(tmpdir, ignore_errors=True)


def render_one(preset, out, size=(200, 200), stroke=120, pressure=1.0,
               timeout=None, route="gui", pressure2=None, strokes=None):
    """渲染单支笔（默认走 GUI 真实渲染路线）。pressure2=笔划终点压力（None=同 pressure）。"""
    if route == "headless":
        return render_one_headless(preset, out, size=size, stroke=stroke,
                                   pressure=pressure, timeout=timeout or DEFAULT_TIMEOUT_S)
    if route == "auto":
        result = render_one_gui(preset, out, size=size, stroke=stroke,
                                pressure=pressure, timeout=timeout or DEFAULT_GUI_TIMEOUT_S,
                                pressure2=pressure2, strokes=strokes)
        if result["ok"]:
            return result
        fallback = render_one_headless(preset, out, size=size, stroke=stroke,
                                       pressure=pressure, timeout=DEFAULT_TIMEOUT_S)
        fallback["gui_reason"] = result["reason"]
        return fallback
    return render_one_gui(preset, out, size=size, stroke=stroke,
                          pressure=pressure, timeout=timeout or DEFAULT_GUI_TIMEOUT_S,
                          pressure2=pressure2, strokes=strokes)


def main():
    parser = argparse.ArgumentParser(description="Krita 单笔渲染外壳（默认 GUI 真实渲染；headless 为证据路线）")
    parser.add_argument("--preset", required=True, help=".kpp 预设路径")
    parser.add_argument("--out", required=True, help="输出 PNG 路径")
    parser.add_argument("--size", default="200x200", help="画布尺寸，形如 200x200")
    parser.add_argument("--stroke", type=int, default=120, help="笔划长度（像素）")
    parser.add_argument("--pressure", type=float, default=1.0, help="压感值 0–1（起点）")
    parser.add_argument("--pressure2", type=float, default=None, help="终点压感 0–1（缺省=--pressure）")
    parser.add_argument("--timeout", type=float, default=None, help="超时秒数（默认 GUI 180 / headless 120）")
    parser.add_argument("--route", choices=["gui", "headless", "auto"], default="gui")
    ns = parser.parse_args()
    parts = ns.size.lower().split("x")
    if len(parts) != 2:
        parser.error("--size 必须形如 200x200")
    result = render_one(
        ns.preset, ns.out, size=(int(parts[0]), int(parts[1])),
        stroke=ns.stroke, pressure=ns.pressure, timeout=ns.timeout, route=ns.route,
        pressure2=ns.pressure2,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
