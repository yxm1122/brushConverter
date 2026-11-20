# -*- coding: utf-8 -*-
"""Krita 资源库只读诊断（不启动 Krita、不写用户 profile）。

用途：排查「bundle 文件明明在、Krita 里却笔刷不可见 / 纹理静默丢失」这类零报错问题。
所有结论都来自 resourcecache.sqlite 的只读副本与 bundle 文件本身，不做任何写操作。

本工具重点提示两类「把原始 .abr 直接导入 Krita」造成的伪缺陷：
  - storages 里 storage_type=4（Adobe Brush Library）的 ABR 直读库：Krita 5.3 会直读 .abr，
    生成一套没有经过本工具转换的同款笔刷（无纹理映射、参数不同），与 .bundle 产物混在同一
    列表里，看起来就像「纹理全丢 / 笔刷不可见」；
  - 跨 storage 同名预设：同名会互相遮蔽/混淆，实际生效的未必是转换产物那份。

典型用法：
    python tools/diagnose_profile.py --krita-dir "%APPDATA%/krita"
    python tools/diagnose_profile.py --krita-dir "%APPDATA%/krita" --bundle <某个.bundle>
    python tools/diagnose_profile.py --krita-dir "%APPDATA%/krita" --bundle <某个.bundle> --predict

退出码：
    0 = 一切正常
    1 = 发现问题（cache 损坏 / ABR 直读库 / 跨 storage 同名预设 / 悬空 storage /
        bundle 缺失或不完整 / bundle 未注册 / 纹理解析失败）
    2 = 参数或读取错误（目录不存在、sqlite 打不开等）
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import zipfile
import zlib
import xml.etree.ElementTree as ET

STORAGE_TYPES = {
    1: "Unknown", 2: "Folder", 3: "Bundle", 4: "Adobe Brush Library",
    5: "Adobe Style Library", 6: "Memory", 7: "Font Storage",
}
MANIFEST_NS = "{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}"


def expand_path(value):
    """展开 %VAR% 与 ~；代码里不写死任何本机目录。"""
    if not value:
        return value
    return os.path.abspath(os.path.expanduser(os.path.expandvars(value)))


def open_db_readonly(krita_dir, tmp_root):
    """把 resourcecache.sqlite 复制到临时目录后以只读方式打开，避免锁库与误写。"""
    src = os.path.join(krita_dir, "resourcecache.sqlite")
    if not os.path.isfile(src):
        raise FileNotFoundError("找不到 resourcecache.sqlite：%s" % src)
    tmpdir = tempfile.mkdtemp(prefix="bcdiag_", dir=tmp_root)
    dst = os.path.join(tmpdir, "resourcecache.sqlite")
    shutil.copyfile(src, dst)
    con = sqlite3.connect("file:%s?mode=ro" % dst.replace("\\", "/"), uri=True)
    return con, dst


def png_chunks(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("不是 PNG 文件（.kpp 必须是 PNG 容器）")
    pos, total = 8, len(data)
    while pos + 8 <= total:
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        yield ctype, payload


def get_preset_xml(data):
    for ctype, payload in png_chunks(data):
        if ctype != b"iTXt":
            continue
        keyword, rest = payload.split(b"\x00", 1)
        if keyword != b"preset":
            continue
        flag = rest[0]
        rest = rest[2:]
        _language, rest = rest.split(b"\x00", 1)
        _translated, text = rest.split(b"\x00", 1)
        if flag == 1:
            text = zlib.decompress(text)
        return text.decode("utf-8")
    raise ValueError("没有 iTXt(preset) 块")


def resolve_storage_path(krita_dir, location):
    if not location:
        return krita_dir
    if os.path.isabs(location):
        return location
    return os.path.join(krita_dir, location)


def section(title):
    print("")
    print("=" * 78)
    print(title)
    print("=" * 78)


def check_integrity(con, issues):
    section("[1/8] resourcecache.sqlite 完整性（PRAGMA integrity_check）")
    try:
        rows = [str(r[0]) for r in con.execute("PRAGMA integrity_check")]
    except Exception as exc:
        print("  无法执行 integrity_check：%r" % (exc,))
        issues.append("integrity_check 无法执行")
        return
    bad = [r for r in rows if r.strip().lower() != "ok"]
    if not bad:
        print("  结果：ok（数据库结构一致）")
        return
    print("  结果：CORRUPT（数据库损坏）")
    for row in bad[:8]:
        print("    - " + row)
    issues.append("resourcecache.sqlite 完整性检查失败")
    print("  影响：资源记录可能时有时无、storage 记录丢失、同名资源互相遮蔽，")
    print("        表现为「资源库看着正常但笔刷不可见 / 纹理静默丢失」且无报错。")
    print("  修复：退出 Krita → 备份 resourcecache.sqlite → 删除它让 Krita 重建 → 重启后重新导入。")


def list_storages(con):
    section("[2/8] storages 概览")
    rows = list(con.execute("select id, storage_type_id, location, active from storages order by id"))
    if not rows:
        print("  （空：DB 里没有任何 storage 记录）")
        return
    for sid, stype, location, active in rows:
        name = STORAGE_TYPES.get(stype, "type%s" % stype)
        print("  id=%-4s 类型=%-20s active=%s  位置=%s" % (
            sid, name, active, location or "(资源目录根)"))


DEFAULT_DUP_GROUP_LIMIT = 20


def storage_label(storage_type_id):
    """storage 类型标签；未知类型原样显示 typeN。"""
    return STORAGE_TYPES.get(storage_type_id, "type%s" % storage_type_id)


def check_adobe_libraries(con, issues):
    """ABR 直读库（storage_type_id=4，Adobe Brush Library）检测。

    Krita 5.3 会把 .abr 当 Adobe Brush Library 直接读，生成一套**没有经过本工具转换**的同款
    笔刷（无纹理映射、参数不同）。它和 .bundle 里的转换产物混在同一列表里，看起来就像
    「纹理全丢 / 笔刷不可见」。实测移除该 .abr 后转换产物的纹理即恢复。
    """
    section("[3/8] ABR 直读库检测（storage 类型 = Adobe Brush Library）")
    rows = [tuple(row) for row in con.execute(
        "select id, storage_type_id, location, active from storages "
        "where storage_type_id = 4 order by id")]
    print("  Adobe Brush Library（ABR 直读库）记录：%d 个" % len(rows))
    if not rows:
        print("  未发现 ABR 直读库：Krita 里没有直接读入的 .abr。")
        print("  （正常姿态：ABR 只经本工具转换成 .bundle，不要把原始 .abr 放进 Krita 资源目录）")
        return
    print("")
    print("  ⚠ 检测到 %d 个 ABR 直读库，可能与转换产物冲突（建议移除后重启 Krita）" % len(rows))
    for sid, _stype, location, active in rows:
        counts = [tuple(x) for x in con.execute(
            "select "
            "sum(case when resource_type_id = 7 then 1 else 0 end), "
            "sum(case when resource_type_id = 9 then 1 else 0 end), "
            "count(*) from resources where storage_id = ?", (sid,))][0]
        print("    id=%-4s active=%s  位置=%s" % (sid, active, location or "(空)"))
        print("           该库资源：预设(paintoppresets)=%s / 图案(patterns)=%s / 全部=%s"
              % (counts[0] or 0, counts[1] or 0, counts[2] or 0))
    print("")
    print("  影响：Krita 5.3 把 ABR 当 Adobe Brush Library 直读，生成的是一套**没有经过本工具")
    print("        转换**的同款笔刷（没有纹理映射、参数与本工具产物不同）。它和 .bundle 里的转换")
    print("        产物混在同一列表里，看起来就像「纹理全丢 / 笔刷不可见」——本缺陷实测有效的成因。")
    print("  处理：① Krita 资源管理器里移除这些 ABR 库；或 ② 退出 Krita 后把 %APPDATA%/krita 下")
    print("        的 .abr 文件移到备份目录，再重启 Krita。移除后转换产物的纹理即恢复。")
    issues.append("检测到 %d 个 ABR 直读库（Adobe Brush Library），可能与转换产物冲突" % len(rows))


def _preset_sources(con, name):
    """某个预设名在各 storage 里的来源（storage id / 类型 / 位置）。"""
    return [tuple(row) for row in con.execute(
        "select r.storage_id, s.storage_type_id, s.location from resources r "
        "join storages s on s.id = r.storage_id "
        "where r.resource_type_id = 7 and r.name = ? group by r.storage_id "
        "order by r.storage_id", (name,))]


def format_sources(sources):
    parts = []
    for sid, stype, location in sources:
        parts.append("[id=%s %s %s]" % (sid, storage_label(stype), location or "(资源目录根)"))
    return " ".join(parts)


def check_duplicate_presets(con, issues):
    """跨 storage 同名预设检测。

    同名会互相遮蔽：Krita 只显示一份，实际取哪一份取决于扫描顺序与 cache 状态；转换产物被
    另一个库的同名项遮蔽时，看起来就是「笔刷在、纹理却没了」。
    「资源目录根(Folder, type=2)」与 Krita 自带 bundle 同名属于常规镜像，单独归为提示，不算问题。
    """
    section("[4/8] 跨 storage 同名预设（可能互相遮蔽 / 混淆）")
    rows = [tuple(row) for row in con.execute(
        "select r.name, count(distinct r.storage_id) from resources r "
        "where r.resource_type_id = 7 and r.name is not null and r.name != '' "
        "group by r.name having count(distinct r.storage_id) > 1 "
        "order by 2 desc, 1")]
    print("  跨 storage 同名的预设（paintoppresets）：%d 组" % len(rows))
    if not rows:
        print("  未发现同名预设。")
        return
    conflicts = []
    benign = []
    for name, _count in rows:
        sources = _preset_sources(con, name)
        non_folder = [item for item in sources if item[1] != 2]
        has_adobe = any(item[1] == 4 for item in sources)
        if has_adobe or len(non_folder) >= 2:
            conflicts.append((name, sources))
        else:
            benign.append((name, sources))
    if conflicts:
        print("")
        print("  ⚠ 同名预设可能互相遮蔽/混淆（需要处理的 %d 组）" % len(conflicts))
        for name, sources in conflicts[:DEFAULT_DUP_GROUP_LIMIT]:
            print("    %s" % name)
            print("        来源：%s" % format_sources(sources))
        if len(conflicts) > DEFAULT_DUP_GROUP_LIMIT:
            print("    …… 其余 %d 组省略" % (len(conflicts) - DEFAULT_DUP_GROUP_LIMIT))
        print("")
        print("  影响：同名时 Krita 只显示一份，实际生效的未必是转换产物那份；被 ABR 直读库同名")
        print("        项遮蔽时就表现为「纹理全丢」。")
        print("  处理：移除或停用其中一个来源（多半是 ABR 直读库），或给其中一份改名。")
        issues.append("发现 %d 组跨 storage 同名预设（可能互相遮蔽/混淆）" % len(conflicts))
    if benign:
        print("")
        print("  提示：另有 %d 组同名只发生在「资源目录根(Folder)」与 Krita 自带库之间，" % len(benign))
        print("        属于 Krita 常规镜像，通常无害（最多列 5 组）：")
        for name, sources in benign[:5]:
            print("    %s → %s" % (name, format_sources(sources)))


def check_dangling(con, krita_dir, issues):
    section("[5/8] 悬空 storage（DB 有记录、磁盘文件不存在）")
    dangling = []
    for sid, stype, location in list(con.execute("select id, storage_type_id, location from storages order by id")):
        if stype in (6, 7):  # Memory / Font Storage 是虚拟 storage，不检查文件
            continue
        path = resolve_storage_path(krita_dir, location or "")
        if os.path.exists(path):
            continue
        dangling.append((sid, location, path))
        print("  id=%-4s 类型=%-20s 位置=%s" % (sid, STORAGE_TYPES.get(stype, stype), location))
        print("        磁盘上不存在：%s" % path)
    if not dangling:
        print("  未发现悬空 storage。")
        return
    issues.append("发现 %d 个悬空 storage" % len(dangling))
    print("  风险：Krita 下次启动可能静默删除该 storage 及其全部资源（笔刷整包消失、无报错）。")
    print("  处理：把缺失的 .bundle/.abr 放回原位；或重启 Krita 让它清理后重新导入。")


def check_bundle(con, bundle_path, issues):
    section("[6/8] bundle 完整性")
    if not os.path.isfile(bundle_path):
        print("  文件不存在：%s" % bundle_path)
        issues.append("bundle 文件不存在")
        return None
    size = os.path.getsize(bundle_path)
    print("  文件：%s" % os.path.basename(bundle_path))
    print("  大小：%d 字节（%.2f MB）" % (size, size / 1024.0 / 1024.0))
    try:
        zf = zipfile.ZipFile(bundle_path)
        names = zf.namelist()
        mimetype = zf.read("mimetype") if "mimetype" in names else b""
        root = ET.fromstring(zf.read("META-INF/manifest.xml").decode("utf-8"))
    except zipfile.BadZipFile as exc:
        print("  不可作为 ZIP 读取：%r" % (exc,))
        print("  判定：bundle 文件不完整（复制/覆盖中断、磁盘满、杀软锁文件都可能造成）。")
        print("        Krita 里可能仍显示预设，但内嵌纹理侧载会静默失败（纹理丢失、无报错）。")
        issues.append("bundle 文件不完整（无法读取 zip）")
        return None
    except Exception as exc:
        print("  读取 manifest 失败：%r" % (exc,))
        issues.append("bundle manifest 读取失败")
        return None

    print("  ZIP 条目数：%d（正常发布产物为 22：mimetype + manifest + meta + 19 个 .kpp）" % len(names))
    print("  mimetype：%r" % (mimetype,))
    if mimetype != b"application/x-krita-resourcebundle":
        print("  ✗ mimetype 不正确")
        issues.append("bundle mimetype 不正确")

    ok = bad = 0
    for fe in root:
        full_path = fe.get(MANIFEST_NS + "full-path")
        md5sum = fe.get(MANIFEST_NS + "md5sum")
        if not md5sum or full_path == "/":
            continue
        try:
            data = zf.read(full_path.lstrip("/"))
        except Exception:
            bad += 1
            print("  ✗ 条目缺失：%s" % full_path)
            continue
        if hashlib.md5(data).hexdigest() == md5sum:
            ok += 1
        else:
            bad += 1
            print("  ✗ md5 不符：%s" % full_path)
    print("  manifest md5：通过 %d 条，失败 %d 条" % (ok, bad))
    if bad:
        issues.append("bundle manifest md5 校验失败 %d 条" % bad)

    presets = [n for n in names if n.lower().endswith(".kpp")]
    print("  预设（.kpp）：%d 个" % len(presets))

    rows = [tuple(x) for x in con.execute(
        "select id, storage_type_id, location, active from storages where location like ?",
        ("%" + os.path.basename(bundle_path) + "%",))]
    if rows:
        stats = list(con.execute(
            "select sum(case when status=1 then 1 else 0 end), "
            "sum(case when status=0 then 1 else 0 end), count(*) from resources where storage_id=?",
            (rows[0][0],)))[0]
        print("  DB 注册状态：已注册（storage id=%s，active=%s）；该 storage 下资源：active=%s / inactive=%s / 共 %s" % (
            rows[0][0], rows[0][3], stats[0], stats[1], stats[2]))
    else:
        print("  DB 注册状态：未注册（storages 表里找不到该文件名的行）")
        print("  判定：Krita 没有扫描/登记这个 bundle——资源库里自然看不到它的预设。")
        print("        覆盖 bundle 后必须完整重启 Krita；若已重启仍未注册，见 [1/8] cache 完整性。")
        issues.append("bundle 未在 storages 表注册")
    return zf, presets


def count_resources(con):
    section("[7/8] 预设 / 图案计数")
    for type_id, type_name in ((7, "paintoppresets"), (9, "patterns")):
        total = list(con.execute(
            "select count(*), sum(case when status=1 then 1 else 0 end) from resources where resource_type_id=?",
            (type_id,)))[0]
        print("  %-16s 共 %s 条（active=%s）" % (type_name, total[0], total[1]))
    mem = list(con.execute(
        "select count(*) from resources r join storages s on s.id=r.storage_id where s.location='memory'"))
    print("  memory storage 资源：%s 条（内嵌纹理/渐变被 side-load 后会写到这里）" % mem[0][0])


def load_texture_mask_module():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        from texture_mask import compute_mask
        return compute_mask
    except Exception:
        return None


def png_pixels(png_bytes):
    """返回 RGBA uint8 数组；缺少 numpy/Pillow 时返回 None。"""
    try:
        import numpy as np
        from PIL import Image, PngImagePlugin
        PngImagePlugin.MAX_TEXT_CHUNK = 256 * 1024 * 1024
        image = Image.open(io.BytesIO(png_bytes))
        image.load()
        return np.asarray(image.convert("RGBA"), dtype=np.uint8)
    except Exception:
        return None


def texture_visibility(compute_mask, pixels, params):
    """按 Krita 纹理算子估算蒙版可见度。"""
    try:
        import numpy as np
        invert = (params.get("Texture/Pattern/Invert") or "").strip() == "true"
        brightness = float(params.get("Texture/Pattern/Brightness") or 0)
        contrast = float(params.get("Texture/Pattern/Contrast") or 1)
        mode = int(params.get("Texture/Pattern/TexturingMode") or 0)
        strength = float(params.get("Texture/Strength/Value") or 1)
        if compute_mask is None:
            gray = pixels[:, :, :3].mean(axis=2) / 255.0
            return "粗略（缺 tools/texture_mask）", "均值=%.4f 标准差=%.4f" % (float(gray.mean()), float(gray.std()))
        mask = np.asarray(compute_mask(pixels, invert, brightness, contrast, mode, strength), dtype="float64")
        mean, std = float(mask.mean()), float(mask.std())
        if std < 0.002:
            desc = "不可见（纹理对 Alpha 无变化）"
        elif mean > 0.98 and std < 0.05:
            desc = "很弱（仅少数像素压暗）"
        else:
            desc = "可见"
        return desc, "蒙版均值=%.5f 标准差=%.5f 范围=[%.4f, %.4f]" % (
            mean, std, float(mask.min()), float(mask.max()))
    except Exception as exc:
        return "无法估算（%r）" % (exc,), ""


def predict_textures(con, zf, presets, issues):
    section("[8/8] 纹理解析预测（按 Krita 5.x 源码语义推演，不启动 Krita）")
    compute_mask = load_texture_mask_module()
    if compute_mask is None:
        print("  提示：未找到 tools/texture_mask.py 或缺少 numpy，可见度只做粗略估计。")
    checked = 0
    for name in sorted(presets):
        try:
            xml_text = get_preset_xml(zf.read(name))
            root = ET.fromstring(" ".join(xml_text.split()))
        except Exception as exc:
            print("  %-46s 无法解析：%r" % (os.path.basename(name), exc))
            issues.append("预设无法解析：%s" % os.path.basename(name))
            continue
        params = {e.get("name"): (e.text or "") for e in root.iter("param") if e.get("name")}
        if (params.get("Texture/Pattern/Enabled") or "").strip() != "true":
            continue
        for res in root.iter("resource"):
            if res.get("type") != "patterns":
                continue
            checked += 1
            raw = base64.b64decode((res.text or "").strip())
            real_md5 = hashlib.md5(raw).hexdigest()
            xml_md5 = (res.get("md5sum") or "").strip()
            param_md5 = (params.get("Texture/Pattern/PatternMD5Sum") or "").strip()
            md5_ok = (xml_md5 == real_md5) and (param_md5 == real_md5)
            hits = list(con.execute(
                "select s.location from versioned_resources v "
                "join resources r on r.id=v.resource_id join storages s on s.id=r.storage_id "
                "where v.md5sum=?", (real_md5,)))
            if not hits:
                conclusion = "需 side-load：加载预设时导入 memory storage，按 md5 命中，预期可用"
            elif md5_ok:
                conclusion = "库内已有同 md5 资源：内嵌会被去重跳过，改用库内那份，预期可用"
            else:
                conclusion = "库内命中但 md5 参数不一致：可能退到 filename/name 兜底"
            print("  %-46s 图案=%s" % (os.path.basename(name), res.get("name") or os.path.basename(name)))
            print("      md5：resource=%s / PatternMD5Sum=%s / 字节=%s → %s" % (
                xml_md5[:12] or "空", param_md5[:12] or "空", real_md5[:12],
                "一致" if md5_ok else "不一致"))
            print("      解析：%s" % conclusion)
            pixels = png_pixels(raw)
            if pixels is not None:
                desc, numbers = texture_visibility(compute_mask, pixels, params)
                print("      预期可见度：%s（%s）" % (desc, numbers))
            if not md5_ok:
                issues.append("纹理 md5 参数与内嵌字节不一致：%s" % os.path.basename(name))
    if checked == 0:
        print("  该 bundle 里没有开启纹理的预设。")
    else:
        print("  合计检查带纹理预设资源：%d 个。" % checked)


class ChineseArgumentParser(argparse.ArgumentParser):
    """让 usage/错误提示也显示中文。"""

    def format_usage(self):
        return super().format_usage().replace("usage:", "用法:", 1)

    def format_help(self):
        return super().format_help().replace("usage:", "用法:", 1).replace("options:", "可选参数:")

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "%s: 错误: %s\n" % (self.prog, message))


def main(argv=None):
    parser = ChineseArgumentParser(
        prog="diagnose_profile.py",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Krita 资源库只读诊断：排查「bundle 文件在、Krita 里却笔刷不可见 / 纹理静默丢失」这类零报错问题。\n"
            "本工具只读：先把 resourcecache.sqlite 复制到临时目录再读，不启动 Krita、不写用户 profile。\n"
            "分节检查：cache 完整性 → storages → ABR 直读库（Adobe Brush Library）→ 跨 storage 同名预设 →"
            " 悬空 storage → bundle 完整性 → 预设/图案计数 → 纹理解析预测。"
        ),
        epilog=(
            "示例：\n"
            "  python tools/diagnose_profile.py --krita-dir \"%APPDATA%/krita\"\n"
            "  python tools/diagnose_profile.py --krita-dir \"%APPDATA%/krita\" --bundle converted/xxx.bundle\n"
            "  python tools/diagnose_profile.py --krita-dir \"%APPDATA%/krita\" --bundle converted/xxx.bundle --predict\n"
            "\n本工具会醒目提示两类「把原始 .abr 直接导入 Krita」造成的伪缺陷："
            "\n  - storages 里 type=4（Adobe Brush Library）的 ABR 直读库——建议移除后重启 Krita；"
            "\n  - 跨 storage 同名预设——同名会互相遮蔽，看起来像纹理丢失。"
            "\n\n退出码：0=正常，1=发现问题（含上述两类冲突），2=参数或读取错误。"
        ),
    )
    parser.add_argument("-h", "--help", action="help", help="显示本帮助并退出")
    parser.add_argument("--krita-dir", required=True,
                        help="Krita 资源目录（通常为 %%APPDATA%%/krita；也接受 ~ 与环境变量）")
    parser.add_argument("--bundle", default="",
                        help="待检查的 .bundle 文件路径（可选；给了才做 bundle 完整性与注册检查）")
    parser.add_argument("--predict", action="store_true",
                        help="对内嵌纹理做解析预测与可见度估计（需要 --bundle；较慢，会解码 PNG）")
    args = parser.parse_args(argv)

    krita_dir = expand_path(args.krita_dir)
    if not os.path.isdir(krita_dir):
        print("错误：资源目录不存在：%s" % krita_dir, file=sys.stderr)
        return 2
    bundle_path = expand_path(args.bundle) if args.bundle else ""
    if args.predict and not bundle_path:
        print("错误：--predict 需要同时指定 --bundle。", file=sys.stderr)
        return 2

    try:
        con, _db_path = open_db_readonly(krita_dir, tempfile.gettempdir())
    except Exception as exc:
        print("错误：无法以只读方式打开 resourcecache.sqlite：%r" % (exc,), file=sys.stderr)
        return 2

    issues = []
    try:
        print("Krita 资源目录：%s" % krita_dir)
        check_integrity(con, issues)
        list_storages(con)
        check_adobe_libraries(con, issues)
        check_duplicate_presets(con, issues)
        check_dangling(con, krita_dir, issues)
        bundle_handle = None
        if bundle_path:
            bundle_handle = check_bundle(con, bundle_path, issues)
        else:
            section("[6/8] bundle 完整性")
            print("  未指定 --bundle，跳过。")
        count_resources(con)
        if bundle_path and args.predict:
            if bundle_handle:
                predict_textures(con, bundle_handle[0], bundle_handle[1], issues)
            else:
                section("[8/8] 纹理解析预测")
                print("  bundle 不可用，跳过。")
        else:
            section("[8/8] 纹理解析预测")
            print("  未启用（加 --bundle 与 --predict 可做逐预设纹理预测）。")
    finally:
        con.close()

    section("结论")
    if issues:
        print("发现 %d 个问题：" % len(issues))
        for item in issues:
            print("  - " + item)
        print("")
        print("建议顺序（先做代价最小的）：")
        print("① 先确认没有把原始 .abr 直接导入 Krita——ABR 直读库会生成无纹理转换的同款笔刷，")
        print("   与转换产物混淆（本工具 [3/8]/[4/8] 已列出具体库与同名项）；")
        print("② 核对 bundle 完整性（大小/条目数/manifest md5）；")
        print("③ 只有上面两步都正常才考虑 cache：退出 Krita → 备份并删除 resourcecache.sqlite")
        print("   → 重启让 Krita 重建 → 重新导入 bundle（会丢自定义预设标签/分组，资源文件不丢）；")
        print("④ 覆盖产物时先退出 Krita，复制完成后重启，不要在 Krita 运行时覆盖。")
        return 1
    print("未发现问题。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
