# -*- coding: utf-8 -*-
"""任务 15 单测：全量验收入口（三层汇总 / 摘要契约 / 归档 / 清单合并）。

不真跑 pytest（会重复跑整套用例），用注入的假层结果验证：
1. 三层（参数/像素/手感）都被依次调用到，且顺序固定；
2. 总览行与任务 01 契约完全一致（字段次序、全角竖线、跳过提示、skip 不计入通过）；
3. 退出码 0/1/2 的换算；
4. 归档文件名带时间戳且同名加序号不覆盖；归档内容含总览表/环境三行/摘要行；
5. feel 子清单合并进顶层 manifest：条数对账 = 合并前 + 新增，且幂等（重复合并新增 0），每项 11 字段。
"""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import verify_all  # noqa: E402
import verify_entry  # noqa: E402

pytestmark = pytest.mark.verify

SUMMARY = "通过 46 项 ｜ 失败 0 项 ｜ 跳过 0 项"


def _fake_layer(key, name, passed=10, failed=0, skipped=0, skips=None, failures=None):
    return {
        "key": key, "name": name,
        "total": passed + failed + skipped,
        "passed": passed, "failed": failed, "skipped": skipped,
        "skips": skips or [], "failures": failures or [],
        "timed_out": False, "exit_code": 0 if failed == 0 else 1,
        "stdout_tail": "", "stderr_tail": "", "metric": "",
    }


def _fake_runner(calls):
    def runner(layer, timeout=900):  # noqa: ARG001
        calls.append((layer["key"], layer["name"]))
        return _fake_layer(layer["key"], layer["name"], passed=2)
    return runner


def test_three_layers_are_called_in_order():
    """三层必须都被调用，且顺序是 参数层 -> 像素层 -> 手感层。"""
    calls = []
    results = verify_all.run_all(layer_runner=_fake_runner(calls), feel_summary_getter=lambda: None)
    assert [name for _key, name in calls] == ["参数层", "像素层", "手感层"]
    assert len(results) == 3
    assert [item["key"] for item in results] == ["params", "pixels", "feel"]


def test_summary_line_matches_task01_contract_exactly():
    """总览行必须与任务 01 的 build_summary 一字不差，字段次序固定。"""
    results = [
        _fake_layer("params", "参数层", passed=3),
        _fake_layer("pixels", "像素层", passed=34),
        _fake_layer("feel", "手感层", passed=9),
    ]
    line = verify_all.build_overall_summary(results)
    assert line == SUMMARY
    assert line == verify_entry.build_summary(verify_all.aggregate(results))
    assert line.index("通过") < line.index("失败") < line.index("跳过")
    assert "｜" in line and "|" not in line  # 全角竖线 U+FF5C，不是 ASCII


def test_skip_is_counted_separately_and_hint_appended():
    """跳过不计入通过；有跳过时摘要行末尾追加「（含跳过，未全验）」。"""
    results = [
        _fake_layer("params", "参数层", passed=3),
        _fake_layer("pixels", "像素层", passed=30, skipped=4,
                    skips=[("tests/test_pixels_diff.py", "test_x", "缺少样本")]),
        _fake_layer("feel", "手感层", passed=9),
    ]
    line = verify_all.build_overall_summary(results)
    assert line == "通过 42 项 ｜ 失败 0 项 ｜ 跳过 4 项（含跳过，未全验）"
    assert verify_entry.SKIP_HINT in line
    total = verify_all.aggregate(results)
    assert total["passed"] == 42 and total["skipped"] == 4 and total["passed"] + total["skipped"] == 46


def test_exit_codes():
    """退出码：有失败 -> 1；全通过（含跳过）-> 0；一条用例都没收集到 -> 2。"""
    ok_results = [_fake_layer("params", "参数层", passed=3)]
    fail_results = [_fake_layer("pixels", "像素层", passed=1, failed=1)]
    empty_results = [_fake_layer("feel", "手感层", passed=0, failed=0, skipped=0)]
    assert verify_all.decide_overall_exit_code(ok_results) == 0
    assert verify_all.decide_overall_exit_code(fail_results) == 1
    assert verify_all.decide_overall_exit_code(empty_results) == 2


def test_archive_name_has_timestamp_and_never_overwrites(tmp_path, monkeypatch):
    """归档文件名带时间戳；同一时间戳重复运行加 -2、-3，不覆盖历史。"""
    monkeypatch.setattr(verify_all, "ARCHIVE_DIR", tmp_path)
    first = verify_all.archive_path("20260101-000000")
    assert first.name == "verify-20260101-000000.md"
    first.write_text("# 第一次", encoding="utf-8")
    second = verify_all.archive_path("20260101-000000")
    assert second.name == "verify-20260101-000000-2.md"
    second.write_text("# 第二次", encoding="utf-8")
    third = verify_all.archive_path("20260101-000000")
    assert third.name == "verify-20260101-000000-3.md"
    assert first.read_text(encoding="utf-8") == "# 第一次"  # 旧文件未被覆盖


def test_archive_content_has_summary_table_and_environment():
    """归档内容必须含总览表、摘要行、失败/跳过小节、环境三行（采集不到写未检测到）。"""
    results = [
        _fake_layer("params", "参数层", passed=3),
        _fake_layer("pixels", "像素层", passed=1, failed=1,
                    failures=[("tests/test_pixels_diff.py", "test_bad")]),
        _fake_layer("feel", "手感层", passed=0, skipped=1,
                    skips=[("tests/test_feel_curve.py", "test_skip", "基线缺失")]),
    ]
    env = {"Python": "Python 3.12.13 64bit", "Krita": "未检测到", "Photoshop": "未检测到"}
    text = verify_all.render_archive(results, env, 1)
    assert "## 一、总览" in text and "## 四、运行环境（自动采集）" in text
    assert "通过 4 项 ｜ 失败 1 项 ｜ 跳过 1 项（含跳过，未全验）" in text
    assert "test_bad" in text and "基线缺失" in text
    assert "Python：Python 3.12.13 64bit" in text
    assert "Krita：未检测到" in text and "Photoshop：未检测到" in text


def _write_png(path, alpha=255, size=(4, 4)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (0, 0, 0, alpha)).save(path, "PNG")


def test_merge_feel_manifest_reconciles_and_is_idempotent(tmp_path):
    """合并条数对账：合并后 = 合并前 + 新增；重复合并新增 0；每项 11 字段齐全。"""
    bases = tmp_path / "baselines"
    feel_dir = bases / "feel"
    _write_png(feel_dir / "feel__a__p1.00__s200x200.png")
    _write_png(feel_dir / "feel__b__p1.00__s200x200.png")
    _write_png(bases / "ps" / "ps__old.png")
    top = bases / "manifest.json"
    top.write_text(json.dumps({
        "schema": "brushconverter.baseline.manifest/v1",
        "counts": {"total": 1, "ps": 1, "krita": 0, "handmade": 0},
        "items": [{"file": "ps/ps__old.png", "source": "ps", "size": "4x4",
                   "pressure": None, "mode": None, "depth": None, "brightness": None,
                   "contrast": None, "status": None, "alpha_nonzero": 16, "note": "旧条目"}],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    feel = feel_dir / "manifest.json"
    feel.write_text(json.dumps({
        "schema_version": 1, "pressure_injected": True,
        "items": [
            {"preset": "a.kpp", "brush": "a", "pressure": 1.0, "file": "feel__a__p1.00__s200x200.png",
             "curve_file": "a.json", "size": "200x200", "stroke_length": 120, "alpha_nonzero": 16},
            {"preset": "b.kpp", "brush": "b", "pressure": 1.0, "file": "feel__b__p1.00__s200x200.png",
             "curve_file": "b.json", "size": "200x200", "stroke_length": 120, "alpha_nonzero": 16},
        ],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    first = verify_all.merge_feel_manifest(top, feel)
    assert first["before_total"] == 1
    assert first["added"] == 2
    assert first["after_total"] == 3
    assert first["reconciliation"] == "3 = 合并前 1 + 新增 2"
    merged = json.loads(top.read_text(encoding="utf-8"))
    assert merged["counts"]["total"] == 3
    assert merged["counts"]["reconciliation"] == "3 = 1 + 2 + 0"
    for item in merged["items"]:
        assert list(item.keys()) == verify_all.FIELD_ORDER
    assert all(item["alpha_nonzero"] > 0 for item in merged["items"])

    second = verify_all.merge_feel_manifest(top, feel)
    assert second["added"] == 0
    assert second["duplicates_skipped"] == 2
    assert second["after_total"] == 3
