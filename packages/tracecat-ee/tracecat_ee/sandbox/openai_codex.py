import json
import textwrap
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import modal
import toml
from pydantic import Field, SecretStr
from tracecat_registry import registry, secrets
from tracecat_registry._internal.exceptions import SecretNotFoundError
from tracecat_registry._internal.models import RegistryOAuthSecret
from tracecat_registry.core.agent import openai_secret
from tracecat_registry.integrations.agents.types import McpServer, StdioMcpServer

from tracecat.logger import logger
from tracecat.registry import fields


@dataclass(kw_only=True)
class PostInstallCommand:
    command: list[str | SecretStr]
    description: str

    def as_bash_command(self) -> str:
        """Convert the command to a bash command, keeps secret redacted."""
        return " ".join([str(cmd) for cmd in self.command])

    def revealed(self) -> list[str]:
        """Convert the command to a list of strings, reveals secrets."""
        return [
            cmd.get_secret_value() if isinstance(cmd, SecretStr) else cmd
            for cmd in self.command
        ]


github_oauth_secret = RegistryOAuthSecret(
    provider_id="github",
    grant_type="authorization_code",
)
"""GitHub OAuth2.0 credentials (Authorization Code grant).

- name: `github`
- provider_id: `github`
- token_name: `GITHUB_USER_TOKEN`
"""

SANDBOX_APP_NAME = "tracecat-openai-codex"
SANDBOX_NAME = "openai-codex"


def _get_base_env() -> dict[str, str]:
    """Get the base environment for the sandbox."""
    base = {
        "TERM": "dumb",
        "NO_COLOR": "1",
    }
    return base


def _get_agents_md(
    post_install_commands: list[PostInstallCommand] | None = None,
) -> str:
    """Generate AGENTS.md file content with OpenAI Codex agent documentation."""
    base_content = textwrap.dedent("""
        # Environment
        You are operating in a Modal sandbox environment under /app.
        """).strip()

    if post_install_commands:
        commands_section = (
            "\n\n## Post-Install Commands\n\n"
            "The following commands were executed during sandbox setup:\n\n"
        )
        for i, cmd in enumerate(post_install_commands, 1):
            commands_section += f"{i}. **{cmd.description}**\n"
            commands_section += f"   ```bash\n   {cmd.as_bash_command()}\n   ```\n\n"
        base_content += commands_section

    return base_content


def _generate_config_toml(
    *,
    mcp_servers: dict[str, McpServer] | None = None,
    reasoning_effort: str = "low",
    enable_search: bool = False,
    model: str = "gpt-5-codex",
    model_provider: str = "openai",
    builtin_mcp_servers: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a TOML configuration file for the sandbox based on Codex configuration spec."""
    config: dict[str, Any] = {
        "model": model,
        "model_provider": model_provider,
        "approval_policy": "never",  # Since we're in sandbox, no approvals needed
        "sandbox_mode": "danger-full-access",  # Full access in Modal sandbox
        "model_reasoning_effort": reasoning_effort,
        "tools": {
            "web_search": enable_search,
        },
        "mcp_servers": {},
    }
    if builtin_mcp_servers:
        for name in builtin_mcp_servers:
            if name in config["mcp_servers"]:
                raise ValueError(
                    f"MCP server {name} already exists. Please use a different name."
                )
            match name:
                case "github":
                    server = StdioMcpServer(
                        command="github-mcp-server",
                        args=["stdio"],
                        env={
                            "GITHUB_PERSONAL_ACCESS_TOKEN": secrets.get(
                                github_oauth_secret.token_name
                            )
                        },
                    )
                case _:
                    logger.warning("Unsupported builtin MCP server", name=name)
                    continue
            config["mcp_servers"][name] = server

    if mcp_servers:
        for name, server in mcp_servers.items():
            # Handle different MCP server types
            if name in config["mcp_servers"]:
                raise ValueError(
                    f"MCP server {name} already exists. Please use a different name."
                )
            match server:
                case {"command": _, "args": _, "env": _}:
                    config["mcp_servers"][name] = server
                case _:
                    logger.warning(
                        "Unsupported MCP server type", name=name, server=server
                    )
                    continue
    if not config["mcp_servers"]:
        del config["mcp_servers"]
    return config


def _create_sandbox(
    *,
    timeout: int,
    block_network: bool = False,
    apt_packages: list[str] | None = None,
    env: dict[str, str] | None = None,
    commands: list[str] | None = None,
) -> modal.Sandbox:
    """Create (or look up) the Modal app and sandbox image for OpenAI Codex."""
    # Dependencies - base tools first
    base_deps = ["ca-certificates", "curl", "ripgrep", "git", "gh", "tree", "jq"]
    if apt_packages:
        base_deps.extend(apt_packages)

    # Environment
    all_env = _get_base_env()
    if env:
        all_env.update(env)

    image = (
        modal.Image.debian_slim(python_version="3.12")
        .run_commands("apt-get update")
        # 3) Install base development tools
        .apt_install(*base_deps)
        # 4) Install a recent Node.js that includes Corepack
        .run_commands(
            [
                "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
                "apt-get update && apt-get install -y nodejs",
            ]
        )
        # 5) Install OpenAI Codex CLI (placeholder - replace with actual package when available)
        # Note: Currently using openai package as placeholder since Codex CLI doesn't exist yet
        .run_commands(
            [
                "node -v",
                "npm -v",
                "uv --version",
                "uvx --version",
                "npm install -g @openai/codex",  # Placeholder - replace with actual Codex CLI package
                "mkdir -p ~/.codex",
            ]
        )
        .workdir("/app")
        .run_commands(["touch final_output"])
        .env(all_env)
    )
    if commands:
        image = image.run_commands(commands)
    app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
    sandbox = modal.Sandbox.create(
        app=app,
        image=image,
        timeout=timeout,
        block_network=block_network,
        cpu=1,
        memory=512,
    )
    return sandbox


def _create_file_sommand(filename: str, content: str) -> str:
    """Convert a string to a file."""
    return f"cat <<EOF > {filename}\n{content}\nEOF"


def _setup_codex(
    sb: modal.Sandbox,
    post_install_commands: list[PostInstallCommand],
    mcp_servers: dict[str, McpServer] | None = None,
    reasoning_effort: str = "low",
    enable_search: bool = False,
    builtin_mcp_servers: list[str] | None = None,
) -> None:
    """Setup Codex. These don't need to be inccluded in the post-install commands."""
    setup_commands = [
        _create_file_sommand("AGENTS.md", _get_agents_md(post_install_commands))
    ]
    config_toml = _generate_config_toml(
        mcp_servers=mcp_servers,
        reasoning_effort=reasoning_effort,
        enable_search=enable_search,
        builtin_mcp_servers=builtin_mcp_servers,
    )
    setup_commands.append(
        _create_file_sommand("~/.codex/config.toml", toml.dumps(config_toml))
    )
    # Login to codex
    setup_commands.append(f"codex login --api-key {secrets.get('OPENAI_API_KEY')}")
    for cmd in setup_commands:
        sb.exec("bash", "-c", cmd)


@registry.register(
    default_title="OpenAI Codex",
    description="OpenAI Codex CLI",
    display_group="OpenAI",
    doc_url="https://developers.openai.com/codex/cli/",
    namespace="ai.openai",
    secrets=[openai_secret, github_oauth_secret],
)
def codex(
    prompt: Annotated[
        str,
        Field(
            ...,
            description="The prompt for the OpenAI Codex CLI.",
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
            description="Mapping of custom Stdio MCP servers to use in the sandbox. Follows the standard MCP server format.",
        ),
    ] = None,
    reasoning_effort: Annotated[
        Literal["low", "medium", "high"],
        Field(
            ...,
            description="The reasoning effort level for the model.",
        ),
    ] = "low",
    enable_search: Annotated[
        bool,
        Field(
            ...,
            description="Whether to enable web search for Codex.",
        ),
    ] = False,
    builtin_mcp_servers: Annotated[
        list[str] | None,
        Field(
            default=None,
            description="List of built-in MCP servers to use in the sandbox.",
        ),
        fields.Select(options=["github"], multiple=True),
    ] = None,
) -> dict[str, Any]:
    """Run a user-provided command inside a pre-configured Modal sandbox with OpenAI Codex CLI."""

    # These are use-case specific commands that are executed after the sandbox is created
    post_install_commands: list[PostInstallCommand] = []
    github_user_token = None
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
                        SecretStr(
                            f"https://{github_user_token}@github.com/{git_repo}.git"
                        ),
                    ],
                    description=f"Cloned the GitHub repo {git_repo}.",
                )
            )
    if builtin_mcp_servers and "github" in builtin_mcp_servers:
        # Install Go and build github-mcp-server
        # NOTE: This should be deprecated once codex supports http/sse mcp servers
        commands = [
            # Install Go (>=1.22 required)
            "curl -OL https://go.dev/dl/go1.22.5.linux-amd64.tar.gz",
            "rm -rf /usr/local/go && tar -C /usr/local -xzf go1.22.5.linux-amd64.tar.gz",
            "ln -s /usr/local/go/bin/go /usr/local/bin/go",
            # Clone and build github-mcp-server
            "mkdir -p /tmp/gh-mcp",
            "git clone https://github.com/github/github-mcp-server.git /tmp/gh-mcp",
            "cd /tmp/gh-mcp/cmd/github-mcp-server && go build -o /usr/local/bin/github-mcp-server",
            "github-mcp-server -v",
        ]
    else:
        commands = None
    with modal.enable_output():
        sb = _create_sandbox(
            timeout=timeout,
            block_network=block_network,
            # Used for git and gh cli
            env={"GITHUB_USER_TOKEN": github_user_token} if github_user_token else {},
            commands=commands,
        )
        _setup_codex(
            sb,
            post_install_commands,
            mcp_servers,
            reasoning_effort,
            enable_search,
            builtin_mcp_servers,
        )

        user_prompt = prompt
        logger.info(
            f"Running sandbox with post-install commands: {post_install_commands}"
        )
        for cmd in post_install_commands:
            logger.info(f"Running command: {cmd}")
            proc = sb.exec(*cmd.revealed())
            proc.wait()
            logger.info(f"Command {cmd.command} stdout: {proc.stdout.read()}")
            logger.info(f"Command {cmd.command} stderr: {proc.stderr.read()}")

        logger.info(f"Full prompt: {user_prompt}")
        options = [
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            "/app",
            "--color",
            "never",
            "--json",
            "--output-last-message",
            "final_output",
        ]
        cmd = ["codex", "exec", *options, f"'{user_prompt}'"]
        logger.info("Run command:", cmd=cmd)

        process = sb.exec(*cmd)
        process.wait()

        # Ndjson
        stdout = process.stdout.read()
        stderr = process.stderr.read()
        logger.info("Stdout/Stderr after:", stdout=stdout, stderr=stderr)
        logger.info(f"{stdout}")
        logger.info("Process finished:", returncode=process.returncode)

        # Check return code before reading output
        if rc := process.returncode:
            raise RuntimeError(
                f"Codex exec command exited with status {rc}.\n"
                f"stdout: {stdout}\n"
                f"stderr: {stderr}"
            )

        # Parse the ndjson
        all_events = [json.loads(line) for line in stdout.splitlines()]
        exclude_events = {"exec_command_output_delta", "token_count"}
        events = [
            event
            for event in all_events
            if event.get("msg", {}).get("type") not in exclude_events
        ]

        # Read the final output
        read_proc = sb.exec("cat", "final_output")
        read_proc.wait()
        read_stdout = read_proc.stdout.read()
        read_stderr = read_proc.stderr.read()
        logger.info("READ PROC STDOUT", stdout=read_stdout)
        logger.info("READ PROC STDERR", stderr=read_stderr)
        if rc := read_proc.returncode:
            raise RuntimeError(
                f"Read final output command exited with status {rc}.\n"
                f"stdout: {read_stdout}\n"
                f"stderr: {read_stderr}"
            )
        return {
            "events": events,
            "response": {
                "result": read_stdout,
                "error": read_stderr,
            },
        }
