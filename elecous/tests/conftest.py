"""Shared pytest fixtures.

Real Data Rule (spec 2.23, 11.19): tests never fabricate observations,
demand, or environmental data. Tests either exercise pure logic (hashing,
lifecycles, configuration...) or fetch real public data over the network.
Network tests are marked ``network`` and skip cleanly when offline.
"""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path

import pytest

from elecous.infrastructure.config import Config, set_active_config
from elecous.infrastructure.event_bus import reset_bus
from elecous.infrastructure.logging import reset_logging
from elecous.storage.database import reset_databases


def _online(host: str = "api.open-meteo.com", port: int = 443, timeout: float = 3.0) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


NETWORK_AVAILABLE = _online()


def pytest_collection_modifyitems(config, items):
    if NETWORK_AVAILABLE:
        return
    skip = pytest.mark.skip(reason="offline: network tests use real public data only (Real Data Rule)")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture()
def isolated_config(tmp_path: Path):
    """Config pointing at a throwaway data directory; databases, logs and
    tiers are isolated per test."""
    base = Config.load()
    base._root = dict(base._root)  # shallow copy is enough for overrides
    import os

    os.environ["ELECOUS_DATA_DIR"] = str(tmp_path / "data")
    set_active_config(base)
    reset_databases()
    reset_bus()
    reset_logging()
    yield base
    reset_databases()
    reset_bus()
    reset_logging()
    os.environ.pop("ELECOUS_DATA_DIR", None)
    set_active_config(Config.load())
