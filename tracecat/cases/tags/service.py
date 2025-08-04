import uuid
from collections.abc import Sequence

from sqlmodel import select

from tracecat.db.schemas import CaseTag, Tag
from tracecat.identifiers import TagID
from tracecat.service import BaseWorkspaceService
from tracecat.tags.service import TagsService


class CaseTagsService(BaseWorkspaceService):
    service_name = "case_tags"

    async def list_tags_for_case(self, case_id: uuid.UUID) -> Sequence[Tag]:
        """List all tags for a case."""
        stmt = select(Tag).join(CaseTag).where(CaseTag.case_id == case_id)
        result = await self.session.exec(stmt)
        return result.all()

    async def get_case_tag(self, case_id: uuid.UUID, tag_id: TagID) -> CaseTag:
        """Get a case tag association."""
        stmt = select(CaseTag).where(
            CaseTag.case_id == case_id, CaseTag.tag_id == tag_id
        )
        result = await self.session.exec(stmt)
        return result.one()

    async def add_case_tag(self, case_id: uuid.UUID, tag_identifier: str) -> Tag:
        """Add a tag to a case by ID or ref."""
        # Resolve tag identifier to ID
        tags_service = TagsService(self.session, self.role)
        tag = await tags_service.get_tag_by_ref_or_id(tag_identifier)

        # Check if already exists
        stmt = select(CaseTag).where(
            CaseTag.case_id == case_id, CaseTag.tag_id == tag.id
        )
        result = await self.session.exec(stmt)
        existing = result.one_or_none()

        if existing:
            return tag  # Already exists, return tag

        # Create new
        case_tag = CaseTag(case_id=case_id, tag_id=tag.id)
        self.session.add(case_tag)

        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    async def remove_case_tag(self, case_id: uuid.UUID, tag_identifier: str) -> None:
        """Remove a tag from a case by ID or ref."""
        tags_service = TagsService(self.session, self.role)
        tag = await tags_service.get_tag_by_ref_or_id(tag_identifier)

        case_tag = await self.get_case_tag(case_id, tag.id)
        await self.session.delete(case_tag)
        await self.session.commit()
