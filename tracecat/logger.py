import logging
import os
from pathlib import Path

import colorlog

LOG_FORMAT = (
    "%(log_color)s%(levelname)-8s%(reset)s"
    " [%(cyan)s%(asctime)s%(reset)s]"
    " [%(green)s%(process)d%(reset)s][%(purple)s%(thread)d%(reset)s]"
    " %(light_red)s%(module)s::%(funcName)s(%(lineno)d)%(reset)s"
    "\t%(light_purple)s%(name)s%(reset)s |"
    " %(log_color)s%(message)s%(reset)s"
)


def file_logger(
    name: str,
    file_path: str | Path,
    level: int | str | None = None,
) -> logging.Logger:
    """Sets up a logger that logs messages to a file."""
    logger = logging.getLogger(name)

    # Set logger level
    if not level:
        level = os.getenv("LOG_LEVEL", "INFO")
    logger.setLevel(level)

    # Create file handler
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.touch(exist_ok=True)

    file_handler = logging.FileHandler(file_path)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

    # Add the handler to the logger
    logger.addHandler(file_handler)

    return logger


def standard_logger(
    name: str,
    level: int | str | None = None,
) -> logging.Logger:
    """Sets up a logger that logs messages to the console."""
    logger = colorlog.getLogger(name)

    # Set logger level
    if not level:
        level = os.getenv("LOG_LEVEL", "INFO")
    logger.setLevel(level)

    # Create stream handler
    stream_handler = colorlog.StreamHandler()
    color_formatter = colorlog.ColoredFormatter(LOG_FORMAT)
    stream_handler.setFormatter(color_formatter)

    # Add the handler to the logger
    logger.addHandler(stream_handler)

    return logger
