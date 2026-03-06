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
    oc_root: str  # OpenCart root directory on VPS
    storage_dir: str  # Storage directory on VPS

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            ssh_host=os.environ.get("OPENCART_SSH_HOST", ""),
            ssh_user=os.environ.get("OPENCART_SSH_USER", ""),
            ssh_key=os.environ.get("OPENCART_SSH_KEY", os.path.expanduser("~/.ssh/id_ed25519")),
            db_user=os.environ.get("OPENCART_DB_USER", ""),
            db_pass=os.environ.get("OPENCART_DB_PASS", ""),
            db_name=os.environ.get("OPENCART_DB_NAME", ""),
            oc_root=os.environ.get("OPENCART_ROOT", ""),
            storage_dir=os.environ.get("OPENCART_STORAGE", ""),
        )
