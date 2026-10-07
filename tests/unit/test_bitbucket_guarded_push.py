"""Provider-level branch preconditions hold through the Git publication race."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tests.unit.test_bitbucket_vcs import role
from tests.unit.test_git_plumbing import local_git, seed
from tracecat.git.plumbing import GitPlumbing
from tracecat.git.types import GitUrl
from tracecat.sync import PushStatus
from tracecat.vcs.bitbucket.types import BitbucketBranch, BitbucketRepository
from tracecat.vcs.bitbucket_data_center.types import DataCenterBranch
from tracecat.workspace_sync.transport import (
    BitbucketDataCenterWorkspaceSyncTransport,
    BitbucketWorkspaceSyncTransport,
    SyncCommitConflictError,
)


@pytest.mark.anyio
@pytest.mark.parametrize("data_center", [False, True])
@pytest.mark.parametrize("target_exists", [False, True])
@pytest.mark.parametrize("concurrent", [False, True])
@pytest.mark.parametrize("branch", ["sync/workspace", "b" * 40])
async def test_bitbucket_guarded_publication(
    tmp_path: Path,
    data_center: bool,
    target_exists: bool,
    concurrent: bool,
    branch: str,
) -> None:
    remote = local_git(tmp_path)
    reviewed = await seed(remote)
    ref = f"refs/heads/{branch}"
    if target_exists:
        await remote.run("update-ref", ref, reviewed)
    transport = (
        BitbucketDataCenterWorkspaceSyncTransport(session=AsyncMock(), role=role())
        if data_center
        else BitbucketWorkspaceSyncTransport(session=AsyncMock(), role=role())
    )
    url = GitUrl(host="bitbucket.org", org="example", repo="sync")
    model = (
        DataCenterBranch(id="refs/heads/main", displayId="main")
        if data_center
        else BitbucketRepository.model_validate({"mainbranch": {"name": "main"}})
    )
    branch_model = (
        DataCenterBranch(id=ref, displayId=branch)
        if data_center
        else BitbucketBranch(name=branch)
    )
    competing_head = reviewed
    files = {"workflows/old.yml": "updated"}
    with TemporaryDirectory() as directory:
        writer = local_git(directory)
        await writer.initialize()
        # Redirect provider URLs to the disposable local remote while retaining
        # the real fetch, commit and push implementations.
        fetch = writer.fetch
        run = writer.run
        commit = writer.commit

        async def fetch_local(
            remote_url: str,
            ref_name: str,
            *,
            ref_kind: Literal["branch", "commit"] | None = None,
        ) -> str:
            return await fetch(str(tmp_path), ref_name, ref_kind=ref_kind)

        async def run_local(*args: str, data: bytes | None = None) -> bytes:
            args = tuple(
                str(tmp_path) if arg.startswith("https://") else arg for arg in args
            )
            return await run(*args, data=data)

        async def commit_then_race(
            parent: str, files: dict[str, str], deleted: set[str], message: str
        ) -> str | None:
            nonlocal competing_head
            sha = await commit(parent, files, deleted, message)
            if concurrent:
                if target_exists:
                    new_head = await remote.commit(
                        reviewed, {"README.md": "concurrent"}, set(), "Another writer"
                    )
                    assert new_head is not None
                    competing_head = new_head
                await remote.run("update-ref", ref, competing_head)
            return sha

        @asynccontextmanager
        async def connection(
            url: GitUrl,
        ) -> AsyncIterator[tuple[httpx.AsyncClient, GitPlumbing, str]]:
            async with httpx.AsyncClient(
                base_url="https://bitbucket.org/rest/api/1.0/"
            ) as client:
                yield (
                    client,
                    writer,
                    "projects/example/repos/sync"
                    if data_center
                    else "repositories/example/sync",
                )

        with (
            patch.object(transport, "_connection", connection),
            patch.object(transport, "_model", AsyncMock(return_value=model)),
            patch.object(
                transport,
                "_list",
                AsyncMock(return_value=[branch_model] if target_exists else []),
            ),
            patch.object(writer, "fetch", fetch_local),
            patch.object(writer, "run", run_local),
            patch.object(writer, "commit", commit_then_race),
        ):
            operation = transport.write_files(
                url=url,
                files=files,
                message="Sync",
                branch=branch,
                create_pr=False,
                expected_commit_sha=reviewed,
            )
            if concurrent:
                with pytest.raises(
                    SyncCommitConflictError, match="Target branch changed"
                ):
                    await operation
                assert (
                    await remote.run("rev-parse", ref)
                ).decode().strip() == competing_head
                assert (await remote.run("show", f"{ref}:workflows/old.yml")) == b"old"
            else:
                result = await operation
                assert result.status is PushStatus.COMMITTED
                assert (
                    await remote.run("rev-parse", ref)
                ).decode().strip() == result.sha
                assert (
                    await remote.run("show", f"{ref}:workflows/old.yml")
                ) == b"updated"
