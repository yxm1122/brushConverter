# bundle_contract 合成夹具（任务 174）

这些 .bundle 是最小结构样本，**不是可绘制笔刷预设**：

- paintoppresets/*.kpp 只是占位字节，用来核对 manifest、md5、zip 与 meta 结构；
- 真实渲染与界面效果不使用这些夹具，统一等任务 187 用真实转换产物验收；
- 全部内容为合成占位值，不含个人信息、绝对路径、邮箱或 IP。

生成与校验（仓库根目录，$PY 先按通用规范核验）：

    & $PY -B tools/probe_bundle_contract.py --emit-fixtures tests/fixtures/bundle_contract
    & $PY -B tools/probe_bundle_contract.py --selftest
    & $PY -B tools/probe_bundle_contract.py --probe tests/fixtures/bundle_contract/native_meta_meta.bundle

| 文件 | 用途 | 按目标源码的预期 |
|---|---|---|
| native_meta_meta.bundle | 正例：meta 根 meta:meta + 每预设 tags | would_load=True，2 个预设各自带标签 |
| office_document_meta_root.bundle | 反例：当前 writer 的 office:document-meta 根 | meta 根被拒绝，would_load=False |
| package_tags_only.bundle | 反例：只有包级 tag，没有逐预设 tags | 包级 tag 被读入，逐预设 tags 为空 |
| unknown_fields.bundle | 未知元素/未知 meta-userdefined/JSON 值 | 可读，未知项保留并给警告 |
| manifest_wrong_namespace.bundle | 反例：manifest 命名空间 URI 错误 | manifest 被拒绝，would_load=False |
| missing_meta.bundle | 反例：缺 meta.xml | would_load=False |
| no_mimetype.bundle | 对照：缺 mimetype | 源码 reader 仍 would_load=True；本项目不变量失败 |

夹具字节由 tools/probe_bundle_contract.py 固定生成规则产生；--selftest 会核对磁盘字节与生成规则一致。
