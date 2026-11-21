"""Static go-live checks that can run without a carrier trunk."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.compliance.opening import render_opening  # noqa: E402
from app.compliance.redline import guard_assistant_text  # noqa: E402


def main() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    if "FEATURE_TELESALES=false" not in example or "FEATURE_COLLECTION=false" not in example:
        raise SystemExit("telesales and collection must default off")
    opening = render_opening(company="样例", purpose="通知")
    if "人工智能" not in opening or "录音" not in opening:
        raise SystemExit("opening is incomplete")
    _text, _hits, action = guard_assistant_text("我们保证收益。", prior_hits=1)
    if action != "hangup":
        raise SystemExit("redline did not hang up on the second hit")
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "backend" / "app").rglob("*.py"))
    if "skip_compliance" not in source:
        raise SystemExit("forbidden compliance switch is not rejected")
    if "def render_opening" not in (ROOT / "backend" / "app" / "compliance" / "opening.py").read_text(encoding="utf-8"):
        raise SystemExit("opening template missing")
    print("go-live static checks passed")
    print("manual gates still required: real trunk smoke, backup restore drill, legal review of section 12")


if __name__ == "__main__":
    main()
