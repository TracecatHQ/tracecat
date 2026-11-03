"""Temporal wire tests for the channel delivery contract."""

import json
import uuid
from typing import Any

import pytest
from pydantic import BaseModel

from tracecat.agent.delivery import (
    ApprovalCard,
    ChannelOutput,
    DeliverChannelOutput,
    MirroredMessage,
    Reply,
    Status,
)
from tracecat.agent.session.inputs import Actor
from tracecat.dsl._converter import PydanticPayloadConverter

SESSION_ID = uuid.UUID("00000000-0000-4000-8000-00000000005e")
WORKSPACE_ID = uuid.UUID("00000000-0000-4000-8000-0000000000c0")
RUN_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
BATCH_ID = uuid.UUID("00000000-0000-4000-8000-0000000000ba")
ACTOR = Actor(
    user_id=uuid.UUID("00000000-0000-4000-8000-0000000000d0"), channel_user_id=None
)

OUTPUTS: list[BaseModel] = [
    Reply(run_id=RUN_ID, segment=2, text="done"),
    Status(run_id=RUN_ID, text="thinking"),
    ApprovalCard(
        run_id=RUN_ID,
        batch_id=BATCH_ID,
        tool_call_id="toolu_0EXAMPLE",
        tool="core.http_request",
        state="approved",
        decided_by=ACTOR,
    ),
    MirroredMessage(
        message_id="m-1", author=ACTOR, author_name="Example User", text="hi"
    ),
]


@pytest.mark.parametrize("output", OUTPUTS, ids=[type(o).__name__ for o in OUTPUTS])
def test_output_round_trip_decodes_with_alias_hint(output: BaseModel) -> None:
    converter = PydanticPayloadConverter()
    hint: Any = ChannelOutput
    decoded = converter.from_payloads(converter.to_payloads([output]), [hint])
    assert decoded == [output]
    assert type(decoded[0]) is type(output)


def test_deliver_request_round_trip_omits_unset_defaults() -> None:
    converter = PydanticPayloadConverter()
    request = DeliverChannelOutput(
        workspace_id=WORKSPACE_ID,
        session_id=SESSION_ID,
        output=Status(run_id=RUN_ID, text="thinking"),
        op_id="op",
    )
    [payload] = converter.to_payloads([request])
    data = json.loads(payload.data)
    assert data["output"]["kind"] == "status"
    # Older readers forbid unknown fields, so unset defaults stay off the wire.
    assert "previous_ref" not in data
    assert converter.from_payloads([payload], [DeliverChannelOutput]) == [request]

    edit = request.model_copy(update={"previous_ref": "msg-ref-1"})
    [edit_payload] = converter.to_payloads([edit])
    assert converter.from_payloads([edit_payload], [DeliverChannelOutput]) == [edit]
