import asyncio
from collections.abc import Coroutine
from typing import Any

import fsspec
import orjson

from tracecat.dsl.models import ActionTest
from tracecat.logging import logger
from tracecat.types.exceptions import TracecatDSLError


def resolve_string_or_uri(string_or_uri: str) -> Any:
    try:
        of = fsspec.open(string_or_uri, "rb")
        with of as f:
            data = f.read()

        return orjson.loads(data)

    except (FileNotFoundError, ValueError) as e:
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


async def resolve_success_output(action_test: ActionTest) -> Any:
    def resolver_coro(_obj: Any) -> Coroutine:
        return asyncio.to_thread(resolve_string_or_uri, _obj)

    obj = action_test.success
    match obj:
        case str():
            return await resolver_coro(obj)
        case list():
            tasks = []
            async with asyncio.TaskGroup() as tg:
                for item in obj:
                    task = tg.create_task(resolver_coro(item))
                    tasks.append(task)
            return [task.result() for task in tasks]
        case _:
            return obj
