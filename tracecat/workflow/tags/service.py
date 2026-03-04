from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tracecat.authz.controls import require_scope
from tracecat.db.models import WorkflowTag, WorkflowTagLink
from tracecat.identifiers import TagID
from tracecat.identifiers.workflow import WorkflowID
from tracecat.service import BaseWorkspaceService


class WorkflowTagsService(BaseWorkspaceService):
    service_name = "workflow_tags"

    async def list_tags_for_workflow(self, wf_id: WorkflowID) -> Sequence[WorkflowTag]:
        stmt = select(WorkflowTag).where(
            WorkflowTag.id == WorkflowTagLink.tag_id,
            WorkflowTagLink.workflow_id == wf_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_workflow_tag(
        self, wf_id: WorkflowID, tag_id: TagID
    ) -> WorkflowTagLink:
        """Get a workflow tag association."""
        stmt = select(WorkflowTagLink).where(
            WorkflowTagLink.workflow_id == wf_id,
            WorkflowTagLink.tag_id == tag_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    @require_scope("workflow:update")
    async def add_workflow_tag(
        self, wf_id: WorkflowID, tag_id: TagID
    ) -> WorkflowTagLink:
        """Add a tag association to a workflow."""
        stmt = select(WorkflowTagLink).where(
            WorkflowTagLink.workflow_id == wf_id,
            WorkflowTagLink.tag_id == tag_id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        wf_tag = WorkflowTagLink(workflow_id=wf_id, tag_id=tag_id)
        try:
            self.session.add(wf_tag)
            await self.session.commit()
        except IntegrityError:
            # Handle concurrent inserts for the same workflow/tag pair.
            await self.session.rollback()
            return await self.get_workflow_tag(wf_id, tag_id)
        return wf_tag

    @require_scope("workflow:update")
    async def remove_workflow_tag(self, wf_tag: WorkflowTagLink) -> None:
        """Delete a workflow tag association."""
        await self.session.delete(wf_tag)
        await self.session.commit()
