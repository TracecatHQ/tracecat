from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.types import AgentConfig
from tracecat.dsl.workflow import _agent_child_run, _with_agent_child_run_id
from tracecat.temporal.patches import WorkflowPatch


@pytest.mark.parametrize(
    ("patched", "expected_source"),
    [
        pytest.param(False, "session", id="legacy-replay"),
        pytest.param(True, "run", id="new-history"),
    ],
)
def test_agent_child_run_id_is_replay_safe(
    patched: bool,
    expected_source: str,
) -> None:
    session_id = uuid.uuid4()
    fresh_run_id = uuid.uuid4()

    with (
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=patched,
        ) as patched_mock,
        patch(
            "tracecat.dsl.workflow.workflow.uuid4",
            return_value=fresh_run_id,
        ) as uuid4_mock,
    ):
        child_run = _agent_child_run(session_id=session_id, requested=False)

    expected_id = session_id if expected_source == "session" else fresh_run_id
    assert child_run.run_id == expected_id
    assert child_run.include_curr_run_id is patched
    patched_mock.assert_called_once_with(WorkflowPatch.AGENT_CHILD_RUN_ID)
    assert uuid4_mock.call_count == int(patched)

    args = _with_agent_child_run_id(
        RunAgentArgs(
            user_prompt="Investigate",
            session_id=session_id,
            config=AgentConfig(model_name="test", model_provider="test"),
        ),
        child_run=child_run,
    )
    serialized = args.model_dump(mode="json", exclude_unset=True)
    assert ("curr_run_id" in serialized) is patched
    if patched:
        assert serialized["curr_run_id"] == str(fresh_run_id)


def test_agent_child_run_keeps_session_id_for_requested_session() -> None:
    """A caller-supplied session keeps the session-scoped workflow ID.

    That preserves Temporal's rejection of a concurrent run of the same
    session, so no patch marker is consumed and no run UUID is generated.
    """
    session_id = uuid.uuid4()

    with (
        patch("tracecat.dsl.workflow.workflow.patched") as patched_mock,
        patch("tracecat.dsl.workflow.workflow.uuid4") as uuid4_mock,
    ):
        child_run = _agent_child_run(session_id=session_id, requested=True)

    assert child_run.run_id == session_id
    assert child_run.include_curr_run_id is False
    patched_mock.assert_not_called()
    uuid4_mock.assert_not_called()
