"""任务165：仅在 .tmp/165/archive-agent 内合成仓库，不读个人Git配置、不提交。"""
from __future__ import annotations

import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote

import pytest

from tools import render_archive_index as archive


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = ROOT / ".tmp" / "165" / "archive-agent"
SCRIPT = ROOT / "tools" / "render_archive_index.py"


@pytest.fixture
def work_area(monkeypatch):
    # 不用 tmp_path/tmp_path_factory，连 tempfile 的显式目录也固定在本任务白名单内。
    WORK.mkdir(parents=True, exist_ok=True)
    area = pathlib.Path(tempfile.mkdtemp(prefix="visibility-", dir=WORK))
    locations = {
        "TEMP": "temp", "TMP": "temp", "TMPDIR": "temp", "HOME": "home",
        "USERPROFILE": "home", "APPDATA": "config/roaming", "LOCALAPPDATA": "config/local",
        "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache", "XDG_DATA_HOME": "data",
        "XDG_STATE_HOME": "state", "PYTHONPYCACHEPREFIX": "cache/pyc",
        "PIP_CACHE_DIR": "cache/pip", "PYINSTALLER_CONFIG_DIR": "config/pyinstaller",
        "MPLCONFIGDIR": "config/matplotlib", "GIT_TEMPLATE_DIR": "git-template",
    }
    for key, relative in locations.items():
        target = area / relative
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(key, str(target))
    monkeypatch.setattr(tempfile, "tempdir", str(area / "temp"))
    for key in (
        "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX",
    ):
        monkeypatch.delenv(key, raising=False)
    empty_config = area / "empty.gitconfig"
    empty_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    monkeypatch.setenv("GIT_ATTR_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "0")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(area))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.setenv("PYTHONUTF8", "1")
    # 不清理合成仓库或旧失败日志，便于根会话回查原件。
    return area


def _git(repo, *args):
    if shutil.which("git") is None:
        pytest.fail("真实Git合成仓库测试需要已安装Git；Git缺失是环境失败，不跳过或安装工具")
    result = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.autocrlf=false", *args],
        cwd=repo, capture_output=True, encoding="utf-8", errors="replace", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return result


def _write(repo, name, text):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _stage(repo, *names):
    _git(repo, "add", "--", *names)


@pytest.fixture
def repo(work_area, monkeypatch):
    path = work_area / "仓库 空间"
    path.mkdir()
    _git(path, "init", "--quiet", "--template=")
    (path / "docs" / "验收报告").mkdir(parents=True)
    (path / "docs" / "研究报告").mkdir(parents=True)
    monkeypatch.chdir(path)
    return path


def _row_names(text):
    result = []
    for line in text.splitlines():
        if line.startswith("| ["):
            match = re.search(r"\]\(<(.*?)>\)", line)
            assert match, line
            result.append(pathlib.PurePosixPath(unquote(match.group(1))).name)
    return result


def _legacy_sample(area):
    directory = area / "旧模式"
    directory.mkdir()
    for name, text in {
        "10-报告.md": "# 十号 | 标题\n",
        "02A-报告.md": "无 H1\n## 不是首级\n",
        "02-报告.md": "说明\n# 二号\n# 第二个 H1 不使用\n",
        "实验-报告.md": "# 实验\n",
        "README.md": "# 不收录\n",
        "INDEX.md": "旧索引\n",
        "子目录/01-报告.md": "# 不递归\n",
    }.items():
        _write(directory, name, text)
    expected = "\n".join([
        "# 旧模式索引", "",
        "> **本文件由脚本生成，禁止手改**：重新生成 `python tools/render_archive_index.py`；",
        "> 校验 `python tools/render_archive_index.py --check`。", ">",
        "> **证据归档，非上手必读**：每份报告是一次性任务自检/验收记录，只作历史追溯。",
        "> 现行映射结论看 `docs/parameter-mapping.md`，数据真相看 `docs/数据/*.json`。", "",
        "| 文件 | 标题 |", "|---|---|",
        "| `02-报告.md` | 二号 |", "| `02A-报告.md` | 02A-报告 |",
        "| `10-报告.md` | 十号 \\| 标题 |", "| `实验-报告.md` | 实验 |", "", "共 4 份。", "",
    ])
    return directory, expected


def test_legacy_render_and_cli_are_byte_compatible_without_git(work_area, monkeypatch, capsys):
    directory, expected = _legacy_sample(work_area)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("旧模式不得查询Git或偷偷变成公开模式")

    monkeypatch.setattr(archive, "_git_output", forbidden)
    assert archive.render(directory) == expected
    assert archive.render(directory, tracked_only=False) == expected
    assert archive.main(["--dir", str(directory), "--stdout"]) == 0
    assert capsys.readouterr().out == expected
    destination = work_area / "legacy-result.md"
    assert archive.main(["--dir", str(directory), "--md", str(destination)]) == 0
    assert destination.read_bytes() == expected.encode("utf-8")
    capsys.readouterr()
    assert archive.main(["--dir", str(directory), "--md", str(destination), "--check"]) == 0
    assert capsys.readouterr().out == f"{destination} 与目录同步\n"
    destination.write_text("旧的失败样例\n", encoding="utf-8")
    before = destination.stat().st_mtime_ns
    assert archive.main(["--dir", str(directory), "--md", str(destination), "--check"]) == 1
    assert capsys.readouterr().err == (
        f"{destination} 与目录不同步，请重跑 python tools/render_archive_index.py\n"
    )
    assert destination.read_text(encoding="utf-8") == "旧的失败样例\n"
    assert destination.stat().st_mtime_ns == before


@pytest.mark.parametrize("folder", ["验收报告", "研究报告"])
def test_mixed_tracked_public_private_and_untracked_reports(repo, folder):
    prefix = f"docs/{folder}/"
    allowed = {
        "10-报告.md": "# 十号\n",
        "2A-报告.md": "# 二号补位\n",
        "2-报告.md": "# 二号\n",
        "162-报告.md": "# 162 已授权摘要\n正文提到原始证据与内部算法，不是本文件可见性声明。\n",
        "162A-报告.md": "# 162A 已授权摘要\n## 原始证据\n这里只引用已脱敏的历史记录。\n",
        "187-公开摘要.md": "---\nvisibility: public\n---\n# 单独授权的摘要，不按编号拒绝\n",
        "188-报告.md": "# 后续已授权报告\n",
        "实验-报告.md": "# 实验记录\n",
    }
    blocked = {
        "11-元信息.md": "---\nvisibility: internal\n---\n# 受跟踪也不公开\n",
        "12-原始记录.md": "# 逐条记录\n> 文档类型：原始证据\n",
        "163-报告.md": "# 163\n> 本地任务报告，不提交、不进入公开索引。日期：示例。\n",
        "164-报告.md": "# 164\n> 本报告为本次执行后产生的本地验收材料，**不提交、不写公共INDEX**。\n",
        "187-报告.md": "# 187\n> 结论：此为本地预写骨架，不提交公开仓库。\n",
        "内部规划-计划.md": "# 规划\n",
        "原始证据-日志.md": "# 原件\n",
        "raw-evidence-capture.md": "# 原始捕获\n",
        "README.md": "# 目录说明\n",
        "INDEX.md": "旧索引\n",
        "归档/01-原始记录.md": "# 嵌套文件即使跟踪也不递归\n",
    }
    names = []
    for name, text in {**allowed, **blocked}.items():
        _write(repo, prefix + name, text)
        names.append(prefix + name)
    _write(repo, prefix + "3-未跟踪私密.md", "# 不可出现在公开输出的未跟踪秘密\n")
    (repo / prefix / "4-未跟踪坏编码.md").write_bytes(b"\xff\xfe")
    _write(repo, "docs/工作流程/规划.md", "# 内部规划\nvisibility: public\n")
    _stage(repo, *names, "docs/工作流程/规划.md")
    text = archive.render(repo / prefix, tracked_only=True)
    assert _row_names(text) == [
        "2-报告.md", "2A-报告.md", "10-报告.md", "162-报告.md", "162A-报告.md",
        "187-公开摘要.md", "188-报告.md", "实验-报告.md",
    ]
    assert "共 8 份。\n" in text
    assert "| 文件 | 标题 |\n|---|---|" in text
    assert "未跟踪秘密" not in text
    assert "受跟踪也不公开" not in text
    assert str(repo) not in text and repo.as_posix() not in text
    assert archive.render(repo / prefix, tracked_only=True) == text


@pytest.mark.parametrize("declaration", [
    "---\nvisibility: internal\n---\n# 报告\n",
    "---\npublic: false\n---\n# 报告\n",
    "---\npublish: no\n---\n# 报告\n",
    "---\nvisibility: confidential\n---\n# 报告\n",
    "---\nvisibility: 待审查\n---\n# 报告\n",
    "# 报告\n> **可见性**：仅本地\n",
    "# 报告\n> 公开范围：不公开\n",
    "# 报告\n> 报告类型：预写报告\n",
    "# 报告\n> 本文件是原始证据\n",
    "# 报告（内部）\n",
    "# 报告（内部，仅本地）\n",
    "# 报告\n> 内部资料，仅本地，不公开\n",
    "---\nprivate: true\n---\n# 报告\n",
    "---\nraw_evidence: true\n---\n# 报告\n",
    "---\nstatus: draft\n---\n# 报告\n",
    "# 报告\n仅本地\n",
    "# 报告\n<!-- archive-visibility: private -->\n",
    "# 报告\n## 证据\n<!-- index_visibility: local-only -->\n",
    "# 报告\n<!--\n类型：原始证据\n-->\n",
    "---\npublic: true\n---\n# 报告\n<!-- visibility: internal -->\n",
])
def test_explicit_private_metadata_blocks_even_tracked_reports(repo, declaration):
    name = "docs/验收报告/20-显式声明.md"
    _write(repo, name, declaration)
    _stage(repo, name)
    text = archive.render(repo / "docs/验收报告", tracked_only=True)
    assert _row_names(text) == []
    assert "共 0 份。\n" in text


@pytest.mark.parametrize("body", [
    "# 公开历史报告\n正文包含原始证据、内部实现、预写示例这些讨论词。\n",
    "# 历史记录（含原始证据）\n## 原始证据\n这里是已经审查的历史数据。\n",
    "# 原始证据的公开审查摘要\n这是公开分析，不是原始证据类型声明。\n",
    "# 报告\n> 本报告解释内部算法并引用原始证据。\n",
    "# 报告\n> 原始证据：已脱敏的历史记录链接。\n",
    "# 报告\n> 本报告不是原始证据，而是审查后的公开摘要。\n",
    "---\nvisibility: public\n---\n# 公开报告\n",
    "---\nprivate: false\n---\n# 公开报告\n",
    "\ufeff---\npublic: true\n---\n# 带BOM的公开报告\n",
    "# 示例说明\n```markdown\nvisibility: internal\n<!-- visibility: private -->\n```\n",
    "# 示例说明\n~~~yaml\npublic: false\n~~~\n",
    "# 报告\n## 字段示例\nvisibility: internal\n普通正文没有元声明效力。\n",
])
def test_body_keywords_and_fenced_examples_do_not_hide_public_history(repo, body):
    name = "docs/研究报告/历史-报告.md"
    _write(repo, name, body)
    _stage(repo, name)
    assert _row_names(archive.render(repo / "docs/研究报告", tracked_only=True)) == ["历史-报告.md"]


def test_reserved_prewrite_name_does_not_become_public_by_staging_or_public_true(repo):
    name = "docs/验收报告/187-报告.md"
    _write(repo, name, "---\npublic: true\n---\n# 预写骨架\n")
    _stage(repo, name)
    assert _row_names(archive.render(repo / "docs/验收报告", tracked_only=True)) == []


def test_chinese_spaces_safe_links_table_escaping_and_custom_command(repo, capsys):
    name = "docs/研究报告/02-中文 空格 [甲](乙)#.md"
    _write(repo, name, "# 中文 | [链接](bad) `代码` <script> & 标题\n")
    _stage(repo, name)
    destination = repo / "输出 空间" / "研究 索引.md"
    destination.parent.mkdir()
    args = ["--tracked-only", "--dir", "docs/研究报告", "--md", "输出 空间/研究 索引.md"]
    assert archive.main([*args, "--stdout"]) == 0
    text = capsys.readouterr().out
    assert _row_names(text) == [pathlib.Path(name).name]
    row = next(line for line in text.splitlines() if line.startswith("| ["))
    assert row.count("|") == 3
    assert "&#124;" in row and "&lt;script&gt;" in row and "&amp;" in row
    assert "&#96;" in row and r"\[链接\]" in row
    uri = re.search(r"\]\(<(.*?)>\)", row).group(1)
    assert "%20" in uri and "%E4%B8%AD" in uri
    assert "%5B" in uri and "%28" in uri and "%23" in uri
    assert (destination.parent / unquote(uri)).resolve() == (repo / name).resolve()
    commands = re.findall(r"` (python .*?) `", text)
    assert len(commands) == 2
    expected = ["python", "tools/render_archive_index.py", *args]
    assert shlex.split(commands[0]) == expected
    assert shlex.split(commands[1]) == [*expected, "--check"]
    assert not destination.exists()
    assert archive.main(args) == 0
    capsys.readouterr()
    assert destination.read_bytes() == text.encode("utf-8")
    assert archive.main([*args, "--check"]) == 0


def test_public_check_is_read_only_for_match_mismatch_missing_and_stdout(repo, monkeypatch, capsys):
    _write(repo, "docs/验收报告/01-报告.md", "# 公开报告\n")
    _stage(repo, "docs/验收报告/01-报告.md")
    destination = repo / "docs/验收报告/INDEX.md"
    text = archive.render(destination.parent, tracked_only=True, md_path=destination)
    destination.write_text(text, encoding="utf-8", newline="\n")
    target_before = (destination.read_bytes(), destination.stat().st_mtime_ns)
    index = repo / ".git/index"
    index_before = (index.read_bytes(), index.stat().st_mtime_ns)
    original_write = pathlib.Path.write_text

    def no_write(*_args, **_kwargs):
        raise AssertionError("--check/--stdout 不允许写文件")

    monkeypatch.setattr(pathlib.Path, "write_text", no_write)
    assert archive.main(["--tracked-only", "--check"]) == 0
    capsys.readouterr()
    assert archive.main(["--tracked-only", "--stdout"]) == 0
    assert capsys.readouterr().out == text
    assert (destination.read_bytes(), destination.stat().st_mtime_ns) == target_before
    original_write(destination, "保留的旧失败内容\n", encoding="utf-8")
    stale_before = (destination.read_bytes(), destination.stat().st_mtime_ns)
    assert archive.main(["--tracked-only", "--check"]) == 1
    assert "--tracked-only" in capsys.readouterr().err
    assert (destination.read_bytes(), destination.stat().st_mtime_ns) == stale_before
    missing = "docs/验收报告/缺少索引.md"
    assert archive.main(["--tracked-only", "--md", missing, "--check"]) == 2
    assert not (repo / missing).exists()
    assert (index.read_bytes(), index.stat().st_mtime_ns) == index_before


def test_custom_tracked_destination_is_not_indexed_as_a_report(repo):
    _write(repo, "docs/验收报告/01-报告.md", "# 正常报告\n")
    destination = _write(repo, "docs/验收报告/公开目录.md", "# 已有自定义索引\n")
    _stage(repo, "docs/验收报告/01-报告.md", "docs/验收报告/公开目录.md")
    text = archive.render(destination.parent, tracked_only=True, md_path=destination)
    assert _row_names(text) == ["01-报告.md"]


@pytest.mark.parametrize("failure", ["missing", "denied", "timeout", "nonzero"])
def test_git_failures_exit_two_without_fallback_or_output(repo, monkeypatch, capsys, failure):
    _write(repo, "docs/验收报告/01-未跟踪.md", "# 绝不能回退泄漏的秘密\n")
    destination = _write(repo, "docs/验收报告/INDEX.md", "保留原索引\n")
    before = (destination.read_bytes(), destination.stat().st_mtime_ns)

    def fail(command, **_kwargs):
        if failure == "missing":
            raise FileNotFoundError("synthetic missing git")
        if failure == "denied":
            raise PermissionError("synthetic denied git")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 15)
        return subprocess.CompletedProcess(command, 128, b"", b"synthetic failure: not public")

    monkeypatch.setattr(archive.subprocess, "run", fail)
    assert archive.main(["--tracked-only", "--stdout"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "Git" in captured.err
    assert "绝不能回退" not in captured.err and "synthetic" not in captured.err
    assert archive.main(["--tracked-only"]) == 2
    assert (destination.read_bytes(), destination.stat().st_mtime_ns) == before


def test_ls_files_failure_also_fails_closed(repo, monkeypatch, capsys):
    name = "docs/验收报告/01-报告.md"
    _write(repo, name, "# 清单失败时不输出任何报告\n")
    _stage(repo, name)
    real_run = archive.subprocess.run
    calls = []

    def fail_listing(command, **kwargs):
        calls.append(command)
        if "ls-files" in command:
            return subprocess.CompletedProcess(command, 128, b"", b"listing denied")
        return real_run(command, **kwargs)

    monkeypatch.setattr(archive.subprocess, "run", fail_listing)
    assert archive.main(["--tracked-only", "--stdout"]) == 2
    assert capsys.readouterr().out == ""
    assert any("rev-parse" in call for call in calls)
    assert any("ls-files" in call for call in calls)


def test_nonrepository_actual_cli_exits_two_without_output(work_area):
    directory = work_area / "非仓库"
    _write(directory, "docs/验收报告/01-秘密.md", "# 非仓库秘密\n")
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--tracked-only", "--stdout"],
        cwd=directory, capture_output=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 2
    assert result.stdout == "" and "Git" in result.stderr
    assert not (directory / "docs/验收报告/INDEX.md").exists()


@pytest.mark.parametrize("directory", [
    "docs/工作流程", "docs/优化提示词", "docs/归档", "docs/验收报告/归档", ".tmp/原始证据",
])
def test_public_input_directory_allowlist_is_not_bypassed_by_tracking(repo, capsys, directory):
    name = directory + "/01-报告.md"
    _write(repo, name, "---\nvisibility: public\n---\n# 跟踪并不改变目录政策\n")
    _stage(repo, name)
    assert archive.main(["--tracked-only", "--dir", directory, "--stdout"]) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("which", ["source", "destination", "git-metadata"])
def test_out_of_repository_paths_and_git_metadata_are_errors(repo, work_area, capsys, which):
    _write(repo, "docs/验收报告/01-报告.md", "# 正常报告\n")
    _stage(repo, "docs/验收报告/01-报告.md")
    external = _write(work_area, "外部/报告.md", "# 不能泄漏的仓库外报告\n")
    args = ["--tracked-only", "--stdout"]
    if which == "source":
        args.extend(["--dir", str(external.parent)])
    elif which == "destination":
        args.extend(["--md", str(external)])
    else:
        args.extend(["--md", ".git/公开索引.md"])
    before = (external.read_bytes(), external.stat().st_mtime_ns)
    assert archive.main(args) == 2
    assert capsys.readouterr().out == ""
    assert (external.read_bytes(), external.stat().st_mtime_ns) == before


def test_tracked_symlink_index_mode_does_not_read_a_private_target(repo, monkeypatch, capsys):
    _write(repo, "private-target.md", "# 链接目标秘密\n")
    name = "docs/验收报告/01-链接.md"
    link = _write(repo, name, "private-target.md\n")
    _stage(repo, name)
    # 合成Git真实120000模式，不要求Windows创建符号链接的额外权限。
    oid = _git(repo, "hash-object", name).stdout.strip()
    _git(repo, "update-index", "--cacheinfo", "120000", oid, name)
    real_read = pathlib.Path.read_text

    def guarded_read(path, *args, **kwargs):
        if path == link:
            raise AssertionError("符号链接模式不得读取正文")
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", guarded_read)
    assert archive.main(["--tracked-only", "--stdout"]) == 2
    assert capsys.readouterr().out == ""


def test_resolved_output_alias_cannot_escape_the_repository(repo, work_area, monkeypatch, capsys):
    target = _write(work_area, "外部保留.md", "保留外部原件\n")
    destination = repo / "docs/验收报告/INDEX.md"
    real_resolve = pathlib.Path.resolve

    def output_alias(path, *args, **kwargs):
        # 合成符号链接/目录联接的resolve结果，不创建任何特权文件系统对象。
        if path.absolute() == destination:
            return target
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "resolve", output_alias)
    assert archive.main(["--tracked-only", "--check"]) == 2
    assert capsys.readouterr().out == ""
    assert target.read_text(encoding="utf-8") == "保留外部原件\n"


def test_bad_utf8_tracked_report_is_an_error_not_a_partial_index(repo, capsys):
    path = repo / "docs/验收报告/01-坏编码.md"
    path.write_bytes(b"\xff\xfe")
    _stage(repo, "docs/验收报告/01-坏编码.md")
    assert archive.main(["--tracked-only", "--stdout"]) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("record", [
    b"broken\0", b"100644 0000000 0\t../outside.md\0",
    "100644 0000000 2\tdocs/验收报告/01-报告.md\0".encode("utf-8"),
])
def test_unprovable_git_index_records_fail_closed(repo, monkeypatch, capsys, record):
    real_output = archive._git_output

    def damaged_index(cwd, *args):
        if "ls-files" in args:
            return record
        return real_output(cwd, *args)

    monkeypatch.setattr(archive, "_git_output", damaged_index)
    assert archive.main(["--tracked-only", "--stdout"]) == 2
    assert capsys.readouterr().out == ""
