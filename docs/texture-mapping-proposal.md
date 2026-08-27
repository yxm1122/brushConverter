# 纹理（Texture）映射方案

> ✅ **已确认并实现完毕（2026-08-25）**：5 项决策全部按推荐方案执行，
> 代码已落地（`abr/patterns.py`、`mapping.py`、`preset_xml.py`、`convert.py`），
> 26 项测试通过；映射清单见 `docs/parameter-mapping.md` 第 7 节。
>
> 基于真实样本 `tests/海怪笔刷-详情展示的勾线笔套装组18支.abr`（v6.2，20 预设）实测逆向得出。
> 本方案供确认后再进入实现；实现方式沿用项目惯例：对照 Krita 5.x 官方导出样本 + 真实文件实测校准。

## 0. 现状

- 当前 `useTexture` 只进 `_collect_warnings`（提示"纹理"未映射），转换时丢弃纹理。
- 该文件 **9/19 支笔刷**开启纹理（勾线笔×2、针管笔、碳铅、硬铅、软铅、上色笔×2、喷枪等），是最大的未映射项。

## 1. 已确证的事实（本次实测）

### 1.1 ABR `patt` 区段格式（逆向完成，已验证）

`patt` 区段 = 4 个「长度前缀」的 Pattern 记录，逐条可走通且精确对齐到区段末尾（33178816 字节 = 0 误差）：

    Record = u32 len | 记录体（len 字节） | pad 到 4
    记录体 = u32 version(=1) | u32 颜色模式(3=RGB) | 2×i16 (宽,高) |
            u32 名字符数(含结尾 NUL) | 名字 UTF-16BE | u8 名字节数(=36) | UUID(ASCII) |
            VMA 列表: u32 version(=3) | u32 body_len | body {
              u32×4 矩形(top,left,bottom,right) | u32 通道数 | (通道数+2) 个通道块 }
    通道块 = u32 is_written(0=空) | u32 len | u32 depth | u32×4 矩形 |
            u16 pixel_depth | u8 compression(0=RAW,1=RLE) | 数据(len-23)

实测 4 个纹理（**按 UUID 关联，不能按名字**）：

| UUID | 名字 | 尺寸 | 通道 | 数据 |
|------|------|------|------|------|
| 438c2948-d232-11e5-b988-9ff33e1af9cd | R. Melentyev's Art Texture | 1×1 | RGB×3 | 纯灰 (236,236,236) 中性纹理 |
| 66e2987f-d47b-fa49-9e36-3ffa8b78533a | Shape 2.png | 1920×1920 | RGB×3 | 灰度纸纹，md5 fe85154f… |
| f648c44c-5189-b14e-8ef2-926477d0bbe7 | Shape 2.png | 1920×1920 | RGB×3 | 灰度纸纹，md5 546cff5b… |
| 69d92381-cf86-a54b-9d04-7f0fc2d9345b | Shape 2.png | 1920×1920 | RGB×3 | 灰度纸纹，md5 303001b2… |

- 3 张 "Shape 2.png" 内容不同（md5 各异），必须用 `Txtr.Idnt`(UUID) 匹配。
- 本样本全部为 compression=0（RAW）。实现时仍要支持 RLE(1)（PSD 版 RLE：每行 u16 字节数 + PackBits 数据）。

### 1.2 笔刷 desc 里的纹理参数（9 支实测）

| ABR 键 | 类型/取值 | 含义 |
|--------|-----------|------|
| `useTexture` | bool True | 启用纹理 |
| `Txtr` | Objc{Nm: TEXT, Idnt: TEXT} | 纹理引用：名字 + UUID（按 Idnt 找 patt 记录） |
| `textureScale` | UntF #Prc（20~73） | 纹理缩放 % |
| `textureDepth` | UntF #Prc（4~55） | 纹理深度 % |
| `minimumDepth` | UntF #Prc（0） | 深度最小值 |
| `textureDepthDynamics` | Objc{bVTy=2, fStp, jitter=0, Mnm=0} | 深度动态（bVTy=2 压感） |
| `textureBlendMode` | enum BlnM，值 `linearHeight` | 混合模式（Linear Height） |
| `InvT` | bool（含 True） | 反相 |
| `textureBrightness` | long（-31~40） | 亮度（-100~100） |
| `textureContrast` | long（-50~6） | 对比度（-100~100） |
| `TxtC` | bool True | "Texture Each Tip"（Krita 无对应） |
| `interpretation` / `protectTexture` | bool | Krita 无直接对应（忽略/标注） |

### 1.3 Krita 5.x 纹理预设 XML（本地官方样本 research/kpp_samples/ref_5.0.xml 已确证）

- **纹理资源内嵌**：与笔尖一样放进 `<resources>`，`type="patterns"`，PNG base64：

    <resource name="test_pattern.png" filename="test_pattern.png"
              type="patterns" md5sum="dc4e9099acb7c3cd33293a48f75c6ff7">
      <![CDATA[iVBORw0KGgo...]]>
    </resource>

- 参数（Krita 5.0 导出样本逐项摘录，internal 为纯文本、string 为 CDATA）：

    Texture/Pattern/Enabled         internal  true
    Texture/Pattern/Name            string    <文件名>
    Texture/Pattern/PatternFileName string    <文件名>
    Texture/Pattern/PatternMD5Sum   string    <PNG 的 md5 hex>      <- 5.0 后新增，权威字段
    Texture/Pattern/PatternMD5      string    （5.0 曾写原始二进制=bug；新版本可留空/省略）
    Texture/Pattern/Scale           internal  1                     <- 0.01~10，1=100%
    Texture/Pattern/Brightness      internal  0
    Texture/Pattern/Contrast        internal  1
    Texture/Pattern/CutoffLeft/Right/CutoffPolicy internal 0/255/0
    Texture/Pattern/Invert          internal  false
    Texture/Pattern/NeutralPoint    internal  0.5
    Texture/Pattern/OffsetX/Y       internal  0 / 0
    Texture/Pattern/isRandomOffsetX/Y internal false / false
    Texture/Pattern/MaximumOffsetX/Y internal  2 / 2
    Texture/Pattern/TexturingMode   internal  1
    Texture/Pattern/UseSoftTexturing internal  false
    Texture/Strength/Sensor         string    <!DOCTYPE params> <params id="pressure"/>
    Texture/Strength/UseCurve       internal  true
    Texture/Strength/UseSameCurve   internal  true
    Texture/Strength/Value          internal  1                     <- 0..1（100%）
    Texture/Strength/commonCurve    string    0,0;1,1;
    Texture/Strength/curveMode      internal  0
    PressureTexture/Strength/       internal  true/false            <- 压感开关

- 默认参数模板（`DEFAULT_PARAMS`）需补充上述 `Texture/…` 项（非纹理笔刷 `Enabled=false`，参照 ref_勾线笔.xml）。

## 2. 映射方案（ABR → Krita）

| ABR 键 | → Krita 参数 | 公式 | 状态 |
|--------|--------------|------|------|
| `useTexture` | `Texture/Pattern/Enabled` | true | ✅ |
| `Txtr.Idnt` | 资源查找 | patt 记录按 UUID 匹配，PNG 内嵌 type="patterns" | ✅ |
| `Txtr.Nm` + UUID | `Name` / `PatternFileName` | 安全文件名（如 `tex_66e2987f.png`） | ✅ |
| — | `PatternMD5Sum` | PNG 字节 md5 hex（`PatternMD5` 留空，绕开 Krita 5.0 写二进制 bug） | ✅ |
| `textureScale` | `Texture/Pattern/Scale` | `textureScale/100`，clamp [0.01, 10] | ✅（数值待实测校准） |
| `textureDepth` | `Texture/Strength/Value` | `textureDepth/100` | ✅ |
| `textureDepthDynamics.bVTy==2` | `PressureTexture/Strength/` = true + `Texture/Strength/UseCurve` = true | 压感→深度（`Mnm` 为最小值） | ✅ |
| `textureDepthDynamics.bVTy==0` | `PressureTexture/Strength/` = true + `Texture/Strength/UseCurve` = false | 效果强度开启、无传感器，`Value`=`textureDepth/100` 恒定 | ✅ |
| `textureDepthDynamics.Mnm` | `Texture/Strength/commonCurve` | `0,{Mnm/100};1,1;`（仅 `bVTy==2`） | ✅ |
| `InvT` | `Texture/Pattern/Invert` | 直接 | ✅ |
| `textureBlendMode` | `Texture/Pattern/TexturingMode` | **见下表** | ✅/❓ |
| `textureBrightness` | `Texture/Pattern/Brightness` | 所有模式统一 `round(0.10-v/250,2)`；clamp [-1,1] | ✅ |
| `textureContrast` | `Texture/Pattern/Contrast` | 所有模式统一使用 PS 中心因子；+100 极限按 Krita UI clamp [0,2] | ✅ |
| `TxtC` / `interpretation` / `protectTexture` | — | Krita 无对应，忽略并保留轻量警告 | ⚠️ |

### 2.1 混合模式映射表（Photoshop → Krita TexturingMode）

| PS 值 | 含义 | Krita 模式 | 预期数值 |
|-------|------|-----------|----------|
| `Hght` | Height (Photoshop) | Height (Photoshop) | 14（测试用.abr 实测） |
| `linearHeight` | Linear Height (Photoshop) | Linear Height (Photoshop) | 15（KisTextureOptionData enum） |
| `Mul ` | Multiply | Multiply | 0 |
| `Scrn` | Screen | Screen | 2 |
| `Sbtr` | Subtract | Subtract | 1 |
| `Ovld` | Overlay | —（无直接对应，回退 Multiply + 警告） | — |
| 其他/未知 | — | 回退 Multiply(0) + 警告 | — |

> 已根据 Krita `KisTextureOptionData.h` 核对完整枚举：Photoshop 专用模式为 Hard Mix=10、Hard Mix Softer=11、Height=14、Linear Height=15。

### 2.2 亮/对比度校准策略（沿用散布 ÷400 的做法）

- 已按 Krita `KisTextureMaskInfo::recalculateMask()` 源码与 Photoshop 定量测试改为最终合成匹配公式：所有模式统一——brightness=`round(0.10-v/250,2)`，contrast 负值=`1+v/100`、正值=`1/(1-v/100)`（round 两位并 clamp [0,2]）。Linear Height (Photoshop) 的特殊映射（0.30 基线/对比度倒数）实测效果不好，2026-08-27 确认取消。
- 中性值必须保守：PS 0/0 → Krita 0/1（Krita 官方样本的中性默认）。

## 3. 实现计划（文件级）

1. **新增 `src/brush_converter/abr/patterns.py`**：`parse_patterns(patt_bytes) -> dict[uuid, PatternTexture]`
   - PatternTexture：name、uuid、image(np.ndarray, RGB 或 L)、width、height、color_mode。
   - 通道解压：RAW 直拷；RLE 用「每行 u16 长度 + PackBits」（PSD 版，参考 psd-tools compression）。
   - 自校验：记录游标必须精确到区段末尾；非 3/1 颜色模式、未知 version 抛明确错误。
2. **`abr/__init__.py`**：导出 patterns；`AbrFile` 加 `patterns` 字段（`parse` 时若有 patt 则解析，惰性/容错）。
3. **`mapping.py`**：
   - 新增 `TextureSettings` dataclass（name/uuid/scale/invert/brightness/contrast/depth/depth_min/pressure/blend_mode/image）。
   - `BrushPreset.texture`；`map_presets` 对 `useTexture` 预设按 `Txtr.Idnt` 查 patt 并填充。
   - `_collect_warnings`：命中纹理且映射成功 → 去掉"纹理"警告；纹理 UUID 找不到 / 混合模式回退 / protectTexture → 保留具体警告。
4. **`kpp/preset_xml.py`**：
   - `DEFAULT_PARAMS` 补 `Texture/…`、`Texture/Strength/…` 默认项（Enabled=false）。
   - `_resources_xml` 支持多资源（笔尖 PNG + 纹理 PNG）。
   - `build_preset_xml` 增 `texture` 参数，按第 2 节写参数。
5. **`convert.py`**：`_render_preset` 生成纹理 PNG（RGB/L → PNG bytes）并传给 XML；bundle 无需改动（纹理在 kpp 内嵌）。
6. **GUI**：警告文案随 mapping 更新（无 UI 结构改动）。
7. **tests**：
   - `tests/test_patterns.py`：真实样本 4 纹理（名字/UUID/尺寸/md5/1×1 与 1920×1920），RLE 解码单元测试（造一个 PackBits 样本）。
   - mapping：`useTexture` 预设 texture 字段正确；警告不再含"纹理"。
   - preset_xml：生成 XML 含 `<resource type="patterns">`、`PatternMD5Sum`、`Enabled=true`、Strength 曲线。
   - convert 端到端：bundle md5 一致（沿用现有测试模式）。
8. **docs**：`parameter-mapping.md` 第 6 节移除纹理、新增纹理映射表；`developer-guide.md` 补 patt 格式与纹理嵌入说明。

## 4. 产出与验证

- 转换后：9 支纹理笔刷的 .kpp 内含 1920×1920 纹理 PNG（体积会增大，预计每支 +1~3MB）；`.bundle` 一键导入。
- **需你在 Krita 实测**：纹理是否出现并生效、缩放/深度/反相/亮度/对比度/混合模式是否接近 PS 原笔刷；1×1 中性纹理（勾线笔）在 Krita 中应表现为近似无纹理效果（符合 PS 中的表现）。

## 5. 待你确认的决策

1. **纹理资源嵌入格式**：PNG 内嵌（type="patterns"，与 Krita 5.0 官方样本一致）——推荐；还是转成 GIMP .pat？
2. **混合模式**：按 2.1 表映射 + 未知回退 Multiply 并警告——推荐；还是未知一律 Multiply 不警告？
3. **亮度/对比度公式**：按 2.2 先行映射、Krita 实测后再校准——推荐；还是本版先不映射亮/对比度（只用默认）？
4. **1×1 中性纹理**（勾线笔×2 用的 R. Melentyev's Art Texture）：保留并启用——推荐（忠实还原）；还是跳过（这两支等于无纹理）？
5. **`protectTexture`/`TxtC` 等**：忽略 + 保留轻量警告——推荐；还是静默忽略？
