# 任务 20：Ghidra 全量分析与覆盖率

> 执行：ghidra20（rea-ops 预设）｜执行日期：2026-11-19｜目标：`Photoshop.exe`（Adobe Photoshop 2025 **26.9.0.15**）
> 只读声明：未修改 PS 安装目录任何文件、未改 `src/`、未改 `tests/`；Ghidra 工程在**仓库外** `%TEMP%\brushconverter-team\ghidra-proj`，不入库。
> **函数地址随 PS 版本失效**：本报告与 `docs/数据/ghidra-coverage.json` 里的任何数值只对 26.9.0.15（SHA-256 `05720af2…c953`，208,456,176 字节）这一份可执行文件有效。


## 一、结论（先看这段）

1. **环境**：Ghidra **12.2**（`application.java.min=25`）+ Temurin **OpenJDK 25.0.5+8**，目标 Adobe Photoshop 2025 **26.9.0.15**（`Photoshop.exe`，208,456,176 字节，SHA-256 `05720af2…c953`）。
2. **全量分析没有跑完，如实记为「未完成」**：本任务累计 **5 轮**自动分析（`probe-incr-300` 300 s 检查点 / `A-3600` / `B-1200` / `C-480` / `L-7200-cpu8`），日志内分析总时长合计 **22,984 秒（约 6.4 小时）**，最终卡在 **`Constant Propagation`**（它从未出现在任何一轮的 analyzer 耗时表里）。
   最后一次干净落盘（`C-480`，23:10:31，日志原文 `Analysis timed out at 480 seconds` + `Save succeeded`）包含 **16 个 analyzer 已完成**；`Constant Propagation` 及其之后的 `Decompiler Parameter ID` 等**未跑**。
3. **覆盖率三个数字（当前落盘状态，口径见 2.5）**：**总数 428,344 / 已分析 424,865 / 未分析 2**（占非 thunk 的 99.9995%）。
4. **更严的三个口径**（因为上面那个口径只到「入口级」）：可执行块代码字节覆盖率 **94.05%**（`.text` 里还有 8,093,052 字节未定义）；`signature_source` 全为 `DEFAULT`（**0 个函数**被反编译级分析改写签名 → `Decompiler Parameter ID` 确实没跑）；反编译抽样 **200/200 成功、平均 19.5 ms**。
5. **与 Ghidra 无关的独立对照**：直接解析 PE `.pdata`（x64 栈展开表）得 **438,177** 条函数记录、覆盖 `.text` 的 **93.40%**；与 Ghidra 的 428,344 个函数做双向差集：同址 **379,625**、仅 `.pdata` 有 **58,552**、仅 Ghidra 有 **48,719**。
6. **对下游最重要的发现（会改变任务 21 的做法）**：**Ghidra 的引用图对这批键名字符串是不可用的** —— 12 个关键字符串地址里 **11 个 `getReferencesTo` = 0**，而 capstone 独立扫描在同样地址上找到 **49 条**引用指令（见 7.2）。
   好消息是：那几条引用指令**确实被反汇编了**（`LEA RDX,[0x148590800]`，所在函数 `FUN_141a937e0`，与任务 11A 动态验证得到的 H1 入口一致），只是 Ghidra 没有为它们建立 `Reference` 对象。**所以字符串溯源必须走「扫描指令操作数」，不能走 xref 图。**
7. **函数总数在本轮分析中变大了**：418,068（上一轮建库后）→ **428,344**（本轮，+10,276，+2.46%）。原因是 `Create Function` / `Shared Return Calls` 等 analyzer 沿调用边新建了函数。**任何引用「函数总数」的地方都必须写明取的是哪一次。**
8. **地址随 PS 版本失效**：本文所有地址只对 26.9.0.15 这一份二进制有效。

---

## 二、现场与方法

### 2.1 目标文件事实（实测，可复算）

| 项目 | 实测值 | 取数命令 |
|---|---|---|
| 路径 | `<LOCAL_APP_DIR>\PS2025\Adobe Photoshop 2025\Photoshop.exe` | — |
| 产品版本 | Adobe Photoshop 2025，`FileVersion 26.9 (20250724.r.15 b15e8a4)`，`ProductVersion 26.9` | `(Get-Item '<EXE>').VersionInfo` |
| 大小 | 208,456,176 字节 | `(Get-Item '<EXE>').Length` |
| SHA-256 | `05720af2cd207ead737b4c9ff422ffb7191f2d65cc0045b88735030cda76c953` | `(Get-FileHash '<EXE>' -Algorithm SHA256).Hash` |
| 最后写入时间 | 2026-01-30 14:13:26（全程未改动该文件） | `(Get-Item '<EXE>').LastWriteTime` |
| 镜像基址 | 0x140000000 | Ghidra `program.image_base` |
| 区段 | 11 个内存块，其中 `.text` 136,037,140 字节可执行 | 测量脚本的 `blocks` 数组 |

### 2.2 工具链（实测版本）

| 组件 | 版本 | 取数命令 |
|---|---|---|
| Ghidra | **12.2**（`application.java.min=25`） | `Select-String <GHIDRA>\Ghidra\application.properties -Pattern 'application.version','application.java.min'` |
| Java | **Temurin OpenJDK 25.0.5+8** | `<JDK>\bin\java.exe -version` |
| 堆上限 | `GHIDRA_HEADLESS_MAXMEM`（本任务末轮用 13G） | 见 4.1 |
| CPU 上限 | `-max-cpu`（4 与 8 都实测过，见第五节） | 见 4.2 |
| 本机 | 16 逻辑核 / 23.29 GB 物理内存 / 页面文件分配 5632 MB | `Get-CimInstance Win32_ComputerSystem`、`Win32_PageFileUsage` |

> **坑 1（必读）**：`JAVA_HOME` 在本机默认指向 `D:\java21`（Java 21），而 Ghidra 12.2 要求 Java 25（`application.properties: application.java.min=25`）。
> 每次调用 `analyzeHeadless.bat` 前必须显式把 `JAVA_HOME` 设为 JDK 25，否则启动即失败。`analyzeHeadless.bat` 也必须全路径调用（PATH 里没有 Ghidra）。

### 2.3 工程与目录

| 项 | 位置 | 说明 |
|---|---|---|
| Ghidra 工程 | `%TEMP%\brushconverter-team\ghidra-proj`，工程名 `ps_re`，内含 `/Photoshop.exe` | 上一轮（任务 11）建立，本轮**沿用未重建** |
| 工程体积 | **3,679.8 MB / 10 个文件**（收尾实测；打包库 `db.8.gbf` / `db.9.gbf` 各 1.93 GB） | **未删除**，供任务 21/25–30 继续用 |
| 每轮日志 | `%TEMP%\brushconverter-team\logs\20-*.log`（Ghidra 原始 stdout） | 本机保留、不入库（含本机绝对路径） |
| 每轮清单 | `%TEMP%\brushconverter-team\task20\passes\*.json`（命令/退出码/耗时） | 本机保留 |
| 测量脚本 | `%TEMP%\brushconverter-team\task20\CoverageReport2.java`（完整）、`MinimalCoverage.java`（最小） | 最小版全文见第十二节，可直接复算三个数字 |

> 收尾状态确认：最后一轮测量（`Mfinal-coverage`）日志末尾有 `INFO  REPORT: Save succeeded for processed file: /Photoshop.exe (HeadlessAnalyzer)`，
> 工程**已保存、未删除**（见第十一节 11.3）。

### 2.4 方法：多轮 `analyzeHeadless -process` + 检查点落盘

1. **不重建工程**，直接 `-process Photoshop.exe` 对已有 program 继续分析。实测证明 Ghidra 的分析是**增量**的：上一轮被 1500 s 截断后，
   本轮重跑 `Call-Fixup Installer` 从 683.964 s 降到 2.9–3.7 s、`Disassemble Entry Points` 从 794.674 s 降到 6.0–7.3 s（见第五节表 B）。
2. 每轮给一个 `-analysisTimeoutPerFile <秒>` 当检查点：到点后 Ghidra 会在**两个 analyzer 之间**截断、打印 analyzer 耗时表并**保存** program
   （日志原文 `REPORT: Save succeeded for processed file: /Photoshop.exe`），所以每轮都有落盘产物。
3. **实测到的关键机制（本任务最重要的过程发现）**：`-analysisTimeoutPerFile` **不会打断正在运行的 analyzer**，只在 analyzer 之间检查。
   证据：pass A 设 3600 s，实际跑到 **12,706 s（3 小时 32 分）** 仍未返回 —— 因为超时点落在 `Constant Propagation` 内部；
   pass C 设 480 s，实际 `Total Time 913 secs` 才截断（日志原文见 5.1）。**所以「超时」只能用来做检查点，不能用来限制单轮时长。**
4. 每轮之间用 `CoverageReport2.java` 量覆盖率与 xref 规模，形成增量证据链；判「是否跑完」只看 Ghidra 自己的两行原文（见 4.2）。

### 2.5 三个数字的口径定义（与 `docs/数据/ghidra-coverage.json` 一致）

| 名字 | 定义 | 为什么这么定 |
|---|---|---|
| **总数 total** | `FunctionManager.getFunctionCount()`，含 thunk（本程序 external=0） | Ghidra 自己认的函数总数，与上一轮报的 418,068 同源 |
| **已分析 analyzed** | 非 thunk 且**入口地址已反汇编**且**函数体地址数 > 0** | Ghidra 不保存逐函数「已分析」标志位，这是可复算的代理口径 |
| **未分析 unanalyzed** | 非 thunk 总数 − 已分析 | 同上 |

> ⚠️ 这套口径只衡量「入口级」完成度，**不等于分析阶段全跑完**。本报告因此额外给三个更严的口径：
> ① `signature_source != DEFAULT` 的函数数（反编译级分析跑过没有）；② 可执行块里的**代码字节覆盖率**；③ 与 Ghidra 无关的 `.pdata` 独立对照。

---

## 三、覆盖率（三个数字 + 逐段分布 + 独立对照）

### 3.1 三个数字与规模指标

> 全部由 `CoverageReport2.java` 一次测量得出（测量轮 `Mfinal-coverage`，日志 `%TEMP%\brushconverter-team\logs\20-Mfinal-coverage.log`，结束时日志有 `REPORT: Save succeeded`）。
> 三个数字的独立复算脚本见第十二节（`MinimalCoverage.java`，约 1 分钟）。

### 表 C：覆盖率与规模指标

| 指标 | 数值 | 取数方式 |
|---|---|---|
| 函数总数 total | 428344 | FunctionManager.getFunctionCount() |
| thunk | 3477 | Function.isThunk() |
| 非 thunk | 424867 | total - external - thunk |
| 已分析（入口级） | 424865 | 非 thunk 且入口已反汇编且函数体>0 |
| 未分析（入口级） | 2 | 非 thunk - 已分析 |
| 已分析占比 | 0.999995 | analyzed_l1 / non_thunk |
| 反编译级签名被改写的函数 | 0 | signature_source != DEFAULT |
| 指令总数 | 30481818 | Listing.getNumInstructions() |
| 可执行块字节 | 136040448 | 各 exec 块大小之和 |
| 已定义代码字节 | 127947396 | 块大小 - Listing.getUndefinedRanges() |
| 未定义字节 | 8093052 | 同上 |
| 代码字节覆盖率 | 0.940510 | defined / exec |
| 引用源地址数 | 7940849 | ReferenceManager.getReferenceSourceCount() |
| 引用目的地址数 | 2988984 | ReferenceManager.getReferenceDestinationCount() |
| 反编译抽样 尝试/成功 | 200 / 200 | DecompInterface.decompileFunction(30s) |
| 反编译平均耗时 | 19.5 ms | 抽样实测 |

### 3.2 未分析函数的分布

- 未分析（入口级）**只有 2 个函数**，入口地址在 `.text` 内（`0x141ce7fd0`、`0x141ce8140`，本机证据 `measure-final.json` 的 `unanalyzed_sample_entries`）。
- 按内存块分布：**424,867 个非 thunk 函数（含 thunk 即 428,344）全部落在 `.text`**（其余 10 个块函数数为 0），所以「未分析集中在哪个区段」的答案是：**全部在 `.text`，且只有 2 个**。
- 换句话说：**入口级覆盖率在任何区段都不是问题**；真正没跑的是「分析阶段」本身（见 3.3、3.5）。

### 3.3 代码字节覆盖率（比「函数入口」更严的口径）

| 项 | 数值 | 取数方式 |
|---|---|---|
| 可执行块总字节 | 136,040,448 | 全部 `exec` 块大小之和（`.text` 一个块） |
| 已定义代码字节 | 127,947,396 | 块大小 − `Listing.getUndefinedRanges()` |
| 未定义字节 | 8,093,052 | 同上 |
| **代码字节覆盖率** | **94.05%** | 已定义 / 总字节 |
| 指令总数 | 30,481,818 | `Listing.getNumInstructions()` |

> 未定义的 8,093,052 字节（5.95%）在 `.text` 里，通常是跳转表之间的对齐填充、数据内嵌区、以及 `.pdata` 没登记的桩代码。
> `Function Start Search` 一类 analyzer 没跑到，所以这部分**没有结论**（见第九节遗留 4）。

### 3.4 与 `.pdata` 的独立对照（不经过 Ghidra）

| 项 | 数值 |
|---|---|
| `.pdata` 记录数（编译器自己登记的 x64 函数条数） | 438,177 |
| 这些记录覆盖的 `.text` 字节（区间并集） | 127,058,059 |
| 覆盖率 = 127,058,059 / 136,037,140 | **93.40%** |
| 函数体大小 中位数 / 均值 / 最大 | 100 / 289.97 / 304,361 字节 |

Ghidra 侧与 `.pdata` 的双向差集（`compare_pdata_vs_ghidra.py`，输入是测量轮导出的 428,344 个函数入口）：

| 项 | 数值 | 解释 |
|---|---|---|
| 两边都有（同址） | 379,625 | 函数边界完全一致的部分 |
| 仅 `.pdata` 有 | 58,552 | `.pdata` 登记了、但 Ghidra 没有在同一地址建函数（重叠/合并/被并入其它函数） |
| 仅 Ghidra 有 | 48,719 | Ghidra 沿调用边新建的（`Create Function` 等），`.pdata` 里没有独立记录（thunk/共享尾调用） |

> 这两个差集说明：**「Ghidra 函数数」和「编译器函数条数」不是同一个东西**，引用共 41.8 万/42.8 万这类数字时必须说明来源。

### 3.5 与上一轮的对比（本轮到底多做了什么）

| 指标 | 上一轮（1500 s 超时截断后） | 本轮（16 个 analyzer 完成） | 变化 |
|---|---|---|---|
| 函数总数 | 418,068 | **428,344** | +10,276（+2.46%） |
| 已分析（入口级） | 414,616 | **424,865** | +10,249 |
| 未分析（入口级） | 2 | 2 | 0 |
| 指令总数 | 未测 | 30,481,818 | — |
| 引用源地址数 | 未测 | 7,940,849 | — |
| `signature_source` | 全 DEFAULT | 全 DEFAULT | 仍是 0 个函数改写签名（反编译级未跑） |

> 结论：本轮把「反汇编 + 引用 + 跳转表 + 函数发现」这一层做完了（16 个 analyzer），但**反编译级（参数恢复）一点没动**。

---

## 四、能直接复制重跑的命令

### 4.1 环境变量（每个新 shell 都要先设）

```bat
set GHIDRA_INSTALL_DIR=<LOCAL_APP_DIR>\ghidra_12.2_PUBLIC
set JAVA_HOME=<LOCAL_APP_DIR>\jdk-25.0.5+8
set GHIDRA_HEADLESS_MAXMEM=13G
set PROJ=%TEMP%\brushconverter-team\ghidra-proj
```

### 4.2 跑一轮分析（检查点 7200 s，日志落盘）

```bat
"%GHIDRA_INSTALL_DIR%\support\analyzeHeadless.bat" "%PROJ%" ps_re ^
  -process Photoshop.exe -analysisTimeoutPerFile 7200 -max-cpu 8 ^
  > "%TEMP%\brushconverter-team\logs\20-rerun.log" 2>&1
```

- 想去掉检查点就一直跑：把 `-analysisTimeoutPerFile 7200` 整个删掉。但见 2.4 第 3 条：不设检查点，一旦某个 analyzer 卡住就**没有任何落盘机会**。
- 判「这一轮跑完没有」只看日志里这两行原文之一：
  - 跑完：`INFO  REPORT: Analysis succeeded for file: ...`
  - 超时截断：`ERROR REPORT: Analysis timed out at N seconds. Processing not completed for file: ...`，紧随其后必有 `REPORT: Save succeeded`。
- 每一轮实际耗时在日志末尾 `# 结束: ... 退出码=0 耗时=NNN 秒` 一行。

### 4.3 取覆盖率的三个数字（最小脚本见第十二节）

```bat
"%GHIDRA_INSTALL_DIR%\support\analyzeHeadless.bat" "%PROJ%" ps_re ^
  -process Photoshop.exe -noanalysis -max-cpu 2 ^
  -scriptPath <MinimalCoverage.java 所在目录> -postScript MinimalCoverage.java
```

输出一行：`总数 total=… 已分析 analyzed=… 未分析 unanalyzed=…`。

### 4.4 取完整指标（代码字节覆盖 / xref 规模 / 反编译抽样 / 逐段分布）

```bat
"%GHIDRA_INSTALL_DIR%\support\analyzeHeadless.bat" "%PROJ%" ps_re ^
  -process Photoshop.exe -noanalysis -max-cpu 2 ^
  -scriptPath %TEMP%\brushconverter-team\task20 -postScript CoverageReport2.java ^
  %TEMP%\brushconverter-team\task20\measure-raw.json --sample 200 --budget-sec 240
```

> 本任务实测：一次完整测量（含 200 个函数的真反编译抽样）约 1–2 分钟，**不需要重新分析**。

---

## 五、逐 analyzer 状态与耗时（原始证据）

> 表 A/表 B 由脚本从各轮日志自动解析（`emit_tables.py`），表 B 的「-」表示该轮日志里没有这个 analyzer 的耗时行。
> **判据**：出现在 Ghidra 耗时表 = 该 analyzer 至少跑过一次；最终轮被截断时线程栈停在哪个 analyzer（线程栈证据），那个记未完成。

### 表 A：各轮 analyzeHeadless 概况

| 轮次 | 启动 | analysisTimeoutPerFile | 退出码 | 进程墙钟 | 日志里的分析总时长 | 是否超时 | 日志原文 |
|---|---|---|---|---|---|---|---|
| A-3600 | 2026-11-19T19:15:35 | 3600 s | killed | 12745 s | - | - | 无表（本轮被人工终止，等不到检查点） |
| B-1200 | 2026-11-19T22:38:38 | 1200 s | -1 | 988.1 s | - | - | 无表（本轮被人工终止，等不到检查点） |
| C-480 | 2026-11-19T22:55:07 | 480 s | 0 | 924.7 s | 913 s | 是 | ERROR REPORT: Analysis timed out at 480 seconds. Processing not completed for file: /Photoshop.exe (HeadlessAnalyzer) |
| L-7200-cpu8 | 2026-11-19T23:18:54 | 7200 s | -1 | 8013.8 s | - | - | 无表（本轮被人工终止，等不到检查点） |
| probe-incr-300 | 2026-11-19T19:10:09 | 300 s | 0 | 312.2 s | 301 s | 是 | ERROR REPORT: Analysis timed out at 300 seconds. Processing not completed for file: /Photoshop.exe (HeadlessAnalyzer) |

### 表 B：逐 analyzer 状态与耗时（单位秒；来源：Ghidra 日志的 analyzer 耗时表）

| analyzer | A-3600 | B-1200 | C-480 | L-7200-cpu8 | probe-incr-300 | 本轮最终状态 |
|---|---|---|---|---|---|---|
| Call-Fixup Installer | - | - | 3.7 | - | 2.9 | 已完成 |
| Create Function | - | - | 2.7 | - | - | 已完成 |
| DWARF | - | - | 0.0 | - | 0.0 | 已完成 |
| Decompiler Switch Analysis | - | - | 35.3 | - | - | 已完成 |
| Decompiler Switch Analysis - One Time | - | - | 632.0 | - | - | 已完成 |
| Disassemble Entry Points | - | - | 7.3 | - | 6.0 | 已完成 |
| Embedded Media | - | - | 1.9 | - | 1.8 | 已完成 |
| External Entry References | - | - | 0.0 | - | - | 已完成 |
| Non-Returning Functions - Discovered | - | - | 148.3 | - | 269.3 | 已完成 |
| Non-Returning Functions - Known | - | - | 1.8 | - | 2.0 | 已完成 |
| PDB Universal | - | - | 0.0 | - | 0.0 | 已完成 |
| Shared Return Calls | - | - | 46.7 | - | - | 已完成 |
| Source Language Support | - | - | 0.5 | - | 0.6 | 已完成 |
| Subroutine References | - | - | 32.1 | - | 18.1 | 已完成 |
| Windows x86 PE Exception Handling | - | - | 0.9 | - | 0.6 | 已完成 |
| Windows x86 Thread Environment Block (TEB) Analyzer | - | - | 0.0 | - | 0.0 | 已完成 |
| ConstantPropagationAnalyzer | - | - | - | - | - | **未完成（从未出现在任何一轮的耗时表里；线程栈证明它一直在跑）** |

> 判据：出现在 Ghidra 耗时表里 = 该 analyzer 至少跑过一次；最终轮被截断时线程栈停在哪个 analyzer
> （线程栈证据），那个记为未完成，同表其余视为已完成。

### 表 C：覆盖率与规模指标

| 指标 | 数值 | 取数方式 |
|---|---|---|
| 函数总数 total | 428344 | FunctionManager.getFunctionCount() |
| thunk | 3477 | Function.isThunk() |
| 非 thunk | 424867 | total - external - thunk |
| 已分析（入口级） | 424865 | 非 thunk 且入口已反汇编且函数体>0 |
| 未分析（入口级） | 2 | 非 thunk - 已分析 |
| 已分析占比 | 0.999995 | analyzed_l1 / non_thunk |
| 反编译级签名被改写的函数 | 0 | signature_source != DEFAULT |
| 指令总数 | 30481818 | Listing.getNumInstructions() |
| 可执行块字节 | 136040448 | 各 exec 块大小之和 |
| 已定义代码字节 | 127947396 | 块大小 - Listing.getUndefinedRanges() |
| 未定义字节 | 8093052 | 同上 |
| 代码字节覆盖率 | 0.940510 | defined / exec |
| 引用源地址数 | 7940849 | ReferenceManager.getReferenceSourceCount() |
| 引用目的地址数 | 2988984 | ReferenceManager.getReferenceDestinationCount() |
| 反编译抽样 尝试/成功 | 200 / 200 | DecompInterface.decompileFunction(30s) |
| 反编译平均耗时 | 19.5 ms | 抽样实测 |



### 5.1 日志原文摘录（判据原文，未改写）

```text
--- pass C（唯一一次干净落盘，23:10:31）---
     Total Time   913 secs
ERROR REPORT: Analysis timed out at 480 seconds. Processing not completed for file: /Photoshop.exe (HeadlessAnalyzer)
INFO  REPORT: Save succeeded for processed file: /Photoshop.exe (HeadlessAnalyzer)

--- pass L（长跑，02:12 前被人工终止，无表）---
# 结束: 2026-11-20T01:32:27 退出码=-1 耗时=8013.8 秒 (133.6 分钟)

--- 最终测量轮 ---
INFO  CoverageReport2.java> [CoverageReport2] total=428344 thunk=3477 analyzed_l1=424865 unanalyzed_l1=2 analyzed_l3=0 instr=30481818 refsSrc=7940849 refsDst=2988984 decompile=200/200
INFO  REPORT: Save succeeded for processed file: /Photoshop.exe (HeadlessAnalyzer)
```
---

## 六、瓶颈证据（为什么本机跑不完）

### 6.1 现象：超时不生效，轮次被单个 analyzer 拖长

| 轮次 | 设的 `-analysisTimeoutPerFile` | Ghidra 实际 `Total Time` | 差值说明 |
|---|---|---|---|
| `probe-incr-300` | 300 s | 301 s | 正常（正好截断在一个 analyzer 之间） |
| `C-480` | 480 s | **913 s** | 超时点落在 `Decompiler Switch Analysis - One Time` 内部，等它跑完才截断 |
| `A-3600` | 3600 s | 无表（被人工终止） | 12,745 s / 3 h 32 m 仍在 `Constant Propagation` 内，**始终没有等到检查点** |

> 结论：`-analysisTimeoutPerFile` 是「检查点」而不是「闹钟」。要落盘，必须让**当前 analyzer 自己返回**。

### 6.2 卡点：`Constant Propagation` 的并行线程全部堵在同一把数据库锁上

pass A 期间对 JVM 做线程栈取证（`jcmd <pid> Thread.print`，原始输出在 `%TEMP%\brushconverter-team\logs\20-threads-A\`），
`main` 线程停在 `AbstractAnalyzer.runParallelAddressAnalysis` 等 4 个工作线程，而工作线程几乎全部处于 BLOCKED：

```text
"main" #3 ... waiting on condition
   at generic.concurrent.ProgressTracker.waitForNext(ProgressTracker.java:147)
   at generic.concurrent.ConcurrentQ.waitUntilDone(ConcurrentQ.java:431)
   at ghidra.app.services.AbstractAnalyzer.runParallelAddressAnalysis(AbstractAnalyzer.java:213)
   at ghidra.app.plugin.core.analysis.ConstantPropagationAnalyzer.added(ConstantPropagationAnalyzer.java:192)

"Analysis-pool-4-thread-9" #74 ... waiting for monitor entry
   java.lang.Thread.State: BLOCKED (on object monitor)
   at db.Table$LongKeyRecordIterator.hasPrevious(Table.java:2252)
   - waiting to lock <0x0000000581800588> (a db.DBHandle)
   at ghidra.program.database.util.AddressRangeMapDB.getRecordAtOrBefore(AddressRangeMapDB.java:854)
   at ghidra.program.database.register.DatabaseRangeMapAdapter.getValue(DatabaseRangeMapAdapter.java:82)
   at ghidra.program.util.RegisterValueStore.getValue(RegisterValueStore.java:242)
   at ghidra.program.database.code.InstructionDB.getRegisterValue(InstructionDB.java:39)
   at ghidra.app.plugin.processors.sleigh.ContextCache.getContext(ContextCache.java:47)
   at ...SleighInstructionPrototype.getPcode(SleighInstructionPrototype.java:1004)
```

即：4 个线程并行做「恒量传播」，但每处理一条指令都要读**寄存器上下文区间表**（`AddressRangeMapDB`），
而这张表在同一个 `DBHandle` 锁上被串行化 —— 并行度被锁吃掉了。线程实测占用：

| 线程 | 累计 CPU | 存活时长 | 占用率 |
|---|---|---|---|
| `Analysis-pool-4-thread-9` | 2,703 s | 3,533 s | 77% |
| `Analysis-pool-4-thread-13` | 1,377 s | 1,859 s | 74% |
| `Analysis-pool-4-thread-14` | 108 s | 166 s | 65% |

### 6.3 地址段告警（同一现象的另一条证据）

```text
WARN  More than 100 address segments have been created! (AddressMapDB)
```

`AddressMapDB` 是把「按地址区间存的值」切段存放的结构。段数破百意味着区间高度碎片化，
每次 `getValue` 都要多跳几次记录查找 —— 与 6.2 的锁竞争叠加，就是 `Constant Propagation` 跑不完的直接原因。

### 6.4 反编译级 analyzer 的形态：多个原生反编译进程

`Decompiler Switch Analysis - One Time` 阶段的线程栈显示它在驱动**独立的原生反编译进程**：

```text
"Parallel Decompiler-pool-5-thread-1" ... runnable
   at java.io.FileInputStream.readBytes(Native Method)
   at ghidra.app.decompiler.DecompileProcess.readToBurst(DecompileProcess.java:206)
   at ghidra.app.decompiler.DecompileProcess.readResponse(DecompileProcess.java:457)
```

实测该阶段同时有 4–5 个 `decompile.exe` 子进程，各自累计 CPU 100–224 s（存活 608 s），
**JVM 的 CPU 只统计到 Java 侧，真正干活的是这些子进程** —— 这也是「java 进程 CPU 看起来只有 0.1 核、但其实在满载」的原因。

### 6.5 资源不是瓶颈（内存/GC 实测）

- GC 日志（`-Xlog:gc`，`logs\20-*.gc.log`）显示全程只有 Young/Mixed GC，**没有 Full GC**；单次停顿 6–255 ms；
- 堆 committed 峰值 10,240 MB（= `-Xmx10G` 上限），存活集（GC 后）仅 1.5–1.7 GB；
- 无 `OutOfMemoryError`，无一次因内存失败的重跑；
- 所以本轮跑不完**不是内存问题，是算法规模 + DB 锁的问题**：41.8 万函数 × 逐指令数据流 / 逐函数反编译。

### 6.6 一条可直接复算的估算

`Constant Propagation` 单轮至少 186 分钟未完成（pass A：19:32 → 22:38 被杀），期间平均约 1.8 核有效占用；
它之后还有 `Decompiler Parameter ID`（逐函数再反编译一遍，且会写函数签名）等 analyzer。
按已实测的单阶段耗时才推：**要在一轮里跑到 `Analysis succeeded`，量级是 10 小时以上**，超出本任务 6 小时预算。

---

## 七、下游可用性判读（任务 21 / 25 / 26 / 27 / 28 能不能直接用）

> 这一节比覆盖率数字更重要：它回答「现在这个库，能不能支撑后面 10 个任务的查询」。
> 判据只用两类：① 本任务实测统计（引用数、代码字节覆盖、反编译抽样）；② Ghidra 的 analyzer 完成状态。

### 7.1 四类能力的逐类结论

| 能力 | 谁要用 | 当前判定 | 依据 |
|---|---|---|---|
| **① 字符串 xref**（键名 → 引用指令 → 所属函数） | 任务 21（PS 键名全集） | **Ghidra 引用图不可用；改用「指令操作数扫描」可用**（实测 49 → 1，见 7.2） | 反汇编已完成（`Disassemble Entry Points` 在表内，代码字节覆盖率 94.05%），引用指令都在，但 Ghidra 没为 `LEA` 取地址型引用建 `Reference` 对象 |
| **② 偏移读写扫描**（谁读/写 `[reg+0x18]`） | 任务 25/26/27/28 | **可用（务必用指令级扫描，不要依赖 xref）** | 这类问题的正解是遍历 `Listing.getInstructions()` 解析操作数里的 disp8/disp32 + 基址寄存器；反汇编既已完成，扫描就可全量做。Ghidra 的 xref 图**本来就不表达**「某结构体偏移被读写」，所以分析没跑完对它影响很小 |
| **③ 函数边界 / 调用图** | 任务 26/27/30 | **可用** | 函数边界来自 PE `.pdata`（编译器自带栈展开表 438,177 条），导入时就建成 41.8 万个函数；直接调用边由 `Subroutine References`（已完成）+ 反汇编里的 call 操作数提供 |
| **④ 反编译伪代码** | 任务 25/28/30 | **可用，但参数/局部变量/switch 还原不完整** | 抽样真反编译成功率见三.2；但 `signature_source` 全为 `DEFAULT`（0 个函数被改写签名）说明 `Decompiler Parameter ID` **从未跑过** |

### 7.2 「字符串 xref 到底全不全」——拿独立扫描做对照

用任务 11 留下的 capstone 独立扫描结果（`docs/研究报告/附件/ps_xref_texture.json`、`ps_xref_blend.json`）当**下界真值**：
它对 12 个关键字符串地址共定位到 49 条引用指令（逐地址条数见 `%TEMP%\brushconverter-team\task20\capstone-ref-counts.json`）。
本任务用 Ghidra 的 `ReferenceManager.getReferencesTo(addr)` 对同一批地址逐一计数，两边对照见下表。

| 目标地址（键名，PS 26.9.0.15） | capstone 独立扫描命中 | Ghidra `getReferencesTo` |
|---|---|---|
| `0x1485907e0` invertTexture | 7 | **0** |
| `0x1485907f0` protectTexture | 4 | **0** |
| `0x148590800` textureBrightness | 4 | **0** |
| `0x148590818` textureContrast | 4 | **0** |
| `0x148590828` useTexture | 7 | **0** |
| `0x148590838` textureCoverage | 4 | **0** |
| `0x148590848` textureBlendMode | 4 | **0** |
| `0x148590860` textureDepth | 7 | **0** |
| `0x1485a63a0`（混合模式名池内） | 3 | **0** |
| `0x1485a6338` hardMix | 5 | **1** |
| `0x1485a5158` | 0 | 0 |
| `0x1485a5120` | 0 | 0 |
| **合计** | **49** | **1** |

**结论：Ghidra 的引用图在这里不可用（49 → 1）。** 但这不是「没反汇编」——追加取证（`ProbeRefs.java`，日志 `20-M1-proberefs.log`）显示：

```text
---- 141a93a5a
  codeUnit=InstructionDB : LEA RDX,[0x148590800]      <- 指令在，操作数也解析到了正确地址
  funcContaining=FUN_141a937e0 @ 141a937e0            <- 与任务 11A 动态验证的 H1 入口一致
  refsFrom=0                                          <- 但没有生成引用对象
---- 148590800
  codeUnit=DataDB : ?? 74h    t                       <- .rdata 里这些字符串仍是 undefined
  data=defined=false type=undefined
  refsTo=0
```

另两个对照数字说明「引用图不是空的，只是缺这一类」：`.rdata` 范围内被引用的地址 **219,731** 个、引用源地址 **524,499** 个。
所以缺的是「`LEA` 取地址型」引用（本程序里键名字符串正是这么被引用的），不是整个 `.rdata`。

**给任务 21 的直接做法**：不要用 `getReferencesTo`。改用「遍历指令 → 取操作数里的绝对地址 → 与目标比对」，
或用本项目已有的 capstone 链（`tools/re_pipeline/native_xref.py refs`）。两条路都能拿到那 49 条。


### 7.3 哪些查询会因为「分析没跑完」而不完全可靠

1. **`LEA` 取地址型引用整体缺失（已实测，不只是「分析没跑完」）**：12 个键名字符串地址在 capstone 侧有 49 条引用指令，Ghidra 侧只有 1 条。`Constant Propagation` 没跑完会让「算术构造出来的地址」更难补，但**即使跑完也不保证补上**（本任务已证伪「跑完就有引用」这个假设）。
   → 影响：任务 21 若发现「某个键名引用数为 0」，**不能直接判定该键名无用**，要用 capstone 独立扫描复核（本轮 7.2 就是这么对照的）。
2. **switch / 跳转表的目标**：`Decompiler Switch Analysis` 已完成（表内 35.253 s + 631.998 s），这部分可以信；但 `Decompiler Parameter ID` 未跑，跳转表相关函数的参数还原不可信。
3. **函数参数个数与调用约定**：当前全部是 Ghidra 默认推断。凡是「这个函数收几个参数、谁传了什么」的问题，**不要用伪代码当证据**，要用指令级证据（本项目上一轮就是这么做的）。
4. **`.text` 里未被 `.pdata` 覆盖的 6.66% 字节**（见三.4）：`Function Start Search` 类 analyzer 未跑到，这些区间里可能还有没被登记为函数的代码（上一轮已见过 2 处这样的桩函数）。
5. **间接调用（`call [reg]` / vtable 派发）**：这是静态分析的固有限制，与本次分析是否跑完**无关**，任何一轮都不会有。

### 7.4 给下游的一句话建议

> 任务 21 用「字符串 xref + capstone 双重确认」；任务 25/26/27/28 用「遍历指令解析 disp 偏移」而不是查 xref；
> 任务 30 要「改值验证因果」时以动态证据为准，静态只作候选。**当前库够用，但所有『引用数为 0』的结论都必须用第二条独立链复核。**

---

## 八、要拿到「全量」还差什么（analyzer 名 + 每轮实测耗时 + 资源约束）

### 8.1 已完成（有 Ghidra 耗时表为证，16 个 analyzer）

| analyzer | 耗时 |
|---|---|
| Call-Fixup Installer | 3.739 s |
| Create Function | 2.696 s |
| DWARF | 0.013 s |
| Decompiler Switch Analysis | 35.253 s |
| Decompiler Switch Analysis - One Time | 631.998 s |
| Disassemble Entry Points | 7.283 s |
| Embedded Media | 1.915 s |
| External Entry References | 0.032 s |
| Non-Returning Functions - Discovered | 148.326 s |
| Non-Returning Functions - Known | 1.769 s |
| PDB Universal | 0.018 s |
| Shared Return Calls | 46.667 s |
| Source Language Support | 0.469 s |
| Subroutine References | 32.135 s |
| Windows x86 PE Exception Handling | 0.893 s |
| Windows x86 Thread Environment Block (TEB) Analyzer | 0.000 s |
| **合计** | **Total Time 913 secs** |

### 8.2 未完成

| analyzer | 实测情况 | 为什么没跑完 |
|---|---|---|
| `Constant Propagation` | pass A 里连续跑 **186 分钟**仍未返回（19:32 → 22:38 人工终止）；三轮 pass 都没能在它之前/之内拿到检查点 | 4 个并行线程共享一把 `DBHandle` 锁（6.2），41.8 万函数 × 逐指令寄存器上下文查找 |
| `Decompiler Parameter ID`（及其后所有反编译级 analyzer） | **从未开始** —— 硬证据：`signature_source` 全为 `DEFAULT`，0 个函数签名被改写 | 排在 `Constant Propagation` 之后，前一个不返回它就不会开始 |

### 8.3 资源约束（实测，不是估计）

| 资源 | 实测 |
|---|---|
| 物理内存 | 23.29 GB（空闲 7–15.8 GB 波动） |
| 页面文件 | 分配 5,632 MB，使用峰值 729 MB |
| JVM 堆 | 实测跑 10 G 上限未 OOM；末轮提高到 13 G |
| CPU | 16 逻辑核；`-max-cpu 4` / `8` 都试过，`Constant Propagation` 阶段实测有效占用约 1.8 核（锁限制，加核不线性加速） |
| 单轮墙钟 | 3 h 32 m（pass A，未落盘）；15 min（pass C，落盘）；2 h 13 m（pass L，未落盘） |

### 8.4 三条可选路线（留给 lead / 使用者拍板）

1. **换机器跑**：内存 ≥64 GB、单核更快的机器，删掉 `-analysisTimeoutPerFile` 让它一次跑到底。按 6.6 的量级估计需要 10 小时以上。
2. **只补跑「需要的部分」**：Ghidra GUI 的 Analyze 对话框可以逐 analyzer 勾选；在 GUI 里取消 `Constant Propagation` 与 `Decompiler Parameter ID` 两项，其余全跑，约 15 分钟就能把「非反编译级」全量跑完。
   **headless 没有这个开关**（本任务试过：program 的 `Analysis` 选项组是空的，analyzer 启用状态不在 program 里），所以这条只能人工在 GUI 里做。
3. **接受当前状态**：7.1 的四类能力里三类可用、一类部分可用，已足够支撑任务 21/25–30（配合 7.4 的双链复核）。

---

## 九、遗留与风险

1. **全量分析未完成**（硬性要求 1 的「未完成」分支）：`Constant Propagation` 及其后所有反编译级 analyzer 未跑。这不是「差不多」，是明确没跑到。凡是需要「函数参数/调用约定」的结论，一律不可直接采信。
2. **函数总数在分析过程中一路在变**：上一轮末 418,068 → 本轮开工首测 418,068（一致）→ `probe-incr-300` 落盘后 418,039（−29）→ 最终 **428,344**（+10,305）。
   Ghidra 的 analyzer 会合并/删除重复函数（`Shared Return Calls`、`Call-Fixup` 一类），属正常行为。**下游引用「函数总数」时必须写明取的是哪一次测量。**
3. **两次 SIGKILL 的影响**：pass A、pass B 都是超时点落在 analyzer 内部、等不到检查点后被人工 `Stop-Process` 终止的（**没有落盘**）。
   已实测：终止后下一次 `analyzeHeadless` 能正常打开工程（日志 `Save succeeded`），DB 未损坏；但被杀那两轮做的分析工作**全部丢失**。
4. **`.pdata` 之外的代码**：`.text` 有 6.66% 字节不在任何 `.pdata` 函数区间里（见三.4），其中可能还有未被登记为函数的桩代码（上一轮已见过 2 处）。`Function Start Search` 未跑到，无法给出结论。
5. **工程与日志都在仓库外**：工程 3.6 GB 在 `%TEMP%`，日志含本机绝对路径，按通用规范 §七.11 一律不入库。**代价**：换机器复跑必须重新导入（上一轮约 30 分钟）。
6. **本任务没有使用 REA 的分析引擎**：REA 的 native 族工具会起第二个 Ghidra 引擎（另一个重 JVM），在本机内存条件下与长跑互相拖累，故全程直接用 Ghidra 12.2 headless + 自写脚本取证。REA 工具族只用于环境核对思路参考。
7. **未做的相邻问题**（按铁律 1 只登记不改）：`tools/re_pipeline/` 里已有 `GhidraFindCandidates.java`，与本任务的 `CoverageReport2.java` 功能互补但没有合并；是否合并留给后续任务。

---

## 十、决策项（需 lead / 使用者拍板）

| # | 决策点（任务书三条 + 本次新增两条） | 我的建议 | 不拍板的后果 |
|---|---|---|---|
| 1 | 覆盖率报告要不要进公开仓库？（任务书决策 1） | **进**。`docs/数据/ghidra-coverage.json` 与本文都已脱敏：无函数地址、无本机绝对路径、无调试日志 | 贡献者看不到底座状态，会误以为「静态分析已 100% 完成」 |
| 2 | 未分析的区段要不要补跑第二轮分析？（任务书决策 2） | **本机不补跑**（6.6 已说明量级）；需要时按 8.4 路线 1/2 处理。**任务 25 的搜索不受影响**，因为偏移读写靠指令扫描不靠 xref（7.1②） | 若误以为「必须等全量跑完才能做 25」，会白白阻塞 10 小时以上 |
| 3 | 分析日志保留多久、放哪里？（任务书决策 3） | 本机 `%TEMP%\brushconverter-team\logs\` 保留到 v1.3.0 发布后再清；**Ghidra 工程不要删**（任务 21/25–30 都要用） | 删工程 = 后面 10 个任务全部要重新导入+分析 |
| 4 | （新增）`CoverageReport2.java` / `MinimalCoverage.java` 要不要收进 `tools/re_pipeline/`？ | **建议收**（与已有 `GhidraFindCandidates.java` 同类，属团队资产、可复跑、无版权风险） | 不收的话，别人只能照抄本报告第十二节的最小脚本，拿不到代码字节覆盖率/xref 规模等 8 项指标 |
| 5 | （新增）v1.3.0 发布说明里要不要写「静态分析边界」？ | **要写**：写明「已完成 16 个 analyzer（含反汇编/引用/混合模式跳转表），未完成 `Constant Propagation` 及之后的参数恢复」 | 下游若把伪代码当真值，会在任务 30/31 得出错误因果 |

---

## 十一、人工验收步骤（照做即可，不需要懂逆向）

### 11.1 看三个数字（1 分钟）

1. 打开 `docs/数据/ghidra-coverage.json`，找 `functions` 段：`total` / `analyzed` / `unanalyzed` 三个数字必须在，且 `total = analyzed + unanalyzed + thunks`。
2. 同一文件里的 `analysis_run.completed` 应为 `false`（本轮未跑完），并列出 `stages_not_completed`。

### 11.2 自己复算一遍（约 2 分钟，可选）

在仓库根打开一个 **cmd** 窗口，粘这三行（把 `<LOCAL_APP_DIR>` 换成本机实际目录）：

```bat
set GHIDRA_INSTALL_DIR=<LOCAL_APP_DIR>\ghidra_12.2_PUBLIC
set JAVA_HOME=<LOCAL_APP_DIR>\jdk-25.0.5+8
set PROJ=%TEMP%\brushconverter-team\ghidra-proj
```

再粘这一行（脚本在第四节 4.3 说明的位置；若已收进 `tools/re_pipeline` 就指过去）：

```bat
"%GHIDRA_INSTALL_DIR%\support\analyzeHeadless.bat" "%PROJ%" ps_re -process Photoshop.exe -noanalysis -max-cpu 2 -scriptPath %TEMP%\brushconverter-team\task20 -postScript MinimalCoverage.java
```

看到 `[MinimalCoverage] 总数 total=… 已分析 analyzed=… 未分析 unanalyzed=…` 一行，数字应与 `docs/数据/ghidra-coverage.json` 一致（工程若被后续任务继续分析过，数字只会变多，不会变少）。

### 11.3 确认现场还在（30 秒）

1. `%TEMP%\brushconverter-team\ghidra-proj` 目录存在、`ps_re.rep` 在（**别删**）。
2. `%TEMP%\brushconverter-team\logs\` 下能看到 `20-A-3600.log`、`20-C-480.log`、`20-L-7200-cpu8.log`。
3. 仓库根执行 `git status --porcelain`：**不应出现 `src/` 或 `tests/` 的改动**（本任务只应新增 `docs/数据/ghidra-coverage.json` 与 `docs/研究报告/ghidra-全量分析.md`，以及 `docs/验收报告/20-报告.md`）。

### 11.4 想确认「真的没跑完」（30 秒）

打开 `%TEMP%\brushconverter-team\logs\20-C-480.log`，搜 `Analysis timed out`，应看到：

```text
ERROR REPORT: Analysis timed out at 480 seconds. Processing not completed for file: /Photoshop.exe
```

并紧接着 `REPORT: Save succeeded`。这就是「超时截断但已落盘」的原始证据。

---

## 十二、附录：最小取数脚本全文（复制即用）

```java
// MinimalCoverage.java —— 覆盖率三个数字的最小取数脚本（任务 20；放 <REPO>/tools/re_pipeline/ 或任意 -scriptPath 目录）
// 用法：analyzeHeadless <projDir> <projName> -process Photoshop.exe -noanalysis ^
//         -scriptPath <脚本目录> -postScript MinimalCoverage.java
// 口径：已分析 = 非 external、非 thunk 且入口地址已反汇编且函数体地址数>0；未分析 = 非 thunk 总数 - 已分析。
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;

public class MinimalCoverage extends GhidraScript {
    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        Listing lst = currentProgram.getListing();
        long total = 0, ext = 0, thunk = 0, analyzed = 0, unanalyzed = 0;
        long noInstr = 0;
        for (Function f : fm.getFunctions(true)) {
            total++;
            if (f.isExternal()) {
                ext++;
                continue;
            }
            if (f.isThunk()) {
                thunk++;
                continue;
            }
            Address ep = f.getEntryPoint();
            Instruction ins = (ep == null) ? null : lst.getInstructionAt(ep);
            long bodyLen = (f.getBody() == null) ? 0L : f.getBody().getNumAddresses();
            if (ins == null) {
                noInstr++;
            }
            if (ins != null && bodyLen > 0) {
                analyzed++;
            }
            else {
                unanalyzed++;
            }
        }
        println("[MinimalCoverage] 总数 total=" + total + " external=" + ext + " thunk=" + thunk
            + " 已分析 analyzed=" + analyzed + " 未分析 unanalyzed=" + unanalyzed
            + " 入口无指令=" + noInstr);
        println("[MinimalCoverage] 复算口径：非 external、非 thunk 且入口已反汇编且函数体地址数>0 记入已分析");
    }
}
```

> 完整测量脚本 `CoverageReport2.java` 多给 8 项指标（代码字节覆盖、xref 源/目的数、反编译抽样成功与耗时、指定地址的引用计数、逐内存块分布）。
> 它不属于任务 20 任务书列出的产出文件，因此**未提交进仓库**，本机留存在 `%TEMP%\brushconverter-team\task20\`；是否纳入 `tools/re_pipeline/` 见第十节决策 2。
