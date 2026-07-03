"""Resource Manager (spec 10.24, 12.17).

Continuously measures CPU, memory, SSD, database size, observation rate and
query time, and adjusts workload so the MacBook stays responsive. The
scheduler consults ``should_pause`` before every tick; heavy engines call
``guard`` before expensive work.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import psutil

from elecous.common import timeutil
from elecous.common.errors import ResourceExhaustion
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger


class ResourceManager:
    def __init__(self, config: Config | None = None):
        self.config = config or active_config()
        self.log = get_logger("infrastructure")
        self._process = psutil.Process()
        self._lock = threading.Lock()
        self._last_sample: dict[str, Any] = {}

    def sample(self) -> dict[str, Any]:
        with self._lock:
            memory = self._process.memory_info()
            disk = psutil.disk_usage(str(self.config.data_dir))
            sample = {
                "timestamp": timeutil.to_iso(timeutil.utcnow()),
                "process_cpu_percent": self._process.cpu_percent(interval=None),
                "system_cpu_percent": psutil.cpu_percent(interval=None),
                "process_memory_mb": round(memory.rss / (1024 * 1024), 1),
                "system_memory_percent": psutil.virtual_memory().percent,
                "disk_free_gb": round(disk.free / (1024**3), 2),
                "data_dir_mb": round(self._dir_size(self.config.data_dir) / (1024 * 1024), 1),
            }
            self._last_sample = sample
            return sample

    @staticmethod
    def _dir_size(path: Path) -> int:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    def should_pause(self) -> bool:
        """True when system pressure is high enough that scheduled work
        should wait for the next tick."""
        sample = self.sample()
        pause_cpu = float(self.config.get("resources.pause_cpu_percent", 85))
        return sample["system_cpu_percent"] >= pause_cpu or sample["system_memory_percent"] >= 92

    def guard(self, operation: str) -> None:
        """Raise ResourceExhaustion when limits are exceeded; callers pause
        their pipeline rather than degrading the machine (spec 10.25)."""
        sample = self.sample()
        max_mem = float(self.config.get("resources.max_memory_mb", 2048))
        if sample["process_memory_mb"] > max_mem:
            raise ResourceExhaustion(
                f"memory limit exceeded during {operation}",
                context={"memory_mb": sample["process_memory_mb"], "limit_mb": max_mem},
            )
        if sample["disk_free_gb"] < 2:
            raise ResourceExhaustion(f"disk nearly full during {operation}", context=sample)

    def last_sample(self) -> dict[str, Any]:
        return dict(self._last_sample) if self._last_sample else self.sample()
