"""Loggers to override default FastAPI uvicorn logger behavior."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import override

import loguru
from loguru import logger

from tracecat.config import LOG_CONFIG


class InterceptHandler(logging.Handler):
    log_level_mapping = {
        50: "CRITICAL",
        40: "ERROR",
        30: "WARNING",
        20: "INFO",
        10: "DEBUG",
        0: "NOTSET",
    }

    def __init__(self, name: str):
        super().__init__()
        self.name = name

    @override
    def emit(self, record: logging.LogRecord):
        try:
            level = logger.level(record.levelname).name
        except AttributeError:
            level = self.log_level_mapping[record.levelno]

        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        (
            logger.bind(tag=self.name)
            .opt(depth=depth, exception=record.exc_info, lazy=True)
            .log(level, record.getMessage())
        )


class LoggerFactory:
    @classmethod
    def make_logger(cls, *, name: str) -> loguru.Logger:
        config = LOG_CONFIG["logger"]
        logger = cls.customize_logging(name=name, **config)
        return logger

    @classmethod
    def customize_logging(
        cls,
        *,
        name: str,
        path: str,
        level: str,
        rotation: str,
        retention: str,
        format: str,
        **kwargs,
    ) -> loguru.Logger:
        global logger
        logger.remove()
        logger.add(
            sys.stderr,
            enqueue=True,
            backtrace=True,
            level=level.upper(),
            format=format,
            colorize=True,
        )
        filepath = Path(path) / f"{name}.log"
        logger.add(
            str(filepath),
            rotation=rotation,
            retention=retention,
            enqueue=True,
            backtrace=True,
            level=level.upper(),
            format=format,
        )
        logger.level("INFO", color="<green>")
        logging.basicConfig(handlers=[InterceptHandler(name=name)], level=0)
        logging.getLogger("uvicorn.access").handlers = [InterceptHandler(name=name)]
        for tag in ["uvicorn", "uvicorn.error", "fastapi"]:
            _logger = logging.getLogger(tag)
            _logger.handlers = [InterceptHandler(name=f"{name}:{tag}")]
            _logger.propagate = False  # Need this to prevent double logging

        return logger
