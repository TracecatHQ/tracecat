from __future__ import annotations

import uuid

from tracecat.auth.executor_tokens import ExecutorTokenPayload
from tracecat.executor.action_gateway.capabilities import _agent_gateway_action_allowed


def test_agent_run_python_cannot_use_unconfigured_action_as_superuser() -> None:
    claims = ExecutorTokenPayload(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        allowed_actions=frozenset({"core.script.run_python"}),
        action="core.script.run_python",
        wf_id="wf-1",
        wf_exec_id="run-1",
    )

    assert not _agent_gateway_action_allowed(
        claims, frozenset({"core.cases.list_cases"})
    )
