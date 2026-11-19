# Krita 5.3.3 能力表（任务 22）

> 数据文件：`docs/数据/krita_caps.json`（每项 `param` / `krita_path` / `type` / `range_or_enum` / `source`）
> 证据来源：**已安装二进制的 git 提交 `858d352`** 的官方源码（不是 master、不是本地副本）；每条都带 `blob/<commit>/<文件>#Lx-Ly` 行号
> 抓取日期：2026-11-19 ｜ 本表由源码文本自动定位行号，可逐条复核

## 一、结论

| 指标 | 数值 |
|---|---:|
| 能力表条目总数 | **45** |
| 其中 纹理参数（scope=texture） | 32 |
| 其中 曲线/传感器机制（scope=mechanism） | 2 |
| 其中 相邻参数（scope=adjacent，供 23/26–28） | 11 |
| **TexturingMode 成员数 / 数值上限** | **16 个 / 上限 15**（0..15 连续） |

**三个数字（与 `docs/研究报告/abr-键实测.md` 一致，可一条命令重算）：ABR 实测键总数 91；仓库解析器已识别 39；Krita 侧能力表条目数 45。**

**四条硬结论**（都可能造成「结构性不可映射」）：

1. **TexturingMode 是 16 个成员的连续枚举，上限 15**：`MULTIPLY(0)` … `LINEAR_HEIGHT_PHOTOSHOP(15)`。12/13（`HEIGHT`/`LINEAR_HEIGHT`）是 Krita 自有算子，14/15 才是 Photoshop 版算子；本项目的转换必须用 14/15（v1.2.1 已按 10·s vs 12·q 修强度）。
2. **对比度 `Contrast` 的 UI/有效范围是 0..2**：PS 对比度 > 50 时 `k=1/(1-C/100) > 2`，Krita 无法表达（与 `docs/parameter-mapping.md` §7.1 局限 1 一致）。
3. **亮度 `Brightness` 范围 -1..1、强度 `Texture/Strength/Value` 范围 0..1**：PS 亮度 ±150、PS 深度 >83% 都会越界（局限 2、3）。
4. **缩放 `Scale` 的 UI 滑块是 0.0..2.0（100%..200%）**，而 ABR 实测纹理缩放到 280%（见 `docs/研究报告/abr-键实测.md` §九）。存入的 double 读取时无 clamp，所以「Krita 能读 2.8」与「UI 只能拉到 2.0」并存——这条要人工在 Krita 里实测确认（见 §九）。

## 二、版本与证据链

| 项 | 值 | 取数方式 |
|---|---|---|
| Krita 版本 | **5.3.3 (git 858d352)** | `'<LOCAL_APP_DIR>/Krita (x64)/bin/krita.com' --version` → `krita 5.3.3 (git 858d352)` |
| exe 元数据 | FileVersion / ProductVersion = `5.3.3 (git 858d352)` | `(Get-Item '...\krita.exe').VersionInfo` |
| 源码提交 | `858d352`（与 exe 的 git 描述一致） | 由上面的版本串得到，未用 master 分支 |
| 源码地址模板 | `https://invent.kde.org/graphics/krita/-/blob/858d352/<路径>#Lx-Ly` | 浏览器可直接打开核对 |
| 抓取日期 | 2026-11-19 | 本次执行时间 |

> 说明：仓库 `research/` 下已有 `KisTextureOptionData.h/.cpp`、`KisTextureMaskInfo.cpp` 等本地副本（任务 07/09 抄录），本表**不以本地副本为最终依据**，而是重新按 858d352 拉取官方源码定位行号；两者内容一致（本地副本用于速查）。

## 三、`TexturingMode` 完整成员清单（任务 22 重点）

来源：KisTextureOptionData.h:L29-45<br>KisTextureOptionData.cpp:L24-24<br>kis_texture_chooser.cpp:L47-79（枚举定义、读写键、UI 下拉框、引擎 flag 四处交叉印证）

| 值 | 枚举成员 | Photoshop 对应 | 说明 |
|---:|---|---|---|
| 0 | `MULTIPLY` | PS `Mul ` / `Mltp` | 基础模式 |
| 1 | `SUBTRACT` | PS `Sbtr` | 基础模式 |
| 2 | `LIGHTNESS` | PS `Lght`（Lightness，Krita 原生枚举中的同名项） | 需引擎 flag，像素笔刷可用 |
| 3 | `GRADIENT` | PS `Grdn`（Gradient，Krita 原生枚举中的同名项） | 需引擎 flag，像素笔刷可用 |
| 4 | `DARKEN` | PS `Drkn` | 普通混合模式 |
| 5 | `OVERLAY` | PS `Ovrl` | 普通混合模式 |
| 6 | `COLOR_DODGE` | PS `CDdg` | 普通混合模式 |
| 7 | `COLOR_BURN` | PS `CBrn` | 普通混合模式 |
| 8 | `LINEAR_DODGE` | PS `LDdg` | 普通混合模式 |
| 9 | `LINEAR_BURN` | PS `LBur` | 普通混合模式 |
| 10 | `HARD_MIX_PHOTOSHOP` | PS `HdMx` / `hardMix` | 普通混合模式 |
| 11 | `HARD_MIX_SOFTER_PHOTOSHOP` | PS `HdMxS` | 普通混合模式 |
| 12 | `HEIGHT` | Krita 自有（非 Photoshop 版算子），PS 无同名列 | 高度系：投影算子不同，v1.2.1 高度系强度需 ×1.2 |
| 13 | `LINEAR_HEIGHT` | Krita 自有（非 Photoshop 版算子），PS 无同名列 | 高度系：投影算子不同，v1.2.1 高度系强度需 ×1.2 |
| 14 | `HEIGHT_PHOTOSHOP` | PS `Hght` / `height`（Photoshop 版算子） | 高度系：投影算子不同，v1.2.1 高度系强度需 ×1.2 |
| 15 | `LINEAR_HEIGHT_PHOTOSHOP` | PS `linearHeight`（Photoshop 版算子） | 高度系：投影算子不同，v1.2.1 高度系强度需 ×1.2 |

**枚举之外的关键事实**：

- 存储默认值 `0`（MULTIPLY），但 UI 打开时默认选中索引 `1`（SUBTRACT）——**两者不一致是 Krita 的既有行为**，读写预设不受影响（预设里写什么就用什么）。
- LIGHTNESS(2)/GRADIENT(3) 只在笔刷引擎传入 SupportsLightnessMode/SupportsGradientMode 时出现在下拉框；本项目使用的像素笔刷引擎在 kis_brushop_settings_widget.cpp:77 传入这两个 flag（KisBrushOpResources.cpp:51），因此 2/3 对本项目可用；不传 flag 的引擎会把 2/3 替换成 SUBTRACT(1)
- 模式 2/3 在其它笔刷引擎可能不可用（会被替换成 1），任务 23/31 若发现数据里出现 2/3，要注明「本项目像素引擎可用」。

## 四、纹理参数全表

共 32 条 = `Texture/Pattern/*` 23 + `Texture/Strength/*` 7 + `PressureTexture/Strength/` 启用位 1 + 引擎 flag 1（含 5 个图案引用键、2 个「读取时被刻意跳过」的 MaximumOffset 键）。

| 参数 | Krita 路径 | 类型 | 默认 / 范围 | 来源（文件:行） |
|---|---|---|---|---|
| 纹理启用开关 | `Texture/Pattern/Enabled` | bool | 默认 false | KisTextureOptionData.cpp:L15-15 |
| 纹理缩放倍数 | `Texture/Pattern/Scale` | double | 默认 1；UI 滑块 0..2；步长 0.01；无（getDouble 原样读取） | KisTextureOptionData.cpp:L16-16<br>kis_texture_chooser.cpp:L21-26 |
| 纹理亮度（Krita 归一化 -1..1） | `Texture/Pattern/Brightness` | double | 默认 0；UI 滑块 -1..1；步长 0.01；无（getDouble 原样读取）；有效值在掩码计算里 clamp 到 0..1 | KisTextureOptionData.cpp:L17-17<br>kis_texture_chooser.cpp:L28-28<br>KisTextureMaskInfo.cpp:L192-192 |
| 纹理对比度（Krita 归一化 0..2，1=不变） | `Texture/Pattern/Contrast` | double | 默认 1；UI 滑块 0..2；步长 0.01；无（getDouble 原样读取） | KisTextureOptionData.cpp:L18-18<br>kis_texture_chooser.cpp:L33-33<br>KisTextureMaskInfo.cpp:L194-194 |
| 中性点（灰点，仅 Lightness/Gradient 模式用） | `Texture/Pattern/NeutralPoint` | double | 默认 0.5；UI 滑块 0..1；步长 0.01 | KisTextureOptionData.cpp:L19-19<br>kis_texture_chooser.cpp:L37-37 |
| 纹理 X 偏移（px） | `Texture/Pattern/OffsetX` | int | 默认 0；上限 图案宽/2（按图案尺寸动态设置） | KisTextureOptionData.cpp:L20-20<br>KisTextureOptionModel.cpp:L82-82 |
| 纹理 Y 偏移（px） | `Texture/Pattern/OffsetY` | int | 默认 0；上限 图案高/2（按图案尺寸动态设置） | KisTextureOptionData.cpp:L21-21<br>KisTextureOptionModel.cpp:L83-83 |
| 随机 X 偏移 | `Texture/Pattern/isRandomOffsetX` | bool | 默认 false | KisTextureOptionData.cpp:L22-22 |
| 随机 Y 偏移 | `Texture/Pattern/isRandomOffsetY` | bool | 默认 false | KisTextureOptionData.cpp:L23-23 |
| 纹理混合模式枚举（任务 22 重点） | `Texture/Pattern/TexturingMode` | int enum | 枚举 0..15（16 个成员） | KisTextureOptionData.h:L29-45<br>KisTextureOptionData.cpp:L24-24<br>kis_texture_chooser.cpp:L47-79 |
| 软纹理开关 | `Texture/Pattern/UseSoftTexturing` | bool | 默认 false | KisTextureOptionData.cpp:L25-25 |
| 截断策略 | `Texture/Pattern/CutoffPolicy` | int enum | 枚举 0..2（3 个成员） | KisTextureOptionData.cpp:L26-26<br>kis_texture_chooser.cpp:L82-82 |
| 截断左端（0..255） | `Texture/Pattern/CutoffLeft` | int | 默认 0；范围 0..255 | KisTextureOptionData.cpp:L27-27 |
| 截断右端（0..255） | `Texture/Pattern/CutoffRight` | int | 默认 255；范围 0..255 | KisTextureOptionData.cpp:L28-28 |
| 纹理反相 | `Texture/Pattern/Invert` | bool | 默认 false | KisTextureOptionData.cpp:L29-29 |
| 橡皮擦时自动反相 | `Texture/Pattern/AutoInvertOnErase` | bool | 默认 false | KisTextureOptionData.cpp:L30-30 |
| 图案 md5 的 base64（旧格式字段） | `Texture/Pattern/PatternMD5` | string(base64) | 默认 "" | KisEmbeddedTextureData.cpp:L85-85 |
| 图案 md5 的 hex（5.1+ 查找键） | `Texture/Pattern/PatternMD5Sum` | string(hex md5) | 默认 "" | KisEmbeddedTextureData.cpp:L86-86 |
| 图案文件名（取 basename） | `Texture/Pattern/PatternFileName` | string | 默认 "" | KisEmbeddedTextureData.cpp:L87-87 |
| 图案资源名 | `Texture/Pattern/Name` | string | 默认 "" | KisEmbeddedTextureData.cpp:L88-88 |
| 内嵌图案 base64（嵌入纹理唯一可靠来源） | `Texture/Pattern/Pattern` | string(base64 PNG) | 默认 "" | KisEmbeddedTextureData.cpp:L89-89 |
| 最大 X 偏移（读取被刻意跳过） | `Texture/Pattern/MaximumOffsetX` | int | 默认 2；read 的 TODO 注释明确不加载，避免覆盖模型算出的值；读取时跳过 | KisTextureOptionData.cpp:L32-32 |
| 最大 Y 偏移（读取被刻意跳过） | `Texture/Pattern/MaximumOffsetY` | int | 默认 2；读取时跳过 | KisTextureOptionData.cpp:L32-32 |
| 纹理强度「效果强度」启用开关（isChecked，不是压感） | `PressureTexture/Strength/` | bool | 默认 false；true = 启用强度曲线 | KisKritaSensorPack.cpp:L120-120 |
| 强度控制源（XML 传感器定义） | `Texture/Strength/Sensor` | string(XML) | 默认 ""；格式：<params><Sensor id="pressure" .../></params>；多传感器根节点 id="sensorslist" | KisKritaSensorPack.cpp:L136-136 |
| 是否启用强度曲线 | `Texture/Strength/UseCurve` | bool | 默认 true | KisKritaSensorPack.cpp:L231-231 |
| 所有传感器共用同一条曲线 | `Texture/Strength/UseSameCurve` | bool | 默认 true | KisKritaSensorPack.cpp:L188-188 |
| 强度值（0..1） | `Texture/Strength/Value` | double | 默认 1；范围 0..1 | KisKritaSensorPack.cpp:L225-225<br>KisCurveOptionDataCommon.h:L57-57 |
| 共用曲线字符串 | `Texture/Strength/commonCurve` | string | 默认 "0,0;1,1;" | KisKritaSensorPack.cpp:L205-205 |
| 曲线模式 | `Texture/Strength/curveMode` | int | 默认 0 | KisKritaSensorPack.cpp:L232-232 |
| 强度选项 id（前缀形式） | `Texture/Strength/` | KoID | KisStrengthOptionData 的 KoID = "Texture/Strength/"；键名 = id + Value/Sensor/UseCurve/commonCurve/curveMode，启用位 = Pressure+id | KisStandardOptionData.h:L113-117 |
| 像素笔刷引擎的纹理模式能力（引擎 flags） | `Texture/Pattern/TexturingMode（引擎 flags）` | flags | 传入 SupportsLightnessMode + SupportsGradientMode 两个 flag，决定枚举 2/3 是否出现在下拉框 | kis_brushop_settings_widget.cpp:L77-77<br>KisBrushOpResources.cpp:L51-51 |

## 五、纹理强度曲线（`Texture/Strength/` 前缀）

`Texture/Strength/` 是本项目 `Texture/Strength/Value` 那一组键的 id 前缀；键名规则 = `id + Value/Sensor/UseCurve/UseSameCurve/commonCurve/curveMode`，启用位 = `Pressure + id`。

| 参数 | Krita 路径 | 类型 | 默认 / 范围 | 来源 |
|---|---|---|---|---|
| 纹理强度「效果强度」启用开关（isChecked，不是压感） | `PressureTexture/Strength/` | bool | 默认 false；true = 启用强度曲线 | KisKritaSensorPack.cpp:L120-120 |
| 强度控制源（XML 传感器定义） | `Texture/Strength/Sensor` | string(XML) | 默认 ""；格式：<params><Sensor id="pressure" .../></params>；多传感器根节点 id="sensorslist" | KisKritaSensorPack.cpp:L136-136 |
| 是否启用强度曲线 | `Texture/Strength/UseCurve` | bool | 默认 true | KisKritaSensorPack.cpp:L231-231 |
| 所有传感器共用同一条曲线 | `Texture/Strength/UseSameCurve` | bool | 默认 true | KisKritaSensorPack.cpp:L188-188 |
| 强度值（0..1） | `Texture/Strength/Value` | double | 默认 1；范围 0..1 | KisKritaSensorPack.cpp:L225-225<br>KisCurveOptionDataCommon.h:L57-57 |
| 共用曲线字符串 | `Texture/Strength/commonCurve` | string | 默认 "0,0;1,1;" | KisKritaSensorPack.cpp:L205-205 |
| 曲线模式 | `Texture/Strength/curveMode` | int | 默认 0 | KisKritaSensorPack.cpp:L232-232 |

## 六、曲线/传感器通用机制

| 参数 | Krita 路径 | 类型 | 说明 | 来源 |
|---|---|---|---|---|
| 曲线选项通用键组 | `<Id>Value / <Id>Sensor / <Id>UseCurve / <Id>UseSameCurve / <Id>commonCurve / <Id>curveMode / Pressure<Id>` | 键组 | <Id>Value 缺省 = strengthMaxValue（构造参数 valueRange.second） | KisCurveOptionData.h:L27-27<br>KisKritaSensorPack.cpp:L136-232 |
| 传感器 id 全集 | `sensor XML @id` | string enum | ascension/declination = 倾斜方向/倾角（序列化名保持旧预设兼容）；sensorslist 为组合容器；17 个取值 | KisDynamicSensorIds.h:L11-35 |

### 6.1 可用传感器 id 全集

共 17 个（含组合容器 `sensorslist`）：

| sensor id | 含义 |
|---|---|
| `pressure` | 压感 |
| `pressurein` | 压感（输入） |
| `tangentialpressure` | 喷枪滚轮 |
| `drawingangle` | 笔迹方向 |
| `xtilt` | X 倾斜 |
| `ytilt` | Y 倾斜 |
| `ascension` | 倾斜方向（序列化名） |
| `declination` | 倾斜倾角（序列化名） |
| `rotation` | 设备旋转 |
| `fuzzy` | 每笔尖随机 |
| `fuzzystroke` | 每笔划随机 |
| `speed` | 速度 |
| `fade` | 渐隐（按笔尖数） |
| `distance` | 距离 |
| `time` | 时间（序列化名 duration） |
| `perspective` | 透视图距离 |
| `sensorslist` | 组合容器（不面向 UI） |

## 七、相邻参数（散布/传递/形状；供任务 23、26–28）

共 11 条。**这些只是「Krita 能表达什么」的证据，不是完整的非纹理能力表**——是否扩到全量非纹理参数是任务书「决策 3」，待人工拍板。

| 参数 | Krita 路径 | 类型 | 默认 / 范围 | 来源（文件:行） |
|---|---|---|---|---|
| 散布量（UI 0..500%） | `ScatterValue` | double | 默认 5；范围 0..5 | KisScatterOptionData.cpp:L33-33 |
| 散布量旧键 | `Scattering/Amount` | double | 范围 0..5 | KisScatterOptionData.cpp:L12-38 |
| 散布 X 轴开关 | `Scattering/AxisX` | bool | 默认 true | KisScatterOptionData.cpp:L10-10 |
| 散布 Y 轴开关 | `Scattering/AxisY` | bool | 默认 true | KisScatterOptionData.cpp:L11-11 |
| 不透明度曲线选项（0..1） | `OpacityValue / PressureOpacity / OpacitySensor …` | double | 范围 0..1 | KisStandardOptionData.h:L12-18 |
| 流量曲线选项（0..1） | `FlowValue / PressureFlow / FlowSensor …` | double | 范围 0..1 | KisStandardOptionData.h:L23-28 |
| 圆度（宽高比）曲线选项（0..1） | `RatioValue / PressureRatio / RatioSensor …` | double | 范围 0..1 | KisStandardOptionData.h:L35-40 |
| 旋转曲线选项（值坐标 0..1，UI 标签 ±180°） | `RotationValue / PressureRotation / RotationSensor …` | double | 范围 0..1 | KisStandardOptionData.h:L55-60<br>KisStandardOptionData.cpp:L23-27 |
| 大小曲线选项（0..1 乘子） | `SizeValue / PressureSize / SizeSensor …` | double | 范围 0..1 | KisSizeOptionData.cpp:L11-11 |
| 锐度曲线选项（0..1） | `SharpnessValue / Sharpness/factor / Sharpness/softness` | double | 范围 0..1 | KisSharpnessOptionData.cpp:L8-37 |
| 间距是否各向同性（间距倍数在 brush_definition XML 的 spacing 属性） | `Spacing/Isotropic` | bool | 默认 false | KisSpacingOptionData.cpp:L10-15 |

## 八、结构性不可映射推导（把范围换算成 PS 参数）

| PS 参数 | PS 范围（ABR 实测/文档） | Krita 侧 | 换算 | 结论 |
|---|---|---|---|---|
| 纹理对比度 `textureContrast` | -50..100（实测 -50..84） | `Contrast` 0..2 | `k = 1/(1-C/100)`（C≥0） | C>50 时 k>2，**结构性不可表达** |
| 纹理亮度 `textureBrightness` | -150..150（实测 -55..53） | `Brightness` -1..1 | calibrated 闭式反解后 clamp | C<0 且亮度绝对值 >127 时越界，结构性 |
| 纹理深度 `textureDepth` | 0..100（实测 4..100） | `Texture/Strength/Value` 0..1 | 高度系 `min(1, 1.2·d/100)` | d>83% 提前饱和，结构性 |
| 纹理缩放 `textureScale` | 0..1000%（实测 16..280%） | `Scale` 读取无 clamp；UI 滑块 0..2 | 直接 ×0.01 | 存储可 >2，但 UI 只能编辑到 2.0；**待人工实测** |
| 纹理模式 | PS 4CC 混合模式 | `TexturingMode` 0..15 | 查表 | 16 个成员覆盖了样本里出现的全部 7 种 PS 模式（缺 `Mltp` 别名登记，见 abr 报告 §七） |

> 与 `docs/parameter-mapping.md` §7.1 的 5 条「局限」逐条对照：局限 1/2/3 本次拿到源码行级依据（`Contrast` 的 UI 0..2、`Brightness` -1..1、`Strength/Value` 0..1 且默认 1.0）；局限 4（C=+100 二值化）属数学边界，源码不额外证据；局限 5（判据口径）与本节无关。

## 九、未做的验证与风险（如实登记）

1. **没有在 Krita GUI 里运行时读控件范围**：本表全部来自「构建该 exe 的源码」。源码与二进制的一致性证据是版本串里的 git hash `858d352`，不是逐字节比对。若 Qt 样式/插件改写控件范围，源码结论可能偏差——建议 26–28 顺手在 Krita 里看一眼滑块两端。
2. **`scaleSlider->addMultiplier(0.1/2/10)` 的语义未查证**：`KisDoubleSliderSpinBox` 的源码本轮没取到（路径 404），因此「UI 能否输入 >2.0 的 Scale」仍未判定（需人工实测）。这条直接影响 ABR 实测 280% 的处置。
3. **模式 2/3 的实际渲染效果未做像素级验证**：本表只证明「像素笔刷引擎会把 2/3 放进下拉框」，不证明其像素行为与 PS 一致。
4. **传感器 id 只登记 id 与含义**，未逐个验证本项目转换器是否产出合法 XML（转换器目前只写 pressure / drawingangle / fuzzy 三种）。

## 十、复现与抽查

```bash
# 1) 重算能力表条目数（三个数字里的第三个）
python -c "import json;print(len(json.load(open('docs/数据/krita_caps.json',encoding='utf-8'))['params']))"

# 2) 重算 TexturingMode 成员数与上限
python -c "import json;d=json.load(open('docs/数据/krita_caps.json',encoding='utf-8'));t=[p for p in d['params'] if p['krita_path']=='Texture/Pattern/TexturingMode'][0];print(len(t['range_or_enum']['enum_complete']),t['range_or_enum']['max'])"

# 3) 抽查任意一条的来源（浏览器打开 source 里的 URL，或 curl 比对行号）
curl -s 'https://invent.kde.org/graphics/krita/-/raw/858d352/plugins/paintops/libpaintop/KisTextureOptionData.h' | sed -n '29,46p'
```

> 版本锁定（铁律 12）：换 Krita 版本必须整表重取；本表的行号只对 5.3.3 / git 858d352 成立。