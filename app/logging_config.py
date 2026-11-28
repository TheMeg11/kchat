"""One-line, severity-aware deployment logs without credential-bearing URLs.

Railway can classify plain stderr output as errors even for Python INFO records.
Emit structured JSON to stdout instead; preserve the real level in each record,
including Uvicorn startup and error logs. This changes observability, not bot IO.
"""
from datetime import datetime, timezone
import json
import logging
import re


_BOT_URL = re.compile(r"(/bot)[^/\s\"'<>]+/", re.IGNORECASE)
_WEBHOOK_URL = re.compile(r"(/webhook/(?:telegram|bale)/)[^/\s\"'<>?]+", re.IGNORECASE)
_LEVELS = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "fatal",
}


def redact(text: str) -> str:
    text = _BOT_URL.sub(r"\1***:REDACTED/", text)
    return _WEBHOOK_URL.sub(r"\1[REDACTED]", text)


class RailwayJSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Format a copy as text, not by mutating the record: other handlers and
        # test capture must still see valid %-interpolation arguments.
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            message += "\n" + self.formatStack(record.stack_info)
        return json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "level": _LEVELS.get(record.levelno, record.levelname.lower()),
                "logger": record.name,
                "message": redact(message),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def log_config() -> dict:
    """A fresh dict because Uvicorn/dictConfig may modify their input."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"railway": {"()": "app.logging_config.RailwayJSONFormatter"}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "railway",
            }
        },
        "root": {"handlers": ["console"], "level": "INFO"},
        "loggers": {
            "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
            # Do not store every successful long-poll/HTTP call. Warnings/errors
            # still reach the same redacting formatter with their real severity.
            "httpx": {"handlers": [], "level": "WARNING", "propagate": True},
            "httpcore": {"handlers": [], "level": "WARNING", "propagate": True},
        },
    }
