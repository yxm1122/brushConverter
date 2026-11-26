"""安全写出与整批取消（任务 169）。

本模块把“先暂存、回读验证、再交付”做成一个独立的事务对象，供转换管线
（convert.py）调用；它只依赖标准库与既有 .bundle 校验工具，不改动任何
笔刷参数、不漏写旧文件。

任务书四条要求的落地方式：

1. **先暂存再验证交付**：所有产物先写进“本次专属暂存目录”，逐个回读校验
   （字节、PNG/iTXt 结构、bundle 的 zip/manifest/md5），全部通过后才进入
   最终交付阶段。默认暂存目录建在目标目录内部（同卷），不使用系统临时目录。
   不承诺跨多文件系统或整批文件的原子替换：最终交付只保证“每个文件
   不经覆盖、按本次新建清单逐个放置”。
2. **取消/异常/重名冲突按事务清单处理**：每次写出都在 items 里登记
   逻辑名、最终名、暂存路径、最终路径、sha256、状态。取消或异常只清理
   本操作创建且经绝对路径核对的内容，不碰旧文件、复用文件、他人目录。
3. **独立 kpp 保护**：独立 .kpp 与 .bundle 走同一套“同内容复用 / 不同内容
   顺延 _2、_3”策略，绝不直接覆盖旧同名文件。最终交付中途失败时，按
   “本次新建”清单回滚；无法可靠回滚的文件保留并进入 blocked/retained
   清单，不写成“全有或全无”。
4. **协作检查点**：取消只在每支笔开始前、每个产物暂存后等检查点轮询，
   不持有线程/进程句柄、不强杀线程；最后交付阶段显式标记“不可取消”，
   期间到达的取消请求只记录，不会打断已经开始的交付。

与任务 167/168 的接口关系：

- 取消令牌、ConversionCancelled、ProgressEvent、notify_progress
  全部复用 export_options.py 已冻结的契约；
- 任务 168 的真实运行摘要由 convert.py 组合，本模块只报告写出事务；
- GUI 取消按钮（172）应传入 CancelToken 或 cancel_callback，并在
  最终交付阶段把界面标成“不可取消”。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from .export_options import (
    CANCELLED_PHASE,
    PHASE_WRITE,
    CancelCallback,
    CancelToken,
    ConversionCancelled,
    ProgressCallback,
    ProgressEvent,
    notify_progress,
)
from .kpp.bundle import (
    BundleIdentity,
    BundleVerifyResult,
    bundle_identity_matches,
    bundle_identity_of_file,
    verify_bundle,
)
from .kpp.kpp_writer import read_preset_xml

__all__ = [
    "ExportTransaction",
    "ExportTransactionError",
    "RESULT_STATUS_ALL_SKIPPED",
    "RESULT_STATUS_CANCELLED",
    "RESULT_STATUS_COMMITTED",
    "RESULT_STATUS_EMPTY",
    "RESULT_STATUS_FAILED",
    "RESULT_STATUS_PARTIAL_BLOCKED",
    "RESULT_STATUS_REUSED_ONLY",
    "RESULT_STATUS_ROLLED_BACK",
    "STAGING_PREFIX",
    "STATE_BLOCKED",
    "STATE_COMMITTED",
    "STATE_REUSED",
    "STATE_ROLLED_BACK",
    "STATE_STAGED",
    "STATE_VERIFIED",
    "STATUS_LABELS",
    "TransactionItem",
    "TransactionSummary",
]

# ---------------------------------------------------------------------------
# 常量：状态名一旦写进测试/界面就不要随手改（机器可读，界面再翻译成中文）
# ---------------------------------------------------------------------------

#: 本次专属暂存目录名前缀；默认放在目标目录里，保证与目标同卷。
STAGING_PREFIX = ".brushconverter-staging-"

#: 事务清单里单个产物的状态。
STATE_STAGED = "staged"
STATE_VERIFIED = "verified"
STATE_COMMITTED = "committed"
STATE_REUSED = "reused"
STATE_ROLLED_BACK = "rolled_back"
STATE_BLOCKED = "blocked"

#: 整批事务的最终状态。
RESULT_STATUS_COMMITTED = "committed"
RESULT_STATUS_REUSED_ONLY = "reused_only"
RESULT_STATUS_CANCELLED = "cancelled"
RESULT_STATUS_EMPTY = "empty"
RESULT_STATUS_ALL_SKIPPED = "all_skipped"
RESULT_STATUS_ROLLED_BACK = "rolled_back"
RESULT_STATUS_PARTIAL_BLOCKED = "partial_blocked"
RESULT_STATUS_FAILED = "failed"

#: 界面/日志用的中文状态标签（机器值仍用上面的英文常量）。
STATUS_LABELS: dict[str, str] = {
    RESULT_STATUS_COMMITTED: "已交付",
    RESULT_STATUS_REUSED_ONLY: "全部复用（无新写入）",
    RESULT_STATUS_CANCELLED: "已取消",
    RESULT_STATUS_EMPTY: "零选中",
    RESULT_STATUS_ALL_SKIPPED: "全部跳过",
    RESULT_STATUS_ROLLED_BACK: "交付失败，已回滚",
    RESULT_STATUS_PARTIAL_BLOCKED: "交付失败，存在受阻文件",
    RESULT_STATUS_FAILED: "暂存/验证失败，未交付",
}


class ExportTransactionError(OSError):
    """安全写出事务失败（暂存、验证或最终交付）。

    继承 OSError 是为了让既有 CLI 的 except (ValueError, OSError) 能
    捕获并返回非零退出码，而不是把半成品当成成功。

    summary 保留失败时的完整事务清单（含 blocked/retained），调用方
    可据此显示“哪些文件没交付、哪些现场被保留”，不需要从异常文本里
    猜。phase 标明失败发生在暂存（staging）还是最终交付（commit）。
    """

    def __init__(
        self,
        message: str,
        *,
        phase: str = "",
        summary: TransactionSummary | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.summary = summary
        self.cause = cause


# ---------------------------------------------------------------------------
# 路径安全工具：所有清理前都先核对“绝对路径 + 确属本次创建”
# ---------------------------------------------------------------------------


def _as_path(value: str | Path) -> Path:
    """统一转成绝对路径（不要求路径已存在）。"""
    return Path(value).expanduser().resolve()


def _nearest_existing(path: Path) -> Path:
    """向上找到最近的已存在路径，用于判断卷标（目标目录可能尚未创建）。"""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return probe


def _volume_key(path: Path) -> str:
    """取路径所在卷的稳定标识（Windows 的 st_dev 即卷序列号）。

    取不到 stat 时退回盘符/根目录；两种口径都不会把不同卷判成同卷。
    """
    probe = _nearest_existing(path)
    try:
        return f"{probe.stat().st_dev}"
    except OSError:
        drive = os.path.splitdrive(str(probe))[0].lower()
        return drive or "/"


def _same_volume(first: Path, second: Path) -> bool:
    """两个路径是否位于同一卷（暂存与目标必须同卷，才谈得上“同卷交付”）。"""
    return _volume_key(first) == _volume_key(second)


def _is_inside(child: Path, parent: Path) -> bool:
    """child 是否是 parent 之下（含相等）；用于清理前的绝对路径核对。"""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 可注入的底层文件动作：测试用 monkeypatch 模拟磁盘错误/交付中失败
# ---------------------------------------------------------------------------


def _write_bytes(path: Path, data: bytes) -> None:
    """写一个暂存文件（测试可替换此函数注入磁盘错误）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _read_bytes(path: Path) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _place_file(staged: Path, final: Path) -> None:
    """把暂存文件放到最终路径，且**绝不覆盖**已存在的文件。

    Windows 与 POSIX 的 rename 语义不同：Windows 遇到已存在目标会报错，
    POSIX 会静默替换。所以优先用硬链接占位（目标存在即 FileExistsError，
    两个平台都不会覆盖），成功后再删暂存名；文件系统不支持硬链接时退回
    “先查存在性、再同卷 rename”的保守路径。
    """
    if final.exists() or final.is_symlink():
        raise FileExistsError(f"目标已存在，拒绝覆盖：{final}")
    try:
        os.link(staged, final)
    except FileExistsError:
        raise
    except (AttributeError, NotImplementedError, OSError):
        # 某些文件系统/权限下硬链接不可用：退回 rename，但仍先做存在性检查。
        if final.exists() or final.is_symlink():
            raise FileExistsError(f"目标已存在，拒绝覆盖：{final}")
        os.rename(staged, final)
    else:
        try:
            os.unlink(staged)
        except OSError:
            # 最终文件已就位；暂存名残留交给清理阶段，不把成功写成失败。
            pass


def _remove_file(path: Path) -> None:
    """删除单个文件（测试可替换此函数模拟“回滚也失败”）。"""
    path.unlink()


# ---------------------------------------------------------------------------
# 事务清单数据结构
# ---------------------------------------------------------------------------


@dataclass
class TransactionItem:
    """一个产物在事务里的完整记录（机器读，不写进界面文案）。"""

    kind: str                       # "kpp" / "bundle"
    logical_name: str                # 逻辑名（bundle 内条目名 / 独立 kpp 原名）
    final_name: str                  # 最终文件名（可能带 _2/_3 后缀）
    final_path: str                  # 最终绝对路径
    staged_path: str = ""            # 暂存绝对路径（复用项为空）
    sha256: str = ""
    size: int = 0
    state: str = STATE_STAGED
    renamed_from: str = ""           # 因重名冲突而顺延时记录原目标名
    reason: str = ""                 # blocked/failed 的人类可读原因

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "logical_name": self.logical_name,
            "final_name": self.final_name,
            "final_path": self.final_path,
            "staged_path": self.staged_path,
            "sha256": self.sha256,
            "size": self.size,
            "state": self.state,
            "renamed_from": self.renamed_from,
            "reason": self.reason,
        }


@dataclass
class TransactionSummary:
    """一次安全写出事务的最终清单与结论（可 JSON 序列化给 GUI/CLI）。"""

    out_dir: str
    staging_dir: str
    status: str
    phase: str
    cancellable: bool
    cancel_requested: bool = False
    cancel_requested_late: bool = False
    cancel_reason: str = ""
    items: list[TransactionItem] = field(default_factory=list)
    blocked: list[dict[str, str]] = field(default_factory=list)
    rolled_back: list[str] = field(default_factory=list)
    retained: list[str] = field(default_factory=list)
    created_dirs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bundle_verify: BundleVerifyResult | None = None
    manifest_path: str = ""

    # ---- 派生视图 ----

    def state_items(self, state: str) -> list[TransactionItem]:
        return [item for item in self.items if item.state == state]

    @property
    def committed(self) -> list[TransactionItem]:
        return self.state_items(STATE_COMMITTED)

    @property
    def reused(self) -> list[TransactionItem]:
        return self.state_items(STATE_REUSED)

    @property
    def bundle_reused(self) -> bool:
        return any(item.kind == "bundle" and item.state == STATE_REUSED
                   for item in self.items)

    @property
    def bundle_renamed(self) -> bool:
        return any(item.kind == "bundle" and item.renamed_from
                   for item in self.items)

    @property
    def counts(self) -> dict[str, int]:
        # 只有真正交付（新建或复用）的项才计入“重名顺延”；取消/失败时
        # 清单里可能已有 staged 的改名项，但它们没有出现在目标目录。
        delivered = (STATE_COMMITTED, STATE_REUSED)
        return {
            "new": len(self.committed),
            "reused": len(self.reused),
            "renamed": sum(1 for item in self.items
                           if item.renamed_from and item.state in delivered),
            "blocked": len(self.blocked),
            "rolled_back": len(self.rolled_back),
            "retained": len(self.retained),
        }

    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def status_message(self) -> str:
        """面向用户的一句话结论；取消/失败绝不写成“转换成功”。"""
        if self.status == RESULT_STATUS_CANCELLED:
            text = "已取消：未进入最终交付，目标目录没有本批文件"
            if self.retained:
                text += f"；{len(self.retained)} 项暂存现场未能清理，已保留"
            return text
        if self.status == RESULT_STATUS_EMPTY:
            return "零选中：未写出任何文件"
        if self.status == RESULT_STATUS_ALL_SKIPPED:
            return "全部跳过（缺少笔尖位图）：未写出任何文件"
        if self.status == RESULT_STATUS_COMMITTED:
            counts = self.counts
            text = f"已安全交付 {counts['new']} 个新文件"
            if counts["reused"]:
                text += f"，复用 {counts['reused']} 个同内容文件（未重写）"
            if counts["renamed"]:
                text += f"，重名顺延 {counts['renamed']} 个（旧文件未覆盖）"
            return text
        if self.status == RESULT_STATUS_REUSED_ONLY:
            return "全部产物已存在同内容文件：未重复写入，旧文件 mtime 未变"
        if self.status == RESULT_STATUS_ROLLED_BACK:
            return "交付中途失败：已按本次新建清单回滚，旧文件未动"
        if self.status == RESULT_STATUS_PARTIAL_BLOCKED:
            return ("交付中途失败：部分文件无法可靠回滚，已保留现场并列入受阻清单，"
                    "不宣称全有或全无")
        return "未能交付任何文件（暂存/验证阶段失败）"

    def to_dict(self) -> dict[str, object]:
        verify = self.bundle_verify
        return {
            "out_dir": self.out_dir,
            "staging_dir": self.staging_dir,
            "status": self.status,
            "status_label": self.status_label(),
            "phase": self.phase,
            "cancellable": self.cancellable,
            "cancel_requested": self.cancel_requested,
            "cancel_requested_late": self.cancel_requested_late,
            "cancel_reason": self.cancel_reason,
            "counts": self.counts,
            "items": [item.to_dict() for item in self.items],
            "blocked": list(self.blocked),
            "rolled_back": list(self.rolled_back),
            "retained": list(self.retained),
            "created_dirs": list(self.created_dirs),
            "warnings": list(self.warnings),
            "bundle_verify": None if verify is None else {
                "ok": verify.ok,
                "preset_count": verify.preset_count,
                "size": verify.size,
                "sha256": verify.sha256,
                "errors": list(verify.errors),
            },
            "manifest_path": self.manifest_path,
        }

    def format_lines(self) -> list[str]:
        """中文摘要行（GUI 状态区/CLI 失败提示可直接复用）。"""
        counts = self.counts
        lines = [
            f"安全写出：{self.status_message()}",
            f"明细：新建 {counts['new']}，复用 {counts['reused']}，"
            f"重名顺延 {counts['renamed']}，受阻 {counts['blocked']}，"
            f"已回滚 {counts['rolled_back']}，保留现场 {counts['retained']}。",
        ]
        if self.blocked:
            for entry in self.blocked:
                lines.append(f"· 受阻：{entry.get('name', '')}（{entry.get('reason', '')}）")
        if self.retained:
            lines.append("· 保留现场：" + "；".join(self.retained))
        return lines


# ---------------------------------------------------------------------------
# 事务对象：暂存 → 回读校验 → 不可取消的最终交付
# ---------------------------------------------------------------------------


class ExportTransaction:
    """一次导出的安全写出事务。

    典型用法（convert.py 的写法）：

        tx = ExportTransaction(out_dir, token=token, progress=callback, total=N)
        tx.begin()
        for ...:
            tx.checkpoint()
            tx.stage_kpp(name, kpp_bytes)
        tx.stage_bundle(base_name, bundle_bytes, content_hash8, identity=build.identity)
        summary = tx.commit()

    取消：转换循环里的 checkpoint() 抛 ConversionCancelled，调用方
    捕获后调用 tx.cancel(reason) 清理暂存；最终交付开始后 checkpoint()
    不再中断，只记录“迟到取消”。
    """

    def __init__(
        self,
        out_dir: str | Path,
        *,
        staging_dir: str | Path | None = None,
        token: CancelToken | None = None,
        cancel_callback: CancelCallback | None = None,
        progress: ProgressCallback | None = None,
        total: int = 0,
        make_standalone: bool = True,
    ) -> None:
        if token is not None and not isinstance(token, CancelToken):
            raise TypeError("token 必须是 CancelToken 或 None")
        if cancel_callback is not None and not callable(cancel_callback):
            raise TypeError("cancel_callback 必须可调用或为 None")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise ValueError(f"total 必须是非负整数，实际 {total!r}")
        self.out_dir = _as_path(out_dir)
        self.kpp_dir = self.out_dir / "kpp"
        self._explicit_staging = (
            None if staging_dir is None else _as_path(staging_dir)
        )
        self._token = token
        self._cancel_callback = cancel_callback
        self._progress = progress
        self._total = total
        self._make_standalone = bool(make_standalone)

        self._staging: Path | None = None
        self._manifest_path: Path | None = None
        self._items: list[TransactionItem] = []
        # 暂存路径一确定就登记（写盘前），这样磁盘半写失败也能被清理阶段识别。
        self._staged_paths: set[Path] = set()
        self._created_dirs: list[Path] = []
        self._created_files: list[TransactionItem] = []
        self._blocked: list[dict[str, str]] = []
        self._rolled_back: list[str] = []
        self._retained: list[str] = []
        self._warnings: list[str] = []
        self._bundle_verify: BundleVerifyResult | None = None

        self._phase = "prepare"
        self._status = ""
        self._cancelled = False
        self._cancel_reason = ""
        self._cancel_requested_late = False
        self._commit_started = False
        self._finished = False

    # ---- 只读状态 ----

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def status(self) -> str:
        return self._status

    @property
    def commit_started(self) -> bool:
        return self._commit_started

    @property
    def cancellable(self) -> bool:
        """最终交付开始后为 False；界面应据此明确标注“不可取消”。"""
        return not self._commit_started and not self._finished

    @property
    def items(self) -> tuple[TransactionItem, ...]:
        return tuple(self._items)

    # ---- 取消检查点 ----

    def _cancel_requested(self) -> bool:
        if self._token is not None and self._token.cancelled:
            return True
        if self._cancel_callback is not None:
            return bool(self._cancel_callback())
        return False

    def _cancel_reason_text(self) -> str:
        if self._token is not None and self._token.cancelled:
            return self._token.reason or "用户取消"
        return "取消回调返回 True"

    def checkpoint(self) -> None:
        """协作式取消检查点：只在允许取消的阶段抛异常。

        最终交付阶段是“故意不可取消”的：这里到达的取消只记录
        cancel_requested_late，不打断已经开始的放置动作，避免把用户
        从“没有半成品”推进到“半个批次”。
        """
        if self._commit_started:
            if self._cancel_requested():
                self._cancel_requested_late = True
            return
        if self._cancel_requested():
            self._cancelled = True
            self._cancel_reason = self._cancel_reason_text()
            self._phase = CANCELLED_PHASE
            raise ConversionCancelled(self._cancel_reason)

    # ---- 暂存目录与目录创建 ----

    def _ensure_dir(self, path: Path) -> None:
        """创建目录并记录“本次创建”，供取消/失败时只清空目录。"""
        if path.exists():
            if not path.is_dir():
                raise ExportTransactionError(f"输出路径不是目录：{path}", phase=self._phase)
            return
        missing: list[Path] = []
        probe = path
        while not probe.exists() and probe != probe.parent:
            missing.append(probe)
            probe = probe.parent
        path.mkdir(parents=True, exist_ok=True)
        for created in reversed(missing):
            if created.exists():
                self._created_dirs.append(created)

    def _pick_staging_dir(self) -> Path:
        if self._explicit_staging is not None:
            staging = self._explicit_staging
            if staging.exists():
                raise ExportTransactionError(
                    f"暂存目录已存在，拒绝复用他人路径：{staging}", phase=self._phase)
            if not staging.parent.exists():
                raise ExportTransactionError(
                    f"暂存目录的父目录不存在：{staging.parent}", phase=self._phase)
            if not _same_volume(staging.parent, self.out_dir):
                raise ExportTransactionError(
                    "暂存目录与目标目录不在同一卷，无法保证同卷交付；"
                    f"暂存={staging.parent}，目标={self.out_dir}", phase=self._phase)
            staging.mkdir()
            return staging
        # 默认：目标目录内部的本次专属目录。同卷由“同目录”直接保证。
        for _ in range(16):
            candidate = self.out_dir / f"{STAGING_PREFIX}{secrets.token_hex(6)}"
            if not candidate.exists():
                candidate.mkdir()
                return candidate
        raise ExportTransactionError("无法创建本次专属暂存目录（名字持续冲突）", phase=self._phase)

    def begin(self) -> None:
        """准备输出目录、独立 kpp 目录与本次专属暂存目录。"""
        if self._staging is not None:
            raise RuntimeError("begin() 只能调用一次")
        self.checkpoint()
        self._phase = "prepare"
        self._ensure_dir(self.out_dir)
        if self._make_standalone:
            self._ensure_dir(self.kpp_dir)
        stale = sorted(
            entry.name for entry in self.out_dir.glob(f"{STAGING_PREFIX}*")
            if entry.is_dir()
        )
        if stale:
            # 只登记、不清理：无法证明这些目录是本次创建，可能属于上次中断的事务。
            self._warnings.append(
                "检测到历史暂存目录（可能是上次异常中断留下），本次不清理："
                + "、".join(stale))
        self._staging = self._pick_staging_dir()
        self._created_dirs.append(self._staging)
        self._manifest_path = self._staging / "manifest.json"
        self._phase = "staging"
        self._write_manifest()

    def _write_manifest(self) -> None:
        """把事务清单写进暂存目录（尽力而为，失败不阻断交付）。"""
        if self._staging is None or self._manifest_path is None:
            return
        payload = {
            "format": "brushconverter.export-transaction",
            "version": 1,
            "out_dir": str(self.out_dir),
            "staging_dir": str(self._staging),
            "cancellable": self.cancellable,
            "items": [item.to_dict() for item in self._items],
        }
        try:
            _write_bytes(
                self._manifest_path,
                json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        except OSError as exc:
            self._warnings.append(f"事务清单写入失败（不影响产物）：{exc}")

    # ---- 暂存单个产物 ----

    @staticmethod
    def _occupied(path: Path) -> bool:
        return path.exists() or path.is_symlink()

    def _resolve_kpp_target(self, name: str, data: bytes) -> tuple[Path, str, Path | None]:
        """决定独立 kpp 的最终路径：同内容复用 / 不同内容顺延，绝不覆盖。"""
        base = self.kpp_dir / name
        for index in range(1000):
            if index == 0:
                candidate = base
            else:
                candidate = base.with_name(f"{base.stem}_{index + 1}{base.suffix}")
            if not self._occupied(candidate):
                return candidate, "new", None
            if candidate.is_dir():
                continue  # 目录占位不能当文件复用，顺延
            try:
                existing = _read_bytes(candidate)
            except OSError:
                continue  # 读不出就不能判同内容，顺延更安全
            if existing == data:
                return candidate, "reused", candidate
        raise ExportTransactionError(
            f"同名独立 kpp 过多，无法为 {name} 找到安全文件名", phase=self._phase)

    def _resolve_bundle_target(
        self, base_name: str, identity: BundleIdentity
    ) -> tuple[Path, str, Path | None, BundleVerifyResult | None]:
        """决定 bundle 的最终路径：同完整逻辑身份且回读通过才复用。

        任务182补充纠偏：v2 身份必须比较完整 64 位 sha256，不能只比 8 位
        hash8；短哈希碰撞时宁可顺延 _2，也绝不把另一份内容的旧包当同包。
        legacy 构建仍按旧 kpp-only hash8 比较，保持 175 及更早的兼容口径。
        """
        base = self.out_dir / base_name
        for index in range(1000):
            if index == 0:
                candidate = base
            else:
                candidate = base.with_name(f"{base.stem}_{index + 1}{base.suffix}")
            if not self._occupied(candidate):
                return candidate, "new", None, None
            if candidate.is_dir():
                continue
            actual = bundle_identity_of_file(candidate)
            if not bundle_identity_matches(actual, identity):
                continue
            verified = verify_bundle(candidate)
            if verified.ok:
                return candidate, "reused", candidate, verified
        raise ExportTransactionError(
            f"同名资源包过多，无法为 {base_name} 找到安全文件名", phase=self._phase)

    def stage_kpp(self, name: str, data: bytes) -> TransactionItem:
        """暂存一个独立 .kpp：先判重名，再写暂存并回读校验，不触碰最终目录。"""
        if self._staging is None:
            raise RuntimeError("必须先调用 begin()")
        if self._phase not in ("staging", "verify"):
            raise RuntimeError(f"当前阶段 {self._phase} 不允许暂存")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("kpp 数据必须是字节")
        blob = bytes(data)
        self.checkpoint()
        final, resolution, _existing = self._resolve_kpp_target(name, blob)
        item = TransactionItem(
            kind="kpp",
            logical_name=name,
            final_name=final.name,
            final_path=str(final),
            sha256=_sha256_bytes(blob),
            size=len(blob),
        )
        if final.name != name:
            item.renamed_from = name
        if resolution == "reused":
            # 复用不写盘，清单里记录**现有文件**的真实哈希/大小，而不是内存字节。
            item.sha256 = _sha256_file(final)
            item.size = final.stat().st_size
            item.state = STATE_REUSED
            self._items.append(item)
            self._write_manifest()
            self.checkpoint()
            return item

        staged = self._staging / "kpp" / final.name
        self._staged_paths.add(staged)
        _write_bytes(staged, blob)
        readback = _read_bytes(staged)
        if readback != blob:
            raise ExportTransactionError(
                f"暂存回读不一致：{staged.name}（期望 {len(blob)}B，"
                f"实际 {len(readback)}B）", phase=self._phase)
        try:
            read_preset_xml(readback)
        except Exception as exc:  # noqa: BLE001 - 结构错误统一转事务错误
            raise ExportTransactionError(
                f"暂存 kpp 结构校验失败：{staged.name}：{exc}",
                phase=self._phase, cause=exc) from exc
        item.staged_path = str(staged)
        item.state = STATE_VERIFIED
        self._items.append(item)
        self._write_manifest()
        self.checkpoint()
        return item

    def stage_bundle(
        self, base_name: str, data: bytes, content_hash8: str, *,
        identity: BundleIdentity,
    ) -> TransactionItem:
        """暂存 .bundle：复用前必须完整身份一致且回读通过；新写则整包校验。

        identity 是本次构建的完整身份（v2 含 64 位 sha256，legacy 含旧 hash8）；
        content_hash8 只保留给既有调用签名，必须与 identity.hash8 一致。
        """
        if self._staging is None:
            raise RuntimeError("必须先调用 begin()")
        if self._phase not in ("staging", "verify"):
            raise RuntimeError(f"当前阶段 {self._phase} 不允许暂存")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("bundle 数据必须是字节")
        if not isinstance(identity, BundleIdentity):
            raise TypeError("identity 必须是 BundleIdentity")
        if identity.hash8 != content_hash8:
            raise ValueError(
                "content_hash8 与完整身份不一致："
                f"参数 {content_hash8}，身份 {identity.hash8}")
        blob = bytes(data)
        self.checkpoint()
        final, resolution, _existing, existing_verify = self._resolve_bundle_target(
            base_name, identity)
        item = TransactionItem(
            kind="bundle",
            logical_name=base_name,
            final_name=final.name,
            final_path=str(final),
            sha256=_sha256_bytes(blob),
            size=len(blob),
        )
        if final.name != base_name:
            item.renamed_from = base_name
        if resolution == "reused":
            # bundle 复用要求“完整逻辑身份”相同（v2 为 64 位 sha256）；
            # zip 字节可能因压缩细节不同，所以清单记录现有文件的真实哈希，
            # 不能拿本次内存字节冒充。
            item.sha256 = _sha256_file(final)
            item.size = final.stat().st_size
            item.state = STATE_REUSED
            self._bundle_verify = existing_verify
            self._items.append(item)
            self._write_manifest()
            self.checkpoint()
            return item

        staged = self._staging / "bundle" / final.name
        self._staged_paths.add(staged)
        _write_bytes(staged, blob)
        try:
            readback = _read_bytes(staged)
        except OSError as exc:
            raise ExportTransactionError(
                f"暂存回读失败：{staged.name}：{exc}", phase=self._phase, cause=exc) from exc
        if readback != blob:
            raise ExportTransactionError(
                f"暂存回读不一致：{staged.name}（期望 {len(blob)}B，"
                f"实际 {len(readback)}B）", phase=self._phase)
        verified = verify_bundle(staged)
        if not verified.ok:
            raise ExportTransactionError(
                "暂存资源包回读校验失败：" + "；".join(verified.errors),
                phase=self._phase)
        item.staged_path = str(staged)
        item.state = STATE_VERIFIED
        self._items.append(item)
        self._write_manifest()
        self.checkpoint()
        return item

    # ---- 最终交付（不可取消） ----

    def _notify_write(self, message: str) -> None:
        if self._progress is None:
            return
        notify_progress(
            self._progress,
            ProgressEvent(
                phase=PHASE_WRITE,
                total=self._total,
                completed=self._total,
                message=message,
            ),
        )

    def _verify_placed(self, item: TransactionItem, final: Path) -> None:
        """最终路径再回读一次：字节必须与暂存一致，结构必须能解析。"""
        actual = _read_bytes(final)
        if _sha256_bytes(actual) != item.sha256:
            raise ExportTransactionError(
                f"交付后回读哈希不一致：{final}", phase="commit")
        if item.kind == "kpp":
            try:
                read_preset_xml(actual)
            except Exception as exc:  # noqa: BLE001
                raise ExportTransactionError(
                    f"交付后 kpp 结构校验失败：{final}：{exc}",
                    phase="commit", cause=exc) from exc
        else:
            verified = verify_bundle(final)
            if not verified.ok:
                raise ExportTransactionError(
                    "交付后资源包校验失败：" + "；".join(verified.errors),
                    phase="commit")
            self._bundle_verify = verified

    def _place_item(self, item: TransactionItem) -> None:
        final = Path(item.final_path)
        if self._occupied(final):
            # 交付阶段出现了同名文件：只允许“恰好同内容”的复用，绝不覆盖。
            if final.is_file() and _sha256_file(final) == item.sha256:
                item.state = STATE_REUSED
                return
            raise FileExistsError(f"交付阶段出现同名文件，拒绝覆盖：{final}")
        _place_file(Path(item.staged_path), final)
        # 先登记“本次新建”，再做交付后回读：若回读失败，回滚清单里必须有它。
        self._created_files.append(item)
        self._verify_placed(item, final)
        item.state = STATE_COMMITTED

    def _remove_committed_if_ours(self, item: TransactionItem) -> bool:
        """只删除“本次新建且内容仍与写入时一致”的文件；否则保留现场。"""
        final = Path(item.final_path)
        if not final.exists():
            return True
        if final.is_symlink() or final.is_dir():
            # 符号链接/目录不是本次创建的文件，绝不代为删除。
            return False
        try:
            if _sha256_file(final) != item.sha256:
                return False
        except OSError:
            return False
        _remove_file(final)
        return True

    def commit(self) -> TransactionSummary:
        """进入短暂、不可取消的最终交付阶段；失败按新建清单回滚。"""
        if self._finished:
            # 已取消/已失败的事务不能再“补交”：直接返回原清单，由调用方显示原因。
            return self.summary()
        if self._staging is None or self._phase not in ("staging", "verify"):
            raise RuntimeError("commit() 必须在 begin() 之后、且尚未交付时调用")
        self._phase = "verify"
        # 最后一个允许取消的检查点：过了这里就不再响应取消。
        self.checkpoint()
        self._notify_write("最终交付阶段：不可取消，正在放置本批产物…")
        self._phase = "commit"
        self._commit_started = True

        for item in [entry for entry in self._items if entry.state == STATE_VERIFIED]:
            try:
                self._place_item(item)
                self._write_manifest()
                # 交付阶段仍会观察取消请求，但只记录、不中断（显式不可取消）。
                self.checkpoint()
            except Exception as exc:  # noqa: BLE001 - 统一回滚并报告
                self._phase = "failed"
                self._blocked.append({
                    "name": item.final_name,
                    "reason": f"{type(exc).__name__}: {exc}",
                })
                self._rollback_after_failure()
                self._finish_cleanup()
                rollback_failed = self._status == RESULT_STATUS_PARTIAL_BLOCKED
                message = (
                    "最终交付中途失败；已按本次新建清单回滚"
                    if not rollback_failed
                    else "最终交付中途失败；部分文件无法可靠回滚，已保留现场"
                )
                message += f"（{item.final_name}：{exc}）"
                blocked_names = "、".join(entry["name"] for entry in self._blocked)
                if blocked_names:
                    message += f"；受阻清单：{blocked_names}"
                if self._retained:
                    message += "；保留现场：" + "、".join(self._retained)
                raise ExportTransactionError(
                    message, phase="commit", summary=self.summary(), cause=exc) from exc

        counts = self.counts_for_status()
        if counts["new"]:
            self._status = RESULT_STATUS_COMMITTED
        elif counts["reused"]:
            self._status = RESULT_STATUS_REUSED_ONLY
        else:
            self._status = RESULT_STATUS_COMMITTED
        self._phase = "done"
        self._finished = True
        self._finish_cleanup()
        return self.summary()

    def _rollback_after_failure(self) -> None:
        """按“本次新建”清单倒序删除；删不掉的保留并列入受阻清单。"""
        for item in reversed(self._created_files):
            try:
                removed = self._remove_committed_if_ours(item)
            except OSError as exc:  # noqa: BLE001 - 保留现场，不吞掉
                removed = False
                self._warnings.append(f"回滚删除失败：{item.final_path}：{exc}")
            if removed:
                item.state = STATE_ROLLED_BACK
                self._rolled_back.append(item.final_name)
            else:
                item.state = STATE_BLOCKED
                self._blocked.append({
                    "name": item.final_name,
                    "reason": "回滚时文件内容已变化或无法删除；保留现场，未强行清理",
                })
                self._retained.append(item.final_path)
        self._created_files = []
        self._status = (
            RESULT_STATUS_PARTIAL_BLOCKED if self._retained else RESULT_STATUS_ROLLED_BACK
        )

    # ---- 取消 / 异常收尾 ----

    def cancel(self, reason: str = "") -> TransactionSummary:
        """在最终交付前取消：清理暂存，目标目录不出现本批文件。"""
        if self._commit_started:
            self._cancel_requested_late = True
            return self.summary()
        self._cancelled = True
        self._cancel_reason = (reason or "").strip() or "用户取消"
        self._phase = CANCELLED_PHASE
        self._status = RESULT_STATUS_CANCELLED
        self._finished = True
        self._finish_cleanup()
        return self.summary()

    def fail(self, exc: BaseException) -> TransactionSummary:
        """暂存/验证阶段异常：清理暂存；若已进入交付则交给 commit 的失败路径。"""
        if self._commit_started:
            return self.summary()
        self._phase = "failed"
        self._status = RESULT_STATUS_FAILED
        self._finished = True
        self._warnings.append(f"{type(exc).__name__}: {exc}")
        self._finish_cleanup()
        return self.summary()

    # ---- 清理：只删本次创建、且绝对路径核对通过的内容 ----

    def _finish_cleanup(self) -> None:
        if self._staging is not None:
            self._cleanup_staging()
        # 只清理本次创建且已空的目录（例如取消时刚创建、还没放产物的 out/kpp）。
        for directory in reversed(self._created_dirs):
            if directory == self._staging:
                continue
            try:
                if directory.exists() and directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
            except OSError:
                self._retained.append(str(directory))

    def _cleanup_staging(self) -> None:
        staging = self._staging
        if staging is None or not staging.exists():
            return
        known: set[Path] = set(self._staged_paths)
        for item in self._items:
            if item.staged_path:
                known.add(Path(item.staged_path))
        if self._manifest_path is not None:
            known.add(self._manifest_path)
        for path in sorted(known, key=lambda value: len(str(value)), reverse=True):
            try:
                if path.exists() and _is_inside(path, staging):
                    _remove_file(path)
            except OSError as exc:  # noqa: BLE001
                self._retained.append(str(path))
                self._warnings.append(f"暂存文件清理失败：{path}：{exc}")
        # 只删空目录；未知文件不删，整个暂存目录保留并列进现场。
        for path in sorted(staging.rglob("*"), key=lambda value: len(str(value)), reverse=True):
            if path.is_dir():
                try:
                    if not any(path.iterdir()):
                        path.rmdir()
                except OSError:
                    pass
        try:
            if not any(staging.iterdir()):
                staging.rmdir()
            else:
                leftovers = [str(entry) for entry in staging.iterdir()]
                self._retained.extend(leftovers)
                self._warnings.append(
                    "暂存目录含非本次创建或未能删除的内容，已保留现场：" + "；".join(leftovers))
        except OSError as exc:  # noqa: BLE001
            self._retained.append(str(staging))
            self._warnings.append(f"暂存目录清理失败：{staging}：{exc}")

    # ---- 汇总 ----

    def counts_for_status(self) -> dict[str, int]:
        return {
            "new": sum(1 for item in self._items if item.state == STATE_COMMITTED),
            "reused": sum(1 for item in self._items if item.state == STATE_REUSED),
            "renamed": sum(1 for item in self._items if item.renamed_from),
        }

    def summary(self) -> TransactionSummary:
        status = self._status
        if not status:
            status = RESULT_STATUS_COMMITTED if self._finished else self._phase
        return TransactionSummary(
            out_dir=str(self.out_dir),
            staging_dir="" if self._staging is None else str(self._staging),
            status=status,
            phase=self._phase,
            cancellable=self.cancellable,
            cancel_requested=self._cancelled,
            cancel_requested_late=self._cancel_requested_late,
            cancel_reason=self._cancel_reason,
            items=list(self._items),
            blocked=list(self._blocked),
            rolled_back=list(self._rolled_back),
            retained=list(self._retained),
            created_dirs=[str(path) for path in self._created_dirs],
            warnings=list(self._warnings),
            bundle_verify=self._bundle_verify,
            manifest_path="" if self._manifest_path is None else str(self._manifest_path),
        )
