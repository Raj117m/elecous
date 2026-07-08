"""Autonomous Backlog Manager (spec 10.15).

Priority queueing logic based on signal-traced operations.
Dynamically re-prioritizes and re-intervals tasks in the central scheduler
based on real-time active signals and environmental volatility.
"""
from __future__ import annotations

from datetime import timedelta

from elecous.environment.world_state import WorldStateBuilder
from elecous.infrastructure.logging import get_logger
from elecous.scheduler.scheduler import Scheduler


class BacklogManager:
    def __init__(self, scheduler: Scheduler, world: WorldStateBuilder):
        self.scheduler = scheduler
        self.world = world
        self.log = get_logger("backlog_manager")
        
        # Store original task constraints to allow relaxing them back to baseline
        self._baseline_intervals = {}
        self._baseline_priorities = {}

        # Register itself to run every 5 minutes to re-tune priorities
        self.scheduler.register(
            name="scheduler:backlog_tuning",
            subsystem="scheduler",
            action=self.retune_priorities,
            interval=timedelta(minutes=5),
            priority=1,
            run_immediately=True
        )

    def retune_priorities(self) -> None:
        """Scan active signals and elevate priority/frequency of related tasks."""
        state = self.world.latest_state()
        if not state:
            return
        active_signals = [s["signal_key"] for s in state.get("signals", []) if abs(s.get("z", 0)) > 2.0]
        
        with self.scheduler._lock:
            for task in self.scheduler._tasks.values():
                if task.name == "scheduler:backlog_tuning":
                    continue
                
                # Capture baseline if not recorded
                if task.name not in self._baseline_intervals:
                    self._baseline_intervals[task.name] = task.interval
                    self._baseline_priorities[task.name] = task.priority

                baseline_interval = self._baseline_intervals[task.name]
                baseline_priority = self._baseline_priorities[task.name]

                # If there are highly active signals, increase priority for mechanisms and prediction
                if active_signals and task.subsystem in ("mechanisms", "prediction", "observation"):
                    task.priority = max(1, baseline_priority - 2)
                    # temporarily shorten interval by 30% for agility during volatile periods
                    new_interval = timedelta(seconds=max(60, int(baseline_interval.total_seconds() * 0.7)))
                    if task.interval != new_interval:
                        task.interval = new_interval
                        self.log.info("Elevated task priority due to active signals", task=task.name, new_priority=task.priority)
                else:
                    # Relax back to baseline if environment is quiet or task isn't critical
                    if task.priority != baseline_priority or task.interval != baseline_interval:
                        task.priority = baseline_priority
                        task.interval = baseline_interval
                        self.log.info("Relaxed task priority to baseline", task=task.name)

