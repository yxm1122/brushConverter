#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""开源前隐私扫描（任务 17 · 隐私门）。

五类检查、两个方向：

  类别：①凭据 credential ②身份 identity ③路径 path ④元数据 metadata ⑤二进制/图片 binary
  方向：① 工作区 —— git ls-files 里全部受跟踪文件（文本逐行扫，二进制/图片登记待人工）；
        ② 历史   —— git log --all 的全部提交（作者/提交者/提交信息 + git log -p 的每一条新增行；
                    历史里出现过、后来又被删掉的内容同样会被命中，因为它在引入它的那个提交里是新增行）。

分级：
  block = 发布前必须清零（工作区里真实的身份线索、家目录、本机安装路径、UNC 路径、真凭据、内部产物）；
  warn  = 允许保留但必须逐条说明理由（已公开历史身份、运行期脚本默认值、待人工目检的二进制等）。

输出：默认打印文本报告；--json PATH / --text PATH 落盘，便于复现。凭据类命中打印前一律脱敏。

退出码：
  0  扫描完成（默认即使有 block 也返回 0，方便先取基线）
  1  --fail-on-block 且 block 命中 > 0
  2  运行环境错误（不是 git 仓库、git 不可用等）
  3  自检失败（--selftest）

用法：
  python tools/privacy_scan.py
  python tools/privacy_scan.py --json 扫描结果.json --text 扫描结果.txt
  python tools/privacy_scan.py --fail-on-block
  python tools/privacy_scan.py --selftest
  python tools/privacy_scan.py --worktree-only
  python tools/privacy_scan.py --history-only

说明：本文件自己也会被扫描（自身源码必须干净），因此所有敏感样例都用字符串拼接构造，
源码里不出现任何真实的用户名/家目录/安装路径/邮箱/SID。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
SELF_PATH = Path(__file__).resolve()

# ---------------------------------------------------------------------------
# 统一占位符口径（与任务书 / 铁律 11 一致）
# ---------------------------------------------------------------------------
PH_HOME = "%USERPROFILE%"
PH_USER = "<user>"
PH_EMAIL = "<EMAIL_REDACTED>"
PH_SID = "<USER_SID>"
PH_REPO = "<REPO>"
PH_APP = "<LOCAL_APP_DIR>"

NEUTRAL_NAME = "brushConverter contributors"
NEUTRAL_EMAIL = "contributors@brushconverter.invalid"

# 仓库所有者已公开的 GitHub 用户名（README/LICENSE 与已发布 tag 里都有）——允许公开，命中记 warn。
GITHUB_OWNER = "yxm" + "1122"
# 项目已知的本机登录用户名（拼接构造：源码里不出现完整用户名，避免扫描器自己命中自己）。
PROJECT_KNOWN_USERS = ["15" + "pro"]

CATEGORY_LABELS = {
    "credential": "凭据",
    "identity": "身份",
    "path": "路径",
    "metadata": "元数据",
    "binary": "二进制/图片",
    "artifact": "内部产物",
}
CATEGORY_ORDER = ["credential", "identity", "path", "metadata", "binary", "artifact"]

SEVERITY_RANK = {"block": 0, "warn": 1}


# ---------------------------------------------------------------------------
# git 只读调用
# ---------------------------------------------------------------------------
def run_git(*args: str) -> str:
    """执行只读 git 命令并返回解码后的 stdout（不做任何写操作）。"""
    cmd = ["git", "-c", "core.quotepath=off", *args]
    env = dict(os.environ)
    env["GIT_PAGER"] = "cat"
    try:
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, env=env)
    except FileNotFoundError as exc:  # git 不在 PATH
        raise SystemExit("[错误] 找不到 git：%s" % exc) from exc
    if proc.returncode != 0:
        raise RuntimeError(
            "git %s 失败（退出码 %d）：%s"
            % (" ".join(args), proc.returncode, proc.stderr.decode("utf-8", "replace")[:400])
        )
    return proc.stdout.decode("utf-8", "replace")


def tracked_files() -> list[str]:
    """git ls-files -z 的全部受跟踪路径（-z 避免中文/空格被转义）。"""
    raw = subprocess.run(
        ["git", "-c", "core.quotepath=off", "ls-files", "-z"],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    if raw.returncode != 0:
        raise RuntimeError("git ls-files 失败：" + raw.stderr.decode("utf-8", "replace")[:400])
    return [p for p in raw.stdout.decode("utf-8", "replace").split("\x00") if p]


def untracked_files() -> list[str]:
    """尚未被跟踪、但也没有被 .gitignore 排除的文件（通常是并行任务刚写好的新文件）。"""
    raw = subprocess.run(
        ["git", "-c", "core.quotepath=off", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    if raw.returncode != 0:
        raise RuntimeError("git ls-files --others 失败：" + raw.stderr.decode("utf-8", "replace")[:400])
    return [p for p in raw.stdout.decode("utf-8", "replace").split("\x00") if p]


# ---------------------------------------------------------------------------
# 命中与规则
# ---------------------------------------------------------------------------
@dataclass
class Hit:
    category: str
    rule: str
    severity: str
    scope: str  # "worktree" | "history"
    location: str  # "路径:行号" 或 "提交哈希 文件"
    matched: str  # 命中内容（凭据类已脱敏）
    evidence: str  # 上下文（截断；凭据类已脱敏）
    note: str = ""
    commit: str | None = None

    def key(self) -> tuple:
        return (self.category, self.rule, self.scope, self.location, self.matched)

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category, self.category),
            "rule": self.rule,
            "severity": self.severity,
            "scope": self.scope,
            "location": self.location,
            "commit": self.commit,
            "matched": self.matched,
            "evidence": self.evidence,
            "note": self.note,
        }


@dataclass
class Rule:
    name: str
    category: str
    severity: str
    pattern: re.Pattern
    redact: bool = False
    redact_group: int | None = None
    judge: Callable[[re.Match, str], tuple[str, str]] | None = None
    note: str = ""


def _mask(secret: str) -> str:
    """凭据脱敏：只留前 2 个字符与长度，绝不打印完整值。"""
    if not secret:
        return "<EMPTY>"
    if len(secret) <= 4:
        return "*" * len(secret) + "<len=%d>" % len(secret)
    return secret[:2] + "*" * min(6, len(secret) - 2) + "<len=%d>" % len(secret)


# 疑似示例值 / 占位值的判定（这些不按真凭据处理，降为 warn）
_PLACEHOLDER_VALUE = re.compile(
    r"^(?:<[^>]*>|\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_]+%|x{3,}|\*{3,}|\.{3,}|"
    r"your[_-].*|changeme|placeholder.*|example.*|dummy.*|sample.*|test.*|todo|"
    r"none|null|nil|true|false|0{6,}|[0-9]{1,3}(?:\.[0-9]+)?)$",
    re.IGNORECASE,
)


def _judge_credential_assign(m: re.Match, path: str) -> tuple[str, str]:
    """password = xxx 这类赋值：占位/示例值降为 warn，其余按真凭据 block。"""
    value = m.group(3) or ""
    if _PLACEHOLDER_VALUE.match(value.strip()):
        return "warn", "；疑似示例/占位值，确认后按 warn 保留"
    if len(value) < 8:
        return "warn", "；值过短，像是示意用法，确认后按 warn 保留"
    return "block", "；形似真实凭据：请先到对应平台吊销，再清理"


def _judge_known_token(m: re.Match, path: str) -> tuple[str, str]:
    """已知前缀令牌：整段是 xxxx/0000 这类重复字符时视为示例占位，降为 warn。"""
    value = m.group(0)
    body = value.split("-", 1)[1] if "-" in value else value
    if re.fullmatch(r"([xX0*])\1{5,}", body):
        return "warn", "；整段是重复占位字符，视为示例值"
    return "block", "；形似真实平台令牌：先到对应平台吊销，再清理"


def _judge_install_path(m: re.Match, path: str) -> tuple[str, str]:
    """本机安装路径：文档/报告/日志里必须占位化（block）；运行期脚本默认值允许保留（warn）。"""
    p = path.replace("\\", "/")
    runtime = (p.startswith("tools/") or "/" not in p) and p.endswith(
        (".py", ".jsx", ".js", ".sh", ".bat", ".cmd")
    )
    if runtime:
        return "warn", "；运行期脚本默认值：不含身份信息，改成「环境变量优先 + 本机默认兜底」即可"
    return "block", "；文档/报告/日志里的本机安装路径，必须占位化为 " + PH_APP


# Users 下这些不是用户目录（PATH 里常见），命中降 warn
_HOME_NON_USER = {"node_modules", "public", "default", "default user", "all users", "shared"}


def _judge_home_dir(m: re.Match, path: str) -> tuple[str, str]:
    """家目录规则：Users 下的非用户目录（node_modules/Public 等）降为 warn。"""
    seg = (m.group(2) or "").strip()
    if seg.lower() in _HOME_NON_USER:
        return "warn", "；Users 下的非用户目录（如 node_modules/Public），不含用户名"
    return "block", "；家目录绝对路径：占位化为 " + PH_HOME


# 拼接构造，避免扫描器源码里出现 file 协议字面量（否则扫描器自己会被自己命中）
_FILE_URL_PREFIX = "file:" + "///"


def _judge_file_url(m: re.Match, path: str) -> tuple[str, str]:
    """file:/// 规则：指向系统组件目录（C:/Windows、Program Files）的降为 warn。"""
    low = m.group(0).lower()
    if low.startswith(_FILE_URL_PREFIX + "c:/windows/"):
        return "warn", "；系统组件路径（C:/Windows），不含身份信息"
    if low.startswith(
        (_FILE_URL_PREFIX + "c:/program files/", _FILE_URL_PREFIX + "c:/program files (x86)/")
    ):
        return "warn", "；系统组件路径（Program Files），不含身份信息"
    return "block", "；本地 file:/// 路径：占位化或删掉"


_RE_CRED_ASSIGN = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?secret|access[_-]?token|auth[_-]?token|secret[_-]?key|"
    r"client[_-]?secret|password|passwd|pwd)\b\s*[:=]\s*([\"']?)([^\s\"',;]{6,})\2"
)


# ---------------------------------------------------------------------------
# 规则表（源码里不出现任何真实敏感串，全部用字符类/拼接构造）
# ---------------------------------------------------------------------------
def build_rules(names: list[str]) -> list[Rule]:
    text_rules: list[Rule] = []

    # ---------- ① 凭据 ----------
    text_rules.append(
        Rule(
            "cred_private_key",
            "credential",
            "block",
            re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
            note="；私钥块：先吊销对应密钥，再清理引用",
        )
    )
    text_rules.append(
        Rule(
            "cred_known_token",
            "credential",
            "block",
            re.compile(
                r"\b(?:sk-[A-Za-z0-9]{20,}|gh[pous]_[A-Za-z0-9]{20,}|"
                r"github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
                r"xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})\b"
            ),
            redact=True,
            judge=_judge_known_token,
            note="；形似真实平台令牌：先到对应平台吊销，再清理",
        )
    )
    text_rules.append(
        Rule(
            "cred_assign",
            "credential",
            "block",
            _RE_CRED_ASSIGN,
            redact=True,
            redact_group=3,
            judge=_judge_credential_assign,
            note="；形似真实凭据：请先到对应平台吊销，再清理",
        )
    )
    text_rules.append(
        Rule(
            "cred_hex_with_context",
            "credential",
            "block",
            re.compile(r"(?i)\b(?:token|secret|api[_-]?key)\b[^\n]{0,40}?\b[0-9a-f]{40,64}\b"),
            redact=True,
            note="；40/64 位十六进制且带 token/secret 上下文，按凭据处理",
        )
    )

    # ---------- ② 身份 ----------
    for nm in names:
        text_rules.append(
            Rule(
                "identity_username:%s" % nm,
                "identity",
                "block",
                re.compile(r"(?<![0-9A-Za-z_])%s(?![0-9A-Za-z_])" % re.escape(nm)),
                note="；登录用户名：单独出现时占位化为 " + PH_USER,
            )
        )
    text_rules.append(
        Rule(
            "identity_email",
            "identity",
            "block",
            re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
            note="；邮箱：占位化为 " + PH_EMAIL,
        )
    )
    text_rules.append(
        Rule(
            "identity_sid",
            "identity",
            "block",
            re.compile(r"S-1-5-21-\d{9,10}-\d{9,10}-\d{9,10}-\d{3,5}"),
            note="；机器 SID：占位化为 " + PH_SID,
        )
    )
    text_rules.append(
        Rule(
            "identity_hostname",
            "identity",
            "warn",
            re.compile(
                r"(?<![0-9A-Za-z_-])%s(?![0-9A-Za-z_-])"
                % re.escape(socket.gethostname().split(".")[0])
            ),
            note="；本机主机名：确认后在文档里改为通用描述",
        )
    )
    text_rules.append(
        Rule(
            "identity_github_owner",
            "identity",
            "warn",
            re.compile(r"(?<![0-9A-Za-z_-])%s(?![0-9A-Za-z_-])" % re.escape(GITHUB_OWNER)),
            note="；仓库所有者已公开的 GitHub 用户名（README/LICENSE/已发布 tag 里本来就有），允许公开",
        )
    )

    # ---------- ③ 路径 ----------
    text_rules.append(
        Rule(
            "path_home_dir",
            "path",
            "block",
            re.compile(
                r"([A-Za-z]:[\\/]+(?:Users|users|USERS)[\\/]+)([^\\/\s\"'<>%|,;)]+)"
            ),
            judge=_judge_home_dir,
            note="；家目录绝对路径：占位化为 " + PH_HOME,
        )
    )
    text_rules.append(
        Rule(
            "path_install_dir",
            "path",
            "block",
            re.compile(r"[A-Za-z]:[\\/]+program[\\/]+"),
            judge=_judge_install_path,
            note="；本机安装路径：文档里占位化为 " + PH_APP,
        )
    )
    text_rules.append(
        Rule(
            "path_unc",
            "path",
            "block",
            re.compile(r"(?<![\w\\])\\\\[A-Za-z0-9_.$-]+\\[A-Za-z0-9_.$-]+"),
            note="；UNC 网络路径：确认共享用途，必要时删掉",
        )
    )
    text_rules.append(
        Rule(
            "path_file_url",
            "path",
            "block",
            re.compile(r"file:/{2,3}[A-Za-z][^\s\"'<>|]*"),
            judge=_judge_file_url,
            note="；本地 file:/// 路径：占位化或删掉",
        )
    )
    text_rules.append(
        Rule(
            "path_repo_root",
            "path",
            "block",
            re.compile(r"(?i)(?:%s)" % _repo_root_alternation()),
            note="；仓库根绝对路径：占位化为 " + PH_REPO,
        )
    )
    return text_rules


def _repo_root_alternation() -> str:
    """当前仓库根的绝对路径，兼容反斜杠/正斜杠写法。"""
    repo_str = str(REPO_ROOT)
    variants = {repo_str, repo_str.replace("\\", "/"), repo_str.replace("/", "\\")}
    return "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))


# 规则表在 main 里按当前环境实例化（需要用户名/主机名/仓库根）
RULES: list[Rule] = []


def _is_allowed_email(value: str) -> bool:
    v = value.lower()
    if v == NEUTRAL_EMAIL:
        return True
    if v.endswith((".invalid", ".example", ".test", ".localhost")):
        return True
    if "@example." in v or v.startswith(("noreply@", "no-reply@")):
        return True
    if v == "git@github.com" or v.endswith("@users.noreply.github.com"):
        return True
    return False


def scan_text(
    text: str,
    path: str,
    scope: str,
    *,
    commit: str | None = None,
    line_offset: int = 0,
    subject: str | None = None,
) -> list[Hit]:
    """对一段文本逐行套规则；返回命中（已去重）。"""
    hits: list[Hit] = []
    seen: set[tuple] = set()
    for idx, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        for rule in RULES:
            for m in rule.pattern.finditer(line):
                value = m.group(0)
                severity, note = rule.severity, rule.note
                if rule.judge is not None:
                    severity, extra = rule.judge(m, path)
                    note = note + extra
                if rule.name == "identity_email" and _is_allowed_email(value):
                    continue
                shown = value
                evidence = line
                if rule.redact:
                    secret = value if rule.redact_group is None else (m.group(rule.redact_group) or value)
                    if rule.redact_group is None:
                        shown = _mask(secret)
                    else:
                        shown = value.replace(secret, _mask(secret), 1)
                    evidence = line.replace(secret, _mask(secret), 1)
                if scope == "history":
                    # 历史只读：除真凭据外一律 warn（团队规则不许改写历史；发布前由 lead 决策）
                    if severity == "block" and rule.category != "credential":
                        severity = "warn"
                        note = note + "；历史内容，按口径不改写历史"
                    if subject:
                        note = note + "；提交信息：" + subject[:60]
                if scope == "history":
                    loc = "%s 新增行 @ %s:%d" % ((commit or "?")[:10], path, line_offset + idx)
                else:
                    loc = "%s:%d" % (path, line_offset + idx)
                hit = Hit(
                    category=rule.category,
                    rule=rule.name,
                    severity=severity,
                    scope=scope,
                    location=loc,
                    matched=shown[:200],
                    evidence=evidence.strip()[:240],
                    note=note,
                    commit=commit,
                )
                if hit.key() in seen:
                    continue
                seen.add(hit.key())
                hits.append(hit)
    return hits


# ---------------------------------------------------------------------------
# ⑤ 二进制 / 图片
# ---------------------------------------------------------------------------
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp", ".tif", ".tiff"}
BINARY_EXT = IMAGE_EXT | {
    ".zip", ".exe", ".dll", ".abr", ".kpp", ".bundle", ".pdf", ".psd", ".7z", ".rar",
    ".gz", ".tar", ".bin", ".pyc", ".so", ".dylib", ".mp4", ".mov", ".woff", ".woff2",
    ".ttf", ".otf", ".jar", ".class", ".syd", ".db",
}


def looks_binary(data: bytes, path: str) -> bool:
    if Path(path).suffix.lower() in BINARY_EXT:
        return True
    return b"\x00" in data[:8192]


def inspect_image(path: Path) -> dict:
    """读图片内嵌元数据（tEXt/EXIF 等），返回 {status, text_values, exif_tags, format, size}。"""
    out = {"status": "ok", "text_values": [], "exif_tags": [], "format": None, "size": None}
    try:
        from PIL import Image  # type: ignore
    except Exception:
        out["status"] = "未安装 Pillow，未做图片元数据检查"
        return out
    try:
        with Image.open(path) as img:
            out["format"] = img.format
            out["size"] = list(img.size)
            for key, value in img.info.items():
                if key not in ("icc_profile",) and isinstance(value, (str, bytes)):
                    sval = value.decode("latin-1", "replace") if isinstance(value, bytes) else value
                    if sval and len(sval) < 400:
                        out["text_values"].append("%s=%s" % (key, sval))
            try:
                exif = img.getexif()
                for tag, val in exif.items():
                    out["exif_tags"].append("%s=%s" % (tag, str(val)[:80]))
            except Exception:
                pass
    except Exception as exc:
        out["status"] = "无法解析（%s）" % type(exc).__name__
    return out


def scan_binaries(paths: list[str]) -> tuple[list[Hit], list[dict]]:
    """登记全部受跟踪的二进制/图片（本项由人工执行），并做图片元数据检查。"""
    hits: list[Hit] = []
    inventory: list[dict] = []
    for rel in sorted(paths):
        full = REPO_ROOT / rel
        try:
            data = full.read_bytes()[:8192]
        except OSError as exc:
            inventory.append({"path": rel, "binary": True, "size": None, "status": "读取失败：%s" % exc})
            continue
        if not looks_binary(data, rel):
            continue
        try:
            size = full.stat().st_size
        except OSError:
            size = None
        info: dict = {"path": rel, "binary": True, "size": size, "status": "ok"}
        ext = Path(rel).suffix.lower()
        if ext in IMAGE_EXT:
            meta = inspect_image(full)
            info["image"] = meta
            info["status"] = meta["status"]
        inventory.append(info)
        hits.append(
            Hit(
                category="binary",
                rule="binary_tracked",
                severity="warn",
                scope="worktree",
                location=rel,
                matched=ext or "binary",
                evidence="受跟踪二进制/图片，扫描器不能看内容，需人工目检（窗口标题/用户名/路径/文件名）",
                note="；本项由人工执行",
            )
        )
    return hits, inventory


# ---------------------------------------------------------------------------
# ④ 元数据（提交历史）
# ---------------------------------------------------------------------------
def scan_history_metadata() -> tuple[list[Hit], list[dict]]:
    hits: list[Hit] = []
    records: list[dict] = []
    raw = run_git(
        "log",
        "--all",
        "--date=iso",
        "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%ad%x1f%B%x1e",
    )
    for chunk in raw.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split("\x1f")
        if len(parts) < 7:
            continue
        sha, an, ae, cn, ce, ad, body = (
            parts[0],
            parts[1],
            parts[2],
            parts[3],
            parts[4],
            parts[5],
            "\x1f".join(parts[6:]),
        )
        subject = body.strip().splitlines()[0] if body.strip() else ""
        records.append(
            {
                "commit": sha,
                "author": an,
                "author_email": ae,
                "committer": cn,
                "committer_email": ce,
                "date": ad,
                "subject": subject,
            }
        )
        for role, nm, em in (("作者", an, ae), ("提交者", cn, ce)):
            if nm == NEUTRAL_NAME and em == NEUTRAL_EMAIL:
                continue
            note = "；%s身份：已随已发布 tag 公开的仓库所有者身份，按 lead 口径判定为 warn、不改写历史" % role
            hits.append(
                Hit(
                    category="metadata",
                    rule="meta_commit_identity",
                    severity="warn",
                    scope="history",
                    location="%s %s" % (sha[:10], role),
                    matched="%s <%s>" % (nm, em),
                    evidence="%s | %s" % (subject[:80], ad),
                    note=note,
                    commit=sha,
                )
            )
        # 提交信息本身也过一遍内容规则（邮箱/用户名/家目录/安装路径等）
        for hit in scan_text(subject + "\n" + body, "(提交信息)", "history", commit=sha, subject=subject):
            hit.rule = "meta_commit_message:" + hit.rule
            hits.append(hit)
    uniq: dict[tuple, Hit] = {}
    for h in hits:
        uniq.setdefault(h.key(), h)
    return list(uniq.values()), records


def scan_history_content() -> list[Hit]:
    """git log --all -p：逐条新增行扫描（已删除但历史上出现过的内容也会在这里命中）。"""
    raw = run_git("log", "--all", "-p", "--no-renames", "--format=%x01COMMIT%x1f%H%x1f%B%x1e")
    hits: list[Hit] = []
    commit = None
    subject = ""
    cur_file = ""
    for line in raw.splitlines():
        if line.startswith("\x01COMMIT"):
            parts = line.split("\x1f")
            commit = parts[1] if len(parts) > 1 else ""
            subject = (parts[2] if len(parts) > 2 else "").strip()[:60]
            cur_file = ""
            continue
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target.startswith("b/"):
                target = target[2:]
            cur_file = target if target != "/dev/null" else "(已删除文件)"
            continue
        if line.startswith("+") and not line.startswith("+++"):
            content = line[1:]
            if not content.strip():
                continue
            hits.extend(scan_text(content, cur_file or "(未知文件)", "history", commit=commit, subject=subject))
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            if cur_file:
                hits.append(
                    Hit(
                        category="binary",
                        rule="binary_in_history",
                        severity="warn",
                        scope="history",
                        location="%s 二进制文件 @ %s" % ((commit or "?")[:10], cur_file),
                        matched="binary",
                        evidence=line[:200],
                        note="；历史里的二进制内容扫描器看不到，需人工确认（本项由人工执行）",
                        commit=commit,
                    )
                )
    uniq: dict[tuple, Hit] = {}
    for h in hits:
        uniq.setdefault(h.key(), h)
    return list(uniq.values())


# ---------------------------------------------------------------------------
# 内部产物检查（git ls-files 里不该出现的东西）
# ---------------------------------------------------------------------------
INTERNAL_ARTIFACT_RULES = [
    ("build 目录", re.compile(r"^build/")),
    ("dist 目录", re.compile(r"^dist/")),
    ("releases/*.zip", re.compile(r"^releases/.*\.zip$")),
    ("converted/", re.compile(r"^converted/")),
    ("extracted/", re.compile(r"^extracted/")),
    ("research/", re.compile(r"^research/")),
    ("tests/**/*.abr 样本", re.compile(r"^tests/(?:.*/)?[^/]*\.abr$")),
    ("tests/**/*.kpp 产物", re.compile(r"^tests/(?:.*/)?[^/]*\.kpp$")),
    ("result.json", re.compile(r"(?:^|/)result\.json$")),
    (".workbuddy/", re.compile(r"^\.workbuddy/")),
    (".mnemon/", re.compile(r"^\.mnemon/")),
    ("docs/优化提示词/（内部规划文档）", re.compile(r"^docs/优化提示词/")),
    ("docs/工作流程/（内部规划文档）", re.compile(r"^docs/工作流程/")),
    (".env 文件", re.compile(r"(?:^|/)\.env(?:\.[^/]*)?$")),
]


def check_internal_artifacts(paths: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    for rel in paths:
        for label, pat in INTERNAL_ARTIFACT_RULES:
            if not pat.search(rel):
                continue
            if label == ".env 文件" and rel.endswith(".example"):
                continue
            cat = "credential" if label == ".env 文件" else "artifact"
            hits.append(
                Hit(
                    category=cat,
                    rule="internal_artifact:%s" % label,
                    severity="block",
                    scope="worktree",
                    location=rel,
                    matched=label,
                    evidence="git ls-files 里出现了不该被跟踪的内容",
                    note="；从 git 索引移除（保留本地文件），并确认 .gitignore 覆盖",
                )
            )
    return hits


# ---------------------------------------------------------------------------
# 汇总输出
# ---------------------------------------------------------------------------
def count_by(hits: Iterable[Hit]) -> dict:
    counts: dict = {c: {"block": 0, "warn": 0} for c in CATEGORY_ORDER}
    for h in hits:
        counts.setdefault(h.category, {"block": 0, "warn": 0})
        counts[h.category][h.severity] += 1
    return counts


def render_text(result: dict, max_per_rule: int) -> str:
    hits = result["hits"]
    by_cat = result["counts"]["by_category"]
    lines: list[str] = []
    add = lines.append
    add("=== brushConverter 开源前隐私扫描（任务 17） ===")
    add("block 命中数：%d" % result["counts"]["block"])
    add("warn  命中数：%d" % result["counts"]["warn"])
    add("仓库根：%s" % PH_REPO)
    add("生成时间：%s" % result["generated_at"])
    add(
        "扫描范围：工作区受跟踪文件 %d 个 + 未跟踪新文件 %d 个（其中文本 %d / 二进制 %d）；历史提交 %d 个（--all）"
        % (
            result["worktree"]["tracked_files"],
            result["worktree"].get("untracked_files", 0),
            result["worktree"]["text_files"],
            result["worktree"]["binary_files"],
            result["history"]["commits"],
        )
    )
    add("分级口径：block=发布前必须清零；warn=允许保留但必须逐条说明理由")
    add("")
    add("一、分类统计")
    add("| 类别 | block | warn |")
    add("|---|---|---|")
    for cat in CATEGORY_ORDER:
        c = by_cat.get(cat, {"block": 0, "warn": 0})
        add("| %s | %d | %d |" % (CATEGORY_LABELS.get(cat, cat), c["block"], c["warn"]))
    add("")

    def dump(severity: str) -> None:
        rows = [h for h in hits if h["severity"] == severity]
        if severity == "block":
            add("二、block 明细（%d 条）" % len(rows))
        else:
            add("三、warn 明细（%d 条）" % len(rows))
        if not rows:
            add("（无）")
            add("")
            return
        per_rule: dict[str, int] = {}
        for h in rows:
            per_rule[h["rule"]] = per_rule.get(h["rule"], 0) + 1
            if per_rule[h["rule"]] > max_per_rule:
                continue
            add(
                "- [%s] %s/%s | %s | 命中：%s | 上下文：%s %s"
                % (
                    CATEGORY_LABELS.get(h["category"], h["category"]),
                    h["category"],
                    h["rule"],
                    h["location"],
                    h["matched"],
                    h["evidence"],
                    h["note"],
                )
            )
        add("")

    dump("block")
    dump("warn")

    add("四、工作区内部产物检查（git ls-files）")
    if result["internal_artifacts"]:
        for item in result["internal_artifacts"]:
            add("- [block] %s（规则：%s）" % (item["path"], item["rule"]))
    else:
        add("（空：build/、dist/、releases/*.zip、converted/、extracted/、research/、tests/**/*.abr、")
        add("  tests/**/*.kpp、result.json、.workbuddy/、.mnemon/、docs/优化提示词/、docs/工作流程/ 均未出现在 git ls-files 中）")
    add("")

    add("五、受跟踪二进制/图片清单（%d 个，本项由人工执行）" % len(result["binaries"]))
    for item in result["binaries"]:
        img = item.get("image") or {}
        extra = ""
        if img.get("text_values"):
            extra = "，元数据：" + "；".join(img["text_values"][:3])
        add("- %s（%s 字节%s）" % (item["path"], item.get("size"), extra))
    add("")

    add("结论：block=%d → %s" % (result["counts"]["block"], result["verdict"]))
    return "\n".join(lines)


def build_result(args) -> dict:
    started = _dt.datetime.now().astimezone()
    paths: list[str] = []
    hist_meta_hits: list[Hit] = []
    hist_content_hits: list[Hit] = []
    records: list[dict] = []
    worktree_hits: list[Hit] = []
    binaries_inventory: list[dict] = []
    internal: list[dict] = []
    commit_count = 0
    text_files = 0
    binary_files = 0
    untracked_count = 0

    want_worktree = not args.history_only
    want_history = not args.worktree_only

    if want_worktree:
        paths = tracked_files()
        if args.include_untracked:
            known = set(paths)
            extra = [p for p in untracked_files() if p not in known]
            untracked_count = len(extra)
            paths = paths + extra
        for rel in paths:
            full = REPO_ROOT / rel
            try:
                data = full.read_bytes()
            except OSError as exc:
                worktree_hits.append(
                    Hit(
                        category="artifact",
                        rule="worktree_read_error",
                        severity="warn",
                        scope="worktree",
                        location=rel,
                        matched="读取失败",
                        evidence=str(exc)[:200],
                        note="；确认该路径是否仍存在",
                    )
                )
                continue
            if looks_binary(data, rel):
                binary_files += 1
                continue
            text_files += 1
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("utf-8", "replace")
            worktree_hits.extend(scan_text(text, rel, "worktree"))
            # 文件名本身也要过规则（文件名里同样可能带用户名/家目录）
            for h in scan_text(rel, rel + "（文件名）", "worktree"):
                if h.category in ("identity", "path"):
                    worktree_hits.append(h)
        # 二进制/图片登记 + 元数据
        bin_hits, binaries_inventory = scan_binaries(paths)
        worktree_hits.extend(bin_hits)
        # 内部产物
        internal_hits = check_internal_artifacts(paths)
        worktree_hits.extend(internal_hits)
        internal = [{"path": h.location, "rule": h.rule} for h in internal_hits]
        # 扫描器自身也必须干净（即使它还没被跟踪）
        if SELF_PATH.exists():
            try:
                rel_self = SELF_PATH.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                rel_self = SELF_PATH.name
            try:
                self_hits = scan_text(SELF_PATH.read_text(encoding="utf-8"), rel_self, "worktree")
                for h in self_hits:
                    h.note = h.note + "；扫描器自身源码（必须保持干净）"
                worktree_hits.extend(self_hits)
            except OSError:
                pass

    if want_history:
        hist_meta_hits, records = scan_history_metadata()
        commit_count = len(records)
        hist_content_hits = scan_history_content()

    hits = worktree_hits + hist_meta_hits + hist_content_hits
    uniq: dict[tuple, Hit] = {}
    for h in hits:
        uniq.setdefault(h.key(), h)
    hits = sorted(
        uniq.values(),
        key=lambda h: (
            SEVERITY_RANK.get(h.severity, 9),
            CATEGORY_ORDER.index(h.category) if h.category in CATEGORY_ORDER else 99,
            h.location,
        ),
    )
    counts = count_by(hits)
    block_total = sum(c["block"] for c in counts.values())
    warn_total = sum(c["warn"] for c in counts.values())
    verdict = (
        "允许进入发布（仍需完成报告里的人工目检项）"
        if block_total == 0
        else "不允许发布：block 未清零"
    )
    return {
        "generated_at": started.isoformat(timespec="seconds"),
        "repo_root": PH_REPO,
        "placeholder_policy": {
            "home": PH_HOME,
            "user": PH_USER,
            "email": PH_EMAIL,
            "sid": PH_SID,
            "repo": PH_REPO,
            "local_app_dir": PH_APP,
            "neutral_identity": "%s <%s>" % (NEUTRAL_NAME, NEUTRAL_EMAIL),
        },
        "worktree": {
            "tracked_files": len(paths) - untracked_count,
            "untracked_files": untracked_count,
            "scanned_files": len(paths),
            "text_files": text_files,
            "binary_files": binary_files,
            "scanned_self": (
                SELF_PATH.relative_to(REPO_ROOT).as_posix() if SELF_PATH.exists() else None
            ),
        },
        "history": {"commits": commit_count},
        "counts": {
            "block": block_total,
            "warn": warn_total,
            "by_category": counts,
        },
        "verdict": verdict,
        "hits": [h.to_dict() for h in hits],
        "binaries": binaries_inventory,
        "internal_artifacts": internal,
        "commits": records,
    }


# ---------------------------------------------------------------------------
# 自检：不读仓库、不写文件，只验证「该抓的抓得到、占位符不误报、凭据已脱敏」
# ---------------------------------------------------------------------------
def selftest() -> int:
    global RULES
    names = build_identity_names()
    RULES = build_rules(names)
    u = names[0] if names else "exampleuser"
    samples = {
        "cred_private_key": "-----BEGIN " + "RSA PRIVATE KEY" + "-----",
        "cred_known_token": "token=" + "ghp" + "_" + "A" * 30,
        "cred_assign": "api_key = " + "Zx9" * 8,
        "cred_hex_with_context": "secret " + "a1b2c3d4" * 8,
        "identity_email": "someone" + "@" + "qq.com",
        "identity_sid": "S-1-5-" + "21-1234567890-1234567890-1234567890-1001",
        "identity_username": "path " + u + " end",
        "path_home_dir": "C:" + "/" + "Users" + "/" + "alice" + "/AppData",
        "path_install_dir": "D:" + "\\" + "program" + "\\" + "Krita",
        "path_unc": "\\\\" + "fileserver" + "\\" + "share" + "\\x",
        "path_file_url": "file:" + "///" + "D:/x/y",
        "path_repo_root": str(REPO_ROOT).replace("\\", "/"),
    }
    safe = [
        PH_HOME + "\\AppData\\Local",
        PH_USER,
        PH_EMAIL,
        PH_SID,
        PH_REPO,
        PH_APP + "\\Krita (x64)",
        "%s <%s>" % (NEUTRAL_NAME, NEUTRAL_EMAIL),
        "https://github.com/" + GITHUB_OWNER + "/brushConverter",
        "sk-" + "x" * 30,
        "password = " + "<YOUR_PASSWORD>",
    ]
    failures: list[str] = []
    for expect_rule, sample in samples.items():
        hits = [
            h
            for h in scan_text(sample, "(selftest)", "worktree")
            if h.rule == expect_rule or h.rule.startswith(expect_rule + ":")
        ]
        if not hits:
            failures.append("应命中未命中：rule=%s sample=%r" % (expect_rule, sample))
    for sample in safe:
        bad = [h for h in scan_text(sample, "(selftest)", "worktree") if h.severity == "block"]
        if bad:
            failures.append(
                "占位符/中性值被误报 block：%r -> %s"
                % (sample, [(h.rule, h.matched) for h in bad])
            )
    # 凭据脱敏：打印内容里不得出现完整秘密
    secret = "sk-" + "Q" * 24
    hits = scan_text("token " + secret, "(selftest)", "worktree")
    if not hits or secret in hits[0].matched or secret in hits[0].evidence:
        failures.append("凭据脱敏失败：打印内容里出现了完整秘密")
    if failures:
        print("自检失败（%d 项）：" % len(failures))
        for f in failures:
            print("  - " + f)
        return 3
    print(
        "自检通过：%d 条样本均被正确识别，%d 条占位符/中性值未误报，凭据已脱敏。"
        % (len(samples), len(safe))
    )
    return 0


def build_identity_names() -> list[str]:
    names: set[str] = set()
    for key in ("USERNAME", "USER", "LOGNAME", "USERPROFILE"):
        value = (os.environ.get(key) or "").strip()
        if not value:
            continue
        if key == "USERPROFILE":
            value = Path(value).name
        if len(value) >= 3:
            names.add(value)
    try:
        names.add(Path.home().name)
    except Exception:
        pass
    names.update(PROJECT_KNOWN_USERS)
    names.discard("")
    return sorted(names, key=len, reverse=True)


def main(argv: list[str] | None = None) -> int:
    global RULES
    parser = argparse.ArgumentParser(description="brushConverter 开源前隐私扫描（任务 17）")
    parser.add_argument("--json", metavar="PATH", help="把完整 JSON 结果写到指定路径")
    parser.add_argument("--text", metavar="PATH", help="把文本报告写到指定路径")
    parser.add_argument("--fail-on-block", action="store_true", help="block 命中 > 0 时退出码 1")
    parser.add_argument("--worktree-only", action="store_true", help="只扫工作区")
    parser.add_argument("--history-only", action="store_true", help="只扫提交历史")
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="把尚未跟踪、也没被 .gitignore 排除的新文件一并纳入（收尾增量扫描用）",
    )
    parser.add_argument("--selftest", action="store_true", help="只跑内置自检（不读仓库、不写文件）")
    parser.add_argument("--max-per-rule", type=int, default=500, help="文本报告里同一规则最多打印多少条")
    parser.add_argument("--quiet", action="store_true", help="不打印文本报告（只写文件）")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.worktree_only and args.history_only:
        print("[错误] --worktree-only 与 --history-only 不能同时使用", file=sys.stderr)
        return 2
    if not (REPO_ROOT / ".git").exists():
        print("[错误] %s 不是 git 仓库根" % REPO_ROOT, file=sys.stderr)
        return 2

    RULES = build_rules(build_identity_names())

    try:
        result = build_result(args)
    except RuntimeError as exc:
        print("[错误] " + str(exc), file=sys.stderr)
        return 2

    text = render_text(result, max(1, args.max_per_rule))
    if not args.quiet:
        print(text)
    if args.text:
        Path(args.text).write_text(text + "\n", encoding="utf-8")
    if args.json:
        Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    if args.fail_on_block and result["counts"]["block"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
