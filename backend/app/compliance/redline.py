"""Local redline rules. The default set is code, not a row that can be disabled."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

SAFE_REWRITE = "这个问题我记下来，让同事回电给您。"
_JUDGE_TIMEOUT_MS = 30.0
_NEGATION = {"不会", "不是", "没有", "并未", "并非"}
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("promise_return", re.compile(r"保证收益|稳赚|百分百|保本")),
    ("fake_authority", re.compile(r"我是(银行|公安|警察|法院|政府)")),
    ("loan_inducement", re.compile(r"无抵押秒批|保证下款|内部渠道放款")),
    ("abuse", re.compile(r"傻逼|滚开|神经病")),
    (
        "pii_disclosure",
        re.compile(r"身份证号|身份证号码|银行卡号|银行卡完整号|完整卡号|(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)"),
    ),
)
_JUDGE_CUE = re.compile(r"承诺|收益|价格|费用|收费|疗效|通过率|我是|银行|公安|警察|法院|政府|身份|元|块")


@dataclass(frozen=True)
class GuardDecision:
    text: str
    hits: list[str]
    action: str
    timed_out: bool


def _negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 2) : start]
    if prefix[-1:] in {"不", "没", "未"}:
        return True
    return prefix in _NEGATION


def scan_redline(text: str) -> list[str]:
    body = text or ""
    hits: list[str] = []
    for code, pattern in _RULES:
        if any(not _negated(body, found.start()) for found in pattern.finditer(body)):
            hits.append(code)
    return hits


def _from_hits(text: str, hits: list[str], prior_hits: int, *, timed_out: bool) -> GuardDecision:
    total = prior_hits + len(hits)
    action = "hangup" if total >= 2 else "rewrite"
    return GuardDecision(SAFE_REWRITE, hits, action, timed_out)


def review_assistant_text(
    text: str,
    *,
    prior_hits: int,
    judge: Callable[[str], list[str]] | None = None,
    clock: Callable[[], float] | None = None,
) -> GuardDecision:
    """Local hits block before any model. A slow second pass is allowed and marked."""
    hits = scan_redline(text)
    if hits:
        return _from_hits(text, hits, prior_hits, timed_out=False)
    if judge is None or _JUDGE_CUE.search(text or "") is None:
        return GuardDecision(text, [], "allow", False)
    tick = clock or time.perf_counter
    started = tick()
    try:
        judged = [code for code in judge(text) if isinstance(code, str) and code]
    except Exception:
        judged = []
    if (tick() - started) * 1000 > _JUDGE_TIMEOUT_MS:
        return GuardDecision(text, [], "allow", True)
    if not judged:
        return GuardDecision(text, [], "allow", False)
    return _from_hits(text, judged, prior_hits, timed_out=False)


def guard_assistant_text(text: str, *, prior_hits: int) -> tuple[str, list[str], str]:
    """Return (spoken_text, hit_codes, action). action is allow, rewrite, or hangup."""
    decision = review_assistant_text(text, prior_hits=prior_hits)
    return decision.text, decision.hits, decision.action
