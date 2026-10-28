"""Data Center API and credential contracts; shared sync uses FakeVcsServer."""

import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import SecretStr

from tests.unit.test_bitbucket_vcs import role
from tracecat.exceptions import (
    EntitlementRequired,
    ScopeDeniedError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.git.types import GitUrl
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.tiers.enums import Entitlement
from tracecat.vcs.bitbucket.app import BitbucketError
from tracecat.vcs.bitbucket.git import BitbucketGit
from tracecat.vcs.bitbucket_data_center.app import BitbucketDataCenterTokenService
from tracecat.vcs.bitbucket_data_center.schemas import (
    BitbucketDataCenterTokenCredentials,
)
from tracecat.vcs.bitbucket_data_center.types import DataCenterBranch
from tracecat.workspace_sync.transport import BitbucketDataCenterWorkspaceSyncTransport

TOKEN = "synthetic-data-center-test-token"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.test",
        "https://user:pass@example.test",
        "https://example.test/?x=y",
        "https://example.test/#fragment",
        "https://example.test/a/../b",
        "https://example.test/%2e%2e",
    ],
)
def test_invalid_instance_url(base_url):
    with pytest.raises(ValueError):
        BitbucketDataCenterTokenCredentials(base_url=base_url, token=SecretStr(TOKEN))


@pytest.mark.anyio
async def test_credential_lifecycle_and_status_never_returns_token():
    service = BitbucketDataCenterTokenService(session=AsyncMock(), role=role())
    secrets = AsyncMock(spec=SecretsService)
    secrets._get_org_secret_by_name.side_effect = TracecatNotFoundError("missing")
    with (
        patch.object(service, "require_entitlement", new=AsyncMock()),
        patch(
            "tracecat.vcs.bitbucket_data_center.app.SecretsService",
            return_value=secrets,
        ),
    ):
        assert not (
            await service.get_bitbucket_data_center_token_credentials_status()
        ).exists
        (
            credentials,
            created,
        ) = await service.save_bitbucket_data_center_token_credentials(
            base_url="https://example.test", token=SecretStr(TOKEN)
        )
        assert created
        params = secrets._create_org_secret.call_args.args[0]
        assert {key.key for key in params.keys} == {"base_url", "token"}
        secrets._get_org_secret_by_name.side_effect = None
        secrets._get_org_secret_by_name.return_value = SimpleNamespace(
            id=uuid.uuid4(), encrypted_keys=b"ciphertext", created_at=None
        )
        with patch.object(
            secrets,
            "decrypt_keys",
            return_value=[
                SecretKeyValue(key="base_url", value=SecretStr(credentials.base_url)),
                SecretKeyValue(key="token", value=SecretStr(TOKEN)),
            ],
        ):
            status = await service.get_bitbucket_data_center_token_credentials_status()
            assert status.exists and not status.is_corrupted
            assert TOKEN not in status.model_dump_json()
            assert (
                await service.get_bitbucket_data_center_token_credentials()
            ).token == SecretStr(TOKEN)
            _, created = await service.save_bitbucket_data_center_token_credentials(
                base_url="https://example.test", token=SecretStr("rotated")
            )
            assert not created
            secrets._update_org_secret.assert_awaited_once()
        with patch.object(secrets, "decrypt_keys", side_effect=ValueError("corrupt")):
            assert (
                await service.get_bitbucket_data_center_token_credentials_status()
            ).is_corrupted
            _, created = await service.save_bitbucket_data_center_token_credentials(
                base_url="https://example.test", token=SecretStr(TOKEN)
            )
            assert not created
        await service.delete_bitbucket_data_center_token_credentials()
        secrets._delete_org_secret.assert_awaited_once()


@pytest.mark.anyio
async def test_credentials_require_settings_scope_and_entitlement():
    service = BitbucketDataCenterTokenService(
        session=AsyncMock(), role=role("workflow:sync")
    )
    with patch.object(service, "require_entitlement", new=AsyncMock()):
        with pytest.raises(ScopeDeniedError):
            await service.save_bitbucket_data_center_token_credentials(
                base_url="https://example.test", token=SecretStr(TOKEN)
            )
        with pytest.raises(ScopeDeniedError):
            await service.get_bitbucket_data_center_token_credentials_status()
        with pytest.raises(ScopeDeniedError):
            await service.delete_bitbucket_data_center_token_credentials()
    with patch.object(
        service,
        "require_entitlement",
        side_effect=EntitlementRequired(Entitlement.GIT_SYNC),
    ):
        with pytest.raises(EntitlementRequired):
            await service.get_bitbucket_data_center_token_credentials()


@pytest.mark.anyio
async def test_instance_auth_and_host_isolation():
    transport = BitbucketDataCenterWorkspaceSyncTransport(
        session=AsyncMock(), role=role()
    )
    credentials = BitbucketDataCenterTokenCredentials(
        base_url="https://example.test:8443/bitbucket", token=SecretStr(TOKEN)
    )
    url = GitUrl(host="example.test", org="DEMO", repo="sync")
    with patch.object(
        BitbucketDataCenterTokenService,
        "get_bitbucket_data_center_token_credentials",
        new=AsyncMock(return_value=credentials),
    ):
        async with transport._connection(url) as (client, git, path):
            assert client.headers["Authorization"] == f"Bearer {TOKEN}"
            assert (
                str(client.base_url)
                == "https://example.test:8443/bitbucket/rest/api/1.0/"
            )
            assert path == "projects/DEMO/repos/sync"
            assert (
                transport._git_remote(url, client)
                == "https://example.test:8443/bitbucket/scm/DEMO/sync.git"
            )
            assert (
                git.env["GIT_CONFIG_KEY_0"]
                == "http.https://example.test:8443/bitbucket/.extraHeader"
            )
            assert git.env["GIT_CONFIG_VALUE_0"] == f"Authorization: Bearer {TOKEN}"
            for target in ("https://elsewhere.test/projects/X", "../../outside"):
                with pytest.raises(BitbucketError, match="Invalid Data Center API URL"):
                    await transport._request(client, "GET", target)
        with pytest.raises(BitbucketError, match="must match"):
            await transport.list_branches(
                url=GitUrl(host="elsewhere.test", org="DEMO", repo="sync")
            )


@pytest.mark.anyio
async def test_data_center_pagination_uses_server_cursor():
    transport = BitbucketDataCenterWorkspaceSyncTransport(
        session=AsyncMock(), role=role()
    )
    starts = []

    def respond(request):
        start = request.url.params["start"]
        starts.append(start)
        return httpx.Response(
            200,
            json={
                "values": [
                    {"id": f"refs/heads/branch-{start}", "displayId": f"branch-{start}"}
                ],
                "isLastPage": start == "7",
                "nextPageStart": 7,
            },
        )

    async with httpx.AsyncClient(
        base_url="https://example.test/rest/api/1.0/",
        transport=httpx.MockTransport(respond),
    ) as client:
        branches = await transport._list(
            client, "projects/DEMO/repos/sync/branches", DataCenterBranch, limit=10
        )
    assert starts == ["0", "7"]
    assert [b.name for b in branches] == ["branch-0", "branch-7"]


@pytest.mark.anyio
async def test_data_center_pr_payload_and_reuse():
    transport = BitbucketDataCenterWorkspaceSyncTransport(
        session=AsyncMock(), role=role()
    )
    prs = []

    def respond(request):
        assert (
            request.url.path
            == "/bitbucket/rest/api/1.0/projects/DEMO/repos/sync/pull-requests"
        )
        if request.method == "GET":
            assert request.url.params["direction"] == "OUTGOING"
            assert request.url.params["at"] == "refs/heads/sync/workspace"
            return httpx.Response(200, json={"values": prs, "isLastPage": True})
        payload = json.loads(request.content)
        repository = {"slug": "sync", "project": {"key": "DEMO"}}
        assert payload["fromRef"] == {
            "id": "refs/heads/sync/workspace",
            "repository": repository,
        }
        assert payload["toRef"] == {"id": "refs/heads/main", "repository": repository}
        prs.append({**payload, "id": 42})
        return httpx.Response(201, json=prs[0])

    with patch.object(
        transport, "_sync_request_body", new=AsyncMock(return_value="Sync")
    ):
        async with httpx.AsyncClient(
            base_url="https://example.test/bitbucket/rest/api/1.0/",
            transport=httpx.MockTransport(respond),
        ) as client:
            args = (
                client,
                "projects/DEMO/repos/sync",
                "sync/workspace",
                "main",
                "Sync",
            )
            first = await transport._upsert_pull_request(*args)
            second = await transport._upsert_pull_request(*args)
    assert first == (
        "https://example.test/bitbucket/projects/DEMO/repos/sync/pull-requests/42",
        42,
        False,
    )
    assert second == (first[0], 42, True)
    assert len(prs) == 1


DC_URL = GitUrl(host="example.test", org="DEMO", repo="sync")
DC_REPO_PATH = "/rest/api/1.0/projects/DEMO/repos/sync"
SHA_A, SHA_B, SHA_C = ("a" * 40, "b" * 40, "c" * 40)
# 2026-03-01, 2026-02-01 and 2026-01-01 at 00:00 UTC, in epoch milliseconds.
TIMESTAMPS = {
    SHA_A: 1_772_323_200_000,
    SHA_B: 1_769_904_000_000,
    SHA_C: 1_767_225_600_000,
}


class _TagServer:
    """Data Center ``/tags`` and ``/commits/{id}`` with start/limit paging."""

    def __init__(self, tags: list[dict[str, str]]) -> None:
        self.tags = tags
        """Tag payloads in ``orderBy=MODIFICATION`` order."""
        self.tag_requests: list[httpx.Request] = []
        self.commit_reads: list[str] = []

    def respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"{DC_REPO_PATH}/tags":
            self.tag_requests.append(request)
            start = int(request.url.params["start"])
            end = start + int(request.url.params["limit"])
            return httpx.Response(
                200,
                json={
                    "values": self.tags[start:end],
                    "isLastPage": end >= len(self.tags),
                    "nextPageStart": end,
                },
            )
        sha = path.removeprefix(f"{DC_REPO_PATH}/commits/")
        self.commit_reads.append(sha)
        if sha not in TIMESTAMPS:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "id": sha,
                "message": "Release",
                "authorTimestamp": TIMESTAMPS[sha],
                "author": {"name": "Example"},
            },
        )


class _MockDataCenterTransport(BitbucketDataCenterWorkspaceSyncTransport):
    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        super().__init__(session=AsyncMock(), role=role())
        self._respond = respond

    @asynccontextmanager
    async def _connection(
        self, url: GitUrl
    ) -> AsyncIterator[tuple[httpx.AsyncClient, BitbucketGit, str]]:
        with TemporaryDirectory() as directory:
            async with httpx.AsyncClient(
                base_url="https://example.test/rest/api/1.0/",
                transport=httpx.MockTransport(self._respond),
            ) as client:
                yield (
                    client,
                    BitbucketGit(directory, SecretStr(TOKEN)),
                    f"projects/{url.org}/repos/{url.repo}",
                )


def _dc_tag(name: str, sha: str) -> dict[str, str]:
    return {"id": f"refs/tags/{name}", "displayId": name, "latestCommit": sha}


@pytest.mark.anyio
async def test_list_tags_pages_by_start_and_dates_each_commit_once():
    server = _TagServer(
        [
            _dc_tag("v3", SHA_A),
            _dc_tag("v2", SHA_B),
            _dc_tag("v2-alias", SHA_B),
            _dc_tag("v1", SHA_C),
        ]
    )
    transport = _MockDataCenterTransport(server.respond)

    pages: list[list[tuple[str, str]]] = []
    cursors: list[str | None] = []
    cursor: str | None = None
    while True:
        tags, cursor = await transport.list_tags(url=DC_URL, limit=3, cursor=cursor)
        pages.append([(t.name, t.date) for t in tags])
        cursors.append(cursor)
        if cursor is None:
            break

    assert pages == [
        [
            ("v3", "2026-03-01T00:00:00+00:00"),
            ("v2", "2026-02-01T00:00:00+00:00"),
            ("v2-alias", "2026-02-01T00:00:00+00:00"),
        ],
        [("v1", "2026-01-01T00:00:00+00:00")],
    ]
    # The cursor is the next start offset.
    assert cursors == ["3", None]
    assert [r.url.params["start"] for r in server.tag_requests] == ["0", "3"]
    assert {r.url.params["orderBy"] for r in server.tag_requests} == {"MODIFICATION"}
    # v2 and v2-alias share a commit, so it is read once.
    assert sorted(server.commit_reads) == [SHA_A, SHA_B, SHA_C]

    # The page size is capped like the other providers.
    await transport.list_tags(url=DC_URL, limit=500)
    assert server.tag_requests[-1].url.params["limit"] == "100"
    requests_before = len(server.tag_requests)
    # Over 4300 digits, int() itself raises a plain ValueError (a 500), so long
    # cursors are rejected before parsing.
    for cursor in ("-1", "1" * 19, "9" * 5000, "٣"):
        with pytest.raises(TracecatValidationError, match="Invalid tag cursor"):
            await transport.list_tags(url=DC_URL, cursor=cursor)
    assert len(server.tag_requests) == requests_before


@pytest.mark.anyio
async def test_list_tags_skips_malformed_and_undatable_tags():
    server = _TagServer(
        [
            _dc_tag("v2", SHA_B),
            # Its commit read fails, so it has no date.
            _dc_tag("gone", "d" * 40),
            {"displayId": "broken"},
        ]
    )

    tags, cursor = await _MockDataCenterTransport(server.respond).list_tags(url=DC_URL)

    assert [(t.name, t.commit_sha) for t in tags] == [("v2", SHA_B)]
    assert cursor is None
