"""Query executor for OpenCart MySQL.

Backends:
- SSH + PHP stdin when OPENCART_SSH_HOST is a hostname
- DDEV when OPENCART_SSH_HOST=ddev
- Direct MySQL (pymysql) when OPENCART_SSH_HOST is empty
"""

import json
import re
import shlex
import subprocess
from datetime import date, datetime
from decimal import Decimal

import paramiko
import pymysql
from pymysql.cursors import DictCursor

from .config import Config

# Noise patterns from cPanel .bashrc to filter from stderr
_NOISE = ("tput:", "WARNING:", "post-quantum", "upgraded", "Unsuccessful stat")
# rewrite upstream-hardcoded "oc_" prefix to detected prefix at query time
_PREFIX_RE = re.compile(r"\boc_")
# capture every DB_* define from config.php, keyed without the "DB_" prefix
_PHP_DB_RE = re.compile(
    r"""define\s*\(\s*['"]DB_(\w+)['"]\s*,\s*['"]((?:\\.|[^'"\\])*)['"]\s*\)"""
)


_SHELL_REQUIRED = (
    "This operation needs SSH or DDEV. Direct MySQL mode only executes SQL."
)


def _php_str(s: str) -> str:
    """Escape a value for embedding in a single-quoted PHP string literal."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _clean_stderr(err: str) -> str:
    """Drop known cPanel .bashrc noise lines from stderr."""
    return "\n".join(
        line for line in err.splitlines() if not any(x in line for x in _NOISE)
    ).strip()


def mysql_cell(value):
    """Normalize pymysql cell values so json.dumps matches the PHP mysqli path,
    which returns everything as strings."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return value


class OpenCartDB:
    """Executes MySQL queries via SSH + PHP scripts, DDEV, or pymysql."""

    def __init__(self, config: Config):
        self.config = config
        self._client: paramiko.SSHClient | None = None
        self._use_ddev = config.is_ddev
        self._use_mysql = config.is_direct_mysql
        self._database: dict[str, str] | None = None

    # ── DB config detection ───────────────────────────────────────

    def _get_config(self) -> dict[str, str]:
        """Resolve the OpenCart DB_* settings, cached after first call.

        Returns a dict keyed by the define name without the "DB_" prefix,
        e.g. self._database['PREFIX'], ['HOSTNAME'], ['USERNAME'], ['PASSWORD'],
        ['DATABASE']. Values from self.config (env) take priority; any missing
        field is read from config.php.
        """
        if self._database is not None:
            return self._database

        # env vars take priority over config.php
        db_cfg: dict[str, str] = {}
        if self.config.db_host:
            db_cfg["HOSTNAME"] = self.config.db_host
        if self.config.db_user:
            db_cfg["USERNAME"] = self.config.db_user
        if self.config.db_pass or self._use_mysql:
            db_cfg["PASSWORD"] = self.config.db_pass
        if self.config.db_name:
            db_cfg["DATABASE"] = self.config.db_name
        if self.config.db_prefix:
            db_cfg["PREFIX"] = self.config.db_prefix

        if not db_cfg.get("HOSTNAME") and self.config.is_ddev:
            db_cfg["HOSTNAME"] = "db"

        if self._use_mysql:
            # No shell to read config.php from — everything must come from env
            db_cfg.setdefault("PREFIX", "oc_")
            env_names = {"HOSTNAME": "OPENCART_DB_HOST", "USERNAME": "OPENCART_DB_USER", "DATABASE": "OPENCART_DB_NAME"}
            missing = [k for k in env_names if k not in db_cfg]
            if missing:
                keys = ", ".join(env_names[k] for k in missing)
                raise RuntimeError(
                    f"Direct MySQL mode needs {keys} (and OPENCART_DB_PASS). "
                    "Set OPENCART_DB_* env vars, or set OPENCART_SSH_HOST to use SSH."
                )
            self._database = db_cfg
            return self._database

        required = ("HOSTNAME", "USERNAME", "PASSWORD", "DATABASE", "PREFIX")
        if not all(k in db_cfg for k in required):
            source = ""
            out = ""
            err: Exception | None = None
            try:
                if self._use_ddev and self.config.local_root:
                    source = f"{self.config.local_root}/config.php"
                    with open(source) as f:
                        out = f.read()
                else:
                    source = f"{self.config.oc_root}/config.php"
                    cmd_argv = ["cat", source]
                    out, _ = self._exec(" ".join(shlex.quote(a) for a in cmd_argv))
            except Exception as e:
                err = e
            # fill missing credentials from config.php DB_* defines,
            # unescaping \' and \\ from PHP single-quoted values
            php_config = (
                {k: re.sub(r"\\(['\\])", r"\1", v) for k, v in _PHP_DB_RE.findall(out)}
                if out
                else {}
            )
            for key in required:
                if key not in db_cfg and key in php_config:
                    db_cfg[key] = php_config[key]
            missing = [k for k in required if k not in db_cfg]
            if missing:
                hint = (
                    "set OPENCART_DB_* env vars, or ensure DDEV is running and cwd is the project root"
                    if self._use_ddev
                    else "set OPENCART_DB_* env vars, or verify OPENCART_ROOT/SSH access to config.php"
                )
                cause = f" (read error: {err})" if err else ""
                keys = ", ".join(f"DB_{k}" for k in missing)
                raise RuntimeError(
                    f"Could not detect {keys} from {source}{cause}. {hint}"
                )
        # cache only after full resolution — a partial cache would poison every
        # later call with bare KeyErrors after one transient read failure
        self._database = db_cfg
        return self._database

    def _retable(self, sql: str) -> str:
        """Rewrite hardcoded 'oc_' table names to the install's actual prefix."""
        prefix = self._get_config()["PREFIX"]
        if prefix == "oc_":
            return sql
        return _PREFIX_RE.sub(prefix, sql)

    def _require_shell(self) -> None:
        if self._use_mysql:
            raise RuntimeError(_SHELL_REQUIRED)

    # ── Direct MySQL backend ──────────────────────────────────────

    def _mysql_query(self, sql: str) -> list[dict] | dict:
        cfg = self._get_config()
        try:
            conn = pymysql.connect(
                host=cfg["HOSTNAME"],
                port=self.config.db_port,
                user=cfg["USERNAME"],
                password=cfg.get("PASSWORD", ""),
                database=cfg["DATABASE"],
                charset="utf8mb4",
                cursorclass=DictCursor,
                connect_timeout=15,
                read_timeout=30,
                write_timeout=30,
            )
        except pymysql.Error as e:
            raise RuntimeError(f"DB connect failed: {e}") from e
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                if cur.description is None:
                    conn.commit()
                    return {"affected_rows": cur.rowcount, "insert_id": conn.insert_id()}
                rows = cur.fetchall()
                return [{k: mysql_cell(v) for k, v in row.items()} for row in rows]
        except pymysql.Error as e:
            raise RuntimeError(f"Query failed: {e}") from e
        finally:
            conn.close()

    # ── DDEV backend ──────────────────────────────────────────────

    def _ddev_exec(self, command: str, timeout: int = 30) -> tuple[str, str]:
        """Execute command inside DDEV web container."""
        result = subprocess.run(
            ["ddev", "exec", "bash", "-c", command],
            capture_output=True, text=True, timeout=timeout,
            cwd=self.config.local_root or None,
        )
        return result.stdout, result.stderr

    def _ddev_exec_php_stdin(self, php_code: str, timeout: int = 30) -> str:
        """Execute PHP code by piping to php inside DDEV. Returns (stdout, stderr)."""
        result = subprocess.run(
            ["ddev", "exec", "php"],
            input=php_code, capture_output=True, text=True, timeout=timeout,
            cwd=self.config.local_root or None,
        )
        return result.stdout, result.stderr

    # ── SSH backend ───────────────────────────────────────────────

    def _get_client(self) -> paramiko.SSHClient:
        """Get or create SSH connection."""
        if self._client is not None:
            transport = self._client.get_transport()
            if transport is not None and transport.is_active():
                return self._client
            self._client = None

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.config.ssh_host,
            port=self.config.ssh_port,
            username=self.config.ssh_user,
            key_filename=self.config.ssh_key,
            timeout=15,
        )
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)
        self._client = client
        return client

    def _ssh_exec(self, command: str, timeout: int = 30) -> tuple[str, str]:
        """Execute command via SSH, return (stdout, stderr)."""
        for attempt in (0, 1):
            try:
                client = self._get_client()
                _, stdout, stderr = client.exec_command(command, timeout=timeout)
                break
            except (paramiko.SSHException, OSError):
                # stale connection (NAT drop etc.) — reconnect once
                self._client = None
                if attempt:
                    raise
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return out, err

    def _ssh_exec_php_stdin(self, php_code: str, timeout: int = 30) -> str:
        """Execute PHP code by piping to php via stdin. Returns (stdout, stderr)."""
        for attempt in (0, 1):
            try:
                client = self._get_client()
                stdin, stdout, stderr = client.exec_command("php", timeout=timeout)
                stdin.write(php_code.encode("utf-8"))
                stdin.channel.shutdown_write()
                break
            except (paramiko.SSHException, OSError):
                # stale connection (NAT drop etc.) — reconnect once
                self._client = None
                if attempt:
                    raise
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return out, err

    # ── Dispatch ──────────────────────────────────────────────────

    def _exec(self, command: str, timeout: int = 30) -> tuple[str, str]:
        self._require_shell()
        if self._use_ddev:
            return self._ddev_exec(command, timeout)
        return self._ssh_exec(command, timeout)

    def _exec_php_stdin(self, php_code: str, timeout: int = 30) -> tuple[str, str]:
        self._require_shell()
        if self._use_ddev:
            return self._ddev_exec_php_stdin(php_code, timeout)
        return self._ssh_exec_php_stdin(php_code, timeout)

    # ── Public API ────────────────────────────────────────────────

    def run_query(self, sql: str) -> list[dict] | dict:
        """Execute SQL query and return results as list of dicts."""
        sql = self._retable(sql)
        if self._use_mysql:
            return self._mysql_query(sql)

        escaped_sql = sql.replace("\\", "\\\\").replace("'", "\\'")
        cfg = self._get_config()

        php = f"""<?php
error_reporting(0);
mysqli_report(MYSQLI_REPORT_OFF);
$db = new mysqli('{_php_str(cfg["HOSTNAME"])}', '{_php_str(cfg["USERNAME"])}', '{_php_str(cfg["PASSWORD"])}', '{_php_str(cfg["DATABASE"])}');
if ($db->connect_error) {{
    echo json_encode(["error" => "DB connect failed: " . $db->connect_error]);
    exit;
}}
$db->set_charset('utf8mb4');
$r = $db->query('{escaped_sql}');
if ($r === false) {{
    echo json_encode(["error" => "Query failed: " . $db->error]);
    exit;
}}
if ($r === true) {{
    echo json_encode(["affected_rows" => $db->affected_rows, "insert_id" => $db->insert_id]);
    exit;
}}
$rows = [];
while ($row = $r->fetch_assoc()) {{
    $rows[] = $row;
}}
echo json_encode($rows);
$db->close();
"""
        out, err = self._exec_php_stdin(php)

        if not out.strip():
            detail = _clean_stderr(err) or "(no stderr)"
            raise RuntimeError(f"Empty PHP output — stderr: {detail[:400]}")

        try:
            result = json.loads(out.strip())
        except json.JSONDecodeError:
            detail = _clean_stderr(err)
            raise RuntimeError(
                f"Invalid JSON from PHP: {out.strip()[:300]}"
                + (f" — stderr: {detail[:200]}" if detail else "")
            )

        if isinstance(result, dict) and "error" in result:
            raise RuntimeError(result["error"])

        return result

    def run_php(self, php_code: str) -> str:
        """Execute arbitrary PHP on VPS via stdin pipe, return raw output."""
        out, err = self._exec_php_stdin(php_code)
        detail = _clean_stderr(err)
        if detail:
            return f"{out}\nSTDERR: {detail}"
        return out

    def run_command(self, command: str, timeout: int = 30) -> str:
        """Execute shell command, return output."""
        out, err = self._exec(command, timeout=timeout)
        if err.strip():
            err_lines = [
                line for line in err.splitlines()
                if not any(x in line for x in _NOISE)
            ]
            if err_lines:
                return f"{out}\nSTDERR: {chr(10).join(err_lines)}"
        return out

    def write_file(self, remote_path: str, content: str):
        """Write content to a file on VPS via SFTP, or via ddev exec."""
        self._require_shell()
        if self._use_ddev:
            import base64
            b64 = base64.b64encode(content.encode()).decode()
            php = f"""<?php echo file_put_contents('{_php_str(remote_path)}', base64_decode('{b64}')) === false ? 'FAIL' : 'ok';"""
            out, err = self._exec_php_stdin(php)
            if "ok" not in out:
                raise RuntimeError(
                    f"DDEV write failed: {(_clean_stderr(err) or out).strip()[:300]}"
                )
        else:
            client = self._get_client()
            sftp = client.open_sftp()
            with sftp.file(remote_path, "w") as f:
                f.write(content)
            sftp.close()

    def close(self):
        """Close SSH connection."""
        if self._client:
            self._client.close()
            self._client = None
