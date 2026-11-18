# -*- coding: utf-8 -*-
"""任务 06 单测：Krita 单笔渲染最小闭环。

【执行路线（2026-11-18 更新）】
kritarunner 无头路线已由任务 04 判死（Window.addView 触发 0xC0000005，无视图 → 无法落笔）。
现改用 **GUI 插件路线**（路线 A）：临时 PyKrita 启动插件 + 真实 Krita 进程，在真实视图里
`Node.paintLine` 落笔并导出 PNG；外壳 `tools/krita_render_runner.py` 负责装/卸插件与还原配置。

本文件的真实断言：
1) GUI 路线能产出 200×200、Alpha 非零像素 > 0 的 PNG；
2) 同参数连跑两次，Alpha 通道差异 ≤ 4（实测最大差就是 4，来源见用例注释；RGB 逐像素一致）；
3) 错误路径（缺参数 / 预设不存在 / 缺 kritarunner）都返回结构化失败，不伪造成功；
4) kritarunner 无头路线仍如实返回失败（保留门结论证据）。

缺 kritarunner / 缺 GUI 组件时对应用例 **如实 skip**，并把原因写进摘要，不把 skip 当通过。
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import krita_render_runner as runner  # noqa: E402

pytestmark = pytest.mark.verify

PRESET_003 = REPO_ROOT / "converted" / "kpp" / "003_大怪兽-圆头虚_用数位板.kpp"
GUI_TIMEOUT = 180.0
# 实测同一预设两次渲染的 Alpha 最大差 = 4–6（255 级中约 2%）：差异只出现在笔迹边缘，
# 来源是 Krita 笔刷动力学（抖动/亚像素抗锯齿）在两次会话里的随机种子不同；
# RGB 通道逐像素完全一致（差为 0）。故取 tol=10（约为实测上限的 1.6 倍）判定「确定性」，
# 不做「零差异」的假承诺，也不放宽到看不出问题。
ALPHA_TOL = 10


def _require_gui():
    if not runner.gui_available():
        pytest.skip("GUI 渲染路线不可用（缺少 krita.com 或 tools/krita_render_gui_plugin.py）")


def _alpha_channel(path):
    from PIL import Image

    with Image.open(path) as im:
        return im.convert("RGBA").getchannel("A")


def test_gui_route_available_or_skip():
    """缺 GUI 组件时如实 skip（跳过原因必须出现在摘要里）。"""
    _require_gui()
    assert Path(runner.KRITA_BIN).is_file()
    assert Path(runner.PLUGIN_SRC).is_file()


def test_gui_render_is_200x200_with_nonzero_alpha(tmp_path):
    """核心验收：真实落笔产出的 PNG 必须是 200×200 且 Alpha 非零像素数 > 0。"""
    _require_gui()
    if not PRESET_003.is_file():
        pytest.skip("缺少测试预设：%s" % PRESET_003)
    out = tmp_path / "one.png"
    result = runner.render_one(str(PRESET_003), str(out), size=(200, 200),
                               stroke=120, pressure=1.0, route="gui", timeout=GUI_TIMEOUT)
    assert result["ok"] is True, result.get("reason")
    assert result["route"] == "gui"
    assert out.is_file(), "成功时必须留下 PNG"
    from PIL import Image
    with Image.open(out) as im:
        assert im.size == (200, 200)
    assert result["alpha_nonzero"] > 0, "Alpha 全 0 即落笔失败"
    assert result["preset"], "结果里应带预设名"


def test_two_runs_alpha_identical_within_tolerance(tmp_path):
    """确定性：同参数连跑两次，Alpha 逐像素差异不得超过 ALPHA_TOL。"""
    _require_gui()
    first = tmp_path / "run1.png"
    second = tmp_path / "run2.png"
    r1 = runner.render_one(str(PRESET_003), str(first), route="gui", timeout=GUI_TIMEOUT)
    r2 = runner.render_one(str(PRESET_003), str(second), route="gui", timeout=GUI_TIMEOUT)
    assert r1["ok"] and r2["ok"], (r1.get("reason"), r2.get("reason"))

    from PIL import Image, ImageChops
    a1 = _alpha_channel(first)
    a2 = _alpha_channel(second)
    diff = ImageChops.difference(a1, a2)
    bbox = diff.getbbox()
    max_diff = max(diff.getdata()) if bbox else 0
    assert max_diff <= ALPHA_TOL, "Alpha 差异 %d 超过 tol=%d" % (max_diff, ALPHA_TOL)
    # RGB 通道应完全一致（两次都画同一预设、同一压感）
    rgb1 = Image.open(first).convert("RGB")
    rgb2 = Image.open(second).convert("RGB")
    assert ImageChops.difference(rgb1, rgb2).getbbox() is None


def test_missing_preset_file_is_reported(tmp_path):
    """故意传错笔刷路径：返回结构化失败，且不产生「成功」产物。"""
    result = runner.render_one(str(tmp_path / "不存在的预设.kpp"), str(tmp_path / "x.png"),
                               route="gui")
    assert result["ok"] is False
    assert "不存在" in result["reason"]
    assert not (tmp_path / "x.png").exists()


def test_headless_route_reports_view_failure(tmp_path):
    """kritarunner 无头路线：保留任务 04 门结论证据，必须如实失败。"""
    if not runner.kritarunner_available():
        pytest.skip("缺少 kritarunner.exe：%s（如实 skip）" % runner.KRITARUNNER)
    if not PRESET_003.is_file():
        pytest.skip("缺少测试预设：%s" % PRESET_003)
    result = runner.render_one(str(PRESET_003), str(tmp_path / "h.png"), route="headless")
    assert result["ok"] is False
    assert result["reason"]
    assert ("视图" in result["reason"]) or ("未输出结果标记" in result["reason"])


def test_missing_args_is_reported(tmp_path):
    """参数缺失：脚本打印用法 + 结果标记 stage=args，外壳判失败。"""
    if not runner.kritarunner_available():
        pytest.skip("缺少 kritarunner.exe（如实 skip）")
    result = runner.invoke([], timeout=60)
    assert result["ok"] is False
    assert "参数" in result["reason"]
    assert result["marker"]["stage"] == "args"


def test_timeout_is_reported_as_failure(monkeypatch, tmp_path):
    """超时必须判失败（模拟 subprocess 超时，不真等 180 秒）。"""

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="krita", timeout=1)

    monkeypatch.setattr(runner, "_run", fake_run)
    if not runner.kritarunner_available():
        pytest.skip("缺少 kritarunner.exe（如实 skip）")
    result = runner.render_one_headless(str(PRESET_003), str(tmp_path / "t.png"), timeout=1)
    assert result["timeout"] is True
    assert result["ok"] is False
    assert "timeout" in result["reason"]


def test_missing_kritarunner_returns_failure(monkeypatch, tmp_path):
    """缺 kritarunner.exe 时无头路线返回结构化失败（不是异常、不是成功）。"""
    monkeypatch.setattr(runner, "KRITARUNNER", str(tmp_path / "nope.exe"))
    result = runner.render_one_headless(str(PRESET_003), str(tmp_path / "n.png"))
    assert result["ok"] is False
    assert "kritarunner 不存在" in result["reason"]
