from __future__ import annotations

import logging
from pathlib import Path

_CAPTURE_ERROR_LOGGERS: dict[str, str] = {}


def capture_sibling_log(source_path: str, suffix: str) -> str:
    """e.g. plan-tflog.log + parse-sdk-errors -> plan-tflog-parse-sdk-errors.log"""
    source = Path(source_path)
    return str(source.with_name(f"{source.stem}-{suffix}.log"))


def configure_capture_error_log(source_path: str, suffix: str) -> logging.Logger:
    """Write parser diagnostics next to the capture file (same directory as TERRAFORM_LOG_PATH)."""
    if not source_path:
        raise ValueError(
            "Set TERRAFORM_LOG_PATH to your capture file before running analysis."
        )

    logger_name = f"commonlib.capture.{suffix}"
    log_path = capture_sibling_log(source_path, suffix)
    logger = logging.getLogger(logger_name)

    if _CAPTURE_ERROR_LOGGERS.get(logger_name) == log_path and logger.handlers:
        return logger

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.ERROR)
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    _CAPTURE_ERROR_LOGGERS[logger_name] = log_path
    return logger


class Config:
    """Notebook path configuration from environment variables.

    TERRAFORM_LOG_PATH is the raw capture file (required).
    Normalized JSON caches and parser error logs are derived automatically next
    to the capture file (see normalized_cache and configure_capture_error_log).
    Set DISABLE_NORMALIZED_CACHE=1 to skip reading and writing cache files.
    Set FORCE_RENORMALIZE=1 to ignore an existing cache file and rebuild it.
    """

    def __init__(self):
        import os

        self.TERRAFORM_LOG_PATH = os.getenv("TERRAFORM_LOG_PATH", "")
        self.DISABLE_NORMALIZED_CACHE = os.getenv("DISABLE_NORMALIZED_CACHE", "")
        self.FORCE_RENORMALIZE = os.getenv("FORCE_RENORMALIZE", "")
