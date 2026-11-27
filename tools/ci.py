#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""可重复的最小 CI 入口（本地与 GitHub Actions 共用；任务 task-2）。

用法（仓库根，Python 3.12+）：
    python -B tools/ci.py                       # 默认 --phase all：先结构检查、再回归
    python -B tools/ci.py --phase checks        # 只跑只读结构检查（纯标准库）
    python -B tools/ci.py --phase tests         # 只跑 pytest -m verify（需已装依赖）
    python -B tools/ci.py --report-dir .tmp/ci/reports

范围与边界（不夸大）：
* checks 只调用仓库现有只读检查脚本：不联网、不写被检查的文档/矩阵、不启动 Krita/PS。
  其中 check_docs.py 与 render_archive_index.py 会以只读方式调用 git 判断“已跟踪文件”，
  不 stage/commit。这些脚本只依赖 Python 标准库。
* tests 运行 "python -m pytest -m verify"；默认排除需要真实 Krita GUI 的
  tests/test_krita_render.py（CI 无 Krita 实机）。它是 external exclusion，不是 skip：
  不计入 passed/failed/skipped、不改变退出码，记录在 summary.json 的 external_exclusions。
* 既有测试里的 skip 原样保留、单独计数：有 skip 时退出码仍为 0，但摘要行末尾追加
  「（含跳过，未全验）」，summary.json 的 full_verification=false。本入口不自行跳过
  缺素材用例，也不把 skip 折算成通过来伪造“全绿/全验”。
* 不覆盖：Krita/PS 实机渲染、真实商用素材回归、GUI 人工验收——这些仍需人工。

隔离：
* pytest 的 --basetemp 固定为 .tmp/ci/pytest-tmp（在 report-dir 之外，避免转换夹具混进
  报告目录）。tests/conftest.py 会把 basetemp 的父目录 .tmp/ci 发布为
  BRUSHCONVERTER_TEST_TASK_ROOT，GUI 测试的隔离断言依赖该契约。
* pytest 子进程还会把 TEMP/TMP/TMPDIR/APPDATA/LOCALAPPDATA/HOME/USERPROFILE/XDG_* 与
  QT_QPA_PLATFORM=offscreen 重定向/固定（开发指南 §9.1），保证 GUI/集成测试的落点
  都在 .tmp/ci 内；父进程 os.environ 保持不变。
* 每次 tests 前删除旧 junit.xml，避免旧报告被误当成当前成功结果；报告目录不自动清理。

退出码（沿 tools/verify_entry.py 约定）：
* 0：已执行阶段全部通过（有 skip 仍 0，见上）；
* 1：任一检查非 0，或 pytest 失败/中断/超时/启动异常；
* 2：pytest 收集 0 条（pytest 退出码 5）、junit 报告缺失，或检查脚本缺失（输入错误）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 报告目录：只放报告白名单文件（checks.log / pytest.log / junit.xml / summary.json / summary.md）。
DEFAULT_REPORT_DIR = REPO_ROOT / ".tmp" / "ci" / "reports"
#: pytest 工作目录：与 report-dir 同级但独立，conftest 把它的父目录当任务隔离根。
#: 固定值即合同：不接受环境变量/参数覆盖（避免任意路径交给 pytest --basetemp 递归清理）。
PYTEST_TMP_DIR = REPO_ROOT / ".tmp" / "ci" / "pytest-tmp"

JUNIT_NAME = "junit.xml"
CHECKS_LOG_NAME = "checks.log"
PYTEST_LOG_NAME = "pytest.log"
SUMMARY_JSON_NAME = "summary.json"
SUMMARY_MD_NAME = "summary.md"

CHECK_TIMEOUT_S = 60.0       # 单条只读检查最多 60 秒（7 项最坏 7 分钟）
PYTEST_TIMEOUT_S = 1200.0    # 整套回归最多 20 分钟；给 workflow job 留出安装与上传余量

#: 摘要行契约与 tools/verify_entry.py 完全一致（tests/test_ci.py 断言两者相等）。
SUMMARY_FMT = "通过 {passed} 项 ｜ 失败 {failed} 项 ｜ 跳过 {skipped} 项"
SKIP_HINT = "（含跳过，未全验）"

KRITA_TEST_REL = "tests/test_krita_render.py"
KRITA_EXCLUSION = (
    "tests/test_krita_render.py（整个文件未参与收集：文件内全部用例未运行，不是 skip。"
    "原因：真实 Krita GUI 插件路线，CI 无 Krita 实机）"
)

#: checks 阶段的只读检查（顺序固定）。相对路径一律以 REPO_ROOT 为 cwd。
CHECK_STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("文档合同检查", ("tools/check_docs.py", "--check")),
    ("验收报告索引", ("tools/render_archive_index.py", "--tracked-only", "--check")),
    (
        "研究报告索引",
        (
            "tools/render_archive_index.py",
            "--dir", "docs/研究报告",
            "--md", "docs/研究报告/INDEX.md",
            "--tracked-only", "--check",
        ),
    ),
    ("参数矩阵 Markdown", ("tools/render_matrix_md.py", "--check")),
    ("参数矩阵校验", ("tools/check_matrix.py",)),
    ("模式总表", ("tools/render_mode_table.py", "--check")),
    ("UI 控件清单", ("tools/check_ui_controls.py",)),
)

PHASES = ("checks", "tests", "all")

STATUS_LABEL = {
    "passed": "通过",
    "failed": "失败",
    "timed_out": "超时",
    "missing_script": "脚本缺失",
    "no_tests_collected": "未收集到用例",
    "junit_missing": "junit 报告缺失",
    "launch_error": "启动异常",
    "skipped": "未执行",
    "error": "错误",
}

#: 注入点：runner(command, cwd, timeout, env) -> 结果字典，便于单测替换子进程调用。
Runner = Callable[[list, Path, float, dict], dict]


# ==========================================================================
# 一、基础工具
# ==========================================================================

def _force_utf8_stdout() -> None:
    """非交互（管道 / 重定向）时把 stdout 切成 UTF-8，避免中文与全角竖线被代码页破坏。"""
    stream = sys.stdout
    if stream is None:
        return
    if hasattr(stream, "reconfigure") and not stream.isatty():
        try:
            stream.reconfigure(encoding="utf-8")
        except (OSError, ValueError):  # 极端环境重配失败就不强求
            pass


#: 开发指南 §9.1 要求的工作区隔离环境变量：落点全部在仓库 .tmp 内，不碰用户目录。
ISOLATION_SUBDIRS = {
    "TEMP": "temp",
    "TMP": "temp",
    "TMPDIR": "temp",
    "APPDATA": "appdata",
    "LOCALAPPDATA": "localappdata",
    "HOME": "home",
    "USERPROFILE": "home",
    "XDG_CONFIG_HOME": "config",
    "XDG_CACHE_HOME": "cache",
    "XDG_DATA_HOME": "data",
    "XDG_STATE_HOME": "state",
    "PYINSTALLER_CONFIG_DIR": "config",
}


def _child_env() -> dict:
    """子进程环境：固定 UTF-8、禁止写 .pyc，其余继承。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _test_env(isolation_root: Path) -> dict:
    """pytest 子进程的隔离环境：TEMP/TMP/APPDATA/HOME/XDG 全部指向任务目录内。

    GUI/集成测试会断言这些落点在本任务隔离根下（开发指南 §9.1），
    不设置就会在 CI 上大面积 setup error，而不是被测代码的真实失败。
    """
    env = _child_env()
    for name, subdir in ISOLATION_SUBDIRS.items():
        path = isolation_root / subdir
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    # 固定值不是目录：Qt 平台插件名必须保持 "offscreen"，不能写成路径。
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _display_command(command: list) -> str:
    """把命令渲染成可复制的一行（日志用）。"""
    if os.name == "nt":
        return subprocess.list2cmdline([str(part) for part in command])
    return shlex.join([str(part) for part in command])


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _run_command(command: list, cwd: Path, timeout: float, env: dict | None = None) -> dict:
    """真实子进程执行；超时与启动异常都转成结构化结果，不抛到主流程外。"""
    started = time.monotonic()
    timed_out = False
    exit_code = None
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            [str(part) for part in command],
            cwd=str(cwd),
            env=env if env is not None else _child_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = _as_text(exc.stdout)
        stderr = _as_text(exc.stderr)
    except OSError as exc:  # 解释器/脚本不存在等启动失败
        exit_code = None
        stderr = "OSError: " + str(exc)
    return {
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - started, 3),
        "stdout": stdout,
        "stderr": stderr,
    }


def _log_section(command: list, result: dict) -> str:
    """一条命令在日志里的段落（命令、退出码、耗时、原始输出）。"""
    lines = ["$ " + _display_command(command), "cwd: " + str(REPO_ROOT)]
    if result.get("timed_out"):
        lines.append("exit_code: TIMEOUT")
    else:
        lines.append("exit_code: " + str(result.get("exit_code")))
    lines.append("duration_s: " + str(result.get("duration_s", "")))
    lines.append("")
    stdout = _as_text(result.get("stdout")).rstrip("\n")
    if stdout:
        lines.append(stdout)
    stderr = _as_text(result.get("stderr")).rstrip("\n")
    if stderr:
        lines.append("--- stderr ---")
        lines.append(stderr)
    return "\n".join(lines) + "\n\n"


def _append_log(log_path: Path, command: list, result: dict) -> None:
    with log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_log_section(command, result))


def _write_log(log_path: Path, command: list, result: dict) -> None:
    log_path.write_text(_log_section(command, result), encoding="utf-8")


def _git_head() -> str | None:
    """只读探测当前提交（CI 证据用）；git 不可用或失败返回 None。"""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    head = completed.stdout.strip()
    return head or None


def _dependency_versions() -> dict:
    """关键运行依赖版本（复现证据；未安装记 None，不据此判失败）。"""
    from importlib import metadata

    versions = {}
    for name in ("numpy", "Pillow", "PySide6", "pytest", "ruff"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def resolve_report_dir(value: str | None) -> Path:
    """解析报告目录：缺省 .tmp/ci/reports；相对路径按仓库根解析。"""
    if not value:
        return DEFAULT_REPORT_DIR
    path = Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


# ==========================================================================
# 二、命令组装（单测直接校验这些纯函数）
# ==========================================================================

def build_checks_commands() -> list:
    """checks 阶段的命令列表：[(检查名, command)]，均为 sys.executable -B 只读脚本。"""
    return [(name, [sys.executable, "-B", *args]) for name, args in CHECK_STEPS]


def build_pytest_command(report_dir: Path, allow_krita: bool) -> list:
    """tests 阶段命令：pytest -m verify + 工作区隔离 + junit + skip 明细。"""
    command = [sys.executable, "-B", "-m", "pytest", "-m", "verify"]
    if not allow_krita:
        command += ["--ignore", KRITA_TEST_REL]
    command += [
        "--basetemp", str(PYTEST_TMP_DIR),
        "-p", "no:cacheprovider",
        "-ra",
        "--junitxml", str(report_dir / JUNIT_NAME),
    ]
    return command


def summarize_junit(path: Path) -> dict:
    """解析 junit 计数；文件缺失或损坏时 available=False（调用方按输入错误处理）。"""
    if not path.is_file():
        return {"available": False, "total": 0, "passed": 0, "failed": 0, "skipped": 0}
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError):
        return {"available": False, "total": 0, "passed": 0, "failed": 0, "skipped": 0}
    total = 0
    failed = 0
    skipped = 0
    for case in root.iter("testcase"):
        total += 1
        tags = {child.tag for child in case}
        if "skipped" in tags:
            skipped += 1
        elif "failure" in tags or "error" in tags:
            failed += 1
    return {
        "available": True,
        "total": total,
        "passed": total - failed - skipped,
        "failed": failed,
        "skipped": skipped,
    }


def build_summary_line(counts: dict) -> str:
    """固定摘要行；有跳过时末尾追加「（含跳过，未全验）」，skip 不计入通过。"""
    line = SUMMARY_FMT.format(
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
    )
    if counts["skipped"] > 0:
        line += SKIP_HINT
    return line


# ==========================================================================
# 三、两个阶段
# ==========================================================================

def run_checks(report_dir: Path, runner: Runner) -> dict:
    """顺序执行只读结构检查；任一失败/超时或脚本缺失则整体失败。"""
    report_dir.mkdir(parents=True, exist_ok=True)
    log_path = report_dir / CHECKS_LOG_NAME
    log_path.write_text("", encoding="utf-8")
    started = time.monotonic()
    env = _child_env()
    items = []
    failed = False
    missing = False
    for name, command in build_checks_commands():
        script = REPO_ROOT / str(command[2])
        if not script.is_file():
            missing = True
            status = "missing_script"
            result = {
                "exit_code": None,
                "timed_out": False,
                "duration_s": 0.0,
                "stdout": "",
                "stderr": "检查脚本不存在：" + str(script),
            }
        else:
            result = runner(command, REPO_ROOT, CHECK_TIMEOUT_S, env)
            if result.get("timed_out"):
                status = "timed_out"
                failed = True
            elif result.get("exit_code") == 0:
                status = "passed"
            else:
                status = "failed"
                failed = True
        _append_log(log_path, command, result)
        items.append(
            {
                "name": name,
                "command": [str(part) for part in command],
                "status": status,
                "exit_code": result.get("exit_code"),
                "timed_out": bool(result.get("timed_out")),
                "duration_s": result.get("duration_s", 0.0),
            }
        )
    if missing:
        exit_code = 2
        phase_status = "error"
    elif failed:
        exit_code = 1
        phase_status = "failed"
    else:
        exit_code = 0
        phase_status = "passed"
    return {
        "phase": "checks",
        "status": phase_status,
        "exit_code": exit_code,
        "duration_s": round(time.monotonic() - started, 3),
        "log": str(log_path),
        "items": items,
    }


def run_tests(report_dir: Path, runner: Runner, allow_krita: bool) -> dict:
    """执行 pytest -m verify；先删旧 junit，再按退出码/junit 判定结果。"""
    report_dir.mkdir(parents=True, exist_ok=True)
    junit_path = report_dir / JUNIT_NAME
    junit_path.unlink(missing_ok=True)  # 防旧报告冒充本次成功
    # conftest 把 --basetemp 的父目录当任务隔离根；先建好它并把 TEMP/TMP/APPDATA/HOME/XDG
    # 全部指到该根内，否则 GUI/集成测试的隔离断言会在 CI 上大面积 setup error。
    isolation_root = PYTEST_TMP_DIR.parent
    isolation_root.mkdir(parents=True, exist_ok=True)
    env = _test_env(isolation_root)
    command = build_pytest_command(report_dir, allow_krita)
    result = runner(command, REPO_ROOT, PYTEST_TIMEOUT_S, env)
    log_path = report_dir / PYTEST_LOG_NAME
    _write_log(log_path, command, result)
    counts = summarize_junit(junit_path)
    summary_line = build_summary_line(counts) if counts["available"] else None
    pytest_exit = result.get("exit_code")
    if result.get("timed_out"):
        exit_code, phase_status = 1, "timed_out"
    elif pytest_exit is None:
        exit_code, phase_status = 1, "launch_error"
    elif pytest_exit == 5:
        exit_code, phase_status = 2, "no_tests_collected"
    elif pytest_exit != 0:
        exit_code, phase_status = 1, "failed"
    elif not counts["available"]:
        exit_code, phase_status = 2, "junit_missing"
    elif counts["total"] == 0:
        exit_code, phase_status = 2, "no_tests_collected"
    elif counts["failed"] > 0:
        # pytest 退出 0 但 junit 里有 failure/error（插件异常、过滤后残留等）也不能算通过
        exit_code, phase_status = 1, "failed"
    else:
        exit_code, phase_status = 0, "passed"
    return {
        "phase": "tests",
        "status": phase_status,
        "exit_code": exit_code,
        "pytest_exit_code": pytest_exit,
        "timed_out": bool(result.get("timed_out")),
        "duration_s": result.get("duration_s", 0.0),
        "command": [str(part) for part in command],
        "junit": str(junit_path),
        "junit_available": counts["available"],
        "counts": counts,
        "summary_line": summary_line,
        "log": str(log_path),
        "allow_krita": bool(allow_krita),
    }


# ==========================================================================
# 四、报告
# ==========================================================================

def build_report(
    *,
    phase: str,
    allow_krita: bool,
    report_dir: Path,
    git_head: str | None,
    checks_result: dict | None,
    tests_result: dict | None,
    exit_code: int,
) -> dict:
    counts = tests_result.get("counts") if tests_result else None
    counts_available = bool(counts and counts.get("available"))
    failed = counts["failed"] if counts_available else 0
    skipped = counts["skipped"] if counts_available else 0
    external = [] if allow_krita else [KRITA_EXCLUSION]
    # 本入口只覆盖自动阶段；Krita/PS 实机、真实素材与 GUI 人工验收永远不在此列，
    # 因此 full_verification 恒为 False，避免“自动阶段全绿”被误读成验收通过。
    automated_suite_complete = bool(
        phase == "all"
        and exit_code == 0
        and checks_result is not None
        and checks_result.get("status") == "passed"
        and tests_result is not None
        and tests_result.get("status") == "passed"
        and counts_available
        and failed == 0
        and skipped == 0
        and not external
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "dependency_versions": _dependency_versions(),
        "platform": platform.platform(),
        "repo_root": str(REPO_ROOT),
        "git_head": git_head,
        "phase": phase,
        "allow_krita": bool(allow_krita),
        "report_dir": str(report_dir),
        "exit_code": exit_code,
        "checks": checks_result,
        "tests": tests_result,
        "counts": counts,
        "summary_line": tests_result.get("summary_line") if tests_result else None,
        "external_exclusions": external,
        "full_verification": False,
        "automated_suite_complete": automated_suite_complete,
        "notes": [
            "full_verification 恒为 false：Krita/PS 实机、真实素材与 GUI 人工验收不在本入口内。",
            "automated_suite_complete 只表示本入口自动阶段全部通过、无 skip、无外部排除。",
            "有 skip 时 exit 0 但 automated_suite_complete=false；skip 从不算作通过。",
            "默认排除 tests/test_krita_render.py（external exclusion，不是 skip）。",
        ],
    }


def render_markdown(report: dict) -> str:
    """人类可读报告；字段值与 summary.json 一致，不含本机隐藏信息。"""
    checks = report.get("checks")
    tests = report.get("tests")
    lines = [
        "# brushConverter CI 报告",
        "",
        "- 生成时间：" + str(report.get("generated_at")),
        "- 解释器：Python " + str(report.get("python", {}).get("version"))
        + "（" + str(report.get("python", {}).get("executable")) + "）",
        "- 平台：" + str(report.get("platform")),
        "- git HEAD：" + (str(report.get("git_head")) if report.get("git_head") else "（git 不可用）"),
        "- 阶段：" + str(report.get("phase"))
        + "（--allow-krita：" + ("是" if report.get("allow_krita") else "否") + "）",
        "- 报告目录：" + str(report.get("report_dir")),
        "",
        "## 结构检查（只读，不联网，不启动 Krita/PS）",
        "",
    ]
    if checks:
        lines += ["| # | 检查 | 结果 | 退出码 | 耗时(s) |", "|---|---|---|---|---|"]
        for index, item in enumerate(checks.get("items", []), start=1):
            lines.append(
                "| {i} | {name} | {status} | {code} | {duration} |".format(
                    i=index,
                    name=item.get("name"),
                    status=STATUS_LABEL.get(item.get("status"), item.get("status")),
                    code=item.get("exit_code"),
                    duration=item.get("duration_s"),
                )
            )
        lines += [
            "",
            "阶段退出码：" + str(checks.get("exit_code")),
            "日志：checks.log",
        ]
    else:
        lines += ["（本次未执行）"]
    lines += ["", "## 自动回归（python -m pytest -m verify）", ""]
    if tests and tests.get("status") == "skipped":
        lines += ["（未执行：" + str(tests.get("reason") or "") + "）"]
    elif tests:
        lines += [
            "- pytest 退出码：" + str(tests.get("pytest_exit_code")),
            "- junit：" + ("已生成" if tests.get("junit_available") else "缺失（按输入错误处理）"),
            "- 日志：pytest.log（-ra 列出跳过原因）",
        ]
        if tests.get("summary_line"):
            lines += ["", "摘要行：" + str(tests.get("summary_line"))]
        counts = tests.get("counts") or {}
        if counts.get("available"):
            lines += [
                "",
                "通过 " + str(counts.get("passed")) + " 项；失败 "
                + str(counts.get("failed")) + " 项；跳过 " + str(counts.get("skipped"))
                + " 项（跳过不计入通过，含跳过即未全验）。",
            ]
    else:
        lines += ["（本次未执行）"]
    external = report.get("external_exclusions") or []
    lines += ["", "## 外部排除", ""]
    if external:
        for item in external:
            lines.append("- " + str(item))
    else:
        lines.append("- 无（--allow-krita 已纳入实机 Krita 用例，是否可用由测试自身如实 skip）")
    lines += [
        "",
        "## 结论",
        "",
        "- 退出码：" + str(report.get("exit_code")),
        "- full_verification：false（Krita/PS 实机、真实素材与 GUI 人工验收不在本入口内，恒 false）",
        "- automated_suite_complete："
        + ("true" if report.get("automated_suite_complete") else "false")
        + "（本入口自动阶段是否全部通过、无 skip、无外部排除）",
        "- 边界：本报告只覆盖只读结构检查与非 Krita 自动回归，不覆盖 Krita/PS 实机渲染、"
        "真实商用素材回归与 GUI 人工验收。",
        "",
    ]
    return "\n".join(lines)


def write_report_files(report_dir: Path, report: dict) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / SUMMARY_JSON_NAME).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (report_dir / SUMMARY_MD_NAME).write_text(render_markdown(report), encoding="utf-8")


def _print_report(report: dict) -> None:
    print("CI 阶段：" + str(report.get("phase")) + "；报告目录：" + str(report.get("report_dir")))
    checks = report.get("checks")
    if checks:
        for item in checks.get("items", []):
            print(
                "  [{status}] {name}（退出码 {code}）".format(
                    status=STATUS_LABEL.get(item.get("status"), item.get("status")),
                    name=item.get("name"),
                    code=item.get("exit_code"),
                )
            )
    tests = report.get("tests")
    if tests and tests.get("status") == "skipped":
        print("  测试阶段未执行：" + str(tests.get("reason") or ""))
    elif tests:
        if tests.get("summary_line"):
            print("  " + str(tests.get("summary_line")))
        else:
            print("  pytest 状态：" + str(tests.get("status")))
    if report.get("external_exclusions"):
        print("外部排除：" + "；".join(str(item) for item in report["external_exclusions"]))
    print(
        "退出码："
        + str(report.get("exit_code"))
        + "；full_verification=false；automated_suite_complete="
        + ("true" if report.get("automated_suite_complete") else "false")
    )


# ==========================================================================
# 五、主流程
# ==========================================================================

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tools/ci.py",
        description="brushConverter 最小 CI 入口（只读结构检查 + 非 Krita 自动回归）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(
            [
                "示例：",
                "  python -B tools/ci.py",
                "  python -B tools/ci.py --phase checks",
                "  python -B tools/ci.py --phase tests --report-dir .tmp/ci/reports",
                "",
                "边界：默认排除实机 Krita 用例（external exclusion，不是 skip）；",
                "有 skip 时退出 0 但未全验（full_verification=false）。",
            ]
        ),
    )
    parser.add_argument(
        "--phase",
        choices=PHASES,
        default="all",
        help="执行阶段（默认 all：先 checks 后 tests，fail-fast）",
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        help="报告目录（默认 .tmp/ci/reports；可已存在，不自动清理）",
    )
    parser.add_argument(
        "--allow-krita",
        action="store_true",
        help="本机有 Krita 时把实机渲染用例纳入 pytest（CI 不用）",
    )
    return parser.parse_args(argv)


def run_ci(argv=None, *, runner: Runner | None = None, git_head: str | None = None) -> int:
    """执行一次 CI 并返回退出码；runner/git_head 仅供单测注入。"""
    _force_utf8_stdout()
    args = parse_args(argv)
    run = runner if runner is not None else _run_command
    report_dir = resolve_report_dir(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    resolved_head = git_head if git_head is not None else _git_head()

    checks_result: dict | None = None
    tests_result: dict | None = None
    if args.phase in ("checks", "all"):
        checks_result = run_checks(report_dir, run)

    if args.phase == "checks":
        exit_code = checks_result["exit_code"] if checks_result else 2
    elif args.phase == "tests":
        tests_result = run_tests(report_dir, run, args.allow_krita)
        exit_code = tests_result["exit_code"]
    else:
        exit_code = checks_result["exit_code"] if checks_result else 2
        if exit_code == 0:
            tests_result = run_tests(report_dir, run, args.allow_krita)
            exit_code = tests_result["exit_code"]
        else:
            tests_result = {
                "phase": "tests",
                "status": "skipped",
                "reason": "checks 未通过（fail-fast，未执行）",
            }
            exit_code = checks_result["exit_code"] if checks_result else 2

    report = build_report(
        phase=args.phase,
        allow_krita=args.allow_krita,
        report_dir=report_dir,
        git_head=resolved_head,
        checks_result=checks_result,
        tests_result=tests_result,
        exit_code=exit_code,
    )
    write_report_files(report_dir, report)
    _print_report(report)
    return exit_code


def main(argv=None) -> int:
    return run_ci(argv)


if __name__ == "__main__":
    sys.exit(main())
