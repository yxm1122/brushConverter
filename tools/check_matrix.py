# -*- coding: utf-8 -*-
"""参数矩阵校验器（v0.1 验收工具，也是以后每加一个键的回归工具）。

校验项（任何一项失败都退出码 1，并打印「第几行、哪个键、哪一栏」）：
  C1 文件存在、可解析、顶层是数组
  C2 每行恰好有八栏：
     key / semantics / range / sources / krita_target / verdict / ui_visibility / ui_control
  C3 每栏非空（sources 至少一个取值）；
     ui_visibility 非空字符串；ui_visibility=有 时 ui_control 必须非空；
     非 有 的行 ui_control 必须是空字符串（保证机器可判，不留半填状态）
  C4 verdict 只能是 可映射 / 需校准 / 无对应 / 未定
  C5 sources 取值只能是 abr / re / krita / parser / ui，且不重复
  C6 与三份输入 JSON + 仓库源码自洽：
     a) sources 含 re    <=> 该键在 docs/数据/ps_keys.json 里
     b) sources 含 abr   <=> 该键在 docs/数据/abr_keys.json 里
     c) sources 含 parser<=> 该键作为字符串字面量出现在 src/brush_converter/**/*.py
        （身份 JSON 模块 export_options.py 不参与：字段名与 ABR 键同名不是解析证据）
     d) krita_target 与 verdict 自洽：无对应 => krita_target=无对应；未定 => krita_target=未定；
        sources 含 krita <=> krita_target 不是 无对应/未定
  C7 基线防回退：总行数 >= BASELINE_KEYS；「逆向有而 ABR 无」行数 >= BASELINE_RE_ONLY
  C8 矩阵里不得出现函数地址（0x 形式的十六进制）
  C9 docs/参数矩阵.md 必须与 tools/render_matrix_md.py 的生成结果逐字节一致（禁止手改 md）
  C10 ui_visibility 只能是 有 / 无 / PS UI 无（内部键） / 未定（任务 14 铁律）
  C11 ui_visibility=有 的 ui_control 必须符合「页签/控件名」格式：
     多段用中文分号「；」分隔、逐段校验；页签必须属于 docs/数据/ps_ui_controls.json 的 8 个页签
     ∪ {底部（勾选项）}。控件名内部允许再出现「/」（界面原文「前景/背景抖动」就是这种），
     故只按第一个「/」切分页签与控件名。

用法（仓库根）：
    python tools/check_matrix.py
    python tools/check_matrix.py --matrix <另一个 json>     # 只校验指定文件（跳过 C9）
退出码：0 = 全部通过；1 = 有检查失败；2 = 输入缺失/读不了。
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent

FIELDS = ("key", "semantics", "range", "sources", "krita_target", "verdict",
          "ui_visibility", "ui_control")
VERDICTS = ("可映射", "需校准", "无对应", "未定")
SOURCE_TAGS = ("abr", "re", "krita", "parser", "ui")
# 任务 102（v0.6）：UI 可见性四态，只有「有」的行才允许投入优化（通用规范第 14 条）
UI_VISIBILITY = ("有", "无", "PS UI 无（内部键）", "未定")
# 底部 5 个勾选项在控件清单里归到这一「页签」名下（见 docs/数据/ps_ui_controls.json controls[].tab）
UI_BOTTOM_TAB = "底部（勾选项）"
UI_SEP = "；"          # ui_control 多段分隔符，中文分号（任务 102 规定）

# ── 基线（防回退阈值）──────────────────────────────────────────────────────────
# 137 = 130（= |ps_keys.json（125）∪ abr_keys.json（91）| = 125 条逆向键 + 5 条
#       「ABR 有、逆向未定位」的键（InvT/TxtC/Txtr/Wtdg/sampledData））
#     + 7  条任务 102 补的「界面有、矩阵无」参数行（ui.shapeDynamics.flipXJitter /
#       ui.shapeDynamics.flipYJitter / ui.dualBrush.size / ui.dualBrush.spacing /
#       ui.dualBrush.scatter / ui.dualBrush.bothAxes / ui.buildup）
# 依据：任务 21 交付 125 条、任务 22 交付 91 条（交集 86 条），任务 102 交付 7 条 UI 漏项。
# 以后只允许增加（新逆向键/新 ABR 键/新 UI 键），不允许减少；要下调必须同时改本常量和报告。
BASELINE_KEYS = 137
# 39 = 任务 21 报出的「逆向有、ABR 样本未见」条数；这批键按硬性要求一条都不许删，
# 所以把它也钉成基线：谁删了一条，这里立刻失败。
BASELINE_RE_ONLY = 39

ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{4,}")


# ── 读取输入 ───────────────────────────────────────────────────────────────────
def load_json(path: pathlib.Path, label: str) -> object:
    if not path.exists():
        print("FAIL [C1] 缺少输入文件 %s（%s）" % (path, label), file=sys.stderr)
        raise SystemExit(2)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print("FAIL [C1] %s 不是合法 JSON：%s" % (path, exc), file=sys.stderr)
        raise SystemExit(2)


#: C6c 只把“解析/映射模块”里的字面量当作 parser 证据。export_options.py 是
#: 任务167 的逐支身份 JSON 字段名（如 "brightness"/"flow"/"scatter"），同名纯属
#: 撞车，它不解析 ABR 键。2026-11-24 任务168 经使用者授权窄修排除该模块，
#: 不改矩阵结论、不改其它扫描范围。
PARSER_SCAN_EXCLUDED = frozenset({"export_options.py"})


def parser_literals() -> set[str]:
    """src/brush_converter/**/*.py 里的字符串字面量（ast 精确匹配，排除 docstring）。

    与任务 22 的 in_repo_parser 判据一致；本脚本自带实现，避免依赖一次性脚本。
    身份 JSON 模块（PARSER_SCAN_EXCLUDED）不参与 C6c，理由见上方常量注释。
    """
    root = REPO / "src/brush_converter"
    lits: set[str] = set()
    for p in sorted(root.rglob("*.py")):
        if p.name in PARSER_SCAN_EXCLUDED:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) \
                        and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    docs.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                lits.add(node.value)
    return lits


def load_render_module():
    """按文件路径导入同目录的 render_matrix_md，用于 C9 一致性与统计口径统一。"""
    path = HERE / "render_matrix_md.py"
    spec = importlib.util.spec_from_file_location("render_matrix_md", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 校验 ───────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验 docs/参数矩阵.json")
    ap.add_argument("--matrix", default=str(REPO / "docs/参数矩阵.json"))
    args = ap.parse_args(argv)

    matrix_path = pathlib.Path(args.matrix)
    default_path = REPO / "docs/参数矩阵.json"
    errors: list[str] = []

    def fail(check: str, where: str, msg: str) -> None:
        errors.append("FAIL [%s] %s: %s" % (check, where, msg))

    # C1
    rows = load_json(matrix_path, "参数矩阵")
    if not isinstance(rows, list):
        print("FAIL [C1] %s 顶层必须是数组" % matrix_path, file=sys.stderr)
        return 1
    if not rows:
        print("FAIL [C1] 矩阵为空（0 行）", file=sys.stderr)
        return 1

    text_all = matrix_path.read_text(encoding="utf-8")

    # 输入真值
    ps = load_json(REPO / "docs/数据/ps_keys.json", "PS 逆向键全集（任务 21）")
    abr = load_json(REPO / "docs/数据/abr_keys.json", "ABR 实测键（任务 22）")
    ps_keys = {x["key"] for x in ps}
    abr_keys = {k["key"] for k in abr["keys"]}
    lits = parser_literals()
    # C11 用：界面页签名全集（任务 101 清单；底部勾选项单列为第 9 个「页签」）
    ui_ctl = load_json(REPO / "docs/数据/ps_ui_controls.json", "PS 界面控件清单（任务 101）")
    ui_tabs = {t["tab"] for t in ui_ctl.get("tabs", [])} | {UI_BOTTOM_TAB}

    seen_keys: dict[str, int] = {}
    for idx, row in enumerate(rows, start=1):
        where = "第 %d 行" % idx
        if not isinstance(row, dict):
            fail("C2", where, "该行不是对象")
            continue
        key = row.get("key")
        if isinstance(key, str) and key:
            where = "第 %d 行（key=%r）" % (idx, key)
        # 重复键
        if isinstance(key, str):
            seen_keys[key] = seen_keys.get(key, 0) + 1

        # C2 栏位齐全（缺栏 / 多栏都算错）
        missing = [f for f in FIELDS if f not in row]
        extra = [f for f in row if f not in FIELDS]
        if missing:
            fail("C2", where, "缺栏 %s" % "、".join(missing))
            continue
        if extra:
            fail("C2", where, "多出栏位 %s（只允许八栏）" % "、".join(extra))

        # C3 每栏非空
        for f in ("key", "semantics", "range", "krita_target", "verdict"):
            v = row[f]
            if not isinstance(v, str) or not v.strip():
                fail("C3", where, "栏 %s 为空或不是字符串" % f)
        sources = row["sources"]
        if not isinstance(sources, list) or not sources:
            fail("C3", where, "栏 sources 为空或不是数组")
        else:
            if any(not isinstance(s, str) or not s.strip() for s in sources):
                fail("C3", where, "栏 sources 含空值")
            if len(set(sources)) != len(sources):
                fail("C5", where, "栏 sources 有重复取值：%r" % (sources,))
            bad = [s for s in sources if s not in SOURCE_TAGS]
            if bad:
                fail("C5", where, "栏 sources 含非法取值 %r（只允许 %s）" % (bad, "、".join(SOURCE_TAGS)))

        # C3（UI 两栏）+ C10 枚举 + C11 格式
        uiv = row["ui_visibility"]
        uic = row["ui_control"]
        if not isinstance(uiv, str) or not uiv.strip():
            fail("C3", where, "栏 ui_visibility 为空或不是字符串")
        elif uiv not in UI_VISIBILITY:
            fail("C10", where, "栏 ui_visibility=%r 非法（只允许 %s）"
                 % (uiv, "、".join(UI_VISIBILITY)))
        if not isinstance(uic, str):
            fail("C3", where, "栏 ui_control 不是字符串（必须写成字符串，无 UI 时写空串）")
        elif uiv == "有":
            if not uic.strip():
                fail("C3", where, "ui_visibility=有 但 ui_control 为空（有 UI 的行必须写界面控件）")
            else:
                for seg in uic.split(UI_SEP):
                    seg = seg.strip()
                    if not seg:
                        fail("C11", where, "ui_control 有空的「%s」段：%r" % (UI_SEP, uic))
                        continue
                    page, sep, name = seg.partition("/")
                    if not sep or not page.strip() or not name.strip():
                        fail("C11", where, "ui_control 段 %r 不符合「页签/控件名」格式" % seg)
                    elif page.strip() not in ui_tabs:
                        fail("C11", where, "ui_control 段 %r 的页签 %r 不在 8 个页签 ∪ {%s} 内：%r"
                             % (seg, page.strip(), UI_BOTTOM_TAB, uic))
        elif uic != "":
            fail("C3", where, "ui_visibility=%r 的行 ui_control 必须为空字符串（实际 %r）"
                 % (uiv, uic))

        # C4 verdict
        verdict = row["verdict"]
        if verdict not in VERDICTS:
            fail("C4", where, "栏 verdict=%r 非法（只允许 %s）" % (verdict, "、".join(VERDICTS)))

        # C6a/C6b/C6c 来源与输入自洽
        src = set(sources) if isinstance(sources, list) else set()
        if isinstance(key, str):
            if ("re" in src) != (key in ps_keys):
                fail("C6a", where, "sources 含 re 与「在 ps_keys.json 里」不一致（实际%s）"
                     % ("在" if key in ps_keys else "不在"))
            if ("abr" in src) != (key in abr_keys):
                fail("C6b", where, "sources 含 abr 与「在 abr_keys.json 里」不一致（实际%s）"
                     % ("在" if key in abr_keys else "不在"))
            if ("parser" in src) != (key in lits):
                fail("C6c", where, "sources 含 parser 与「src/brush_converter 有该字面量」不一致（实际%s）"
                     % ("有" if key in lits else "无"))
        # C6d krita_target 与 verdict / sources 自洽
        kt = row["krita_target"] if isinstance(row["krita_target"], str) else ""
        if verdict == "无对应" and kt != "无对应":
            fail("C6d", where, "verdict=无对应 但 krita_target=%r（应为 无对应）" % kt)
        if verdict == "未定" and kt != "未定":
            fail("C6d", where, "verdict=未定 但 krita_target=%r（应为 未定）" % kt)
        if kt not in ("无对应", "未定") and "krita" not in src:
            fail("C6d", where, "krita_target 写明了承载 %r 但 sources 缺 krita" % kt)
        if kt in ("无对应", "未定") and "krita" in src:
            fail("C6d", where, "sources 含 krita 但 krita_target=%r" % kt)

    # 重复键
    for key, n in sorted(seen_keys.items()):
        if n > 1:
            fail("C2", "key=%r" % key, "同一键出现 %d 行（每个键只能一行）" % n)

    # C7 基线
    if len(rows) < BASELINE_KEYS:
        fail("C7", "矩阵整体", "总行数 %d 低于基线 %d（防回退；确需下调请同时改 BASELINE_KEYS 与报告）"
             % (len(rows), BASELINE_KEYS))
    re_only = [r for r in rows if isinstance(r, dict)
               and isinstance(r.get("sources"), list)
               and "re" in r["sources"] and "abr" not in r["sources"]]
    if len(re_only) < BASELINE_RE_ONLY:
        fail("C7", "矩阵整体", "「逆向有而 ABR 无」只有 %d 行，低于基线 %d（这批键一条都不许删）"
             % (len(re_only), BASELINE_RE_ONLY))
    # re-only 集合必须正好等于 ps − abr
    expect_re_only = ps_keys - abr_keys
    got_re_only = {r.get("key") for r in re_only}
    if got_re_only != expect_re_only:
        lost = sorted(expect_re_only - got_re_only)
        extra = sorted(got_re_only - expect_re_only)
        fail("C6a", "矩阵整体", "「逆向有而 ABR 无」集合与 ps_keys−abr_keys 不一致：缺 %r / 多 %r"
             % (lost, extra))

    # C8 地址禁令
    for idx, row in enumerate(rows, start=1):
        dumped = json.dumps(row, ensure_ascii=False)
        hit = ADDRESS_RE.search(dumped)
        if hit:
            fail("C8", "第 %d 行（key=%r）" % (idx, row.get("key")), "出现疑似函数地址 %r（矩阵禁止地址）"
                 % hit.group(0))
    if ADDRESS_RE.search(text_all) and not any(e.startswith("FAIL [C8]") for e in errors):
        fail("C8", "矩阵整体", "文件里出现疑似函数地址（矩阵禁止地址）")

    # C9 md 与 JSON 同步（仅默认路径；换 --matrix 时跳过）
    if matrix_path.resolve() == default_path.resolve():
        try:
            mod = load_render_module()
            want = mod.render(rows)
            md_path = REPO / "docs/参数矩阵.md"
            if not md_path.exists():
                fail("C9", "docs/参数矩阵.md", "文件不存在，请跑 python tools/render_matrix_md.py")
            elif md_path.read_text(encoding="utf-8") != want:
                fail("C9", "docs/参数矩阵.md", "与 JSON 生成结果不一致（被手改或未重生成），请跑 python tools/render_matrix_md.py")
        except Exception as exc:                                  # noqa: BLE001
            fail("C9", "docs/参数矩阵.md", "无法与 render_matrix_md.py 结果比对：%r" % (exc,))

    # 输出
    n_undecided = sum(1 for r in rows if isinstance(r, dict) and r.get("verdict") == "未定")
    if errors:
        for e in errors:
            print(e)
        print("")
        print("=== 校验失败：%d 项 ===" % len(errors))
        return 1

    n_ui = {v: sum(1 for r in rows if isinstance(r, dict) and r.get("ui_visibility") == v)
            for v in UI_VISIBILITY}
    print("=== 参数矩阵校验通过 ===")
    print("栏位校验              = 8 栏（%s）" % " / ".join(FIELDS))
    print("矩阵总行数            = %d" % len(rows))
    print("逆向有而 ABR 无的行数 = %d" % len(re_only))
    print("未定的行数            = %d" % n_undecided)
    for v in UI_VISIBILITY:
        print("ui_visibility=%s : %d" % (v, n_ui[v]))
    print("基线（防回退）        = 总行数 >= %d，逆向独有 >= %d" % (BASELINE_KEYS, BASELINE_RE_ONLY))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
