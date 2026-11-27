# 验收报告（证据归档）

> **非上手必读**：本目录是每个任务的一次性自检/验收记录，只作历史追溯与证据复核。
> 现行映射结论看 [`../parameter-mapping.md`](../parameter-mapping.md)，数据真相看 [`../数据/`](../数据/)。

- 逐份清单见 [`INDEX.md`](INDEX.md)（脚本生成，禁止手改）。
- 在仓库根维护公开索引：`python tools/render_archive_index.py --tracked-only`；只读校验：`python tools/render_archive_index.py --tracked-only --check`。不使用默认旧模式，避免收录仅本地报告。
- 报告正文是一次性历史记录，不随现行功能或版本更新回写；目录说明与生成索引是维护入口。编号对应当时任务书，不用于推断当前验收状态。
- `归档/` 子目录是全量验收的时间戳快照（`tools/verify_all.py --archive` 生成）。