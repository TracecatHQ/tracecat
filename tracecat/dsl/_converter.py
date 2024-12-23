from functools import partial
from typing import Any

import orjson
from fastapi.encoders import jsonable_encoder
from temporalio.api.common.v1 import Payload
from temporalio.converter import (
    CompositePayloadConverter,
    DataConverter,
    DefaultPayloadConverter,
    JSONPlainPayloadConverter,
)


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
                # We exclude unset values to avoid sending them to Temporal
                # as after serialization they will are treated as set values.
                default=partial(jsonable_encoder, exclude_unset=True),
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


pydantic_data_converter = DataConverter(
    payload_converter_class=PydanticPayloadConverter
)
"""Data converter using Pydantic JSON conversion."""


# test = {
#     "role": {
#         "type": "service",
#         "workspace_id": UUID("c0963ef7-8577-4da6-9860-6ea7b4db900b"),
#         "user_id": UUID("00000000-0000-4444-aaaa-000000000000"),
#         "service_id": "tracecat-runner",
#     },
#     "dsl": {
#         "title": "test_workflow_override_environment_correct",
#         "description": "Test that we can set the runtime environment for a workflow. The workflow should use the environment set in the DSL config.",
#         "entrypoint": {"ref": "a", "expects": {}},
#         "actions": [
#             {
#                 "ref": "a",
#                 "description": "",
#                 "action": "core.transform.reshape",
#                 "args": {"value": "${{ ENV.environment }}"},
#                 "depends_on": [],
#             }
#         ],
#         "config": {"environment": "__TEST_ENVIRONMENT__"},
#         "triggers": [],
#         "inputs": {},
#         "tests": [],
#         "returns": "${{ ACTIONS.a.result }}",
#     },
#     "wf_id": "wf-00000000000000000000000000000000",
# }
