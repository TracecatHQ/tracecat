"""Git-based workflow store implementation."""

import fnmatch
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path

from tracecat.git import run_git
from tracecat.identifiers.workflow import LEGACY_WF_ID_PATTERN, WF_ID_SHORT_PATTERN
from tracecat.logger import logger
from tracecat.workflows.store import ExternalWorkflowStore, WorkflowSource


class GitWorkflowStore(ExternalWorkflowStore):
    """Git-based external workflow storage implementation.

    Provides access to workflow YAML files stored in a Git repository by
    performing sparse checkout of specific commits and enumerating matching files.
    """

    def __init__(
        self,
        repo_url: str,
        commit_sha: str,
        *,
        include_globs: list[str] | None = None,
        work_dir: Path | None = None,
        env: dict[str, str] | None = None,
    ):
        """Initialize the Git workflow store.

        Args:
            repo_url: Git repository URL
            commit_sha: Specific commit SHA to checkout
            include_globs: File patterns to include (default: ["**/*.yml", "**/*.yaml"])
            work_dir: Working directory for git operations (temp dir if None)
            env: Environment variables for git commands (SSH config, etc.)
        """
        self.repo_url = repo_url
        self.commit_sha = commit_sha
        self.include_globs = include_globs or ["**/*.yml", "**/*.yaml"]
        self.work_dir = work_dir
        self.env = env or {}
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._work_path: Path | None = None

    async def list_sources(self) -> Iterable[WorkflowSource]:
        """List all workflow sources in the repository.

        Returns:
            Iterable of WorkflowSource objects with path, SHA, and workflow_id
        """
        work_path = await self._ensure_work_dir()

        # Initialize repository if not already done
        if not (work_path / ".git").exists():
            await self._init_repository(work_path)

        # Get list of files from the commit
        file_paths = await self._list_files_from_commit()

        sources = []
        for path in file_paths:
            # Check if file matches any include glob
            if not any(fnmatch.fnmatch(path, glob) for glob in self.include_globs):
                continue

            # Extract workflow_id from filename
            workflow_id = self._extract_workflow_id(path)
            if workflow_id is None:
                logger.debug(f"Skipping file {path}: no workflow ID found in filename")
                continue

            sources.append(
                WorkflowSource(
                    path=path,
                    sha=self.commit_sha,
                    workflow_id=workflow_id,
                )
            )

        return sources

    async def fetch_yaml(self, path: str, sha: str) -> str:
        """Fetch YAML content for a specific file and commit.

        Args:
            path: File path within the repository
            sha: Commit SHA (should match store's commit_sha)

        Returns:
            YAML content as string

        Raises:
            ValueError: If SHA doesn't match or file not found
        """
        if sha != self.commit_sha:
            raise ValueError(f"SHA mismatch: expected {self.commit_sha}, got {sha}")

        work_path = await self._ensure_work_dir()

        # Use git show to get file content at specific commit
        returncode, stdout, stderr = await run_git(
            ["git", "show", f"{self.commit_sha}:{path}"],
            env=self.env,
            cwd=str(work_path),
            timeout=30.0,
        )

        if returncode != 0:
            logger.error(f"Failed to fetch {path} at {sha}: {stderr}")
            raise ValueError(f"Failed to fetch file {path}: {stderr}")

        return stdout

    async def _ensure_work_dir(self) -> Path:
        """Ensure working directory exists and return its path."""
        if self._work_path is None:
            if self.work_dir is not None:
                self._work_path = self.work_dir
                self._work_path.mkdir(parents=True, exist_ok=True)
            else:
                self._temp_dir = tempfile.TemporaryDirectory()
                self._work_path = Path(self._temp_dir.name)
        return self._work_path

    async def _init_repository(self, work_path: Path) -> None:
        """Initialize git repository and fetch the specific commit."""
        logger.info(f"Initializing git repository in {work_path}")

        # Initialize empty repository
        returncode, stdout, stderr = await run_git(
            ["git", "init"],
            env=self.env,
            cwd=str(work_path),
            timeout=10.0,
        )
        if returncode != 0:
            raise RuntimeError(f"Failed to initialize git repository: {stderr}")

        # Add remote origin
        returncode, stdout, stderr = await run_git(
            ["git", "remote", "add", "origin", self.repo_url],
            env=self.env,
            cwd=str(work_path),
            timeout=10.0,
        )
        if returncode != 0:
            raise RuntimeError(f"Failed to add remote origin: {stderr}")

        # Fetch specific commit with depth 1
        returncode, stdout, stderr = await run_git(
            ["git", "fetch", "--depth=1", "origin", self.commit_sha],
            env=self.env,
            cwd=str(work_path),
            timeout=60.0,
        )
        if returncode != 0:
            raise RuntimeError(f"Failed to fetch commit {self.commit_sha}: {stderr}")

        # Checkout the commit
        returncode, stdout, stderr = await run_git(
            ["git", "checkout", "--detach", self.commit_sha],
            env=self.env,
            cwd=str(work_path),
            timeout=10.0,
        )
        if returncode != 0:
            raise RuntimeError(f"Failed to checkout commit {self.commit_sha}: {stderr}")

    async def _list_files_from_commit(self) -> list[str]:
        """List all files in the specific commit."""
        work_path = await self._ensure_work_dir()

        returncode, stdout, stderr = await run_git(
            ["git", "ls-tree", "-r", "--name-only", self.commit_sha],
            env=self.env,
            cwd=str(work_path),
            timeout=30.0,
        )

        if returncode != 0:
            raise RuntimeError(
                f"Failed to list files from commit {self.commit_sha}: {stderr}"
            )

        return [line.strip() for line in stdout.splitlines() if line.strip()]

    def _extract_workflow_id(self, file_path: str) -> str | None:
        """Extract workflow ID from file path using regex patterns.

        Args:
            file_path: Path to the workflow file

        Returns:
            Workflow ID if found, None otherwise
        """
        filename = Path(file_path).name

        # Try short pattern first (e.g., wf_ABC123)
        match = re.search(WF_ID_SHORT_PATTERN, filename)
        if match:
            return match.group(0)

        # Fallback to legacy pattern (e.g., wf-1234567890abcdef...)
        match = re.search(LEGACY_WF_ID_PATTERN, filename)
        if match:
            return match.group(0)

        return None

    def __del__(self) -> None:
        """Clean up temporary directory if created."""
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
