"""API errors. The HTTP handler turns these into the standard envelope."""

from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, data: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.data = data or {}
