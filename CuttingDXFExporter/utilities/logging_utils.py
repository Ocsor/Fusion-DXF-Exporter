"""Session logging configuration."""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

LOGGER_NAME = "CuttingDXFExporter"
LOG_FILENAME = "cutting_dxf_export.log"


def configure_session_logger(
    output_folder: str,
    addin_version: str,
    fusion_version: str = "unknown",
) -> logging.Logger:
    """Configure a UTF-8 session log in the selected output folder."""

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    close_handlers(logger)

    log_path = os.path.join(output_folder, LOG_FILENAME)
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.info("=" * 72)
    logger.info(
        "Analysis session started UTC=%s add-in=%s Fusion=%s",
        datetime.now(timezone.utc).isoformat(),
        addin_version,
        fusion_version,
    )
    return logger


def get_logger() -> logging.Logger:
    """Return the shared logger, including a null handler before configuration."""

    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def finish_session(logger: logging.Logger, outcome: str) -> None:
    """Write a finish record and flush the session log."""

    logger.info(
        "Analysis session finished UTC=%s outcome=%s",
        datetime.now(timezone.utc).isoformat(),
        outcome,
    )
    for handler in logger.handlers:
        handler.flush()


def close_handlers(logger: Optional[logging.Logger] = None) -> None:
    """Close and remove handlers so Fusion can release the log file."""

    target = logger or logging.getLogger(LOGGER_NAME)
    for handler in list(target.handlers):
        target.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
