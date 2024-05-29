"""Loggers to override default FastAPI uvicorn logger behavior."""

from loguru import logger as base_logger

logger = base_logger
