"""HTTP coverage for the platform build details endpoint."""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.admin.build_info.router as build_router_module
from tracecat import __version__ as APP_VERSION
from tracecat.auth.types import Role


@pytest.mark.anyio
async def test_build_info_reports_baked_in_metadata(
    client: TestClient,
    test_admin_role: Role,
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

    response = client.get("/admin/build-info")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "version": APP_VERSION,
        "edition": "ee",
        "commit": "0123456789abcdef",
        "built_at": "2026-11-03T12:34:56Z",
    }


@pytest.mark.anyio
async def test_build_info_without_build_args(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Images built without build args report OSS with unknown commit and time."""
    monkeypatch.setattr(
        build_router_module, "has_installed_agent_backend", lambda: False
    )
    monkeypatch.setattr(build_router_module.config, "TRACECAT__BUILD_COMMIT", None)
    monkeypatch.setattr(
        build_router_module.config, "TRACECAT__BUILD_TIME", "not-a-timestamp"
    )

    response = client.get("/admin/build-info")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "version": APP_VERSION,
        "edition": "oss",
        "commit": None,
        "built_at": None,
    }
