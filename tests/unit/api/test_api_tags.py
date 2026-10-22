"""HTTP-level tests for tag and variable validation."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DataError, IntegrityError, NoResultFound

from tracecat.api import common
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.tags import router as tags_router
from tracecat.variables import internal_router as variables_internal_router
from tracecat.variables import router as variables_router


@pytest.fixture(params=[False, True], ids=["legacy", "workspace"])
def resource_prefix(request: pytest.FixtureRequest, test_admin_role: Role) -> str:
    return f"/workspaces/{test_admin_role.workspace_id}" if request.param else ""


@pytest.mark.anyio
async def test_duplicate_tag_maps_to_http_409(
    client: TestClient,
    test_admin_role: Role,
    resource_prefix: str,
) -> None:
    with patch.object(tags_router, "TagsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.create_tag.side_effect = TracecatConflictError(
            "Tag with this name already exists"
        )
        mock_service_cls.return_value = mock_service

        response = client.post(
            f"{resource_prefix}/tags",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"name": "Synthetic tag"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Tag with this name already exists"


@pytest.mark.anyio
async def test_missing_tag_update_maps_to_http_404(
    client: TestClient,
    test_admin_role: Role,
    resource_prefix: str,
) -> None:
    with patch.object(tags_router, "TagsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_tag.side_effect = NoResultFound
        mock_service_cls.return_value = mock_service

        response = client.patch(
            f"{resource_prefix}/tags/{uuid.uuid4()}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"name": "Synthetic renamed tag"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Tag not found"


@pytest.mark.anyio
async def test_invalid_variable_name_is_request_validation_error(
    client: TestClient,
    test_admin_role: Role,
    resource_prefix: str,
) -> None:
    response = client.get(
        f"{resource_prefix}/variables/search",
        params={
            "workspace_id": str(test_admin_role.workspace_id),
            "name": "invalid-variable-name",
        },
    )

    assert response.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize(
    "resource,method,operation",
    [
        ("tags", "POST", "create_tag"),
        ("tags", "PATCH", "update_tag"),
        ("variables", "POST", "create_variable"),
        ("variables", "POST", "update_variable"),
    ],
)
async def test_mutation_conflicts_are_constant(
    client: TestClient,
    test_admin_role: Role,
    resource_prefix: str,
    resource: str,
    method: str,
    operation: str,
) -> None:
    module = tags_router if resource == "tags" else variables_router
    service_name = "TagsService" if resource == "tags" else "VariablesService"
    with patch.object(module, service_name) as service_cls:
        service = AsyncMock()
        # The parametrized route matrix selects different mock service methods.
        getattr(service, operation).side_effect = TracecatConflictError(
            "synthetic private detail"
        )
        service_cls.return_value = service
        suffix = f"/{uuid.uuid4()}" if operation.startswith("update") else ""
        payload = (
            {"name": "synthetic", "values": {"key": "value"}}
            if resource == "variables"
            else {"name": "Synthetic tag"}
        )
        response = client.request(
            method,
            f"{resource_prefix}/{resource}{suffix}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )
    assert response.status_code == 409
    expected = (
        "Tag with this name already exists"
        if resource == "tags"
        else (
            "Variable creation integrity error"
            if operation == "create_variable"
            else "Variable already exists"
        )
    )
    assert response.json() == {"detail": expected}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "resource,method,operation",
    [
        ("tags", "GET", "list_tags"),
        ("tags", "GET", "get_tag"),
        ("tags", "POST", "create_tag"),
        ("tags", "PATCH", "update_tag"),
        ("tags", "DELETE", "delete_tag"),
        ("variables", "GET", "list_variables"),
        ("variables", "GET", "search_variables"),
        ("variables", "GET", "get_variable_by_name"),
        ("variables", "POST", "create_variable"),
        ("variables", "POST", "update_variable"),
        ("variables", "DELETE", "delete_variable"),
    ],
)
@pytest.mark.parametrize("error_type", [IntegrityError, DataError])
async def test_unexpected_database_failures_are_sanitized(
    client: TestClient,
    test_admin_role: Role,
    resource_prefix: str,
    resource: str,
    method: str,
    operation: str,
    error_type: type[IntegrityError] | type[DataError],
) -> None:
    module = tags_router if resource == "tags" else variables_router
    service_name = "TagsService" if resource == "tags" else "VariablesService"
    with (
        patch.object(module, service_name) as service_cls,
        patch.object(common, "logger") as logger,
    ):
        service = AsyncMock()
        # The parametrized route matrix selects different mock service methods.
        getattr(service, operation).side_effect = error_type(
            "synthetic statement", {}, Exception("synthetic database detail")
        )
        service_cls.return_value = service
        suffix = ""
        if operation == "search_variables":
            suffix = "/search"
        elif operation not in {
            "list_tags",
            "list_variables",
            "create_tag",
            "create_variable",
        }:
            suffix = f"/{uuid.uuid4()}"
        payload = (
            {"name": "synthetic", "values": {"key": "value"}}
            if resource == "variables"
            else {"name": "Synthetic tag"}
        )
        response = client.request(
            method,
            f"{resource_prefix}/{resource}{suffix}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )
    assert response.status_code == 500
    assert response.json() == {
        "message": "An unexpected error occurred. Please try again later."
    }
    logger.exception.assert_called_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "resource,method,operation",
    [
        ("tags", "GET", "get_tag"),
        ("tags", "DELETE", "get_tag"),
        ("tags", "PATCH", "update_tag"),
        ("variables", "GET", "get_variable_by_name"),
        ("variables", "POST", "get_variable"),
        ("variables", "POST", "update_variable"),
        ("variables", "DELETE", "get_variable"),
    ],
)
async def test_missing_resources_are_not_found(
    client: TestClient,
    test_admin_role: Role,
    resource_prefix: str,
    resource: str,
    method: str,
    operation: str,
) -> None:
    module = tags_router if resource == "tags" else variables_router
    service_name = "TagsService" if resource == "tags" else "VariablesService"
    with patch.object(module, service_name) as service_cls:
        service = AsyncMock()
        error = NoResultFound if resource == "tags" else TracecatNotFoundError
        # The parametrized route matrix selects different mock service methods.
        getattr(service, operation).side_effect = error("synthetic private detail")
        service_cls.return_value = service
        response = client.request(
            method,
            f"{resource_prefix}/{resource}/{uuid.uuid4()}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"name": "synthetic"},
        )
    assert response.status_code == 404
    assert "synthetic private detail" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "resource,field",
    [
        ("tags", "name"),
        ("variables", "name"),
        ("variables", "environment"),
        ("variables", "values"),
    ],
)
async def test_required_update_fields_reject_null(
    client: TestClient,
    test_admin_role: Role,
    resource_prefix: str,
    resource: str,
    field: str,
) -> None:
    method = "PATCH" if resource == "tags" else "POST"
    response = client.request(
        method,
        f"{resource_prefix}/{resource}/{uuid.uuid4()}",
        params={"workspace_id": str(test_admin_role.workspace_id)},
        json={field: None},
    )
    assert response.status_code == 422
    assert f"{field} cannot be null" in response.text


@pytest.mark.anyio
@pytest.mark.parametrize("environment", ["", "x" * 256])
async def test_variable_create_environment_validation(
    client: TestClient,
    test_admin_role: Role,
    resource_prefix: str,
    environment: str,
) -> None:
    response = client.post(
        f"{resource_prefix}/variables",
        params={"workspace_id": str(test_admin_role.workspace_id)},
        json={
            "name": "synthetic",
            "values": {"key": "value"},
            "environment": environment,
        },
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_internal_variable_value_name_validation(
    action_gateway_client: TestClient, test_admin_role: Role
) -> None:
    response = action_gateway_client.get(
        "/internal/variables/Bad-Name/value",
        params={"key": "key", "workspace_id": str(test_admin_role.workspace_id)},
    )
    assert response.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize("value_lookup", [False, True])
async def test_internal_unexpected_database_error(
    action_gateway_client: TestClient, test_admin_role: Role, value_lookup: bool
) -> None:
    with patch.object(variables_internal_router, "VariablesService") as service_cls:
        service = AsyncMock()
        operation = (
            service.get_variable_value if value_lookup else service.get_variable_by_name
        )
        operation.side_effect = IntegrityError(
            "synthetic statement", {}, Exception("synthetic private detail")
        )
        service_cls.return_value = service
        suffix = "/value" if value_lookup else ""
        response = action_gateway_client.get(
            f"/internal/variables/synthetic{suffix}",
            params={"key": "key", "workspace_id": str(test_admin_role.workspace_id)},
        )
    assert response.status_code == 500
    assert response.json() == {
        "message": "An unexpected error occurred. Please try again later."
    }


@pytest.mark.anyio
async def test_internal_missing_value_is_null(
    action_gateway_client: TestClient, test_admin_role: Role
) -> None:
    with patch.object(variables_internal_router, "VariablesService") as service_cls:
        service = AsyncMock()
        service.get_variable_value.return_value = None
        service_cls.return_value = service
        response = action_gateway_client.get(
            "/internal/variables/synthetic/value",
            params={
                "key": "missing",
                "workspace_id": str(test_admin_role.workspace_id),
            },
        )
    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.anyio
async def test_internal_missing_variable_is_not_found(
    action_gateway_client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(variables_internal_router, "VariablesService") as service_cls:
        service = AsyncMock()
        service.get_variable_by_name.side_effect = TracecatNotFoundError(
            "Variable not found"
        )
        service_cls.return_value = service
        response = action_gateway_client.get(
            "/internal/variables/synthetic",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )
    assert response.status_code == 404
    assert response.json() == {"detail": "Variable not found"}
