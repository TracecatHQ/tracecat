"""Agent preset specs exported here keep their thinking choice on older servers.

A server from before reasoning levels reads only ``enable_thinking``, defaults it
to true, and keeps unknown keys such as ``reasoning_effort`` without using them.
Until a contract migration drops the flag, exports also write ``enable_thinking``
derived from ``reasoning_effort``, so a preset that is ``off`` stays off when an
older or rolled-back server pulls the repository. This version keeps preferring
``reasoning_effort`` on import, and re-exporting an imported spec is stable.
"""

import pytest
import yaml
from pydantic import BaseModel, ConfigDict

from tracecat.workspace_sync.schemas import AgentPresetResourceSpec
from tracecat.workspace_sync.serialization import serialize_yaml_model


class _PreReasoningPresetSpec(BaseModel):
    """The part of ``AgentPresetResourceSpec`` that older servers read."""

    model_config = ConfigDict(extra="allow")

    enable_thinking: bool = True


@pytest.mark.parametrize(
    ("reasoning_effort", "enable_thinking"),
    [("off", False), ("high", True), (None, True)],
)
def test_export_keeps_thinking_choice_for_older_servers(
    reasoning_effort: str | None, enable_thinking: bool
) -> None:
    spec = AgentPresetResourceSpec.model_validate(
        {
            "id": "triage",
            "slug": "triage",
            "name": "Triage",
            "reasoning_effort": reasoning_effort,
        }
    )

    exported = yaml.safe_load(serialize_yaml_model(spec))

    assert _PreReasoningPresetSpec.model_validate(exported).enable_thinking is (
        enable_thinking
    )
    imported = AgentPresetResourceSpec.model_validate(exported)
    assert imported.reasoning_effort == reasoning_effort
    assert serialize_yaml_model(imported) == serialize_yaml_model(spec)
