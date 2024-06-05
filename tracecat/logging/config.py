from __future__ import annotations

import sys
from typing import Literal

import loguru
import orjson

LogConfigType = Literal["stderr:log", "file:log", "file:json"]

LOG_FORMAT = (
    "<level>{level: <8}</level>"
    " [<cyan>{time:YYYY-MM-DD HH:mm:ss.SSS}</cyan>]"
    " [<green>{process}</green>][<magenta>{thread}</magenta>]"
    " <light-red>{name}</light-red>:<light-red>{function}</light-red><light-red>@{line}</light-red>"
    " - <level>{message}</level> | {extra}"
)


def serialize(record: loguru.Record):
    subset = {"timestamp": record["time"].timestamp(), "message": record["message"]}
    return orjson.dumps(subset).decode()


def formatter(record: loguru.Record):
    # Note this function returns the string to be formatted, not the actual message to be logged
    record["extra"]["serialized"] = serialize(record)
    return "{extra[serialized]}\n"


LOG_CONFIG = {
    "stderr:log": {
        "sink": sys.stderr,
        "level": "INFO",
        "colorize": True,
        "backtrace": True,
        # "enqueue": True,  # Causes pickling errors if enabled
        "format": "{time} | <level>{level: <8}</level> <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level> | {extra}",
    },
    "file:log": {
        "sink": "/var/lib/tracecat/logs/debug_log.log",
        "level": "DEBUG",
        "rotation": "20 days",
        "retention": "1 months",
        "backtrace": True,
        "enqueue": True,
        "format": "{time} | <level>{level: <8}</level> <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level> | {extra}",
    },
    "file:json": {
        "sink": "/var/lib/tracecat/logs/info_log.ndjson",
        "level": "INFO",
        "rotation": "20 days",
        "retention": "1 months",
        "serialize": True,
        "format": formatter,
        "enqueue": True,
    },
}
