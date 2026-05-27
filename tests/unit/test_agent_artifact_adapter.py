from __future__ import annotations

from tracecat.agent.adapter.artifact import (
    ArtifactAdapter,
    WorkflowArtifact,
    artifact_data_payload,
)


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

    assert artifact_data_payload("add", artifact) == {
        "op": "add",
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
