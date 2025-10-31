from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Webhook
from tracecat.identifiers import WorkflowID


async def get_webhook(
    session: AsyncSession,
    workspace_id,
    workflow_id: WorkflowID,
) -> Webhook | None:
    statement = select(Webhook).where(
        Webhook.owner_id == workspace_id,
        Webhook.workflow_id == workflow_id,
    )
    result = await session.exec(statement)
    return result.first()
