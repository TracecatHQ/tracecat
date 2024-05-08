"""Loggers to override default FastAPI uvicorn logger behavior."""

from __future__ import annotations

import logging
from typing import override

import loguru
from loguru import logger as base_logger

from tracecat.logging.config import LOG_CONFIG


class InterceptHandler(logging.Handler):
    _log_level_mapping = {
        50: "CRITICAL",
        40: "ERROR",
        30: "WARNING",
        20: "INFO",
        10: "DEBUG",
        0: "NOTSET",
    }

    @override
    def emit(self, record: logging.LogRecord):
        try:
            level = logger.level(record.levelname).name
        except AttributeError:
            level = self._log_level_mapping[record.levelno]

        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info, lazy=True).log(
            level, record.getMessage()
        )


class Logger:
    _logger: loguru.Logger = None

    def __new__(cls, tag: str | None = None) -> loguru.Logger:
        # Initialize the logger
        if cls._logger is None:
            base_logger.remove()
            base_logger.level("INFO", color="<green>")
            base_logger.add(**LOG_CONFIG["stderr:log"])
            base_logger.add(**LOG_CONFIG["file:log"])
            base_logger.add(**LOG_CONFIG["file:json"])
            cls._logger = base_logger
            _override_uvicorn_loggers()

        # If a name is provided, bind it to the logger
        # We also configure the logger to write to a file
        if tag is not None:
            return cls._logger.bind(tag=tag)

        # Otherwise, return the base logger
        return cls._logger


def _override_uvicorn_loggers():
    logging.basicConfig(handlers=[InterceptHandler()])
    logging.getLogger("uvicorn.access").handlers = [InterceptHandler()]
    for tag in ["uvicorn", "uvicorn.error", "fastapi"]:
        _logger = logging.getLogger(tag)
        _logger.handlers = [InterceptHandler()]
        _logger.propagate = False  # Need this to prevent double logging


logger = Logger()
