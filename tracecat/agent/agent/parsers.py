from typing import Any

import orjson
from pydantic import Json
from pydantic_core import from_json


def try_parse_json(x: Any) -> Json[Any] | str:
    try:
        return orjson.loads(x)
    except orjson.JSONDecodeError:
        try:
            return from_json(x, allow_partial=True)
        except ValueError:
            return x
