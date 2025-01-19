from typing import Any

import orjson
import temporalio.api.common.v1

from tracecat.logger import logger


def extract_payload(payload: temporalio.api.common.v1.Payloads, index: int = 0) -> Any:
    """Extract the first payload from a workflow history event."""
    raw_data = payload.payloads[index].data
    try:
        return orjson.loads(raw_data)
    except orjson.JSONDecodeError as e:
        logger.warning(
            "Failed to decode JSON data, attemping to decode as string",
            raw_data=raw_data,
            e=e,
        )

    try:
        return raw_data.decode()
    except UnicodeDecodeError:
        logger.warning("Failed to decode data as string, returning raw bytes")
        return raw_data


def extract_first(input_or_result: temporalio.api.common.v1.Payloads) -> Any:
    """Extract the first payload from a workflow history event."""
    return extract_payload(input_or_result, index=0)
