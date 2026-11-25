"""brush_converter 命令行入口。

用法:
    python cli.py info <file.abr>
    python cli.py extract <file.abr> -o 输出目录 [--contact-sheet]
    python cli.py convert <file.abr> -o 输出目录
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import struct
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent / "src"))

from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.convert import ConvertResult, convert_presets  # noqa: E402
from brush_converter.export_options import (  # noqa: E402
    SWITCH_IDS,
    CancelToken,
    ConfigIssue,
    ConversionCancelled,
    ExportOptionState,
    OptionConfigError,
    option_config_payload,
    source_digest_of_bytes,
)
from brush_converter.export_transaction import ExportTransactionError  # noqa: E402
from brush_converter.kpp.bundle import verify_bundle  # noqa: E402
from brush_converter.mapping import map_presets  # noqa: E402


def cmd_gui(args: argparse.Namespace) -> int:
    """启动 PySide6 图形界面。"""
    sys.path.insert(0, str(Path(__file__).parent / "gui"))
    from gui.main_window import main
    return main()


def _load_source(path: str) -> tuple[AbrFile, ExportOptionState]:
    """同一份源字节同时用于解析与身份；不按筛选后的序号重建 ID。"""
    data = Path(path).read_bytes()
    try:
        abr = AbrFile._from_bytes(data, Path(path).stem)
    except (struct.error, IndexError) as exc:
        raise ValueError("ABR 结构损坏") from exc
    state = ExportOptionState.create(map_presets(abr), source_digest_of_bytes(data))
    return abr, state


def _apply_option_file(state: ExportOptionState, path: str) -> None:
    """只补严格 JSON 解码；字段、身份、适用性全部复用167共用校验。"""
    issues = []

    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                issues.append(ConfigIssue("bad_json", "JSON 对象含重复键，拒绝覆盖", "$"))
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("JSON 不允许 NaN 或 Infinity")

    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise OptionConfigError([ConfigIssue("unreadable", "无法读取配置文件", "$")]) from exc
    except UnicodeError as exc:
        raise OptionConfigError([ConfigIssue("bad_json", "配置必须是 UTF-8", "$")]) from exc
    try:
        payload = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError) as exc:
        location = f"行{exc.lineno}列{exc.colno}" if isinstance(exc, json.JSONDecodeError) else "$"
        raise OptionConfigError([ConfigIssue("bad_json", "JSON 语法或编码无效", location)]) from exc
    if issues:
        raise OptionConfigError(issues)
    state.apply_config(payload)


def convert(abr_path: str, out_dir: str, *, make_bundle: bool = True,
            make_standalone: bool = True, options_file: str | None = None,
            preset_ids: list[str] | None = None, cancel: CancelToken | None = None,
            progress=None) -> ConvertResult:
    """CLI 薄适配：载入/校验/选子集后只调用既有169转换事务，不复制转换逻辑。"""
    abr, state = _load_source(abr_path)
    if options_file is not None:
        # 先校验整份配置；未选中项也不能藏着未知ID或非法开关。
        _apply_option_file(state, options_file)
    selected = set(state.preset_ids) if preset_ids is None else set(preset_ids)
    issues = []
    seen = set()
    for index, key in enumerate(preset_ids or []):
        location = f"--preset-id[{index}]"
        if key not in state.preset_ids:
            issues.append(ConfigIssue("unknown_id", "选择中含未知 ID", location))
        if key in seen:
            issues.append(ConfigIssue("duplicate_id", "选择中含重复 ID", location))
        seen.add(key)
    if issues:
        raise OptionConfigError(issues)
    keys = [key for key in state.preset_ids if key in selected]
    presets = [state.preset(key) for key in keys]
    subset = ExportOptionState(presets, [state.identity(key) for key in keys], state.source_digest)
    payload = option_config_payload(state)
    payload["presets"] = [entry for entry in payload["presets"] if entry["id"] in selected]
    subset.apply_config(payload)
    if cancel is not None:
        cancel.raise_if_cancelled()
    return convert_presets(abr, presets, out_dir, make_bundle=make_bundle,
                          make_standalone=make_standalone, option_state=subset,
                          cancel=cancel, progress=progress)


@contextmanager
def _cooperative_interrupts(token: CancelToken):
    """中断只置取消令牌；重复按键也不抛异常、不强杀，退出必恢复旧处理器。"""
    installed = []

    def request_cancel(_signum, _frame):
        token.cancel("命令行中断请求")

    try:
        for sig in (signal.SIGINT, getattr(signal, "SIGBREAK", None)):
            if sig is not None:
                previous = signal.getsignal(sig)
                signal.signal(sig, request_cancel)
                installed.append((sig, previous))
        yield
    finally:
        for sig, previous in reversed(installed):
            signal.signal(sig, previous)


def _print_transaction(summary, *, stream) -> None:
    """只投影169可信状态与数字；原始名称、路径、异常和元数据不回显。"""
    print(f"安全写出：{summary.status_label()}；{summary.status_message()}", file=stream)
    counts = summary.counts
    print("事务明细：" + "，".join(f"{key}={counts[key]}" for key in
          ("new", "reused", "renamed", "blocked", "rolled_back", "retained")), file=stream)
    for index, item in enumerate(summary.items, 1):
        print(f"  产物#{index}：类型={item.kind}，状态={item.state}，sha256={item.sha256}",
              file=stream)
    if summary.blocked or summary.retained:
        print("受阻/保留现场未自动删除：请保留指定输出目录及本次暂存清单；"
              "名称、绝对路径和原始原因不回显，按上方产物编号/摘要核对。", file=stream)


def _print_actual_summary(result: ConvertResult) -> None:
    """仅统计核心真实运行记录，不把配置勾选或未选中项写成实际处理。"""
    summary = result.options_summary
    if summary is None:
        return
    counts = summary.counts()
    print(f"逐支处理（真实运行，evidence={summary.evidence}）："
          f"{counts['total_presets']} 支；现行处理 {counts['applied']} 项，"
          f"可编辑替代 {counts['alternative']} 项，不适用 {counts['not_applicable']} 项，"
          f"未处理 {counts['not_processed']} 项。")
    if result.cancelled:
        print(f"取消前内存生成 {result.rendered_in_memory} 支；本批交付 0 支。")
    for item in summary.items:
        for switch_id in SWITCH_IDS:
            outcome = item.switches[switch_id]
            if outcome.actual != "not_applicable":
                # 原因来自168生产计划，仅含固定说明与数值，不含笔刷名或源元数据。
                print(f"  {item.identity.preset_id} / {switch_id}："
                      f"实际={outcome.actual}；{outcome.actual_reason}")


def cmd_convert(args: argparse.Namespace) -> int:
    """完整校验后进入169事务；输出仅含稳定身份、可信状态和真实处理计数。"""
    options_files = args.options_files or []
    if len(options_files) > 1:
        raise OptionConfigError([ConfigIssue("bad_field", "只能提供一份配置文件", "--options-file")])
    if args.list_presets:
        if options_files or args.preset_ids is not None:
            raise OptionConfigError([ConfigIssue("bad_field", "列出ID不能与配置或选择混用", "--list-presets")])
        _, state = _load_source(args.file)
        payload = {"format": "brushconverter.preset-list", "version": 1,
                   "source_digest": state.source_digest, "presets": [
                       {"id": item.preset_id, "ordinal": item.ordinal,
                        "applicable": {key: state.applicability(item.preset_id, key).applicable
                                       for key in SWITCH_IDS},
                        "defaults": {key: state.default_enabled(item.preset_id, key)
                                     for key in SWITCH_IDS}}
                       for item in state.identities]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.no_bundle and args.no_standalone:
        raise OptionConfigError([ConfigIssue("bad_field", "至少保留一种输出格式", "输出格式")])
    shared_token = getattr(args, "_cancel_token", None)
    token = shared_token if shared_token is not None else CancelToken()
    notified = False

    def progress(event):
        nonlocal notified
        if event.phase == "write" and not notified:
            print("写出提示：全部验证后进入短暂不可取消交付；期间到达的取消请求仍完成本批。")
            notified = True

    # main统一守护到失败摘要结束；直接调用此命令函数时仍提供独立守护。
    with nullcontext() if shared_token is not None else _cooperative_interrupts(token):
        result = convert(args.file, args.output, make_bundle=not args.no_bundle,
                         make_standalone=not args.no_standalone,
                         options_file=options_files[0] if options_files else None,
                         preset_ids=args.preset_ids, cancel=token, progress=progress)
        if result.transaction is not None:
            _print_transaction(result.transaction, stream=sys.stdout)
        if result.cancelled or result.status == "cancelled":
            print("已取消：本批未交付；不是转换成功。")
            _print_actual_summary(result)
            return 130
        if result.status in ("empty", "all_skipped"):
            print(result.status_message())
            print(f"跳过 {len(result.skipped)} 支；本批未写出文件。")
            return 2
        if result.status in ("failed", "rolled_back", "partial_blocked"):
            return 3
        if result.bundle_path:
            verified = result.verify if result.verify is not None else verify_bundle(result.bundle_path)
            if not verified.ok:
                print("输出校验：失败；原始路径与元数据不回显。", file=sys.stderr)
                return 3
            print(f"输出校验：OK（{verified.preset_count} 个预设，"
                  f"sha256={verified.sha256}，大小={verified.size}B）")
        print(f"转换完成：{len(result.kpp_files)} 个预设（含真实纹理 {result.texture_count} 个）")
        if result.bundle_path:
            print("资源包：位于指定输出目录（文件名不回显）。")
        if result.skipped:
            print(f"跳过 {len(result.skipped)} 支：缺少笔尖位图（未计为已转换）。")
        _print_actual_summary(result)
        if token.cancelled or (result.transaction is not None and result.transaction.cancel_requested_late):
            print("取消请求在不可取消阶段或完成摘要时到达：本批已完整交付，不冒报已取消。")
        return 0



def cmd_info(args: argparse.Namespace) -> int:
    """打印 ABR 文件的版本、区段与笔尖概况。"""
    abr = AbrFile.parse(args.file)
    print(abr.summary())
    return 0 if abr.tips else 2


def _safe_component(name: str) -> str:
    """把（可能来自 ABR 的）名字清洗为安全的单段文件名。

    Windows 保留字符 \\ : * ? " < > | 与控制字符都会让 open() 失败，
    点号/空格结尾在 Windows 上也会被静默截断，全部替换掉。
    """
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .")
    return cleaned or "tip"


def cmd_extract(args: argparse.Namespace) -> int:
    """把采样笔尖导出为 PNG（含 info.json，可选总览图）。"""
    abr = AbrFile.parse(args.file)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.file).stem

    if not abr.tips:
        print("错误: 未从该文件读取到任何笔刷，无法提取。", file=sys.stderr)
        for d in abr.diagnostics:
            print(f"  诊断: {d}", file=sys.stderr)
        return 2

    manifest = []
    for tip in abr.tips:
        safe_name = _safe_component(tip.uuid or tip.name or f"tip{tip.index:03d}")
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

    if args.contact_sheet:  # abr.tips 已由上面的 `if not abr.tips` 提前返回保证非空
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


class _ArgumentParser(argparse.ArgumentParser):
    """参数错误不回显用户传入的路径或元数据；仍保留标准退出码2。"""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "参数错误：请按 --help 核对命令、必填值和参数组合（输入内容不回显）。\n")


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器：info / extract / convert / gui 四个子命令。"""
    parser = _ArgumentParser(prog="brush_converter", description="ABR 笔刷转换工具",
                             allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="查看 ABR 文件概况")
    p_info.add_argument("file")
    p_info.set_defaults(func=cmd_info)

    p_ext = sub.add_parser("extract", help="提取采样笔尖为 PNG")
    p_ext.add_argument("file")
    p_ext.add_argument("-o", "--output", default="extracted")
    p_ext.add_argument("--contact-sheet", action="store_true", help="额外生成总览图")
    p_ext.set_defaults(func=cmd_extract)

    p_conv = sub.add_parser(
        "convert", help="转换为 Krita 预设/资源包", allow_abbrev=False,
        description="逐支复用GUI核心选项和安全写出；不提供新参数时仍转换全部笔刷。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''条件默认（每次载入重算，不记住上次手动值）：
  rasterize_computed：计算笔刷默认位图化；关闭保留原生硬度字段，但几何外观有代价。
  prescale_texture：有纹理位图、scale>100且取整后尺寸变化才预缩放；关闭保留源图与Scale。
  bake_texture_bc：有纹理位图且核心亮度越界判定命中才预烘焙；关闭保留原生字段但可能截断。
  仅适用项默认true；false逐支关闭，不适用项不能强开true。
配置：UTF-8 JSON（可带BOM），format=brushconverter.export-options，version=1，
  source_digest建议填写--list-presets给出的完整摘要；presets数组按原始稳定id覆盖switches。
  例：{"format":"brushconverter.export-options","version":1,"presets":[
       {"id":"复制列表中的完整ID","switches":{"rasterize_computed":false}}]}
  省略的笔刷/开关仍用条件默认；配置不是选择清单。--preset-id才限制选择，可重复给不同ID，
  导出保持源顺序；未选中项仍完整校验但不导出。空presets=[]不是零选中。
  未知/重复ID、同名歧义、未知开关、类型/版本/源摘要错、格式错误、重复JSON键均失败，
  不回退默认、不按名字猜；唯一name仅沿用167显式校验，推荐始终用id。
  --list-presets只列ID/原始序号/适用性/默认值，不写输出目录，不打印原名或源元数据。
PowerShell转义：路径含空格用单引号；单引号自身写成两个单引号。
  & $PY -B cli.py convert '.tmp/171/样本.abr' --list-presets
  & $PY -B cli.py convert '.tmp/171/样本.abr' -o '.tmp/171/输出' --options-file '.tmp/171/逐支配置.json'
  JSON在文件内使用双引号；字符串中的双引号写成\\"，反斜杠写成\\\\，中文直接UTF-8。
取消与退出：Ctrl+C（Windows也可Ctrl+Break）请求协作取消；已开始的内存处理允许完成，
  最终交付前取消整批不发布，短暂不可取消交付期收到请求仍完成本批。不可强杀进程。
  0=成功/同内容复用；1=输入/内部错误；2=配置/选择/零源/全部跳过；
  3=安全写出/校验失败；130=取消。已有文件同内容复用、异内容顺延，不覆盖。
  实际摘要只依据核心真实运行，未选中/跳过项不冒充已处理；控制台隐藏原名、路径与元数据。
完整内部示例见任务171命令示例；正式产品文档在任务182统一出版。''')
    p_conv.add_argument("file", help="输入ABR路径")
    p_conv.add_argument("-o", "--output", default="converted")
    p_conv.add_argument("--no-bundle", action="store_true", help="不生成 .bundle")
    p_conv.add_argument("--no-standalone", action="store_true", help="不生成单独 .kpp")
    p_conv.add_argument("--options-file", dest="options_files", action="append", metavar="JSON",
                        help="显式逐支配置文件，只能给一份；坏配置非零失败且不写出")
    p_conv.add_argument("--preset-id", dest="preset_ids", action="append", metavar="ID",
                        help="仅选指定原始ID；可重复给不同ID，不使用名称或筛选行号")
    p_conv.add_argument("--list-presets", action="store_true",
                        help="仅输出稳定ID/源摘要JSON；不转换、不创建目录，不与配置/选择混用")
    p_conv.set_defaults(func=cmd_convert)

    p_gui = sub.add_parser("gui", help="启动图形界面")
    p_gui.set_defaults(func=cmd_gui)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口：分发到子命令，捕获解析/IO 错误并返回退出码。"""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        # 脚本仍用SystemExit(main())；程序内调用也能拿到帮助/参数错误的同一退出码。
        return int(exc.code)
    if args.command == "convert":
        token = CancelToken()
        args._cancel_token = token
        # 异常分支的事务清单也属于本次命令，中断不得打断失败事实或暴露traceback。
        with _cooperative_interrupts(token):
            return _dispatch(args)
    return _dispatch(args)


def _dispatch(args: argparse.Namespace) -> int:
    """统一错误出口；convert的成功/失败/取消摘要全处于同一协作信号守护内。"""
    try:
        return args.func(args)
    except OptionConfigError as exc:
        # 保留全部错误码和安全位置；167原始报文可能含本机路径/源名称，不能直接回显。
        messages = {
            "bad_field": "配置字段/类型/参数组合错误；只允许契约字段，布尔值用true/false",
            "bad_json": "JSON格式/UTF-8编码错误或重复键；请核对引号与转义",
            "unreadable": "无法读取配置文件；请核对文件存在且可读",
            "version_mismatch": "配置version必须为整数1",
            "source_mismatch": "配置源摘要与当前ABR不匹配；请重新列出ID",
            "unknown_id": "未知ID；请复制当前源的完整稳定ID，不按名字猜测",
            "duplicate_id": "重复ID；同一支只能配置/选择一次",
            "unknown_name": "未知名称；建议改用稳定ID",
            "duplicate_name": "名称重复或存在同名歧义；必须改用稳定ID",
            "unknown_switch": "未知开关；只允许三项固定开关ID",
            "inapplicable_on": "不适用项不能强开true；请查询列表的applicable",
        }
        print(f"导出选项配置无效：共{len(exc.issues)}项；未进入转换写出。", file=sys.stderr)
        for issue in exc.issues:
            # location也可能带未知键内容，仅保留契约固定路径与数组下标。
            location = issue.location
            allowed = r"\$(?:\.(?:format|version|source_digest|presets|id|name|switches|rasterize_computed|prescale_texture|bake_texture_bc)|\[\d+\])*|--preset-id\[\d+\]|--options-file|--list-presets|输出格式|行\d+列\d+"
            if re.fullmatch(allowed, location) is None:
                location = "配置位置已隐藏"
            code = issue.code if issue.code in messages else "bad_field"
            print(f"  [{code}] {location}: {messages[code]}", file=sys.stderr)
        return 2
    except ExportTransactionError as exc:
        print("安全写出失败：本批未完成；原始异常含路径或元数据，不直接回显。", file=sys.stderr)
        if exc.summary is not None:
            _print_transaction(exc.summary, stream=sys.stderr)
        else:
            print("没有可用事务清单；请保留输出目录现场，不自动删除。", file=sys.stderr)
        return 3
    except (ConversionCancelled, KeyboardInterrupt):
        print("已取消：未完成转换写出。", file=sys.stderr)
        return 130
    except (ValueError, OSError) as exc:
        if args.command != "convert":
            print(f"错误: {exc}", file=sys.stderr)
            return 1
        print("输入/读取/转换失败：请核对输入文件与输出目录；路径、原始元数据不回显。",
              file=sys.stderr)
        return 1
    except Exception:
        # 只约束本任务convert入口；其它旧子命令保留原有异常传播以免扩大任务范围。
        if args.command != "convert":
            raise
        print("转换内部错误：本批未完成；请保留现场交维护者复核，不回显源码路径。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
