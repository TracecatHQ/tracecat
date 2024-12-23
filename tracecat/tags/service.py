from collections.abc import Sequence

from sqlmodel import select

from tracecat.db.schemas import Tag
from tracecat.identifiers import TagID
from tracecat.service import Service
from tracecat.tags.models import TagCreate, TagUpdate


class TagsService(Service):
    _service_name = "tags"

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

    async def create_tag(self, tag: TagCreate) -> Tag:
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")
        db_tag = Tag(name=tag.name, owner_id=workspace_id, color=tag.color)  # type: ignore
        self.session.add(db_tag)
        await self.session.commit()
        return db_tag

    async def update_tag(self, tag: Tag, tag_update: TagUpdate) -> Tag:
        for key, value in tag_update.model_dump(exclude_unset=True).items():
            setattr(tag, key, value)
        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    async def delete_tag(self, tag: Tag) -> None:
        await self.session.delete(tag)
        await self.session.commit()
