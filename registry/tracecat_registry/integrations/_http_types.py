"""HTTP types."""

from typing import Any, Literal

RequestMethods = Literal["GET", "POST", "PUT", "DELETE"]
JSONObjectOrArray = dict[str, Any] | list[Any]
