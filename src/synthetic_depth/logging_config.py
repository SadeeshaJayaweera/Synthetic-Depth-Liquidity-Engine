"""Structured and standardized logging configuration."""

import logging
import sys
from typing import Optional


def setup_logging(level: Optional[str] = None) -> logging.Logger:
    """Configure root and package loggers with a clean console formatter.

    Args:
        level: Optional log level string (DEBUG, INFO, WARNING, ERROR). Defaults to INFO.

    Returns:
        Configured logger instance.
    """
    log_level = (level or "INFO").upper()
    numeric_level = getattr(logging, log_level, logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(numeric_level)

    root_logger = logging.getLogger()
    # Avoid duplicate handlers on re-configuration
    if not root_logger.handlers:
        root_logger.addHandler(handler)
    else:
        root_logger.handlers[0] = handler
    root_logger.setLevel(numeric_level)

    logger = logging.getLogger("synthetic_depth")
    logger.setLevel(numeric_level)

    return logger
