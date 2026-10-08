"""Isolated batch uninstall checks; database locking is covered separately."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.skill.library.router import batch_uninstall_library_skills
from tracecat.agent.skill.library.schemas import LibrarySkillBatchUninstall
from tracecat.agent.skill.library.service import SkillLibraryService
from tracecat.auth.types import Role
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatNotFoundError,
    TracecatValidationError,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def batch_session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.scalars.return_value = MagicMock()
    session.scalars.return_value.all.return_value = ["a-unused", "b-bound", "c-unused"]
    session.execute.return_value = MagicMock()
    session.execute.return_value.tuples.return_value.all.return_value = []
    return session


@pytest.fixture
def service(batch_session: AsyncMock) -> SkillLibraryService:
    role = Role(
        type="user",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        scopes=frozenset({"agent:delete"}),
    )
    return SkillLibraryService(batch_session, role=role)


@pytest.mark.anyio
async def test_batch_rejects_bound_skill_before_any_delete(
    service: SkillLibraryService,
    batch_session: AsyncMock,
) -> None:
    session = batch_session
    session.execute.return_value.tuples.return_value.all.return_value = [
        ("analyst", "Analyst")
    ]

    with pytest.raises(TracecatValidationError) as exc:
        await service.uninstall_many(["a-unused", "b-bound", "c-unused"])

    assert exc.value.detail == {
        "code": "library_skill_in_use",
        "slugs": ["a-unused", "b-bound", "c-unused"],
        "presets": ["analyst"],
    }
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_batch_rejects_missing_install_before_any_delete(
    service: SkillLibraryService,
    batch_session: AsyncMock,
) -> None:
    batch_session.scalars.return_value.all.return_value = ["a-unused"]

    with pytest.raises(TracecatNotFoundError):
        await service.uninstall_many(["a-unused", "missing"])

    batch_session.execute.assert_not_awaited()
    batch_session.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_batch_locks_in_order_and_deletes_once_in_workspace(
    service: SkillLibraryService,
    batch_session: AsyncMock,
) -> None:
    await service.uninstall_many(["c-unused", "b-bound", "a-unused", "a-unused"])

    lock = batch_session.scalars.call_args.args[0]
    sql = str(lock.compile(dialect=postgresql.dialect()))
    assert "ORDER BY skill_library_install.library_slug FOR UPDATE" in sql
    assert lock.compile().params["workspace_id_1"] == service.workspace_id
    assert lock.compile().params["library_slug_1"] == [
        "a-unused",
        "b-bound",
        "c-unused",
    ]
    bound = batch_session.execute.call_args_list[0].args[0]
    assert "?| ARRAY[" in str(bound.compile(dialect=postgresql.dialect()))
    delete = batch_session.execute.call_args.args[0]
    assert str(delete).startswith("DELETE FROM skill_library_install")
    assert delete.compile().params["workspace_id_1"] == service.workspace_id
    batch_session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_batch_requires_delete_scope(
    service: SkillLibraryService,
    batch_session: AsyncMock,
) -> None:
    role = service.role.model_copy(update={"scopes": frozenset({"agent:read"})})
    with pytest.raises(ScopeDeniedError):
        await SkillLibraryService(service.session, role=role).uninstall_many(["triage"])
    batch_session.scalars.assert_not_awaited()


@pytest.mark.anyio
async def test_batch_route_returns_conflict_for_bound_skill(
    service: SkillLibraryService, monkeypatch: pytest.MonkeyPatch
) -> None:
    uninstall = AsyncMock(
        side_effect=TracecatValidationError(
            "No skills were uninstalled.", detail={"code": "library_skill_in_use"}
        )
    )
    monkeypatch.setattr(SkillLibraryService, "uninstall_many", uninstall)

    with pytest.raises(HTTPException) as exc:
        await batch_uninstall_library_skills(
            role=service.role,
            session=service.session,
            params=LibrarySkillBatchUninstall(slugs=["triage", "hunt"]),
        )

    assert exc.value.status_code == 409
    uninstall.assert_awaited_once_with(["triage", "hunt"])
