from __future__ import annotations

from tracecat.agent.common.stream_types import StreamEventType
from tracecat.agent.stream.artifacts import (
    artifact_stream_event,
    artifact_stream_events_for_tool_result,
)
from tracecat.artifacts.schemas import ArtifactAdapter


def test_artifact_stream_event_uses_semantic_event_type() -> None:
    artifact = ArtifactAdapter.validate_python(
        {
            "type": "workflow",
            "id": "wf_123",
            "title": "Triage workflow",
            "color": "#64748b",
            "isPublished": True,
        }
    )

    event = artifact_stream_event("upsert", artifact)

    assert event.type is StreamEventType.ARTIFACT
    assert event.artifact_data is not None
    assert event.artifact_data.op == "upsert"
    assert event.artifact_data.artifact == {
        "type": "workflow",
        "id": "wf_123",
        "title": "Triage workflow",
        "color": "#64748b",
        "isPublished": True,
    }


def test_artifact_stream_events_for_tool_result_projects_side_effects() -> None:
    events = list(
        artifact_stream_events_for_tool_result(
            tool_name="core.cases.create_case",
            tool_input={"summary": "Suspicious login"},
            tool_output={
                "id": "case_123",
                "summary": "Suspicious login",
                "severity": "high",
                "status": "new",
            },
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert len(events) == 1
    event = events[0]
    assert event.type is StreamEventType.ARTIFACT
    assert event.artifact_data is not None
    assert event.artifact_data.op == "upsert"
    assert event.artifact_data.artifact == {
        "type": "case",
        "id": "case_123",
        "title": "Suspicious login",
        "scope": {"parentToolCallId": "toolu_123"},
        "severity": "high",
        "status": "new",
    }
