# Open issues

## #1 SAVE10 only discounts the first item

With a mug ($12.00) and a T-shirt ($25.00) in the cart, SAVE10 takes $1.20
off. It should take 10% off the whole subtotal: $3.70. Percent coupons must
apply to every line, including quantities.

## #2 Orders of exactly $50.00 are charged shipping

The site says "free shipping on orders of $50 or more", but a cart whose
discounted subtotal is exactly $50.00 is charged $5.99.

## #3 Removing an item should be able to remove just one unit

`cart.remove("MUG")` drops the whole line even when there are 3 mugs.
Add an optional `qty` argument: `cart.remove(sku, qty=None)`. With no qty,
remove the whole line (current behavior). With a qty, decrease the line by
that many units, removing the line when it reaches zero. Removing more units
than are in the cart, or a SKU that is not in the cart, raises `KeyError`.
