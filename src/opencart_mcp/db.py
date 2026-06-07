"""SSH + PHP query executor for OpenCart MySQL, with DDEV support."""

import json
import re
import shlex
import subprocess

import paramiko

from .config import Config

# Noise patterns from cPanel .bashrc to filter from stderr
_NOISE = ("tput:", "WARNING:", "post-quantum", "upgraded", "Unsuccessful stat")
# rewrite upstream-hardcoded "oc_" prefix to detected prefix at query time
_PREFIX_RE = re.compile(r"\boc_")
# capture every DB_* define from config.php, keyed without the "DB_" prefix
_PHP_DB_RE = re.compile(
    r"""define\s*\(\s*['"]DB_(\w+)['"]\s*,\s*['"]([^'"]*)['"]\s*\)"""
)


class OpenCartDB:
    """Executes MySQL queries on VPS via SSH + PHP scripts, or via DDEV."""

    def __init__(self, config: Config):
        self.config = config
        self._client: paramiko.SSHClient | None = None
        self._use_ddev = config.is_ddev
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
        self._database = {}
        if self.config.db_host:
            self._database["HOSTNAME"] = self.config.db_host
        if self.config.db_user:
            self._database["USERNAME"] = self.config.db_user
        if self.config.db_pass:
            self._database["PASSWORD"] = self.config.db_pass
        if self.config.db_name:
            self._database["DATABASE"] = self.config.db_name
        if self.config.db_prefix:
            self._database["PREFIX"] = self.config.db_prefix
            
        if not self._database.get("HOSTNAME") and self.config.is_ddev:
            self._database["HOSTNAME"] = "db"

        required = ("HOSTNAME", "USERNAME", "PASSWORD", "DATABASE", "PREFIX")
        if all(k in self._database for k in required):
            return self._database

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
        # fill any missing database credentials from config.php DB_* defines
        php_config = {k: v for k, v in _PHP_DB_RE.findall(out)} if out else {}
        for key in required:
            if key not in self._database and key in php_config:
                self._database[key] = php_config[key]
        missing = [k for k in required if k not in self._database]
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
        return self._database

    def _retable(self, sql: str) -> str:
        """Rewrite hardcoded 'oc_' table names to the install's actual prefix."""
        prefix = self._get_config()["PREFIX"]
        if prefix == "oc_":
            return sql
        return _PREFIX_RE.sub(prefix, sql)

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
        """Execute PHP code by piping to php inside DDEV."""
        result = subprocess.run(
            ["ddev", "exec", "php"],
            input=php_code, capture_output=True, text=True, timeout=timeout,
            cwd=self.config.local_root or None,
        )
        return result.stdout

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
        self._client = client
        return client

    def _ssh_exec(self, command: str, timeout: int = 30) -> tuple[str, str]:
        """Execute command via SSH, return (stdout, stderr)."""
        client = self._get_client()
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return out, err

    def _ssh_exec_php_stdin(self, php_code: str, timeout: int = 30) -> str:
        """Execute PHP code by piping to php via stdin. Returns raw stdout."""
        client = self._get_client()
        stdin, stdout, stderr = client.exec_command("php", timeout=timeout)
        stdin.write(php_code.encode("utf-8"))
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", errors="replace")
        return out

    # ── Dispatch ──────────────────────────────────────────────────

    def _exec(self, command: str, timeout: int = 30) -> tuple[str, str]:
        if self._use_ddev:
            return self._ddev_exec(command, timeout)
        return self._ssh_exec(command, timeout)

    def _exec_php_stdin(self, php_code: str, timeout: int = 30) -> str:
        if self._use_ddev:
            return self._ddev_exec_php_stdin(php_code, timeout)
        return self._ssh_exec_php_stdin(php_code, timeout)

    # ── Public API ────────────────────────────────────────────────

    def run_query(self, sql: str) -> list[dict] | dict:
        """Execute SQL query and return results as list of dicts."""
        sql = self._retable(sql)
        escaped_sql = sql.replace("\\", "\\\\").replace("'", "\\'")
        cfg = self._get_config()

        php = f"""<?php
error_reporting(0);
$db = new mysqli('{cfg["HOSTNAME"]}', '{cfg["USERNAME"]}', '{cfg["PASSWORD"]}', '{cfg["DATABASE"]}');
if ($db->connect_error) {{
    echo json_encode(["error" => "DB connect failed: " . $db->connect_error]);
    exit;
}}
$db->set_charset('utf8');
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
        out = self._exec_php_stdin(php)

        if not out.strip():
            return {"error": "Empty PHP output — query may have failed silently"}

        try:
            result = json.loads(out.strip())
        except json.JSONDecodeError:
            return {"error": f"Invalid JSON: {out.strip()[:300]}"}

        if isinstance(result, dict) and "error" in result:
            raise RuntimeError(result["error"])

        return result

    def run_php(self, php_code: str) -> str:
        """Execute arbitrary PHP on VPS via stdin pipe, return raw output."""
        return self._exec_php_stdin(php_code)

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
        if self._use_ddev:
            import base64
            b64 = base64.b64encode(content.encode()).decode()
            php = f"""<?php file_put_contents('{remote_path}', base64_decode('{b64}')); echo 'ok';"""
            self._exec_php_stdin(php)
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
