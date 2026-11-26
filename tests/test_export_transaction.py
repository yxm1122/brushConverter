# -*- coding: utf-8 -*-
"""任务169：安全写出与整批取消的定向测试。

覆盖用户可见验收目标：
1. 取消显示已取消、不显示转换成功；最终目录无本批半成品。
2. 已有文件内容与 mtime 不变；重名、复用、磁盘错误、提交中失败有注入测试。
3. 取消后可再次转换；清理只碰本次创建的内容；没有悬挂线程。
4. 零选中/全跳过有明确状态；最终交付阶段标不可取消。

测试临时目录固定在工作区 .tmp/169/tests/（不使用系统临时目录）。
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import brush_converter.export_transaction as txmod  # noqa: E402
from brush_converter.convert import convert_presets  # noqa: E402
from brush_converter.export_options import (  # noqa: E402
    CANCELLED_PHASE,
    PHASE_CONVERT,
    PHASE_DONE,
    PHASE_PREPARE,
    PHASE_WRITE,
    CancelToken,
)
from brush_converter.export_transaction import (  # noqa: E402
    RESULT_STATUS_ALL_SKIPPED,
    RESULT_STATUS_CANCELLED,
    RESULT_STATUS_COMMITTED,
    RESULT_STATUS_EMPTY,
    RESULT_STATUS_FAILED,
    RESULT_STATUS_PARTIAL_BLOCKED,
    RESULT_STATUS_REUSED_ONLY,
    RESULT_STATUS_ROLLED_BACK,
    STAGING_PREFIX,
    STATE_COMMITTED,
    STATE_VERIFIED,
    ExportTransaction,
    ExportTransactionError,
)
from brush_converter.kpp.bundle import (  # noqa: E402
    build_bundle,
    build_bundle_bytes,
    bundle_content_hash8,
    verify_bundle,
)
from brush_converter.kpp.kpp_writer import build_kpp, read_preset_xml  # noqa: E402
from brush_converter.kpp.preset_xml import build_preset_xml  # noqa: E402
from brush_converter.mapping import BrushPreset  # noqa: E402

pytestmark = [pytest.mark.verify]

TASK_TMP = REPO_ROOT / ".tmp" / "169"
TESTS_TMP = TASK_TMP / "tests"


class _FakeAbr:
    """convert_presets 只用 abr.name 作为 bundle 基名。"""

    name = "样本笔刷"


def _fresh(name: str) -> Path:
    """在工作区任务目录下准备空目录；只删除本任务 tests 下的路径。"""
    target = TESTS_TMP / name
    if target.exists():
        assert target.resolve().is_relative_to(TESTS_TMP.resolve()), \
            "拒绝清理本任务目录之外的路径"
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _presets(count: int = 3) -> list[BrushPreset]:
    """构造 count 个采样笔刷（灰度值不同 → 产物字节不同）。"""
    return [
        BrushPreset(name=f"笔刷{i}",
                    tip_gray=np.full((16, 16), 200 + i, dtype=np.uint8))
        for i in range(count)
    ]


def _kpp_bytes(name: str, value: int = 0) -> bytes:
    """构造一个合法 .kpp（8x8 RGBA 预览 + 预设 XML）。"""
    preview = np.zeros((8, 8, 4), dtype=np.uint8)
    preview[..., :3] = value
    xml = build_preset_xml(name, "", None)
    return build_kpp(xml, preview)


def _run(out: Path, presets: list[BrushPreset] | None = None, **kwargs):
    selected = _presets() if presets is None else presets
    return convert_presets(_FakeAbr(), selected, str(out), **kwargs)


def _mtimes(root: Path) -> dict[Path, int]:
    return {path: path.stat().st_mtime_ns
            for path in root.rglob("*") if path.is_file()}


def _staging_dirs(root: Path) -> list[Path]:
    return [path for path in root.glob(f"{STAGING_PREFIX}*") if path.is_dir()]


# ---------------------------------------------------------------------------
# 1) 暂存 → 回读验证 → 最终交付；事务清单可读
# ---------------------------------------------------------------------------


def test_staging_verified_then_committed_with_manifest():
    base = _fresh("stage_commit")
    out = base / "out"
    staging = base / "staging"
    tx = ExportTransaction(out, staging_dir=staging, total=1)
    tx.begin()

    kpp = _kpp_bytes("甲", 10)
    item = tx.stage_kpp("000_甲.kpp", kpp)
    assert item.state == STATE_VERIFIED
    assert item.staged_path and Path(item.staged_path).is_file()
    assert not any((out / "kpp").iterdir()), "交付前最终目录不应出现产物"

    # 事务清单在暂存目录里按 JSON 记录本次计划（sha256 与最终绝对路径）
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "brushconverter.export-transaction"
    assert manifest["items"][0]["sha256"] == item.sha256
    assert Path(manifest["items"][0]["final_path"]).is_absolute()

    content_hash = bundle_content_hash8([("000_甲.kpp", kpp)])
    built = build_bundle([("000_甲.kpp", kpp)], "样本")
    # 补传完整身份（这里是 legacy 口径）；事务会核对 content_hash8 一致。
    bundle_item = tx.stage_bundle(
        "样本.bundle", built.data, content_hash, identity=built.identity)
    assert bundle_item.state == STATE_VERIFIED
    assert not (out / "样本.bundle").exists()

    summary = tx.commit()
    assert summary.status == RESULT_STATUS_COMMITTED
    assert summary.cancellable is False
    assert summary.counts["new"] == 2
    assert summary.items[0].state == STATE_COMMITTED

    final_kpp = out / "kpp" / "000_甲.kpp"
    final_bundle = out / "样本.bundle"
    assert final_kpp.read_bytes() == kpp
    assert read_preset_xml(final_kpp.read_bytes())
    assert verify_bundle(final_bundle).ok
    assert summary.bundle_verify is not None and summary.bundle_verify.ok
    assert not staging.exists(), "交付成功后本次专属暂存目录应被清理"
    assert txmod._same_volume(out, base)


# ---------------------------------------------------------------------------
# 2) 取消：不写最终目录、不动旧文件、清理暂存、可再次转换
# ---------------------------------------------------------------------------


def test_cancel_before_commit_leaves_no_files_and_keeps_existing():
    base = _fresh("cancel")
    out = base / "out"
    (out / "kpp").mkdir(parents=True)
    old_kpp = out / "kpp" / "000_笔刷0.kpp"
    old_kpp.write_bytes(b"old-kpp")
    keep = out / "keep.txt"
    keep.write_bytes(b"keep")
    old_mtimes = _mtimes(out)

    token = CancelToken()
    events = []

    def on_progress(event):
        events.append(event)
        if event.phase == PHASE_CONVERT and not token.cancelled:
            token.cancel("用户在转换阶段取消")

    result = _run(out, cancel=token, progress=on_progress)

    assert result.cancelled is True
    assert result.status == RESULT_STATUS_CANCELLED
    assert "已取消" in result.status_message()
    assert "转换完成" not in result.status_message()
    assert result.kpp_files == []
    assert result.bundle_path is None
    assert result.verify is None
    assert result.rendered_in_memory == 1
    assert result.not_processed == ["笔刷1", "笔刷2"]
    assert result.transaction.status == RESULT_STATUS_CANCELLED

    assert old_kpp.read_bytes() == b"old-kpp"
    assert keep.read_bytes() == b"keep"
    assert _mtimes(out) == old_mtimes, "取消后已有文件内容与 mtime 都不能变"
    assert [path.name for path in (out / "kpp").iterdir()] == ["000_笔刷0.kpp"]
    assert _staging_dirs(out) == [], "取消后不能留下本批暂存目录"
    assert events[-1].phase == CANCELLED_PHASE
    assert all(event.phase != PHASE_DONE for event in events)

    # 取消后可再次转换：旧文件仍不被覆盖，新产物顺延。
    again = _run(out)
    assert again.status == RESULT_STATUS_COMMITTED
    assert old_kpp.read_bytes() == b"old-kpp"
    assert keep.read_bytes() == b"keep"
    assert (out / "kpp" / "000_笔刷0_2.kpp").is_file()
    assert not _staging_dirs(out)


def test_cancel_at_write_checkpoint_leaves_no_files():
    """暂存全部完成后的最后一个可取消检查点：取消仍不得进入交付。"""
    base = _fresh("cancel_write")
    out = base / "out"
    token = CancelToken()
    events = []

    def on_event(event):
        events.append(event)
        if event.phase == PHASE_WRITE and not token.cancelled:
            token.cancel("暂存完成时取消")

    result = _run(out, cancel=token, progress=on_event)

    assert result.cancelled is True
    assert result.status == RESULT_STATUS_CANCELLED
    assert result.bundle_path is None
    assert not list(out.glob("*.bundle"))
    assert not (out / "kpp").exists() or not any((out / "kpp").iterdir())
    assert _staging_dirs(out) == []
    assert events[-1].phase == CANCELLED_PHASE


def test_pre_cancelled_token_creates_no_directory():
    base = _fresh("precancel")
    out = base / "out"
    token = CancelToken()
    token.cancel("开工前取消")

    result = _run(out, cancel=token)

    assert result.cancelled is True
    assert result.status == RESULT_STATUS_CANCELLED
    assert result.not_processed == ["笔刷0", "笔刷1", "笔刷2"]
    assert result.transaction.status == RESULT_STATUS_CANCELLED
    assert not out.exists(), "取消发生在 begin 检查点，连输出目录都不应创建"


# ---------------------------------------------------------------------------
# 3) 零选中 / 全跳过：明确状态且不创建目录
# ---------------------------------------------------------------------------


def test_zero_selection_status_and_no_files():
    out = _fresh("empty") / "out"
    events = []

    result = _run(out, [], progress=events.append)

    assert result.status == RESULT_STATUS_EMPTY
    assert result.kpp_files == [] and result.bundle_path is None
    assert result.status_message() == "零选中：未写出任何文件"
    assert not out.exists()
    assert [event.phase for event in events] == [PHASE_PREPARE, PHASE_DONE]


def test_all_skipped_status_and_no_files():
    out = _fresh("skipped") / "out"
    presets = [BrushPreset(name=f"缺笔尖{i}") for i in range(2)]

    result = _run(out, presets)

    assert result.status == RESULT_STATUS_ALL_SKIPPED
    assert len(result.skipped) == 2
    assert result.kpp_files == [] and result.bundle_path is None
    assert "全部跳过" in result.status_message()
    assert not out.exists()


# ---------------------------------------------------------------------------
# 4) 重名与复用：独立 kpp 与 bundle 都不覆盖旧同名文件
# ---------------------------------------------------------------------------


def test_independent_kpp_never_overwrites_old_same_name():
    base = _fresh("kpp_conflict")
    out = base / "out"
    (out / "kpp").mkdir(parents=True)
    old = out / "kpp" / "000_笔刷0.kpp"
    old.write_bytes(b"old")
    old_mtime = old.stat().st_mtime_ns

    result = _run(out, make_bundle=False)

    assert result.status == RESULT_STATUS_COMMITTED
    new_path = Path(result.standalone_paths["000_笔刷0.kpp"])
    assert new_path.name == "000_笔刷0_2.kpp"
    assert new_path.is_file()
    assert old.read_bytes() == b"old"
    assert old.stat().st_mtime_ns == old_mtime
    assert any("顺延编号" in warning for warning in result.warnings)


def test_same_content_kpp_reused_mtime_unchanged():
    out = _fresh("kpp_reuse") / "out"
    first = _run(out, make_bundle=False)
    paths = {name: Path(path) for name, path in first.standalone_paths.items()}
    assert len(paths) == 3
    before = {path: path.stat().st_mtime_ns for path in paths.values()}

    second = _run(out, make_bundle=False)

    assert second.status == RESULT_STATUS_REUSED_ONLY
    assert second.bundle_path is None
    assert {path: path.stat().st_mtime_ns for path in paths.values()} == before
    assert "全部产物已存在同内容文件" in second.status_message()


def test_bundle_conflict_suffix_then_reuse_without_rewrite():
    out = _fresh("bundle_conflict") / "out"
    first = _run(out, make_bundle=True, make_standalone=False)
    first_bundle = Path(first.bundle_path)
    assert first_bundle.is_file()

    occupied = b"not a bundle, occupies the target path"
    first_bundle.write_bytes(occupied)

    second = _run(out, make_bundle=True, make_standalone=False)
    second_bundle = Path(second.bundle_path)
    assert second.bundle_renamed is True
    assert second_bundle.name == f"{first_bundle.stem}_2{first_bundle.suffix}"
    assert first_bundle.read_bytes() == occupied, "旧同名文件必须一字不动"
    assert verify_bundle(second_bundle).ok
    assert second.verify is not None and second.verify.ok

    mtime = second_bundle.stat().st_mtime_ns
    third = _run(out, make_bundle=True, make_standalone=False)
    assert third.bundle_reused is True
    assert Path(third.bundle_path) == second_bundle
    assert second_bundle.stat().st_mtime_ns == mtime, "同内容复用不得重写文件"


# ---------------------------------------------------------------------------
# 5) 注入测试：暂存磁盘错误、回读失败、提交中失败、回滚失败
# ---------------------------------------------------------------------------


def test_disk_error_during_staging_writes_nothing(monkeypatch):
    base = _fresh("disk_stage")
    out = base / "out"
    out.mkdir(parents=True)
    keep = out / "keep.txt"
    keep.write_bytes(b"keep")
    real_write = txmod._write_bytes

    def flaky_write(path, data):
        if str(path).endswith(".kpp"):
            raise OSError(28, "No space left on device（注入）")
        return real_write(path, data)

    monkeypatch.setattr(txmod, "_write_bytes", flaky_write)

    with pytest.raises(ExportTransactionError) as caught:
        _run(out, make_bundle=False)

    error = caught.value
    assert "安全写出失败" in str(error)
    assert error.summary is not None
    assert error.summary.status == RESULT_STATUS_FAILED
    assert keep.read_bytes() == b"keep"
    assert not (out / "kpp").exists()
    assert _staging_dirs(out) == []


def test_staging_structure_error_cleans_staging(monkeypatch):
    base = _fresh("staging_verify")
    out = base / "out"
    out.mkdir(parents=True)

    def flaky_read(blob):
        raise ValueError("注入：kpp 结构损坏")

    monkeypatch.setattr(txmod, "read_preset_xml", flaky_read)

    with pytest.raises(ExportTransactionError) as caught:
        _run(out, make_bundle=False)

    error = caught.value
    assert error.summary is not None
    assert error.summary.status == RESULT_STATUS_FAILED
    assert not (out / "kpp").exists(), "暂存失败后不应留下空的独立 kpp 目录"
    assert _staging_dirs(out) == []


def test_commit_failure_rolls_back_only_new_files(monkeypatch):
    base = _fresh("commit_fail")
    out = base / "out"
    out.mkdir(parents=True)
    keep = out / "keep.txt"
    keep.write_bytes(b"keep")
    keep_mtime = keep.stat().st_mtime_ns
    real_place = txmod._place_file
    calls = []

    def flaky_place(staged, final):
        calls.append(final.name)
        if len(calls) == 2:
            raise OSError(28, "磁盘已满（注入）")
        return real_place(staged, final)

    monkeypatch.setattr(txmod, "_place_file", flaky_place)

    with pytest.raises(ExportTransactionError) as caught:
        _run(out, make_bundle=False)

    summary = caught.value.summary
    assert summary.status == RESULT_STATUS_ROLLED_BACK
    assert summary.counts["new"] == 0
    assert len(summary.rolled_back) == 1
    assert len(summary.blocked) == 1
    assert "已按本次新建清单回滚" in summary.status_message()
    assert keep.read_bytes() == b"keep"
    assert keep.stat().st_mtime_ns == keep_mtime
    assert not (out / "kpp").exists(), "回滚后新建的独立目录也应清掉"
    assert _staging_dirs(out) == []


def test_rollback_failure_keeps_blocked_scene(monkeypatch):
    base = _fresh("rollback_fail")
    out = base / "out"
    out.mkdir(parents=True)
    state = {"first": None, "calls": 0}
    real_place = txmod._place_file
    real_remove = txmod._remove_file

    def flaky_place(staged, final):
        state["calls"] += 1
        if state["calls"] == 2:
            raise OSError(28, "磁盘已满（注入）")
        state["first"] = final
        return real_place(staged, final)

    def flaky_remove(path):
        if state["first"] is not None and Path(path) == state["first"]:
            raise OSError(5, "拒绝访问（注入）")
        return real_remove(path)

    monkeypatch.setattr(txmod, "_place_file", flaky_place)
    monkeypatch.setattr(txmod, "_remove_file", flaky_remove)

    with pytest.raises(ExportTransactionError) as caught:
        _run(out, make_bundle=False)

    summary = caught.value.summary
    assert summary.status == RESULT_STATUS_PARTIAL_BLOCKED
    assert state["first"].exists(), "无法可靠回滚的文件必须保留现场"
    assert str(state["first"]) in summary.retained
    assert len(summary.blocked) >= 2
    assert "不宣称全有或全无" in summary.status_message()


def test_final_verification_failure_rolls_back(monkeypatch):
    base = _fresh("final_verify")
    out = base / "out"
    real_verify = txmod.verify_bundle

    def flaky_verify(path):
        verified = real_verify(path)
        if STAGING_PREFIX not in str(path):
            verified.ok = False
            verified.errors.append("注入：交付后校验失败")
        return verified

    monkeypatch.setattr(txmod, "verify_bundle", flaky_verify)

    with pytest.raises(ExportTransactionError) as caught:
        _run(out, make_bundle=True, make_standalone=False)

    summary = caught.value.summary
    assert summary.status == RESULT_STATUS_ROLLED_BACK
    assert list(out.glob("*.bundle")) == []
    assert any("资源包" in entry["reason"] for entry in summary.blocked)


def test_commit_refuses_target_created_after_planning():
    base = _fresh("late_target")
    out = base / "out"
    tx = ExportTransaction(out, total=1)
    tx.begin()
    item = tx.stage_kpp("000_甲.kpp", _kpp_bytes("甲", 3))
    final = Path(item.final_path)
    final.write_bytes(b"someone else")

    with pytest.raises(ExportTransactionError) as caught:
        tx.commit()

    summary = caught.value.summary
    assert summary.status == RESULT_STATUS_ROLLED_BACK
    assert final.read_bytes() == b"someone else", "交付阶段出现的同名文件不得被覆盖"
    assert summary.blocked and summary.blocked[0]["name"] == final.name


# ---------------------------------------------------------------------------
# 6) 最终交付阶段不可取消；未知内容不清理；无悬挂线程
# ---------------------------------------------------------------------------


def test_commit_phase_ignores_cancel_but_records_it(monkeypatch):
    base = _fresh("late_cancel")
    out = base / "out"
    staging = base / "staging"
    token = CancelToken()
    holder = {}
    observed = {}
    real_place = txmod._place_file

    def place_then_cancel(staged, final):
        real_place(staged, final)
        observed["cancellable"] = holder["tx"].cancellable
        token.cancel("交付阶段取消（注入）")

    monkeypatch.setattr(txmod, "_place_file", place_then_cancel)

    tx = ExportTransaction(out, staging_dir=staging, token=token, total=2)
    holder["tx"] = tx
    tx.begin()
    tx.stage_kpp("000_甲.kpp", _kpp_bytes("甲", 1))
    tx.stage_kpp("001_乙.kpp", _kpp_bytes("乙", 2))

    summary = tx.commit()

    assert summary.status == RESULT_STATUS_COMMITTED
    assert summary.cancel_requested_late is True
    assert summary.cancellable is False
    assert observed["cancellable"] is False
    assert len(summary.committed) == 2
    assert (out / "kpp" / "000_甲.kpp").is_file()
    assert (out / "kpp" / "001_乙.kpp").is_file()
    assert not staging.exists()


def test_cleanup_keeps_unknown_staging_content():
    base = _fresh("unknown_staging")
    out = base / "out"
    staging = base / "staging"
    tx = ExportTransaction(out, staging_dir=staging, total=1)
    tx.begin()
    unknown = staging / "他人文件.txt"
    unknown.write_bytes(b"unknown")

    summary = tx.cancel("测试取消")

    assert summary.status == RESULT_STATUS_CANCELLED
    assert unknown.read_bytes() == b"unknown"
    assert str(unknown) in summary.retained or str(staging) in summary.retained
    assert staging.exists()
    shutil.rmtree(staging)  # 只清理本测试创建的任务目录


def test_progress_phases_on_success_and_cancel():
    out = _fresh("progress") / "out"
    events = []
    result = _run(out, make_bundle=False, progress=events.append)

    assert result.status == RESULT_STATUS_COMMITTED
    phases = [event.phase for event in events]
    assert phases[0] == PHASE_PREPARE
    assert phases.count(PHASE_CONVERT) == 3
    assert phases[-1] == PHASE_DONE
    assert PHASE_WRITE in phases, "最终交付前必须给出“不可取消”的写阶段事件"
    done = events[-1]
    assert done.completed == done.total == 3
    assert done.cancelled is False

    cancel_out = _fresh("progress_cancel") / "out"
    token = CancelToken()
    cancel_events = []

    def on_event(event):
        cancel_events.append(event)
        if event.phase == PHASE_CONVERT and not token.cancelled:
            token.cancel("取消")

    cancelled = _run(cancel_out, cancel=token, progress=on_event)
    assert cancelled.cancelled is True
    assert cancel_events[-1].phase == CANCELLED_PHASE
    assert cancel_events[-1].cancelled is True
    assert all(event.phase != PHASE_DONE for event in cancel_events)


def test_done_callback_error_does_not_undo_delivery():
    out = _fresh("done_callback") / "out"

    def on_event(event):
        if event.phase == PHASE_DONE:
            raise RuntimeError("注入：界面回调抛错")

    result = _run(out, make_bundle=False, progress=on_event)

    assert result.status == RESULT_STATUS_COMMITTED
    assert (out / "kpp" / "000_笔刷0.kpp").is_file()
    assert any("进度回调在完成事件中抛错" in warning for warning in result.warnings)


def test_no_dangling_threads_after_cancel_and_rerun():
    before = {thread.ident for thread in threading.enumerate() if thread.is_alive()}
    out = _fresh("threads") / "out"
    token = CancelToken()

    def on_event(event):
        if event.phase == PHASE_CONVERT and not token.cancelled:
            token.cancel("取消")

    _run(out, cancel=token, progress=on_event)
    _run(out, make_bundle=False)

    after = {thread.ident for thread in threading.enumerate() if thread.is_alive()}
    assert after - before == set(), "转换/取消不应遗留悬挂线程"


def test_staging_dir_is_exclusive_and_same_volume():
    base = _fresh("staging_dir")
    out = base / "out"
    staging = base / "staging"
    out.mkdir(parents=True)

    tx = ExportTransaction(out, staging_dir=staging, total=0)
    tx.begin()
    assert txmod._same_volume(out, staging)
    summary = tx.cancel("清理")
    assert summary.staging_dir == str(staging)
    assert not staging.exists()
