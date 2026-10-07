"""Whole-branch compare-and-swap against real disposable Git repositories."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from pydantic import SecretStr

from tracecat.git.plumbing import GitPlumbing, GitRefConflictError


def local_git(directory: Path | str) -> GitPlumbing:
    git = GitPlumbing(
        str(directory),
        SecretStr("synthetic-token"),
        credential_url="https://gitlab.example.test/",
    )
    git.env.update(
        GIT_CONFIG_COUNT="6",
        GIT_CONFIG_KEY_5="protocol.file.allow",
        GIT_CONFIG_VALUE_5="always",
    )
    return git


async def seed(git: GitPlumbing) -> str:
    await git.initialize()
    tree = (await git.run("mktree", data=b"")).decode().strip()
    root = (await git.run("commit-tree", tree, data=b"Initial commit")).decode().strip()
    sha = await git.commit(
        root, {"README.md": "keep", "workflows/old.yml": "old"}, set(), "Seed"
    )
    assert sha is not None
    await git.run("update-ref", "refs/heads/main", sha)
    return sha


@pytest.mark.anyio
async def test_branch_exists_uses_exact_head_ref(tmp_path: Path) -> None:
    remote = local_git(tmp_path)
    sha = await seed(remote)
    await remote.run("update-ref", "refs/heads/prefix/sync/test", sha)
    await remote.run("update-ref", "refs/tags/sync/test", sha)
    assert not await remote.branch_exists(str(tmp_path), "sync/test")
    await remote.run("update-ref", "refs/heads/sync/test", sha)
    assert await remote.branch_exists(str(tmp_path), "sync/test")


@pytest.mark.anyio
@pytest.mark.parametrize("concurrent", [False, True])
async def test_leased_push_rejects_concurrent_unselected_path(
    tmp_path: Path, concurrent: bool
) -> None:
    remote = local_git(tmp_path)
    reviewed = await seed(remote)
    with TemporaryDirectory() as directory:
        writer = local_git(directory)
        await writer.initialize()
        await writer.fetch(str(tmp_path), reviewed)
        prepared = await writer.commit(
            reviewed, {"workflows/new.yml": "new"}, {"workflows/old.yml"}, "Sync"
        )
        assert prepared is not None
        if concurrent:
            intervening = await remote.commit(
                reviewed,
                {"workflows/concurrent.yml": "external"},
                set(),
                "External addition",
            )
            assert intervening is not None
            await remote.run("update-ref", "refs/heads/main", intervening)
            with pytest.raises(GitRefConflictError):
                await writer.push_with_lease(str(tmp_path), prepared, "main", reviewed)
            assert (
                await remote.run("rev-parse", "main")
            ).decode().strip() == intervening
            assert (
                await remote.run("show", "main:workflows/concurrent.yml") == b"external"
            )
        else:
            await writer.push_with_lease(str(tmp_path), prepared, "main", reviewed)
            assert (await remote.run("rev-parse", "main")).decode().strip() == prepared
            assert await remote.run("show", "main:README.md") == b"keep"
            assert set(await remote.entries(prepared)) == {
                "README.md",
                "workflows/new.yml",
            }


@pytest.mark.anyio
@pytest.mark.parametrize("concurrent", [False, True])
async def test_leased_branch_creation_requires_absence(
    tmp_path: Path, concurrent: bool
) -> None:
    remote = local_git(tmp_path)
    reviewed = await seed(remote)
    with TemporaryDirectory() as directory:
        writer = local_git(directory)
        await writer.initialize()
        await writer.fetch(str(tmp_path), reviewed)
        prepared = await writer.commit(
            reviewed, {"workflows/new.yml": "new"}, set(), "Sync"
        )
        assert prepared is not None
        if concurrent:
            # Even an independently created branch at the same reviewed SHA must
            # not be overwritten by an operation that required branch absence.
            await remote.run("update-ref", "refs/heads/sync/new", reviewed)
            with pytest.raises(GitRefConflictError):
                await writer.push_with_lease(str(tmp_path), prepared, "sync/new", None)
            assert (
                await remote.run("rev-parse", "sync/new")
            ).decode().strip() == reviewed
        else:
            await writer.push_with_lease(str(tmp_path), prepared, "sync/new", None)
            assert (
                await remote.run("rev-parse", "sync/new")
            ).decode().strip() == prepared


def test_git_credentials_are_scoped_to_configured_host_and_environment(
    tmp_path: Path,
) -> None:
    git = GitPlumbing(
        str(tmp_path),
        SecretStr("synthetic-token"),
        credential_url="https://gitlab.example.test/",
    )
    assert (
        git.env["GIT_CONFIG_KEY_0"] == "http.https://gitlab.example.test/.extraHeader"
    )
    assert git.env["GIT_CONFIG_VALUE_2"] == "false"
    assert git.env["GIT_TERMINAL_PROMPT"] == "0"
    assert git.env["GIT_CONFIG_VALUE_3"] == "never"


@pytest.mark.anyio
async def test_leased_push_recovers_a_lost_success_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = local_git(tmp_path)
    reviewed = await seed(remote)
    with TemporaryDirectory() as directory:
        writer = local_git(directory)
        await writer.initialize()
        await writer.fetch(str(tmp_path), reviewed)
        prepared = await writer.commit(
            reviewed, {"workflows/new.yml": "new"}, set(), "Sync"
        )
        assert prepared is not None
        original_run = writer.run

        async def lost_response(*args: str, data: bytes | None = None) -> bytes:
            result = await original_run(*args, data=data)
            if args[0] == "push":
                raise RuntimeError("Synthetic lost response")
            return result

        monkeypatch.setattr(writer, "run", lost_response)
        await writer.push_with_lease(str(tmp_path), prepared, "main", reviewed)
        assert (await remote.run("rev-parse", "main")).decode().strip() == prepared
