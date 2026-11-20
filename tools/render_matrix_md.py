# -*- coding: utf-8 -*-
"""从 docs/参数矩阵.json 生成 docs/参数矩阵.md（唯一生成入口，禁止手写 md）。

用法（仓库根）：
    python tools/render_matrix_md.py                 # 写 docs/参数矩阵.md
    python tools/render_matrix_md.py --check         # 只比对，不写；不一致退出码 1
    python tools/render_matrix_md.py --stdout        # 打印到 stdout（供 diff）

确定性约定：输出不含时间戳、不含绝对路径、不含随机/环境相关字段；
行序 = JSON 行序（130 条旧行按键名排序；任务 102 补的 7 条 ui.* 行追加在末尾），
分节按 VERDICT_ORDER 固定。
带尾随空格的 4CC 键在 md 里用 U+2420（SYMBOL FOR SPACE）显式标出，避免读者复制时丢掉空格。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent  # 本文件目录（用于导入同目录的 render_mode_table）

B = chr(96)          # 反引号（源码里不直接写反引号，便于本文件被其它工具串联）
SP = chr(0x2420)     # 空格符号，用于标出尾随空格

VERDICT_ORDER = ("可映射", "需校准", "无对应", "未定")
# 任务 102：UI 可见性四态（顺序固定，总览表按此顺序列出）
UI_VISIBILITY = ("有", "无", "PS UI 无（内部键）", "未定")

# 6 个带尾随空格的 4CC 键：md 里用 U+2420 显式标出空格个数。
SPACE_KEYS = ("Cnt ", "H   ", "Md  ", "Mnm ", "Nm  ", "Rpt ")

HEADER_TMPL = """# ABR → Krita 参数矩阵

> **本文件由脚本生成，禁止手改**：数据真身是 @B@docs/参数矩阵.json@B@。
> 重新生成：@B@python tools/render_matrix_md.py@B@
> 校验：@B@python tools/check_matrix.py@B@（每行 8 栏非空 / verdict 合法 / ui_visibility 枚举与格式 / 键数不低于基线 / 与三份输入 JSON 自洽）
>
> 八栏：**键名 / 语义 / 取值范围（含 clamp）/ 来源 / Krita 对应 / 结论 / UI 可见性 / UI 控件**。
> @B@verdict@B@ 只允许四个值：@B@可映射@B@ / @B@需校准@B@ / @B@无对应@B@ / @B@未定@B@。
>
> 判定口径：
> - **可映射**：Krita 侧有承载字段，换算是精确单位换算或布尔/枚举直映，无经验系数。
> - **需校准**：有承载字段，但需要经验系数、拟合公式、范围裁剪，或已知存在结构性越界。
> - **无对应**：Krita 5.3.3 笔刷预设里没有该语义的承载字段（能力表 45 条 + @B@docs/parameter-mapping.md@B@ 双证）。
> - **未定**：本轮证据不足（Krita 侧未覆盖该域，或 PS 侧语义未定）；按铁律 4 不猜，登记待 v0.2。
>
> 来源栏（多选）：@B@abr@B@ = @B@docs/数据/abr_keys.json@B@ 实测出现过；@B@re@B@ = @B@docs/数据/ps_keys.json@B@ 逆向有该键；
> @B@krita@B@ = 本行在 Krita 能力表/映射表里有明确承载字段；@B@parser@B@ = 该键作为字符串字面量出现在 @B@src/brush_converter/**/*.py@B@；
> @B@ui@B@ = 该行由任务 102 从 @B@docs/数据/ps_ui_controls.json@B@ 补入（界面有、原矩阵无）。
>
> UI 可见性栏（任务 102，取值只有四种，通用规范第 14 条）：@B@有@B@ = PS「画笔设置」对话框里有该控件（UI 控件栏写明 @B@页签/控件名@B@，多个控件用中文分号分隔）；
> @B@无@B@ = 只在工具选项栏或「画笔」面板，不在「画笔设置」对话框；@B@PS UI 无（内部键）@B@ = 容器/内部键，界面无控件；
> @B@未定@B@ = 拿不准（原因见 @B@docs/验收报告/102-报告.md@B@）。**只有 UI 可见性 = 有 的行才允许投入优化**。
>
> 带尾随空格的 4CC 键（@SP@ 代表一个空格，JSON 里是原样空格）：{space_keys}
"""
HEADER = HEADER_TMPL.replace("@B@", B).replace("@SP@", SP)


def load(path: pathlib.Path) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit("参数矩阵.json 顶层必须是数组")
    return rows


def _cell(text: str) -> str:
    """把单元格里的竖线与换行转义，避免破坏表格。"""
    return str(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _key_cell(key: str) -> str:
    """键名放在代码段里；尾随空格用 U+2420 显式标出（原文仍是空格）。"""
    shown = key
    if key in SPACE_KEYS:
        shown = key.rstrip(" ") + SP * (len(key) - len(key.rstrip(" ")))
    return B + _cell(shown) + B


def _space_keys_text() -> str:
    parts = []
    for k in SPACE_KEYS:
        parts.append(B + k.rstrip(" ") + SP * (len(k) - len(k.rstrip(" "))) + B)
    return "、".join(parts)


def _code(tok: str) -> str:
    return B + tok + B


TABLE_HEAD = ["| 键名 | 语义 | 取值范围（含 clamp） | 来源 | Krita 对应 | 结论 | UI 可见性 | UI 控件 |",
              "|---|---|---|---|---|---|---|---|"]


def _row(r: dict) -> str:
    return "| %s | %s | %s | %s | %s | %s | %s | %s |" % (
        _key_cell(r["key"]), _cell(r["semantics"]), _cell(r["range"]),
        "·".join(r["sources"]), _cell(r["krita_target"]), r["verdict"],
        r["ui_visibility"], _cell(r["ui_control"]))


def _mode_section() -> list[str]:
    """模式总表节（任务 37）：数据真身 docs/数据/mode-table.json，渲染逻辑在同目录 render_mode_table.py。

    单独成节而不是塞进 130 行的参数矩阵，是因为「模式」不是 ABR 的键：它是 textureBlendMode 的取值域，
    一个模式一行、判据与参数矩阵不同（像素比对而非单位换算）。两者共用本生成器，保证「只有一份真相」。
    """
    import importlib.util

    path = HERE / "render_mode_table.py"
    spec = importlib.util.spec_from_file_location("render_mode_table", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    table = mod.TABLE_JSON
    if not table.exists():
        raise SystemExit("找不到 %s，请先跑 python tools/render_mode_table.py --build" % table.name)
    return mod.render_section(mod.load(table)).rstrip("\n").split("\n")


def render(rows: list[dict]) -> str:
    by_verdict: dict[str, list[dict]] = {v: [] for v in VERDICT_ORDER}
    for r in rows:
        by_verdict.setdefault(r["verdict"], []).append(r)

    re_only = [r for r in rows if "re" in r["sources"] and "abr" not in r["sources"]]
    abr_only = [r for r in rows if "abr" in r["sources"] and "re" not in r["sources"]]
    parser_hit = [r for r in rows if "parser" in r["sources"]]
    krita_hit = [r for r in rows if "krita" in r["sources"]]

    out: list[str] = []
    out.append(HEADER.format(space_keys=_space_keys_text()))
    out.append("## 总览")
    out.append("")
    out.append("| 指标 | 行数 |")
    out.append("|---|---:|")
    out.append("| 矩阵总行数 | %d |" % len(rows))
    for v in VERDICT_ORDER:
        out.append("| 结论 = %s | %d |" % (v, len(by_verdict[v])))
    # 任务 102：UI 可见性四态（只有「有」的行允许投入优化）
    for v in UI_VISIBILITY:
        out.append("| UI 可见性 = %s | %d |" % (v, sum(1 for r in rows if r["ui_visibility"] == v)))
    out.append("| 来源含 %s（逆向有） | %d |" % (_code("re"), sum(1 for r in rows if "re" in r["sources"])))
    out.append("| 来源含 %s（ABR 实测有） | %d |" % (_code("abr"), sum(1 for r in rows if "abr" in r["sources"])))
    out.append("| 来源含 %s（Krita 有承载） | %d |" % (_code("krita"), len(krita_hit)))
    out.append("| 来源含 %s（仓库解析器提过） | %d |" % (_code("parser"), len(parser_hit)))
    out.append("| 来源含 %s（界面清单补入） | %d |" % (_code("ui"), sum(1 for r in rows if "ui" in r["sources"])))
    out.append("| **逆向有、ABR 样本无** | **%d** |" % len(re_only))
    out.append("| ABR 有、逆向未定位 | %d |" % len(abr_only))
    out.append("")
    out.append("## 逆向有、ABR 样本里没有的键（%d 条，按硬性要求一条未删）" % len(re_only))
    out.append("")
    out.append("这些键在 Photoshop 逆向里有证据，但在 7 个 ABR 样本（79 个预设）里一次都没出现。")
    out.append("它们**不能因为样本里没有就删掉**——样本覆盖不到不等于 PS 没有。")
    out.append("")
    out.extend(TABLE_HEAD)
    out.extend(_row(r) for r in re_only)
    out.append("")
    if abr_only:
        out.append("## ABR 样本有、逆向未定位的键（%d 条）" % len(abr_only))
        out.append("")
        out.extend(TABLE_HEAD)
        out.extend(_row(r) for r in abr_only)
        out.append("")
    for v in VERDICT_ORDER:
        out.append("## 结论 = %s（%d 条）" % (v, len(by_verdict[v])))
        out.append("")
        out.extend(TABLE_HEAD)
        out.extend(_row(r) for r in by_verdict[v])
        out.append("")
    out.extend(_mode_section())
    out.append("")
    out.append("## 生成与校验")
    out.append("")
    out.append(B * 3 + "bash")
    out.append("# 由 JSON 重新生成本文件（唯一生成入口）")
    out.append("python tools/render_matrix_md.py")
    out.append("# 校验矩阵（8 栏、verdict、ui_visibility 枚举与格式、基线行数、与三份输入 JSON 自洽）")
    out.append("python tools/check_matrix.py")
    out.append("# 模式总表（任务 37）：先由 JSON 重算，再由本脚本写入；--check 校验节是否同步")
    out.append("python tools/render_mode_table.py --build")
    out.append("python tools/render_mode_table.py --check")
    out.append(B * 3)
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 docs/参数矩阵.json 生成 docs/参数矩阵.md")
    ap.add_argument("--json", default="docs/参数矩阵.json")
    ap.add_argument("--md", default="docs/参数矩阵.md")
    ap.add_argument("--check", action="store_true", help="只比对磁盘上的 md 是否与生成结果一致")
    ap.add_argument("--stdout", action="store_true", help="输出到 stdout，不写文件")
    args = ap.parse_args(argv)

    json_path = pathlib.Path(args.json)
    md_path = pathlib.Path(args.md)
    if not json_path.exists():
        print("找不到 %s" % json_path, file=sys.stderr)
        return 2
    text = render(load(json_path))
    if args.stdout:
        sys.stdout.write(text)
        return 0
    if args.check:
        if not md_path.exists():
            print("缺少 %s" % md_path, file=sys.stderr)
            return 2
        if md_path.read_text(encoding="utf-8") != text:
            print("%s 与 JSON 不同步，请重跑 python tools/render_matrix_md.py" % md_path, file=sys.stderr)
            return 1
        print("%s 与 JSON 同步" % md_path)
        return 0
    md_path.write_text(text, encoding="utf-8", newline="\n")
    print("已写 %s（%d 行矩阵）" % (md_path, len(load(json_path))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
