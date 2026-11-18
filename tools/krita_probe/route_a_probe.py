# -*- coding: utf-8 -*-
"""路线 A 探针：在真实 Krita GUI 会话里自动落笔（任务 04 追加实验）。

流程：
1) 备份 kritarc（%LOCALAPPDATA%/kritarc）→ 注入 enable_bc_auto_render=true；
2) 把 PyKrita 插件复制到 %APPDATA%/krita/pykrita/bc_auto_render/；
3) subprocess 启动 krita.com --nosplash（带超时），等待进程退出；
4) finally：还原 kritarc、删除插件目录（不留持久改动）。

用法（conda 侧）：
    python tools/krita_probe/route_a_probe.py --out /abs/out.png [--preset 大怪兽] [--size 200x200]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
# 环境变量优先（BRUSHCONVERTER_KRITA），本机默认值兜底（不含身份信息）
KRITA_BIN = os.environ.get("BRUSHCONVERTER_KRITA", r"D:\program\Krita (x64)\bin\krita.com")
PLUGIN_SRC = os.path.join(HERE, "krita_auto_plugin", "__init__.py")
PLUGIN_NAME = "bc_auto_render"
LOG_PATH = os.path.join(REPO_ROOT, "tools", "krita_probe", "route_a_log.txt")


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--preset", default="")
    parser.add_argument("--size", default="200x200")
    parser.add_argument("--stroke", default="120")
    parser.add_argument("--timeout", type=float, default=180.0)
    ns = parser.parse_args()

    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    kritarc = os.path.join(localappdata, "kritarc")
    pykrita_dir = os.path.join(appdata, "krita", "pykrita")
    plugin_dir = os.path.join(pykrita_dir, PLUGIN_NAME)
    desktop_path = os.path.join(pykrita_dir, PLUGIN_NAME + ".desktop")

    backup_path = kritarc + ".bc_backup"
    original_kritarc = read_text(kritarc)
    log_handle = None
    try:
        # 1) 备份 kritarc
        write_text(backup_path, original_kritarc)
        # 2) 注入启用键（放在 [python] 段头之后）
        injected = original_kritarc.replace("[python]\n", "[python]\nenable_%s=true\n" % PLUGIN_NAME, 1)
        assert "enable_%s=true" % PLUGIN_NAME in injected, "注入失败：kritarc 里没有 [python] 段"
        # 2b) 会话启动行为改成「新会话」：实测 sessionOnStartup=0 会先弹「会话管理器」对话框，
        #     主窗口一直不创建（插件轮询 30 秒 windows=0）。临时改成 2，退出后还原。
        import re
        injected = re.sub(r"(?m)^sessionOnStartup=\d+$", "sessionOnStartup=2", injected)
        assert re.search(r"(?m)^sessionOnStartup=2$", injected), "注入失败：kritarc 里没有 sessionOnStartup"
        write_text(kritarc, injected)

        # 3) 安装插件
        os.makedirs(plugin_dir, exist_ok=True)
        shutil.copyfile(PLUGIN_SRC, os.path.join(plugin_dir, "__init__.py"))
        write_text(
            desktop_path,
            "[Desktop Entry]\nType=Service\nServiceTypes=Krita/PythonPlugin\n"
            "X-KDE-Library=%s\nX-Python-2-Compatible=false\nName=BC Auto Render\n"
            % PLUGIN_NAME,
        )

        # 4) 清空日志并启动 Krita
        if os.path.isfile(LOG_PATH):
            os.remove(LOG_PATH)
        env = os.environ.copy()
        env["BC_AUTO_LOG"] = LOG_PATH
        env["BC_AUTO_OUT"] = os.path.abspath(ns.out)
        env["BC_AUTO_PRESET"] = ns.preset
        env["BC_AUTO_SIZE"] = ns.size
        env["BC_AUTO_STROKE"] = ns.stroke
        env["BC_AUTO_PRESULT"] = os.path.abspath(ns.out) + ".result.json"
        env["BC_AUTO_PRESET_DUMP"] = os.path.abspath(os.path.join(REPO_ROOT, "tools", "krita_probe", "presets_dump.json"))
        started = time.time()
        try:
            proc = subprocess.run(
                [KRITA_BIN, "--nosplash"],
                env=env, timeout=ns.timeout, capture_output=True,
            )
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            proc = exc
        elapsed = time.time() - started

        result = {
            "ok": False,
            "timed_out": timed_out,
            "elapsed_s": round(elapsed, 2),
            "out_exists": os.path.isfile(ns.out),
            "out_bytes": os.path.getsize(ns.out) if os.path.isfile(ns.out) else 0,
        }
        if not timed_out:
            result["exit_code"] = proc.returncode
            result["stdout_tail"] = (proc.stdout or b"").decode("utf-8", "replace")[-3000:]
            result["stderr_tail"] = (proc.stderr or b"").decode("utf-8", "replace")[-3000:]
        result["log"] = read_text(LOG_PATH) if os.path.isfile(LOG_PATH) else "<无日志：插件没有被加载>"
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    finally:
        # 5) 还原 kritarc、删除插件
        try:
            write_text(kritarc, original_kritarc)
            os.remove(backup_path)
        except Exception as exc:
            sys.stderr.write("还原 kritarc 失败：%r\n" % (exc,))
        try:
            shutil.rmtree(plugin_dir, ignore_errors=True)
            if os.path.isfile(desktop_path):
                os.remove(desktop_path)
        except Exception as exc:
            sys.stderr.write("清理插件失败：%r\n" % (exc,))


if __name__ == "__main__":
    sys.exit(main())
