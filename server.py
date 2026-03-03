"""
Tuma250 MCP server — entrypoint.

Run via stdio (default for Cursor / LangGraph integration):
    python -m server

The server exposes 6 tools under the "tuma250" namespace:
    - login
    - search_products
    - add_to_cart
    - get_cart
    - list_recent_orders
    - get_order_details
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from tuma250_mcp.client_core import Tuma250Client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("tuma250")


# ---------------------------------------------------------------------------
# Tool: login
# ---------------------------------------------------------------------------


@mcp.tool()
async def login() -> dict[str, Any]:
    """
    Ensure an authenticated session with the Tuma250 site.

    Logs in using the configured credentials if the current session is not
    already authenticated. Subsequent tool calls auto-login as needed, so
    calling this tool explicitly is optional.

    Returns:
        dict: {"success": bool, "message": str}
    """
    async with Tuma250Client() as client:
        try:
            await client.ensure_logged_in()
            return {"success": True, "message": "Authenticated successfully."}
        except Exception as exc:
            logger.exception("Login failed")
            return {"success": False, "message": str(exc)}


# ---------------------------------------------------------------------------
# Tool: search_products
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_products(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """
    Search for products on the Tuma250 grocery site.

    Args:
        query: Free-text search string, e.g. "butter 500g" or "rice 5kg".
        max_results: Maximum number of products to return (default 10).

    Returns:
        list of product objects, each containing:
            - id (str | None): WooCommerce product ID.
            - name (str | None): Product display name.
            - brand (str | None): Brand name if available.
            - package_size (str | None): Package size / unit description.
            - price (float | None): Unit price.
            - url (str | None): Product page URL.
            - category_path (str | None): Category breadcrumb.
    """
    async with Tuma250Client() as client:
        return await client.search_products(query=query, max_results=max_results)


# ---------------------------------------------------------------------------
# Tool: add_to_cart
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_product_variations(product_url: str) -> list[dict[str, Any]]:
    """
    Return the available variants for a variable WooCommerce product.

    Call this when search_products returns a product and you need to know
    which size/weight options exist before adding it to the cart.

    Args:
        product_url: Full product page URL (from search_products result["url"]).

    Returns:
        list of variation objects, each containing:
            - variation_id (str): Pass this to add_to_cart.
            - attributes (dict): Human-readable attributes, e.g. {"quantity": "500g"}.
            - raw_attributes (dict): WooCommerce attribute keys, e.g. {"attribute_quantity": "500g"}.
            - price (float | None): Price for this variant.
            - in_stock (bool): Whether this variant is currently available.
        Returns an empty list for simple (non-variable) products.
    """
    async with Tuma250Client() as client:
        return await client.get_product_variations(product_url=product_url)


@mcp.tool()
async def add_to_cart(
    product_slug: str,
    quantity: int = 1,
    variation_attributes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Add a product to the Tuma250 shopping cart.

    Uses the product slug (from search_products URL or get_order_details).
    For variable products, pass variation_attributes so the page pre-selects
    the variant, e.g. {"attribute_quantity": "500g"}.

    Args:
        product_slug: Product URL slug, e.g. "fresh-carrots-1kg" (from URL or
            get_order_details items).
        quantity: Number of units to add (default 1).
        variation_attributes: For variable products, attribute key/value pairs
            e.g. {"attribute_quantity": "500g"} (from get_order_details or
            get_product_variations raw_attributes).

    Returns:
        dict containing:
            - success (bool): Whether the item was confirmed in the cart.
            - cart_total_items (int): Total number of line items in the cart.
            - cart_total_price (float | None): Cart grand total.
            - line_item_summary (list): Each item with id, name, qty.
    """
    async with Tuma250Client() as client:
        return await client.add_to_cart(
            product_slug=product_slug,
            quantity=quantity,
            variation_attributes=variation_attributes,
        )


# ---------------------------------------------------------------------------
# Tool: get_cart
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_cart() -> dict[str, Any]:
    """
    Retrieve the current contents of the Tuma250 shopping cart.

    Returns:
        dict containing:
            - items (list): Each item with product_id, slug, variation_attributes,
              name, qty, price, subtotal.
            - total_items (int): Number of distinct line items.
            - subtotal (float | None): Items cost before shipping.
            - shipping_options (list): Available shipping methods with label and price.
            - total (float | None): Grand total including selected shipping.
    """
    async with Tuma250Client() as client:
        return await client.get_cart()


# ---------------------------------------------------------------------------
# Tool: list_recent_orders
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_recent_orders(limit: int = 10) -> list[dict[str, Any]]:
    """
    List recent orders from the Tuma250 "My Orders" page.

    Args:
        limit: Maximum number of orders to return (default 10).

    Returns:
        list of order summary objects, each containing:
            - order_id (str | None)
            - date (str | None)
            - status (str | None)
            - total (float | None)
            - link (str | None): URL to the order detail page.
    """
    async with Tuma250Client() as client:
        return await client.list_recent_orders(limit=limit)


# ---------------------------------------------------------------------------
# Tool: get_order_details
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_order_details(order_id: str) -> dict[str, Any]:
    """
    Fetch the line items for a specific Tuma250 order.

    Args:
        order_id: The WooCommerce order ID (from list_recent_orders).

    Returns:
        dict containing:
            - order_id (str)
            - items (list): Each item with slug, variation_attributes,
              name, qty, price, brand, unit_size, category_path.
    """
    async with Tuma250Client() as client:
        return await client.get_order_details(order_id=order_id)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the tuma250-mcp console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
