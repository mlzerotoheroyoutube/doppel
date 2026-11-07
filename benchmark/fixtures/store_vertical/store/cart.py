"""A tiny shopping cart. All money is integer cents.

Rules (the contract both humans and agents are held to):

* Subtotal is the sum of price_cents * qty over every line in the cart.
* Adding a SKU that is already in the cart increases that line's qty.
* At most one coupon may be applied to a cart. Applying a second coupon
  raises CouponError.
* A percent coupon takes that percent off the whole subtotal, rounded down
  to the cent.
* A fixed coupon takes a flat amount off, but only when the subtotal is at
  least the coupon's minimum. The discount can never exceed the subtotal.
* Shipping is free when the discounted subtotal is $50.00 or more; otherwise
  it is a flat $5.99. An empty cart has no shipping charge.
* Tax is 8% of the discounted subtotal, rounded half up to the cent.
  Shipping is not taxed.
* total = discounted subtotal + shipping + tax, and is never negative.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .coupons import COUPONS, Coupon, CouponError

FREE_SHIPPING_THRESHOLD_CENTS = 5000
SHIPPING_CENTS = 599
TAX_RATE_PERCENT = 8


@dataclass
class Item:
    sku: str
    name: str
    price_cents: int
    qty: int = 1


@dataclass
class Cart:
    items: list[Item] = field(default_factory=list)
    coupon: Coupon | None = None

    def add(self, item: Item) -> None:
        """Add an item. Adding an existing SKU increases its quantity."""
        if item.qty <= 0:
            raise ValueError("qty must be positive")
        for line in self.items:
            if line.sku == item.sku:
                line.qty += item.qty
                return
        self.items.append(Item(item.sku, item.name, item.price_cents, item.qty))

    def remove(self, sku: str, qty: int | None = None) -> None:
        """Remove a SKU. With no qty, remove the whole line; with a qty,
        remove that many units. Raises KeyError if the SKU is missing or
        there are fewer than qty units."""
        for i, line in enumerate(self.items):
            if line.sku == sku:
                if qty is None or qty == line.qty:
                    del self.items[i]
                    return
                if qty > line.qty or qty <= 0:
                    raise KeyError(sku)
                line.qty -= qty
                return
        raise KeyError(sku)

    def subtotal_cents(self) -> int:
        """Sum of price_cents * qty for every line."""
        return sum(line.price_cents * line.qty for line in self.items)

    def apply_coupon(self, code: str) -> None:
        """Apply a coupon by code. Raises CouponError if unknown or if a
        coupon is already applied."""
        if self.coupon is not None:
            raise CouponError("only one coupon per cart")
        if code not in COUPONS:
            raise CouponError(f"unknown coupon {code!r}")
        self.coupon = COUPONS[code]

    def discount_cents(self) -> int:
        """Discount from the applied coupon, in cents (0 if none)."""
        if self.coupon is None or not self.items:
            return 0
        if self.coupon.kind == "percent":
            return self.subtotal_cents() * self.coupon.amount // 100
        subtotal = self.subtotal_cents()
        if subtotal < self.coupon.min_subtotal_cents:
            return 0
        return min(self.coupon.amount, subtotal)

    def shipping_cents(self) -> int:
        """Flat shipping under the free-shipping threshold."""
        if not self.items:
            return 0
        if self.subtotal_cents() - self.discount_cents() >= FREE_SHIPPING_THRESHOLD_CENTS:
            return 0
        return SHIPPING_CENTS

    def tax_cents(self) -> int:
        """8% of the discounted subtotal, rounded half up."""
        taxable = self.subtotal_cents() - self.discount_cents()
        return (taxable * TAX_RATE_PERCENT + 50) // 100

    def total_cents(self) -> int:
        """Discounted subtotal + shipping + tax. Never negative."""
        discounted = self.subtotal_cents() - self.discount_cents()
        return max(0, discounted + self.shipping_cents() + self.tax_cents())
