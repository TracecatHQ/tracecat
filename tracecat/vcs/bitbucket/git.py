"""Isolated Git plumbing for Bitbucket Cloud workspace sync.

No checkout, hooks, repository configuration, or credentials on the command line.
"""

import re

from pydantic import SecretStr

from tracecat.git.plumbing import GitPlumbing
from tracecat.git.types import GitUrl
from tracecat.vcs.bitbucket.app import BitbucketError


def repository_path(url: GitUrl) -> str:
    """Accept only Cloud workspace/repository slugs before accessing credentials."""
    if url.host.lower() != "bitbucket.org" or any(
        not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part) or part in {".", ".."}
        for part in (url.org, url.repo)
    ):
        raise BitbucketError(
            "Only Bitbucket Cloud repositories on bitbucket.org are supported"
        )
    return f"{url.org}/{url.repo}"


class BitbucketGit(GitPlumbing):
    """Provider-specific credentials for shared isolated Git plumbing."""

    def __init__(
        self,
        directory: str,
        token: SecretStr,
        *,
        credential_url: str = "https://bitbucket.org/",
        bearer: bool = False,
    ) -> None:
        super().__init__(
            directory,
            token,
            credential_url=credential_url,
            username="x-bitbucket-api-token-auth",
            bearer=bearer,
            error_type=BitbucketError,
        )
