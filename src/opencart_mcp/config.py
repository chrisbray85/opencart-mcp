"""Configuration from environment variables."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Config:
    ssh_host: str
    ssh_user: str
    ssh_key: str  # path to SSH private key
    ssh_port: int  # SSH port; defaults to 22 if OPENCART_SSH_PORT is unset
    db_host: str  # MySQL host; if empty, DB_HOSTNAME from config.php is used
    db_port: int  # MySQL port for direct (pymysql) connections
    db_user: str
    db_pass: str
    db_name: str
    db_prefix: str  # table prefix; if empty, DB_PREFIX from config.php is used
    oc_root: str  # OpenCart root directory (container path for DDEV)
    storage_dir: str  # Storage directory (container path for DDEV)
    local_root: str  # Local project path (cwd for ddev commands)
    language_id: int  # 0 = detect from oc_setting/oc_language

    @property
    def is_ddev(self) -> bool:
        return self.ssh_host.lower() == "ddev"

    @property
    def is_direct_mysql(self) -> bool:
        """True when SSH/DDEV are not in use — queries go straight to MySQL."""
        return (not self.is_ddev) and (not self.ssh_host.strip())

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()  # the documented setup flow writes .env; nothing else read it
        ssh_host = os.environ.get("OPENCART_SSH_HOST", "")
        local_root = os.environ.get("OPENCART_ROOT", "")

        if ssh_host.lower() == "ddev":
            oc_root = "/var/www/html"
            storage_dir = f"{oc_root}/system/storage"
        else:
            oc_root = local_root
            storage_dir = os.environ.get("OPENCART_STORAGE", f"{oc_root}/system/storage")

        return cls(
            ssh_host=ssh_host,
            ssh_user=os.environ.get("OPENCART_SSH_USER", ""),
            ssh_key=os.path.expanduser(os.environ.get("OPENCART_SSH_KEY", "~/.ssh/id_ed25519")),
            ssh_port=int(os.environ.get("OPENCART_SSH_PORT", "22")),
            db_host=os.environ.get("OPENCART_DB_HOST", ""),
            db_port=int(os.environ.get("OPENCART_DB_PORT", "3306")),
            db_user=os.environ.get("OPENCART_DB_USER", ""),
            db_pass=os.environ.get("OPENCART_DB_PASS", ""),
            db_name=os.environ.get("OPENCART_DB_NAME", ""),
            db_prefix=os.environ.get("OPENCART_DB_PREFIX", ""),
            oc_root=oc_root,
            storage_dir=storage_dir,
            local_root=local_root,
            language_id=int(os.environ.get("OPENCART_LANGUAGE_ID", "0").strip() or "0"),
        )
