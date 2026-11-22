"""Model output still passes the code redline. A model cannot disable it."""

from app.compliance.redline import guard_assistant_text

__all__ = ["guard_assistant_text"]
