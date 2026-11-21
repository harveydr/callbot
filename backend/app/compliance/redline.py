"""Local redline rules. The default set is code, not a row that can be disabled."""

from __future__ import annotations

import re

SAFE_REWRITE = "这个问题我记下来，让同事回电给您。"

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("promise_return", re.compile(r"保证收益|稳赚|百分百|保本")),
    ("fake_authority", re.compile(r"我是(银行|公安|警察|法院|政府)")),
    ("loan_inducement", re.compile(r"无抵押秒批|保证下款|内部渠道放款")),
    ("abuse", re.compile(r"傻逼|滚开|神经病")),
)


def scan_redline(text: str) -> list[str]:
    return [code for code, pattern in _RULES if pattern.search(text or "")]


def guard_assistant_text(text: str, *, prior_hits: int) -> tuple[str, list[str], str]:
    """Return (spoken_text, hit_codes, action). action is allow, rewrite, or hangup."""
    hits = scan_redline(text)
    if not hits:
        return text, [], "allow"
    total = prior_hits + len(hits)
    if total >= 2:
        return SAFE_REWRITE, hits, "hangup"
    return SAFE_REWRITE, hits, "rewrite"
