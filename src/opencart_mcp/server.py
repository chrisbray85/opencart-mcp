"""OpenCart MCP Server — query and manage OpenCart via Claude Code."""

import json
import re
import shlex

from fastmcp import FastMCP

from .config import Config
from .db import OpenCartDB

config = Config.from_env()
db = OpenCartDB(config)

# Statuses excluded from revenue: missing(0), cancelled(7), denied(8),
# canceled reversal(9), failed(10), refunded(11), reversed(12), chargeback(13),
# expired(14), voided(16)
EXCLUDED_ORDER_STATUS_IDS = "(0, 7, 8, 9, 10, 11, 12, 13, 14, 16)"

_lang_id: int | None = None


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


def _shell_error() -> str | None:
    """JSON error for tools that need a shell, when running in direct-MySQL mode."""
    if config.is_direct_mysql:
        return json.dumps({
            "error": "File and cache tools need SSH or DDEV. Direct MySQL mode only runs SQL.",
        })
    return None


def esc(s: str) -> str:
    """Escape a value for interpolation into a single-quoted MySQL string.
    Backslash first, then quote — MySQL consumes backslash escapes."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


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


def _j3_query(sql: str):
    """Journal3 tables are optional — missing table means empty result, per README."""
    try:
        return db.run_query(sql)
    except RuntimeError as e:
        if "doesn't exist" in str(e):
            return []
        raise

mcp = FastMCP("OpenCart")


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
               p.weight, p.image,
               su.keyword AS seo_url,
               pd.meta_title, pd.meta_description{desc_col}
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
        SELECT p.*, pd.name, pd.description, pd.meta_title, pd.meta_description, pd.tag,
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
        SELECT c.category_id, cd.name, c.parent_id, c.status, c.sort_order,
               su.keyword AS seo_url,
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
def get_j3_settings(pattern: str = "") -> str:
    """Get Journal3 theme settings. Filter by setting_name pattern (SQL LIKE)."""

    where = "WHERE 1=1"
    if pattern:
        safe = esc(pattern)
        where += f" AND setting_name LIKE '{safe}'"

    sql = f"""
        SELECT setting_name, setting_value
        FROM oc_journal3_setting
        {where}
        ORDER BY setting_name
        LIMIT 100
    """
    rows = _j3_query(sql)
    return json.dumps(rows)


@mcp.tool()
def get_j3_skin_settings(pattern: str = "", skin_id: int = 1) -> str:
    """Get Journal3 skin settings. Filter by setting_name pattern (SQL LIKE)."""

    where = f"WHERE skin_id = {int(skin_id)}"
    if pattern:
        safe = esc(pattern)
        where += f" AND setting_name LIKE '{safe}'"

    sql = f"""
        SELECT setting_name, setting_value
        FROM oc_journal3_skin_setting
        {where}
        ORDER BY setting_name
        LIMIT 100
    """
    rows = _j3_query(sql)
    return json.dumps(rows)


@mcp.tool()
def get_modules(module_type: str = "", search: str = "") -> str:
    """List Journal3 modules. Filter by type (e.g. 'products', 'slider', 'product_tabs').
    Search module_data content with search parameter."""

    where_parts = []
    if module_type:
        safe = esc(module_type)
        where_parts.append(f"module_type = '{safe}'")
    if search:
        safe = esc(search)
        where_parts.append(f"module_data LIKE '%{safe}%'")

    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    sql = f"""
        SELECT module_id, module_type,
               SUBSTRING(module_data, 1, 200) AS module_data_preview
        FROM oc_journal3_module
        {where}
        ORDER BY module_type, module_id
    """
    rows = _j3_query(sql)
    return json.dumps(rows)


@mcp.tool()
def get_j3_module(module_id: int) -> str:
    """Get full Journal3 module data for a single module by ID.
    Returns full JSON config — can be large for complex modules."""

    sql = f"""
        SELECT module_id, module_type, module_data
        FROM oc_journal3_module
        WHERE module_id = {int(module_id)}
    """
    rows = _j3_query(sql)
    if not rows:
        return json.dumps({"error": f"Module {module_id} not found"})
    return json.dumps(rows[0])


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


# Cap MCP response size; OpenCart error.log can be hundreds of MB.
_GET_FILE_MAX_LINES = 2000
# When from_end+grep, only scan this many trailing lines (avoid full-file grep).
_GET_FILE_GREP_WINDOW = 50000


@mcp.tool()
def get_file(
    path: str,
    max_lines: int = 200,
    from_end: bool = False,
    grep: str = "",
) -> str:
    """Read a file from the VPS. Path is relative to OpenCart root unless absolute.

    By default returns the first max_lines lines (head). Set from_end=True for the
    last max_lines (tail) — preferred for large logs such as error.log.
    Optional grep is a fixed substring filter (not a regex). With from_end=True,
    grep only searches the last ~50k lines so huge logs stay fast; with
    from_end=False, grep scans the whole file then takes the first max_lines
    matches (can be slow on very large files). max_lines is capped at 2000.
    Requires SSH or DDEV."""

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

    n = max(1, min(int(max_lines), _GET_FILE_MAX_LINES))
    quoted = shlex.quote(full_path)
    pattern = grep.strip()

    if pattern:
        quoted_pat = shlex.quote(pattern)
        if from_end:
            window = max(n, _GET_FILE_GREP_WINDOW)
            cmd = (
                f"tail -n {window} {quoted} | grep -F -- {quoted_pat} | tail -n {n}"
            )
        else:
            cmd = f"grep -F -- {quoted_pat} {quoted} | head -n {n}"
        # grep exits 1 when there are no matches; run_command ignores exit codes.
        return db.run_command(cmd, timeout=60)

    if from_end:
        cmd = f"tail -n {n} {quoted}"
    else:
        cmd = f"head -n {n} {quoted}"
    return db.run_command(cmd)


# ─── WRITE TOOLS ─────────────────────────────────────────────


@mcp.tool()
def update_product(
    product_id: int,
    price: float | None = None,
    quantity: int | None = None,
    status: int | None = None,
    meta_title: str | None = None,
    meta_description: str | None = None,
    name: str | None = None,
) -> str:
    """Update product fields. Only specified fields are changed."""

    updates_product = []
    updates_desc = []

    if price is not None:
        updates_product.append(f"price = {float(price)}")
    if quantity is not None:
        updates_product.append(f"quantity = {int(quantity)}")
    if status is not None:
        updates_product.append(f"status = {int(status)}")

    if meta_title is not None:
        safe = esc(meta_title)
        updates_desc.append(f"meta_title = '{safe}'")
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
def update_j3_setting(setting_name: str, setting_value: str) -> str:
    """Update a Journal3 theme setting."""

    safe_name = esc(setting_name)
    safe_value = esc(setting_value)

    result = db.run_query(
        f"UPDATE oc_journal3_setting SET setting_value = '{safe_value}' WHERE setting_name = '{safe_name}'"
    )
    return json.dumps({**_update_status(result), "setting_name": setting_name, "result": result})


@mcp.tool()
def update_j3_skin_setting(setting_name: str, setting_value: str, skin_id: int = 1) -> str:
    """Update a Journal3 skin setting."""

    safe_name = esc(setting_name)
    safe_value = esc(setting_value)

    result = db.run_query(
        f"UPDATE oc_journal3_skin_setting SET setting_value = '{safe_value}' WHERE setting_name = '{safe_name}' AND skin_id = {int(skin_id)}"
    )
    return json.dumps({**_update_status(result), "setting_name": setting_name, "result": result})


@mcp.tool()
def update_j3_module(module_id: int, find: str, replace: str) -> str:
    """Update text within a Journal3 module's JSON data using find/replace.
    Safer than rewriting the entire module — only changes the matched text.
    Use get_j3_module first to see the current content."""

    # Fetch current module data
    rows = db.run_query(f"SELECT module_data FROM oc_journal3_module WHERE module_id = {int(module_id)}")
    if not rows:
        return json.dumps({"error": f"Module {module_id} not found"})

    current = rows[0]["module_data"]

    if find not in current:
        return json.dumps({"error": f"Text '{find}' not found in module {module_id}"})

    count = current.count(find)
    updated = current.replace(find, replace)

    safe_updated = esc(updated)
    result = db.run_query(
        f"UPDATE oc_journal3_module SET module_data = '{safe_updated}' WHERE module_id = {int(module_id)}"
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
    meta_description: str | None = None,
    status: int | None = None,
) -> str:
    """Update category fields. Only specified fields are changed."""

    updates_cat = []
    updates_desc = []

    if status is not None:
        updates_cat.append(f"status = {int(status)}")

    if name is not None:
        safe = esc(name)
        updates_desc.append(f"name = '{safe}'")
    if meta_title is not None:
        safe = esc(meta_title)
        updates_desc.append(f"meta_title = '{safe}'")
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
    Creates parent directories if needed. Use with caution. Requires SSH or DDEV."""

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
    """Clear OpenCart and Journal3 caches on VPS. Requires SSH or DDEV."""

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

    where = f"WHERE id.language_id = {lang_id()}"
    if search:
        safe = esc(search)
        where += f" AND id.title LIKE '%{safe}%'"

    sql = f"""
        SELECT i.information_id, id.title, i.status, i.sort_order,
               SUBSTRING(id.description, 1, 300) AS description_preview,
               su.keyword AS seo_url
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
        SELECT i.information_id, id.title, id.description, id.meta_title,
               id.meta_description, id.meta_keyword, i.status, i.sort_order,
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


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
