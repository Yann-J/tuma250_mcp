---
name: tuma250
description: Manage the Tuma250 grocery cart and orders via the configured tuma250 MCP server (through mcporter). Use when the user wants to search Tuma250 products, choose variants/sizes, add/remove/update cart quantities, inspect the current basket totals/shipping options, or review recent orders and order details.
---

# Tuma250

Operate the **Tuma250** grocery site, also called simply "Tuma", using the local **tuma250 MCP server** via `mcporter`.

## Quick start (the 90% flow)

1) **Search**

- `mcporter call tuma250.search_products query='eggplant' max_results=5`

1) **If the product has sizes/variants, list them**

- `mcporter call tuma250.get_product_variations product_url='https://tuma250.com/product/.../'`

1) **Add to cart**

- Simple product:
  - `mcporter call tuma250.add_to_cart --args '{"product_id":"12310","quantity":1}'`
- Variable product (choose a variation from step 2):
  - `mcporter call tuma250.add_to_cart --args '{"product_id":"<parent>","quantity":1,"variation_id":"<variation>","variation_attributes":{...}}'`

1) **View cart (basket)**

- `mcporter call tuma250.get_cart --output json`

## Common tasks

### Add an item (by name)

1) Search with a good query (include brand/size if you can).
2) If needed, select a variation.
3) Add to cart.

Notes:

- Prefer `--args` JSON for `add_to_cart` so `product_id` stays a **string**.

### Confirm what’s currently in the basket

- `mcporter call tuma250.get_cart --output json`

Return shape includes:

- `items[]` (name, qty, price, subtotal)
- `shipping_options[]`
- `subtotal`, `total`

### Review recent orders

- `mcporter call tuma250.list_recent_orders limit=10`

### Get details for one order

- `mcporter call tuma250.get_order_details order_id='<id>'`

## Authentication

Usually you don’t need to do anything: tools auto-login as needed.

If you want to force it:

- `mcporter call tuma250.login()`
