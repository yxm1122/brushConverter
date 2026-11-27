# -*- coding: utf-8 -*-
"""任务165：小型合成正反例；全部临时文件留在工作区，失败样本不自动删除。"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile

import pytest

from tools import check_docs as docs

WORK = Path(__file__).resolve().parents[1] / ".tmp/165/docs-fixtures"


def put(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def root(monkeypatch) -> Path:
    # 合成发布版本独立于当前产品版本；历史版本正反例无需随补丁发版改写。
    monkeypatch.setattr(docs, "RELEASED", "v1.6.0")
    monkeypatch.setattr(docs, "RELEASE_DATE", "2026-11-27")
    # 不使用系统临时目录，也不修改全局pytest配置。
    WORK.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="case-", dir=WORK))
    put(root, "README.md", "# brushConverter\nA brush conversion tool for Krita.\n"
        "已发布 v1.6.0 · [下载](https://example.invalid/releases/tag/v1.6.0)\n"
        "## 已知限制（摘要）\n见用户指南。\n## 升级必读\n请重转。\n")
    put(root, "docs/user-guide.md", "# 用户指南\nAn introduction to brush conversion.\n" +
        "\n".join(f'<a id="{key}"></a>' for key in docs.STABLE_ANCHORS["docs/user-guide.md"]))
    put(root, "docs/troubleshooting.md", "# 排错\n## 第一项\n先排除原始ABR直读，不要混淆。\n")
    put(root, "docs/developer-guide.md", "# 开发参考\n## 0. 文档索引（先看这里）\n"
        '<a id="maintenance-checks"></a>\n')
    put(root, "docs/parameter-mapping.md", "# 参数参考\n" +
        "\n".join(f"## {i}. 参考章节" for i in range(1, 11)) + "\n" +
        "\n".join(f'<a id="{key}"></a>' for key in docs.STABLE_ANCHORS["docs/parameter-mapping.md"]))
    put(root, "releases/RELEASE_NOTES.md", "# brushConverter v1.6.0（2026-11-27）\n"
        "This release was published on 2026-11-27.\n" +
        "\n".join(f"## {title}\n最终数据待回填。" for title in
                    ("主要变化", "升级注意", "已知限制", "验证", "回退方式")) +
        "\n# brushConverter v1.5.0（2026-11-23）\n旧发布段冻结，不必强行套新模板。\n")
    old, new = docs.COMPATIBILITY[0]
    put(root, new, "# 旧标题\n## 原章节\n旧事实只作历史。\n")
    put(root, old, "# 旧标题\n## 原章节\n[归档原文](<归档/" + Path(new).name + ">)\n")
    return root


@pytest.fixture
def candidate(root, monkeypatch):
    """候选门的虚构版本夹具，不代表项目已立项下一版本。"""
    monkeypatch.setattr(docs, "CANDIDATE", "v9.9.0")
    path = root / "releases/RELEASE_NOTES.md"
    text = path.read_text(encoding="utf-8").replace(
        "# brushConverter v1.6.0（2026-11-27）",
        "# brushConverter v9.9.0（候选 · 待发布，未发布）", 1).replace(
        "This release was published on 2026-11-27.", "This candidate is not released.", 1)
    path.write_text(text, encoding="utf-8")
    return root


def snapshot(root: Path):
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def rules(root: Path, path: Path) -> set[str]:
    return {issue.rule for issue in docs.check_paths(root, [path])}


def test_positive_contract_cli_is_read_only(root, capsys):
    before = snapshot(root)
    assert docs.main(["--check", "--root", str(root)]) == 0
    assert snapshot(root) == before
    output = capsys.readouterr().out
    assert "0项失败" in output
    assert "不是Krita/Markdown官方标准" in output
    assert "外链未联网" in output


def test_relative_root_reference_html_and_unicode_links(root):
    put(root, "docs/中文 空白 (一).md", "# 中文 标题\n重复标题\n---\n")
    path = put(root, "docs/入口.md", "# 入口\n"
        "[文件相对](<中文 空白 (一).md#中文-标题>)\n"
        "[根相对](</docs/%E4%B8%AD%E6%96%87%20%E7%A9%BA%E7%99%BD%20(%E4%B8%80).md#中文-标题>)\n"
        "[带标题](<中文 空白 (一).md> \"说明\")\n"
        "[名称含[中括号]](<中文 空白 (一).md#重复标题>)\n"
        "[引用][目标]\n[目标]: <中文 空白 (一).md#中文-标题> \"引用标题\"\n"
        "[目标]\n[目标][]\n"
        '<a href="中文 空白 (一).md#中文-标题">HTML链接</a>\n'
        '<img src="图片 空白.png" />\n')
    put(root, "docs/图片 空白.png", "合成附件，不是需要解码的真图片")
    assert docs.check_paths(root, [path]) == []


def test_parentheses_escaped_and_image_links(root):
    put(root, "docs/名字(一).md", "# 好\n")
    path = put(root, "docs/入口.md", "[括号](名字(一).md#好)\n"
        r"[转义](名字\(一\).md#好)" + "\n![图片](名字(一).md)\n")
    assert docs.check_paths(root, [path]) == []


def test_current_links_follow_file_relative_resolution(root):
    put(root, "同名.md", "# 根目录\n")
    put(root, "docs/同名.md", "# 文件目录\n")
    path = put(root, "docs/入口.md", "[文件相对](同名.md#文件目录)\n"
        "[明确文件相对](./同名.md#文件目录)\n[父目录](../同名.md#根目录)\n")
    assert docs.check_paths(root, [path]) == []
    path.write_text("[错误锚点](同名.md#根目录)\n", encoding="utf-8")
    assert rules(root, path) == {"D_ANCHOR_MISSING"}


def test_duplicate_heading_suffix_and_literal_collision(root):
    path = put(root, "docs/重名.md", "# 名称\n# 名称\n# 名称-1\n# 名称\n"
        "[一](#名称) [二](#名称-1) [字面碰撞](#名称-1-1) [三](#名称-2)\n")
    assert docs.check_paths(root, [path]) == []
    assert set(docs.read_document(path).anchors) == {"名称", "名称-1", "名称-1-1", "名称-2"}


@pytest.mark.parametrize("text", ['<a id="同名"></a>\n<a name="同名"></a>\n',
                                    '# 同名\n<a id="同名"></a>\n'])
def test_explicit_duplicate_anchor_reports_location(root, text):
    path = put(root, "docs/重复.md", text)
    issues = docs.check_paths(root, [path])
    assert issues[0].file == "docs/重复.md"
    assert issues[0].line == 2
    assert issues[0].rule == "D_ANCHOR_DUP"


@pytest.mark.parametrize("link, expected", [
    ("[坏链接](不存在.md)", "D_LINK_MISSING"),
    ("[坏章节](#不存在)", "D_ANCHOR_MISSING"),
    ("[未定义][空引用]", "D_REFERENCE"),
    ("[坏行号](代码.py#L0)", "D_LINE_RANGE"),
    ("[超界](代码.py#L1-L4)", "D_LINE_RANGE"),
    ("[倒序](代码.py#L2-L1)", "D_LINE_RANGE"),
    ("[越界](../../外部.md)", "D_PATH_ESCAPE"),
    ("[危险](javascript:alert(1))", "D_LINK"),
])
def test_negative_links_are_actionable(root, link, expected):
    put(root, "docs/代码.py", "第一行\n第二行\n")
    path = put(root, "docs/坏链接.md", "# 标题\n" + link + "\n")
    issues = docs.check_paths(root, [path])
    assert any(i.file == "docs/坏链接.md" and i.line == 2 and i.rule == expected and i.reason for i in issues)


def test_good_line_range_and_encoded_fragment(root):
    put(root, "docs/代码.py", "第一行\n第二行\n")
    path = put(root, "docs/入口.md", "# 中文\n[行号](代码.py#L1-L2)\n[编码](#%E4%B8%AD%E6%96%87)\n")
    assert docs.check_paths(root, [path]) == []


def test_code_comments_and_bare_paths_are_not_links(root):
    tick = chr(96)
    sample = "# 示例\n不存在.md、输入/我的笔刷.abr、[Spcn]、[A-Za-z0-9_]只是文本。\n"
    sample += tick + "[伪链接](不存在.md)" + tick + "\n"
    sample += tick * 2 + "[伪链接](不存在.md) " + tick + "嵌套" + tick * 2 + "\n"
    sample += tick * 3 + "powershell\n[伪链接](不存在.md)\n# 伪标题\n" + tick * 3 + "\n"
    sample += "~~~python\n[伪链接](不存在.md)\n~~~\n"
    sample += "    [缩进示例](不存在.md)\n"
    sample += '<!-- [注释](不存在.md)\n<a id="示例"></a> -->\n'
    sample += r"\[转义文字](不存在.md)" + "\n"
    path = put(root, "docs/静态误报.md", sample)
    assert docs.check_paths(root, [path]) == []
    assert docs.read_document(path).anchors == {"示例": 1}


@pytest.mark.parametrize("target", ["docs/工作流程/计划.md", "docs/优化提示词/归档/旧快照.md",
                                   "../docs/工作流程/尚不存在.md", ".tmp/原始日志.md",
                                   "docs/验收报告/187-报告.md"])
def test_public_to_private_fails_even_when_target_missing(root, target):
    path = put(root, "docs/公开入口.md", f"# 公开\n[内部](<{target}>)\n")
    assert rules(root, path) == {"D_PRIVATE"}


def test_internal_snapshots_and_evidence_are_not_live_instructions(root):
    for name in ("docs/工作流程/归档/旧任务.md", "docs/优化提示词/归档/旧规范.md",
                 "docs/验收报告/187-报告.md", "docs/研究报告/旧实验.md", "docs/归档/旧提案.md"):
        put(root, name, "# 旧快照\n[旧示例](不存在.md)\n正式版 v1.6.0\n")
    # 公开归档的正文命令示例应排除，但真正链接仍须检查。
    tick = chr(96) * 3
    put(root, "docs/归档/旧提案.md", "# 旧快照\n" + tick + "text\n[旧示例](不存在.md)\n" + tick + "\n")
    assert docs.main(["--check", "--root", str(root)]) == 0
    put(root, "docs/验收报告/INDEX.md", "# 索引\n[坏链接](不存在.md)\n")
    assert docs.main(["--check", "--root", str(root)]) == 1


def test_wrong_released_version_is_rejected(root, capsys):
    path = root / "README.md"
    path.write_text(path.read_text(encoding="utf-8").replace("已发布 v1.6.0", "已发布 v1.5.0"), encoding="utf-8")
    before = snapshot(root)
    assert docs.main(["--check", "--root", str(root)]) == 1
    assert snapshot(root) == before
    output = capsys.readouterr().out
    assert "README.md:1 [D_VERSION]" in output
    assert "[D_CANDIDATE]" not in output


@pytest.mark.parametrize("addition", ["发布日期：2026-12-01", "tag: v9.9.0",
                                      "[候选下载](https://example.invalid/releases/tag/v9.9.0)",
                                      "[候选资产](https://example.invalid/releases/download/v9.9.0/a.zip)"])
def test_candidate_must_not_pretend_published(root, candidate, addition):
    path = root / "releases/RELEASE_NOTES.md"
    text = path.read_text(encoding="utf-8").replace("## 主要变化", addition + "\n## 主要变化", 1)
    path.write_text(text, encoding="utf-8")
    assert "D_CANDIDATE" in {i.rule for i in docs.check_contract(root)}


def test_required_section_must_exist_and_not_be_empty(root):
    path = root / "releases/RELEASE_NOTES.md"
    text = path.read_text(encoding="utf-8").replace("## 验证\n最终数据待回填。", "## 验证\n")
    path.write_text(text, encoding="utf-8")
    assert any(i.rule == "D_SECTION" and "内容为空" in i.reason for i in docs.check_contract(root))
    path.write_text(text.replace("## 验证", "## 其它"), encoding="utf-8")
    assert any(i.rule == "D_SECTION" and "缺少必需章节：验证" in i.reason for i in docs.check_contract(root))


def test_abstract_missing_and_old_sections_are_frozen(root):
    path = root / "releases/RELEASE_NOTES.md"
    text = path.read_text(encoding="utf-8").replace("This release was published on 2026-11-27.", "版本简介待补。")
    path.write_text(text, encoding="utf-8")
    assert any(i.rule == "D_ABSTRACT" and i.file == "releases/RELEASE_NOTES.md" for i in docs.check_contract(root))


def test_old_path_and_anchors_remain_a_contract(root):
    old, new = docs.COMPATIBILITY[0]
    put(root, old, "# 旧标题\n[原文](<归档/" + Path(new).name + ">)\n")
    assert any(i.rule == "D_COMPAT" and "#原章节" in i.reason for i in docs.check_contract(root))


def test_required_mapping_section_and_stable_anchor(root):
    path = root / "docs/parameter-mapping.md"
    text = path.read_text(encoding="utf-8").replace("## 10. 参考章节", "## 删掉的章节").replace('id="residual-current"', 'id="临时名字"')
    path.write_text(text, encoding="utf-8")
    failed = {i.rule for i in docs.check_contract(root)}
    assert {"D_COMPAT", "D_SECTION"} <= failed


def test_non_utf8_input_returns_two_without_crashing(root, capsys):
    (root / "docs/坏编码.md").write_bytes(bytes([255, 254, 253]))
    assert docs.main(["--check", "--root", str(root)]) == 2
    assert "[D_INPUT]" in capsys.readouterr().out


def test_missing_root_and_required_page_are_not_success(root, capsys):
    assert docs.main(["--check", "--root", str(root / "不存在")]) == 2
    assert "[D_INPUT]" in capsys.readouterr().err
    (root / "docs/user-guide.md").unlink()
    assert docs.main(["--check", "--root", str(root)]) == 1
    assert "[D_REQUIRED]" in capsys.readouterr().out


def test_external_links_never_connect(root, monkeypatch):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError("检查外链不得联网")
    monkeypatch.setattr(socket, "socket", forbidden)
    path = put(root, "docs/外链.md", "[外链](https://example.invalid/no-such-file)\n"
        "[邮件](mailto:public@example.invalid)\n[协议相对](//example.invalid/page)\n")
    assert docs.check_paths(root, [path]) == []


def test_tracked_public_history_still_checks_real_links(root, monkeypatch):
    path = put(root, "docs/研究报告/已公开.md", "# 历史\n[真实断链](不存在.md)\n")
    monkeypatch.setattr(docs, "_tracked_reports", lambda repo: {"docs/研究报告/已公开.md"}, raising=False)
    assert docs.main(["--check", "--root", str(root)]) == 1
    assert path in docs.discover(root)


def test_candidate_inline_tag_is_a_fact_not_a_command_example(root, candidate):
    path = root / "releases/RELEASE_NOTES.md"
    tick = chr(96)
    text = path.read_text(encoding="utf-8").replace("## 主要变化", f"tag: {tick}v9.9.0{tick}\n## 主要变化", 1)
    path.write_text(text, encoding="utf-8")
    assert any(i.rule == "D_CANDIDATE" for i in docs.check_contract(root))


def test_compatibility_requires_actual_approved_destination(root):
    old, new = docs.COMPATIBILITY[0]
    put(root, "伪目录/归档/" + Path(new).name, "# 旧标题\n## 原章节\n")
    put(root, old, "# 旧标题\n## 原章节\n[错误原文](<../伪目录/归档/" + Path(new).name + ">)\n")
    assert any(i.rule == "D_COMPAT" and "未链接" in i.reason for i in docs.check_contract(root))


def test_git_inventory_failure_is_not_silent_success(root, monkeypatch, capsys):
    (root / ".git").mkdir()
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("合成Git缺失")
    monkeypatch.setattr(docs.subprocess, "run", unavailable)
    assert docs.main(["--check", "--root", str(root)]) == 2
    assert "D_INPUT" in capsys.readouterr().err


def test_file_url_and_case_variant_do_not_bypass_private_rules(root):
    # 运行时拼接合成file协议，避免把真实协议样本写进待公开的测试源码。
    file_url = "file:" + "//host/private.md"
    path = put(root, "docs/公开入口.md", f"[文件]({file_url})\n[内部](DOCS/工作流程/规划.md)\n")
    assert rules(root, path) == {"D_LINK", "D_PRIVATE"}


def test_xml_sensor_id_is_not_a_document_anchor(root):
    path = put(root, "docs/研究报告/格式示例.md", '# 示例\n<params><Sensor id="pressure" .../></params>\n'
        '<params><Sensor id="pressure" .../></params>\n<a id="真实入口"></a>\n[跳转](#真实入口)\n')
    assert docs.check_paths(root, [path]) == []


def test_approved_static_register_notation_does_not_hide_file_links(root):
    path = put(root, "docs/研究报告/静态-合成.md", "# 静态报告\n[rcx+0x04](Spcn)\n"
        "[rcx+0x08](真实坏链接.md)\n")
    issues = docs.check_paths(root, [path])
    assert not any(i.line == 2 for i in issues)
    assert any(i.rule == "D_LINK_MISSING" and i.line == 3 for i in issues)
    assert not any(i.rule == "D_LINK_MISSING" and i.line == 2 for i in issues)


@pytest.mark.parametrize("target, rule", [("docs/工作流程/内部.png", "D_PRIVATE"),
                                           ("坏图片.png", "D_LINK_MISSING")])
def test_nested_image_link_cannot_hide_inner_target(root, target, rule):
    path = put(root, "docs/公开入口.md", f"[![图片](<{target}>)](https://example.invalid/)\n")
    assert rule in rules(root, path)


def test_required_section_can_contain_subheadings(root):
    path = root / "releases/RELEASE_NOTES.md"
    text = path.read_text(encoding="utf-8").replace("## 验证\n最终数据待回填。", "## 验证\n### 自动检查\n最终数据待回填。")
    path.write_text(text, encoding="utf-8")
    assert not any(i.rule == "D_SECTION" for i in docs.check_contract(root))


def test_english_published_claim_cannot_contradict_chinese(root, candidate):
    path = root / "README.md"
    text = path.read_text(encoding="utf-8").replace("A brush conversion tool for Krita.", "Published version: v9.9.0.")
    path.write_text(text, encoding="utf-8")
    assert any(i.rule == "D_CANDIDATE" for i in docs.check_contract(root))


def test_comment_opener_in_fence_cannot_hide_later_links(root):
    tick = chr(96) * 3
    path = put(root, "docs/注释示例.md", tick + "html\n<!--\n" + tick + "\n[坏](不存在.md)\n")
    assert any(i.rule == "D_LINK_MISSING" and i.line == 4 for i in docs.check_paths(root, [path]))


def test_published_inline_version_and_code_only_section_are_valid(root):
    tick = chr(96)
    path = root / "README.md"
    path.write_text(path.read_text(encoding="utf-8").replace("已发布 v1.6.0", f"已发布 {tick}v1.6.0{tick}"), encoding="utf-8")
    path = root / "releases/RELEASE_NOTES.md"
    text = path.read_text(encoding="utf-8").replace("## 回退方式\n最终数据待回填。", f"## 回退方式\n{tick}git checkout v1.5.0{tick}")
    path.write_text(text, encoding="utf-8")
    assert docs.check_contract(root) == []


def test_non_template_current_page_still_obeys_candidate_red_lines(root, candidate):
    put(root, "docs/其它现行指南.md", "# 指南\n已发布 v9.9.0\n"
        "[候选下载](https://example.invalid/releases/tag/v9.9.0)\n")
    issues = docs.check_contract(root)
    assert sum(i.file == "docs/其它现行指南.md" and i.rule == "D_CANDIDATE" for i in issues) == 2


@pytest.mark.parametrize("name, link", [
    ("docs/普通指南.md", "[rcx+0x04](Spcn)"),
    ("docs/研究报告/静态-合成.md", "[普通参数](Spcn)"),
    ("docs/研究报告/静态-合成.md", "[rcx+0xGG](Spcn)"),
    ("docs/研究报告/静态-合成.md", "[rcx+0x04](目录/坏文件.md)"),
])
def test_static_notation_rule_does_not_exempt_other_links(root, name, link):
    path = put(root, name, "# 真实链接仍检查\n" + link + "\n")
    assert "D_LINK_MISSING" in rules(root, path)


def test_released_notes_allow_release_date_tag_and_download(root):
    assert docs.CANDIDATE is None
    path = root / "releases/RELEASE_NOTES.md"
    text = path.read_text(encoding="utf-8").replace("## 主要变化",
        "发布日期：2026-11-27\ntag: v1.6.0\n"
        "[正式下载](https://example.invalid/releases/tag/v1.6.0)\n## 主要变化", 1)
    path.write_text(text, encoding="utf-8")
    assert docs.check_contract(root) == []


@pytest.mark.parametrize("claim", ["v1.6.0 未发布", "v1.6.0（候选 · 待发布，未发布）",
                                    "unreleased v1.6.0", "v1.6.0 is an unpublished candidate."])
def test_released_version_cannot_keep_unreleased_status(root, claim):
    put(root, "docs/其它现行指南.md", "# 指南\n" + claim + "\n")
    assert any(i.rule == "D_RELEASE_STATUS" for i in docs.check_contract(root))


@pytest.mark.parametrize("explanation", [
    "构建状态unreleased不表示 v1.6.0 尚未发布。",
    "It does not mean v1.6.0 is unreleased.",
])
def test_build_marker_explanation_is_not_a_stale_release_claim(root, explanation):
    put(root, "docs/构建状态.md", "# 构建状态\n" + explanation + "\n")
    assert docs.check_contract(root) == []


def test_current_link_does_not_fall_back_to_repository_root(root):
    put(root, "tools/privacy_scan.py", "# 合成工具\n")
    path = put(root, "docs/入口.md", "[误写](tools/privacy_scan.py)\n")
    assert rules(root, path) == {"D_LINK_MISSING"}
    path.write_text("[正确](../tools/privacy_scan.py)\n", encoding="utf-8")
    assert docs.check_paths(root, [path]) == []


def test_legacy_research_root_resolution_is_read_only(root):
    put(root, "tools/old.py", "# 合成工具\n")
    path = put(root, "docs/研究报告/历史.md", "# 历史\n[旧根路径](tools/old.py)\n")
    before = snapshot(root)
    assert docs.check_paths(root, [path]) == []
    assert snapshot(root) == before


def test_one_shot_acceptance_reports_are_not_maintained_but_targets_are_checked(root, monkeypatch):
    path = put(root, "docs/验收报告/已公开.md", "# 当时结论\n[旧断链](不存在.md)\nv1.6.0 未发布\n")
    monkeypatch.setattr(docs, "_tracked_reports", lambda repo: {"docs/验收报告/已公开.md"})
    before = snapshot(root)
    assert path not in docs.discover(root)
    assert docs.main(["--check", "--root", str(root)]) == 0
    assert snapshot(root) == before
    live = put(root, "docs/现行.md", "[原证据](验收报告/已公开.md#当时结论)\n")
    assert docs.check_paths(root, [live]) == []
    live.write_text("[坏章节](验收报告/已公开.md#不存在)\n", encoding="utf-8")
    assert rules(root, live) == {"D_ANCHOR_MISSING"}


@pytest.mark.parametrize("prefix", ["", "\n<!-- published documentation -->\n"])
def test_release_status_check_handles_heading_after_comments(root, prefix):
    path = root / "releases/RELEASE_NOTES.md"
    text = path.read_text(encoding="utf-8").replace(
        "This release was published on 2026-11-27.", "This is unreleased v1.6.0.", 1)
    path.write_text(prefix + text, encoding="utf-8")
    assert any(i.rule == "D_RELEASE_STATUS" for i in docs.check_contract(root))


def test_candidate_unpublished_description_is_not_a_release_claim(root, candidate):
    path = root / "releases/RELEASE_NOTES.md"
    text = path.read_text(encoding="utf-8").replace(
        "This candidate is not released.", "Unpublished version: v9.9.0.", 1)
    path.write_text(text, encoding="utf-8")
    assert docs.check_contract(root) == []


def test_old_release_status_is_not_rewritten_by_current_contract(root):
    path = root / "releases/RELEASE_NOTES.md"
    text = path.read_text(encoding="utf-8") + "\n## 当时计划\nv1.6.0 未发布\n"
    path.write_text(text, encoding="utf-8")
    assert docs.check_contract(root) == []
