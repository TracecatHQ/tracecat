"""Tests for core.workflow actions."""

from __future__ import annotations

import pytest
from tracecat_registry import ActionIsInterfaceError
from tracecat_registry.core import workflow as core_workflow


@pytest.mark.anyio
async def test_execute_is_interface() -> None:
    with pytest.raises(ActionIsInterfaceError):
        await core_workflow.execute(  # type: ignore[misc]
            workflow_alias="subflow",
            trigger_inputs={"a": 1},
        )
