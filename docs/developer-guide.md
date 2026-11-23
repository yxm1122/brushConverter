# brushConverter 项目结构与原理

> 面向开发者：解释这个项目如何把 Photoshop 笔刷（`.abr`）转换成 Krita 笔刷（`.kpp` / `.bundle`），以及开发过程中踩过的坑和关键结论。
>
> 参数映射的完整逐字段清单见 [`parameter-mapping.md`](parameter-mapping.md)。

## 0. 文档索引（先看这里）

> 仓库里文档不少，按「你现在要做什么」查更快。**内部规划文档（任务书/铁律/台账）不在本仓库**（仅维护者本地保留），不要在这里找。

### 0.1 按目的查

| 我想… | 看这份 |
|---|---|
| 知道逐字段怎么映射（尺寸/间距/角度/散布/纹理…） | [`parameter-mapping.md`](parameter-mapping.md)（**映射唯一权威**，含纹理 §7/§7.1，纹理模式逐模式结论见 §7.2） |
| 查纹理混合模式结论（PS UI 仅 10 项可选；另 37 内部值不计限制） | [`数据/mode-table.json`](数据/mode-table.json)（**模式结论唯一真相**，47 行完整枚举归档，含 evidence_grade/证据强度/可区分/塌缩类） |
| 查某个参数键的结论、来源与承载字段 | [`参数矩阵.json`](参数矩阵.json)（**参数结论唯一真相**，137 行 × 8 栏，含 `ui_visibility` / `ui_control`；渲染版 [`参数矩阵.md`](参数矩阵.md)，由脚本生成，禁止手改） |
| 判断某个映射为什么这么定、有没有实测依据 | [`parameter-mapping.md`](parameter-mapping.md) + 下方「研究结论」 |
| 跑验证、看懂「通过/失败/跳过」口径 | [`验证体系使用说明.md`](验证体系使用说明.md) |
| 加一张基准图 / 搞清楚命名与登记规则 | [`基准库命名规范.md`](基准库命名规范.md) |
| 做两张图的差异比对与「哪支笔/哪个参数」定位 | [`比对与定位说明.md`](比对与定位说明.md) |
| 做手感（压感宽度/不透明度曲线）比对 | [`手感比对说明.md`](手感比对说明.md) |
| 一条命令跑完三层验收并归档 | [`全量验收说明.md`](全量验收说明.md) |
| 用 numpy 复刻 Krita 纹理掩码算法 | [`纹理掩码复刻说明.md`](纹理掩码复刻说明.md) |
| 让 Krita 自动出图（为什么不能用 kritarunner） | [`Krita无头渲染说明.md`](Krita无头渲染说明.md) |
| 批量渲染并把产物登记进基准库 | [`批量渲染操作说明.md`](批量渲染操作说明.md) |
| 让 Photoshop 自动产出真值图（JSX/COM） | [`PS真值自动化说明.md`](PS真值自动化说明.md) |
| 发布前打包、看体积与已知限制 | [`../releases/RELEASE_NOTES.md`](../releases/RELEASE_NOTES.md) + 本文 §7.4 |
| 了解 v1.2.x 文案/文档对齐的处置 | [`文档对齐报告.md`](归档/文档对齐报告.md) |

### 0.2 研究结论（为什么这么映射，按主题）

| 主题 | 文档 | 一句话结论 |
|---|---|---|
| 纹理亮度/对比度·现成成果调研 | [`研究报告/纹理亮度对比度-调研.md`](研究报告/纹理亮度对比度-调研.md) | 未找到可直接采用的公式（0/148 达 ≤2%） |
| 纹理公式·黑盒拟合 | [`研究报告/lut-拟合.md`](研究报告/lut-拟合.md) | 1D LUT 0.00% / 参数化 0.01%（旧经验公式 73.46%） |
| 纹理公式·逆向（静态/动态/参考实现） | [`研究报告/ps-逆向-静态.md`](研究报告/ps-逆向-静态.md)、[`ps-逆向-动态.md`](研究报告/ps-逆向-动态.md)、[`ps-纹理公式-参考实现.md`](研究报告/ps-纹理公式-参考实现.md) | 参数语义被动态证实（int32、无 clamp、depth clamp 100）；像素公式来自数据拟合 |
| Krita 无头渲染可行性 | [`研究报告/krita-runner-可行性.md`](研究报告/krita-runner-可行性.md) | kritarunner 无头落笔不可用；改真实 GUI + 启动插件（路线 A） |
| **缩放/深度是否准（v1.2.1 依据）** | [`研究报告/缩放深度-归因实验.md`](研究报告/缩放深度-归因实验.md) | multiply 深度 0.00% 准；高度系差 2q（已修）；缩放 50% 准、150/200% 属重采样差异 |
| 历史方案（已被取代，勿照抄，存于 `归档/`） | [`mapping-proposal.md`](归档/mapping-proposal.md)、[`texture-mapping-proposal.md`](归档/texture-mapping-proposal.md)、[`feasibility.md`](归档/feasibility.md) | 仅作演进记录 |

### 0.3 验收证据（证据归档，非上手必读；编号对应内部任务书）

- [`验收报告/`](验收报告/)：逐份清单见 [`验收报告/INDEX.md`](验收报告/INDEX.md)（脚本生成，禁止手改）；`01`–`116` 为各任务自检报告（含 `06A/07A/11A/11B/18A/30A` 等拆分），`90`–`92` 为发布验收。
- [`研究报告/`](研究报告/)：逐份清单见 [`研究报告/INDEX.md`](研究报告/INDEX.md)（脚本生成）；按主题导航见上方 §0.2。
- `实验-PS侧-报告.md` / `实验-Krita侧-报告.md` / `实验-归因-报告.md`：v1.2.0 之后追加的「缩放/深度 2×2 归因实验」与 v1.2.1 渲染回归。
- 全量验收归档在 `验收报告/归档/verify-<时间戳>.md`（`tools/verify_all.py --archive` 生成）。
- 索引再生成：`python tools/render_archive_index.py`（验收）/ `--dir docs/研究报告 --md docs/研究报告/INDEX.md`（研究）；`--check` 校验同步。

### 0.4 常见接手路径

1. **第一次接触项目**：`../README.md` → 本文 §2（结构）→ [`parameter-mapping.md`](parameter-mapping.md) → [`验证体系使用说明.md`](验证体系使用说明.md)。
2. **改映射**：本文 §4/§5 + `parameter-mapping.md` + `tests/golden/params_expected.json`（**先改期望值表再改代码**，否则改坏了也测不出来）。
3. **改纹理**：本文 §5.3 与 §7.5/§7.6 + [`纹理掩码复刻说明.md`](纹理掩码复刻说明.md) + `研究报告/缩放深度-归因实验.md`（**高度系强度有 ×1.2 增益、资源名必须走内容哈希**，别改回去）。
4. **加验证**：[`验证体系使用说明.md`](验证体系使用说明.md) + [`基准库命名规范.md`](基准库命名规范.md) + [`全量验收说明.md`](全量验收说明.md)。
5. **发版**：本文 §7.4 + [`全量验收说明.md`](全量验收说明.md) + `../releases/RELEASE_NOTES.md`；跑 `python tools/verify_all.py --archive` 与 `python -m pytest -q` 必须全绿。

### 0.5 数据文件索引（v1.3.0 定稿）

这些 JSON 是「结论的唯一真相」，文档和代码都应以它们为准；改动后要重跑对应的校验/渲染脚本。

| 文件 | 用途 | 生成/校验 |
|---|---|---|
| [`参数矩阵.json`](参数矩阵.json) | 137 行参数键结论（8 栏：semantics / range / sources / krita_target / verdict / ui_visibility / ui_control） | 手改 JSON 后跑 `python tools/render_matrix_md.py`，再跑 `python tools/check_matrix.py`（退出码 0 才算过） |
| [`参数矩阵.md`](参数矩阵.md) | 矩阵的渲染版 + 模式总表节 | 只由 `tools/render_matrix_md.py` 生成，**不许手改**（check_matrix 的 C9 会红） |
| [`数据/ps_keys.json`](数据/ps_keys.json) | 逆向得到的 PS 参数键全集（125 条） | 任务 21 产出；check_matrix 的 C6a 依赖 |
| [`数据/abr_keys.json`](数据/abr_keys.json) | 真实 .abr 样本里实测出现的键（91 条） | 任务 22 产出；check_matrix 的 C6b 依赖 |
| [`数据/candidates.json`](数据/candidates.json) | 候选映射清单（跨模式候选、判据与置信度） | 任务 29 产出；模式判定时查 |
| [`数据/mode-table.json`](数据/mode-table.json) | **纹理混合模式逐模式结论**（唯一真相；PS UI 仅 10 项可选，47 行为完整枚举归档） | `python tools/render_mode_table.py --build`；`--check` 校验与 `参数矩阵.md` 同步 |
| [`数据/mode-batch.json`](数据/mode-batch.json) | 三批（A/B/C）模式的批次成员、PS 真值状态、Krita 渲染登记 | `python tools/mode_batch.py --plan/--manifest`；重跑 `--manifest` 后须再跑 `render_mode_table.py --amend-data` |
| [`数据/mode-align.json`](数据/mode-align.json) | PS 模式号 ↔ Krita TexturingMode 的对齐表（含 runtime_id） | 任务 33/37 数据；Krita 基线生成时断言用 |

---

## 1. 概述

**brushConverter** 是一个把 Adobe Photoshop 笔刷集（`.abr`）转换为 Krita 笔刷（`.kpp` 预设 / `.bundle` 资源包）的工具，带命令行（CLI）和图形界面（GUI）两种形态，可用 PyInstaller 打包成 Windows 可执行文件。

**技术栈**：Python 3.12 + numpy + Pillow + PySide6（GUI）+ PyInstaller（打包）。

**核心数据流**：

```
.abr 文件
   │  AbrFile.parse()          —— 拆 8BIM 区段
   ├─ samp 区 → 采样笔尖位图（BrushTip）
   ├─ desc 区 → 笔刷参数描述符（Descriptor）
   ├─ patt 区 → 纹理图案（v1.1 起已映射，PNG base64 内嵌）
   └─ phry 区 → 缩略图（忽略）
   │
   │  map_presets()            —— 参数映射
   ▼
BrushPreset 列表（名称 / 尺寸 / 间距 / 角度 / 硬度 / 动态 / 散布…）
   │
   │  convert_presets()        —— 生成产物
   ├─ build_preset_xml()  → 预设 XML
   ├─ build_kpp()         → .kpp（PNG + iTXt）
   └─ write_bundle()      → .bundle（ZIP 资源包）
```

## 2. 项目结构

```
brushConverter/
├── src/brush_converter/          # 核心库（纯逻辑，不依赖 GUI）
│   ├── abr/                      # ABR 解析
│   │   ├── reader.py             #   头部 + 8BIM 区段遍历（AbrFile）
│   │   ├── samples.py            #   samp 采样笔尖提取（v1/v2 + v6.1/v6.2）
│   │   ├── rle.py                #   Photoshop PackBits RLE 解码
│   │   ├── descriptors.py        #   desc 描述符解析（Descriptor/Value/Reader）
│   │   └── patterns.py           #   patt 纹理图案解析（Pattern/VMA/RLE）
│   ├── kpp/                      # KPP/bundle 生成
│   │   ├── preset_xml.py         #   预设 XML 组装 + 笔尖定义（png_brush/auto_brush）
│   │   ├── kpp_writer.py         #   PNG 块手工构造（iTXt/tEXt/IDAT）
│   │   └── bundle.py             #   .bundle（OpenDocument manifest/meta）
│   ├── mapping.py                # 描述符 → BrushPreset 参数映射
│   └── convert.py                # 转换管线编排（convert/convert_presets）
├── gui/                          # PySide6 图形界面
│   ├── main_window.py            #   主窗口（预览/勾选/格式选择/未映射提醒）
│   ├── workers.py                #   QThread 后台解析/转换
│   └── __main__.py               #   python -m gui 入口
├── cli.py                        # 命令行入口（info/extract/convert/gui）
├── brushConverter_gui_entry.py   # GUI 打包专用入口（避开 sys.path hack）
├── brushconverter-*.spec         # PyInstaller 打包配置（cli / gui）
├── tests/test_mapping.py         # 参数映射回归测试（需自备样本）
├── docs/                         # 文档
├── assets/                       # 应用图标
└── research/                     # 调研用的第三方源码/样本（不进 git）
```

## 3. ABR 解析

### 3.1 文件格式总览

ABR 有两个时代：

| 版本 | 布局 |
|------|------|
| v1 / v2（旧） | 头部（version + subversion=笔刷数量）后，各笔刷**依次排列** |
| v6 / v10（新） | 头部后是 **8BIM 标签块**序列 |

新格式的 8BIM 区段（`reader.py` 遍历）：

| 区段 key | 内容 | 本项目是否使用 |
|----------|------|----------------|
| `samp` | 采样笔尖位图（RLE 压缩灰度） | ✅ 提取笔尖 |
| `desc` | 笔刷参数描述符 | ✅ 参数映射 |
| `patt` | 纹理图案 | ✅ 提取并内嵌（`type="patterns"`） |
| `phry` | 缩略图 | ⛔ 忽略 |

每个 8BIM 块 = `8BIM`(4B) + key(4B) + length(4B, 大端) + 数据 + 4 字节对齐填充（**最后一段可能没有填充**）。

### 3.2 samp 采样笔尖（`samples.py`）

v6.2 单笔尖项布局（对照 GIMP `gimpbrush-load.c`）：

```
u32  item_size
     [v6.2 跳过 301 字节]  (v6.1 跳过 47 字节)
     [1+36 字节 UUID（Pascal 风格：1 字节长度 + ASCII）]
4×i32 rect (top, left, bottom, right)
i16  depth_bits   (8 或 16)
i8   compress     (0=原始, 1=RLE)
...  像素数据
```

笔尖宽高 = `right - left`、`bottom - top`。下一支笔尖位置 = `pos + 4 + round_up_to_4(item_size)`。

v1/v2 旧布局：`u16 type`（1=计算笔刷跳过 / 2=采样）→ `u32 size` → `u32 misc` → `u16 spacing` → [v2: UCS-2 名称] → `u8 aa` → `4×i16 short bounds` → `4×i32 long bounds` → `u16 depth` → `u8 compress` → 数据。

### 3.3 RLE（`rle.py`）

Photoshop PackBits：先读 `height` 个 `int16`（大端）作为每行压缩长度，再逐行 PackBits 解码：

- `n >= 0`：后 `n+1` 字节是字面量
- `n < 0`（`n != -128`）：后 1 字节重复 `-n+1` 次
- `n == -128`：无操作

16 位原始像素按**小端**读取（与 GIMP `GUINT16_FROM_LE` 一致）。

### 3.4 灰度语义（重要，直接决定生成端）

ABR 采样字节里 **0 = 墨（不透明）、255 = 透明**。Krita 源码证实：ABR 加载器 `convertToQImage` 和 GBR 加载器都做 `255 - byte` 反转。

因此 `BrushTip.gray` 统一定义为**不透明蒙版**（255=墨、0=透明），解码时 `255 - raw`。这个约定贯穿整个项目——预览、PNG 笔尖生成都直接用它。

### 3.5 desc 描述符（`descriptors.py`）

`desc` 区段 = `u32 version`（忽略）+ 一个 Photoshop 二进制 Descriptor：

```
Descriptor: unicode name → classID → u32 itemCount → items[]
item:       key(readId) + type(4 字符码) + value
```

value 类型：`long`/`doub`/`bool`/`TEXT`/`enum`/`UntF`（4 字节单位 + double）/`Objc`（嵌套描述符）/`VlLs`（值列表）/`tdta`/`alis`/`obj ` 等。

笔刷预设列表在 `desc["Brsh"]`（VlLs），每项是 `Objc`，包含 `Nm `（名称）、`Brsh`（笔尖定义：`Dmtr`/`Hrdn`/`Angl`/`Rndn`/`Spcn`/`sampledData` UUID）以及各种动态键（`szVr`/`opVr`/`angleDynamics`/`scatterDynamics`…）。

> 解析算法参考 SonyStone/ABR-Viewer（MIT），`research/descriptor-parser.ts` 有完整 TS 参考。

### 3.6 patt 纹理图案（`patterns.py`）

`patt` 区段存纹理图案，结构为「长度前缀」的 Pattern 记录（实测逆向，对照 psd-tools 的
Pattern / VirtualMemoryArrayList 结构修正 ABR 差异）：

```
Record = u32 len | 记录体（len 字节） | pad 到 4
记录体 = u32 version(=1) | u32 颜色模式(3=RGB, 1=灰度) | 2×i16 (宽,高) |
        u32 名字符数(含结尾 NUL 字符) | 名字 UTF-16BE | u8 名字节数(=36) | UUID(ASCII) |
        VMA 列表: u32 version(=3) | u32 body_len | body {
          u32×4 矩形(top,left,bottom,right) | u32 通道数 | (通道数+2) 个通道块 }
通道块 = u32 is_written(0=空) | u32 len | u32 depth | u32×4 矩形 |
        u16 pixel_depth | u8 compression(0=RAW, 1=RLE) | 数据(len-23)
```

要点：
- **名字符数包含结尾 NUL 字符**（27 的字符串实际 26 个可见字符 + NUL）。
- **图案 id 是 u8 长度 + ASCII UUID**（与 psd-tools PSD Pattern 的 Pascal 字符串不同）。
- 图案按 **UUID** 关联笔刷（`desc` 的 `Txtr.Idnt`）；不能按名字（样本里有 3 张同名
  "Shape 2.png" 内容各不同）。
- RLE(1) 为 PSD 版：`height` 个 u16 行字节数 + 每行 PackBits（4 个重复字节的头是
  `0xFD`，即 256-3，不是 256-4）。
- `parse_patterns()` 游标必须精确走完区段（4 字节对齐），否则视为格式错误。

## 4. 参数映射（`mapping.py`）

`map_presets(abr)` 把 desc 里的每个预设描述符转成一个 `BrushPreset` dataclass，字段包括：名称、间距、角度、圆度、硬度、直径、缩放、笔尖灰度数组、UUID、是否计算笔刷、各动态曲线、散布参数、未映射警告。

映射后的 `BrushPreset` 在 `preset_xml.py` 里被序列化成 Krita 的预设 XML。核心映射：

| 类别 | ABR 源 | Krita 目标 |
|------|--------|-----------|
| 尺寸 | `Dmtr` | `scale = Dmtr / max(笔尖宽, 笔尖高)`；采样笔尖内嵌 PNG 补零为正方形 |
| 间距 | `Spcn`（#Prc） | 计算笔刷 `spacing = Spcn/100`；采样笔刷再乘短边/长边（见 `parameter-mapping.md` §1） |
| 角度 | `Angl`（度） | `angle = math.radians(Angl % 360)` |
| 硬度（计算笔刷） | `Hrdn` | `hfade/vfade = Hrdn/100` |
| 压感→大小 | `szVr.bVTy==2` + `minimumDiameter` | `SizecommonCurve` |
| 压感→不透明度 | `opVr.Mnm` | `OpacitycommonCurve` |
| 压感→流量 | `prVr.Mnm` | `FlowcommonCurve` |
| 压感→宽高比 | `roundnessDynamics` + `minimumRoundness` | `RatiocommonCurve` |
| 旋转 | `angleDynamics.bVTy` | `RotationSensor`（drawingangle/pressure） |
| 散布 | `useScatter` + `scatterDynamics.jitter` | `PressureScatter` + `ScatterValue` |
| 纹理 | `useTexture` + `Txtr.Idnt` + `textureScale/Depth/BlendMode/InvT/Brightness/Contrast` | `Texture/Pattern/*` + `Texture/Strength/*`（逐项见 `parameter-mapping.md` §7） |

**控制源编码（bVTy）**：0=关、1=渐隐、2=压力、3=倾斜、4=转轮、5=旋转随机、6/7=初始方向/方向。映射到 Krita 传感器：pressure（bVTy=2）、drawingangle（bVTy=6/7，随笔迹方向）；bVTy=1/3/4/5 未映射（若 jitter>0 会在 GUI 提示「旋转控制源未映射」）。旋转/散布的逐项换算（含 bVTy=0 纯随机抖动的 fuzzy 传感器、`RotationValue=jitter/100`、随机度曲线归一化 `0,0;1,1;`）见 [`parameter-mapping.md`](parameter-mapping.md) §3、§8。

**计算笔刷**（无 `sampledData` UUID，只有参数）：映射为 Krita 的 `auto_brush`（程序化圆形笔尖），`diameter`/`ratio`（圆度）/`hfade`/`vfade`（硬度）放进 `MaskGenerator`。

完整逐字段清单见 [`parameter-mapping.md`](parameter-mapping.md)。

## 5. KPP / bundle 生成（`kpp/`）

### 5.1 .kpp 结构

`.kpp` **不是 ZIP**，本质是一个 PNG 文件：

```
PNG 签名
IHDR        （200×200, 8bit RGBA 预览）
iTXt        （keyword="preset"，zlib 压缩的预设 XML）
tEXt        （keyword="version"="5.0"）
IDAT        （预览图像数据）
IEND
```

`kpp_writer.py` 手工构造这些 PNG 块（长度 + 类型 + 数据 + CRC32）。

### 5.2 笔尖内嵌方式

Krita 5.x 用 `embedded_resources="2"`，笔尖 PNG 以 **base64 内嵌**在预设 XML 的 `<resources>` 里：

```xml
<Preset paintopid="paintbrush" name="..." embedded_resources="2">
  <resources>
    <resource name="..." filename="....png" type="brushes" md5sum="...">
      <![CDATA[ base64(PNG) ]]>
    </resource>
  </resources>
  ...
  <param name="brush_definition" type="string"><![CDATA[
    <Brush type="png_brush" filename="....png" md5sum="..." spacing="..." angle="..." scale="..."/>
  ]]></param>
</Preset>
```

`md5sum` = 笔尖 PNG **原始字节**的 MD5（hex）。

### 5.3 纹理内嵌（`type="patterns"`）

纹理与笔尖一样内嵌在 `<resources>` 里，但 `type="patterns"`，PNG base64：

```xml
<resource name="tex_0123456789ab.png" filename="tex_0123456789ab.png"
         type="patterns" md5sum="...">
  <![CDATA[ base64(PNG) ]]></resource>
```

资源名由**内嵌 PNG 字节的 sha256 前 12 位**派生（v1.2.1 起；旧版为与内容无关的
`tex_<uuid8>.png`），且内嵌 PNG 追加只含元数据的 tEXt 块——两个 Krita 5.3.3
静默坑的规避方式与证据见 §7.6。

预设参数（`preset_xml.py` 的 `TextureXml` / `_apply_texture`）：
`Texture/Pattern/{Enabled,Name,PatternFileName,PatternMD5Sum,Scale,Brightness,
Contrast,Invert,TexturingMode}`、`Texture/Strength/{Value,UseCurve,commonCurve}`、
`PressureTexture/Strength/`。

`PressureTexture/Strength/` 是 Krita「效果强度」选项的启用开关（`isChecked`，
见 `KisKritaSensorPack::read` 的 `getBool("Pressure"+id)`），与压感无关：
PS 纹理深度无论是否接传感器都要设为 `true`；深度随压感（`bVTy==2`）时再设
`Texture/Strength/UseCurve=true` 并写 `commonCurve`（0 端点 = 同口径的深度最小值），
`bVTy==0`（无传感器）时设 `Texture/Strength/UseCurve=false`，由
`Texture/Strength/Value` 提供恒定强度。

强度换算（v1.2.1 起）：高度系（`Hght`/`height`/`HghtPS`/`linearHeight`/
`linearHeightPhotoshop`/`linearHeightPS`）一律
`min(1.0, 1.2 × depth/100)`，压感曲线 0 端点用
`min(1.0, 1.2 × depth_min/100)`；其余模式保持 `depth/100` 与 `depth_min/100`。
常数与函数在 `convert.py`（`HEIGHT_STRENGTH_GAIN`、`_texture_strength`、
`_texture_strength_min`），依据见 §7.6 第 23 条。

注意：
- **`PatternMD5Sum` 存 hex md5**；`PatternMD5` 留空——Krita 5.0 曾在该字段写原始
  二进制导致非法 XML（Krita 官方 MR 修复），新版本以 `PatternMD5Sum` 为准。
- `TexturingMode` 数值来自 Krita `KisTextureOptionData::TexturingMode`：Multiply=0、Subtract=1、Lightness=2、Gradient=3、Darken=4、Overlay=5、Color Dodge=6、Color Burn=7、Linear Dodge=8、Linear Burn=9、Hard Mix (Photoshop)=10、Hard Mix Softer (Photoshop)=11、Height=12、Linear Height=13、Height (Photoshop)=14、Linear Height (Photoshop)=15。ABR 的 `linearHeight` 优先映射到 15，避免误落到 Darken=4。v1.3.0 把 `_TEXTURING_MODE` 由 19 键扩到 **34 键**并逐模式定论：**PS UI 仅 10 项可选模式全部支持（8 可映射 / 1 需校准 hardMix / 1 不可映射 Drkn）**；完整 47 模式枚举归档为 22 可映射 / 1 需校准 / 24 不可映射（其中 23 个不可映射是 UI 选不到的内部值，不计为用户限制），未登记的模式回退 0 并警告；逐模式结论、证据强度与「0.00% ≠ 语义等价」的口径见 [`数据/mode-table.json`](数据/mode-table.json) 与 [`parameter-mapping.md`](parameter-mapping.md) §7.2。
- 亮/对比度换算依据 Krita `KisTextureMaskInfo::recalculateMask()` 与 Photoshop/Krita 对照。**默认公式为 `calibrated`（v1.2.0 起，任务 12 拟合 + 任务 13 转正）**：对比度 `c_k = k`（PS 中心因子，`C<0→1+C/100`、`C>=0→1/(1-C/100)`）；亮度 `b_k = (1-k)/(510k) - v/255`（C>=0）或 `(1-k)/(510k) - v/(255k)`（C<0）；保留 3 位小数，最终 clamp 到 Krita UI 范围（亮度 [-1,1]、对比度 [0,2]）。掩码空间 A/B：`calibrated` 0.00% vs 旧公式 70.75%。
  历史方案（`legacy`，v1.1.2 行为，可用环境变量回退）：所有模式统一亮度 `round(0.10-v/250,2)`，对比度用同一 PS 中心因子并 round 两位。开关与回退命令见 `parameter-mapping.md` §7.1。
  Linear Height (Photoshop) 曾使用 0.30 基线与对比度倒数的特殊映射，实测效果不好，2026-08-27 确认取消。源码快照与来源链接见 `research/krita_texture/` 和 `research/README.md`。

### 5.4 bundle 结构

`.bundle` 是 ZIP 归档（严格照 `KoResourceBundleManifest.cpp`）：

```
mimetype                      首项、stored 不压缩 = "application/x-krita-resourcebundle"
META-INF/manifest.xml         OpenDocument manifest 命名空间（易错，见 §7）
meta.xml                      OpenDocument meta
paintoppresets/*.kpp          自包含笔刷预设
```

**v1.3.1 产物加固**（内容短哈希命名 / 覆盖防呆 / 回读校验 / 纹理计数，使用者批准的 4 条）：

- **命名**：`<ABR名>_v<版本>_<8位逻辑内容哈希>.bundle`，让「同名 ⇒ 同内容」成立（`bundle_content_hash8`）；版本号隔离不同版本产物。
- **覆盖防呆**：目标同内容 → 复用不重写；同名不同内容 → 自动 `_2/_3` 后缀、原文件不覆盖；旧版 `stem.bundle` 只提示不覆盖不删（`_resolve_bundle_path`）。
- **回读校验**：写出后校验 zip 可读 / 条目数 / mimetype / manifest md5 / 每个 `.kpp`，失败结论进 `ConvertResult.verify`，CLI 退出码 3（`verify_bundle`）。
- **纹理计数**：「含真实纹理」口径 = `Texture/Pattern/Enabled=true` 且内嵌图案非 1×1（`count_real_textures`，GUI 摘要与回归共用）。

## 6. CLI 与 GUI

- **CLI**（`cli.py`）：`info`（概况）、`extract`（导出笔尖 PNG）、`convert`（转换）、`gui`（启动界面）。
- **GUI**（`gui/`）：解析和转换都在 `QThread` 后台线程（`workers.py`），界面不卡。每支笔刷一张卡片（勾选框 + 缩略图 + 名称 + 未映射警告），支持全选/全不选、产物格式选择（.kpp/.bundle）、转换前对含未映射参数的笔刷弹窗提醒。

## 7. 开发过程中的重要发现（坑）

这些是花时间对照源码、反复试错才确定的结论，供后续维护参考：

### 7.1 格式层面

1. **`.kpp` 是 PNG + iTXt 块，不是 ZIP**。预设 XML 经 zlib 压缩放进 `iTXt`（keyword=`preset`），旧 2.2 格式用 `zTXt` + version=`2.2`。
> **v1.5.0 纠正**：本条旧表述「白=墨」是错的（130/135 在 Krita 5.3.3 实测 + 2026-11-23 对 ABR 样本复核）。
2. **笔尖是 PNG，不是 GBR**。Krita 5.x 的 L 模式 `png_brush` 把「全灰无 alpha」的 PNG 当 mask：**深(0)=墨、白(255)=透明**。所以直接用灰度 PNG 即可，无需 GBR——这是阶段 3 推翻阶段 1「GBR 方案」的关键转折。
3. **灰度约定（正确极性）**：ABR 采样字节 **255=墨、0=透明**（实测 `tests/测试用.abr` 笔尖角点 raw=0、中心 raw=255）；Krita 的 L `png_brush` **深(0)=墨、白(255)=透明**。转换链内部位图统一为 **0=墨/255=透明**（`samples._decode_pixels` 做一次 `255−raw` 反相），与 Krita 极性一致；Krita 自己的 ABR importer 也做 `255−byte` 反相，所以 **ABR 字节→内嵌 PNG 字节是反相关，但落笔墨区极性经两次反相相消、结果与 PS 同极**。
4. **bundle 的 `manifest.xml` 用 OpenDocument 命名空间**（`urn:oasis:names:tc:opendocument:xmlns:manifest:1.0`），不是自定义格式，容易写错。
5. **Krita 官方导入 ABR 时不解析 desc**（`kis_abr_brush_collection.cpp` 只取 samp 位图、参数用默认值）。所以「参数映射」是本项目相对官方导入的增值点。

### 7.2 参数映射层面

6. **角度存弧度，不是度**。Krita 笔刷 XML 的 `angle` 存弧度（`CommonData.angle`），UI 用 `zoom(scale(180/π))` 转成度显示。
7. **角度 UI 范围是 [0,360)，负角度会被 clamp 到 0**。`KisAngleSelector` 的 spin box 是 `setRange(0,360)`，负角度 `-33°` 会被 `setValue` 夹到 0（=360°）。所以必须 `angle % 360` 先 normalize 成正角度（`-33° → 327°`）再转弧度。这是一个「度→弧度方向对了、但负角度仍显示 360°」的二次 bug。
8. **硬度 `fade = Hrdn/100`，不是 `(100-Hrdn)/100`**。Krita auto_brush 的 `hfade/vfade` 是「实心区占半径比例」（`valueAt` 里 `n < fade` 为实心），与 Photoshop 硬度同向（84% → 0.84，不是 0.16）。
9. **`PressureScatter` 是「散布选项是否启用」的开关，不是「压感控制散布」**。它序列化的是 `isChecked`（`getBool("Pressure" + id)`），压感控制由 `ScatterSensor` 决定。误读它会同时搞错两个方向。
10. **散布量在 `scatterDynamics.jitter`，不在顶层 `Spcn`**。顶层 `Spcn` 是间距。散布量 `jitter` 是 0–1000%。
11. **散布换算 `÷400`（经验）**。PS 散布用高斯分布（值代表最大范围但集中度高），Krita `ScatterValue` 用均匀分布，视觉密度不同。实测锚点 PS 145% ≈ Krita 35%，得系数约 `÷4`。
12. **模板版本陷阱**：预设模板若取自旧 `Basic_tip_default.kpp`（2.2 格式），会缺 Krita 5.x 的一批 `*commonCurve`/`Pressure*` 参数，且布尔/数值要用 `type="internal"` + 纯文本（不是 `type="string"` + CDATA），否则 Krita 读不到、回退默认值、动态「无控制」。

### 7.3 解析实现层面

13. **numpy 2.x 不允许 `arr[a:b] = bytes`**，必须用 `np.frombuffer(buf, dtype, count, offset)`。
14. **16 位原始像素按小端读**。
15. **最后一 8BIM 段可能没有 4 字节对齐填充**，遍历时要容忍轻微越界。
16. **v6.1 跳 47 字节、v6.2 跳 301 字节**（`abr_skipped_bytes`）。
17. **采样笔尖要 `reshape(height, width)`**，否则 1D 数组会被存成「1 像素高的横条」（用户看到的「细白线」bug 根因）。

### 7.4 打包与发布层面

18. **PyInstaller 重打包时先手动清旧产物**。某些沙箱环境的安全删除 shim 会拦截 PyInstaller `--clean`/`--noconfirm` 内部的 `shutil.rmtree`（报 `SAFE_DELETE_FAIL_CLOSED`），解法是先 `rm -rf dist/ build/` 再打包、且不加 `--clean`。
19. **Git Credential Manager 的 token 获取不稳定**。无 `gh`/无 PAT 时，可用 `git credential fill` 从 GCM 取 OAuth token 调 REST API 发布 release，但每次新 shell 常失败，需先 `git ls-remote`（或 push）触发凭据刷新，再**同一命令里**立即 fill + 使用。

### 7.5 渲染验证与公式开关层面（v1.2.0 新增）

20. **Krita 5.3.3 的 `kritarunner` 无头路线不能落笔，真实渲染必须走「GUI 路线 A」，且必须串行**。
    `kritarunner` 能建文档、能读资源库（137 个预设），但 `Window.addView()` 必崩（退出码 `-1073741819`，即 Windows 访问违例，probe 3/3 复现）；
    `Node.paintLine()` 依赖「当前主窗口 → 当前视图」，无视图即访问违例；且它的 Python API 只能按资源库选预设，
    **没有「按 `.kpp` 路径加载预设」的接口**。所以真实渲染走路线 A：临时装 PyKrita 启动插件 + 临时改 `kritarc` +
    把 `.kpp` 临时复制进 `paintoppresets/` → 启动 `krita.com --nosplash` → 插件 `addView`/`paintLine` 落笔 → 导出 PNG →
    `finally` 还原配置。代价：需要交互式桌面会话、单支 11–14 秒，且**同一时刻只能跑一个渲染**（共用 `kritarc` 与插件目录；
    上一轮 `krita.exe` 没退干净会让新实例秒退或插件不加载，任务 06 已用「等残留退出 + 清理残留 + 失败重试一次」兜底）。
    判成败**不看退出码**（kritarunner 不透传，实测恒 0），看结果 JSON + PNG 产物 + Alpha 非零像素 > 0。
    证据：`docs/验收报告/04-报告.md`、`docs/验收报告/06-报告.md`、`docs/研究报告/krita-runner-可行性.md`。

21. **纹理亮度/对比度有环境变量开关 `BRUSHCONVERTER_TEXTURE_FORMULA`，默认 `calibrated`，回退用 `legacy`**。
    v1.2.0 起默认公式已从 v1.1.2 的经验公式（legacy）切到按 Photoshop 公式反解的 calibrated；两条路径都保留在
    `preset_xml.py`，选择集中在 `_texture_formula()`；非法取值回退默认公式并向 stderr 打印一次警告。回退命令
    （GUI 同理：启动 GUI 前设同一环境变量）：`BRUSHCONVERTER_TEXTURE_FORMULA=legacy python cli.py convert tests/测试用.abr -o converted`。
    **改默认值会改变产物字节**：默认路径下 15/25 个 `.kpp` 与 v1.1.2 不同（恰为 15 支带纹理笔刷），显式 `legacy` 下 25/25 与 v1.1.2 的 sha256 全同。
    回归用例 `tests/test_texture_formula_switch.py` 同时断言两条路径的具体数值。公式、A/B 数字与 7 条局限见 `docs/parameter-mapping.md` §7.1 与 `docs/验收报告/13-报告.md`。

### 7.6 纹理深度与资源命名层面（v1.2.1 新增）

22. **Krita 5.3.3 的内嵌纹理有两个静默坑：同 md5 会被静默禁用、同名会串用别人的图案**。
    前者：内嵌纹理与用户图案库里某文件 md5 相同时，Krita 解析该内嵌资源失败 → 纹理被**静默禁用**
    （预设加载正常、`Enabled=true`、字段回读全对，但落笔 Alpha 恒 255，无任何报错）。
    后者：资源名与库内图案同名时被遮蔽，不同字节也渲染出库内那张错图——实测旧命名
    `tex_<uuid8>.png`（如 `tex_66e2987f.png`）撞车后 Alpha 均值 211.6。
    修法（v1.2.1）：内嵌 PNG 追加只含元数据的 tEXt 块（keyword `brushconverter`，内容
    `sha256=<像素 sha256>`，像素逐字节不变）让 md5 必然不同；资源名改为
    `tex_<sha256(内嵌PNG)前12>.png` 保证「重名 ⇒ 同内容」。**两件事必须一起做**：
    只改名避不开 md5 去重坑，只加标记避不开同名遮蔽坑；也**不能只在撞车时才加标记**，
    因为转换器不知道接收方 Krita 图案库里有什么。回归用例 `tests/test_texture_v121_fixes.py`
    断言「解码像素与源数组逐像素相同」「内嵌 md5 ≠ 源文件 md5」「资源名形如 `tex_[0-9a-f]{12}.png`」。
    证据：`docs/验收报告/实验-Krita侧-报告.md` §8.2。

23. **高度系纹理强度必须 ×1.2（PS 权重 12 vs Krita 权重 10）**。PS 高度/线性高度算子按 `12·q`
    加权（`q=round(2.55·depth)`），Krita 写 `10·s`，归一化后固定差 `2q`：d10/scale=100 实测
    PS vs Krita mean\|Δ\|=43.31、max=52、95.36% 像素超阈。修法是把高度系
    `Texture/Strength/Value` 改成 `min(1.0, 1.2×depth/100)`（压感曲线 0 端点同样 ×1.2），
    实测 mean 43.31→**1.39**、max 52→**2**、超阈 95.36%→**0.00%**。
    注意：**非高度系（multiply/subtract/hardMix）不加增益**（实测已 0.00%，动了会引入新偏差）；
    系数不要为了 scale≠100% 的残差继续上调——那部分残差是缩放重采样（PS vs Krita Qt 平滑），
    调系数会破坏 s100 已取得的 0.00%。常数集中在 `convert.py`：
    `HEIGHT_STRENGTH_GAIN`、`_HEIGHT_BLEND_MODES`、`_texture_strength()`。
    证据：`docs/验收报告/实验-归因-报告.md` §4.2/§4.3、`docs/验收报告/实验-Krita侧-报告.md` §17。

### 7.7 资源注册与 md5 语义层面（2026-11 缺陷复盘新增）

24. **`.kpp` 内嵌纹理的 `Texture/Pattern/PatternMD5` 必须留空**。Krita 自己保存预设时会写入 16 字节二进制
    （可能在 XML 里产生非法字符），加载时第一步就把它删掉（`kis_paintop_preset.cpp:189-193`）；
    真正参与校验的是内嵌 resource 的 `md5sum` 属性与 `Texture/Pattern/PatternMD5Sum` 参数，
    **两者都必须等于内嵌 PNG 文件字节的 md5**——Krita 从不哈希解码后的像素（`KoMD5Generator` 直接对字节做 `QCryptographicHash::Md5`）。
    填了 `PatternMD5` 会改变「按 md5 去重」的取用语义，且没有收益。v1.2.1 给内嵌 PNG 追加 tEXt 让字节 md5 必然不同，
    只是规避「同 md5 被库内图案去重」的坑，解码像素逐字节不变；它不改变 md5 的计算对象。
    回归检查：转换产物里 `resource@md5sum == PatternMD5Sum == md5(内嵌PNG)`（可用 `tools/diagnose_profile.py --bundle ... --predict` 一次看出）。

25. **bundle 文件在磁盘上 ≠ 已被 Krita 注册**。Krita 的注册结果只在 `resourcecache.sqlite` 的 `storages`/`resources` 行里；
    覆盖 bundle 后若没有完整重启 Krita，或 cache 已损坏，就会出现「资源库看着正常，但笔刷不可见 / 纹理静默丢失」且**零报错**：
    - 删掉/移走 bundle 文件 → 下次启动 Krita 会**静默删除**该 storage 与整包预设（对应「导入的笔刷彻底不可见」）；
    - bundle 被截断/不完整（复制或覆盖中断、磁盘满、杀软锁文件）→ 预设仍可见可画，但内嵌纹理侧载**静默失败**
      （实测 006 的 Alpha 非零数 8844→4672，纹理列表为空）。
    排查：看 `storages.location` 有没有对应行、`resources.status` 是否 active、`PRAGMA integrity_check` 是否 ok；
    修复：退出 Krita → 备份并删除 `resourcecache.sqlite` 让其重建 → 重启 Krita → 重新导入 bundle；
    覆盖产物时务必「先退出 Krita，复制完成后再启动」。一条命令体检：`python tools/diagnose_profile.py --krita-dir "%APPDATA%/krita" --bundle <你的.bundle> --predict`。

26. **Krita 5.3 会把 `.abr` 当「Adobe Brush Library」直读，生成一套没有经过本工具转换的同款笔刷**。
    原始 `.abr` 被放进 `%APPDATA%\krita`（或在资源管理器里导入 ABR）时，Krita 不走转换链，而是自己解析 ABR
    生成资源：`storages` 里多出一行 `storage_type_id=4`（`Adobe Brush Library`），其下资源是**画笔笔尖
    （brush tips，`resource_type_id=1`）**，不带本工具写入的内嵌纹理与参数映射。它和 `.bundle` 里的转换产物
    混在同一笔刷列表里，看起来就是「纹理全丢 / 笔刷不可见」——本次缺陷的实测真因就在这儿，**不是转换格式问题**。
    **实测：使用者删掉 `%APPDATA%\krita` 下的 `.abr`（或移除该库）并重启 Krita 后，转换产物的纹理即恢复。**
    排查：`tools/diagnose_profile.py` 的 `[3/8]` 节会醒目列出 `storage_type=4` 的库及「该库资源：预设=0 /
    图案=0 / 全部=N」（v1.3.1 起支持检测，之前只能人工看 `storages` 概览）；`[4/8]` 节列出跨 storage 同名
    预设（同名会互相遮蔽/混淆）。两者都算「发现问题」，退出码 1。
    **结论门槛**：遇到「纹理没了」先确认 Krita 里没有 ABR 直读库，再谈产物与缓存——
    相对地，`resourcecache.sqlite` 的 `integrity_check` 轻微异常（`Page N is never used` + 索引条目数不符）
    **不是**本缺陷的必要条件，重建缓存只是可选兜底（代价：自定义预设标签与分组丢失，资源文件不丢）。
    证据：`docs/验收报告/缺陷-纹理丢失-v1.3.1-文档加固.md`。

### 7.8 v1.4.0 新增的坑（参数修正、测试 .abr、传感器默认）

27. **从零构造 `.abr` 时，采样笔尖像素数据后必须跟 8 个 0 字节尾块**。三个样本共 21 个采样笔尖项，
    无论 RAW 还是 RLE，像素之后都固定跟 `0000000000000000`；漏掉时 Photoshop 直接判整份文件
    「与这个版本的 Photoshop 不兼容」。坑在于**本项目自己的解析器照样能读**（`_decode_pixels` 只取
    `width*height` 字节），所以自检全绿、PS 打不开，极易漏。测试笔刷生成器
    `tools/make_test_abr.py` 已固定写入该尾块（证据：`docs/验收报告/110-报告.md` §三）。
28. **`*Sensor` 必须写合法 XML，禁止空 `<params/>`**。`.kpp` 模板的 17 组 `*Sensor` 默认写
    合法的 `pressure` 占位，未映射动态用 `Pressure*=false` + `*UseCurve=false` 关闭；只有 ABR
    明确映射到压感/方向等控制源时才开启（`build_preset_xml` 里按需覆盖）。**空 `<params/>` 会
    让 Krita 的 `KisKritaSensorPack::read()` 在 sensorId 为空时早退**，后面的 `UseCurve` /
    `Value` / `commonCurve` 都不读，界面出现异常曲线或旧值（5.3.3 日志实测
    `!sensorId.isEmpty()` 断言 19,850 次）。`d448d24`（2026-11-21 历史重写前的短哈希，现已失效）只修了“默认压感”，空 XML 写法本身是错的；
    2026-11-21 已改为合法 `pressure` 占位（回归 `tests/test_sensor_defaults_v140.py`）。
    **旧版产物必须重转。**
29. **间距短边补偿只改 spacing，不改补白**。采样笔刷 `spacing = Spcn/100 × 短边/长边`
    （`BrushPreset.spacing_basis`，在 `convert._sampled_brush_def()` 乘上）；严格正方形=1、
    近方形按实际比值、计算笔刷不补偿。补白成正方形、255 边框是绕 Krita bug 的刻意做法（铁律 17），
    不要为了修间距去动它——那会同时改笔迹本体（非方形笔迹仍比 PS 高约 1.3–1.5×，属已知）。
30. **Krita 界面参数错乱先查 sensor id，不要先归因 UI bug**。5.3.3 里点「新建画笔（像素引擎）」
    再切回测试笔刷，确实可能让编辑器显示状态混乱；但 2026-11-21 复核发现，之前记录的
    “所有测试笔刷曲线异常、散布 500%/强度 9%”是本项目 `.kpp` 写了空 `<params/>` 导致
    `KisKritaSensorPack::read()` 早退，不是 Krita 的锅。判定前先**彻底退出 Krita、重新导入
    新产物、重启后再看**；同时用 `kpp_params` 检查所有 `*Sensor` 都有非空 `id`，并查
    `%LOCALAPPDATA%\krita.log` 是否还有 `!sensorId.isEmpty()`（证据：`docs/验收报告/112-报告.md`
    §5.5 的 2026-11-21 更正）。
31. **公开 / 本地地址分隔**。函数地址、偏移、模块基址这类整数地址只留在本地逆向记录
    （`docs/优化提示词/`，不入公开仓库）；公开文档与矩阵一律用占位或只写结论，
    也不写本机绝对路径（通用规范第 11 条、任务 113）。

### 7.9 v1.5.0 新增的坑（采样笔尖生成、预缩放哈希、legacy 删除）

32. **采样笔尖生成位图沿用「0=墨」极性，外圈必须整圈 255（=透明）**。`hardness_tip.build_tip` 按剖面算 coverage α，
    再写 `stored = 255−α`、`mask = 255−stored`；`verify_generated_tip` 断言「正方形 + 外圈整圈 255」，
    失败即抛错（宁可转换失败也不产出补集/带透明边框的位图——130 首轮 5 组作废的教训）。
    别再按旧文档「白=墨」写补集；极性推导与实测见 §7.1 第 2/3 条。
33. **计算笔刷位图的正方形与边框 255 是结构性质，不是补白**。`build_tip` 边长 = `2·ceil(r_vis)+2`，
    外环按「位图半径 ≥ 边长/2 − 1」强制归零，反相后自然整圈 255；它**不经** `_square_tip`。
    铁律 17 的「补平方 + 边框 255」对采样笔尖仍是刻意做法，**不许为了间距/尺寸去改补白**（会同时改笔迹本体）。
34. **纹理预缩放会改资源名与内容哈希 ⇒ 旧产物必重转**。v1.5.0（135-1）s>100 时先 `_prescale_texture_image`
    重采样再内嵌，`tex_<sha256(内嵌PNG)前12>.png` 与 `PatternMD5Sum` 都按**新字节**重算、`Scale` 写 1.0；
    `_texture_png_array` 的 tEXt 标记也按新像素数组派生。别把旧哈希写死，也别让不同内容复用旧资源名
    （会踩 Krita 同名串用坑）。体积代价 +0.8 KB~+434 KB/预设。
35. **legacy 纹理公式已物理删除（135）**。`BRUSHCONVERTER_TEXTURE_FORMULA` 与旧公式分支、旧用例全部移除
    （引用 13 处 → 0）；不要按旧文档给「回退命令」——运行时没有回退，只能 `git revert` + 重转。
    对比度 `min(2.0, k)` 同时删除：C>50 现在写 k 原值（9 位小数），Krita 滑块只显示 0..2 属预期。

## 8. 参考实现与样本

- **GIMP**（GPL）：`app/core/gimpbrush-load.c` + `gimp-utils.c`——ABR v6 笔尖解析与 RLE 解码的权威来源。
- **Krita**（GPL）：`libs/brush/kis_abr_brush_collection.cpp`（ABR 导入）、`kis_circle_mask_generator.cpp`（fade 数学）、`kis_brush.cpp`/`kis_global.h`（角度单位）、`KisScatterOption.cpp`（散布公式）、`KoResourceBundleManifest.cpp`（bundle 格式）。
- **SonyStone/ABR-Viewer**（MIT）：desc 描述符解析参考。
- 以上源码下载在 `research/`（本地保留、不进 git）。

**取源码技巧**：invent.kde.org 的 raw 链接会重定向到 HTML，用 `raw.githubusercontent.com/KDE/krita/master/...` 替代；GitHub 的 `git/trees?recursive=1` 可免认证列文件树。

## 9. 开发与测试

```bash
# 环境
conda activate brushConverter
pip install -r requirements.txt

# 转换（CLI）
python cli.py convert <file.abr> -o converted

# 图形界面
python cli.py gui        # 或 python -m gui

# 测试（样本缺失时自动跳过；样本放 tests/ 即可跑完整断言）
python -m pytest tests/ -q

# 打包
pyinstaller brushconverter-cli.spec    # → dist/brushconverter-cli/
pyinstaller brushconverter-gui.spec    # → dist/brushConverter/
```

测试样本是 42MB 的商用笔刷集，未纳入版本库（见 `.gitignore`），需自备到 `tests/` 目录。
