"""Identity of one approval pause."""

import uuid


def approval_batch_id(run_id: uuid.UUID, tool_call_ids: list[str]) -> uuid.UUID:
    """Derive the batch id from the paused run and its pending tool calls."""
    # Prefixed so it never equals the continuation stream id derived from the same run.
    return uuid.uuid5(run_id, f"approval-batch:{','.join(sorted(tool_call_ids))}")
