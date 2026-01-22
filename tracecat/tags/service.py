import uuid
from collections.abc import Sequence

from slugify import slugify
from sqlalchemy import select

from tracecat.audit.logger import audit_log
from tracecat.db.models import Tag
from tracecat.identifiers import TagID
from tracecat.service import BaseWorkspaceService
from tracecat.tags.schemas import TagCreate, TagUpdate


class TagsService(BaseWorkspaceService):
    service_name = "tags"

    async def list_tags(self) -> Sequence[Tag]:
        statement = select(Tag).where(Tag.workspace_id == self.workspace_id)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_tag(self, tag_id: TagID) -> Tag:
        statement = select(Tag).where(
            Tag.workspace_id == self.workspace_id,
            Tag.id == tag_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def get_tag_by_ref(self, ref: str) -> Tag:
        """Get a tag by its ref."""
        statement = select(Tag).where(
            Tag.workspace_id == self.workspace_id,
            Tag.ref == ref,
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def get_tag_by_ref_or_id(self, tag_identifier: str) -> Tag:
        """Get a tag by either ref or ID."""
        # Try UUID first
        try:
            uuid_obj = uuid.UUID(tag_identifier)
            return await self.get_tag(uuid_obj)
        except ValueError:
            # Not a UUID, try ref
            return await self.get_tag_by_ref(tag_identifier)

    @audit_log(resource_type="tag", action="create")
    async def create_tag(self, tag: TagCreate) -> Tag:
        # Generate ref
        ref = slugify(tag.name)

        # Check if ref already exists
        existing = await self.session.execute(
            select(Tag).where(Tag.ref == ref, Tag.workspace_id == self.workspace_id)
        )
        if existing.one_or_none():
            raise ValueError(f"Tag with slug '{ref}' already exists")

        db_tag = Tag(
            name=tag.name, ref=ref, workspace_id=self.workspace_id, color=tag.color
        )
        self.session.add(db_tag)
        await self.session.commit()
        return db_tag

    @audit_log(resource_type="tag", action="update")
    async def update_tag(self, tag: Tag, tag_update: TagUpdate) -> Tag:
        """Update tag and regenerate ref if name changed."""
        if tag_update.name and tag_update.name != tag.name:
            tag.ref = slugify(tag_update.name)

        for key, value in tag_update.model_dump(exclude_unset=True).items():
            setattr(tag, key, value)
        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    @audit_log(resource_type="tag", action="delete")
    async def delete_tag(self, tag: Tag) -> None:
        await self.session.delete(tag)
        await self.session.commit()
