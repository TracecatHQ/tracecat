"""Loggers to override default FastAPI uvicorn logger behavior."""

import os
import sys

from loguru import logger as base_logger

try:
    base_logger.remove(0)
except ValueError:
    pass

base_logger.add(
    sink=sys.stderr,
    colorize=True,
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="{time} | <level>{level: <8}</level> [{process}] <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level> | {extra}",
)

logger = base_logger
