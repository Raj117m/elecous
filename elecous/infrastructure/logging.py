"""Structured per-subsystem logging (spec 10.21, 11.15).

Every subsystem writes an independent JSON-lines log file so problems are
isolated immediately. Logs are observable data: the dashboard's health panels
read them. No print statements anywhere in ELECOUS.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from elecous.common import timeutil
from elecous.infrastructure.config import active_config

_lock = threading.Lock()
_configured: set[str] = set()


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": timeutil.to_iso(timeutil.utcnow()),
            "level": record.levelname,
            "subsystem": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "context", None)
        if extra:
            payload["context"] = extra
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class SubsystemLogger:
    """Thin wrapper adding structured context to stdlib logging."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level: int, message: str, **context: Any) -> None:
        self._logger.log(level, message, extra={"context": context or None})

    def debug(self, message: str, **context: Any) -> None:
        self._log(logging.DEBUG, message, **context)

    def info(self, message: str, **context: Any) -> None:
        self._log(logging.INFO, message, **context)

    def warning(self, message: str, **context: Any) -> None:
        self._log(logging.WARNING, message, **context)

    def error(self, message: str, **context: Any) -> None:
        self._log(logging.ERROR, message, **context)

    def exception(self, message: str, **context: Any) -> None:
        self._logger.error(message, exc_info=True, extra={"context": context or None})


def get_logger(subsystem: str) -> SubsystemLogger:
    config = active_config()
    logger = logging.getLogger(subsystem)
    with _lock:
        if subsystem not in _configured:
            logger.setLevel(config.get("logging.level", "INFO"))
            handler = logging.FileHandler(config.log_dir / f"{subsystem}.log", encoding="utf-8")
            handler.setFormatter(JsonLineFormatter())
            logger.addHandler(handler)
            logger.propagate = False
            _configured.add(subsystem)
    return SubsystemLogger(logger)


def reset_logging() -> None:
    """Detach handlers (tests switch data directories)."""
    with _lock:
        for name in list(_configured):
            logger = logging.getLogger(name)
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            _configured.discard(name)
