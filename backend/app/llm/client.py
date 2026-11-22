"""LLM adapter. Tests and local runs stay on the deterministic fallback and do not call a vendor."""

from __future__ import annotations

import os
from typing import Any


class LlmUnavailable(RuntimeError):
    pass


async def complete(prompt: str) -> dict[str, Any]:
    if not os.environ.get("LLM_BASE_URL"):
        return {"intent": "inquiry", "degraded": True, "prompt_chars": len(prompt)}
    raise LlmUnavailable("LLM_UNAVAILABLE")
