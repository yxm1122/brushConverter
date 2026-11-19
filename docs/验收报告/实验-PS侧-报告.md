# 实验-PS 侧报告（PS 真值矩阵 A+B+参考图，29 张）

- 任务：PS 侧实验施工（为 Krita vs PS 纹理掩码对比提供真值图）
- 仓库：`<REPO>`
- 环境：Windows + MSYS bash；Python `<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe`（3.12.13）；
  Photoshop 2025 / **26.9.0.15**（`<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe`）
- 结论时间：2026-11-19
- 关键事实：**29/29 张一次跑通（56.6 秒），全部回读校验通过，运行结束无 Photoshop 残留**

---

## 一、结论

1. 矩阵 A（24 张：mode ∈ {multiply, linearHeight} × depth ∈ {10,60,100} × scale ∈ {50,100,150,200}）、
   矩阵 B（4 张：(b,c) ∈ {(-50,0),(50,0),(0,50),(0,-50)}，固定 multiply/d100/s100）、
   无纹理参考图（1 张）共 **29 张**已全部自动出图到 `tests/baselines/exp/ps/`。
2. 每张图出完后都用 PS 自己的 JSX 读回 **图案名 / useTexture / invertTexture / scale / depth /
   brightness / contrast / blendMode**，与写入期望值逐字段比对：**29/29 通过**
   （JSX 侧 verify=true，Python 侧再独立解析回读串复核，双保险）。
3. 参考图 `exp__ps__ref__notex.png` 实测 **Alpha 全 255（min=255、max=255、非零占比 1.0000）**，
   满足「参考图处处 255」的几何前提（Krita 侧同判据）。
4. 纹理相位证据：矩阵 A 的 `multiply/d100/s100/b0_c0` 与
   `tools/texture_mask.py` 用 64×64 纹理按 **锚点 (0,0) + 原始尺寸平铺** 到 100×100 再
   `invert=True` 算出的掩码 **逐像素完全一致：差异 0/10000，max_abs=0**；
   换成 `invert=False` 则 10000/10000 像素不同（max_abs=255），证明 **invertTexture=true 是必需的且已生效**。
5. 覆盖率：27 张 Alpha 非零占比 ≥0.97；**2 张 <0.97 已标注保留**（0.7299 / 0.6623），
   原因是 multiply/d100 下亮度/对比度把纹理曲线推出 0..255，反相后一端落到 Alpha=0（PS 真实行为），
   不是缺角或未覆盖（下方有阈值数据证明）。

---

## 二、命令与耗时

### 2.1 出图命令（唯一入口）

```bash
cd /d/PycharmProjects/brushConverter
"<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/ps_probe.py --exp-batch
```

关键输出（原文；中文因控制台代码页显示为乱码，含义见括号）：

```text
[实验] 矩阵 A 24 张 + 矩阵 B 4 张 + 参考图 1 张 = 29 张；纹理 测试用纹理.png sha256=16dc1656c7d53cc4…；启动 Photoshop …
[实验] 出图完成：PS 侧成功 29/29 张，耗时 56.6s，Photoshop 残留=无
[实验] 清单：tests\baselines\exp\ps\manifest.json（29 条）
[实验][警告] Alpha 非零占比 <0.97 的图 2 张：[{'file': 'exp__ps__multiply__d100_s100_b50_c0.png', 'nonzero_ratio': 0.7299}, {'file': 'exp__ps__multiply__d100_s100_b0_c50.png', 'nonzero_ratio': 0.6623}]
```

### 2.2 校验命令

```bash
"<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/ps_probe.py --exp-verify
```

```text
[实验清单] 29 条；期望 29 条
[覆盖率] 低于 0.97 的图：['exp__ps__multiply__d100_s100_b50_c0.png', 'exp__ps__multiply__d100_s100_b0_c50.png']
[通过] 实验目录：29 张齐全、命名合规、尺寸/RGBA/sha256/回读/覆盖率标注全部一致
```

### 2.3 运行前后自检

| 检查 | 命令 | 结果 |
|---|---|---|
| 开跑前无其它 PS 实例 | 脚本内 `photoshop_running()` 预检（命中即 abort，且**不会强杀**别人启动的 PS） | 通过（否则不会开跑） |
| 运行结束无 PS 残留 | 脚本内 `taskkill` 后复查 + 手工 `tasklist \| grep -i photoshop \| wc -l` | `PS_RUNNING_COUNT=0` |
| 产物数量 | `ls tests/baselines/exp/ps/*.png \| wc -l` | `PNG_COUNT=29` |

耗时构成：Photoshop 冷启动约 10–15 s，29 张建画布+写参数+单笔尖+导出约 40 s；
单张平均约 1.7 s（含首次启动）。本次批量 **56.6 s**。

---

## 三、口径（与 Krita 侧对齐的部分）

| 项 | 取值 | 证据 |
|---|---|---|
| 笔刷 | `tests/定量测试专用.abr` 里唯一那支计算笔刷「定量测试用」，**不换笔、不改笔刷几何** | ABR 解析：preset_count=1、computed=True、diameter=200、hardness=100、spacing=10、roundness=100；PS 回读 `diameter=200 hardness=100 spacing=1000 roundness=100` |
| 纹理 | `research/测试用纹理.png` **原始字节**（64×64 RGBA，13331 B，md5=5e748763e7af2f1c5a99a27b3a3aee38，sha256=16dc1656c7d53cc4…） | PS 侧 `app.open(该 PNG)` + `definePattern` 定义图案，按名 `bc_exp_texture` 引用；清单 `texture` 字段记录 name/path/sha256/md5/size |
| 反相 | **invertTexture=true**（显式写入） | ABR 源纹理 `bp.texture.invert=True`；JSX 每张写 `putBoolean(sid("invertTexture"), true)` 并回读；相位比对只有 invert=True 才逐像素一致 |
| 画布 | 100×100 @72ppi、RGB、透明背景，导出 PNG | 清单 `canvas`；29 张尺寸均为 100×100 |
| 落笔 | 路径 (50,50) → (50.4,50.4)（0.4 px），`strokePath(ToolType.BRUSH)` 单笔尖 | JSX `paintOne()`；直径 200px 硬笔尖圆心≈(50.2,50.2)，画布最远角 70.7px < 半径 100px |
| 前景色 | 纯黑 (0,0,0) | JSX 设置并记录 |
| 其它工具开关 | opacity=100、flow=100、useTipDynamics=false、useScatter=false、smoothing=false | 清单 `brush.jsx_flags_set` |
| 参考图 | 仅 `useTexture=false`，笔刷/画布/落笔几何全部相同 | 回读 `useTexture=false`；Alpha 全 255 |

### 3.1 纹理图案证据（lead 要求：图案名 + 来源文件 + 大小/md5）

- 图案名：`bc_exp_texture`
- 来源：`research/测试用纹理.png`（只读，未复制、未重采样、未改字节）
- 尺寸/字节：64×64 RGBA / 13331 bytes
- md5：`5e748763e7af2f1c5a99a27b3a3aee38`（与 Krita 侧 preset 内嵌纹理 md5 相同）
- sha256：`16dc1656c7d53cc4d5fa9e0782fd68ad6e5b000d9c93a586c261bf1df0e01362`
- 清单字段：`texture.name / texture.path / texture.sha256 / texture.md5 / texture.size / texture.invert_texture`

---

## 四、回读证据（PS 自己读回的值 = 期望值）

回读在 **PS 内部**完成（`readBack()` → `done.json` + `batch.log`），字段顺序固定：
`useTexture;invert;pattern;scale;depth;brightness;contrast;blend`。

### 4.1 至少 3 张回读原文（batch.log 原样）

```text
READBACK exp__ps__multiply__d10_s50_b0_c0.png | useTexture=true;invert=true;pattern=bc_exp_texture;scale=50;depth=10;brightness=0;contrast=0;blend=multiply
READBACK exp__ps__multiply__d100_s150_b0_c0.png | useTexture=true;invert=true;pattern=bc_exp_texture;scale=150;depth=100;brightness=0;contrast=0;blend=multiply
READBACK exp__ps__linearHeight__d100_s100_b0_c0.png | useTexture=true;invert=true;pattern=bc_exp_texture;scale=100;depth=100;brightness=0;contrast=0;blend=linearHeight
READBACK exp__ps__multiply__d100_s100_b50_c0.png | useTexture=true;invert=true;pattern=bc_exp_texture;scale=100;depth=100;brightness=50;contrast=0;blend=multiply
READBACK exp__ps__multiply__d100_s100_b0_c-50.png | useTexture=true;invert=true;pattern=bc_exp_texture;scale=100;depth=100;brightness=0;contrast=-50;blend=multiply
READBACK exp__ps__ref__notex.png | useTexture=false;invert=true;pattern=bc_exp_texture;scale=100;depth=ABSENT;brightness=0;contrast=-50;blend=ABSENT
```

说明：参考图关闭纹理后 PS 仍保留上一次的 surface 值（invert/scale/brightness/contrast 是上一条任务的残留），
但 `useTexture=false` 时纹理键不参与渲染，参考图实测 Alpha 全 255 也证明纹理确实没生效；
`depth/blend=ABSENT` 表示这两个键在关闭纹理后不存在（不是写入失败）。

### 4.2 笔刷加载与选中证据

```text
LOAD_ABR OK <REPO>/tests/定量测试专用.abr
SELECT_BRUSH OK 定量测试用
BRUSH_READBACK diameter=200 hardness=100 spacing=1000 roundness=100
pattern defined: bc_exp_texture
```

回读原文行数核对：`grep -c '^OK exp__ps__\|^READBACK exp__ps__' batch.log` → **58 行 = 29 条 OK + 29 条 READBACK**，与 29 张图一一对应。

### 4.3 回读校验机制（双重复核）

1. **JSX 侧**：每张写完参数后立即 `readBack()`，逐字段与期望比对，`verify` 布尔写进 `done.json`；
2. **Python 侧**：`exp_check_readback()` 重新解析回读串，再比一次（图案名、invert、scale、depth、
   brightness、contrast、blendMode 全等），28 张纹理图全部 `readback_ok=true`，参考图按
   `useTexture=false` 判据通过。

---

## 五、纹理相位与掩码一致性（与 texture_mask.py 比对）

对象：`exp__ps__multiply__d100_s100_b0_c0.png`（矩阵 A 中 scale=100 的基准点）。

方法与结果（脚本：`tests/baselines/exp/ps/_work/phase_check.py`，原始 JSON：
`tests/baselines/exp/ps/_work/phase_check.json`）：

1. 把 `research/测试用纹理.png` 按 64×64 **平铺**到 100×100（锚点 (0,0)、原始像素尺寸）；
2. 用 `tools/texture_mask.py` 的 `texture_mask_from_rgba(tiled, invert=True)` 算掩码 ×255 四舍五入；
3. 与 PS 输出 Alpha 逐像素比。

```text
invert=True  : diff_pixels=0 / 10000，diff_ratio=0.000000，max_abs=0，mean_abs=0.0
invert=False : diff_pixels=10000 / 10000，diff_ratio=1.000000，max_abs=255，mean_abs=122.97
```

结论：**PS 的纹理锚点就是画布 (0,0)、scale=100% 时 64×64 图案按原尺寸平铺，且 invertTexture=true**；
相位与 Krita 的 (0,0) 锚点一致，PS 纹理图可以直接当掩码用。
（该比对只覆盖 scale=100%；150/200 的插值/相位只保证是 PS 原样输出，未与 Krita 逐像素验证。）

---

## 六、29 张统计（Alpha 非零占比 / min / max / sha256）

完整字段（含每张的 `ps_scale_pct / ps_depth / ps_brightness / ps_contrast / ps_mode /
invert_texture / pattern_name / alpha / sha256 / readback_raw / postprocess`）见
`tests/baselines/exp/ps/manifest.json`（44270 B，29 条）。下表为摘要：

| # | 文件 | 模式 | d | s | b | c | Alpha非零占比 | min | max | sha256(前12) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | exp__ps__multiply__d10_s50_b0_c0.png | multiply | 10 | 50 | 0 | 0 | 1.0000 | 231 | 252 | e06ffc80074a |
| 2 | exp__ps__multiply__d10_s100_b0_c0.png | multiply | 10 | 100 | 0 | 0 | 1.0000 | 229 | 255 | 4458111e5353 |
| 3 | exp__ps__multiply__d10_s150_b0_c0.png | multiply | 10 | 150 | 0 | 0 | 1.0000 | 229 | 255 | 3c0876f25962 |
| 4 | exp__ps__multiply__d10_s200_b0_c0.png | multiply | 10 | 200 | 0 | 0 | 1.0000 | 229 | 255 | c2fd0b8d00b5 |
| 5 | exp__ps__multiply__d60_s50_b0_c0.png | multiply | 60 | 50 | 0 | 0 | 1.0000 | 116 | 235 | 46044c657bb2 |
| 6 | exp__ps__multiply__d60_s100_b0_c0.png | multiply | 60 | 100 | 0 | 0 | 1.0000 | 102 | 255 | d69542fda9db |
| 7 | exp__ps__multiply__d60_s150_b0_c0.png | multiply | 60 | 150 | 0 | 0 | 1.0000 | 102 | 255 | 6a018dd24923 |
| 8 | exp__ps__multiply__d60_s200_b0_c0.png | multiply | 60 | 200 | 0 | 0 | 1.0000 | 102 | 255 | 890cd43a8358 |
| 9 | exp__ps__multiply__d100_s50_b0_c0.png | multiply | 100 | 50 | 0 | 0 | 1.0000 | 23 | 222 | 4e4f93050f32 |
| 10 | exp__ps__multiply__d100_s100_b0_c0.png | multiply | 100 | 100 | 0 | 0 | 0.9897 | 0 | 255 | e4954bc47483 |
| 11 | exp__ps__multiply__d100_s150_b0_c0.png | multiply | 100 | 150 | 0 | 0 | 0.9989 | 0 | 255 | 4b9dddad3a4b |
| 12 | exp__ps__multiply__d100_s200_b0_c0.png | multiply | 100 | 200 | 0 | 0 | 0.9967 | 0 | 255 | 648a435b3f26 |
| 13 | exp__ps__linearHeight__d10_s50_b0_c0.png | linearHeight | 10 | 50 | 0 | 0 | 1.0000 | 90 | 255 | 3945abff031d |
| 14 | exp__ps__linearHeight__d10_s100_b0_c0.png | linearHeight | 10 | 100 | 0 | 0 | 1.0000 | 57 | 255 | ebb2ff708b40 |
| 15 | exp__ps__linearHeight__d10_s150_b0_c0.png | linearHeight | 10 | 150 | 0 | 0 | 1.0000 | 57 | 255 | 5a2f33a5c24b |
| 16 | exp__ps__linearHeight__d10_s200_b0_c0.png | linearHeight | 10 | 200 | 0 | 0 | 1.0000 | 57 | 255 | 3d1023d85043 |
| 17 | exp__ps__linearHeight__d60_s50_b0_c0.png | linearHeight | 60 | 50 | 0 | 0 | 1.0000 | 255 | 255 | 08e3b5cb2f45 |
| 18 | exp__ps__linearHeight__d60_s100_b0_c0.png | linearHeight | 60 | 100 | 0 | 0 | 1.0000 | 255 | 255 | 08e3b5cb2f45 |
| 19 | exp__ps__linearHeight__d60_s150_b0_c0.png | linearHeight | 60 | 150 | 0 | 0 | 1.0000 | 255 | 255 | 08e3b5cb2f45 |
| 20 | exp__ps__linearHeight__d60_s200_b0_c0.png | linearHeight | 60 | 200 | 0 | 0 | 1.0000 | 255 | 255 | 08e3b5cb2f45 |
| 21 | exp__ps__linearHeight__d100_s50_b0_c0.png | linearHeight | 100 | 50 | 0 | 0 | 1.0000 | 255 | 255 | 08e3b5cb2f45 |
| 22 | exp__ps__linearHeight__d100_s100_b0_c0.png | linearHeight | 100 | 100 | 0 | 0 | 1.0000 | 255 | 255 | 08e3b5cb2f45 |
| 23 | exp__ps__linearHeight__d100_s150_b0_c0.png | linearHeight | 100 | 150 | 0 | 0 | 1.0000 | 255 | 255 | 08e3b5cb2f45 |
| 24 | exp__ps__linearHeight__d100_s200_b0_c0.png | linearHeight | 100 | 200 | 0 | 0 | 1.0000 | 255 | 255 | 08e3b5cb2f45 |
| 25 | exp__ps__multiply__d100_s100_b-50_c0.png | multiply | 100 | 100 | -50 | 0 | 1.0000 | 50 | 255 | 70391724102d |
| 26 | exp__ps__multiply__d100_s100_b50_c0.png | multiply | 100 | 100 | 50 | 0 | **0.7299** | 0 | 205 | 7cfa94c1f708 |
| 27 | exp__ps__multiply__d100_s100_b0_c50.png | multiply | 100 | 100 | 0 | 50 | **0.6623** | 0 | 255 | fbc968d4f371 |
| 28 | exp__ps__multiply__d100_s100_b0_c-50.png | multiply | 100 | 100 | 0 | -50 | 1.0000 | 64 | 192 | 83b424afa5e6 |
| 29 | exp__ps__ref__notex.png | none | — | — | — | — | 1.0000 | 255 | 255 | 08e3b5cb2f45 |

### 6.1 覆盖率 <0.97 的两张（已标注保留，原因如下）

| 文件 | 非零占比 | Alpha=0 像素 | 这些像素对应纹理值 | 非零像素对应纹理值上限 |
|---|---|---|---|---|
| `exp__ps__multiply__d100_s100_b50_c0.png` | 0.7299（2701/10000） | 2701 | 纹理灰度 **205..255** | 204 |
| `exp__ps__multiply__d100_s100_b0_c50.png` | 0.6623（3377/10000） | 3377 | 纹理灰度 **191..255** | 190 |

解释（属 PS 真实行为，不是缺角/未覆盖）：
- multiply 且 depth=100 时，PS 输出 Alpha ≈ 反相后的纹理值（见第五节：invert=True 时与掩码逐像素一致）；
- 亮度 +50 / 对比度 +50 把纹理曲线推向后段，反相后一部分像素落到 **0 端** → 这些像素 Alpha=0（全透明）；
- 数据呈**干净阈值**（0 像素对应纹理 ≥205 / ≥191，非零像素 ≤204 / ≤190），说明这是**值域截断**而不是
  「笔尖没盖住画布」；参考图与其它 26 张的覆盖率也证明几何是全画布覆盖。
- 下游按 Alpha 比对时，这两张图的 0 值本身也是有效的真值（PS 的截断行为），**不要删**。

另注：`multiply d100 s50/s150/s200` 的 min 也是 0（少量裁剪像素），但占比仍 ≥0.9967，未触发告警。

### 6.2 饱和图（8 张，必须过滤）

`linearHeight` 的 d60 与 d100 共 8 张，Alpha 全部 = 255（unique=1），
且这 8 张与参考图 **sha256 完全相同**（`08e3b5cb2f45…`，即「全不透明黑图」）。
这与任务 05 的结论一致（高度/线性高度 d≥60 饱和），属 PS 真实行为；
拟合/对比前应按 `alpha.unique==1` 过滤，否则会得到无信息量的样本。

---

## 七、几何一致性说明（笔尖直径 / 路径描边 / 画布）

| 项 | 值 | 说明 |
|---|---|---|
| 笔刷来源 | `tests/定量测试专用.abr` 的「定量测试用」（计算笔刷） | `app.load(File)` 加载 → `putName(cid("Brsh"), "定量测试用")` + `slct` 选中；**未安装进 PS 预设目录，只作用于本次会话** |
| 笔尖直径 | **200 px**（PS 回读 `diameter=200`） | ABR 解析 Dmtr=200 一致 |
| 硬度 / 圆度 | 100% / 100% | PS 回读 `hardness=100 roundness=100` |
| 间距 | ABR 解析 spacing=10（Krita 侧=1000%）；PS 回读 `spacing=1000` | 两者指同一间距口径；0.4px 路径在 200px 笔刷下只落 **1 个 dab**，间距不改变结果 |
| 画布 | 100×100 @72ppi，透明背景 | 29 张尺寸均 100×100 |
| 落笔方式 | 路径 (50,50) → (50.4,50.4)，0.4 px，`strokePath(ToolType.BRUSH)` | 单笔尖：0.4 px ≪ 直径 200 px |
| 实际 dab 圆心 | ≈ (50.2, 50.2) | 到画布最远角 (99,99) 距离 ≈ 70.7 px < 半径 100 px，**整个画布都在硬笔尖内部** |
| 参考图验证 | `exp__ps__ref__notex.png` Alpha 全 255（min=max=255，占比 1.0） | 与 lead 的判据一致 |
| 纹理图验证 | 纹理图 Alpha 与「反相纹理平铺」逐像素一致（scale=100） | 说明笔尖内部 alpha 均匀、纹理即掩码 |

因此 PS 侧 29 张图的几何完全一致，唯一变量是纹理参数；Krita 侧只要求参考图处处 255，PS 已满足。

---

## 八、扩展点（本次对 JSX / 驱动脚本的改动）

### 8.1 `tools/ps_export.jsx`（向后兼容）

1. **工作目录参数化**：`BRUSHCONVERTER_PS_WORK` 环境变量指定 job/done/log 目录；
   未设置时回落到任务 05 老路径 `tests/baselines/ps/_work`（老调用不受影响）。
2. **每图可覆盖纹理参数**：新增 `use_texture`（默认 true，false=参考图）、
   `texture_scale`（按图覆盖顶层 scale）、`invert_texture`（默认 false）。
3. **ABR 加载与选笔**：新增顶层 `abr_path` / `brush_name`，用
   `app.load(new File(abr))` + `putName(cid("Brsh"), name)` + `slct` 按名选中；
   不写 computedBrush，**不改笔刷几何**。
4. **回读**：每张读回 `useTexture / invertTexture / texture(图案名) / textureScale / textureDepth /
   textureBrightness / textureContrast / textureBlendMode`，写进 `done.json` 的
   `readback/expected/verify` 并在 `batch.log` 留 `READBACK` 原文；done.json 顶层新增
   `ps_version` 与 `brush`（abr_loaded/brush_selected/diameter/hardness/spacing/roundness）。

### 8.2 `tools/ps_probe.py`（新增实验通路，老通路未改）

- 新增 `--exp-batch`（29 张出图 + 写清单）与 `--exp-verify`（校验实验目录/清单）；
- 新增常量：`EXP_DIR/WORK/MANIFEST/JOB/DONE/BATCH_LOG`、`EXP_TEXTURE_PATH`、`EXP_ABR_PATH`、
  `EXP_BRUSH_NAME`、`EXP_INVERT_TEXTURE`、矩阵 A/B 参数、`EXP_COVERAGE_MIN=0.97`；
- 清单 `tests/baselines/exp/ps/manifest.json`：每条含
  `ps_mode/ps_depth/ps_scale_pct/ps_brightness/ps_contrast/invert_texture/pattern_name/
  alpha_nonzero_ratio/alpha{min,max,mean,unique}/sha256/readback_* /postprocess`，
  顶层含 `brush{abr_path,brush_name,ps_readback}`、`geometry`、`texture{sha256,md5,size}`、`run`；
- 参考图后处理：PS 对**全不透明**图会导出 **RGB**（无 Alpha），Python 侧补 `Alpha=255`
  并逐条登记 `postprocess`（只补通道，RGB/尺寸不变），以满足「RGBA PNG」口径。

> 说明：任务 05 的 `--batch` 路径代码未改（只新增 exp 段），但**没有重跑 `--batch`**，
> 因为那会覆盖 `tests/baselines/ps/`（本任务禁写）。

---

## 九、这组图能不能代表 PS 真值（可信度边界）

**可以代表的部分**：PS 2025 单笔尖下「纹理图案 → 亮度/对比度 → 反相 → 混合模式 → 输出 Alpha」这一层，
因为参数写入有 PS 回读逐字段确认、纹理相位与独立实现（texture_mask.py）逐像素吻合、
几何（200px 硬笔尖 + 单 dab）能保证纹理图 = 掩码。

**不能代表 / 引用前必须注意**：

1. **版本锁**：只对 Photoshop 2025 / 26.9.0.15 有效，换版本必须整批重出。
2. **单笔尖、无笔划搭接**：没有压力、没有动态、没有 dab 重叠；不能代表真实笔划手感（任务 14 的范围）。
3. **只验证了 scale=100% 的相位**：150/200 的输出是 PS 原样，未与 Krita 逐像素对齐验证；
   若 Krita 的缩放语义与 PS 不同，150/200 两组可能不可直接比。
4. **invertTexture=true 是 PS 侧口径**：Krita preset 的 `Texture/Pattern/Invert` 必须也是 true 才可比
   （Krita 侧由另一位代理负责，本报告只证明 PS 侧）。
5. **饱和图**：linearHeight d60/d100 共 8 张全 255、与参考图同 sha，无信息量，拟合前必须按
   `alpha.unique==1` 过滤。
6. **2 张覆盖 <0.97**：是 multiply 值域截断的真实结果，保留；下游若用「非零占比」做筛选会被误伤。
7. **RGB 未清零**：本批保留 PS 原始导出（透明像素 RGB 为白），只保证 Alpha 是真值；
   与任务 05「RGB 清零」口径不同，若下游用 RGB 需注意。
8. **参考图 Alpha 由 Python 补齐**：PS 不写全不透明图的 Alpha 通道，参考图是「PS 内容 + Python 补
   Alpha=255」，文件字节不是纯 PS 导出（已登记 postprocess）。
9. **纹理图案来自文件原始字节**，但 PS definePattern 会按会话定义；图案名 `bc_exp_texture`
   只在本次会话有效，不写 PS 安装目录。

---

## 十、决策记录（lead 代为决策）

| 决策点 | 选项 | 选择 | 理由 |
|---|---|---|---|
| 笔刷是否按 ABR 选笔 | 沿用任务 05 的 300px 手工参数 / 加载 ABR 单支 | **加载 ABR 单支，且不写 computedBrush** | lead 口径要求与 Krita 同一支笔；PS 回读 diameter=200 与 ABR 解析一致 |
| 参考图 Alpha | 改笔刷/裁 1 像素制造透明 / Python 补 Alpha | **Python 补 Alpha=255** | 不破坏几何；PS 只是「全不透明图不写 Alpha」，补通道不改变像素 |
| 覆盖 <0.97 的两张 | 重跑/删除/保留标注 | **保留并标注** | lead 明确「先别删，标注后保留」；阈值数据证明是值域截断 |
| 饱和 8 张 | 删除 / 保留 | **保留并在报告+清单标注** | 是 PS 真实行为，可作边界样本；拟合侧按 unique==1 过滤 |
| 回读校验 | 只信 JSX verify / JSX+Python 双复核 | **双重复核** | 防止 JSX 与期望串同时写错 |
| plot 保存方式 | saveAs / SaveForWeb | **saveAs** | 尝试 SaveForWeb 时 PS 未返回（等待 200s 超时），放弃该路线；改为 saveAs + Python 补 Alpha |

---

## 十一、产物与改动清单

**新建（产物）**

- `tests/baselines/exp/ps/exp__ps__*.png`（29 张：矩阵 A 24 + 矩阵 B 4 + 参考图 1）
- `tests/baselines/exp/ps/manifest.json`（29 条，44270 B，sha256=1e323c08a7eb16cb1b6072208c4026f2b7358f34c98532c6b2eb1bc2e2ea8e38）
- `tests/baselines/exp/ps/_work/`：`job.json`、`done.json`、`batch.log`（29 条 OK+READBACK 原文）、
  `phase_check.json`、`report_table.md`、`probe_brush.jsx`/`probe_job.json`/`probe.log`/`probe.json`（ABR 选笔与 invert 预验证）、
  `probe_textured.png`/`probe_notex.png`（相位分析用样张）、以及若干分析与检查小脚本

**修改**

- `tools/ps_export.jsx`（扩展：WORK 参数化、每图 use_texture/texture_scale/invert_texture、ABR 加载选笔、回读与 verify）
- `tools/ps_probe.py`（新增 exp 通路：`--exp-batch` / `--exp-verify`、29 张矩阵、清单、参考图 RGBA 后处理；老 `--batch/--verify/--com-check` 未改）

**新建（文档）**

- `docs/验收报告/实验-PS侧-报告.md`（本文件）

改动范围自检（只读 `git status --short`）：
```text
 M tools/ps_export.jsx
 M tools/ps_probe.py
?? "docs/验收报告/实验-PS侧-报告.md"
?? tests/baselines/exp/
```
即 `tests/baselines/ps/` 没有任何改动（无输出行）。

**未触碰**：`tests/baselines/ps/`、`tests/baselines/krita/`、`tests/baselines/feel/`、
`research/`、`src/`、`docs/优化提示词/`、`docs/工作流程/`；无任何 git 写操作。

---

## 十二、耗时与后续

- PS 侧 29 张全量重跑：**约 1.0–1.5 分钟**（本次 56.6 s；PS 冷启动占 10–15 s，单张约 1.7 s）。
- 若后续要扩矩阵（例如再加 subtract/height 模式或 b/c 网格），只需改
  `tools/ps_probe.py` 的 `exp_jobs_all()` 再跑 `--exp-batch`，命名/清单/回读会自动跟随；
  每增加 20 张约 +35 s。
