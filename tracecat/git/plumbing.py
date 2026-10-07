"""Isolated Git plumbing shared by workspace sync providers.

No checkout, hooks, repository configuration, or credentials on the command line.
"""

import asyncio
import base64
import os
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from pydantic import SecretStr


@dataclass(frozen=True, slots=True)
class GitEntry:
    mode: str
    sha: str


class GitRefConflictError(RuntimeError):
    """The destination ref no longer matches the reviewed head."""


def validate_path(path: str, error_type: type[Exception] = RuntimeError) -> None:
    """Reject ambiguous Git index paths without touching a working tree."""
    parts = PurePosixPath(path).parts
    if (
        not path
        or path.startswith("/")
        or any(part in {".", "..", ".git"} for part in path.lower().split("/"))
        or "\x00" in path
        or "\\" in path
        or str(PurePosixPath(path)) != path
        or not parts
    ):
        raise error_type("Invalid workspace sync file path")


class GitPlumbing:
    """A disposable bare repository whose authentication lives only in its environment."""

    def __init__(
        self,
        directory: str,
        token: SecretStr,
        *,
        credential_url: str,
        username: str = "oauth2",
        error_type: type[Exception] = RuntimeError,
        bearer: bool = False,
    ) -> None:
        self.directory = directory
        self.error_type = error_type
        basic = base64.b64encode(
            f"{username}:{token.get_secret_value()}".encode()
        ).decode()
        self.env = {
            "PATH": os.defpath,
            "HOME": directory,
            "LANG": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "5",
            "GIT_CONFIG_KEY_0": f"http.{credential_url}.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Bearer {token.get_secret_value()}"
            if bearer
            else f"Authorization: Basic {basic}",
            "GIT_CONFIG_KEY_1": "core.hooksPath",
            "GIT_CONFIG_VALUE_1": os.devnull,
            "GIT_CONFIG_KEY_2": "http.followRedirects",
            "GIT_CONFIG_VALUE_2": "false",
            "GIT_CONFIG_KEY_3": "protocol.allow",
            "GIT_CONFIG_VALUE_3": "never",
            "GIT_CONFIG_KEY_4": f"protocol.{urlsplit(credential_url).scheme}.allow",
            "GIT_CONFIG_VALUE_4": "always",
            "GIT_AUTHOR_NAME": "Tracecat",
            "GIT_AUTHOR_EMAIL": "sync@tracecat.com",
            "GIT_COMMITTER_NAME": "Tracecat",
            "GIT_COMMITTER_EMAIL": "sync@tracecat.com",
        }

        if ca_file := os.environ.get("SSL_CERT_FILE"):
            self.env["GIT_SSL_CAINFO"] = ca_file

    async def run(self, *args: str, data: bytes | None = None) -> bytes:
        """Run without logging arguments, environment, or remote error output."""
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=self.directory,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(data), timeout=120
            )
        except (TimeoutError, asyncio.CancelledError):
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise self.error_type(
                "Git operation failed. Check credentials, repository access, "
                "and branch permissions. If the remote branch changed, preview and retry."
            )
        return stdout

    async def initialize(self) -> None:
        await self.run("init", "--bare", "--template=", ".")

    async def fetch(self, remote: str, ref: str) -> str:
        # Pass only a fully qualified branch or a commit SHA, never revision syntax.
        if not re.fullmatch(r"[0-9a-fA-F]{40}", ref):
            await self.run("check-ref-format", f"refs/heads/{ref}")
            ref = f"refs/heads/{ref}"
        await self.run(
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            "--depth=1",
            "--",
            remote,
            ref,
        )
        return (
            (await self.run("rev-parse", "--verify", "FETCH_HEAD^{commit}"))
            .decode()
            .strip()
        )

    async def entries(self, sha: str) -> dict[str, GitEntry]:
        result: dict[str, GitEntry] = {}
        for record in (await self.run("ls-tree", "-r", "-z", sha)).split(b"\0"):
            if not record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, object_sha = metadata.decode().split()
            if kind == "blob":
                # Git paths are byte strings; preserve non-UTF-8 names reversibly.
                result[raw_path.decode("utf-8", errors="surrogateescape")] = GitEntry(
                    mode, object_sha
                )
        return result

    async def commit(
        self, parent: str, files: dict[str, str], deleted: set[str], message: str
    ) -> str | None:
        """Build one tree/commit, preserving every path outside the requested edits."""
        for path in files.keys() | deleted:
            validate_path(path, self.error_type)
        await self.run("read-tree", parent)
        entries = await self.entries(parent)
        records: list[bytes] = []
        for path in sorted(deleted):
            records.append(
                f"0 {'0' * 40}\t{path}".encode("utf-8", errors="surrogateescape")
                + b"\0"
            )
        for path, content in files.items():
            sha = (
                (await self.run("hash-object", "-w", "--stdin", data=content.encode()))
                .decode()
                .strip()
            )
            entry = entries.get(path)
            mode = (
                entry.mode if entry and entry.mode in {"100644", "100755"} else "100644"
            )
            records.append(
                f"{mode} {sha}\t{path}".encode("utf-8", errors="surrogateescape")
                + b"\0"
            )
        await self.run("update-index", "-z", "--index-info", data=b"".join(records))
        tree = (await self.run("write-tree")).decode().strip()
        old_tree = (await self.run("rev-parse", f"{parent}^{{tree}}")).decode().strip()
        if tree == old_tree:
            return None
        return (
            (await self.run("commit-tree", tree, "-p", parent, data=message.encode()))
            .decode()
            .strip()
        )

    async def push(self, remote: str, sha: str, branch: str) -> None:
        await self.run("check-ref-format", f"refs/heads/{branch}")
        await self.run("push", "--", remote, f"{sha}:refs/heads/{branch}")

    async def branch_exists(self, remote: str, branch: str) -> bool:
        """Resolve one exact branch ref without enumerating repository branches."""
        ref = f"refs/heads/{branch}"
        await self.run("check-ref-format", ref)
        refs = await self.run("ls-remote", "--refs", "--", remote, ref)
        return any(line.split(b"\t")[-1] == ref.encode() for line in refs.splitlines())

    async def push_with_lease(
        self, remote: str, sha: str, branch: str, expected_head: str | None
    ) -> None:
        """Publish only if the entire destination head is still the reviewed SHA."""
        ref = f"refs/heads/{branch}"
        await self.run("check-ref-format", ref)
        try:
            await self.run(
                "push",
                "--porcelain",
                f"--force-with-lease={ref}:{expected_head or ''}",
                "--",
                remote,
                f"{sha}:{ref}",
            )
        except self.error_type:
            # Inspect structured ref output, never provider error-message strings.
            refs = await self.run("ls-remote", "--refs", "--", remote, ref)
            actual_head = refs.split(b"\t", 1)[0].decode().strip() if refs else None
            if actual_head == sha:
                # The server accepted the push but its response was lost.
                return
            if actual_head != expected_head:
                raise GitRefConflictError(
                    "Target branch changed; create a new preview"
                ) from None
            raise
