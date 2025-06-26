from typing import Any

import orjson
from pydantic import BaseModel
from pydantic_core import to_jsonable_python
from temporalio.api.common.v1 import Payload
from temporalio.converter import (
    CompositePayloadConverter,
    DataConverter,
    DefaultPayloadConverter,
    JSONPlainPayloadConverter,
)

from tracecat.dsl.compression import get_compression_payload_codec


def _serializer(obj: Any) -> Any:
    """Serializer for arbitrary objects.

    This is used to serialize arbitrary objects to JSON.
    """
    if isinstance(obj, BaseModel):
        # We exclude unset values to avoid sending them to Temporal
        # as after serialization they will are treated as set values.
        return obj.model_dump(exclude_unset=True)
    return to_jsonable_python(obj, fallback=str)


class PydanticORJSONPayloadConverter(JSONPlainPayloadConverter):
    """Pydantic ORJSON payload converter.

    This extends the :py:class:`JSONPlainPayloadConverter` to override
    :py:meth:`to_payload` using the Pydantic encoder.
    """

    def to_payload(self, value: Any) -> Payload | None:
        """Convert all values with Pydantic encoder or fail.

        Like the base class, we fail if we cannot convert. This payload
        converter is expected to be the last in the chain, so it can fail if
        unable to convert.
        """
        # We let JSON conversion errors be thrown to caller
        return Payload(
            metadata={"encoding": self.encoding.encode()},
            data=orjson.dumps(
                value,
                default=_serializer,
                option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS,
            ),
        )


class PydanticPayloadConverter(CompositePayloadConverter):
    """Payload converter that replaces Temporal JSON conversion with Pydantic
    JSON conversion.
    """

    def __init__(self) -> None:
        super().__init__(
            *(
                c
                if not isinstance(c, JSONPlainPayloadConverter)
                else PydanticORJSONPayloadConverter()
                for c in DefaultPayloadConverter.default_encoding_payload_converters
            )
        )


def get_data_converter(*, compression_enabled: bool = False) -> DataConverter:
    """Data converter using Pydantic JSON conversion with optional compression."""
    return DataConverter(
        payload_converter_class=PydanticPayloadConverter,
        payload_codec=get_compression_payload_codec() if compression_enabled else None,
    )
