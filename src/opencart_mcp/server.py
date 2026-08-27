"""LiveStore MCP Server — query and manage LiveStore/OpenCart 3 + Technics."""

import json
import re
import shlex

from fastmcp import FastMCP

from .config import Config
from .db import OpenCartDB

config = Config.from_env()
db = OpenCartDB(config)

# LiveStore/ocStore: missing(0), cancelled(7), denied(8), canceled reversal(9),
# failed(10), refunded(11), reversed(12), chargeback(13), expired(14), voided/fraud(16)
EXCLUDED_ORDER_STATUS_IDS = "(0, 7, 8, 9, 10, 11, 12, 13, 14, 16)"

_lang_id: int | None = None


def esc(s: str) -> str:
    """Escape a value for interpolation into a single-quoted MySQL string.
    Backslash first, then quote — MySQL consumes backslash escapes."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def lang_id() -> int:
    """Storefront language_id: env override, else config_language, else first enabled."""
    global _lang_id
    if _lang_id is not None:
        return _lang_id
    if config.language_id:
        _lang_id = int(config.language_id)
        return _lang_id
    rows = db.run_query("""
        SELECT l.language_id
        FROM oc_language l
        JOIN oc_setting s ON s.`key` = 'config_language' AND s.store_id = 0 AND s.value = l.code
        LIMIT 1
    """)
    if rows:
        _lang_id = int(rows[0]["language_id"])
        return _lang_id
    rows = db.run_query(
        "SELECT language_id FROM oc_language WHERE status = 1 ORDER BY language_id LIMIT 1"
    )
    _lang_id = int(rows[0]["language_id"]) if rows else 1
    return _lang_id


def _update_status(*results) -> dict:
    """Summarise UPDATE results. MySQL reports affected_rows=0 both when the
    target row doesn't exist and when the values were already identical."""
    changed = sum(
        int(r.get("affected_rows", 0) or 0) for r in results if isinstance(r, dict)
    )
    status = {"updated": changed > 0, "rows_changed": changed}
    if changed == 0:
        status["warning"] = "No rows changed — target not found, or values already identical"
    return status


def _optional_query(sql: str):
    """Technics/LiveStore extras are optional — missing table means empty result."""
    try:
        return db.run_query(sql)
    except RuntimeError as e:
        msg = str(e).lower()
        if "doesn't exist" in msg or "unknown table" in msg:
            return []
        raise


def _shell_error() -> str | None:
    if config.is_direct_mysql:
        return json.dumps({
            "error": "File and cache tools need SSH or DDEV. Direct MySQL mode only runs SQL.",
        })
    return None

mcp = FastMCP("LiveStore")


# ─── READ TOOLS ──────────────────────────────────────────────


@mcp.tool()
def get_products(
    search: str = "",
    category_id: int = 0,
    limit: int = 50,
    include_description: bool = False,
) -> str:
    """List products with stock, prices, and SEO data.
    Search by name. Optionally filter by category_id.
    Set include_description=True to include full HTML descriptions."""

    where = "WHERE p.status = 1"
    if search:
        safe = esc(search)
        where += f" AND pd.name LIKE '%{safe}%'"
    if category_id:
        where += f" AND p.product_id IN (SELECT product_id FROM oc_product_to_category WHERE category_id = {int(category_id)})"

    desc_col = ", pd.description" if include_description else ""

    sql = f"""
        SELECT p.product_id, pd.name, p.model, p.sku, p.price, p.quantity,
               p.stock_status_id, p.status, p.date_modified,
               p.weight, p.image, p.noindex,
               su.keyword AS seo_url,
               pd.meta_title, pd.meta_h1, pd.meta_description{desc_col}
        FROM oc_product p
        JOIN oc_product_description pd ON p.product_id = pd.product_id AND pd.language_id = {lang_id()}
        LEFT JOIN oc_seo_url su ON su.query = CONCAT('product_id=', p.product_id) AND su.language_id = {lang_id()}
        {where}
        ORDER BY pd.name
        LIMIT {int(limit)}
    """
    rows = db.run_query(sql)
    return json.dumps(rows)


@mcp.tool()
def get_product(product_id: int) -> str:
    """Get full details for a single product including images, options, and attributes."""

    sql = f"""
        SELECT p.*, pd.name, pd.description, pd.meta_title, pd.meta_h1, pd.meta_description, pd.tag,
               su.keyword AS seo_url
        FROM oc_product p
        JOIN oc_product_description pd ON p.product_id = pd.product_id AND pd.language_id = {lang_id()}
        LEFT JOIN oc_seo_url su ON su.query = 'product_id={int(product_id)}' AND su.language_id = {lang_id()}
        WHERE p.product_id = {int(product_id)}
    """
    product = db.run_query(sql)
    if not product:
        return json.dumps({"error": f"Product {product_id} not found"})

    # Get images
    images = db.run_query(
        f"SELECT image, sort_order FROM oc_product_image WHERE product_id = {int(product_id)} ORDER BY sort_order"
    )

    # Get categories
    cats = db.run_query(f"""
        SELECT cd.name, ptc.category_id
        FROM oc_product_to_category ptc
        JOIN oc_category_description cd ON ptc.category_id = cd.category_id AND cd.language_id = {lang_id()}
        WHERE ptc.product_id = {int(product_id)}
    """)

    # Get options
    options = db.run_query(f"""
        SELECT pov.product_option_value_id, od.name AS option_name, ovd.name AS value_name,
               pov.quantity, pov.price, pov.price_prefix, pov.weight, pov.weight_prefix
        FROM oc_product_option_value pov
        JOIN oc_product_option po ON pov.product_option_id = po.product_option_id
        JOIN oc_option_description od ON po.option_id = od.option_id AND od.language_id = {lang_id()}
        JOIN oc_option_value_description ovd ON pov.option_value_id = ovd.option_value_id AND ovd.language_id = {lang_id()}
        WHERE pov.product_id = {int(product_id)}
    """)

    result = product[0]
    result["images"] = images
    result["categories"] = cats
    result["options"] = options
    return json.dumps(result)


@mcp.tool()
def get_orders(
    status: str = "",
    limit: int = 20,
    days: int = 30,
) -> str:
    """Get recent orders. Filter by status name (e.g. 'Complete', 'Pending').
    Default: last 30 days, 20 orders."""

    where = f"WHERE o.date_added >= DATE_SUB(NOW(), INTERVAL {int(days)} DAY)"
    if status:
        safe = esc(status)
        where += f" AND os.name = '{safe}'"

    sql = f"""
        SELECT o.order_id, CONCAT(o.firstname, ' ', o.lastname) AS customer,
               o.email, o.total, o.currency_code, os.name AS status,
               o.date_added, o.date_modified,
               o.payment_method, o.shipping_method,
               o.shipping_city, o.shipping_postcode, o.shipping_country
        FROM oc_order o
        LEFT JOIN oc_order_status os ON o.order_status_id = os.order_status_id AND os.language_id = {lang_id()}
        {where}
        ORDER BY o.date_added DESC
        LIMIT {int(limit)}
    """
    rows = db.run_query(sql)
    return json.dumps(rows)


@mcp.tool()
def get_order(order_id: int) -> str:
    """Get full order details including line items and totals."""

    order = db.run_query(f"""
        SELECT o.*, os.name AS status_name
        FROM oc_order o
        LEFT JOIN oc_order_status os ON o.order_status_id = os.order_status_id AND os.language_id = {lang_id()}
        WHERE o.order_id = {int(order_id)}
    """)
    if not order:
        return json.dumps({"error": f"Order {order_id} not found"})

    items = db.run_query(f"""
        SELECT product_id, name, model, quantity, price, total, tax
        FROM oc_order_product
        WHERE order_id = {int(order_id)}
    """)

    totals = db.run_query(f"""
        SELECT code, title, value, sort_order
        FROM oc_order_total
        WHERE order_id = {int(order_id)}
        ORDER BY sort_order
    """)

    history = db.run_query(f"""
        SELECT oh.date_added, os.name AS status, oh.comment
        FROM oc_order_history oh
        LEFT JOIN oc_order_status os ON oh.order_status_id = os.order_status_id AND os.language_id = {lang_id()}
        WHERE oh.order_id = {int(order_id)}
        ORDER BY oh.date_added DESC
    """)

    result = order[0]
    result["items"] = items
    result["totals"] = totals
    result["history"] = history
    return json.dumps(result)


@mcp.tool()
def get_customers(search: str = "", limit: int = 20) -> str:
    """Search customers by name or email."""
    where = "WHERE 1=1"
    if search:
        safe = esc(search)
        where += f" AND (c.email LIKE '%{safe}%' OR c.firstname LIKE '%{safe}%' OR c.lastname LIKE '%{safe}%')"

    sql = f"""
        SELECT c.customer_id, c.firstname, c.lastname, c.email, c.telephone,
               c.date_added, c.status,
               (SELECT COUNT(*) FROM oc_order o WHERE o.customer_id = c.customer_id) AS order_count,
               (SELECT SUM(o.total) FROM oc_order o WHERE o.customer_id = c.customer_id AND o.order_status_id > 0) AS total_spent
        FROM oc_customer c
        {where}
        ORDER BY c.date_added DESC
        LIMIT {int(limit)}
    """
    rows = db.run_query(sql)
    return json.dumps(rows)


@mcp.tool()
def get_categories(parent_id: int = 0) -> str:
    """Get category tree. Set parent_id=0 for top-level categories."""

    sql = f"""
        SELECT c.category_id, cd.name, c.parent_id, c.status, c.sort_order, c.noindex,
               su.keyword AS seo_url, cd.meta_h1,
               (SELECT COUNT(*) FROM oc_product_to_category ptc WHERE ptc.category_id = c.category_id) AS product_count
        FROM oc_category c
        JOIN oc_category_description cd ON c.category_id = cd.category_id AND cd.language_id = {lang_id()}
        LEFT JOIN oc_seo_url su ON su.query = CONCAT('category_id=', c.category_id) AND su.language_id = {lang_id()}
        WHERE c.parent_id = {int(parent_id)}
        ORDER BY c.sort_order, cd.name
    """
    rows = db.run_query(sql)
    return json.dumps(rows)


@mcp.tool()
def get_settings(group: str = "", key: str = "") -> str:
    """Get OpenCart settings. Filter by group (e.g. 'config') and/or key pattern (SQL LIKE)."""

    where = "WHERE store_id = 0"
    if group:
        safe = esc(group)
        where += f" AND code = '{safe}'"
    if key:
        safe = esc(key)
        where += f" AND `key` LIKE '{safe}'"

    sql = f"SELECT setting_id, code, `key`, value, serialized FROM oc_setting {where} ORDER BY code, `key`"
    rows = db.run_query(sql)
    return json.dumps(rows)


@mcp.tool()
def get_theme_settings(pattern: str = "") -> str:
    """Get Technics theme settings from oc_setting (codes theme_technics*).
    Filter by key pattern (SQL LIKE). License keys are omitted."""

    where = (
        "WHERE store_id = 0 AND (code LIKE 'theme_technics%' OR `key` LIKE 'theme_technics%') "
        "AND `key` NOT LIKE '%license%' AND `key` NOT LIKE '%_key'"
    )
    if pattern:
        where += f" AND `key` LIKE '{esc(pattern)}'"

    sql = f"""
        SELECT setting_id, code, `key`, value, serialized
        FROM oc_setting
        {where}
        ORDER BY code, `key`
    """
    return json.dumps(_optional_query(sql))


@mcp.tool()
def get_modules(module_type: str = "", search: str = "") -> str:
    """List OpenCart modules from oc_module (Technics stores JSON in setting).
    Filter by code substring (e.g. 'technics_main_slider', 'html'). Search name or setting."""

    where_parts = []
    if module_type:
        where_parts.append(f"code LIKE '%{esc(module_type)}%'")
    if search:
        where_parts.append(f"(name LIKE '%{esc(search)}%' OR setting LIKE '%{esc(search)}%')")
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    sql = f"""
        SELECT module_id, name, code,
               LEFT(setting, 240) AS setting_preview
        FROM oc_module
        {where}
        ORDER BY code, module_id
    """
    return json.dumps(db.run_query(sql))


@mcp.tool()
def get_module(module_id: int) -> str:
    """Get full oc_module row including JSON setting. Can be large for sliders/tabs."""

    rows = db.run_query(
        f"SELECT module_id, name, code, setting FROM oc_module WHERE module_id = {int(module_id)}"
    )
    if not rows:
        return json.dumps({"error": f"Module {module_id} not found"})
    return json.dumps(rows[0])


@mcp.tool()
def get_layouts() -> str:
    """List layouts, routes, and assigned modules (positions)."""

    layouts = db.run_query("""
        SELECT l.layout_id, l.name, lr.store_id, lr.route
        FROM oc_layout l
        LEFT JOIN oc_layout_route lr ON l.layout_id = lr.layout_id
        ORDER BY l.layout_id, lr.route
    """)
    modules = db.run_query("""
        SELECT lm.layout_module_id, lm.layout_id, l.name AS layout,
               lm.code, lm.position, lm.sort_order
        FROM oc_layout_module lm
        JOIN oc_layout l ON l.layout_id = lm.layout_id
        ORDER BY lm.layout_id, lm.position, lm.sort_order
    """)
    return json.dumps({"layouts": layouts, "modules": modules})


@mcp.tool()
def get_order_statuses() -> str:
    """List all order statuses with their IDs."""

    sql = f"""
        SELECT order_status_id, name
        FROM oc_order_status
        WHERE language_id = {lang_id()}
        ORDER BY order_status_id
    """
    rows = db.run_query(sql)
    return json.dumps(rows)


@mcp.tool()
def get_product_attributes(product_id: int) -> str:
    """Get all attributes for a product (e.g. CAS number, molecular weight, storage)."""

    sql = f"""
        SELECT pa.attribute_id, ad.name AS attribute_name,
               agd.name AS attribute_group, pa.text AS value
        FROM oc_product_attribute pa
        JOIN oc_attribute a ON pa.attribute_id = a.attribute_id
        JOIN oc_attribute_description ad ON a.attribute_id = ad.attribute_id AND ad.language_id = {lang_id()}
        JOIN oc_attribute_group_description agd ON a.attribute_group_id = agd.attribute_group_id AND agd.language_id = {lang_id()}
        WHERE pa.product_id = {int(product_id)} AND pa.language_id = {lang_id()}
        ORDER BY agd.name, ad.name
    """
    rows = db.run_query(sql)
    return json.dumps(rows)


@mcp.tool()
def sales_summary(days: int = 30, top_n: int = 20) -> str:
    """Get sales summary: total revenue, order count, top selling products.
    Covers the last N days. Excludes cancelled/failed/refunded orders."""

    # Overall stats
    stats = db.run_query(f"""
        SELECT COUNT(*) AS total_orders,
               ROUND(SUM(total), 2) AS total_revenue,
               ROUND(AVG(total), 2) AS avg_order_value,
               COUNT(DISTINCT email) AS unique_customers
        FROM oc_order
        WHERE date_added >= DATE_SUB(NOW(), INTERVAL {int(days)} DAY)
          AND order_status_id NOT IN {EXCLUDED_ORDER_STATUS_IDS}
    """)

    # Top products by units sold
    top_products = db.run_query(f"""
        SELECT op.product_id, pd.name,
               SUM(op.quantity) AS units_sold,
               ROUND(SUM(op.total), 2) AS revenue,
               COUNT(DISTINCT op.order_id) AS order_count
        FROM oc_order_product op
        JOIN oc_order o ON op.order_id = o.order_id
        JOIN oc_product_description pd ON op.product_id = pd.product_id AND pd.language_id = {lang_id()}
        WHERE o.date_added >= DATE_SUB(NOW(), INTERVAL {int(days)} DAY)
          AND o.order_status_id NOT IN {EXCLUDED_ORDER_STATUS_IDS}
        GROUP BY op.product_id, pd.name
        ORDER BY units_sold DESC
        LIMIT {int(top_n)}
    """)

    # Daily revenue for the period
    daily = db.run_query(f"""
        SELECT DATE(date_added) AS date,
               COUNT(*) AS orders,
               ROUND(SUM(total), 2) AS revenue
        FROM oc_order
        WHERE date_added >= DATE_SUB(NOW(), INTERVAL {int(days)} DAY)
          AND order_status_id NOT IN {EXCLUDED_ORDER_STATUS_IDS}
        GROUP BY DATE(date_added)
        ORDER BY date DESC
    """)

    result = {
        "period_days": days,
        "summary": stats[0] if stats else {},
        "top_products": top_products,
        "daily_revenue": daily,
    }
    return json.dumps(result)


@mcp.tool()
def get_modifications() -> str:
    """List all OCMOD modifications with status."""

    sql = """
        SELECT modification_id, name, code, author, status, date_added
        FROM oc_modification
        ORDER BY name
    """
    rows = db.run_query(sql)
    return json.dumps(rows)


@mcp.tool()
def get_extensions() -> str:
    """List installed OpenCart extensions."""

    sql = """
        SELECT extension_id, type, code
        FROM oc_extension
        ORDER BY type, code
    """
    rows = db.run_query(sql)
    return json.dumps(rows)


@mcp.tool()
def query(sql: str) -> str:
    """Execute a read-only SQL query. Only SELECT statements allowed.
    Use this for custom queries not covered by other tools."""

    cleaned = sql.strip().rstrip(";").strip()

    # Block write operations
    first_word = cleaned.split()[0].upper() if cleaned.split() else ""
    if first_word not in ("SELECT", "SHOW", "DESCRIBE", "EXPLAIN"):
        return json.dumps({"error": f"Only SELECT/SHOW/DESCRIBE/EXPLAIN allowed. Got: {first_word}"})

    # Block dangerous patterns
    dangerous = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|INTO\s+OUTFILE|LOAD\s+DATA)\b",
        re.IGNORECASE,
    )
    scan = re.sub(r"'[^']*'", "''", cleaned)  # strip literals: data isn't SQL
    if dangerous.search(scan):
        return json.dumps({"error": "Write operations not allowed in query(). Use run_sql() instead."})

    rows = db.run_query(cleaned)
    return json.dumps(rows)


@mcp.tool()
def get_table_schema(table: str) -> str:
    """Show columns for an OpenCart table. The install's prefix (e.g. 'oc_') is added automatically if missing."""

    prefix = db._get_config()["PREFIX"]
    if table.startswith("oc_") and prefix != "oc_":
        table = table[3:]  # caller used the generic oc_ name; re-prefix below
    if not table.startswith(prefix):
        table = f"{prefix}{table}"

    # Validate table name (alphanumeric + underscore only)
    if not re.match(r"^[a-zA-Z0-9_]+$", table):
        return json.dumps({"error": "Invalid table name"})

    rows = db.run_query(f"SHOW COLUMNS FROM {table}")
    return json.dumps(rows)


@mcp.tool()
def list_tables(pattern: str | None = None) -> str:
    """List database tables matching pattern. Default: all OpenCart tables (using detected prefix)."""

    if pattern is None:
        pattern = f"{db._get_config()['PREFIX']}%"
    safe = esc(pattern)
    rows = db.run_query(f"SHOW TABLES LIKE '{safe}'")
    return json.dumps(rows)


@mcp.tool()
def get_file(path: str, max_lines: int = 200) -> str:
    """Read a file from the VPS. Path is relative to OpenCart root unless absolute.
    Returns first max_lines lines. Requires SSH or DDEV."""

    blocked = _shell_error()
    if blocked:
        return blocked

    if not path.startswith("/"):
        full_path = f"{config.oc_root}/{path}"
    else:
        full_path = path

    # Security: block path traversal
    if ".." in path:
        return json.dumps({"error": "Path traversal not allowed"})

    out = db.run_command(f"head -n {int(max_lines)} {shlex.quote(full_path)}")
    return out


# ─── WRITE TOOLS ─────────────────────────────────────────────


@mcp.tool()
def update_product(
    product_id: int,
    price: float | None = None,
    quantity: int | None = None,
    status: int | None = None,
    meta_title: str | None = None,
    meta_h1: str | None = None,
    meta_description: str | None = None,
    name: str | None = None,
    noindex: int | None = None,
) -> str:
    """Update product fields. Only specified fields are changed.
    LiveStore extras: meta_h1, noindex (0/1)."""

    updates_product = []
    updates_desc = []

    if price is not None:
        updates_product.append(f"price = {float(price)}")
    if quantity is not None:
        updates_product.append(f"quantity = {int(quantity)}")
    if status is not None:
        updates_product.append(f"status = {int(status)}")
    if noindex is not None:
        updates_product.append(f"noindex = {int(noindex)}")

    if meta_title is not None:
        safe = esc(meta_title)
        updates_desc.append(f"meta_title = '{safe}'")
    if meta_h1 is not None:
        safe = esc(meta_h1)
        updates_desc.append(f"meta_h1 = '{safe}'")
    if meta_description is not None:
        safe = esc(meta_description)
        updates_desc.append(f"meta_description = '{safe}'")
    if name is not None:
        safe = esc(name)
        updates_desc.append(f"name = '{safe}'")

    if not updates_product and not updates_desc:
        return json.dumps({"error": "No fields to update"})

    results = []
    if updates_product:
        sql = f"UPDATE oc_product SET {', '.join(updates_product)} WHERE product_id = {int(product_id)}"
        r = db.run_query(sql)
        results.append({"table": "oc_product", "result": r})

    if updates_desc:
        sql = f"UPDATE oc_product_description SET {', '.join(updates_desc)} WHERE product_id = {int(product_id)} AND language_id = {lang_id()}"
        r = db.run_query(sql)
        results.append({"table": "oc_product_description", "result": r})

    status = _update_status(*[x["result"] for x in results])
    return json.dumps({**status, "product_id": product_id, "results": results})


@mcp.tool()
def update_setting(group: str, key: str, value: str) -> str:
    """Update an OpenCart setting."""

    safe_group = esc(group)
    safe_key = esc(key)
    safe_value = esc(value)

    result = db.run_query(
        f"UPDATE oc_setting SET value = '{safe_value}' WHERE code = '{safe_group}' AND `key` = '{safe_key}' AND store_id = 0"
    )
    return json.dumps({**_update_status(result), "code": group, "key": key, "result": result})


@mcp.tool()
def update_theme_setting(key: str, value: str, group: str = "theme_technics") -> str:
    """Update a Technics setting in oc_setting. Default group theme_technics.
    License keys cannot be changed through this tool."""

    if "license" in key.lower() or key.endswith("_key"):
        return json.dumps({"error": "License/key settings cannot be updated via MCP"})

    result = db.run_query(
        f"UPDATE oc_setting SET value = '{esc(value)}' "
        f"WHERE code = '{esc(group)}' AND `key` = '{esc(key)}' AND store_id = 0"
    )
    return json.dumps({**_update_status(result), "code": group, "key": key, "result": result})


@mcp.tool()
def update_module(module_id: int, find: str, replace: str) -> str:
    """Find/replace text inside an oc_module setting JSON blob.
    Use get_module first to see the current content."""

    rows = db.run_query(f"SELECT setting FROM oc_module WHERE module_id = {int(module_id)}")
    if not rows:
        return json.dumps({"error": f"Module {module_id} not found"})

    current = rows[0]["setting"]
    if find not in current:
        return json.dumps({"error": f"Text '{find}' not found in module {module_id}"})

    count = current.count(find)
    updated = current.replace(find, replace)
    result = db.run_query(
        f"UPDATE oc_module SET setting = '{esc(updated)}' WHERE module_id = {int(module_id)}"
    )
    return json.dumps({
        **_update_status(result), "module_id": module_id,
        "replacements": count, "find": find, "replace": replace,
        "result": result,
    })


@mcp.tool()
def update_seo_url(query: str, keyword: str) -> str:
    """Update or create an SEO URL mapping. Query is e.g. 'product_id=123' or 'category_id=45'.
    Keyword is the URL slug (e.g. 'bpc-157-5mg')."""

    safe_query = esc(query)
    safe_keyword = esc(keyword)

    # Check if mapping exists
    existing = db.run_query(
        f"SELECT seo_url_id FROM oc_seo_url WHERE query = '{safe_query}' AND store_id = 0 AND language_id = {lang_id()}"
    )

    if existing:
        result = db.run_query(
            f"UPDATE oc_seo_url SET keyword = '{safe_keyword}' WHERE query = '{safe_query}' AND store_id = 0 AND language_id = {lang_id()}"
        )
        return json.dumps({**_update_status(result), "query": query, "keyword": keyword, "result": result})
    else:
        result = db.run_query(
            f"INSERT INTO oc_seo_url (store_id, language_id, query, keyword) VALUES (0, {lang_id()}, '{safe_query}', '{safe_keyword}')"
        )
        return json.dumps({"created": True, "query": query, "keyword": keyword, "result": result})


@mcp.tool()
def update_category(
    category_id: int,
    name: str | None = None,
    meta_title: str | None = None,
    meta_h1: str | None = None,
    meta_description: str | None = None,
    status: int | None = None,
    noindex: int | None = None,
) -> str:
    """Update category fields. Only specified fields are changed.
    LiveStore extras: meta_h1, noindex."""

    updates_cat = []
    updates_desc = []

    if status is not None:
        updates_cat.append(f"status = {int(status)}")
    if noindex is not None:
        updates_cat.append(f"noindex = {int(noindex)}")

    if name is not None:
        safe = esc(name)
        updates_desc.append(f"name = '{safe}'")
    if meta_title is not None:
        safe = esc(meta_title)
        updates_desc.append(f"meta_title = '{safe}'")
    if meta_h1 is not None:
        safe = esc(meta_h1)
        updates_desc.append(f"meta_h1 = '{safe}'")
    if meta_description is not None:
        safe = esc(meta_description)
        updates_desc.append(f"meta_description = '{safe}'")

    if not updates_cat and not updates_desc:
        return json.dumps({"error": "No fields to update"})

    results = []
    if updates_cat:
        sql = f"UPDATE oc_category SET {', '.join(updates_cat)} WHERE category_id = {int(category_id)}"
        r = db.run_query(sql)
        results.append({"table": "oc_category", "result": r})

    if updates_desc:
        sql = f"UPDATE oc_category_description SET {', '.join(updates_desc)} WHERE category_id = {int(category_id)} AND language_id = {lang_id()}"
        r = db.run_query(sql)
        results.append({"table": "oc_category_description", "result": r})

    status = _update_status(*[x["result"] for x in results])
    return json.dumps({**status, "category_id": category_id, "results": results})


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write content to a file on the VPS via SFTP. Path is relative to OpenCart root unless absolute.
    Creates parent directories if needed. Requires SSH or DDEV."""

    blocked = _shell_error()
    if blocked:
        return blocked

    if not path.startswith("/"):
        full_path = f"{config.oc_root}/{path}"
    else:
        full_path = path

    if ".." in path:
        return json.dumps({"error": "Path traversal not allowed"})

    # Ensure parent directory exists
    parent = "/".join(full_path.split("/")[:-1])
    db.run_command(f"mkdir -p {shlex.quote(parent)}")

    db.write_file(full_path, content)
    return json.dumps({"written": True, "path": full_path, "bytes": len(content)})


@mcp.tool()
def clear_cache() -> str:
    """Clear OpenCart cache on the server. Requires SSH or DDEV."""

    blocked = _shell_error()
    if blocked:
        return blocked

    if not config.storage_dir.rstrip("/"):
        return json.dumps({"error": "OPENCART_STORAGE not configured — refusing to rm -rf"})
    cache_dir = shlex.quote(f"{config.storage_dir.rstrip('/')}/cache")
    out = db.run_command(f"rm -rf {cache_dir}/* 2>&1 && echo 'Cache cleared'")
    return out.strip()


@mcp.tool()
def run_sql(sql: str) -> str:
    """Execute a write SQL statement (INSERT/UPDATE/DELETE).
    Use with caution — changes the database directly."""

    cleaned = sql.strip().rstrip(";").strip()
    first_word = cleaned.split()[0].upper() if cleaned.split() else ""

    if first_word in ("DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT", "REVOKE"):
        return json.dumps({"error": f"DDL operation '{first_word}' not allowed. Too dangerous."})

    result = db.run_query(cleaned)
    return json.dumps(result)


@mcp.tool()
def refresh_modifications() -> str:
    """Clear the OCMOD modification cache so OpenCart serves unmodified files.
    Run Admin > Extensions > Modifications > Refresh afterwards for the full
    recompile — this tool cannot do that step."""

    # Check the DB is reachable and count active mods BEFORE deleting anything —
    # never leave a live store stripped of its OCMOD cache on a failed run.
    rows = db.run_query("SELECT COUNT(*) AS n FROM oc_modification WHERE status = 1")
    active = int(rows[0]["n"]) if rows else 0

    blocked = _shell_error()
    if blocked:
        payload = json.loads(blocked)
        payload["active_modifications"] = active
        payload["note"] = "Refresh OCMOD in Admin > Extensions > Modifications instead."
        return json.dumps(payload)

    mod_dir = shlex.quote(f"{config.storage_dir.rstrip('/')}/modification")
    out = db.run_command(f"rm -rf {mod_dir}/* 2>&1 && echo __cleared__")
    if "__cleared__" not in out:
        return json.dumps({"error": f"Cache clear failed: {out.strip()[:300]}"})
    return json.dumps({
        "cleared": True,
        "active_modifications": active,
        "note": "Run Admin > Extensions > Modifications > Refresh in browser for full recompile",
    })


# ─── INFORMATION PAGES ────────────────────────────────────────


@mcp.tool()
def get_information_pages(search: str = "") -> str:
    """List CMS/information pages (About Us, FAQ, T&Cs, etc.) with title and content preview.
    Search by title text."""

    where = "WHERE 1=1"
    if search:
        safe = esc(search)
        where += f" AND id.title LIKE '%{safe}%'"

    sql = f"""
        SELECT i.information_id, id.title, i.status, i.sort_order, i.noindex,
               SUBSTRING(id.description, 1, 300) AS description_preview,
               su.keyword AS seo_url, id.meta_h1
        FROM oc_information i
        JOIN oc_information_description id ON i.information_id = id.information_id AND id.language_id = {lang_id()}
        LEFT JOIN oc_seo_url su ON su.query = CONCAT('information_id=', i.information_id) AND su.language_id = {lang_id()}
        {where}
        ORDER BY i.sort_order, id.title
    """
    rows = db.run_query(sql)
    return json.dumps(rows)


@mcp.tool()
def get_information_page(information_id: int) -> str:
    """Get full content of a single information/CMS page by ID."""

    sql = f"""
        SELECT i.information_id, id.title, id.description, id.meta_title, id.meta_h1,
               id.meta_description, id.meta_keyword, i.status, i.sort_order, i.noindex,
               su.keyword AS seo_url
        FROM oc_information i
        JOIN oc_information_description id ON i.information_id = id.information_id AND id.language_id = {lang_id()}
        LEFT JOIN oc_seo_url su ON su.query = 'information_id={int(information_id)}' AND su.language_id = {lang_id()}
        WHERE i.information_id = {int(information_id)}
    """
    rows = db.run_query(sql)
    if not rows:
        return json.dumps({"error": f"Information page {information_id} not found"})
    return json.dumps(rows[0])


@mcp.tool()
def update_information(information_id: int, find: str, replace: str) -> str:
    """Update text within an information/CMS page using find/replace.
    Works on the HTML description field. Use get_information_page first to see current content."""

    rows = db.run_query(
        f"SELECT description FROM oc_information_description WHERE information_id = {int(information_id)} AND language_id = {lang_id()}"
    )
    if not rows:
        return json.dumps({"error": f"Information page {information_id} not found"})

    current = rows[0]["description"]

    if find not in current:
        return json.dumps({"error": f"Text not found in information page {information_id}"})

    count = current.count(find)
    updated = current.replace(find, replace)

    safe_updated = esc(updated)
    result = db.run_query(
        f"UPDATE oc_information_description SET description = '{safe_updated}' "
        f"WHERE information_id = {int(information_id)} AND language_id = {lang_id()}"
    )
    return json.dumps({
        **_update_status(result), "information_id": information_id,
        "replacements": count, "result": result,
    })


# ─── UTILITY ─────────────────────────────────────────────────


@mcp.tool()
def get_seo_urls(query_pattern: str = "") -> str:
    """Get SEO URL mappings. Filter by query pattern (e.g. 'product_id=%')."""

    where = f"WHERE store_id = 0 AND language_id = {lang_id()}"
    if query_pattern:
        safe = esc(query_pattern)
        where += f" AND query LIKE '{safe}'"

    sql = f"""
        SELECT seo_url_id, query, keyword
        FROM oc_seo_url
        {where}
        ORDER BY keyword
        LIMIT 200
    """
    rows = db.run_query(sql)
    return json.dumps(rows)


@mcp.tool()
def get_stock_report(limit: int = 200) -> str:
    """Get stock levels for active products, sorted by quantity (lowest first)."""

    sql = f"""
        SELECT p.product_id, pd.name, p.model, p.sku, p.quantity, p.price,
               ss.name AS stock_status
        FROM oc_product p
        JOIN oc_product_description pd ON p.product_id = pd.product_id AND pd.language_id = {lang_id()}
        LEFT JOIN oc_stock_status ss ON p.stock_status_id = ss.stock_status_id AND ss.language_id = {lang_id()}
        WHERE p.status = 1
        ORDER BY p.quantity ASC
        LIMIT {int(limit)}
    """
    rows = db.run_query(sql)
    return json.dumps(rows)


# ─── ORDERS, COUPONS, DASHBOARD ──────────────────────────────


@mcp.tool()
def update_order_status(
    order_id: int,
    order_status_id: int,
    comment: str = "",
    notify: bool = False,
) -> str:
    """Change an order's status and append an order-history entry — the same
    thing the admin status dropdown does. notify=True marks the history row
    as customer-notified but does NOT send the email itself.
    Use get_order_statuses to look up status IDs."""

    order = db.run_query(
        f"SELECT order_id, order_status_id FROM oc_order WHERE order_id = {int(order_id)}"
    )
    if not order:
        return json.dumps({"error": f"Order {order_id} not found"})

    status_row = db.run_query(
        f"SELECT name FROM oc_order_status WHERE order_status_id = {int(order_status_id)} AND language_id = {lang_id()}"
    )
    if not status_row:
        return json.dumps({"error": f"Unknown order_status_id {order_status_id} — see get_order_statuses"})

    result = db.run_query(
        f"UPDATE oc_order SET order_status_id = {int(order_status_id)}, date_modified = NOW() "
        f"WHERE order_id = {int(order_id)}"
    )
    db.run_query(
        "INSERT INTO oc_order_history (order_id, order_status_id, notify, comment, date_added) "
        f"VALUES ({int(order_id)}, {int(order_status_id)}, {1 if notify else 0}, '{esc(comment)}', NOW())"
    )
    return json.dumps({
        **_update_status(result),
        "order_id": order_id,
        "previous_status_id": int(order[0]["order_status_id"]),
        "new_status_id": order_status_id,
        "new_status": status_row[0]["name"],
        "history_added": True,
    })


@mcp.tool()
def get_coupons(search: str = "", include_disabled: bool = False, limit: int = 50) -> str:
    """List discount coupons with usage counts. Search by code or name.
    Default: enabled, unexpired coupons only."""

    where = "WHERE 1=1"
    if search:
        where += f" AND (c.code LIKE '%{esc(search)}%' OR c.name LIKE '%{esc(search)}%')"
    if not include_disabled:
        where += " AND c.status = 1 AND c.date_end >= CURDATE()"

    sql = f"""
        SELECT c.coupon_id, c.code, c.name, c.type, c.discount,
               c.total AS min_order_total, c.shipping AS free_shipping,
               c.date_start, c.date_end, c.uses_total, c.uses_customer, c.status,
               (SELECT COUNT(*) FROM oc_coupon_history ch WHERE ch.coupon_id = c.coupon_id) AS times_used
        FROM oc_coupon c
        {where}
        ORDER BY c.date_added DESC
        LIMIT {int(limit)}
    """
    rows = db.run_query(sql)
    return json.dumps(rows)


@mcp.tool()
def create_coupon(
    code: str,
    name: str,
    discount: float,
    type: str = "P",
    min_order_total: float = 0,
    days_valid: int = 30,
    uses_total: int = 0,
    uses_per_customer: int = 1,
    free_shipping: bool = False,
    logged_in_only: bool = False,
) -> str:
    """Create a discount coupon, enabled and valid from today.
    type: 'P' = percentage, 'F' = fixed amount. uses_total=0 = unlimited
    total uses; uses_per_customer=0 = unlimited per customer."""

    if type not in ("P", "F"):
        return json.dumps({"error": "type must be 'P' (percentage) or 'F' (fixed amount)"})
    if len(code) > 20:
        return json.dumps({"error": "code must be 20 characters or fewer (oc_coupon.code is varchar(20))"})

    existing = db.run_query(f"SELECT coupon_id FROM oc_coupon WHERE code = '{esc(code)}'")
    if existing:
        return json.dumps({"error": f"Coupon code '{code}' already exists (coupon_id {existing[0]['coupon_id']})"})

    # NB uses_customer is varchar(11) in the OC3 schema — quoted deliberately
    result = db.run_query(
        "INSERT INTO oc_coupon (name, code, type, discount, logged, shipping, total, "
        "date_start, date_end, uses_total, uses_customer, status, date_added) VALUES "
        f"('{esc(name)}', '{esc(code)}', '{type}', {float(discount)}, "
        f"{1 if logged_in_only else 0}, {1 if free_shipping else 0}, {float(min_order_total)}, "
        f"CURDATE(), DATE_ADD(CURDATE(), INTERVAL {int(days_valid)} DAY), "
        f"{int(uses_total)}, '{int(uses_per_customer)}', 1, NOW())"
    )
    return json.dumps({
        "created": True,
        "coupon_id": result.get("insert_id") if isinstance(result, dict) else None,
        "code": code,
        "type": type,
        "discount": discount,
        "days_valid": days_valid,
    })


@mcp.tool()
def update_coupon(
    coupon_id: int,
    status: int | None = None,
    discount: float | None = None,
    date_end: str | None = None,
    uses_total: int | None = None,
    name: str | None = None,
) -> str:
    """Update a coupon: enable/disable (status 1/0), change discount,
    extend date_end (YYYY-MM-DD), adjust total-use limit, or rename.
    Only specified fields are changed."""

    updates = []
    if status is not None:
        updates.append(f"status = {int(status)}")
    if discount is not None:
        updates.append(f"discount = {float(discount)}")
    if date_end is not None:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_end):
            return json.dumps({"error": "date_end must be YYYY-MM-DD"})
        updates.append(f"date_end = '{date_end}'")
    if uses_total is not None:
        updates.append(f"uses_total = {int(uses_total)}")
    if name is not None:
        updates.append(f"name = '{esc(name)}'")

    if not updates:
        return json.dumps({"error": "No fields to update"})

    result = db.run_query(
        f"UPDATE oc_coupon SET {', '.join(updates)} WHERE coupon_id = {int(coupon_id)}"
    )
    return json.dumps({**_update_status(result), "coupon_id": coupon_id})


@mcp.tool()
def get_vouchers(limit: int = 50) -> str:
    """List gift vouchers with amount, sender/recipient, and status."""

    sql = f"""
        SELECT voucher_id, order_id, code, from_name, to_name, to_email,
               amount, status, date_added
        FROM oc_voucher
        ORDER BY date_added DESC
        LIMIT {int(limit)}
    """
    return json.dumps(db.run_query(sql))


@mcp.tool()
def dashboard(low_stock_threshold: int = 5) -> str:
    """One-call store overview: revenue today / 7 days / 30 days, order status
    breakdown, stock alerts, and the latest orders. The 'give me a store
    summary' tool. Excludes cancelled/failed/refunded orders from revenue."""

    excluded = EXCLUDED_ORDER_STATUS_IDS

    revenue = db.run_query(f"""
        SELECT
            COUNT(CASE WHEN date_added >= CURDATE() THEN 1 END) AS orders_today,
            ROUND(COALESCE(SUM(CASE WHEN date_added >= CURDATE() THEN total END), 0), 2) AS revenue_today,
            COUNT(CASE WHEN date_added >= DATE_SUB(NOW(), INTERVAL 7 DAY) THEN 1 END) AS orders_7d,
            ROUND(COALESCE(SUM(CASE WHEN date_added >= DATE_SUB(NOW(), INTERVAL 7 DAY) THEN total END), 0), 2) AS revenue_7d,
            COUNT(*) AS orders_30d,
            ROUND(COALESCE(SUM(total), 0), 2) AS revenue_30d,
            ROUND(COALESCE(AVG(total), 0), 2) AS avg_order_30d
        FROM oc_order
        WHERE date_added >= DATE_SUB(NOW(), INTERVAL 30 DAY)
          AND order_status_id NOT IN {excluded}
    """)

    statuses = db.run_query(f"""
        SELECT os.name AS status, COUNT(*) AS orders
        FROM oc_order o
        LEFT JOIN oc_order_status os ON o.order_status_id = os.order_status_id AND os.language_id = {lang_id()}
        WHERE o.date_added >= DATE_SUB(NOW(), INTERVAL 30 DAY) AND o.order_status_id > 0
        GROUP BY os.name
        ORDER BY orders DESC
    """)

    stock = db.run_query(f"""
        SELECT COUNT(*) AS active_products,
               COUNT(CASE WHEN quantity <= 0 THEN 1 END) AS out_of_stock,
               COUNT(CASE WHEN quantity BETWEEN 1 AND {int(low_stock_threshold)} THEN 1 END) AS low_stock
        FROM oc_product
        WHERE status = 1
    """)

    lowest = db.run_query(f"""
        SELECT p.product_id, pd.name, p.quantity
        FROM oc_product p
        JOIN oc_product_description pd ON p.product_id = pd.product_id AND pd.language_id = {lang_id()}
        WHERE p.status = 1
        ORDER BY p.quantity ASC
        LIMIT 5
    """)

    latest = db.run_query(f"""
        SELECT o.order_id, CONCAT(o.firstname, ' ', o.lastname) AS customer,
               o.total, os.name AS status, o.date_added
        FROM oc_order o
        LEFT JOIN oc_order_status os ON o.order_status_id = os.order_status_id AND os.language_id = {lang_id()}
        ORDER BY o.date_added DESC
        LIMIT 5
    """)

    return json.dumps({
        "revenue": revenue[0] if revenue else {},
        "order_status_breakdown_30d": statuses,
        "stock": {**(stock[0] if stock else {}), "lowest_5": lowest},
        "latest_orders": latest,
    })


# ─── TECHNICS / LIVESTORE ─────────────────────────────────────


@mcp.tool()
def get_technics_blog(search: str = "", limit: int = 50, include_description: bool = False) -> str:
    """List Technics blog posts (oc_technics_blog). Search by title."""

    where = f"WHERE bd.language_id = {lang_id()}"
    if search:
        where += f" AND bd.title LIKE '%{esc(search)}%'"
    desc_col = ", bd.description" if include_description else ", LEFT(bd.description, 240) AS description_preview"
    sql = f"""
        SELECT b.blog_id, bd.title, b.status, b.viewed, b.date_added, b.image,
               bd.meta_title, bd.meta_h1{desc_col}
        FROM oc_technics_blog b
        JOIN oc_technics_blog_description bd ON b.blog_id = bd.blog_id AND bd.language_id = {lang_id()}
        {where}
        ORDER BY b.date_added DESC, b.blog_id DESC
        LIMIT {int(limit)}
    """
    return json.dumps(_optional_query(sql))


@mcp.tool()
def get_technics_blog_post(blog_id: int) -> str:
    """Get a single Technics blog post with full HTML and comments."""

    rows = _optional_query(f"""
        SELECT b.*, bd.title, bd.description, bd.meta_title, bd.meta_h1,
               bd.meta_description, bd.meta_keyword, bd.tag
        FROM oc_technics_blog b
        JOIN oc_technics_blog_description bd ON b.blog_id = bd.blog_id AND bd.language_id = {lang_id()}
        WHERE b.blog_id = {int(blog_id)}
    """)
    if not rows:
        return json.dumps({"error": f"Technics blog post {blog_id} not found"})
    comments = _optional_query(f"""
        SELECT comment_id, author, text, rating, status, date_added
        FROM oc_technics_blog_comment
        WHERE blog_id = {int(blog_id)}
        ORDER BY date_added DESC
    """)
    result = rows[0]
    result["comments"] = comments
    return json.dumps(result)


@mcp.tool()
def get_technics_news(search: str = "", limit: int = 50) -> str:
    """List Technics news items."""

    where = f"WHERE nd.language_id = {lang_id()}"
    if search:
        where += f" AND nd.title LIKE '%{esc(search)}%'"
    sql = f"""
        SELECT n.news_id, nd.title, n.status, n.date_added, n.sort_order,
               LEFT(nd.description, 240) AS description_preview, nd.meta_h1
        FROM oc_technics_news n
        JOIN oc_technics_news_description nd ON n.news_id = nd.news_id AND nd.language_id = {lang_id()}
        {where}
        ORDER BY n.date_added DESC, n.news_id DESC
        LIMIT {int(limit)}
    """
    return json.dumps(_optional_query(sql))


@mcp.tool()
def get_technics_sets() -> str:
    """List Technics product sets (kits) with titles."""

    sql = f"""
        SELECT s.set_id, sd.title, s.mode, s.discount, s.sort_order, s.status, s.date_added,
               LEFT(sd.description, 240) AS description_preview
        FROM oc_technics_set s
        JOIN oc_technics_set_description sd ON s.set_id = sd.set_id AND sd.language_id = {lang_id()}
        ORDER BY s.sort_order, s.set_id
    """
    return json.dumps(_optional_query(sql))


@mcp.tool()
def get_callbacks(limit: int = 50) -> str:
    """List Technics callback requests (oc_callback)."""

    sql = f"""
        SELECT call_id, name, telephone, date_added, date_modified, status_id, store_id,
               LEFT(comment, 240) AS comment_preview
        FROM oc_callback
        ORDER BY date_added DESC
        LIMIT {int(limit)}
    """
    return json.dumps(_optional_query(sql))


@mcp.tool()
def get_articles(search: str = "", limit: int = 50, include_description: bool = False) -> str:
    """List LiveStore built-in blog articles (oc_article), not Technics blog."""

    where = f"WHERE ad.language_id = {lang_id()}"
    if search:
        where += f" AND ad.name LIKE '%{esc(search)}%'"
    desc_col = ", ad.description" if include_description else ", LEFT(ad.description, 240) AS description_preview"
    sql = f"""
        SELECT a.article_id, ad.name, a.status, a.noindex, a.date_added, a.date_modified,
               a.viewed, a.image, ad.meta_title, ad.meta_h1{desc_col}
        FROM oc_article a
        JOIN oc_article_description ad ON a.article_id = ad.article_id AND ad.language_id = {lang_id()}
        {where}
        ORDER BY a.date_added DESC
        LIMIT {int(limit)}
    """
    return json.dumps(_optional_query(sql))


@mcp.tool()
def get_article(article_id: int) -> str:
    """Get a single LiveStore article with full HTML."""

    rows = _optional_query(f"""
        SELECT a.*, ad.name, ad.description, ad.meta_title, ad.meta_h1,
               ad.meta_description, ad.meta_keyword, ad.tag
        FROM oc_article a
        JOIN oc_article_description ad ON a.article_id = ad.article_id AND ad.language_id = {lang_id()}
        WHERE a.article_id = {int(article_id)}
    """)
    if not rows:
        return json.dumps({"error": f"Article {article_id} not found"})
    return json.dumps(rows[0])


@mcp.tool()
def update_article(article_id: int, find: str, replace: str) -> str:
    """Find/replace text in a LiveStore article HTML description."""

    rows = _optional_query(
        f"SELECT description FROM oc_article_description "
        f"WHERE article_id = {int(article_id)} AND language_id = {lang_id()}"
    )
    if not rows:
        return json.dumps({"error": f"Article {article_id} not found"})
    current = rows[0]["description"]
    if find not in current:
        return json.dumps({"error": f"Text not found in article {article_id}"})
    count = current.count(find)
    result = db.run_query(
        f"UPDATE oc_article_description SET description = '{esc(current.replace(find, replace))}' "
        f"WHERE article_id = {int(article_id)} AND language_id = {lang_id()}"
    )
    return json.dumps({
        **_update_status(result), "article_id": article_id,
        "replacements": count, "result": result,
    })


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
