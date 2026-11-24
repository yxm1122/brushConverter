# -*- coding: utf-8 -*-
"""任务 162A：增量隐私入口的回归测试。

全部使用 .tmp/162A/incremental/ 下的合成 git 仓库，不触碰本仓库提交历史与真实身份；
敏感样例一律在运行时拼接，测试源码本身不出现真实路径/邮箱/令牌。
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "privacy_scan.py"
WORK = REPO / ".tmp" / "162A" / "incremental"
PY = sys.executable

# 敏感样例：运行时拼接，源码里不出现完整家目录（否则扫描器自己会被自己命中）。
HOME_LINE = "home = " + "C:" + "/" + "Users" + "/" + "alice" + "/AppData"
MESSAGE_HOME = "debug " + "C:" + "/" + "Users" + "/" + "bob" + "/tmp"

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _load_scanner():
    spec = importlib.util.spec_from_file_location("privacy_scan_162a_incremental", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses 需要模块已在 sys.modules 里（否则解析注解时会 AttributeError）。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _patch(module, repo: Path) -> None:
    """把扫描器指向合成仓库；已公开引用置空，避免依赖真实 tag/远端。"""
    module.REPO_ROOT = repo
    module.SCAN_ROOT = repo
    module.PUBLISHED_TAG = "refs/tags/__no_published_tag__"
    module.PUSHED_REF = "refs/remotes/__no_remote__"
    module.PUBLISHED_COMMITS.clear()
    module.RULES = module.build_rules(module.build_identity_names(), repo)


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    merged = dict(os.environ)
    merged.update(env or {})
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8", env=merged
    )
    assert proc.returncode == 0, "git %s 失败：%s" % (" ".join(args), proc.stderr)
    return proc.stdout


def _rmtree(path: Path) -> None:
    """清掉上一次失败留下的合成仓库；git 对象在 Windows 上是只读的。"""
    if not path.exists():
        return

    def _force(func, target, _exc):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_force)


def _new_repo(name: str) -> Path:
    repo = WORK / name
    if repo.exists():
        _rmtree(repo)
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "brushConverter contributors")
    _git(repo, "config", "user.email", "contributors@brushconverter.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def _commit(
    repo: Path,
    message: str,
    files: dict[str, str | bytes],
    *,
    author: tuple[str, str] | None = None,
    committer: tuple[str, str] | None = None,
) -> str:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    env: dict = {}
    if author:
        env["GIT_AUTHOR_NAME"], env["GIT_AUTHOR_EMAIL"] = author
    if committer:
        env["GIT_COMMITTER_NAME"], env["GIT_COMMITTER_EMAIL"] = committer
    _git(repo, "commit", "-q", "-m", message, env=env)
    return _git(repo, "rev-parse", "HEAD").strip()


def _scan(module, repo: Path, *argv: str, out_name: str) -> tuple[int, dict | None]:
    _patch(module, repo)
    out = WORK / out_name
    if out.exists():
        out.unlink()
    code = module.main([*argv, "--quiet", "--json", str(out)])
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return code, data


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [PY, "-B", str(TOOL), *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def test_help_shows_daily_and_release_gate_sections() -> None:
    proc = _run_cli("--help")
    assert proc.returncode == 0
    text = proc.stdout
    assert "--since-baseline" in text
    assert "--artifact" in text
    assert "日常增量" in text
    assert "发布前全量" in text
    assert "186" in text
    assert "4 基线阻塞" in text


def test_selftest_still_passes() -> None:
    proc = _run_cli("--selftest")
    assert proc.returncode == 0, proc.stderr
    assert "自检通过" in proc.stdout


def test_incremental_history_finds_deleted_line() -> None:
    repo = _new_repo("history")
    base = _commit(repo, "base", {"base.txt": "clean\n"})
    added = _commit(repo, "add secret", {"secret.txt": HOME_LINE + "\n"})
    _git(repo, "rm", "-q", "secret.txt")
    _git(repo, "commit", "-q", "-m", "delete secret")

    module = _load_scanner()
    code, data = _scan(module, repo, "--since-baseline", base, out_name="history.json")
    assert code == 0
    assert data is not None
    assert data["scan_mode"]["kind"] == "incremental"
    assert data["scan_mode"]["release_gate"] is False
    assert data["history"]["commits"] == 2
    text = module.render_text(data, 10)
    assert "日常增量" in text and "186" in text
    assert "允许进入发布" not in text
    # 日常增量不跑全量覆盖清单，也不该把「没扫到的目录」判成覆盖缺失。
    assert data["coverage"] == []
    matched = [h for h in data["hits"] if h["rule"] == "path_home_dir"]
    # 引入敏感行、后来又被删掉的提交仍然要被抓到。
    assert any(h["commit"] == added and "secret.txt" in h["location"] for h in matched)
    # 基线之前的提交不在增量范围里。
    assert not any(h["commit"] == base for h in data["hits"])


def test_incremental_history_scans_author_committer_and_message() -> None:
    repo = _new_repo("metadata")
    base = _commit(repo, "base", {"base.txt": "clean\n"})
    author = ("Alice Example", "alice" + "@" + "corp.example")
    committer = ("Bob Example", "bob" + "@" + "corp.example")
    _commit(repo, "msg " + MESSAGE_HOME, {"x.txt": "ok\n"}, author=author, committer=committer)

    module = _load_scanner()
    code, data = _scan(module, repo, "--since-baseline", base, out_name="metadata.json")
    assert code == 0
    assert data is not None
    identity_hits = [h for h in data["hits"] if h["rule"] == "meta_commit_identity"]
    assert len(identity_hits) == 2  # 作者 + 提交者各一条
    assert all(h["severity"] == "block" for h in identity_hits)
    assert any(h["rule"] == "meta_commit_message:path_home_dir" for h in data["hits"])


def test_invalid_baseline_exits_4(capsys) -> None:
    repo = _new_repo("bad-base")
    _commit(repo, "base", {"base.txt": "clean\n"})
    module = _load_scanner()
    _patch(module, repo)
    code = module.main(["--since-baseline", "no-such-ref-162a", "--quiet"])
    assert code == 4
    err = capsys.readouterr().err
    assert "基线" in err
    assert "未执行任何扫描" in err


def test_baseline_must_be_ancestor_of_head(capsys) -> None:
    repo = _new_repo("not-ancestor")
    _commit(repo, "base", {"base.txt": "clean\n"})
    _git(repo, "checkout", "-q", "-b", "side")
    side = _commit(repo, "side", {"side.txt": "side\n"})
    _git(repo, "checkout", "-q", "main")

    module = _load_scanner()
    _patch(module, repo)
    code = module.main(["--since-baseline", side, "--quiet"])
    assert code == 4
    assert "祖先" in capsys.readouterr().err


def test_baseline_blocks_when_other_ref_has_new_commits(capsys) -> None:
    repo = _new_repo("other-ref")
    base = _commit(repo, "base", {"base.txt": "clean\n"})
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "side commit", {"side.txt": "clean\n"})
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "main commit", {"main.txt": "clean\n"})

    module = _load_scanner()
    _patch(module, repo)
    code = module.main(["--since-baseline", base, "--quiet"])
    assert code == 4
    err = capsys.readouterr().err
    assert "其它引用" in err or "引用集合" in err


def test_incremental_worktree_scans_changed_and_untracked() -> None:
    repo = _new_repo("worktree")
    base = _commit(repo, "base", {"old.txt": HOME_LINE + "\n", "notes.txt": "clean\n"})
    (repo / "notes.txt").write_text("changed\n" + HOME_LINE + "\n", encoding="utf-8")
    (repo / "pending.txt").write_text("pending\n" + HOME_LINE + "\n", encoding="utf-8")
    (repo / "pending.png").write_bytes(PNG_1PX)

    module = _load_scanner()
    code, data = _scan(module, repo, "--since-baseline", base, out_name="worktree.json")
    assert code == 0
    assert data is not None
    locations = [h["location"] for h in data["hits"] if h["rule"] == "path_home_dir"]
    assert any(loc.startswith("notes.txt:") for loc in locations)
    assert any(loc.startswith("pending.txt:") for loc in locations)
    # 基线里已有、但基线后没动过的文件不重扫：证明增量没有偷跑成全量。
    assert not any(loc.startswith("old.txt:") for loc in locations)
    assert not any(h["commit"] == base for h in data["hits"])

    pending = [b for b in data["binaries"] if b["path"] == "pending.png"]
    assert pending
    assert pending[0].get("manual_review", "").startswith("待人工")


def test_default_full_scan_still_covers_all_history() -> None:
    repo = _new_repo("full-default")
    base = _commit(repo, "base", {"old.txt": HOME_LINE + "\n"})
    _commit(repo, "later", {"later.txt": "clean\n"})

    module = _load_scanner()
    code, data = _scan(module, repo, out_name="full.json")
    assert code == 0
    assert data is not None
    assert data["scan_mode"]["kind"] == "full"
    assert data["scan_mode"]["release_gate"] is True
    assert data["history"]["commits"] == 2
    text = module.render_text(data, 10)
    assert "发布前全量" in text
    assert any(h["commit"] == base and h["rule"] == "path_home_dir" for h in data["hits"])
    # 发布前全量保留原来的覆盖清单门（合成仓库没有 docs/数据 等目录，会如实报覆盖缺失）。
    assert data["coverage"] and any(row["files"] == 0 for row in data["coverage"])
    assert data["counts"]["block"] >= 1
    assert data["verdict"] == "不允许发布：block 未清零"
