"""Tests for GitLab workspace sync transport."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import quote, unquote

import httpx
import pytest
from pydantic import SecretStr

from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.exceptions import ScopeDeniedError, TracecatNotFoundError
from tracecat.git.types import GitUrl
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.sync import PushStatus
from tracecat.vcs.gitlab.app import GITLAB_TOKEN_SECRET_NAME, GitLabTokenService
from tracecat.vcs.gitlab.schemas import GitLabTokenCredentials
from tracecat.workspace_sync.schemas import MANIFEST_FILENAME, WorkspaceManifest
from tracecat.workspace_sync.serialization import canonical_json_text
from tracecat.workspace_sync.transport import GitLabWorkspaceSyncTransport


@dataclass(frozen=True)
class _Commit:
    sha: str
    message: str
    files: dict[str, str]


class _MockGitLabApi:
    """Small in-memory subset of the GitLab REST API used by the transport."""

    def __init__(
        self,
        *,
        project_path: str,
        files: Mapping[str, str],
        default_branch: str = "main",
    ) -> None:
        self.project_path = project_path
        self.encoded_project_path = quote(project_path, safe="")
        self.default_branch = default_branch
        self.raw_paths: list[str] = []
        self.requests: list[tuple[str, str]] = []
        self.commit_payloads: list[dict[str, Any]] = []
        self.merge_request_payloads: list[dict[str, Any]] = []
        self._counter = 0
        self._commits: dict[str, _Commit] = {}
        self._branches: dict[str, str] = {}
        self._commit_branches: dict[str, str] = {}
        self._blobs: dict[str, str] = {}
        self._merge_requests: list[dict[str, Any]] = []
        initial = self._new_commit(
            branch=default_branch,
            message="Initial commit",
            files=dict(files),
        )
        self._branches[default_branch] = initial.sha

    def response(self, request: httpx.Request) -> httpx.Response:
        raw_path = request.url.raw_path.split(b"?", maxsplit=1)[0].decode()
        self.raw_paths.append(raw_path)
        self.requests.append((request.method, raw_path))
        prefix = f"/api/v4/projects/{self.encoded_project_path}"
        if not raw_path.startswith(prefix):
            return httpx.Response(404, json={"message": "Project Not Found"})

        subpath = raw_path[len(prefix) :]
        try:
            if request.method == "GET" and subpath == "/repository/branches":
                page = int(request.url.params.get("page", "1"))
                per_page = int(request.url.params.get("per_page", "100"))
                branches = sorted(self._branches)
                start = (page - 1) * per_page
                end = start + per_page
                next_page = str(page + 1) if end < len(branches) else ""
                return self._json(
                    [
                        {
                            "name": branch,
                            "default": branch == self.default_branch,
                        }
                        for branch in branches[start:end]
                    ],
                    headers={"x-next-page": next_page},
                )
            if request.method == "GET" and subpath.startswith("/repository/branches/"):
                branch = unquote(subpath.removeprefix("/repository/branches/"))
                if branch not in self._branches:
                    return httpx.Response(404, json={"message": "404 Branch Not Found"})
                return self._json(
                    {"name": branch, "default": branch == self.default_branch}
                )
            if request.method == "POST" and subpath == "/repository/branches":
                branch = request.url.params["branch"]
                ref = request.url.params["ref"]
                self._branches[branch] = self._commit_at_ref(ref).sha
                return self._json({"name": branch, "default": False}, status_code=201)
            if request.method == "GET" and subpath == "/repository/commits":
                branch = request.url.params.get("ref_name", "main")
                commits = [
                    commit
                    for sha, commit in self._commits.items()
                    if self._commit_branches.get(sha) == branch
                ]
                commits.sort(key=lambda commit: commit.sha, reverse=True)
                return self._json(
                    [
                        {
                            "id": commit.sha,
                            "message": commit.message,
                            "author_name": "GitLab User",
                            "author_email": "gitlab@example.test",
                            "authored_date": "2026-01-01T00:00:00Z",
                        }
                        for commit in commits
                    ]
                )
            if request.method == "GET" and subpath.startswith("/repository/commits/"):
                ref = unquote(subpath.removeprefix("/repository/commits/"))
                commit = self._commit_at_ref(ref)
                return self._json({"id": commit.sha, "message": commit.message})
            if request.method == "GET" and subpath == "/repository/tree":
                ref = request.url.params["ref"]
                commit = self._commit_at_ref(ref)
                tree: list[dict[str, str]] = []
                for path, content in sorted(commit.files.items()):
                    blob_id = f"{commit.sha}:{path}"
                    self._blobs[blob_id] = content
                    tree.append({"id": blob_id, "type": "blob", "path": path})
                return self._json(tree)
            if (
                request.method == "GET"
                and subpath.startswith("/repository/blobs/")
                and subpath.endswith("/raw")
            ):
                blob_id = unquote(
                    subpath.removeprefix("/repository/blobs/").removesuffix("/raw")
                )
                if blob_id not in self._blobs:
                    return httpx.Response(404, json={"message": "404 Blob Not Found"})
                return httpx.Response(200, content=self._blobs[blob_id].encode())
            if request.method == "POST" and subpath == "/repository/commits":
                payload = json.loads(request.content.decode())
                self.commit_payloads.append(payload)
                commit = self.commit_actions(
                    branch=payload["branch"],
                    message=payload["commit_message"],
                    actions=payload["actions"],
                )
                return self._json({"id": commit.sha}, status_code=201)
            if request.method == "GET" and subpath == "/repository/compare":
                base_commit = self._commit_at_ref(request.url.params["from"])
                target_commit = self._commit_at_ref(request.url.params["to"])
                return self._json(
                    {
                        "commits": []
                        if base_commit.sha == target_commit.sha
                        else [{"id": target_commit.sha}]
                    }
                )
            if request.method == "GET" and subpath == "/merge_requests":
                source_branch = request.url.params.get("source_branch")
                target_branch = request.url.params.get("target_branch")
                state = request.url.params.get("state")
                return self._json(
                    [
                        mr
                        for mr in self._merge_requests
                        if mr["source_branch"] == source_branch
                        and mr["target_branch"] == target_branch
                        and mr["state"] == state
                    ]
                )
            if request.method == "POST" and subpath == "/merge_requests":
                payload = json.loads(request.content.decode())
                self.merge_request_payloads.append(payload)
                mr = self.add_merge_request(
                    source_branch=payload["source_branch"],
                    target_branch=payload["target_branch"],
                    title=payload["title"],
                )
                return self._json(mr, status_code=201)
        except KeyError as e:
            return httpx.Response(404, json={"message": f"Not Found: {str(e)}"})

        return httpx.Response(404, json={"message": f"Unhandled path {subpath}"})

    def create_branch(self, branch: str, ref: str) -> None:
        self._branches[branch] = self._commit_at_ref(ref).sha

    def commit_actions(
        self,
        *,
        branch: str,
        message: str,
        actions: list[dict[str, str]],
    ) -> _Commit:
        current = self._commit_at_ref(branch)
        files = dict(current.files)
        for action in actions:
            file_path = action["file_path"]
            match action["action"]:
                case "create" | "update":
                    files[file_path] = action["content"]
                case "delete":
                    files.pop(file_path, None)
                case unexpected:
                    raise ValueError(f"Unexpected action {unexpected}")
        commit = self._new_commit(branch=branch, message=message, files=files)
        self._branches[branch] = commit.sha
        return commit

    def add_merge_request(
        self,
        *,
        source_branch: str,
        target_branch: str,
        title: str = "Sync",
    ) -> dict[str, Any]:
        iid = len(self._merge_requests) + 1
        mr = {
            "iid": iid,
            "title": title,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "state": "opened",
            "web_url": (
                f"https://gitlab.example.test/{self.project_path}/-/merge_requests/{iid}"
            ),
        }
        self._merge_requests.append(mr)
        return mr

    def files_at_ref(self, ref: str) -> dict[str, str]:
        return dict(self._commit_at_ref(ref).files)

    def _commit_at_ref(self, ref: str) -> _Commit:
        sha = self._branches.get(ref, ref)
        return self._commits[sha]

    def _new_commit(
        self,
        *,
        branch: str,
        message: str,
        files: dict[str, str],
    ) -> _Commit:
        self._counter += 1
        sha = f"{self._counter:040x}"
        commit = _Commit(sha=sha, message=message, files=dict(files))
        self._commits[sha] = commit
        self._commit_branches[sha] = branch
        return commit

    @staticmethod
    def _json(
        value: Any,
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return httpx.Response(status_code, json=value, headers=headers)


class _MockGitLabTransport(GitLabWorkspaceSyncTransport):
    def __init__(self, *, api: _MockGitLabApi) -> None:
        self._api = api
        super().__init__(
            session=AsyncMock(),
            role=Role(
                type="user",
                workspace_id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
                user_id=None,
                service_id="tracecat-api",
                scopes=ADMIN_SCOPES,
            ),
        )

    async def _credentials(self) -> GitLabTokenCredentials:
        return GitLabTokenCredentials(
            base_url="https://gitlab.example.test",
            token=SecretStr("test-token"),
        )

    def _client(self, credentials: GitLabTokenCredentials) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{credentials.base_url}/api/v4",
            headers={"PRIVATE-TOKEN": credentials.token.get_secret_value()},
            transport=httpx.MockTransport(self._api.response),
        )


def _git_url() -> GitUrl:
    return GitUrl(
        host="gitlab.example.test",
        org="group/subgroup",
        repo="project",
    )


def _git_url_with_ssh_port() -> GitUrl:
    return GitUrl(
        host="gitlab.example.test:2222",
        org="group/subgroup",
        repo="project",
    )


def _manifest() -> str:
    return canonical_json_text(WorkspaceManifest())


def _gitlab_settings_role(*scopes: str) -> Role:
    return Role(
        type="user",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset(scopes),
    )


@pytest.mark.anyio
async def test_gitlab_transport_encodes_nested_project_path_and_lists_refs() -> None:
    api = _MockGitLabApi(
        project_path="group/subgroup/project",
        files={MANIFEST_FILENAME: _manifest()},
    )
    api.commit_actions(
        branch="main",
        message="Second commit",
        actions=[{"action": "create", "file_path": "workflows/a.yml", "content": "a"}],
    )
    transport = _MockGitLabTransport(api=api)

    branches = await transport.list_branches(url=_git_url(), limit=10)
    commits = await transport.list_commits(url=_git_url(), branch="main", limit=10)

    assert [(branch.name, branch.is_default) for branch in branches] == [("main", True)]
    assert commits[0].message == "Second commit"
    assert (
        "/api/v4/projects/group%2Fsubgroup%2Fproject/repository/branches"
        in api.raw_paths
    )


@pytest.mark.anyio
async def test_gitlab_transport_list_branches_includes_default_beyond_limit() -> None:
    api = _MockGitLabApi(
        project_path="group/subgroup/project",
        files={MANIFEST_FILENAME: _manifest()},
        default_branch="zz-default",
    )
    for index in range(105):
        api.create_branch(f"branch-{index:03d}", "zz-default")
    transport = _MockGitLabTransport(api=api)

    branches = await transport.list_branches(url=_git_url(), limit=10)

    assert len(branches) == 10
    assert [(branch.name, branch.is_default) for branch in branches] == [
        ("branch-000", False),
        ("branch-001", False),
        ("branch-002", False),
        ("branch-003", False),
        ("branch-004", False),
        ("branch-005", False),
        ("branch-006", False),
        ("branch-007", False),
        ("branch-008", False),
        ("zz-default", True),
    ]


@pytest.mark.anyio
async def test_gitlab_transport_reads_manifest_roots_and_blobs() -> None:
    api = _MockGitLabApi(
        project_path="group/subgroup/project",
        files={
            MANIFEST_FILENAME: _manifest(),
            "workflows/a.yml": "workflow",
            "README.md": "outside managed roots",
        },
    )
    transport = _MockGitLabTransport(api=api)

    snapshot = await transport.read_files(url=_git_url(), ref="main")

    assert snapshot.commit_sha == "0" * 39 + "1"
    assert snapshot.tree_sha is None
    assert snapshot.files == {
        MANIFEST_FILENAME: _manifest(),
        "workflows/a.yml": "workflow",
    }


@pytest.mark.anyio
async def test_gitlab_transport_allows_repo_ssh_port_on_same_instance() -> None:
    api = _MockGitLabApi(
        project_path="group/subgroup/project",
        files={MANIFEST_FILENAME: _manifest()},
    )
    transport = _MockGitLabTransport(api=api)

    snapshot = await transport.read_files(url=_git_url_with_ssh_port(), ref="main")

    assert snapshot.commit_sha == "0" * 39 + "1"


@pytest.mark.anyio
async def test_gitlab_transport_commits_create_update_delete_actions() -> None:
    api = _MockGitLabApi(
        project_path="group/subgroup/project",
        files={
            MANIFEST_FILENAME: _manifest(),
            "workflows/old.yml": "old",
            "workflows/stale.yml": "stale",
            "README.md": "preserved",
        },
    )
    transport = _MockGitLabTransport(api=api)

    result = await transport.write_files(
        url=_git_url(),
        files={
            MANIFEST_FILENAME: _manifest(),
            "workflows/old.yml": "new",
            "workflows/new.yml": "created",
        },
        message="Sync workspace",
        branch="sync/workspace",
        create_pr=False,
        delete_missing_paths_under=("workflows",),
    )

    assert result.status is PushStatus.COMMITTED
    assert result.ref == "sync/workspace"
    assert result.base_ref == "main"
    assert api.files_at_ref("sync/workspace") == {
        MANIFEST_FILENAME: _manifest(),
        "workflows/old.yml": "new",
        "workflows/new.yml": "created",
        "README.md": "preserved",
    }
    assert api.commit_payloads[0]["actions"] == [
        {
            "action": "create",
            "file_path": "workflows/new.yml",
            "content": "created",
        },
        {
            "action": "update",
            "file_path": "workflows/old.yml",
            "content": "new",
        },
        {"action": "delete", "file_path": "workflows/stale.yml"},
    ]


@pytest.mark.anyio
async def test_gitlab_transport_noop_reuses_existing_merge_request() -> None:
    api = _MockGitLabApi(
        project_path="group/subgroup/project",
        files={MANIFEST_FILENAME: _manifest(), "workflows/a.yml": "main"},
    )
    api.create_branch("sync/workspace", "main")
    api.commit_actions(
        branch="sync/workspace",
        message="Existing branch commit",
        actions=[
            {
                "action": "update",
                "file_path": "workflows/a.yml",
                "content": "branch",
            }
        ],
    )
    existing_mr = api.add_merge_request(
        source_branch="sync/workspace",
        target_branch="main",
    )
    transport = _MockGitLabTransport(api=api)

    result = await transport.write_files(
        url=_git_url(),
        files={MANIFEST_FILENAME: _manifest(), "workflows/a.yml": "branch"},
        message="Sync workspace",
        branch="sync/workspace",
        create_pr=True,
        delete_missing_paths_under=("workflows",),
    )

    assert result.status is PushStatus.NO_OP
    assert result.pr_url == existing_mr["web_url"]
    assert result.pr_number == existing_mr["iid"]
    assert result.pr_reused is True
    assert api.commit_payloads == []
    assert api.merge_request_payloads == []


@pytest.mark.anyio
async def test_gitlab_transport_creates_merge_request_after_commit() -> None:
    api = _MockGitLabApi(
        project_path="group/subgroup/project",
        files={MANIFEST_FILENAME: _manifest(), "workflows/a.yml": "main"},
    )
    transport = _MockGitLabTransport(api=api)

    with patch("tracecat.workspace_sync.transport.WorkspaceService") as workspace_cls:
        workspace_service = AsyncMock()
        workspace_service.get_workspace.return_value = type(
            "WorkspaceStub",
            (),
            {"name": "Sync workspace"},
        )()
        workspace_cls.return_value = workspace_service

        result = await transport.write_files(
            url=_git_url(),
            files={MANIFEST_FILENAME: _manifest(), "workflows/a.yml": "branch"},
            message="Sync workspace",
            branch="sync/workspace",
            create_pr=True,
            delete_missing_paths_under=("workflows",),
        )

    assert result.status is PushStatus.COMMITTED
    assert result.pr_url == (
        "https://gitlab.example.test/group/subgroup/project/-/merge_requests/1"
    )
    assert result.pr_number == 1
    assert result.pr_reused is False
    assert api.merge_request_payloads[0]["source_branch"] == "sync/workspace"
    assert api.merge_request_payloads[0]["target_branch"] == "main"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("url", "pr_base_branch"),
    [
        (_git_url(), "release/base"),
        (
            GitUrl(
                host="gitlab.example.test",
                org="group/subgroup",
                repo="project",
                ref="release/base",
            ),
            None,
        ),
    ],
    ids=["pr-base-branch", "url-ref"],
)
async def test_gitlab_transport_skips_branch_list_when_base_is_explicit(
    url: GitUrl,
    pr_base_branch: str | None,
) -> None:
    api = _MockGitLabApi(
        project_path="group/subgroup/project",
        files={MANIFEST_FILENAME: _manifest(), "workflows/a.yml": "base"},
        default_branch="zz-default",
    )
    api.create_branch("release/base", "zz-default")
    for index in range(105):
        api.create_branch(f"branch-{index:03d}", "zz-default")
    transport = _MockGitLabTransport(api=api)

    with patch("tracecat.workspace_sync.transport.WorkspaceService") as workspace_cls:
        workspace_service = AsyncMock()
        workspace_service.get_workspace.return_value = type(
            "WorkspaceStub",
            (),
            {"name": "Sync workspace"},
        )()
        workspace_cls.return_value = workspace_service

        result = await transport.write_files(
            url=url,
            files={MANIFEST_FILENAME: _manifest(), "workflows/a.yml": "branch"},
            message="Sync workspace",
            branch="sync/workspace",
            create_pr=True,
            pr_base_branch=pr_base_branch,
            delete_missing_paths_under=("workflows",),
        )

    branch_list_path = (
        f"/api/v4/projects/{api.encoded_project_path}/repository/branches"
    )
    assert ("GET", branch_list_path) not in api.requests
    assert result.status is PushStatus.COMMITTED
    assert api.merge_request_payloads[0]["target_branch"] == "release/base"


@pytest.mark.anyio
async def test_gitlab_transport_resolves_default_branch_beyond_first_page() -> None:
    api = _MockGitLabApi(
        project_path="group/subgroup/project",
        files={MANIFEST_FILENAME: _manifest(), "workflows/a.yml": "main"},
        default_branch="zz-default",
    )
    for index in range(105):
        api.create_branch(f"branch-{index:03d}", "zz-default")
    transport = _MockGitLabTransport(api=api)

    with patch("tracecat.workspace_sync.transport.WorkspaceService") as workspace_cls:
        workspace_service = AsyncMock()
        workspace_service.get_workspace.return_value = type(
            "WorkspaceStub",
            (),
            {"name": "Sync workspace"},
        )()
        workspace_cls.return_value = workspace_service

        result = await transport.write_files(
            url=GitUrl(
                host="gitlab.example.test",
                org="group/subgroup",
                repo="project",
            ),
            files={MANIFEST_FILENAME: _manifest(), "workflows/a.yml": "branch"},
            message="Sync workspace",
            branch="sync/workspace",
            create_pr=True,
            delete_missing_paths_under=("workflows",),
        )

    assert result.status is PushStatus.COMMITTED
    assert api.merge_request_payloads[0]["target_branch"] == "zz-default"


@pytest.mark.anyio
async def test_gitlab_token_save_creates_org_secret_with_settings_scope() -> None:
    created_params: list[Any] = []

    class FakeSecretsService:
        def __init__(self, *, session: Any, role: Role) -> None:
            del session, role

        async def _get_org_secret_by_name(
            self, secret_name: str, environment: str | None = None
        ) -> Any:
            del secret_name, environment
            raise TracecatNotFoundError("missing")

        async def _create_org_secret(self, params: Any) -> None:
            created_params.append(params)

    service = GitLabTokenService(
        session=AsyncMock(),
        role=_gitlab_settings_role("org:settings:update"),
    )

    with (
        patch.object(service, "require_entitlement", new=AsyncMock()),
        patch("tracecat.vcs.gitlab.app.SecretsService", FakeSecretsService),
    ):
        credentials, was_created = await service.save_gitlab_token_credentials(
            base_url="https://gitlab.example.test",
            token=SecretStr("new-token"),
        )

    assert credentials.base_url == "https://gitlab.example.test"
    assert was_created is True
    assert created_params[0].name == GITLAB_TOKEN_SECRET_NAME


@pytest.mark.anyio
async def test_gitlab_token_save_updates_org_secret_with_settings_scope() -> None:
    existing_secret = SimpleNamespace(id=uuid.uuid4(), encrypted_keys=b"encrypted")
    updated_calls: list[tuple[Any, Any]] = []

    class FakeSecretsService:
        def __init__(self, *, session: Any, role: Role) -> None:
            del session, role

        async def _get_org_secret_by_name(
            self, secret_name: str, environment: str | None = None
        ) -> Any:
            del secret_name, environment
            return existing_secret

        def decrypt_keys(self, encrypted_keys: bytes) -> list[SecretKeyValue]:
            assert encrypted_keys == b"encrypted"
            return [
                SecretKeyValue(
                    key="base_url",
                    value=SecretStr("https://gitlab.example.test"),
                ),
                SecretKeyValue(key="token", value=SecretStr("old-token")),
            ]

        async def _update_org_secret(self, secret: Any, params: Any) -> None:
            updated_calls.append((secret, params))

    service = GitLabTokenService(
        session=AsyncMock(),
        role=_gitlab_settings_role("org:settings:update"),
    )

    with (
        patch.object(service, "require_entitlement", new=AsyncMock()),
        patch("tracecat.vcs.gitlab.app.SecretsService", FakeSecretsService),
    ):
        credentials, was_created = await service.save_gitlab_token_credentials(
            base_url="https://gitlab.example.test",
            token=SecretStr("new-token"),
        )

    assert credentials.token.get_secret_value() == "new-token"
    assert was_created is False
    assert updated_calls[0][0] is existing_secret


@pytest.mark.anyio
async def test_gitlab_token_status_requires_settings_read_scope() -> None:
    service = GitLabTokenService(
        session=AsyncMock(),
        role=_gitlab_settings_role(),
    )

    with patch.object(service, "require_entitlement", new=AsyncMock()):
        with pytest.raises(ScopeDeniedError) as exc_info:
            await service.get_gitlab_token_credentials_status()

    assert "org:settings:read" in exc_info.value.missing_scopes
