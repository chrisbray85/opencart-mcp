"""Access policy ranks and best-effort secret guardrails."""

from __future__ import annotations

import os
import re
from pathlib import PurePosixPath

POLICY_RANK = {
    "safe": 0,
    "manager": 10,
    "developer": 20,
    "all": 30,
}

VALID_POLICIES = frozenset(POLICY_RANK)

# Tables that hold credentials / sessions (logical oc_ names; prefix rewritten at runtime).
_SENSITIVE_TABLES = frozenset(
    {
        "user",
        "api",
        "api_session",
        "session",
        "customer_login",
    }
)

_SECRET_KEY_RE = re.compile(
    r"(password|secret|api_?key|private_?key|smtp_password|client_secret)",
    re.IGNORECASE,
)

# Credential columns on otherwise-legitimate tables (oc_customer holds password/salt).
_SENSITIVE_COLUMN_RE = re.compile(r"(?i)\b(password|salt)\b")

# Tables whose star-selects leak those columns without naming them.
_CREDENTIAL_COLUMN_TABLES = frozenset({"customer"})

# SELECT * / SELECT c.* — but not COUNT(*), where '(' precedes the star.
_STAR_SELECT_RE = re.compile(r"(?i)(?:select|,)\s*(?:`?\w+`?\.)?\*")

_SECRET_BASENAMES = frozenset(
    {
        "config.php",
        ".env",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
    }
)


def parse_policy(value: str | None) -> str:
    """Normalize OPENCART_MCP_POLICY; default manager. Raise on unknown values."""
    raw = (
        value if value is not None else os.environ.get("OPENCART_MCP_POLICY", "manager")
    ) or "manager"
    policy = raw.strip().lower()
    if policy not in VALID_POLICIES:
        allowed = ", ".join(sorted(VALID_POLICIES, key=lambda p: POLICY_RANK[p]))
        raise ValueError(f"Invalid OPENCART_MCP_POLICY={raw!r}. Allowed: {allowed}")
    return policy


def secrets_guarded(policy: str) -> bool:
    """True when path/SQL/settings filters apply (everything except all)."""
    return policy != "all"


def deny_sensitive_path(path: str) -> str | None:
    """Return an error message if path looks like a secret file; else None."""
    normalized = path.replace("\\", "/").strip()
    lower = normalized.lower()
    name = PurePosixPath(lower).name

    if name in _SECRET_BASENAMES or name.startswith(".env."):
        return f"Path blocked by policy ({name})"
    # startswith also catches copies like config.php.bak / config.php~ / config.php.old
    if name.startswith("config.php"):
        return "Path blocked by policy (config.php)"
    if "/.ssh/" in f"/{lower}/" or lower.endswith("/.ssh") or "/.ssh/" in lower:
        return "Path blocked by policy (.ssh)"
    if name.endswith(".pem"):
        return f"Path blocked by policy ({name})"
    if "/storage/session/" in lower or lower.endswith("/storage/session"):
        return "Path blocked by policy (session storage)"
    return None


def deny_sensitive_sql(sql: str, table_prefix: str) -> str | None:
    """Return an error if SQL references credential/session tables; else None.

    Best-effort: strips quoted string literals, then matches prefixed table names
    (oc_user / {prefix}user). Bare words like ``user`` are ignored to limit
    false positives on column names.
    """
    scan = re.sub(r"'([^']|'')*'", "''", sql)
    scan = re.sub(r'"([^"]|"")*"', '""', scan)
    prefix = (table_prefix or "oc_").lower()
    blocked: list[str] = []
    for logical in _SENSITIVE_TABLES:
        names = {f"oc_{logical}", f"{prefix}{logical}"}
        for name in names:
            if re.search(rf"(?i)(?:`{re.escape(name)}`|\b{re.escape(name)}\b)", scan):
                blocked.append(f"oc_{logical}")
                break
    if blocked:
        return (
            "Query blocked by policy (sensitive table: "
            + ", ".join(sorted(set(blocked)))
            + ")"
        )
    # oc_customer isn't a blocked table (lookups are legitimate), but its
    # password/salt columns are credentials — catch them by column name,
    # and catch SELECT * / SELECT c.*, which returns them without naming them.
    col = _SENSITIVE_COLUMN_RE.search(scan)
    if col:
        return f"Query blocked by policy (credential column: {col.group(1).lower()})"
    for logical in _CREDENTIAL_COLUMN_TABLES:
        names = {f"oc_{logical}", f"{prefix}{logical}"}
        referenced = any(
            re.search(rf"(?i)(?:`{re.escape(n)}`|\b{re.escape(n)}\b)", scan) for n in names
        )
        if referenced and _STAR_SELECT_RE.search(scan):
            return (
                f"Query blocked by policy (SELECT * on oc_{logical} returns credential "
                "columns — list the columns you need instead)"
            )
    return None


def redact_setting_value(key: str, value):
    """Mask obviously secret setting values; leave others unchanged."""
    if _SECRET_KEY_RE.search(key or ""):
        return "***"
    return value


def redact_setting_rows(rows: list[dict]) -> list[dict]:
    """Return a shallow copy of setting rows with secret values redacted."""
    out = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        copy = dict(row)
        key = str(copy.get("key", copy.get("setting_name", "")))
        if "value" in copy:
            copy["value"] = redact_setting_value(key, copy.get("value"))
        if "setting_value" in copy:
            copy["setting_value"] = redact_setting_value(key, copy.get("setting_value"))
        out.append(copy)
    return out
