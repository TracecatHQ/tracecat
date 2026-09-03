from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs

from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.types import AgentConfig
from tracecat.dsl.workflow import (
    _agent_child_run,
    _agent_child_session,
    _with_agent_child_run_id,
    _with_agent_parent_session_id,
)
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
        child_run = _agent_child_run(session_id=session_id)

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


@pytest.mark.parametrize(
    ("requested", "patched", "expected_session", "expected_parent", "continues"),
    [
        pytest.param(None, None, "fresh", None, False, id="fresh-session"),
        pytest.param(
            "parent",
            False,
            "parent",
            None,
            True,
            id="legacy-replay",
        ),
        pytest.param(
            "parent",
            True,
            "fresh",
            "parent",
            False,
            id="fork-parent",
        ),
    ],
)
def test_agent_child_session_forks_explicit_parent(
    requested: str | None,
    patched: bool | None,
    expected_session: str,
    expected_parent: str | None,
    continues: bool,
) -> None:
    parent_session_id = uuid.uuid4()
    fresh_session_id = uuid.uuid4()

    with (
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=patched,
        ) as patched_mock,
        patch(
            "tracecat.dsl.workflow.workflow.uuid4",
            return_value=fresh_session_id,
        ) as uuid4_mock,
    ):
        child_session = _agent_child_session(
            requested_session_id=(parent_session_id if requested == "parent" else None)
        )

    expected_session_id = (
        parent_session_id if expected_session == "parent" else fresh_session_id
    )
    expected_parent_session_id = (
        parent_session_id if expected_parent == "parent" else None
    )
    assert child_session.session_id == expected_session_id
    assert child_session.parent_session_id == expected_parent_session_id
    assert child_session.continue_existing_session is continues
    assert uuid4_mock.call_count == int(expected_session == "fresh")

    workflow_args = _with_agent_parent_session_id(
        AgentWorkflowArgs.model_construct(),
        child_session=child_session,
    )
    serialized = workflow_args.model_dump(mode="json", exclude_unset=True)
    assert ("parent_session_id" in serialized) is (expected_parent == "parent")
    if expected_parent == "parent":
        assert serialized["parent_session_id"] == str(parent_session_id)

    if requested is None:
        patched_mock.assert_not_called()
    else:
        patched_mock.assert_called_once_with(WorkflowPatch.AGENT_SESSION_FORK)
