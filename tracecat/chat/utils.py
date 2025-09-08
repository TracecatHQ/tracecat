import uuid
from collections.abc import Callable

from tracecat.chat.enums import ChatEntity
from tracecat.identifiers import WorkflowUUID

ENTITY_ID_CONVERTER: dict[str, Callable[[str], uuid.UUID]] = {
    ChatEntity.WORKFLOW: WorkflowUUID.new,
    ChatEntity.CASE: uuid.UUID,
}
