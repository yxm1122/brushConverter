# 任务 11：逆向定位候选代码路径（Photoshop 纹理亮度/对比度 + 混合模式）

> 执行者：re-line（rea-ops 预设）｜执行日期：2026-11-18｜目标：`<LOCAL_APP_DIR>\PS2025\Adobe Photoshop 2025\Photoshop.exe`
> 只读静态分析：未修改 PS 安装目录任何文件、未注入、未 hook、未改注册表。
> 证据文件全部在 `docs/研究报告/附件/`，Ghidra 工程在**仓库外** `%USERPROFILE%\AppData\Local\Temp\brushconverter-team\ghidra-proj`。

---

## 一、结论（先看这一段）

1. **候选函数共 15 个**（命中 ≥1 条纹理/混合模式线索且能定位到函数边界的），其中 **high 档 4 个、medium 档 5 个、low 档 6 个**；另有 1 组「不在 `.pdata` 函数表内的代码点」单列（2.4 节说明原因）。
2. 本任务最重要的发现**不是某个函数**，而是**结构化现场**：Photoshop 把笔刷纹理参数当作**描述符（descriptor）键值**存取，键名是一个字符串池，位置与字段偏移已实测确认——
   - 键名池（`.rdata`，文件偏移 140046336 起）：`textureBrightness`、`textureContrast`、`textureBlendMode`、`textureDepth`、`minimumDepth`、`textureDepthDynamics`、`textureScale`、`invertTexture`、`protectTexture`、`useTexture`、`textureCoverage`
   - 宿主结构体偏移（由读、写两侧指令共同确认）：`+0x10`=textureScale(double)、`+0x18`=textureContrast(int32)、`+0x1c`=textureBrightness(int32)、`+0x20`=invertTexture(bool)、`+0x21`=protectTexture(bool)
3. **high 档 4 个**：`0x141a939d5`（读亮度/对比度组）、`0x141a96230`（写同组）、`0x141a935f0`（读混合模式/深度组）、`0x141b94750`（TransferMode 名字→枚举注册表构造）。每个都给两条以上独立证据（第四节）。
4. **给任务 11A 的关键建议**：不要去「猜」像素数学函数的地址，而是在 11A 里对参数结构体的 `+0x18` / `+0x1c` 下**硬件读断点（watchpoint）**，一次就能抓到真正消费 brightness/contrast 的像素代码（第六节建议 0）。
5. **PS 自己的枚举号**已实测：`linearHeight=0x2A(42)`、`height=0x29(41)`、`hardMix=0x1E(30)`、`linearDodge=0x1F(31)`。注意这**不等于** Krita 侧的 `linearHeight=15`（那是 Krita 自己的 PS 兼容映射），两者不能混用——直接影响任务 11B/13 的枚举表。
6. **与任务 10 的实测锚点逐条对得上**（详见「六·补」节）：静态代码里「depth 被 clamp 到 100、brightness/contrast 不 clamp」与任务 10 的 JSX 实测**完全一致**——这反向证明 H1/H3 就是这组参数的真实读写点，不是同名巧合。
7. **Ghidra 全量自动分析在本机不可行（已实测证伪）**：`.text` 有 136,037,140 字节；实跑 `analyzeHeadless` 共 **1,744.9 秒（29.1 分钟）**，其中自动分析在 **1,500 秒**上限被强制中断（日志原文 `Analysis timed out at 1500 seconds. Processing not completed`），12 个目标地址的交叉引用只拿到 **1 条**；而本机分析期间实测空闲物理内存仅 5.5～6.7 GB、页面文件可用 0.55 GB。相反，Ghidra 仍成功建库并给出量级：**函数 418,068 个 / 符号 2,969,153 个**（`附件/ghidra_photoshop_xref.tsv`）。因此主证据链改用「`.pdata` 函数表 + capstone 精确反汇编」，Ghidra 侧只做受限验证（实测数据见第九节）。这一改动**没有降低证据强度**：每条结论都附「指令地址 + 反汇编文本 + 函数边界」，而不是伪代码猜测。

---

## 二、现场与方法

### 2.1 目标文件事实（实测）

| 项目 | 实测值 | 取数方式 |
|---|---|---|
| 文件大小 | 208,456,176 字节 | `pe_scan.py` |
| SHA-256 | `05720af2cd207ead737b4c9ff422ffb7191f2d65cc0045b88735030cda76c953` | `mcp__rea__inspect_pe_imports`（REA 证据） |
| 镜像基址 | 0x140000000 | `pe_scan.py` |
| 区段数 | 9 | 同上 |
| `.text` | VA 0x1000；虚拟大小 136,037,140；原始大小 136,037,376；可执行 | 同上 |
| `.rdata` | VA 0x81be000；虚拟大小 50,608,082 | 同上 |
| `.data` | VA 0xb202000；虚拟大小 15,536,048；**原始大小只有 5,202,944**（其余是 BSS，运行时才填） | 同上 |
| `.pdata` | VA 0xc0d3000；5,258,124 字节 → **438,177 个函数记录** | 同上 / `native_xref.py` |
| 入口点 RVA | 0x2a04d30 | 同上 |
| 导出表 | RVA 0xb1aa900，104,544 字节 | 同上 |
| 导入表 | 84 个 DLL / 4,036 个函数；延迟导入 640 字节 | `mcp__rea__inspect_pe_imports` |
| 加固属性 | DYNAMIC_BASE、HIGH_ENTROPY_VA、NX 均开启 | `mcp__rea__inspect_binary_hardening` |

### 2.2 为什么分析 Photoshop.exe 本体（换目标的证据）

任务书允许「缩小/更换分析范围」，但必须先给证据。三条证据都指向「笔刷引擎就在 exe 里」：

1. **同目录没有任何笔刷/绘制引擎 DLL**：

   ~~~
   $ ls "/d/program/PS2025/Adobe Photoshop 2025/Required/" | grep -iE "brush|paint|render|pixel|raster|mask|textur"
   DBrush
   Default Brushes.abr
   $ ls -la ".../Required/DBrush/"
   BristleBrushCollection_CS5.dbrsb   254863
   BristleBrushCollection_CS5.dbrush  361492
   ~~~

   `DBrush` 只是两个笔刷**数据**文件（不是 DLL）；`Plug-ins/` 下只有 `Generator / MouseHookPro64.8li / sd-ppp`。
2. **笔刷纹理参数键名字符串就在 exe 的 `.rdata` 里**（2.5 节），而且被 exe 自己的代码用 rip 相对寻址直接引用。
3. **导入表里没有任何像素/图像处理 API**：84 个导入 DLL 中与图形相关的只有 `GDI32/USER32/gdiplus/opencv_world4110/d3d12/DirectML/onnxruntime/aif.dll(OpenCL)`；`GetDIBits/StretchBlt/AlphaBlend/CreateDIBSection` 这类位图 API 一条都没被导入（见 `附件/ps_imports_summary.json`）。说明**纹理合成是 exe 内部自己写的**，不在可替换的图形库里。

> 结论：本任务「换目标」不成立——分析 Photoshop.exe 是对的；真正要换的是**分析方法**（不用 Ghidra 全量自动分析），理由见第九节。

### 2.3 三条证据链（`tools/re_pipeline/` 里的脚本）

| 链 | 工具 | 做什么 | 为什么可信 |
|---|---|---|---|
| ① 字符串定位 | `pe_scan.py` | 自写 PE 解析器：镜像基址、9 个区段的 VA/RVA/文件偏移映射；全文件按 ASCII 与 UTF-16LE 双编码扫描关键词 | 纯文件字节取证，可复算（报告给出文件偏移→VA 的全过程） |
| ② 函数边界 | `native_xref.py range / refs` | 直接解析 **`.pdata`（异常目录 / RUNTIME_FUNCTION 表）** 得到 438,177 条 `(begin,end,unwind)` 记录，二分查找把任意代码地址归到所属函数 | 这是 Windows x64 编译器自己写进去的栈展开表，**不是启发式**；函数边界误差为 0 |
| ③ 交叉引用 | `native_xref.py refs` | 先在 `.text` 里按机器码模式（`REX + 0x8D/0x8B + ModRM(rip) + disp32`，7 字节）预筛，再用 **capstone 5.0.7 逐条反汇编校验**有效地址，最后用 `.pdata` 归函数 | 纹理组预筛 60 条、校验通过 60 条（**零误报**）；报告每条都给出「指令地址 + 反汇编文本」 |

调用关系另有一条链：扫 `.text` 里的 `E8 rel32`，目标落在候选函数起始地址后，**从调用者函数起点线性反汇编到该地址，确认那里确实是一条 call** 才计入。

### 2.4 方法的已知局限（必须一起读）

1. **只覆盖 rip 相对寻址的 `lea` / `mov`**：`mov reg, imm64`、寄存器间接、把字符串地址塞进跳转表的写法会漏。本次实测漏检的例子就是第 2 条。
2. **`.pdata` 里没有记录的函数**：有 2 处代码点（`0x1463a576f` / `0x146494b8f` 附近）引用了 `linearDodge`/`hardMix`/`linearHeight`，但所在区域没有栈展开记录，归不到函数。反汇编看得很清楚：那是一串 `lea rax,[rip+X]; ret` 的**桩函数（stub）**，每个返回一个混合模式的内核名字符串：

   ~~~
   0x1463a5767 lea rax, [rip + 0x2200c22]  ; "passThrough" @0x1485a6390
   0x1463a576f lea rax, [rip + 0x2200bca]  ; "linearDodge" @0x1485a6340
   0x1463a5777 lea rax, [rip + 0x2200bd2]  ; "linearBurn"  @0x1485a6350
   0x1463a5797 lea rax, [rip + 0x2200b9a]  ; "hardMix"     @0x1485a6338
   0x1463a57bf lea rax, [rip + 0x202aba2]  ; "height"      @0x1483d0368
   ~~~

   这类桩通常是**跳转表分支目标**，11A 用动态断点确认更可靠（第六节建议 3）。
3. **间接调用（`call [reg]` / vtable 派发）抓不到**：所以「调用者数量」是**下界**，不是全集。
4. **`Brightness`/`Contrast`/`Multiply`/`Subtract` 这类泛词是噪声源**：`Multiply` 有 1,002 处、`Subtract` 有 1,044 处（字体表、色彩管理、UI 文案都用）。本任务只用**带前缀的内部标识符**（`textureBrightness`、`blend_hard_mix`、`linearHeight`）当线索——这就是候选从上百个收敛到 15 个的真正原因。

### 2.5 结构化现场（本任务最有价值的产出）

**(a) 笔刷纹理参数键名池**（`.rdata`，文件偏移 140046240 起，逐条实测）：

~~~
140046280 texture            140046376 useTexture
140046288 textureScale       140046392 textureCoverage
140046304 invertTexture      140046408 textureBlendMode
140046320 protectTexture     140046432 textureDepth
140046336 textureBrightness  140046448 minimumDepth
140046360 textureContrast    140046464 textureDepthDynamics
~~~

这 11 个键名**同时**被 `0x141a8b470`（11 条）与 `0x141a8e060`（11 条）引用，前后紧邻 `brushPreset`、`useScatter`、`scatterDynamics`、`dualBrush`、`wetEdges` 等键——即**笔刷预设描述符的完整键表**，与本项目 ABR 解析器读到的参数是同一组。

**(b) 宿主结构体字段偏移**（由「读」「写」两侧同字段对照得出，两侧指令见第四节）：

| 偏移 | 字段 | 类型证据 |
|---|---|---|
| +0x00 | useTexture | `movzx r8d, byte ptr [rcx]` |
| +0x01 | textureCoverage | `byte ptr [rbx+1]` |
| +0x04 | textureBlendMode | `dword ptr [rbx+4]`，经 TransferMode 注册表解析 |
| +0x08 | textureDepth | 整数被 clamp 到 0～100（`cmp eax,0x64` + `cmovg`） |
| +0x0c | minimumDepth | 同上 clamp |
| +0x10 | textureScale / textureDepthDynamics | 双精度（`movsd` / `cvtdq2pd`） |
| +0x18 | **textureContrast** | `dword ptr [rdi+0x18]` |
| +0x1c | **textureBrightness** | `dword ptr [rdi+0x1c]` |
| +0x20 | invertTexture | `byte ptr [rdi+0x20]` |
| +0x21 | protectTexture | `byte ptr [rdi+0x21]` |

**(c) TransferMode 名字→枚举注册表**：`0x141b94750` 是静态初始化函数，逐个调用「字符串→内部 id」登记函数（`0x142c90280`），把 `(内部id, 枚举号)` 成对写进 `.data` 区 `0x14b219bc0` 起的表；遍历该表的代码在 `0x141b94090`（读 textureBlendMode）：

~~~
0x141b940d9 call 0x141b94750             ; 取表基址
0x141b940de cmp dword ptr [rax], 0x36    ; 0x36 是结束哨兵
0x141b940e3 cmp dword ptr [rax + 4], ebx ; 与描述符里的值比较
0x141b940e8 add rax, 8                   ; 记录跨度 8 字节 = {id:dword, value:dword}
~~~

实测到的枚举号（`0x141b94750` 内指令序列）：`hardMix=0x1E`、`linearDodge=0x1F`、`linearBurn=0x20`、`linearLight=0x21`、`vividLight=0x22`、`pinLight=0x1B`、`passThrough=0x28`、`height=0x29`、`linearHeight=0x2A`、`lighterColor=0x2B`、`blendSubtraction=0x35`、`blendDivide=0x36`。

---

## 三、候选函数表（15 个）

> 地址均为 VA（镜像基址 0x140000000）。「引用次数」= capstone 校验通过且能归到该函数体的指令条数（下界，见 2.4-3）。

| # | 函数地址 | 大小 | 档位 | 命中线索（引用次数） | 判读（证据支撑） |
|---|---|---|---|---|---|
| H1 | `0x141a939d5` | 206 | **high** | `invertTexture`、`protectTexture`、`textureBrightness`、`textureContrast`（4 条） | 从描述符**读出**纹理的反相/保护/亮度/对比度到结构体 +0x20/+0x21/+0x1c/+0x18 |
| H2 | `0x141a96230` | 332 | **high** | `textureScale`、`invertTexture`、`protectTexture`、`textureBrightness`、`textureContrast`（5 条） | 把同一组字段**写回**描述符（H1 的写侧对照） |
| H3 | `0x141a935f0` | 486 | **high** | `useTexture`、`textureCoverage`、`textureBlendMode`、`textureDepth`、`minimumDepth`、`textureDepthDynamics`（6 条） | 从描述符**读出**混合模式/深度组到 +0x00/+0x01/+0x04/+0x08/+0x0c/+0x10，深度 clamp 到 0～100 |
| H4 | `0x141b94750` | 670 | **high** | `hardMix`、`linearDodge`、`linearHeight` 等 20+ 个模式名（表内 3 条直接命中） | 构造「TransferMode 名字 → (内部id, 枚举号)」注册表，返回表基址 |
| M1 | `0x141a960c0` | 357 | medium | 同 H3 的 6 条 | H3 的写侧：把混合模式/深度组写回描述符 |
| M2 | `0x141a8b470` | 921 | medium | `textureScale`…`textureDepthDynamics`（11 条全齐） | 构建/比对 11 个纹理键名的指针数组（键表本身） |
| M3 | `0x141a8e060` | 3,061 | medium | 11 条全齐，且调用 `0x141b952c0` 写混合模式 | 批量读写纹理参数（含混合模式写入路径） |
| M4 | `0x141b94090` | 166 | medium | 经 `0x141b94750` 注册表解析 `textureBlendMode` | 「描述符里的混合模式值 → 内部枚举号」翻译器；被 H3/M3 调用 |
| M5 | `0x14298d3e0` | 1,324 | medium | `hardMix`、`linearDodge`（2 条）+ `cmp ecx,0x27` 跳转表 | 反向映射：枚举号(0～0x27) → 模式名（normal/dissolve/multiply/screen/overlay/softLight/hardMix/linearDodge/blendDivide…） |
| L1 | `0x141a937fb` | 474 | low | `textureScale`（1 条） | 只碰纹理缩放，与亮度/对比度无关 |
| L2 | `0x141308af0` | 1,022 | low | `copyBrushTexture`（1 条） | 纹理位图拷贝（数据准备，不是公式） |
| L3 | `0x1438d2ae0` | 4,422 | low | `useTexture`、`invertTexture`、`textureDepth`（3 条，各 2 处）+ `cmp edx,0x35` 跳转表 | 另一套「读混合模式」实现（键用 4CC 数字常量而非字符串） |
| L4 | `0x1438dfcf0` | 2,344 | low | `useTexture`、`invertTexture`、`textureDepth`（3 条） | 与 L3 成对的写侧 |
| L5 | `0x141b952c0` | 157 | low | 无字符串，但遍历 H4 的表并写入 `dword [*out]` | 「内部枚举 → 描述符值」的反向翻译（M4 的对偶） |
| L6 | `0x141ef92e0` / `0x141efd020` | 2,502 / 2,026 | low | 第二份 `protectTexture` 字符串副本 | 调用 H3/H2 的上层批量函数（笔刷预设装载/保存链路） |

**没进表的（附理由）**：`0x1431a5770`（251,619 字节）/ `0x1431e2e60`（304,361 字节）只命中 `Blend Modes` 一次，且函数体大到不像真实函数（`.pdata` 记录粒度问题或巨石代码块），信息量被稀释，未纳入；`0x140212aa7` 附近命中的是 `Multiply` 的**另一份字符串副本**（噪声）；`OWLThemeGetBrightnessAdjustedColor` 是 Adobe 自家 UI 框架的「界面配色调亮」，是最容易误判的**同名陷阱**，不是纹理处理。

---

## 四、high 档候选的两条独立证据（逐条）

### H1 `0x141a939d5`（读 亮度/对比度/反相/保护）

**证据 A（字符串引用，指令级，4 条全部 capstone 校验）**：

~~~
0x141a93a2a  lea rdx, [rip + 0x6afcdaf]  ; "invertTexture"      @0x1485907e0
0x141a93a42  lea rdx, [rip + 0x6afcda7]  ; "protectTexture"     @0x1485907f0
0x141a93a5a  lea rdx, [rip + 0x6afcd9f]  ; "textureBrightness"  @0x148590800
0x141a93a72  lea rdx, [rip + 0x6afcd9f]  ; "textureContrast"    @0x148590818
~~~

**证据 B（与写侧成对，字段偏移一致）**——同一结构体的偏移在写函数 `0x141a96230` 里被逐一读取并写进描述符，顺序与偏移完全对应：

~~~
0x141a962b1  movsd xmm3, qword ptr [rdi + 0x10]  ; textureScale      (double)
0x141a962db  movzx r8d, byte ptr [rdi + 0x20]     ; invertTexture     (bool)
0x141a96300  movzx r8d, byte ptr [rdi + 0x21]     ; protectTexture    (bool)
0x141a96320  mov r8d, dword ptr [rdi + 0x1c]      ; textureBrightness (int32)
0x141a9633f  mov r8d, dword ptr [rdi + 0x18]      ; textureContrast   (int32)
~~~

**证据 C（第三条，补充）**：读侧先经 `0x142c52870`（判断描述符有无该键）再经 `0x142c4e820`（取整数值）落到 `[rdi+0x1c]` / `[rdi+0x18]`，与写侧 `0x142c5b3f0`（写整数值）对称——**同一二进制里读写两个方向的 API 与偏移都吻合**，可排除「名字像但不是同一组参数」的可能。

### H2 `0x141a96230`（写 亮度/对比度组）

**证据 A**：5 条字符串引用（`0x141a962aa` / `0x141a962d4` / `0x141a962f9` / `0x141a96319` / `0x141a96338`）。
**证据 B（调用者，capstone 逐个校验）**：2 个直接调用者，说明它处在真实业务链路而不是死代码：

~~~
0x141ef1736  call 0x141a96230   (caller 0x141ef15c0, size 2339)
0x141efd083  call 0x141a96230   (caller 0x141efd020, size 2026)
~~~

### H3 `0x141a935f0`（读 混合模式/深度组）

**证据 A**：6 条字符串引用（`0x141a93622` / `0x141a9363e` / `0x141a93651` / `0x141a9365d` / `0x141a936c5` / `0x141a9371f`）。
**证据 B（4 个直接调用者 + 与写侧 M1 成对）**：

~~~
0x141a8f96b  call 0x141a935f0   (caller 0x141a8f520, size 2286)
0x141a90741  call 0x141a935f0   (caller 0x141a90410, size 1781)
0x141ef2222  call 0x141a935f0   (caller 0x141ef1f90, size 2183)
0x141ef9480  call 0x141a935f0   (caller 0x141ef92e0, size 2502)
~~~

而且它取 `textureBlendMode` 不是直接读，而是 `0x141a93651 call 0x141b94090`（走 TransferMode 注册表翻译）——这条把 H3 与 H4 **硬连接**，是本任务「同一调用簇」判定的核心依据。

### H4 `0x141b94750`（TransferMode 注册表构造）

**证据 A**：函数体内连续出现 `lea rcx,[rip+X]` + `call 0x142c90280`（字符串→内部 id）与 `mov dword [rip+Y], 枚举号` 的成对序列，覆盖 `hardMix=0x1E`、`linearDodge=0x1F`、`linearBurn=0x20`、`linearLight=0x21`、`vividLight=0x22`、`pinLight=0x1B`、`passThrough=0x28`、`height=0x29`、`linearHeight=0x2A`、`lighterColor=0x2B`、`blendSubtraction=0x35`、`blendDivide=0x36`（原始输出见附件 `证据汇总-原始输出尾部.txt` 的 disasm 段）。
**证据 B**：7 个直接调用者（capstone 校验），其中一个就是混合模式解析器 `0x141b94090`，另一个是写侧 `0x141b952c0`：

~~~
0x141b91e6f → caller 0x141b91e60 (82)
0x141b935b7 → caller 0x141b93520 (414)
0x141b93daf → caller 0x141b93da0 (76)
0x141b9401f → caller 0x141b93fe0 (163)
0x141b940d9 → caller 0x141b94090 (166)   ← textureBlendMode 读
0x141b95237 → caller 0x141b95220 (149)
0x141b952df → caller 0x141b952c0 (157)   ← textureBlendMode 写
~~~

---

## 五、分档依据（判据写清楚，便于复核）

| 档位 | 判据（同时满足） |
|---|---|
| **high** | ① 命中 ≥3 条**带前缀的内部标识符**（不是 Multiply/Contrast 这类泛词）；② 有 ≥2 条**独立**证据（字符串引用 / 调用者 / 字段偏移 / 注册表联动，四者取二）；③ 函数体 <4 KB，不是巨石代码块；④ 与「纹理」语义直接相关 |
| **medium** | ① 命中 ≥1 条内部标识符；② 只有一类证据（例如只有字符串引用，或只有「与 high 成对」）；③ 或功能明确但与亮度/对比度无直接关系（键表构建、缩放、批量 IO） |
| **low** | ① 只命中 1 条线索，或线索来自**第二份字符串副本**；② 或属于数据准备/上层装载链路；③ 或函数体异常巨大、信息量被稀释 |

「同一调用簇」的落地方式：H3 → `0x141b94090` → H4 注册表 → `0x141b952c0` → M3 批量写；四个节点用**实际 call 指令地址**串起来，不是靠地址相邻猜。

---

## 六、给任务 11A 的验证建议（在哪下断点、看哪些参数）

> 前置：x64dbg 未安装（`/d/program` 下无 `x64dbg*`），本机只能用 Ghidra 自带调试器或纯黑盒（任务 12）。以下观察点**不依赖 x64dbg 也能做**。

### 建议 0（最重要）：用「硬件数据断点」抓像素数学，而不是猜函数地址

静态分析只能确认「参数在哪、什么类型」，**像素公式在哪算**靠静态很难定（SIMD 展开 + 内联）。最省时的做法：

1. 在 `0x141a939d5`（H1）返回处停下，记下 `rdi`（结构体基址）；
2. 对 `[rdi+0x18]`（contrast）与 `[rdi+0x1c]`（brightness）各下一个**硬件读断点**（x64dbg 用 `ba r4 [rdi+0x18]`，Ghidra 调试器用 `watch`）；
3. 继续运行 → 第一次命中的代码就是**真正消费亮度/对比度的函数**（大概率就是纹理掩码合成函数）。这比在 136 MB 代码里找 SIMD 特征快两个数量级。

### 建议 1：确认单位与量纲（决定 11B 的公式形式）

- 断点：`0x141a93a61`（textureBrightness 读取后）/ `0x141a93a79`（textureContrast 读取后）
- 看：`[rdi+0x1c]` / `[rdi+0x18]` 的整数值，与 ABR 文件里该笔刷的 brightness/contrast 原始字节对照。
- 已知线索：同函数里 `textureScale` 是 double，深度类字段是「整数 0～100 后 clamp」；而 brightness/contrast 是 **int32 且无 clamp**。结合任务 10 的 JSX 实测（brightness 接受 -150..150、contrast 接受 -100..100、原样不钳制），**最可能存的是 -150..150 / -100..100 的整数**（`+0x18`=contrast、`+0x1c`=brightness 各 4 字节）。仍需 11A 用真实 ABR 值核对，**不许直接写进 11B**。

### 建议 2：确认混合模式枚举号映射（决定 11B/13 的枚举表）

- 断点：`0x141a93651`（H3 内 `call 0x141b94090` 处）
- 看：进入时 `rdx` 指向 `textureBlendMode`；返回后 `[rbx+4]` 就是内部枚举号。用 ABR 里 `Linear Height` / `Hard Mix` 两种纹理模式各跑一次，即可把 **ABR 模式值 ↔ PS 枚举号**钉死。
- 已确认 PS 侧枚举（静态证据）：`hardMix=0x1E`、`linearHeight=0x2A`、`height=0x29`、`linearDodge=0x1F`。
- **警告**：Krita 的 `linearHeight=15` 是 Krita 自己的 PS 兼容枚举，**不要**与 PS 内部 0x2A 混用；11B 转写时必须分成两张表。

### 建议 3：桩函数（2.4-2）用动态断点确认

- 地址：`0x1463a576f`（返回 linearDodge）、`0x1463a5797`（hardMix）、`0x1463a57c7`（linearHeight）
- 做法：在这些 `lea rax,[rip+X]; ret` 上下断点，看谁 call 进来（这类桩基本只当跳转表分支），即可补出「枚举号 → 实现函数」的真表。

### 建议 4：不要下的断点

- `0x141b94750`（H4）：只在启动时执行一次（静态初始化），运行时断不到，**改为读表**（表基址 `0x14b219bc0`，记录跨度 8 字节，`dword[0]==0x36` 结束）。
- `0x141a8b470` / `0x141a8e060`（M2/M3）：键表构建/批量 IO，命中的是字符串**地址**而不是参数值，断它只能确认「参数被拷贝」，看不到量纲。

---

## 六·补、与任务 10 证据的对照（lead 转达的 178 张真值图结论）

> 任务 10 已确认「未找到可直接采用的 PS 纹理亮度/对比度公式」，并给出 4 条实测锚点。下面逐条对照本任务找到的候选——**这决定 11A 该在哪下断点**。

| 任务 10 的实测锚点 | 本任务静态证据 | 对照结论 |
|---|---|---|
| **① d=100 时位移型模式被精确复现**：减去 = clip(base − brightness)；变暗/正片叠底/线性加深 = clip(base + brightness)（差异 0.00%、最大差值 0） | H1 `0x141a939d5` 把 brightness 读成 **int32**（`dword [rdi+0x1c]`），H2 原样写回；没有比例缩放、没有查表 | **一致（参数侧）**：亮度在参数层就是「一个整数标量」，后面做 `base ± brightness` 的逐点偏移+截断完全说得通。**但执行偏移与 clip 的代码仍未定位**（第十节遗留 1）。 |
| **② 对比度 = 以 127.5 为中点的分段倍率**，改 128 立刻恶化到 25.89% → 说明是「8-bit 整数域 + 127.5 中点/舍入」实现 | contrast 同样是 **int32**（`dword [rdi+0x18]`），无钳制；同函数内可见整数↔双精度转换（`cvtdq2pd` / `movsd`）与常数 **100.0**（实测 `0x1483d7978` = `double 100.0`，文件偏移 138240376）——即「百分数 → 归一化」的痕迹 | **一致（量纲侧）**：参数是百分数整数，转成浮点再进 8-bit 运算，与 127.5 中点这种「整数域舍入敏感」的实现方式不矛盾。**注意**：这段 100.0 出现在 **depth/scale** 的读取路径上（H3），不一定是对比度路径；不要把 100.0 直接当成对比度公式的系数。 |
| **③ 负面线索：d<100 与 Height 系列（Height/Linear Height/Hard Mix）的亮度/对比度不在最终图的可解释 LUT 里**（最优 26%–100% 不合格）→ 应找「纹理预处理 → 掩码合成前的插入点」，不要找整图 LUT/后处理 | 本任务 15 个候选**全部落在「笔刷预设参数读写层」**（描述符键 ↔ 结构体字段），没有一个落在「整图 LUT / 整图后处理」API 上；像素运算函数尚未出现在候选里 | **强一致**：静态结构正是「参数进结构体 → 后面某处内联消费」。这反过来说明：**11A 不要在全球范围找 LUT 表，而要在结构体字段被消费的那一瞬间抓现行**（见第六节建议 0 的 watchpoint 方案）。这条负面线索与我们的候选分布互相印证。 |
| **④ JSX 实测：brightness ∈ -150..150、contrast ∈ -100..100 原样接受不钳制；depth 101→100 被钳制** | H3 `0x141a935f0`：`textureDepth` / `minimumDepth` **有** `cmp eax,0x64` + `cmovg`（clamp 到 100）；H1 的 brightness/contrast 读取路径**没有任何 clamp** | **完全一致（行为级交叉验证）**：静态代码的「谁被 clamp、谁不被 clamp」与 JSX 实测**逐项对得上**。这是本任务最硬的一条外部交叉证据——说明 H1/H3 就是那组参数的真实读写点，不是同名巧合。 |

**由此调整 11A 的优先级**（比第六节更具体）：

1. 先跑 **建议 1**（确认 brightness/contrast 的量纲）——因为任务 10 已经证明「位移语义 + 0.00% 误差」在 d=100 成立，只要确认 PS 内部存的是 -150..150 的整数（而不是 0..100），11B 就能把 `clip(base ± brightness)` 直接写成参考实现，不必等像素函数定位。
2. 再跑 **建议 0**（watchpoint）——目标函数要能解释任务 10 的第 ③ 条：它应当作用在「纹理层 → 掩码」之间，且**对 d<100 与 Height 系列也生效**（否则就不是我们要的那一段）。
3. 若 watchpoint 抓到的函数只处理 d=100 分支，说明还有第二处消费点（Height 系列另走一条路径），需再挂一次 watchpoint。

---

## 七、验收标准逐条对照

| 任务书验收标准 | 结论 | 证据 |
|---|---|---|
| `docs/研究报告/ps-逆向-静态.md` 存在，含「候选表 / 分档依据 / 给 11A 的验证建议」三部分 | 达成 | 本文件第三、五、六节 |
| 候选表每行都有地址、命中的线索、被引用次数 | 达成 | 第三节表格：地址 / 大小 / 档位 / 命中线索（引用次数）/ 判读 |
| 至少 1 个 high 档候选给出两条独立证据 | 达成（4 个 high 全部 ≥2 条） | 第四节 H1～H4 |
| 建库耗时与符号数量有具体数字 | 达成 | Ghidra 对 `Photoshop.exe`：**总耗时 1,744.9 秒（29.1 分钟）**，**函数 418,068 个 / 符号 2,969,153 个**（`附件/ghidra_photoshop_xref.tsv` 首部）；自动分析在 1500 秒上限被中断（原文见 `附件/ghidra_photoshop.log`）。另：Ghidra 冒烟（PSRes.dll）**16.3 秒 / 127 符号**；免 Ghidra 主链路的函数边界数 **438,177**（`.pdata` 记录） |
| Ghidra 工程与伪代码没有进 git | 达成 | 工程在仓库外 `%USERPROFILE%\AppData\Local\Temp\brushconverter-team\ghidra-proj`；`git status --short` 只见 `tools/re_pipeline/` 与 `docs/研究报告/` 两个新增目录 |
| PS 安装目录文件修改时间未变 | 达成 | mtime 仍为 `Jan 30 2026`、大小 208,456,176、SHA-256 `05720af2…c953`；全程只以 `"rb"` 只读打开 |

---

## 八、关键数据与改动清单

### 8.1 关键数字（供 lead 回写台账）

- 目标 208,456,176 字节；`.text` 136,037,140 字节；`.pdata` 函数记录 **438,177** 个。
- 字符串命中：`Linear Height`×2、`Hard Mix`×5、`Linear Dodge`×4；内部标识符池 `blend_*` 共 **185** 条。
- 交叉引用扫描：纹理组 21 个字符串地址 → 预筛 60 条 → capstone 校验 **60 条（100% 通过，零误报）** → 14 个函数；混合模式组 2,072 个字符串地址 → 30 条 → 30 条 → 15 个函数。
- 候选：**15 个，high 4 / medium 5 / low 6**。
- 单次 refs 扫描（136 MB `.text` 全扫 + capstone 校验）耗时：**约 11 秒**（对比 Ghidra 全量分析 1,744.9 秒仍未完成）。
- Ghidra 侧实测：总耗时 **1,744.9 秒**、函数 **418,068** 个、符号 **2,969,153** 个、自动分析在 **1500 秒**上限被中断。

### 8.2 改动清单（全部为新增，未修改任何既有文件）

| 文件 | 说明 |
|---|---|
| `tools/re_pipeline/pe_scan.py` | 新增。PE 区段/地址换算 + 双编码字符串扫描（免依赖、只读） |
| `tools/re_pipeline/native_xref.py` | 新增。`.pdata` 函数表 + capstone 校验的交叉引用/调用者/反汇编/字符串池/指针槽位工具（8 个子命令） |
| `tools/re_pipeline/ghidra_headless.py` | 新增。`analyzeHeadless` 封装（工程放仓库外、MAXMEM 限制、日志落盘、超时） |
| `tools/re_pipeline/GhidraFindCandidates.java` | 新增。Ghidra 侧取证脚本（按给定字符串地址输出「引用者函数」TSV + 程序概况） |
| `tools/re_pipeline/collect_evidence.sh` | 新增。一键复跑全部取证命令，输出「命令原文 + 原始输出」 |
| `docs/研究报告/ps-逆向-静态.md` | 新增。本报告 |
| `docs/研究报告/附件/*` | 新增。原始证据（清单见第十一节） |

---

## 九、可行性实测：为什么不用 Ghidra 全量自动分析（原始数据）

### 9.1 环境偏差（先记清楚）

| 项 | 任务书/旧文档写的 | 本机实测 | 影响 |
|---|---|---|---|
| Ghidra | 11.4.1（`<LOCAL_APP_DIR>\ghidra_11.4.1_PUBLIC`） | **12.2**（`<LOCAL_APP_DIR>\ghidra_12.2_PUBLIC`，`application.version=12.2`、`application.java.min=25`） | 脚本 API 按 12.2 写（PyGhidra 取代 Jython，故用 `.java` 脚本） |
| Java | 21.0.3 | **25.0.5**（`<LOCAL_APP_DIR>\jdk-25.0.5+8`） | Ghidra 12.2 要求 ≥25，正好满足 |
| x64dbg | 未安装 | 仍未安装 | 11A 需降级（第六节前置说明） |
| 任务 10 结论 | 应有 `docs/研究报告/纹理亮度对比度-调研.md` | **并行执行中，本任务收尾时已落盘**（31,314 字节，2026-11-18 22:29，结论为「未找到可直接采用的公式」） | 本任务开工时该文件尚未出现，故分析是独立完成的；收尾时已按其结论补写「六·补」对照节（与 4 条实测锚点逐条比对） |

### 9.2 内存与规模（决定性数字）

- 分析期间实测（`GlobalMemoryStatusEx`）：`total_GB=23.29 avail_GB=5.56 load=76% pagefile_avail_GB=0.55`。
- Ghidra 侧：以 `MAXMEM=3g`、`-max-cpu 4` 导入 `Photoshop.exe` 后，`java.exe` 常驻已达 **1.84 GB**；136 MB `.text` 的自动分析在这台机器上不是「几分钟」的量级，而**页面文件可用只有 0.55 GB**（换页余量几乎为零）。
- 因此主证据链不依赖 Ghidra 全量分析：改用 `.pdata`（编译器写的函数边界表）+ capstone（工业级反汇编库），两者都是确定性、可复算的证据，单次全扫 11 秒。

### 9.3 Ghidra 侧实测（含原始数据）

1. **工具链冒烟通过**：对 `PSRes.dll`（55 KB）跑「建库 + 自动分析 + 脚本」，**耗时 16.3 秒**，脚本报告 `函数总数=0 符号总数=127`（该 DLL 是纯资源 DLL，无代码）。日志：`附件/ghidra_smoke.log`。
2. **对 Photoshop.exe 的完整 Ghidra 建库尝试（已完成，结果如实证伪了「全量分析可行」）**：
   - 命令（原文见 `附件/ghidra_photoshop.log` 首行）：`analyzeHeadless.bat <proj> ps_re -import Photoshop.exe -analysisTimeoutPerFile 1500 -max-cpu 4 -scriptPath tools/re_pipeline -postScript GhidraFindCandidates.java …`，`MAXMEM=3g`。
   - **总耗时 1,744.9 秒（29.1 分钟）**，退出码 0：其中导入（含 84 个依赖库导出符号解析）约 4 分钟，**自动分析在 1500 秒上限处被强制中断**。
   - 分析阶段耗时明细（Ghidra 自报，原文可查）：
     ~~~
     Call-Fixup Installer      683.964 secs
     Disassemble Entry Points  794.674 secs
     Create Function             0.542 secs
     Subroutine References      16.867 secs
     Total Time                1500 secs
     ~~~
     日志原文：`ERROR REPORT: Analysis timed out at 1500 seconds. Processing not completed …`
   - **脚本仍拿到了关键计数**（`附件/ghidra_photoshop_xref.tsv` 首部）：
     `function_count=418068`、`symbol_count=2969153`、`image_base=140000000`、9 个内存块边界与 `pe_scan.py` 的解析**完全一致**。
   - **交叉引用基本为空**（12 个目标地址里只有 `0x1485a6338（hardMix）` 拿到 1 条），原因是**参考（Reference）建立阶段还没跑到就被超时截断**——这正好反证：① 本机跑不完 Ghidra 全量分析；② 本报告的交叉引用必须靠 `.pdata`+capstone 那条链，而且那条链的结论**比这次 Ghidra 输出更完整**（60 条 vs 1 条）。
   - 结论：Ghidra 侧证据不是「失败」，而是**一次可量化的容量实测**：136 MB `.text` 的自动分析在本机至少需要 >1500 秒且未完成，配合 0.55 GB 页面文件余量，继续硬扛只有 OOM/拖死整机两种结果。

### 9.4 REA MCP 侧的两个硬限制（原始报错，供 lead 决策）

1. REA 的免 Ghidra PE 快检工具**默认拒绝 >64 MiB 输入**：

   ~~~
   REA 调用失败: {"error":{"code":"resource_constraint","message":"Selected artifact exceeds 67108864 bytes:
   <LOCAL_APP_DIR>\\PS2025\\Adobe Photoshop 2025\\Photoshop.exe","remediation":{"action":"Raise max_file_bytes
   above 67108864 bytes ..."}}}
   ~~~

   显式传 `max_file_bytes=268435456` 后可正常解析（导入表/加固信息就是这样拿到的），但**说明默认配置下 REA 不会碰这个文件**，后续任务要注意。
2. REA 的 Ghidra provider 走 MCP 单次响应 10 MiB + 宿主 deadline 约束，对 198 MB PE 的**首次全量分析**必然超时中断；按 REA 文档，宿主超时属「客户端 deadline」问题，不宜反复重试。

---

## 十、遗留问题 / 未完成（如实写）

1. **像素公式本身没有定位**（本任务范围外，但也确实没做到）：亮度/对比度落在结构体 `+0x18/+0x1c`，**谁消费这两个字段做像素运算，静态未定**。给出的是 11A 的 watchpoint 方案（第六节建议 0）。**不许把「结构体偏移」当成「公式」写进 11B。**
2. **`.pdata` 无记录区域的桩函数**（2.4-2）只确认了「返回哪个名字」，**没确认哪些枚举号跳到它**——需 11A 动态确认或读跳转表数据。
3. **间接调用/虚表派发的调用者抓不到**，所以「调用者数量」是下界；M2/M3 这类函数可能还有更多调用方。
4. **UTF-16 编码的相关字符串一条都没命中**（`Linear Height` 等 UTF-16 命中 0），说明 PS 内部用 ASCII 存这些键名；若后续要查中文资源串，需另做 `.rsrc` 解析（本任务未做）。
5. **Ghidra 对 Photoshop.exe 的「建库 + 全量分析」只做到「建库成功、分析超时」**（9.3-2 有完整数字：29.1 分钟、函数 418,068、符号 2,969,153、分析在 1500 秒被中断、交叉引用几乎为空）。也就是说：**任务书那条「建库一次并记录耗时/符号数」达成了**，但「用 Ghidra 的引用库直接产出候选表」在本机做不到——候选表是靠 `.pdata`+capstone 链得到的。这是**如实的能力边界**，不是「做完了」。
6. **任务 10 的报告在本任务开工时尚未落盘**（收尾时已出现，31,314 字节）。因此本任务的前半段是独立开展的，收尾时已按 lead 转达的 4 条实测锚点补写「六·补」对照节；若需要更严格的「先确认前置结论再开工」流程，建议 lead 在台账里把任务 10→11 设成显式依赖。

---

## 十一、附件清单（原始输出，均在 `docs/研究报告/附件/`）

| 文件 | 内容 |
|---|---|
| `证据汇总-原始输出尾部.txt` | 全部取证命令的**命令原文 + 原始输出**（由 `collect_evidence.sh` 生成，可一键复跑） |
| `ps_string_hits.json` / `ps_string_hits_ext.json` | 字符串命中与 VA/区段/编码（含 Linear Height / Hard Mix / textureBrightness 等） |
| `ps_xref_blend.json` | 混合模式组交叉引用（2,072 字符串地址 → 30 条指令 → 15 个函数） |
| `ps_xref_texture.json` | 纹理参数组交叉引用（21 字符串地址 → 60 条指令 → 14 个函数） |
| `ps_grep_texture.json` / `ps_grep_brush.json` / `ps_grep_bc.json` / `ps_grep_blendnames.json` | 字符串池正则检索（纹理 / 笔刷 / 亮度对比度 / `blend_*` 185 条） |
| `ps_imports_summary.json` | 84 个导入 DLL / 4,036 个函数的摘要 + SHA-256 |
| `ps_hardening.json` | 加固属性（DYNAMIC_BASE / HIGH_ENTROPY_VA / NX） |
| `ghidra_smoke.log` | Ghidra 12.2 冒烟建库完整日志（尾部 30 行含函数/符号计数） |
| `ghidra_photoshop.log` | 对 Photoshop.exe 的 Ghidra 建库/分析完整日志（29.1 分钟、分析超时、函数/符号计数；9.3 数据来源） |
| `ghidra_photoshop_xref.tsv` | Ghidra 脚本产出的程序概况 + 12 个目标地址的交叉引用结果（image_base、9 个内存块、function_count/symbol_count） |

---

## 十二、决策记录（lead 代为决策）

> 本会话禁止调用 `ask_user_question`，任务书第六节的三个人工决策按团队规则改写为「决策点 / 选项 / 选择 / 理由」，由 lead 复核后代为拍板。

| # | 决策点 | 选项 | 选择 | 理由 |
|---|---|---|---|---|
| 1 | high 档候选要不要继续投入（任务书决策 1） | (a) 继续 11A 动态验证（推荐）(b) 先并行试任务 12 黑盒拟合 (c) 到此为止 | **(a)，并建议 (a)+(b) 并行** | 现在已有「参数在哪、什么类型、哪个结构体偏移」的硬证据，动态验证成本被压到很低（一次 watchpoint 就能定位消费函数）；任务 12 黑盒拟合不依赖逆向，可同时跑、风险对冲。选 (c) 等于白做本任务。 |
| 2 | Ghidra 工程放哪（任务书决策 2） | (a) 本机某目录（推荐）(b) 外部盘 (c) 用完即删 | **(a) 仓库外固定目录 `%USERPROFILE%\AppData\Local\Temp\brushconverter-team\ghidra-proj`** | 天然不进 git（在仓库外），路径在临时区、可整目录删除，不占项目盘。**实测该工程已有 1,814.9 MB（10 个文件）**，页面文件可用只剩 0.55 GB——不建议在这台机器继续跑全量分析，若确需全量分析应换 ≥32 GB 内存的机器。 |
| 3 | 逆向产物能否进 git（任务书决策 3） | (a) 一律不进 (b) 只进结论 md (c) 只进脚本 | **(b)+(c)：进脚本 + 进结论 md，不进工程与反汇编原文** | 脚本（`tools/re_pipeline/*`）是团队资产、可复跑、无版权风险；结论 md 只写「地址 + 字符串名 + 结论」，不含大段伪代码。Ghidra 工程、完整反汇编、导出 TSV 一律留在仓库外。**未改 `.gitignore`**（按团队规则由 lead 决定是否加）。 |
| 4 | 是否更换分析目标（本任务临时决策） | (a) 坚持 `Photoshop.exe`（推荐）(b) 改分析某个 DLL | **(a)** | 见 2.2：同目录没有笔刷引擎 DLL，键名字符串与引用代码都在 exe 里，导入表也没有可替换的图形库。换 DLL 会直接**丢失证据源**。 |
| 5 | 分析方法：Ghidra 全量 vs `.pdata`+capstone（本任务临时决策） | (a) 硬扛 Ghidra 全量（可能数小时 + 换页卡死）(b) `.pdata`+capstone 为主、Ghidra 受限验证（推荐） | **(b)** | 本机空闲内存 5.5～6.7 GB、页面文件余量 0.55 GB，全量分析会拖慢整机并可能 OOM；而 (b) 每条证据都是「指令地址 + 反汇编文本 + 函数边界」，证据强度不低于伪代码阅读，且 11 秒可复算。代价：抓不到间接调用（已在第十节如实列为局限）。 |
| 6 | 新装 `capstone` 依赖 | (a) 不装，退回纯字节模式匹配 (b) 装（推荐） | **(b) capstone 5.0.7** | 纯模式匹配无法校验「取到的 disp32 真的是那条指令的操作数」，会产生假阳性；capstone 让 60 条预筛全部通过校验，并能输出可读反汇编作为证据。装在 conda 环境 `<LOCAL_APP_DIR>\anaconda3\envs\brushConverter`，**不改项目依赖、不进 requirements**（脚本内有 import 失败提示）。 |

---

## 十三、人工如何验收（给使用者）

1. 打开 `docs/研究报告/ps-逆向-静态.md`，看第一节结论与第三节候选表（每行都有地址/线索/引用次数）。
2. 想复核某条证据，在仓库根执行：

   ~~~
   "<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/re_pipeline/native_xref.py disasm --exe "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe" --func 0x141a939d5 --limit 40
   ~~~

   应看到 `"invertTexture" / "protectTexture" / "textureBrightness" / "textureContrast"` 四条 `lea` 注释——这就是 high 档 H1 的第一条证据。
3. 想一键复跑全部取证：`bash tools/re_pipeline/collect_evidence.sh`（约 2 分钟），结果写进 `docs/研究报告/附件/证据汇总-原始输出尾部.txt`。
4. 确认没动 PS：`ls -la "/d/program/PS2025/Adobe Photoshop 2025/Photoshop.exe"` 的日期应仍是 `Jan 30 2026`、大小 `208456176`。
---

## 十四、勘误（2026-11-18 由任务 11A 运行期验证补充）

任务 11A 用 REA 内置 Frida 对 `Photoshop.exe` 做了真实运行期插桩，证实了 H1/H3，并纠正本报告两处静态推断：

1. **H1 的函数入口是 `0x141a937e0`，不是 `0x141a939d5`**（后者是函数内部地址）。三条证据：函数体有 `add rsp,0x50 / pop rdi` 却无对应 `push`；动态返回地址 `0x1ef2218` 回代后前一条是 `0x141ef2213 call 0x141a937e0`；`callers --funcs 0x141a937e0` 找到 **4 个**直接调用者。因此本文「H1 无直接调用者」应更正为「有 4 个」。
2. H1/H3 由 `0x141ef2213` / `0x141ef2222` 背靠背调用（间隔 15 字节），「同一调用簇」由运行期返回地址钉死。
3. 运行期观测到的纹理模式号是**第三套编号**（multiply=7、subtract=39、height=40、linearHeight=41），与本报告 §六 静态注册表（`0x2A`=42 等）以及 Krita 侧 0/1/14/15 都不是同一套；转换映射以 [ps-逆向-动态.md](ps-逆向-动态.md) §3 的实测值为准。
4. 像素消费端在 11A 中仍为**未判定**（页级内存监控 0 命中），参数→像素公式的依据是任务 12 的数据拟合，见 [ps-纹理公式-参考实现.md](ps-纹理公式-参考实现.md)。
