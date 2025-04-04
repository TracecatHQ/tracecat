import uuid
from collections.abc import Sequence

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.models import CaseCreate, CaseUpdate
from tracecat.db.schemas import Case
from tracecat.service import BaseService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatAuthorizationError


class CasesService(BaseService):
    service_name = "cases"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        if self.role.workspace_id is None:
            raise TracecatAuthorizationError("Cases service requires workspace")
        self.workspace_id = self.role.workspace_id

    """Management"""

    async def list_cases(self) -> Sequence[Case]:
        statement = select(Case).where(Case.owner_id == self.workspace_id)
        result = await self.session.exec(statement)
        return result.all()

    async def get_case(self, case_id: uuid.UUID) -> Case | None:
        statement = select(Case).where(
            Case.owner_id == self.workspace_id,
            Case.id == case_id,
        )
        result = await self.session.exec(statement)
        return result.first()

    async def create_case(self, params: CaseCreate) -> Case:
        db_case = Case(
            owner_id=self.workspace_id,
            summary=params.summary,
            description=params.description,
            priority=params.priority,
            severity=params.severity,
        )
        self.session.add(db_case)
        await self.session.commit()
        return db_case

    async def update_case(self, case: Case, params: CaseUpdate) -> Case:
        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(case, key, value)
        await self.session.commit()
        await self.session.refresh(case)
        return case

    async def delete_case(self, case: Case) -> None:
        await self.session.delete(case)
        await self.session.commit()
