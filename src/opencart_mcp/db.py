"""SSH + PHP query executor for OpenCart MySQL."""

import json
import paramiko

from .config import Config

# Noise patterns from cPanel .bashrc to filter from stderr
_NOISE = ("tput:", "WARNING:", "post-quantum", "upgraded", "Unsuccessful stat")


class OpenCartDB:
    """Executes MySQL queries on VPS via SSH + PHP scripts."""

    def __init__(self, config: Config):
        self.config = config
        self._client: paramiko.SSHClient | None = None

    def _get_client(self) -> paramiko.SSHClient:
        """Get or create SSH connection."""
        if self._client is not None:
            transport = self._client.get_transport()
            if transport is not None and transport.is_active():
                return self._client
            # Connection dead, reconnect
            self._client = None

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.config.ssh_host,
            username=self.config.ssh_user,
            key_filename=self.config.ssh_key,
            timeout=15,
        )
        self._client = client
        return client

    def _exec(self, command: str, timeout: int = 30) -> tuple[str, str]:
        """Execute command via SSH, return (stdout, stderr)."""
        client = self._get_client()
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return out, err

    def _exec_php_stdin(self, php_code: str, timeout: int = 30) -> str:
        """Execute PHP code by piping to php via stdin. Returns raw stdout."""
        client = self._get_client()
        stdin, stdout, stderr = client.exec_command("php", timeout=timeout)
        stdin.write(php_code.encode("utf-8"))
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", errors="replace")
        return out

    def run_query(self, sql: str) -> list[dict] | dict:
        """Execute SQL query and return results as list of dicts."""
        escaped_sql = sql.replace("\\", "\\\\").replace("'", "\\'")

        php = f"""<?php
error_reporting(0);
$db = new mysqli('localhost', '{self.config.db_user}', '{self.config.db_pass}', '{self.config.db_name}');
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
        """Execute shell command on VPS, return output."""
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
        """Write content to a file on VPS via SFTP."""
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
