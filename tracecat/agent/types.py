from __future__ import annotations

import uuid


class StreamKey(str):
    def __new__(
        cls,
        workspace_id: uuid.UUID | str,
        session_id: uuid.UUID | str,
        *,
        namespace: str = "agent",
    ) -> StreamKey:
        return super().__new__(
            cls,
            f"{namespace}-stream:{str(workspace_id)}:{str(session_id)}",
        )
