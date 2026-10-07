"""Contract checks for the in-memory VCS used by sync tests."""

import pytest

from tests.support.fake_vcs import FakeVcsServer
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
