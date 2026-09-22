"""Tests for policy parsing and the secret guardrails."""

import unittest
from unittest import mock

from opencart_mcp.policy import (
    deny_sensitive_path,
    deny_sensitive_sql,
    parse_policy,
    redact_setting_value,
    secrets_guarded,
)


class ParsePolicyTest(unittest.TestCase):
    def test_default_is_manager(self):
        # Safe by default: no run_sql/write_file, guardrails on.
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(parse_policy(None), "manager")
        self.assertEqual(parse_policy(""), "manager")

    def test_explicit_values_pass_through(self):
        for value in ("safe", "manager", "developer", "all"):
            self.assertEqual(parse_policy(value), value)

    def test_case_and_whitespace_normalized(self):
        self.assertEqual(parse_policy("  Developer "), "developer")

    def test_unknown_value_raises(self):
        with self.assertRaises(ValueError):
            parse_policy("banana")

    def test_guardrails_active_below_all(self):
        self.assertTrue(secrets_guarded("manager"))
        self.assertFalse(secrets_guarded("all"))


class PathGuardTest(unittest.TestCase):
    def test_blocks_secret_files(self):
        blocked = [
            "/var/www/html/config.php",
            "/var/www/html/admin/config.php",
            "CONFIG.PHP",
            "/var/www/html/config.php.bak",  # copies leak the DB password too
            "config.php~",
            "/home/user/.ssh/id_rsa",
            "/var/www/html/.env.production",
            "system/storage/session/sess_abc",
            "certs/server.pem",
        ]
        for path in blocked:
            self.assertIsNotNone(deny_sensitive_path(path), path)

    def test_allows_ordinary_files(self):
        allowed = [
            "/var/www/html/catalog/view/theme.css",
            "system/config/default.php",
            "admin/controller/common/dashboard.php",
        ]
        for path in allowed:
            self.assertIsNone(deny_sensitive_path(path), path)


class SqlGuardTest(unittest.TestCase):
    def test_blocks_credential_tables(self):
        cases = [
            ("SELECT password FROM oc_user", "oc_"),
            ("select token from OC_API_SESSION", "oc_"),
            ("SELECT * FROM shop_user", "shop_"),  # custom prefix
        ]
        for sql, prefix in cases:
            self.assertIsNotNone(deny_sensitive_sql(sql, prefix), sql)

    def test_blocks_credential_columns_on_customer(self):
        # oc_customer is queryable, but its password/salt columns are not.
        for sql in (
            "SELECT password FROM oc_customer",
            "SELECT email, salt FROM oc_customer WHERE customer_id = 1",
            "UPDATE oc_customer SET PASSWORD = 'x' WHERE customer_id = 1",
        ):
            self.assertIsNotNone(deny_sensitive_sql(sql, "oc_"), sql)

    def test_allows_ordinary_queries(self):
        for sql in (
            "SELECT * FROM oc_customer WHERE email = 'a@b.com'",
            "SELECT 'oc_user' AS label FROM oc_product",  # name only in a literal
            "SELECT * FROM oc_user_group",  # word-boundary near-miss
            "SELECT `key`, value FROM oc_setting WHERE `key` LIKE '%password%'",
        ):
            self.assertIsNone(deny_sensitive_sql(sql, "oc_"), sql)


class RedactionTest(unittest.TestCase):
    def test_secret_keys_masked(self):
        self.assertEqual(redact_setting_value("config_smtp_password", "hunter2"), "***")
        self.assertEqual(redact_setting_value("payment_stripe_secret_key", "sk_x"), "***")

    def test_plain_keys_untouched(self):
        self.assertEqual(redact_setting_value("config_name", "My Shop"), "My Shop")


if __name__ == "__main__":
    unittest.main()
