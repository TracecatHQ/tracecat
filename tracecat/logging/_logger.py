"""Loggers to override default FastAPI uvicorn logger behavior."""

from loguru import logger as base_logger

from .config import LOG_CONFIG

base_logger.remove(0)
base_logger.add(**LOG_CONFIG["stderr:log"])

logger = base_logger
