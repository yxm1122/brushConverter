"""阶段3 参数映射的回归测试。

对照用户手动转换的 Krita 参考预设（勾线笔）校准的关键值。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.mapping import map_presets  # noqa: E402

# 测试样本为商用笔刷素材（42MB），不纳入版本库。样本缺失时整体跳过，
# 需跑完整断言时把 .abr 放到 tests/ 目录即可。
ABR = Path(__file__).parent / "海怪笔刷-详情展示的勾线笔套装组18支.abr"

pytestmark = pytest.mark.skipif(
    not ABR.exists(),
    reason="缺少测试样本 .abr（请自备到 tests/ 目录）",
)


def _presets():
    abr = AbrFile.parse(ABR)
    return map_presets(abr)


def test_preset_count():
    # 源文件 20 个预设，去掉 1 个完全重复的「淘宝店」横幅 → 19
    assert len(_presets()) == 19


def test_dedup_banner():
    names = [p.name for p in _presets()]
    assert names.count("淘宝店：大怪兽素材噗收集整理") == 1


def test_scatter_mapping():
    presets = _presets()
    line = next(p for p in presets if "大怪兽-勾线笔" in p.name)
    # 勾线笔 useScatter=True，散布量随压感(bVTy=2)
    assert line.scatter is True
    assert line.scatter_pressure is True
    assert line.scatter_both_axes is False

    # 气泡 useScatter=True 但 bVTy=0（散布不随压感），仍应启用
    bubble = next(p for p in presets if "气泡" in p.name)
    assert bubble.scatter is True
    assert bubble.scatter_pressure is False
    assert bubble.scatter_both_axes is True


def test_scatter_amount():
    presets = _presets()
    # 气泡：scatterDynamics.jitter=145%（顶层 Spcn 是间距，不是散布量）
    bubble = next(p for p in presets if "气泡" in p.name)
    assert bubble.scatter_amount == 145.0
    # 蝴蝶：jitter=85%
    butterfly = next(p for p in presets if "蝴蝶" in p.name)
    assert butterfly.scatter_amount == 85.0
    # 勾线笔：jitter=0（散布启用但量为 0）
    line = next(p for p in presets if "大怪兽-勾线笔" in p.name)
    assert line.scatter_amount == 0.0


def test_line_pen_scale():
    # 大怪兽-勾线笔【用数位板】(index 2) 共用笔尖 87465e27，Dmtr=26
    presets = _presets()
    line = next(p for p in presets if "大怪兽-勾线笔" in p.name)
    assert line.diameter == 26.0
    # 笔尖宽 1895 → scale = 26/1895 ≈ 0.01372
    assert abs(line.scale - 26.0 / 1895.0) < 1e-9


def test_line_pen_dynamics():
    presets = _presets()
    line = next(p for p in presets if "大怪兽-勾线笔" in p.name)
    # 流量 min 17%，不透明度 min 80%，宽高比 min 30%
    assert line.flow_curve == "0,0.17;1,1;"
    assert line.opacity_curve == "0,0.8;1,1;"
    assert line.ratio_curve == "0,0.3;1,1;"
    # 旋转随笔迹方向，散布由压感控制
    assert line.rotation_sensor == "drawingangle"
    assert line.rotation_jitter == 6.0
    assert line.scatter is True


def test_computed_brush():
    presets = _presets()
    comp = next(p for p in presets if "圆头虚" in p.name)
    assert comp.is_computed is True
    assert comp.tip_gray is None
    assert comp.diameter == 25.0
    assert comp.hardness == 84.0
