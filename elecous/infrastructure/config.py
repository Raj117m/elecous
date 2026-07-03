"""Configuration system (spec 10.20).

Nothing is hardcoded. Configuration files control sources, schedules,
thresholds, storage paths, database paths, logging, regions and categories.
Changing configuration never requires changing code.

Layout: ``elecous/configs/*.yaml``. ``elecous.yaml`` is the root config; other
files (sources.yaml, entities.yaml, thresholds.yaml, schedules.yaml) are
referenced by name. A local override file ``elecous.local.yaml`` merges on top
so machine-specific paths never enter version control.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # .../elecous
CONFIG_DIR = PACKAGE_ROOT / "configs"
DATA_DIR_ENV = "ELECOUS_DATA_DIR"


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class Config:
    """Immutable-by-convention view over the merged YAML configuration."""

    def __init__(self, root: dict[str, Any], config_dir: Path):
        self._root = root
        self.config_dir = config_dir

    @classmethod
    def load(cls, config_dir: Path | None = None) -> "Config":
        config_dir = config_dir or CONFIG_DIR
        root_file = config_dir / "elecous.yaml"
        root = yaml.safe_load(root_file.read_text()) or {}
        local = config_dir / "elecous.local.yaml"
        if local.exists():
            root = _deep_merge(root, yaml.safe_load(local.read_text()) or {})
        return cls(root, config_dir)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._root
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        value = self.get(dotted, default=None)
        if value is None:
            raise KeyError(f"missing required configuration key: {dotted}")
        return value

    def section(self, dotted: str) -> dict[str, Any]:
        value = self.get(dotted, default={})
        return dict(value) if isinstance(value, dict) else {}

    def load_file(self, name: str) -> Any:
        """Load a sibling configuration file (e.g. sources.yaml)."""
        path = self.config_dir / name
        return yaml.safe_load(path.read_text()) or {}

    # ------------------------------------------------------------------ paths
    @property
    def data_dir(self) -> Path:
        env = os.environ.get(DATA_DIR_ENV)
        path = Path(env) if env else PACKAGE_ROOT / self.get("storage.data_dir", "data")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def db_path(self, domain: str) -> Path:
        db_dir = self.data_dir / self.get("storage.databases_dir", "databases")
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / f"{domain}.duckdb"

    def tier_dir(self, tier: str) -> Path:
        path = self.data_dir / self.get(f"storage.tiers.{tier}", tier)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def log_dir(self) -> Path:
        path = self.data_dir / self.get("logging.dir", "logs")
        path.mkdir(parents=True, exist_ok=True)
        return path


_lock = threading.Lock()
_active: Config | None = None


def active_config() -> Config:
    """Process-wide configuration, loaded lazily."""
    global _active
    with _lock:
        if _active is None:
            _active = Config.load()
        return _active


def set_active_config(config: Config) -> None:
    """Used by tests and tools to point ELECOUS at an isolated data dir."""
    global _active
    with _lock:
        _active = config
