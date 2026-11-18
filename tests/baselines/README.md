# tests/baselines —— 像素级基准图库

本目录存放「画出来像不像」的比对基准图，配合 tools/imgdiff.py（任务 03）使用。
**未在 manifest 里登记的图不算基准**，比对结果不能引用它。

## 目录分工

| 子目录 | 放谁的图 | 谁来产图 |
|---|---|---|
| ps/ | Photoshop 真值渲染结果（含任务 05 的 PS 脚本与临时工作区） | 任务 05 |
| krita/ | Krita 无头渲染结果（同参数、同画布） | 任务 06A |
| handmade/ | 手工合成的极简基准图，只用来验证比对管线本身能跑通 | 任务 03 |

## 登记规则（重要）

- 权威清单是 **顶层 tests/baselines/manifest.json**，由任务 09/15 维护合并；
  按团队分工，其他任务只写**自己子目录**的 manifest（例如 handmade/manifest.json），
  不直接改顶层文件。
- 每条记录至少包含 file / source / size / note；建议附 mode / brightness / contrast / sha256 / bytes，
  便于核对「这张图到底是哪组参数产出的」。
- 文件名必须符合 docs/基准库命名规范.md：<来源>__<笔刷或参数标识>__<模式>__<关键参数>.png。
- 单张图不超过 2MB；超了不许进 git，改为「图放本机 + manifest 记绝对路径与 sha256 + 记生成命令」。

## 比对口径

- 只比较 Alpha 通道（PS 纹理导出 PNG 的 RGB 全黑，信息在 Alpha）。
- 差异比例分母是全部像素 W×H，不是非零像素数。
- 判定阈值默认差异比例 ≤ 2% 视为通过（详见 docs/验证体系使用说明.md 第 9 节）。

## 现有 handmade 基准（两条）

| 文件 | 尺寸 | 说明 |
|---|---|---|
| handmade__渐变圆__正片叠底__b0_c0.png | 64×64 | Alpha 水平渐变（0→255） |
| handmade__实心圆__正片叠底__b0.10_c1.33.png | 64×64 | Alpha 实心圆（半径 24，1px 抗锯齿） |
