"""Data Center API and credential contracts; shared sync uses FakeVcsServer."""

import json
import uuid
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
)
from tracecat.git.types import GitUrl
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.tiers.enums import Entitlement
from tracecat.vcs.bitbucket.app import BitbucketError
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
