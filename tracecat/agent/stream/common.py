from __future__ import annotations

import uuid
from dataclasses import dataclass

from pydantic import BaseModel

from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamFormat
from tracecat.agent.stream.writers import AgentStreamWriter, StreamWriter


@dataclass
class PersistableStreamingAgentDeps:
    stream_writer: StreamWriter

    def to_spec(self) -> PersistableStreamingAgentDepsSpec:
        """Serialize the dependency metadata for transport."""
        return PersistableStreamingAgentDepsSpec.from_deps(self)

    @classmethod
    async def new(
        cls,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> PersistableStreamingAgentDeps:
        stream = await AgentStream.new(session_id, workspace_id)
        return cls(stream_writer=AgentStreamWriter(stream=stream))

    @classmethod
    async def from_spec(
        cls, spec: PersistableStreamingAgentDepsSpec
    ) -> PersistableStreamingAgentDeps:
        return await cls.new(spec.session_id, spec.workspace_id)


class PersistableStreamingAgentDepsSpec(BaseModel):
    """Serializable metadata required to reconstruct PersistableStreamingAgentDeps."""

    session_id: uuid.UUID
    workspace_id: uuid.UUID

    async def build(self) -> PersistableStreamingAgentDeps:
        """Reconstruct dependencies from this spec."""
        return await PersistableStreamingAgentDeps.from_spec(self)

    @classmethod
    def from_deps(
        cls, deps: PersistableStreamingAgentDeps
    ) -> PersistableStreamingAgentDepsSpec:
        stream = getattr(deps.stream_writer, "stream", None)
        if stream is None:
            msg = (
                "PersistableStreamingAgentDeps requires a stream-backed writer to "
                "create a serialization spec."
            )
            raise ValueError(msg)

        return cls(
            session_id=stream.session_id,
            workspace_id=stream.workspace_id,
        )


def get_stream_headers(format: StreamFormat = "vercel") -> dict[str, str]:
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
    if format == "vercel":
        headers["x-vercel-ai-ui-message-stream"] = "v1"
    return headers
