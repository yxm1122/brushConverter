# -*- coding: utf-8 -*-
"""从 Photoshop 中文语言包里抽「画笔设置」界面的中文词条（任务 101 A 线，只读工具）。

用途
----
`tw10428_Photoshop_zh_CN.dat` 是 Photoshop 2025 的整包中文文案池（UTF-16，逐行
`"$$$/路径/键=中文"` 的形式，本机实测 38,043 行、含中文的取值串 35,021 条）。
本脚本**只读**打开它，用「页签候选词 / 控件候选词 / 勾选项候选词 / 取值枚举词」四张
候选表双向过滤，只把命中的词落成机器可读的 `docs/数据/ps_ui_terms_raw.json`；
绝不把整包中文倒出来，也绝不往 PS 安装目录写任何东西。

匹配口径（刻意从严，宁可少抽也不掺噪声）
----------------------------------------
1. **等值匹配**：候选词与池内「取值部分」在归一化之后必须完全相等。
   归一化 = 去首尾空白 + 去掉尾部冒号（PS 常把滑杆标签写成 `大小:` / `硬度:`，
   同一个词在 `$$$/Dialog/BrushStyler/Size=大小` 里不带冒号）；
   比的是取值，不是整行，所以 `颗粒大小` 不会污染 `大小`。
2. **别名**：只对「任务书里这么写、池里那么写」的少量词做定向别名探测，
   且限定在画笔对话框自己的命名空间前缀内（见 `ALIASES`），例如
   任务书写「画笔姿势」，PS 2025 的实际页签名是「画笔笔势」
   （`$$$/BrushStylerIndex/BrushPose`）；任务书写勾选项「喷枪」，
   对话框里的复选框标签是「建立」（`$$$/BrushStylerIndex/AirbrushBuildup`）。
3. **去重**：同一个词（归一化后）只保留**首次出现**的那一条；
   一个词同属多类时按 页签 > 勾选项 > 控件 > 取值 的顺序归一次类，跨类重叠在 stdout 里点名。
4. **确定性**：结果按 (offset, term) 排序，JSON 用固定缩进与固定键序写出；
   同一份 .dat 重复跑，输出逐字节一致（stdout 会打印 sha256 供核对）。

「双向过滤」具体指两件事，两件都做了：
  * 正向（候选词 → 池）：四张候选表逐词去池里找等值串，命中的才成为 terms；
    池里没被任何候选词认领的中文串一条都不进输出（35,021 条中文串只留下几十条）。
  * 反向（池 → 候选表）：被选中的词再回指候选表，用来①判定一个词同属多类时的归属、
    ②判定缺词、③核对任务书写法与 PS 实际写法不一致的别名。

隐私
----
输出里的 `source_file` 只写占位路径 `<LOCAL_APP_DIR>`，不写本机安装路径；
写盘前会对整份 JSON 做一次隐私自检（盘符路径 / 家目录 / 邮箱 / SID / UNC / 本机用户名），
命中即**拒绝写盘**并返回退出码 3。

用法（仓库根）
--------------
    python tools/extract_ui_terms.py                    # 默认输入/输出
    python tools/extract_ui_terms.py --audit 200        # 附带 200 条语料池抽样自检
    python tools/extract_ui_terms.py --dat <语言包路径> --out <输出 json>

环境变量
--------
`BRUSHCONVERTER_PS_LOCALE_DAT` 可覆盖默认语言包路径（默认值只是本机安装位置）。

退出码
------
0 = 抽取并写盘成功；2 = 输入缺失/解码失败/输出路径非法；3 = 隐私自检命中，未写盘。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent

# Windows 控制台默认是 GBK，摘要行里的「✓」会直接报 UnicodeEncodeError；显式改成 UTF-8
# （只影响 stdout/stderr，写文件始终用 utf-8 字节）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

DEFAULT_OUT = REPO / "docs" / "数据" / "ps_ui_terms_raw.json"
DEFAULT_DAT = pathlib.Path(
    os.environ.get(
        "BRUSHCONVERTER_PS_LOCALE_DAT",
        r"D:/program/PS2025/Adobe Photoshop 2025/Locales/zh_CN/Support Files/tw10428_Photoshop_zh_CN.dat",
    )
)

SCHEMA_VERSION = "1.0"
GENERATED_BY = "tools/extract_ui_terms.py"
# 输出里只允许出现占位路径，真实盘符路径一律不落盘（隐私门要求）
SOURCE_PLACEHOLDER = (
    r"<LOCAL_APP_DIR>\PS2025\Adobe Photoshop 2025\Locales\zh_CN\Support Files\tw10428_Photoshop_zh_CN.dat"
)

# ── 候选词表（全部来自任务 101 任务书，逐字照抄，不改写）────────────────────────
# 页签：任务书给的 8 个
TAB_TERMS = (
    "画笔笔尖形状",
    "形状动态",
    "散布",
    "纹理",
    "双重画笔",
    "颜色动态",
    "传递",
    "画笔姿势",
)
# 底部 5 个勾选项
CHECKBOX_TERMS = ("杂色", "湿边", "喷枪", "平滑", "保护纹理")
# 控件：按页签罗列，去重后 45 个（括号内是任务书里的页签分组）
CONTROL_TERMS = (
    # 画笔笔尖形状
    "大小", "翻转 X", "翻转 Y", "角度", "圆度", "硬度", "间距", "画笔大小", "预览",
    # 形状动态
    "大小抖动", "控制", "最小直径", "倾斜缩放比例", "角度抖动", "圆度抖动", "最小圆度",
    "翻转 X 抖动", "翻转 Y 抖动",
    # 散布
    "散布", "两轴", "数量", "数量抖动",
    # 纹理
    "纹理", "反相", "缩放", "亮度", "对比度", "深度", "深度抖动", "为每个笔尖设置纹理", "模式",
    # 双重画笔（模式/大小/间距/散布/两轴/数量/数量抖动/硬度 与上面重复，已去重）
    # 颜色动态
    "前景/背景抖动", "色相抖动", "饱和度抖动", "亮度抖动", "纯度",
    # 传递
    "不透明度抖动", "流量抖动", "湿度抖动", "混合抖动", "最小",
    # 画笔姿势
    "倾斜 X", "倾斜 Y", "旋转", "压力",
)
# 动态控制的取值枚举 + 弹出提示里的固定词
VALUE_TERMS = (
    "关", "渐隐", "钢笔压力", "钢笔斜度", "光笔轮", "旋转", "方向", "初始方向",
    "倾斜", "光标位置", "颜色", "棱锥形", "球形", "任意形状", "连续", "拖移", "随机", "可能值：",
)

CANDIDATE_TABLES = (
    ("tab", TAB_TERMS),
    ("checkbox", CHECKBOX_TERMS),
    ("control", CONTROL_TERMS),
    ("value", VALUE_TERMS),
)

# 别名：(任务书里的写法, 池里的实际写法, 限定命名空间前缀, 归类)
# 只在这里声明，命中证据的 raw 会带上完整键路径，方便人工核对改写关系。
# 「笔刷笔尖形状」是任务书另一处的写法（同一页签），这里只作等价核对用：
# 该词条已由页签表里的「画笔笔尖形状」命中，去重后不会重复出条目。
ALIASES = (
    ("画笔姿势", "画笔笔势", "$$$/BrushStylerIndex/", "tab"),
    ("喷枪", "建立", "$$$/BrushStylerIndex/", "checkbox"),
    ("笔刷笔尖形状", "画笔笔尖形状", "$$$/BrushStylerIndex/", "tab"),
)

# 任务书「已知缺词」四个，逐个复核实际状态（只打印，不写进 terms）
BRIEF_MISSING = ("笔刷笔尖形状", "纯度抖动", "画笔姿势", "笔尖姿势")

# 画笔设置对话框自己的命名空间：用于给「对话框内证据」计数，以及别名探测
BRUSH_NS = (
    "$$$/Dialog/BrushStyler/",
    "$$$/Dialog/BrushOptions/",
    "$$$/BrushStylerIndex/",
)

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 隐私自检模式（写盘前对本 JSON 全文扫描）
PRIVACY_PATTERNS = (
    ("盘符路径", re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")),
    ("家目录", re.compile(r"[\\/]Users[\\/]")),
    ("环境变量", re.compile(r"%USERPROFILE%|%APPDATA%|%LOCALAPPDATA%")),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")),
    ("机器 SID", re.compile(r"S-1-5-21-\d")),
    ("UNC 路径", re.compile(r"\\\\[A-Za-z0-9_.$-]+\\")),
)


# ── 读入与解码 ────────────────────────────────────────────────────────────────
def decode_pool(raw: bytes) -> tuple[str, str]:
    """解码语言包字节流，返回 (文本, 编码判定说明)。只处理 UTF-16 两种端序。"""
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le"), "UTF-16LE（BOM FF FE，已去 BOM）"
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be"), "UTF-16BE（BOM FE FF，已去 BOM）"
    try:
        return raw.decode("utf-16-le"), "UTF-16LE（无 BOM，小端解出）"
    except UnicodeDecodeError:
        return raw.decode("utf-16-be"), "UTF-16BE（无 BOM，小端失败后按大端解出）"


def parse_pool(text: str) -> tuple[list[dict], list[dict], dict]:
    """把文案池拆成「键 / 取值 / 行首字符偏移」记录。

    返回 (全部记录, 含中文的记录, 计数)。含中文的记录带 string_index（0 起），
    偏移一律按「去 BOM 后的解码文本」的字符位置计算；空取值（如 `键= `）跳过。
    """
    records: list[dict] = []
    chinese: list[dict] = []
    pos = 0
    lines = text.splitlines(keepends=True)
    kv_lines = 0
    empty_values = 0
    for line in lines:
        content = line.rstrip("\r\n")
        line_off = pos
        pos += len(line)
        eq = content.find("=")
        if eq < 0:
            continue
        kv_lines += 1
        key = content[:eq].strip().lstrip('"').strip()
        tail = content[eq + 1:]
        left_trimmed = tail.lstrip()
        lead = len(tail) - len(left_trimmed)
        value = left_trimmed.rstrip()
        if value.endswith('"'):
            value = value[:-1].rstrip()
        if not value:
            empty_values += 1
            continue
        rec = {
            "key": key,
            "value": value,
            "offset": line_off + eq + 1 + lead,
            "raw": content,
            "string_index": -1,
        }
        records.append(rec)
        if CJK_RE.search(value):
            rec["string_index"] = len(chinese)
            chinese.append(rec)
    counts = {"lines": len(lines), "kv": kv_lines, "empty": empty_values}
    return records, chinese, counts


def norm_term(text: str) -> str:
    """归一化：去首尾空白 + 去尾部冒号（中英文两种冒号）。"""
    return text.strip().rstrip(":：").strip()


def term_category() -> dict[str, str]:
    """词 → 类别；同一个词出现在多张表时，按 页签 > 勾选项 > 控件 > 取值 取第一个。"""
    mapping: dict[str, str] = {}
    for category, words in CANDIDATE_TABLES:
        for word in words:
            mapping.setdefault(word, category)
    return mapping


def first_exact(chinese: list[dict], word: str, ns_prefix: str | None = None) -> dict | None:
    """按出现顺序找第一个「归一化后取值 == 候选词」的记录；可按命名空间前缀限定。"""
    for rec in chinese:
        if ns_prefix is not None and not rec["key"].startswith(ns_prefix):
            continue
        if norm_term(rec["value"]) == word:
            return rec
    return None


def count_in_brush_ns(chinese: list[dict], word: str) -> int:
    """该词在画笔对话框命名空间内出现的次数（用于标注证据强度）。"""
    return sum(
        1
        for rec in chinese
        if rec["key"].startswith(BRUSH_NS) and norm_term(rec["value"]) == word
    )


# ── 抽词 ──────────────────────────────────────────────────────────────────────
def extract(chinese: list[dict]) -> tuple[list[dict], dict[str, list[tuple[str, dict | None]]], dict[str, dict]]:
    """候选表 → 文案池：逐词等值匹配；返回 (terms 列表, 每类命中明细, 别名命中表)。"""
    cat_of = term_category()
    stats: dict[str, list[tuple[str, dict | None]]] = {}
    terms: list[dict] = []
    seen: set[str] = set()
    alias_map: dict[str, dict] = {}

    for category, words in CANDIDATE_TABLES:
        stats[category] = []
        for word in words:
            rec = first_exact(chinese, word)
            stats[category].append((word, rec))
            if rec is None or word in seen:
                continue
            seen.add(word)
            terms.append(
                {
                    "term": word,
                    "category": cat_of[word],
                    "matched_expected": word,
                    "offset": rec["offset"],
                    "string_index": rec["string_index"],
                    "raw": rec["raw"][:200],
                }
            )

    # 别名：按命名空间限定取证，归到声明的类别下
    for expected, actual, ns_prefix, category in ALIASES:
        rec = first_exact(chinese, actual, ns_prefix)
        if rec is None:
            continue
        alias_map[expected] = rec
        if actual in seen:
            continue
        seen.add(actual)
        terms.append(
            {
                "term": actual,
                "category": category,
                "matched_expected": expected,
                "offset": rec["offset"],
                "string_index": rec["string_index"],
                "raw": rec["raw"][:200],
            }
        )

    terms.sort(key=lambda item: (item["offset"], item["term"]))
    return terms, stats, alias_map


def cross_category_terms() -> dict[str, list[str]]:
    """同一个词挂在不同候选表里的情况（双向过滤的第二向：池里的词回指候选表）。"""
    where: dict[str, list[str]] = {}
    for category, words in CANDIDATE_TABLES:
        for word in words:
            where.setdefault(word, [])
            if category not in where[word]:
                where[word].append(category)
    return {word: cats for word, cats in where.items() if len(cats) > 1}


def index_entries(chinese: list[dict]) -> list[tuple[str, str]]:
    """画笔对话框自己的索引命名空间（页签名 + 底部勾选项名就在这里面）。"""
    prefix = "$$$/BrushStylerIndex/"
    return [(rec["key"][len(prefix):], rec["value"]) for rec in chinese if rec["key"].startswith(prefix)]


# ── 隐私自检 ──────────────────────────────────────────────────────────────────
def privacy_hits(text: str, extra_tokens: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """返回 [(规则名, 命中的片段)]；用户名按环境变量做精确子串比对，只报命中不打印原值。"""
    hits: list[tuple[str, str]] = []
    for name, pattern in PRIVACY_PATTERNS:
        for match in pattern.finditer(text):
            snippet = match.group(0)
            hits.append((name, snippet if name != "邮箱" else "<已遮蔽>"))
    lowered = text.lower()
    for index, token in enumerate(extra_tokens):
        if token and token.lower() in lowered:
            hits.append((f"本机账户名#{index + 1}", "<已遮蔽>"))
    return hits


def env_identity_tokens() -> tuple[str, ...]:
    """取本机账户名用于比对；返回值只在内存里用，不打印、不落盘。"""
    tokens = []
    for key in ("USERNAME", "USER", "LOGNAME"):
        value = os.environ.get(key)
        if value and len(value) >= 3:
            tokens.append(value)
    return tuple(dict.fromkeys(tokens))


def deterministic_sample(chinese: list[dict], size: int) -> list[dict]:
    """等步长抽样，保证同一输入每次抽到同一批串。"""
    if size <= 0 or not chinese:
        return []
    step = max(1, len(chinese) // size)
    return chinese[::step][:size]


# ── 输出 ──────────────────────────────────────────────────────────────────────
def build_document(encoding_guess: str, total_chinese: int, terms: list[dict]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": GENERATED_BY,
        "source_file": SOURCE_PLACEHOLDER,
        "encoding_guess": encoding_guess,
        "total_chinese_strings": total_chinese,
        "terms": terms,
    }


def dump_json(document: dict) -> str:
    """固定键序 + 固定缩进 + LF，保证重复跑逐字节一致。"""
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def document_text(node: object) -> str:
    """把文档里的所有字符串摊平成一段文本（未转义），供隐私自检使用。

    注意：必须扫「未转义」的内容——JSON 里 `\\PS2025\\` 是单个反斜杠路径段，
    对已转义文本扫 UNC 会一律误报。
    """
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "\n".join(document_text(value) for value in node.values())
    if isinstance(node, (list, tuple)):
        return "\n".join(document_text(value) for value in node)
    return str(node)


def is_under(child: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 PS 中文语言包抽画笔设置 UI 词条（只读）")
    parser.add_argument("--dat", type=pathlib.Path, default=DEFAULT_DAT, help="语言包 .dat 路径（只读）")
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT, help="输出 json 路径")
    parser.add_argument("--audit", type=int, default=200, help="额外抽检多少条语料池原文（0=关闭）")
    args = parser.parse_args(argv)

    dat_path: pathlib.Path = args.dat
    out_path: pathlib.Path = args.out
    if not dat_path.is_file():
        print(f"FAIL 语言包不存在：{dat_path}（可用 --dat 或 BRUSHCONVERTER_PS_LOCALE_DAT 指定）", file=sys.stderr)
        return 2
    # 铁律：不许往 PS 安装目录写任何东西（语言包上溯 3 级即安装根）
    install_root = dat_path.parents[3] if len(dat_path.parents) > 3 else dat_path.parent
    if is_under(out_path, install_root):
        print(f"FAIL 输出路径落在 PS 安装目录内，拒绝写盘：{out_path}", file=sys.stderr)
        return 2

    raw = dat_path.read_bytes()
    try:
        text, encoding_guess = decode_pool(raw)
    except UnicodeDecodeError as exc:
        print(f"FAIL 语言包不是可用的 UTF-16 文本：{exc}", file=sys.stderr)
        return 2

    records, chinese, counters = parse_pool(text)
    terms, stats, alias_map = extract(chinese)

    document = build_document(encoding_guess, len(chinese), terms)
    payload = dump_json(document)

    # 隐私自检：命中即不写盘（扫未转义内容，见 document_text 的说明）
    hits = privacy_hits(document_text(document), env_identity_tokens())
    if hits:
        print(f"FAIL 输出含隐私线索 {len(hits)} 处，已拒绝写盘：", file=sys.stderr)
        for name, snippet in hits[:20]:
            print(f"  - {name}: {snippet}", file=sys.stderr)
        return 3

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload.encode("utf-8"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ── stdout：命中统计（供交付回执直接摘抄）────────────────────────────────
    print("== 语言包 ==")
    print(f"文件（只读）：{SOURCE_PLACEHOLDER}")
    print(f"字节：{len(raw):,}")
    print(f"编码判定：{encoding_guess}")
    print(
        f"行数：{counters['lines']:,}　含等号的行：{counters['kv']:,}"
        f"　空取值跳过：{counters['empty']}　非空取值串：{len(records):,}"
        f"　其中含中文：{len(chinese):,}"
    )
    print(f"去重后的中文取值：{len({rec['value'] for rec in chinese}):,}")

    expected_total = sum(len(words) for _, words in CANDIDATE_TABLES)
    hit_total = sum(1 for _, pairs in stats.items() for _, rec in pairs if rec is not None)
    labels = {"tab": "页签", "checkbox": "勾选项", "control": "控件", "value": "取值"}
    alias_terms = [t for t in terms if t["matched_expected"] != t["term"]]
    print("")
    print(
        f"== 候选命中（四张候选表共 {expected_total} 个期望词，等值直接命中 {hit_total} 个，"
        f"另有别名命中 {len(alias_terms)} 条）=="
    )
    for category, _ in CANDIDATE_TABLES:
        pairs = stats[category]
        hit = sum(1 for _, rec in pairs if rec is not None)
        misses = [word for word, rec in pairs if rec is None]
        filled = [word for word in misses if word in alias_map]
        still = [word for word in misses if word not in alias_map]
        parts = [f"直接命中 {hit}/{len(pairs)}"]
        if filled:
            parts.append("别名覆盖 " + "、".join(f"{w}→{norm_term(alias_map[w]['value'])}" for w in filled))
        parts.append("仍缺 " + (f"{len(still)}：{'、'.join(still)}" if still else "0"))
        print(f"{labels[category]:<4}" + "；".join(parts))
        for word, rec in pairs:
            if rec is None:
                continue
            ns = count_in_brush_ns(chinese, word)
            mark = f"对话框命名空间内 {ns} 处" if ns else "仅在池内通用文案命中（证据较弱）"
            print(f"    ✓ {word}（{mark}）")
    print("")
    print(f"== 别名命中 {len(alias_terms)} 条（raw 里带完整键路径，可人工核对改写关系）==")
    for item in alias_terms:
        print(f"    {item['matched_expected']} → {item['term']}　[{item['category']}]　{item['raw'][:110]}")

    print("")
    print("== 结构核对：画笔对话框索引命名空间（$$$/BrushStylerIndex/）==")
    entries = index_entries(chinese)
    alias_reverse = {actual: expected for expected, actual, _ns, _cat in ALIASES}
    tab_set = set(TAB_TERMS)
    cb_set = set(CHECKBOX_TERMS)
    n_tab = n_cb = 0
    for short_key, value in entries:
        if value in tab_set:
            tag = "页签"
            n_tab += 1
        elif value in cb_set:
            tag = "勾选项"
            n_cb += 1
        elif value in alias_reverse:
            expected = alias_reverse[value]
            if expected in tab_set:
                tag = f"页签（别名：任务书写作 {expected}）"
                n_tab += 1
            else:
                tag = f"勾选项（别名：任务书写作 {expected}）"
                n_cb += 1
        else:
            tag = "未在候选表"
        print(f"    {value}　[{tag}]　{short_key}")
    print(f"    合计 {len(entries)} 条；其中判为页签 {n_tab} 条、勾选项 {n_cb} 条（任务书要求 8 + 5）")

    print("")
    print("== 任务书四个缺词的复核 ==")
    for word in BRIEF_MISSING:
        direct = first_exact(chinese, word)
        alias = next(
            (
                (actual, first_exact(chinese, actual, ns))
                for expected, actual, ns, _cat in ALIASES
                if expected == word
            ),
            (None, None),
        )
        if direct is not None:
            print(f"    {word}：命中 @ {direct['key']}")
        elif alias[1] is not None:
            print(f"    {word}：原词 0 命中；等价写法「{alias[0]}」命中 @ {alias[1]['key']}")
        else:
            print(f"    {word}：原词 0 命中，池内也没有等价写法")

    overlaps = cross_category_terms()
    print("")
    print("== 跨类同名词（去重时按 页签>勾选项>控件>取值 归类）==")
    for word, cats in overlaps.items():
        print(f"    {word}：{' / '.join(labels[c] for c in cats)}")

    if args.audit > 0:
        sample = deterministic_sample(chinese, args.audit)
        term_text = "\n".join(item["term"] + "\n" + item["raw"] for item in terms)
        sample_text = "\n".join(rec["raw"] for rec in sample)
        term_hits = privacy_hits(term_text, env_identity_tokens())
        sample_hits = privacy_hits(sample_text, env_identity_tokens())
        print("")
        print("== 隐私抽检 ==")
        print(f"    terms 全量 {len(terms)} 条：命中 {len(term_hits)} 处")
        print(f"    语料池等步长抽样 {len(sample)} 条：命中 {len(sample_hits)} 处")
        for name, snippet in (term_hits + sample_hits)[:10]:
            print(f"    - {name}: {snippet}")

    print("")
    print("== 输出 ==")
    print(f"文件：{out_path}")
    print(f"条目数：{len(terms)}　按类别：" + "　".join(
        f"{labels[category]} {sum(1 for t in terms if t['category'] == category)}"
        for category, _ in CANDIDATE_TABLES
    ))
    print(f"sha256：{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
