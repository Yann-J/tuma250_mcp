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
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from tuma250_mcp.config import Tuma250Settings, get_settings
from tuma250_mcp.parsing import (
    parse_cart_item,
    parse_cart_totals,
    parse_order_detail_item,
    parse_order_row,
    parse_product_availability,
    parse_product_card,
    parse_product_variations,
    parse_woocommerce_notices,
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
    # Login form — scoped to the WooCommerce form so header/Google widgets
    # with duplicate ids cannot steal fill/click (Flatsome + social login).
    "login_username": "form.woocommerce-form-login #username",
    "login_password": "form.woocommerce-form-login #password",
    "login_submit": "form.woocommerce-form-login button[name='login']",
    "login_remember": "form.woocommerce-form-login #rememberme",
    "login_error": ".woocommerce-error",
    # Flatsome does not render .woocommerce-MyAccount-navigation; the
    # customer-logout link only appears for authenticated users.
    "login_success_indicator": "a[href*='customer-logout']",
    # Product search results — Flatsome nests a decorative div.product-small.box
    # inside each card; type-product is only on the real card.
    "product_card": "div.product-small.type-product",
    # Cart — standard WooCommerce table classes
    "cart_item_row": "tr.woocommerce-cart-form__cart-item",
    "cart_total_price": ".order-total .woocommerce-Price-amount",
    # Informational only — add-to-cart uses the ?add-to-cart= URL approach
    "add_to_cart_button": "[data-product_id='{product_id}']",
    # Orders list — standard WooCommerce My Account orders table
    "order_row": "tr.woocommerce-orders-table__row",
    # Order detail items — standard WooCommerce order detail table
    "order_detail_item_row": "tr.woocommerce-table__line-item",
    # Product page — hidden inputs with IDs for add-to-cart
    "product_page_product_id": "input[name='product_id']",
    "product_page_variation_id": "input.variation_id",
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

    async def _extract_ids_from_product_page(self) -> tuple[str, str | None]:
        """
        Read product_id and variation_id from the product page.

        Variable products: hidden inputs
          <input type="hidden" name="product_id" value="12295">
          <input type="hidden" name="variation_id" class="variation_id" value="54913">

        Simple products: Add to cart button
          <button name="add-to-cart" value="12256">Add to cart</button>

        Returns:
            (product_id, variation_id). variation_id is None for simple products.
        """
        product_id: str | None = None
        product_id_el = await self.page.query_selector(
            SELECTORS["product_page_product_id"]
        )
        if product_id_el:
            product_id = await product_id_el.get_attribute("value")
        if not product_id or not product_id.isdigit():
            btn = await self.page.query_selector('button[name="add-to-cart"]')
            if btn:
                product_id = await btn.get_attribute("value")
        if not product_id or not product_id.isdigit():
            raise ValueError(
                "Product page missing product_id (input[name='product_id'] or "
                "button[name='add-to-cart'] value). Ensure the page has fully loaded."
            )
        var_el = await self.page.query_selector(SELECTORS["product_page_variation_id"])
        variation_id: str | None = None
        if var_el:
            raw = await var_el.get_attribute("value")
            # WooCommerce uses value="0" when no variant is selected.
            if raw and raw.isdigit() and raw != "0":
                variation_id = raw
        return (product_id, variation_id)

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError(
                "Tuma250Client must be used as an async context manager."
            )
        return self._page

    async def _save_session(self) -> None:
        """Persist the current browser storage state to disk."""
        if self._context:
            await self._context.storage_state(path=self._settings.session_file)
            logger.debug("Session saved to %s", self._settings.session_file)

    async def _wait_for_login_page_state(self) -> None:
        """Wait until the logout link or the login form is present."""
        await self.page.wait_for_selector(
            f"{SELECTORS['login_success_indicator']}, {SELECTORS['login_username']}",
            timeout=15_000,
        )

    async def _login_error_text(self) -> str | None:
        """Return WooCommerce login error text if the notice is on the page."""
        el = await self.page.query_selector(SELECTORS["login_error"])
        if not el:
            return None
        text = (await el.inner_text()).strip()
        return text or None

    async def _is_logged_in(self) -> bool:
        """
        Check whether the current session is authenticated.

        Returns:
            bool: True if the logout link is present (user is authenticated).
        """
        await self.page.goto(self._url("login"))
        try:
            await self._wait_for_login_page_state()
        except PlaywrightTimeoutError:
            return False
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
        # Reload so we submit against a fresh WooCommerce login nonce.
        await self.page.goto(self._url("login"))
        await self._wait_for_login_page_state()
        await self.page.fill(SELECTORS["login_username"], self._settings.username)
        await self.page.fill(SELECTORS["login_password"], self._settings.password)
        if await self.page.query_selector(SELECTORS["login_remember"]):
            await self.page.check(SELECTORS["login_remember"])
        await self.page.click(SELECTORS["login_submit"])

        # WooCommerce pages often never reach networkidle (analytics/chat).
        # Wait for the dashboard nav or a login error instead.
        try:
            await self.page.wait_for_selector(
                f"{SELECTORS['login_success_indicator']}, {SELECTORS['login_error']}",
                timeout=20_000,
            )
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                "Login did not complete (no logout link or error message). "
                "The site may have changed or is blocking automated login."
            ) from exc

        error_text = await self._login_error_text()
        if error_text:
            raise RuntimeError(f"Login failed: {error_text}")

        if not await self.page.query_selector(SELECTORS["login_success_indicator"]):
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
            list[dict[str, Any]]: List of parsed product dicts, including
            ``in_stock`` so callers can skip unavailable items.
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
        product_slug: str,
        quantity: int = 1,
        variation_attributes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Add a product to the cart by its product slug.

        Navigates to the product page, extracts product_id and variation_id from
        hidden inputs in the DOM, then adds to cart. For variable products, pass
        variation_attributes (e.g. {"attribute_quantity": "500g"}) — they are
        appended to the product URL so the page pre-selects the variant and the
        variation_id hidden input is populated.

        Args:
            product_slug (str): Product URL slug, e.g. "fresh-carrots-1kg".
            quantity (int): Number of units to add.
            variation_attributes (dict[str, str] | None): Attribute key/value
                pairs for variable products, e.g. {"attribute_quantity": "500g"}.

        Returns:
            dict[str, Any]: Summary with success flag, error/message on
            failure, and updated cart state.
        """
        await self.ensure_logged_in()

        # Build product URL; variation_attributes in query string pre-select the variant
        product_url = self._url("product", slug=product_slug)
        if variation_attributes:
            qs = "&".join(f"{k}={v}" for k, v in variation_attributes.items())
            product_url = f"{product_url}?{qs}"

        logger.info(
            "Adding product slug %r (qty=%d) to cart → %s",
            product_slug,
            quantity,
            product_url,
        )
        await self.page.goto(product_url)
        await self.page.wait_for_load_state("networkidle")

        availability = await parse_product_availability(self.page)
        product_name = availability.get("name") or product_slug

        if availability.get("missing"):
            return self._add_to_cart_result(
                success=False,
                error="product_not_found",
                message=(
                    f"Could not find product {product_slug!r}. "
                    "Check the slug from search_products."
                ),
            )

        if not availability.get("in_stock", True):
            return self._add_to_cart_result(
                success=False,
                error="out_of_stock",
                message=f"{product_name} is currently out of stock.",
            )

        try:
            product_id, variation_id = await self._extract_ids_from_product_page()
        except ValueError as exc:
            return self._add_to_cart_result(
                success=False,
                error="product_not_found",
                message=str(exc),
            )
        logger.debug(
            "Extracted product_id=%s variation_id=%s from product page",
            product_id,
            variation_id,
        )

        if availability.get("is_variable") and not variation_id:
            if variation_attributes:
                message = (
                    f"Could not match variation_attributes on {product_name}. "
                    "Call get_product_variations and pass raw_attributes."
                )
            else:
                message = (
                    f"{product_name} has size/weight options that must be selected. "
                    "Call get_product_variations and pass variation_attributes."
                )
            return self._add_to_cart_result(
                success=False,
                error="variation_required",
                message=message,
            )

        params = f"add-to-cart={product_id}&quantity={quantity}"
        if variation_id:
            params += f"&variation_id={variation_id}"
        if variation_attributes:
            for key, value in variation_attributes.items():
                params += f"&{key}={value}"

        add_url = f"{self._settings.base_url.rstrip('/')}/?{params}"
        await self.page.goto(add_url)
        await self.page.wait_for_load_state("networkidle")

        notices = await parse_woocommerce_notices(self.page)
        cart = await self.get_cart()
        item_ids = [item["product_id"] for item in cart.get("items", [])]
        target_id = variation_id or product_id
        success = str(target_id) in item_ids or str(product_id) in item_ids

        if success:
            return self._add_to_cart_result(success=True, cart=cart)

        notice_text = next(
            (n["text"] for n in notices if n.get("type") == "error"),
            notices[0]["text"] if notices else None,
        )
        if notice_text:
            return self._add_to_cart_result(
                success=False,
                cart=cart,
                error=self._classify_cart_notice(notice_text),
                message=notice_text,
            )

        return self._add_to_cart_result(
            success=False,
            cart=cart,
            error="add_failed",
            message=f"{product_name} was not added to the cart.",
        )

    @staticmethod
    def _classify_cart_notice(text: str) -> str:
        """Map WooCommerce notice text to a stable error code."""
        lowered = text.lower()
        if "out of stock" in lowered:
            return "out_of_stock"
        if "choose product options" in lowered:
            return "variation_required"
        return "add_failed"

    @staticmethod
    def _add_to_cart_result(
        *,
        success: bool,
        cart: dict[str, Any] | None = None,
        error: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        cart = cart or {
            "items": [],
            "total_items": 0,
            "subtotal": None,
            "shipping_options": [],
            "total": None,
        }
        return {
            "success": success,
            "error": error,
            "message": message,
            "cart_total_items": cart.get("total_items", 0),
            "subtotal": cart.get("subtotal"),
            "shipping_options": cart.get("shipping_options", []),
            "total": cart.get("total"),
            "line_item_summary": [
                {
                    "id": i["product_id"],
                    "slug": i.get("slug"),
                    "variation_attributes": i.get("variation_attributes"),
                    "name": i["name"],
                    "qty": i["qty"],
                }
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
