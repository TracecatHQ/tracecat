import json
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import modal
from modal._pty import get_pty_info
from pydantic import Field
from tracecat_registry import registry, secrets
from tracecat_registry._internal.exceptions import SecretNotFoundError
from tracecat_registry._internal.models import RegistryOAuthSecret
from tracecat_registry.core.agent import anthropic_secret, bedrock_secret
from tracecat_registry.integrations.agents.types import McpServer

from tracecat.logger import logger
from tracecat.registry import fields

github_oauth_secret = RegistryOAuthSecret(
    provider_id="github",
    grant_type="authorization_code",
)
"""GitHub OAuth2.0 credentials (Authorization Code grant).

- name: `github`
- provider_id: `github`
- token_name: `GITHUB_USER_TOKEN`
"""

SANDBOX_APP_NAME = "tracecat-claude-code"
SANDBOX_NAME = "claude-code"


def _get_base_env() -> dict[str, str]:
    """Get the base environment for the sandbox."""
    # Bedrock takes precedence over Anthropic
    base = {
        "TERM": "dumb",
        "NO_COLOR": "1",
    }
    if (
        (aws_access_key_id := secrets.get_or_default("AWS_ACCESS_KEY_ID"))
        and (aws_secret_access_key := secrets.get_or_default("AWS_SECRET_ACCESS_KEY"))
        and (aws_region := secrets.get_or_default("AWS_REGION"))
    ):
        base.update(
            {
                "AWS_ACCESS_KEY_ID": aws_access_key_id,
                "AWS_SECRET_ACCESS_KEY": aws_secret_access_key,
                "AWS_REGION": aws_region,
                "CLAUDE_CODE_USE_BEDROCK": "1",
                "ANTHROPIC_SMALL_FAST_MODEL_AWS_REGION": aws_region,
            }
        )
    elif anthropic_api_key := secrets.get_or_default("ANTHROPIC_API_KEY"):
        base.update(
            {
                "ANTHROPIC_API_KEY": anthropic_api_key,
            }
        )
    else:
        raise SecretNotFoundError(
            "No Anthropic API key or AWS Bedrock credentials found."
        )
    return base


def _create_sandbox(
    *,
    timeout: int,
    block_network: bool = False,
    apt_packages: list[str] | None = None,
    env: dict[str, str] | None = None,
    mcp_servers: dict[str, McpServer] | None = None,
    commands: list[str] | None = None,
) -> modal.Sandbox:
    """Create (or look up) the Modal app and sandbox image for Claude Code."""
    # Dependencies - base tools first
    base_deps = ["git", "gh"]
    if apt_packages:
        base_deps.extend(apt_packages)

    # Environment
    all_env = _get_base_env()
    if env:
        all_env.update(env)

    # Commands for MCP servers and user commands
    post_install_commands = []
    if mcp_servers:
        for server_name, info in mcp_servers.items():
            post_install_commands.append(
                f"claude mcp add-json {server_name} '{json.dumps(info)}'"
            )
    # Run user defined commands after installing dependencies
    if commands:
        post_install_commands.extend(commands)

    image = (
        modal.Image.debian_slim(python_version="3.12")
        .run_commands("apt-get update")
        # 1) base tools first
        .apt_install("ca-certificates", "curl", "gnupg")
        .apt_install(*base_deps)
        # 2) install a recent Node that includes Corepack
        .run_commands(
            [
                "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
                "apt-get update && apt-get install -y nodejs",
            ]
        )
        # 4) now pnpm exists; use pnpm *add* for globals
        .run_commands(
            [
                "node -v",
                "npm -v",
                "npm install -g @anthropic-ai/claude-code@1.0.117",
            ]
        )
        .run_commands(
            [
                "uv --version",
                "uvx --version",
            ]
        )
        .env(all_env)
        .run_commands(post_install_commands)
    )
    app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
    sandbox = modal.Sandbox.create(
        app=app,
        image=image,
        timeout=timeout,
        block_network=block_network,
        cpu=2,
        # 10gb
        memory=10240,
    )
    return sandbox


ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


@dataclass(kw_only=True)
class PostInstallCommand:
    command: list[str]
    description: str


@registry.register(
    default_title="Claude Code",
    description="Claude Code CLI",
    display_group="Anthropic",
    doc_url="https://docs.claude.com/en/docs/claude-code/sdk/sdk-headless",
    namespace="ai.anthropic",
    secrets=[anthropic_secret, bedrock_secret, github_oauth_secret],
)
def claude_code(
    prompt: Annotated[
        str,
        Field(
            ...,
            description="The prompt for the Claude Code CLI.",
            min_length=1,
            max_length=10000,
        ),
        fields.TextArea(),
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
            description="List of Stdio MCP servers to use in the sandbox.",
        ),
    ] = None,
) -> dict[str, Any]:
    """Run a user-provided command inside a pre-configured Modal sandbox with Claude Code CLI."""

    post_install_commands: list[PostInstallCommand] = []
    if git_repo:
        if len(git_repo.split("/")) != 2:
            raise ValueError("Git repo must be in the format 'owner/repo'")
        try:
            github_user_token = secrets.get(github_oauth_secret.token_name)
        except SecretNotFoundError:
            raise SecretNotFoundError(
                "Git repo provided but GitHub OAuth2.0 credentials are not configured. "
                "Please configure the GitHub OAuth2.0 credentials in the Tracecat registry."
            ) from None
        else:
            # Use git clone with token authentication for better compatibility and simplicity
            # gh CLI requires additional setup and may not be available in all sandbox environments
            # TODO: Use different git host if needed
            post_install_commands.append(
                PostInstallCommand(
                    command=[
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        f"https://{github_user_token}@github.com/{git_repo}.git",
                    ],
                    description=f"Cloned the GitHub repo {git_repo}.",
                )
            )
    with modal.enable_output():
        sandbox = _create_sandbox(
            timeout=1800,
            block_network=block_network,
            env={"GITHUB_USER_TOKEN": github_user_token},
            mcp_servers=mcp_servers,
        )

        user_prompt = prompt
        logger.info(
            f"Running sandbox with post-install commands: {post_install_commands}"
        )
        for cmd in post_install_commands:
            logger.info(f"Running command: {cmd}")
            sandbox.exec(*cmd.command)

        # If a GitHub repo is provided, download it into the sandbox
        # Execute the command with Claude Code CLI
        logger.info(
            f"Running command: claude -p --permission-mode={permission_mode} {user_prompt}"
        )
        post_install_commands_str = "\n".join(
            [
                f"Description: {cmd.description}\nCommand: {cmd.command}"
                for cmd in post_install_commands
            ]
        )
        full_prompt = "\n".join(
            [
                user_prompt,
                f"The following additional commands were executed in the sandbox:\n{post_install_commands_str}",
            ]
        )
        logger.info(f"Full prompt: {full_prompt}")

        process = sandbox.exec(
            "claude",
            "-p",
            # f"--permission-mode={permission_mode}",
            "--verbose",
            "--output-format=json",  # Output a single JSON response
            full_prompt,
            _pty_info=get_pty_info(shell=True),
        )

        # Wait for the process to complete first
        logger.info("Sandbox process:", process=str(process))
        logger.info("Stdout before", stdout=process.stdout.read())
        process.wait()
        logger.info("Stdout after:", stdout=process.stdout.read())
        logger.info("Process finished:", returncode=process.returncode)

        # Check return code before reading output
        if rc := process.returncode:
            stderr = process.stderr.read()
            stdout = process.stdout.read()
            raise RuntimeError(
                f"Sandbox command exited with status {rc}.\n"
                f"stdout: {stdout}\n"
                f"stderr: {stderr}"
            )

        # Read and clean stdout after successful completion
        raw_stdout = process.stdout.read()
        # Strip ANSI escape sequences as CC runs with a pseudo-terminal
        stdout = ansi_escape.sub("", raw_stdout).strip()
        logger.info("Sandbox stdout cleaned:", stdout=stdout)
        logger.info("Sandbox final output:", stdout=stdout)
        return json.loads(stdout)
