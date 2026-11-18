#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证入口汇总器（任务 01）。

用途
----
一条命令跑完整个验证体系，并给出「通过 / 失败 / 跳过」的固定格式摘要：

    python tools/verify_entry.py

它内部用子进程调用「python -m pytest -m verify」（带 --junitxml），
解析 junit 报告后在终端统一打印摘要与跳过明细，不会把 pytest 的原始输出
和摘要混在一起。退出码约定（写死，后续任务依赖）：

* 有失败（含用例报错 / pytest 异常中断） -> 1
* 全部通过且无跳过                      -> 0
* 全部通过但有跳过                      -> 0，且摘要行末尾追加「（含跳过，未全验）」
* 一次都没收集到用例                    -> 2

两种情况都不把跳过算成通过：通过数、跳过数分开计数。

本文件同时被 pytest 当作插件加载（见仓库根 pytest.ini 的
addopts = -p tools.verify_entry）：pytest_collection_modifyitems
钩子会给所有被收集的用例补上 verify 标记，因此「现有测试全部纳入
verify」不需要逐个修改既有测试文件。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# 仓库根目录（本文件在 <repo>/tools/ 下）
REPO_ROOT = Path(__file__).resolve().parent.parent

# 全量验证入口的标记名
VERIFY_MARK = "verify"

# 摘要行的固定格式（字段顺序与文字不可改：后续任务按这一行解析）。
# 注意「｜」是 U+FF5C 全角竖线，不是 ASCII 的 "|"。
SUMMARY_FMT = "通过 {passed} 项 ｜ 失败 {failed} 项 ｜ 跳过 {skipped} 项"

# 有跳过时追加在摘要行末尾的提示，一字不差
SKIP_HINT = "（含跳过，未全验）"


# ==========================================================================
# 一、pytest 插件部分：给所有用例补 verify 标记
# ==========================================================================

def pytest_configure(config):  # noqa: ARG001  (pytest 钩子签名固定)
    """pytest 启动时统一输出编码（非交互终端切 UTF-8，交互终端保持系统编码）。"""
    _force_utf8_stdout()


def pytest_collection_modifyitems(config, items):  # noqa: ARG001  (pytest 钩子签名固定)
    """pytest 钩子：给本次收集到的每个用例补 verify 标记。

    这样 pytest -m verify 能一次收全 tests/ 下的所有用例，
    而不需要改动任何一个既有测试文件（任务 01 的硬性要求）。
    """
    import pytest  # 延迟导入：本文件被当普通脚本执行时不需要 pytest

    verify_mark = pytest.mark.verify
    for item in items:
        if VERIFY_MARK not in item.keywords:
            item.add_marker(verify_mark)


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ARG001
    """pytest 跑完时，在它自己的输出末尾再打一行同样格式的摘要。

    这样「python -m pytest -m verify」单独跑也能一眼看到通过/失败/跳过，
    而 tools/verify_entry.py 的摘要打印逻辑与之共用同一套文字契约。
    """
    stats = terminalreporter.stats
    passed = len(stats.get("passed", []))
    failed = len(stats.get("failed", [])) + len(stats.get("error", []))
    skipped = len(stats.get("skipped", []))
    total = passed + failed + skipped
    if total == 0:
        # 0 用例的情况交给 verify_entry.py 处理（退出码 2），这里不重复刷屏
        return
    line = SUMMARY_FMT.format(passed=passed, failed=failed, skipped=skipped)
    if skipped > 0:
        line += SKIP_HINT
    terminalreporter.write_sep("-", "验证摘要")
    terminalreporter.write_line(line)


# ==========================================================================
# 二、junit 报告解析
# ==========================================================================

def _decode_stdout(data) -> str:
    """把子进程 stdout 解码成文本：优先 UTF-8，失败退回系统本地编码。"""
    if isinstance(data, str):
        return data
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _case_file(case: ET.Element) -> str:
    """从 junit 用例节点取「所属测试文件名」。"""
    file_attr = case.get("file")
    if file_attr:
        return file_attr
    classname = case.get("classname") or ""
    if not classname:
        return "?"
    # 没有 file 属性时用 classname 反推（classname 形如 tests.test_mapping）
    return classname.replace(".", "/") + ".py"


def _skip_reason(case: ET.Element, skipped: ET.Element) -> str:
    """取 pytest 的 skip reason 原文：优先 message 属性，其次正文首行。"""
    message = skipped.get("message")
    if message:
        return message.strip()
    text = (skipped.text or "").strip()
    if text:
        return text.splitlines()[0].strip()
    return "（pytest 未给出跳过原因）"


def parse_junit(xml_path) -> dict:
    """解析 junit 报告，返回计数与明细。

    返回字典字段：total / passed / failed / skipped /
    skips（[(文件, 用例, 原因)]）/ failures（[(文件, 用例)]）。
    """
    root = ET.parse(str(xml_path)).getroot()
    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]

    result = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "skips": [],
        "failures": [],
    }
    for suite in suites:
        for case in suite.findall("testcase"):
            result["total"] += 1
            name = case.get("name") or "?"
            file_name = _case_file(case)
            skipped = case.find("skipped")
            failure = case.find("failure")
            error = case.find("error")
            if skipped is not None:
                result["skipped"] += 1
                result["skips"].append((file_name, name, _skip_reason(case, skipped)))
            elif failure is not None or error is not None:
                result["failed"] += 1
                result["failures"].append((file_name, name))
            else:
                result["passed"] += 1
    return result


# ==========================================================================
# 三、摘要文本与退出码
# ==========================================================================

def build_summary(result: dict) -> str:
    """生成固定格式的摘要行；有跳过时在末尾追加「（含跳过，未全验）」。"""
    line = SUMMARY_FMT.format(
        passed=result["passed"],
        failed=result["failed"],
        skipped=result["skipped"],
    )
    if result["skipped"] > 0:
        line += SKIP_HINT
    return line


def decide_exit_code(pytest_code: int, result: dict) -> int:
    """按任务 01 的退出码约定换算最终退出码。"""
    # 一次都没收集到用例（标记没注册 / 路径写错 / 收集阶段失败）→ 2
    if result["total"] == 0:
        return 2
    # 有用例失败，或 pytest 自己异常退出（收集错误、中断等）→ 1
    if result["failed"] > 0 or pytest_code not in (0, 1):
        return 1
    # 全通过：无跳过 0，有跳过也是 0（但摘要带提示）
    return 0


# ==========================================================================
# 四、子进程调用 pytest
# ==========================================================================

def run_pytest(extra_args, junit_path) -> subprocess.CompletedProcess:
    """用子进程跑「python -m pytest -m verify」，把结果写进 junit 文件。"""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        VERIFY_MARK,
        "-q",
        "--junitxml=" + str(junit_path),
        *[str(a) for a in extra_args],
    ]
    env = dict(os.environ)
    # 强制子进程用 UTF-8 输出，避免中文在管道里被本地代码页破坏
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        env=env,
        check=False,
    )


# ==========================================================================
# 五、主流程
# ==========================================================================

def _force_utf8_stdout() -> None:
    """非交互（管道 / 重定向）时把 stdout 切成 UTF-8。

    原因：Windows 中文环境下 Python 往管道写中文会按本地代码页（cp936）编码，
    被 CI 或 MSYS bash 按 UTF-8 读走就变成乱码，摘要行里的「｜」也会坏掉。
    交互终端（isatty）保持系统默认编码，避免 cmd.exe 里反而乱码。
    """
    stream = sys.stdout
    if hasattr(stream, "reconfigure") and not stream.isatty():
        try:
            stream.reconfigure(encoding="utf-8")
        except (OSError, ValueError):  # 极端环境重配失败就不强求
            pass


def main(args=None) -> int:
    """执行一次全量验证并打印摘要，返回退出码。

    args 是传给 pytest 的额外参数列表（不含程序名）；默认取 sys.argv[1:]。
    """
    _force_utf8_stdout()
    extra = list(sys.argv[1:] if args is None else args)

    with tempfile.TemporaryDirectory(prefix="verify_entry_") as tmp_dir:
        junit_path = Path(tmp_dir) / "junit.xml"
        proc = run_pytest(extra, junit_path)
        stdout_text = _decode_stdout(proc.stdout)
        stderr_text = _decode_stdout(proc.stderr)

        if not junit_path.exists():
            print(SUMMARY_FMT.format(passed=0, failed=0, skipped=0))
            print("错误：pytest 未生成 junit 报告，无法统计（多半是收集阶段就失败了）。")
            if stderr_text.strip():
                print("pytest stderr 原文：")
                print(stderr_text.strip())
            return 2

        result = parse_junit(junit_path)

    # 先打印摘要，再打印明细：摘要必须是最容易被看到/被脚本解析的那一行
    print(build_summary(result))

    if result["skips"]:
        print("跳过明细：")
        for file_name, name, reason in result["skips"]:
            print("  - {file}::{name} — {reason}".format(file=file_name, name=name, reason=reason))

    if result["failures"]:
        print("失败明细：")
        for file_name, name in result["failures"]:
            print("  - {file}::{name}".format(file=file_name, name=name))
        if stdout_text.strip():
            print("pytest 输出尾部（排查用）：")
            for line in stdout_text.strip().splitlines()[-30:]:
                print("  " + line)

    if result["total"] == 0:
        print("错误：本次一次都没收集到用例（退出码 2）。请检查 -m verify 标记与 --junitxml 配置。")
        if stdout_text.strip():
            print("pytest 输出原文：")
            print(stdout_text.strip())
        return 2

    return decide_exit_code(proc.returncode, result)


if __name__ == "__main__":
    sys.exit(main())
