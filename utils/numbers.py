"""Shared helpers: numbers."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def money(x: float) -> float:
    return float(Decimal(repr(float(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
