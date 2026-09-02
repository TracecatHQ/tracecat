from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from tracecat.dsl.workflow import _agent_child_workflow_id
from tracecat.temporal.patches import WorkflowPatch


@pytest.mark.parametrize(
    ("patched", "expected_source"),
    [
        pytest.param(False, "session", id="legacy-replay"),
        pytest.param(True, "run", id="new-history"),
    ],
)
def test_agent_child_workflow_id_is_replay_safe(
    patched: bool,
    expected_source: str,
) -> None:
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    with patch(
        "tracecat.dsl.workflow.workflow.patched",
        return_value=patched,
    ) as patched_mock:
        workflow_id = _agent_child_workflow_id(
            session_id=session_id,
            run_id=run_id,
        )

    expected_id = session_id if expected_source == "session" else run_id
    assert str(workflow_id) == f"agent/{expected_id}"
    patched_mock.assert_called_once_with(WorkflowPatch.AGENT_CHILD_RUN_ID)
