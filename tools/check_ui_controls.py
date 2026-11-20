# -*- coding: utf-8 -*-
"""任务 101：PS「画笔设置」界面控件清单的只读校验脚本。

用途
----
校验 `docs/数据/ps_ui_controls.json`（任务 101 C 线合并产物）的结构与口径，
并逐条核对 `binding.matrix_key` 是否真的存在于 `docs/参数矩阵.json`。

本脚本**只读**：不写任何文件、不改任何配置、不改矩阵，可重复跑（同样输入出同样输出、同样退出码）。

退出码
------
* `0` 全部通过；
* `1` 校验不通过（逐条打印「第几条 / 哪个 id / 哪一项」）；
* `2` 输入缺失或无法解析（文件不存在、JSON 语法错误、顶层不是对象/数组）。

用法
----
    PYTHONIOENCODING=utf-8 python tools/check_ui_controls.py

默认路径按脚本位置推断为仓库根下的 `docs/数据/ps_ui_controls.json` 与 `docs/参数矩阵.json`，
因此无论从哪个目录调用都能跑；需要核对别处的清单时可传两个位置参数覆盖。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------- 常量口径

#: 清单顶层必须有的字段（字段名固定，任务 101 不许增删改名）
TOP_FIELDS = (
    "schema_version",
    "task",
    "generated_at",
    "ps_version",
    "source_files",
    "tabs",
    "checkboxes",
    "controls",
)

#: 顶层字符串字段（必须非空字符串）
TOP_STR_FIELDS = ("schema_version", "task", "generated_at", "ps_version")

#: 控件必须有的字段
CONTROL_FIELDS = (
    "id",
    "tab",
    "tab_order",
    "control",
    "ui_type",
    "default_hint",
    "range_hint",
    "source",
    "confidence",
    "note",
    "binding",
)

#: binding 必须有的字段
BINDING_FIELDS = ("matrix_key", "method", "confidence", "note")

#: source 枚举（截图 / 语言包 / 两者）
SOURCE_ENUM = ("screenshot", "locale", "both")

#: confidence 枚举
CONFIDENCE_ENUM = ("高", "中", "低")

#: ui_type 枚举（任务 101 约定，不许自创）
UI_TYPE_ENUM = ("数值+滑杆", "下拉", "复选", "按钮", "图案选择", "颜色")

#: 绑定方式枚举
METHOD_ENUM = ("同名直配", "文档+语义推定", "仅语义推定", "无对应矩阵键（待 102 补行）")

#: 「无对应矩阵键」这个取值的原样字符串，用于判定 matrix_key 必须为 null
METHOD_NO_KEY = "无对应矩阵键（待 102 补行）"

#: 页签 tab_order 取值
TAB_ORDERS = tuple(range(1, 9))

#: 底部勾选项统一使用的 tab 名与 tab_order
BOTTOM_TAB = "底部（勾选项）"
BOTTOM_ORDER = 9

#: 底部勾选项的 order 取值
CHECKBOX_ORDERS = tuple(range(9, 14))

#: 低置信条目 note 的最短长度（太短等于没写理由）
MIN_LOW_CONF_NOTE = 8


class InputError(Exception):
    """输入缺失或无法解析（退出码 2）。"""


# ---------------------------------------------------------------- 读输入


def load_json(path: Path, label: str) -> object:
    """只读打开 JSON；不存在或语法错就抛 InputError。"""
    if not path.is_file():
        raise InputError(f"输入缺失：{label} 不存在 → {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise InputError(f"输入无法解析：{label} 不是合法 JSON（{path}）→ {exc}") from exc
    except OSError as exc:
        raise InputError(f"输入无法读取：{label}（{path}）→ {exc}") from exc


def repo_root() -> Path:
    """按脚本位置推断仓库根（tools/ 的上一级）。"""
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 校验主体


def is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def check_top(data: object, errors: list[str]) -> None:
    """顶层字段是否齐全、类型是否正确。"""
    if not isinstance(data, dict):
        errors.append("顶层（整体）：必须是 JSON 对象，实际是 %s" % type(data).__name__)
        return
    for field in TOP_FIELDS:
        if field not in data:
            errors.append(f"顶层（整体）：缺少字段 {field}")
    for field in TOP_STR_FIELDS:
        if field in data and not is_nonempty_str(data[field]):
            errors.append(f"顶层（整体）：字段 {field} 必须是非空字符串")
    if data.get("schema_version") not in (None, "1.0"):
        errors.append(f"顶层（整体）：schema_version 必须为 \"1.0\"，实际 {data.get('schema_version')!r}")
    src = data.get("source_files")
    if not isinstance(src, list) or not src or not all(is_nonempty_str(s) for s in src):
        errors.append("顶层（整体）：source_files 必须是非空字符串数组")
    for field in ("tabs", "checkboxes", "controls"):
        if field in data and not isinstance(data[field], list):
            errors.append(f"顶层（整体）：{field} 必须是数组")


def check_tabs(data: dict, errors: list[str]) -> list[dict]:
    """页签：恰好 8 个、tab_order 1..8 不重不漏、tab 名非空。返回合法页签列表。"""
    tabs = data.get("tabs")
    if not isinstance(tabs, list):
        return []
    if len(tabs) != 8:
        errors.append(f"tabs（页签表）：必须恰好 8 个，实际 {len(tabs)} 个")
    seen_orders: list[object] = []
    seen_names: list[str] = []
    for idx, tab in enumerate(tabs, start=1):
        if not isinstance(tab, dict):
            errors.append(f"tabs 第 {idx} 条：必须是对象")
            continue
        for field in ("tab_order", "tab", "task_book_name", "screenshot", "note"):
            if field not in tab:
                errors.append(f"tabs 第 {idx} 条：缺少字段 {field}")
        order = tab.get("tab_order")
        name = tab.get("tab")
        if order not in TAB_ORDERS:
            errors.append(f"tabs 第 {idx} 条（{name!r}）：tab_order 必须是 1..8，实际 {order!r}")
        if not is_nonempty_str(name):
            errors.append(f"tabs 第 {idx} 条：tab 名不能为空")
        elif name != name.strip():
            errors.append(f"tabs 第 {idx} 条（{name!r}）：tab 名首尾不许有空白")
        if not is_nonempty_str(tab.get("task_book_name")):
            errors.append(f"tabs 第 {idx} 条（{name!r}）：task_book_name 不能为空")
        if not is_nonempty_str(tab.get("screenshot")):
            errors.append(f"tabs 第 {idx} 条（{name!r}）：screenshot 不能为空")
        if "note" in tab and not isinstance(tab["note"], str):
            errors.append(f"tabs 第 {idx} 条（{name!r}）：note 必须是字符串")
        seen_orders.append(order)
        if isinstance(name, str):
            seen_names.append(name)
    # 不重不漏
    orders = [o for o in seen_orders if o in TAB_ORDERS]
    dup = [o for o, n in Counter(orders).items() if n > 1]
    if dup:
        errors.append(f"tabs（页签表）：tab_order 重复 {sorted(dup)}")
    missing = [o for o in TAB_ORDERS if o not in orders]
    if missing:
        errors.append(f"tabs（页签表）：tab_order 缺 {missing}")
    dup_names = [n for n, c in Counter(seen_names).items() if c > 1]
    if dup_names:
        errors.append(f"tabs（页签表）：tab 名重复 {dup_names}")
    return [t for t in tabs if isinstance(t, dict)]


def check_checkboxes(data: dict, errors: list[str]) -> list[dict]:
    """底部勾选项：恰好 5 个、order 9..13、control 非空。"""
    boxes = data.get("checkboxes")
    if not isinstance(boxes, list):
        return []
    if len(boxes) != 5:
        errors.append(f"checkboxes（勾选项表）：必须恰好 5 个，实际 {len(boxes)} 个")
    seen_orders: list[object] = []
    for idx, box in enumerate(boxes, start=1):
        if not isinstance(box, dict):
            errors.append(f"checkboxes 第 {idx} 条：必须是对象")
            continue
        for field in ("order", "control", "task_book_name", "screenshot"):
            if field not in box:
                errors.append(f"checkboxes 第 {idx} 条：缺少字段 {field}")
        order = box.get("order")
        control = box.get("control")
        if order not in CHECKBOX_ORDERS:
            errors.append(f"checkboxes 第 {idx} 条（{control!r}）：order 必须是 9..13，实际 {order!r}")
        if not is_nonempty_str(control):
            errors.append(f"checkboxes 第 {idx} 条：control 不能为空")
        if not is_nonempty_str(box.get("task_book_name")):
            errors.append(f"checkboxes 第 {idx} 条（{control!r}）：task_book_name 不能为空")
        if not is_nonempty_str(box.get("screenshot")):
            errors.append(f"checkboxes 第 {idx} 条（{control!r}）：screenshot 不能为空")
        seen_orders.append(order)
    orders = [o for o in seen_orders if o in CHECKBOX_ORDERS]
    dup = [o for o, n in Counter(orders).items() if n > 1]
    if dup:
        errors.append(f"checkboxes（勾选项表）：order 重复 {sorted(dup)}")
    missing = [o for o in CHECKBOX_ORDERS if o not in orders]
    if missing:
        errors.append(f"checkboxes（勾选项表）：order 缺 {missing}")
    return [b for b in boxes if isinstance(b, dict)]


def check_controls(data: dict, tabs: list[dict], boxes: list[dict], matrix_keys: set[str], errors: list[str]) -> list[dict]:
    """逐条校验控件字段、枚举、页签对应、绑定指向。返回合法控件列表。"""
    controls = data.get("controls")
    if not isinstance(controls, list):
        return []
    if not controls:
        errors.append("controls（控件表）：不能为空")
        return []

    # 页签名 → tab_order（来自 tabs 表，作为「页签 1..8」的唯一真相）
    tab_order_of = {}
    for tab in tabs:
        name = tab.get("tab")
        order = tab.get("tab_order")
        if isinstance(name, str) and isinstance(order, int):
            tab_order_of[name] = order
    allowed_tabs = set(tab_order_of) | {BOTTOM_TAB}

    seen_ids: Counter = Counter()
    for idx, ctrl in enumerate(controls, start=1):
        if not isinstance(ctrl, dict):
            errors.append(f"controls 第 {idx} 条：必须是对象")
            continue
        cid = ctrl.get("id")
        tag = f"controls 第 {idx} 条（id={cid!r}）"
        # 1) 字段齐全
        for field in CONTROL_FIELDS:
            if field not in ctrl:
                errors.append(f"{tag}：缺少字段 {field}")
        # 2) id
        if not is_nonempty_str(cid):
            errors.append(f"{tag}：id 必须是非空字符串")
        else:
            seen_ids[cid] += 1
        # 3) 字符串字段类型（允许空串，但 source/confidence 不许空）
        for field in ("default_hint", "range_hint", "note", "control", "ui_type"):
            if field in ctrl and not isinstance(ctrl[field], str):
                errors.append(f"{tag}：{field} 必须是字符串（可为空串）")
        if not is_nonempty_str(ctrl.get("control")):
            errors.append(f"{tag}：control（界面原文名）不能为空")
        # 4) 枚举
        if ctrl.get("source") not in SOURCE_ENUM:
            errors.append(f"{tag}：source 只能是 {'/'.join(SOURCE_ENUM)}，实际 {ctrl.get('source')!r}")
        if ctrl.get("confidence") not in CONFIDENCE_ENUM:
            errors.append(f"{tag}：confidence 只能是 {'/'.join(CONFIDENCE_ENUM)}，实际 {ctrl.get('confidence')!r}")
        if ctrl.get("ui_type") not in UI_TYPE_ENUM:
            errors.append(f"{tag}：ui_type 只能是 {'/'.join(UI_TYPE_ENUM)}，实际 {ctrl.get('ui_type')!r}")
        # 5) 页签与 tab_order 对应一致
        tab = ctrl.get("tab")
        order = ctrl.get("tab_order")
        if tab not in allowed_tabs:
            errors.append(f"{tag}：tab 必须是 8 个页签之一或「{BOTTOM_TAB}」，实际 {tab!r}")
        else:
            expect_order = BOTTOM_ORDER if tab == BOTTOM_TAB else tab_order_of.get(tab)
            if order != expect_order:
                errors.append(f"{tag}：tab={tab!r} 要求 tab_order={expect_order}，实际 {order!r}")
        # 6) binding
        binding = ctrl.get("binding")
        if not isinstance(binding, dict):
            errors.append(f"{tag}：binding 必须是对象")
            continue
        for field in BINDING_FIELDS:
            if field not in binding:
                errors.append(f"{tag}：binding 缺少字段 {field}")
        key = binding.get("matrix_key")
        method = binding.get("method")
        bconf = binding.get("confidence")
        bnote = binding.get("note")
        if method not in METHOD_ENUM:
            errors.append(f"{tag}：binding.method 只能是 {'/'.join(METHOD_ENUM)}，实际 {method!r}")
        if bconf not in CONFIDENCE_ENUM:
            errors.append(f"{tag}：binding.confidence 只能是 {'/'.join(CONFIDENCE_ENUM)}，实际 {bconf!r}")
        if "note" in binding and not isinstance(bnote, str):
            errors.append(f"{tag}：binding.note 必须是字符串（可为空串）")
        if key is None:
            if method != METHOD_NO_KEY:
                errors.append(f"{tag}：matrix_key 为 null 时 binding.method 必须是「{METHOD_NO_KEY}」，实际 {method!r}")
        else:
            if not is_nonempty_str(key):
                errors.append(f"{tag}：binding.matrix_key 必须是非空字符串或 null，实际 {key!r}")
            elif key not in matrix_keys:
                errors.append(f"{tag}：binding.matrix_key={key!r} 不在 docs/参数矩阵.json 的 key 集合里")
            if method == METHOD_NO_KEY:
                errors.append(f"{tag}：binding.method 写着「{METHOD_NO_KEY}」但 matrix_key={key!r} 不为 null")
        # 7) 低置信必须写理由
        low = ctrl.get("confidence") == "低" or bconf == "低"
        if low:
            note_ok = isinstance(ctrl.get("note"), str) and len(ctrl["note"].strip()) >= MIN_LOW_CONF_NOTE
            bnote_ok = isinstance(bnote, str) and len(bnote.strip()) >= MIN_LOW_CONF_NOTE
            if not (note_ok or bnote_ok):
                errors.append(
                    f"{tag}：低置信条目必须在 note 或 binding.note 写清猜的依据"
                    f"（至少 {MIN_LOW_CONF_NOTE} 字），当前两处都为空或过短"
                )

    dup_ids = [i for i, n in seen_ids.items() if n > 1]
    for cid in dup_ids:
        errors.append(f"controls（控件表）：id 重复 {cid!r}（出现 {seen_ids[cid]} 次）")
    return [c for c in controls if isinstance(c, dict)]


def check_coverage(controls: list[dict], tabs: list[dict], boxes: list[dict], errors: list[str]) -> Counter:
    """每个页签至少 1 条控件；底部 5 个勾选项各至少 1 条（按 checkboxes 的 control 名匹配）。"""
    per_tab = Counter(c.get("tab") for c in controls)
    for tab in tabs:
        name = tab.get("tab")
        if not per_tab.get(name):
            errors.append(f"覆盖（按页签）：页签 {name!r} 没有任何控件（每个页签至少 1 条）")
    bottom_names = [b.get("control") for b in boxes if is_nonempty_str(b.get("control"))]
    bottom_controls = [c.get("control") for c in controls if c.get("tab") == BOTTOM_TAB]
    for name in bottom_names:
        if name not in bottom_controls:
            errors.append(f"覆盖（底部勾选项）：勾选项 {name!r} 在 controls 里没有对应控件（tab={BOTTOM_TAB}）")
    for name in bottom_controls:
        if name not in bottom_names:
            errors.append(f"覆盖（底部勾选项）：controls 里底部控件 {name!r} 不在 checkboxes 的 control 名里")
    return per_tab


# ---------------------------------------------------------------- 输出


def print_stats(data: dict, controls: list[dict], per_tab: Counter) -> None:
    """打印统计行与按页签分布。"""
    tabs = data.get("tabs") if isinstance(data.get("tabs"), list) else []
    boxes = data.get("checkboxes") if isinstance(data.get("checkboxes"), list) else []
    low = [c for c in controls if c.get("confidence") == "低" or (c.get("binding") or {}).get("confidence") == "低"]
    bound = [c for c in controls if (c.get("binding") or {}).get("matrix_key")]
    print("统计：tabs=%d checkboxes=%d controls=%d 低置信=%d 有绑定=%d 无绑定=%d"
          % (len(tabs), len(boxes), len(controls), len(low), len(bound), len(controls) - len(bound)))
    tabs_sorted = sorted([t for t in tabs if isinstance(t, dict)],
                         key=lambda t: t.get("tab_order") or 0)
    for tab in tabs_sorted:
        name = tab.get("tab")
        print("  页签 %d %s：%d 条" % (tab.get("tab_order") or 0, name, per_tab.get(name, 0)))
    print("  底部（勾选项）：%d 条" % per_tab.get(BOTTOM_TAB, 0))
    if low:
        print("  低置信条目：")
        for c in low:
            print("    - %s（%s）" % (c.get("id"), c.get("control")))


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台按 UTF-8 输出中文
    except (AttributeError, ValueError):
        pass

    root = repo_root()
    controls_path = Path(argv[1]) if len(argv) > 1 else root / "docs" / "数据" / "ps_ui_controls.json"
    matrix_path = Path(argv[2]) if len(argv) > 2 else root / "docs" / "参数矩阵.json"

    try:
        data = load_json(controls_path, "控件清单 ps_ui_controls.json")
        matrix = load_json(matrix_path, "参数矩阵 参数矩阵.json")
    except InputError as exc:
        print("输入错误：%s" % exc)
        return 2

    if not isinstance(matrix, list):
        print("输入错误：参数矩阵 参数矩阵.json 顶层必须是数组（130 行）")
        return 2
    matrix_keys = {row.get("key") for row in matrix if isinstance(row, dict) and isinstance(row.get("key"), str)}

    errors: list[str] = []
    check_top(data, errors)
    if not isinstance(data, dict):
        print("校验失败（%d 项）：" % len(errors))
        for err in errors:
            print("  - %s" % err)
        return 1

    tabs = check_tabs(data, errors)
    boxes = check_checkboxes(data, errors)
    controls = check_controls(data, tabs, boxes, matrix_keys, errors)
    per_tab = check_coverage(controls, tabs, boxes, errors)

    print_stats(data, controls, per_tab)

    if errors:
        print("校验失败（%d 项）：" % len(errors))
        for err in errors:
            print("  - %s" % err)
        return 1
    print("校验通过：exit=0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
