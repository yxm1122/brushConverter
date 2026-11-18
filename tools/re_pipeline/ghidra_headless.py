# -*- coding: utf-8 -*-
r"""
ghidra_headless.py —— 封装 Ghidra 的 analyzeHeadless，用于任务 11 的「建库 + 脚本取证」。

本机实况（2026-11-18 实测，与任务书写的 11.4.1 不同，以实测为准）：
  * Ghidra = <LOCAL_APP_DIR>\ghidra_12.2_PUBLIC（12.2 PUBLIC，需 Java 25+）
  * Java   = <LOCAL_APP_DIR>\jdk-25.0.5+8（OpenJDK 25.0.5）
  * 目标   = <LOCAL_APP_DIR>\PS2025\Adobe Photoshop 2025\Photoshop.exe（208,456,176 字节，.text 136 MB）
  （本机实测路径已被占位化，复现请改成你自己的安装路径）

设计要点：
  1. 工程目录默认放在**仓库外**（C:\Users\<user>\AppData\Local\Temp\brushconverter-team\ghidra-proj），
     避免几 GB 的 Ghidra 工程进 git（任务书硬性要求 2）；
  2. 用环境变量 MAXMEM 限制 JVM 堆（Ghidra 的 support/launch.bat 会读取它），
     防在本机空闲内存只有 5～6 GB 时把整机拖死；
  3. stdout/stderr 全部 tee 进日志文件，并在结束时打印耗时、工程路径、日志路径；
  4. 支持超时（默认 3600 秒）与 -noanalysis（只建库不分析）、-analysisTimeoutPerFile（分析超时兜底）。

用法：
  # 1) 只建库（快速拿到符号数量、验证工具链）
  python tools/re_pipeline/ghidra_headless.py --exe "…/Photoshop.exe" --noanalysis --log docs/研究报告/附件/ghidra_import.log

  # 2) 建库 + 分析 + 跑取证脚本
  python tools/re_pipeline/ghidra_headless.py --exe "…/Photoshop.exe" \
      --script GhidraFindCandidates.java \
      --script-args "C:/tmp/xref.tsv,0x148590800,0x148590818" \
      --analysis-timeout 1200 --maxmem 5g \
      --log docs/研究报告/附件/ghidra_analysis.log
"""
from __future__ import annotations

import argparse
import datetime
import os
import shlex
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 环境变量优先（GHIDRA_HOME / JAVA_HOME），本机默认值兜底：不含身份信息，脚本需要它才能在本机直接跑
GHIDRA_HOME_DEFAULT = os.environ.get("GHIDRA_HOME", r"D:\program\ghidra_12.2_PUBLIC")
JAVA_HOME_DEFAULT = os.environ.get("JAVA_HOME", r"D:\program\jdk-25.0.5+8")
PROJ_DIR_DEFAULT = os.path.join(
    os.environ.get("TEMP") or tempfile.gettempdir(),
    "brushconverter-team",
    "ghidra-proj",
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def build_cmd(args) -> list[str]:
    headless = os.path.join(args.ghidra_home, "support", "analyzeHeadless.bat")
    if not os.path.isfile(headless):
        raise SystemExit("找不到 analyzeHeadless.bat：%s" % headless)
    cmd = [headless, args.project_dir, args.project_name]
    cmd += ["-import", args.exe]
    if args.noanalysis:
        cmd.append("-noanalysis")
    if args.analysis_timeout:
        cmd += ["-analysisTimeoutPerFile", str(args.analysis_timeout)]
    if args.max_cpu:
        cmd += ["-max-cpu", str(args.max_cpu)]
    if args.script:
        cmd += ["-scriptPath", SCRIPT_DIR]
        cmd += ["-postScript", args.script]
        if args.script_args:
            # 逗号分隔 -> 多个独立参数（Ghidra 脚本按位置取 getScriptArgs()）
            cmd += [x for x in args.script_args.split(",") if x.strip()]
    if args.delete_project:
        cmd.append("-deleteProject")
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="Ghidra analyzeHeadless 封装（任务 11）")
    ap.add_argument("--exe", required=True, help="要导入的 PE 文件（只读）")
    ap.add_argument("--ghidra-home", default=GHIDRA_HOME_DEFAULT)
    ap.add_argument("--java-home", default=JAVA_HOME_DEFAULT)
    ap.add_argument("--project-dir", default=PROJ_DIR_DEFAULT, help="工程目录（默认仓库外）")
    ap.add_argument("--project-name", default="ps_re")
    ap.add_argument("--script", default="", help="postScript 脚本名（放在本脚本同目录）")
    ap.add_argument("--script-args", default="", help="逗号分隔的脚本参数")
    ap.add_argument("--analysis-timeout", type=int, default=0, help="单文件分析超时秒数（0=不设）")
    ap.add_argument("--max-cpu", type=int, default=0, help="最多使用多少个 CPU 核")
    ap.add_argument("--maxmem", default="5g", help="JVM 最大堆（Ghidra 读环境变量 MAXMEM）")
    ap.add_argument("--noanalysis", action="store_true", help="只导入不分析")
    ap.add_argument("--delete-project", action="store_true")
    ap.add_argument("--timeout", type=int, default=3600, help="整体超时秒数")
    ap.add_argument("--log", default="", help="日志落盘路径（中文目录可直接给）")
    args = ap.parse_args()

    if not os.path.isfile(args.exe):
        raise SystemExit("目标文件不存在：%s" % args.exe)
    os.makedirs(args.project_dir, exist_ok=True)

    cmd = build_cmd(args)
    env = dict(os.environ)
    env["JAVA_HOME"] = args.java_home
    env["MAXMEM"] = args.maxmem
    env["PATH"] = os.path.join(args.java_home, "bin") + os.pathsep + env.get("PATH", "")

    print("[ghidra_headless] 开始：", datetime.datetime.now().isoformat(timespec="seconds"))
    print("[ghidra_headless] 目标：", args.exe, os.path.getsize(args.exe), "字节")
    print("[ghidra_headless] 工程：", args.project_dir, "/", args.project_name)
    print("[ghidra_headless] MAXMEM =", args.maxmem, " JAVA_HOME =", args.java_home)
    print("[ghidra_headless] 命令：", " ".join(shlex.quote(c) for c in cmd), flush=True)

    log_f = None
    if args.log:
        os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
        log_f = open(args.log, "w", encoding="utf-8", errors="replace")
        log_f.write("命令：" + " ".join(shlex.quote(c) for c in cmd) + "\n\n")

    t0 = time.time()
    timed_out = False
    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if log_f:
                log_f.write(line)
                log_f.flush()
            print(line.rstrip())
            if time.time() - t0 > args.timeout:
                timed_out = True
                proc.kill()
                break
        rc = proc.wait()
    except KeyboardInterrupt:
        raise SystemExit("人工中断")
    finally:
        elapsed = time.time() - t0
        print("\n[ghidra_headless] 退出码=%s 耗时=%.1f 秒（%.1f 分钟）%s"
              % (rc if not timed_out else "TIMEOUT", elapsed, elapsed / 60.0,
                 "（已超时强杀）" if timed_out else ""))
        if log_f:
            log_f.write("\n# 退出码=%s 耗时=%.1f 秒\n" % (rc, elapsed))
            log_f.close()
            print("[ghidra_headless] 日志：", args.log)
    return 0 if (rc == 0 and not timed_out) else 1


if __name__ == "__main__":
    raise SystemExit(main())
