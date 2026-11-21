"""Intent prompt. Sampling and the compliance line are fixed in code."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_PROMPTS = Path(__file__).resolve().parent / "prompts"
_CONTRACT = "不得承诺未授权内容、不得隐瞒 AI 身份、不得索取身份证号/银行卡完整号。"
_BUDGET = 6000
_HISTORY_TURNS = 8

_ENV = Environment(
    loader=FileSystemLoader(_PROMPTS),
    autoescape=select_autoescape(enabled_extensions=()),
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class Sample:
    temperature: float
    top_p: float
    max_tokens: int


FIRST_SAMPLE = Sample(0.3, 0.8, 150)
RETRY_SAMPLE = Sample(0.0, 0.8, 150)


def sample_for_attempt(index: int) -> Sample:
    """The first intent call uses 0.3. The one retry uses 0."""
    if index <= 0:
        return FIRST_SAMPLE
    return RETRY_SAMPLE


def clip_history(turns: list[str]) -> tuple[str, list[str]]:
    """Keep the latest eight turns. Older lines stay only as a short preface."""
    cleaned = [turn.strip() for turn in turns if turn.strip()]
    if len(cleaned) <= _HISTORY_TURNS:
        return "", cleaned
    older = "；".join(cleaned[:-_HISTORY_TURNS])
    return f"前文摘要：{older}", cleaned[-_HISTORY_TURNS:]


def render_intent_prompt(
    *,
    last_user_text: str,
    node_id: str,
    asr_confidence: str,
    history: list[str],
) -> str:
    summary, recent = clip_history(history)
    history_text = _fit_history(summary, recent, last_user_text=last_user_text, node_id=node_id, asr_confidence=asr_confidence)
    body = _ENV.get_template("intent.j2").render(
        last_user_text=last_user_text,
        node_id=node_id or "n_open",
        asr_confidence=asr_confidence,
        history=history_text,
    )
    return f"{body.rstrip()}\n{_CONTRACT}"


def _fit_history(
    summary: str,
    recent: list[str],
    *,
    last_user_text: str,
    node_id: str,
    asr_confidence: str,
) -> str:
    """Drop preface text until the rendered prompt stays inside the budget."""
    kept_summary = summary
    kept_recent = list(recent)
    while True:
        history_text = _history_text(kept_summary, kept_recent)
        prompt = _ENV.get_template("intent.j2").render(
            last_user_text=last_user_text,
            node_id=node_id or "n_open",
            asr_confidence=asr_confidence,
            history=history_text,
        )
        if len(f"{prompt.rstrip()}\n{_CONTRACT}") <= _BUDGET:
            return history_text
        if kept_summary:
            kept_summary = kept_summary[: max(0, len(kept_summary) // 2)]
            if len(kept_summary) < len("前文摘要："):
                kept_summary = ""
            continue
        if kept_recent:
            kept_recent.pop(0)
            continue
        return ""


def _history_text(summary: str, recent: list[str]) -> str:
    lines = [line for line in (summary, *recent) if line]
    return "\n".join(lines)
