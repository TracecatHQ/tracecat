import uuid
from collections.abc import Sequence
from typing import cast

from slugify import slugify
from sqlalchemy.exc import NoResultFound
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import select

from tracecat.db.schemas import CaseTag, CaseTagLink
from tracecat.identifiers import CaseTagID
from tracecat.service import BaseWorkspaceService
from tracecat.tags.models import TagCreate, TagUpdate


class CaseTagsService(BaseWorkspaceService):
    service_name = "case_tags"

    async def list_workspace_tags(self) -> Sequence[CaseTag]:
        """List all tags available in the current workspace."""
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        stmt = select(CaseTag).where(CaseTag.owner_id == workspace_id)
        result = await self.session.exec(stmt)
        return result.all()

    async def get_tag(self, tag_id: CaseTagID) -> CaseTag:
        """Get a case tag by ID scoped to the current workspace."""
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        stmt = select(CaseTag).where(
            CaseTag.owner_id == workspace_id,
            CaseTag.id == tag_id,
        )
        result = await self.session.exec(stmt)
        return result.one()

    async def get_tag_by_ref(self, ref: str) -> CaseTag:
        """Get a case tag by its slug reference."""
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        stmt = select(CaseTag).where(
            CaseTag.owner_id == workspace_id,
            CaseTag.ref == ref,
        )
        result = await self.session.exec(stmt)
        return result.one()

    async def get_tag_by_ref_or_id(self, tag_identifier: str) -> CaseTag:
        """Get a case tag by either UUID or slug."""
        try:
            tag_uuid = uuid.UUID(tag_identifier)
        except ValueError:
            return await self.get_tag_by_ref(tag_identifier)

        try:
            return await self.get_tag(tag_uuid)
        except NoResultFound:
            return await self.get_tag_by_ref(tag_identifier)

    async def create_tag(self, params: TagCreate) -> CaseTag:
        """Create a new case tag in the current workspace."""
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        ref = slugify(params.name)

        existing = await self.session.exec(
            select(CaseTag).where(CaseTag.owner_id == workspace_id, CaseTag.ref == ref)
        )
        if existing.one_or_none():
            raise ValueError(f"Case tag with slug '{ref}' already exists")

        tag = CaseTag(
            name=params.name,
            ref=ref,
            owner_id=workspace_id,
            color=params.color,
        )
        self.session.add(tag)
        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    async def update_tag(self, tag: CaseTag, params: TagUpdate) -> CaseTag:
        """Update an existing case tag."""
        if params.name and params.name != tag.name:
            new_ref = slugify(params.name)
            if new_ref != tag.ref:
                owner_id = tag.owner_id
                if owner_id is None:
                    raise ValueError("Case tag owner is required")

                existing = await self.session.exec(
                    select(CaseTag).where(
                        CaseTag.owner_id == owner_id,
                        CaseTag.ref == new_ref,
                        CaseTag.id != tag.id,
                    )
                )
                if existing.one_or_none():
                    raise ValueError(f"Case tag with slug '{new_ref}' already exists")

                tag.ref = new_ref

        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(tag, key, value)

        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    async def delete_tag(self, tag: CaseTag) -> None:
        """Delete a case tag definition."""
        await self.session.delete(tag)
        await self.session.commit()

    async def list_tags_for_case(self, case_id: uuid.UUID) -> Sequence[CaseTag]:
        """List all tags attached to a specific case."""
        onclause = cast(ColumnElement[bool], CaseTagLink.tag_id == CaseTag.id)
        condition = cast(ColumnElement[bool], CaseTagLink.case_id == case_id)
        stmt = select(CaseTag).join(CaseTagLink, onclause).where(condition)
        result = await self.session.exec(stmt)
        return result.all()

    async def get_case_tag(
        self, case_id: uuid.UUID, tag_id: CaseTagID
    ) -> CaseTagLink | None:
        """Get a case tag association."""
        stmt = select(CaseTagLink).where(
            CaseTagLink.case_id == case_id,
            CaseTagLink.tag_id == tag_id,
        )
        result = await self.session.exec(stmt)
        return result.one_or_none()

    async def add_case_tag(self, case_id: uuid.UUID, tag_identifier: str) -> CaseTag:
        """Add a tag to a case by ID or ref."""
        # Resolve tag identifier to ID
        tag = await self.get_tag_by_ref_or_id(tag_identifier)

        # Check if already exists
        stmt = select(CaseTagLink).where(
            CaseTagLink.case_id == case_id,
            CaseTagLink.tag_id == tag.id,
        )
        result = await self.session.exec(stmt)
        existing = result.one_or_none()

        if existing:
            return tag  # Already exists, return tag

        # Create new
        case_tag = CaseTagLink(case_id=case_id, tag_id=tag.id)
        self.session.add(case_tag)

        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    async def remove_case_tag(self, case_id: uuid.UUID, tag_identifier: str) -> None:
        """Remove a tag from a case by ID or ref."""
        tag = await self.get_tag_by_ref_or_id(tag_identifier)

        case_tag = await self.get_case_tag(case_id, tag.id)
        if not case_tag:
            raise ValueError(f"Tag {tag_identifier} not found on case {case_id}")
        await self.session.delete(case_tag)
        await self.session.commit()
