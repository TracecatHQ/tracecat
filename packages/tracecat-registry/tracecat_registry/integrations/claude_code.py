"""Claude Code integration.

Sandbox options:
- [x] Modal sandbox
- [ ] AWS Lambda (enterprise only)

Supports GitHub repo download and in-sandbox MCP server via Modal tunnel (https://modal.com/docs/guide/tunnels) and uvx.
https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28#download-a-repository-archive-tar
"""

import modal
from typing import Annotated, Literal

from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets
from tracecat_registry.core.agent import anthropic_secret, bedrock_secret


modal_secret = RegistrySecret(
    name="modal",
    keys=["MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"],
)
"""Modal API key.

- name: `modal`
- keys:
    - `MODAL_TOKEN_ID`
    - `MODAL_TOKEN_SECRET`
"""


SANDBOX_APP_NAME = "tracecat-claude-code"
SANDBOX_NAME = "claude-code"


def _get_sandbox(timeout: int, block_network: bool) -> modal.Sandbox:
    """Create (or look up) the Modal app and sandbox image for Claude Code."""

    anthropic_api_key = secrets.get("ANTHROPIC_API_KEY")
    anthropic_secret = modal.Secret.from_dict({"ANTHROPIC_API_KEY": anthropic_api_key})

    image = (
        modal.Image.debian_slim()
        .apt_install("nodejs", "pnpm")
        .run_commands("pnpm install -g @anthropic-ai/claude-code@1.0.117")
    )

    app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
    sandbox = modal.Sandbox.create(
        app=app,
        image=image,
        secrets=[anthropic_secret],
        timeout=timeout,
        block_network=block_network,
    )
    return sandbox


@registry.register(
    default_title="Claude Code",
    description="Claude Code CLI",
    display_group="Anthropic",
    doc_url="https://docs.claude.com/en/docs/claude-code/sdk/sdk-headless",
    namespace="ai.claude_code",
    secrets=[anthropic_secret, bedrock_secret, modal_secret],
)
def claude_code(
    prompt: Annotated[
        str,
        Field(
            ...,
            description="Your prompt.",
            min_length=1,
            max_length=10000,
        ),
    ],
    git_repo: Annotated[
        str | None,
        Field(
            ...,
            description="If provided, downloads the specified Git repository into the sandbox. E.g. 'owner/repo'",
        ),
    ],
    permission_mode: Annotated[
        Literal["acceptEdits", "bypassPermissions", "default", "plan"],
        Field(
            ...,
            description="Claude Code permission mode.",
        ),
    ] = "default",
    timeout: Annotated[
        int,
        Field(
            ...,
            description="Lifetime of the sandbox in seconds.",
        ),
    ] = 600,
    block_network: Annotated[
        bool,
        Field(
            ...,
            description="Whether to block network access for the sandbox.",
        ),
    ] = False,
) -> str:
    """Run a user-provided command inside a pre-configured Modal sandbox with Claude Code CLI."""

    sandbox = _get_sandbox(timeout=timeout, block_network=block_network)

    # If a GitHub repo is provided, download it into the sandbox
    # Execute the command with Claude Code CLI
    output = sandbox.exec(
        "claude",
        "-p",
        f"--permission-mode={permission_mode}",
        prompt,
    )

    returncode = output.returncode
    if returncode != 0:
        stdout = output.stdout.read()
        stderr = output.stderr.read()
        raise RuntimeError(
            f"Sandbox command exited with status {returncode}.\n"
            f"stdout: {stdout}\n"
            f"stderr: {stderr}"
        )

    return output.stdout.read()
