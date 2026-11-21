# tools/_archive（归档脚手架）

> **非产品代码**：这里存放已完成使命的一次性探索脚本与产物，只作历史追溯与证据复核，
> 不参与转换、不被 `tests/` 或 durable 工具 import。

## krita_probe/

Krita 无头渲染路线（kritarunner）的可行性探针（`probe2`…`probe15`、`probe_env`、`pressure_probe`、
`route_a_probe` 等）及其输出产物（`.log` / `.png` / `.result.json`）。结论：kritarunner 无头落笔不可用，
改走真实 GUI + 启动插件（路线 A）。这些探针是该结论的**失败证据**，故归档保留而非删除。

- 可行性论证与原始复现命令见 `docs/研究报告/krita-runner-可行性.md`（历史报告，其中
  `cd tools/krita_probe` 记录的是归档前路径；探针现位于本目录）。
- `route_a_probe.py` 按相对路径引用同目录的 `krita_auto_plugin/`，整目录归档后仍可运行。
- durable 工具 `tools/krita_render.py` 的注释引用已更新为本归档路径。