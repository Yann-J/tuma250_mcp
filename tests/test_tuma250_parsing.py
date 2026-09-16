"""
Unit tests for tuma250_mcp/parsing.py.

Uses mock ElementHandle objects to test parsing logic without a real browser.
Selectors are verified against the live Tuma250 site (WooCommerce 10.5.2, Flatsome theme).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tuma250_mcp.parsing import (
    _parse_price,
    parse_cart_item,
    parse_cart_totals,
    parse_order_detail_item,
    parse_order_row,
    parse_product_availability,
    parse_product_card,
    parse_product_variations,
    parse_woocommerce_notices,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_element(
    text_value: str | None = None, attr_value: str | None = None
) -> AsyncMock:
    """Build a mock ElementHandle that returns fixed text/attribute values."""
    el = AsyncMock()
    el.inner_text = AsyncMock(return_value=text_value or "")
    el.get_attribute = AsyncMock(return_value=attr_value)
    return el


def _mock_card(
    product_id: str = "194006",
    name: str = "Rice 1kg",
    price_text: str = "RWF\xa01,500",
    url: str = "https://tuma250.com/product/rice-1kg/",
    css_class: str = "product-small col has-hover instock type-product",
    out_of_stock_label: bool = False,
) -> AsyncMock:
    """
    Build a mock ElementHandle representing a div.product-small card.

    query_selector returns different mocks depending on the CSS selector.
    """
    card = AsyncMock()
    card.get_attribute = AsyncMock(return_value=css_class)

    async def query_selector(selector: str) -> AsyncMock | None:
        if selector == "[data-product_id]":
            return _mock_element(attr_value=product_id)
        if selector == ".woocommerce-loop-product__title":
            return _mock_element(text_value=name)
        if selector == ".price .woocommerce-Price-amount":
            return _mock_element(text_value=price_text)
        if selector == "a.woocommerce-LoopProduct-link":
            return _mock_element(attr_value=url)
        if selector == ".product-category":
            return None
        if selector == ".out-of-stock-label":
            return _mock_element(text_value="OUT OF STOCK") if out_of_stock_label else None
        return None

    card.query_selector = query_selector
    return card


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------


def test_parse_price_rwf_format() -> None:
    """RWF price with non-breaking space and comma is parsed correctly."""
    assert _parse_price("RWF\xa01,500") == 1500.0


def test_parse_price_plain_float() -> None:
    """Plain decimal string is parsed correctly."""
    assert _parse_price("3000.00") == 3000.0


def test_parse_price_none_input() -> None:
    """None input returns None without raising."""
    assert _parse_price(None) is None


def test_parse_price_unparseable_returns_none() -> None:
    """Non-numeric string returns None."""
    assert _parse_price("N/A") is None


# ---------------------------------------------------------------------------
# parse_product_card
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_product_card_extracts_all_fields() -> None:
    """parse_product_card extracts id, name, price, and url from a card element."""
    card = _mock_card(
        product_id="194006",
        name="Imperial Leather Jasmine & Rice Bathing Soap 150g",
        price_text="RWF\xa01,500",
        url="https://tuma250.com/product/imperial-leather-jasmine-rice-bathing-soap-150g/",
    )

    result = await parse_product_card(card)

    assert result["id"] == "194006"
    assert result["slug"] == "imperial-leather-jasmine-rice-bathing-soap-150g"
    assert result["name"] == "Imperial Leather Jasmine & Rice Bathing Soap 150g"
    assert result["price"] == 1500.0
    assert (
        result["url"]
        == "https://tuma250.com/product/imperial-leather-jasmine-rice-bathing-soap-150g/"
    )
    assert result["in_stock"] is True


@pytest.mark.asyncio
async def test_parse_product_card_handles_missing_price() -> None:
    """parse_product_card returns price=None when the price element is absent."""
    card = AsyncMock()
    card.get_attribute = AsyncMock(
        return_value="product-small col has-hover instock type-product"
    )

    async def query_selector(selector: str) -> AsyncMock | None:
        if selector == "[data-product_id]":
            return _mock_element(attr_value="999")
        if selector == ".woocommerce-loop-product__title":
            return _mock_element(text_value="Out of Stock Product")
        if selector == ".price .woocommerce-Price-amount":
            return None  # price element absent
        if selector == "a.woocommerce-LoopProduct-link":
            return _mock_element(attr_value="https://tuma250.com/product/oos/")
        if selector == ".out-of-stock-label":
            return None
        return None

    card.query_selector = query_selector

    result = await parse_product_card(card)

    assert result["price"] is None
    assert result["id"] == "999"


@pytest.mark.asyncio
async def test_parse_product_card_detects_out_of_stock() -> None:
    """Cards with the WooCommerce outofstock class are reported as unavailable."""
    card = _mock_card(
        name="Passion Fruit ( Maracuja /Amatunda)",
        url="https://tuma250.com/shop/fruits-vegetables/fruits/passion-fruit-maracuja/",
        css_class=(
            "product-small col has-hover out-of-stock product type-product "
            "outofstock product-type-variable"
        ),
        out_of_stock_label=True,
    )

    result = await parse_product_card(card)

    assert result["in_stock"] is False
    assert result["slug"] == "passion-fruit-maracuja"


# ---------------------------------------------------------------------------
# parse_cart_item
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_cart_item_defaults_qty_to_1_when_missing() -> None:
    """parse_cart_item defaults qty to 1 when the qty input is absent."""
    row = AsyncMock()

    async def query_selector(selector: str) -> AsyncMock | None:
        if selector == "[data-product_id]":
            return _mock_element(attr_value="p1")
        if selector == ".product-name a":
            return _mock_element(
                text_value="Rice 1kg",
                attr_value="https://tuma250.com/product/rice-1kg/",
            )
        if selector == "input.qty":
            return None  # qty input absent
        if selector == ".product-price .woocommerce-Price-amount":
            return _mock_element(text_value="RWF\xa01,500")
        if selector == ".product-subtotal .woocommerce-Price-amount":
            return _mock_element(text_value="RWF\xa01,500")
        return None

    row.query_selector = query_selector

    result = await parse_cart_item(row)

    assert result["qty"] == 1
    assert result["product_id"] == "p1"
    assert result["slug"] == "rice-1kg"
    assert result["variation_attributes"] is None
    assert result["price"] == 1500.0


@pytest.mark.asyncio
async def test_parse_cart_item_extracts_slug_and_variation_from_link() -> None:
    """parse_cart_item derives slug and variation_attributes from product link URL."""
    row = AsyncMock()

    async def query_selector(selector: str) -> AsyncMock | None:
        if selector == "[data-product_id]":
            return _mock_element(attr_value="54913")
        if selector == ".product-name a":
            return _mock_element(
                text_value="Fresh Carrots 500g",
                attr_value="https://tuma250.com/product/fresh-carrots-1kg/?attribute_quantity=500g",
            )
        if selector == "input.qty":
            return _mock_element(attr_value="2")
        if selector == ".product-price .woocommerce-Price-amount":
            return _mock_element(text_value="RWF\xa0800")
        if selector == ".product-subtotal .woocommerce-Price-amount":
            return _mock_element(text_value="RWF\xa01,600")
        return None

    row.query_selector = query_selector

    result = await parse_cart_item(row)

    assert result["product_id"] == "54913"
    assert result["slug"] == "fresh-carrots-1kg"
    assert result["variation_attributes"] == {"attribute_quantity": "500g"}
    assert result["qty"] == 2


# ---------------------------------------------------------------------------
# parse_order_row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_order_row_parses_total() -> None:
    """parse_order_row correctly parses the order total amount."""
    row = AsyncMock()

    async def query_selector(selector: str) -> AsyncMock | None:
        if selector == ".woocommerce-orders-table__cell-order-number a":
            return _mock_element(
                text_value="#12345",
                attr_value="https://tuma250.com/my-account/view-order/12345/",
            )
        if selector == ".woocommerce-orders-table__cell-order-date":
            return _mock_element(text_value="January 15, 2026")
        if selector == ".woocommerce-orders-table__cell-order-status":
            return _mock_element(text_value="Completed")
        if (
            selector
            == ".woocommerce-orders-table__cell-order-total .woocommerce-Price-amount"
        ):
            return _mock_element(text_value="RWF\xa045,000")
        return None

    row.query_selector = query_selector

    result = await parse_order_row(row)

    assert result["order_id"] == "12345"
    assert result["total"] == 45000.0
    assert result["status"] == "Completed"
    assert result["link"] == "https://tuma250.com/my-account/view-order/12345/"


# ---------------------------------------------------------------------------
# parse_order_detail_item
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_order_detail_item_derives_slug_from_url() -> None:
    """parse_order_detail_item derives slug from the product URL."""
    row = AsyncMock()

    async def query_selector(selector: str) -> AsyncMock | None:
        if selector == ".woocommerce-table__product-name a":
            return _mock_element(
                text_value="Rice Basmati 5kg",
                attr_value="https://tuma250.com/product/rice-basmati-5kg/",
            )
        if selector == ".woocommerce-table__product-name":
            return _mock_element(text_value="Rice Basmati 5kg × 2")
        if selector == ".woocommerce-table__product-total .woocommerce-Price-amount":
            return _mock_element(text_value="RWF\xa030,000")
        return None

    row.query_selector = query_selector

    result = await parse_order_detail_item(row)

    assert result["slug"] == "rice-basmati-5kg"
    assert result["name"] == "Rice Basmati 5kg"
    assert result["qty"] == 2
    assert result["price"] == 30000.0


# ---------------------------------------------------------------------------
# parse_product_variations
# ---------------------------------------------------------------------------

_VARIATIONS_JSON = '[{"variation_id":54912,"attributes":{"attribute_quantity":"250g"},"display_price":400,"is_in_stock":true},{"variation_id":54913,"attributes":{"attribute_quantity":"500g"},"display_price":800,"is_in_stock":true},{"variation_id":54914,"attributes":{"attribute_quantity":"1kg"},"display_price":1600,"is_in_stock":false}]'


@pytest.mark.asyncio
async def test_parse_product_variations_returns_all_variants() -> None:
    """parse_product_variations returns one entry per variant with clean attributes."""
    page = AsyncMock()
    form = AsyncMock()
    form.get_attribute = AsyncMock(return_value=_VARIATIONS_JSON)
    page.query_selector = AsyncMock(return_value=form)

    results = await parse_product_variations(page)

    assert len(results) == 3
    assert results[0]["variation_id"] == "54912"
    assert results[0]["attributes"] == {"quantity": "250g"}
    assert results[0]["raw_attributes"] == {"attribute_quantity": "250g"}
    assert results[0]["price"] == 400.0
    assert results[0]["in_stock"] is True
    assert results[2]["in_stock"] is False


@pytest.mark.asyncio
async def test_parse_product_variations_empty_for_simple_product() -> None:
    """parse_product_variations returns [] when no variations form is present."""
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)

    results = await parse_product_variations(page)

    assert results == []


@pytest.mark.asyncio
async def test_parse_product_variations_handles_malformed_json() -> None:
    """parse_product_variations returns [] when the JSON blob is invalid."""
    page = AsyncMock()
    form = AsyncMock()
    form.get_attribute = AsyncMock(return_value="not-valid-json")
    page.query_selector = AsyncMock(return_value=form)

    results = await parse_product_variations(page)

    assert results == []


# ---------------------------------------------------------------------------
# parse_woocommerce_notices / parse_product_availability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_woocommerce_notices_extracts_error_text() -> None:
    """WooCommerce error notices are returned as type=error with visible text."""
    page = AsyncMock()
    error_el = _mock_element(
        text_value=(
            'You cannot add "Passion Fruit ( Maracuja /Amatunda) - 250g" '
            "to the cart because the product is out of stock."
        )
    )
    empty: list[Any] = []

    async def query_selector_all(selector: str) -> list[AsyncMock]:
        if selector == ".woocommerce-error":
            return [error_el]
        return empty

    page.query_selector_all = query_selector_all

    notices = await parse_woocommerce_notices(page)

    assert notices == [
        {
            "type": "error",
            "text": (
                'You cannot add "Passion Fruit ( Maracuja /Amatunda) - 250g" '
                "to the cart because the product is out of stock."
            ),
        }
    ]


def _mock_product_page(
    *,
    product_class: str,
    name: str = "Passion Fruit ( Maracuja /Amatunda)",
    stock_text: str | None = "Out of stock",
    og_availability: str | None = "out of stock",
    variations_json: str | None = None,
    variation_id: str | None = "0",
) -> AsyncMock:
    """Build a product-page mock covering availability selectors."""
    page = AsyncMock()
    product = AsyncMock()
    product.get_attribute = AsyncMock(return_value=product_class)
    product.query_selector = AsyncMock(
        return_value=_mock_element(text_value=stock_text) if stock_text else None
    )

    async def query_selector(selector: str) -> AsyncMock | None:
        if selector == "div.product.type-product":
            return product
        if selector == "h1.product_title, h1.product-title":
            return _mock_element(text_value=name)
        if selector == 'meta[property="og:availability"]':
            return (
                _mock_element(attr_value=og_availability) if og_availability else None
            )
        if selector == "form.variations_form[data-product_variations]":
            if not variations_json:
                return None
            form = AsyncMock()
            form.get_attribute = AsyncMock(return_value=variations_json)
            return form
        if selector == "form.variations_form":
            return AsyncMock() if variations_json else None
        if selector == "input.variation_id":
            return _mock_element(attr_value=variation_id) if variation_id else None
        return None

    page.query_selector = query_selector
    return page


_OOS_VARIATIONS_JSON = (
    '[{"variation_id":48915,"attributes":{"attribute_weight":"250g"},'
    '"display_price":850,"is_in_stock":false},'
    '{"variation_id":48916,"attributes":{"attribute_weight":"500g"},'
    '"display_price":1650,"is_in_stock":false}]'
)


@pytest.mark.asyncio
async def test_parse_product_availability_detects_out_of_stock_variable() -> None:
    """A variable product with every variant OOS is reported as unavailable."""
    page = _mock_product_page(
        product_class="product type-product outofstock product-type-variable",
        variations_json=_OOS_VARIATIONS_JSON,
    )

    result = await parse_product_availability(page)

    assert result["missing"] is False
    assert result["in_stock"] is False
    assert result["is_variable"] is True
    assert result["name"] == "Passion Fruit ( Maracuja /Amatunda)"


@pytest.mark.asyncio
async def test_parse_product_availability_simple_in_stock() -> None:
    """A simple in-stock product is reported as available."""
    page = _mock_product_page(
        product_class="product type-product instock product-type-simple",
        name="Papaya Fruit/pcs",
        stock_text=None,
        og_availability="in stock",
        variations_json=None,
        variation_id=None,
    )

    result = await parse_product_availability(page)

    assert result["missing"] is False
    assert result["in_stock"] is True
    assert result["is_variable"] is False
    assert result["name"] == "Papaya Fruit/pcs"


@pytest.mark.asyncio
async def test_parse_product_availability_missing_product() -> None:
    """Pages without a product wrapper are flagged as missing."""
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)

    result = await parse_product_availability(page)

    assert result["missing"] is True
    assert result["in_stock"] is False


@pytest.mark.asyncio
async def test_parse_product_availability_selected_variant_out_of_stock() -> None:
    """Selecting an OOS variant marks the product unavailable even if others are in stock."""
    page = _mock_product_page(
        product_class="product type-product instock product-type-variable",
        name="Fresh Carrots",
        stock_text=None,
        og_availability="in stock",
        variations_json=_VARIATIONS_JSON,
        variation_id="54914",
    )

    result = await parse_product_availability(page)

    assert result["in_stock"] is False
    assert result["is_variable"] is True
