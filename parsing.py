"""
HTML/DOM parsing helpers for the Tuma250 WooCommerce site.

All functions receive a Playwright Page or ElementHandle and return
typed dicts that map directly to the Pydantic models used by the MCP tools.

Selectors are verified against the live site (WooCommerce 10.5.2, Flatsome theme).
The site uses div.product-small cards rather than the standard li.product layout.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from playwright.async_api import ElementHandle, Page

logger = logging.getLogger(__name__)


def _parse_price(raw: str | None) -> float | None:
    """
    Convert a raw price string like "RWF 1,500" or "1,500.00" to a float.

    Args:
        raw (str | None): Raw price text from the DOM.

    Returns:
        float | None: Parsed price, or None if unparseable.
    """
    if not raw:
        return None
    # Strip currency symbols, spaces, and non-numeric chars except comma/dot
    cleaned = raw.strip()
    for symbol in ("RWF", "$", "€", "£", "\xa0", "\u00a0"):
        cleaned = cleaned.replace(symbol, "")
    cleaned = cleaned.replace(",", "").replace(" ", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        logger.warning("Could not parse price: %r", raw)
        return None


async def parse_product_card(card: ElementHandle) -> dict[str, Any]:
    """
    Extract structured product data from a single product card element.

    Verified against Tuma250 (Flatsome theme, WooCommerce 10.5.2).
    Card container: div.product-small

    Args:
        card (ElementHandle): A Playwright handle to a div.product-small element.

    Returns:
        dict[str, Any]: Parsed product fields. Missing fields default to None.
    """
    async def text(selector: str) -> str | None:
        el = await card.query_selector(selector)
        return (await el.inner_text()).strip() if el else None

    async def attr(selector: str, attribute: str) -> str | None:
        el = await card.query_selector(selector)
        return await el.get_attribute(attribute) if el else None

    # Product ID lives on the add-to-cart button as data-product_id
    product_id = await attr("[data-product_id]", "data-product_id")

    # Title is inside p.woocommerce-loop-product__title > a.woocommerce-LoopProduct-link
    name = await text(".woocommerce-loop-product__title")

    # Price: span.price > span.woocommerce-Price-amount
    price_raw = await text(".price .woocommerce-Price-amount")

    # Product URL from the title link
    url = await attr("a.woocommerce-LoopProduct-link", "href")

    # Category is not rendered on the search result card in this theme
    # It can be derived from the product URL path or product page if needed
    category: str | None = None

    return {
        "id": product_id,
        "name": name,
        "brand": None,       # Not exposed on the card; available on the product page
        "package_size": None, # Not exposed on the card; parse from name if needed
        "unit": None,
        "price": _parse_price(price_raw),
        "url": url,
        "category_path": category,
    }


async def parse_cart_item(row: ElementHandle) -> dict[str, Any]:
    """
    Extract a single line item from the WooCommerce cart table.

    Standard WooCommerce cart table row: tr.woocommerce-cart-form__cart-item

    Args:
        row (ElementHandle): A Playwright handle to a cart row (<tr>) element.

    Returns:
        dict[str, Any]: Cart item fields.
    """
    async def text(selector: str) -> str | None:
        el = await row.query_selector(selector)
        return (await el.inner_text()).strip() if el else None

    async def attr(selector: str, attribute: str) -> str | None:
        el = await row.query_selector(selector)
        return await el.get_attribute(attribute) if el else None

    # Product ID on the remove button or quantity input
    product_id = await attr("[data-product_id]", "data-product_id")

    # Product name link
    name = await text(".product-name a")

    # Quantity input value
    qty_raw = await attr("input.qty", "value")

    # Price per unit
    price_raw = await text(".product-price .woocommerce-Price-amount")

    # Line subtotal
    subtotal_raw = await text(".product-subtotal .woocommerce-Price-amount")

    return {
        "product_id": product_id,
        "name": name,
        "qty": int(qty_raw) if qty_raw and qty_raw.isdigit() else 1,
        "price": _parse_price(price_raw),
        "subtotal": _parse_price(subtotal_raw),
    }


async def parse_order_row(row: ElementHandle) -> dict[str, Any]:
    """
    Extract a single order summary from the "My Orders" table.

    Standard WooCommerce My Account orders table row:
    tr.woocommerce-orders-table__row

    Args:
        row (ElementHandle): A Playwright handle to an order row element.

    Returns:
        dict[str, Any]: Order summary fields.
    """
    async def text(selector: str) -> str | None:
        el = await row.query_selector(selector)
        return (await el.inner_text()).strip() if el else None

    async def attr(selector: str, attribute: str) -> str | None:
        el = await row.query_selector(selector)
        return await el.get_attribute(attribute) if el else None

    # Standard WooCommerce My Account orders table column classes
    order_id = await text(".woocommerce-orders-table__cell-order-number a")
    date = await text(".woocommerce-orders-table__cell-order-date")
    status = await text(".woocommerce-orders-table__cell-order-status")
    total_raw = await text(".woocommerce-orders-table__cell-order-total .woocommerce-Price-amount")
    link = await attr(".woocommerce-orders-table__cell-order-number a", "href")

    return {
        "order_id": order_id,
        "date": date,
        "status": status,
        "total": _parse_price(total_raw),
        "link": link,
    }


async def parse_order_detail_item(row: ElementHandle) -> dict[str, Any]:
    """
    Extract a single product line from an order detail page.

    Standard WooCommerce order detail table row: tr.woocommerce-table__line-item

    Args:
        row (ElementHandle): A Playwright handle to an order item row element.

    Returns:
        dict[str, Any]: Order item fields including product metadata.
    """
    async def text(selector: str) -> str | None:
        el = await row.query_selector(selector)
        return (await el.inner_text()).strip() if el else None

    async def attr(selector: str, attribute: str) -> str | None:
        el = await row.query_selector(selector)
        return await el.get_attribute(attribute) if el else None

    # Standard WooCommerce order detail column classes
    name = await text(".woocommerce-table__product-name a")
    price_raw = await text(
        ".woocommerce-table__product-total .woocommerce-Price-amount"
    )
    product_link = await attr(".woocommerce-table__product-name a", "href")

    # Quantity is shown as "× N" in the product name cell
    qty_text = await text(".woocommerce-table__product-name")
    qty = 1
    if qty_text:
        # Reason: WooCommerce renders quantity as "Product Name × 2" in the name cell
        import re
        match = re.search(r"×\s*(\d+)", qty_text)
        if match:
            qty = int(match.group(1))

    # Try data-product_id first if the theme exposes it (numeric ID, add-to-cart ready).
    # Else derive product_id (slug) and variation_attributes from the product link.
    # Variable product URLs look like:
    #   /product/fresh-carrots-1kg/?attribute_quantity=500g
    # Simple product URLs look like:
    #   /product/gorillas-roasted-beans-1000g/
    product_id: str | None = await attr("[data-product_id]", "data-product_id")
    variation_attributes: dict[str, str] = {}
    if not product_id and product_link:
        # Split path from query string before extracting the slug
        path_part, _, qs = product_link.partition("?")
        parts = [p for p in path_part.rstrip("/").split("/") if p]
        product_id = parts[-1] if parts else None
        if qs:
            for pair in qs.split("&"):
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    variation_attributes[k] = v

    return {
        "product_id": product_id,
        "variation_attributes": variation_attributes or None,
        "name": name,
        "qty": qty,
        "price": _parse_price(price_raw),
        "brand": None,
        "unit_size": None,
        "category_path": None,
    }


async def parse_product_variations(page: Page) -> list[dict[str, Any]]:
    """
    Extract variation data from a WooCommerce variable product page.

    WooCommerce embeds the full variation list as JSON in the
    ``data-product_variations`` attribute of the ``.variations_form`` element.
    For simple (non-variable) products this element is absent and an empty
    list is returned.

    Args:
        page (Page): The Playwright page showing the product.

    Returns:
        list[dict[str, Any]]: Each entry contains:
            - variation_id (str)
            - attributes (dict[str, str]): e.g. {"quantity": "500g"}
            - price (float | None)
            - in_stock (bool)
    """
    form = await page.query_selector("form.variations_form[data-product_variations]")
    if not form:
        return []

    raw = await form.get_attribute("data-product_variations")
    if not raw:
        return []

    try:
        variations_data: list[dict[str, Any]] = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Could not parse data-product_variations JSON")
        return []

    results: list[dict[str, Any]] = []
    for v in variations_data:
        # Reason: strip the "attribute_" prefix from keys so callers get
        # clean names like {"quantity": "500g"} rather than
        # {"attribute_quantity": "500g"}.
        raw_attrs: dict[str, str] = v.get("attributes", {})
        clean_attrs = {
            k.removeprefix("attribute_"): val for k, val in raw_attrs.items()
        }
        results.append(
            {
                "variation_id": str(v.get("variation_id", "")),
                "attributes": clean_attrs,
                # Keep raw_attributes so callers can pass them directly to
                # add_to_cart(variation_attributes=...) without transformation.
                "raw_attributes": raw_attrs,
                "price": float(v["display_price"]) if v.get("display_price") is not None else None,
                "in_stock": bool(v.get("is_in_stock", False)),
            }
        )

    return results


async def parse_cart_totals(page: Page, selectors: dict[str, str]) -> dict[str, Any]:
    """
    Extract the full cost breakdown from the cart totals table.

    Reads subtotal, each shipping option label + price, and the grand total
    separately so callers can present an accurate breakdown to the user.

    Args:
        page (Page): The Playwright page currently showing the cart.
        selectors (dict[str, str]): Selector map from client_core.SELECTORS.

    Returns:
        dict[str, Any]:
            - subtotal (float | None): Items cost before shipping.
            - shipping (list[dict]): Each available shipping method with
              "label" (str) and "price" (float | None).
            - total (float | None): Grand total including selected shipping.
            - total_items (int): Number of distinct line items in the cart.
    """
    async def _text(selector: str) -> str | None:
        el = await page.query_selector(selector)
        return (await el.inner_text()).strip() if el else None

    # Subtotal row
    subtotal_raw = await _text("tr.cart-subtotal .woocommerce-Price-amount")

    # Grand total row
    total_raw = await _text("tr.order-total .woocommerce-Price-amount")

    # Shipping options — each <li> inside the shipping row
    shipping: list[dict[str, Any]] = []
    shipping_items = await page.query_selector_all(
        "tr.woocommerce-shipping-totals .shipping__list_item"
    )
    for item in shipping_items:
        label_el = await item.query_selector("label")
        if not label_el:
            continue
        label_text = (await label_el.inner_text()).strip()
        price_el = await label_el.query_selector(".woocommerce-Price-amount")
        price_raw = (await price_el.inner_text()).strip() if price_el else None
        # Reason: "Store Pick-Up (Free)" has no price element; treat as 0
        price = _parse_price(price_raw) if price_raw else 0.0
        # Strip the price text from the label so we get a clean name
        label_name = label_text.replace(price_raw or "", "").strip().rstrip(":").strip()
        shipping.append({"label": label_name, "price": price})

    rows = await page.query_selector_all(selectors["cart_item_row"])
    return {
        "subtotal": _parse_price(subtotal_raw),
        "shipping": shipping,
        "total": _parse_price(total_raw),
        "total_items": len(rows),
    }
