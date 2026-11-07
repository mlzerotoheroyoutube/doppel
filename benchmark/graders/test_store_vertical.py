"""Hidden acceptance tests for the vertical receipts feature. Never shown to any agent."""
import random

import pytest

from store import Cart, Item

store = pytest.importorskip("store")


def cart(lines, coupon=None):
    c = Cart()
    for i, (price, qty) in enumerate(lines):
        c.add(Item(f"S{i}", f"Item {i}", price, qty))
    if coupon:
        c.apply_coupon(coupon)
    return c


def test_i1_types_exported():
    assert hasattr(store, "Receipt") and hasattr(store, "ReceiptLine")


def test_i1_worked_example():
    r = cart([(1000, 1), (2000, 1), (3005, 1)], "SAVE10").receipt()
    assert [l.gross_cents for l in r.lines] == [1000, 2000, 3005]
    assert [l.discount_cents for l in r.lines] == [100, 200, 300]
    assert [l.net_cents for l in r.lines] == [900, 1800, 2705]
    assert [l.tax_cents for l in r.lines] == [72, 144, 216]
    assert (r.subtotal_cents, r.discount_cents, r.shipping_cents, r.tax_cents, r.total_cents) == \
        (6005, 600, 0, 432, 5837)


def test_i1_ties_go_to_earlier_line():
    # 3 equal lines, discount 10 -> 3,3,3 + 1 leftover to the first line
    r = cart([(3333, 1), (3333, 1), (3334, 1)], "TENOFF").receipt()
    assert [l.discount_cents for l in r.lines] == [333, 333, 334]
    r = cart([(1000, 1), (1000, 1), (1000, 1)], "TENOFF").receipt()
    assert [l.discount_cents for l in r.lines] == [334, 333, 333]


def test_i1_quantities_and_fields():
    r = cart([(1200, 3), (2500, 1)], "SAVE10").receipt()
    first = r.lines[0]
    assert (first.sku, first.name, first.qty, first.gross_cents) == ("S0", "Item 0", 3, 3600)
    assert sum(l.discount_cents for l in r.lines) == 610


def test_i1_no_coupon_and_empty():
    r = cart([(1999, 2)]).receipt()
    assert r.lines[0].discount_cents == 0 and r.discount_cents == 0
    assert r.shipping_cents == 599
    e = Cart().receipt()
    assert e.lines == [] and (e.subtotal_cents, e.discount_cents, e.shipping_cents, e.tax_cents, e.total_cents) == \
        (0, 0, 0, 0, 0)


@pytest.mark.parametrize("seed", range(40))
def test_i1_always_reconciles(seed):
    rnd = random.Random(seed)
    lines = [(rnd.randint(1, 9000), rnd.randint(1, 4)) for _ in range(rnd.randint(1, 6))]
    c = cart(lines, rnd.choice([None, "SAVE10", "HALFOFF", "FIVEOFF", "TENOFF"]))
    r = c.receipt()
    assert sum(l.gross_cents for l in r.lines) == r.subtotal_cents == c.subtotal_cents()
    assert sum(l.discount_cents for l in r.lines) == r.discount_cents == c.discount_cents()
    assert sum(l.tax_cents for l in r.lines) == r.tax_cents == c.tax_cents()
    assert r.shipping_cents == c.shipping_cents()
    assert r.total_cents == c.total_cents() == r.subtotal_cents - r.discount_cents + r.shipping_cents + r.tax_cents
    assert all(l.net_cents == l.gross_cents - l.discount_cents for l in r.lines)
    assert all(l.discount_cents >= 0 and l.tax_cents >= 0 for l in r.lines)


def test_contract_existing_behavior_kept():
    c = cart([(1200, 1), (2500, 1)], "SAVE10")
    assert c.discount_cents() == 370 and c.shipping_cents() == 599
