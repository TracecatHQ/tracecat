from __future__ import annotations

import uuid
from typing import Any, Never
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from tracecat.auth.types import Role
from tracecat.exceptions import EntitlementRequired
from tracecat.tiers.enums import Entitlement
from tracecat.vcs import router as vcs_router
from tracecat.vcs.schemas import GitLabTokenCredentialsRequest


class _EntitlementFailingGitLabTokenService:
    def __init__(self, *, session: Any, role: Role) -> None:
        del session, role

    def _raise_entitlement(self) -> Never:
        raise EntitlementRequired(Entitlement.GIT_SYNC)

    async def save_gitlab_token_credentials(
        self, *, base_url: str, token: SecretStr
    ) -> tuple[Any, bool]:
        del base_url, token
        self._raise_entitlement()

    async def delete_gitlab_token_credentials(self) -> None:
        self._raise_entitlement()

    async def get_gitlab_token_credentials_status(self) -> dict[str, Any]:
        self._raise_entitlement()


def _org_settings_role(scope: str) -> Role:
    return Role(
        type="user",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({scope}),
    )


@pytest.mark.anyio
async def test_save_gitlab_credentials_reraises_entitlement_required() -> None:
    with patch.object(
        vcs_router,
        "GitLabTokenService",
        _EntitlementFailingGitLabTokenService,
    ):
        with pytest.raises(EntitlementRequired, match=Entitlement.GIT_SYNC.value):
            await vcs_router.save_gitlab_token_credentials(
                session=AsyncMock(),
                role=_org_settings_role("org:settings:update"),
                request=GitLabTokenCredentialsRequest(
                    base_url="https://gitlab.example.test",
                    token=SecretStr("test-token"),
                ),
            )


@pytest.mark.anyio
async def test_delete_gitlab_credentials_reraises_entitlement_required() -> None:
    with patch.object(
        vcs_router,
        "GitLabTokenService",
        _EntitlementFailingGitLabTokenService,
    ):
        with pytest.raises(EntitlementRequired, match=Entitlement.GIT_SYNC.value):
            await vcs_router.delete_gitlab_token_credentials(
                session=AsyncMock(),
                role=_org_settings_role("org:settings:delete"),
            )


@pytest.mark.anyio
async def test_gitlab_credentials_status_reraises_entitlement_required() -> None:
    with patch.object(
        vcs_router,
        "GitLabTokenService",
        _EntitlementFailingGitLabTokenService,
    ):
        with pytest.raises(EntitlementRequired, match=Entitlement.GIT_SYNC.value):
            await vcs_router.get_gitlab_token_credentials_status(
                session=AsyncMock(),
                role=_org_settings_role("org:settings:read"),
            )
