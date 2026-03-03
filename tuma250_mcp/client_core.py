"""
Low-level Playwright session manager and site interaction logic for Tuma250.

All CSS selectors and URL paths are centralised in the SELECTORS and URLS
dictionaries at the top of this module. When the Tuma250 site changes,
only these two dicts need updating.

Usage:
    async with Tuma250Client() as client:
        await client.ensure_logged_in()
        products = await client.search_products("butter 500g")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from tuma250_mcp.config import Tuma250Settings, get_settings
from tuma250_mcp.parsing import (
    parse_cart_item,
    parse_cart_totals,
    parse_order_detail_item,
    parse_order_row,
    parse_product_card,
    parse_product_variations,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Site-specific constants — adapt these when the DOM or URL structure changes
# ---------------------------------------------------------------------------

URLS: dict[str, str] = {
    "login": "/my-account/",
    "cart": "/cart/",
    "orders": "/my-account/orders/",
    "order_detail": "/my-account/view-order/{order_id}/",
    "search": "/?s={query}&post_type=product",
    "product": "/product/{slug}/",
}

SELECTORS: dict[str, str] = {
    # Login form — confirmed from live DOM (WooCommerce 10.5.2 + Flatsome theme)
    "login_username": "#username",
    "login_password": "#password",
    "login_submit": "button[name='login']",
    # Present only when authenticated (standard WooCommerce My Account nav)
    "login_success_indicator": ".woocommerce-MyAccount-navigation",
    # Product search results — Flatsome theme uses div.product-small, not li.product
    "product_card": "div.product-small",
    # Cart — standard WooCommerce table classes
    "cart_item_row": "tr.woocommerce-cart-form__cart-item",
    "cart_total_price": ".order-total .woocommerce-Price-amount",
    # Informational only — add-to-cart uses the ?add-to-cart= URL approach
    "add_to_cart_button": "[data-product_id='{product_id}']",
    # Orders list — standard WooCommerce My Account orders table
    "order_row": "tr.woocommerce-orders-table__row",
    # Order detail items — standard WooCommerce order detail table
    "order_detail_item_row": "tr.woocommerce-table__line-item",
}


class Tuma250Client:
    """
    Async context manager wrapping a persistent Playwright browser session.

    The session state (cookies, localStorage) is persisted to disk so that
    subsequent runs do not need to re-authenticate.

    Example:
        async with Tuma250Client() as client:
            results = await client.search_products("rice 5kg", max_results=5)
    """

    def __init__(self, settings: Tuma250Settings | None = None) -> None:
        self._settings: Tuma250Settings = settings or get_settings()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    # ------------------------------------------------------------------
    # Context manager lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "Tuma250Client":
        self._playwright = await async_playwright().start()
        headless = not self._settings.debug
        self._browser = await self._playwright.chromium.launch(headless=headless)

        session_path = Path(self._settings.session_file)
        if session_path.exists():
            logger.debug("Restoring browser session from %s", session_path)
            self._context = await self._browser.new_context(
                storage_state=str(session_path)
            )
        else:
            self._context = await self._browser.new_context()

        self._page = await self._context.new_page()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, key: str, **kwargs: str) -> str:
        """Build an absolute URL from the URLS map."""
        path = URLS[key].format(**kwargs)
        return self._settings.base_url.rstrip("/") + path

    async def _resolve_slug_to_product_id(self, product_id: str) -> str:
        """
        Resolve a product slug to the numeric WooCommerce product ID.

        Memory stores slugs (e.g. fresh-tomatoes-for-salad-big-size-inyanya)
        but add-to-cart requires the numeric ID. Visit the product page and
        extract the ID from the add-to-cart form.
        """
        if product_id.isdigit():
            return product_id
        product_url = self._url("product", slug=product_id)
        logger.debug("Resolving slug %r → numeric ID from %s", product_id, product_url)
        await self.page.goto(product_url)
        await self.page.wait_for_load_state("networkidle")
        # WooCommerce: variable products have input[name=add-to-cart] with parent ID
        add_input = await self.page.query_selector(
            'form.cart input[name="add-to-cart"]'
        )
        if add_input:
            numeric_id = await add_input.get_attribute("value")
            if numeric_id and numeric_id.isdigit():
                logger.info("Resolved slug %r → product_id=%s", product_id, numeric_id)
                return numeric_id
        # Fallback: data-product_id on the add-to-cart button
        btn = await self.page.query_selector(
            "button.single_add_to_cart_button[data-product_id], "
            ".single_add_to_cart_button[data-product_id]"
        )
        if btn:
            numeric_id = await btn.get_attribute("data-product_id")
            if numeric_id and numeric_id.isdigit():
                logger.info(
                    "Resolved slug %r → product_id=%s (from button)",
                    product_id,
                    numeric_id,
                )
                return numeric_id
        logger.warning("Could not resolve slug %r to numeric ID", product_id)
        return product_id

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Tuma250Client must be used as an async context manager.")
        return self._page

    async def _save_session(self) -> None:
        """Persist the current browser storage state to disk."""
        if self._context:
            await self._context.storage_state(path=self._settings.session_file)
            logger.debug("Session saved to %s", self._settings.session_file)

    async def _is_logged_in(self) -> bool:
        """
        Check whether the current session is authenticated.

        Returns:
            bool: True if the account navigation element is visible.
        """
        await self.page.goto(self._url("login"))
        el = await self.page.query_selector(SELECTORS["login_success_indicator"])
        return el is not None

    # ------------------------------------------------------------------
    # Public tool methods
    # ------------------------------------------------------------------

    async def ensure_logged_in(self) -> None:
        """
        Ensure the browser session is authenticated, logging in if necessary.

        Raises:
            RuntimeError: If login fails (wrong credentials or unexpected DOM).
        """
        if await self._is_logged_in():
            logger.info("Session already authenticated.")
            return

        logger.info("Logging in as %s …", self._settings.username)
        await self.page.goto(self._url("login"))
        await self.page.fill(SELECTORS["login_username"], self._settings.username)
        await self.page.fill(SELECTORS["login_password"], self._settings.password)
        await self.page.click(SELECTORS["login_submit"])
        await self.page.wait_for_load_state("networkidle")

        if not await self._is_logged_in():
            raise RuntimeError(
                "Login failed. Check TUMA250_USERNAME / TUMA250_PASSWORD and the login selectors."
            )

        await self._save_session()
        logger.info("Login successful.")

    async def search_products(
        self, query: str, max_results: int = 10
    ) -> list[dict[str, Any]]:
        """
        Search for products on Tuma250 and return structured results.

        Args:
            query (str): Free-text search query, e.g. "butter 500g".
            max_results (int): Maximum number of results to return.

        Returns:
            list[dict[str, Any]]: List of parsed product dicts.
        """
        await self.ensure_logged_in()
        url = self._url("search", query=query.replace(" ", "+"))
        logger.info("Searching products: %r → %s", query, url)
        await self.page.goto(url)
        await self.page.wait_for_load_state("networkidle")

        cards = await self.page.query_selector_all(SELECTORS["product_card"])
        logger.debug("Found %d product cards (capped at %d)", len(cards), max_results)

        results: list[dict[str, Any]] = []
        for card in cards[:max_results]:
            try:
                results.append(await parse_product_card(card))
            except Exception:
                logger.exception("Failed to parse product card")

        return results

    async def get_product_variations(self, product_url: str) -> list[dict[str, Any]]:
        """
        Return the available variants for a variable WooCommerce product.

        Parses the ``data-product_variations`` JSON blob embedded in the
        variations form on the product page.  For simple (non-variable)
        products the list will be empty.

        Args:
            product_url (str): Full URL of the product page
                               (e.g. from search_products result["url"]).

        Returns:
            list[dict[str, Any]]: Each entry contains:
                - variation_id (str)
                - attributes (dict[str, str]): e.g. {"quantity": "500g"}
                - price (float | None)
                - in_stock (bool)
        """
        await self.ensure_logged_in()
        logger.info("Fetching variations from %s", product_url)
        await self.page.goto(product_url)
        await self.page.wait_for_load_state("networkidle")
        return await parse_product_variations(self.page)

    async def add_to_cart(
        self,
        product_id: str,
        quantity: int = 1,
        variation_id: str | None = None,
        variation_attributes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Add a product to the cart by its WooCommerce product ID.

        For variable products, ``variation_id`` (and optionally
        ``variation_attributes``) must be supplied — use
        ``get_product_variations`` to discover the available options first.

        Args:
            product_id (str): The WooCommerce parent product ID.
            quantity (int): Number of units to add.
            variation_id (str | None): Required for variable products.
            variation_attributes (dict[str, str] | None): Attribute key/value
                pairs, e.g. ``{"attribute_quantity": "500g"}``.  Used together
                with ``variation_id`` to fully specify the variant.

        Returns:
            dict[str, Any]: Summary with success flag and updated cart state.
        """
        await self.ensure_logged_in()

        # Resolve slug → numeric WooCommerce product ID. Memory stores slugs.
        numeric_product_id = await self._resolve_slug_to_product_id(product_id)

        # Auto-resolve variation_id from the product page when only
        # variation_attributes are known (e.g. from stored preferences).
        if variation_attributes and not variation_id:
            product_url = self._url("product", slug=product_id)
            variations = await self.get_product_variations(product_url)
            for v in variations:
                raw_attrs = v.get("raw_attributes", {})
                if all(
                    raw_attrs.get(k) == val
                    for k, val in variation_attributes.items()
                ):
                    variation_id = v["variation_id"]
                    logger.info(
                        "Auto-resolved variation_id=%s for %s %s",
                        variation_id,
                        product_id,
                        variation_attributes,
                    )
                    break
            else:
                logger.warning(
                    "Could not resolve variation_id for %s %s — "
                    "attempting add without it.",
                    product_id,
                    variation_attributes,
                )

        logger.info(
            "Adding product %s (variation=%s, qty=%d) to cart",
            numeric_product_id,
            variation_id,
            quantity,
        )

        # Reason: WooCommerce accepts add-to-cart via a GET URL.
        # add-to-cart must be the numeric product ID, not the slug.
        params = f"add-to-cart={numeric_product_id}&quantity={quantity}"
        if variation_id:
            params += f"&variation_id={variation_id}"
        if variation_attributes:
            for key, value in variation_attributes.items():
                params += f"&{key}={value}"

        add_url = f"{self._settings.base_url.rstrip('/')}/?{params}"
        await self.page.goto(add_url)
        await self.page.wait_for_load_state("networkidle")

        cart = await self.get_cart()
        item_ids = [item["product_id"] for item in cart.get("items", [])]
        # Variable products: cart stores variation_id. Simple: cart stores
        # numeric product_id. Check both for robustness.
        target_id = variation_id or numeric_product_id
        success = (
            str(target_id) in item_ids
            or str(numeric_product_id) in item_ids
        )

        return {
            "success": success,
            "cart_total_items": cart.get("total_items", 0),
            "subtotal": cart.get("subtotal"),
            "shipping_options": cart.get("shipping_options", []),
            "total": cart.get("total"),
            "line_item_summary": [
                {"id": i["product_id"], "name": i["name"], "qty": i["qty"]}
                for i in cart.get("items", [])
            ],
        }

    async def get_cart(self) -> dict[str, Any]:
        """
        Fetch and parse the current cart contents.

        Returns:
            dict[str, Any]: Cart details with items list and totals.
        """
        await self.ensure_logged_in()
        await self.page.goto(self._url("cart"))
        await self.page.wait_for_load_state("networkidle")

        rows = await self.page.query_selector_all(SELECTORS["cart_item_row"])
        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                items.append(await parse_cart_item(row))
            except Exception:
                logger.exception("Failed to parse cart item row")

        totals = await parse_cart_totals(self.page, SELECTORS)

        return {
            "items": items,
            "total_items": totals["total_items"],
            "subtotal": totals["subtotal"],
            "shipping_options": totals["shipping"],
            "total": totals["total"],
        }

    async def list_recent_orders(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        Fetch the list of recent orders from the "My Orders" page.

        Args:
            limit (int): Maximum number of orders to return.

        Returns:
            list[dict[str, Any]]: List of order summary dicts.
        """
        await self.ensure_logged_in()
        await self.page.goto(self._url("orders"))
        await self.page.wait_for_load_state("networkidle")

        rows = await self.page.query_selector_all(SELECTORS["order_row"])
        logger.debug("Found %d order rows (capped at %d)", len(rows), limit)

        orders: list[dict[str, Any]] = []
        for row in rows[:limit]:
            try:
                orders.append(await parse_order_row(row))
            except Exception:
                logger.exception("Failed to parse order row")

        return orders

    async def get_order_details(self, order_id: str) -> dict[str, Any]:
        """
        Fetch the line items for a specific order.

        Args:
            order_id (str): The WooCommerce order ID.

        Returns:
            dict[str, Any]: Order details including the items list.
        """
        await self.ensure_logged_in()
        url = self._url("order_detail", order_id=order_id)
        logger.info("Fetching order details for order %s → %s", order_id, url)
        await self.page.goto(url)
        await self.page.wait_for_load_state("networkidle")

        rows = await self.page.query_selector_all(SELECTORS["order_detail_item_row"])
        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                items.append(await parse_order_detail_item(row))
            except Exception:
                logger.exception("Failed to parse order detail item")

        return {"order_id": order_id, "items": items}
