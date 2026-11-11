"""Managed resources source binding and its cross-organization read path."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import httpx
import orjson
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.managed_resources.schemas import ManagedSourceUpdate
from tracecat_ee.admin.managed_resources.service import AdminManagedResourcesService

from tracecat import config
from tracecat.audit.service import AuditService
from tracecat.auth.types import PlatformRole, Role
from tracecat.authz.controls import check_scopes
from tracecat.authz.scopes import ADMIN_SCOPES, ORG_ADMIN_SCOPES
from tracecat.db.models import PlatformSetting, Workspace
from tracecat.exceptions import (
    EntitlementRequired,
    ScopeDeniedError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.git.types import GitUrl
from tracecat.managed_resources import source as source_module
from tracecat.managed_resources.source import (
    MANAGED_RESOURCES_SOURCE_KEY,
    SOURCE_REPOSITORY_SCOPES,
    ManagedSource,
    ManagedSourceUnavailableError,
    get_managed_source,
    list_source_tags,
    load_managed_source,
    pin_source,
    source_repository_role,
)
from tracecat.pagination import PageParams
from tracecat.registry.repositories.schemas import GitTagInfo
from tracecat.tiers import defaults as tier_defaults
from tracecat.vcs.gitlab.app import GitLabTokenService
from tracecat.workspace_sync import service as sync_service_module
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspaces.schemas import WorkspaceSettings

GITHUB_REPO = "git+ssh://git@github.com/example-org/managed.git"
OTHER_REPO = "git+ssh://git@github.com/example-org/other.git"


def _workspace(**settings: str) -> Workspace:
    return Workspace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name="source",
        settings=settings,
    )


def _source(workspace: Workspace) -> ManagedSource:
    """The source for a workspace bound to ``GITHUB_REPO``."""
    return ManagedSource(
        workspace_id=workspace.id,
        organization_id=workspace.organization_id,
        git_provider=VcsProvider.GITHUB,
        git_repo_url=GITHUB_REPO,
    )


def _stored(workspace: Workspace) -> bytes:
    return pin_source(_source(workspace)).model_dump_json().encode()


def _session(*scalars: object) -> AsyncMock:
    """A session whose ``scalar`` calls return ``scalars`` in order."""
    session = AsyncMock()
    session.scalar.side_effect = list(scalars)
    return session


# --- Loading a candidate source ---


@pytest.mark.anyio
async def test_load_managed_source_returns_binding() -> None:
    workspace = _workspace(
        git_provider="gitlab", git_repo_url="https://gitlab.example.test/g/r.git"
    )

    source = await load_managed_source(_session(workspace), workspace.id)

    assert source == ManagedSource(
        workspace_id=workspace.id,
        organization_id=workspace.organization_id,
        git_provider=VcsProvider.GITLAB,
        git_repo_url="https://gitlab.example.test/g/r.git",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"git_provider": "github"},
        {"git_repo_url": GITHUB_REPO},
        {"git_provider": "not-a-provider", "git_repo_url": GITHUB_REPO},
    ],
)
async def test_load_managed_source_requires_git_binding(
    settings: dict[str, str],
) -> None:
    with pytest.raises(TracecatValidationError):
        await load_managed_source(_session(_workspace(**settings)), uuid.uuid4())


@pytest.mark.anyio
async def test_load_managed_source_requires_existing_workspace() -> None:
    with pytest.raises(TracecatNotFoundError):
        await load_managed_source(_session(None), uuid.uuid4())


@pytest.mark.anyio
async def test_unrelated_malformed_setting_leaves_the_source_usable() -> None:
    workspace = Workspace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name="source",
        settings={
            "git_provider": "github",
            "git_repo_url": GITHUB_REPO,
            "workflow_default_timeout_seconds": "not-a-number",
            "allowed_attachment_extensions": 42,
        },
    )

    source = await load_managed_source(_session(workspace), workspace.id)

    assert source == _source(workspace)
    # The same holds when reading a pinned source back.
    assert await get_managed_source(_session(_stored(workspace), workspace)) == source


# --- Reading the configured source ---


@pytest.mark.anyio
async def test_get_managed_source_returns_none_when_unset() -> None:
    assert await get_managed_source(_session(None)) is None


@pytest.mark.anyio
async def test_get_managed_source_returns_pinned_binding() -> None:
    workspace = _workspace(git_provider="github", git_repo_url=GITHUB_REPO)

    source = await get_managed_source(_session(_stored(workspace), workspace))

    assert source == _source(workspace)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "live_settings",
    [
        # Someone with workspace:update repointed the repository.
        {"git_provider": "github", "git_repo_url": "https://github.com/x/other.git"},
        {"git_provider": "gitlab", "git_repo_url": GITHUB_REPO},
        # The binding was removed.
        {},
        # The live binding is malformed: a 409, not a 500.
        {"git_provider": "github", "git_repo_url": 42},
    ],
)
async def test_get_managed_source_rejects_a_changed_binding(
    live_settings: dict[str, object],
) -> None:
    pinned = _workspace(git_provider="github", git_repo_url=GITHUB_REPO)
    live = Workspace(
        id=pinned.id,
        organization_id=pinned.organization_id,
        name="source",
        settings=live_settings,
    )

    with pytest.raises(ManagedSourceUnavailableError):
        await get_managed_source(_session(_stored(pinned), live))


@pytest.mark.anyio
async def test_non_object_settings_are_unusable_not_a_500() -> None:
    pinned = _workspace(git_provider="github", git_repo_url=GITHUB_REPO)
    # JSONB lets a malformed row hold a list where an object is expected.
    live = Workspace(
        id=pinned.id,
        organization_id=pinned.organization_id,
        name="source",
        settings=["github", GITHUB_REPO],
    )

    with pytest.raises(TracecatValidationError, match="malformed"):
        await load_managed_source(_session(live), live.id)
    with pytest.raises(ManagedSourceUnavailableError, match="malformed"):
        await get_managed_source(_session(_stored(pinned), live))


@pytest.mark.anyio
async def test_get_managed_source_rejects_a_deleted_workspace() -> None:
    pinned = _workspace(git_provider="github", git_repo_url=GITHUB_REPO)
    with pytest.raises(ManagedSourceUnavailableError, match="no longer valid"):
        await get_managed_source(_session(_stored(pinned), None))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        # The pre-pinning format: a bare workspace ID string.
        b'"2f0b6d0e-5b7a-4c1e-9f4a-0a1b2c3d4e5f"',
        b'{"workspace_id": "not-a-uuid", "git_provider": "github", "git_repo_url": "x"}',
    ],
)
async def test_get_managed_source_rejects_a_malformed_setting(raw: bytes) -> None:
    with pytest.raises(ManagedSourceUnavailableError, match="malformed"):
        await get_managed_source(_session(raw))


# --- Scoping the cross-organization read ---


def test_source_repository_role_is_narrow() -> None:
    source = _source(_workspace(git_provider="github", git_repo_url=GITHUB_REPO))

    role = source_repository_role(source)

    assert role.organization_id == source.organization_id
    assert role.workspace_id == source.workspace_id
    assert role.scopes == SOURCE_REPOSITORY_SCOPES == {"workspace_sync:sync"}
    assert not role.is_platform_superuser
    check_scopes(role, "workspace_sync:sync")
    for scope in ("org:settings:read", "workflow:update", "workspace:update"):
        with pytest.raises(ScopeDeniedError):
            check_scopes(role, scope)


class _RecordingTransport:
    """Stands in for a provider transport and records what it was asked."""

    def __init__(self) -> None:
        self.factory_calls: list[tuple[VcsProvider, Role]] = []
        self.urls: list[GitUrl] = []

    def factory(
        self, provider: VcsProvider, *, session: object, role: Role
    ) -> _RecordingTransport:
        self.factory_calls.append((provider, role))
        return self

    async def list_tags(
        self, *, url: GitUrl, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[GitTagInfo], str | None]:
        self.urls.append(url)
        return [], None


@pytest.mark.anyio
async def test_list_source_tags_reads_the_pinned_repo_as_the_source_role() -> None:
    actor = PlatformRole(type="user", user_id=uuid.uuid4(), service_id="tracecat-api")
    source = _source(_workspace(git_provider="github", git_repo_url=GITHUB_REPO))
    transport = _RecordingTransport()

    with (
        patch.object(source_module, "get_managed_source", AsyncMock(return_value=None)),
        pytest.raises(ManagedSourceUnavailableError, match="not configured"),
    ):
        await list_source_tags(AsyncMock(), actor, limit=10, cursor=None)

    with (
        patch.object(
            source_module, "get_managed_source", AsyncMock(return_value=source)
        ),
        patch.object(
            sync_service_module, "vcs_transport_for_provider", transport.factory
        ),
    ):
        await list_source_tags(AsyncMock(), actor, limit=10, cursor=None)

    # Credentials come from the narrow source role, for the pinned provider.
    assert transport.factory_calls == [
        (VcsProvider.GITHUB, source_repository_role(source))
    ]
    assert transport.urls == [
        GitUrl(host="github.com", org="example-org", repo="managed")
    ]


# --- End to end through the real scope and entitlement checks ---


GITLAB_HOST = "gitlab.example.test"


def _gitlab_tags(request: httpx.Request) -> httpx.Response:
    raw_path = request.url.raw_path.split(b"?", maxsplit=1)[0].decode()
    assert raw_path == "/api/v4/projects/group%2Fmanaged/repository/tags"
    return httpx.Response(
        200,
        json=[{"name": "v1", "commit": {"id": "a" * 40, "authored_date": "2026"}}],
        headers={"x-next-page": ""},
    )


def _set_git_sync_entitlement(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(update={"git_sync": enabled}),
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_source_tags_pass_real_scope_and_entitlement_checks(
    session: AsyncSession,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only HTTP is mocked: the synthetic role meets the real decorators."""
    monkeypatch.setattr(AuditService, "create_event", AsyncMock())
    _set_git_sync_entitlement(monkeypatch, True)
    svc_workspace.settings = WorkspaceSettings(
        git_provider=VcsProvider.GITLAB,
        git_repo_url=f"git+ssh://git@{GITLAB_HOST}/group/managed.git",
    )
    await session.commit()
    org_admin = Role(
        type="user",
        user_id=uuid.uuid4(),
        organization_id=svc_workspace.organization_id,
        service_id="tracecat-api",
        scopes=ADMIN_SCOPES | ORG_ADMIN_SCOPES,
    )
    await GitLabTokenService(
        session=session, role=org_admin
    ).save_gitlab_token_credentials(
        base_url=f"https://{GITLAB_HOST}", token=SecretStr("synthetic-token")
    )
    actor = PlatformRole(type="user", user_id=uuid.uuid4(), service_id="tracecat-api")
    service = AdminManagedResourcesService(session, actor)
    await service.set_source(ManagedSourceUpdate(workspace_id=svc_workspace.id))

    with respx.mock(assert_all_called=True) as router:
        router.route(host=GITLAB_HOST).mock(side_effect=_gitlab_tags)
        page = await service.list_source_tags(PageParams(limit=10))
    assert [(tag.name, tag.commit_sha) for tag in page.items] == [("v1", "a" * 40)]
    assert page.next_cursor is None

    # Without Git sync, the source org's credentials are never read.
    _set_git_sync_entitlement(monkeypatch, False)
    with respx.mock(assert_all_called=False) as router:
        route = router.route(host=GITLAB_HOST).mock(side_effect=_gitlab_tags)
        with pytest.raises(EntitlementRequired):
            await service.list_source_tags(PageParams(limit=10))
    assert not route.called


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_source_setting_round_trip(
    session: AsyncSession,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_event = AsyncMock()
    monkeypatch.setattr(AuditService, "create_event", create_event)
    actor = PlatformRole(type="user", user_id=uuid.uuid4(), service_id="tracecat-api")
    service = AdminManagedResourcesService(session, actor)
    assert (await service.get_source()).source is None

    def audited_resource_ids() -> list[uuid.UUID | None]:
        """Resource IDs of the audit events since the last call."""
        ids = [call.kwargs["resource_id"] for call in create_event.call_args_list]
        create_event.reset_mock()
        return ids

    # A workspace without a Git binding, or no workspace, is rejected.
    with pytest.raises(TracecatValidationError):
        await service.set_source(ManagedSourceUpdate(workspace_id=svc_workspace.id))
    with pytest.raises(TracecatNotFoundError):
        await service.set_source(ManagedSourceUpdate(workspace_id=uuid.uuid4()))
    assert (await service.get_source()).source is None

    svc_workspace.settings = WorkspaceSettings(
        git_provider=VcsProvider.GITHUB, git_repo_url=GITHUB_REPO
    )
    await session.commit()
    audited_resource_ids()
    updated = await service.set_source(
        ManagedSourceUpdate(workspace_id=svc_workspace.id)
    )
    # Both the attempt and the success event name the source workspace.
    assert audited_resource_ids() == [svc_workspace.id, svc_workspace.id]
    # Setting it again upserts the same row.
    await service.set_source(ManagedSourceUpdate(workspace_id=svc_workspace.id))
    read = await service.get_source()

    assert read.source == updated
    assert updated.workspace_id == svc_workspace.id
    assert updated.organization_id == svc_workspace.organization_id
    assert updated.git_provider == VcsProvider.GITHUB
    stored = (
        await session.scalars(
            select(PlatformSetting).where(
                PlatformSetting.key == MANAGED_RESOURCES_SOURCE_KEY
            )
        )
    ).all()
    assert len(stored) == 1
    assert orjson.loads(stored[0].value) == {
        "workspace_id": str(svc_workspace.id),
        "git_provider": "github",
        "git_repo_url": GITHUB_REPO,
    }
    assert not stored[0].is_encrypted

    # Repointing the workspace's repository makes the source unavailable.
    svc_workspace.settings = WorkspaceSettings(
        git_provider=VcsProvider.GITHUB,
        git_repo_url="https://github.com/example-org/other.git",
    )
    await session.commit()
    with pytest.raises(ManagedSourceUnavailableError, match="changed"):
        await service.get_source()

    audited_resource_ids()
    cleared = await service.clear_source()
    assert cleared is not None and cleared.workspace_id == svc_workspace.id
    # The success event records the source that was removed.
    assert audited_resource_ids() == [None, svc_workspace.id]
    assert (await service.get_source()).source is None
    # Clearing an unset source is a no-op.
    assert await service.clear_source() is None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_binding_changed_after_the_pin_check_is_not_read(
    session: AsyncSession,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repoint racing the pin check cannot redirect the tag read."""
    monkeypatch.setattr(AuditService, "create_event", AsyncMock())
    svc_workspace.settings = WorkspaceSettings(
        git_provider=VcsProvider.GITHUB, git_repo_url=GITHUB_REPO
    )
    await session.commit()
    actor = PlatformRole(type="user", user_id=uuid.uuid4(), service_id="tracecat-api")
    service = AdminManagedResourcesService(session, actor)
    await service.set_source(ManagedSourceUpdate(workspace_id=svc_workspace.id))

    check = source_module.get_managed_source

    async def check_then_repoint(check_session: AsyncSession) -> ManagedSource | None:
        """Run the real pin check, then repoint the workspace before the read."""
        source = await check(check_session)
        svc_workspace.settings = WorkspaceSettings(
            git_provider=VcsProvider.GITHUB, git_repo_url=OTHER_REPO
        )
        await session.commit()
        return source

    transport = _RecordingTransport()
    monkeypatch.setattr(source_module, "get_managed_source", check_then_repoint)
    monkeypatch.setattr(
        sync_service_module, "vcs_transport_for_provider", transport.factory
    )

    await service.list_source_tags(PageParams(limit=10))

    # Only the transport is faked: the read went to the pinned repository.
    assert transport.urls == [
        GitUrl(host="github.com", org="example-org", repo="managed")
    ]
