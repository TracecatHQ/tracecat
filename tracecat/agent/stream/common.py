from __future__ import annotations

import uuid
from dataclasses import dataclass

from pydantic import BaseModel

from tracecat.agent.models import MessageStore
from tracecat.agent.persistence import DBMessageStore
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamFormat
from tracecat.agent.stream.writers import AgentStreamWriter, StreamWriter


@dataclass
class PersistableStreamingAgentDeps:
    stream_writer: StreamWriter
    message_store: MessageStore | None = None

    def to_spec(self) -> PersistableStreamingAgentDepsSpec:
        """Serialize the dependency metadata for transport."""
        return PersistableStreamingAgentDepsSpec.from_deps(self)

    @classmethod
    async def new(
        cls,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID,
        *,
        persistent: bool = True,
        namespace: str = "agent",
    ) -> PersistableStreamingAgentDeps:
        stream = await AgentStream.new(session_id, workspace_id, namespace=namespace)
        return cls(
            stream_writer=AgentStreamWriter(stream=stream),
            message_store=DBMessageStore() if persistent else None,
        )

    @classmethod
    async def from_spec(
        cls, spec: PersistableStreamingAgentDepsSpec
    ) -> PersistableStreamingAgentDeps:
        return await cls.new(
            spec.session_id,
            spec.workspace_id,
            persistent=spec.persistent,
            namespace=spec.namespace,
        )


class PersistableStreamingAgentDepsSpec(BaseModel):
    """Serializable metadata required to reconstruct PersistableStreamingAgentDeps."""

    session_id: uuid.UUID
    workspace_id: uuid.UUID
    persistent: bool = True
    namespace: str = "agent"

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
            namespace=stream.namespace,
            persistent=deps.message_store is not None,
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
