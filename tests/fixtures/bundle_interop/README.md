# 179 原生关联回归夹具与 187 交接

English: Independent, source-contract-based bundle association regression fixtures. Machine checks supplement, but never replace, Krita import/filter/restart acceptance.

## 1. 现有文件与运行期产物

- [测试入口](<tests/test_bundle_interop.py>)：真实转换/事务输出 + 独立回读 + 正反例。
- [声明式场景与手列期望](<tests/fixtures/bundle_interop/cases.json>)：公共、来源、逐支增删、逐笔最终集合和每个标签的筛选集合。
- 二进制 ABR、bundle、负例原始错误与交接清单只在工作区 [.tmp/179/](<.tmp/179/>) 生成；本目录不存二进制、不修改旧基准。
- 每次完整定向测试生成新的交接子目录，不覆盖之前的日志或样例；路径形如：

~~~text
.tmp/179/handoff/run-<本轮唯一编号>/
  catalog.json                        # 绑定完整 SHA256、稳定 ID、实际预设文件和筛选期望
  01-native.bundle                    # 正例
  02-repeat.bundle                    # 逆序标签输入重新导出，与正例字节相同
  03-metadata-only.bundle             # 只改说明，笔刷与逐笔标签不变，包身份必须变
  <反例名>.bundle + <反例名>.json       # 只在副本造坏，保存独立及生产校验的结果
~~~

所有这些是合成测试素材，不含商用笔刷；元信息中的作者、许可是虚构测试文字。源样例是 12 支自制 v1 ABR，选择原序号 2/5/9/12；显示名由测试场景显式赋值，不声称验证了 ABR 名称映射。

## 2. 三层口径，不能混写

1. **目标源码支持，未实机**：按 174 已绑定的 Krita 5.3.3 / git 858d352。独立 minidom 读者按 manifest 命名空间、字面 meta:meta 根、首个直接 tags、后代文本与同键后项覆盖读取；逐笔关系反转成文件名筛选集合。元信息解析要求 XML 前缀绑定，是 minidom 与目标 QDom 的已知差别；本组全部 XML 都绑定前缀，不外推到未声明前缀的包。
2. **项目严格合同与业务期望**：MD5 是资源原始字节；拒重 ZIP/manifest、缺资源、空/重复标签；用 Pillow 独立读取 KPP 的 PNG、预设 XML 和名称，再与手列集合逐文件比较。生产 verify_bundle 只是第二条交叉检查，不提供期待值。
3. **187 待人工**：真实导入、筛选、同一包退出重启后可见、真实旧标签与个人资源库不变、二进制隐私目检。机器结果不能代填这些项。

读取器不是 Krita 的完整实现：只为本任务 paintoppresets 场景建资源视图；重复 ZIP 名的目标后端选择未证实，返回“未知”，不猜首项/末项。174 既有探针对多 tags、混合文本、重名等有简化，本任务不改旧探针，改用测试内独立读者覆盖这些边界。

## 3. 四支笔刷如何区分

| 代号 | 显示名 | 原序号 | 包内预设文件（正例） | 关键区别 |
|---|---|---:|---|---|
| A | 179同名笔刷 | 2 | 000_179同名笔刷.kpp | 有“仅甲”，移除来源标签 |
| B | 179同名笔刷 | 5 | 001_179同名笔刷.kpp | 有“仅乙”，移除“共享”、Ink、Café |
| C | 179空标签 | 9 | 002_179空标签.kpp | 最终无标签；取消标签筛选时仍应在四支集合内 |
| D | 179特殊 & <尖括号> "引号" | 12 | 003_179特殊_尖括号_引号.kpp | 特殊字符名称、空白归一、NFC é |

A/B 的显示名刻意相同；不能仅凭名字认定关联正确。机器检查以手工源笔尖像素指纹独立绑定资源；第二来源只改未选择的第 1 支笔尖，四支选择内容不变，单独验证源摘要参与 ID。交接清单另列稳定 ID 和资源文件。查看“仅甲/仅乙”时各应一支，“勾线 练习”应有两支同名笔刷加 D，共三支；若界面不显示文件名，用 187 的资源详情或截图对照，不能凭口述合并成一支。

## 4. 每个标签的完整期望筛选集合

以下为正例、重复导出、只改说明对照的共同期望，标签文字按归一后的精确值：

| 标签 | 应筛出代号 | 支数 |
|---|---|---:|
| 共享 | A、D | 2 |
| 勾线 练习（中间一个空格） | A、B、D | 3 |
| Café（NFC） | A、D | 2 |
| Ink | A、D | 2 |
| 仅甲 | A | 1 |
| 仅乙 | B | 1 |
| 保留来源 | B | 1 |
| 原来源 | B、D | 2 |
| 特殊 空格（中间一个空格） | D | 1 |
| é（NFC） | D | 1 |
| 符号 & &lt;tag&gt; "双" '单' | A、D | 2 |
| 来源可删 | 无 | 0 |
| 只在包级 | 无逐笔关联 | 0 |
| 不存在 | 无 | 0 |
| 空字符串标签 | 不应创建 | 0 |

无标签集合只有 C。空白、中文与大小写重复输入不会创建多份标签；移除“来源可删”应影响所有笔刷；公共标签仍可被单支移除。预期值手写在 [场景 JSON](<tests/fixtures/bundle_interop/cases.json>)，不是从生产 final()/verify_bundle 的返回值自动生成。

## 5. 负例如何判断

- 缺 meta/manifest、旧 office 根、错误 manifest 命名空间、截断 ZIP：源码模拟不加载，项目门拒绝。
- 坏 MD5、缺 KPP、缺 md5 属性、多 tags 容器、重复声明、空/重复标签、缺 mimetype：项目门拒绝；其中原生 load 可以继续，不能写成“Krita 必须拒绝”。缺 KPP 时源码剔除该条目；多 tags 只取第一个；重复 manifest 同键后项覆盖。
- 只写包级标签、漏一支原生 tags：容器可加载，但逐笔语义门必须失败，不能只看 XML 有标签文字或全包标签并集。
- 重复 ZIP 的 KPP/meta/manifest：项目门明确拒绝；目标后端选择仍未知。
- **自洽错误源定位对照**：同时交换 A/B 同名笔刷的 trace 文件定位、字节摘要与 manifest 标签，生产仍自洽；四支手工 8×8 L 像素指纹必须抓住串笔。指纹不是从生产导出回填，构造规则为第 0/7 行 255、第 2..5 行与第 2..5 列交集为 255−原序号×13，其余 0；资源 MD5 仍取内嵌 PNG 原始字节。
- **自洽错关联对照**：把 A/B 的 manifest 标签和 trace 标签同时互换，保持全包标签并集和生产自洽校验通过；独立的手列逐笔期望必须失败。这不是要求 Krita 拒绝的格式坏包，而是证明写入器自证不够。

单个 pytest 负例显示“通过”，含义是“确实抓到指定错误”，不是坏包可用。每个生成的负例 JSON 保存具体独立错误，普通坏包同时保存生产错误；自洽错关联单独标 production_ok=true 并保存独立语义失败。

## 6. 机器复现（先核验解释器与隔离）

在项目根目录的 PowerShell 中，先按通用规范确定真实 $PY，不能照抄不存在的解释器。下面只设置本进程环境，不安装依赖、不启动应用：

~~~powershell
$T = (Join-Path (Get-Location) '.tmp/179/reproduce').Replace([char]92, [char]47)
New-Item -ItemType Directory -Force -Path $T | Out-Null
foreach ($K in 'TEMP','TMP','TMPDIR','APPDATA','LOCALAPPDATA','HOME','USERPROFILE','XDG_CACHE_HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME') {
    $P = Join-Path $T $K
    New-Item -ItemType Directory -Force -Path $P | Out-Null
    [Environment]::SetEnvironmentVariable($K, $P, 'Process')
}
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
# 每轮改用新的 RUN 名；保留旧输出，不复用 --basetemp 删除旧证据。
$RUN = [guid]::NewGuid().ToString('N')
$env:PYTEST_ADDOPTS = '--basetemp="' + $T + '/pytest-' + $RUN + '" -o cache_dir="' + $T + '/cache" --junitxml="' + $T + '/result-' + $RUN + '.xml"'
& $PY -B -c "import sys,tempfile; from pathlib import Path; assert sys.version_info >= (3,12); assert Path(tempfile.gettempdir()).resolve().is_relative_to(Path.cwd()/'.tmp/179'); print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw '解释器或隔离核验失败，请停止' }
& $PY -B -c "from _pytest.config import _prepareconfig; from pathlib import Path; c=_prepareconfig([]); r=Path.cwd()/'.tmp/179'; p=(c.option.basetemp,c.getini('cache_dir'),c.option.xmlpath); print(p); assert all(Path(v).resolve().is_relative_to(r.resolve()) for v in p); c._ensure_unconfigure()"
if ($LASTEXITCODE -ne 0) { throw 'pytest 实际参数落点不在任务内，请停止' }
& $PY -B -m pytest tests/test_bundle_interop.py -q
# 非0退出或任何跳过都不得记成通过；完整记录本轮终端输出与退出码。
~~~

测试本身另将逐例 TEMP、HOME、APPDATA 等设置到唯一任务子目录，不使用默认 tmp_path。pytest 启动之前的缓存/插件也需要上面的外层隔离；不能因为测试内隔离就省略它。不要同时跑另一套全量或渲染。

## 7. 187 时再做的人工验收（当前全部待验）

1. 使用 187 批准的隔离/备份资源库方案；不删除个人 resourcecache、不清旧标签、不修改旧包。先记录 Krita 版本和旧资源/标签状态，并核对本轮交接清单绑定的包哈希。
2. 导入正例，逐项按第 4 节筛选、记录实际代号/文件/支数与截图；尤其检查两支同名笔刷没串标签、C 在不筛选时可见。
3. 正常退出 Krita 再启动，再核对同一包与同一筛选集合，实际结果回填 187；本轮不能代填重启通过。
4. 按隔离方案分别对照重复包与只改元信息包。重复包与正例字节相同，不要求资源库多出第二份；只改元信息的 KPP 相同，Krita 可能按资源内容合并，不能预先承诺出现两套同名笔刷。
5. 核对描述等详情字段、旧包和旧标签、实际用户库未被改坏；逐项关闭二进制/图片人工隐私盲区。模拟用户库哨兵不等于真实用户库已验。
6. 坏包只在隔离环境按 187 安排抽测，记录真实现象；不把本节的项目严格规则误写成 Krita 必须弹窗拒绝。

任何实机/独立回读与源码合同的分歧应保留原件并冻结相关格式，交由使用者裁决，不改期待值掩盖差异。
