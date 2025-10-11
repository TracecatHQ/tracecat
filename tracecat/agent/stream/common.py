from __future__ import annotations

import uuid
from dataclasses import dataclass

from tracecat.agent.models import MessageStore
from tracecat.agent.persistence import DBMessageStore
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamFormat
from tracecat.agent.stream.writers import AgentStreamWriter, StreamWriter


@dataclass
class PersistableStreamingAgentDeps:
    stream_writer: StreamWriter
    message_store: MessageStore | None = None

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


def get_stream_headers(format: StreamFormat = "vercel") -> dict[str, str]:
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
    if format == "vercel":
        headers["x-vercel-ai-ui-message-stream"] = "v1"
    return headers
