from __future__ import annotations

from tracecat.agent.adapter.artifact import (
    ArtifactAdapter,
    WorkflowArtifact,
    artifact_data_payload,
    artifact_stream_event,
)
from tracecat.agent.common.stream_types import StreamEventType


def test_artifact_data_payload_serializes_camel_case_fields() -> None:
    artifact = ArtifactAdapter.validate_python(
        {
            "type": "workflow",
            "id": "wf_123",
            "title": "Triage workflow",
            "color": "#64748b",
            "isPublished": True,
        }
    )

    assert artifact_data_payload("upsert", artifact) == {
        "op": "upsert",
        "artifact": {
            "type": "workflow",
            "id": "wf_123",
            "title": "Triage workflow",
            "color": "#64748b",
            "isPublished": True,
        },
    }


def test_artifact_adapter_validates_aliases() -> None:
    artifact = ArtifactAdapter.validate_python(
        {
            "type": "workflow",
            "id": "wf_123",
            "title": "Triage workflow",
            "color": "#64748b",
            "isPublished": False,
        }
    )

    assert isinstance(artifact, WorkflowArtifact)
    assert artifact.is_published is False


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
