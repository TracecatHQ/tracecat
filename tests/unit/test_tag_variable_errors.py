"""Typed database failure handling for workspace tags and variables."""

import uuid
from typing import Literal, cast
from unittest.mock import AsyncMock, Mock

import pytest
from asyncpg import (
    ForeignKeyViolationError,
    NotNullViolationError,
    UniqueViolationError,
)
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from tracecat.auth.types import Role
from tracecat.db.models import WorkflowTag, WorkspaceVariable
from tracecat.db.types import PostgresConstraintError
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.tags.schemas import TagCreate, TagUpdate
from tracecat.tags.service import TagsService
from tracecat.variables.schemas import VariableCreate, VariableUpdate
from tracecat.variables.service import VariablesService

Domain = Literal["tags", "variables"]
Operation = Literal["create", "update", "delete"]


@pytest.fixture
def mutation_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"*"}),
    )


async def mutate(
    domain: Domain, operation: Operation, session: AsyncSession, role: Role
) -> None:
    if domain == "tags":
        service = TagsService(session, role)
        tag = WorkflowTag(
            name="Synthetic", ref="synthetic", workspace_id=role.workspace_id
        )
        if operation == "create":
            await service.create_tag(TagCreate(name="Synthetic"))
        elif operation == "update":
            await service.update_tag(tag, TagUpdate(name="Synthetic renamed"))
        else:
            await service.delete_tag(tag)
    else:
        service = VariablesService(session, role)
        variable = WorkspaceVariable(
            name="synthetic", values={"key": "value"}, workspace_id=role.workspace_id
        )
        if operation == "create":
            await service.create_variable(
                VariableCreate(name="synthetic", values={"key": "value"})
            )
        elif operation == "update":
            await service.update_variable(
                variable, VariableUpdate(name="synthetic_renamed")
            )
        else:
            await service.delete_variable(variable)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "domain,constraint",
    [
        ("tags", "uq_workflow_tag_name_workspace"),
        ("tags", "uq_workflow_tag_ref_workspace"),
        ("variables", "uq_workspace_variable_name_environment_workspace_id"),
    ],
)
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_known_unique_conflicts_rollback(
    domain: Domain, constraint: str, operation: Operation, mutation_role: Role
) -> None:
    pg_error = UniqueViolationError("synthetic private detail")
    cast(PostgresConstraintError, pg_error).constraint_name = constraint
    wrapper = Exception("synthetic driver wrapper")
    wrapper.__cause__ = pg_error
    error = IntegrityError("synthetic statement", {}, wrapper)
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.one_or_none.return_value = None
    session.execute.return_value = result
    session.commit.side_effect = error
    with pytest.raises(TracecatConflictError) as raised:
        await mutate(domain, operation, session, mutation_role)
    assert raised.value.__cause__ is error
    session.rollback.assert_awaited_once()
    session.refresh.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("domain", ["tags", "variables"])
@pytest.mark.parametrize("operation", ["create", "update", "delete"])
@pytest.mark.parametrize(
    "pg_type,constraint",
    [
        (UniqueViolationError, "uq_unrelated_synthetic"),
        (UniqueViolationError, None),
        (NotNullViolationError, "uq_workflow_tag_ref_workspace"),
        (
            ForeignKeyViolationError,
            "uq_workspace_variable_name_environment_workspace_id",
        ),
    ],
)
async def test_unrelated_integrity_errors_rollback_and_propagate(
    domain: Domain,
    operation: Operation,
    pg_type: type[UniqueViolationError]
    | type[NotNullViolationError]
    | type[ForeignKeyViolationError],
    constraint: str | None,
    mutation_role: Role,
) -> None:
    pg_error = pg_type("synthetic private detail")
    cast(PostgresConstraintError, pg_error).constraint_name = constraint
    error = IntegrityError("synthetic statement", {}, pg_error)
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.one_or_none.return_value = None
    session.execute.return_value = result
    session.commit.side_effect = error
    with pytest.raises(IntegrityError) as raised:
        await mutate(domain, operation, session, mutation_role)
    assert raised.value is error
    session.rollback.assert_awaited_once()
    session.refresh.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("domain", ["tags", "variables"])
async def test_concurrently_deleted_update_is_not_found(
    domain: Domain, mutation_role: Role
) -> None:
    session = AsyncMock(spec=AsyncSession)
    error = StaleDataError("synthetic deleted row")
    session.commit.side_effect = error
    expected = NoResultFound if domain == "tags" else TracecatNotFoundError
    with pytest.raises(expected) as raised:
        await mutate(domain, "update", session, mutation_role)
    assert raised.value.__cause__ is error
    session.rollback.assert_awaited_once()
