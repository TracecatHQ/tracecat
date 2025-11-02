from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.exc import MultipleResultsFound, NoResultFound
from sqlmodel import col, select

from tracecat.db.models import WorkspaceVariable
from tracecat.identifiers import VariableID
from tracecat.service import BaseWorkspaceService
from tracecat.types.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.variables.schemas import (
    VariableCreate,
    VariableSearch,
    VariableUpdate,
)


class VariablesService(BaseWorkspaceService):
    """Workspace variables service."""

    service_name = "variables"

    async def list_variables(
        self, *, environment: str | None = None
    ) -> Sequence[WorkspaceVariable]:
        statement = select(WorkspaceVariable).where(
            WorkspaceVariable.owner_id == self.workspace_id
        )
        if environment is not None:
            statement = statement.where(WorkspaceVariable.environment == environment)
        result = await self.session.exec(statement)
        return result.all()

    async def search_variables(
        self, params: VariableSearch
    ) -> Sequence[WorkspaceVariable]:
        statement = select(WorkspaceVariable).where(
            WorkspaceVariable.owner_id == self.workspace_id
        )

        if params.environment is not None:
            statement = statement.where(
                WorkspaceVariable.environment == params.environment
            )
        if params.names:
            statement = statement.where(col(WorkspaceVariable.name).in_(params.names))
        if params.ids:
            statement = statement.where(col(WorkspaceVariable.id).in_(params.ids))

        result = await self.session.exec(statement)
        return result.all()

    async def get_variable(self, variable_id: VariableID) -> WorkspaceVariable:
        statement = select(WorkspaceVariable).where(
            WorkspaceVariable.owner_id == self.workspace_id,
            WorkspaceVariable.id == variable_id,
        )
        result = await self.session.exec(statement)
        try:
            return result.one()
        except MultipleResultsFound as exc:
            self.logger.error(
                "Multiple variables found with ID",
                variable_id=variable_id,
                workspace=self.workspace_id,
            )
            raise TracecatNotFoundError(
                "Multiple variables found when searching by ID"
            ) from exc
        except NoResultFound as exc:
            self.logger.warning(
                "Variable not found",
                variable_id=variable_id,
                workspace=self.workspace_id,
            )
            raise TracecatNotFoundError(
                "Variable not found when searching by ID. Please check that the ID was correctly input."
            ) from exc

    async def get_variable_by_name(
        self, name: str, *, environment: str | None = None
    ) -> WorkspaceVariable:
        statement = select(WorkspaceVariable).where(
            WorkspaceVariable.owner_id == self.workspace_id,
            WorkspaceVariable.name == name,
        )
        if environment is not None:
            statement = statement.where(WorkspaceVariable.environment == environment)
        result = await self.session.exec(statement)
        try:
            return result.one()
        except MultipleResultsFound as exc:
            self.logger.error(
                "Multiple variables found with name",
                variable_name=name,
                workspace=self.workspace_id,
                environment=environment,
            )
            raise TracecatNotFoundError(
                "Multiple variables found when searching by name."
                f" Expected one variable {name!r} (env: {environment!r}) only."
            ) from exc
        except NoResultFound as exc:
            self.logger.warning(
                "Variable not found",
                variable_name=name,
                workspace=self.workspace_id,
                environment=environment,
            )
            raise TracecatNotFoundError(
                f"Variable {name!r} (env: {environment!r}) not found when searching by name."
                " Please double check that the name was correctly input."
            ) from exc

    async def create_variable(self, params: VariableCreate) -> WorkspaceVariable:
        if self.workspace_id is None:
            raise TracecatAuthorizationError(
                "Workspace ID is required to create a workspace variable"
            )
        variable = WorkspaceVariable(
            owner_id=self.workspace_id,
            name=params.name,
            description=params.description,
            values=dict(params.values),
            environment=params.environment,
            tags=params.tags,
        )
        self.session.add(variable)
        await self.session.commit()
        await self.session.refresh(variable)
        return variable

    async def update_variable(
        self, variable: WorkspaceVariable, params: VariableUpdate
    ) -> WorkspaceVariable:
        update_fields = params.model_dump(exclude_unset=True)
        if values := update_fields.pop("values", None):
            variable.values = dict(values)
        for field, value in update_fields.items():
            setattr(variable, field, value)
        self.session.add(variable)
        await self.session.commit()
        await self.session.refresh(variable)
        return variable

    async def delete_variable(self, variable: WorkspaceVariable) -> None:
        await self.session.delete(variable)
        await self.session.commit()
