from collections.abc import Sequence

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.authz.controls import require_scope
from tracecat.db.models import Workflow, WorkflowTag, WorkflowTagLink
from tracecat.identifiers import TagID
from tracecat.identifiers.workflow import WorkflowID
from tracecat.service import BaseWorkspaceService


class WorkflowTagsService(BaseWorkspaceService):
    service_name = "workflow_tags"

    async def _require_workflow_and_tag_in_workspace(
        self, wf_id: WorkflowID, tag_id: TagID
    ) -> None:
        workflow_exists = exists(
            select(Workflow.id).where(
                Workflow.id == wf_id,
                Workflow.workspace_id == self.workspace_id,
            )
        )
        tag_exists = exists(
            select(WorkflowTag.id).where(
                WorkflowTag.id == tag_id,
                WorkflowTag.workspace_id == self.workspace_id,
            )
        )
        is_allowed = await self.session.scalar(select(workflow_exists & tag_exists))
        if not is_allowed:
            raise NoResultFound("Workflow or tag not found")

    async def list_tags_for_workflow(self, wf_id: WorkflowID) -> Sequence[WorkflowTag]:
        stmt = (
            select(WorkflowTag)
            .join(WorkflowTagLink, WorkflowTag.id == WorkflowTagLink.tag_id)
            .join(Workflow, Workflow.id == WorkflowTagLink.workflow_id)
            .where(
                WorkflowTagLink.workflow_id == wf_id,
                Workflow.workspace_id == self.workspace_id,
                WorkflowTag.workspace_id == self.workspace_id,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_workflow_tag(
        self, wf_id: WorkflowID, tag_id: TagID
    ) -> WorkflowTagLink:
        """Get a workflow tag association."""
        stmt = (
            select(WorkflowTagLink)
            .join(Workflow, Workflow.id == WorkflowTagLink.workflow_id)
            .join(WorkflowTag, WorkflowTag.id == WorkflowTagLink.tag_id)
            .where(
                WorkflowTagLink.workflow_id == wf_id,
                WorkflowTagLink.tag_id == tag_id,
                Workflow.workspace_id == self.workspace_id,
                WorkflowTag.workspace_id == self.workspace_id,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    @require_scope("workflow:update")
    async def add_workflow_tag(
        self, wf_id: WorkflowID, tag_id: TagID
    ) -> WorkflowTagLink:
        """Add a tag association to a workflow."""
        await self._require_workflow_and_tag_in_workspace(wf_id, tag_id)
        wf_tag = WorkflowTagLink(workflow_id=wf_id, tag_id=tag_id)
        self.session.add(wf_tag)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ValueError("Tag already assigned to workflow") from e
        return wf_tag

    @require_scope("workflow:update")
    async def remove_workflow_tag(self, wf_tag: WorkflowTagLink) -> None:
        """Delete a workflow tag association."""
        await self.session.delete(wf_tag)
        await self.session.commit()
