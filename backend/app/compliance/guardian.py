"""A minor contact needs an approved guardian consent and a mandatory verification line."""

from __future__ import annotations


def guardian_line(node_key: str, node_type: str, content: dict[str, object]) -> str | None:
    """Return the spoken guardian check, when this node is that branch."""
    if node_type != "mandatory":
        return None
    marked = content.get("guardian_check") is True or node_key == "n_guardian"
    if not marked:
        return None
    text = content.get("text")
    if not isinstance(text, str) or not text.strip():
        must_say = content.get("must_say")
        text = must_say if isinstance(must_say, str) else ""
    cleaned = text.strip()
    return cleaned or None


def minor_marked(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "y", "是"}
