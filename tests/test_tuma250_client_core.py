"""
Unit tests for tuma250_mcp/client_core.py.

All Playwright browser interactions are mocked — no real browser is launched.
Tests focus on the logic inside Tuma250Client methods, not on the DOM selectors.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tuma250_mcp.client_core import Tuma250Client
from tuma250_mcp.config import Tuma250Settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings(**kwargs: Any) -> Tuma250Settings:
    """Build a minimal Tuma250Settings instance for testing."""
    defaults = {
        "base_url": "https://tuma250.com",
        "username": "test@example.com",
        "password": "secret",
        "session_file": "/tmp/test_session.json",
        "debug": False,
    }
    defaults.update(kwargs)
    return Tuma250Settings.model_construct(**defaults)


def _make_client(settings: Tuma250Settings | None = None) -> Tuma250Client:
    """Build a Tuma250Client with a pre-wired mock page."""
    client = Tuma250Client(settings=settings or _make_settings())
    client._page = AsyncMock()
    client._context = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# ensure_logged_in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_logged_in_skips_when_already_authenticated() -> None:
    """If _is_logged_in returns True, the login form is never filled."""
    client = _make_client()

    with patch.object(
        client, "_is_logged_in", new_callable=AsyncMock, return_value=True
    ):
        await client.ensure_logged_in()

    client._page.fill.assert_not_called()
    client._page.click.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_logged_in_fills_form_and_saves_session() -> None:
    """If not logged in, the form is filled, submitted, and session is saved."""
    client = _make_client()
    client._page.wait_for_load_state = AsyncMock()

    # First call (check) → not logged in; second call (verify) → logged in
    with (
        patch.object(
            client,
            "_is_logged_in",
            new_callable=AsyncMock,
            side_effect=[False, True],
        ),
        patch.object(client, "_save_session", new_callable=AsyncMock) as mock_save,
    ):
        await client.ensure_logged_in()

    client._page.fill.assert_any_call("#username", "test@example.com")
    client._page.fill.assert_any_call("#password", "secret")
    client._page.click.assert_called_once_with("button[name='login']")
    mock_save.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_logged_in_raises_on_failure() -> None:
    """If login fails (still not authenticated after submit), RuntimeError is raised."""
    client = _make_client()
    client._page.wait_for_load_state = AsyncMock()

    # Both checks return False → login failed
    with patch.object(
        client,
        "_is_logged_in",
        new_callable=AsyncMock,
        return_value=False,
    ):
        with pytest.raises(RuntimeError, match="Login failed"):
            await client.ensure_logged_in()


# ---------------------------------------------------------------------------
# search_products
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_products_returns_parsed_cards() -> None:
    """search_products returns one dict per product card found on the page."""
    client = _make_client()
    client._page.wait_for_load_state = AsyncMock()

    # Two mock card handles
    mock_card_1 = AsyncMock()
    mock_card_2 = AsyncMock()
    client._page.query_selector_all = AsyncMock(return_value=[mock_card_1, mock_card_2])

    parsed_product = {
        "id": "123",
        "name": "Rice 1kg",
        "brand": None,
        "package_size": None,
        "unit": None,
        "price": 1500.0,
        "url": "https://tuma250.com/product/rice-1kg/",
        "category_path": None,
    }

    with (
        patch.object(client, "ensure_logged_in", new_callable=AsyncMock),
        patch(
            "tuma250_mcp.client_core.parse_product_card",
            new_callable=AsyncMock,
            return_value=parsed_product,
        ),
    ):
        results = await client.search_products("rice", max_results=10)

    assert len(results) == 2
    assert results[0]["id"] == "123"
    assert results[0]["name"] == "Rice 1kg"


@pytest.mark.asyncio
async def test_search_products_respects_max_results() -> None:
    """search_products caps results at max_results even if more cards exist."""
    client = _make_client()
    client._page.wait_for_load_state = AsyncMock()

    # 5 cards returned by the page
    client._page.query_selector_all = AsyncMock(
        return_value=[AsyncMock() for _ in range(5)]
    )

    parsed_product = {
        "id": "1",
        "name": "Rice",
        "brand": None,
        "package_size": None,
        "unit": None,
        "price": None,
        "url": None,
        "category_path": None,
    }

    with (
        patch.object(client, "ensure_logged_in", new_callable=AsyncMock),
        patch(
            "tuma250_mcp.client_core.parse_product_card",
            new_callable=AsyncMock,
            return_value=parsed_product,
        ),
    ):
        results = await client.search_products("rice", max_results=2)

    assert len(results) == 2


# ---------------------------------------------------------------------------
# add_to_cart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_to_cart_success() -> None:
    """add_to_cart returns success=True when the product appears in the cart."""
    client = _make_client()
    client._page.wait_for_load_state = AsyncMock()

    cart_with_item = {
        "items": [
            {
                "product_id": "42",
                "name": "Rice",
                "qty": 1,
                "price": 1500.0,
                "subtotal": 1500.0,
            }
        ],
        "total_items": 1,
        "subtotal": 1500.0,
        "shipping_options": [{"label": "Same Day Delivery", "price": 1800.0}],
        "total": 3300.0,
    }

    with (
        patch.object(client, "ensure_logged_in", new_callable=AsyncMock),
        patch.object(
            client, "get_cart", new_callable=AsyncMock, return_value=cart_with_item
        ),
    ):
        result = await client.add_to_cart("42", quantity=1)

    assert result["success"] is True
    assert result["cart_total_items"] == 1


@pytest.mark.asyncio
async def test_add_to_cart_failure() -> None:
    """add_to_cart returns success=False when the product is absent from the cart."""
    client = _make_client()
    empty_cart = {
        "items": [],
        "total_items": 0,
        "subtotal": 0.0,
        "shipping_options": [],
        "total": 0.0,
    }

    with (
        patch.object(client, "ensure_logged_in", new_callable=AsyncMock),
        patch.object(
            client, "get_cart", new_callable=AsyncMock, return_value=empty_cart
        ),
        patch.object(
            client,
            "_resolve_slug_to_product_id",
            new_callable=AsyncMock,
            return_value="99",
        ),
    ):
        result = await client.add_to_cart("p99", quantity=1)

    assert result["success"] is False


# ---------------------------------------------------------------------------
# get_cart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cart_empty() -> None:
    """get_cart returns an empty items list when the cart has no rows."""
    client = _make_client()
    client._page.wait_for_load_state = AsyncMock()
    client._page.query_selector_all = AsyncMock(return_value=[])

    with (
        patch.object(client, "ensure_logged_in", new_callable=AsyncMock),
        patch(
            "tuma250_mcp.client_core.parse_cart_totals",
            new_callable=AsyncMock,
            return_value={
                "total_items": 0,
                "subtotal": None,
                "shipping": [],
                "total": None,
            },
        ),
    ):
        result = await client.get_cart()

    assert result["items"] == []
    assert result["total_items"] == 0


# ---------------------------------------------------------------------------
# list_recent_orders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_recent_orders_capped_at_limit() -> None:
    """list_recent_orders returns at most `limit` orders."""
    client = _make_client()
    client._page.wait_for_load_state = AsyncMock()

    # 10 mock row handles
    client._page.query_selector_all = AsyncMock(
        return_value=[AsyncMock() for _ in range(10)]
    )

    parsed_order = {
        "order_id": "100",
        "date": "2026-01-01",
        "status": "completed",
        "total": 5000.0,
        "link": None,
    }

    with (
        patch.object(client, "ensure_logged_in", new_callable=AsyncMock),
        patch(
            "tuma250_mcp.client_core.parse_order_row",
            new_callable=AsyncMock,
            return_value=parsed_order,
        ),
    ):
        results = await client.list_recent_orders(limit=3)

    assert len(results) == 3


# ---------------------------------------------------------------------------
# get_order_details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_to_cart_variable_product_includes_variation_in_url() -> None:
    """add_to_cart with a variation_id navigates to the correct URL."""
    client = _make_client()
    client._page.wait_for_load_state = AsyncMock()

    navigated_urls: list[str] = []

    async def fake_goto(url: str, **_: Any) -> None:
        navigated_urls.append(url)

    client._page.goto = fake_goto

    cart_with_item = {
        "items": [
            {
                "product_id": "54913",
                "name": "Fresh Carrots 500g",
                "qty": 1,
                "price": 800.0,
                "subtotal": 800.0,
            }
        ],
        "total_items": 1,
        "subtotal": 800.0,
        "shipping_options": [],
        "total": 800.0,
    }

    with (
        patch.object(client, "ensure_logged_in", new_callable=AsyncMock),
        patch.object(
            client, "get_cart", new_callable=AsyncMock, return_value=cart_with_item
        ),
    ):
        result = await client.add_to_cart(
            "12295",
            quantity=1,
            variation_id="54913",
            variation_attributes={"attribute_quantity": "500g"},
        )

    assert result["success"] is True
    assert any("variation_id=54913" in u for u in navigated_urls)
    assert any("attribute_quantity=500g" in u for u in navigated_urls)


@pytest.mark.asyncio
async def test_get_product_variations_delegates_to_parse() -> None:
    """get_product_variations navigates to the URL and returns parsed variants."""
    client = _make_client()
    client._page.wait_for_load_state = AsyncMock()

    parsed_variants = [
        {
            "variation_id": "54912",
            "attributes": {"quantity": "250g"},
            "raw_attributes": {"attribute_quantity": "250g"},
            "price": 400.0,
            "in_stock": True,
        },
    ]

    with (
        patch.object(client, "ensure_logged_in", new_callable=AsyncMock),
        patch(
            "tuma250_mcp.client_core.parse_product_variations",
            new_callable=AsyncMock,
            return_value=parsed_variants,
        ),
    ):
        result = await client.get_product_variations(
            "https://tuma250.com/product/fresh-carrots-1kg/"
        )

    assert len(result) == 1
    assert result[0]["variation_id"] == "54912"


@pytest.mark.asyncio
async def test_get_order_details_returns_items() -> None:
    """get_order_details returns a dict with the order_id and parsed items list."""
    client = _make_client()
    client._page.wait_for_load_state = AsyncMock()

    mock_row = AsyncMock()
    client._page.query_selector_all = AsyncMock(return_value=[mock_row, mock_row])

    parsed_item = {
        "product_id": "p1",
        "name": "Rice",
        "qty": 2,
        "price": 3000.0,
        "brand": None,
        "unit_size": None,
        "category_path": None,
    }

    with (
        patch.object(client, "ensure_logged_in", new_callable=AsyncMock),
        patch(
            "tuma250_mcp.client_core.parse_order_detail_item",
            new_callable=AsyncMock,
            return_value=parsed_item,
        ),
    ):
        result = await client.get_order_details("order-123")

    assert result["order_id"] == "order-123"
    assert len(result["items"]) == 2
    assert result["items"][0]["name"] == "Rice"
