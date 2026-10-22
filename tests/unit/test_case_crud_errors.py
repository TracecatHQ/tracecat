from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import get_args
from unittest.mock import AsyncMock, MagicMock

import pytest
from asyncpg.exceptions import (
    DatatypeMismatchError,
    DuplicateColumnError,
    ForeignKeyViolationError,
    UndefinedColumnError,
    UniqueViolationError,
)
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.api.common import generic_exception_handler
from tracecat.auth.dependencies import ExecutorWorkspaceRole, WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.cases import internal_router as cases_internal
from tracecat.cases import router as cases_public
from tracecat.cases.attachments import internal_router as attachments_internal
from tracecat.cases.attachments import router as attachments_public
from tracecat.cases.attachments.service import CaseAttachmentService
from tracecat.cases.dropdowns import router as dropdown_routes
from tracecat.cases.dropdowns.service import CaseDropdownDefinitionsService
from tracecat.cases.durations import router as duration_routes
from tracecat.cases.durations.service import (
    CaseDurationDefinitionService,
    CaseDurationService,
)
from tracecat.cases.rows import internal_router as rows_internal
from tracecat.cases.rows import router as rows_public
from tracecat.cases.rows.exceptions import raise_case_row_link_integrity_error
from tracecat.cases.rows.service import CaseTableRowsService
from tracecat.cases.service import CaseFieldsService, CasesService, CaseTasksService
from tracecat.cases.tag_definitions import internal_router as definitions_internal
from tracecat.cases.tag_definitions import router as definitions_public
from tracecat.cases.tags import internal_router as tags_internal
from tracecat.cases.tags import router as tags_public
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.engine import get_async_session
from tracecat.db.models import (
    Case,
    CaseDropdownDefinition,
    CaseDropdownOption,
    CaseDuration,
    CaseDurationDefinition,
    CaseTask,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.service import BaseWorkspaceService
from tracecat.tables.exceptions import TableColumnConflictError, TableRowError

CASE_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
RESOURCE_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
PRIVATE_DIAGNOSTIC = "synthetic diagnostic: not found permission"


class NamedUniqueViolationError(UniqueViolationError):
    constraint_name: str


class NamedForeignKeyViolationError(ForeignKeyViolationError):
    constraint_name: str


def _integrity_error(constraint: str, *, unique: bool = True) -> IntegrityError:
    error = (
        NamedUniqueViolationError(PRIVATE_DIAGNOSTIC)
        if unique
        else NamedForeignKeyViolationError(PRIVATE_DIAGNOSTIC)
    )
    error.constraint_name = constraint
    return IntegrityError("synthetic statement", {}, error)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, MagicMock]]:
    app = FastAPI()
    app.add_exception_handler(Exception, generic_exception_handler)
    for router in (
        cases_public.cases_router,
        cases_public.case_fields_router,
        cases_internal.router,
        cases_internal.task_router,
        attachments_public.router,
        attachments_internal.router,
        dropdown_routes.definitions_router,
        duration_routes.definitions_router,
        duration_routes.durations_router,
        definitions_public.router,
        definitions_internal.router,
        tags_public.router,
        tags_internal.router,
        rows_public.router,
        rows_internal.router,
    ):
        app.include_router(router)
    role = Role(
        type="service",
        service_id="tracecat-runner",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        scopes=frozenset(
            {"case:read", "case:create", "case:update", "case:delete", "table:create"}
        ),
    )
    for role_type in (WorkspaceActorRouteRole, ExecutorWorkspaceRole):
        app.dependency_overrides[get_args(role_type)[1].dependency] = lambda: role
    session = MagicMock(spec=AsyncSession)
    app.dependency_overrides[get_async_session] = lambda: session
    monkeypatch.setattr(BaseWorkspaceService, "require_entitlement", AsyncMock())
    monkeypatch.setattr(
        CasesService, "get_case", AsyncMock(return_value=Case(id=CASE_ID))
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, session


@pytest.mark.parametrize(
    ("method", "path", "operation"),
    [
        (
            "GET",
            f"/cases/{CASE_ID}/attachments/{RESOURCE_ID}",
            "get_attachment_download_url",
        ),
        ("DELETE", f"/cases/{CASE_ID}/attachments/{RESOURCE_ID}", "delete_attachment"),
        (
            "GET",
            f"/internal/cases/{CASE_ID}/attachments/{RESOURCE_ID}",
            "get_attachment_download_url",
        ),
        (
            "GET",
            f"/internal/cases/{CASE_ID}/attachments/{RESOURCE_ID}/url",
            "get_attachment_download_url",
        ),
        (
            "DELETE",
            f"/internal/cases/{CASE_ID}/attachments/{RESOURCE_ID}",
            "delete_attachment",
        ),
    ],
)
@pytest.mark.parametrize("error_kind", ["missing", "forbidden", "unexpected"])
def test_attachment_errors_are_typed_and_sanitized(
    api: tuple[TestClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    operation: str,
    error_kind: str,
) -> None:
    client, _ = api
    error: Exception
    if error_kind == "missing":
        error, expected = TracecatNotFoundError("Attachment not found"), 404
    elif error_kind == "forbidden":
        error, expected = TracecatAuthorizationError("Access denied"), 403
    else:
        error, expected = _integrity_error("synthetic_fk", unique=False), 500
    monkeypatch.setattr(CaseAttachmentService, operation, AsyncMock(side_effect=error))
    response = client.request(method, path)
    assert response.status_code == expected
    assert PRIVATE_DIAGNOSTIC not in response.text


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "error_kind",
    ["missing", "duplicate", "unexpected", "state_conflict", "invalid_type"],
)
def test_case_field_ddl_errors(
    api: tuple[TestClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    error_kind: str,
) -> None:
    client, _ = api
    if error_kind == "missing":
        driver_error, expected = UndefinedColumnError(PRIVATE_DIAGNOSTIC), 404
    elif error_kind == "duplicate":
        driver_error, expected = DuplicateColumnError(PRIVATE_DIAGNOSTIC), 409
    elif error_kind == "invalid_type":
        driver_error, expected = DatatypeMismatchError(PRIVATE_DIAGNOSTIC), 400
    else:
        driver_error, expected = ForeignKeyViolationError(PRIVATE_DIAGNOSTIC), 500
    if error_kind == "state_conflict":
        error = TableColumnConflictError(
            "Existing values conflict with the column type"
        )
        expected = 409
    else:
        error = ProgrammingError("synthetic statement", {}, driver_error)
    operation = {
        "POST": "create_field",
        "PATCH": "update_field",
        "DELETE": "delete_field",
    }[method]
    monkeypatch.setattr(CaseFieldsService, operation, AsyncMock(side_effect=error))
    path = "/case-fields" if method == "POST" else "/case-fields/synthetic"
    response = client.request(method, path, json={"name": "renamed", "type": "TEXT"})
    assert response.status_code == expected
    assert PRIVATE_DIAGNOSTIC not in response.text


@pytest.mark.parametrize(
    ("method", "path", "operation", "association"),
    [
        ("POST", "/case-tags", "create_tag", False),
        ("PATCH", f"/case-tags/{RESOURCE_ID}", "update_tag", False),
        ("POST", "/internal/case-tags", "create_tag", False),
        ("POST", f"/cases/{CASE_ID}/tags", "add_case_tag", True),
        ("POST", f"/internal/cases/{CASE_ID}/tags", "add_case_tag", True),
    ],
)
@pytest.mark.parametrize("error_kind", ["name", "ref", "foreign_key", "other_unique"])
def test_case_tag_integrity_classification(
    api: tuple[TestClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    operation: str,
    association: bool,
    error_kind: str,
) -> None:
    client, session = api
    constraint = (
        "pk_case_tag_link" if association else f"uq_case_tag_{error_kind}_workspace"
    )
    known = error_kind in {"name", "ref"}
    if error_kind == "other_unique":
        constraint = "synthetic_other_unique"
    error = _integrity_error(constraint, unique=error_kind != "foreign_key")
    monkeypatch.setattr(CaseTagsService, operation, AsyncMock(side_effect=error))
    body = {"tag_id": str(RESOURCE_ID)} if association else {"name": "Synthetic tag"}
    response = client.request(method, path, json=body)
    expected = (409 if association else 400) if known else 500
    assert response.status_code == expected
    assert PRIVATE_DIAGNOSTIC not in response.text
    session.rollback.assert_awaited_once()


@pytest.mark.parametrize(
    "operation",
    [
        "create",
        "create_at_commit",
        "create_option_at_commit",
        "update",
        "add_option",
        "update_option",
    ],
)
@pytest.mark.parametrize("error_kind", ["known", "foreign_key", "other_unique"])
def test_dropdown_integrity_classification(
    api: tuple[TestClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    error_kind: str,
) -> None:
    client, session = api
    definition = CaseDropdownDefinition(
        id=RESOURCE_ID, name="Synthetic", ref="synthetic"
    )
    option = CaseDropdownOption(id=RESOURCE_ID, ref="synthetic", label="Synthetic")
    monkeypatch.setattr(
        CaseDropdownDefinitionsService,
        "get_definition",
        AsyncMock(return_value=definition),
    )
    monkeypatch.setattr(
        CaseDropdownDefinitionsService, "_get_option", AsyncMock(return_value=option)
    )
    option_operation = "option" in operation
    constraint = (
        "uq_case_dropdown_option_ref_definition"
        if option_operation
        else "uq_case_dropdown_definition_ref_workspace"
    )
    if error_kind == "other_unique":
        constraint = "synthetic_other_unique"
    error = _integrity_error(constraint, unique=error_kind != "foreign_key")
    session.commit.side_effect = error
    body: dict[str, str]
    if operation.startswith("create"):
        if operation == "create":
            session.flush.side_effect = error
        method, path, body = (
            "POST",
            "/case-dropdowns",
            {"name": "Synthetic", "ref": "synthetic"},
        )
    elif operation == "update":
        method, path, body = (
            "PATCH",
            f"/case-dropdowns/{RESOURCE_ID}",
            {"ref": "renamed"},
        )
    elif operation == "add_option":
        method, path, body = (
            "POST",
            f"/case-dropdowns/{RESOURCE_ID}/options",
            {"label": "Synthetic", "ref": "synthetic"},
        )
    else:
        method, path, body = (
            "PATCH",
            f"/case-dropdowns/{RESOURCE_ID}/options/{RESOURCE_ID}",
            {"ref": "renamed"},
        )
    payload = (
        {**body, "options": [{"label": "Synthetic", "ref": "synthetic"}]}
        if operation == "create_option_at_commit"
        else body
    )
    response = client.request(method, path, json=payload)
    assert response.status_code == (409 if error_kind == "known" else 500)
    assert PRIVATE_DIAGNOSTIC not in response.text
    session.rollback.assert_awaited_once()


@pytest.mark.parametrize(
    "operation",
    [
        "create_definition",
        "create_definition_backfill",
        "update_definition",
        "update_definition_backfill",
        "create_duration",
        "update_duration",
    ],
)
@pytest.mark.parametrize("error_kind", ["known", "foreign_key", "other_unique"])
def test_duration_uniqueness_races(
    api: tuple[TestClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    error_kind: str,
) -> None:
    client, session = api
    definition = CaseDurationDefinition(
        id=RESOURCE_ID,
        name="Synthetic",
        start_event_type="case_created",
        end_event_type="case_closed",
    )
    duration = CaseDuration(id=RESOURCE_ID, case_id=CASE_ID, definition_id=RESOURCE_ID)
    monkeypatch.setattr(
        CaseDurationDefinitionService,
        "_get_definition_entity",
        AsyncMock(return_value=definition),
    )
    monkeypatch.setattr(
        CaseDurationDefinitionService, "_ensure_unique_name", AsyncMock()
    )
    monkeypatch.setattr(
        CaseDurationDefinitionService, "_backfill_after_definition_change", AsyncMock()
    )
    monkeypatch.setattr(CaseDurationDefinitionService, "get_definition", AsyncMock())
    monkeypatch.setattr(
        CaseDurationService, "_resolve_case", AsyncMock(return_value=Case(id=CASE_ID))
    )
    monkeypatch.setattr(
        CaseDurationService,
        "_get_case_duration_entity",
        AsyncMock(return_value=duration),
    )
    monkeypatch.setattr(
        CaseDurationService, "_ensure_unique_case_duration", AsyncMock()
    )
    constraint = (
        "uq_case_duration_definition_workspace_name"
        if "definition" in operation
        else "uq_case_duration_case_definition"
    )
    if error_kind == "other_unique":
        constraint = "synthetic_other_unique"
    session.commit.side_effect = _integrity_error(
        constraint, unique=error_kind != "foreign_key"
    )
    if operation.endswith("_backfill"):
        monkeypatch.setattr(
            CaseDurationDefinitionService,
            "_backfill_after_definition_change",
            AsyncMock(side_effect=session.commit.side_effect),
        )
    if operation.startswith("create_definition"):
        method, path, body = (
            "POST",
            "/case-durations",
            {
                "name": "Synthetic",
                "start_anchor": {"event_type": "case_created"},
                "end_anchor": {"event_type": "case_closed"},
            },
        )
    elif operation.startswith("update_definition"):
        method, path, body = (
            "PATCH",
            f"/case-durations/{RESOURCE_ID}",
            {"name": "Renamed"},
        )
    elif operation == "create_duration":
        method, path, body = (
            "POST",
            f"/cases/{CASE_ID}/durations",
            {"definition_id": str(RESOURCE_ID)},
        )
    else:
        method, path, body = (
            "PATCH",
            f"/cases/{CASE_ID}/durations/{RESOURCE_ID}",
            {"definition_id": str(RESOURCE_ID)},
        )
    response = client.request(method, path, json=body)
    assert response.status_code == (400 if error_kind == "known" else 500)
    assert PRIVATE_DIAGNOSTIC not in response.text
    session.rollback.assert_awaited_once()


@pytest.mark.parametrize("internal", [False, True])
@pytest.mark.parametrize("expected", [400, 500])
def test_case_row_insert_errors(
    api: tuple[TestClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    internal: bool,
    expected: int,
) -> None:
    client, _ = api
    monkeypatch.setattr(
        CaseTableRowsService,
        "get_case_or_raise",
        AsyncMock(return_value=Case(id=CASE_ID)),
    )
    error = (
        TableRowError("invalid_value", "Row values do not match the table columns.")
        if expected == 400
        else _integrity_error("synthetic_other_unique")
    )
    monkeypatch.setattr(
        CaseTableRowsService, "insert_row_to_case", AsyncMock(side_effect=error)
    )
    prefix = "/internal" if internal else ""
    response = client.post(
        f"{prefix}/cases/{CASE_ID}/rows/insert",
        json={"table_id": str(RESOURCE_ID), "row": {"data": {"synthetic": "value"}}},
    )
    assert response.status_code == expected
    assert PRIVATE_DIAGNOSTIC not in response.text
    if expected == 400:
        assert response.json()["detail"]["code"] == "invalid_value"


@pytest.mark.parametrize(
    ("internal", "operation"),
    [
        (False, "create"),
        (False, "update"),
        (False, "delete"),
        (True, "create"),
        (True, "update"),
        (True, "delete"),
        ("task_id_only", "update"),
        ("task_id_only", "delete"),
    ],
)
@pytest.mark.parametrize("error_kind", ["missing", "unexpected", "invalid"])
def test_task_failures_preserve_not_found_and_unexpected_errors(
    api: tuple[TestClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    internal: bool | str,
    operation: str,
    error_kind: str,
) -> None:
    client, _ = api
    error = (
        TracecatNotFoundError("Task not found")
        if error_kind == "missing"
        else TracecatValidationError("Invalid task values")
        if error_kind == "invalid"
        else _integrity_error("synthetic_fk", unique=False)
    )
    monkeypatch.setattr(
        CaseTasksService, f"{operation}_task", AsyncMock(side_effect=error)
    )
    monkeypatch.setattr(
        CaseTasksService,
        "get_task",
        AsyncMock(return_value=CaseTask(id=RESOURCE_ID, case_id=CASE_ID)),
    )
    path = f"{'/internal' if internal else ''}/cases/{CASE_ID}/tasks"
    method = {"create": "POST", "update": "PATCH", "delete": "DELETE"}[operation]
    if operation != "create":
        path += f"/{RESOURCE_ID}"
    if internal == "task_id_only":
        path = f"/internal/cases/tasks/{RESOURCE_ID}"
    response = client.request(method, path, json={"title": "Synthetic task"})
    expected = (
        404
        if error_kind == "missing"
        else 400
        if error_kind == "invalid" and operation != "delete"
        else 500
    )
    assert response.status_code == expected
    assert PRIVATE_DIAGNOSTIC not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize("error_kind", ["known", "foreign_key", "message_only"])
async def test_case_row_link_uses_typed_constraint(error_kind: str) -> None:
    session = MagicMock(spec=AsyncSession)
    error = _integrity_error(
        "uq_case_table_row_link", unique=error_kind != "foreign_key"
    )
    if error_kind == "message_only":
        error = IntegrityError(
            "uq_case_table_row_link", {}, Exception("uq_case_table_row_link")
        )
    if error_kind == "known":
        with pytest.raises(HTTPException) as raised:
            await raise_case_row_link_integrity_error(session, error)
        assert raised.value.status_code == 409
    else:
        with pytest.raises(IntegrityError) as raised_error:
            await raise_case_row_link_integrity_error(session, error)
        assert raised_error.value is error
    session.rollback.assert_awaited_once()
