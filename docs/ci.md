# brushConverter 持续集成（CI）使用说明与覆盖边界

> **English abstract**: Maintainer reference for the GitHub Actions CI workflow and the reproducible local CI entry: what it checks, how to run it locally, how to read reports and exit codes, and which acceptance areas it deliberately does not cover. CI is an automated regression and structural check, not a full real-machine acceptance.

> 面向维护者：CI 与本地共用同一个入口。它只证明自动检查在干净环境中的结果，不替代 Krita/Photoshop 实机渲染、真实商用素材回归与人工验收。页面中的判定口径以`tools/ci.py`的实际输出为准；一次运行的具体数字留在运行报告与交付记录中，不在本页冻结。

## 1. 目的与定位

- **尽早发现破坏性改动**：在干净检出上复跑可自动化的回归与只读结构检查，让转换、导出、文档合同与数据一致性问题在合并前暴露。
- **结果分层**：CI 只回答"自动检查是否通过"；它不回答"转换效果是否与 Photoshop 一致""Krita 导入显示是否正常""发布包是否可交付"等需要实机或人工判断的问题。
- **不虚称**：不做状态徽章，不把跳过折成通过，不引用历史验收数字充当本次结果。历史验收报告是一次性只读记录，不是持续维护的现状页。

## 2. 触发与运行环境

工作流文件：`.github/workflows/ci.yml`；工作流名 `CI`，job id `ci`，job 显示名 `CI (Windows · Python 3.12)`（GitHub 会把矩阵值渲染进去；required status check 选它，见 §8）。

**触发事件**：

- push 到 `main`；
- 任意 `pull_request`；
- `workflow_dispatch` 手动触发。

没有 `paths` 过滤：只改文档也会触发，因为文档合同检查本身就要拦文档断链与版本口径漂移。

**运行环境**：

- `windows-latest`（Windows 64 位）+ Python 3.12；矩阵只列这一条，其他平台/解释器未实测，不写入支持面。新增矩阵项前须先有本地实测证据，并同步本页。
- 权限只读：`permissions: contents: read`；checkout `persist-credentials: false`。CI 不提交、不发布、不写仓库。
- `concurrency: ci-${{ github.workflow }}-${{ github.ref }}` + `cancel-in-progress: true`：同一分支/PR 的新运行取消旧运行。
- 超时预算：`tools/ci.py` 内部限制为 checks **单项 60 秒**（7 项最坏约 7 分钟）、pytest **整套 1200 秒（20 分钟）**；工作流 `ci.py` step `timeout-minutes: 35` 作为安全网（覆盖 checks 最坏 + pytest 20 分钟 + 写报告余量，正常安装耗时下 ci.py 能在被兜底前先写完报告），job `timeout-minutes: 75`，安装 step `timeout-minutes: 20`。
- 固定 UTF-8 环境：`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`、`PYTHONUNBUFFERED=1`，避免 Windows 代码页（如 936）把中文摘要弄乱。
- 三个 action 均固定到 commit SHA（行内注版本）：`actions/checkout` v7.0.1、`actions/setup-python` v7.1.0、`actions/upload-artifact` v7.0.2；它们运行需要 node24，GitHub 托管 runner 已满足，自托管旧 runner 须先升级。

**步骤顺序**：

1. Checkout（不带凭据）。
2. 安装 Python 3.12，pip 缓存键含 `requirements-ci.txt` 与 `pyproject.toml`。
3. 安装依赖：`pip install --upgrade pip` → `pip install -r requirements-ci.txt` → `pip install -e .`。
4. 阻断 lint（ruff `E9,F63,F7,F82`）。
5. 建议 lint（全量 ruff 写 `ruff.log`，`continue-on-error`，不阻断）。
6. `python -B tools/ci.py --phase all --report-dir .tmp/ci/reports`：结构检查→回归，fail-fast，退出码语义见 §6。
7. `if: always()` 把 `summary.md` 追加到 GitHub Actions 运行页摘要（不改变 job 成败）。
8. `if: always()` 按 6 文件白名单上传 Artifact（见 §5.5）。

## 3. 本地等价运行

在仓库根目录，使用项目要求的 Python 3.12；先解析并核验解释器再运行，不要拿 PATH 上不确定来源的 `python` 直接断言：

```powershell
$PY = (py -3.12 -c 'import sys; print(sys.executable)')
if ($LASTEXITCODE -ne 0 -or -not $PY) { throw '未找到 Python 3.12 解释器，停止' }
& $PY -B -c "import sys; print(sys.executable); assert sys.version_info >= (3, 12)"
if ($LASTEXITCODE -ne 0) { throw '解释器核验失败，停止' }
```

若本机没有 `py` 启动器，把 `$PY` 直接赋为已核实的 3.12 解释器绝对路径。**推荐用仓库内独立 `.venv`**，避免向全局解释器装包污染其它项目（完整 PowerShell 安装顺序）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-ci.txt
.\.venv\Scripts\python.exe -m pip install -e .
$PY = (Resolve-Path .\.venv\Scripts\python.exe).Path
```

若已有可用的 3.12 环境可跳过创建，但仍须核验 `sys.version_info >= (3, 12)`，并与 CI 使用同一入口。CI 与本地共用一条入口：

```powershell
& $PY -B tools/ci.py                                    # 默认 all：先只读检查，再 pytest；checks 失败即停
& $PY -B tools/ci.py --phase checks                     # 只读结构检查
& $PY -B tools/ci.py --phase tests                      # 只跑 pytest -m verify
& $PY -B tools/ci.py --phase all --report-dir .tmp/ci/reports  # 自定义报告目录（--phase all 可省略）
& $PY -B tools/ci.py --phase tests --allow-krita        # 仅本机有 Krita 实机时使用；CI 不用
```

- `--phase` 取 `checks`、`tests`、`all`（默认）；`--report-dir` 默认 `.tmp/ci/reports`；`--allow-krita` 只用于本机实机，CI 不启用。
- `checks` 纯标准库、不联网、不启动 Krita/Photoshop；`tests` 需要 numpy、Pillow、PySide6。
- 本地运行请为报告目录使用本任务的工作区内目录；CI 每次运行使用全新 checkout 与独立报告目录。

## 4. 依赖

- **运行依赖**：`numpy>=2.0`、`Pillow>=10.0`、`PySide6>=6.5`；唯一事实来源是 `pyproject.toml` 的 `[project].dependencies`，CI 用 `python -m pip install -e .` 安装，不在 `requirements-ci.txt` 重复声明。
- **CI 专用依赖**（`requirements-ci.txt`）：只放 `pytest` 与 `ruff`，并精确固定版本以保证 lint/测试口径可复现（当前落地 `pytest==9.2.0`、`ruff==0.17.6`，以文件为准）。`pytest` 只在这里声明（`requirements.txt` 没有它），缺失会导致回归阶段无法启动；升级版本须先在本地 Windows 3.12 实测再改，不要只改云端。
- **版本漂移定位**：`summary.json` 的 `dependency_versions` 记录运行时解析到的 `numpy`、`Pillow`、`PySide6`、`pytest`、`ruff` 版本（未安装记 null）；运行依赖仍由 `pyproject.toml` 的 `>=` 约束，**不是全依赖锁定**，只有 CI 专用的 pytest/ruff 精确固定。
- **checks 阶段**：不需要第三方包。
- 安装顺序（工作流与本地一致）：先 `python -m pip install -r requirements-ci.txt`，再 `python -m pip install -e .`。

## 5. 检查内容与报告

### 5.1 checks（只读结构检查，全部 `--check`）

- 文档合同：`tools/check_docs.py`。
- 公开索引：`tools/render_archive_index.py --tracked-only`；验收报告 INDEX 与研究报告 INDEX 各一次。
- 矩阵渲染：`tools/render_matrix_md.py --check`。
- 矩阵数据：`tools/check_matrix.py`。
- 模式总表：`tools/render_mode_table.py --check`。
- UI 控件：`tools/check_ui_controls.py`。

这些检查只读、不联网、不改工作区；其中文档合同与公开索引两项会以只读方式调用 git（如 `ls-files`）判断已跟踪文件，因此 **CI 检出必须保留 `.git` 且 git 可用**（`actions/checkout` 默认即可）；不写仓库、不 stage/commit。`check_docs` 在无 `.git` 时有降级路径，但那不是等效 CI：CI 结论只以保留 `.git` 的运行为准，不要拿降级检查冒充 CI。失败先保留报错并按对应说明排查，不用改规则或手改生成物凑绿。

### 5.2 tests（pytest 回归）

`tools/ci.py` 的 tests 阶段实际执行 `pytest -m verify`，并固定以下口径：

- `--basetemp .tmp/ci/pytest-tmp`：固定独立目录，位于报告目录之外；由 `ci.py` 内部固定，**不接受参数或环境变量覆盖**，这样避免任意路径被 pytest `--basetemp` 递归清理。`tests/conftest.py` 会把其父目录 `.tmp/ci` 发布为 `BRUSHCONVERTER_TEST_TASK_ROOT`，GUI 隔离断言依赖这个任务根。工作流不要另传 `--basetemp`；需要并行跑回归请用独立 checkout（或只跑单测/mock），不要改这个落点。完整子进程环境隔离见 §5.3。
- `-p no:cacheprovider`：不在 checkout 内写 `.pytest_cache`。
- `-ra`：摘要列出跳过原因，便于区分"缺样本"与"外部排除"。
- `--junitxml <report-dir>/junit.xml`：机器可读结果。
- 默认排除 `tests/test_krita_render.py`（需要真实 Krita GUI 插件；CI 无实机）。这是**外部排除（external exclusion）**：不计入 passed/failed/skipped、不影响退出码，控制台与 `summary.md` 各有一行说明，`summary.json` 的 `external_exclusions` 字段列出该文件，且 `automated_suite_complete=false`；本机加 `--allow-krita` 时 `external_exclusions=[]`。排除方式是把**整个文件从收集中排除**（文件内全部用例未运行，不是逐项 skip；`external_exclusions` 记录为"整个文件未参与收集"）。原因是该文件原有 8 项用例：6 项在缺少 Krita/kritarunner 时会 skip，1 项（缺 kritarunner 的失败路径）无需 Krita 也能通过，1 项（缺测试预设的失败路径）在无 Krita 环境会失败；若整体纳入，实机缺失会被误报成回归失败。

- pytest 阶段同样要求 git 可用：`tests/test_release_version.py` 调用 `check_docs.check_contract`，其 `discover()` 走 `git ls-files`。CI 的 `actions/checkout` 默认保留 `.git`；无 `.git` 时 `check_docs` 有降级路径，不能视为与 CI 等效，相关测试也可能因环境差异失败——因此本地复现应保留仓库 `.git`。

### 5.3 测试子进程隔离（pytest env）

`tests` 阶段只给 **pytest 子进程**设置隔离环境；父进程 `os.environ`、系统设置与 GUI 代码都不改。`checks` 子进程只设 UTF-8 与禁写 `.pyc`，只读检查不需要落点。

- 隔离根 `isolation_root` = pytest `--basetemp` 的父目录 = `.tmp/ci`；basetemp 固定为 `.tmp/ci/pytest-tmp`。两者都是 `ci.py` 内部固定值，不接受外部参数/环境变量覆盖。
- 以下变量全部指向隔离根内子目录，并在运行前预建：
  - `TEMP` / `TMP` / `TMPDIR` → `.tmp/ci/temp`
  - `APPDATA` → `.tmp/ci/appdata`
  - `LOCALAPPDATA` → `.tmp/ci/localappdata`
  - `HOME` / `USERPROFILE` → `.tmp/ci/home`
  - `XDG_CONFIG_HOME` → `.tmp/ci/config`；`PYINSTALLER_CONFIG_DIR` → `.tmp/ci/config`
  - `XDG_CACHE_HOME` → `.tmp/ci/cache`
  - `XDG_DATA_HOME` → `.tmp/ci/data`
  - `XDG_STATE_HOME` → `.tmp/ci/state`
- 同时固定 `PYTHONNOUSERSITE=1`、`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`、`PYTHONDONTWRITEBYTECODE=1`（不写 `.pyc`），并设 `QT_QPA_PLATFORM=offscreen`（这是字面值 `offscreen`，不是目录映射；GUI 测试自身也设 offscreen，CI 无桌面）。
- 目的：满足既有 GUI/集成测试"落点必须在本任务隔离根下"的断言，让环境缺失表现为隔离失败而不是被测代码失败；**不放宽任何测试**。
- 边界：这是**路径隔离，不是 OS 安全 sandbox**。个别原生 API 仍可能读到真实 HOME 等位置；隔离只保证测试写入落在任务目录内，不宣称系统级隔离。
- 隔离目录与 `pytest-tmp` 都在 `.tmp/ci` 下、在报告目录 `.tmp/ci/reports` 之外，**不在上传白名单**，不会被 Artifact 带走。
- 本地复现：跑 `tools/ci.py --phase tests` 会使用同一套隔离 env；若手工直接跑 pytest，需要自行设置这些变量，或改用 `ci.py` 入口。

### 5.4 Lint（ruff，由工作流直接执行）

结构检查前还有两步 ruff；它们由工作流直接调用，**不在 `tools/ci.py` 的 checks 内**，所以只跑 `ci.py` 不等于完整复现 CI：

- **阻断项**：`python -m ruff check . --select E9,F63,F7,F82 --output-format=concise`，只拦语法错误、未定义名、坏导入这类确定性错误（工作流 step `timeout-minutes: 5`）。
- **建议项（非阻断）**：全量 `python -m ruff check .`，输出写入报告目录的 `ruff.log` 并随 Artifact 上传。当前既存的全量 lint 基线仍有历史风格问题，工作流不加入 `ignore` 掩盖，也不因既存问题把 CI 变红；具体数量随代码变化，以实际 `ruff.log` 为准（工作流 step `timeout-minutes: 10`）。

本地要完整复现 CI，除 `tools/ci.py --phase all` 外还需手动跑这两条 ruff 命令。

### 5.5 报告

报告目录（默认 `.tmp/ci/reports`）保留：

- `checks.log`、`pytest.log`：原文输出。
- `junit.xml`：pytest 机器结果。
- `summary.json`、`summary.md`：汇总，含 passed/failed/skipped 计数、`external_exclusions`、`automated_suite_complete` 与恒为 false 的 `full_verification`。
- `summary.json` 还含 `git_head`、Python 版本/解释器绝对路径、`platform`、`repo_root`、`phase`、`allow_krita`、`dependency_versions`、`notes`。这些环境信息用于复现与定位版本漂移，会随 Artifact 对可见者公开；不含凭据，但不要把它描述成"无环境信息"。

pytest 的临时目录在 `.tmp/ci/pytest-tmp`（位于报告目录之外）。每次运行前会删除旧的 `junit.xml`，避免旧报告被当成当前成功；失败报告同样保留、不自动清理。

报告目录自身保留 5 个文件；工作流另把全量 ruff 的 `ruff.log` 写入同一目录，并在 `if: always()` 下**只按 6 文件白名单上传**：`checks.log`、`pytest.log`、`junit.xml`、`summary.json`、`summary.md`、`ruff.log`；artifact 名 `ci-reports-${{ runner.os }}-py${{ matrix.python-version }}`（当前即 `ci-reports-Windows-py3.12`），保留 14 天，`if-no-files-found: warn`，`include-hidden-files: true`（`.tmp` 是隐藏目录）。不整目录上传，避免把转换夹具带进 Artifact。

此外 `summary.md` 会被追加到 Actions 运行页摘要；完整证据仍以 Artifact 与报告目录为准。

## 6. 退出码与判定口径

| 退出码 | 含义 | 判定 |
|---|---|---|
| 0 | 全部检查与测试通过 | 可合并候选；`skipped>0` 时摘要追加"（含跳过，未全验）"；只有外部排除时摘要行不追加提示，改由"外部排除"节与 `summary.json` 披露，`automated_suite_complete=false` |
| 1 | 任一检查或 pytest 失败、中断、超时 | 不通过，先修问题 |
| 2 | pytest 收集 0 条、junit 缺失，或检查脚本缺失/输入错误 | 视为异常，不是通过；先查选择器/环境与检查脚本是否存在 |

- **all 是 fail-fast**：先 checks、后 tests；checks 非 0 时 tests 不再执行（记为 skipped），整体退出码取 checks 的 1 或 2。**推荐工作流单 step 跑 `--phase all`**：结束时只写一份合并摘要，含 checks 与 tests 两段。若确实要分两个 step，必须给两个阶段用**不同报告目录**（如 `.tmp/ci/reports/checks` 与 `.tmp/ci/reports/tests`）；共用同一目录时后一阶段会覆盖前一阶段摘要（tests-only 的 `checks:null` 是如实记录，不是结构检查失败）。
- **`full_verification` 恒为 false**：本入口只覆盖自动阶段，Krita/PS 实机、真实商用素材与 GUI 人工验收永远不在此列；这不是失败标志，也不是发布批准依据。判断自动阶段是否全部完成请用 `automated_suite_complete`：仅当所有检查与测试通过、skip=0 且无外部排除时为 true。注意 CI 默认运行就带外部排除，所以**云端 CI 上该值恒为 false**；只有本机 `--allow-krita` 且无 skip、checks 与 tests 全通过时才可能为 true。
- **skip 单独计数，绝不折算通过**。缺商用 ABR 夹具时既有 skip 原样保留；报告必须列 passed/failed/skipped，不能写"全绿""全验"。
- `full_verification` 恒为 false，因此任何 CI 运行都不能单独作为发布批准依据；发布仍须人工验收与准确对象批准。

## 7. 覆盖边界（明确不覆盖）

以下项目**不在 CI 覆盖内**，仍需本机或人工完成：

- **Krita 实机渲染与笔触效果**：含 `tests/test_krita_render.py` 的实机用例（默认外部排除）。Krita 版本边界仍按[用户指南](<user-guide.md>)与[发布说明](<../releases/RELEASE_NOTES.md>)。
- **Photoshop 真值新出图、实机渲染与人工手感复核**：需要 Photoshop、数位板或 GUI 实机装置的验证不在 CI 内。注意区分：基于仓库内既有公开基准的离线像素/手感用例仍会随 tests 阶段运行；离线复算不等于 PS 实机复核，也不等于人工手感结论。
- **真实商用 ABR 素材回归**：仓库不收录商用样本（`.gitignore` 排除 `tests/**/*.abr`，仅 `tests/abr-generated/` 例外）。`tests/test_mapping.py` 是模块级 `skipif`：缺样本时整个模块在 CI 中不执行，不能据此认为映射公式链路已被 CI 回归；CI 不因跳过而宣称真实素材已回归。
- **GUI 真实窗口交互与人工验收**：导入、显示、重启、标签、逐支选项等实机结论，以及发布前人工验收边界（如 30 项通过/六项未验的历史口径），都不由 CI 代签。
- **打包与发布**：PyInstaller 构建、ZIP 产物检查、隐私门与发布流程不在 CI 内；CI 不构建发布包。
- **不自动发布**：CI 不创建 tag/Release，不推送；发布仍走冻结、机器检查、人工验收与准确对象批准。

## 8. 启用 required status checks

CI 工作流本身不会阻止合并；需要在 GitHub 仓库设置里把它的检查设为必需：

1. 仓库 **Settings → Branches**（或 **Rulesets**）→ 为 `main` 添加/编辑分支保护规则。
2. 勾选 **Require status checks to pass before merging**。
3. 先让工作流在 PR 上成功跑过至少一次，再在状态检查列表里选择 **`CI (Windows · Python 3.12)`**（job 显示名；job id 是 `ci`，矩阵值会渲染进名字）。勾选这一个 job 即可覆盖它下面的全部步骤；列表只显示触发过的工作流，没跑过时先手动 `workflow_dispatch` 一次。
4. 建议同时勾选 **Require branches to be up to date before merging**，减少过期分支绕过。
5. required check 只作用于 GitHub 上的合并门禁，不能替代本地验证、实机验证与人工验收；也不要为了凑绿跳过或弱化检查。

## 9. 维护约定

- 接口变化（`tools/ci.py` 参数/退出码、工作流文件或 job 名称、`requirements-ci.txt`）时同步更新本页。
- 本页新增链接由 `python -B tools/check_docs.py --check` 检查；不要链接内部规划、预写报告或原始证据目录。
- 本页不冻结一次运行的具体数字；把某次结果当现状引用时，必须标明运行环境与是否含跳过。
