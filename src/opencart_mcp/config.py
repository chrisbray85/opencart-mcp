"""Configuration from environment variables."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    ssh_host: str
    ssh_user: str
    ssh_key: str  # path to SSH private key
    db_user: str
    db_pass: str
    db_name: str
    oc_root: str  # OpenCart root directory (container path for DDEV)
    storage_dir: str  # Storage directory (container path for DDEV)
    local_root: str  # Local project path (cwd for ddev commands)

    @property
    def is_ddev(self) -> bool:
        return self.ssh_host.lower() == "ddev"

    @classmethod
    def from_env(cls) -> "Config":
        ssh_host = os.environ.get("OPENCART_SSH_HOST", "")
        local_root = os.environ.get("OPENCART_ROOT", "")

        if ssh_host.lower() == "ddev":
            oc_root = "/var/www/html"
            storage_dir = f"{oc_root}/system/storage"
        else:
            oc_root = local_root or os.environ.get("OPENCART_ROOT", "")
            storage_dir = os.environ.get("OPENCART_STORAGE", f"{oc_root}/system/storage")

        return cls(
            ssh_host=ssh_host,
            ssh_user=os.environ.get("OPENCART_SSH_USER", ""),
            ssh_key=os.environ.get("OPENCART_SSH_KEY", os.path.expanduser("~/.ssh/id_ed25519")),
            db_user=os.environ.get("OPENCART_DB_USER", ""),
            db_pass=os.environ.get("OPENCART_DB_PASS", ""),
            db_name=os.environ.get("OPENCART_DB_NAME", ""),
            oc_root=oc_root,
            storage_dir=storage_dir,
            local_root=local_root,
        )
