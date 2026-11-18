"""任务 01：验证入口 tools/verify_entry.py 的口径回归测试。

这里锁死的是「摘要契约 + 退出码约定」，不是 pytest 自己的行为：

* 通过 / 失败 / 跳过 三个计数分开，「跳过绝不计入通过」；
* 摘要行文字一字不差（后续任务要按这一行解析）；
* 全通过但有跳过 -> 退出码 0，摘要行末尾带「（含跳过，未全验）」；
* 有失败 -> 退出码 1；
* 一次都没收集到用例 -> 退出码 2。

前三个情境用临时目录里现造的小测试 + 真实子进程跑，保证测的是
verify_entry 的端到端行为；「0 用例」情境用 monkeypatch 伪造 junit 报告，
因为它没法靠正常测试文件自然构造出来。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFY_ENTRY = REPO_ROOT / "tools" / "verify_entry.py"

pytestmark = pytest.mark.verify


def _run_entry(extra_args):
    """用真实子进程跑一遍 verify_entry，返回 (退出码, stdout 文本, stderr 文本)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, str(VERIFY_ENTRY), *extra_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        env=env,
        check=False,
    )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
    )


def _write_case(tmp_path, filename, body):
    """在临时目录里写一个小测试文件，返回它的路径。"""
    case = tmp_path / filename
    case.write_text(body, encoding="utf-8")
    return case


def test_skip_is_counted_separately_from_pass(tmp_path):
    """缺样本 -> 跳过：退出码 0，摘要带「（含跳过，未全验）」并列出原因。"""
    case = _write_case(
        tmp_path,
        "test_skip_probe.py",
        "import pytest\n\n\ndef test_skip_probe():\n    pytest.skip(\"缺少样本：临时探针\")\n",
    )
    code, out, err = _run_entry(["-c", "pytest.ini", str(case)])

    assert code == 0, f"全通过但有跳过应返回 0，实际 {code}\nstdout:\n{out}\nstderr:\n{err}"
    first_line = out.splitlines()[0] if out.splitlines() else ""
    assert first_line == "通过 0 项 ｜ 失败 0 项 ｜ 跳过 1 项（含跳过，未全验）", (
        f"摘要行不符合契约：{first_line!r}"
    )
    # 逐条列出跳过项的文件名与原因原文
    assert "test_skip_probe" in out, "摘要里没有跳过项文件名"
    assert "缺少样本：临时探针" in out, "摘要里没有 pytest 的 skip reason 原文"


def test_all_pass_without_skip(tmp_path):
    """正常通过 -> 退出码 0，摘要不带「（含跳过，未全验）」。"""
    case = _write_case(
        tmp_path,
        "test_pass_probe.py",
        "def test_pass_probe():\n    assert 1 + 1 == 2\n",
    )
    code, out, err = _run_entry(["-c", "pytest.ini", str(case)])

    assert code == 0, f"全通过应返回 0，实际 {code}\nstdout:\n{out}\nstderr:\n{err}"
    first_line = out.splitlines()[0] if out.splitlines() else ""
    assert first_line == "通过 1 项 ｜ 失败 0 项 ｜ 跳过 0 项", f"摘要行不符合契约：{first_line!r}"
    assert "（含跳过，未全验）" not in out, "没有跳过时不应出现跳过提示"


def test_failure_returns_exit_code_1(tmp_path):
    """故意失败 -> 退出码 1，「失败 M 项」不为 0，并点名失败用例。"""
    case = _write_case(
        tmp_path,
        "test_fail_probe.py",
        "def test_fail_probe():\n    assert 1 == 2, \"这个断言故意不成立\"\n",
    )
    code, out, err = _run_entry(["-c", "pytest.ini", str(case)])

    assert code == 1, f"有失败应返回 1，实际 {code}\nstdout:\n{out}\nstderr:\n{err}"
    assert "通过 0 项 ｜ 失败 1 项 ｜ 跳过 0 项" in out, f"摘要行不符合契约：{out}"
    assert "test_fail_probe" in out, "摘要里没有点名失败的用例"


def test_no_test_collected_returns_exit_code_2(monkeypatch, capsys):
    """一次都没收集到用例 -> 退出码 2（用伪造的空 junit 报告构造）。"""
    # 延迟到用例内部导入，避免测试收集阶段就加载被测模块
    sys.path.insert(0, str(REPO_ROOT))
    from tools import verify_entry  # noqa: PLC0415

    def fake_run_pytest(extra_args, junit_path):  # noqa: ARG001  (签名对齐即可)
        Path(junit_path).write_text(
            '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" tests="0" />'
            "</testsuites>",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=5, stdout=b"", stderr=b"")

    monkeypatch.setattr(verify_entry, "run_pytest", fake_run_pytest)
    code = verify_entry.main([])
    out = capsys.readouterr().out

    assert code == 2, f"0 用例应返回 2，实际 {code}\nstdout:\n{out}"
    assert "通过 0 项 ｜ 失败 0 项 ｜ 跳过 0 项" in out, f"摘要行不符合契约：{out}"
    assert "一次都没收集到用例" in out, "没有给出 0 用例的原因提示"
