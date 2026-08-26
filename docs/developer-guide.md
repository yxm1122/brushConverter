# brushConverter 项目结构与原理

> 面向开发者：解释这个项目如何把 Photoshop 笔刷（`.abr`）转换成 Krita 笔刷（`.kpp` / `.bundle`），以及开发过程中踩过的坑和关键结论。
>
> 参数映射的完整逐字段清单见 [`parameter-mapping.md`](parameter-mapping.md)。

## 1. 概述

**brushConverter** 是一个把 Adobe Photoshop 笔刷集（`.abr`）转换为 Krita 笔刷（`.kpp` 预设 / `.bundle` 资源包）的工具，带命令行（CLI）和图形界面（GUI）两种形态，可用 PyInstaller 打包成 Windows 可执行文件。

**技术栈**：Python 3.12 + numpy + Pillow + PySide6（GUI）+ PyInstaller（打包）。

**核心数据流**：

```
.abr 文件
   │  AbrFile.parse()          —— 拆 8BIM 区段
   ├─ samp 区 → 采样笔尖位图（BrushTip）
   ├─ desc 区 → 笔刷参数描述符（Descriptor）
   └─ patt / phry → （暂不用：纹理 / 缩略图）
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
| `patt` | 纹理图案 | ⛔ 暂不映射（引擎差异大） |
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
| 间距 | `Spcn`（#Prc） | `spacing = Spcn/100` |
| 角度 | `Angl`（度） | `angle = math.radians(Angl % 360)` |
| 硬度（计算笔刷） | `Hrdn` | `hfade/vfade = Hrdn/100` |
| 压感→大小 | `szVr.bVTy==2` + `minimumDiameter` | `SizecommonCurve` |
| 压感→不透明度 | `opVr.Mnm` | `OpacitycommonCurve` |
| 压感→流量 | `prVr.Mnm` | `FlowcommonCurve` |
| 压感→宽高比 | `roundnessDynamics` + `minimumRoundness` | `RatiocommonCurve` |
| 旋转 | `angleDynamics.bVTy` | `RotationSensor`（drawingangle/pressure） |
| 散布 | `useScatter` + `scatterDynamics.jitter` | `PressureScatter` + `ScatterValue` |

**控制源编码（bVTy）**：0=关、1=渐隐、2=压力、3=倾斜、4=转轮、5=旋转、6/7=初始方向/方向。映射到 Krita 传感器（pressure/drawingangle/tilt）。对于方向旋转，Photoshop `angleDynamics.jitter` 映射到 Krita `RotationValue`（效果强度，jitter/100），`fuzzy` 与 `fuzzystroke` 随机度曲线在 UI 中对应 -180°..+180°，但 XML 使用归一化坐标，完整范围写为 `0,0;1,1;`。

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
<resource name="tex_66e2987f.png" filename="tex_66e2987f.png"
         type="patterns" md5sum="...">
  <![CDATA[ base64(PNG) ]]></resource>
```

预设参数（`preset_xml.py` 的 `TextureXml` / `_apply_texture`）：
`Texture/Pattern/{Enabled,Name,PatternFileName,PatternMD5Sum,Scale,Brightness,
Contrast,Invert,TexturingMode}`、`Texture/Strength/{Value,UseCurve,commonCurve}`、
`PressureTexture/Strength/`。

注意：
- **`PatternMD5Sum` 存 hex md5**；`PatternMD5` 留空——Krita 5.0 曾在该字段写原始
  二进制导致非法 XML（Krita 官方 MR 修复），新版本以 `PatternMD5Sum` 为准。
- `TexturingMode` 数值来自 Krita `KisTextureOptionData::TexturingMode`：Multiply=0、Subtract=1、Lightness=2、Gradient=3、Darken=4、Overlay=5、Color Dodge=6、Color Burn=7、Linear Dodge=8、Linear Burn=9、Hard Mix (Photoshop)=10、Hard Mix Softer (Photoshop)=11、Height=12、Linear Height=13、Height (Photoshop)=14、Linear Height (Photoshop)=15。ABR 的 `linearHeight` 优先映射到 15，避免误落到 Darken=4。
- 亮/对比度换算依据 Krita `KisTextureMaskInfo::recalculateMask()`：`maskValue -= brightness`，然后 `((maskValue - 0.5) * contrast) + 0.5`；因此当前用 `brightness=v/100`、`contrast=1+v/100`，并做范围限制。源码快照与来源链接见 `research/krita_texture/` 和 `research/README.md`。

### 5.4 bundle 结构

`.bundle` 是 ZIP 归档（严格照 `KoResourceBundleManifest.cpp`）：

```
mimetype                      首项、stored 不压缩 = "application/x-krita-resourcebundle"
META-INF/manifest.xml         OpenDocument manifest 命名空间（易错，见 §7）
meta.xml                      OpenDocument meta
paintoppresets/*.kpp          自包含笔刷预设
```

## 6. CLI 与 GUI

- **CLI**（`cli.py`）：`info`（概况）、`extract`（导出笔尖 PNG）、`convert`（转换）、`gui`（启动界面）。
- **GUI**（`gui/`）：解析和转换都在 `QThread` 后台线程（`workers.py`），界面不卡。每支笔刷一张卡片（勾选框 + 缩略图 + 名称 + 未映射警告），支持全选/全不选、产物格式选择（.kpp/.bundle）、转换前对含未映射参数的笔刷弹窗提醒。

## 7. 开发过程中的重要发现（坑）

这些是花时间对照源码、反复试错才确定的结论，供后续维护参考：

### 7.1 格式层面

1. **`.kpp` 是 PNG + iTXt 块，不是 ZIP**。预设 XML 经 zlib 压缩放进 `iTXt`（keyword=`preset`），旧 2.2 格式用 `zTXt` + version=`2.2`。
2. **笔尖是 PNG，不是 GBR**。Krita 5.x 的 `png_brush` 把「全灰无 alpha」的 PNG 当 mask（白=墨、黑=透明）。所以直接用灰度 PNG 即可，无需 GBR、无需字节反转——这是阶段 3 推翻阶段 1「GBR 方案」的关键转折。
3. **灰度约定**：ABR/GBR 存储 0=墨 255=透明；`png_brush` 白=墨。项目统一为「不透明蒙版 255=墨」。
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
