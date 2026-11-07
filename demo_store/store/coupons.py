"""Coupon catalog."""

from __future__ import annotations

from dataclasses import dataclass


class CouponError(Exception):
    """Raised for unknown coupons or when a cart already has a coupon."""


@dataclass(frozen=True)
class Coupon:
    code: str
    kind: str  # "percent" or "fixed"
    amount: int  # percent (0-100) for percent coupons, cents for fixed coupons
    min_subtotal_cents: int = 0


COUPONS: dict[str, Coupon] = {
    "SAVE10": Coupon("SAVE10", "percent", 10),
    "HALFOFF": Coupon("HALFOFF", "percent", 50),
    "FIVEOFF": Coupon("FIVEOFF", "fixed", 500, min_subtotal_cents=2000),
    "TENOFF": Coupon("TENOFF", "fixed", 1000),
}
