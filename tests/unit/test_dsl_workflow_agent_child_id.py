from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from tracecat.dsl.workflow import _agent_child_run_id
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
        run_id = _agent_child_run_id(session_id=session_id)

    expected_id = session_id if expected_source == "session" else fresh_run_id
    assert run_id == expected_id
    patched_mock.assert_called_once_with(WorkflowPatch.AGENT_CHILD_RUN_ID)
    assert uuid4_mock.call_count == int(patched)
