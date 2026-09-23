"""Real Git smart HTTPS round trips against the local Data Center REST mock."""

import threading
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from tests.support.bitbucket_data_center_mock import TOKEN, MockDataCenter
from tests.unit.test_bitbucket_vcs import role
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
from tracecat.vcs.bitbucket_data_center.app import BitbucketDataCenterTokenService
from tracecat.vcs.bitbucket_data_center.schemas import (
    BitbucketDataCenterTokenCredentials,
)
from tracecat.workspace_sync.transport import BitbucketDataCenterWorkspaceSyncTransport

URL = GitUrl(host="localhost", org="DEMO", repo="sync")


@pytest.fixture
def dc(tmp_path, monkeypatch):
    server = MockDataCenter(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SSL_CERT_FILE", str(server.cert))
    credentials = BitbucketDataCenterTokenCredentials(
        base_url=server.base_url, token=SecretStr(TOKEN)
    )
    with patch.object(
        BitbucketDataCenterTokenService,
        "get_bitbucket_data_center_token_credentials",
        new=AsyncMock(return_value=credentials),
    ):
        yield server
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.anyio
async def test_https_git_roundtrip_and_pr_reuse(dc):
    transport = BitbucketDataCenterWorkspaceSyncTransport(
        session=AsyncMock(), role=role()
    )
    files = {
        "workflows/example.yml": "workflow",
        "variables/example.yml": "variable",
        "README.md": "preserve",
    }
    with patch.object(
        transport,
        "_sync_request_body",
        new=AsyncMock(return_value="Synthetic development sync"),
    ):
        first = await transport.write_files(
            url=URL,
            files=files,
            message="Sync",
            branch="sync/workspace",
            create_pr=True,
        )
        assert first.status == PushStatus.COMMITTED
        assert first.pr_number == 1
        second = await transport.write_files(
            url=URL,
            files=files,
            message="Sync",
            branch="sync/workspace",
            create_pr=True,
        )
        assert second.status == PushStatus.NO_OP
        assert second.pr_reused
        assert len(dc.prs) == 1
        commits = await transport.list_commits(url=URL, branch="sync/workspace")
        assert commits[0].sha == first.sha
        assert any(b.is_default for b in await transport.list_branches(url=URL))


@pytest.mark.anyio
async def test_snapshot_delete_retry_and_pagination(dc):
    from tracecat.vcs.bitbucket.app import BitbucketError

    transport = BitbucketDataCenterWorkspaceSyncTransport(
        session=AsyncMock(), role=role()
    )
    files = {"variables/example.yml": "name: example\n", "README.md": "preserve"}
    with patch.object(
        transport, "_sync_request_body", new=AsyncMock(return_value="Sync")
    ):
        dc.fail_next_pr = True
        with pytest.raises(BitbucketError, match="503"):
            await transport.write_files(
                url=URL, files=files, message="Sync", branch="sync/test", create_pr=True
            )
        retry = await transport.write_files(
            url=URL, files=files, message="Sync", branch="sync/test", create_pr=True
        )
        assert retry.status == PushStatus.NO_OP and retry.pr_number == 1
        snapshot = await transport.read_files(url=URL, ref="sync/test")
        assert snapshot.files["variables/example.yml"] == files["variables/example.yml"]
        for n in range(7):
            dc.git("update-ref", f"refs/heads/branch-{n}", snapshot.commit_sha)
        assert len(await transport.list_branches(url=URL, limit=20)) == 9
        assert len(await transport.list_branches(url=URL, limit=3)) == 3
        updated = await transport.write_files(
            url=URL,
            files={"variables/new.yml": "name: new\n"},
            message="Update",
            branch="sync/test",
            create_pr=False,
            delete_missing_paths_under=("variables",),
        )
        assert updated.sha
        paths = (
            dc.git("ls-tree", "-r", "--name-only", updated.sha).decode().splitlines()
        )
        assert paths == ["README.md", "variables/new.yml"]


@pytest.mark.anyio
async def test_credentials_bound_to_instance(dc):
    import httpx

    from tracecat.vcs.bitbucket.app import BitbucketError

    transport = BitbucketDataCenterWorkspaceSyncTransport(
        session=AsyncMock(), role=role()
    )
    with pytest.raises(BitbucketError, match="must match"):
        await transport.list_branches(
            url=GitUrl(host="elsewhere.example.test", org="DEMO", repo="sync")
        )
    assert not dc.requests
    async with httpx.AsyncClient(base_url=dc.base_url + "/rest/api/1.0/") as client:
        for path in ("https://elsewhere.example.test/projects/X", "../../outside"):
            with pytest.raises(BitbucketError, match="Invalid Data Center API URL"):
                await transport._request(client, "GET", path)


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
async def test_all_resources_roundtrip_over_https(dc, session, svc_role):
    from tests.unit.test_workspace_sync_acceptance_contract import (
        _assert_projected_workspaces_match,
        _create_workspace_role,
        _expanded_full_git_tree,
        _set_workspace_git_repo_url,
    )
    from tracecat.registry.lock.types import RegistryLock
    from tracecat.sync import PullOptions
    from tracecat.workspace_sync.enums import VcsProvider
    from tracecat.workspace_sync.schemas import WorkspaceSyncExportRequest
    from tracecat.workspace_sync.service import WorkspaceSyncService

    provider = VcsProvider.BITBUCKET_DATA_CENTER
    await _set_workspace_git_repo_url(
        session,
        workspace_id=svc_role.workspace_id,
        repo_url=URL.to_url(),
        provider=provider,
    )
    target_role = await _create_workspace_role(
        session,
        source_role=svc_role,
        workspace_name="dc-target",
        repo_url=URL.to_url(),
        provider=provider,
    )
    source = WorkspaceSyncService(session=session, role=svc_role, provider=provider)
    target = WorkspaceSyncService(session=session, role=target_role, provider=provider)
    transport = BitbucketDataCenterWorkspaceSyncTransport(
        session=session, role=svc_role
    )
    seed = await transport.write_files(
        url=URL,
        files=_expanded_full_git_tree(include_schedules=False),
        message="Seed",
        branch="seed",
        create_pr=False,
    )
    assert seed.sha
    with patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    ):
        pulled = await source.pull(options=PullOptions(commit_sha=seed.sha))
        assert pulled.success
        exported = await source.export_workspace(
            WorkspaceSyncExportRequest(
                message="Sync", branch="sync/all", create_pr=False
            )
        )
        assert exported.commit.sha
        preview = await target.pull(
            options=PullOptions(commit_sha=exported.commit.sha, dry_run=True)
        )
        assert preview.success
        imported = await target.pull(
            options=PullOptions(commit_sha=exported.commit.sha)
        )
        assert imported.success
        await _assert_projected_workspaces_match(source, target)

        from tracecat.workspace_sync.enums import SyncResourceType
        from tracecat.workspace_sync.schemas import ResourceRef

        selected = await source.export_workspace(
            WorkspaceSyncExportRequest(
                message="Workflow",
                branch="sync/workflow",
                create_pr=False,
                resources=[
                    ResourceRef(
                        resource_type=SyncResourceType.WORKFLOW, source_id="qa-root"
                    )
                ],
            )
        )
        assert selected.commit.sha
        selected_snapshot = await transport.read_files(url=URL, ref=selected.commit.sha)
        assert "workflows/qa-root/definition.yml" in selected_snapshot.files
        assert "workflows/qa-child/definition.yml" in selected_snapshot.files
        assert "workflows/qa-orphan/definition.yml" not in selected_snapshot.files
        workflow_role = await _create_workspace_role(
            session,
            source_role=svc_role,
            workspace_name="dc-workflow-target",
            repo_url=URL.to_url(),
            provider=provider,
        )
        workflow_target = WorkspaceSyncService(
            session=session, role=workflow_role, provider=provider
        )
        workflow_pull = await workflow_target.pull(
            options=PullOptions(commit_sha=selected.commit.sha)
        )
        assert workflow_pull.success
