# -*- coding: utf-8 -*-
"""任务 162A：--artifact 显式候选的回归测试。

覆盖：被 .gitignore 忽略的 ZIP 仍被纳入、坏路径/越界拒绝、ZIP 穿越与解压炸弹防护、
二进制/图片只登记待人工（不写成已通过）、限额截断如实报告。

所有合成仓库与 ZIP 都放在 .tmp/162A/artifacts/，不写系统临时目录，也不解压到外部。
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "privacy_scan.py"
WORK = REPO / ".tmp" / "162A" / "artifacts"
PY = sys.executable

HOME_LINE = "home = " + "C:" + "/" + "Users" + "/" + "alice" + "/AppData"

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _load_scanner():
    spec = importlib.util.spec_from_file_location("privacy_scan_162a_artifacts", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses 需要模块已在 sys.modules 里（否则解析注解时会 AttributeError）。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _patch(module, repo: Path) -> None:
    module.REPO_ROOT = repo
    module.SCAN_ROOT = repo
    module.PUBLISHED_TAG = "refs/tags/__no_published_tag__"
    module.PUSHED_REF = "refs/remotes/__no_remote__"
    module.PUBLISHED_COMMITS.clear()
    module.RULES = module.build_rules(module.build_identity_names(), repo)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8"
    )
    assert proc.returncode == 0, "git %s 失败：%s" % (" ".join(args), proc.stderr)
    return proc.stdout


def _commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


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
    # 基线提交里放一个受跟踪的敏感文件：artifact-only 不应顺手做全量工作区扫描。
    _commit(
        repo,
        "base",
        {
            ".gitignore": "*.zip\ncand/\n",
            "secret.txt": HOME_LINE + "\n",
        },
    )
    return repo


def _scan(module, repo: Path, *argv: str, out_name: str) -> tuple[int, dict | None]:
    _patch(module, repo)
    out = WORK / out_name
    if out.exists():
        out.unlink()
    code = module.main([*argv, "--quiet", "--json", str(out)])
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return code, data


def _make_zip(path: Path, entries: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def test_ignored_zip_is_included_and_text_scanned() -> None:
    repo = _new_repo("ignored-zip")
    zip_path = repo / "candidate.zip"
    _make_zip(zip_path, {"inner.txt": HOME_LINE + "\n"})
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "candidate.zip"], cwd=str(repo)
    )
    assert ignored.returncode == 0  # 先证明它确实被 .gitignore 忽略

    module = _load_scanner()
    code, data = _scan(module, repo, "--artifact", str(zip_path), out_name="ignored.json")
    assert code == 0
    assert data is not None
    assert data["scan_mode"]["kind"] == "artifact"
    assert data["scan_mode"]["release_gate"] is False
    assert data["artifacts"]["files"] == 1
    hits = [(h["rule"], h["location"]) for h in data["hits"]]
    assert any(
        rule == "path_home_dir" and "candidate.zip!inner.txt" in loc for rule, loc in hits
    )
    # artifact-only 不触发全量工作区扫描：受跟踪的 secret.txt 不应出现。
    assert not any("secret.txt" in loc for _, loc in hits)
    text = module.render_text(data, 10)
    assert "显式候选" in text and "不代表已通过" in text


def test_bad_paths_are_rejected_with_exit_2(capsys) -> None:
    repo = _new_repo("bad-path")
    module = _load_scanner()
    _patch(module, repo)

    missing = repo / "missing.zip"
    assert module.main(["--artifact", str(missing), "--quiet"]) == 2
    assert "不存在" in capsys.readouterr().err

    outside = repo.parent / "outside.zip"
    assert module.main(["--artifact", str(outside), "--quiet"]) == 2
    assert "越界" in capsys.readouterr().err

    assert module.main(["--artifact", str(repo / ".git"), "--quiet"]) == 2


def test_zip_traversal_is_blocked_without_extraction() -> None:
    repo = _new_repo("zip-traversal")
    zip_path = repo / "evil.zip"
    _make_zip(
        zip_path,
        {
            "../evil-out.txt": "x\n",
            "/abs-out.txt": "x\n",
            "C:/drive-out.txt": "x\n",
            "ok.txt": "clean\n",
        },
    )
    module = _load_scanner()
    code, data = _scan(module, repo, "--artifact", str(zip_path), out_name="traversal.json")
    assert code == 0
    assert data is not None
    rules = [h["rule"] for h in data["hits"]]
    assert rules.count("artifact_zip_traversal") == 3
    assert data["counts"]["block"] == 3
    fail_code, _ = _scan(
        module,
        repo,
        "--artifact",
        str(zip_path),
        "--fail-on-block",
        out_name="traversal-fail.json",
    )
    assert fail_code == 1
    # 没有任何条目被写到外部目录。
    assert not (WORK / "evil-out.txt").exists()
    assert not (repo / "evil-out.txt").exists()
    assert not (repo / "abs-out.txt").exists()
    assert not (repo / "drive-out.txt").exists()


def test_zip_bomb_is_blocked_without_extraction() -> None:
    repo = _new_repo("zip-bomb")
    zip_path = repo / "bomb.zip"
    payload = b"\x00" * (2 * 1024 * 1024)  # 全零：压缩比远超阈值
    _make_zip(zip_path, {"zeros.bin": payload})
    module = _load_scanner()
    code, data = _scan(module, repo, "--artifact", str(zip_path), out_name="bomb.json")
    assert code == 0
    assert data is not None
    assert any(h["rule"] == "artifact_zip_bomb_suspected" for h in data["hits"])
    assert not (repo / "zeros.bin").exists()


def test_zip_entry_count_limit() -> None:
    repo = _new_repo("zip-entries")
    zip_path = repo / "many.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for index in range(1001):
            zf.writestr("f%04d.txt" % index, "x")
    module = _load_scanner()
    code, data = _scan(module, repo, "--artifact", str(zip_path), out_name="entries.json")
    assert code == 0
    assert data is not None
    assert any(h["rule"] == "artifact_zip_entry_limit" for h in data["hits"])


def test_unreadable_zip_is_block() -> None:
    repo = _new_repo("bad-zip")
    zip_path = repo / "bad.zip"
    zip_path.write_bytes(b"this is not a zip")
    module = _load_scanner()
    code, data = _scan(module, repo, "--artifact", str(zip_path), out_name="badzip.json")
    assert code == 0
    assert data is not None
    assert any(h["rule"] == "artifact_zip_unreadable" for h in data["hits"])


def test_binary_and_image_are_manual_only() -> None:
    repo = _new_repo("binary")
    (repo / "shot.png").write_bytes(PNG_1PX)
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
    module = _load_scanner()
    code, data = _scan(
        module,
        repo,
        "--artifact",
        str(repo / "shot.png"),
        "--artifact",
        str(repo / "blob.bin"),
        out_name="binary.json",
    )
    assert code == 0
    assert data is not None
    entries = {item["path"]: item for item in data["artifacts"]["entries"]}
    assert "shot.png" in entries and "blob.bin" in entries
    for path in ("shot.png", "blob.bin"):
        status = entries[path]["status"]
        assert status.startswith("待人工目检")
        assert "已人工通过" not in status
    assert data["artifacts"]["manual"]
    assert all("待人工" in item["status"] for item in data["artifacts"]["manual"])
    assert data["counts"]["block"] == 0


def test_large_text_is_truncated_and_reported() -> None:
    repo = _new_repo("big-text")
    big = repo / "big.log"
    big.write_bytes((("clean\n" * 200000) + HOME_LINE + "\n").encode("utf-8"))
    module = _load_scanner()
    code, data = _scan(module, repo, "--artifact", str(big), out_name="truncated.json")
    assert code == 0
    assert data is not None
    assert any(h["rule"] == "artifact_text_truncated" for h in data["hits"])
    # 敏感行落在限额之后，扫不到就必须如实说「截断」，不能假装干净。
    assert not any(h["rule"] == "path_home_dir" for h in data["hits"])
    assert "截断" in data["artifacts"]["entries"][0]["status"]


def test_artifact_directory_is_recursive() -> None:
    repo = _new_repo("directory")
    candidate = repo / "cand"
    (candidate / "sub").mkdir(parents=True)
    (candidate / "sub" / "note.txt").write_text(HOME_LINE + "\n", encoding="utf-8")
    module = _load_scanner()
    code, data = _scan(module, repo, "--artifact", str(candidate), out_name="dir.json")
    assert code == 0
    assert data is not None
    assert data["artifacts"]["files"] == 1
    assert any("cand/sub/note.txt" in h["location"] for h in data["hits"])


def test_artifact_can_combine_with_baseline() -> None:
    repo = _new_repo("combined")
    base = _git(repo, "rev-parse", "HEAD").strip()
    zip_path = repo / "extra.zip"
    _make_zip(zip_path, {"candidate.txt": HOME_LINE + "\n"})
    module = _load_scanner()
    code, data = _scan(
        module, repo, "--since-baseline", base, "--artifact", str(zip_path), out_name="combined.json"
    )
    assert code == 0
    assert data is not None
    assert data["scan_mode"]["kind"] == "incremental"
    assert data["scan_mode"]["artifacts"] == ["extra.zip"]
    assert any("extra.zip!candidate.txt" in h["location"] for h in data["hits"])
