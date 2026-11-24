# brushConverter
Convert Photoshop brush sets (.abr) into Krita presets (.kpp) and resource packs (.bundle). Published version: v1.5.0; Windows GUI and CLI downloads are available below.
把 Photoshop 笔刷转成 Krita 预设与资源包。**已发布 v1.5.0** · [正式版下载](https://github.com/yxm1122/brushConverter/releases/tag/v1.5.0)

## 下载与三步上手

不熟悉命令行就选图形界面（GUI）；解压整个压缩包后使用，不需要另装 Python。

| 你需要什么 | 在正式版下载页选择 | 使用入口 |
|---|---|---|
| 点按钮转换（推荐新手） | `brushConverter-v1.5.0-win64.zip` | [GUI 分步操作](<docs/user-guide.md#gui-convert>) |
| 用命令批量转换 | `brushconverter-cli-v1.5.0-win64.zip` | [CLI 分步操作](<docs/user-guide.md#cli-convert>) |

1. **转换**：用本工具打开原始 `.abr`，选择笔刷、格式和一个新的输出目录；新手先选 `.bundle` 资源包。
2. **导入**：把生成的 `.bundle` 导入 Krita，**不是把原始 `.abr` 直接导入**。另有独立 `.kpp` 的导入办法，见[导入与检查](<docs/user-guide.md#import-krita>)。
3. **遇到问题**：先保留完整报错，再按[排错手册](<docs/troubleshooting.md>)找相同现象；缓存重建是有代价的可选兜底，不是必做步骤。

操作说明以 Windows 64 位、Krita 5.3.3 的既有记录为参考；其他系统与 Krita 版本未验证。完整说明见[用户指南](<docs/user-guide.md>)；[GUI 外观示意](<docs/screenshot-gui.png>)仅作参考，不代表计划功能已实现。

## 已知限制（摘要）

- **转换不等于逐像素复刻 Photoshop**：纹理混合模式（blend mode）有近似、无等价承载或有条件成立的情况，不能把“能转换”理解为所有模式完全准确。详见[参数映射参考](<docs/parameter-mapping.md>) §7.2、§10。
- **部分设置已烘焙（bake）进像素**：计算笔刷的硬度，以及预放大纹理的缩放/相位，不能在 Krita 中按原 PS 语义继续调整；双笔刷、颜色动态、湿边等尚未映射。见[转换取舍](<docs/user-guide.md#tradeoffs>)与[映射参考](<docs/parameter-mapping.md>) §2、§6、§7。
- **边界残差仍存在**：硬度、散布端点、hardMix、纹理缩放等需按适用条件判断；逐条证据与待验证项只维护在[映射参考](<docs/parameter-mapping.md>) §10，不在首页另抄一套数字表。
- **输出保护有范围**：资源包有同内容复用、冲突改名与回读校验；独立 `.kpp` 仍可能覆盖同名文件，请每次选新目录。详见[输出与安全边界](<docs/user-guide.md#outputs>)。
- **候选 v1.6.0（未发布）**：这是本地待发布工作，不是上方下载包。候选已提交的变化见[版本说明](<releases/RELEASE_NOTES.md>)首段；逐支可编辑性开关、取消、元信息与标签等尚未落地的部分只标[计划中](<docs/user-guide.md#candidate-status>)，不提供假按钮或假命令。

## 升级必读（已发布版本的历史记录）

以下保留当时的升级口径，不作为回退建议；未发布候选对旋转抖动等的后续变化另见[版本说明](<releases/RELEASE_NOTES.md>)首段。每次重转都请使用新的输出目录。

### v1.5.0 升级必读

- **旧 `.kpp` / `.bundle` 必须重转**：v1.5.0 三批改动（计算笔刷类型、散布/旋转换算、纹理预缩放）都改变产物字节与语义。
- **计算笔刷类型变了**：`auto_brush` → `png_brush`（按 PS 硬度剖面生成位图；尺寸=PS 实际足印——PS 标称 200 的笔在 Krita 显示 290/250/204）。代价：Krita 里 **fade/硬度不可再调**，每支多 3–12 KB。
- **散布数值 ×2、旋转抖动 j<50 ×2**：`ScatterValue = jitter/200`（旧 ÷400）、`RotationValue = min(1, jitter/50)`（旧 ÷100）。旧产物的散布偏小一半、小角度抖动偏小约 2 倍。
- **纹理缩放 >100% 改为预缩放内嵌**：先按 PS 同核放大再内嵌、`Scale` 写 1.0；单预设体积 +0.8 KB~+434 KB，资源名/内容哈希随之变化；缩放/相位不可再在 Krita 里调。
- **legacy 开关已删除**：`BRUSHCONVERTER_TEXTURE_FORMULA` 与旧公式分支已移除，无法回退到 v1.1.2 公式；整体回退只能 `git revert` + 重转。
- 传感器 XML 写法与 v1.4.0 相同（17 组 `*Sensor` 写合法 `pressure` 占位，禁止空 `<params/>`）。

### v1.4.0 升级必读

- **非方形采样笔尖间距会变**：`spacing = Spcn/100 × 短边/长边`（补偿「补白成正方形」的基准差）。非方形笔刷重转后中心距更小、更接近 PS；严格正方形与计算笔刷不变（见 parameter-mapping §1、§10.1）。
- **旧 `.kpp` 必须重转**：基础模板 17 组 `*Sensor` 写合法 `pressure` 占位，未映射动态用 `Pressure*=false` + `*UseCurve=false` 关闭，**禁止空 `<params/>`**（空 XML 会被 Krita 当成有定义又因 sensorId 为空早退，曲线/散布不读；2026-11-21 修正）。旧产物重转后再导入（见 parameter-mapping §4 传感器默认注）。

## 继续阅读

- 普通用户：[用户指南](<docs/user-guide.md>) → [导入 Krita](<docs/user-guide.md#import-krita>) → [按现象排错](<docs/troubleshooting.md>)。
- 维护者：[开发指南与文档索引](<docs/developer-guide.md>)、[参数映射参考](<docs/parameter-mapping.md>)、[验证体系说明](<docs/验证体系使用说明.md>)。
- 升级与版本差异：[版本说明](<releases/RELEASE_NOTES.md>)；当前源码不等于正式下载包，不能仅凭产物文件名判断来源。

项目的 MIT 许可不代表原始笔刷素材的许可；请只转换和分享你有权使用、分发的素材。
