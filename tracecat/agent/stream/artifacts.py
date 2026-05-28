"""Artifact stream projection helpers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, cast

from tracecat.agent.common.stream_types import UnifiedStreamEvent
from tracecat.artifacts.bindings import artifact_side_effects_for_tool_result
from tracecat.artifacts.schemas import (
    Artifact,
    ArtifactOp,
    artifact_data_payload,
)


def artifact_stream_event(op: ArtifactOp, artifact: Artifact) -> UnifiedStreamEvent:
    """Serialize an artifact operation as a durable unified stream event."""
    payload = artifact_data_payload(op, artifact)
    return UnifiedStreamEvent.artifact_event(
        op=op,
        artifact=cast(dict[str, Any], payload["artifact"]),
    )


def artifact_stream_events_for_tool_result(
    *,
    tool_name: str | None,
    tool_input: Mapping[str, Any] | None,
    tool_output: Any,
    is_error: bool,
    tool_call_id: str | None,
) -> Iterator[UnifiedStreamEvent]:
    """Serialize action-result artifact side effects as stream events."""
    for effect in artifact_side_effects_for_tool_result(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
        is_error=is_error,
        tool_call_id=tool_call_id,
    ):
        yield artifact_stream_event(effect.op, effect.artifact)
