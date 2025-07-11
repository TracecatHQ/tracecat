import orjson
from pydantic_core import from_json
from typing import Any
from pydantic import Json


def try_parse_json(x: Any) -> Json[Any] | str:
    try:
        x = orjson.loads(x)
    except orjson.JSONDecodeError:
        try:
            x = from_json(x, allow_partial=True)
        except ValueError:
            pass
    finally:
        return x
