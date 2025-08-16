"""GitHub API client for workflow store operations using PyGithub."""

from __future__ import annotations

from typing import Any

from github import Auth, Github
from github.GithubException import GithubException
from pydantic import BaseModel

from tracecat.logger import logger
from tracecat.vcs.github.models import GitHubPullRequest, GitHubRepository


class GitHubClientError(Exception):
    """GitHub client error."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class PullRequestCreate(BaseModel):
    """Pull request creation data."""

    title: str
    body: str
    head: str  # Branch name
    base: str  # Target branch
    draft: bool = False


class GitHubClient:
    """GitHub API client using PyGithub with installation tokens."""

    def __init__(self, token: str, repo_owner: str, repo_name: str):
        """Initialize GitHub client.

        Args:
            token: GitHub installation access token
            repo_owner: Repository owner (org or user)
            repo_name: Repository name
        """
        self.token = token
        self.repo_owner = repo_owner
        self.repo_name = repo_name

        # Initialize PyGithub with authentication
        auth = Auth.Token(token)
        self.github = Github(auth=auth)

        self.logger = logger.bind(
            service="github_client",
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.github.close()

    def _handle_github_exception(self, e: GithubException, operation: str) -> None:
        """Handle PyGithub exceptions and convert to GitHubAPIError."""
        self.logger.error(
            f"GitHub API error during {operation}",
            status_code=e.status,
            data=e.data,
        )

        raise GitHubClientError(
            message=f"GitHub API error: {e.status} - {e.data.get('message', 'Unknown error')}",
            status_code=e.status,
            response=e.data,
        ) from e

    async def create_pull_request(
        self, pr_data: PullRequestCreate
    ) -> GitHubPullRequest:
        """Create a pull request.

        Args:
            pr_data: Pull request creation data

        Returns:
            Created pull request data
        """
        self.logger.info(
            "Creating pull request",
            title=pr_data.title,
            head=pr_data.head,
            base=pr_data.base,
        )

        try:
            repo = self.github.get_repo(f"{self.repo_owner}/{self.repo_name}")

            github_pr = repo.create_pull(
                title=pr_data.title,
                body=pr_data.body,
                head=pr_data.head,
                base=pr_data.base,
                draft=pr_data.draft,
            )

            pr = GitHubPullRequest(
                number=github_pr.number,
                title=github_pr.title,
                body=github_pr.body or "",
                html_url=github_pr.html_url,
                head_branch=github_pr.head.ref,
                base_branch=github_pr.base.ref,
                state=github_pr.state,
                created_at=github_pr.created_at.isoformat()
                if github_pr.created_at
                else "",
                updated_at=github_pr.updated_at.isoformat()
                if github_pr.updated_at
                else "",
            )

            self.logger.info(
                "Created pull request",
                pr_number=pr.number,
                pr_url=pr.html_url,
            )

            return pr

        except GithubException as e:
            self._handle_github_exception(e, "create_pull_request")
            raise  # This ensures the function doesn't implicitly return None
        except Exception as e:
            self.logger.error("Unexpected error creating pull request", error=str(e))
            raise GitHubClientError(
                f"Unexpected error creating pull request: {e}"
            ) from e

    async def list_repositories(self, installation_id: int) -> list[GitHubRepository]:
        """List repositories accessible to the installation.

        Args:
            installation_id: GitHub App installation ID

        Returns:
            List of accessible repositories
        """
        try:
            # For installation tokens, we need to use the paginated API endpoint directly
            # PyGithub doesn't have a direct method for this, so we'll iterate through accessible repos
            repos = self.github.get_repos()

            repositories = []
            for repo in repos:
                repository = GitHubRepository(
                    id=repo.id,
                    name=repo.name,
                    full_name=repo.full_name,
                    private=repo.private,
                    default_branch=repo.default_branch or "main",
                )
                repositories.append(repository)

            return repositories

        except GithubException as e:
            self._handle_github_exception(e, "list_repositories")
            raise  # This ensures the function doesn't implicitly return None
        except Exception as e:
            self.logger.error("Unexpected error listing repositories", error=str(e))
            raise GitHubClientError(
                f"Unexpected error listing repositories: {e}"
            ) from e

    async def get_repository(self) -> GitHubRepository:
        """Get repository information.

        Returns:
            Repository data
        """
        try:
            repo = self.github.get_repo(f"{self.repo_owner}/{self.repo_name}")

            return GitHubRepository(
                id=repo.id,
                name=repo.name,
                full_name=repo.full_name,
                private=repo.private,
                default_branch=repo.default_branch or "main",
            )

        except GithubException as e:
            self._handle_github_exception(e, "get_repository")
            raise  # This ensures the function doesn't implicitly return None
        except Exception as e:
            self.logger.error("Unexpected error getting repository", error=str(e))
            raise GitHubClientError(f"Unexpected error getting repository: {e}") from e

    async def check_permissions(self) -> dict[str, str]:
        """Check repository permissions for the installation.

        Returns:
            Permission mapping
        """
        try:
            repo = self.github.get_repo(f"{self.repo_owner}/{self.repo_name}")

            # Get repository permissions
            permissions = repo.permissions

            return {
                "admin": str(permissions.admin).lower(),
                "maintain": str(permissions.maintain).lower(),
                "push": str(permissions.push).lower(),
                "pull": str(permissions.pull).lower(),
            }

        except GithubException as e:
            self._handle_github_exception(e, "check_permissions")
            raise  # This ensures the function doesn't implicitly return None
        except Exception as e:
            self.logger.error("Unexpected error checking permissions", error=str(e))
            raise GitHubClientError(
                f"Unexpected error checking permissions: {e}"
            ) from e
