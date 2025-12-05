"""Dependencies for workflow action routes."""

from typing import Annotated

from fastapi import Depends

from tracecat.identifiers.action import ActionUUID


def action_id_path_dependency(action_id: str) -> ActionUUID:
    """Convert any action ID format (UUID, short, legacy) to ActionUUID."""
    return ActionUUID.new(action_id)


AnyActionIDPath = Annotated[ActionUUID, Depends(action_id_path_dependency)]
"""An action ID that can be a UUID, short ID (act_xxx), or legacy format (act-<hex>)."""
