from typing import Any

import fsspec
import orjson

from tracecat.logger import logger
from tracecat.types.exceptions import TracecatDSLError


def resolve_string_or_uri(string_or_uri: str) -> Any:
    try:
        of = fsspec.open(string_or_uri, "rb")
        with of as f:
            data = f.read()

        return orjson.loads(data)

    except FileNotFoundError as e:
        logger.info(
            "Fsspec file not found",
            string_or_uri=string_or_uri,
            error=e,
        )
        raise TracecatDSLError(
            f"Failed to read fsspec file, file not found: {string_or_uri}"
        ) from e
    except ValueError as e:
        if "protocol not known" in str(e).lower():
            raise TracecatDSLError(
                f"Failed to read fsspec file, protocol not known: {string_or_uri}"
            ) from e
        logger.info(
            "String input did not match fsspec, handling as normal",
            string_or_uri=string_or_uri,
            error=e,
        )
        return string_or_uri
