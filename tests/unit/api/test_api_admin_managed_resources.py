"""HTTP-level tests for the platform admin managed resources endpoints."""

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from tracecat_ee.admin.managed_resources import router as managed_resources_router
from tracecat_ee.admin.managed_resources.schemas import (
    ManagedSourceRead,
    ManagedSourceSettingRead,
)
from tracecat_ee.admin.router import router as admin_router

from tracecat.auth.credentials import require_superuser, require_superuser_step_up
from tracecat.auth.types import PlatformRole
from tracecat.auth.users import current_active_user
from tracecat.db.engine import get_async_session_bypass_rls
from tracecat.db.models import User
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.managed_resources.source import ManagedSourceUnavailableError
from tracecat.pagination import Page, PageParams
from tracecat.registry.repositories.schemas import GitTagInfo
from tracecat.vcs.bitbucket.app import BitbucketError
from tracecat.vcs.github.app import GitHubAppError
from tracecat.vcs.gitlab.app import GitLabApiError
from tracecat.workspace_sync.enums import VcsProvider

SOURCE_PATH = "/admin/managed-resources/source"
TAGS_PATH = f"{SOURCE_PATH}/tags"


async def _mock_session() -> AsyncMock:
    return AsyncMock(name="mock_async_session")


def _admin_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_async_session_bypass_rls] = _mock_session
    return app


@pytest.fixture
def non_superuser_client() -> Iterator[TestClient]:
    app = _admin_app()
    app.dependency_overrides[current_active_user] = lambda: User(
        id=uuid.uuid4(), is_superuser=False
    )
    yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def superuser_client() -> Iterator[TestClient]:
    app = _admin_app()
    role = PlatformRole(type="user", user_id=uuid.uuid4(), service_id="tracecat-api")
    app.dependency_overrides[require_superuser] = lambda: role
    app.dependency_overrides[require_superuser_step_up] = lambda: role
    yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def service() -> Iterator[AsyncMock]:
    with patch.object(
        managed_resources_router, "AdminManagedResourcesService"
    ) as service_cls:
        yield service_cls.return_value


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", SOURCE_PATH),
        ("PUT", SOURCE_PATH),
        ("DELETE", SOURCE_PATH),
        ("GET", TAGS_PATH),
    ],
)
def test_routes_reject_non_superusers(
    non_superuser_client: TestClient, method: str, path: str
) -> None:
    with patch.object(
        managed_resources_router, "AdminManagedResourcesService"
    ) as service_cls:
        response = non_superuser_client.request(
            method, path, json={"workspace_id": str(uuid.uuid4())}
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    service_cls.assert_not_called()


def test_get_source(superuser_client: TestClient, service: AsyncMock) -> None:
    source = ManagedSourceRead(
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        git_provider=VcsProvider.GITHUB,
        git_repo_url="https://github.com/example-org/managed.git",
    )
    service.get_source = AsyncMock(
        side_effect=[
            ManagedSourceSettingRead(source=None),
            ManagedSourceSettingRead(source=source),
            ManagedSourceUnavailableError("binding changed"),
        ]
    )

    assert superuser_client.get(SOURCE_PATH).json() == {"source": None}
    assert superuser_client.get(SOURCE_PATH).json() == {
        "source": source.model_dump(mode="json")
    }
    unavailable = superuser_client.get(SOURCE_PATH)
    assert unavailable.status_code == status.HTTP_409_CONFLICT
    assert unavailable.json()["detail"] == "binding changed"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TracecatNotFoundError("Workspace not found"), status.HTTP_404_NOT_FOUND),
        (TracecatValidationError("no Git binding"), status.HTTP_400_BAD_REQUEST),
    ],
)
def test_set_source_maps_validation_errors(
    superuser_client: TestClient,
    service: AsyncMock,
    error: Exception,
    expected: int,
) -> None:
    service.set_source = AsyncMock(side_effect=error)
    response = superuser_client.put(
        SOURCE_PATH, json={"workspace_id": str(uuid.uuid4())}
    )

    assert response.status_code == expected
    assert response.json()["detail"] == str(error)


def test_clear_source(superuser_client: TestClient, service: AsyncMock) -> None:
    service.clear_source = AsyncMock(return_value=None)
    response = superuser_client.delete(SOURCE_PATH)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    service.clear_source.assert_awaited_once_with()


def test_list_source_tags_returns_a_cursor_page(
    superuser_client: TestClient, service: AsyncMock
) -> None:
    service.list_source_tags = AsyncMock(
        return_value=Page(
            items=[GitTagInfo(name="v2", commit_sha="a" * 40, date="2026-02-01")],
            next_cursor="next-page",
        )
    )
    response = superuser_client.get(f"{TAGS_PATH}?limit=5&cursor=abc")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["items"] == [
        {"name": "v2", "commit_sha": "a" * 40, "date": "2026-02-01"}
    ]
    assert body["next_cursor"] == "next-page"
    service.list_source_tags.assert_awaited_once_with(PageParams(limit=5, cursor="abc"))


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ManagedSourceUnavailableError("not configured"), status.HTTP_409_CONFLICT),
        (TracecatValidationError("Invalid tag cursor"), status.HTTP_400_BAD_REQUEST),
        # No upstream status: configuration, mapped like workspace sync routes.
        (GitHubAppError("App not installed"), status.HTTP_400_BAD_REQUEST),
        (GitLabApiError("gone", status_code=404), status.HTTP_404_NOT_FOUND),
        # The provider rejected the source org's credentials, limited, or failed.
        (BitbucketError("denied", status_code=401), status.HTTP_502_BAD_GATEWAY),
        (GitHubAppError("forbidden", status_code=403), status.HTTP_502_BAD_GATEWAY),
        (GitHubAppError("rate limited", status_code=429), status.HTTP_502_BAD_GATEWAY),
        (BitbucketError("down", status_code=503), status.HTTP_502_BAD_GATEWAY),
        (GitLabApiError("bad request", status_code=400), status.HTTP_400_BAD_REQUEST),
    ],
)
def test_list_source_tags_maps_errors(
    superuser_client: TestClient,
    service: AsyncMock,
    error: Exception,
    expected: int,
) -> None:
    service.list_source_tags = AsyncMock(side_effect=error)
    response = superuser_client.get(TAGS_PATH)

    assert response.status_code == expected
    assert response.json()["detail"] == str(error)


BASE = "/admin/managed-resources"
TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000042")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", f"{BASE}/source/releases"),
        ("GET", f"{BASE}/workspaces"),
        ("GET", f"{BASE}/targets"),
        ("POST", f"{BASE}/targets"),
        ("PATCH", f"{BASE}/targets/{TARGET_ID}"),
        ("POST", f"{BASE}/targets/{TARGET_ID}/unassign"),
        ("GET", f"{BASE}/targets/{TARGET_ID}/references"),
        ("PUT", f"{BASE}/targets/{TARGET_ID}/references"),
        ("POST", f"{BASE}/rollouts/preview"),
        ("POST", f"{BASE}/rollouts/apply"),
        ("GET", f"{BASE}/rollouts"),
        ("GET", f"{BASE}/rollouts/{TARGET_ID}"),
    ],
)
def test_rollout_routes_reject_non_superusers(
    non_superuser_client: TestClient, method: str, path: str
) -> None:
    with patch.object(
        managed_resources_router, "AdminManagedResourcesService"
    ) as service_cls:
        response = non_superuser_client.request(method, path, json={})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    service_cls.assert_not_called()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TracecatNotFoundError("Tag 'v9.9.9' not found"), status.HTTP_404_NOT_FOUND),
        (
            TracecatValidationError("The source workspace cannot be a target"),
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        ),
        (ManagedSourceUnavailableError("no source"), status.HTTP_409_CONFLICT),
    ],
)
def test_assign_target_maps_domain_errors(
    superuser_client: TestClient,
    service: AsyncMock,
    error: Exception,
    expected: int,
) -> None:
    service.assign_target = AsyncMock(side_effect=error)

    response = superuser_client.post(
        f"{BASE}/targets",
        json={"workspace_id": str(uuid.uuid4()), "pinned_tag": "v9.9.9"},
    )

    assert response.status_code == expected
    assert response.json()["detail"] == str(error)


def test_cut_release_returns_the_new_tag(
    superuser_client: TestClient, service: AsyncMock
) -> None:
    tag = GitTagInfo(name="v1.1.0", commit_sha="c" * 40, date="2026-10-29T00:00:00Z")
    service.cut_release = AsyncMock(return_value=tag)

    response = superuser_client.post(
        f"{BASE}/source/releases", json={"tag": "v1.1.0", "commit_sha": "c" * 40}
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert GitTagInfo.model_validate(response.json()) == tag
    [params] = service.cut_release.await_args.args
    assert params.commit_sha == "c" * 40
