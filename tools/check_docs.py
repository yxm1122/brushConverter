# -*- coding: utf-8 -*-
"""brushConverter 自建文档合同检查；不是 Markdown 或 Krita 官方判据。

用法：python -B tools/check_docs.py --check [--root 仓库根]
只读，不改文档、不联网、不执行文档中的命令。退出码：0 通过，1 规则失败，2 输入错误。
当前发布事实为 v1.6.0（2026-11-27）；没有另行声明的候选版本，不预设下一版本。

范围：README、docs、releases 的 Markdown；内部规划及隐藏目录不扫描。已受Git
跟踪的研究报告只检查链接；一次性验收报告正文不纳入日常维护，目录README/INDEX仍检查。
仅识别真正的 Markdown 链接/图片、引用链接、HTML href/src；代码块、行内代码、
HTML 注释和单纯文件名不是链接。现行页面按文件相对路径解析；历史研究保留旧根优先兼容。
标题锚点采用本项目约定：小写、去标点、空白变连字符，重名依次 -1、-2；显式
HTML id/name 重名报错。历史静态研究报告的“寄存器+偏移→裸字段”按项目约定
视为说明，其余目标仍检查。该规则不承诺所有阅读器一致，真实跳转仍须人工验收。
"""
from __future__ import annotations

import argparse
import html
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
import sys
from typing import NamedTuple
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parents[1]
RELEASED = "v1.6.0"
RELEASE_DATE = "2026-11-27"
CANDIDATE: str | None = None  # 仅在另有候选立项时显式指定；不虚构下一版本。
INTERNAL = ("docs/优化提示词", "docs/工作流程", ".tmp", ".git")
EVIDENCE = ("docs/验收报告", "docs/研究报告")
# 162批准、164已保留的公开旧入口；不能因当前入链少就静默删除。
COMPATIBILITY = (("docs/photoshop-texture-brightness-contrast-research.md",
                  "docs/归档/photoshop-texture-brightness-contrast-research.md"),)
STABLE_ANCHORS = {
    "docs/user-guide.md": ("gui-convert", "cli-convert", "import-krita", "outputs",
                           "tradeoffs", "candidate-status", "source-run"),
    "docs/parameter-mapping.md": ("texture-bc-current", "texture-modes-current", "residual-current"),
    "docs/developer-guide.md": ("0-文档索引先看这里", "maintenance-checks"),
}


class Issue(NamedTuple):
    file: str
    line: int
    rule: str
    reason: str


class Link(NamedTuple):
    line: int
    target: str
    label: str = ""


class Heading(NamedTuple):
    line: int
    level: int
    title: str


class Document(NamedTuple):
    path: Path
    text: str
    prose: str
    headings: list[Heading]
    anchors: dict[str, int]
    links: list[Link]
    duplicates: list[tuple[str, int]]
    missing_refs: list[tuple[str, int]]


def _blank(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def _without_blocks(text: str) -> str:
    """逐行处理围栏和注释：围栏内的未闭合HTML示例不能吞掉后续正文。"""
    result, fence, comment = [], None, False
    for line in text.splitlines(keepends=True):
        if fence:
            match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
            result.append(_blank(line))
            if match and match[1][0] == fence[0] and len(match[1]) >= fence[1] and not match[2].strip():
                fence = None
            continue
        masked, pos = "", 0
        while pos < len(line):
            if comment:
                close = line.find("-->", pos)
                end = close + 3 if close >= 0 else len(line)
                masked += _blank(line[pos:end])
                pos, comment = end, close < 0
            else:
                start = line.find("<!--", pos)
                if start < 0:
                    masked += line[pos:]
                    break
                masked += line[pos:start] + _blank(line[start:start + 4])
                pos, comment = start + 4, True
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", masked.rstrip("\r\n"))
        if match:
            fence = (match[1][0], len(match[1]))
            result.append(_blank(masked))
        elif masked.startswith("    ") or masked.startswith("\t"):
            result.append(_blank(masked))
        else:
            result.append(masked)
    return "".join(result)


def _without_code(text: str) -> str:
    pattern = r"(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)"
    return re.sub(pattern, lambda m: _blank(m[0]), text, flags=re.S)


def _plain(text: str) -> str:
    text = re.sub(r"!?\[([^]\n]*)\]\([^\n]*?\)", r"\1", text)
    text = re.sub(r"<[^>]*>", "", text)
    return html.unescape(text.replace(chr(96), "").replace("*", "").replace("~", ""))


def slug(title: str) -> str:
    title = _plain(title).lower().strip()
    return "".join("-" if c.isspace() else c for c in title if c.isalnum() or c in "_-" or c.isspace())


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids: list[tuple[str, int]] = []
        self.links: list[Link] = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value is None:
                continue
            # XML的Sensor/params等id不是文档锚点；只认实际HTML容器和a/name。
            html_tag = tag in {"a", "div", "span", "p", "section", "article", "main", "header", "footer",
                               "details", "summary", "table", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"}
            if (key == "id" and html_tag) or (tag == "a" and key == "name"):
                self.ids.append((value, self.getpos()[0]))
            if (tag == "a" and key == "href") or (tag == "img" and key == "src"):
                self.links.append(Link(self.getpos()[0], value))


def _destination(text: str, pos: int) -> tuple[str, int] | None:
    """读取圆括号内的目标；支持尖括号空白路径、转义括号和可选标题。"""
    while pos < len(text) and text[pos].isspace():
        pos += 1
    start = pos
    if pos < len(text) and text[pos] == "<":
        end = text.find(">", pos + 1)
        if end == -1 or "\n" in text[pos:end]:
            return None
        target, pos = text[pos + 1:end], end + 1
    else:
        depth = 0
        while pos < len(text):
            char = text[pos]
            if char == "\\" and pos + 1 < len(text):
                pos += 2
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                if not depth:
                    break
                depth -= 1
            if char.isspace() and not depth:
                break
            pos += 1
        target = text[start:pos]
    while pos < len(text) and text[pos].isspace():
        pos += 1
    if pos < len(text) and text[pos] in ('"', "'", "("):
        closer = ")" if text[pos] == "(" else text[pos]
        pos += 1
        while pos < len(text) and text[pos] != closer:
            pos += 2 if text[pos] == "\\" else 1
        pos += 1
        while pos < len(text) and text[pos].isspace():
            pos += 1
    if pos < len(text) and text[pos] == ")":
        return re.sub(r"\\([\\() <>])", r"\1", target), pos + 1
    return None


def _ref_id(text: str) -> str:
    return " ".join(text.split()).casefold()


def _links(text: str, inherited: dict[str, str] | None = None) -> tuple[list[Link], list[tuple[str, int]]]:
    definitions = dict(inherited or {})
    # 给定义行补一个封口，仅复用目标读取器，不把定义当成额外的使用位置。
    pattern = r"(?m)^ {0,3}\[([^]\n]+)\]:[ \t]*(.*)$"
    for match in re.finditer(pattern, text):
        dest = _destination(match[2] + ")", 0)
        if dest:
            definitions.setdefault(_ref_id(match[1]), dest[0])
    text = re.sub(pattern, lambda m: _blank(m[0]), text)
    links, missing = [], []
    pos = 0
    while pos < len(text):
        start = text.find("[", pos)
        if start == -1:
            break
        if start and text[start - 1] == "\\":
            pos = start + 1
            continue
        end, depth = start + 1, 1
        while end < len(text) and depth:
            if text[end] == "\\":
                end += 2
                continue
            depth += (text[end] == "[") - (text[end] == "]")
            end += 1
        if depth:
            break
        label = text[start + 1:end - 1]
        line = text.count("\n", 0, start) + 1
        pos = end
        # 图片可嵌在外链标签里；外层是外链不能掩盖内层私有路径或坏图片。
        if "[" in label:
            inner_links, inner_missing = _links(label, definitions)
            links.extend(Link(item.line + line - 1, item.target, item.label) for item in inner_links)
            missing.extend((ref, number + line - 1) for ref, number in inner_missing)
        if end < len(text) and text[end] == "(":
            dest = _destination(text, end + 1)
            if dest:
                links.append(Link(line, dest[0], label))
                pos = dest[1]
        elif end < len(text) and text[end] == "[":
            close = text.find("]", end + 1)
            if close != -1:
                ref = text[end + 1:close] or label
                if _ref_id(ref) in definitions:
                    links.append(Link(line, definitions[_ref_id(ref)], label))
                else:
                    missing.append((ref, line))
                pos = close + 1
        elif _ref_id(label) in definitions:
            links.append(Link(line, definitions[_ref_id(label)], label))
    return links, missing


def read_document(path: Path) -> Document:
    text = path.read_text(encoding="utf-8-sig")
    visible = _without_blocks(text)
    prose = _without_code(visible)
    headings = []
    lines = visible.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", line)
        if match:
            headings.append(Heading(index + 1, len(match[1]), match[2]))
        elif index + 1 < len(lines) and line.strip() and re.fullmatch(r" {0,3}(=+|-+)[ \t]*", lines[index + 1]):
            headings.append(Heading(index + 1, 1 if "=" in lines[index + 1] else 2, line.strip()))
    parser = _HTML()
    parser.feed(prose)
    anchors, duplicates = {}, []
    # 标题同名是合法的；后缀按全页实际已使用的标题ID分配，而非简单计数。
    for heading in headings:
        base = slug(heading.title)
        anchor, count = base, 0
        while anchor in anchors:
            count += 1
            anchor = f"{base}-{count}"
        anchors[anchor] = heading.line
    for anchor, line in parser.ids:
        if anchor in anchors:
            duplicates.append((anchor, line))
        else:
            anchors[anchor] = line
    links, refs = _links(prose)
    return Document(path, text, prose, headings, anchors, links + parser.links, duplicates, refs)


def _under(path: str, prefix: str) -> bool:
    path, prefix = path.casefold(), prefix.casefold()
    return path == prefix or path.startswith(prefix + "/")


def _private(path: str) -> bool:
    return any(_under(path, p) for p in INTERNAL) or path.casefold() == "docs/验收报告/187-报告.md".casefold()


def _history(path: str) -> bool:
    return (any(_under(path, p) for p in EVIDENCE) and Path(path).name not in ("README.md", "INDEX.md")) or (
        _under(path, "docs/归档") and Path(path).name != "维护参考与归档说明.md")


def _link_candidates(root: Path, source: Path, value: str, *, history: bool = False) -> list[Path]:
    """现行相对链接不因仓库根存在同名文件而假通过；旧研究证据仅保留解析兼容。"""
    if not value:
        return [source]
    if value.startswith("/"):
        return [root / value.lstrip("/")]
    if history:
        return [root / value, source.parent / value]
    return [source.parent / value]


def _tracked_reports(root: Path) -> set[str]:
    """公开证据只取Git库存；失败不退化为全收本地报告。合成无Git根不含公开报告。"""
    if not (root / ".git").exists():
        return set()
    proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", *EVIDENCE],
                          capture_output=True, timeout=30)
    if proc.returncode:
        raise OSError("Git报告库存不可用；不能确认公开可见性")
    return set(proc.stdout.decode("utf-8").split("\x00"))


def _walk_error(error: OSError) -> None:
    raise error


def discover(root: Path) -> list[Path]:
    tracked = _tracked_reports(root)
    paths = [root / "README.md"] if (root / "README.md").is_file() else []
    # Path.walk避免进入工作区内部目录；不跟随目录链接。
    for folder in (root / "docs", root / "releases"):
        if not folder.is_dir():
            continue
        for base, dirs, files in folder.walk(follow_symlinks=False, on_error=_walk_error):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not _private((base / d).relative_to(root).as_posix()))
            for name in sorted(files):
                path = base / name
                relative = path.relative_to(root).as_posix()
                local_report = (any(_under(relative, p) for p in EVIDENCE)
                                and name not in ("README.md", "INDEX.md") and relative not in tracked)
                one_shot = _under(relative, "docs/验收报告") and name not in ("README.md", "INDEX.md")
                if path.suffix.lower() == ".md" and not local_report and not one_shot and not _private(relative):
                    paths.append(path)
    return sorted(paths)


def check_paths(root: Path, paths: list[Path]) -> list[Issue]:
    """供合成夹具复用的只读链接检查；CLI另外强制执行完整页面合同。"""
    root = root.resolve()
    issues: list[Issue] = []
    cache: dict[Path, Document] = {}

    def load(path: Path) -> Document:
        if path not in cache:
            cache[path] = read_document(path)
        return cache[path]

    for source in paths:
        source = source.resolve()
        if not source.is_relative_to(root):
            issues.append(Issue("<输入>", 1, "D_INPUT", "输入文件越出仓库根，不读取"))
            continue
        name = source.relative_to(root).as_posix()
        try:
            doc = load(source)
        except (OSError, UnicodeError) as exc:
            issues.append(Issue(name, 1, "D_INPUT", f"不能读取Markdown：{type(exc).__name__}"))
            continue
        for anchor, line in doc.duplicates:
            issues.append(Issue(name, line, "D_ANCHOR_DUP", f"显式锚点 {anchor!r} 重名；不能唯一定位"))
        for ref, line in doc.missing_refs:
            issues.append(Issue(name, line, "D_REFERENCE", f"引用链接 [{ref}] 没有目标定义"))
        for link in doc.links:
            line, target = link.line, link.target
            try:
                url = urlsplit(html.unescape(target))
            except ValueError:
                issues.append(Issue(name, line, "D_LINK", f"链接目标无法解析：{target}"))
                continue
            if url.scheme.lower() in ("http", "https", "mailto", "ftp", "tel", "data") or (url.netloc and not url.scheme):
                continue
            if url.scheme:
                issues.append(Issue(name, line, "D_LINK", f"不允许本机绝对路径或执行型链接：{target}"))
                continue
            path_text, fragment = unquote(url.path), unquote(url.fragment)
            if "\x00" in path_text or "\\" in path_text:
                issues.append(Issue(name, line, "D_LINK", f"路径含空字符或反斜线，应使用仓库相对URL：{target}"))
                continue
            candidates = [p.resolve() for p in _link_candidates(root, source, path_text, history=_history(name))]
            safe = [p for p in candidates if p.is_relative_to(root)]
            dest = next((p for p in safe if p.exists()), safe[0] if safe else None)
            if dest is None:
                issues.append(Issue(name, line, "D_PATH_ESCAPE", f"链接越出仓库根：{target}"))
                continue
            private_targets = [dest, (root / path_text.lstrip("/")).resolve()] if path_text else [dest]
            if not _history(name) and any(p.is_relative_to(root) and _private(p.relative_to(root).as_posix())
                                          for p in private_targets):
                issues.append(Issue(name, line, "D_PRIVATE", f"公开入口引用内部规划、预写报告或原始证据：{target}"))
                continue
            if not dest.exists():
                register = r"(?:r(?:[abcd]x|[sd]i|[bs]p|\d{1,2}(?:d|w|b)?)|e(?:[abcd]x|[sd]i|[bs]p))\+0x[0-9a-f]+"
                if name.startswith("docs/研究报告/静态-") and re.fullmatch(register, link.label, re.I) and re.fullmatch(r"\w+", target):
                    # 使用者裁决后的项目约定：仅历史静态报告的“寄存器+偏移→裸字段”是说明。
                    # 含目录/扩展名/锚点的真实目标、其它页面及其它标签仍按链接检查。
                    continue
                issues.append(Issue(name, line, "D_LINK_MISSING", f"目标不存在：{target}"))
                continue
            if not fragment:
                continue
            line_ref = re.fullmatch(r"L(\d+)(?:-L?(\d+))?", fragment)
            try:
                if line_ref and dest.is_file():
                    low, high = int(line_ref[1]), int(line_ref[2] or line_ref[1])
                    total = len(dest.read_text(encoding="utf-8-sig").splitlines())
                    if not 1 <= low <= high <= total:
                        issues.append(Issue(name, line, "D_LINE_RANGE", f"{target} 行号超界或倒序（文件共{total}行）"))
                elif dest.suffix.lower() == ".md" and dest.is_file():
                    if fragment not in load(dest).anchors:
                        issues.append(Issue(name, line, "D_ANCHOR_MISSING", f"{target} 没有此锚点；请核对标题后缀或显式id"))
                elif dest.is_dir():
                    issues.append(Issue(name, line, "D_ANCHOR_MISSING", f"目录不能承载章节锚点：{target}"))
                # 非Markdown的其它片段语法不作官方正确性判断；行号片段仍须有效。
            except (OSError, UnicodeError):
                issues.append(Issue(name, line, "D_INPUT", f"不能读取链接目标：{target}"))
    return issues


def check_contract(root: Path, paths: list[Path] | None = None) -> list[Issue]:
    """约束当前入口与首个版本段；历史版本和一次性证据不套现行模板。"""
    issues = []

    def add(path, line, rule, reason):
        issues.append(Issue(path, line, rule, reason))

    required = ("README.md", "docs/user-guide.md", "docs/troubleshooting.md", "docs/developer-guide.md",
                "docs/parameter-mapping.md", "releases/RELEASE_NOTES.md")
    def load(name):
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise OSError("合同文件越出仓库根，不读取")
        return read_document(path)

    documents = {}
    for name in required:
        try:
            documents[name] = load(name)
        except (OSError, UnicodeError):
            add(name, 1, "D_REQUIRED", "缺少合同必需页面或无法读取；计划中文件不能冒充已实现")
    for name, ids in STABLE_ANCHORS.items():
        if name in documents:
            for anchor in ids:
                if anchor not in documents[name].anchors:
                    add(name, 1, "D_COMPAT", f"缺少已公开的稳定章节锚点 #{anchor}")
    for name in ("README.md", "docs/user-guide.md"):
        if name in documents and not re.search(r"[A-Za-z]{3,}[^\n]*[.!]", "\n".join(documents[name].prose.splitlines()[:10])):
            add(name, 1, "D_ABSTRACT", "文首须有1–3句英文简介；不能用代码示例代替")
    readme = documents.get("README.md")
    if readme:
        screen = "\n".join(_without_blocks(readme.text).splitlines()[:3])
        if not re.search(r"已发布\s*" + re.escape(RELEASED) + r"(?!\d|\.\d)", _plain(screen)):
            add("README.md", 1, "D_VERSION", f"首三行必须标已发布 {RELEASED}，不得将旧版或候选充当当前下载版")
        if not any(RELEASED in link.target and "/releases/" in link.target and link.line <= 3 for link in readme.links):
            add("README.md", 1, "D_DOWNLOAD", f"首三行缺少已发布 {RELEASED} 的下载入口")
        for section in ("已知限制", "升级必读"):
            if not any(section in h.title for h in readme.headings):
                add("README.md", 1, "D_SECTION", f"缺少必需章节：{section}")
    mapping = documents.get("docs/parameter-mapping.md")
    if mapping:
        for number in range(1, 11):
            if not any(h.level == 2 and re.match(rf"{number}\.\s", h.title) for h in mapping.headings):
                add("docs/parameter-mapping.md", 1, "D_SECTION", f"缺少兼容的第{number}节；逐字段参考§1–§10不能静默删改")
    trouble = documents.get("docs/troubleshooting.md")
    if trouble:
        sections = [h for h in trouble.headings if h.level == 2]
        first = trouble.prose.splitlines()[sections[0].line - 1:sections[1].line - 1 if len(sections) > 1 else None] if sections else []
        if not re.search(r"原始.{0,20}(?:ABR|abr)|(?:ABR|abr).{0,20}直读", "\n".join(first)):
            add("docs/troubleshooting.md", sections[0].line if sections else 1, "D_SECTION", "第一项排错须先排除原始ABR直读与转换产物混淆")
    notes_name = "releases/RELEASE_NOTES.md"
    notes = documents.get(notes_name)
    if notes:
        top = next((h for h in notes.headings if h.level == 1), None)
        expected_title = (f"brushConverter {CANDIDATE}（候选 · 待发布，未发布）" if CANDIDATE
                          else f"brushConverter {RELEASED}（{RELEASE_DATE}）")
        if not top or top.title != expected_title:
            add(notes_name, top.line if top else 1, "D_VERSION", f"第一段标题必须为 {expected_title}")
        if top:
            end = next((h.line for h in notes.headings if h.level == 1 and h.line > top.line), len(notes.text.splitlines()) + 1)
            headings = [h for h in notes.headings if top.line < h.line < end]
            # 合同写“五项 + 可选根因说明”；不把历史各段强制重写为新模板。
            for title in ("主要变化", "升级注意", "已知限制", "验证", "回退方式"):
                section = next((h for h in headings if h.level == 2 and h.title == title), None)
                if not section:
                    add(notes_name, top.line, "D_SECTION", f"当前版本段缺少必需章节：{title}")
                else:
                    stop = next((h.line for h in headings if h.line > section.line and h.level <= section.level), end)
                    # 非空检查保留命令正文；代码不是链接，不代表它不能构成章节内容。
                    body = "\n".join(notes.text.splitlines()[section.line:stop - 1])
                    body = re.sub(r"<!--.*?(?:-->|\Z)", "", body, flags=re.S)
                    body = re.sub(r"(?m)^ {0,3}#{1,6}\s+.*$", "", body).strip(" \n\r->" + chr(96))
                    if not body:
                        add(notes_name, section.line, "D_SECTION", f"当前版本章节{title}内容为空；可写待回填，不能空节")
            intro_end = next((h.line for h in headings if h.level == 2), end)
            intro = "\n".join(notes.prose.splitlines()[top.line:intro_end - 1])
            if not re.search(r"[A-Za-z]{3,}[^\n]*[.!]", intro):
                add(notes_name, top.line, "D_ABSTRACT", "当前版本段首缺少英文简介；旧版本段不强行补写")
            body_lines = _without_blocks(notes.text).splitlines()[top.line - 1:end - 1]
            for offset, text in enumerate(body_lines, top.line) if CANDIDATE else []:
                if re.search(r"(?:发布日期|发布于)\s*[:：]?\s*20\d{2}[-/.年]\d", _plain(text)) or re.search(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}", top.title):
                    add(notes_name, offset, "D_CANDIDATE", "未发布候选不得填写发布日期；不是禁止记录测试时点")
                if re.search(r"(?:git\s+tag|tag\s*[:：=])[^\n]*" + re.escape(CANDIDATE), text, re.I):
                    add(notes_name, offset, "D_CANDIDATE", "候选不得提前指定发布tag；由批准发布任务创建")
    live_documents = dict(documents)
    for path in paths if paths is not None else discover(root):
        name = path.relative_to(root).as_posix()
        if name not in live_documents and not _history(name):
            try:
                live_documents[name] = load(name)
            except (OSError, UnicodeError):
                pass  # 输入错误由链接层报告，不冒充通过。
    for name, doc in live_documents.items():
        # 版本标签在行内代码中也可能是真实声明；只剔除围栏命令示例，不掩盖这种声明。
        visible_lines = _without_blocks(doc.text).splitlines()
        second_version = None
        if name == notes_name:
            version_headings = [h for h in doc.headings if h.level == 1]
            second_version = version_headings[1].line if len(version_headings) > 1 else None
            if second_version:
                visible_lines = visible_lines[:second_version - 1]
        for number, text in enumerate(visible_lines, 1):
            text = _plain(text)
            if CANDIDATE:
                if re.search(r"(?:已发布|正式版|最新稳定版)\s*[:：]?\s*" + re.escape(CANDIDATE) + r"(?!\d|\.\d)", text) or re.search(re.escape(CANDIDATE) + r"\s*[（(:：]?\s*(?:已发布|正式版|最新稳定版)", text):
                    add(name, number, "D_CANDIDATE", f"{CANDIDATE} 尚未发布，不能标成正式版或稳定版")
                if re.search(r"\b(?:published|stable|official)(?:\s+version|\s+release)?\s*[:：]?\s*" + re.escape(CANDIDATE) + r"(?!\d|\.\d)", text, re.I):
                    add(name, number, "D_CANDIDATE", f"英文简介不能将候选{CANDIDATE}写成已发布或稳定版")
            released = re.escape(RELEASED) + r"(?!\d|\.\d)"
            stale_zh = released + r"\s*(?:为|是|仍是)?\s*[（(:：]?\s*(?:尚未发布|未发布|候选[^。；\n]{0,12}未发布)"
            stale_en = r"(?:unreleased|unpublished)\s+" + released + "|" + released + r"\s+(?:is|remains)\s+(?:an?\s+)?(?:unreleased|unpublished)"
            stale_claims = list(re.finditer(stale_zh, text)) + list(re.finditer(stale_en, text, re.I))
            negation = r"(?:不(?:表示|代表|意味着|等于)|并非|不是|does not mean|does not imply)\s*$"
            if any(not re.search(negation, text[:claim.start()], re.I) for claim in stale_claims):
                add(name, number, "D_RELEASE_STATUS", f"{RELEASED} 已发布，现行说明不能仍声明为未发布")
        for link in doc.links:
            if second_version and link.line >= second_version:
                continue  # 旧版本段的发布事实不被当前候选规则覆盖。
            if CANDIDATE and re.search(r"/releases/(?:tag|download)/" + re.escape(CANDIDATE) + r"(?:/|$|[?#])", link.target):
                add(name, link.line, "D_CANDIDATE", "不得链接尚未批准发布的候选Release或资产")
    for old, new in COMPATIBILITY:
        try:
            original, archive = load(old), load(new)
        except (OSError, UnicodeError):
            add(old, 1, "D_COMPAT", f"已批准归档须同时保留旧入口和原文：{new}")
            continue
        for anchor in archive.anchors:
            if anchor not in original.anchors:
                add(old, 1, "D_COMPAT", f"旧入口缺少归档原文的兼容锚点 #{anchor}")
        destinations = []
        for link in original.links:
            url = urlsplit(link.target)
            if not url.scheme and not url.netloc:
                value = unquote(url.path)
                candidates = [p.resolve() for p in _link_candidates(root, root / old, value)]
                destinations.append(next((p for p in candidates if p.is_relative_to(root.resolve()) and p.exists()), None))
        if (root / new).resolve() not in destinations:
            add(old, 1, "D_COMPAT", "旧入口未链接到批准的归档原文")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", required=True, help="只检查，不改文档")
    parser.add_argument("--root", type=Path, default=REPO, help="仓库根；主要用于隔离合成夹具")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        print("FAIL <输入>:1 [D_INPUT] 仓库根不存在", file=sys.stderr)
        return 2
    try:
        paths = discover(root)
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        print(f"FAIL <输入>:1 [D_INPUT] 无法完整发现文档或Git库存：{type(exc).__name__}", file=sys.stderr)
        return 2
    issues = check_paths(root, paths) + check_contract(root, paths)
    for issue in sorted(set(issues)):
        print(f"FAIL {issue.file}:{issue.line} [{issue.rule}] {issue.reason}")
    print(f"文档检查：{len(paths)}份公开Markdown，{len(set(issues))}项失败；只读，外链未联网。")
    print("口径：本项目自建合同，不是Krita/Markdown官方标准；一次性验收报告正文、内部快照与本地报告排除，维护入口仍检查；研究历史保留旧链接解析，现行页面按文件相对路径检查。")
    return 2 if any(i.rule == "D_INPUT" for i in issues) else int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
