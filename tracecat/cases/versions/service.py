"""Persistence service for immutable case text-field versions."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.enums import CaseVersionField
from tracecat.db.models import Case, CaseVersion, User
from tracecat.exceptions import TracecatNotFoundError
from tracecat.service import BaseWorkspaceService


class CaseVersionsService(BaseWorkspaceService):
    """Create immutable versions for case titles and descriptions."""

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

    async def append_version(
        self,
        *,
        case_id: uuid.UUID,
        field: CaseVersionField,
        content: str,
    ) -> CaseVersion:
        """Append and flush the next version without committing."""
        await self.lock_case(case_id)
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
