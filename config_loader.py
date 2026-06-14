"""config_loader.py — load the non-secret config.yml. Secrets stay in env."""
from __future__ import annotations

import os
import yaml

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yml")


def load_config(path: str = DEFAULT_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)
