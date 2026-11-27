# brushConverter 项目结构与原理

> **English abstract**: Maintainer reference for the ABR-to-Krita pipeline, its evidence trail, and reproducible checks. Version v1.6.0 was officially released on 2026-11-27; 30 human acceptance items passed and six unverified items were conditionally waived, not marked as passed. Historical reports are not current acceptance results.
>
> 面向维护者：按主题查找现行实现、历史原证据与复核入口。逐字段说明以[参数映射](<parameter-mapping.md>)为准，版本变更以[发布说明](<../releases/RELEASE_NOTES.md>)为准。
> **版本边界**：v1.6.0已于2026-11-27正式发布，合并第五轮149A/153与逐支选项、事务、元信息/标签/追溯；两份冻结构建已作为正式附件发布。187人工验收30项通过；E4、E6、F1、F3、F4、G3六项未验、条件性放行，不写通过。历史实验不是本次文档修订的新回归；精确验收范围、已知问题及隐私裁决以[发布说明](<../releases/RELEASE_NOTES.md>)首段为准。

## 0. 文档索引（先看这里）

公开技术参考与历史证据分层阅读；内部任务书、规范、台账和流程图仅维护者本地保留，不作为公开上手入口。

### 0.1 按目的查

| 我想… | 看这份 |
|---|---|
| 安装、转换与导入 | [README](<../README.md>) → [用户指南](<user-guide.md>) |
| 处理纹理丢失、导入或输出问题 | [排错指南](<troubleshooting.md>)；先排除原始ABR直读库混淆，不先删缓存 |
| 查逐字段公式与已知残差 | [参数映射](<parameter-mapping.md>)（映射说明唯一权威，§7纹理、§7.2模式、§10残差） |
| 查参数键的结论、来源和承载 | [参数矩阵JSON](<参数矩阵.json>)；[渲染版](<参数矩阵.md>)由脚本生成，禁止手改 |
| 查逐模式结论、证据等级与UI可见性 | [模式总表JSON](<数据/mode-table.json>)（47行完整研究枚举，不把内部值全算成用户限制） |
| 看懂通过、失败、跳过与退出码 | [验证体系使用说明](<验证体系使用说明.md>)（验证唯一入口）；当前任务判据不能被旧说明中的宽松摘要替代 |
| 加基准、比图或做手感分析 | [基准库命名规范](<基准库命名规范.md>)、[比对与定位](<比对与定位说明.md>)、[手感比对](<手感比对说明.md>) |
| 查三层验收、纹理掩码复刻 | [全量验收说明](<全量验收说明.md>)、[纹理掩码复刻说明](<纹理掩码复刻说明.md>) |
| 理解Krita/PS出图装置 | [Krita渲染说明](<Krita无头渲染说明.md>)、[批量渲染说明](<批量渲染操作说明.md>)、[PS真值自动化](<PS真值自动化说明.md>)；不是本轮启动软件的授权 |
| 准备候选包与发布 | 本文§7.4/§9 + [发布说明](<../releases/RELEASE_NOTES.md>)；先冻结、机器检查、人工验收与准确对象批准，再发布 |

### 0.2 研究结论（为什么这么映射，按主题）

| 主题 | 原证据 | 适用范围与现行入口 |
|---|---|---|
| 亮度/对比度早期假设（已被取代） | [早期调研原文](<归档/photoshop-texture-brightness-contrast-research.md>) | 旧0.10/0.30基线、对比度倒数与“缺少自动真值能力”仅作演进记录；当前看[参数§7.1](<parameter-mapping.md#texture-bc-current>) |
| 现成成果调研、黑盒拟合 | [纹理亮度对比度调研](<研究报告/纹理亮度对比度-调研.md>)、[LUT拟合](<研究报告/lut-拟合.md>) | 保留当时样本和误差口径，不把拟合写成PS像素源码复刻 |
| 参数语义的静态/动态依据 | [PS逆向静态](<研究报告/ps-逆向-静态.md>)、[动态](<研究报告/ps-逆向-动态.md>)、[参考实现](<研究报告/ps-纹理公式-参考实现.md>) | 参数语义证据与数据拟合的像素公式分开；未定位消费端不等于没有像素作用 |
| 缩放/深度（v1.2.1历史归因） | [缩放深度归因实验](<研究报告/缩放深度-归因实验.md>) | 当时缩放残差不是当前现状：v1.5.0已落地>100%预缩放，剩余边界看[参数§10](<parameter-mapping.md#residual-current>) |
| Krita渲染通路 | [kritarunner可行性](<研究报告/krita-runner-可行性.md>) | 记录5.3.3无头落笔失败与GUI路线A；本轮未重渲，不承诺其它版本 |
| 第五轮历史候选新增与旧结论勘误 | [149A报告](<验收报告/149A-报告.md>)、[152报告](<验收报告/152-报告.md>)、[153报告](<验收报告/153-报告.md>) | 单随机源、B9错配更正、B10预烘焙；当前残差仍以参数§10和数据真相为准 |
| 历史方案 | [初版映射](<归档/mapping-proposal.md>)、[纹理方案](<归档/texture-mapping-proposal.md>)、[可行性](<归档/feasibility.md>)、[文档对齐报告](<归档/文档对齐报告.md>) | 已被取代或阶段性报告，不据此恢复GBR、legacy、旧系数或旧生产路径；见[归档说明](<归档/维护参考与归档说明.md>) |

### 0.3 验收证据（证据归档，非上手必读；编号对应内部任务书）

- [验收报告索引](<验收报告/INDEX.md>)与[研究报告索引](<研究报告/INDEX.md>)由原生成器维护；验收报告是一次性只读历史，记录“当时测出了什么”，不是持续维护的现状页。后续勘误在现行参考文档说明原结论、失效范围、根因与新证据，不回写验收报告正文、不重排旧编号或旧数据。
- [验收归档目录](<验收报告/归档/>)保留三层验收记录；生成命令及退出码口径见[全量验收说明](<全量验收说明.md>)。历史实验图、JSON、kpp和二进制不因目录整齐而搬迁。
- **公开索引使用已实现的 `--tracked-only` 模式**：只收录顶层、Git已跟踪且未声明为内部/仅本地/预写/原始证据的报告；本地报告不公开。两份索引已在文档汇合阶段由主会话串行生成，日常只读检查见§9。默认旧模式仍保留，不用于维护这两份公开INDEX；可见性过滤不等于隐私门或发布批准。

### 0.4 常见接手路径

1. **首次接手**：[README](<../README.md>) → 本文§2 → [参数映射](<parameter-mapping.md>) → [验证入口](<验证体系使用说明.md>)。
2. **查或改映射**：本文§4/§5 → 参数参考 → [独立期望表](<../tests/golden/params_expected.json>)。先核准证据再由获授权任务维护期望与实现，禁止从当前产物反抄期望“修绿”；本轮文档任务不改矩阵、模式结论或旧基准。
3. **查纹理**：本文§5.3/§7.6 → [当前公式](<parameter-mapping.md#texture-bc-current>)和[残差](<parameter-mapping.md#residual-current>) → 原研究报告。历史结论不能覆盖已落地的预缩放、预烘焙或单随机源。
4. **加验证**：[验证入口](<验证体系使用说明.md>) + [命名规范](<基准库命名规范.md>) + [全量验收说明](<全量验收说明.md>)。新增数据另建任务目录，旧商用样本和证据只读。
5. **准备交付**：本文§7.4/§9 + [发布说明](<../releases/RELEASE_NOTES.md>)。跳过不算全验；全量与应用渲染串行，真实界面和最终批准由使用者完成。

### 0.5 数据文件索引（v1.3.0 定稿）

节名保留旧锚点；内容包含后续轮次。逐项结论只在JSON真相维护，其余页面给摘要与链接，不另造结论副本。

| 文件 | 唯一职责 | 维护与校验 |
|---|---|---|
| [参数矩阵JSON](<参数矩阵.json>) | 参数键结论、来源、承载和UI绑定 | 本轮只读；获授权唯一写者更新后由原生成器同步，跑check_matrix与render_matrix_md --check |
| [矩阵Markdown](<参数矩阵.md>) | 矩阵及模式节渲染版 | 生成物，禁止手改 |
| [模式总表](<数据/mode-table.json>) | 逐模式verdict、证据等级、复跑线索 | render_mode_table --check；禁跑--build，避免丢后补字段 |
| [PS键全集](<数据/ps_keys.json>)、[ABR键全集](<数据/abr_keys.json>)、[UI控件清单](<数据/ps_ui_controls.json>) | 键集合与UI对账 | check_matrix与check_ui_controls的输入，保持原址 |
| [候选清单](<数据/candidates.json>) | 历史候选与证据行号 | 静态研究报告按行号被引用，不插行、不迁移 |
| [模式批次](<数据/mode-batch.json>)、[模式对齐](<数据/mode-align.json>) | 批次与运行期模式对齐 | 原工具独占维护；若重跑--manifest，须按原流程补--amend-data，文档任务不重建 |

### 0.6 现行实现与计划功能分开

- **已实现的生产链**：[逐支契约](<../src/brush_converter/export_options.py#L489-L549>) → [转换计划](<../src/brush_converter/convert.py>) → [安全事务](<../src/brush_converter/export_transaction.py>)。三项适用才默认开启；关闭进入独立原生替代，不恢复legacy或旧双随机源。采样笔尖补方/255不变，bundle与独立KPP均不再直接覆盖旧文件。
- **已实现的接入**：[CLI](<../cli.py#L731-L805>)与[GUI](<../gui/main_window.py>)共用稳定ID、条件默认和实际运行摘要。GUI换源/重载重置，筛选/排序/语言保留隐藏选择；CLI显式JSON与源摘要校验，不按显示名猜身份。用户操作只维护在[用户指南](<user-guide.md>)。
- **已实现的包格式**：[元信息模型](<../src/brush_converter/export_metadata.py>)与[写包/回读](<../src/brush_converter/kpp/bundle.py>)支持原生逐笔标签、包内追溯及v2身份；独立KPP不承载这套包信息。GUI空表单和不带metadata-file的CLI不是同口径，见§6。
- **v1.6.0已完成发布，未验项如实保留**：183于2026-11-27全量1149/0/0/0（两次一致），verify_all235/0/0，四结构校验exit0；185已构建两附件，186源码/全部历史block=0，ZIP的14个block及未读项按裁决接受并披露。187人工30项通过、六项未验条件性放行，188已按批准对象发布；不将条件放行、人工自述或机器结构成功改写为全量人工通过。详细范围与D-2/D-6见发布说明首段。

### 0.7 归档与维护约定

技术事实按主题查本页和参数参考，历史数据按原报告查。验收报告正文一次性交付后只读，不因后续发版、勘误或链接规则变化维护回写；日常文档检查排除其正文，但现行文档指向报告的目标/锚点仍检查，目录README/INDEX仍作为维护入口检查。机器引用的原证据不迁移、不重排行号，现行勘误另述，不改原件。其他人工维护Markdown迁移须明确批准并保留旧路径/原标题锚点与行号对照；生成物不搬、不手改，内部资料不进公共索引。退出码0不能替代未做的真实效果或发布门。

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
   └─ build_bundle()    → .bundle（元信息/逐笔标签/实际追溯）
    │
    └─ ExportTransaction —— 同卷暂存、逐项回读、最后检查点、交付/回滚清单
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
│   ├── export_options.py         # 稳定身份、条件默认、配置/取消与摘要契约
│   ├── export_metadata.py        # 包信息及逐笔标签归一
│   ├── export_transaction.py     # 两格式暂存、验证、交付与回滚清单
│   ├── mapping.py                # 描述符 → BrushPreset 参数映射
│   ├── hardness_tip.py           # PS硬度剖面生成位图及正方形/边框自检
│   └── convert.py                # 转换管线编排（convert/convert_presets）
├── gui/                          # PySide6 图形界面
│   ├── brush_selection.py        # 按稳定ID保存选择/开关与可见集合
│   ├── tag_editor.py             # 元信息/逐支标签状态与导出快照
│   ├── main_window.py            # 主窗口、逐支代价、确认、进度与安全关闭
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
     [项头 skip 字节：v6.2=301，v6.1=47；UUID已包含在其中]
     [项体+0为UUID长度，+1起为UUID；不额外再跳过1+36字节]
4×i32 rect (top, left, bottom, right)  # 项体+skip
u16  depth_bits   (8 或 16)            # 项体+skip+16
u8   compress     (0=原始, 1=RLE)      # 项体+skip+18
...  像素数据                         # 项体+skip+19
```

项体从item_size之后开始（pos+4）；UUID从该处读取，矩形固定在pos+4+skip，不再额外加37字节。笔尖宽高=`right-left`、`bottom-top`；下一项位置=`pos+4+round_up_to_4(item_size)`，见[解析实现](<../src/brush_converter/abr/samples.py#L136-L162>)。

v1/v2 旧布局：`u16 type`（1=计算笔刷跳过 / 2=采样）→ `u32 size` → `u32 misc` → `u16 spacing` → [v2: UCS-2 名称] → `u8 aa` → `4×i16 short bounds` → `4×i32 long bounds` → `u16 depth` → `u8 compress` → 数据。

### 3.3 RLE（`rle.py`）

Photoshop PackBits：先读 `height` 个 `int16`（大端）作为每行压缩长度，再逐行 PackBits 解码：

- `n >= 0`：后 `n+1` 字节是字面量
- `n < 0`（`n != -128`）：后 1 字节重复 `-n+1` 次
- `n == -128`：无操作

16 位原始像素按**小端**读取（与 GIMP `GUINT16_FROM_LE` 一致）。

### 3.4 灰度语义（重要，直接决定生成端）

**沿用§7.1已纠正的极性**：已复核样本ABR原始字节为255=墨、0=透明；[解码器](<../src/brush_converter/abr/samples.py#L46-L68>)实际执行 `255 - raw`，转换链用于内嵌PNG的灰度为0=墨、255=白=透明。源码中“255=墨／生成GBR”的旧注释不是新证据，本轮不改源码。

计算笔刷由[硬度剖面生成器](<../src/brush_converter/hardness_tip.py>)生成stored与预览mask，二者极性不同，不把预览mask直接内嵌。历史纠正见§7.1与[任务133报告](<验收报告/133-报告.md>)。

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

映射后的 `BrushPreset` 在 `preset_xml.py` 序列化。下表为外观优先的条件默认口径；v1.6.0关闭后的原生字段/可调范围见[参数§2](<parameter-mapping.md>)及[用户取舍表](<user-guide.md#tradeoffs>)，不得把默认位图路径写成唯一生产承载。核心映射：

| 类别 | ABR 源 | Krita 目标 |
|------|--------|-----------|
| 尺寸 | `Dmtr` | `scale = Dmtr / max(笔尖宽, 笔尖高)`；采样笔尖内嵌PNG以255居中补白为正方形（不改现行补白）；计算笔刷为硬度剖面生成的 `png_brush`（尺寸=PS 实际足印） |
| 间距 | `Spcn`（#Prc） | 默认位图化计算笔刷：`spacing = Spcn/100 × Dmtr × clamp(Rndn/100,0.001,1) / edge`（edge为位图边长，圆度100%才可省圆度因子）；采样笔刷：`Spcn/100 × 原始短边/长边`；关闭位图化的原生分支为Spcn/100，见参数§1/§2 |
| 角度 | `Angl`（度） | `angle = math.radians(Angl % 360)` |
| 硬度（计算笔刷） | `Hrdn` | v1.5.0 起按 PS 剖面生成 `png_brush` 位图（`hardness_tip.build_tip`），不再写 `hfade/vfade`（Krita 里硬度不可再调） |
| 压感→大小 | `szVr.bVTy==2` + `minimumDiameter` | `SizecommonCurve` |
| 压感→不透明度 | `opVr.Mnm` | `OpacitycommonCurve` |
| 压感→流量 | `prVr.Mnm` | `FlowcommonCurve` |
| 压感→宽高比 | `roundnessDynamics` + `minimumRoundness` | `RatiocommonCurve` |
| 旋转 | `angleDynamics.bVTy` | `RotationSensor`（drawingangle / pressure / speed / rotation）；随机抖动只写 **1 条** fuzzy 加法源（禁止 fuzzystroke） |
| 散布 | `useScatter` + `scatterDynamics.jitter` | `PressureScatter` + `ScatterValue = jitter/200`（`PressureScatter` 只是启用开关） |
| 纹理 | `useTexture` + `Txtr.Idnt` + `textureScale/Depth/BlendMode/InvT/Brightness/Contrast` | `Texture/Pattern/*` + `Texture/Strength/*`（逐项见 `parameter-mapping.md` §7） |

**控制源编码（bVTy）**：0=关、1=渐隐、2=压力、3=倾斜、4=转轮、**5=初始方向、6=笔划方向、7=速度、8=旋转**（v1.5.0 任务 134-3 按 124 A 级 9 值表修正；旧文档写的「5 旋转 / 6 方向 / 7 初始方向」是错的）。映射到 Krita 传感器：pressure（2）、drawingangle（6）、speed（7）、rotation（8）；5/1/3/4 未映射（Krita 同名传感器语义不等价：`fade`=时间渐隐、`ytilt`=倾侧、`tangentialpressure`=转轮）。旋转/散布的逐项换算（含 bVTy=0 纯随机抖动的 fuzzy 传感器、`RotationValue=min(1,jitter/50)`、散布 `ScatterValue=jitter/200`、随机度曲线归一化 `0,0;1,1;`，且旋转只写 **1 条** fuzzy 随机曲线、禁止 fuzzystroke）见 [`parameter-mapping.md`](parameter-mapping.md) §3、§5、§8。

**计算笔刷**（无自带位图）：v1.5.0起默认按PS硬度剖面生成png_brush，尺寸为实际足印、硬度/fade不再按PS语义编辑。v1.6.0关闭位图化后走独立生产级 `_native_computed_brush_def`，而不是历史研究 `_computed_brush_def`；原生spacing=Spcn/100，不乘位图edge/圆度补偿，size为标称直径、ratio为圆度比。原生硬度可调但受1.00R、几何和满覆盖条件限制；结构自检不等于任意参数实机编辑通过。既有默认残差仍见参数§10。

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
`Texture/Strength/UseCurve=true` 并写 `commonCurve`（当前实现0端点写强度比值，不是绝对强度；已知minimumDepth量纲缺陷仍未修，见[参数§10.5](<parameter-mapping.md#residual-current>)），
`bVTy==0`（无传感器）时设 `Texture/Strength/UseCurve=false`，由
`Texture/Strength/Value` 提供恒定强度。

强度与曲线只在[参数参考§7](<parameter-mapping.md#texture-bc-current>)维护；[生产管线](<../src/brush_converter/convert.py>)先按高度系增益/非高度系算强度，再生成乘性曲线比值。§7.6第23条保留增益来源，不把旧绝对0端点复制回实现。

注意：
- **`PatternMD5Sum` 存 hex md5**；`PatternMD5` 留空——Krita 5.0 曾在该字段写原始
  二进制导致非法 XML（Krita 官方 MR 修复），新版本以 `PatternMD5Sum` 为准。
- 纹理枚举与逐模式口径以[模式总表](<数据/mode-table.json>)及[参数§7.2](<parameter-mapping.md#texture-modes-current>)为准：PS UI十项是8可映射/1需校准hardMix/1不可映射Drkn，不承诺全部支持；0.00%可能受饱和、退化或塌缩条件限制。
- 亮度/对比度当前只有calibrated路径。v1.5.0取消的是对比度min(2.0,k)上限，contrast仍round(k,3)并保留非负防御；brightness在k≤2时round3、否则round9。XML整数纯十进制，非整数用:g六位有效数字；计算舍入不等于最终小数位数，UI滑块0..2也不是读取上限。完整公式、越界预烘焙与局限见[参数§7.1](<parameter-mapping.md#texture-bc-current>)及[实现](<../src/brush_converter/kpp/preset_xml.py#L380-L388>)、[序列化](<../src/brush_converter/kpp/preset_xml.py#L453-L462>)。
- legacy旧公式和环境变量在v1.5.0已删除，没有运行时回退。Linear Height旧0.30基线/对比度倒数仅作历史；不得据[早期调研](<归档/photoshop-texture-brightness-contrast-research.md>)恢复。整体版本回退需明确授权并重转，不能把旧缺陷包装成开关。

**v1.6.0：亮度越界区预烘焙（默认处理，源自第五轮历史候选）**：有纹理且未截断亮度绝对值>1才适用，适用才默认开；显式关闭保留原生字段并按[-1,1]截断，不改成另一条烘焙链。C=−50且亮度绝对值>127只是旧实验切片。
转换器把 PS 的亮度/对比度/反相按 PS 8bit 链（C<0 先对比度后亮度 / C≥0 先亮度后对比度）烘进内嵌纹理 PNG，
Krita字段的中性起点与hardMix满覆盖无压感偏置例外见[参数§7.1](<parameter-mapping.md#texture-bc-current>)；历史端到端12/12=0.000%/max0–1。代价：命中区纹理字段失去PS语义（再调会二次叠加），
每 (b,c,invert) 一份纹理资源、旧 `.kpp` 必重转；非命中区仍走 calibrated 闭式解。依据：`docs/验收报告/153-报告.md`。

### 5.4 bundle 结构

`.bundle` 是 ZIP 归档（严格照 `KoResourceBundleManifest.cpp`）：

```
mimetype                      首项、stored 不压缩 = "application/x-krita-resourcebundle"
META-INF/manifest.xml         OpenDocument manifest 命名空间（易错，见 §7）
meta.xml                      meta:meta根；原生字段及meta-userdefined包内追溯
paintoppresets/*.kpp          自包含笔刷预设
```

**v1.3.1 产物加固**（内容短哈希命名 / 覆盖防呆 / 回读校验 / 纹理计数，使用者批准的 4 条）：

- **命名**：`<ABR名>_v<版本>_<8位逻辑内容哈希>.bundle`；短哈希便于定位，不是完整SHA-256或发布凭证。启用本轮元信息链后按v2身份涵盖meta/manifest/KPP，不能只看预设字节判同包。
- **覆盖防呆**：v1.6.0由同一ExportTransaction保护bundle与独立KPP；复用不重写、冲突顺延，旧文件不删不覆盖。最终多文件交付非OS原子性；回滚/清理失败保留清单，不静默宣称全有或全无。
- **回读校验**：v1.3.1旧接口把包校验结果放在ConvertResult.verify；v1.6.0暂存校验失败直接抛[ExportTransactionError](<../src/brush_converter/export_transaction.py#L803-L807>)并携事务清单，CLI退出3。成功交付/复用才取得回读结果；不能只看result.verify推断失败路径。
- **纹理计数**：「含真实纹理」口径 = `Texture/Pattern/Enabled=true` 且内嵌图案非 1×1（`count_real_textures`，GUI 摘要与回归共用）。

<a id="55-原生元信息逐笔标签追溯与身份v160候选"></a>
### 5.5 原生元信息、逐笔标签、追溯与身份（v1.6.0）

- **来源与许可**：[模型字段](<../src/brush_converter/export_metadata.py#L185-L202>)明确区分工具生成信息与素材信息。GUI/显式配置CLI采集包名、说明、可空作者/许可/包版本；来源只取文件名与源字节摘要，不读系统身份，不套项目MIT、不自动日期。默认说明为Converted from Photoshop ABR。模型底层的邮箱/网站/日期不是CLI表单入口，不能虚构相应参数。
- **原生格式证据**：meta根为字面meta:meta；manifest每个paintoppresets条目内的tags/tag才是逐笔关联，包级meta-userdefined tag不能替代。目标已锁Krita5.3.3（git858d352）；文件结构与读取源码证据不等于用户界面/重启已通过。
- **字段显示范围**：按目标源码，说明/作者/许可有详情字段；dc:title不作管理器标题，包版本/来源等为包内扩展。详细追溯存meta-userdefined，meta:name=brushconverter-trace，内容是版本1的JSON；Krita界面不显示这份追溯，重存可能丢扩展字段，应保留原导出包。
- **实际处理**：[追溯结构](<../src/brush_converter/kpp/bundle.py#L501-L536>)记录producer版本/构建时release标记、source文件名/摘要、稳定ID、最终标签、applicable/enabled/actual/limited与代价文本哈希。跳过项不冒充命中；预计计划、内存生成、成功交付和实机效果是四层事实。
- **v2身份**：[身份计算](<../src/brush_converter/kpp/bundle.py#L548-L573>)覆盖meta.xml、manifest和各KPP原字节，包含meta/tag/实际追溯；排除ZIP头时间、输出路径/文件名、自身哈希，不自引用。逻辑身份格式2、追溯JSON版本1、程序1.6.0与用户资源包版本是四个不同概念；已发布v1.6.0沿用冻结构建，包内unreleased为构建时保留值，不据此反推当前未公开发布，也不为改标记重写已发布产物。公开发布状态以正式tag/发布说明为准。
- **升级兼容**：旧包能读，不作为同一v2包复用；只改meta/tag也应形成新身份，KPP可以保持不变。判断复用与格式版本不能只比较旧kpp-only哈希；完整身份与8位文件名后缀分清。

- **复用判定已绑定完整身份（182补充纠偏）**：此前真实CLI复现v2完整身份不同但hash8相同的两包误复用；[事务判重](<../src/brush_converter/export_transaction.py#L663-L689>)现比较实际包与本次构建的`scheme`+完整64位`sha256`，legacy仅沿用旧kpp-only hash8兼容口径，跨scheme/invalid一律不复用。原A/B反例复跑第二份交付为`_2`包、reused=0；真实32位前缀碰撞负例在`tests/test_bundle_identity.py`。定向63/227及后续183全量已覆盖相关用例；185构建、186隐私裁决、187人工条件放行和188发布均已完成，精确边界见§0.6与发布说明。8位文件名后缀不是同包凭证，也不把30项人工通过扩大为六项未验已完成。
- **适用与责任**：本节只覆盖bundle。归一、独立回读与坏包反例是机器证据；2026-11-27人工结果只限明确通过的项目，E4逐笔命中/同名不串、E6重存扩展/追溯及其余四项仍未验。不能把XML有字段、笼统“标签可用”或发布状态替代这些具体目标。

## 6. CLI 与 GUI

- **CLI**（`cli.py`）：info/extract/convert/gui保留；convert新增真实--list-presets、可重复--preset-id、--options-file及--metadata-file。两类UTF-8 JSON分别按export-options/export-metadata的version=1校验；至少一种输出，元信息与--no-bundle冲突会拒绝，不悄悄丢字段。完整示例见[用户指南](<user-guide.md#cli-convert>)，不让普通用户复制内部笔记。
- **GUI**（`gui/`）：QThread传递真实阶段与安全取消请求，输入冻结至finished。按稳定ID保存逐支选项/标签、仅批量操作可见集合，导出确认显示隐藏已选和受限代价。换源/重载重置；关窗不强杀、取消关闭不撤回停止。实际界面验收按2026-11-27的30项通过/六项未验边界记录，不将已验GUI操作推广到所有未验效果。
- **必须显式对齐元信息**：不带metadata-file的CLI沿用兼容口径，不启用新表单/原生标签/追溯；给出空metadata/tags配置才与GUI空表单同口径。两入口字节等价须同源、同选择、同开关、同配置及同程序版本。

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
8. **auto_brush的fade=Hrdn/100，与硬度同向**。v1.5.0默认计算笔刷改用位图；v1.6.0关闭位图化时以独立原生生产分支重新承载，不调用历史研究helper。字段可调不等于PS软边几何等价；真实编辑只按187明确已验范围，六项未验及历史边界不因发布转为通过。
9. **`PressureScatter` 是「散布选项是否启用」的开关，不是「压感控制散布」**。它序列化的是 `isChecked`（`getBool("Pressure" + id)`），压感控制由 `ScatterSensor` 决定。误读它会同时搞错两个方向。
10. **散布量在 `scatterDynamics.jitter`，不在顶层 `Spcn`**。顶层 `Spcn` 是间距。散布量 `jitter` 是 0–1000%。
11. **旧“÷400／PS高斯分布”解释已失效**。当前为 `jitter/200`，依据[任务129报告](<验收报告/129-报告.md>)的分布型统计量，不是单点视觉锚定；1000%端点仍有已量测未修残差，见[参数§10.3](<parameter-mapping.md#residual-current>)。编号保留，不据旧说改回系数。
12. **模板版本陷阱**：预设模板若取自旧 `Basic_tip_default.kpp`（2.2 格式），会缺 Krita 5.x 的一批 `*commonCurve`/`Pressure*` 参数，且布尔/数值要用 `type="internal"` + 纯文本（不是 `type="string"` + CDATA），否则 Krita 读不到、回退默认值、动态「无控制」。

### 7.3 解析实现层面

13. **numpy 2.x 不允许 `arr[a:b] = bytes`**，必须用 `np.frombuffer(buf, dtype, count, offset)`。
14. **16 位原始像素按小端读**。
15. **最后一 8BIM 段可能没有 4 字节对齐填充**，遍历时要容忍轻微越界。
16. **v6.1 跳 47 字节、v6.2 跳 301 字节**（`abr_skipped_bytes`）。
17. **采样笔尖要 `reshape(height, width)`**，否则 1D 数组会被存成「1 像素高的横条」（用户看到的「细白线」bug 根因）。

### 7.4 打包与发布层面

18. **历史删除绕行不再作为维护命令**。曾遇SAFE_DELETE_FAIL_CLOSED只说明当时环境，不得据此批量删除旧dist/build或换Shell绕过拒绝。构建用工作区内全新任务专属dist/work/cache目录，先核对绝对落点；不能隔离就停，旧产物保留。
19. **发布授权与中性身份先于联网**。凭据交已授权认证流程管理，不把token读入日志；认证失败先留原始错误，不先push“试网络”。冻结候选获使用者绑定准确对象的明确批准后才发布，不擅改已发布历史。

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

21. **历史说明：纹理亮度/对比度曾用环境变量开关（`BRUSHCONVERTER_TEXTURE_FORMULA`，v1.5.0 已删除，回退只能 `git revert`）**。
    v1.2.0–v1.4.x 期间默认 `calibrated`、可切 `legacy`（v1.1.2 经验公式）；v1.5.0（任务 135）已把环境变量、legacy 分支与旧用例**物理删除**
    （`preset_xml.py` 引用 13 处 → 0），只剩 calibrated 一条公式，**运行时没有回退路径**。旧文档里的
    `BRUSHCONVERTER_TEXTURE_FORMULA=legacy python cli.py convert …` 命令对当前版本无效；整体回退 = `git revert` + 重转。
    对比度 `min(2.0,k)` 上限也在同一任务删除，当前contrast仍round(k,3)，不表示无舍入原值写入；见§7.9第35条。
    回归用例 `tests/test_texture_formula_switch.py` 现只断言 calibrated 数值与 152 的 B9 记录；公式、A/B 数字与局限见 `docs/parameter-mapping.md` §7.1 与 `docs/验收报告/13-报告.md`。

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
    `Texture/Strength/Value` 改成 `min(1.0, 1.2×depth/100)`；此处是增益来源的历史说明，当前压感0端点写强度比值而非绝对强度，见[参数§7](<parameter-mapping.md#texture-bc-current>)及§10.5已知量纲缺陷，
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
    可选兜底（不是首选）：由使用者退出Krita、备份配置并核对准确文件后重建 `resourcecache.sqlite`；自定义标签/分组可能丢失，不能据此删除资源文件。先排除ABR直读库混淆，见第26条与[排错指南](<troubleshooting.md>)；本轮模型不操作用户配置或重启软件。
    覆盖产物须先正常退出Krita、复制完成后再启动。可选诊断只使用[排错手册的完整隔离步骤](<troubleshooting.md#readonly-diagnosis>)，不再维护PATH默认Python、CMD式变量与未引用占位符的第二套内联命令。

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
    （[转换管线](<../src/brush_converter/convert.py>)中的spacing_basis）；近方形按实际短/长边。默认位图化计算笔刷按 `Spcn/100 × Dmtr × clamp(Rndn/100,0.001,1) / edge` 反算spacing，只有圆度100%才可省圆度因子；关闭后的原生分支直接Spcn/100，见[参数§2](<parameter-mapping.md>)。采样笔尖补方与255填充值是刻意保留的，
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
    对比度取消的是min(2.0,k)上限，仍为max(0,round(k,3))；brightness按k≤2用round3、否则round9。XML整数纯十进制、非整数:g六位有效数字；不是对比度九位或无舍入直写，Krita滑块0..2另属显示限制。
    §7.5 第 21 条是同一事件的历史说明（已同步为「已删除」），两处不再矛盾。

<a id="710-v160-候选新增的坑构建-path压感装置旋转传感器会话恢复纹理预烘焙"></a>
### 7.10 v1.6.0 新增的坑（构建 PATH、压感装置、旋转传感器、会话恢复、纹理预烘焙）

36. **构建会话 PATH 会改变 PyInstaller 产物内容（任务 154）**。若 PATH 含 `<GIT_MINGW64_BIN>`（Git for Windows 自带 OpenSSL 3），
    `_ssl.pyd`/`_hashlib.pyd` 的依赖 `libcrypto-3-x64.dll`（5,122,716 B）+ `libssl-3-x64.dll`（930,460 B）会被收进 `_internal/`：
    目录 +2 文件 / **+6,053,176 B**（zip 约 +2.3 MB），构建日志少 3 条 `Library not found` WARNING；常态 PATH 则相反。
    v1.4.0 与 v1.5.0 的约 6 MB 体积差就是这一条，不是代码/spec 差异（两版 spec 逐字节未变）。**POSIX 风格 PATH（`/c/...`）对 native Python 无效**，
    须用 Windows 风格注入；发布前用 `PyInstaller.depend.bindepend.resolve_library_path('libssl-3-x64.dll')` 自检并记录构建机 PATH 快照。
    不要用两版文件数/体积差反推代码变化。
37. **PS 压感必须走官方脚本 API，且工具状态跨文档保留（任务 143）**。`PathItem.strokePath(ToolType.BRUSH, true)` 的第二参数才是「模拟压力」
    （旧 `ps_export.jsx` 只传一个参数，走不到压力）；Action Manager `strk` 路线 PS 2025 直接报 8800，不要再试。
    模拟压力 = 沿路径的三角波（两端 0、中点 1，线性），可作 0→1 压力源。PS 工具状态（opacity/flow/useTipDynamics）跨文档保留，
    **每张 job 必须显式写死**（首版批曾把 F-9 的 opacity=50 漏进 hardMix 组，重跑才修正）；判复现性用像素差口径，
    不能只看 sha256（跨会话有 1–6 灰度 AA 抖动）。
38. **Krita 的 `RotationSensor` 不能同时写 `fuzzy` 与 `fuzzystroke`（任务 149A）**。Krita 把同一曲线选项下所有加法型传感器的值求和，
    两个 [−1,1] 相加 ⇒ 随机跨度 **2×360°×R**（PS 是 360°×R）。正确写法是只留 1 条 `fuzzy` 曲线、幅度由 `RotationValue=min(1,jitter/50)` 缩放；
    旧产物 j<50 幅度约为 PS 的 2 倍（j=25 +38.20%），改后实机三点 PASS。回归 `tests/test_rotation_source_v160.py` 钉死「加法随机源 == ['fuzzy']」。
39. **Krita会话恢复框是历史自动化事故，不是自动清理授权**。任务148曾因恢复框阻塞；不得据此扫描/移动系统临时文件、自动关闭恢复框或强杀使用者应用。先证实TEMP与运行配置在工作区隔离；不能证实就停。真实恢复、重启和界面操作由使用者集中执行；长任务保留受管理作业编号与超时原文，不用重试覆盖失败。
40. **纹理亮度越界区预烘焙后字段失去 PS 语义（任务 153）**。命中判据 = calibrated 反解的未截断亮度 `|b_k|>1`；命中时把 PS 8bit 链
    （C<0先对比度后亮度／C≥0先亮度后对比度）烘进内嵌PNG；Krita字段及hardMix偏置例外统一见[参数§7.1](<parameter-mapping.md#texture-bc-current>)，资源名/内容哈希/MD5按新字节重算；
    非命中区仍走 calibrated 闭式解。别把命中区的 brightness/contrast 当可双向编辑字段（再调会二次叠加），也别忘了旧 `.kpp` 必重转。
41. **矩阵 verdict 语义按 D142-1（任务 156）**：「可映射」= 精确单位换算/枚举直映、**无经验系数**；「需校准」= 需经验系数、拟合公式、
    范围裁剪，或已知结构性越界；「未量测」**不得单独作为 verdict 依据**（要么给可复算证据、要么给构造性理由）。第五轮据此把 `Mnm`/`Rndn`
    改「可映射」、其余 12 行维持并写清归属——不要用「未量测」搪塞，也不要把「残差归零」等同于「可映射」（有经验系数即需校准）。
42. **Krita 批量会话远比逐张快（129/152）**：一次 Krita 启动可连渲几十张，单张纯渲染约 **0.37s**（129 批量日志的逐张 `elapsed_s`），
    而逐张每张 **11–14s**（144 实测含启动 22.9–24.3s）。批量路线要点：`BRUSHCONVERTER_KRITA` 环境变量必须传（否则回退逐张）；
    批量会话里每个 `.kpp` 的内部 `<Preset name>` 必须唯一（否则 Krita 按内部名去重、只加载 1 个，表现为「预设未找到」）；失败自动回退逐张。
43. **PS 真值文件名必须带纹理后缀（任务 152）**：多纹理实验的 m10 真值文件名不带纹理后缀时，后一批会覆盖前一批 ⇒ 得出「PS(tex2) 真值 vs
    Krita(tex1) 渲染」的错配结论（B9 的 76.680%/57.570% 就是这么来的）。凡「多纹理 × 多模式」批量，真值与 Krita 文件名都必须包含纹理标识；
    更正旧数字时写清作废根因，不要只改数。

## 8. 参考实现与样本

- **GIMP**（GPL）：`app/core/gimpbrush-load.c` + `gimp-utils.c`——ABR v6 笔尖解析与 RLE 解码的权威来源。
- **Krita**（GPL）：`libs/brush/kis_abr_brush_collection.cpp`（ABR 导入）、`kis_circle_mask_generator.cpp`（fade 数学）、`kis_brush.cpp`/`kis_global.h`（角度单位）、`KisScatterOption.cpp`（散布公式）、`KoResourceBundleManifest.cpp`（bundle 格式）。
- **SonyStone/ABR-Viewer**（MIT）：desc 描述符解析参考。
- 以上源码下载在 `research/`（本地保留、不进 git）。

**引用版本规则**：元信息/标签读取合同已核目标Krita5.3.3（git858d352）；2026-11-27人工导入/显示/重启结论仅限187明确通过的项目，E4/E6等六项仍未验。其余GIMP/PS与研究helper按原报告所述范围作历史参考，未登记准确版本仍标“跨版本未验证”，不拿master或本地快照扩大承诺。183历史全量与187人工结果分层引用；后续更新现行参考，不回写一次性验收报告。

## 9. 开发与测试

<a id="maintenance-checks"></a>

### 9.1 先核环境与工作区隔离

从仓库根运行，先按本机已核实解释器设置PowerShell变量$PY；不要直接用PATH上的python，也不要盲装依赖。

```powershell
& $PY -B -c "import sys; print(sys.executable); print(sys.version); assert sys.version_info >= (3,12)"
if ($LASTEXITCODE -ne 0) { throw '解释器核验失败，停止' }
# 维护时替换为本任务的全新工作区内目录；已有目录不删，另起编号。
$RUN = Join-Path (Get-Location).Path '.tmp/maintenance-01'
if (Test-Path -LiteralPath $RUN) { throw '目录已存在，请换一个本任务目录，不覆盖旧证据' }
New-Item -ItemType Directory -Path $RUN | Out-Null
foreach ($name in 'temp','cache','config','data') {
    New-Item -ItemType Directory -Path (Join-Path $RUN $name) | Out-Null
}
$env:TEMP = Join-Path $RUN 'temp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:XDG_CACHE_HOME = Join-Path $RUN 'cache'
$env:XDG_CONFIG_HOME = Join-Path $RUN 'config'
$env:XDG_DATA_HOME = Join-Path $RUN 'data'
$env:PYINSTALLER_CONFIG_DIR = $env:XDG_CONFIG_HOME
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
& $PY -B -c "import pathlib,tempfile; root=pathlib.Path.cwd().resolve(); p=pathlib.Path(tempfile.gettempdir()).resolve(); assert p.is_relative_to(root / '.tmp'); print('临时目录已在工作区内')"
if ($LASTEXITCODE -ne 0) { throw '临时目录未隔离，停止' }
```

TEMP只约束运行期临时文件，不等于所有工具已隔离：先读工具的固定输出路径、配置写入与应用启动行为；不能证明隔离就停。特别是[旧偏差工具](<../tools/measure_deviation.py#L1621-L1654>)即使--analyze也会回写结果与manifest，不能在只读证据目录“顺手复算”。缺工具先核来源、版本与落点，工作区外安装单独授权。

### 9.2 本轮可执行的只读检查


a. 文档合同与公开索引检查均已实现。以下命令只读：现行文档按文件相对路径检查，历史研究报告保留旧解析兼容；一次性验收报告正文不做日常维护扫描，但入链目标/锚点及目录README/INDEX照查。索引必须显式使用 `--tracked-only`，否则会计入本地报告。失败先保留报错、区分可见性与真实断链；只有获准任务主会话可按同参数去掉 `--check` 串行重生成索引，不借此改报告正文、手改生成物或削弱规则凑绿。

```powershell
& $PY -B tools/check_docs.py --check
& $PY -B tools/render_archive_index.py --tracked-only --check
& $PY -B tools/render_archive_index.py --dir docs/研究报告 --md docs/研究报告/INDEX.md --tracked-only --check
& $PY -B tools/render_matrix_md.py --check
& $PY -B tools/check_matrix.py
& $PY -B tools/render_mode_table.py --check
```

b. 日常隐私使用162A已经实现的增量入口；将BASE设为本任务开工前核实的完整提交哈希。无效/非祖先/覆盖不明基线退出4，不能退化成只扫HEAD。

```powershell
& $PY -B tools/privacy_scan.py --since-baseline $BASE --fail-on-block --json (Join-Path $RUN 'privacy.json')
```

显式候选可按已实现--artifact重复指定；二进制/图片只登记待人工，不等于通过。日常增量不能替代候选冻结后的完整工作区与全部历史门。

### 9.3 回归、构建与发布交接（本轮文档任务不执行）

1. **全量回归串行（v1.6.0对应183，已完成）**：本版历史结果为1149/0/0/0与verify_all235/0/0，不冒作本次文档修订新结果。后续回归的pytest指定工作区内basetemp/cache_dir，三层入口与退出码以[验证说明](<验证体系使用说明.md>)为准；通过/失败/跳过分别记录，退出0但有跳过仍未全验。不改阈值、旧基准或一次性报告。
2. **构建阶段（v1.6.0对应185，已完成）**：本版两附件已冻结并发布，不重打同名版本。未来构建先核项目解释器/PyInstaller及[CLI配置](<../brushconverter-cli.spec>)、[GUI配置](<../brushconverter-gui.spec>)、libexpat和PATH（§7.10第36条）；dist/work/cache放全新任务目录，不清旧包。字节数、SHA-256、真实CLI与GUI结果分开留证。
3. **冻结后隐私（v1.6.0对应186/187，已裁决）**：本版源码/全部历史block=0；ZIP仍有14个block及未读覆盖，已按使用者裁决接受并在发布说明披露，人工目检为自述且未附索引，不改写成扫描全绿。未来冻结后仍须完整工作区+全部历史扫描并登记图片/二进制盲区；未处置block不自动通过，漂移使旧门失效，已发布历史不擅改。
4. **批准发布（v1.6.0的187/188已完成）**：本版于2026-11-27按准确对象批准并正式发布；人工30项通过、六项未验条件性放行，不能补写通过。未来发布仍须绑定仓库、提交与两包摘要并复核同一对象；没有批准不push/tag/Release，不force-push或移动旧tag。
5. **仅提交获准业务文件**：显式暂存白名单，不提交内部规划、预写报告或原始证据；提交使用中性身份，不读取系统个人身份。提交后检查署名与逐文件清单，不用git add -f或全目录重新暂存。

测试样本为自备商用ABR，保持只读，不修改、不删除、不提交；新增合成数据只进本任务目录。未来涉及实际GUI、浏览器、Krita/PS操作与最终效果仍须使用者验收；本版沿用已公布的30项通过/六项未验边界，不以文件解析或发布状态冒充未验通过。
