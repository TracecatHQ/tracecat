"""Claude Code integration.

Sandbox options:
- [x] Modal sandbox
- [ ] AWS Lambda (enterprise only)

Supports GitHub repo download and in-sandbox MCP server via Modal tunnel (https://modal.com/docs/guide/tunnels) and uvx.
https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28#download-a-repository-archive-tar
"""

import json
import modal
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets
from tracecat_registry.core.agent import anthropic_secret, bedrock_secret


class StdioMcpServer(TypedDict):
    command: str
    args: list[str]
    env: NotRequired[dict[str, str]]


class RemoteMcpServer(TypedDict):
    type: Literal["sse", "http"]
    url: str
    headers: NotRequired[dict[str, str]]


type McpServer = StdioMcpServer | RemoteMcpServer


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


def _create_sandbox(
    *,
    timeout: int,
    block_network: bool = False,
    apt_packages: list[str] | None = None,
    env: dict[str, str] | None = None,
    mcp_servers: dict[str, McpServer] | None = None,
) -> modal.Sandbox:
    """Create (or look up) the Modal app and sandbox image for Claude Code."""
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("nodejs", "pnpm", "uv", "git", "gh", *(apt_packages or []))
        .run_commands("pnpm install -g @anthropic-ai/claude-code@1.0.117")
    )
    if env:
        # User defined environment variables
        image = image.env(env)
    if mcp_servers:
        for server_name, info in mcp_servers.items():
            image = image.run_commands(
                f"claude mcp add-json {server_name} '{json.dumps(info)}'"
            )

    app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
    sandbox = modal.Sandbox.create(
        app=app,
        image=image,
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
    mcp_servers: Annotated[
        dict[str, McpServer] | None,
        Field(
            default=None,
            description="List of MCP servers to use in the sandbox.",
        ),
    ] = None,
) -> str:
    """Run a user-provided command inside a pre-configured Modal sandbox with Claude Code CLI."""

    anthropic_api_key = secrets.get("ANTHROPIC_API_KEY")
    sandbox = _create_sandbox(
        timeout=timeout,
        block_network=block_network,
        env={"ANTHROPIC_API_KEY": anthropic_api_key},  # Platform secret
        mcp_servers=mcp_servers,
    )

    # If a GitHub repo is provided, download it into the sandbox
    # Execute the command with Claude Code CLI
    output = sandbox.exec(
        "claude",
        "-p",
        f"--permission-mode={permission_mode}",
        prompt,
    )

    stdout = output.stdout.read()
    if rc := output.returncode:
        stderr = output.stderr.read()
        raise RuntimeError(
            f"Sandbox command exited with status {rc}.\n"
            f"stdout: {stdout}\n"
            f"stderr: {stderr}"
        )
    return stdout
