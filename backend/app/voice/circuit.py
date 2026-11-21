"""Vendor circuit. A 429 opens it; later audio uses the local fallback."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VendorCircuit:
    open: bool = False
    reason: str = ""

    def observe(self, status: int) -> None:
        if status == 429:
            self.open = True
            self.reason = "vendor_429"

    def allow_vendor(self) -> bool:
        return not self.open
