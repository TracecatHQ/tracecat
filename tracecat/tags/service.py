import uuid
from collections.abc import Sequence

from slugify import slugify
from sqlmodel import select

from tracecat.db.schemas import Tag
from tracecat.identifiers import TagID
from tracecat.service import BaseService
from tracecat.tags.models import TagCreate, TagUpdate


class TagsService(BaseService):
    service_name = "tags"

    async def list_tags(self) -> Sequence[Tag]:
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")
        statement = select(Tag).where(Tag.owner_id == workspace_id)
        result = await self.session.exec(statement)
        return result.all()

    async def get_tag(self, tag_id: TagID) -> Tag:
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")
        statement = select(Tag).where(
            Tag.owner_id == workspace_id,
            Tag.id == tag_id,
        )
        result = await self.session.exec(statement)
        return result.one()

    async def get_tag_by_ref(self, ref: str) -> Tag:
        """Get a tag by its ref."""
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")
        statement = select(Tag).where(
            Tag.owner_id == workspace_id,
            Tag.ref == ref,
        )
        result = await self.session.exec(statement)
        return result.one()

    async def get_tag_by_ref_or_id(self, tag_identifier: str) -> Tag:
        """Get a tag by either ref or ID."""
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        # Try UUID first
        try:
            uuid_obj = uuid.UUID(tag_identifier)
            return await self.get_tag(uuid_obj)
        except ValueError:
            # Not a UUID, try ref
            return await self.get_tag_by_ref(tag_identifier)

    async def create_tag(self, tag: TagCreate) -> Tag:
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        # Generate ref
        ref = slugify(tag.name)

        db_tag = Tag(name=tag.name, ref=ref, owner_id=workspace_id, color=tag.color)  # type: ignore
        self.session.add(db_tag)
        await self.session.commit()
        return db_tag

    async def update_tag(self, tag: Tag, tag_update: TagUpdate) -> Tag:
        """Update tag and regenerate ref if name changed."""
        if tag_update.name and tag_update.name != tag.name:
            tag.ref = slugify(tag_update.name)

        for key, value in tag_update.model_dump(exclude_unset=True).items():
            setattr(tag, key, value)
        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    async def delete_tag(self, tag: Tag) -> None:
        await self.session.delete(tag)
        await self.session.commit()
