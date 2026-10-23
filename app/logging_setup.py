"""JSON logging setup."""

import json
import re
import logging
import sys
import time
from typing import Any, Dict, Optional

from app.config import get_settings


_SECRET_FRAGMENT = re.compile(r"""(access_token|refresh_token|client_secret|code_verifier)=[^&\s"']+""")


def redact(text: str) -> str:
    """Blank `access_token=...` style fragments (platform tokens ride in query strings)."""
    return _SECRET_FRAGMENT.sub(r"\g<1>=[redacted]", text)


class JsonFormatter(logging.Formatter):
    """JSON log formatter with standard fields."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }

        # Add extra fields from record.__dict__
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "message", "pathname", "process", "processName", "relativeCreated",
                "thread", "threadName", "exc_info", "exc_text", "stack_info"
            }:
                # Skip internal attributes
                if not key.startswith("_"):
                    log_data[key] = value

        if record.exc_info:
            log_data["exception"] = redact(self.formatException(record.exc_info))

        return json.dumps(log_data, ensure_ascii=False)


def configure_logging() -> None:
    """Configure application logging with JSON formatter."""
    settings = get_settings()

    # Clear any existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    # Set level
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    root_logger.setLevel(level)

    # Create handler with JSON formatter
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root_logger.addHandler(handler)

    # Reduce noise from third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    # httpx logs full request URLs at INFO; platform tokens can sit in query strings
    for name in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)