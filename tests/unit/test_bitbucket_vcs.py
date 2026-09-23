"""Cloud transport tests with real disposable Git repositories and a mocked REST API."""

import json
import subprocess
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import SecretStr

from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES, ORG_ADMIN_SCOPES
from tracecat.exceptions import (
    EntitlementRequired,
    ScopeDeniedError,
    TracecatNotFoundError,
)
from tracecat.git.types import GitUrl
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.sync import PushStatus
from tracecat.tiers.enums import Entitlement
from tracecat.vcs.bitbucket.app import BitbucketError, BitbucketTokenService
from tracecat.vcs.bitbucket.git import BitbucketGit, repository_path, validate_path
from tracecat.vcs.bitbucket.types import BitbucketBranch
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.service import WorkspaceSyncService
from tracecat.workspace_sync.transport import (
    BitbucketWorkspaceSyncTransport,
    vcs_transport_for_provider,
)
from tracecat.workspaces.schemas import WorkspaceSettingsUpdate

TOKEN = SecretStr("synthetic-test-token")
URL = GitUrl(host="bitbucket.org", org="example-workspace", repo="example-repo")


def role(*scopes: str) -> Role:
    return Role(
        type="user",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset(scopes) if scopes else ADMIN_SCOPES | ORG_ADMIN_SCOPES,
    )


def local_git(directory: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(directory), *args], text=True
    ).strip()


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(path)],
        check=True,
        capture_output=True,
    )
    return path


class LocalGit(BitbucketGit):
    def __init__(self, directory: str, remote: Path):
        super().__init__(directory, TOKEN)
        self.remote = remote
        self.env.update(
            GIT_CONFIG_COUNT="6",
            GIT_CONFIG_KEY_5="protocol.file.allow",
            GIT_CONFIG_VALUE_5="always",
        )

    async def fetch(self, remote: str, ref: str) -> str:
        return await super().fetch(str(self.remote), ref)

    async def push(self, remote: str, sha: str, branch: str) -> None:
        await super().push(str(self.remote), sha, branch)


async def seed(remote: Path) -> str:
    with TemporaryDirectory() as directory:
        git = LocalGit(directory, remote)
        await git.initialize()
        tree = (await git.run("mktree", data=b"")).decode().strip()
        first = (
            (await git.run("commit-tree", tree, data=b"Initial commit"))
            .decode()
            .strip()
        )
        sha = await git.commit(
            first,
            {
                "README.md": "keep",
                "workflows/old.yml": "remove",
                "workflows/a.yml": "old",
                "skills/example/SKILL.md": "skill",
            },
            set(),
            "Seed",
        )
        assert sha
        await git.push("", sha, "main")
        return sha


class LocalTransport(BitbucketWorkspaceSyncTransport):
    def __init__(self, remote: Path):
        super().__init__(session=AsyncMock(), role=role())
        self.remote = remote
        self.pull_requests: list[dict[str, object]] = []
        self.fail_pr = False

    def response(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pullrequests"):
            if request.method == "GET":
                return httpx.Response(200, json={"values": self.pull_requests})
            if self.fail_pr:
                return httpx.Response(503)
            payload = json.loads(request.content)
            assert payload["source"]["branch"]["name"] == "sync/workspace"
            pr = {
                "id": 1,
                "links": {
                    "html": {
                        "href": "https://bitbucket.org/example-workspace/example-repo/pull-requests/1"
                    }
                },
            }
            self.pull_requests.append(pr)
            return httpx.Response(201, json=pr)
        if path.endswith("/refs/branches"):
            names = local_git(
                self.remote, "for-each-ref", "--format=%(refname:short)", "refs/heads"
            ).splitlines()
            if query := request.url.params.get("q"):
                name = json.loads(query.split("=", 1)[1])
                names = [n for n in names if n == name]
            return httpx.Response(200, json={"values": [{"name": n} for n in names]})
        if "/commits/" in path:
            sha = local_git(self.remote, "rev-parse", "refs/heads/sync/workspace")
            return httpx.Response(
                200,
                json={
                    "values": [
                        {
                            "hash": sha,
                            "message": "Sync",
                            "date": "2026-01-01T00:00:00Z",
                            "author": {"raw": "Example <example@example.com>"},
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"mainbranch": {"name": "main"}})

    @asynccontextmanager
    async def _connection(
        self, url: GitUrl
    ) -> AsyncIterator[tuple[httpx.AsyncClient, BitbucketGit, str]]:
        with TemporaryDirectory() as directory:
            git = LocalGit(directory, self.remote)
            await git.initialize()
            async with httpx.AsyncClient(
                base_url="https://api.bitbucket.org/2.0/",
                transport=httpx.MockTransport(self.response),
            ) as client:
                yield client, git, f"repositories/{repository_path(url)}"

    async def _sync_request_body(self) -> str:
        return "Workspace sync"


@pytest.mark.anyio
async def test_atomic_export_preserves_unmanaged_files_and_noop_reuses_pr(remote: Path):
    old = await seed(remote)
    transport = LocalTransport(remote)
    files = {"workflows/a.yml": "updated", "agent_presets/example.json": "{}"}
    result = await transport.write_files(
        url=URL,
        files=files,
        message="Sync",
        branch="sync/workspace",
        create_pr=True,
        delete_missing_paths_under=("workflows",),
    )
    assert result.status is PushStatus.COMMITTED
    assert result.pr_number == 1 and not result.pr_reused
    assert local_git(remote, "show", "sync/workspace:README.md") == "keep"
    assert (
        local_git(remote, "show", "sync/workspace:skills/example/SKILL.md") == "skill"
    )
    assert "workflows/old.yml" not in local_git(
        remote, "ls-tree", "-r", "--name-only", "sync/workspace"
    )
    assert local_git(remote, "rev-parse", "sync/workspace^") == old
    retry = await transport.write_files(
        url=URL,
        files=files,
        message="Sync",
        branch="sync/workspace",
        create_pr=True,
        delete_missing_paths_under=("workflows",),
    )
    assert retry.status is PushStatus.NO_OP and retry.sha is None and retry.pr_reused
    assert result.sha is not None
    snapshot = await transport.read_files(url=URL, ref=result.sha)
    assert snapshot.files["workflows/a.yml"] == "updated"
    assert "README.md" not in snapshot.files
    assert "README.md" in snapshot.blob_paths
    assert (await transport.read_files(url=URL, ref=old)).files[
        "workflows/a.yml"
    ] == "old"


@pytest.mark.anyio
async def test_retry_recovers_pr_after_successful_push(remote: Path):
    await seed(remote)
    transport = LocalTransport(remote)
    transport.fail_pr = True

    async def export():
        return await transport.write_files(
            url=URL,
            files={"workflows/a.yml": "updated"},
            message="Sync",
            branch="sync/workspace",
            create_pr=True,
        )

    with pytest.raises(BitbucketError, match="503"):
        await export()
    sha = local_git(remote, "rev-parse", "sync/workspace")
    transport.fail_pr = False
    result = await export()
    assert result.status is PushStatus.NO_OP and result.pr_number == 1
    assert local_git(remote, "rev-parse", "sync/workspace") == sha


@pytest.mark.anyio
async def test_concurrent_push_is_rejected(remote: Path):
    await seed(remote)
    with TemporaryDirectory() as a, TemporaryDirectory() as b:
        one, two = LocalGit(a, remote), LocalGit(b, remote)
        for git in (one, two):
            await git.initialize()
        first, second = await one.fetch("", "main"), await two.fetch("", "main")
        sha_one = await one.commit(first, {"workflows/a.yml": "one"}, set(), "One")
        sha_two = await two.commit(second, {"workflows/a.yml": "two"}, set(), "Two")
        assert sha_one and sha_two
        await one.push("", sha_one, "main")
        with pytest.raises(BitbucketError, match="remote branch changed"):
            await two.push("", sha_two, "main")
        assert local_git(remote, "rev-parse", "main") == sha_one


@pytest.mark.anyio
async def test_pagination_checks_origin_before_sending_credentials():
    transport = BitbucketWorkspaceSyncTransport(session=AsyncMock(), role=role())
    requests: list[str] = []

    def response(request: httpx.Request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "values": [{"name": "main"}],
                "next": "https://attacker.example/2.0/repositories/example",
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.bitbucket.org/2.0/",
        auth=("example@example.com", "secret"),
        transport=httpx.MockTransport(response),
    ) as client:
        with pytest.raises(BitbucketError, match="Invalid Bitbucket"):
            await transport._list(
                client,
                "repositories/example/repo/refs/branches",
                BitbucketBranch,
                limit=2,
            )
    assert len(requests) == 1


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/absolute",
        "a/../b",
        ".git/config",
        "a/.GIT/config",
        "a//b",
        "a\x00b",
    ],
)
def test_unsafe_paths_rejected(path: str):
    with pytest.raises(BitbucketError):
        validate_path(path)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "host", ["bitbucket.example.com", "bitbucket.org:443", "bitbucket.org.evil.test"]
)
async def test_cloud_host_guard_precedes_credential_access(host: str):
    transport = BitbucketWorkspaceSyncTransport(session=AsyncMock(), role=role())
    with patch.object(
        BitbucketTokenService, "get_bitbucket_token_credentials", new_callable=AsyncMock
    ) as credentials:
        with pytest.raises(BitbucketError):
            await transport.read_files(
                url=GitUrl(host=host, org="example", repo="repo"), ref="main"
            )
        credentials.assert_not_called()


def test_provider_factory_and_git_auth_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("GIT_TRACE", "1")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "99")
    git = BitbucketGit(str(tmp_path), TOKEN)
    assert "GIT_TRACE" not in git.env
    assert git.env["GIT_CONFIG_COUNT"] == "5"
    assert isinstance(
        vcs_transport_for_provider(
            VcsProvider.BITBUCKET, session=AsyncMock(), role=role()
        ),
        BitbucketWorkspaceSyncTransport,
    )


@pytest.mark.anyio
async def test_saved_bitbucket_binding_resolves_through_workspace_service():
    service = WorkspaceSyncService(
        session=AsyncMock(), role=role(), provider=VcsProvider.BITBUCKET
    )
    service._workspace = AsyncMock(
        return_value=SimpleNamespace(
            settings={"git_provider": "bitbucket", "git_repo_url": URL.to_url()}
        )
    )
    assert await service._workspace_git_url() == URL


@pytest.mark.anyio
async def test_credential_lifecycle_and_status_never_returns_token():
    service = BitbucketTokenService(session=AsyncMock(), role=role())
    secrets = AsyncMock(spec=SecretsService)
    secrets._get_org_secret_by_name.side_effect = TracecatNotFoundError("missing")
    with (
        patch.object(service, "require_entitlement", new=AsyncMock()),
        patch("tracecat.vcs.bitbucket.app.SecretsService", return_value=secrets),
    ):
        assert not (await service.get_bitbucket_token_credentials_status()).exists
        credentials, created = await service.save_bitbucket_token_credentials(
            email="example@example.com", token=TOKEN
        )
        assert created
        params = secrets._create_org_secret.call_args.args[0]
        assert {key.key for key in params.keys} == {"email", "token"}
        secrets._get_org_secret_by_name.side_effect = None
        secrets._get_org_secret_by_name.return_value = SimpleNamespace(
            id=uuid.uuid4(), encrypted_keys=b"ciphertext", created_at=None
        )
        with patch.object(
            secrets,
            "decrypt_keys",
            return_value=[
                SecretKeyValue(key="email", value=SecretStr(credentials.email)),
                SecretKeyValue(key="token", value=TOKEN),
            ],
        ):
            status = await service.get_bitbucket_token_credentials_status()
            assert status.exists and not status.is_corrupted
            assert TOKEN.get_secret_value() not in status.model_dump_json()
            assert (await service.get_bitbucket_token_credentials()).token == TOKEN
            _, created = await service.save_bitbucket_token_credentials(
                email="example@example.com", token=SecretStr("rotated")
            )
            assert not created
            secrets._update_org_secret.assert_awaited_once()
        with patch.object(secrets, "decrypt_keys", side_effect=ValueError("corrupt")):
            assert (await service.get_bitbucket_token_credentials_status()).is_corrupted
            _, created = await service.save_bitbucket_token_credentials(
                email="example@example.com", token=TOKEN
            )
            assert not created
        await service.delete_bitbucket_token_credentials()
        secrets._delete_org_secret.assert_awaited_once()


@pytest.mark.anyio
async def test_credentials_require_settings_scope_and_entitlement():
    service = BitbucketTokenService(session=AsyncMock(), role=role("workflow:sync"))
    with patch.object(service, "require_entitlement", new=AsyncMock()):
        with pytest.raises(ScopeDeniedError):
            await service.save_bitbucket_token_credentials(
                email="example@example.com", token=TOKEN
            )
        with pytest.raises(ScopeDeniedError):
            await service.get_bitbucket_token_credentials_status()
        with pytest.raises(ScopeDeniedError):
            await service.delete_bitbucket_token_credentials()
    with patch.object(
        service,
        "require_entitlement",
        side_effect=EntitlementRequired(Entitlement.GIT_SYNC),
    ):
        with pytest.raises(EntitlementRequired):
            await service.get_bitbucket_token_credentials()


@pytest.mark.parametrize(
    "url",
    [
        "git+ssh://git@bitbucket.example.com/workspace/repo.git",
        "git+ssh://git@bitbucket.org:443/workspace/repo.git",
    ],
)
def test_workspace_settings_reject_data_center(url: str):
    with pytest.raises(ValueError, match="Bitbucket Cloud"):
        WorkspaceSettingsUpdate(git_provider=VcsProvider.BITBUCKET, git_repo_url=url)


@pytest.mark.anyio
async def test_pagination_follows_cloud_next_and_applies_limit():
    transport = BitbucketWorkspaceSyncTransport(session=AsyncMock(), role=role())
    requests: list[httpx.Request] = []

    def response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("page") == "2":
            return httpx.Response(
                200, json={"values": [{"name": "second"}, {"name": "third"}]}
            )
        return httpx.Response(
            200,
            json={
                "values": [{"name": "first"}],
                "next": "https://api.bitbucket.org/2.0/repositories/example/repo/refs/branches?page=2",
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.bitbucket.org/2.0/",
        transport=httpx.MockTransport(response),
    ) as client:
        branches = await transport._list(
            client,
            "repositories/example/repo/refs/branches",
            BitbucketBranch,
            limit=2,
            params={"pagelen": 1},
        )
    assert [b.name for b in branches] == ["first", "second"]
    assert len(requests) == 2
    assert "pagelen" not in requests[1].url.params


@pytest.mark.anyio
async def test_pinned_base_branch_is_used_for_new_exports(remote: Path):
    await seed(remote)
    local_git(remote, "branch", "release/base", "main")
    transport = LocalTransport(remote)
    url = GitUrl(
        host="bitbucket.org",
        org="example-workspace",
        repo="example-repo",
        ref="release/base",
    )
    result = await transport.write_files(
        url=url,
        files={"workflows/a.yml": "changed"},
        message="Sync",
        branch="sync/pinned",
        create_pr=False,
    )
    assert result.base_ref == "release/base"
    assert local_git(remote, "rev-parse", "sync/pinned^") == local_git(
        remote, "rev-parse", "release/base"
    )
