# -*- coding: utf-8 -*-
"""从报告目录生成 INDEX.md（唯一生成入口，禁止手写索引）。

用法（仓库根）：
    python tools/render_archive_index.py                 # 写 docs/验收报告/INDEX.md
    python tools/render_archive_index.py --check         # 只比对，不写；不一致退出码 1
    python tools/render_archive_index.py --stdout        # 打印到 stdout（供 diff）
    python tools/render_archive_index.py --dir docs/研究报告 --md docs/研究报告/INDEX.md
    python tools/render_archive_index.py --tracked-only --stdout  # 显式公开模式，不写文件

默认 render(dir_path) 和不带 --tracked-only 的 CLI 保留旧模式的全部字节与范围。
公开模式仅索引当前 Git 仓库的公开报告；无法证明 Git/路径边界时退出 2，绝不回退。

确定性约定：输出不含时间戳、不含绝对路径、不含随机/环境相关字段；
行序按任务编号（数字前缀升序、字母后缀次之），无编号者（实验-/缺陷-）排最后按文件名排序。
描述取各报告首个 H1 标题。README.md 与 INDEX.md 自身不计入。
"""
from __future__ import annotations

import argparse
import html
import os
import pathlib
import re
import shlex
import subprocess
import sys
from urllib.parse import quote

B = chr(96)          # 反引号（源码里不直接写，便于被其它工具串联）


def _sort_key(name: str) -> tuple:
    m = re.match(r"^(\d+)([A-Za-z]*)", name)
    if m:
        return (0, int(m.group(1)), m.group(2), name)
    return (1, 0, "", name)


def _title(path: pathlib.Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ")



# 公开模式是额外的可见性门，不是隐私扫描或发布授权：
# 1. 只接受调用目录所在 Git 仓库的这两个公开报告目录，且仍只取顶层 *.md。
# 2. 只取 Git 索引中正常、无冲突的受跟踪文件；不读取未跟踪报告的正文。
# 3. 保留文件名和显式元声明优先于「已跟踪」及 public:true，不以任务编号段拒绝。
#    187-报告.md 是文档契约锁定的预写保留名；今后公开须另行审查修改本政策。
# 4. 显式声明包括页首（首个二级标题前）的可见性/类型字段、本报告本地声明、
#    独立标签或标题括号标签，以及代码围栏之外的 HTML 元注释；不是全文关键词搜索。
#    正文/章节提到「原始证据」「内部算法」不改变公开历史报告的可见性。
# 5. 没有声明的旧报告按公开目录的历史归档规则处理；本工具不推导素材或发布许可。
_PUBLIC_DIRECTORIES = {("docs", "验收报告"), ("docs", "研究报告")}
_RESERVED_REPORTS = {"docs/验收报告/187-报告.md"}
_PRIVATE_LABELS = {
    "内部", "内部报告", "内部资料", "内部规划", "仅本地", "不公开", "预写",
    "预写报告", "预写骨架", "原始证据", "internal", "private", "local-only",
    "local only", "non-public", "prewritten", "draft", "raw-evidence", "raw evidence",
}
_PUBLIC_VALUES = {"public", "true", "yes", "1", "公开", "可公开", "允许公开", "已授权公开"}
_VISIBILITY_KEYS = {
    "visibility", "archivevisibility", "indexvisibility", "archiveindex", "public", "publish",
    "可见性", "公开", "可公开", "公开性", "允许公开", "公开范围", "索引可见性", "归档可见性", "报告可见性",
}
_KIND_KEYS = {"kind", "type", "status", "类型", "文档类型", "报告类型", "状态"}
_PRIVATE_KEYS = {"private", "internal", "prewritten", "内部", "仅本地", "不公开", "预写"}
_RAW_FLAG_KEYS = {"raw", "rawevidence", "原始证据"}
_RESERVED_NAME = re.compile(
    r"^(?:\d+[A-Za-z]*[-_ .·]+)?"
    r"(?:内部(?:规划|资料|报告)?|仅本地|不公开|预写(?:报告|骨架)?|原始证据|"
    r"raw[-_ ]evidence|internal|private|local[-_ ]only)"
    r"(?=[-_ .·(（]|$)", re.IGNORECASE,
)
_PRIVATE_VALUE = re.compile(
    r"内部|仅本地|不公开|预写|原始证据|"
    r"\b(?:internal|private|confidential|local[-_ ]only|non[-_ ]public|prewritten|draft|raw[-_ ]evidence)\b",
    re.IGNORECASE,
)
_SELF_DECLARATION = re.compile(r"^(?:本(?:文件|报告|文档|地(?:任务)?报告)|[此该](?:文件|报告|文档)|此为|本文|仅供)")
_PRIVATE_SELF_TEXT = re.compile(
    r"不公开|仅本地|仅供(?:内部|本地)|本地(?:任务报告|验收材料|预写骨架)|预写(?:报告|骨架)|"
    r"不(?:提交|进入|纳入|写入|写|进)(?:到)?(?:公开(?:仓库|索引)?|公共\s*INDEX)|"
    r"(?<!不)(?:为|是|属于)\s*(?:内部(?:报告|资料|规划)|原始证据)", re.IGNORECASE,
)


class PublicIndexError(ValueError):
    """无法证明公开模式的仓库、路径或读取边界；CLI 必须退出 2。"""


def _git_output(cwd: pathlib.Path, *args: str) -> bytes:
    env = os.environ.copy()
    # 不允许外部 GIT_DIR / GIT_INDEX_FILE 把「当前仓库」悄悄换成其它来源。
    for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX"):
        env.pop(key, None)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
            cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublicIndexError("公开索引需要可用的 Git；已停止，未回退旧模式。") from exc
    if proc.returncode != 0:
        # 不回显 Git stderr，以免把本机绝对路径或配置内容写入索引/公开日志。
        raise PublicIndexError("Git 仓库或跟踪清单校验失败；已停止，未回退旧模式。")
    return proc.stdout


def _public_context(dir_path: pathlib.Path, md_path: pathlib.Path | None) -> tuple:
    try:
        cwd = pathlib.Path.cwd().resolve(strict=True)
        raw_root = _git_output(cwd, "rev-parse", "--show-toplevel").decode("utf-8").rstrip("\r\n")
        if not raw_root or "\n" in raw_root or "\r" in raw_root:
            raise PublicIndexError("Git 仓库根路径无效。")
        root = pathlib.Path(raw_root).resolve(strict=True)
        directory = dir_path.resolve(strict=True)
        destination = (md_path if md_path is not None else directory / "INDEX.md").resolve()
        if not cwd.is_relative_to(root) or directory.relative_to(root).parts not in _PUBLIC_DIRECTORIES:
            raise PublicIndexError("公开索引只允许仓库内 docs/验收报告 或 docs/研究报告 的顶层报告。")
        relative_md = destination.relative_to(root)
        if not relative_md.parts or relative_md.parts[0].casefold() == ".git":
            raise PublicIndexError("公开索引输出不能写入仓库元数据。")
        if any(ord(char) < 32 for char in relative_md.as_posix()):
            raise PublicIndexError("公开索引输出路径不能包含控制字符。")
        if not directory.is_dir():
            raise PublicIndexError("公开报告目录不存在。")
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        if isinstance(exc, PublicIndexError):
            raise
        raise PublicIndexError("公开索引路径无效或越过仓库边界；已停止。") from exc
    return root, directory, destination


def _tracked_reports(root: pathlib.Path, directory: pathlib.Path) -> dict[str, str]:
    relative_dir = directory.relative_to(root)
    raw = _git_output(root, "ls-files", "--cached", "--stage", "-z", "--full-name",
                      "--", relative_dir.as_posix() + "/")
    tracked: dict[str, str] = {}
    try:
        for record in raw.split(b"\0"):
            if not record:
                continue
            info, name = record.split(b"\t", 1)
            mode, _oid, stage = info.decode("ascii").split()
            relative = pathlib.PurePosixPath(name.decode("utf-8"))
            if relative.is_absolute() or any(part in (".", "..") for part in relative.parts):
                raise ValueError("unsafe Git path")
            if relative.parts[:-1] != relative_dir.parts or relative.suffix != ".md":
                continue  # 保持顶层 glob，不递归收录归档或原始证据子目录。
            if stage != "0":
                raise PublicIndexError("公开报告存在 Git 索引冲突；已停止。")
            tracked[relative.name] = mode
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, PublicIndexError):
            raise
        raise PublicIndexError("Git 跟踪清单无效；已停止。") from exc
    return tracked


def _declaration_is_private(line: str) -> bool:
    # 只对元声明使用这个函数，不把普通正文当成隐私标签。
    clean = re.sub(r"^[\s>#-]+", "", line).replace("*", "").replace(B, "").strip()
    labels = [clean, *re.findall(r"[\[（(【]([^\]）)】]+)[\]）)】]", clean)]
    if any(part.strip().casefold().rstrip("。.;；") in _PRIVATE_LABELS
           for label in labels for part in re.split(r"[、/，,;；|]", label)):
        return True
    field = re.match(r"^([^:：=]+)[:：=]\s*(.*?)\s*$", clean)
    if field:
        key = re.sub(r"[-_\s]", "", field.group(1)).casefold()
        value = field.group(2).split(" #", 1)[0].strip().strip("\"'").casefold()
        if key in _VISIBILITY_KEYS:
            # 未知可见性、false/no/0 与否定标签都不能被当成允许公开。
            return bool(_PRIVATE_VALUE.search(value)) or value not in _PUBLIC_VALUES
        if key in _PRIVATE_KEYS and value not in ("false", "no", "0", "否"):
            return True
        # 「原始证据：某个链接」是来源引用，不等于声明本报告就是原始证据。
        if key in _RAW_FLAG_KEYS and value in ("true", "yes", "1", "是"):
            return True
        if key in _KIND_KEYS and _PRIVATE_VALUE.search(value):
            return True
        if _SELF_DECLARATION.match(value) and _PRIVATE_SELF_TEXT.search(value):
            return True
    return bool(_SELF_DECLARATION.match(clean) and _PRIVATE_SELF_TEXT.search(clean))


def _is_private_report(text: str) -> bool:
    visible_lines = []
    fence_char = ""
    fence_size = 0
    in_prologue = True
    for line in text.splitlines():
        fence = re.match(r"^ {0,3}([\x60~]{3,})", line)
        if fence and len(set(fence.group(1))) == 1:
            marker = fence.group(1)
            if not fence_char:
                fence_char, fence_size = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size:
                fence_char = ""
            continue
        if fence_char:
            continue
        visible_lines.append(line)
        if re.match(r"^ {0,3}#{2,6}\s", line):
            in_prologue = False
        if in_prologue and _declaration_is_private(line):
            return True
    # 显式 HTML 元注释可以位于正文，但围栏中的示例不算声明。
    for comment in re.findall(r"<!--(.*?)-->", "\n".join(visible_lines), re.DOTALL):
        if any(_declaration_is_private(line) for line in comment.strip().splitlines()):
            return True
    return False


def _public_cell(text: str) -> str:
    escaped = html.escape(str(text), quote=False)
    for char in ("\\", "[", "]", "*", "_", "~"):
        escaped = escaped.replace(char, "\\" + char)
    return escaped.replace("|", "&#124;").replace(B, "&#96;").replace("\r", " ").replace("\n", " ")


def _public_command(root: pathlib.Path, directory: pathlib.Path, destination: pathlib.Path) -> str:
    return " ".join(shlex.quote(arg) for arg in (
        "python", "tools/render_archive_index.py", "--tracked-only",
        "--dir", directory.relative_to(root).as_posix(), "--md", destination.relative_to(root).as_posix(),
    ))


def _code_span(text: str) -> str:
    longest = max((len(run) for run in re.findall(B + "+", text)), default=0)
    marker = B * (longest + 1)
    return marker + " " + text + " " + marker


def _render_public(dir_path: pathlib.Path, md_path: pathlib.Path | None) -> str:
    root, directory, destination = _public_context(dir_path, md_path)
    tracked = _tracked_reports(root, directory)
    reports: list[tuple[pathlib.Path, str]] = []
    for path in sorted(directory.glob("*.md"), key=lambda p: _sort_key(p.name)):
        if path.name not in tracked or path.name.casefold() in ("readme.md", "index.md"):
            continue
        relative = path.relative_to(root).as_posix()
        if relative in _RESERVED_REPORTS or _RESERVED_NAME.match(path.stem):
            continue
        try:
            if path.is_symlink() or not path.resolve(strict=True).is_relative_to(directory):
                raise PublicIndexError("公开报告存在符号链接或越界路径；已停止。")
            if path.resolve(strict=True) == destination:
                continue
            if tracked[path.name] not in ("100644", "100755") or not path.is_file():
                raise PublicIndexError("公开报告不是正常受跟踪文件；已停止。")
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise PublicIndexError("公开报告读取失败；已停止，未写入索引。") from exc
        if not _is_private_report(text):
            title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
            reports.append((path, title))
    command = _public_command(root, directory, destination)
    out = ["# %s索引" % directory.name, "",
           "> **本文件由脚本生成，禁止手改**：重新生成 %s；" % _code_span(command),
           "> 校验 %s。" % _code_span(command + " --check"), ">",
           "> **公开索引**：仅收录公开报告目录顶层、Git 已跟踪且未声明内部/仅本地/不公开/预写/原始证据的 Markdown 报告；保留名不收录。",
           "> 跟踪状态不等于公开授权；本索引不能替代隐私审查或人工发布批准。",
           "> **证据归档，非上手必读**：每份报告是一次性任务自检/验收记录，只作历史追溯。",
           "> 现行映射结论看 %sdocs/parameter-mapping.md%s，数据真相看 %sdocs/数据/*.json%s。" % (B, B, B, B),
           "", "| 文件 | 标题 |", "|---|---|"]
    for path, title in reports:
        link = quote(pathlib.Path(os.path.relpath(path, destination.parent)).as_posix(), safe="/.-_~")
        out.append("| [%s](<%s>) | %s |" % (_public_cell(path.name), link, _public_cell(title)))
    out.extend(("", "共 %d 份。" % len(reports), ""))
    return "\n".join(out)


def render(
    dir_path: pathlib.Path, *, tracked_only: bool = False, md_path: pathlib.Path | None = None,
) -> str:
    if tracked_only:
        return _render_public(dir_path, md_path)
    files = [p for p in dir_path.glob("*.md")
             if p.name not in ("README.md", "INDEX.md")]
    files.sort(key=lambda p: _sort_key(p.name))

    out: list[str] = []
    out.append("# %s索引" % dir_path.name)
    out.append("")
    out.append("> **本文件由脚本生成，禁止手改**：重新生成 %spython tools/render_archive_index.py%s；"
               % (B, B))
    out.append("> 校验 %spython tools/render_archive_index.py --check%s。" % (B, B))
    out.append(">")
    out.append("> **证据归档，非上手必读**：每份报告是一次性任务自检/验收记录，只作历史追溯。")
    out.append("> 现行映射结论看 %sdocs/parameter-mapping.md%s，数据真相看 %sdocs/数据/*.json%s。"
               % (B, B, B, B))
    out.append("")
    out.append("| 文件 | 标题 |")
    out.append("|---|---|")
    for p in files:
        out.append("| %s%s%s | %s |" % (B, p.name, B, _cell(_title(p))))
    out.append("")
    out.append("共 %d 份。" % len(files))
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从报告目录生成 INDEX.md")
    ap.add_argument("--dir", default="docs/验收报告")
    ap.add_argument("--md", default="docs/验收报告/INDEX.md")
    ap.add_argument("--check", action="store_true", help="只比对磁盘上的 md 是否与生成结果一致")
    ap.add_argument("--stdout", action="store_true", help="输出到 stdout，不写文件")
    ap.add_argument("--tracked-only", action="store_true",
                    help="显式公开模式：Git跟踪 + 公开目录 + 元声明过滤；失败退出2，不回退")
    args = ap.parse_args(argv)

    dir_path = pathlib.Path(args.dir)
    md_path = pathlib.Path(args.md)
    if not dir_path.is_dir():
        print("找不到目录 %s" % dir_path, file=sys.stderr)
        return 2
    try:
        text = render(dir_path, tracked_only=True, md_path=md_path) if args.tracked_only else render(dir_path)
    except PublicIndexError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.stdout:
        sys.stdout.write(text)
        return 0
    if args.check:
        if not md_path.exists():
            print("缺少 %s" % md_path, file=sys.stderr)
            return 2
        try:
            existing = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            if not args.tracked_only:
                raise
            print("公开索引目标读取失败；已停止，未写入文件。", file=sys.stderr)
            return 2
        if existing != text:
            command = "python tools/render_archive_index.py"
            if args.tracked_only:
                command = " ".join(shlex.quote(arg) for arg in (
                    "python", "tools/render_archive_index.py", "--tracked-only",
                    "--dir", args.dir, "--md", args.md,
                ))
            print("%s 与目录不同步，请重跑 %s" % (md_path, command), file=sys.stderr)
            return 1
        print("%s 与目录同步" % md_path)
        return 0
    try:
        md_path.write_text(text, encoding="utf-8", newline="\n")
    except (OSError, UnicodeError):
        if not args.tracked_only:
            raise
        print("公开索引目标写入失败；已停止。", file=sys.stderr)
        return 2
    print("已写 %s" % md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())