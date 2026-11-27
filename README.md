# brushConverter
Bring your favorite Photoshop brushes to Krita. Convert ABR sets into import-ready presets and bundles, with brush tips, textures, pressure response, and scatter mapped to bring strokes closer to their original look. Per-brush options let you balance visual similarity and editability—less setup, more creating.
把熟悉的 Photoshop 笔刷带进 Krita。将 ABR 笔刷集转成可导入的预设与资源包，不只提取笔尖，还映射纹理、压感和散布等常用效果，尽量接近原笔触。支持逐支在外观还原与编辑空间之间取舍，少折腾设置，多花时间画画。**已发布 v1.6.1** · [正式版下载](https://github.com/yxm1122/brushConverter/releases/tag/v1.6.1)

![brushConverter v1.6.1 English GUI with synthetic demo brushes](<docs/screenshot-gui-en.png>)

*English GUI · v1.6.1 · Synthetic demo brushes / 英文界面，使用合成演示笔刷。*

## 下载与三步上手

不熟悉命令行就选图形界面（GUI）；解压整个压缩包后使用，不需要另装 Python。

| 你需要什么 | 在正式版下载页选择 | 使用入口 |
|---|---|---|
| 点按钮转换（推荐新手） | `brushConverter-v1.6.1-win64.zip` | [GUI 分步操作](<docs/user-guide.md#gui-convert>) |
| 用命令批量转换 | `brushconverter-cli-v1.6.1-win64.zip` | [CLI 分步操作](<docs/user-guide.md#cli-convert>) |

> **Windows 运行提示**：本项目 Windows 程序暂未进行代码签名，首次运行时可能出现 SmartScreen“Windows 已保护你的电脑 / 发布者未知”提示，这不等于检测到病毒。请仅从本项目[正式发布页](https://github.com/yxm1122/brushConverter/releases)下载，确认来源可信后，点击“更多信息”→“仍要运行”（按钮已显示时可直接点击）。无需关闭 SmartScreen 或 Defender；若出现明确的病毒检测，请停止运行并反馈。

1. **转换**：用本工具打开原始 `.abr`，选择笔刷、格式和一个新的输出目录；新手先选 `.bundle` 资源包。
2. **导入**：把生成的 `.bundle` 导入 Krita，**不是把原始 `.abr` 直接导入**。另有独立 `.kpp` 的导入办法，见[导入与检查](<docs/user-guide.md#import-krita>)。
3. **遇到问题**：先保留完整报错，再按[排错手册](<docs/troubleshooting.md>)找相同现象；缓存重建是有代价的可选兜底，不是必做步骤。

操作目标为 Windows 64 位、Krita 5.3.3；其他系统与 Krita 版本未验证。完整说明见[用户指南](<docs/user-guide.md>)；上方[英文 GUI 截图](<docs/screenshot-gui-en.png>)来自 v1.6.1 的真实窗口与合成演示数据，不代表 Photoshop/Krita 笔触效果或完整实机验收。

## 已知限制（摘要）

- **转换不等于逐像素复刻 Photoshop**：纹理混合模式（blend mode）有近似、无等价承载或有条件成立的情况，不能把“能转换”理解为所有模式完全准确。详见[参数映射参考](<docs/parameter-mapping.md>) §7.2、§10。
- **外观优先有编辑代价**：v1.6.0 默认按条件位图化/预缩放/亮度越界烘焙，可逐支关闭改走受限原生字段，并承担外观或范围代价；不能同时承诺完全复刻与完全可编辑。双笔刷、颜色动态、湿边等仍未映射。见[转换取舍](<docs/user-guide.md#tradeoffs>)。
- **边界残差仍存在**：硬度、散布端点、hardMix、纹理缩放等需按适用条件判断；逐条证据与待验证项只维护在[映射参考](<docs/parameter-mapping.md>) §10，不在首页另抄一套数字表。
- **输出保护须看版本**：v1.6.0 的 bundle 与独立 KPP 均先暂存验证、复用/冲突改名并保护旧文件；取消是协作请求，最终交付期不可取消，不虚称多文件系统原子性。v1.5.0 及更早版本的独立 KPP 仍可能覆盖。见[输出与安全边界](<docs/user-guide.md#outputs>)。
- **资源包与散装 KPP 是同一批预设的两种形态，不要同时导入 Krita**：两者文件名相同，Krita 按文件名跨来源去重、后导入一方被静默忽略；删掉生效那份并重启后预设消失、只余标签（2026-11-27 实测）。要单独用散装 KPP，请先移除同批 bundle。见[导入 Krita](<docs/user-guide.md#import-krita>)。
- **v1.6.0 已发布，不等于所有项目全验**：上方正式包包含三开关、筛选/隐藏选择、安全取消、包信息与逐支标签/追溯。2026-11-27 使用者集中验收记录为 30 项通过、6 项未验并条件放行；未验不写通过，详见[正式版与验收边界](<docs/user-guide.md#candidate-status>)、[CLI 配置](<docs/user-guide.md#cli-convert>)及[包信息/标签范围](<docs/user-guide.md#metadata-tags>)。
- **CLI 重定向已知问题**：输出接管道或重定向且系统代码页为 936 时，打印含 U+2212 的替代理由可能在产物已交付、回读 OK 后失败并退出 1；真实控制台直连正常。先保留报错并核对产物，不盲目覆盖重转，见[排错手册](<docs/troubleshooting.md#conversion-error>)。

**v1.6.0 升级必读**：亮度越界命中区、所有旋转随机抖动的旧 KPP 及所属 bundle 须从原 ABR 重转；只改元信息/标签产生新包身份是另一件事，不表示映射算法又变了。详见[v1.6.0 升级说明](<releases/RELEASE_NOTES.md>)；不恢复 legacy 公式或双随机源。

**v1.6.0 历史发布验证与保留边界（2026-11-27）**：资源包复用已按完整 v2 身份（scheme+64 位 sha256）比较，定向反例与发布前全量 1149/0/0/0（两次一致）、verify_all 235/0/0 已留证；两份正式包已上传并核对。发布 ZIP 扫描的 14 个 block 经使用者接受并披露，6 项人工未验仍保留，不因发布改写为全验；详见[版本说明的限制与验证](<releases/RELEASE_NOTES.md#release-v160>)。

## 升级必读（已发布版本的历史记录）

以下保留当时的升级口径，不作为回退建议；v1.6.0 对旋转抖动等的后续变化另见[版本说明](<releases/RELEASE_NOTES.md>)首段。每次重转都请使用新的输出目录。

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
- 维护者：[开发指南与文档索引](<docs/developer-guide.md>)、[参数映射参考](<docs/parameter-mapping.md>)、[验证体系说明](<docs/验证体系使用说明.md>)、[CI 与覆盖边界](<docs/ci.md>)。
- 升级与版本差异：[版本说明](<releases/RELEASE_NOTES.md>)；当前源码不等于正式下载包，不能仅凭产物文件名判断来源。

项目的 MIT 许可不代表原始笔刷素材的许可；请只转换和分享你有权使用、分发的素材。
