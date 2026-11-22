"""Intent gate for the local fallback classifier. Recall on this set must stay at or above 0.90."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.voice.intent import classify_intent  # noqa: E402

CASES: tuple[tuple[str, str], ...] = (
    ("", "invalid"),
    ("   ", "invalid"),
    ("现在方便", "agree"),
    ("可以的", "agree"),
    ("好的，你说", "agree"),
    ("我同意", "agree"),
    ("没问题", "agree"),
    ("不需要", "refuse"),
    ("别再打了", "refuse"),
    ("不要再打", "refuse"),
    ("请不要再联系我", "refuse"),
    ("别打了谢谢", "refuse"),
    ("你们是哪家公司", "inquiry"),
    ("什么事情", "inquiry"),
    ("请再说一遍", "inquiry"),
    ("我在开会", "inquiry"),
    ("您拨打的用户暂时无法接通，请在嘀声后留言", "voicemail"),
    ("语音信箱已满", "voicemail"),
    ("方便明天再联系吗", "agree"),
    ("可以，但我只有一分钟", "agree"),
)


def main() -> None:
    matched = sum(1 for text, label in CASES if classify_intent(text) == label)
    score = matched / len(CASES)
    print(f"intent accuracy {score:.3f} ({matched}/{len(CASES)})")
    if score < 0.90:
        raise SystemExit("intent accuracy below 0.90")


if __name__ == "__main__":
    main()
