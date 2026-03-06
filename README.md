# OpenCart MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that gives AI assistants like Claude direct access to your OpenCart store. Query products, orders, customers, settings, and Journal3 theme data — without writing SQL or SSH scripts.

## What It Does

Instead of manually writing PHP scripts and running them over SSH, you get **25 tools** that Claude (or any MCP client) can call directly:

```
You:    "What's the stock level for BPC-157?"
Claude: *calls get_products(search="BPC-157")* → returns product data instantly
```

**Before MCP:** Write PHP script → upload via SSH → run → parse output (5+ steps, 2-3 minutes)

**After MCP:** One tool call, 5 seconds.

## Tools

### Read Tools
| Tool | Description |
|------|-------------|
| `get_products` | Search products with stock, prices, SEO data |
| `get_product` | Full product details with images, options, categories |
| `get_orders` | Recent orders filtered by status and date range |
| `get_order` | Order details with line items, totals, history |
| `get_customers` | Search customers by name/email with order stats |
| `get_categories` | Category tree with product counts and SEO URLs |
| `get_stock_report` | All products sorted by stock level (lowest first) |
| `get_settings` | OpenCart core settings |
| `get_j3_settings` | Journal3 theme settings |
| `get_j3_skin_settings` | Journal3 skin/layout settings |
| `get_modules` | Journal3 modules by type |
| `get_modifications` | OCMOD modifications with status |
| `get_extensions` | Installed extensions list |
| `get_seo_urls` | SEO URL mappings |
| `query` | Custom read-only SQL (SELECT/SHOW/DESCRIBE only) |
| `get_table_schema` | Show columns for any table |
| `list_tables` | List tables matching a pattern |
| `get_file` | Read files from the server |

### Write Tools
| Tool | Description |
|------|-------------|
| `update_product` | Update product price, stock, name, SEO fields |
| `update_setting` | Change OpenCart settings |
| `update_j3_setting` | Change Journal3 settings |
| `update_j3_skin_setting` | Change Journal3 skin settings |
| `run_sql` | Execute INSERT/UPDATE/DELETE statements |
| `clear_cache` | Flush OpenCart + Journal3 caches |
| `refresh_modifications` | Clear OCMOD modification cache |

## How It Works

```
Claude Code (your machine) → stdio → MCP Server (your machine)
                                          ↓
                                    SSH to your VPS
                                          ↓
                                    PHP → MySQL queries
```

The server runs locally on your machine and connects to your OpenCart server via SSH. It pipes PHP scripts to the remote `php` interpreter via stdin, so there's no need for a newer Python version on the server. MySQL credentials never leave the SSH connection.

## Requirements

- **Local machine:** Python 3.10+ (where Claude Code runs)
- **Remote server:** SSH access + PHP + MySQL (any version — even PHP 5.6 works)
- **OpenCart:** 3.x (tested with 3.0.3.8 through 3.0.5.0)
- **Optional:** Journal3 theme (J3-specific tools gracefully handle missing tables)

## Installation

### 1. Clone and set up

```bash
git clone https://github.com/chrisbray85/opencart-mcp.git
cd opencart-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Configure

Copy the example env file and fill in your details:

```bash
cp .env.example .env
```

Edit `.env` with your server details:

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

**Finding your paths:**
- `OPENCART_ROOT` — the directory containing `index.php`, `admin/`, `catalog/`, `system/`
- `OPENCART_STORAGE` — check your `config.php` for the `DIR_STORAGE` value (often outside the web root)

### 3. Test it works

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

### 4. Add to Claude Code

Add this to your `~/.claude.json` (or project-level config) under `mcpServers`:

```json
{
  "mcpServers": {
    "opencart": {
      "command": "/path/to/opencart-mcp/.venv/bin/python",
      "args": ["-m", "opencart_mcp.server"],
      "cwd": "/path/to/opencart-mcp",
      "env": {
        "PYTHONPATH": "/path/to/opencart-mcp/src",
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

Restart Claude Code — the `opencart` tools will appear automatically.

## Multiple Stores

You can configure multiple OpenCart instances by adding separate MCP server entries with different env vars:

```json
{
  "mcpServers": {
    "opencart-dev": {
      "command": "/path/to/opencart-mcp/.venv/bin/python",
      "args": ["-m", "opencart_mcp.server"],
      "cwd": "/path/to/opencart-mcp",
      "env": {
        "OPENCART_DB_NAME": "my_dev_database",
        ...
      }
    },
    "opencart-live": {
      "command": "/path/to/opencart-mcp/.venv/bin/python",
      "args": ["-m", "opencart_mcp.server"],
      "cwd": "/path/to/opencart-mcp",
      "env": {
        "OPENCART_DB_NAME": "my_live_database",
        ...
      }
    }
  }
}
```

## Security

- **Read-only by default.** The `query()` tool only allows SELECT/SHOW/DESCRIBE/EXPLAIN. Write operations require explicit `run_sql()` or dedicated update tools.
- **No DDL.** DROP, ALTER, TRUNCATE, CREATE are blocked even in `run_sql()`.
- **SSH-only.** Database credentials are used inside the SSH tunnel — they never leave the connection.
- **Path traversal blocked.** The `get_file()` tool rejects `..` in paths.
- **Claude Code confirmation.** Write tools trigger Claude Code's built-in confirmation prompt before executing.

## Journal3 Support

If you're running the Journal3 theme, you get extra tools for:
- **J3 settings** (`get_j3_settings`, `update_j3_setting`)
- **Skin settings** (`get_j3_skin_settings`, `update_j3_skin_setting`) — controls checkout layout, performance, styling
- **Modules** (`get_modules`) — list J3 modules by type (products, sliders, menus, etc.)

These tools will return empty results (not errors) if Journal3 tables don't exist.

## Troubleshooting

**Empty results from queries:**
- Check your SSH key is set up for passwordless login: `ssh your-user@your-server "echo ok"`
- Verify the PHP path works: `ssh your-user@your-server "echo '<?php echo 1;' | php"`
- Check database credentials by running the test script above

**cPanel servers (tput warnings):**
cPanel shared hosting prints `tput: No value for $TERM` warnings over SSH. The server filters these automatically — they won't affect results.

**Connection timeouts:**
If queries are slow, your SSH connection might be going through a VPN. The default timeout is 30 seconds per query.

## License

MIT
