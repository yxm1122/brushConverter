# 实验报告（Krita 侧）：纹理参数矩阵 A/B + 无纹理参考

> 任务：v1.2.0 之后的双侧对照实验（Krita 侧施工）
> 执行代理：exp-krita｜执行日期：2026-11-19｜工作目录：`<REPO>`
> 本报告只描述 Krita 侧；PS 侧见 `docs/验收报告/实验-PS侧-报告.md`。所有结论均附命令与实测数据，未验证项如实标注。

---

## 一、结论

**完成。** 53 张实验图全部渲染成功（conv 28 / raw 24 / 参考 1），0 失败，单张 14.22–17.98 秒、平均 15.42 秒；
每张的 Alpha 非零占比、min/max、sha256 与 .kpp 纹理字段回读值都写进了 `tests/baselines/exp/krita/manifest.json`。

三个关键结论：

1. **矩阵 A（b=c=中性）下 conv 与 raw 的纹理字段逐项相同**（Scale=scale/100、Strength=depth/100、Mode=0/15、Brightness=0、Contrast=1、Invert=true），
   实际渲染 **16/24 张 sha256 完全相同**；只有 multiply 的 depth=10/60 共 8 张不同，差异**恰好是常量 255×(1−s)**，
   来源是转换链给 multiply 写了 `UseSoftTexturing=true` 而 raw 用默认 false（见 §7）。
2. **纹理锚点 = 画布 (0,0)**：64×64 纹理按 100% 平铺、invert 后 multiply(d100,s100) 的预测掩码与实测 Alpha
   **逐像素完全一致（10000/10000，max|Δ|=0）**；最佳相位搜索同样落在 (0,0)，invert=false 反例差异 99.29%（§9）。
3. **踩到一个 Krita 5.3.3 的真实坑（影响产品，不只是本实验）**：内嵌纹理的 md5 与用户图案库里已安装图案相同时，
   Krita 会把该内嵌纹理解析失败并**静默禁用纹理**（预设照常加载、Enabled=true、Alpha 恒 255）；图案**文件名**与库中已有图案
   相同时还会**串用别人的图案**。本实验因此首轮 52 张作废重渲，最终用「源文件字节 + 1 个 tEXt 元数据块」的等价副本规避（§8）。

---

## 二、交付清单

| 路径 | 说明 |
|---|---|
| `tools/exp_krita_params.py` | 实验工具：生成 .kpp（conv/raw/ref）、串行渲染、写清单、字段回读、conv/raw 差异、纹理相位检验 |
| `tests/baselines/exp/krita/exp__krita__*.png` | 53 张实测图（命名严格按规格） |
| `tests/baselines/exp/krita/manifest.json` | 本目录清单（53 条，含 variant / 参数 / Alpha 统计 / sha256 / krita_scale / krita_depth_strength / krita_mode_enum / kpp_fields） |
| `tests/baselines/exp/krita/_work/kpp/` | 53 个实验用 .kpp（可复现，不入基准本体） |
| `tests/baselines/exp/krita/_work/kpp_fields.json`、`kpp_fields.log` | 逐张 XML 字段回读明细 |
| `tests/baselines/exp/krita/_work/render_log.json`、`render3.log` | 逐张渲染结果与耗时 |
| `tests/baselines/exp/krita/_work/variant_diff.json` | conv vs raw 24 对逐张差异 |
| `tests/baselines/exp/krita/_work/phase_check.json` | 纹理相位证据 |
| `tests/baselines/exp/krita/_work/predict_check.json` | 实测 vs `tools/texture_mask.py` 预测的逐张校核 |
| `tests/baselines/exp/krita/_work/probe/`、`probe_*.py` | 排查纹理加载异常用的诊断脚本与产物（保留作证据） |
| `docs/验收报告/实验-Krita侧-报告.md` | 本报告 |

交付张数：**53 张**（conv A 24 + conv B 4 + raw A 24 + 参考 1）。任务书正文「56/57 张」与「B 组只做 conv」自相矛盾，
lead 已确认以「仅矩阵 A 出 raw」为准（53 张）。

---

## 三、实验口径（与 PS 侧一致）

| 项 | 值 |
|---|---|
| 源预设 | `tests/定量测试专用.abr` 唯一一支「定量测试用」（计算笔刷，Dmtr=200、硬度 100、间距 Spcn=1000） |
| 纹理 | `research/测试用纹理.png`（64×64 RGBA，13331 字节，md5 `5e748763e7af2f1c5a99a27b3a3aee38`） |
| 画布 | 100×100，导出 RGBA PNG |
| 几何（全部图同一常量） | 笔划 stroke=60、压感 1.0、落笔为**单个 dab**：圆心 (20,50)、半径 100px（原因见 §6） |
| 矩阵 A | mode ∈ {multiply, linearHeight} × depth ∈ {10,60,100} × scale ∈ {50,100,150,200}，b=0、c=0 → 24 组 |
| 矩阵 B | 固定 multiply / depth=100 / scale=100，(b,c) ∈ {(-50,0),(50,0),(0,50),(0,-50)} → 4 组 |
| 参考图 | 同一预设 useTexture=false，其余完全相同 → 1 张 |
| 命名 | `exp__krita__<variant>__<modeKey>__d<depth>_s<scale>_b<b>_c<c>.png`；参考图 `exp__krita__ref__notex.png` |

---

## 四、.kpp 生成方式

### 4.1 conv（转换后，=用户实际会拿到的预设）

走项目现成转换链：`AbrFile.parse → map_presets → convert._texture_xml()`（内含
`texture_brightness_contrast()` 的 calibrated 逆公式、`scale/100`、`strength=depth/100`、
`_TEXTURING_MODE` 枚举映射、Height 系关软纹理策略），再 `build_preset_xml()` 组装、`build_kpp()` 落盘。
只做一处替换：把内嵌纹理位图换成「源文件字节的等价副本」（§8），其余字段全部沿用转换链计算结果。

### 4.2 raw（PS 原数值直传）

不套转换链的亮度/对比度标定与软纹理策略，把 PS 数字按自然单位直接写进 Krita 纹理字段：

| PS | Krita 字段 | 换算 |
|---|---|---|
| scale=50/100/150/200（%） | `Texture/Pattern/Scale` | ÷100 → 0.5/1/1.5/2（Krita 1.0=100%） |
| depth=10/60/100（%） | `Texture/Strength/Value` | ÷100 → 0.1/0.6/1.0 |
| mode=multiply / linearHeight | `Texture/Pattern/TexturingMode` | 0 / 15（任务 07A 枚举） |
| b=c=0（中性） | Brightness / Contrast | 0 / 1.0 |
| — | Invert | 照抄 ABR 源预设 = true（两 variant 一致，不参与矩阵） |

矩阵 B 只做 conv：Krita 亮度范围 [-1,1]、对比度 [0,2]，与 PS 的 b∈[-150,150]、c∈[-50,100] 量纲不同，
raw 直传必然被 clamp 成无意义值，故不做（任务书要求）。

---

## 五、字段回读证据（从生成的 .kpp 里读 XML）

回读方式：`tools/exp_krita_params.py --dump-fields` → 用 `tools/kpp_params.py` 的 `read_kpp_xml()`
取 PNG iTXt 块里的预设 XML，再用 ElementTree 取 `<param name=...>` 的值（明细见 `_work/kpp_fields.json`）。

### 5.1 矩阵 A（conv 与 raw 的 Scale/Strength/Mode/Brightness/Contrast 完全一致，仅 Soft 不同）

| 模式 | PS 深度 | PS 缩放 | Krita Scale | Krita Strength | Krita Mode | Brightness | Contrast | Invert | Soft(conv) | Soft(raw) |
|---|---|---|---|---|---|---|---|---|---|---|
| multiply | 10 | 50% | 0.5 | 0.1 | 0 | 0 | 1 | true | true | false |
| multiply | 10 | 100% | 1 | 0.1 | 0 | 0 | 1 | true | true | false |
| multiply | 10 | 150% | 1.5 | 0.1 | 0 | 0 | 1 | true | true | false |
| multiply | 10 | 200% | 2 | 0.1 | 0 | 0 | 1 | true | true | false |
| multiply | 60 | 50% | 0.5 | 0.6 | 0 | 0 | 1 | true | true | false |
| multiply | 60 | 100% | 1 | 0.6 | 0 | 0 | 1 | true | true | false |
| multiply | 60 | 150% | 1.5 | 0.6 | 0 | 0 | 1 | true | true | false |
| multiply | 60 | 200% | 2 | 0.6 | 0 | 0 | 1 | true | true | false |
| multiply | 100 | 50% | 0.5 | 1 | 0 | 0 | 1 | true | true | false |
| multiply | 100 | 100% | 1 | 1 | 0 | 0 | 1 | true | true | false |
| multiply | 100 | 150% | 1.5 | 1 | 0 | 0 | 1 | true | true | false |
| multiply | 100 | 200% | 2 | 1 | 0 | 0 | 1 | true | true | false |
| linearHeight | 10 | 50% | 0.5 | 0.1 | 15 | 0 | 1 | true | false | false |
| linearHeight | 10 | 100% | 1 | 0.1 | 15 | 0 | 1 | true | false | false |
| linearHeight | 10 | 150% | 1.5 | 0.1 | 15 | 0 | 1 | true | false | false |
| linearHeight | 10 | 200% | 2 | 0.1 | 15 | 0 | 1 | true | false | false |
| linearHeight | 60 | 50% | 0.5 | 0.6 | 15 | 0 | 1 | true | false | false |
| linearHeight | 60 | 100% | 1 | 0.6 | 15 | 0 | 1 | true | false | false |
| linearHeight | 60 | 150% | 1.5 | 0.6 | 15 | 0 | 1 | true | false | false |
| linearHeight | 60 | 200% | 2 | 0.6 | 15 | 0 | 1 | true | false | false |
| linearHeight | 100 | 50% | 0.5 | 1 | 15 | 0 | 1 | true | false | false |
| linearHeight | 100 | 100% | 1 | 1 | 15 | 0 | 1 | true | false | false |
| linearHeight | 100 | 150% | 1.5 | 1 | 15 | 0 | 1 | true | false | false |
| linearHeight | 100 | 200% | 2 | 1 | 15 | 0 | 1 | true | false | false |

### 5.2 矩阵 B（calibrated 逆公式的实际落值）

| PS 亮度 b | PS 对比度 c | Krita Brightness（回读） | Krita Contrast（回读） | 说明 |
|---|---|---|---|---|
| -50 | 0 | 0.196 | 1 | 亮度 -50 → b_k=+0.196（Krita 亮度是减法） |
| 50 | 0 | -0.196 | 1 | 亮度 +50 → b_k=-0.196 |
| 0 | 50 | -0.001 | 2 | 对比度 +50 → k=2、中心补偿 -0.001 |
| 0 | -50 | 0.002 | 0.5 | 对比度 -50 → k=0.5、中心补偿 +0.002 |

### 5.3 内嵌纹理回读

52 个带纹理 .kpp 的内嵌 `patterns` 资源全部满足：
`资源实际 md5 == 声明 md5sum == Texture/Pattern/PatternMD5Sum == 552a2ae24291e640de09f4488d6e05c0`，
字节数 13433，资源名 `expkrita_tex_552a2ae2.png`。
该字节串 = 源文件 13331 字节 + 1 个 tEXt 块（去掉该块可逐字节还原源文件，像素未动，原因见 §8）。

---

## 六、渲染方式、耗时与几何一致性

### 6.1 渲染口径

- 路线 A（真实 GUI + PyKrita 启动插件）：`tools/krita_render_runner.py::render_one(route="gui")`，
  每次渲染临时安装插件、临时导入 .kpp、建透明画布、落笔、导出 PNG、还原 kritarc/resourcecache、删除临时插件与 .kpp。
- `--size 100x100`、压感 1.0、stroke=60（全部图同一常量，参考图同样几何）。
- **严格串行**：同一时刻只跑一个 Krita GUI 会话（渲染前实测无其他 krita.exe 进程）。

命令：

```bash
PYTHONIOENCODING=utf-8 "<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/exp_krita_params.py --build
PYTHONIOENCODING=utf-8 "<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/exp_krita_params.py --render --resume
PYTHONIOENCODING=utf-8 "<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/exp_krita_params.py --manifest --diff-variants --phase-check
PYTHONIOENCODING=utf-8 "<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/exp_krita_params.py --dump-fields
```

### 6.2 耗时与成功/失败

| 批次 | 张数 | 成功 | 失败 | 总耗时 | 平均 |
|---|---|---|---|---|---|
| 首批（含参考图） | 52 | 52 | 0 | 810.5 秒 | 15.59 秒/张 |
| 参考图单独 | 1 | 1 | 0 | 14.48 秒 | 14.48 秒 |
| 纹理修正后重渲 | 51 | 51 | 0 | 818.4 秒 | 16.05 秒/张 |

逐张耗时（render_log.json，53 条）：min 14.22 / avg 15.42 / max 17.98 秒。
**首轮 52 张因纹理加载异常（§8）作废并整批重渲**，报告里如实记录这次返工。

### 6.3 几何一致性（为什么是「单 dab」而不是连续笔迹）

源预设间距 `Spcn=1000` → 项目映射 `spacing=10.0`（Krita 1000%），200px 笔刷的 dab 间距 = 2000px，
因此 `paintLine` 在 100px 画布上只落**起始 dab 一个圆盘**（半径 100px）；实测图像边界与「单圆盘」模型逐像素吻合。

插件把笔划起点算成 `x0=max(1,(宽-stroke)//2)=20`、`y0=高//2=50`，所以：
**圆心 (20,50)、半径 100px**，到画布最远角 93.5px < 100px → 几何上覆盖整块画布（参考图实测 Alpha 全 255、覆盖 10000/10000）。
若取 stroke=100 则圆心落在 (1,50)，右端会留 320 个透明像素（覆盖 0.968），故统一取 60。已报 lead 并获确认。

---

## 七、Alpha 覆盖率与 min/max（逐张见 manifest）

- 全部 53 张：覆盖率（Alpha 非零占比）范围 **0.6623 – 1.0000**；Alpha 取值范围 0–255。
- **参考图（无纹理）：覆盖率 1.0000，Alpha 全 255**（几何全覆盖的证据）。
- 覆盖率 < 0.97 的 3 张（已在 manifest 标 `coverage_warning` 并在本报告说明）：

| 文件 | 覆盖率 | 原因（与几何无关，是纹理掩码本身把像素打到 0） |
|---|---|---|
| `exp__krita__conv__multiply__d100_s100_b0_c50.png` | 0.6623 | 对比度 +50 → Krita contrast=2、brightness=-0.001；掩码以 0.5 为中心拉开后大量像素 ≤0 → Alpha=0 |
| `exp__krita__conv__multiply__d100_s100_b50_c0.png` | 0.7299 | 亮度 +50 → calibrated 反解 brightness=-0.196（Krita 是减法，等效压暗）；大量掩码被压到 0 |
| `exp__krita__raw__multiply__d10_s100_b0_c0.png` | 0.9678 | multiply 硬合成 out=mask×s，s=round(0.1×255)/255=0.102；暗部 0.102×mask 取整后为 0 |

（linearHeight 组 24 张覆盖率全部 1.0000：`weight=10×s` 的 height 公式在 s 较小时仍恒 >0。）

---

## 八、纹理加载异常（重要发现，影响产品）

### 8.1 现象

按「源文件逐字节内嵌」生成 .kpp 后，预设能正常加载、字段回读全部正确（Enabled=true、Scale/Strength/Mode/Invert 都对），
但**落笔时纹理被静默禁用**：带纹理图的 Alpha 恒 255，与无纹理参考图一模一样。首轮 52 张因此全部作废。

### 8.2 排查（全部是真实渲染对照，不是推测）

| 实验 | 结果 |
|---|---|
| 原文件逐字节内嵌（md5 `5e748763…`），任意文件名 | 纹理禁用：Alpha 恒 255 |
| 同像素、只改 pHYs 数值的 PNG（md5 `3863a554…`） | 正常生效，且与 `texture_mask.py` 预测 **逐像素一致（mean|Δ|=0）** |
| 同像素、PIL 重存 RGB/RGBA/灰度（md5 变化） | 正常生效，预测逐像素一致 |
| 用户 Krita 图案库中的 `测试用纹理.png` | 与本实验源文件 **md5 完全相同**（`5e748763…`） |
| 图案名用转换链默认的 `tex_<uuid8>.png`（= `tex_66e2987f.png`） | **串用资源库里同名图案**：不同字节也渲染出同一张错图（Alpha 均值 211.6）；换唯一名字后正常 |

### 8.3 结论与处置

- **根因（可复现）**：Krita 5.3.3 处理内嵌纹理时按 md5 与已安装图案去重；当内嵌纹理 md5 与图案库中已有图案相同时，
  该内嵌纹理解析失败 → 纹理被静默禁用（无报错、无警告，只有 Alpha 全 255）。
  另外图案**文件名**与库中已有图案相同时会被遮蔽（用别人的图案）。
- **处置（最小改动、可逐字节还原）**：内嵌纹理 = 源文件全部字节 + 在 IEND 前追加 1 个 tEXt 块
  （keyword `brushconverter-exp`、内容为源文件 sha256），得到唯一 md5 `552a2ae24291e640de09f4488d6e05c0`、13433 字节；
  IHDR/IDAT 像素数据一字未动。图案资源名同步改为 `expkrita_tex_<md5前8>.png` 避免撞名。
  → 与任务书「同一文件字节」的唯一偏差就是这 92 字节的元数据块，已如实报 lead；去掉该块即可逐字节还原源文件。
- **对产品的启示（建议后续任务跟进）**：转换链目前用 `tex_<uuid8>.png` 作图案资源名、并直接内嵌源图案字节；
  一旦用户库里有同名或同 md5 的图案，用户会遇到「纹理明明开着却不生效/用错图案」且没有任何提示。
  建议转换时给图案资源用**唯一名字**（如带内容哈希），并在同 md5 场景下调整内嵌字节或改引用方式。

---

## 九、纹理相位证据（lead 指定项）

对比对象：`exp__krita__conv__multiply__d100_s100_b0_c0.png`（Krita 实测 Alpha）
与方法：64×64 纹理按 `Texture/Pattern/Scale=1`（100%）**平铺到 100×100（锚点取画布 (0,0)）**，
用 `tools/texture_mask.py` 复刻 Krita 掩码（invert=true、brightness=0、contrast=1、mode=Multiply(0)、depth=1.0，base=1），
把预测掩码 ×255 取整后与实测 Alpha 逐像素比较（明细 `_work/phase_check.json`）：

| 假设 | 差异比例（>2 灰度级） | 最大差 | 平均差 |
|---|---|---|---|
| **锚点 (0,0) + invert=true** | **0.0000%** | **0** | **0.0** |
| 锚点 (0,0) + invert=false | 99.29% | 255 | 122.97 |
| 最佳相位（全 64×64 偏移搜索） | 落在 **(0,0)**，差异 0 | 0 | 0.0 |

**结论**：Krita 的纹理锚点就是画布 (0,0)，与 PS 一致；invert 生效；该图 10000/10000 像素与预测完全相同（不是「接近」）。

附带校核（`_work/predict_check.json`，52 张带纹理图逐张跑同一预测）：
- **scale=100% 的 16 张（A 组 12 + B 组 4）全部逐像素一致**（mean|Δ|=0、max=0）。
  其中 conv multiply 的 8 张（d10/d60/d100 + B 组）用「软纹理 = 硬结果 + (1−s)」模型同样得到 0 差，
  说明 §7 的软纹理常量差异模型成立。
- scale≠100% 的 36 张中：24 张有差异——原因是 Krita 对图案按 scale 做**平滑重采样**（Qt SmoothTransformation）后再平铺，
  而本预测器按最近邻平铺；另有 12 张 linearHeight（d60/d100）因掩码饱和到 255 而与预测一致（缩放不可见）。
  这是 numpy 预测器的已知限制，不是渲染/映射错误。

---

## 十、conv 与 raw 的差别在哪（lead 指定项）

### 10.1 字段层面：矩阵 A 五项数值完全相同

见 §5.1：Scale / Strength / Mode / Brightness / Contrast 逐项相同（calibrated 公式在 b=0、c=0 时
正好退化为 brightness=0、contrast=1；scale/100 与 depth/100 也是两侧共同换算）。

### 10.2 实际渲染：16/24 张 sha256 相同，8 张不同

| 分组 | 张数 | 是否逐像素相同 | 差异 |
|---|---|---|---|
| linearHeight × 全部 depth/scale | 12 | **sha256 完全相同** | 0 |
| multiply d100 × 4 scale | 4 | **sha256 完全相同** | 0 |
| multiply d10 × 4 scale | 4 | 不同 | 全部像素差 **229**（=255×(1−s)，s=26/255） |
| multiply d60 × 4 scale | 4 | 不同 | 全部像素差 **102**（=255×(1−s)，s=153/255） |

**原因（唯一差别）**：`UseSoftTexturing` —— 转换链对 multiply 写 `true`（Height/LinearHeight 系写 false），raw 用默认 `false`。
实测 Krita 的软纹理 multiply 公式为
`Alpha_soft = clip(Alpha_hard + (1 − s), 0, 1)`，s = round(strength×255)/255（s=1 时与硬合成相同 → d100 全等）。
逐对明细见 `_work/variant_diff.json`。

### 10.3 结论

**矩阵 A 下转换链对 Krita 侧没有实质改变**（16/24 张逐字节相同）；唯一的实质差异来自转换链主动开启的
Soft Texturing，且只影响 multiply 的低深度（d10/d60），d100 时该开关不影响结果。
linearHeight 组两 variant 完全一致（转换链本就对 Height 系关闭软纹理）。

---

## 十一、验收标准对照表

| 标准 | 结论 | 证据 |
|---|---|---|
| 53 张全部渲染成功（conv 28 / raw 24 / 参考 1） | 通过 | manifest：rendered 53 / missing 0；render_log 53 条全 rendered |
| 命名严格按规格（含 variant、modeKey、b/c 符号） | 通过 | `tests/baselines/exp/krita/` 文件名；manifest `naming` 字段 |
| 每张记录模式/深度/缩放/亮度/对比度/Alpha 占比/min/max/sha256 | 通过 | manifest 每条含这些字段 + `krita_scale`/`krita_depth_strength`/`krita_mode_enum` |
| conv 用项目现成转换链 | 通过 | `exp_krita_params.conv_texture_xml` 调用 `convert._texture_xml` |
| raw 按 PS 数值直传（scale/100、strength=depth/100、mode 0/15、b=0、c=1） | 通过 | §5.1 字段回读表 |
| 仅矩阵 A 出 raw，B 只做 conv | 通过 | raw 24 张全为矩阵 A；B 组 4 张只有 conv |
| 参考图无纹理且几何相同 | 通过 | 参考图 Alpha 全 255、覆盖 1.0000；同 stroke=60/压感 1.0 |
| 全画布覆盖（<0.97 标注并说明） | 通过（几何全覆盖；纹理置 0 的 3 张已标注） | §7；manifest `coverage_below_0_97` |
| 同一时刻只跑一个 Krita 会话 | 通过 | 串行渲染，无并发；渲染前后进程核对 |
| 结束后清理临时插件/配置、无残留进程 | 通过 | no krita.exe；pykrita 仅 preset_groups；kritarc enable_bc_auto_render=0；paintoppresets/patterns/TEMP 无残留 |
| .kpp 字段回读证据 | 通过 | §5（`kpp_fields.json` / `kpp_fields.log`） |
| 纹理相位证据 | 通过 | §9（差异 0、最佳相位 (0,0)） |
| conv 与 raw 差别说明 + sha256 对比数量 | 通过 | §10（16 相同 / 8 不同） |
| 不改 src/、research/、docs/优化提示词/、docs/工作流程/、既有 baselines、tests/baselines/ps|krita|feel | 通过 | `git status --short` 仅见本任务新增文件（`tests/baselines/exp/`、`tools/exp_krita_params.py`、本报告） |
| 无任何 git 写操作 | 通过 | 全程只读 `git status` |
| 全中文 | 通过 | 本报告、工具注释均为中文 |

---

## 十二、复现命令

```bash
cd <REPO>
PY="<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe"

# 1) 生成 53 个 .kpp（conv/raw/ref）
PYTHONIOENCODING=utf-8 "$PY" tools/exp_krita_params.py --build

# 2) 回读 .kpp 纹理字段（打印表格 + 写 _work/kpp_fields.json）
PYTHONIOENCODING=utf-8 "$PY" tools/exp_krita_params.py --dump-fields

# 3) 串行渲染 53 张（约 14 分钟；--resume 跳过已有）
PYTHONIOENCODING=utf-8 "$PY" tools/exp_krita_params.py --render --resume

# 4) 写 manifest + conv/raw 差异 + 纹理相位检验
PYTHONIOENCODING=utf-8 "$PY" tools/exp_krita_params.py --manifest --diff-variants --phase-check

# 5) 抽查（应输出 100x100、覆盖比例、sha256）
PYTHONIOENCODING=utf-8 "$PY" -c "import json;m=json.load(open('tests/baselines/exp/krita/manifest.json',encoding='utf-8'));print(m['summary'])"
```

---

## 十三、遗留问题

1. **内嵌纹理字节与源文件不是逐字节相同**（多 1 个 tEXt 块）：由 §8 的 Krita 5.3.3 限制导致，已在 manifest/texture 与
   `embedded_texture_bytes()` 注释里写清；若后续要求严格字节相同，需要换成「引用图案库文件」方案（依赖用户环境，不推荐）。
2. **scale≠100% 的掩码预测精度**：numpy 侧需复刻 Qt SmoothTransformation 才能逐像素预测；本次未做（与实验交付无关）。
3. **转换链图案命名/md5 冲突风险**（§8.3）尚未在 `src/` 落地修复——本任务不改 `src/`，建议单独立项。
4. 矩阵 B 的 raw 未做（任务书明确无意义）；若 lead 需要「B 组 raw 的失真证据」，可再补 4 张（约 1 分钟渲染）。
5. 首轮作废图未保留（直接删除重渲），只保留 `render.log`/结算记录；如需「错误纹理」样本可复跑诊断脚本 `_work/probe_*`。

---

## 十四、决策记录（lead 代为决策，已回执）

| 决策点 | 选项 | 选择 | 理由 |
|---|---|---|---|
| Krita 侧张数：53 还是 57 | 53（仅 A 出 raw）/ 57（B 也出 raw） | **53** | 正文明确「B 只做 conv」；56/57 是笔误，lead 已回执确认 |
| raw 的 scale 量纲 | 字面 50/100/150/200 / 换算 0.5…2 | **换算（/100）** | Krita 的 Scale 1.0=100%；字面值会被 clamp 到 10×，四档退化，且与「只有 B 组因量纲不同才无意义」矛盾；lead 已确认 |
| raw 的 invert | 照抄 true / 取默认 false | **照抄 true** | invert 不是数值参数，两 variant 一致才能隔离数值矩阵；lead 已确认 |
| 几何常量 | stroke=100（有 320 px 缺角） / stroke=60（全覆盖） | **stroke=60** | 源预设 1000% 间距导致单 dab；60 使圆心 (20,50) 覆盖全画布（参考图 10000/10000）；lead 已确认 |
| 纹理加载异常处置 | 改引用库图案 / 追加 tEXt 得唯一 md5 / 放弃 | **追加 tEXt** | 最小改动、可逐字节还原、预设自包含；像素数据未动 |
| 首轮 52 张 | 保留 / 作废重渲 | **作废重渲** | 首轮用的是错图案（Alpha 均值 211.6），数据不可用；如实记录返工 |

---

## 十五、环境清理核对（渲染结束后实测）

```text
tasklist /FI "IMAGENAME eq krita.exe" /NH   → No tasks are running which match the specified criteria.
ls "$APPDATA/krita/pykrita"                  → preset_groups  preset_groups.desktop（无 bc_auto_render）
grep -c enable_bc_auto_render "$LOCALAPPDATA/kritarc" → 0
ls "$APPDATA/krita/paintoppresets" | grep -c exp__krita → 0
ls "$APPDATA/krita/patterns" | grep -c expkrita          → 0
ls -d "$TEMP"/bc_krita_gui_* | wc -l                     → 0
```

## 十六、改动清单（相对仓库根）

| 文件 | 类型 | 说明 |
|---|---|---|
| `tools/exp_krita_params.py` | 新建 | 实验工具（生成 .kpp / 渲染 / 清单 / 字段回读 / 差异 / 相位检验） |
| `tests/baselines/exp/krita/exp__krita__*.png` | 新建 53 个 | 实验图（命名按规格） |
| `tests/baselines/exp/krita/manifest.json` | 新建 | 本目录清单（53 条） |
| `tests/baselines/exp/krita/_work/**` | 新建 | .kpp、逐张日志、字段回读、差异/相位/预测校核 JSON、排查脚本 |
| `docs/验收报告/实验-Krita侧-报告.md` | 新建 | 本报告 |

未修改：`src/`、`research/`、`docs/优化提示词/`、`docs/工作流程/`、`tests/baselines/ps|krita|feel`、`docs/验收报告/` 下既有报告。
`git status --short` 里另外两项 `M tools/ps_export.jsx`、`M tools/ps_probe.py` 与 `docs/验收报告/实验-PS侧-报告.md`
是 PS 侧代理的改动，不属于本任务。

---

## 十七、fixA 实测验证（追加小任务，2026-11-19）

### 17.1 背景（来自 `docs/验收报告/实验-归因-报告.md`）

- multiply 的深度语义在 conv 下与 PS **逐像素相同（0.00%）**；
- **linearHeight d10 不准**：PS vs Krita conv 差异 mean|Δ|=43.31、max=52、95.36% 像素超阈，偏差精确 = 2q（q=round(2.55×10)=26 → 52）；
- 根因假设：PS 高度系权重 **W=12** vs Krita **W=10**；修法 A：**高度系 strength = min(1.0, 1.2 × depth/100)**。
  模型预测：mean|Δ| 43.31 → **1.39**、超阈像素 95.36% → **0.00%**、max 52 → **2**。

本次把该修法从「预测」变成「实测」。

### 17.2 做法

- **只改** `Texture/Strength/Value`：height 系 = `min(1.0, 1.2 × depth/100)`；其余字段与同参数 conv **完全一致**
  （Scale=scale/100、TexturingMode=15、Brightness=0、Contrast=1、Invert=true、UseSoftTexturing=false）。
- 6 张（lead 点名）：`d10_s100`、`d10_s50`、`d10_s150`、`d10_s200`、`d60_s100`、`d100_s100`；
  命名 `exp__krita__fixA__linearHeight__d<depth>_s<scale>_b0_c0.png`；串行渲染 **6/6 成功、88.7 秒（14.79 秒/张）**。
- 字段回读（`_work/kpp_fields.log` 的 fixA 行）：Strength = **0.12 / 0.72 / 1.0**（d100 被 1.0 封顶，与 conv 相同），
  Scale 随参数 0.5/1/1.5/2，Mode=15、Brightness=0、Contrast=1、Invert=true、Soft=false。

### 17.3 逐张对比（Alpha 通道；分母 10000 像素；|Δ|>2 记为超阈）

| 参数 | fixA 超阈 | fixA mean\|Δ\| | fixA max\|Δ\| | conv 超阈 | conv mean\|Δ\| | conv max\|Δ\| | Strength |
|---|---|---|---|---|---|---|---|
| d10_s100 | **0.0000%** | **1.39** | **2** | 95.3600% | 43.31 | 52 | 0.12 |
| d10_s50 | 45.1900% | 2.29 | 3 | 100.0000% | 51.44 | 53 | 0.12 |
| d10_s150 | 79.1700% | 13.09 | 93 | 99.7900% | 49.51 | 143 | 0.12 |
| d10_s200 | 84.3000% | 19.22 | 91 | 98.7800% | 49.25 | 126 | 0.12 |
| d60_s100 | 0.0000% | 0.00 | 0 | 0.0000% | 0.00 | 0 | 0.72 |
| d100_s100 | 0.0000% | 0.00 | 0 | 0.0000% | 0.00 | 0 | 1.0 |

（PS 真值取 `tests/baselines/exp/ps/exp__ps__linearHeight__d<depth>_s<scale>_b0_c0.png`；
conv 为同参数旧图；明细 `_work/fixa_compare.json`。）

### 17.4 结论：修法 A 实测是否成立

**成立（在可判读的 scale=100% 上逐位吻合模型预测）**：

1. `d10_s100`：43.31 → **1.39**、52 → **2**、95.36% → **0.00%**，与模型预测的 1.39 / 2 / 0.00% **完全一致**；
   即 PS 与 Krita 的高度系权重差确实就是 W=12 vs W=10，strength×1.2 可以精确抵消。
2. `d60_s100`、`d100_s100`：两图 Alpha 恒 255（与无纹理参考图 sha256 相同 `6f9f1ab5…`），属**双侧饱和区、不可判读**，
   fixA 与 conv 都是 0 差异，不构成反例。
3. `scale≠100%` 的 3 行：高度权重误差**确已被消除**（mean 51.44/49.51/49.25 → 2.29/13.09/19.22，
   max 53/143/126 → 3/93/91），但超阈比例仍 45.19%/79.17%/84.30%；残差随缩放单调增大
   （s50 只剩 ≈±3 的固定级差；s150/s200 达 90 级），与归因报告「150%/200% 放大只判部分、重采样口径差异」一致 →
   **这部分残差归缩放重采样（PS vs Krita Qt 平滑），不是高度权重**；**不应再调 1.2 系数**
   （调系数会破坏 s100 已取得的 0.00%，把权重误差重新引入）。缩放重采样问题建议另立任务（与本次 fixA 解耦）。

一句话：**修法 A（高度系 strength ×1.2、封顶 1.0）实测成立，可直接采用；scale≠100% 的残留是另一个（缩放重采样）问题。**

### 17.5 复跑命令

```bash
cd <REPO>
PY="<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe"

# 1) 生成全部 .kpp（现为 59 个：conv 28 + raw 24 + fixA 6 + ref 1）
PYTHONIOENCODING=utf-8 "$PY" tools/exp_krita_params.py --build

# 2) 串行渲染（--resume 只补缺失的 fixA 6 张，约 90 秒）
PYTHONIOENCODING=utf-8 "$PY" tools/exp_krita_params.py --render --resume

# 3) fixA vs PS / conv 三项指标
PYTHONIOENCODING=utf-8 "$PY" tools/exp_krita_params.py --fixa-compare

# 4) 更新清单（59 条，含 variant="fixA" 6 条）
PYTHONIOENCODING=utf-8 "$PY" tools/exp_krita_params.py --manifest
```

### 17.6 本次追加的文件

| 文件 | 说明 |
|---|---|
| `tests/baselines/exp/krita/exp__krita__fixA__linearHeight__*.png` | 6 张 fixA 实测图 |
| `tests/baselines/exp/krita/_work/kpp/exp__krita__fixA__*.kpp` | 6 个 fixA 预设 |
| `tests/baselines/exp/krita/_work/fixa_compare.json` | 逐张「fixA vs PS / conv vs PS / fixA vs conv」三项指标 |
| `tests/baselines/exp/krita/manifest.json` | 53 → **59** 条（新增 `variant="fixA"` 6 条，含 `krita_depth_strength`=0.12/0.72/1.0） |
| `tools/exp_krita_params.py` | 新增 fixA 变体与 `--fixa-compare`（未改既有行为） |
| `docs/验收报告/实验-Krita侧-报告.md` | 本节（追加，前面各节未改动） |

环境核对（fixA 渲染结束后实测）：无 `krita.exe` 进程；`pykrita` 仅 `preset_groups`；`kritarc` 的 `enable_bc_auto_render=0`；
`paintoppresets` 无 `exp__krita` 残留；`%TEMP%\bc_krita_gui_*` 为 0。

---

## 十八、v1.2.1 渲染回归（2026-11-19，追加任务）

### 18.1 目标与做法

验证 v1.2.1 工作区（未提交）的两处源码修复，用**真实 Krita 渲染**（路线 A）对拍 v1.2.0：

1. 高度系纹理 Strength = min(1.0, 1.2 × depth/100)（依据：PS W=12 vs Krita W=10，见 §17 fixA 实测）；
2. 内嵌纹理 PNG 追加 tEXt 标记（keyword=`brushconverter`、值=像素 sha256）+ 资源名改为 `tex_<内嵌PNG sha256前12位>.png`
   （规避 §8 的两个静默坑：同 md5 → 纹理被禁用；同名 → 串用库内图案）。

做法：

```bash
cd <REPO>
PY="<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe"
# 旧代码（v1.2.0）：只读 git 操作取出 HEAD 的 src（HEAD = e0f4f61，版本号仍是 1.2.0）
git archive HEAD src | tar -x -C "$TEMP/bc_v120_src"
# 两套 .kpp + 13 张真实渲染 + 指标 + 清单（串行渲染）
PYTHONIOENCODING=utf-8 "$PY" tests/baselines/exp/regress/run_regress.py
```

产物都在 `tests/baselines/exp/regress/`（`kpp/`、`png/`、`manifest.json`）；
命名区分：`old__*.kpp`（v1.2.0 转换）/ `new__*.kpp`（v1.2.1 转换）。
渲染口径与实验矩阵完全一致：100×100、stroke=60、压感 1.0（单 dab、圆心 (20,50)），串行。

5 个用例（每个 old/new 各一张，共 13 张渲染，单张 14.76–16.00 秒、总计 195.45 秒 ≈ 15.03 秒/张）：

| 用例 | 来源 | 说明 |
|---|---|---|
| `height_real` | `tests/测试用.abr` 的 **Hght** 预设「Kyle 的绘画盒 - 快乐 HB」（depth=11、scale=42、b=-55、c=37） | 真实预设原样转换 |
| `height_s100` | 同上，仅纹理 scale 改 100% | 去掉图案缩放，便于精确预测 |
| `multiply_mul` | `tests/测试用.abr` 的真实预设「KYLE 终极硬心铅笔」，仅混合模式改成 `'Mul '` | 仓库内 ABR **没有** multiply 纹理预设，故只改混合模式 |
| `multiply_calc` | `tests/定量测试专用.abr`（计算笔刷=渲染确定性），仅混合模式改成 `'Mul '` | multiply 回归的**主判据** |
| `height_calc_s100` | `tests/定量测试专用.abr`（linearHeight、depth=6），仅纹理 scale 改 100% | 高度系回归的**主判据**（计算笔刷覆盖整画布、base=1、可逐像素预测） |

### 18.2 高度系回归（主判据 `height_calc_s100`，确定性）

strength：**old=0.060 → new=0.072**（=min(1.0, 1.2×6%)）。

| 对比 | 超阈比例(|Δ|>2) | mean|Δ| | max|Δ| |
|---|---|---|---|
| old 渲染 vs new 渲染 | 82.23% | 5.11 | 19 |
| **new 渲染 vs new 预测**（texture_mask，depth=0.072） | **0.00%** | **0.47** | **1** |
| old 渲染 vs old 预测（texture_mask，depth=0.060） | 0.00% | 0.48 | 1 |
| old 渲染 vs **PS 参考**（同纹理/参数，PS 拟合 W=12） | 88.70% | 5.58 | 20 |
| **new 渲染 vs PS 参考** | **0.00%** | **0.47** | **1** |

结论：**修法 1 实测成立**。new 渲染与 new 预测、以及与应用 W=12 的 PS 参考都只差 ≤1 个灰度级（8-bit 量化残差）；
old 与 PS 参考差 mean 5.58 / max 20 / 88.70% 超阈，正是少乘 1.2 的 2q 量级偏差。
（注：本用例纹理是 1920×1920 的 Shape 2、depth=6、scale=100%、b=c=0；与 §17 的 64×64 实验纹理/depth=10 不同，
但 PS 参考用的是同一套 PS 拟合公式 `tools/ps_texture_reference.ps_mask`（高度系 W=12），量级结论一致：
§17 里 PS vs conv 43.31 → 修法后 1.39，这里 5.58 → 0.47。）

**真实 Hght 采样笔刷（`height_real` / `height_s100`）**：strength 0.11 → 0.132。

| 用例/口径 | old vs new 超阈 | mean|Δ| | max|Δ| | 噪声基线（同 kpp 两次渲染） |
|---|---|---|---|---|
| `height_s100` 全画布（10000 px） | 2.21% | 0.51 | 121 | max 37 / mean 0.12 |
| `height_s100` 有笔迹像素（583 px） | 37.91% | 8.73 | 121 | mean 2.09 / max 37 |
| `height_real` 有笔迹像素（605 px） | 41.82% | 8.98 | 79 | —（同源几何） |

该预设是**采样笔刷**，Krita 每次渲染本身带随机性（同一 kpp 两次渲染 max 差 37），因此不作为逐像素判据；
但 old/new 差（max 121 / 有笔迹 mean 8.73）明显大于渲染噪声（max 37 / mean 2.09），方向与「new 更强」一致。

### 18.3 multiply 回归（必须 0）

| 用例 | old vs new 超阈 | mean|Δ| | max|Δ| | 判定 |
|---|---|---|---|---|
| **`multiply_calc`（确定性，strength 两版都是 0.06）** | **0.00%** | **0.00** | **0** | **通过：10000/10000 像素相同** |
| `multiply_mul`（采样笔刷，对照） 全画布 | 3.83% | 0.90 | 104 | 见下（渲染噪声） |
| `multiply_mul` 有笔迹像素（595 px） | 64.37% | 15.06 | 104 | 同 kpp 噪声基线：mean 9.42 / max 88 |

**结论**：multiply 路径的修复（只改内嵌纹理字节与资源名，strength 路径不动）**实测 max|Δ| = 0**，满足「0 或 ≤1」。
采样笔刷 `multiply_mul` 的差异与「同一 kpp 连续渲染两次」的噪声同量级（max 104 vs 88、有笔迹 mean 15.06 vs 9.42），
属 Krita 对采样笔刷的渲染随机性，不是代码差异（确定性用例已排除）。

### 18.4 纹理是否真生效（最关键）

判据：Alpha **不是恒 255**，即 `unique > 1 且 min < 255`（若被静默禁用，纹理不参与，Alpha 会退化成笔尖自身的恒定值）。

| new .kpp | Alpha min | Alpha max | unique | 非零占比 | 判定 |
|---|---|---|---|---|---|
| `height_calc_s100` | 1 | 113 | 106 | 1.0000 | **纹理生效** |
| `height_real` | 0 | 255 | 95 | 0.0604 | **纹理生效** |
| `multiply_calc` | 246 | 255 | 10 | 1.0000 | **纹理生效**（multiply d6 + 反相亮纹理，接近不透明但仍有 10 级变化） |

命名/标记证据（manifest `metrics.texture_resources` / `textmarkers`）：

| 版本 | 图案资源名 | 内嵌 PNG 文本块 |
|---|---|---|
| new | `tex_287b7eaa3880.png`（Hght）、`tex_3ff43c49f112.png`（multiply）、`tex_bb586e4480d4.png`（calc 两例） | tEXt：`brushconverter` + `sha256=…`（逐张列在 manifest） |
| old | `tex_c02fddff.png`、`tex_2ab8ff79.png`、`tex_66e2987f.png` | 无 |

（注意 old 版 `multiply_calc`/`height_calc_s100` 的图案名正是 §8 撞过的 `tex_66e2987f.png`——旧代码仍用 uuid 命名；
本次因为库内同名图案内容恰好与源纹理同像素（同一 ABR 的 Shape 2），old 渲染内容没变形，但这是运气不是保证。）

**「图案库里仍有同内容图案」前提下的坑 A/B**（库内 `测试用纹理.png` md5 `5e748763…` 仍在，`in_krita_library=true`）：

| 用例（同一份源纹理字节、multiply depth=0.6） | Alpha min/max | unique | 判定 |
|---|---|---|---|
| old：**唯一名字 + 逐字节原文**（无 tEXt） | 255 / 255 | 1 | **纹理被静默禁用** → 复现 md5 坑 |
| old：**旧命名 `tex_66e2987f.png` + 逐字节原文** | 1 / 97 | 91 | 纹理“生效”但**串用了库内同名图案**（复现名字坑） |
| **new：tEXt 标记 + `tex_41da8e73cc0e.png`（sha 命名）** | 0 / 153 | 147 | **正常生效**，不受库内同内容图案影响 |

结论：**修法 2 实测成立**——md5 撞车（静默禁用）与文件名撞车（串用库内图案）两个坑都被 tEXt 标记 + 内容哈希命名规避。

### 18.5 回归结论

1. **高度系修复通过**：new 渲染与「W=12 的 PS 参考/掩码预测」一致（0.00% 超阈、max 1）；old 的 2q 偏差（88.70% 超阈、max 20）被消除。
2. **multiply 修复通过**：确定性用例 old vs new **max|Δ|=0（10000 像素全同）**；采样笔刷用例的差异落在 Krita 渲染噪声内。
3. **纹理生效通过**：新 .kpp 的 Alpha 不再恒 255（unique 10–106），且在图案库仍持有同内容图案的环境下，
   「原字节 + 旧命名」会禁用或串味，「tEXt + sha 命名」正常生效——两个静默坑均被覆盖。

可比性边界：

- `tests/baselines/exp/ps/exp__ps__linearHeight__*` 中最接近的参数是 `d10_s100_b0_c0`，但那是 64×64 实验纹理 + 200px 计算笔刷 + b=c=0；
  本节 `height_calc_s100` 是 1920×1920 Shape 2 纹理 + depth=6，`height_real` 更是采样小笔刷 + b=-55/c=37 → **不能与 PS 实验图逐像素比**；
  因此本节用「同一纹理、同一参数」的 PS 拟合实现（W=12）做参考，并援引 §17 同参数实测（43.31 → 1.39）作为趋势佐证。
- Krita 对**采样笔刷**的渲染不是逐次确定的（本次实测同 kpp 两次渲染 max 差 37–88）；
  凡是需要「逐像素相同」的回归，必须用计算笔刷（`定量测试专用.abr`）或按噪声基线判定。

### 18.6 环境清理核对（本次实测）

```text
tasklist /FI "IMAGENAME eq krita.exe" /NH   → No tasks are running which match the specified criteria.
ls "$APPDATA/krita/pykrita"                  → preset_groups  preset_groups.desktop（无 bc_auto_render）
grep -c enable_bc_auto_render "$LOCALAPPDATA/kritarc" → 0
grep -n sessionOnStartup "$LOCALAPPDATA/kritarc"      → sessionOnStartup=0
```

如实记录一次环境意外：本任务中有一轮 `run_regress.py` 被执行超时的外层工具中断（渲染进行中），
导致 `kritarc` 残留 `enable_bc_auto_render=true` 与 `sessionOnStartup=2`；已用脚本还原为
`sessionOnStartup=0` 并删除该项，复核为 0（上面输出）。未发现 paintoppresets / patterns / TEMP 残留。

### 18.7 本次新增/修改的文件

| 文件 | 说明 |
|---|---|
| `tests/baselines/exp/regress/make_kpp.py` | 双版本转换（old/new）：5 个用例的 .kpp |
| `tests/baselines/exp/regress/make_pit.py` | 纹理 md5/命名坑的字节级 A/B .kpp |
| `tests/baselines/exp/regress/run_regress.py` | 串行渲染 + 指标 + manifest 的主控脚本 |
| `tests/baselines/exp/regress/probe_abrs.py`、`diag_*.py`、`_fix_kritarc.py`、`_fill_manifest_elapsed.py` | 选材探针/诊断/环境修复脚本（保留作证据） |
| `tests/baselines/exp/regress/kpp/` | `old__*.kpp` / `new__*.kpp` 共 13 个 |
| `tests/baselines/exp/regress/png/` | 13 张渲染图 + 3 张重复渲染噪声基线（`*__rerun2.png`） |
| `tests/baselines/exp/regress/manifest.json` | 产物、版本、strength、差异、噪声基线、预测、PS 参考、纹理资源/标记、pit A/B 全记录 |
| `docs/验收报告/实验-Krita侧-报告.md` | 本节（追加，前面各节未改动） |

未修改 `src/`（只读使用工作区与 `git archive` 的旧版）、既有 baselines、其他代理报告；无 git 写操作。
