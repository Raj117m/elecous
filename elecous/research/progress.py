"""Stall visibility (rewrite directive, Stage 2.5, Requirement 1).

Root-caused this session by direct diagnosis (``ps``/``lsof``, not
assumption): a ``ResearchEngine.tick()`` call appeared to hang for 5+
minutes. The process was alive, blocked on a real GDELT request --
``GdeltMediaVolumeConnector.collect(mode="live")`` iterates every theme and
every one of the ~288 real catalog topics, each preceded by a real 6-second
per-host politeness wait, making one "live" call a ~290-request, ~30-minute
sweep. ``ProgressReporter`` replaces the resulting silent gap with real,
periodic, numeric log lines -- it reports what already happened, it does
not estimate or invent progress.
"""

from __future__ import annotations

import time

from elecous.infrastructure.logging import get_logger

#: Log at most this often even if every item completes faster.
_LOG_INTERVAL_SECONDS = 10.0

#: A gap this long between ticks with zero progress is a real stall, not
#: normal cadence -- matches the rewrite directive's literal "60 seconds".
STALL_THRESHOLD_SECONDS = 60.0


class ProgressReporter:
    def __init__(self, total: int, label: str, subsystem: str = "research"):
        self.total = total
        self.label = label
        self.done = 0
        self.start = time.monotonic()
        self.last_log = self.start
        self.last_tick = self.start
        self.log = get_logger(subsystem)

    def tick(self, n: int = 1) -> None:
        self.done += n
        now = time.monotonic()
        self.last_tick = now
        if now - self.last_log >= _LOG_INTERVAL_SECONDS or self.done >= self.total:
            elapsed = now - self.start
            throughput = self.done / elapsed if elapsed > 0 else 0.0
            remaining = self.total - self.done
            eta_seconds = remaining / throughput if throughput > 0 else None
            self.log.info(
                "progress",
                label=self.label,
                done=self.done,
                total=self.total,
                throughput_per_sec=round(throughput, 3),
                eta_seconds=round(eta_seconds, 1) if eta_seconds is not None else None,
            )
            self.last_log = now

    def stalled_for(self) -> float:
        """Real elapsed seconds since the last recorded tick -- a caller in
        a loop can check this to detect and log a genuine stall rather than
        sitting silent."""
        return time.monotonic() - self.last_tick

    def check_stall(self) -> bool:
        stalled = self.stalled_for() >= STALL_THRESHOLD_SECONDS
        if stalled:
            self.log.warning(
                "stall detected", label=self.label, done=self.done, total=self.total,
                stalled_for_seconds=round(self.stalled_for(), 1),
            )
        return stalled
