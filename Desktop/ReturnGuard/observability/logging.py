"""Structured logging (NFR-OBS-2).

JSON logs carrying request_id / order_id / node / outcome when provided via ``extra``.
Phase 10 layers tracing on top; this gives the service structured logs from the start.
"""

from __future__ import annotations

import json
import logging

from config.settings import settings

_STD = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STD and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    if settings.LOG_JSON:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.LOG_LEVEL)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
