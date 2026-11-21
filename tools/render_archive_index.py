# -*- coding: utf-8 -*-
"""从报告目录生成 INDEX.md（唯一生成入口，禁止手写索引）。

用法（仓库根）：
    python tools/render_archive_index.py                 # 写 docs/验收报告/INDEX.md
    python tools/render_archive_index.py --check         # 只比对，不写；不一致退出码 1
    python tools/render_archive_index.py --stdout        # 打印到 stdout（供 diff）
    python tools/render_archive_index.py --dir docs/研究报告 --md docs/研究报告/INDEX.md

确定性约定：输出不含时间戳、不含绝对路径、不含随机/环境相关字段；
行序按任务编号（数字前缀升序、字母后缀次之），无编号者（实验-/缺陷-）排最后按文件名排序。
描述取各报告首个 H1 标题。README.md 与 INDEX.md 自身不计入。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

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


def render(dir_path: pathlib.Path) -> str:
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
    args = ap.parse_args(argv)

    dir_path = pathlib.Path(args.dir)
    md_path = pathlib.Path(args.md)
    if not dir_path.is_dir():
        print("找不到目录 %s" % dir_path, file=sys.stderr)
        return 2
    text = render(dir_path)
    if args.stdout:
        sys.stdout.write(text)
        return 0
    if args.check:
        if not md_path.exists():
            print("缺少 %s" % md_path, file=sys.stderr)
            return 2
        if md_path.read_text(encoding="utf-8") != text:
            print("%s 与目录不同步，请重跑 python tools/render_archive_index.py" % md_path,
                  file=sys.stderr)
            return 1
        print("%s 与目录同步" % md_path)
        return 0
    md_path.write_text(text, encoding="utf-8", newline="\n")
    print("已写 %s" % md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())