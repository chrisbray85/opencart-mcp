# LiveStore MCP Server

Fork of [chrisbray85/opencart-mcp](https://github.com/chrisbray85/opencart-mcp) for **LiveStore 3.x** (ocStore / OpenCart 3) with the **Technics** theme.

Query and edit your store from Claude Code or any MCP client. Products, orders, customers, Technics modules, SEO URLs, CMS pages, LiveStore blog — 49 tools, all through natural language.

Built for store owners and developers who are tired of SSH + phpMyAdmin + admin panel clicking to get simple answers.

```
"Which products are low on stock?"
"Update the meta H1 for category 25"
"Show me today's orders over 5000 RUB"
"Find the Technics slider module and fix the typo"
```

It just works. You ask, Claude calls the right tool, you get the answer.

> Prefer the terminal? Check out [**opencart-cli**](https://github.com/chrisbray85/opencart-cli) — same OpenCart understanding, pretty tables, sparklines, AI in the shell (`opencart ask "..."`), interactive REPL, live order watching. `pip install opencart-cli`.

---

## Safe by default

This matters if you're connecting AI to a live store. Every decision here was made with that in mind.

- **Read-only queries** — `query()` only allows SELECT, SHOW, DESCRIBE, EXPLAIN
- **DDL is blocked** — DROP, ALTER, TRUNCATE, CREATE will never run, even through `run_sql()`
- **Direct MySQL or SSH** — with SSH, database credentials stay inside the encrypted connection. Direct MySQL is for hosts without SSH (remote MySQL or a tunnel)
- **File tools refuse without SSH** — `get_file()`, `write_file()`, `clear_cache()`, and `refresh_modifications()` error in direct-MySQL mode instead of touching a local copy of the store
- **Path traversal blocked** — `get_file()` and `write_file()` reject `..` in paths
- **Write confirmation** — Claude Code prompts you before any write tool executes
- **License keys omitted** — Technics license settings are not returned or updated
- **Nothing runs on your server** — no agents, no daemons, no PHP files uploaded. The server runs on your machine

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

### Technics / LiveStore
- List, inspect, and edit Technics modules (sliders, promo, product tabs) stored as JSON in `oc_module`
- Read and update Technics theme settings in `oc_setting` (`theme_technics*`)
- Technics blog, news, product sets, and callback requests
- LiveStore built-in articles (`oc_article`) plus `meta_h1` / `noindex` on products and categories
- Layouts and layout-module assignments

---

## How it compares

| Task | Admin panel | SSH + SQL | This MCP server |
|------|------------|-----------|----------------|
| Check stock levels | Click through pages | Write a query, run it | "What's low on stock?" |
| Update a product price | Find product, edit, save | UPDATE query by hand | "Set product 47 to 29.99" |
| Read a Technics module | JSON blob in the database | Copy-paste from phpMyAdmin | "Show me module 12" |
| Edit slider text | Find module, decode JSON, edit, re-encode | Pain | "Replace X with Y in module 12" |
| Sales report | Reports page, manually filter | Write aggregation queries | "Sales summary for the last 7 days" |
| Check SEO URLs | Admin > Marketing > SEO URL, paginate | SELECT from oc_seo_url | "Show SEO URLs containing 'headphones'" |
| Manage CMS pages | Admin > Catalog > Information | Direct DB access | "Show me the About Us page" |

---

## Quick start

### 1. Install

```bash
git clone https://github.com/Penikov/livestore-mcp.git
cd livestore-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

<details>
<summary><strong>Or install with Nix (flake)</strong></summary>

Run it directly, no clone:

```bash
nix run github:Penikov/livestore-mcp
```

Install via a flake input (NixOS / home-manager) — adds the `opencart-mcp`
binary to `PATH`:

```nix
{
  inputs.opencart-mcp.url = "github:Penikov/livestore-mcp";

  # then, in your NixOS configuration (configuration.nix / a module):
  environment.systemPackages = [
    inputs.opencart-mcp.packages.${pkgs.system}.default
  ];

  # …or home-manager:
  home.packages = [
    inputs.opencart-mcp.packages.${pkgs.system}.default
  ];
}
```

Dev shell with all deps (skip the venv steps above):

```bash
nix develop
```

</details>

### 2. Configure

```bash
cp .env.example .env
```

**Direct MySQL** (no SSH — host must allow remote connections, or use a tunnel):

```env
OPENCART_SSH_HOST=
OPENCART_DB_HOST=your-mysql-host
OPENCART_DB_PORT=3306
OPENCART_DB_USER=your_db_user
OPENCART_DB_PASS=your_db_password
OPENCART_DB_NAME=your_opencart_database
OPENCART_DB_PREFIX=oc_
OPENCART_ROOT=/path/to/opencart
OPENCART_STORAGE=/path/to/storage
```

**SSH** (preferred when available):

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

Optional extras:

```env
OPENCART_SSH_PORT=22           # if SSH runs on a non-standard port
OPENCART_DB_HOST=localhost     # if MySQL isn't on the same host (e.g. a tunnel)
OPENCART_DB_PREFIX=oc_         # table prefix override
OPENCART_LANGUAGE_ID=1         # skip auto-detect from config_language
```

Any `OPENCART_DB_*` value you leave unset is read from the install's `config.php` automatically **when SSH or DDEV is used**. Direct MySQL mode requires `OPENCART_DB_HOST`, user, password, and name in the environment.

#### Using DDEV for local development?

Set `OPENCART_SSH_HOST=ddev` and point `OPENCART_ROOT` at the local project directory — commands will run via `ddev exec` inside your container instead of SSH:

```env
OPENCART_SSH_HOST=ddev
OPENCART_DB_USER=db
OPENCART_DB_PASS=db
OPENCART_DB_NAME=db
OPENCART_ROOT=/Users/you/Sites/your-opencart-project
```

Container paths (`/var/www/html` etc.) are auto-resolved — you only need the local project path. (DDEV support contributed by [@IceDBorn](https://github.com/IceDBorn) — thanks!)

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
    "livestore": {
      "command": "/absolute/path/to/livestore-mcp/.venv/bin/python",
      "args": ["-m", "opencart_mcp.server"],
      "cwd": "/absolute/path/to/livestore-mcp",
      "env": {
        "PYTHONPATH": "/absolute/path/to/livestore-mcp/src",
        "OPENCART_DB_HOST": "your-mysql-host",
        "OPENCART_DB_USER": "your_db_user",
        "OPENCART_DB_PASS": "your_db_password",
        "OPENCART_DB_NAME": "your_opencart_database",
        "OPENCART_DB_PREFIX": "oc_",
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
    "livestore": {
      "command": "/absolute/path/to/livestore-mcp/.venv/bin/python",
      "args": ["-m", "opencart_mcp.server"],
      "cwd": "/absolute/path/to/livestore-mcp",
      "env": {
        "PYTHONPATH": "/absolute/path/to/livestore-mcp/src",
        "OPENCART_DB_HOST": "your-mysql-host",
        "OPENCART_DB_USER": "your_db_user",
        "OPENCART_DB_PASS": "your_db_password",
        "OPENCART_DB_NAME": "your_opencart_database",
        "OPENCART_DB_PREFIX": "oc_",
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
"Search for products with 'wireless' in the name"
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
"List all SEO URLs containing 'sale'"
"Update the SEO URL for product 23 to 'wireless-mouse-pro'"
"Show me the FAQ page content"
"Replace 'old company name' with 'new company name' in the About Us page"
```

**Technics theme**
```
"List Technics modules whose code contains slider"
"Show me the full JSON for module 12"
"Replace 'Free shipping' with 'Free shipping over 5000' in that module"
"What theme_technics settings contain 'header'?"
"List Technics blog posts and LiveStore articles"
```

**Technical**
```
"Show the schema for oc_order_product"
"List all tables matching 'technics'"
"Run: SELECT order_id, total FROM oc_order WHERE total > 100 ORDER BY date_added DESC LIMIT 10"
"What OCMOD modifications are active?"
```

---

## All 49 tools

### Read (34)

| Tool | What it does |
|------|-------------|
| `get_products` | Search products with stock, prices, SEO, `meta_h1`, `noindex` |
| `get_product` | Full product details — images, options, categories, attributes |
| `get_orders` | Recent orders filtered by status and date range |
| `get_order` | Full order with line items, totals, status history |
| `get_customers` | Search by name/email with order count and total spent |
| `get_categories` | Category tree with product counts, SEO URLs, `meta_h1` |
| `get_stock_report` | All products sorted by stock level (lowest first) |
| `get_settings` | OpenCart core settings by group/key |
| `get_theme_settings` | Technics settings from `oc_setting` (`theme_technics*`) |
| `get_modules` | `oc_module` rows by code/name — search JSON setting |
| `get_module` | Full module JSON for any `oc_module` id |
| `get_layouts` | Layouts, routes, and assigned modules |
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
| `get_file` | Read files from the server (SSH/DDEV required) |
| `get_coupons` | List discount coupons with usage counts |
| `get_vouchers` | List gift vouchers |
| `dashboard` | One-call store overview — revenue, order statuses, stock alerts, latest orders |
| `get_technics_blog` | Technics blog posts |
| `get_technics_blog_post` | Single Technics blog post plus comments |
| `get_technics_news` | Technics news items |
| `get_technics_sets` | Technics product sets/kits |
| `get_callbacks` | Technics callback requests |
| `get_articles` | LiveStore built-in blog (`oc_article`) |
| `get_article` | Single LiveStore article |

### Write (15)

| Tool | What it does |
|------|-------------|
| `update_product` | Update price, stock, name, SEO title, `meta_h1`, `noindex` |
| `update_setting` | Change OpenCart core settings |
| `update_theme_setting` | Change Technics `oc_setting` keys |
| `update_module` | Find/replace text within `oc_module` JSON |
| `update_information` | Find/replace text within CMS page HTML (About Us, T&Cs, etc.) |
| `update_seo_url` | Create or update SEO URL mappings |
| `update_category` | Update category name, meta, `meta_h1`, status, `noindex` |
| `write_file` | Write files to server via SFTP (SSH/DDEV required) |
| `run_sql` | Execute INSERT/UPDATE/DELETE (DDL blocked) |
| `clear_cache` | Flush OpenCart cache (SSH/DDEV required) |
| `refresh_modifications` | Clear OCMOD modification cache (SSH/DDEV required) |
| `update_order_status` | Change order status + append order history |
| `create_coupon` | Create a discount coupon (percentage or fixed) |
| `update_coupon` | Enable/disable, extend, or edit a coupon |
| `update_article` | Find/replace text in a LiveStore article |

---

## How it works

**Direct MySQL** (leave `OPENCART_SSH_HOST` empty):

```
Your machine                          MySQL host
┌──────────────┐                     ┌──────────────┐
│ MCP client   │     pymysql         │              │
│      ↓       │ ──────────────────→ │   MySQL      │
│  MCP Server  │                     │              │
│  (Python)    │ ←────────────────── │   (rows)     │
└──────────────┘                     └──────────────┘
```

**SSH** (set `OPENCART_SSH_HOST`):

```
Your machine                          Your server
┌──────────────┐                     ┌──────────────┐
│ MCP client   │     SSH tunnel      │              │
│      ↓       │ ──────────────────→ │   PHP cli    │
│  MCP Server  │   PHP via stdin     │      ↓       │
│  (Python)    │ ←────────────────── │   MySQL      │
└──────────────┘                     └──────────────┘
```

The server runs **on your machine**. Nothing is installed on your store. No files uploaded, no cleanup, no extra ports opened for the MCP itself.

- **Direct MySQL** — `pymysql`, for hosts without SSH
- **PHP via stdin** — works with any PHP version, nothing written to disk
- **Paramiko** — pure Python SSH when SSH is configured

---

## Multiple stores

Run dev and live as separate instances in the same Claude session:

```json
{
  "mcpServers": {
    "opencart_dev": {
      "command": "/path/to/livestore-mcp/.venv/bin/python",
      "args": ["-m", "opencart_mcp.server"],
      "cwd": "/path/to/livestore-mcp",
      "env": { "OPENCART_DB_NAME": "my_dev_database" }
    },
    "opencart_live": {
      "command": "/path/to/livestore-mcp/.venv/bin/python",
      "args": ["-m", "opencart_mcp.server"],
      "cwd": "/path/to/livestore-mcp",
      "env": { "OPENCART_DB_NAME": "my_live_database" }
    }
  }
}
```

Claude prefixes tools automatically — `opencart_dev__get_products` vs `opencart_live__get_products` — so there's no confusion about which store you're querying.

---

## Tested with

| Component | Versions |
|-----------|----------|
| LiveStore / ocStore | 3.0.4.4 (OpenCart 3.x) |
| Technics | 1.4.x |
| PHP | 5.6+ (server-side, SSH mode only) |
| Python | 3.10+ (local machine) |
| Hosting | VPS, dedicated servers, shared hosting with remote MySQL or SSH |
| Clients | Claude Code CLI, VS Code extension, JetBrains extension, Cursor |

---

## Troubleshooting

### Direct MySQL connection fails

- Confirm the host allows your IP on port 3306 (many shared hosts only allow localhost)
- Use an SSH tunnel and set `OPENCART_DB_HOST=127.0.0.1` plus `OPENCART_DB_PORT` to the local tunnel port
- File/cache tools will still refuse until SSH or DDEV is configured — that is expected

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

### Technics tables not found

Technics extras (`oc_technics_blog`, `oc_callback`, …) return empty results instead of errors if those tables are missing.

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
- [x] LiveStore + Technics tools, direct MySQL *(v0.7.0)*
- [x] Coupon and voucher management tools *(v0.6.0)*
- [x] Order status update tool *(v0.6.0)*
- [ ] Bulk product import/export
- [ ] Customer group management
- [x] Dashboard summary tool (one prompt, full store overview) *(v0.6.0)*

Got a feature request? [Open an issue](https://github.com/Penikov/livestore-mcp/issues).

---

## Changelog

See [releases](https://github.com/Penikov/livestore-mcp/releases) for fork history. Upstream history: [chrisbray85/opencart-mcp](https://github.com/chrisbray85/opencart-mcp/releases).

### 0.7.0

- Direct MySQL transport when `OPENCART_SSH_HOST` is empty
- Journal3 tools replaced with Technics / LiveStore tools
- Language id resolved from `config_language`
- LiveStore `meta_h1` / `noindex`; wider excluded order-status set for sales

---

## Contributing

Issues and PRs welcome. If you're running this on a hosting setup or OpenCart version not listed above, let us know what works and what doesn't.

Thanks to the upstream contributors:

- [@IceDBorn](https://github.com/IceDBorn) — DDEV support and the Nix flake
- [@ClayRabbit](https://github.com/ClayRabbit) — configurable SSH port and full `config.php` DB fallback

## License

MIT — see [LICENSE](LICENSE) for details.
