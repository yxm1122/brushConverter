# 验收报告（证据归档）

> **非上手必读**：本目录是每个任务的一次性自检/验收记录，只作历史追溯与证据复核。
> 现行映射结论看 [`../parameter-mapping.md`](../parameter-mapping.md)，数据真相看 [`../数据/`](../数据/)。

- 逐份清单见 [`INDEX.md`](INDEX.md)（脚本生成，禁止手改）。
- 重新生成：`python tools/render_archive_index.py`；校验：`python tools/render_archive_index.py --check`。
- 编号对应内部任务书（`01`–`116`，含 `06A/07A/11A/11B/18A/30A` 等拆分）；`90`–`92` 为发布验收。
- `归档/` 子目录是全量验收的时间戳快照（`tools/verify_all.py --archive` 生成）。