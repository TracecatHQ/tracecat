"""Temporal wire tests for session input verbs and their results."""

import json
import uuid
from typing import Any

import pytest
from pydantic import BaseModel

from tracecat.agent.session.inputs import (
    Actor,
    ChannelOrigin,
    DecisionInput,
    DecisionRecorded,
    MessageInput,
    MessageResult,
    RunAlreadyFinished,
    RunBusy,
    RunRef,
    RunStarted,
    RunSteered,
    RunStopping,
    RunSuperseded,
    ScheduleOrigin,
    SessionInput,
    StopInput,
    StopResult,
    WebOrigin,
)
from tracecat.dsl._converter import PydanticPayloadConverter

SESSION_ID = uuid.UUID("00000000-0000-4000-8000-00000000005e")
RUN_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
STREAM_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a2")
BATCH_ID = uuid.UUID("00000000-0000-4000-8000-0000000000ba")
BOT_ID = uuid.UUID("00000000-0000-4000-8000-0000000000b0")
USER_ID = uuid.UUID("00000000-0000-4000-8000-0000000000d0")

ACTOR = Actor(user_id=USER_ID, channel_user_id=None)
MESSAGE = MessageInput(
    request_id="channel:Ev0EXAMPLE",
    session_id=SESSION_ID,
    actor=ACTOR,
    origin=ChannelOrigin(provider="example", bot_id=BOT_ID),
    text="hello",
)
DECISION = DecisionInput(
    request_id="web:1",
    session_id=SESSION_ID,
    actor=ACTOR,
    origin=WebOrigin(),
    batch_id=BATCH_ID,
    tool_call_id="toolu_0EXAMPLE",
    action="deny",
    reason="not now",
)
STOP = StopInput(
    request_id="schedule:1",
    session_id=SESSION_ID,
    actor=Actor(user_id=None, channel_user_id=None),
    origin=ScheduleOrigin(schedule_id="sch-1"),
    expected_run_id=RUN_ID,
)

ALIAS_CASES: list[tuple[Any, BaseModel]] = [
    (SessionInput, MESSAGE),
    (SessionInput, DECISION),
    (SessionInput, STOP),
    (MessageResult, RunStarted(run_id=RUN_ID)),
    (MessageResult, RunSteered(run_id=RUN_ID)),
    (MessageResult, RunBusy(run_id=RUN_ID)),
    (StopResult, RunStopping(run_id=RUN_ID)),
    (StopResult, RunAlreadyFinished(run_id=RUN_ID)),
    (StopResult, RunSuperseded(current_run_id=RUN_ID)),
    (
        DecisionRecorded,
        DecisionRecorded(
            pending_tool_call_ids=["toolu_1"],
            resumed=RunRef(run_id=RUN_ID, stream_id=STREAM_ID),
        ),
    ),
]


@pytest.mark.parametrize(
    ("hint", "value"),
    ALIAS_CASES,
    ids=[type(value).__name__ for _, value in ALIAS_CASES],
)
def test_temporal_round_trip_decodes_with_alias_hint(
    hint: Any, value: BaseModel
) -> None:
    converter = PydanticPayloadConverter()
    decoded = converter.from_payloads(converter.to_payloads([value]), [hint])
    assert decoded == [value]
    assert type(decoded[0]) is type(value)


def test_temporal_payload_keeps_defaulted_discriminators() -> None:
    converter = PydanticPayloadConverter()
    [message, decision] = converter.to_payloads([MESSAGE, DECISION])
    message_json = json.loads(message.data)
    decision_json = json.loads(decision.data)
    assert message_json["kind"] == "message"
    assert message_json["origin"]["kind"] == "channel"
    assert decision_json["kind"] == "decision"
    assert decision_json["origin"] == {"kind": "web"}


def test_unset_defaults_stay_off_the_wire() -> None:
    converter = PydanticPayloadConverter()
    bare = DecisionInput(
        request_id="web:2",
        session_id=SESSION_ID,
        actor=ACTOR,
        origin=WebOrigin(),
        batch_id=BATCH_ID,
        tool_call_id="toolu_0EXAMPLE",
        action="approve",
    )
    # Older readers forbid unknown fields, so unset defaults are omitted.
    assert "reason" not in json.loads(converter.to_payloads([bare])[0].data)
