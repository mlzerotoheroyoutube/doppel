"""Independent acceptance checks for demo_store issues. Never shown to any agent;
used only to grade what the coding loop ships. Test names start with the issue."""
import pytest

from store import Cart, CouponError, Item


def cart(*items, coupon=None):
    c = Cart()
    for it in items:
        c.add(it)
    if coupon:
        c.apply_coupon(coupon)
    return c


# ---- issue 1: percent coupons cover the whole subtotal
def test_i1_two_lines():
    assert cart(Item("MUG", "Mug", 1200), Item("TEE", "Tee", 2500), coupon="SAVE10").discount_cents() == 370


def test_i1_quantities():
    assert cart(Item("MUG", "Mug", 1200, qty=3), Item("TEE", "Tee", 2500), coupon="SAVE10").discount_cents() == 610


def test_i1_rounds_whole_subtotal_down():
    # 10% of 2410 = 241.0; rounding each line separately would give 240
    assert cart(Item("A", "A", 1205), Item("B", "B", 1205), coupon="SAVE10").discount_cents() == 241


def test_i1_halfoff_and_total():
    c = cart(Item("A", "A", 3000), Item("B", "B", 1000, qty=2), coupon="HALFOFF")
    assert c.discount_cents() == 2500
    assert c.total_cents() == 2500 + 599 + 200


# ---- issue 2: free shipping at $50.00 or more, after discounts
def test_i2_exactly_50():
    assert cart(Item("BAG", "Bag", 5000)).shipping_cents() == 0


def test_i2_just_below():
    assert cart(Item("BAG", "Bag", 4999)).shipping_cents() == 599


def test_i2_discount_pushes_below_threshold():
    assert cart(Item("BAG", "Bag", 5500), coupon="SAVE10").shipping_cents() == 599


def test_i2_discount_lands_exactly_on_threshold():
    assert cart(Item("BAG", "Bag", 5500), coupon="FIVEOFF").shipping_cents() == 0


# ---- issue 3: remove(sku, qty=None)
def test_i3_partial():
    c = cart(Item("MUG", "Mug", 1200, qty=3))
    c.remove("MUG", qty=1)
    assert c.items[0].qty == 2


def test_i3_to_zero_removes_line():
    c = cart(Item("MUG", "Mug", 1200, qty=2), Item("TEE", "Tee", 2500))
    c.remove("MUG", qty=2)
    assert [i.sku for i in c.items] == ["TEE"]


def test_i3_no_qty_removes_whole_line():
    c = cart(Item("MUG", "Mug", 1200, qty=3), Item("TEE", "Tee", 2500))
    c.remove("MUG")
    assert c.subtotal_cents() == 2500


def test_i3_too_many_raises():
    c = cart(Item("MUG", "Mug", 1200, qty=2))
    with pytest.raises(KeyError):
        c.remove("MUG", qty=3)
    assert c.items[0].qty == 2


def test_i3_missing_sku_raises():
    with pytest.raises(KeyError):
        cart(Item("MUG", "Mug", 1200)).remove("PEN", qty=1)


# ---- contract that must keep working (every task)
def test_contract_merge_and_coupon_rules():
    c = cart(Item("MUG", "Mug", 1200), Item("MUG", "Mug", 1200))
    assert len(c.items) == 1 and c.items[0].qty == 2
    c.apply_coupon("SAVE10")
    with pytest.raises(CouponError):
        c.apply_coupon("FIVEOFF")


def test_contract_fixed_coupon_minimum_and_cap():
    assert cart(Item("PEN", "Pen", 1500), coupon="FIVEOFF").discount_cents() == 0
    assert cart(Item("PEN", "Pen", 300), coupon="TENOFF").discount_cents() == 300


def test_contract_tax_and_total():
    c = cart(Item("PEN", "Pen", 1006))
    assert c.tax_cents() == 80
    assert c.total_cents() == 1006 + 599 + 80
