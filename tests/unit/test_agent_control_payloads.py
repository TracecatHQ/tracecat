import hashlib
from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from tracecat.agent.control.payloads import (
    COMMAND_PAYLOAD,
    AbortPayload,
    CommandPayload,
    DecidePayload,
    SteerPayload,
    SubmitPayload,
    WithdrawPayload,
)
from tracecat.agent.control.store import payload_hash
from tracecat.dsl._converter import PydanticPayloadConverter


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for pure unit tests."""
    yield


@pytest.mark.parametrize(
    "payload",
    [
        SubmitPayload(input="hi"),
        SteerPayload(text="hi"),
        DecidePayload(action="deny", reason="no"),
        AbortPayload(),
        WithdrawPayload(),
        WithdrawPayload(target_ids=["req-1"]),
    ],
)
def test_every_payload_keeps_its_type_through_an_exclude_unset_dump(
    payload: CommandPayload,
) -> None:
    """The worker converter dumps with exclude_unset; the tag must survive it."""
    dumped = payload.model_dump(mode="json", exclude_unset=True)
    converted = PydanticPayloadConverter().to_payloads([payload])[0]

    assert dumped["type"] == payload.type
    assert COMMAND_PAYLOAD.validate_python(dumped) == payload
    assert COMMAND_PAYLOAD.validate_json(converted.data) == payload


@pytest.mark.parametrize(
    ("payload", "canonical"),
    [
        (SubmitPayload(input="hi"), '{"input":"hi","type":"submit"}'),
        # Non-ASCII stays \\u-escaped, as stdlib json.dumps writes it.
        (SteerPayload(text="héllo"), '{"text":"h\\u00e9llo","type":"steer"}'),
    ],
)
def test_unchanged_shapes_hash_byte_for_byte_like_existing_rows(
    payload: CommandPayload, canonical: str
) -> None:
    """Submit and steer digests equal those of the sorted-key compact JSON form."""
    assert payload_hash(payload) == hashlib.sha256(canonical.encode()).hexdigest()


def test_decide_carries_no_approval_id() -> None:
    """The command's request ID is the approval ID."""
    with pytest.raises(ValidationError):
        DecidePayload.model_validate(
            {"type": "decide", "action": "approve", "approval_id": "a-1"}
        )


def test_abort_takes_no_fields() -> None:
    with pytest.raises(ValidationError):
        AbortPayload.model_validate({"type": "abort", "background": False})
    assert AbortPayload().model_dump(mode="json") == {"type": "abort"}


def test_withdraw_without_targets_takes_back_everything() -> None:
    assert WithdrawPayload().target_ids is None


def test_decide_reason_and_withdraw_targets_are_bounded() -> None:
    with pytest.raises(ValidationError):
        DecidePayload(action="deny", reason="x" * 100_001)
    with pytest.raises(ValidationError):
        WithdrawPayload(target_ids=[f"req-{i}" for i in range(1_001)])
