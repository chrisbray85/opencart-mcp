"""OpenCart MCP Server — query and manage OpenCart via Claude Code."""

import json
import re

from fastmcp import FastMCP

from .config import Config
from .db import OpenCartDB

config = Config.from_env()
db = OpenCartDB(config)

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
        safe = search.replace("'", "\\'")
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
        JOIN oc_product_description pd ON p.product_id = pd.product_id AND pd.language_id = 1
        LEFT JOIN oc_seo_url su ON su.query = CONCAT('product_id=', p.product_id) AND su.language_id = 1
        {where}
        ORDER BY pd.name
        LIMIT {int(limit)}
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_product(product_id: int) -> str:
    """Get full details for a single product including images, options, and attributes."""

    sql = f"""
        SELECT p.*, pd.name, pd.description, pd.meta_title, pd.meta_description, pd.tag,
               su.keyword AS seo_url
        FROM oc_product p
        JOIN oc_product_description pd ON p.product_id = pd.product_id AND pd.language_id = 1
        LEFT JOIN oc_seo_url su ON su.query = 'product_id={int(product_id)}' AND su.language_id = 1
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
        JOIN oc_category_description cd ON ptc.category_id = cd.category_id AND cd.language_id = 1
        WHERE ptc.product_id = {int(product_id)}
    """)

    # Get options
    options = db.run_query(f"""
        SELECT pov.product_option_value_id, od.name AS option_name, ovd.name AS value_name,
               pov.quantity, pov.price, pov.price_prefix, pov.weight, pov.weight_prefix
        FROM oc_product_option_value pov
        JOIN oc_product_option po ON pov.product_option_id = po.product_option_id
        JOIN oc_option_description od ON po.option_id = od.option_id AND od.language_id = 1
        JOIN oc_option_value_description ovd ON pov.option_value_id = ovd.option_value_id AND ovd.language_id = 1
        WHERE pov.product_id = {int(product_id)}
    """)

    result = product[0]
    result["images"] = images
    result["categories"] = cats
    result["options"] = options
    return json.dumps(result, indent=2)


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
        safe = status.replace("'", "\\'")
        where += f" AND os.name = '{safe}'"

    sql = f"""
        SELECT o.order_id, CONCAT(o.firstname, ' ', o.lastname) AS customer,
               o.email, o.total, o.currency_code, os.name AS status,
               o.date_added, o.date_modified,
               o.payment_method, o.shipping_method,
               o.shipping_city, o.shipping_postcode, o.shipping_country
        FROM oc_order o
        LEFT JOIN oc_order_status os ON o.order_status_id = os.order_status_id AND os.language_id = 1
        {where}
        ORDER BY o.date_added DESC
        LIMIT {int(limit)}
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_order(order_id: int) -> str:
    """Get full order details including line items and totals."""

    order = db.run_query(f"""
        SELECT o.*, os.name AS status_name
        FROM oc_order o
        LEFT JOIN oc_order_status os ON o.order_status_id = os.order_status_id AND os.language_id = 1
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
        LEFT JOIN oc_order_status os ON oh.order_status_id = os.order_status_id AND os.language_id = 1
        WHERE oh.order_id = {int(order_id)}
        ORDER BY oh.date_added DESC
    """)

    result = order[0]
    result["items"] = items
    result["totals"] = totals
    result["history"] = history
    return json.dumps(result, indent=2)


@mcp.tool()
def get_customers(search: str = "", limit: int = 20) -> str:
    """Search customers by name or email."""
    where = "WHERE 1=1"
    if search:
        safe = search.replace("'", "\\'")
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
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_categories(parent_id: int = 0) -> str:
    """Get category tree. Set parent_id=0 for top-level categories."""

    sql = f"""
        SELECT c.category_id, cd.name, c.parent_id, c.status, c.sort_order,
               su.keyword AS seo_url,
               (SELECT COUNT(*) FROM oc_product_to_category ptc WHERE ptc.category_id = c.category_id) AS product_count
        FROM oc_category c
        JOIN oc_category_description cd ON c.category_id = cd.category_id AND cd.language_id = 1
        LEFT JOIN oc_seo_url su ON su.query = CONCAT('category_id=', c.category_id) AND su.language_id = 1
        WHERE c.parent_id = {int(parent_id)}
        ORDER BY c.sort_order, cd.name
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_settings(group: str = "", key: str = "") -> str:
    """Get OpenCart settings. Filter by group (e.g. 'config') and/or key pattern (SQL LIKE)."""

    where = "WHERE store_id = 0"
    if group:
        safe = group.replace("'", "\\'")
        where += f" AND code = '{safe}'"
    if key:
        safe = key.replace("'", "\\'")
        where += f" AND `key` LIKE '{safe}'"

    sql = f"SELECT setting_id, code, `key`, value, serialized FROM oc_setting {where} ORDER BY code, `key`"
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_j3_settings(pattern: str = "") -> str:
    """Get Journal3 theme settings. Filter by setting_name pattern (SQL LIKE)."""

    where = "WHERE 1=1"
    if pattern:
        safe = pattern.replace("'", "\\'")
        where += f" AND setting_name LIKE '{safe}'"

    sql = f"""
        SELECT setting_name, setting_value
        FROM oc_journal3_setting
        {where}
        ORDER BY setting_name
        LIMIT 100
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_j3_skin_settings(pattern: str = "", skin_id: int = 1) -> str:
    """Get Journal3 skin settings. Filter by setting_name pattern (SQL LIKE)."""

    where = f"WHERE skin_id = {int(skin_id)}"
    if pattern:
        safe = pattern.replace("'", "\\'")
        where += f" AND setting_name LIKE '{safe}'"

    sql = f"""
        SELECT setting_name, setting_value
        FROM oc_journal3_skin_setting
        {where}
        ORDER BY setting_name
        LIMIT 100
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_modules(module_type: str = "", search: str = "") -> str:
    """List Journal3 modules. Filter by type (e.g. 'products', 'slider', 'product_tabs').
    Search module_data content with search parameter."""

    where_parts = []
    if module_type:
        safe = module_type.replace("'", "\\'")
        where_parts.append(f"module_type = '{safe}'")
    if search:
        safe = search.replace("'", "\\'")
        where_parts.append(f"module_data LIKE '%{safe}%'")

    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    sql = f"""
        SELECT module_id, module_type,
               SUBSTRING(module_data, 1, 200) AS module_data_preview
        FROM oc_journal3_module
        {where}
        ORDER BY module_type, module_id
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_j3_module(module_id: int) -> str:
    """Get full Journal3 module data for a single module by ID.
    Returns full JSON config — can be large for complex modules."""

    sql = f"""
        SELECT module_id, module_type, module_data
        FROM oc_journal3_module
        WHERE module_id = {int(module_id)}
    """
    rows = db.run_query(sql)
    if not rows:
        return json.dumps({"error": f"Module {module_id} not found"})
    return json.dumps(rows[0], indent=2)


@mcp.tool()
def get_order_statuses() -> str:
    """List all order statuses with their IDs."""

    sql = """
        SELECT order_status_id, name
        FROM oc_order_status
        WHERE language_id = 1
        ORDER BY order_status_id
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_product_attributes(product_id: int) -> str:
    """Get all attributes for a product (e.g. CAS number, molecular weight, storage)."""

    sql = f"""
        SELECT pa.attribute_id, ad.name AS attribute_name,
               agd.name AS attribute_group, pa.text AS value
        FROM oc_product_attribute pa
        JOIN oc_attribute a ON pa.attribute_id = a.attribute_id
        JOIN oc_attribute_description ad ON a.attribute_id = ad.attribute_id AND ad.language_id = 1
        JOIN oc_attribute_group_description agd ON a.attribute_group_id = agd.attribute_group_id AND agd.language_id = 1
        WHERE pa.product_id = {int(product_id)} AND pa.language_id = 1
        ORDER BY agd.name, ad.name
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


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
          AND order_status_id NOT IN (0, 7, 8, 10, 14, 11, 12)
    """)

    # Top products by units sold
    top_products = db.run_query(f"""
        SELECT op.product_id, pd.name,
               SUM(op.quantity) AS units_sold,
               ROUND(SUM(op.total), 2) AS revenue,
               COUNT(DISTINCT op.order_id) AS order_count
        FROM oc_order_product op
        JOIN oc_order o ON op.order_id = o.order_id
        JOIN oc_product_description pd ON op.product_id = pd.product_id AND pd.language_id = 1
        WHERE o.date_added >= DATE_SUB(NOW(), INTERVAL {int(days)} DAY)
          AND o.order_status_id NOT IN (0, 7, 8, 10, 14, 11, 12)
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
          AND order_status_id NOT IN (0, 7, 8, 10, 14, 11, 12)
        GROUP BY DATE(date_added)
        ORDER BY date DESC
    """)

    result = {
        "period_days": days,
        "summary": stats[0] if stats else {},
        "top_products": top_products,
        "daily_revenue": daily,
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def get_modifications() -> str:
    """List all OCMOD modifications with status."""

    sql = """
        SELECT modification_id, name, code, author, status, date_added
        FROM oc_modification
        ORDER BY name
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_extensions() -> str:
    """List installed OpenCart extensions."""

    sql = """
        SELECT extension_id, type, code
        FROM oc_extension
        ORDER BY type, code
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


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
    if dangerous.search(cleaned):
        return json.dumps({"error": "Write operations not allowed in query(). Use run_sql() instead."})

    rows = db.run_query(cleaned)
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_table_schema(table: str) -> str:
    """Show columns for an OpenCart table. Prefix 'oc_' is added automatically if missing."""

    if not table.startswith("oc_"):
        table = f"oc_{table}"

    # Validate table name (alphanumeric + underscore only)
    if not re.match(r"^[a-zA-Z0-9_]+$", table):
        return json.dumps({"error": "Invalid table name"})

    rows = db.run_query(f"SHOW COLUMNS FROM {table}")
    return json.dumps(rows, indent=2)


@mcp.tool()
def list_tables(pattern: str = "oc_%") -> str:
    """List database tables matching pattern. Default: all OpenCart tables."""

    safe = pattern.replace("'", "\\'")
    rows = db.run_query(f"SHOW TABLES LIKE '{safe}'")
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_file(path: str, max_lines: int = 200) -> str:
    """Read a file from the VPS. Path is relative to OpenCart root unless absolute.
    Returns first max_lines lines."""

    if not path.startswith("/"):
        full_path = f"{config.oc_root}/{path}"
    else:
        full_path = path

    # Security: block path traversal
    if ".." in path:
        return json.dumps({"error": "Path traversal not allowed"})

    out = db.run_command(f"head -n {int(max_lines)} '{full_path}' 2>&1")
    return out


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
        safe = meta_title.replace("'", "\\'")
        updates_desc.append(f"meta_title = '{safe}'")
    if meta_description is not None:
        safe = meta_description.replace("'", "\\'")
        updates_desc.append(f"meta_description = '{safe}'")
    if name is not None:
        safe = name.replace("'", "\\'")
        updates_desc.append(f"name = '{safe}'")

    if not updates_product and not updates_desc:
        return json.dumps({"error": "No fields to update"})

    results = []
    if updates_product:
        sql = f"UPDATE oc_product SET {', '.join(updates_product)} WHERE product_id = {int(product_id)}"
        r = db.run_query(sql)
        results.append({"table": "oc_product", "result": r})

    if updates_desc:
        sql = f"UPDATE oc_product_description SET {', '.join(updates_desc)} WHERE product_id = {int(product_id)} AND language_id = 1"
        r = db.run_query(sql)
        results.append({"table": "oc_product_description", "result": r})

    return json.dumps({"updated": True, "product_id": product_id, "results": results}, indent=2)


@mcp.tool()
def update_setting(group: str, key: str, value: str) -> str:
    """Update an OpenCart setting."""

    safe_group = group.replace("'", "\\'")
    safe_key = key.replace("'", "\\'")
    safe_value = value.replace("'", "\\'")

    result = db.run_query(
        f"UPDATE oc_setting SET value = '{safe_value}' WHERE code = '{safe_group}' AND `key` = '{safe_key}' AND store_id = 0"
    )
    return json.dumps({"updated": True, "code": group, "key": key, "result": result}, indent=2)


@mcp.tool()
def update_j3_setting(setting_name: str, setting_value: str) -> str:
    """Update a Journal3 theme setting."""

    safe_name = setting_name.replace("'", "\\'")
    safe_value = setting_value.replace("'", "\\'")

    result = db.run_query(
        f"UPDATE oc_journal3_setting SET setting_value = '{safe_value}' WHERE setting_name = '{safe_name}'"
    )
    return json.dumps({"updated": True, "setting_name": setting_name, "result": result}, indent=2)


@mcp.tool()
def update_j3_skin_setting(setting_name: str, setting_value: str, skin_id: int = 1) -> str:
    """Update a Journal3 skin setting."""

    safe_name = setting_name.replace("'", "\\'")
    safe_value = setting_value.replace("'", "\\'")

    result = db.run_query(
        f"UPDATE oc_journal3_skin_setting SET setting_value = '{safe_value}' WHERE setting_name = '{safe_name}' AND skin_id = {int(skin_id)}"
    )
    return json.dumps({"updated": True, "setting_name": setting_name, "result": result}, indent=2)


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

    safe_updated = updated.replace("\\", "\\\\").replace("'", "\\'")
    result = db.run_query(
        f"UPDATE oc_journal3_module SET module_data = '{safe_updated}' WHERE module_id = {int(module_id)}"
    )
    return json.dumps({
        "updated": True, "module_id": module_id,
        "replacements": count, "find": find, "replace": replace,
        "result": result,
    }, indent=2)


@mcp.tool()
def update_seo_url(query: str, keyword: str) -> str:
    """Update or create an SEO URL mapping. Query is e.g. 'product_id=123' or 'category_id=45'.
    Keyword is the URL slug (e.g. 'bpc-157-5mg')."""

    safe_query = query.replace("'", "\\'")
    safe_keyword = keyword.replace("'", "\\'")

    # Check if mapping exists
    existing = db.run_query(
        f"SELECT seo_url_id FROM oc_seo_url WHERE query = '{safe_query}' AND store_id = 0 AND language_id = 1"
    )

    if existing:
        result = db.run_query(
            f"UPDATE oc_seo_url SET keyword = '{safe_keyword}' WHERE query = '{safe_query}' AND store_id = 0 AND language_id = 1"
        )
        return json.dumps({"updated": True, "query": query, "keyword": keyword, "result": result}, indent=2)
    else:
        result = db.run_query(
            f"INSERT INTO oc_seo_url (store_id, language_id, query, keyword) VALUES (0, 1, '{safe_query}', '{safe_keyword}')"
        )
        return json.dumps({"created": True, "query": query, "keyword": keyword, "result": result}, indent=2)


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
        safe = name.replace("'", "\\'")
        updates_desc.append(f"name = '{safe}'")
    if meta_title is not None:
        safe = meta_title.replace("'", "\\'")
        updates_desc.append(f"meta_title = '{safe}'")
    if meta_description is not None:
        safe = meta_description.replace("'", "\\'")
        updates_desc.append(f"meta_description = '{safe}'")

    if not updates_cat and not updates_desc:
        return json.dumps({"error": "No fields to update"})

    results = []
    if updates_cat:
        sql = f"UPDATE oc_category SET {', '.join(updates_cat)} WHERE category_id = {int(category_id)}"
        r = db.run_query(sql)
        results.append({"table": "oc_category", "result": r})

    if updates_desc:
        sql = f"UPDATE oc_category_description SET {', '.join(updates_desc)} WHERE category_id = {int(category_id)} AND language_id = 1"
        r = db.run_query(sql)
        results.append({"table": "oc_category_description", "result": r})

    return json.dumps({"updated": True, "category_id": category_id, "results": results}, indent=2)


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write content to a file on the VPS via SFTP. Path is relative to OpenCart root unless absolute.
    Creates parent directories if needed. Use with caution."""

    if not path.startswith("/"):
        full_path = f"{config.oc_root}/{path}"
    else:
        full_path = path

    if ".." in path:
        return json.dumps({"error": "Path traversal not allowed"})

    # Ensure parent directory exists
    parent = "/".join(full_path.split("/")[:-1])
    db.run_command(f"mkdir -p '{parent}'")

    db.write_file(full_path, content)
    return json.dumps({"written": True, "path": full_path, "bytes": len(content)}, indent=2)


@mcp.tool()
def clear_cache() -> str:
    """Clear OpenCart and Journal3 caches on VPS."""

    out = db.run_command(f"rm -rf {config.storage_dir}/cache/* 2>&1 && echo 'Cache cleared'")
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
    return json.dumps(result, indent=2)


@mcp.tool()
def refresh_modifications() -> str:
    """Trigger OCMOD modification refresh — recompiles all modification cache files."""

    php_code = f"""<?php
// Bootstrap OpenCart for modification refresh
$_SERVER['SERVER_PORT'] = 80;
$_SERVER['SERVER_PROTOCOL'] = 'HTTP/1.1';
$_SERVER['REQUEST_METHOD'] = 'GET';
$_SERVER['REMOTE_ADDR'] = '127.0.0.1';
$_SERVER['HTTP_HOST'] = 'localhost';
$_SERVER['REQUEST_URI'] = '/admin/';

define('DIR_APPLICATION', '{config.oc_root}/admin/');
define('DIR_SYSTEM', '{config.oc_root}/system/');
define('DIR_DATABASE', DIR_SYSTEM . 'database/');
define('DIR_LANGUAGE', DIR_APPLICATION . 'language/');
define('DIR_TEMPLATE', DIR_APPLICATION . 'view/template/');
define('DIR_CONFIG', DIR_SYSTEM . 'config/');
define('DIR_IMAGE', '{config.oc_root}/image/');
define('DIR_STORAGE', '{config.storage_dir}/');
define('DIR_CATALOG', '{config.oc_root}/catalog/');
define('DIR_EXTENSION', '{config.oc_root}/extension/');
define('DIR_MODIFICATION', DIR_STORAGE . 'modification/');
define('DIR_LOGS', DIR_STORAGE . 'logs/');
define('DIR_CACHE', DIR_STORAGE . 'cache/');
define('DIR_UPLOAD', DIR_STORAGE . 'upload/');
define('DIR_DOWNLOAD', DIR_STORAGE . 'download/');
define('APPLICATION', 'Admin');

// Clear existing modification cache
$files = glob(DIR_MODIFICATION . '*');
foreach ($files as $file) {{
    if (is_file($file)) unlink($file);
    elseif (is_dir($file)) {{
        $it = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($file, RecursiveDirectoryIterator::SKIP_DOTS),
            RecursiveIteratorIterator::CHILD_FIRST
        );
        foreach ($it as $f) {{
            if ($f->isDir()) rmdir($f->getRealPath());
            else unlink($f->getRealPath());
        }}
        rmdir($file);
    }}
}}

echo "Modification cache cleared. ";

// Connect to DB for modification data
$db = new mysqli('localhost', '{config.db_user}', '{config.db_pass}', '{config.db_name}');
$db->set_charset('utf8');

$result = $db->query("SELECT * FROM oc_modification WHERE status = 1 ORDER BY sort_order, name");
$modifications = [];
while ($row = $result->fetch_assoc()) {{
    $modifications[] = $row;
}}

echo count($modifications) . " active modifications found. ";
echo "Run Admin > Extensions > Modifications > Refresh in browser for full recompile.";
$db->close();
"""
    out = db.run_php(php_code)
    return out.strip()


# ─── INFORMATION PAGES ────────────────────────────────────────


@mcp.tool()
def get_information_pages(search: str = "") -> str:
    """List CMS/information pages (About Us, FAQ, T&Cs, etc.) with title and content preview.
    Search by title text."""

    where = "WHERE id.language_id = 1"
    if search:
        safe = search.replace("'", "\\'")
        where += f" AND id.title LIKE '%{safe}%'"

    sql = f"""
        SELECT i.information_id, id.title, i.status, i.sort_order,
               SUBSTRING(id.description, 1, 300) AS description_preview,
               su.keyword AS seo_url
        FROM oc_information i
        JOIN oc_information_description id ON i.information_id = id.information_id AND id.language_id = 1
        LEFT JOIN oc_seo_url su ON su.query = CONCAT('information_id=', i.information_id) AND su.language_id = 1
        {where}
        ORDER BY i.sort_order, id.title
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_information_page(information_id: int) -> str:
    """Get full content of a single information/CMS page by ID."""

    sql = f"""
        SELECT i.information_id, id.title, id.description, id.meta_title,
               id.meta_description, id.meta_keyword, i.status, i.sort_order,
               su.keyword AS seo_url
        FROM oc_information i
        JOIN oc_information_description id ON i.information_id = id.information_id AND id.language_id = 1
        LEFT JOIN oc_seo_url su ON su.query = 'information_id={int(information_id)}' AND su.language_id = 1
        WHERE i.information_id = {int(information_id)}
    """
    rows = db.run_query(sql)
    if not rows:
        return json.dumps({"error": f"Information page {information_id} not found"})
    return json.dumps(rows[0], indent=2)


@mcp.tool()
def update_information(information_id: int, find: str, replace: str) -> str:
    """Update text within an information/CMS page using find/replace.
    Works on the HTML description field. Use get_information_page first to see current content."""

    rows = db.run_query(
        f"SELECT description FROM oc_information_description WHERE information_id = {int(information_id)} AND language_id = 1"
    )
    if not rows:
        return json.dumps({"error": f"Information page {information_id} not found"})

    current = rows[0]["description"]

    if find not in current:
        return json.dumps({"error": f"Text not found in information page {information_id}"})

    count = current.count(find)
    updated = current.replace(find, replace)

    safe_updated = updated.replace("\\", "\\\\").replace("'", "\\'")
    result = db.run_query(
        f"UPDATE oc_information_description SET description = '{safe_updated}' "
        f"WHERE information_id = {int(information_id)} AND language_id = 1"
    )
    return json.dumps({
        "updated": True, "information_id": information_id,
        "replacements": count, "result": result,
    }, indent=2)


# ─── UTILITY ─────────────────────────────────────────────────


@mcp.tool()
def get_seo_urls(query_pattern: str = "") -> str:
    """Get SEO URL mappings. Filter by query pattern (e.g. 'product_id=%')."""

    where = "WHERE store_id = 0 AND language_id = 1"
    if query_pattern:
        safe = query_pattern.replace("'", "\\'")
        where += f" AND query LIKE '{safe}'"

    sql = f"""
        SELECT seo_url_id, query, keyword
        FROM oc_seo_url
        {where}
        ORDER BY keyword
        LIMIT 200
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


@mcp.tool()
def get_stock_report() -> str:
    """Get stock levels for all active products, sorted by quantity (lowest first)."""

    sql = """
        SELECT p.product_id, pd.name, p.model, p.sku, p.quantity, p.price,
               ss.name AS stock_status
        FROM oc_product p
        JOIN oc_product_description pd ON p.product_id = pd.product_id AND pd.language_id = 1
        LEFT JOIN oc_stock_status ss ON p.stock_status_id = ss.stock_status_id AND ss.language_id = 1
        WHERE p.status = 1
        ORDER BY p.quantity ASC
    """
    rows = db.run_query(sql)
    return json.dumps(rows, indent=2)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
