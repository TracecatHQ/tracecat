import uuid

from tracecat.agent.stream.events import StreamFormat


class StreamKey(str):
    def __new__(cls, session_id: uuid.UUID | str) -> "StreamKey":
        return super().__new__(cls, f"agent-stream:{str(session_id)}")


def get_stream_headers(format: StreamFormat = "vercel") -> dict[str, str]:
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
    if format == "vercel":
        headers["x-vercel-ai-ui-message-stream"] = "v1"
    return headers
