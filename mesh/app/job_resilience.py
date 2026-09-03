"""Pipeline / Preview 容错：错误分类 → 有界重试 → 局部降级 → 可追踪状态。

产品约定：
- 可重试错误自动重试（≤ max_retries，默认 2）
- 按 source / 按任务隔离，单点失败不拖垮整期
- 最终态：ok | degraded | failed；全程留下原因与次数
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# 用户约定：自动重试最多 2 次（不含首次）
DEFAULT_MAX_RETRIES = 2

Outcome = str  # ok | skipped | failed
FinalStatus = str  # ok | degraded | failed


@dataclass
class AttemptRecord:
    unit: str
    kind: str  # source | card | draft | other
    attempt: int  # 1-based within this unit
    outcome: Outcome
    error: str = ""
    error_class: str = ""  # retryable | fatal | unknown
    action: str = ""  # retry | skip | fail | ok


@dataclass
class ResilienceReport:
    """写入 mesh_jobs.payload，便于控制台/日志追踪。"""

    max_retries: int = DEFAULT_MAX_RETRIES
    attempts: list[AttemptRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed_units: list[str] = field(default_factory=list)
    retry_total: int = 0

    def record(self, rec: AttemptRecord) -> None:
        self.attempts.append(rec)
        if rec.action == "retry":
            self.retry_total += 1
        if rec.outcome == "skipped" and rec.unit not in self.skipped:
            self.skipped.append(rec.unit)
        if rec.outcome == "failed" and rec.unit not in self.failed_units:
            self.failed_units.append(rec.unit)

    def final_status(self, *, ok_units: int, required: bool = True) -> FinalStatus:
        if self.failed_units and (required or ok_units <= 0):
            return "failed"
        if self.skipped or self.retry_total:
            if ok_units <= 0 and required:
                return "failed"
            if self.skipped:
                return "degraded"
        return "ok"

    def summary_message(self, *, ok_units: int, unit_label: str = "项") -> str:
        parts = [f"成功 {ok_units} {unit_label}"]
        if self.retry_total:
            parts.append(f"自动重试 {self.retry_total} 次")
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)}：{'、'.join(self.skipped[:5])}")
        if self.failed_units:
            parts.append(f"失败 {len(self.failed_units)}：{'、'.join(self.failed_units[:5])}")
        return "；".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "retry_total": self.retry_total,
            "skipped": list(self.skipped),
            "failed_units": list(self.failed_units),
            "attempts": [
                {
                    "unit": a.unit,
                    "kind": a.kind,
                    "attempt": a.attempt,
                    "outcome": a.outcome,
                    "error": (a.error or "")[:300],
                    "error_class": a.error_class,
                    "action": a.action,
                }
                for a in self.attempts[-40:]
            ],
        }


def classify_error(exc: BaseException | str | None) -> str:
    """retryable | fatal | unknown"""
    if exc is None:
        return "unknown"
    msg = str(exc)
    low = msg.lower()
    # 业务硬伤：不重试
    fatal_markers = (
        "未配置",
        "没有这一期",
        "请先完成挖掘",
        "已上线",
        "published_preview",
        "权限",
        "forbidden",
        "拆段置信度低",
        "待指定归属",
        "preview_gate_blocked",
    )
    if any(x in msg for x in fatal_markers):
        return "fatal"
    # 瞬时 / 模型抖动
    retryable_markers = (
        "timeout",
        "timed out",
        "排队超时",
        "暂时",
        "temporarily",
        "rate limit",
        "overloaded",
        "json 无法解析",
        "不是合法 json",
        "expecting",
        "delimiter",
        "connection",
        "网络异常",
        " 408",
        " 409",
        " 425",
        " 429",
        " 500",
        " 502",
        " 503",
        " 504",
        " 524",
        "524：",
        "bedrockexception",
        "unavailable",
    )
    if any(x in low for x in retryable_markers) or any(x in msg for x in ("JSON 无法解析", "模型接口返回 5", "模型接口返回 4")):
        return "retryable"
    # JobBusy / AskBusy
    name = type(exc).__name__ if isinstance(exc, BaseException) else ""
    if "Busy" in name or "Timeout" in name or "LLMError" in name:
        return "retryable"
    return "unknown"


def run_with_retries(
    fn: Callable[[], T],
    *,
    unit: str,
    kind: str = "other",
    report: ResilienceReport | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sleep_sec: float = 1.5,
    on_attempt: Callable[[int, BaseException | None], None] | None = None,
) -> T:
    """执行 fn；可重试错误最多再试 max_retries 次。仍失败则抛出最后一次异常。"""
    rep = report or ResilienceReport(max_retries=max_retries)
    last: BaseException | None = None
    attempts = max_retries + 1
    for i in range(1, attempts + 1):
        try:
            if on_attempt:
                on_attempt(i, None)
            out = fn()
            rep.record(
                AttemptRecord(
                    unit=unit,
                    kind=kind,
                    attempt=i,
                    outcome="ok",
                    error_class="",
                    action="ok",
                )
            )
            return out
        except BaseException as e:
            last = e
            cls = classify_error(e)
            will_retry = cls == "retryable" and i < attempts
            rep.record(
                AttemptRecord(
                    unit=unit,
                    kind=kind,
                    attempt=i,
                    outcome="failed" if not will_retry else "ok",
                    error=str(e).replace("\n", " ")[:300],
                    error_class=cls,
                    action="retry" if will_retry else "fail",
                )
            )
            # 修正：重试中的中间失败 outcome 记为 failed 中间态不太好看；用 action=retry 即可
            if will_retry:
                rep.attempts[-1].outcome = "failed"
                if on_attempt:
                    on_attempt(i, e)
                time.sleep(sleep_sec * i)
                continue
            if on_attempt:
                on_attempt(i, e)
            raise
    assert last is not None
    raise last


def try_unit(
    fn: Callable[[], T],
    *,
    unit: str,
    kind: str,
    report: ResilienceReport,
    max_retries: int = DEFAULT_MAX_RETRIES,
    on_skip: Callable[[BaseException], None] | None = None,
    sleep_sec: float = 1.5,
) -> T | None:
    """跑一个可隔离单元：重试耗尽后跳过并记入 report，返回 None。"""
    try:
        return run_with_retries(
            fn,
            unit=unit,
            kind=kind,
            report=report,
            max_retries=max_retries,
            sleep_sec=sleep_sec,
        )
    except BaseException as e:
        # run_with_retries 已记最后一次 fail；再标 skip
        report.record(
            AttemptRecord(
                unit=unit,
                kind=kind,
                attempt=0,
                outcome="skipped",
                error=str(e).replace("\n", " ")[:300],
                error_class=classify_error(e),
                action="skip",
            )
        )
        if on_skip:
            on_skip(e)
        return None
