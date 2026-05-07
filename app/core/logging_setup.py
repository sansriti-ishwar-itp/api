"""Structured JSON logging configuration.

Configured once at app startup. Every log record gets `request_id` (when a
request middleware set one) and a stable timestamp/level/logger field set so
downstream tooling (Loki/CloudWatch/Datadog) can index without scraping.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

try:  # python-json-logger >= 3.x prefers `pythonjsonlogger.json`
    from pythonjsonlogger.json import JsonFormatter as _JsonFormatter
except ImportError:  # pragma: no cover - older versions
    from pythonjsonlogger.jsonlogger import JsonFormatter as _JsonFormatter

_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(value: str | None) -> None:
    _request_id_ctx.set(value)


def get_request_id() -> str | None:
    return _request_id_ctx.get()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id") or record.request_id is None:
            record.request_id = _request_id_ctx.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    fmt: dict[str, Any] = {
        "%(asctime)s": "asctime",
        "%(levelname)s": "levelname",
        "%(name)s": "logger",
        "%(message)s": "message",
        "%(request_id)s": "request_id",
    }
    formatter = _JsonFormatter(
        fmt=" ".join(fmt.keys()),
        rename_fields={"asctime": "ts", "levelname": "level"},
        json_default=str,
    )
    handler.setFormatter(formatter)
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Quiet very chatty libraries by default.
    for noisy in ("uvicorn.access", "openstack", "keystoneauth", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
