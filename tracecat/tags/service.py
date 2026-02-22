import uuid
from collections.abc import Sequence

from slugify import slugify
from sqlalchemy import select

from tracecat.audit.logger import audit_log
from tracecat.authz.controls import require_scope
from tracecat.db.models import WorkflowTag
from tracecat.identifiers import TagID
from tracecat.service import BaseWorkspaceService
from tracecat.tags.schemas import TagCreate, TagUpdate


class TagsService(BaseWorkspaceService):
    service_name = "tags"

    async def list_tags(self) -> Sequence[WorkflowTag]:
        statement = select(WorkflowTag).where(
            WorkflowTag.workspace_id == self.workspace_id
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_tag(self, tag_id: TagID) -> WorkflowTag:
        statement = select(WorkflowTag).where(
            WorkflowTag.workspace_id == self.workspace_id,
            WorkflowTag.id == tag_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def get_tag_by_ref(self, ref: str) -> WorkflowTag:
        """Get a tag by its ref."""
        statement = select(WorkflowTag).where(
            WorkflowTag.workspace_id == self.workspace_id,
            WorkflowTag.ref == ref,
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def get_tag_by_ref_or_id(self, tag_identifier: str) -> WorkflowTag:
        """Get a tag by either ref or ID."""
        # Try UUID first
        try:
            uuid_obj = uuid.UUID(tag_identifier)
            return await self.get_tag(uuid_obj)
        except ValueError:
            # Not a UUID, try ref
            return await self.get_tag_by_ref(tag_identifier)

    @require_scope("tag:create")
    @audit_log(resource_type="tag", action="create")
    async def create_tag(self, tag: TagCreate) -> WorkflowTag:
        # Generate ref
        ref = slugify(tag.name)

        # Check if ref already exists
        existing = await self.session.execute(
            select(WorkflowTag).where(
                WorkflowTag.ref == ref, WorkflowTag.workspace_id == self.workspace_id
            )
        )
        if existing.one_or_none():
            raise ValueError(f"Tag with slug '{ref}' already exists")

        db_tag = WorkflowTag(
            name=tag.name, ref=ref, workspace_id=self.workspace_id, color=tag.color
        )
        self.session.add(db_tag)
        await self.session.commit()
        return db_tag

    @require_scope("tag:update")
    @audit_log(resource_type="tag", action="update")
    async def update_tag(self, tag: WorkflowTag, tag_update: TagUpdate) -> WorkflowTag:
        """Update tag and regenerate ref if name changed."""
        if tag_update.name and tag_update.name != tag.name:
            tag.ref = slugify(tag_update.name)

        for key, value in tag_update.model_dump(exclude_unset=True).items():
            setattr(tag, key, value)
        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    @require_scope("tag:delete")
    @audit_log(resource_type="tag", action="delete")
    async def delete_tag(self, tag: WorkflowTag) -> None:
        await self.session.delete(tag)
        await self.session.commit()
