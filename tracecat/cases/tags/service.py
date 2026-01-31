import uuid
from collections.abc import Sequence

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound

from tracecat.cases.durations.service import CaseDurationService
from tracecat.cases.enums import CaseEventType
from tracecat.db.models import Case, CaseEvent, CaseTag, CaseTagLink
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers import CaseTagID
from tracecat.service import BaseWorkspaceService
from tracecat.tags.schemas import TagCreate, TagUpdate


class CaseTagsService(BaseWorkspaceService):
    service_name = "case_tags"

    async def _get_case(self, case_id: uuid.UUID) -> Case:
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        stmt = select(Case).where(
            Case.workspace_id == workspace_id,
            Case.id == case_id,
        )
        result = await self.session.execute(stmt)
        case = result.scalar_one_or_none()
        if case is None:
            raise TracecatNotFoundError(f"Case {case_id} not found in this workspace.")
        return case

    async def _create_tag_event(
        self,
        *,
        case: Case,
        tag: CaseTag,
        event_type: CaseEventType,
    ) -> None:
        event = CaseEvent(
            workspace_id=self.workspace_id,
            case_id=case.id,
            type=event_type,
            data={
                "tag_id": str(tag.id),
                "tag_ref": tag.ref,
                "tag_name": tag.name,
            },
            user_id=self.role.user_id,
        )
        self.session.add(event)
        await self.session.flush()

        # Auto-sync durations after creating an event
        durations_service = CaseDurationService(session=self.session, role=self.role)
        await durations_service.sync_case_durations(case)

    async def list_workspace_tags(self) -> Sequence[CaseTag]:
        """List all tags available in the current workspace."""
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        stmt = select(CaseTag).where(CaseTag.workspace_id == workspace_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_tag(self, tag_id: CaseTagID) -> CaseTag:
        """Get a case tag by ID scoped to the current workspace."""
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        stmt = select(CaseTag).where(
            CaseTag.workspace_id == workspace_id,
            CaseTag.id == tag_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_tag_by_ref(self, ref: str) -> CaseTag:
        """Get a case tag by its slug reference."""
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        stmt = select(CaseTag).where(
            CaseTag.workspace_id == workspace_id,
            CaseTag.ref == ref,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_tag_by_ref_or_id(self, tag_identifier: str) -> CaseTag:
        """Get a case tag by either UUID or slug; gracefully handle free-form names."""
        try:
            tag_uuid = uuid.UUID(tag_identifier)
        except ValueError:
            candidates: list[str] = []
            if tag_identifier:
                candidates.append(tag_identifier)
                slug = slugify(tag_identifier)
                if slug and slug not in candidates:
                    candidates.append(slug)
            else:
                candidates.append(tag_identifier)

            last_error: NoResultFound | None = None
            for candidate in candidates:
                try:
                    return await self.get_tag_by_ref(candidate)
                except NoResultFound as exc:
                    last_error = exc
                    continue

            checked = ", ".join([repr(candidate) for candidate in candidates])
            raise NoResultFound(
                f"Case tag '{tag_identifier}' not found. Checked refs: {checked}. "
                "Ensure the tag exists or provide its UUID."
            ) from last_error

        try:
            return await self.get_tag(tag_uuid)
        except NoResultFound as exc:
            raise NoResultFound(
                f"Case tag ID '{tag_identifier}' not found in this workspace."
            ) from exc

    async def create_tag(self, params: TagCreate) -> CaseTag:
        """Create a new case tag in the current workspace."""
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        ref = slugify(params.name)

        existing = await self.session.execute(
            select(CaseTag).where(
                CaseTag.workspace_id == workspace_id, CaseTag.ref == ref
            )
        )
        if existing.one_or_none():
            raise ValueError(f"Case tag with slug '{ref}' already exists")

        tag = CaseTag(
            name=params.name,
            ref=ref,
            workspace_id=workspace_id,
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
                workspace_id = tag.workspace_id
                if workspace_id is None:
                    raise ValueError("Case tag workspace is required")

                existing = await self.session.execute(
                    select(CaseTag).where(
                        CaseTag.workspace_id == workspace_id,
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
        onclause = CaseTagLink.tag_id == CaseTag.id
        condition = CaseTagLink.case_id == case_id
        stmt = select(CaseTag).join(CaseTagLink, onclause).where(condition)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_case_tag(
        self, case_id: uuid.UUID, tag_id: CaseTagID
    ) -> CaseTagLink | None:
        """Get a case tag association."""
        stmt = select(CaseTagLink).where(
            CaseTagLink.case_id == case_id,
            CaseTagLink.tag_id == tag_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_case_tag(
        self,
        case_id: uuid.UUID,
        tag_identifier: str,
        *,
        create_if_missing: bool = False,
    ) -> CaseTag:
        """Add a tag to a case by ID or ref.

        Args:
            case_id: The case UUID.
            tag_identifier: The tag ID (UUID), ref, or name.
            create_if_missing: If True, create the tag if it doesn't exist.

        Returns:
            The tag that was added.
        """
        case = await self._get_case(case_id)

        # Resolve tag identifier to ID
        try:
            tag = await self.get_tag_by_ref_or_id(tag_identifier)
        except NoResultFound:
            if not create_if_missing:
                raise
            # Create the tag
            tag = await self.create_tag(TagCreate(name=tag_identifier))

        # Check if already exists
        stmt = select(CaseTagLink).where(
            CaseTagLink.case_id == case_id,
            CaseTagLink.tag_id == tag.id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            return tag  # Already exists, return tag

        # Create new
        case_tag = CaseTagLink(case_id=case_id, tag_id=tag.id)
        self.session.add(case_tag)

        await self._create_tag_event(
            case=case, tag=tag, event_type=CaseEventType.TAG_ADDED
        )

        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    async def remove_case_tag(self, case_id: uuid.UUID, tag_identifier: str) -> None:
        """Remove a tag from a case by ID or ref."""
        case = await self._get_case(case_id)
        tag = await self.get_tag_by_ref_or_id(tag_identifier)

        case_tag = await self.get_case_tag(case_id, tag.id)
        if not case_tag:
            raise ValueError(f"Tag {tag_identifier} not found on case {case_id}")
        await self.session.delete(case_tag)
        await self._create_tag_event(
            case=case, tag=tag, event_type=CaseEventType.TAG_REMOVED
        )
        await self.session.commit()
