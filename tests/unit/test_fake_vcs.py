"""Contract checks for the in-memory VCS used by sync tests."""

import pytest

from tests.support.fake_vcs import FakeVcsServer
from tracecat.exceptions import TracecatValidationError
from tracecat.git.types import GitUrl
from tracecat.workspace_sync.enums import VcsProvider


@pytest.mark.anyio
@pytest.mark.parametrize("existing_target", [False, True])
async def test_branch_start_is_independent_of_reported_pr_base(existing_target):
    server = FakeVcsServer()
    url = GitUrl(host="github.com", org="example", repo="sync-test")
    transport = server.transport_factory(VcsProvider.GITHUB, session=None, role=None)
    await transport.write_files(
        url=url,
        files={"inherited.txt": "reviewed"},
        message="Prepare source",
        branch="reviewed",
        create_pr=False,
    )
    branch = "reviewed" if existing_target else "sync/test"
    for message in ["Write changes", "Repeat unchanged contents"]:
        result = await transport.write_files(
            url=url,
            files={"managed.txt": "changed"},
            message=message,
            branch=branch,
            create_pr=True,
            pr_base_branch="main",
            branch_start_ref="reviewed",
        )
        assert result.base_ref == "main"
        assert server.repo_files(url, ref=branch) == {
            "inherited.txt": "reviewed",
            "managed.txt": "changed",
        }


@pytest.mark.anyio
async def test_guarded_write_checks_branch_omitted_from_listing(monkeypatch):
    server = FakeVcsServer()
    url = GitUrl(host="github.com", org="example", repo="sync-test")
    transport = server.transport_factory(VcsProvider.GITHUB, session=None, role=None)
    main = await transport.read_files(url=url, ref="main")
    await transport.write_files(
        url=url,
        files={"managed.txt": "existing"},
        message="Target head",
        branch="sync/target",
        create_pr=False,
    )
    repo = server._repo(url)
    monkeypatch.setattr(type(repo), "branch_names", lambda self, *, limit: ["main"])
    with pytest.raises(TracecatValidationError, match="Target branch changed"):
        await transport.write_files(
            url=url,
            files={"managed.txt": "stale"},
            message="Stale write",
            branch="sync/target",
            create_pr=False,
            expected_commit_sha=main.commit_sha,
        )
    assert server.repo_files(url, ref="sync/target") == {"managed.txt": "existing"}


@pytest.mark.anyio
async def test_explicit_ref_kind_never_falls_back_to_another_kind():
    server = FakeVcsServer()
    url = GitUrl(host="github.com", org="example", repo="sync-test")
    transport = server.transport_factory(VcsProvider.GITHUB, session=None, role=None)
    initial = await transport.read_files(url=url, ref="main", ref_kind="branch")
    with pytest.raises(KeyError):
        await transport.read_files(url=url, ref=initial.commit_sha, ref_kind="branch")
    with pytest.raises(KeyError):
        await transport.read_files(url=url, ref="main", ref_kind="commit")
    await transport.write_files(
        url=url,
        files={"new.txt": "branch"},
        message="Synthetic",
        branch=initial.commit_sha,
        create_pr=False,
    )
    branch = await transport.read_files(
        url=url, ref=initial.commit_sha, ref_kind="branch"
    )
    commit = await transport.read_files(
        url=url, ref=initial.commit_sha, ref_kind="commit"
    )
    assert branch.files == {"new.txt": "branch"}
    assert commit.commit_sha == initial.commit_sha
    assert branch.commit_sha != commit.commit_sha


@pytest.mark.anyio
async def test_tags_page_newest_first_like_the_real_providers():
    server = FakeVcsServer()
    url = GitUrl(host="github.com", org="example", repo="sync-test")
    transport = server.transport_factory(VcsProvider.GITHUB, session=None, role=None)
    for version in range(1, 6):
        await transport.write_files(
            url=url,
            files={"release.txt": str(version)},
            message=f"Release {version}",
            branch="main",
            create_pr=False,
        )
        server.create_tag(url, tag=f"v{version}", ref="main")

    names: list[str] = []
    cursors: list[str] = []
    cursor: str | None = None
    page_sizes: list[int] = []
    while True:
        tags, cursor = await transport.list_tags(url=url, limit=2, cursor=cursor)
        names.extend(tag.name for tag in tags)
        page_sizes.append(len(tags))
        if cursor is None:
            break
        cursors.append(cursor)

    assert names == ["v5", "v4", "v3", "v2", "v1"]
    assert page_sizes == [2, 2, 1]
    for limit in (0, -1):
        assert await transport.list_tags(url=url, limit=limit) == ([], None)
    # Cursors are validated like the real providers'.
    with pytest.raises(TracecatValidationError, match="different page size"):
        await transport.list_tags(url=url, limit=3, cursor=cursors[0])
    with pytest.raises(TracecatValidationError, match="Invalid tag cursor"):
        await transport.list_tags(url=url, limit=2, cursor="2")
