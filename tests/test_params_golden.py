"""任务 02：参数级比对（期望值表 vs 真实 .abr 转出的 .kpp）。

期望值来源：tests/golden/params_expected.json，由 tests/测试用.abr 的 desc 源参数
按 docs/parameter-mapping.md 的公式算出（每条都带 _source 与算式），
**不是**从当前转换产物里抄的——所以映射改坏时这里必须变红。

失败信息固定五项：笔刷名 / 字段 / 期望 / 实际 / 差值；一次把所有差异列完再失败。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from brush_converter.convert import convert  # noqa: E402
from tools.kpp_params import (  # noqa: E402
    DEFAULT_TOL,
    close,
    extract_params,
    read_kpp_xml,
)

pytestmark = [pytest.mark.verify, pytest.mark.verify_params]

HERE = Path(__file__).parent
ABR = HERE / "测试用.abr"
GOLDEN = HERE / "golden" / "params_expected.json"

# 每支笔刷必须核对的字段（第 4 支另有散布三件套，见 JSON）
BASE_FIELDS = ("spacing", "angle_deg", "scale", "size")


def _load_golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def actual_params() -> dict[str, dict]:
    """把 测试用.abr 真实转成 .kpp，再读回来抽成「名字 → 参数」字典。"""
    if not ABR.exists():
        pytest.skip("缺少测试用.abr（参数级比对的源样本）")
    with tempfile.TemporaryDirectory(prefix="params_golden_") as tmp_dir:
        result = convert(str(ABR), tmp_dir, make_bundle=False)
        actual: dict[str, dict] = {}
        for filename, _raw in result.kpp_files:
            xml = read_kpp_xml(Path(tmp_dir) / "kpp" / filename)
            params = extract_params(xml)
            actual[params["name"]] = params
    if not actual:
        pytest.fail("转换没有产出任何 .kpp，无法做参数级比对")
    return actual


def _fmt(value) -> str:
    """把期望/实际值格式化成稳定文本（浮点保留 6 位，布尔写 True/False）。"""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".") if value != int(value) else f"{value:.6f}"
    return str(value)


def _diff_text(name: str, field: str, expected, actual) -> str:
    """生成五項齐全的差异文本：[笔刷名] 字段 X 期望 V1 实际 V2（差 D）。"""
    if isinstance(expected, bool) or isinstance(actual, bool):
        delta = "—（布尔字段严格相等）"
    elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        delta = f"{_fmt(float(actual) - float(expected))}"
    else:
        delta = "—（非数值字段严格相等）"
    return f"[{name}] 字段 {field} 期望 {_fmt(expected)} 实际 {_fmt(actual)}（差 {delta}）"


def test_golden_expected_fields(actual_params):
    """逐字段比对期望值表；一次列出全部差异后再失败。"""
    golden = _load_golden()
    diffs: list[str] = []
    checked_fields = 0

    for item in golden["brushes"]:
        name = item["name"]
        actual = actual_params.get(name)
        if actual is None:
            diffs.append(
                f"[{name}] 字段 name 期望 {name} 实际 <产物中找不到这支笔>（差 —）"
            )
            continue
        # name 单独用「按名字索引」核对，不作为参数项参与数值比对
        expected_fields = [k for k in item if not k.startswith("_") and k != "name"]
        # 每支笔刷至少要核对基础四字段，避免期望表被删字段后静默变松
        missing = [f for f in BASE_FIELDS if f not in expected_fields]
        assert not missing, f"[{name}] 期望值表缺基础字段：{missing}"
        for field in expected_fields:
            expected = item[field]
            got = actual[field]
            checked_fields += 1
            if not close(expected, got):
                diffs.append(_diff_text(name, field, expected, got))

    assert not diffs, (
        f"参数级比对发现 {len(diffs)} 处差异（共核对 {checked_fields} 个字段）：\n"
        + "\n".join(diffs)
    )


def test_golden_covers_expected_brushes(actual_params):
    """核对规模：样本 6 支笔刷全部在期望表里，且字段数能报出来。"""
    golden = _load_golden()
    names = [item["name"] for item in golden["brushes"]]
    assert len(names) == len(set(names)), "期望表里笔刷名重复，按名字索引会串味"

    produced = set(actual_params)
    missing_in_golden = produced - set(names)
    missing_in_product = set(names) - produced
    assert not missing_in_product, f"期望表里的笔刷没有出现在转换产物中：{sorted(missing_in_product)}"
    assert not missing_in_golden, f"转换产出的笔刷没有登记进期望表：{sorted(missing_in_golden)}"

    field_count = sum(
        1 for item in golden["brushes"] for k in item if not k.startswith("_") and k != "name"
    )
    print(f"\n参数级比对规模：{len(names)} 支笔刷 × {field_count} 个字段（容差 {DEFAULT_TOL:g}）")


def test_golden_has_sources():
    """每条期望值都要有 _source 与逐字段算式，且来源不许写「当前产物」。"""
    golden = _load_golden()
    for item in golden["brushes"]:
        name = item["name"]
        assert "_source" in item, f"[{name}] 缺少 _source"
        source = item["_source"]
        assert "当前产物" not in source, f"[{name}] _source 写了「当前产物」，等于把输出抄成答案"
        assert source.startswith("参数表公式") or source.startswith("手工核对"), (
            f"[{name}] _source 取值非法：{source!r}"
        )
        detail = item.get("_source_detail") or {}
        for field in (k for k in item if not k.startswith("_") and k != "name"):
            assert field in detail, f"[{name}] 字段 {field} 缺少算式来源（_source_detail）"
