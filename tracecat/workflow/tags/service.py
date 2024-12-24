from collections.abc import Sequence

from sqlmodel import select

from tracecat.db.schemas import Tag, WorkflowTag
from tracecat.identifiers import TagID
from tracecat.identifiers.workflow import WorkflowID
from tracecat.service import Service


class WorkflowTagsService(Service):
    _service_name = "workflow-tags"

    async def list_tags_for_workflow(self, wf_id: WorkflowID) -> Sequence[Tag]:
        stmt = select(Tag).where(
            Tag.id == WorkflowTag.tag_id, WorkflowTag.workflow_id == wf_id
        )
        result = await self.session.exec(stmt)
        return result.all()

    async def get_workflow_tag(self, wf_id: WorkflowID, tag_id: TagID) -> WorkflowTag:
        """Get a workflow tag association."""
        stmt = select(WorkflowTag).where(
            WorkflowTag.workflow_id == wf_id, WorkflowTag.tag_id == tag_id
        )
        result = await self.session.exec(stmt)
        return result.one()

    async def add_workflow_tag(self, wf_id: WorkflowID, tag_id: TagID) -> WorkflowTag:
        """Add a tag association to a workflow."""
        wf_tag = WorkflowTag(workflow_id=wf_id, tag_id=tag_id)
        self.session.add(wf_tag)
        await self.session.commit()
        return wf_tag

    async def remove_workflow_tag(self, wf_tag: WorkflowTag) -> None:
        """Delete a workflow tag association."""
        await self.session.delete(wf_tag)
        await self.session.commit()
