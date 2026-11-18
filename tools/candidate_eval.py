# -*- coding: utf-8 -*-
"""任务 10：候选「Photoshop 纹理亮度/对比度」公式的逐像素核验脚本。

背景
----
项目当前的纹理亮度/对比度映射是「经验公式」（见 docs/parameter-mapping.md 第 7 节）。
任务 10 要求把调研到的候选公式逐个拿去和真值图逐像素比对，达标与否用**差异比例数字**说话。

本脚本不猜结论，只做两件事：
1) 把候选公式（纯函数，见 CANDIDATES_A / CANDIDATES_B）应用到真值数据上；
2) 用 tools/imgdiff.py 的统一口径算差异比例，并按任务书写死的四档判据判定。

两条核验轴（必须分开看，否则会把「引擎差异」误判成「公式错误」）
----------------------------------------------------------------
A 轴「PS 侧行为复刻」
    输入 = Photoshop 同组基线图（b0_c0）的 Alpha；套候选的 PS 内部亮度/对比度变换；
    真值 = Photoshop 同组同参数图。这一轴回答的是「候选公式是否描述 PS 自己的行为」，
    完全在 PS 一侧，不含 Krita。

B 轴「PS→Krita 参数映射」
    输入 = 候选映射把 PS 的 (亮度, 对比度) 换算成 Krita 的 (Brightness, Contrast)；
    再用 tools/texture_mask.compute_mask()（任务 07 已按 Krita 源码逐行复刻，
    对 research/krita结果/ 的 23 张真值图差异比例 0.00%）渲染出「Krita 会画成什么样」；
    真值 = Photoshop 同参数图。这一轴回答的是「候选公式能不能直接拿去用」——
    即「按它设 Krita 参数，画面是否和 PS 一致」。

C 轴「跨引擎基线差异」（不是候选，是可达性证据）
    同一纹理、同样 0/0 参数下，Krita 渲染与 PS 真值的差异比例。
    如果这个数字本身已经远大于 10%，那么任何 PS→Krita 参数映射在绝对逐像素判据下
    都不可能合格——报告里必须把这个前提摆出来，否则「不合格」会归因错误。

口径（写死，不改）
------------------
* 差异比例：复用 tools/imgdiff.diff_alpha()，即 |ΔAlpha| > 2 灰度级的像素数 / 全部像素数；
  分母是 W×H，不是非零像素数（见 tools/imgdiff.py 模块 docstring）。
* 判定：差异比例 ≤ 2% 记「合格」；2%–10% 记「部分合格」；> 10% 记「不合格」；
  数据缺失/模式不支持/算法不可复刻记「无法核验（原因）」。
* 一个候选只要有一张适用图不合格，就不判「合格」（以最差图为准，不允许用平均值掩盖失败）。

只读边界
--------
只读 research/测试结果、research/测试结果2、research/测试用纹理.png、tests/baselines/ps（若存在）。
预测图写到系统临时目录（tempfile.mkdtemp）只为喂给 imgdiff.diff_alpha，不进仓库、不改任何真值图。

用法
----
    "<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/candidate_eval.py
    "<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/candidate_eval.py --json out.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.imgdiff import DEFAULT_TOL, diff_alpha  # noqa: E402  统一口径，不另算一套
from tools.texture_mask import (  # noqa: E402  Krita 掩码算法复刻（任务 07）
    MODE_HEIGHT_PHOTOSHOP,
    MODE_LINEAR_HEIGHT_PHOTOSHOP,
    MODE_MULTIPLY,
    MODE_SUBTRACT,
    compute_mask,
    texture_mask_from_rgba,
)

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
TEXTURE_PATH = ROOT / "research" / "测试用纹理.png"
DATASET_DIRS = [ROOT / "research" / "测试结果", ROOT / "research" / "测试结果2"]
PS_BASELINE_DIR = ROOT / "tests" / "baselines" / "ps"

#: 真值图里出现的模式名（目录名里带的是中文）
PS_MODES = {
    "减去": "SUBTRACT",
    "正片叠底": "MULTIPLY",
    "变暗": "DARKEN",
    "线性加深": "LINEAR_BURN",
    "高度": "HEIGHT_PS",
    "线性高度": "LINEAR_HEIGHT_PS",
}

#: PS 模式 → Krita TexturingMode 枚举号；None 表示 tools/texture_mask.py 未复刻该模式
KRITA_MODE = {
    "MULTIPLY": MODE_MULTIPLY,                    # 0
    "SUBTRACT": MODE_SUBTRACT,                    # 1
    "HEIGHT_PS": MODE_HEIGHT_PHOTOSHOP,           # 14
    "LINEAR_HEIGHT_PS": MODE_LINEAR_HEIGHT_PHOTOSHOP,  # 15
    "DARKEN": None,                               # Krita 枚举 4，texture_mask 未实现
    "LINEAR_BURN": None,                          # Krita 枚举 9，texture_mask 未实现
}

#: 判据（任务书 §三.4 写死）
BUCKET_OK = 0.02
BUCKET_PARTIAL = 0.10


# ---------------------------------------------------------------------------
# 数据装载
# ---------------------------------------------------------------------------
def _alpha_u8(path: Path) -> np.ndarray:
    """读 PNG 的 Alpha 通道（uint8 二维）。RGB 全黑、信息在 Alpha，口径同 imgdiff。"""
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"), dtype=np.uint8)[:, :, 3]


def _png_md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


@dataclass
class TruthImage:
    path: Path
    mode: str        # PS 模式键（如 MULTIPLY）
    depth: float     # 纹理深度 0..1（目录名 d60 → 0.60）
    brightness: int  # PS textureBrightness
    contrast: int    # PS textureContrast
    group: str       # 目录名


def _parse_filename(name: str):
    """解析 b-150_c0.png / b0_c-50.png / b0_-25.png（d5 组命名不规范，兼容处理）。"""
    stem = name[:-4] if name.lower().endswith(".png") else name
    # 规范写法 b<亮度>_c<对比度>
    m = re.match(r"^b(-?\d+)_c(-?\d+)$", stem)
    if m:
        return int(m.group(1)), int(m.group(2))
    # d5 组里 b0_-25.png 表示「对比度 -25」
    m = re.match(r"^b(-?\d+)_(-?\d+)$", stem)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def load_truth_images() -> list[TruthImage]:
    """扫描两轮 PS 真值图（只读），返回按目录分组的清单。"""
    images: list[TruthImage] = []
    for base in DATASET_DIRS:
        if not base.is_dir():
            continue
        for group_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            m = re.match(r"^(.+)_d(\d+)$", group_dir.name)
            if not m:
                continue
            mode_cn, depth_pct = m.group(1), int(m.group(2))
            mode = PS_MODES.get(mode_cn)
            if mode is None:
                continue
            for png in sorted(group_dir.glob("*.png")):
                parsed = _parse_filename(png.name)
                if parsed is None:
                    continue
                images.append(TruthImage(
                    path=png, mode=mode, depth=depth_pct / 100.0,
                    brightness=parsed[0], contrast=parsed[1], group=group_dir.name,
                ))
    return images


def load_ps_auto_images() -> list[TruthImage]:
    """装载并行任务 05 在 tests/baselines/ps 下产出的「自动纹理A」真值图（若已生成）。

    文件名形状：ps__自动纹理A__<模式中文>__d<深度>_b<亮度>_c<对比度>.png
    这些图与 research/测试结果 不是同一批纹理（用的是并行任务的 64×64 噪点纹理），
    因此单独成组（组名后缀 @ps_auto），只用于 A 轴（PS 侧行为），不参与 C 轴跨引擎基线。
    """
    images: list[TruthImage] = []
    if not PS_BASELINE_DIR.is_dir():
        return images
    pat = re.compile(r"^ps__自动纹理A__(.+?)__d(\d+)_b(-?\d+)_c(-?\d+)\.png$")
    for png in sorted(PS_BASELINE_DIR.glob("ps__自动纹理A__*.png")):
        m = pat.match(png.name)
        if not m:
            continue
        mode_cn, depth_pct, b, c = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        mode = PS_MODES.get(mode_cn)
        if mode is None:
            continue
        images.append(TruthImage(path=png, mode=mode, depth=depth_pct / 100.0,
                                 brightness=b, contrast=c,
                                 group=f"{mode_cn}_d{depth_pct}@ps_auto"))
    return images


def load_texture_mask(size: int) -> np.ndarray:
    """把 64×64 灰度纹理平铺成 size×size 的 0..1 掩码（偏移 0,0，与任务 07 口径一致）。"""
    with Image.open(TEXTURE_PATH) as image:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    tex = texture_mask_from_rgba(rgba)
    if tex.shape[0] == 0:
        raise ValueError("纹理读取为空")
    ys = np.arange(size) % tex.shape[0]
    xs = np.arange(size) % tex.shape[1]
    return tex[np.ix_(ys, xs)]


# ---------------------------------------------------------------------------
# 候选公式
# ---------------------------------------------------------------------------
def _clip255(x: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(x), 0, 255).astype(np.uint8)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _a_applicable(needs: str, img: "TruthImage") -> bool:
    """A 轴的候选只在它能解释的图上核验，避免「亮度候选拿对比度图来比」这种伪失败。

    * brightness：只用「对比度=0、亮度≠0」的图；
    * contrast  ：只用「亮度=0、对比度≠0」的图；
    * both      ：全部非基线图。
    """
    has_b = img.brightness != 0
    has_c = img.contrast != 0
    if needs == "brightness":
        return has_b and not has_c
    if needs == "contrast":
        return has_c and not has_b
    if needs == "both":
        return has_b or has_c
    raise ValueError(f"未知的适用性标记: {needs}")


def ps_contrast_factor(contrast: float) -> float:
    """PS 对比度 → 以中点为心的倍率（研究结论 / 候选「控制量分段因子」）。

    C < 0 → 1 + C/100；C > 0 → 1/(1 - C/100)（C=100 时趋于无穷，调用方需 clamp）。
    """
    c = float(contrast)
    if c < 0:
        return 1.0 + c / 100.0
    if c >= 100.0:
        return 1e6  # 近似无穷，随后由 clamp 截断
    return 1.0 / (1.0 - c / 100.0)


def axis_a_predict(kind: str, base_u8: np.ndarray, brightness: int, contrast: int,
                   mode: str, pivot: float = 127.5) -> np.ndarray | None:
    """A 轴：把候选的 PS 内部亮度/对比度变换套到 PS 基线图上。

    kind 取值见 CANDIDATES_A 的 predictor 字段；返回 uint8 Alpha 预测图。
    只处理「单参数」样本（真值图每组只有亮度或只有对比度，二者不同时出现）。
    """
    base = base_u8.astype(np.float64)

    if kind == "identity":
        return _clip255(base)

    # --- 亮度：0..255 空间里的平移 ---
    if kind == "shift_plus":
        return _clip255(base + brightness)
    if kind == "shift_minus":
        return _clip255(base - brightness)
    if kind == "shift_scale150":  # 按 Adobe 控件范围 -150..150 归一后再平移
        return _clip255(base + brightness * 255.0 / 150.0)

    # --- 亮度 + 对比度组合（先用亮度平移，再做中心倍率） ---
    if kind in ("shift_minus_contrast", "shift_plus_contrast"):
        f = ps_contrast_factor(contrast)
        shifted = base - brightness if kind == "shift_minus_contrast" else base + brightness
        return _clip255((shifted - pivot) * f + pivot)

    # --- 对比度：以 pivot 为心的倍率 ---
    if kind in ("contrast_factor", "contrast_inverse", "contrast_pivot128"):
        f = ps_contrast_factor(contrast)
        if kind == "contrast_inverse":
            f = 1.0 / f
        pv = 128.0 if kind == "contrast_pivot128" else pivot
        return _clip255((base - pv) * f + pv)

    raise ValueError(f"未知的 A 轴候选类型: {kind}")


@dataclass
class CandidateA:
    code: str
    source: str      # 来源（URL 或项目内文件）
    formula: str     # 公式原文/伪代码
    scope: str       # 适用模式
    kind: str        # 对应 axis_a_predict 的 kind
    needs: str       # 只在哪些图上核验：brightness / contrast / both


CANDIDATES_A: list[CandidateA] = [
    CandidateA("A0", "基线（无变换）", "out = base", "全部（对照用，预期不合格）", "identity", "both"),
    CandidateA("A1", "Adobe 官方文档：Use Legacy 为「统一平移所有像素值」"
                     " https://helpx.adobe.com/photoshop/desktop/adjust-color/color-corrections/"
                     "apply-brightness-and-contrast-adjustment.html",
               "out = clip(base + B, 0, 255)", "全部模式（统一 +）", "shift_plus", "brightness"),
    CandidateA("A2", "同上（平移方向取负，检验方向）",
               "out = clip(base - B, 0, 255)", "全部模式（统一 -）", "shift_minus", "brightness"),
    CandidateA("A3", "Adobe 控件范围 -150..150 归一化写法（把滑杆量映射到 0..255）",
               "out = clip(base + B*255/150, 0, 255)", "全部模式", "shift_scale150", "brightness"),
    CandidateA("A4", "项目研究结论 + Adobe 调整层量程：对比度为中心的倍率（pivot=127.5）",
               "f = 1+C/100 (C<0)；f = 1/(1-C/100) (C>0)；out = clip((base-127.5)*f+127.5)",
               "全部模式", "contrast_factor", "contrast"),
    CandidateA("A5", "同 A4（倍率取倒数，检验方向）",
               "out = clip((base-127.5)/f+127.5)", "全部模式", "contrast_inverse", "contrast"),
    CandidateA("A6", "同 A4（pivot 取 128，检验取整口径）",
               "out = clip((base-128)*f+128)", "全部模式", "contrast_pivot128", "contrast"),
    CandidateA("A7", "组合候选（本次调研推导）：亮度平移 + 对比度中心倍率，亮度取负号",
               "out1 = base - B；out = clip((out1-127.5)*f+127.5)", "全部模式", "shift_minus_contrast", "both"),
    CandidateA("A8", "组合候选（本次调研推导）：亮度平移 + 对比度中心倍率，亮度取正号",
               "out1 = base + B；out = clip((out1-127.5)*f+127.5)", "全部模式", "shift_plus_contrast", "both"),
]


@dataclass
class CandidateB:
    code: str
    source: str
    formula: str
    scope: str


CANDIDATES_B: list[CandidateB] = [
    CandidateB("B0（现有经验公式·基线）",
               "src/brush_converter/kpp/preset_xml.py（项目现状，docs/parameter-mapping.md 第 7 节）",
               "Kb = round(clamp(0.10 - PS_b/250, -1, 1), 2)；"
               "Kc = round(clamp(PS_c<0 ? 1+PS_c/100 : 1/(1-PS_c/100), 0, 2), 2)",
               "全部模式（统一）"),
    CandidateB("B1（Krita 源码直译·负号）",
               "Krita KisTextureMaskInfo.cpp：mask -= brightness；mask = (mask-0.5)*contrast+0.5"
               "（https://invent.kde.org/graphics/krita/-/raw/master/plugins/paintops/libpaintop/"
               "KisTextureMaskInfo.cpp）",
               "Kb = clamp(-PS_b/255, -1, 1)；Kc = clamp(PS_c 因子, 0, 2)",
               "全部模式（统一）"),
    CandidateB("B2（Krita 源码直译·正号）",
               "同 B1（亮度方向取反，检验符号）",
               "Kb = clamp(+PS_b/255, -1, 1)；Kc = clamp(PS_c 因子, 0, 2)",
               "全部模式（统一）"),
    CandidateB("B3（Adobe 量程归一·负号）",
               "Adobe 官方文档：亮度滑杆 -150..150、对比度 -50..100",
               "Kb = clamp(-PS_b/150, -1, 1)；Kc = clamp(PS_c 因子, 0, 2)",
               "全部模式（统一）"),
    CandidateB("B4（Krita 源码直译·对比度取倒数）",
               "research/photoshop-texture-test-analysis.md 观察：Linear Height 对比度方向相反",
               "Kb = clamp(-PS_b/255, -1, 1)；Kc = clamp(1/PS_c 因子, 0, 2)",
               "全部模式（统一）"),
    CandidateB("B5（恒等映射）",
               "无来源（对照用：PS 参数原样当 Krita 参数）",
               "Kb = 0；Kc = 1", "全部模式"),
    CandidateB("B6（Krita 直译 + 基线偏移补偿·拟合）",
               "本次调研推导（拟合项，非第三方来源）：用 C 轴测出的同组基线中位偏移补偿引擎差异",
               "Kb = clamp(-PS_b/255, -1, 1)；Kc = clamp(因子, 0, 2)；"
               "pred += median(PS_base - Krita_base)（按模式+深度组）",
               "全部模式"),
]


def axis_b_params(code: str, ps_b: int, ps_c: int) -> tuple[float, float]:
    """B 轴：候选把 PS (b, c) 换算成 Krita (Brightness, Contrast) 参数。"""
    f = ps_contrast_factor(ps_c)
    if code.startswith("B0"):
        kb = round(_clamp(0.10 - ps_b / 250.0, -1.0, 1.0), 2)
        kc = round(_clamp(f, 0.0, 2.0), 2)
    elif code.startswith("B1"):
        kb = round(_clamp(-ps_b / 255.0, -1.0, 1.0), 2)
        kc = round(_clamp(f, 0.0, 2.0), 2)
    elif code.startswith("B2"):
        kb = round(_clamp(ps_b / 255.0, -1.0, 1.0), 2)
        kc = round(_clamp(f, 0.0, 2.0), 2)
    elif code.startswith("B3"):
        kb = round(_clamp(-ps_b / 150.0, -1.0, 1.0), 2)
        kc = round(_clamp(f, 0.0, 2.0), 2)
    elif code.startswith("B4"):
        kb = round(_clamp(-ps_b / 255.0, -1.0, 1.0), 2)
        kc = round(_clamp(1.0 / f, 0.0, 2.0), 2)
    elif code.startswith("B5"):
        kb, kc = 0.0, 1.0
    elif code.startswith("B6"):
        # 与 B1 同一映射，额外在渲染后叠加「同组基线中位偏移」补偿（拟合项，见调用处）
        kb = round(_clamp(-ps_b / 255.0, -1.0, 1.0), 2)
        kc = round(_clamp(f, 0.0, 2.0), 2)
    else:
        raise ValueError(f"未知的 B 轴候选: {code}")
    return kb, kc


# ---------------------------------------------------------------------------
# 度量
# ---------------------------------------------------------------------------
def measure(pred_u8: np.ndarray, truth_path: Path, tmpdir: Path, tag: str) -> dict:
    """把预测图写成临时 PNG 再交给 tools/imgdiff.diff_alpha（统一口径，不另算）。"""
    tmp_png = tmpdir / (re.sub(r"[^0-9A-Za-z_.-]", "_", tag) + ".png")
    rgba = np.zeros((pred_u8.shape[0], pred_u8.shape[1], 4), dtype=np.uint8)
    rgba[:, :, 3] = pred_u8
    Image.fromarray(rgba, mode="RGBA").save(tmp_png)
    result = diff_alpha(tmp_png, truth_path)   # tol=DEFAULT_TOL=2，与全项目一致
    result["mae"] = float(np.mean(np.abs(pred_u8.astype(np.int16) - _alpha_u8(truth_path).astype(np.int16))))
    result["tol"] = DEFAULT_TOL
    return result


def bucket(ratio: float) -> str:
    if ratio <= BUCKET_OK:
        return "合格"
    if ratio <= BUCKET_PARTIAL:
        return "部分合格"
    return "不合格"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="任务 10：候选公式逐像素核验")
    parser.add_argument("--json", default="", help="把完整结果写成 JSON（可选路径）")
    parser.add_argument("--skip-b", action="store_true", help="只跑 A 轴（调试用）")
    parser.add_argument("--markdown", action="store_true", help="额外打印可直接粘进报告的 Markdown 表")
    args = parser.parse_args(argv)

    images = load_truth_images()
    auto_images = load_ps_auto_images()
    if auto_images:
        images.extend(auto_images)
        print(f"[数据] 追加并行任务 05 的「自动纹理A」真值图 {len(auto_images)} 张（组名带 @ps_auto）")
    if not images:
        print("错误：没有找到真值图（research/测试结果*）", file=sys.stderr)
        return 2
    groups: dict[str, list[TruthImage]] = {}
    for img in images:
        groups.setdefault(img.group, []).append(img)
    print(f"[数据] 真值图 {len(images)} 张，{len(groups)} 组：")
    for name, items in groups.items():
        print(f"  - {name}: {len(items)} 张（模式={items[0].mode}, 深度={items[0].depth:.2f}）")

    tmpdir = Path(tempfile.mkdtemp(prefix="cand_eval_"))
    out: dict = {"groups": {}, "axis_a": [], "axis_b": [], "axis_c": [], "meta": {}}
    try:
        # 每组基线（b0_c0）与其 md5：C 轴与 A 轴都要用
        baselines: dict[str, TruthImage] = {}
        for name, items in groups.items():
            for img in items:
                if img.brightness == 0 and img.contrast == 0:
                    baselines[name] = img
        print(f"\n[基线] 找到 b0_c0 基线的组：{len(baselines)} / {len(groups)}")
        for name in groups:
            if name in baselines:
                out["groups"][name] = {
                    "mode": groups[name][0].mode,
                    "depth": groups[name][0].depth,
                    "count": len(groups[name]),
                    "baseline_md5": _png_md5(baselines[name].path),
                }

        # ---------------- A 轴：PS 侧行为复刻 ----------------
        print("\n[A 轴] PS 侧行为复刻（输入=PS 基线图，真值=PS 同参数图）")
        for cand in CANDIDATES_A:
            rows = []
            for name, items in groups.items():
                base_img = baselines.get(name)
                if base_img is None:
                    continue
                base_alpha = _alpha_u8(base_img.path)
                for img in items:
                    if img.brightness == 0 and img.contrast == 0:
                        continue  # 基线自己不用比
                    if not _a_applicable(cand.needs, img):
                        continue  # 该候选不解释这一类图（见 _a_applicable 注释）
                    pred = axis_a_predict(cand.kind, base_alpha, img.brightness, img.contrast, img.mode)
                    if pred is None:
                        continue
                    m = measure(pred, img.path, tmpdir, f"A_{cand.code}_{img.group}_{img.path.name}")
                    rows.append({"group": name, "image": img.path.name,
                                 "b": img.brightness, "c": img.contrast,
                                 "ratio": m["diff_ratio"], "max_delta": m["max_delta"],
                                 "mae": m["mae"], "bucket": bucket(m["diff_ratio"])})
            worst = max((r["ratio"] for r in rows), default=None)
            mean = float(np.mean([r["ratio"] for r in rows])) if rows else None
            verdict = bucket(worst) if worst is not None else "无法核验（无适用图）"
            out["axis_a"].append({"code": cand.code, "source": cand.source, "formula": cand.formula,
                                  "scope": cand.scope, "n": len(rows), "worst": worst,
                                  "mean": mean, "verdict": verdict, "rows": rows})
            print(f"  {cand.code}: 用图 {len(rows)} 张，最差差异比例 "
                  f"{'-' if worst is None else f'{worst*100:.2f}%'}，"
                  f"平均 {'-' if mean is None else f'{mean*100:.2f}%'} → {verdict}")

        # 分模式看 A 轴（判方向：同一候选在不同模式下差异比例不同）
        print("\n[A 轴·分模式] 各候选在每个模式组上的最差差异比例（用于判断方向是否随模式改变）")
        mode_order = ["减去", "正片叠底", "变暗", "线性加深", "高度", "线性高度"]
        print("  候选 | " + " | ".join(mode_order) + "（每格=该模式所有深度组的最差差异比例）")
        out["axis_a_by_mode"] = {}
        for cand in CANDIDATES_A:
            cells = []
            for m in mode_order:
                keys = [k for k in groups if k.startswith(m + "_d")]
                ratios = []
                for k in keys:
                    base_img = baselines.get(k)
                    if base_img is None:
                        continue
                    base_alpha = _alpha_u8(base_img.path)
                    for img in groups[k]:
                        if img.brightness == 0 and img.contrast == 0:
                            continue
                        if not _a_applicable(cand.needs, img):
                            continue
                        pred = axis_a_predict(cand.kind, base_alpha, img.brightness, img.contrast, img.mode)
                        if pred is None:
                            continue
                        mt = measure(pred, img.path, tmpdir, f"Am_{cand.code}_{img.group}_{img.path.name}")
                        ratios.append(mt["diff_ratio"])
                cells.append(None if not ratios else max(ratios))
            out["axis_a_by_mode"][cand.code] = {m: c for m, c in zip(mode_order, cells)}
            print(f"  {cand.code} | " + " | ".join(
                "-" if c is None else f"{c*100:.1f}%" for c in cells))

        # ---------------- A 轴·组×候选矩阵（报告用：看清哪些「模式+深度」组合被候选稳定复现） ----------------
        # 注意：不能用「该组最好的一张图」下结论——亮度极大值会让上下截断饱和，
        # 饱和图对任何候选都容易 0%，属于伪精确。这里按「该组全部适用图的最差差异比例」评估。
        group_matrix: dict[str, dict[str, dict]] = {}
        for row in out["axis_a"]:
            for r in row["rows"]:
                slot = group_matrix.setdefault(r["group"], {}).setdefault(
                    row["code"], {"worst": 0.0, "n": 0, "ok": 0})
                slot["worst"] = max(slot["worst"], r["ratio"])
                slot["n"] += 1
                if r["ratio"] <= BUCKET_OK:
                    slot["ok"] += 1
        best_per_group = {}
        for g, cells in group_matrix.items():
            code, cell = min(cells.items(), key=lambda kv: kv[1]["worst"])
            best_per_group[g] = {"code": code, "ratio": cell["worst"],
                                 "n": cell["n"], "ok": cell["ok"],
                                 "bucket": bucket(cell["worst"])}
        out["axis_a_group_matrix"] = group_matrix
        out["axis_a_best_per_group"] = best_per_group
        print("\n[A 轴·组×候选] 每组的「最差差异比例」（括号内为该组全部图都≤2% 的候选）")
        for g in sorted(group_matrix):
            cells = group_matrix[g]
            best = best_per_group[g]
            perfect = [c for c, v in cells.items() if v["n"] > 0 and v["ok"] == v["n"]]
            print(f"  {g}: 最优 {best['code']} {best['ratio']*100:.2f}%（{best['ok']}/{best['n']} 张合格）"
                  f"{'；全图合格候选=' + ','.join(sorted(perfect)) if perfect else '；无候选能让全组合格'}")

        # ---------------- 附：并行任务 tests/baselines/ps 的 v1 图是否为同源副本 ----------------
        dup_check = {"checked": 0, "identical": 0, "different": [], "unmatched": []}
        if PS_BASELINE_DIR.is_dir():
            for png in sorted(PS_BASELINE_DIR.glob("ps__定量测试v1__*.png")):
                m = re.match(r"^ps__定量测试v1__(.+?)__d(\d+)_b(-?\d+)_c(-?\d+)\.png$", png.name)
                if not m:
                    continue
                mode_cn, depth_pct, b, c = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
                target = ROOT / "research" / "测试结果" / f"{mode_cn}_d{depth_pct}" / f"b{b}_c{c}.png"
                dup_check["checked"] += 1
                if not target.exists():
                    dup_check["unmatched"].append(png.name)
                    continue
                if _png_md5(png) == _png_md5(target):
                    dup_check["identical"] += 1
                else:
                    dup_check["different"].append(png.name)
        out["meta"]["ps_v1_dup_check"] = dup_check
        print(f"\n[附·同源核对] tests/baselines/ps 下 ps__定量测试v1__* 共 {dup_check['checked']} 张："
              f"与 research/测试结果 同 md5 的 {dup_check['identical']} 张，不同 {len(dup_check['different'])} 张，"
              f"找不到对应 {len(dup_check['unmatched'])} 张")
        if dup_check["different"]:
            print("  内容不同的：" + ", ".join(dup_check["different"][:10]))
        # ---------------- B 轴：PS→Krita 参数映射 ----------------
        if not args.skip_b:
            print("\n[B 轴] PS→Krita 参数映射（输入=映射后的 Krita 参数 → Krita 算法复刻渲染，真值=PS 同参数图）")
            tex100 = load_texture_mask(100)
            # B6 需要「同组基线中位偏移」：先用基线图（b0_c0）量一次引擎差异
            baseline_offsets: dict[str, float] = {}
            for name, items in groups.items():
                base_img = baselines.get(name)
                kmode = KRITA_MODE.get(items[0].mode)
                if base_img is None or kmode is None:
                    continue
                base_pred = compute_mask(tex100, False, 0.0, 1.0, kmode, items[0].depth)
                base_pred_u8 = _clip255(base_pred.astype(np.float64) * 255.0)
                baseline_offsets[name] = float(np.median(
                    _alpha_u8(base_img.path).astype(np.float64) - base_pred_u8.astype(np.float64)))
            for cand in CANDIDATES_B:
                rows = []
                skipped = []
                for name, items in groups.items():
                    mode = items[0].mode
                    kmode = KRITA_MODE.get(mode)
                    if kmode is None:
                        skipped.append(f"{name}（Krita 侧无对应可复刻模式）")
                        continue
                    depth = items[0].depth
                    for img in items:
                        kb, kc = axis_b_params(cand.code, img.brightness, img.contrast)
                        pred = compute_mask(tex100, False, kb, kc, kmode, depth)
                        pred_u8 = _clip255(pred.astype(np.float64) * 255.0)
                        if cand.code.startswith("B6"):
                            off = baseline_offsets.get(name, 0.0)
                            pred_u8 = _clip255(pred_u8.astype(np.float64) + off)
                        m = measure(pred_u8, img.path, tmpdir,
                                    f"B_{cand.code}_{img.group}_{img.path.name}")
                        rows.append({"group": name, "image": img.path.name,
                                     "ps_b": img.brightness, "ps_c": img.contrast,
                                     "krita_b": kb, "krita_c": kc,
                                     "ratio": m["diff_ratio"], "max_delta": m["max_delta"],
                                     "mae": m["mae"], "bucket": bucket(m["diff_ratio"])})
                worst = max((r["ratio"] for r in rows), default=None)
                mean = float(np.mean([r["ratio"] for r in rows])) if rows else None
                verdict = bucket(worst) if worst is not None else "无法核验（无适用图）"
                out["axis_b"].append({"code": cand.code, "source": cand.source, "formula": cand.formula,
                                      "scope": cand.scope, "n": len(rows), "worst": worst,
                                      "mean": mean, "verdict": verdict,
                                      "skipped": sorted(set(skipped)), "rows": rows})
                print(f"  {cand.code}: 用图 {len(rows)} 张，最差差异比例 "
                      f"{'-' if worst is None else f'{worst*100:.2f}%'}，"
                      f"平均 {'-' if mean is None else f'{mean*100:.2f}%'} → {verdict}")
                if skipped:
                    print(f"    跳过的组：{sorted(set(skipped))}")

        # ---------------- C 轴：跨引擎基线差异（可达性证据） ----------------
        print("\n[C 轴] 跨引擎基线差异（Krita 算法复刻 b=0/c=1 vs PS 真值 b0_c0）")
        tex100 = load_texture_mask(100)
        for name, items in groups.items():
            base_img = baselines.get(name)
            if base_img is None:
                continue
            kmode = KRITA_MODE.get(items[0].mode)
            if kmode is None:
                out["axis_c"].append({"group": name, "ratio": None,
                                      "verdict": "无法核验（Krita 侧无对应模式）"})
                print(f"  {name}: 无法核验（Krita 侧无对应模式）")
                continue
            pred = compute_mask(tex100, False, 0.0, 1.0, kmode, items[0].depth)
            pred_u8 = _clip255(pred.astype(np.float64) * 255.0)
            m = measure(pred_u8, base_img.path, tmpdir, f"C_{name}_baseline")
            verdict = bucket(m["diff_ratio"])
            out["axis_c"].append({"group": name, "ratio": m["diff_ratio"],
                                  "max_delta": m["max_delta"], "mae": m["mae"],
                                  "verdict": verdict})
            print(f"  {name}: 差异比例 {m['diff_ratio']*100:.2f}%，最大差值 {m['max_delta']}，"
                  f"MAE {m['mae']:.2f} → {verdict}")

        # ---------------- 附：tests/baselines/ps 里的并行真值图 ----------------
        extra = []
        if PS_BASELINE_DIR.is_dir():
            for png in sorted(PS_BASELINE_DIR.rglob("*.png")):
                extra.append(str(png.relative_to(ROOT)))
        out["meta"]["ps_baselines_found"] = extra
        print(f"\n[附] tests/baselines/ps 下发现 {len(extra)} 张 PNG（并行任务产物）")
        if extra:
            print("  这些图缺少「同组 b0_c0 基线 + 模式/深度元数据」，不能直接进 A 轴；"
                  "B 轴也不适用（它们是 PS 真值，不是 Krita 渲染）。已如实记入报告，未纳入判定。")
            for item in extra[:10]:
                print(f"  - {item}")

        out["meta"].update({
            "texture": str(TEXTURE_PATH.relative_to(ROOT)),
            "images": len(images),
            "groups": len(groups),
            "tol": DEFAULT_TOL,
            "bucket_ok": BUCKET_OK,
            "bucket_partial": BUCKET_PARTIAL,
        })

        print("\n[汇总] A 轴判定：")
        for row in out["axis_a"]:
            print(f"  {row['code']}: {row['verdict']}（最差 {row['worst']*100:.2f}%）"
                  if row["worst"] is not None else f"  {row['code']}: {row['verdict']}")
        if not args.skip_b:
            print("[汇总] B 轴判定：")
            for row in out["axis_b"]:
                print(f"  {row['code']}: {row['verdict']}（最差 {row['worst']*100:.2f}%）"
                      if row["worst"] is not None else f"  {row['code']}: {row['verdict']}")

        if args.markdown:
            def pct(v):
                return "-" if v is None else f"{v * 100:.2f}%"

            mode_order2 = ["减去", "正片叠底", "变暗", "线性加深", "高度", "线性高度"]
            ps_mode_of_group = {k: v["mode"] for k, v in out["groups"].items()}
            ps_mode_to_cn = {v: k for k, v in PS_MODES.items()}

            print("\n===== MARKDOWN：A 轴候选表 =====")
            print("| 候选 | 来源 | 公式 | 适用模式 | 用图数 | 最差差异比例 | 判定 |")
            print("|---|---|---|---|---|---|---|")
            for row in out["axis_a"]:
                print(f"| {row['code']} | {row['source']} | {row['formula']} | {row['scope']} | "
                      f"{row['n']} | {pct(row['worst'])} | {row['verdict']} |")

            print("\n===== MARKDOWN：A 轴逐组最优（按全组最差图判定）=====")
            print("| 组 | 最优候选 | 该组最差差异比例 | 合格图数/总图数 | 判定 |")
            print("|---|---|---|---|---|")
            for g in sorted(out["axis_a_best_per_group"]):
                b = out["axis_a_best_per_group"][g]
                print(f"| {g} | {b['code']} | {pct(b['ratio'])} | {b['ok']}/{b['n']} | {b['bucket']} |")
            print("\n===== MARKDOWN：A 轴分模式最差差异比例 =====")
            print("| 候选 | " + " | ".join(mode_order2) + " |")
            print("|---|" + "---|" * len(mode_order2))
            for code, cells in out["axis_a_by_mode"].items():
                print(f"| {code} | " + " | ".join(pct(cells.get(m)) for m in mode_order2) + " |")

            if not args.skip_b:
                print("\n===== MARKDOWN：B 轴候选表 =====")
                print("| 候选 | 来源 | 公式 | 适用模式 | 用图数 | 最差差异比例 | 判定 |")
                print("|---|---|---|---|---|---|---|")
                for row in out["axis_b"]:
                    print(f"| {row['code']} | {row['source']} | {row['formula']} | {row['scope']} | "
                          f"{row['n']} | {pct(row['worst'])} | {row['verdict']} |")

                print("\n===== MARKDOWN：B 轴分模式最差差异比例 =====")
                print("| 候选 | " + " | ".join(mode_order2) + " |")
                print("|---|" + "---|" * len(mode_order2))
                for row in out["axis_b"]:
                    cells = {}
                    for r in row["rows"]:
                        cn = ps_mode_to_cn.get(ps_mode_of_group.get(r["group"], ""), r["group"])
                        cells[cn] = max(cells.get(cn, 0.0), r["ratio"])
                    print(f"| {row['code']} | " + " | ".join(pct(cells.get(m)) for m in mode_order2) + " |")

            print("\n===== MARKDOWN：C 轴跨引擎基线差异 =====")
            print("| 组 | 差异比例 | 最大差值 | MAE | 判定 |")
            print("|---|---|---|---|---|")
            for row in out["axis_c"]:
                if row.get("ratio") is None:
                    print(f"| {row['group']} | - | - | - | {row['verdict']} |")
                else:
                    print(f"| {row['group']} | {pct(row['ratio'])} | {row['max_delta']} | "
                          f"{row['mae']:.2f} | {row['verdict']} |")

        if args.json:
            Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n[输出] 完整结果已写入 {args.json}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
