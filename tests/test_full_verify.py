# -*- coding: utf-8 -*-
"""任务 15 单测：全量验收入口（三层汇总 / 摘要契约 / 归档 / 清单合并）。

不真跑 pytest（会重复跑整套用例），用注入的假层结果验证：
1. 三层（参数/像素/手感）都被依次调用到，且顺序固定；
2. 总览行与任务 01 契约完全一致（字段次序、全角竖线、跳过提示、skip 不计入通过）；
3. 退出码 0/1/2 的换算；
4. 归档文件名带时间戳且同名加序号不覆盖；归档内容含总览表/环境三行/摘要行；
5. feel 合并按四源对账；新增/历史三源迁移与重复合并字节幂等均使用工作区合成夹具，不写真实基准。
"""

import json
import sys
import tempfile
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


def test_archive_name_has_timestamp_and_never_overwrites(workspace_tmp_path, monkeypatch):
    """归档文件名带时间戳；同一时间戳重复运行加 -2、-3，不覆盖历史。"""
    monkeypatch.setattr(verify_all, "ARCHIVE_DIR", workspace_tmp_path)
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


@pytest.fixture
def workspace_tmp_path():
    """每个磁盘夹具独占全新工作区 .tmp 子目录；不接触真实基准或系统临时目录。"""
    work = (REPO_ROOT / ".tmp").resolve()
    if not work.is_relative_to(REPO_ROOT):
        raise RuntimeError("合成夹具目录越出工作区，停止")
    work.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="verify-merge-", dir=work))


def _write_png(path, alpha=255, size=(4, 4)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (0, 0, 0, alpha)).save(path, "PNG")


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _item(source, filename):
    return {"file": source + "/" + filename, "source": source, "size": "4x4",
            "pressure": None, "mode": None, "depth": None, "brightness": None,
            "contrast": None, "status": None, "alpha_nonzero": 16, "note": "原始合成条目"}


def _feel_entry(filename):
    return {"preset": "a.kpp", "brush": "a", "pressure": 1.0, "file": filename,
            "curve_file": "a.json", "size": "4x4", "stroke_length": 120,
            "alpha_nonzero": 9999}  # 新增时必须读 PNG，不抄这个故意错误的值。


def _four_source_fixture(root):
    """合成与发布前相同的 341=298+29+2+12 规模，不读写仓库真实清单。"""
    counts = {"ps": 298, "krita": 29, "handmade": 2, "feel": 12}
    items = [_item(source, "%s__%03d.png" % (source, index))
             for source, count in counts.items() for index in range(count)]
    specs = {"ps": "images", "krita": "items", "handmade": "entries", "feel": "items"}
    top_data = {
        "schema": "brushconverter.baseline.manifest/v1",
        "merged_at": "2026-01-01T00:00:00+08:00", "merged_by": "原始合成写者",
        "field_spec": {"source": "ps / krita / handmade / feel"},
        "counts": {"total": 341, **counts, "reconciliation": "341 = 298 + 29 + 2 + 12"},
        "sources": {source: {"manifest": "tests/baselines/%s/manifest.json" % source,
                             "list_key": specs[source], "count": count}
                    for source, count in counts.items()},
        "items": items, "merge_history": [{"by": "历史记录", "added": 12}],
        "note": "保留原注释", "superseded": "保留原来源声明",
    }
    top_data["sources"]["feel"].update(upgraded_to_source=True, note="独立手感来源")
    top = root / "baselines" / "manifest.json"
    feel = top.parent / "feel" / "manifest.json"
    feel_data = {"schema_version": 1, "pressure_injected": True,
                 "items": [_feel_entry("feel__%03d.png" % i) for i in range(12)]}
    _write_json(top, top_data)
    _write_json(feel, feel_data)
    return top, feel, top_data, feel_data


def test_merge_feel_manifest_reconciles_and_is_idempotent(workspace_tmp_path):
    """新增走 feel 正式来源；重复合并保持字节、mtime、时间戳及历史不变。"""
    bases = workspace_tmp_path / "baselines"
    filenames = ["feel__a__p1.00__s200x200.png", "feel__b__p1.00__s200x200.png"]
    for filename in filenames:
        _write_png(bases / "feel" / filename)
    top = bases / "manifest.json"
    _write_json(top, {"schema": "brushconverter.baseline.manifest/v1",
                     "counts": {"total": 1, "ps": 1, "krita": 0, "handmade": 0},
                     "items": [_item("ps", "ps__old.png")]})
    feel = bases / "feel" / "manifest.json"
    _write_json(feel, {"items": [_feel_entry(filename) for filename in filenames]})

    first = verify_all.merge_feel_manifest(top, feel)
    assert first["before_total"] == 1 and first["added"] == 2 and first["after_total"] == 3
    assert first["changed"] and first["migrated"] == 0
    assert first["reconciliation"] == "3 = 合并前 1 + 新增 2"
    merged = json.loads(top.read_text(encoding="utf-8"))
    assert merged["counts"] == {"total": 3, "ps": 1, "krita": 0, "handmade": 0,
                                "feel": 2, "reconciliation": "3 = 1 + 0 + 0 + 2"}
    assert {source: row["count"] for source, row in merged["sources"].items()} == {
        "ps": 1, "krita": 0, "handmade": 0, "feel": 2}
    for item in merged["items"]:
        assert list(item.keys()) == verify_all.FIELD_ORDER
        assert item["alpha_nonzero"] == 16
    before_bytes, before_mtime = top.read_bytes(), top.stat().st_mtime_ns
    second = verify_all.merge_feel_manifest(top, feel)
    assert second["added"] == 0 and second["duplicates_skipped"] == 2
    assert second["after_total"] == 3 and not second["changed"]
    assert top.read_bytes() == before_bytes and top.stat().st_mtime_ns == before_mtime
    assert json.loads(top.read_text(encoding="utf-8")) == merged


def test_four_source_341_manifest_repeat_is_byte_identical(workspace_tmp_path, monkeypatch):
    """当前四源规模的合成副本 341 条全部保留；无新增不触碰 PNG 或元数据。"""
    top, feel, original, _feel_data = _four_source_fixture(workspace_tmp_path)
    before_bytes, before_mtime = top.read_bytes(), top.stat().st_mtime_ns
    monkeypatch.setattr(verify_all, "_alpha_nonzero", lambda _path: pytest.fail("重复项不应重读 PNG"))
    for _ in range(2):
        result = verify_all.merge_feel_manifest(top, feel)
        assert result["added"] == result["migrated"] == 0
        assert result["duplicates_skipped"] == 12 and result["after_total"] == 341
        assert not result["changed"]
        assert top.read_bytes() == before_bytes and top.stat().st_mtime_ns == before_mtime
        assert json.loads(top.read_text(encoding="utf-8")) == original


def test_new_feel_preserves_all_341_existing_items(workspace_tmp_path):
    top, feel, original, child = _four_source_fixture(workspace_tmp_path)
    child["items"].append(_feel_entry("feel__new.png"))
    _write_json(feel, child)
    _write_png(feel.parent / "feel__new.png")
    result = verify_all.merge_feel_manifest(top, feel)
    assert result["added"] == 1 and result["duplicates_skipped"] == 12
    merged = json.loads(top.read_text(encoding="utf-8"))
    assert merged["items"][:341] == original["items"]
    assert merged["items"][341]["source"] == "feel"
    assert merged["items"][341]["alpha_nonzero"] == 16
    assert merged["counts"] == {"total": 342, "ps": 298, "krita": 29, "handmade": 2,
                                "feel": 13, "reconciliation": "342 = 298 + 29 + 2 + 13"}
    for source in ("ps", "krita", "handmade", "feel"):
        assert merged["sources"][source]["count"] == merged["counts"][source]
    assert merged["merge_history"][:-1] == original["merge_history"]
    assert merged["note"] == original["note"] and merged["superseded"] == original["superseded"]
    before_bytes, before_mtime = top.read_bytes(), top.stat().st_mtime_ns
    again = verify_all.merge_feel_manifest(top, feel)
    assert not again["changed"] and again["added"] == 0
    assert top.read_bytes() == before_bytes and top.stat().st_mtime_ns == before_mtime


def test_legacy_three_source_feel_prefix_migrates_once(workspace_tmp_path):
    """旧三源只迁移 feel/ 的 krita 项；旧历史/像素登记不动，四源按已入顶层计数。"""
    bases = workspace_tmp_path / "baselines"
    top, feel = bases / "manifest.json", bases / "feel" / "manifest.json"
    old_feel = _item("feel", "feel__old.png")
    old_feel["source"] = "krita"
    old_history = [{"at": "2026-01-01", "by": "task-15", "added": 1}]
    original_items = [_item("ps", "ps__old.png"), _item("krita", "krita__old.png"), old_feel]
    _write_json(top, {
        "field_spec": {"source": "ps / krita / handmade"},
        "counts": {"total": 3, "ps": 1, "krita": 2, "handmade": 0,
                   "reconciliation": "3 = 1 + 2 + 0"},
        "sources": {"krita": {"count": 2, "note": "含任务14合入的手感基线 12 张（来源子清单 tests/baselines/feel/manifest.json）"},
                    "feel": {"count": 1, "merged_as_source": "krita", "custom": "保留"}},
        "items": original_items, "merge_history": old_history,
    })
    # 缺失 PNG 的未入顶层项不能用子清单长度冒充 sources.feel.count。
    _write_json(feel, {"items": [_feel_entry("feel__old.png"), _feel_entry("feel__missing.png")]})
    result = verify_all.merge_feel_manifest(top, feel)
    assert result["added"] == 0 and result["migrated"] == 1 and result["changed"]
    merged = json.loads(top.read_text(encoding="utf-8"))
    assert merged["items"][:2] == original_items[:2]
    expected_feel = dict(old_feel, source="feel")
    assert merged["items"][2] == expected_feel
    assert merged["counts"] == {"total": 3, "ps": 1, "krita": 1, "handmade": 0,
                                "feel": 1, "reconciliation": "3 = 1 + 1 + 0 + 1"}
    assert merged["sources"]["krita"]["count"] == 1 and "note" not in merged["sources"]["krita"]
    assert merged["sources"]["feel"]["count"] == 1
    assert merged["sources"]["feel"]["upgraded_to_source"] is True
    assert "merged_as_source" not in merged["sources"]["feel"]
    assert merged["sources"]["feel"]["custom"] == "保留"
    assert merged["field_spec"]["source"] == "ps / krita / handmade / feel"
    assert merged["merge_history"][:-1] == old_history
    assert merged["merge_history"][-1]["migrated"] == 1
    before_bytes, before_mtime = top.read_bytes(), top.stat().st_mtime_ns
    again = verify_all.merge_feel_manifest(top, feel)
    assert again["migrated"] == 0 and not again["changed"]
    assert top.read_bytes() == before_bytes and top.stat().st_mtime_ns == before_mtime


def test_merge_unknown_source_fails_without_rewriting(workspace_tmp_path):
    top = workspace_tmp_path / "baselines" / "manifest.json"
    feel = top.parent / "feel" / "manifest.json"
    _write_json(top, {"items": [_item("unknown", "unrecognised.png")]})
    _write_json(feel, {"items": []})
    before = top.read_bytes()
    with pytest.raises(ValueError, match="未知 source"):
        verify_all.merge_feel_manifest(top, feel)
    assert top.read_bytes() == before
