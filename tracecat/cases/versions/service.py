"""Persistence service for immutable case text-field versions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.cases.enums import CaseVersionField
from tracecat.cases.versions.schemas import (
    CaseVersionActorRead,
    CaseVersionCompareRead,
    CaseVersionContentRead,
    CaseVersionReadMinimal,
)
from tracecat.db.models import Case, CaseVersion, User
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import (
    CursorPaginatedResponse,
    PageParams,
    paginate,
)
from tracecat.service import BaseWorkspaceService


class CaseVersionsService(BaseWorkspaceService):
    """Manage immutable versions for case titles and descriptions."""

    service_name = "case_versions"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self._user_id_resolved = False
        self._user_id: uuid.UUID | None = None

    async def _resolve_user_id(self) -> uuid.UUID | None:
        """Return the actor only when it references a persisted user."""
        if self._user_id_resolved:
            return self._user_id
        if self.role.user_id is not None:
            user = await self.session.get(User, self.role.user_id)
            self._user_id = user.id if user is not None else None
        self._user_id_resolved = True
        return self._user_id

    async def lock_case(self, case_id: uuid.UUID) -> None:
        """Lock a case row to serialize versioned field writes."""
        statement = (
            select(Case.id)
            .where(
                Case.workspace_id == self.workspace_id,
                Case.id == case_id,
            )
            .with_for_update()
        )
        if (await self.session.execute(statement)).scalar_one_or_none() is None:
            raise TracecatNotFoundError(f"Case '{case_id}' not found")

    async def create_initial_versions(
        self,
        *,
        case_id: uuid.UUID,
        summary: str,
        description: str,
    ) -> None:
        """Insert the initial case text versions without allocation queries."""
        user_id = await self._resolve_user_id()
        self.session.add_all(
            [
                CaseVersion(
                    workspace_id=self.workspace_id,
                    case_id=case_id,
                    field=CaseVersionField.SUMMARY,
                    version=1,
                    content=summary,
                    user_id=user_id,
                ),
                CaseVersion(
                    workspace_id=self.workspace_id,
                    case_id=case_id,
                    field=CaseVersionField.DESCRIPTION,
                    version=1,
                    content=description,
                    user_id=user_id,
                ),
            ]
        )
        await self.session.flush()

    async def append_version(
        self,
        *,
        case_id: uuid.UUID,
        field: CaseVersionField,
        content: str,
    ) -> CaseVersion:
        """Append and flush the next version while the caller holds the case lock."""
        statement = (
            select(CaseVersion.version)
            .where(
                CaseVersion.workspace_id == self.workspace_id,
                CaseVersion.case_id == case_id,
                CaseVersion.field == field,
            )
            .order_by(CaseVersion.version.desc())
            .limit(1)
        )
        latest_version = (await self.session.execute(statement)).scalar_one_or_none()
        version = CaseVersion(
            workspace_id=self.workspace_id,
            case_id=case_id,
            field=field,
            version=(latest_version or 0) + 1,
            content=content,
            user_id=await self._resolve_user_id(),
        )
        self.session.add(version)
        await self.session.flush()
        return version

    @require_scope("case:read")
    async def list_versions(
        self,
        *,
        case_id: uuid.UUID,
        page: PageParams,
        field: CaseVersionField | None = None,
    ) -> CursorPaginatedResponse[CaseVersionReadMinimal]:
        """List case version metadata newest-first without loading content."""
        latest = CaseVersion.__table__.alias("latest_case_version")
        latest_version = (
            select(func.max(latest.c.version))
            .where(
                latest.c.workspace_id == self.workspace_id,
                latest.c.case_id == case_id,
                latest.c.field == CaseVersion.field,
            )
            .correlate(CaseVersion)
            .scalar_subquery()
        )
        user = User.__table__
        statement = (
            select(
                CaseVersion.id,
                CaseVersion.field,
                CaseVersion.version,
                CaseVersion.created_at,
                CaseVersion.version == latest_version,
                user.c.id,
                user.c.email,
                user.c.first_name,
                user.c.last_name,
            )
            .outerjoin(user, user.c.id == CaseVersion.user_id)
            .where(
                CaseVersion.workspace_id == self.workspace_id,
                CaseVersion.case_id == case_id,
            )
        )
        if field is not None:
            statement = statement.where(CaseVersion.field == field)

        result = await paginate(
            self.session,
            statement,
            page=page,
            order_by=(
                CaseVersion.created_at.desc(),
                CaseVersion.surrogate_id.desc(),
            ),
            row_factory=self._version_metadata_from_row,
        )
        return CursorPaginatedResponse(
            items=result.items,
            next_cursor=result.next_cursor,
            prev_cursor=result.prev_cursor,
            has_more=result.has_more,
            has_previous=result.has_previous,
        )

    @staticmethod
    def _version_metadata_from_row(
        values: tuple[object, ...],
    ) -> CaseVersionReadMinimal:
        """Build list metadata from the projected version and actor columns."""
        actor_id = cast(uuid.UUID | None, values[5])
        actor = None
        if actor_id is not None:
            actor = CaseVersionActorRead(
                id=actor_id,
                email=cast(str, values[6]),
                first_name=cast(str | None, values[7]),
                last_name=cast(str | None, values[8]),
            )
        return CaseVersionReadMinimal(
            id=cast(uuid.UUID, values[0]),
            field=cast(CaseVersionField, values[1]),
            version=cast(int, values[2]),
            created_at=cast(datetime, values[3]),
            is_latest=cast(bool, values[4]),
            actor=actor,
        )

    @require_scope("case:read")
    async def get_version(
        self,
        *,
        case_id: uuid.UUID,
        version_id: uuid.UUID,
    ) -> CaseVersion | None:
        """Load a version only when its workspace and parent case match."""
        statement = select(CaseVersion).where(
            CaseVersion.workspace_id == self.workspace_id,
            CaseVersion.case_id == case_id,
            CaseVersion.id == version_id,
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    @require_scope("case:read")
    async def compare_with_predecessor(
        self,
        *,
        case_id: uuid.UUID,
        version_id: uuid.UUID,
    ) -> CaseVersionCompareRead | None:
        """Return one scoped version and its immediate same-field predecessor."""
        selected = aliased(CaseVersion, name="selected_case_version")
        predecessor = aliased(CaseVersion, name="predecessor_case_version")
        statement = (
            select(selected, predecessor)
            .outerjoin(
                predecessor,
                (predecessor.workspace_id == selected.workspace_id)
                & (predecessor.case_id == selected.case_id)
                & (predecessor.field == selected.field)
                & (predecessor.version == selected.version - 1),
            )
            .where(
                selected.workspace_id == self.workspace_id,
                selected.case_id == case_id,
                selected.id == version_id,
            )
        )
        row = (await self.session.execute(statement)).one_or_none()
        if row is None:
            return None
        selected_version = cast(CaseVersion, row[0])
        predecessor_version = cast(CaseVersion | None, row[1])
        selected_read = CaseVersionContentRead.model_validate(selected_version)
        predecessor_read = (
            CaseVersionContentRead.model_validate(predecessor_version)
            if predecessor_version is not None
            else None
        )
        return CaseVersionCompareRead(
            selected=selected_read,
            predecessor=predecessor_read,
        )
