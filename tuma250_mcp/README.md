# tuma250-mcp

An [MCP](https://modelcontextprotocol.io) server for the [Tuma 250](https://tuma250.com) WooCommerce grocery site (Kigali, Rwanda).

Gives any MCP-compatible AI client (Cursor, Claude Desktop, etc.) the ability to search products, manage a shopping cart, and browse order history on Tuma250 — using a headless Playwright browser under the hood.

## Tools

| Tool | Description |
|------|-------------|
| `login` | Authenticate and persist the browser session |
| `search_products` | Search for products by keyword |
| `get_product_variations` | List available variants (size/weight) for a variable product |
| `add_to_cart` | Add a product (or specific variant) to the cart |
| `get_cart` | Retrieve cart contents with full cost breakdown |
| `list_recent_orders` | List recent orders from My Account |
| `get_order_details` | Fetch line items for a specific order |

## Installation

```bash
pip install tuma250-mcp
playwright install chromium
```

## Configuration

The server reads credentials from environment variables (or a `.env` file):

```env
TUMA250_BASE_URL=https://tuma250.com
TUMA250_USERNAME=your-email@example.com
TUMA250_PASSWORD=your-password

# Optional
TUMA250_SESSION_FILE=.tuma250_session.json  # persists login between runs
TUMA250_DEBUG=false                          # set true for headed browser
```

## Usage

### Cursor / Claude Desktop

Add to `~/.cursor/mcp.json` / `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tuma250": {
      "command": "uvx",
      "args": ["tuma250-mcp"],
      "env": {
        "TUMA250_BASE_URL": "https://tuma250.com",
        "TUMA250_USERNAME": "your-email@example.com",
        "TUMA250_PASSWORD": "your-password"
      }
    }
  }
}
```

### Direct (stdio)

```bash
TUMA250_USERNAME=you@example.com TUMA250_PASSWORD=secret tuma250-mcp
```

## Session persistence

After the first successful login, the browser session (cookies) is saved to `TUMA250_SESSION_FILE` (default: `.tuma250_session.json`). Subsequent runs reuse the saved session and skip the login step entirely.

## Variable products

Some products on Tuma250 require a size/weight selection before they can be added to the cart. Use `get_product_variations` to discover the available options first:

```
1. search_products("fresh carrots")          → returns product URL
2. get_product_variations(product_url)       → lists 250g / 500g / 1kg variants
3. add_to_cart(product_id, variation_id, variation_attributes)
```

## License

MIT
