"""Central scheduler (spec 10.13).

Nothing runs continuously without reason. Every subsystem registers tasks
with an interval and a priority; the scheduler executes due tasks on a small
worker pool, respects the Resource Manager's pause signal, and adapts
intervals when tasks ask for it (dynamic scheduling, spec 2.7).

Design: a cooperative tick loop rather than cron threads — cheap on a
MacBook, deterministic in tests (``run_pending`` can be driven manually),
and trivially replaceable by a distributed queue later (spec 10.26).
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from elecous.common import timeutil
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger


@dataclass
class ScheduledTask:
    name: str
    subsystem: str
    action: Callable[[], Any]
    interval: timedelta
    priority: int = 5  # 1 = highest
    enabled: bool = True
    next_run: datetime = field(default_factory=timeutil.utcnow)
    last_run: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    total_runs: int = 0
    total_failures: int = 0

    def due(self, now: datetime) -> bool:
        return self.enabled and now >= self.next_run

    def reschedule(self, now: datetime) -> None:
        self.next_run = now + self.interval


class Scheduler:
    def __init__(self, config: Config | None = None, pause_check: Callable[[], bool] | None = None):
        self.config = config or active_config()
        self.log = get_logger("scheduler")
        self._tasks: dict[str, ScheduledTask] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # pause_check returns True when the Resource Manager wants us to back off
        self._pause_check = pause_check or (lambda: False)

    # ------------------------------------------------------------ registration
    def register(
        self,
        name: str,
        subsystem: str,
        action: Callable[[], Any],
        interval: timedelta,
        priority: int = 5,
        run_immediately: bool = False,
    ) -> ScheduledTask:
        task = ScheduledTask(name=name, subsystem=subsystem, action=action, interval=interval, priority=priority)
        if not run_immediately:
            task.next_run = timeutil.utcnow() + interval
        with self._lock:
            self._tasks[name] = task
        self.log.info("task registered", task=name, subsystem=subsystem, interval_s=interval.total_seconds())
        return task

    def set_interval(self, name: str, interval: timedelta) -> None:
        """Dynamic scheduling: observation intelligence re-tunes cadence
        (spec 2.19) through this call, never by editing code."""
        with self._lock:
            task = self._tasks[name]
            task.interval = interval
            task.next_run = min(task.next_run, timeutil.utcnow() + interval)

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            self._tasks[name].enabled = enabled

    # ---------------------------------------------------------------- running
    def run_pending(self, now: datetime | None = None) -> int:
        """Execute all due tasks in priority order. Returns tasks executed."""
        now = now or timeutil.utcnow()
        if self._pause_check():
            self.log.warning("scheduler paused by resource manager")
            return 0
        with self._lock:
            due = sorted((t for t in self._tasks.values() if t.due(now)), key=lambda t: t.priority)
        executed = 0
        for task in due:
            self._execute(task, now)
            executed += 1
        return executed

    def _execute(self, task: ScheduledTask, now: datetime) -> None:
        task.last_run = now
        task.total_runs += 1
        try:
            task.action()
            task.last_success = timeutil.utcnow()
            task.last_error = None
            task.consecutive_failures = 0
        except Exception:
            task.total_failures += 1
            task.consecutive_failures += 1
            task.last_error = traceback.format_exc(limit=8)
            self.log.error(
                "task failed",
                task=task.name,
                subsystem=task.subsystem,
                consecutive_failures=task.consecutive_failures,
            )
            # Exponential backoff on repeated failure (spec 10.22 retry ladder);
            # self-healing (Phase 10) watches consecutive_failures.
            backoff = min(2 ** task.consecutive_failures, 64)
            task.next_run = now + task.interval * backoff
            return
        task.reschedule(now)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        tick = float(self.config.get("scheduler.tick_seconds", 5))

        def loop() -> None:
            while not self._stop.wait(tick):
                try:
                    self.run_pending()
                except Exception:
                    self.log.exception("scheduler tick failed")

        self._thread = threading.Thread(target=loop, name="elecous-scheduler", daemon=True)
        self._thread.start()
        self.log.info("scheduler started", tick_seconds=tick)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.log.info("scheduler stopped")

    # ------------------------------------------------------------- reporting
    def report(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "name": t.name,
                    "subsystem": t.subsystem,
                    "interval_seconds": t.interval.total_seconds(),
                    "priority": t.priority,
                    "enabled": t.enabled,
                    "next_run": timeutil.to_iso(t.next_run),
                    "last_success": timeutil.to_iso(t.last_success) if t.last_success else None,
                    "consecutive_failures": t.consecutive_failures,
                    "total_runs": t.total_runs,
                    "total_failures": t.total_failures,
                    "last_error": t.last_error,
                }
                for t in self._tasks.values()
            ]
