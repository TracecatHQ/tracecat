"""HTTP coverage for the platform build details endpoint."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

import tracecat.admin.build_info.router as build_router_module
from tracecat import __version__ as APP_VERSION
from tracecat.auth.credentials import require_superuser
from tracecat.auth.types import PlatformRole
from tracecat.auth.users import current_active_user
from tracecat.db.models import User

PATH = "/admin/build-info"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(build_router_module.router, prefix="/admin")
    return app


@pytest.fixture
def non_superuser_client() -> Iterator[TestClient]:
    app = _app()
    app.dependency_overrides[current_active_user] = lambda: User(
        id=uuid.uuid4(), is_superuser=False
    )
    yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def superuser_client() -> Iterator[TestClient]:
    app = _app()
    role = PlatformRole(type="user", user_id=uuid.uuid4(), service_id="tracecat-api")
    app.dependency_overrides[require_superuser] = lambda: role
    yield TestClient(app, raise_server_exceptions=False)


def test_build_info_rejects_non_superusers(
    non_superuser_client: TestClient,
) -> None:
    """Build details are platform admin only."""
    response = non_superuser_client.get(PATH)

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_build_info_reports_baked_in_metadata(
    superuser_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An installed agent backend marks the deployment as EE; build args pass through."""
    monkeypatch.setattr(
        build_router_module, "has_installed_agent_backend", lambda: True
    )
    monkeypatch.setattr(
        build_router_module.config, "TRACECAT__BUILD_COMMIT", "0123456789abcdef"
    )
    monkeypatch.setattr(
        build_router_module.config,
        "TRACECAT__BUILD_TIME",
        "2026-11-03T12:34:56.000Z",
    )

    response = superuser_client.get(PATH)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "version": APP_VERSION,
        "edition": "ee",
        "commit": "0123456789abcdef",
        "built_at": "2026-11-03T12:34:56Z",
    }


@pytest.mark.parametrize(
    ("commit", "built_at"),
    [(None, None), ("not-a-sha", "not-a-timestamp")],
)
def test_build_info_without_usable_build_args(
    superuser_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    commit: str | None,
    built_at: str | None,
) -> None:
    """Missing or malformed build args report OSS with unknown commit and time."""
    monkeypatch.setattr(
        build_router_module, "has_installed_agent_backend", lambda: False
    )
    monkeypatch.setattr(build_router_module.config, "TRACECAT__BUILD_COMMIT", commit)
    monkeypatch.setattr(build_router_module.config, "TRACECAT__BUILD_TIME", built_at)

    response = superuser_client.get(PATH)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "version": APP_VERSION,
        "edition": "oss",
        "commit": None,
        "built_at": None,
    }
