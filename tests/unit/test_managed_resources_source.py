"""Managed resources source: tag listing, tag reads, and the source binding."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import orjson
import pytest
from github.GithubException import GithubException
from pydantic import SecretStr

from tests.support.fake_vcs import FakeVcsServer
from tracecat.auth.types import PlatformRole, Role
from tracecat.authz.controls import check_scopes
from tracecat.db.models import Workspace
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.git.types import GitUrl
from tracecat.managed_resources import source as source_module
from tracecat.managed_resources.source import (
    SOURCE_REPOSITORY_SCOPES,
    ManagedSource,
    ManagedSourceNotConfiguredError,
    get_managed_source,
    load_managed_source,
    source_repository_role,
    source_sync_service,
)
from tracecat.vcs.bitbucket.git import BitbucketGit, repository_path
from tracecat.vcs.github.app import GitHubAppError
from tracecat.vcs.gitlab.app import GitLabApiError
from tracecat.vcs.gitlab.schemas import GitLabTokenCredentials
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.service import WorkspaceSyncService
from tracecat.workspace_sync.transport import (
    BitbucketDataCenterWorkspaceSyncTransport,
    BitbucketWorkspaceSyncTransport,
    GitHubWorkspaceSyncTransport,
    GitLabWorkspaceSyncTransport,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
TAG_OBJECT_SHA = "f" * 40

type Handler = Callable[[httpx.Request], httpx.Response]


def _role(*scopes: str) -> Role:
    return Role(
        type="user",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset(scopes or {"workspace_sync:sync"}),
    )


def _raw_path(request: httpx.Request) -> str:
    return request.url.raw_path.split(b"?", maxsplit=1)[0].decode()


# --- GitHub ---


def _github_repo() -> Mock:
    """A repo with one annotated tag (v2) and one lightweight tag (v1)."""
    repo = Mock()
    repo.get_git_matching_refs.return_value = [
        SimpleNamespace(
            ref="refs/tags/v2",
            object=SimpleNamespace(type="tag", sha=TAG_OBJECT_SHA),
        ),
        SimpleNamespace(
            ref="refs/tags/v1",
            object=SimpleNamespace(type="commit", sha=SHA_B),
        ),
    ]
    repo.get_git_tag.return_value = SimpleNamespace(
        tagger=SimpleNamespace(date=datetime(2026, 3, 1, tzinfo=UTC)),
        object=SimpleNamespace(type="commit", sha=SHA_A),
    )
    repo.get_git_commit.return_value = SimpleNamespace(
        author=SimpleNamespace(date=datetime(2026, 2, 1, tzinfo=UTC))
    )
    return repo


@asynccontextmanager
async def _github_transport(repo: Mock) -> AsyncIterator[GitHubWorkspaceSyncTransport]:
    gh = Mock()
    gh.get_repo.return_value = repo
    gh_service = AsyncMock()
    gh_service.get_github_client_for_repo.return_value = gh
    with patch(
        "tracecat.workspace_sync.transport.GitHubAppService",
        return_value=gh_service,
    ):
        yield GitHubWorkspaceSyncTransport(session=AsyncMock(), role=_role())


GITHUB_URL = GitUrl(host="github.com", org="example-org", repo="managed")


@pytest.mark.anyio
async def test_github_list_tags_uses_tagger_date_then_commit_date() -> None:
    repo = _github_repo()
    async with _github_transport(repo) as transport:
        tags = await transport.list_tags(url=GITHUB_URL, limit=10)

    assert [(t.name, t.commit_sha, t.date) for t in tags] == [
        # Annotated: peeled to its commit, dated by the tagger.
        ("v2", SHA_A, "2026-03-01T00:00:00+00:00"),
        # Lightweight: no tag object, so the commit's author date.
        ("v1", SHA_B, "2026-02-01T00:00:00+00:00"),
    ]
    repo.get_git_matching_refs.assert_called_once_with("tags/")
    repo.get_git_tag.assert_called_once_with(TAG_OBJECT_SHA)
    repo.get_git_commit.assert_called_once_with(SHA_B)


@pytest.mark.anyio
async def test_github_list_tags_wraps_provider_errors() -> None:
    repo = _github_repo()
    repo.get_git_matching_refs.side_effect = GithubException(403, "denied")
    async with _github_transport(repo) as transport:
        with pytest.raises(GitHubAppError):
            await transport.list_tags(url=GITHUB_URL)


@pytest.mark.anyio
async def test_github_resolve_tag_peels_annotated_tag() -> None:
    repo = _github_repo()
    repo.get_git_ref.return_value = SimpleNamespace(
        ref="refs/tags/v2", object=SimpleNamespace(type="tag", sha=TAG_OBJECT_SHA)
    )
    async with _github_transport(repo) as transport:
        sha = await transport.resolve_tag(url=GITHUB_URL, tag="v2")

    assert sha == SHA_A
    repo.get_git_ref.assert_called_once_with("tags/v2")
    repo.get_git_commit.assert_not_called()


# --- GitLab ---


class _GitLabTransport(GitLabWorkspaceSyncTransport):
    def __init__(self, handler: Handler) -> None:
        super().__init__(session=AsyncMock(), role=_role())
        self._handler = handler

    async def _credentials(self) -> GitLabTokenCredentials:
        return GitLabTokenCredentials(
            base_url="https://gitlab.example.test", token=SecretStr("test-token")
        )

    def _client(self, credentials: GitLabTokenCredentials) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{credentials.base_url}/api/v4",
            transport=httpx.MockTransport(self._handler),
        )


GITLAB_URL = GitUrl(host="gitlab.example.test", org="group/sub", repo="managed")
GITLAB_TAGS_PATH = "/api/v4/projects/group%2Fsub%2Fmanaged/repository/tags"


@pytest.mark.anyio
async def test_gitlab_list_tags_paginates_and_falls_back_to_commit_date() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert _raw_path(request) == GITLAB_TAGS_PATH
        if request.url.params["page"] == "1":
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "v3",
                        "created_at": "2026-03-01T00:00:00Z",
                        "commit": {"id": SHA_A, "authored_date": "2026-01-01"},
                    },
                    {
                        "name": "v2",
                        "created_at": None,
                        "commit": {"id": SHA_B, "authored_date": "2026-02-01"},
                    },
                ],
                headers={"x-next-page": "2"},
            )
        return httpx.Response(
            200,
            json=[{"name": "v1", "commit": {"id": SHA_C, "committed_date": "2025"}}],
            headers={"x-next-page": ""},
        )

    tags = await _GitLabTransport(handler).list_tags(url=GITLAB_URL, limit=10)

    assert [(t.name, t.commit_sha, t.date) for t in tags] == [
        ("v3", SHA_A, "2026-03-01T00:00:00Z"),
        ("v2", SHA_B, "2026-02-01"),
        ("v1", SHA_C, "2025"),
    ]
    assert [r.url.params["page"] for r in requests] == ["1", "2"]
    assert requests[0].url.params["order_by"] == "updated"
    assert requests[0].url.params["sort"] == "desc"


@pytest.mark.anyio
async def test_gitlab_resolve_tag_encodes_name_and_surfaces_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if _raw_path(request) == f"{GITLAB_TAGS_PATH}/release%2F1.0":
            return httpx.Response(
                200, json={"name": "release/1.0", "commit": {"id": SHA_A}}
            )
        return httpx.Response(404, json={"message": "404 Tag Not Found"})

    transport = _GitLabTransport(handler)
    assert await transport.resolve_tag(url=GITLAB_URL, tag="release/1.0") == SHA_A
    with pytest.raises(GitLabApiError) as exc:
        await transport.resolve_tag(url=GITLAB_URL, tag="missing")
    assert exc.value.status_code == 404


# --- Bitbucket Cloud ---


class _BitbucketTransport(BitbucketWorkspaceSyncTransport):
    def __init__(self, handler: Handler) -> None:
        super().__init__(session=AsyncMock(), role=_role())
        self._handler = handler

    @asynccontextmanager
    async def _connection(
        self, url: GitUrl
    ) -> AsyncIterator[tuple[httpx.AsyncClient, BitbucketGit, str]]:
        with TemporaryDirectory() as directory:
            async with httpx.AsyncClient(
                base_url="https://api.bitbucket.org/2.0/",
                transport=httpx.MockTransport(self._handler),
            ) as client:
                yield (
                    client,
                    BitbucketGit(directory, SecretStr("test-token")),
                    f"repositories/{repository_path(url)}",
                )


BITBUCKET_URL = GitUrl(host="bitbucket.org", org="example-ws", repo="managed")
BITBUCKET_TAGS_PATH = "/2.0/repositories/example-ws/managed/refs/tags"


@pytest.mark.anyio
async def test_bitbucket_list_tags_follows_next_and_prefers_tag_date() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == BITBUCKET_TAGS_PATH
        if request.url.params.get("page") == "2":
            return httpx.Response(
                200,
                json={
                    "values": [
                        {"name": "v1", "target": {"hash": SHA_B, "date": "2026-01"}}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "values": [
                    {
                        "name": "v2",
                        "date": "2026-03",
                        "target": {"hash": SHA_A, "date": "2026-02"},
                    }
                ],
                "next": f"https://api.bitbucket.org{BITBUCKET_TAGS_PATH}?page=2",
            },
        )

    tags = await _BitbucketTransport(handler).list_tags(url=BITBUCKET_URL, limit=10)

    assert [(t.name, t.commit_sha, t.date) for t in tags] == [
        ("v2", SHA_A, "2026-03"),
        ("v1", SHA_B, "2026-01"),
    ]
    assert requests[0].url.params["sort"] == "-target.date"
    assert requests[0].url.params["pagelen"] == "10"
    # The next link carries its own query; ours is not resent.
    assert "sort" not in requests[1].url.params


@pytest.mark.anyio
async def test_bitbucket_resolve_tag_encodes_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert _raw_path(request) == f"{BITBUCKET_TAGS_PATH}/release%2F1.0"
        return httpx.Response(
            200,
            json={"name": "release/1.0", "target": {"hash": SHA_A, "date": "2026"}},
        )

    transport = _BitbucketTransport(handler)
    assert await transport.resolve_tag(url=BITBUCKET_URL, tag="release/1.0") == SHA_A


# --- Bitbucket Data Center ---


class _DataCenterTransport(BitbucketDataCenterWorkspaceSyncTransport):
    def __init__(self, handler: Handler) -> None:
        super().__init__(session=AsyncMock(), role=_role())
        self._handler = handler

    @asynccontextmanager
    async def _connection(
        self, url: GitUrl
    ) -> AsyncIterator[tuple[httpx.AsyncClient, BitbucketGit, str]]:
        with TemporaryDirectory() as directory:
            async with httpx.AsyncClient(
                base_url="https://example.test/rest/api/1.0/",
                transport=httpx.MockTransport(self._handler),
            ) as client:
                yield (
                    client,
                    BitbucketGit(directory, SecretStr("test-token")),
                    f"projects/{url.org}/repos/{url.repo}",
                )


DATA_CENTER_URL = GitUrl(host="example.test", org="DEMO", repo="managed")
DATA_CENTER_REPO_PATH = "/rest/api/1.0/projects/DEMO/repos/managed"


@pytest.mark.anyio
async def test_data_center_list_tags_uses_commit_dates_once_per_commit() -> None:
    commit_reads: list[str] = []
    timestamps = {SHA_A: 1_772_323_200_000, SHA_B: 1_769_904_000_000}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"{DATA_CENTER_REPO_PATH}/tags":
            assert request.url.params["orderBy"] == "MODIFICATION"
            return httpx.Response(
                200,
                json={
                    "values": [
                        {
                            "id": "refs/tags/v2",
                            "displayId": "v2",
                            "latestCommit": SHA_A,
                        },
                        {
                            "id": "refs/tags/v1",
                            "displayId": "v1",
                            "latestCommit": SHA_B,
                        },
                        {
                            "id": "refs/tags/v1-alias",
                            "displayId": "v1-alias",
                            "latestCommit": SHA_B,
                        },
                    ],
                    "isLastPage": True,
                },
            )
        sha = path.removeprefix(f"{DATA_CENTER_REPO_PATH}/commits/")
        commit_reads.append(sha)
        return httpx.Response(
            200,
            json={
                "id": sha,
                "message": "Release",
                "authorTimestamp": timestamps[sha],
                "author": {"name": "Example"},
            },
        )

    tags = await _DataCenterTransport(handler).list_tags(url=DATA_CENTER_URL, limit=10)

    assert [(t.name, t.commit_sha, t.date) for t in tags] == [
        ("v2", SHA_A, "2026-03-01T00:00:00+00:00"),
        ("v1", SHA_B, "2026-02-01T00:00:00+00:00"),
        ("v1-alias", SHA_B, "2026-02-01T00:00:00+00:00"),
    ]
    assert sorted(commit_reads) == [SHA_A, SHA_B]


@pytest.mark.anyio
async def test_data_center_resolve_tag_returns_latest_commit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"{DATA_CENTER_REPO_PATH}/tags/v2"
        return httpx.Response(
            200,
            json={"id": "refs/tags/v2", "displayId": "v2", "latestCommit": SHA_A},
        )

    transport = _DataCenterTransport(handler)
    assert await transport.resolve_tag(url=DATA_CENTER_URL, tag="v2") == SHA_A


# --- Reading at a tag ---


@pytest.mark.anyio
async def test_read_files_at_tag_reads_the_resolved_commit() -> None:
    server = FakeVcsServer()
    url = GitUrl(host="github.com", org="example-org", repo="managed")
    transport = server.transport_factory(VcsProvider.GITHUB, session=None, role=None)
    await transport.write_files(
        url=url, files={"a.yml": "v1"}, message="v1", branch="main", create_pr=False
    )
    tag_sha = server.create_tag(url, tag="v1", ref="main")
    # The branch moves on; the tag must still read the tagged tree.
    await transport.write_files(
        url=url, files={"a.yml": "v2"}, message="v2", branch="main", create_pr=False
    )
    service = WorkspaceSyncService(
        session=AsyncMock(),
        role=_role("workspace_sync:sync"),
        provider=VcsProvider.GITHUB,
        transport_factory=server.transport_factory,
    )

    with patch.object(service, "_workspace_git_url", AsyncMock(return_value=url)):
        snapshot = await service.read_files_at_tag("v1")
        with pytest.raises(TracecatNotFoundError):
            await service.read_files_at_tag("missing")

    assert snapshot.commit_sha == tag_sha
    assert snapshot.files == {"a.yml": "v1"}


@pytest.mark.anyio
async def test_read_files_at_tag_requires_sync_scope() -> None:
    service = WorkspaceSyncService(
        session=AsyncMock(),
        role=_role("workflow:read"),
        transport_factory=FakeVcsServer().transport_factory,
    )
    with pytest.raises(ScopeDeniedError):
        await service.read_files_at_tag("v1")


# --- Source binding ---


def _workspace(**settings: str) -> Workspace:
    return Workspace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name="source",
        settings=settings,
    )


@pytest.mark.anyio
async def test_load_managed_source_returns_binding() -> None:
    workspace = _workspace(
        git_provider="gitlab", git_repo_url="https://gitlab.example.test/g/r.git"
    )
    session = AsyncMock()
    session.scalar.return_value = workspace

    source = await load_managed_source(session, workspace.id)

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
        {"git_repo_url": "https://github.com/example-org/managed.git"},
        {
            "git_provider": "not-a-provider",
            "git_repo_url": "https://github.com/example-org/managed.git",
        },
    ],
)
async def test_load_managed_source_requires_git_binding(
    settings: dict[str, str],
) -> None:
    session = AsyncMock()
    session.scalar.return_value = _workspace(**settings)
    with pytest.raises(TracecatValidationError):
        await load_managed_source(session, uuid.uuid4())


@pytest.mark.anyio
async def test_load_managed_source_requires_existing_workspace() -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    with pytest.raises(TracecatNotFoundError):
        await load_managed_source(session, uuid.uuid4())


@pytest.mark.anyio
async def test_get_managed_source_reads_platform_setting() -> None:
    workspace = _workspace(
        git_provider="github", git_repo_url="https://github.com/example-org/m.git"
    )
    session = AsyncMock()
    session.scalar.return_value = None
    assert await get_managed_source(session) is None

    # First the platform setting row, then the workspace it names.
    session.scalar.side_effect = [orjson.dumps(str(workspace.id)), workspace]
    source = await get_managed_source(session)
    assert source is not None
    assert source.workspace_id == workspace.id


def test_source_repository_role_is_narrow() -> None:
    source = ManagedSource(
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        git_provider=VcsProvider.GITHUB,
        git_repo_url="https://github.com/example-org/managed.git",
    )

    role = source_repository_role(source)

    assert role.organization_id == source.organization_id
    assert role.workspace_id == source.workspace_id
    assert role.scopes == SOURCE_REPOSITORY_SCOPES == {"workspace_sync:sync"}
    assert not role.is_platform_superuser
    check_scopes(role, "workspace_sync:sync")
    for scope in ("org:settings:read", "workflow:update", "workspace:update"):
        with pytest.raises(ScopeDeniedError):
            check_scopes(role, scope)


@pytest.mark.anyio
async def test_source_sync_service_uses_source_role() -> None:
    actor = PlatformRole(type="user", user_id=uuid.uuid4(), service_id="tracecat-api")
    source = ManagedSource(
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        git_provider=VcsProvider.GITHUB,
        git_repo_url="https://github.com/example-org/managed.git",
    )
    session = AsyncMock()
    for_workspace = AsyncMock()

    with (
        patch.object(source_module, "get_managed_source", AsyncMock(return_value=None)),
        pytest.raises(ManagedSourceNotConfiguredError),
    ):
        await source_sync_service(session, actor)

    with (
        patch.object(
            source_module, "get_managed_source", AsyncMock(return_value=source)
        ),
        patch.object(WorkspaceSyncService, "for_workspace", for_workspace),
    ):
        await source_sync_service(session, actor)

    for_workspace.assert_awaited_once_with(session, source_repository_role(source))
