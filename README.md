# OpenCart MCP Server

Query and edit your OpenCart store from Claude Code. Products, orders, customers, Journal3 modules, SEO URLs, CMS pages — 36 tools, all through natural language.

Built for store owners and developers who are tired of SSH + phpMyAdmin + admin panel clicking to get simple answers.

```
"Which products are low on stock?"
"Update the meta description for category 25"
"Show me today's orders over £50"
"Find the Journal3 module that contains our FAQ text and fix the typo"
```

It just works. You ask, Claude calls the right tool, you get the answer.

---

## Safe by default

This matters if you're connecting AI to a live store. Every decision here was made with that in mind.

- **Read-only queries** — `query()` only allows SELECT, SHOW, DESCRIBE, EXPLAIN
- **DDL is blocked** — DROP, ALTER, TRUNCATE, CREATE will never run, even through `run_sql()`
- **SSH tunnel** — database credentials stay inside the encrypted connection, never exposed
- **Path traversal blocked** — `get_file()` and `write_file()` reject `..` in paths
- **Write confirmation** — Claude Code prompts you before any write tool executes
- **Nothing runs on your server** — no agents, no daemons, no PHP files uploaded. The server runs on your machine and connects over SSH

You can point this at a production store and not worry about it doing something stupid.

---

## What can you actually do with it?

### Store owners
- "How many orders came in this week?" → instant sales summary with daily breakdown
- "What's running low?" → stock report sorted by quantity, lowest first
- "Update the price of product 47 to 29.99" → done, one confirmation click
- "Show me the About Us page content" → full CMS page, ready to review or edit

### Developers
- "Show me the schema for oc_order" → column definitions without opening phpMyAdmin
- "List all OCMOD modifications and their status" → instant audit
- "What extensions are installed?" → full list, no admin panel needed
- "Run this SELECT against the orders table" → custom SQL with safety rails

### Agencies managing multiple stores
- Run dev and live as separate MCP instances in the same Claude session
- `opencart_dev__get_products` vs `opencart_live__get_products` — no confusion
- Compare stock levels, settings, or module content across environments

### Journal3 users
- List, inspect, and edit J3 modules — FAQ accordions, sliders, banners, product tabs
- Read and update theme settings and skin settings per skin
- **Find/replace inside module JSON** — safely change text without rewriting the entire module
- J3 tools return empty results (not errors) if Journal3 isn't installed, so the server works with any theme

---

## How it compares

| Task | Admin panel | SSH + SQL | This MCP server |
|------|------------|-----------|----------------|
| Check stock levels | Click through pages | Write a query, run it | "What's low on stock?" |
| Update a product price | Find product, edit, save | UPDATE query by hand | "Set product 47 to £29.99" |
| Read a J3 module | JSON blob in the database | Copy-paste from phpMyAdmin | "Show me module 505" |
| Edit FAQ text | Find module, decode JSON, edit, re-encode | Pain | "Replace X with Y in module 505" |
| Sales report | Reports page, manually filter | Write aggregation queries | "Sales summary for the last 7 days" |
| Check SEO URLs | Admin > Marketing > SEO URL, paginate | SELECT from oc_seo_url | "Show SEO URLs containing 'peptide'" |
| Manage CMS pages | Admin > Catalog > Information | Direct DB access | "Show me the About Us page" |

---

## Quick start

### 1. Install

```bash
git clone https://github.com/chrisbray85/opencart-mcp.git
cd opencart-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
```

Fill in your server details:

```env
OPENCART_SSH_HOST=your-server-ip
OPENCART_SSH_USER=your-ssh-username
OPENCART_SSH_KEY=~/.ssh/id_ed25519
OPENCART_DB_USER=your_db_user
OPENCART_DB_PASS=your_db_password
OPENCART_DB_NAME=your_opencart_database
OPENCART_ROOT=/path/to/opencart
OPENCART_STORAGE=/path/to/storage
```

> **Where to find your paths:**
> - `OPENCART_ROOT` — the directory containing `index.php`, `admin/`, `catalog/`, `system/`
> - `OPENCART_STORAGE` — check your `config.php` for the `DIR_STORAGE` value (often outside the web root on OpenCart 3.0.3.3+)

### 3. Test the connection

```bash
source .venv/bin/activate
PYTHONPATH=src python -c "
from opencart_mcp.config import Config
from opencart_mcp.db import OpenCartDB
import json

config = Config.from_env()
db = OpenCartDB(config)
result = db.run_query('SELECT COUNT(*) as product_count FROM oc_product WHERE status = 1')
print(json.dumps(result, indent=2))
db.close()
"
```

You should see something like `[{"product_count": "42"}]`. If not, check [Troubleshooting](#troubleshooting).

### 4. Add to Claude Code

<details>
<summary><strong>VS Code (Claude Code extension)</strong></summary>

Open your VS Code settings JSON (`Cmd+Shift+P` → "Open User Settings (JSON)") and add:

```json
{
  "claude.mcpServers": {
    "opencart": {
      "command": "/absolute/path/to/opencart-mcp/.venv/bin/python",
      "args": ["-m", "opencart_mcp.server"],
      "cwd": "/absolute/path/to/opencart-mcp",
      "env": {
        "PYTHONPATH": "/absolute/path/to/opencart-mcp/src",
        "OPENCART_SSH_HOST": "your-server-ip",
        "OPENCART_SSH_USER": "your-ssh-username",
        "OPENCART_SSH_KEY": "~/.ssh/id_ed25519",
        "OPENCART_DB_USER": "your_db_user",
        "OPENCART_DB_PASS": "your_db_password",
        "OPENCART_DB_NAME": "your_opencart_database",
        "OPENCART_ROOT": "/path/to/opencart",
        "OPENCART_STORAGE": "/path/to/storage"
      }
    }
  }
}
```

Restart VS Code and the tools will appear in the Claude Code panel.

</details>

<details>
<summary><strong>Claude Code CLI</strong></summary>

Add to `~/.claude.json` (global) or `.claude/settings.json` (project-level):

```json
{
  "mcpServers": {
    "opencart": {
      "command": "/absolute/path/to/opencart-mcp/.venv/bin/python",
      "args": ["-m", "opencart_mcp.server"],
      "cwd": "/absolute/path/to/opencart-mcp",
      "env": {
        "PYTHONPATH": "/absolute/path/to/opencart-mcp/src",
        "OPENCART_SSH_HOST": "your-server-ip",
        "OPENCART_SSH_USER": "your-ssh-username",
        "OPENCART_SSH_KEY": "~/.ssh/id_ed25519",
        "OPENCART_DB_USER": "your_db_user",
        "OPENCART_DB_PASS": "your_db_password",
        "OPENCART_DB_NAME": "your_opencart_database",
        "OPENCART_ROOT": "/path/to/opencart",
        "OPENCART_STORAGE": "/path/to/storage"
      }
    }
  }
}
```

Restart Claude Code and the tools load automatically.

</details>

<details>
<summary><strong>JetBrains (Claude Code extension)</strong></summary>

JetBrains uses the same `~/.claude.json` configuration as the CLI. Follow the CLI instructions above and restart your IDE.

</details>

---

## Example prompts

These all work out of the box. Just type them into Claude Code.

**Products & stock**
```
"Show me all products with less than 5 in stock"
"Get full details for product 123 including options and images"
"Search for products with 'collagen' in the name"
"Update the price of product 47 to 34.99"
```

**Orders & customers**
```
"Show me today's orders"
"Get order 5892 with line items and status history"
"Find customer john@example.com — how many orders have they placed?"
"Sales summary for the last 7 days with top sellers"
```

**SEO & content**
```
"List all SEO URLs containing 'research'"
"Update the SEO URL for product 23 to 'bpc-157-5mg'"
"Show me the FAQ page content"
"Replace 'old company name' with 'new company name' in the About Us page"
```

**Journal3 theme**
```
"List all Journal3 FAQ modules"
"Show me the full content of module 505"
"Replace 'Free shipping over £50' with 'Free shipping over £75' in the banner module"
"What skin settings are configured for skin 1?"
```

**Technical**
```
"Show the schema for oc_order_product"
"List all tables matching 'journal3'"
"Run: SELECT order_id, total FROM oc_order WHERE total > 100 ORDER BY date_added DESC LIMIT 10"
"What OCMOD modifications are active?"
```

---

## All 36 tools

### Read (24)

| Tool | What it does |
|------|-------------|
| `get_products` | Search products with stock, prices, SEO data. Filter by category |
| `get_product` | Full product details — images, options, categories, attributes |
| `get_orders` | Recent orders filtered by status and date range |
| `get_order` | Full order with line items, totals, status history |
| `get_customers` | Search by name/email with order count and total spent |
| `get_categories` | Category tree with product counts and SEO URLs |
| `get_stock_report` | All products sorted by stock level (lowest first) |
| `get_settings` | OpenCart core settings by group/key |
| `get_j3_settings` | Journal3 theme settings |
| `get_j3_skin_settings` | Journal3 skin/layout settings per skin |
| `get_modules` | Journal3 modules by type — search content within modules |
| `get_j3_module` | Full module JSON data for any J3 module |
| `get_information_pages` | List CMS pages (About Us, FAQ, T&Cs) with content preview |
| `get_information_page` | Full HTML content of a single CMS/information page |
| `get_order_statuses` | All order status mappings with IDs |
| `get_product_attributes` | Product attributes (weight, storage conditions, etc.) |
| `sales_summary` | Revenue, top sellers, daily stats for any period |
| `get_modifications` | OCMOD modifications with status |
| `get_extensions` | Installed extensions list |
| `get_seo_urls` | SEO URL mappings with filtering |
| `query` | Custom read-only SQL (SELECT/SHOW/DESCRIBE/EXPLAIN only) |
| `get_table_schema` | Column definitions for any table |
| `list_tables` | List tables matching a pattern |
| `get_file` | Read files from the server (path traversal blocked) |

### Write (12)

| Tool | What it does |
|------|-------------|
| `update_product` | Update price, stock, name, SEO title, meta description |
| `update_setting` | Change OpenCart core settings |
| `update_j3_setting` | Change Journal3 theme settings |
| `update_j3_skin_setting` | Change Journal3 skin settings |
| `update_j3_module` | Find/replace text within J3 module JSON (banners, FAQ, sliders) |
| `update_information` | Find/replace text within CMS page HTML (About Us, T&Cs, etc.) |
| `update_seo_url` | Create or update SEO URL mappings |
| `update_category` | Update category name, meta, status |
| `write_file` | Write files to server via SFTP |
| `run_sql` | Execute INSERT/UPDATE/DELETE (DDL blocked) |
| `clear_cache` | Flush OpenCart + Journal3 caches |
| `refresh_modifications` | Recompile OCMOD modification cache |

---

## How it works

```
Your machine                          Your server
┌──────────────┐                     ┌──────────────┐
│ Claude Code  │                     │              │
│      ↓       │     SSH tunnel      │   PHP cli    │
│  MCP Server  │ ──────────────────→ │      ↓       │
│  (Python)    │   PHP via stdin     │   MySQL      │
│              │ ←────────────────── │   (JSON)     │
└──────────────┘                     └──────────────┘
```

The server runs **on your machine**. It connects to your OpenCart server via SSH, pipes PHP to the remote interpreter via stdin, and gets JSON back. Nothing is installed on your server. No files uploaded, no cleanup, no ports opened.

- **PHP via stdin** — works with any PHP version, nothing written to disk
- **SSH tunnel** — credentials never leave the encrypted connection
- **Paramiko** — pure Python SSH, no system dependencies beyond Python 3.10+

---

## Multiple stores

Run dev and live as separate instances in the same Claude session:

```json
{
  "mcpServers": {
    "opencart_dev": {
      "command": "/path/to/opencart-mcp/.venv/bin/python",
      "args": ["-m", "opencart_mcp.server"],
      "cwd": "/path/to/opencart-mcp",
      "env": { "OPENCART_DB_NAME": "my_dev_database", "..." }
    },
    "opencart_live": {
      "command": "/path/to/opencart-mcp/.venv/bin/python",
      "args": ["-m", "opencart_mcp.server"],
      "cwd": "/path/to/opencart-mcp",
      "env": { "OPENCART_DB_NAME": "my_live_database", "..." }
    }
  }
}
```

Claude prefixes tools automatically — `opencart_dev__get_products` vs `opencart_live__get_products` — so there's no confusion about which store you're querying.

---

## Tested with

| Component | Versions |
|-----------|----------|
| OpenCart | 3.0.3.2 — 3.0.5.0 (any 3.x should work) |
| PHP | 5.6+ (server-side) |
| Python | 3.10+ (local machine) |
| Journal3 | 3.x (optional — everything works without it) |
| Hosting | VPS, dedicated servers, shared hosting with SSH |
| Clients | Claude Code CLI, VS Code extension, JetBrains extension |

Used daily on production stores with 100+ products, thousands of orders, and Journal3 theme.

---

## Troubleshooting

### SSH connection fails

```bash
# Test SSH works
ssh your-user@your-server "echo ok"

# Test PHP is available
ssh your-user@your-server "echo '<?php echo 1;' | php"
```

If SSH needs a password instead of a key:
```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub your-user@your-server
```

### Empty results

- Run the [test script](#3-test-the-connection) to check credentials
- `OPENCART_ROOT` should point to the directory containing `index.php`
- `OPENCART_STORAGE` should match `DIR_STORAGE` in your `config.php`

### cPanel / shared hosting

cPanel prints `tput: No value for $TERM` warnings over SSH. The server filters these automatically.

### Slow queries

Default timeout is 30 seconds. If queries are slow, check if SSH goes through a VPN (adds latency) or if the server is under load.

### Journal3 tables not found

Normal if you're not running Journal3. The J3 tools return empty results instead of errors.

### Common path issues

| Hosting | Typical OPENCART_ROOT | Typical OPENCART_STORAGE |
|---------|----------------------|-------------------------|
| cPanel | `/home/user/public_html` | `/home/user/oc_storage` |
| Plesk | `/var/www/vhosts/domain/httpdocs` | Above web root |
| Custom VPS | `/var/www/html` or `/var/www/opencart` | Varies |

Check your `config.php` — both `DIR_APPLICATION` and `DIR_STORAGE` are defined there.

---

## Roadmap

- [ ] OpenCart 4.x support
- [ ] Coupon and voucher management tools
- [ ] Order status update tool
- [ ] Bulk product import/export
- [ ] Customer group management
- [ ] Dashboard summary tool (one prompt, full store overview)

Got a feature request? [Open an issue](https://github.com/chrisbray85/opencart-mcp/issues).

---

## Changelog

### v0.3.0 (current)
- 3 new tools: `get_information_pages`, `get_information_page`, `update_information` for managing CMS pages
- 36 tools total (24 read + 12 write)

### v0.2.0
- VS Code / CLI / JetBrains setup instructions
- Multi-store configuration docs
- Compatibility table

### v0.1.0
- Initial release — 33 tools covering products, orders, customers, categories, settings, J3 modules
- SSH + PHP architecture
- Journal3 support with graceful fallback
- Security: read-only default, DDL blocked, path traversal protection

---

## Contributing

Issues and PRs welcome. If you're running this on a hosting setup or OpenCart version not listed above, let us know what works and what doesn't.

## License

MIT — see [LICENSE](LICENSE) for details.
