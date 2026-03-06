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
        where += f" AND `group` = '{safe}'"
    if key:
        safe = key.replace("'", "\\'")
        where += f" AND `key` LIKE '{safe}'"

    sql = f"SELECT setting_id, `group`, `key`, value, serialized FROM oc_setting {where} ORDER BY `group`, `key`"
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
def get_modules(module_type: str = "") -> str:
    """List Journal3 modules. Filter by type (e.g. 'products', 'slider', 'product_tabs')."""

    where = ""
    if module_type:
        safe = module_type.replace("'", "\\'")
        where = f"WHERE module_type = '{safe}'"

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
        f"UPDATE oc_setting SET value = '{safe_value}' WHERE `group` = '{safe_group}' AND `key` = '{safe_key}' AND store_id = 0"
    )
    return json.dumps({"updated": True, "group": group, "key": key, "result": result}, indent=2)


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
