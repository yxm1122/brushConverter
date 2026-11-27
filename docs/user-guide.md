# 用户指南：从 ABR 到 Krita

This guide covers the published v1.6.1 Windows downloads, per-brush choices, safe cancellation, bundle metadata and tags. Version 1.6.1 improves GUI layout without changing the v1.6.0 conversion mappings. It separates older-version risks from current behavior and retains the known issues and six untested acceptance items; release does not promise identical Photoshop rendering or complete acceptance.

[返回首页](<../README.md>) · [按现象排错](<troubleshooting.md>) · [版本说明](<../releases/RELEASE_NOTES.md>)

## 一、先选对版本和文件

- **当前正式版 v1.6.1（2026-11-27）**：从[正式版下载页](https://github.com/yxm1122/brushConverter/releases/tag/v1.6.1)获取 Windows 64 位压缩包；新手选 GUI 包，重复批量操作可选 CLI 包。完整解压，不在压缩包内运行、不只复制 exe；发行包不要求另装 Python。
- **本文的当前操作适用于 v1.6.1（同样适用于 v1.6.0）**：三开关、筛选、取消及元信息/标签已随两份正式包发布；v1.5.0 及更早版本不具备这些新增控件或接口。源码入口另见[源码运行](<#source-run>)，不是 exe 用户的必做步骤。
- 原始 `.abr` 是本工具的输入；`.bundle` 适合整包导入，`.kpp` 是单支预设。PNG 笔尖不是完整预设，**不要将原始 ABR 直接导入 Krita 代替转换产物**。
- 保留原始 ABR 与旧产物，每次先选新的空输出目录，不将输出设到原始素材目录或已有资源库。作者、版权和许可由你确认；项目 MIT 不会授权第三方笔刷的分享。
- 适用目标为 **Windows 64 位、Krita 5.3.3**；其他环境未验证。2026-11-27 使用者集中验收记录为 **30 项通过、6 项未验并条件放行**，不是所有功能/组合均通过；具体未验项、已知问题与发布边界见[第七节](<#candidate-status>)。源码或 XML 回读不能代替实机通过。

<a id="gui-convert"></a>
## 二、图形界面转换（新手推荐）

### 当前正式版 v1.6.1 的基本路径

完整解压 GUI 包 `brushConverter-v1.6.1-win64.zip`，运行其中的 `brushConverter.exe` → 打开 ABR → 勾选笔刷 → 选格式和新目录 → 阅读警告后开始转换 → 核对非零预设数与回读结果，再[导入 Krita](<#import-krita>)。下述选择、三开关及取消控件属于当前正式版；v1.5.0 及更早版本没有这些新增按钮。

### v1.6.0：选择与逐支调整

1. 打开 ABR，等待解析结束；先核对名称、缩略图和警告。v1.6.1 的列表会随窗口宽度重新换行和计算行高，长卡片可连续滚动；“查看/调整”会定位到当前笔刷说明。解析报错就保留原文，不继续导入半成品。
2. 在列表勾选要导出的笔刷。**行复选框只决定导出选择**；点击笔刷行或“查看/调整”，在“逐支处理与编辑能力”查看三项处理、适用原因、条件默认和开启/关闭的后果，见[三开关取舍表](<#tradeoffs>)。不适用项不能强开。
3. 可搜索名称、稳定 ID 或源序号，筛选“全部笔刷 / 仅已选 / 计算笔刷 / 采样笔刷 / 有适用处理”，按源顺序或名称升降序排序。稳定 ID 是这支笔刷的身份，不是显示名或筛选后的行号；同名笔刷不会因此共用设置。源序号指解析去重后、筛选前的顺序。
4. **“全选可见项”“取消可见项选择”只作用于当前可见行**。搜索、筛选、排序和切语言不清掉隐藏选择、逐支设置或标签；“总已选 / 隐藏已选”会提示实际数量，隐藏已选仍会导出。要全不选，先清搜索、恢复“全部笔刷”，再取消可见项选择。
5. 启动后载入、换文件、点击“重新载入”都按源条件重算三开关，**不记忆上次手动值**；重新载入也重置选中状态与本批元信息/标签。只是搜索、排序、切语言不重置；不要用重载来刷新列表而期待保留调整。
6. GUI 一次二选一：默认 `.bundle` 或独立 `.kpp`，不会同时输出两种。选 bundle 后可填[包信息与标签](<#metadata-tags>)；切成 KPP 时该表停用且不写出，同一文件切回 bundle 可继续编辑，重载则恢复默认。
7. 选择新目录，点击“开始转换”。阅读未映射警告及“导出前逐支确认”，检查**含隐藏已选的总数、预计跳过和受限替代**。这是预计清单，不是成功记录；“返回调整”不会开始导出，“确认并导出”才继续。
8. 运行时只认真实阶段：“已处理 N/M 支（含跳过）”不是整批交付百分比。完成后核对交付状态、预设数、真实纹理数、改名/复用情况，以及基于回读的元信息和标签摘要；处理过不等于已交付，0 支、全部跳过或失败都不能算成功。

<a id="cancel-recover"></a>
### 取消、关窗与再次运行（v1.6.0）

- 点击“取消本批”是**请求协作取消**；当前解析或单支内存处理会到安全检查点才停。界面会区分“已接收请求”“正在安全停止”“已取消”；输入冻结到后台线程结束，不重复点开始、不强杀进程。
- 最后一次安全检查前仍可请求取消；**最终交付中不可取消**。此时收到请求仍完成本批，并如实报告迟到请求，不能把已完整交付写成“已取消”。
- 已确认取消且清理成功时，本批不交付半成品，原文件保留。解析取消可重新载入；转换取消后等线程结束，再核对原有选择/开关/标签、换新目录重新开始，**没有断点续转**。若点击重载或换源，手动设置需重新填写。
- 后台工作时关窗会问“等待安全停止后关闭”或“取消本次关闭”。前者等待安全结束，不强杀；取消关闭只保留窗口，**不能撤回已经发出的停止请求**。交付期只能等结果。
- 报“部分受阻”、清理失败或状态未确认时，先保留原始错误与暂存/受阻清单，不当成安全取消完成；不要自动删除暂存目录。按[失败恢复步骤](<troubleshooting.md#cancel-recover>)核对现场。

<a id="cli-convert"></a>
## 三、命令行转换（可选）

CLI 不会替你启动 Krita 或导入资源。下列输入名是示例，须改为你实际拥有的 ABR；不存在就停止，不去找“仓库自带的我的笔刷”。

### v1.6.0：基本命令（兼容旧版用法）

在 CLI 包的完整解压目录打开 PowerShell：

~~~powershell
.\brushconverter-cli.exe convert --help
# 默认同时生成 bundle 与独立 KPP；只要资源包可加 --no-standalone。
.\brushconverter-cli.exe convert '.\input\我的笔刷.abr' -o '.\output\run-01'
$LASTEXITCODE
~~~

v1.6.0 的 exe 与源码入口均支持相同参数，但**无配置 CLI 不等于 GUI 空表单**：不加 `--metadata-file` 走兼容口径，不启用新表单元信息、原生逐支标签和包内追溯；不能据此声称包里已有本轮信息。

### v1.6.0：逐支配置与标签示例

以下用源码入口展示完整示例：先在项目根目录另开 PowerShell，**在这个同一窗口中**按[源码运行](<#source-run>)设置并核验 `$PY`，再继续；普通变量不会跨新窗口继承。此完整脚本含 Python 环境与临时目录核验，限已有源码环境的用户使用；exe 用户可按上面的基本命令转换，不必为此安装源码环境。示例只选第一支并关闭其所有适用处理，演示受限编辑优先及[外观代价](<#tradeoffs>)。要保持条件默认，**删去下面整个 foreach 关闭循环，保留 `$Switches = @{}`**；不是原样运行该循环后仍为空表。

~~~powershell
$Abr = '.\input\我的笔刷.abr'  # 改为实际输入；不修改源文件。
$Work = Join-Path (Get-Location).Path '.tmp/user-export-01'
if (Test-Path -LiteralPath $Work) { throw '目录已存在，请换一个新目录' }
if (-not (Test-Path -LiteralPath $Abr -PathType Leaf)) { throw '先填写实际 ABR 路径' }
New-Item -ItemType Directory -Path $Work | Out-Null
$env:TEMP = New-Item -ItemType Directory -Path (Join-Path $Work 'temp') | Select-Object -ExpandProperty FullName
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
& $PY -B -c "import os,tempfile; assert os.path.realpath(tempfile.gettempdir()) == os.path.realpath(os.environ['TEMP'])"
if ($LASTEXITCODE -ne 0) { throw '临时目录未隔离，停止' }

# 只列清单，不转换；不能同时加配置或 --preset-id。
$Raw = & $PY -B cli.py convert $Abr --list-presets
if ($LASTEXITCODE -ne 0) { throw '清单读取失败，保留原始输出' }
$List = ($Raw -join [Environment]::NewLine) | ConvertFrom-Json
if (@($List.presets).Count -eq 0) { throw '源没有可选预设' }
$First = $List.presets[0]
$Id = $First.id
$Switches = @{}
foreach ($Key in 'rasterize_computed','prescale_texture','bake_texture_bc') {
    if ($First.applicable.$Key) { $Switches[$Key] = $false }
}
$Options = @{
    format='brushconverter.export-options'; version=1
    source_digest=$List.source_digest
    presets=@(@{ id=$Id; switches=$Switches })
}
$OptionsFile = Join-Path $Work 'options.json'
$Options | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $OptionsFile

# 元信息配置与开关配置分开；空作者/许可表示未知，不借用项目 MIT。
# 下面只演示标签，不代表素材已有分享许可；source=[] 明确禁用来源标签。
$PerBrush = @{}
$PerBrush[$Id] = @{ added=@('练习'); removed=@('共享') }
$Metadata = @{
    format='brushconverter.export-metadata'; version=1
    source_digest=$List.source_digest
    metadata=@{ name='示例包'; description='自用转换'; author=''; license=''; package_version='1.0' }
    tags=@{ common=@('共享'); source=@(); presets=$PerBrush }
}
$MetadataFile = Join-Path $Work 'metadata.json'
$Metadata | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $MetadataFile
& $PY -B cli.py convert $Abr -o (Join-Path $Work 'output') --preset-id $Id --options-file $OptionsFile --metadata-file $MetadataFile --no-standalone
$LASTEXITCODE
~~~

此例最终仅第一支导出为 bundle，标签为“练习”，“共享”因逐支移除而不写入。路径含空格用引号；不要手改稳定 ID 或把姓名当 ID。需要多支时重复 `--preset-id` 并用不同 ID，仍按源顺序输出。

- 开关 JSON：`format=brushconverter.export-options`、`version=1`；`presets` 是配置覆盖，不是选择清单。省略项用条件默认，空 `presets=[]` **不是零选中**；未选中项也完整校验，不能把坏配置藏在里面。
- 元信息 JSON：`format=brushconverter.export-metadata`、`version=1`；至少有 `metadata` 或 `tags` 一节。要与 GUI 空表单同口径，显式写 `"metadata":{},"tags":{}`；不是省略整个配置。两类文件不能混用字段，每类只给一份。
- `metadata` 只允许 `name/description/author/license/package_version`；`source_digest` 可选但建议取本源完整摘要。`tags.source` 省略=ABR 文件名主干，空数组=禁用，其他数组=替换；`tags.presets` 按 ID 写 `added/removed`。未知或未选中的标签 ID、非法字符、源摘要或版本不符均整批拒绝，不静默丢字段。
- UTF-8 JSON 可带 BOM；布尔值只能 `true/false`，拒绝重复键、未知字段/开关和不适用强开。ABR 字节变化后应重列 ID，不沿用旧配置。
- Ctrl+C（Windows 也可 Ctrl+Break）请求协作取消；交付前取消退出 130，迟到取消完成本批时返回实际结果。不能用强杀代替取消。

### 可选命令或选项：逐项说明后果

| 选择 | 实际后果 | 适用提醒 |
|---|---|---|
| `convert … --no-standalone` | 只交付资源包，包内仍有预设 | 想整包导入时可选 |
| `convert … --no-bundle` | 只写独立 KPP | v1.6.0 中与 `--metadata-file` 冲突会退出 2，不假装写了标签 |
| 同时使用两个 `--no-*` | 两种输出都关掉，报错 | 不是试运行/只检查模式 |
| `--list-presets`（v1.6.0） | 只输出源摘要、ID、源序号、适用性和默认值 | 不写输出目录、不打印原名，不与选择/配置混用 |
| `info '实际输入.abr'` | 只读显示版本/区段/采样笔尖 | 无采样笔尖返回 2，不代表计算笔刷不能转 |
| `extract … -o '新目录'` | 提取采样笔尖 PNG 与信息 JSON | 不是转换必做步骤，不享有 `convert` 的整批事务保护；只用新目录 |
| `extract … --contact-sheet` | 额外写笔尖总览图 | 不是完整笔触对照，也不是 KPP |

表中的省略号不是输入字符。v1.6.0 的 `convert` 退出码：**0 成功/复用、1 输入/内部错误、2 配置/选择/零源/全部跳过、3 安全写出或校验失败、130 取消**。有部分跳过时仍需逐项核对，退出 0 不等于整源全转或外观通过；详细恢复见[排错手册](<troubleshooting.md#conversion-error>)。

<a id="outputs"></a>
## 四、输出在哪，哪些保护已经实现

| 输出 | 在所选输出目录中的位置 | v1.6.0 保护 |
|---|---|---|
| 资源包 | 形如 `源名_v版本_内容短哈希.bundle` | 同卷暂存、校验后交付；同内容复用不重写，冲突顺延改名 |
| 独立预设 | `kpp/` 子目录，每支一个 `.kpp` | 同样先暂存验证，复用/改名，不覆盖已有文件 |

v1.6.0 两种输出均受同一整批事务保护，**最终多文件交付不是操作系统原子提交**；失败时按清单回滚本批新文件，若删除/回滚受阻会保留明细并报告，不能承诺任何故障下全有或全无。只在确认“已取消”且清理完成时，才说本批未交付；见[取消恢复](<#cancel-recover>)。

**v1.6.0 复用纠偏（定向与发布前全量验证）**：不同说明的v2资源包即使8位短哈希相同，也不再按旧包复用；当前实现比较完整逻辑身份（scheme+64位sha256）。沿用原反例复跑，第二份交付为独立`_2`包、`reused=0`、回读身份属于第二份，第一份字节与mtime不变；真实碰撞负例已进定向测试。见[版本说明](<../releases/RELEASE_NOTES.md#已知限制>)。发布前全量已于 2026-11-27 重跑通过（1149/0/0/0，两次一致），随后完成批准对象的构建、隐私处置与发布；人工验收保留 30 项通过、6 项未验条件放行的边界。该修复不是仅换目录或改文案，不降低不同内容不得误复用的承诺。

**不要把 v1.6.0 的保护套给旧版**：v1.5.0 及更早版本的独立 KPP 仍可能覆盖同名文件。正式分发包仍保留追溯构建状态字面值 `unreleased`，本次没有重打包；它不表示 v1.6.0 尚未发布。反之，文件名中的 1.6.0 或短哈希也不能单独证明来源，应核对正式下载渠道、发布提交和包摘要。当前 CLI 为保护隐私不回显文件名/绝对路径，请按自己传入的目录、产物编号和摘要找文件。

<a id="metadata-tags"></a>
### 包信息、标签与追溯（v1.6.0，仅 bundle）

1. 在“资源包信息（仅 .bundle）”填包名、说明、原作者/版权归属、素材许可与资源包版本。包名默认来自 ABR 名称，来源只记文件名（只读，不含目录）；空说明用 `Converted from Photoshop ABR`。作者、许可和包版本可空，不读系统个人身份，不继承上次文件输入，不自动写日期。
2. 作者和许可**只按你确认的本次信息写入**，不推断用途或授权，也不自动套 MIT。资源包版本是你填的素材版本，不是程序版本 1.6.0，也不是 bundle 格式版本 1。
3. 在“公共与来源标签”每行写一个标签；内部空格属于同一标签，连续空白会归一。来源默认是 ABR 文件名主干，可改名或整批清空；也可仅在某支的“单支移除”中排除。
4. 点击某支，在“单支追加”“单支移除（优先）”增删。最终集合=（公共 ∪ 来源 ∪ 单支追加）−单支移除；移除优先于追加，大小写不敏感去重，中文按 NFC 归一、稳定排序。改名可移除旧标签再追加新标签。检查“最终标签及来源关系”，公共修改同样影响隐藏已选；未选中或跳过的笔刷不会交付标签关联。
5. 标签写到 bundle 内**每个预设的原生关联**，不是用包级标签冒充逐笔关系；独立 KPP 不承载这套标签、包信息或追溯。Krita 5.3.3 的已检查项目已进入使用者集中验收记录，未验边界见[第七节](<#candidate-status>)；不能扩大为所有标签/重启组合均通过，更不能仅以“XML 有字段”代替实机效果。
6. 按目标 5.3.3 源码，说明/作者/许可有原生详情字段；包内标题不作为管理器标题，资源包版本、来源与详细追溯不在该界面显示。这些字段的显示范围以注明版本的源码参考为依据，不扩大成任意字段均已实机显示的承诺。Krita 重存可能丢失扩展字段及追溯，保留本工具原始导出包，不只留重存副本。
7. 启用新元信息链后，包内追溯记录源文件名/摘要、工具版本与构建状态字面值（本次正式包仍为 `unreleased`）、稳定 ID、最终标签、**实际命中的处理及代价**；不是开关快照，不把跳过当命中。只改包说明或标签也会形成新逻辑身份，即使 KPP 字节没变；旧包不覆盖、不冒作相同新版包复用。重复同源同配置应复用相同内容，不写本次导出时间。

<a id="import-krita"></a>
## 五、导入 Krita 并核对来源

**只导入本工具生成的资源包或预设，不直接导入原始 ABR。** 同名的 ABR 直读库与转换产物是两套来源。

### 路线 A：导入资源包（推荐）

1. 确认本批成功交付、预设数非零且无回读失败，找到输出目录中的实际 bundle。
2. 在 Krita 的“设置 → 管理资源”相关入口使用资源包导入；菜单名称可能随语言或版本变化，找不到就保留实际界面信息，不乱点或先重建缓存。
3. 核对名称/缩略图，排除原始 ABR 同名预设。对启用新标签链的 v1.6.0 包，按最终标签集合核对每支筛选结果，含移除标签不再命中、同名笔刷不串用；不能以机器回读代替这项验收。
4. 检查代表笔触与可编辑性，正常退出、重启后再看标签/资源。发布时的已验/未验边界见[第七节](<#candidate-status>)，不把已验样例推广为所有组合；没有新元信息配置的旧式 CLI 包，不应期待新增标签。

### 路线 B：独立 KPP（可选）

仅在需要逐个管理时使用。先在 Krita 中确认实际资源目录，正常退出后，将本次输出 `kpp/` 内预设复制到其 `paintoppresets/` 再启动。Windows 常见默认位置为 `%APPDATA%\krita\paintoppresets`，自定义/便携配置可能不同。

**后果**：v1.6.0 转换器保护自己的输出，不代表你手动复制进 Krita 时也会防覆盖；遇同名先备份、停止覆盖确认。此路线不承诺原生标签自动关联。**不要为了“两种都试”同时导入同一批包与散装预设**：两者文件名相同，Krita 按文件名跨来源去重、后导入一方被静默忽略；删掉生效那份并重启后预设消失、只余标签（2026-11-27 实测）。找不到先查[不可见/纹理像丢了](<troubleshooting.md#invisible-texture>)。

### 更新已有产物（需要时才做）

先正常退出 Krita，备份要替换的旧产物，再换成本次转换文件后启动；不在运行中覆盖已加载资源。保留两版便于回退，但一次只核对一个来源，避免同名混装。无需每次更新都重建缓存。

<a id="tradeoffs"></a>
## 六、转换取舍：先知道哪些不能再调

烘焙是把结果写进像素。v1.6.0 三开关均**仅适用项默认开启**；关闭保留部分原生字段/可调范围，不保证 PS 外观相同，也不是恢复 legacy 公式或旧双随机源。v1.5.0 及更早版本没有这些逐支开关。

| v1.6.0 选项 / CLI 键 | 何时适用并默认开启 | 开启的代价 | 关闭的代价与边界 |
|---|---|---|---|
| 计算笔刷位图化 / `rasterize_computed` | 计算笔刷（无自带位图） | 生成 PS 硬度剖面位图，fade/硬度不能再按 PS 语义调；显示尺寸为实际足印，每支约多 3–12 KB（历史样本） | 独立生产级原生 `auto_brush`，直径/间距/角度/圆度/硬度字段可调但受限；Krita fade 仅在 1.00R 内，PS 软边伸出标称半径；不再套 hardMix 满覆盖偏置，未验组合的真实编辑效果仍待验证 |
| 纹理预缩放 / `prescale_texture` | 有有效纹理位图、缩放>100%，且取整后目标尺寸变化 | 内嵌预缩放图、Scale=1；源尺寸/缩放与相位基准改写，再调 Scale 不再沿用源图基准，体积可增加 | 源纹理尺寸与原生 Scale 保留；旧实验 150%/200% 相对残差约 22%/32% 属该原生路线，不是默认预缩放的新残差；Scale>10 是否截断待实机 |
| 亮度越界预烘焙 / `bake_texture_bc` | 有纹理位图，calibrated 反解未截断亮度绝对值>1 | 亮度/对比度/反相烘进纹理、原字段失去 PS 语义；中性字段起点与 hardMix 例外见映射参考 | 保留原生亮度/对比度/反相字段，但亮度按 [-1,1] 截断；越界区不能完整表达，不烘焙、不换另一条烘焙链 |

亮度判据不是对所有对比度一律“PS 亮度>127”；以每支适用原因为准。采样笔尖**补白成正方形、边框255不变**，不是这三开关可取消的步骤。未映射的双笔刷/颜色动态/湿边等不因关开关而恢复。已证实残差、受限替代的历史数字与未测组合统一见[映射参考](<parameter-mapping.md>) §2、§7.1、§10；旧残差本轮不追加校准。

<a id="candidate-status"></a>
## 七、当前正式版与验收边界

> 兼容旧链接：上方显式锚点 `candidate-status` 继续保留；本节描述的是已发布正式版，不再表示候选状态。

**当前 v1.6.1 为 GUI 显示与布局补丁**，本版回归和新包检查见[本版发布说明](<../releases/RELEASE_NOTES.md>)；下述数字与人工结论属于 v1.6.0 历史验收，不自动成为新版本的验证结果。

**v1.6.0 已于 2026-11-27 16:33:35（Asia/Shanghai）正式发布**，合并第五轮亮度越界预烘焙、旋转抖动单随机源修正，以及本轮三开关/筛选/取消/元信息/标签/追溯。[升级说明](<../releases/RELEASE_NOTES.md>)区分旧 KPP/bundle 必须从 ABR 重转、只改元信息导致新包身份，以及可编辑性取舍。

v1.6.0 发布前机器回归记录为 1149/0/0/0（两次一致）、verify_all 235/0/0，Krita 8/8 在全量同批；两份分发包已构建、上传并完成摘要核对。使用者集中验收记录为 **30 项通过，E4/E6/F1/F3/F4/G3 六项未验并条件放行**，未验不写通过。发布 ZIP 的 **14 个 block**（9 个 numpy 第三方文本、1 个 CI 家目录、4 个扫描容量上限）经使用者接受并披露，不等于机器扫描已清零；人工目检为使用者自述完成、未附索引。**D-2**（代码页 936 下重定向/管道打印 U+2212 可能在已交付后退出 1）与 **D-6**（同名 bundle/KPP 同时导入互相遮挡）仍是已知问题，见[版本说明](<../releases/RELEASE_NOTES.md#release-v160>)与[排错手册](<troubleshooting.md>)。正式发布不补写人工通过，也不改写包内 `unreleased` 构建状态。

<a id="source-run"></a>
## 八、从源码运行（可选，不是 exe 用户的必做步骤）

需要完整源码与已配置依赖的 Python **3.12 或以上**。在项目根目录新 PowerShell 窗口中指定自己的解释器，不照抄未经核验的 PATH 默认 Python：

~~~powershell
# 把占位文字换为实际路径；不会自动安装或修改环境。
$PY = '填写你的 Python 3.12 或以上版本的 python.exe 完整路径'
& $PY -B -c "import sys; print(sys.executable); print(sys.version); assert sys.version_info >= (3, 12)"
if ($LASTEXITCODE -ne 0) { throw '解释器核验失败，停止。' }
& $PY -B cli.py convert --help
~~~

确认环境后可用 `& $PY -B cli.py convert '实际输入.abr' -o '新的输出目录'`；源码 GUI 用 `& $PY -B cli.py gui`，此处是操作入口，不承诺任意源码/依赖组合均已实测；正式包的集中验收边界见[第七节](<#candidate-status>)。缺依赖先留原始错误，不在错误解释器里反复安装；维护环境见[开发指南](<developer-guide.md>)，可选诊断的临时目录隔离见[排错手册](<troubleshooting.md#readonly-diagnosis>)。

[回首页](<../README.md>) · [排错手册](<troubleshooting.md>) · [维护参考](<developer-guide.md>)
