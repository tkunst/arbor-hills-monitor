"""config_loader.py — load the non-secret config.yml. Secrets stay in env."""
from __future__ import annotations

import os
import yaml

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yml")


def load_config(path: str = DEFAULT_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_dotenv(path: str = ".env") -> None:
    """Best-effort: load a .env (default: in cwd) so a local run has creds without
    exporting. Never overrides an already-set variable. Used by the manual scripts."""
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))
