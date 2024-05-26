"""Set up shared environment variables and S3 proxy server (MinIO) for integration tests."""

import sys

from loguru import logger

logger.add(
    sink=sys.stderr,
    level="INFO",
    format="{time} | <level>{level: <8}</level> <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level> | {extra}",
)
