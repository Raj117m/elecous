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
from concurrent.futures import ThreadPoolExecutor
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
        # Performance directive: bounded concurrency, sized for the target
        # MacBook -- enough to overlap network-bound connectors with CPU
        # work without monopolizing the machine.
        self._pool = ThreadPoolExecutor(
            max_workers=int(self.config.get("scheduler.max_workers", 4)), thread_name_prefix="elecous-task"
        )
        self._running: set[str] = set()
        self._subsystem_locks: dict[str, threading.Lock] = {}

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
    def run_pending(self, now: datetime | None = None, wait: bool = True) -> int:
        """Execute all due tasks. Returns tasks dispatched.

        Performance directive: sequential execution meant one
        network-bound connector (a real GDELT sweep is ~300 requests x 6s
        legally-required politeness ~= 30 real minutes) starved every
        other subsystem for its whole duration. Due tasks are now
        dispatched to a bounded worker pool: subsystems proceed
        concurrently, while tasks *within* one subsystem stay serialized
        (per-subsystem lock) so heavy same-domain jobs never overlap
        themselves. Safe by construction: every Database call is already
        RLock-wrapped and HTTP politeness is per-host.

        wait=True (default) preserves the historical synchronous contract
        (tests, manual driving): the batch runs concurrently but the call
        returns only when it finishes. The background loop passes
        wait=False so a still-running long task never blocks the next
        tick from dispatching other, newly-due work."""
        now = now or timeutil.utcnow()
        if self._pause_check():
            self.log.warning("scheduler paused by resource manager")
            return 0
        with self._lock:
            due = sorted(
                (t for t in self._tasks.values() if t.due(now) and t.name not in self._running),
                key=lambda t: t.priority,
            )
            for task in due:
                self._running.add(task.name)
        futures = [self._pool.submit(self._execute_locked, task, now) for task in due]
        if wait:
            for future in futures:
                future.result()
        return len(due)

    def _execute_locked(self, task: ScheduledTask, now: datetime) -> None:
        subsystem_lock = self._subsystem_locks.setdefault(task.subsystem, threading.Lock())
        try:
            with subsystem_lock:
                self._execute(task, now)
        finally:
            with self._lock:
                self._running.discard(task.name)

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
                    self.run_pending(wait=False)
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
    def telemetry(self) -> dict[str, Any]:
        """Performance directive: live worker/queue state for the
        dashboard's mission-control view -- real counters, not estimates."""
        with self._lock:
            running = sorted(self._running)
            now = timeutil.utcnow()
            due_waiting = [t.name for t in self._tasks.values() if t.due(now) and t.name not in self._running]
        return {
            "max_workers": self._pool._max_workers,
            "running_tasks": running,
            "due_waiting": due_waiting,
            "registered": len(self._tasks),
        }

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
