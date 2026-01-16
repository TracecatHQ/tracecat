from __future__ import annotations

import pytest

from tracecat.dsl.action import materialize_context
from tracecat.dsl.schemas import ExecutionContext, TaskResult
from tracecat.storage.object import InlineObject


@pytest.mark.anyio
async def test_materialize_context_rehydrates_task_result_dicts() -> None:
    ctx = ExecutionContext(
        ACTIONS={
            "a1": TaskResult.from_result({"ok": True}),
        },
        TRIGGER=InlineObject(data={"trigger": 1}),
    )

    materialized = await materialize_context(ctx)

    # Verify keys exist (they're optional in TypedDict due to total=False)
    assert "ACTIONS" in materialized
    assert "TRIGGER" in materialized
    assert materialized["ACTIONS"]["a1"]["result"] == {"ok": True}
    assert materialized["ACTIONS"]["a1"]["result_typename"] == "dict"
    assert materialized["TRIGGER"] == {"trigger": 1}
