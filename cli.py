"""brush_converter 命令行入口。

用法:
    python cli.py info <file.abr>
    python cli.py extract <file.abr> -o 输出目录 [--contact-sheet]
    python cli.py convert <file.abr> -o 输出目录
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent / "src"))

from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.convert import convert  # noqa: E402


def cmd_gui(args: argparse.Namespace) -> int:
    """启动 PySide6 图形界面。"""
    sys.path.insert(0, str(Path(__file__).parent / "gui"))
    from gui.main_window import main
    return main()


def cmd_convert(args: argparse.Namespace) -> int:
    """转换 .abr 为 Krita 预设（.kpp）或资源包（.bundle）。"""
    result = convert(args.file, args.output,
                     make_bundle=not args.no_bundle,
                     make_standalone=not args.no_standalone)
    print(f"转换完成：{len(result.kpp_files)} 个预设")
    for fname, _ in result.kpp_files:
        print(f"  - {fname}")
    if result.bundle_path:
        print(f"资源包：{result.bundle_path}")
    if result.skipped:
        print("跳过：")
        for s in result.skipped:
            print(f"  - {s}")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """打印 ABR 文件的版本、区段与笔尖概况。"""
    abr = AbrFile.parse(args.file)
    print(abr.summary())
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """把采样笔尖导出为 PNG（含 info.json，可选总览图）。"""
    abr = AbrFile.parse(args.file)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.file).stem

    manifest = []
    for tip in abr.tips:
        safe_name = (tip.uuid or tip.name or f"tip{tip.index:03d}").replace("/", "_")
        png_path = out_dir / f"{stem}_{tip.index:02d}_{safe_name}.png"
        img = Image.fromarray(tip.gray, mode="L")
        img.save(png_path)
        manifest.append({
            "index": tip.index,
            "file": png_path.name,
            "width": tip.width,
            "height": tip.height,
            "depth_bits": tip.depth_bits,
            "uuid": tip.uuid,
            "name": tip.name,
            "source_offset": tip.source_offset,
        })

    info = {
        "source": str(args.file),
        "version": f"{abr.version}.{abr.subversion}",
        "sections": {k: s.length for k, s in abr.sections.items()},
        "tip_count": len(abr.tips),
        "tips": manifest,
    }
    (out_dir / f"{stem}_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.contact_sheet and abr.tips:
        _contact_sheet(abr, out_dir / f"{stem}_preview.png")

    print(f"已导出 {len(abr.tips)} 个笔尖到 {out_dir}")
    return 0


def _contact_sheet(abr: AbrFile, path: Path, cell: int = 160, cols: int = 6) -> None:
    """生成笔尖总览图（黑底白笔，便于目检）。"""
    tips = abr.tips
    rows = (len(tips) + cols - 1) // cols
    sheet = Image.new("L", (cols * cell, rows * cell), 20)
    for i, tip in enumerate(tips):
        img = Image.fromarray(tip.gray, mode="L")
        img.thumbnail((cell - 8, cell - 8))
        x = (i % cols) * cell + (cell - img.width) // 2
        y = (i // cols) * cell + (cell - img.height) // 2
        sheet.paste(img, (x, y))
    sheet.save(path)


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器：info / extract / convert / gui 四个子命令。"""
    parser = argparse.ArgumentParser(prog="brush_converter", description="ABR 笔刷转换工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="查看 ABR 文件概况")
    p_info.add_argument("file")
    p_info.set_defaults(func=cmd_info)

    p_ext = sub.add_parser("extract", help="提取采样笔尖为 PNG")
    p_ext.add_argument("file")
    p_ext.add_argument("-o", "--output", default="extracted")
    p_ext.add_argument("--contact-sheet", action="store_true", help="额外生成总览图")
    p_ext.set_defaults(func=cmd_extract)

    p_conv = sub.add_parser("convert", help="转换为 Krita 预设/资源包")
    p_conv.add_argument("file")
    p_conv.add_argument("-o", "--output", default="converted")
    p_conv.add_argument("--no-bundle", action="store_true", help="不生成 .bundle")
    p_conv.add_argument("--no-standalone", action="store_true", help="不生成单独 .kpp")
    p_conv.set_defaults(func=cmd_convert)

    p_gui = sub.add_parser("gui", help="启动图形界面")
    p_gui.set_defaults(func=cmd_gui)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口：分发到子命令，捕获解析/IO 错误并返回退出码。"""
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
