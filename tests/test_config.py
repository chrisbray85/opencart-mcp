"""Config transport selection and MySQL cell normalisation."""

import unittest
from datetime import date, datetime
from decimal import Decimal

from opencart_mcp.config import Config
from opencart_mcp.db import OpenCartDB, mysql_cell


def _cfg(**overrides) -> Config:
    base = dict(
        ssh_host="",
        ssh_user="",
        ssh_key="~/.ssh/id_ed25519",
        ssh_port=22,
        db_host="127.0.0.1",
        db_port=3306,
        db_user="user",
        db_pass="pass",
        db_name="db",
        db_prefix="oc_",
        oc_root="/var/www",
        storage_dir="/var/www/storage",
        local_root="",
        language_id=0,
        policy="all",
    )
    base.update(overrides)
    return Config(**base)


class ConfigTransportTest(unittest.TestCase):
    def test_empty_ssh_is_direct_mysql(self):
        cfg = _cfg()
        self.assertTrue(cfg.is_direct_mysql)
        self.assertFalse(cfg.is_ddev)
        self.assertTrue(OpenCartDB(cfg)._use_mysql)

    def test_ssh_host_disables_direct_mysql(self):
        cfg = _cfg(ssh_host="192.0.2.10")
        self.assertFalse(cfg.is_direct_mysql)
        self.assertFalse(OpenCartDB(cfg)._use_mysql)

    def test_ddev_is_not_direct_mysql(self):
        cfg = _cfg(ssh_host="ddev")
        self.assertTrue(cfg.is_ddev)
        self.assertFalse(cfg.is_direct_mysql)

    def test_direct_mysql_requires_db_env(self):
        db = OpenCartDB(_cfg(db_host="", db_user="", db_name=""))
        with self.assertRaises(RuntimeError):
            db._get_config()

    def test_shell_tools_refuse_in_direct_mode(self):
        db = OpenCartDB(_cfg())
        with self.assertRaises(RuntimeError):
            db.run_command("ls")
        with self.assertRaises(RuntimeError):
            db.write_file("/tmp/x", "y")


class MysqlCellTest(unittest.TestCase):
    def test_decimal_datetime_bytes(self):
        self.assertEqual(mysql_cell(Decimal("12.50")), "12.50")
        self.assertEqual(mysql_cell(datetime(2026, 8, 27, 14, 0, 0)), "2026-08-27 14:00:00")
        self.assertEqual(mysql_cell(date(2026, 8, 27)), "2026-08-27")
        self.assertEqual(mysql_cell(b"ok"), "ok")
        self.assertIsNone(mysql_cell(None))


if __name__ == "__main__":
    unittest.main()
