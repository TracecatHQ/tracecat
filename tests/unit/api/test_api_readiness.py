from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import tracecat_registry
from fastapi import Response, status

from tracecat.api.app import app, check_ready


@pytest.mark.anyio
async def test_check_ready_returns_503_response_when_registry_not_synced(mocker):
    repos_service = mocker.Mock()
    repos_service.get_repository = AsyncMock(return_value=None)
    mocker.patch(
        "tracecat.api.app.PlatformRegistryReposService",
        return_value=repos_service,
    )
    response = Response()

    readiness = await check_ready(response=response, session=AsyncMock())

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert readiness.status == "not_ready"
    assert readiness.registry.synced is False
    assert readiness.registry.expected_version == tracecat_registry.__version__
    assert readiness.registry.current_version is None


@pytest.mark.anyio
async def test_check_ready_returns_200_when_registry_synced(mocker):
    current_version = SimpleNamespace(version=tracecat_registry.__version__)
    repository = SimpleNamespace(current_version=current_version)
    repos_service = mocker.Mock()
    repos_service.get_repository = AsyncMock(return_value=repository)
    mocker.patch(
        "tracecat.api.app.PlatformRegistryReposService",
        return_value=repos_service,
    )
    response = Response()

    readiness = await check_ready(response=response, session=AsyncMock())

    assert response.status_code == status.HTTP_200_OK
    assert readiness.status == "ready"
    assert readiness.registry.synced is True
    assert readiness.registry.expected_version == tracecat_registry.__version__
    assert readiness.registry.current_version == tracecat_registry.__version__


def test_check_ready_documents_not_ready_response():
    app.openapi_schema = None

    responses = app.openapi()["paths"]["/ready"]["get"]["responses"]

    assert responses["503"]["description"] == (
        "API startup or platform registry sync is incomplete."
    )
    assert responses["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReadinessResponse"
    }
