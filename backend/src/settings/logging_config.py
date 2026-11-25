"""Route standard-library logging into loguru and choose where logs are written."""

import logging
import sys
from types import FrameType
from typing import Optional, cast

from loguru import logger

# Rotated log files to keep next to the active one; bounds disk use to ~60 MB.
LOG_FILE_RETENTION = 5
LOG_FILE_ROTATION = "10 MB"


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = str(record.levelno)

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:  # noqa: WPS609
            frame = cast(FrameType, frame.f_back)
            depth += 1
        mess = str(record)
        try:
            mess = record.getMessage()
        except Exception:  # noqa
            pass
        logger.opt(depth=depth, exception=record.exc_info).log(level, mess)


def configure_logging(stderr_level: str, log_file_path: Optional[str]) -> None:
    """Send all logs to stderr, and to a rotated file only when a path is configured.

    Containers run with a read-only root filesystem and are read through their stderr,
    so the file sink is opt-in (local development) instead of always on.

    Args:
        stderr_level: Lowest loguru level written to stderr, e.g. "WARNING".
        log_file_path: File to also write every record to; None or blank disables it.
    """
    logging.getLogger().handlers = [InterceptHandler()]
    logger.configure(handlers=[{"sink": sys.stderr, "level": stderr_level}])
    if log_file_path and log_file_path.strip():
        logger.add(log_file_path.strip(), rotation=LOG_FILE_ROTATION, retention=LOG_FILE_RETENTION)
