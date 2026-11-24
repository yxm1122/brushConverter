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
  2  运行环境错误（不是 git 仓库、git 不可用、--artifact 路径无效等）
  3  自检失败（--selftest）
  4  基线阻塞（--since-baseline 不存在 / 不是 HEAD 祖先 / 引用集合无法确认覆盖）

三种口径（162A 起；--help 里按「日常增量 / 发布前全量」分组，不会混在一起）：

  ① 发布前全量（默认；186 候选冻结后集中执行一次）
     python tools/privacy_scan.py --include-untracked --fail-on-block
  ② 日常增量（推荐；只扫基线之后，成本低，但不能替代发布门）
     python tools/privacy_scan.py --since-baseline v1.5.0
     python tools/privacy_scan.py --since-baseline HEAD --artifact 候选.zip
  ③ 仅显式候选（补扫被 .gitignore 忽略的 ZIP/文件；不触发全量）
     python tools/privacy_scan.py --artifact 候选.zip --artifact 某目录/

  其它（调试/取证）：
  python tools/privacy_scan.py --json 扫描结果.json --text 扫描结果.txt
  python tools/privacy_scan.py --selftest
  python tools/privacy_scan.py --worktree-only
  python tools/privacy_scan.py --history-only
  python tools/privacy_scan.py --fix-worktree            # 只预览要占位化的文件（不落盘）
  python tools/privacy_scan.py --fix-worktree --apply     # 真正占位化工作区
  python tools/privacy_scan.py --fix-tree --root <路径>   # 供 git filter-branch --tree-filter 调用（重写历史）

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
import zipfile
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

# 已发布 / 已推送的边界：这些提交里的命中只登记为 warn（改写会破坏已公开的 tag 与别人已 clone 的历史）。
# 未推送范围（origin/main..HEAD）里的命中是 block，必须占位化后重写历史。
# 2026-11-24 任务 162A 使用者裁决：已发布边界从 v1.2.1 改为最新已发布 tag v1.5.0。
PUBLISHED_TAG = "v" + "1.5.0"
PUSHED_REF = "refs/remotes/origin/main"

# 本轮新增/需要显式证明「扫过」的目录（覆盖清单，0 个文件即报 block：覆盖缺失）。
COVERAGE_DIRS = [
    ("docs/数据/", "本轮新增数据（参数矩阵 / 键名全集 / 模式表 / 动态命中）"),
    ("docs/数据/dyn-hits/", "动态命中记录（30A 改值因果实验）"),
    ("docs/研究报告/", "本轮研究报告（静态-* / 动态-* / 模式-* / 逆向-*）"),
    ("docs/验收报告/", "本轮验收报告"),
    ("tests/baselines/mode/", "PS 真值基线 mode"),
    ("tests/baselines/mode-A/", "模式批 A 产物"),
    ("tests/baselines/mode-B/", "模式批 B 产物"),
    ("tests/baselines/mode-C/", "模式批 C 产物"),
    ("tools/re_pipeline/", "逆向流水线脚本"),
    ("gui/", "GUI 源码与 i18n"),
]

# 扫描根：默认仓库根；--fix-tree 时由 --root 指定（历史重写时路径按原仓库根构造规则）。
SCAN_ROOT = REPO_ROOT

# 全部提交（由 build_result 在扫描前填充）；历史命中按「是否已公开」分级。
PUBLISHED_COMMITS: set[str] = set()

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
    published: bool | None = None

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
            "published": self.published,
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
    fix: Callable[[re.Match], str] | None = None


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
    # 运行期脚本（tools/、tests/、gui/ 等处的代码，但不在 docs/ 文档里）的本机默认值允许保留：
    # 不含身份信息，按第一轮口径记 warn（脚本需要本机默认值才能直接跑；应写成「环境变量优先 + 默认兜底」）。
    code_ext = (".py", ".jsx", ".js", ".sh", ".bat", ".cmd", ".java", ".ps1")
    runtime = p.endswith(code_ext) and not p.startswith("docs/")
    if runtime:
        return "warn", "；运行期脚本默认值：不含身份信息，改成「环境变量优先 + 本机默认兜底」即可"
    return "block", "；文档/报告/日志里的本机安装路径，必须占位化为 " + PH_APP


# 占位符修复动作（--fix-worktree / --fix-tree / 历史重写共用同一套映射）
def _fix_plain(value: str) -> Callable[[re.Match], str]:
    return lambda _m: value


def _fix_install_dir(m: re.Match) -> str:
    """吃掉盘符与 program 段，保留末尾分隔符的写法（JSON 里是双反斜杠，必须原样保留）。"""
    tail = re.search(r"[\\/]+$", m.group(0))
    return PH_APP + (tail.group(0) if tail else "")


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
def build_rules(names: list[str], root: Path | None = None) -> list[Rule]:
    base = root or SCAN_ROOT
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
                fix=_fix_plain(PH_USER),
            )
        )
    text_rules.append(
        Rule(
            "identity_email",
            "identity",
            "block",
            re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
            note="；邮箱：占位化为 " + PH_EMAIL,
            fix=_fix_plain(PH_EMAIL),
        )
    )
    text_rules.append(
        Rule(
            "identity_sid",
            "identity",
            "block",
            re.compile(r"S-1-5-21-\d{9,10}-\d{9,10}-\d{9,10}-\d{3,5}"),
            note="；机器 SID：占位化为 " + PH_SID,
            fix=_fix_plain(PH_SID),
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
            fix=_fix_plain(PH_HOME),
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
            fix=_fix_install_dir,
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
            re.compile(r"(?i)(?:%s)" % _repo_root_alternation(base)),
            note="；仓库根绝对路径：占位化为 " + PH_REPO,
            fix=_fix_plain(PH_REPO),
        )
    )
    # 本机第三方工具链安装目录（Java / IDE / PortableApps / nvm 等）：不含身份信息，登记为 warn。
    text_rules.append(
        Rule(
            "path_local_toolchain",
            "path",
            "warn",
            re.compile(
                r"(?i)[A-Za-z]:[\\/]+(?:java\d+|portableapps|intellij[^\\/\s\"'<>|]*|"
                r"pycharm[^\\/\s\"'<>|]*|nvm[\\/]+installs)[\\/]"
            ),
            note="；本机第三方工具链安装目录：只暴露装了什么软件，不含身份信息；按 lead 口径登记 warn、不占位化",
        )
    )
    return text_rules


def _repo_root_alternation(root: Path | None = None) -> str:
    """当前仓库根的绝对路径，兼容反斜杠/正斜杠写法。"""
    repo_str = str(root or SCAN_ROOT)
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


def _rev_list(*args: str) -> list[str]:
    try:
        return run_git("rev-list", *args).split()
    except RuntimeError:
        return []


def published_commits() -> set[str]:
    """已公开的提交集合 = 已发布 tag 可达 ∪ 已推送 origin/main 可达。

    这批提交里出现过的内容已经能被任何人 clone 出来，改写只会破坏 tag 与别人的仓库，
    因此只如实登记为 warn；其余（未推送）提交里的命中是 block，必须占位化后重写历史。
    """
    shas: set[str] = set()
    for ref in (PUBLISHED_TAG, PUSHED_REF):
        shas.update(_rev_list(ref))
    return shas


def scan_text(
    text: str,
    path: str,
    scope: str,
    *,
    commit: str | None = None,
    line_offset: int = 0,
    subject: str | None = None,
    published: bool = True,
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
                    if not published:
                        # 未推送历史（origin/main..HEAD）：命中仍是 block，必须占位化后重写；
                        # 只删最新提交不算过——旧提交里的内容照样能被 git log -S 检索到。
                        if severity == "block":
                            note = note + "；未推送历史：必须占位化后重写"
                        else:
                            note = note + "；未推送历史：warn 级，登记原因"
                    elif severity == "block" and rule.category != "credential":
                        # 已公开历史（已发布 tag / 已推送 origin）：改写会破坏已公开 tag 与他人 clone，
                        # 按 lead 口径只登记、不改写。
                        severity = "warn"
                        note = note + "；已公开历史（已发布 tag / 已推送 origin），按口径不改写，如实登记"
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
                    published=(published if scope == "history" else None),
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
        info: dict = {
            "path": rel,
            "binary": True,
            "size": size,
            "status": "ok",
            "manual_review": "待人工目检（不代表已通过）",
        }
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
def scan_history_metadata(revspec: str = "--all") -> tuple[list[Hit], list[dict]]:
    """扫描提交元数据；revspec 为 "--all"（发布前全量）或 "<基线>..<HEAD>"（日常增量）。"""
    hits: list[Hit] = []
    records: list[dict] = []
    raw = run_git(
        "log",
        revspec,
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
        is_published = sha in PUBLISHED_COMMITS
        for role, nm, em in (("作者", an, ae), ("提交者", cn, ce)):
            if nm == NEUTRAL_NAME and em == NEUTRAL_EMAIL:
                continue
            if is_published:
                sev = "warn"
                note = (
                    "；%s身份：已随已发布 tag / 已推送提交公开的仓库所有者身份，"
                    "按 lead 口径判定为 warn、不改写历史" % role
                )
            else:
                sev = "block"
                note = (
                    "；%s身份不是中性署名：未推送提交必须用 brushConverter contributors 中性署名重写" % role
                )
            hits.append(
                Hit(
                    category="metadata",
                    rule="meta_commit_identity",
                    severity=sev,
                    scope="history",
                    location="%s %s" % (sha[:10], role),
                    matched="%s <%s>" % (nm, em),
                    evidence="%s | %s" % (subject[:80], ad),
                    note=note,
                    commit=sha,
                    published=is_published,
                )
            )
        # 提交信息本身也过一遍内容规则（邮箱/用户名/家目录/安装路径等）
        for hit in scan_text(
            subject + "\n" + body,
            "(提交信息)",
            "history",
            commit=sha,
            subject=subject,
            published=is_published,
        ):
            hit.rule = "meta_commit_message:" + hit.rule
            hits.append(hit)
    uniq: dict[tuple, Hit] = {}
    for h in hits:
        uniq.setdefault(h.key(), h)
    return list(uniq.values()), records


def scan_history_content(revspec: str = "--all") -> list[Hit]:
    """git log <revspec> -p：逐条新增行扫描。

    基线之后新增、后来又删掉的内容同样会命中（它在引入它的那个提交里是新增行）；
    未推送提交的命中仍按 block 处理，已公开历史只登记 warn、不改写。
    """
    raw = run_git("log", revspec, "-p", "--no-renames", "--format=%x01COMMIT%x1f%H%x1f%B%x1e")
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
            hits.extend(
                scan_text(
                    content,
                    cur_file or "(未知文件)",
                    "history",
                    commit=commit,
                    subject=subject,
                    published=(commit in PUBLISHED_COMMITS),
                )
            )
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
    ("_work/（一次性探针脚手架 / 调试日志）", re.compile(r"(?:^|/)_work/")),
    (".env 文件", re.compile(r"(?:^|/)\.env(?:\.[^/]*)?$")),
]

# 110/111 自产测试笔刷：tests/abr-generated/*.abr 是有意入库的可复现测试资产
# （.gitignore 显式放行，约 5KB/支，无敏感内容）。2026-11-21 使用者批准白名单：
# 仍登记为 warn 以便审计，但不阻断发布。
ALLOWED_INTERNAL_ARTIFACTS = {
    "tests/**/*.abr 样本": re.compile(r"^tests/abr-generated/[^/]+\.abr$"),
}


def check_internal_artifacts(paths: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    for rel in paths:
        for label, pat in INTERNAL_ARTIFACT_RULES:
            if not pat.search(rel):
                continue
            if label == ".env 文件" and rel.endswith(".example"):
                continue
            allow = ALLOWED_INTERNAL_ARTIFACTS.get(label)
            if allow is not None and allow.search(rel):
                hits.append(
                    Hit(
                        category="artifact",
                        rule="internal_artifact:%s" % label,
                        severity="warn",
                        scope="worktree",
                        location=rel,
                        matched=label,
                        evidence="使用者 2026-11-21 批准：自产测试笔刷，自包含、无敏感内容",
                        note="；白名单保留（不删文件、不误报 block）",
                    )
                )
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
# 覆盖清单（本轮新增目录必须真的被扫到，0 个文件即报覆盖缺失）
# ---------------------------------------------------------------------------
def coverage_report(paths: list[str]) -> list[dict]:
    """逐目录统计扫描覆盖；目录里 0 个文件视为覆盖缺失（block）。"""
    rows: list[dict] = []
    for prefix, desc in COVERAGE_DIRS:
        matched = [p for p in paths if p.replace("\\", "/").startswith(prefix)]
        rows.append({"dir": prefix, "desc": desc, "files": len(matched)})
    return rows


def coverage_hits(rows: list[dict]) -> list[Hit]:
    hits: list[Hit] = []
    for row in rows:
        if row["files"] > 0:
            continue
        hits.append(
            Hit(
                category="artifact",
                rule="coverage_missing:" + row["dir"],
                severity="block",
                scope="worktree",
                location=row["dir"],
                matched="0 个文件",
                evidence="本轮新增目录没有被扫描覆盖",
                note="；检查该目录是否已被 git 跟踪、是否改名",
            )
        )
    return hits


# ---------------------------------------------------------------------------
# 占位符修复（--fix-worktree / --fix-tree；历史重写复用同一套映射，保证口径一致）
# ---------------------------------------------------------------------------
def _fix_rules() -> list[Rule]:
    """按「先长后短」顺序返回带修复动作的规则：先整条路径，最后才是裸用户名。"""
    order = ["path_repo_root", "path_home_dir", "path_install_dir", "identity_email", "identity_sid"]
    picked: list[Rule] = []
    for name in order:
        picked.extend(r for r in RULES if r.name == name and r.fix is not None)
    picked.extend(r for r in RULES if r.name.startswith("identity_username:") and r.fix is not None)
    return picked


def apply_fixes(text: str, path: str) -> tuple[str, int]:
    """按规则把 block 级命中替换成统一占位符；返回 (新文本, 替换次数)。warn 级一律保留。"""
    total = 0
    for rule in _fix_rules():
        counter = {"n": 0}

        def _sub(m: re.Match, _rule=rule, _counter=counter) -> str:
            severity = _rule.severity
            if _rule.judge is not None:
                severity, _ = _rule.judge(m, path)
            if severity != "block":
                return m.group(0)
            # 中性/示例邮箱（contributors@brushconverter.invalid、git@github.com 等）与扫描器口径一致：不替换
            if _rule.name == "identity_email" and _is_allowed_email(m.group(0)):
                return m.group(0)
            replaced = _rule.fix(m) if _rule.fix is not None else m.group(0)
            if replaced != m.group(0):
                _counter["n"] += 1
            return replaced

        text = rule.pattern.sub(_sub, text)
        total += counter["n"]
    return text, total


_FIX_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".idea", ".vscode", "node_modules"}


def _iter_tree_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _FIX_SKIP_DIRS]
        for name in filenames:
            if name == ".git":  # git worktree 里 .git 是文件而不是目录
                continue
            yield Path(dirpath) / name


def fix_files(root: Path, paths: list[str], apply: bool) -> dict:
    """对给定文件列表做占位化；apply=False 只预览。二进制/非 UTF-8 文件跳过。"""
    changed: list[dict] = []
    replacements = 0
    skipped = 0
    for rel in paths:
        full = root / rel
        if full.is_symlink() or not full.is_file():
            continue
        try:
            data = full.read_bytes()
        except OSError:
            skipped += 1
            continue
        if b"\x00" in data[:8192]:
            skipped += 1
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue
        new_text, n = apply_fixes(text, rel)
        if n <= 0 or new_text == text:
            continue
        if apply:
            try:
                full.write_bytes(new_text.encode("utf-8"))
            except OSError:
                skipped += 1
                continue
        changed.append({"path": rel, "replacements": n})
        replacements += n
    return {
        "files_changed": len(changed),
        "replacements": replacements,
        "binary_skipped": skipped,
        "applied": apply,
        "details": changed,
    }


def fix_worktree(paths: list[str], apply: bool) -> dict:
    return fix_files(REPO_ROOT, paths, apply)


def fix_tree(root: Path, apply: bool) -> dict:
    paths = [p.relative_to(root).as_posix() for p in _iter_tree_files(root) if p.is_file()]
    return fix_files(root, paths, apply)


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
    mode = result.get("scan_mode", {})
    add("=== brushConverter 开源前隐私扫描（任务 17 / 162A） ===")
    add("block 命中数：%d" % result["counts"]["block"])
    add("warn  命中数：%d" % result["counts"]["warn"])
    add("扫描模式：%s" % mode.get("label", "发布前全量（工作区 + 全部提交历史）"))
    if mode.get("baseline"):
        b = mode["baseline"]
        add(
            "基线校验：%s（%s）→ HEAD（%s）；基线后新增提交 %d 个；"
            "已校验存在 / 祖先关系 / 引用集合"
            % (b["ref"], b["commit"][:10], b["head"][:10], b["commits"])
        )
    if mode.get("artifacts"):
        add("显式候选：%s" % "、".join(mode["artifacts"]))
    if mode.get("kind") != "full":
        add("口径：本次不是发布前全量扫描；候选冻结后仍须 186 执行全量（block 必须为 0）。")
    add("仓库根：%s" % PH_REPO)
    add("生成时间：%s" % result["generated_at"])
    if mode.get("kind") == "artifact":
        add(
            "扫描范围：显式候选 %d 个根 / %d 个文件；未扫描工作区全量、未扫描全部历史"
            % (len(mode.get("artifacts") or []), result.get("artifacts", {}).get("files", 0))
        )
    elif mode.get("kind") == "incremental":
        add(
            "扫描范围：工作区增量文件 %d 个（未跟踪新文件 %d 个，其中文本 %d / 二进制 %d）；"
            "基线后提交 %d 个（%s）"
            % (
                result["worktree"]["changed_files"],
                result["worktree"].get("untracked_files", 0),
                result["worktree"]["text_files"],
                result["worktree"]["binary_files"],
                result["history"]["commits"],
                result["history"].get("revspec", "--all"),
            )
        )
    else:
        add(
            "扫描范围：工作区受跟踪文件 %d 个 + 未跟踪新文件 %d 个（其中文本 %d / 二进制 %d）；"
            "历史提交 %d 个（--all）"
            % (
                result["worktree"]["tracked_files"],
                result["worktree"].get("untracked_files", 0),
                result["worktree"]["text_files"],
                result["worktree"]["binary_files"],
                result["history"]["commits"],
            )
        )
    if result["worktree"]["text_files"]:
        add(
            "文件类型覆盖：文本 %s"
            % "、".join(
                "%s×%d" % (k or "(无扩展名)", v)
                for k, v in list(result["worktree"].get("text_extensions", {}).items())[:14]
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
    sc = result.get("scopes", {})
    ps = result.get("published_split", {})
    add(
        "block 构成：工作区 %d 条 / 未推送历史 %d 条 / 显式候选 %d 条；已公开历史命中已降级为 warn（%d 条）"
        % (
            sc.get("worktree", {}).get("block", 0),
            ps.get("unpublished", {}).get("block", 0),
            sc.get("artifact", {}).get("block", 0),
            ps.get("published", {}).get("warn", 0),
        )
    )
    add("")
    if result.get("coverage"):
        add("一·五、覆盖清单（本轮新增目录，0 个文件即判覆盖缺失）")
        for row in result.get("coverage", []):
            add("- %s（%s）：%d 个文件" % (row["dir"], row["desc"], row["files"]))
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

    # 章节号随模式连续：artifact-only 没有「工作区内部产物」一节，避免出现跳号。
    section_numbers = ["四", "五", "六"]
    section = 0
    if mode.get("kind") != "artifact":
        add("四、工作区内部产物检查（本次扫描到的路径）")
        section = 1
        if result["internal_artifacts"]:
            for item in result["internal_artifacts"]:
                add("- [block] %s（规则：%s）" % (item["path"], item["rule"]))
        else:
            add("（空：build/、dist/、releases/*.zip、converted/、extracted/、research/、tests/**/*.abr、")
            add("  tests/**/*.kpp、result.json、.workbuddy/、.mnemon/、docs/优化提示词/、docs/工作流程/ 均未出现在本次扫描路径中）")
        add("")

    add(
        "%s、受跟踪二进制/图片清单（%d 个，仅登记待人工，不代表已通过）"
        % (section_numbers[section], len(result["binaries"]))
    )
    section += 1
    for item in result["binaries"]:
        img = item.get("image") or {}
        extra = ""
        if img.get("text_values"):
            extra = "，元数据：" + "；".join(img["text_values"][:3])
        add("- %s（%s 字节%s）" % (item["path"], item.get("size"), extra))
    add("")

    art = result.get("artifacts") or {}
    if art.get("roots"):
        add("%s、显式候选清单（--artifact；只读扫描，不代表已通过）" % section_numbers[section])
        add("候选根：%s" % "、".join(art["roots"]))
        shown = 0
        for item in art.get("entries", []):
            if shown >= max(1, max_per_rule):
                break
            label = item["path"] + ("!" + item["entry"] if item.get("entry") else "")
            size = "" if item.get("size") is None else "（%s 字节）" % item["size"]
            add("- %s%s：%s" % (label, size, item.get("status", "")))
            shown += 1
        total = len(art.get("entries", []))
        if total > shown:
            add("- ……（其余 %d 条见 --json）" % (total - shown))
        add("说明：%s" % art.get("note", ""))
        add("      二进制/图片只是登记待人工，不代表已人工目检通过；ZIP 条目未解压到外部目录。")
        add("")

    add("结论：block=%d → %s" % (result["counts"]["block"], result["verdict"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 162A：日常增量（--since-baseline）与显式候选（--artifact）
#
# 设计口径：
#   * 日常增量只覆盖「基线 → 当前」：工作区新增/修改文件 + 新增提交的元数据与新增行。
#   * 增量绝不静默退化为只扫 HEAD；基线不存在、不是 HEAD 祖先、或引用集合无法确认覆盖时，
#     抛 BaselineBlocked，由 main 返回退出码 4。
#   * --artifact 只接受当前仓库根内的显式候选；ZIP 只在内存中读取条目，绝不调用 extract；
#     二进制/图片只登记「待人工目检」，不写成「已通过」。
#   * 默认（不带增量参数）仍是发布前全量：工作区 + --all 全部历史，口径不变。
# ---------------------------------------------------------------------------
INCREMENTAL_EXIT_BLOCKED = 4

# 限额读取：候选可以很大，但扫描器不能因此把整包读进内存或解压到外部。
MAX_ARTIFACT_FILES = 2000
MAX_ARTIFACT_TEXT_BYTES = 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_ARCHIVE_ENTRY_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_TEXT_BYTES = 1024 * 1024
ZIP_BOMB_MIN_BYTES = 1024 * 1024
ZIP_BOMB_RATIO = 100


class BaselineBlocked(RuntimeError):
    """基线存在/祖先关系/引用集合无法确认覆盖：必须阻塞（退出码 4）。"""


class ArtifactPathError(RuntimeError):
    """--artifact 路径无效或越界（退出码 2）。"""


def _run_git_allow_fail(*args: str) -> tuple[int, str, str]:
    """执行只读 git 命令，允许失败：返回 (退出码, stdout, stderr)。"""
    cmd = ["git", "-c", "core.quotepath=off", *args]
    env = dict(os.environ)
    env["GIT_PAGER"] = "cat"
    try:
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, env=env)
    except FileNotFoundError as exc:
        raise RuntimeError("找不到 git：%s" % exc) from exc
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


def _git_output(*args: str) -> str:
    rc, out, err = _run_git_allow_fail(*args)
    if rc != 0:
        raise RuntimeError("git %s 失败（退出码 %d）：%s" % (" ".join(args), rc, err.strip()[:400]))
    return out


def resolve_commit(ref: str) -> str | None:
    """把分支/tag/短哈希解析成完整提交哈希；不存在或不是提交时返回 None。"""
    rc, out, _ = _run_git_allow_fail("rev-parse", "--verify", "--quiet", ref + "^{commit}")
    if rc != 0:
        return None
    return out.strip() or None


def _rev_set(*args: str) -> set[str]:
    return set(_git_output("rev-list", *args).split())


def changed_worktree_files(base: str) -> list[str]:
    """基线到当前工作树的新增/修改文件（含已提交与未提交；删除的文件不算）。"""
    raw = _git_output("diff", "--name-only", "-z", "--diff-filter=ACMRT", base, "--")
    return [p for p in raw.split("\x00") if p]


def validate_baseline(ref: str) -> dict:
    """校验增量基线；不能确认覆盖时抛 BaselineBlocked，绝不退化为只扫 HEAD。"""
    head = resolve_commit("HEAD")
    if head is None:
        raise RuntimeError("无法解析 HEAD（当前仓库可能还没有提交）")
    base = resolve_commit(ref)
    if base is None:
        raise BaselineBlocked(
            "基线不存在或不是提交：%s（请给出已存在的提交/分支/tag，例如已发布 tag 或上次扫描的 HEAD）" % ref
        )
    rc_shallow, out_shallow, _ = _run_git_allow_fail("rev-parse", "--is-shallow-repository")
    if rc_shallow == 0 and out_shallow.strip().lower() == "true":
        raise BaselineBlocked("当前仓库是浅克隆（shallow clone）：基线之前的历史不完整，无法确认增量覆盖")
    rc_anc, _, _ = _run_git_allow_fail("merge-base", "--is-ancestor", base, head)
    if rc_anc == 1:
        raise BaselineBlocked(
            "基线 %s（%s）不是 HEAD（%s）的祖先：历史可能被改写或基线选错，拒绝只扫 HEAD"
            % (ref, base[:10], head[:10])
        )
    if rc_anc != 0:
        raise RuntimeError("git merge-base --is-ancestor 执行失败（退出码 %d）" % rc_anc)
    # 可达集合同时纳入 HEAD（支持 detached HEAD：它不在任何分支引用里，但仍是当前历史）。
    reachable = _rev_set("--all", head)
    if base not in reachable:
        raise BaselineBlocked(
            "基线 %s（%s）不在当前引用与 HEAD 的可达历史里：历史可能已被改写，无法确认增量覆盖"
            % (ref, base[:10])
        )
    # 引用集合校验：基线之后若还有提交只存在于其它引用（分支/tag/远端）而不在 HEAD 历史里，
    # 只扫 BASE..HEAD 会漏掉它们；这种情况必须阻塞，让使用者先收敛引用或改用全量。
    after_all = _rev_set("--all", head, "^" + base)
    after_head = set(_git_output("rev-list", "%s..%s" % (base, head)).split())
    extra = after_all - after_head
    if extra:
        sample = sorted(extra)[:3]
        raise BaselineBlocked(
            "基线之后有 %d 个提交只存在于其它引用、不在 HEAD 历史中（例如 %s）：增量无法确认覆盖；"
            "请先收敛分支/引用，或改用发布前全量"
            % (len(extra), "、".join(s[:10] for s in sample))
        )
    return {
        "ref": ref,
        "commit": base,
        "head": head,
        "commits": len(after_head),
        "worktree_files": len(changed_worktree_files(base)) + len(untracked_files()),
    }


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def validate_artifact_paths(raw_paths: list[str]) -> list[dict]:
    """校验 --artifact：只允许当前仓库根内的显式文件/目录；坏路径/越界整批拒绝（退出码 2）。"""
    repo = REPO_ROOT.resolve()
    resolved: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    for raw in raw_paths:
        given = Path(raw)
        absolute = given if given.is_absolute() else (Path.cwd() / given)
        if absolute.is_symlink():
            errors.append("%s（不允许符号链接候选，请给真实路径）" % raw)
            continue
        try:
            full = absolute.resolve()
        except OSError as exc:
            errors.append("%s（无法解析：%s）" % (raw, exc))
            continue
        if full == repo or not _is_within(repo, full):
            errors.append("%s（越界：只允许仓库根 %s 内的显式候选）" % (raw, PH_REPO))
            continue
        if ".git" in full.parts:
            errors.append("%s（不允许扫描 .git 内部）" % raw)
            continue
        if not full.exists():
            errors.append("%s（路径不存在）" % raw)
            continue
        if not (full.is_file() or full.is_dir()):
            errors.append("%s（既不是普通文件也不是目录）" % raw)
            continue
        key = str(full)
        if key in seen:
            continue
        seen.add(key)
        resolved.append({"path": full, "display": full.relative_to(repo).as_posix()})
    if errors:
        raise ArtifactPathError("；".join(errors))
    return resolved


def _artifact_hit(
    category: str,
    rule: str,
    severity: str,
    location: str,
    matched: str,
    evidence: str,
    note: str = "",
) -> Hit:
    return Hit(
        category=category,
        rule=rule,
        severity=severity,
        scope="artifact",
        location=location,
        matched=matched[:200],
        evidence=evidence[:240],
        note=note,
    )


def _zip_entry_name_unsafe(name: str) -> bool:
    """ZIP 条目名安全判断：拒绝 ..、绝对路径、盘符、反斜杠穿越与 NUL。"""
    if not name or "\x00" in name:
        return True
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        return True
    normalized = name.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    return any(p == ".." for p in parts)


def _zip_entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def scan_zip_artifact(full: Path, display: str) -> tuple[list[Hit], list[dict], list[dict], list[dict]]:
    """只读扫描 ZIP：条目在内存中读取，绝不解压到外部目录。

    返回 (hits, entries, manual, inventory)。穿越/炸弹/超限一律登记为 block 并跳过读取；
    二进制/图片只进人工清单，不给出「通过」结论。
    """
    hits: list[Hit] = []
    entries: list[dict] = []
    manual: list[dict] = []
    inventory: list[dict] = []
    try:
        zf = zipfile.ZipFile(full)
    except (zipfile.BadZipFile, OSError) as exc:
        hits.append(
            _artifact_hit(
                "artifact",
                "artifact_zip_unreadable",
                "block",
                display,
                "无法读取 ZIP",
                "%s: %s" % (type(exc).__name__, exc),
                "；显式候选 ZIP 打不开，内容无法确认，按阻塞处理",
            )
        )
        return hits, entries, manual, inventory
    try:
        infos = zf.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            hits.append(
                _artifact_hit(
                    "artifact",
                    "artifact_zip_entry_limit",
                    "block",
                    display,
                    "条目数 %d 超过上限 %d" % (len(infos), MAX_ARCHIVE_ENTRIES),
                    "；为避免解压炸弹，整包未读取",
                    "；请拆分候选或由人工检查",
                )
            )
            return hits, entries, manual, inventory
        total = 0
        for info in infos:
            name = info.filename
            location = "%s!%s" % (display, name)
            if _zip_entry_name_unsafe(name):
                hits.append(
                    _artifact_hit(
                        "artifact",
                        "artifact_zip_traversal",
                        "block",
                        location,
                        "可疑条目名：%s" % name[:120],
                        "；条目名含 ..、绝对路径或盘符，拒绝读取/解压",
                        "；不解压、不落盘；由人工核对来源",
                    )
                )
                continue
            if info.is_dir():
                continue
            if _zip_entry_is_symlink(info):
                hits.append(
                    _artifact_hit(
                        "artifact",
                        "artifact_zip_symlink",
                        "block",
                        location,
                        "符号链接条目",
                        "；ZIP 内的符号链接不在扫描范围，拒绝读取",
                        "；如确需检查，请在隔离环境解包",
                    )
                )
                continue
            if info.file_size >= ZIP_BOMB_MIN_BYTES and info.compress_size > 0:
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio >= ZIP_BOMB_RATIO:
                    hits.append(
                        _artifact_hit(
                            "artifact",
                            "artifact_zip_bomb_suspected",
                            "block",
                            location,
                            "解压比 %.0f:1（%d → %d 字节）"
                            % (ratio, info.compress_size, info.file_size),
                            "；疑似解压炸弹，停止读取该条目",
                            "；不解压、不落盘",
                        )
                    )
                    continue
            if info.file_size > MAX_ARCHIVE_ENTRY_BYTES:
                hits.append(
                    _artifact_hit(
                        "artifact",
                        "artifact_zip_entry_limit",
                        "block",
                        location,
                        "条目解压后 %d 字节超过上限 %d" % (info.file_size, MAX_ARCHIVE_ENTRY_BYTES),
                        "；不读取该条目",
                        "；请拆分候选或由人工检查",
                    )
                )
                continue
            total += info.file_size
            if total > MAX_ARCHIVE_TOTAL_BYTES:
                hits.append(
                    _artifact_hit(
                        "artifact",
                        "artifact_zip_total_limit",
                        "block",
                        display,
                        "累计解压后 %d 字节超过上限 %d" % (total, MAX_ARCHIVE_TOTAL_BYTES),
                        "；剩余条目未读取",
                        "；请拆分候选或由人工检查",
                    )
                )
                break
            try:
                with zf.open(info) as fp:
                    data = fp.read(MAX_ARCHIVE_TEXT_BYTES + 8192)
            except (RuntimeError, OSError, zipfile.BadZipFile) as exc:
                hits.append(
                    _artifact_hit(
                        "artifact",
                        "artifact_zip_read_error",
                        "block",
                        location,
                        "条目读取失败",
                        "%s: %s" % (type(exc).__name__, exc),
                        "；内容无法确认",
                    )
                )
                continue
            truncated = len(data) > MAX_ARCHIVE_TEXT_BYTES
            if truncated:
                data = data[:MAX_ARCHIVE_TEXT_BYTES]
            if looks_binary(data[:8192], name):
                manual.append(
                    {
                        "path": display,
                        "entry": name,
                        "size": info.file_size,
                        "status": "待人工目检（压缩包内二进制/图片；未解压，不代表已通过）",
                    }
                )
                inventory.append(
                    {
                        "path": "%s!%s" % (display, name),
                        "kind": Path(name).suffix.lower() or "binary",
                        "size": info.file_size,
                        "manual_review": "待人工目检（不代表已通过）",
                    }
                )
                continue
            text = data.decode("utf-8", "replace")
            hits.extend(scan_text(text, location, "artifact"))
            if truncated:
                hits.append(
                    _artifact_hit(
                        "artifact",
                        "artifact_text_truncated",
                        "warn",
                        location,
                        "只读到 %d 字节" % MAX_ARCHIVE_TEXT_BYTES,
                        "；压缩包内文本超过读取上限，剩余内容未扫描",
                        "；需人工检查或拆分候选",
                    )
                )
            entries.append(
                {
                    "path": display,
                    "entry": name,
                    "size": info.file_size,
                    "status": "已扫描" + ("（截断，剩余待人工）" if truncated else ""),
                }
            )
    finally:
        zf.close()
    return hits, entries, manual, inventory


def _collect_artifact_files(root: dict) -> tuple[list[tuple[Path, str]], list[Hit]]:
    """展开 --artifact 目录；跳过符号链接与 .git，超过文件数上限时阻塞。"""
    full: Path = root["path"]
    display: str = root["display"]
    hits: list[Hit] = []
    files: list[tuple[Path, str]] = []
    if full.is_file():
        return [(full, display)], hits
    repo = REPO_ROOT.resolve()
    for dirpath, dirnames, filenames in os.walk(full):
        dirnames[:] = sorted(
            d for d in dirnames if d != ".git" and not (Path(dirpath) / d).is_symlink()
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            shown = path.relative_to(repo).as_posix() if _is_within(repo, path) else str(path)
            if path.is_symlink():
                hits.append(
                    _artifact_hit(
                        "artifact",
                        "artifact_symlink_skipped",
                        "warn",
                        shown,
                        "符号链接",
                        "；显式候选目录里的符号链接不跟随，未扫描",
                        "；确需检查请给真实路径",
                    )
                )
                continue
            try:
                resolved = path.resolve()
            except OSError as exc:
                hits.append(
                    _artifact_hit(
                        "artifact", "artifact_read_error", "warn", shown, "无法解析", str(exc),
                        "；确认路径",
                    )
                )
                continue
            if not _is_within(repo, resolved):
                hits.append(
                    _artifact_hit(
                        "artifact", "artifact_path_escape", "block", shown, "越界",
                        "；候选目录里的文件解析后落在仓库根之外", "；不读取该文件",
                    )
                )
                continue
            files.append((path, resolved.relative_to(repo).as_posix()))
            if len(files) >= MAX_ARTIFACT_FILES:
                hits.append(
                    _artifact_hit(
                        "artifact",
                        "artifact_file_limit",
                        "block",
                        display,
                        "候选文件数达到上限 %d" % MAX_ARTIFACT_FILES,
                        "；为避免长时间扫描，后续文件未纳入",
                        "；请缩小候选目录范围",
                    )
                )
                return files, hits
    return files, hits


def scan_artifact_paths(roots: list[dict]) -> dict:
    """扫描显式候选：文本逐行过规则，ZIP 只读条目，二进制/图片只登记待人工。"""
    hits: list[Hit] = []
    entries: list[dict] = []
    manual: list[dict] = []
    inventory: list[dict] = []
    candidates: list[tuple[Path, str]] = []
    for root in roots:
        got, more = _collect_artifact_files(root)
        candidates.extend(got)
        hits.extend(more)
    if not candidates:
        # 显式候选里一个文件都没有：不能给出「干净」的暗示，至少留一条 warn 让使用者确认。
        hits.append(
            _artifact_hit(
                "artifact",
                "artifact_empty_root",
                "warn",
                "、".join(root["display"] for root in roots),
                "0 个文件",
                "；显式候选里没有可扫描文件",
                "；确认路径是否选对",
            )
        )
    for full, display in candidates:
        try:
            size = full.stat().st_size
        except OSError as exc:
            hits.append(
                _artifact_hit(
                    "artifact", "artifact_read_error", "warn", display, "读取失败", str(exc),
                    "；确认路径",
                )
            )
            continue
        # 文件名本身也要过规则（候选文件名里可能带用户名/家目录）
        hits.extend(scan_text(display, display + "（候选文件名）", "artifact"))
        if full.suffix.lower() == ".zip":
            zh, ze, zm, zi = scan_zip_artifact(full, display)
            hits.extend(zh)
            entries.extend(ze)
            manual.extend(zm)
            inventory.extend(zi)
            continue
        try:
            with full.open("rb") as fp:
                data = fp.read(MAX_ARTIFACT_TEXT_BYTES + 1)
        except OSError as exc:
            hits.append(
                _artifact_hit(
                    "artifact", "artifact_read_error", "warn", display, "读取失败", str(exc),
                    "；确认路径",
                )
            )
            continue
        truncated = len(data) > MAX_ARTIFACT_TEXT_BYTES
        if looks_binary(data[:8192], display):
            info = {
                "path": display,
                "size": size,
                "status": "待人工目检（二进制/图片；不代表已通过）",
                "manual_review": True,
            }
            if Path(display).suffix.lower() in IMAGE_EXT:
                info["image"] = inspect_image(full)
            entries.append(info)
            manual.append({"path": display, "size": size, "status": info["status"]})
            hits.append(
                _artifact_hit(
                    "binary",
                    "artifact_binary",
                    "warn",
                    display,
                    Path(display).suffix.lower() or "binary",
                    "显式候选二进制/图片：扫描器不能看内容",
                    "；仅登记待人工目检，不代表已通过",
                )
            )
            continue
        if truncated:
            data = data[:MAX_ARTIFACT_TEXT_BYTES]
        text = data.decode("utf-8", "replace")
        hits.extend(scan_text(text, display, "artifact"))
        if truncated:
            hits.append(
                _artifact_hit(
                    "artifact",
                    "artifact_text_truncated",
                    "warn",
                    display,
                    "只读到 %d 字节" % MAX_ARTIFACT_TEXT_BYTES,
                    "；候选文本超过读取上限，剩余内容未扫描",
                    "；需人工检查或拆分候选",
                )
            )
        entries.append(
            {
                "path": display,
                "size": size,
                "status": "已扫描" + ("（截断，剩余待人工）" if truncated else ""),
            }
        )
    uniq: dict[tuple, Hit] = {}
    for h in hits:
        uniq.setdefault(h.key(), h)
    return {
        "hits": list(uniq.values()),
        "entries": entries,
        "manual": manual,
        "inventory": inventory,
        "files": len(candidates),
    }


def build_result(args) -> dict:
    started = _dt.datetime.now().astimezone()
    PUBLISHED_COMMITS.clear()
    PUBLISHED_COMMITS.update(published_commits())
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
    text_ext_counts: dict[str, int] = {}
    coverage_rows: list[dict] = []

    want_worktree = not args.history_only
    want_history = not args.worktree_only

    # 162A 三种模式：默认（不给增量参数）仍是「工作区 + 全部历史」的发布前全量，
    # 保持 17/39/159 的既有口径不变。
    baseline = None
    scan_kind = "full"
    artifact_roots: list[dict] = []
    artifact_hits: list[Hit] = []
    artifact_entries: list[dict] = []
    artifact_manual: list[dict] = []
    artifact_inventory: list[dict] = []
    artifact_files = 0
    history_revspec = "--all"

    artifact_args = list(getattr(args, "artifact", None) or [])
    baseline_ref = getattr(args, "since_baseline", None)
    if artifact_args:
        # 路径校验先做：坏路径/越界要立刻退出，不能退化成「扫了别的」。
        artifact_roots = validate_artifact_paths(artifact_args)
    if baseline_ref:
        if args.worktree_only or args.history_only:
            raise RuntimeError(
                "--since-baseline 不能与 --worktree-only/--history-only 同时使用（增量必须同时看工作区与历史）"
            )
        baseline = validate_baseline(baseline_ref)
        scan_kind = "incremental"
        want_worktree = True
        want_history = True
    elif artifact_args:
        if args.worktree_only or args.history_only:
            raise RuntimeError(
                "--artifact 不能与 --worktree-only/--history-only 同时使用（只查显式候选时不要限定扫描方向）"
            )
        # 只查显式候选：不扫工作区全量、不扫全部历史，避免日常检查触发全量成本。
        scan_kind = "artifact"
        want_worktree = False
        want_history = False

    if want_worktree:
        if baseline is not None:
            # 工作区增量：基线→工作树的全部新增/修改文件（含已提交与未提交），
            # 加上尚未跟踪、但未被 .gitignore 排除的新文件（待审二进制也在这里）。
            paths = changed_worktree_files(baseline["commit"])
            known = set(paths)
            extra = [p for p in untracked_files() if p not in known]
            untracked_count = len(extra)
            paths = paths + extra
        else:
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
            _ext = Path(rel).suffix.lower()
            text_ext_counts[_ext] = text_ext_counts.get(_ext, 0) + 1
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
        # 覆盖清单只对发布前全量有意义：日常增量没有动到的目录当然可能是 0 个文件。
        if scan_kind == "full":
            coverage_rows = coverage_report(paths)
            worktree_hits.extend(coverage_hits(coverage_rows))
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

    if artifact_roots:
        artifact = scan_artifact_paths(artifact_roots)
        artifact_hits = artifact["hits"]
        artifact_entries = artifact["entries"]
        artifact_manual = artifact["manual"]
        artifact_inventory = artifact["inventory"]
        artifact_files = artifact["files"]

    if want_history:
        # 增量只看基线之后的新增提交；默认仍是 --all（发布前全量）。
        history_revspec = "%s..%s" % (baseline["commit"], baseline["head"]) if baseline else "--all"
        hist_meta_hits, records = scan_history_metadata(history_revspec)
        commit_count = len(records)
        hist_content_hits = scan_history_content(history_revspec)

    hits = worktree_hits + hist_meta_hits + hist_content_hits + artifact_hits
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
    scopes: dict = {
        "worktree": {"block": 0, "warn": 0},
        "history": {"block": 0, "warn": 0},
        "artifact": {"block": 0, "warn": 0},
    }
    published_split: dict = {"published": {"block": 0, "warn": 0}, "unpublished": {"block": 0, "warn": 0}}
    for h in hits:
        scopes.setdefault(h.scope, {"block": 0, "warn": 0})[h.severity] += 1
        if h.scope == "history":
            published_split["published" if h.published else "unpublished"][h.severity] += 1
    block_total = sum(c["block"] for c in counts.values())
    warn_total = sum(c["warn"] for c in counts.values())
    # 只有发布前全量才配说「允许发布」；日常增量绝不冒充发布门。
    if block_total == 0:
        verdict = (
            "允许进入发布（仍需完成报告里的人工目检项）"
            if scan_kind == "full"
            else "增量扫描未发现 block（不代表发布前全量已通过；186 仍需全量）"
        )
    else:
        verdict = (
            "不允许发布：block 未清零"
            if scan_kind == "full"
            else "增量扫描发现 block：未通过，须先处置"
        )
    mode_labels = {
        "full": "发布前全量（工作区受跟踪文件 + 全部提交历史 --all）",
        "incremental": "日常增量（基线 %s → HEAD；工作区改动 + 新增提交）"
        % (baseline["ref"] if baseline else "?"),
        "artifact": "仅显式候选（不扫工作区全量、不扫全部历史）",
    }
    return {
        "generated_at": started.isoformat(timespec="seconds"),
        "repo_root": PH_REPO,
        "scan_mode": {
            "kind": scan_kind,
            "label": mode_labels[scan_kind],
            "release_gate": scan_kind == "full",
            "baseline": baseline,
            "artifacts": [r["display"] for r in artifact_roots],
            "history_revspec": history_revspec,
        },
        "baseline": baseline,
        "artifacts": {
            "roots": [r["display"] for r in artifact_roots],
            "files": artifact_files,
            "entries": artifact_entries,
            "manual": artifact_manual,
            "inventory": artifact_inventory,
            "note": "二进制/图片仅登记待人工目检，不代表已通过；ZIP 只读内容、不解压到外部",
        },
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
            "mode": scan_kind,
            "changed_files": len(paths),
            "tracked_files": len(paths) - untracked_count,
            "untracked_files": untracked_count,
            "scanned_files": len(paths),
            "text_files": text_files,
            "binary_files": binary_files,
            "text_extensions": dict(sorted(text_ext_counts.items(), key=lambda kv: -kv[1])),
            "scanned_self": (
                SELF_PATH.relative_to(REPO_ROOT).as_posix()
                if SELF_PATH.exists() and _is_within(REPO_ROOT, SELF_PATH)
                else (SELF_PATH.name if SELF_PATH.exists() else None)
            ),
        },
        "history": {
            "revspec": history_revspec,
            "commits": commit_count,
            "published_tag": PUBLISHED_TAG,
            "published_commits": len(PUBLISHED_COMMITS),
            "unpublished_commits": len(set(records and [r["commit"] for r in records]) - PUBLISHED_COMMITS),
        },
        "scopes": scopes,
        "published_split": published_split,
        "coverage": coverage_rows,
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
        "path_local_toolchain": "D:" + "\\" + "java21" + "\\" + "bin",
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
    # 占位化器自检：对合成样本跑一遍，block 必须清零，且占位符不被二次改写
    fix_sample = (
        "C:" + "/" + "Users" + "/" + "alice" + "/AppData "
        + "D:" + "\\" + "program" + "\\" + "Krita (x64)"
    )
    fixed, nfix = apply_fixes(fix_sample, "(selftest)")
    if nfix < 2:
        failures.append("占位化器替换不足：%r -> %r（%d 处）" % (fix_sample, fixed, nfix))
    leftover = [h for h in scan_text(fixed, "(selftest)", "worktree") if h.severity == "block"]
    if leftover:
        failures.append(
            "占位化后仍有 block：%r -> %s" % (fixed, [(h.rule, h.matched) for h in leftover])
        )

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
    parser = argparse.ArgumentParser(
        description="brushConverter 开源前隐私扫描（任务 17 / 162A）",
        epilog=(
            "口径区分（--help 必须看清）：\n"
            "  日常增量（推荐，成本低）：--since-baseline <基线提交/分支/tag>\n"
            "    只扫基线之后的工作区新增/修改文件与新增提交（作者/提交者/提交信息/新增行，\n"
            "    含后来又被删掉的敏感行）；可再叠加 --artifact。它不能替代发布门，\n"
            "    结论不会写成「允许发布」。\n"
            "  发布前全量（186 候选冻结后集中执行一次）：不带任何增量参数。\n"
            "    工作区 + --all 全部提交历史；冻结后应加 --include-untracked --fail-on-block，block 必须为 0。\n"
            "  仅显式候选：只给 --artifact 时，只扫这些候选（含被 .gitignore 忽略的 ZIP），\n"
            "    不扫工作区全量、不扫全部历史。\n"
            "退出码：0 完成；1 --fail-on-block 且 block>0；2 参数/环境错误；3 自检失败；\n"
            "        4 基线阻塞（不存在/不是 HEAD 祖先/引用集合无法确认覆盖；绝不静默退为只扫 HEAD）。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    daily = parser.add_argument_group("日常增量（推荐；不能替代发布门）")
    daily.add_argument(
        "--since-baseline",
        metavar="REF",
        help=(
            "显式基线（提交/分支/tag）。只扫基线之后的工作区改动与新增提交；"
            "基线不存在、不是 HEAD 祖先或引用集合无法确认覆盖时退出码 4（阻塞）"
        ),
    )
    daily.add_argument(
        "--artifact",
        metavar="PATH",
        action="append",
        help=(
            "可重复。显式候选文件/目录（相对路径按当前命令目录解析，解析后必须在仓库根内；"
            "允许被 .gitignore 忽略的 ZIP）。ZIP 只读条目、不解压到外部；"
            "二进制/图片只登记待人工，不代表已通过。单独使用时只扫候选，不触发全量"
        ),
    )
    full = parser.add_argument_group("发布前全量（186 候选冻结后集中执行）")
    full.add_argument(
        "--include-untracked",
        action="store_true",
        help="把尚未跟踪、也没被 .gitignore 排除的新文件一并纳入（全量收尾用）",
    )
    full.add_argument("--fail-on-block", action="store_true", help="block 命中 > 0 时退出码 1")
    scope = parser.add_argument_group("扫描范围与输出（调试/取证）")
    scope.add_argument("--worktree-only", action="store_true", help="只扫工作区（不能与增量/候选参数同用）")
    scope.add_argument("--history-only", action="store_true", help="只扫提交历史（不能与增量/候选参数同用）")
    scope.add_argument("--json", metavar="PATH", help="把完整 JSON 结果写到指定路径")
    scope.add_argument("--text", metavar="PATH", help="把文本报告写到指定路径")
    scope.add_argument("--max-per-rule", type=int, default=500, help="文本报告里同一规则最多打印多少条")
    scope.add_argument("--quiet", action="store_true", help="不打印文本报告（只写文件）")
    tool = parser.add_argument_group("自检与占位化（维护用）")
    tool.add_argument("--selftest", action="store_true", help="只跑内置自检（不读仓库、不写文件）")
    tool.add_argument(
        "--fix-worktree",
        action="store_true",
        help="预览工作区占位化（配合 --apply 才写回文件）",
    )
    tool.add_argument(
        "--fix-tree",
        action="store_true",
        help="对当前目录整棵树做占位化（供 git filter-branch --tree-filter 重写历史调用）",
    )
    tool.add_argument("--root", metavar="PATH", help="扫描根（默认仓库根；历史重写时指定原仓库根）")
    tool.add_argument("--apply", action="store_true", help="与 --fix-worktree/--fix-tree 连用：真正写回文件")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.worktree_only and args.history_only:
        print("[错误] --worktree-only 与 --history-only 不能同时使用", file=sys.stderr)
        return 2

    global SCAN_ROOT
    if args.root:
        SCAN_ROOT = Path(args.root).resolve()

    if args.fix_tree:
        RULES = build_rules(build_identity_names(), SCAN_ROOT)
        root = Path.cwd()
        stats = fix_tree(root, apply=True)
        print(
            "[fix-tree] %s：改动文件 %d 个 / 替换 %d 处 / 跳过二进制 %d 个"
            % (root, stats["files_changed"], stats["replacements"], stats["binary_skipped"])
        )
        if args.json:
            Path(args.json).write_text(
                json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return 0

    if args.fix_worktree:
        RULES = build_rules(build_identity_names(), SCAN_ROOT)
        paths = tracked_files()
        if args.include_untracked:
            known = set(paths)
            paths += [p for p in untracked_files() if p not in known]
        stats = fix_worktree(paths, apply=args.apply)
        print(
            "[fix-worktree] 模式=%s 改动文件 %d 个 / 替换 %d 处 / 跳过二进制 %d 个"
            % (
                "写回" if args.apply else "预览",
                stats["files_changed"],
                stats["replacements"],
                stats["binary_skipped"],
            )
        )
        for item in stats["details"]:
            print("  - %s（%d 处）" % (item["path"], item["replacements"]))
        if args.json:
            Path(args.json).write_text(
                json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return 0

    if not (REPO_ROOT / ".git").exists():
        print("[错误] %s 不是 git 仓库根" % REPO_ROOT, file=sys.stderr)
        return 2

    RULES = build_rules(build_identity_names(), SCAN_ROOT)

    try:
        result = build_result(args)
    except BaselineBlocked as exc:
        # 基线不能确认覆盖：阻塞退出，绝不静默退化到只扫 HEAD。
        print("[基线阻塞] " + str(exc), file=sys.stderr)
        print(
            "[基线阻塞] 未执行任何扫描，也未退化为只扫 HEAD；请处置基线/引用后重跑，或改用发布前全量。",
            file=sys.stderr,
        )
        return INCREMENTAL_EXIT_BLOCKED
    except ArtifactPathError as exc:
        print("[错误] --artifact 路径无效：" + str(exc), file=sys.stderr)
        return 2
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
