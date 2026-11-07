# Open issues

## #1 Itemized receipts that add up to the cent

Add `Cart.receipt()` returning a `Receipt`, and the dataclasses `Receipt` and `ReceiptLine`
(exported from the `store` package).

`ReceiptLine` fields: `sku`, `name`, `qty`, `gross_cents`, `discount_cents`, `net_cents`, `tax_cents`.
`Receipt` fields: `lines` (list of `ReceiptLine`, in cart order), `subtotal_cents`, `discount_cents`,
`shipping_cents`, `tax_cents`, `total_cents`.

Rules:
- `gross_cents` = price_cents * qty for the line.
- The cart's total discount (`Cart.discount_cents()`) is split across lines in proportion to
  `gross_cents`: each line first gets floor(total_discount * gross / subtotal). Leftover cents are then
  given one at a time to the lines with the largest remainder of that division, ties going to the
  earlier line in the cart. Line discounts therefore sum exactly to the cart discount.
- `net_cents` = gross_cents - discount_cents for the line.
- The cart's tax (`Cart.tax_cents()`) is split across lines the same way, in proportion to `net_cents`.
- Receipt totals equal the cart's own numbers: subtotal, discount, shipping, tax and total
  (`total_cents` = subtotal - discount + shipping + tax = `Cart.total_cents()`).
- An empty cart gives a receipt with no lines and every total 0.

Example: lines of 1000, 2000 and 3005 cents with SAVE10 give line discounts 100, 200, 300
(cart discount 600) and line taxes 72, 144, 216 (cart tax 432), total 5837.
