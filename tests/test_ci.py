# -*- coding: utf-8 -*-
"""task-2 单测：tools/ci.py 的最小 CI 入口契约。

不真跑整套 pytest（会重复跑所有用例）：用注入的假 runner 校验命令组装、退出码
传播、报告落盘、skip 与外部排除口径。只用标准库 + pytest，不需要 numpy/PySide6/Krita。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import ci  # noqa: E402
import verify_entry  # noqa: E402

pytestmark = pytest.mark.verify

GIT_HEAD = "a" * 40
SUMMARY_KEYS = ("checks.log", "pytest.log", "junit.xml", "summary.json", "summary.md")


def _junit(passed: int = 0, failed: int = 0, skipped: int = 0) -> str:
    """合成 junit：passed 正常、failed 带 failure、skipped 带 skipped 原因。"""
    cases = []
    for index in range(passed):
        cases.append('<testcase classname="tests.test_fake" name="p{}" />'.format(index))
    for index in range(failed):
        cases.append(
            '<testcase classname="tests.test_fake" name="f{}">'
            '<failure message="boom" /></testcase>'.format(index)
        )
    for index in range(skipped):
        cases.append(
            '<testcase classname="tests.test_fake" name="s{}">'
            '<skipped message="缺商用素材" /></testcase>'.format(index)
        )
    total = passed + failed + skipped
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="pytest" tests="{}" failures="{}" errors="0" skipped="{}">'
        "{}"
        "</testsuite></testsuites>"
    ).format(total, failed, skipped, "".join(cases))


def _result(exit_code=0, timed_out=False) -> dict:
    return {
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_s": 0.01,
        "stdout": "fake stdout",
        "stderr": "",
    }


class FakeRunner:
    """记录命令并返回预设结果；pytest 调用按需把 junit 写到 --junitxml 指定路径。"""

    def __init__(
        self,
        *,
        check_exits=None,
        check_timeouts=(),
        pytest_exit=0,
        pytest_timeout=False,
        junit=None,
    ):
        self.commands = []
        self.check_exits = dict(check_exits or {})
        self.check_timeouts = set(check_timeouts)
        self.pytest_exit = pytest_exit
        self.pytest_timeout = pytest_timeout
        self.junit = junit

    def __call__(self, command, cwd, timeout, env=None):
        self.commands.append(
            {
                "command": list(command),
                "cwd": Path(cwd),
                "timeout": timeout,
                "env": dict(env or {}),
            }
        )
        if list(command[2:4]) == ["-m", "pytest"]:
            if self.junit is not None:
                junit_path = Path(command[command.index("--junitxml") + 1])
                junit_path.write_text(self.junit, encoding="utf-8")
            return _result(self.pytest_exit, timed_out=self.pytest_timeout)
        script = str(command[2])
        if script in self.check_timeouts:
            return _result(None, timed_out=True)
        return _result(self.check_exits.get(script, 0))


def _run(argv, runner, git_head=GIT_HEAD):
    return ci.run_ci(list(argv), runner=runner, git_head=git_head)


def _load(report_dir: Path) -> dict:
    return json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))


def _is_pytest_call(entry) -> bool:
    return list(entry["command"][2:4]) == ["-m", "pytest"]


@pytest.fixture()
def report_dir(tmp_path):
    return tmp_path / "report"


# ------------------------------------------------------------------ 默认与阶段


def test_default_phase_is_all():
    assert ci.parse_args([]).phase == "all"
    assert ci.parse_args(["--phase", "checks"]).phase == "checks"
    assert ci.parse_args(["--phase", "tests", "--allow-krita"]).allow_krita is True


def test_default_runs_checks_then_pytest(report_dir):
    runner = FakeRunner(junit=_junit(passed=3))
    exit_code = _run(["--report-dir", str(report_dir)], runner)
    assert exit_code == 0
    flags = [_is_pytest_call(entry) for entry in runner.commands]
    assert flags == [False] * len(ci.CHECK_STEPS) + [True]
    assert len(ci.CHECK_STEPS) == 7

    pytest_command = runner.commands[-1]["command"]
    assert "--basetemp" in pytest_command
    assert pytest_command[pytest_command.index("--basetemp") + 1] == str(ci.PYTEST_TMP_DIR)
    assert pytest_command[pytest_command.index("--junitxml") + 1] == str(report_dir / "junit.xml")
    assert "verify" in pytest_command
    assert "no:cacheprovider" in pytest_command
    assert "-ra" in pytest_command

    summary = _load(report_dir)
    assert summary["phase"] == "all"
    assert summary["exit_code"] == 0
    assert summary["git_head"] == GIT_HEAD
    assert summary["full_verification"] is False  # 实机/人工未纳入，恒 false
    assert summary["automated_suite_complete"] is False  # 默认排除实机 Krita，不算完整
    for name in SUMMARY_KEYS:
        assert (report_dir / name).is_file(), name
    checks_log = (report_dir / "checks.log").read_text(encoding="utf-8")
    assert "tools/check_matrix.py" in checks_log
    assert "exit_code: 0" in checks_log


def test_phase_checks_only_does_not_run_pytest(report_dir):
    runner = FakeRunner()
    exit_code = _run(["--phase", "checks", "--report-dir", str(report_dir)], runner)
    assert exit_code == 0
    assert len(runner.commands) == len(ci.CHECK_STEPS)
    assert all(not _is_pytest_call(entry) for entry in runner.commands)
    summary = _load(report_dir)
    assert summary["checks"]["status"] == "passed"
    assert summary["tests"] is None
    assert not (report_dir / "pytest.log").exists()
    assert summary["full_verification"] is False


def test_phase_tests_only_does_not_run_checks(report_dir):
    runner = FakeRunner(junit=_junit(passed=2))
    exit_code = _run(["--phase", "tests", "--report-dir", str(report_dir)], runner)
    assert exit_code == 0
    assert len(runner.commands) == 1
    assert _is_pytest_call(runner.commands[0])
    summary = _load(report_dir)
    assert summary["checks"] is None
    assert not (report_dir / "checks.log").exists()
    assert summary["counts"]["passed"] == 2
    assert summary["full_verification"] is False


# ------------------------------------------------------------------ 失败传播


def test_check_failure_is_fail_fast(report_dir):
    runner = FakeRunner(check_exits={"tools/check_matrix.py": 1}, junit=_junit(passed=1))
    exit_code = _run(["--report-dir", str(report_dir)], runner)
    assert exit_code == 1
    assert not any(_is_pytest_call(entry) for entry in runner.commands)
    summary = _load(report_dir)
    assert summary["checks"]["status"] == "failed"
    assert summary["tests"]["status"] == "skipped"
    assert not (report_dir / "pytest.log").exists()


def test_check_timeout_fails(report_dir):
    runner = FakeRunner(check_timeouts={"tools/check_docs.py": 0})
    exit_code = _run(["--phase", "checks", "--report-dir", str(report_dir)], runner)
    assert exit_code == 1
    summary = _load(report_dir)
    assert summary["checks"]["items"][0]["status"] == "timed_out"


def test_missing_check_script_is_input_error(report_dir, monkeypatch):
    monkeypatch.setattr(ci, "CHECK_STEPS", (("缺失检查", ("tools/__missing_check__.py",)),))
    runner = FakeRunner()
    exit_code = _run(["--phase", "checks", "--report-dir", str(report_dir)], runner)
    assert exit_code == 2
    summary = _load(report_dir)
    assert summary["checks"]["status"] == "error"
    assert summary["checks"]["items"][0]["status"] == "missing_script"


def test_pytest_failure_exit_1(report_dir):
    runner = FakeRunner(pytest_exit=1, junit=_junit(passed=2, failed=1))
    exit_code = _run(["--phase", "tests", "--report-dir", str(report_dir)], runner)
    assert exit_code == 1
    summary = _load(report_dir)
    assert summary["tests"]["status"] == "failed"
    assert summary["counts"]["failed"] == 1
    assert summary["summary_line"].startswith("通过 2 项 ｜ 失败 1 项")


def test_pytest_zero_exit_with_junit_failure_is_failed(report_dir):
    """pytest 退出 0 但 junit 有 failure/error，不能当成功。"""
    runner = FakeRunner(pytest_exit=0, junit=_junit(passed=1, failed=1))
    exit_code = _run(["--phase", "tests", "--report-dir", str(report_dir)], runner)
    assert exit_code == 1
    summary = _load(report_dir)
    assert summary["tests"]["status"] == "failed"
    assert summary["counts"]["failed"] == 1
    assert summary["automated_suite_complete"] is False


def test_pytest_no_tests_collected_exit_2(report_dir):
    runner = FakeRunner(pytest_exit=5, junit=None)
    exit_code = _run(["--phase", "tests", "--report-dir", str(report_dir)], runner)
    assert exit_code == 2
    summary = _load(report_dir)
    assert summary["tests"]["status"] == "no_tests_collected"
    assert summary["summary_line"] is None
    assert "junit：缺失" in (report_dir / "summary.md").read_text(encoding="utf-8")


def test_pytest_zero_exit_but_junit_missing_is_input_error(report_dir):
    runner = FakeRunner(pytest_exit=0, junit=None)
    exit_code = _run(["--phase", "tests", "--report-dir", str(report_dir)], runner)
    assert exit_code == 2
    assert _load(report_dir)["tests"]["status"] == "junit_missing"


def test_stale_junit_is_removed_before_tests(report_dir):
    report_dir.mkdir(parents=True)
    (report_dir / "junit.xml").write_text(_junit(passed=1, failed=9), encoding="utf-8")
    runner = FakeRunner(pytest_exit=0, junit=None)  # 本次不生产 junit
    exit_code = _run(["--phase", "tests", "--report-dir", str(report_dir)], runner)
    assert exit_code == 2  # 旧 junit 未被当成当前成功
    assert _load(report_dir)["tests"]["status"] == "junit_missing"


def test_pytest_launch_error_exit_1(report_dir):
    runner = FakeRunner(pytest_exit=None, junit=None)
    exit_code = _run(["--phase", "tests", "--report-dir", str(report_dir)], runner)
    assert exit_code == 1
    assert _load(report_dir)["tests"]["status"] == "launch_error"


def test_pytest_timeout_exit_1(report_dir):
    runner = FakeRunner(pytest_exit=None, pytest_timeout=True, junit=None)
    exit_code = _run(["--phase", "tests", "--report-dir", str(report_dir)], runner)
    assert exit_code == 1
    assert _load(report_dir)["tests"]["status"] == "timed_out"


# ------------------------------------------------------------------ skip 与外部排除口径


def test_skips_counted_but_not_full_verification(report_dir):
    runner = FakeRunner(junit=_junit(passed=5, skipped=2))
    exit_code = _run(["--report-dir", str(report_dir)], runner)
    assert exit_code == 0
    summary = _load(report_dir)
    assert summary["counts"] == {"available": True, "total": 7, "passed": 5, "failed": 0, "skipped": 2}
    assert summary["summary_line"] == "通过 5 项 ｜ 失败 0 项 ｜ 跳过 2 项（含跳过，未全验）"
    assert summary["full_verification"] is False
    assert summary["automated_suite_complete"] is False
    markdown = (report_dir / "summary.md").read_text(encoding="utf-8")
    assert "（含跳过，未全验）" in markdown
    assert "full_verification：false" in markdown


def test_summary_contract_matches_verify_entry():
    assert ci.SUMMARY_FMT == verify_entry.SUMMARY_FMT
    assert ci.SKIP_HINT == verify_entry.SKIP_HINT
    counts = {"passed": 5, "failed": 0, "skipped": 2}
    assert ci.build_summary_line(counts) == verify_entry.build_summary(counts)
    counts = {"passed": 1, "failed": 0, "skipped": 0}
    assert ci.build_summary_line(counts) == verify_entry.build_summary(counts)


def test_krita_render_test_excluded_by_default(report_dir):
    runner = FakeRunner(junit=_junit(passed=1))
    exit_code = _run(["--phase", "tests", "--report-dir", str(report_dir)], runner)
    assert exit_code == 0
    command = runner.commands[0]["command"]
    assert "--ignore" in command
    assert command[command.index("--ignore") + 1] == "tests/test_krita_render.py"
    summary = _load(report_dir)
    assert summary["external_exclusions"]
    assert "test_krita_render.py" in summary["external_exclusions"][0]
    markdown = (report_dir / "summary.md").read_text(encoding="utf-8")
    assert "test_krita_render.py" in markdown
    assert "不是 skip" in markdown


def test_allow_krita_includes_render_test(report_dir):
    runner = FakeRunner(junit=_junit(passed=1))
    exit_code = _run(
        ["--phase", "tests", "--allow-krita", "--report-dir", str(report_dir)], runner
    )
    assert exit_code == 0
    assert "--ignore" not in runner.commands[0]["command"]
    summary = _load(report_dir)
    assert summary["external_exclusions"] == []
    assert summary["full_verification"] is False
    assert summary["automated_suite_complete"] is False  # 只跑了 tests，结构检查未执行


def test_automated_suite_complete_only_reports_automated_scope(report_dir):
    """all + allow-krita + 0 skip 时自动阶段完整，但 full_verification 仍是 false。"""
    runner = FakeRunner(junit=_junit(passed=4))
    exit_code = _run(
        ["--allow-krita", "--report-dir", str(report_dir)], runner
    )
    assert exit_code == 0
    summary = _load(report_dir)
    assert summary["external_exclusions"] == []
    assert summary["automated_suite_complete"] is True
    assert summary["full_verification"] is False
    markdown = (report_dir / "summary.md").read_text(encoding="utf-8")
    assert "full_verification：false" in markdown
    assert "automated_suite_complete：true" in markdown


# ------------------------------------------------------------------ checks 只读口径


def test_checks_are_existing_read_only_scripts():
    commands = ci.build_checks_commands()
    assert [name for name, _ in commands] == [name for name, _ in ci.CHECK_STEPS]
    assert len(commands) == 7
    for _name, command in commands:
        assert command[0] == sys.executable
        assert command[1] == "-B"
        script = command[2]
        assert script.startswith("tools/"), script
        assert (REPO_ROOT / script).is_file(), script
        assert "--check" in command or script in (
            "tools/check_matrix.py",
            "tools/check_ui_controls.py",
        )
        for forbidden in ("--build", "--stdout", "install", "git"):
            assert forbidden not in command, (script, forbidden)


def test_pytest_runs_with_workspace_temp_isolation(report_dir):
    """GUI/集成测试要求 TEMP/TMP/APPDATA/HOME/XDG 全落在任务隔离根内（开发指南 §9.1）。"""
    runner = FakeRunner(junit=_junit(passed=1))
    exit_code = _run(["--phase", "tests", "--report-dir", str(report_dir)], runner)
    assert exit_code == 0
    env = runner.commands[0]["env"]
    isolation_root = ci.PYTEST_TMP_DIR.parent
    names = (
        "TEMP", "TMP", "TMPDIR", "APPDATA", "LOCALAPPDATA", "HOME",
        "USERPROFILE", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
    )
    for name in names:
        assert name in env, name
        path = Path(env[name])
        assert path.is_relative_to(isolation_root), (name, path)
        assert path.is_dir(), name
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["QT_QPA_PLATFORM"] == "offscreen"  # 必须是平台名，不能是隔离目录路径
    assert env["PYTHONNOUSERSITE"] == "1"


def test_qt_platform_offscreen_is_literal(tmp_path):
    """QT_QPA_PLATFORM 必须是字面 offscreen，不能被目录映射写成路径。"""
    root = tmp_path / "isolation"
    env = ci._test_env(root)
    assert env["QT_QPA_PLATFORM"] == "offscreen"
    assert not (root / "offscreen").exists()
    assert env["PYTHONNOUSERSITE"] == "1"


def test_real_subprocess_tempfile_lands_in_isolation_root(tmp_path):
    """真实子进程 probe：tempfile.gettempdir() 必须落在任务隔离根内（非 monkeypatch）。"""
    root = tmp_path / "isolation"
    root.mkdir()
    env = ci._test_env(root)
    probe = subprocess.run(
        [sys.executable, "-B", "-c", "import tempfile; print(tempfile.gettempdir())"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert probe.returncode == 0, probe.stderr
    reported = Path(probe.stdout.strip()).resolve()
    assert reported.is_relative_to(root.resolve()), reported


def test_real_qt_standard_paths_land_in_isolation_root(tmp_path):
    """真实子进程 probe：Qt Temp/AppConfig 落点必须在任务隔离根内。"""
    pytest.importorskip("PySide6", reason="未安装 PySide6，跳过真实 Qt 路径 probe")
    root = tmp_path / "isolation"
    root.mkdir()
    env = ci._test_env(root)
    code = (
        "import json, os, tempfile\n"
        "from PySide6.QtCore import QCoreApplication, QStandardPaths\n"
        "_app = QCoreApplication([])\n"
        "print(json.dumps({\n"
        "  'temp': QStandardPaths.writableLocation(QStandardPaths.TempLocation),\n"
        "  'config': QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation),\n"
        "  'tempfile': tempfile.gettempdir(),\n"
        "  'qt_platform': os.environ.get('QT_QPA_PLATFORM'),\n"
        "}))\n"
    )
    probe = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert probe.returncode == 0, probe.stderr[-2000:]
    payload = json.loads(probe.stdout.strip().splitlines()[-1])
    assert payload["qt_platform"] == "offscreen", payload["qt_platform"]
    for key in ("temp", "config", "tempfile"):
        reported = Path(payload[key]).resolve()
        assert reported.is_relative_to(root.resolve()), (key, reported)


def test_checks_child_env_is_utf8(tmp_path):
    runner = FakeRunner()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(ci, "CHECK_STEPS", (("单条", ("tools/check_matrix.py",)),))
        ci.run_checks(tmp_path / "checks-report", runner)
    env = runner.commands[0]["env"]
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"


def test_dependency_versions_recorded():
    versions = ci._dependency_versions()
    assert set(versions) == {"numpy", "Pillow", "PySide6", "pytest", "ruff"}
    assert versions["pytest"]  # 跑单测时 pytest 必然可用
    for value in versions.values():
        assert value is None or isinstance(value, str)


def test_report_dir_defaults_and_isolation():
    assert ci.DEFAULT_REPORT_DIR == REPO_ROOT / ".tmp" / "ci" / "reports"
    assert ci.PYTEST_TMP_DIR == REPO_ROOT / ".tmp" / "ci" / "pytest-tmp"
    assert ci.PYTEST_TMP_DIR.parent == ci.DEFAULT_REPORT_DIR.parent
    assert not ci.PYTEST_TMP_DIR.is_relative_to(ci.DEFAULT_REPORT_DIR)
    assert ci.resolve_report_dir(None) == ci.DEFAULT_REPORT_DIR
    assert ci.resolve_report_dir(".tmp/ci/unit-report") == (
        REPO_ROOT / ".tmp" / "ci" / "unit-report"
    ).resolve()
