from __future__ import annotations

import uuid


class StreamKey(str):
    def __new__(cls, session_id: uuid.UUID | str) -> StreamKey:
        return super().__new__(cls, f"agent-stream:{str(session_id)}")
