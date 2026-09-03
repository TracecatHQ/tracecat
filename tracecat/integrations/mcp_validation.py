"""Validation for MCP stdio server configurations.

This module provides security validation for stdio MCP servers to prevent
command injection and other security vulnerabilities.
"""

import re
import uuid

from tracecat.agent.common.config import AGENT_RUNTIME_PROTECTED_ENV_VARS

# Allowlist of commands that can be used for MCP servers
ALLOWED_MCP_COMMANDS = frozenset({"npx", "uvx", "python", "python3", "node"})

# Environment variables that cannot be overridden
PROTECTED_ENV_VARS = AGENT_RUNTIME_PROTECTED_ENV_VARS | frozenset(
    {
        "PATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PYTHONPATH",
        "NODE_PATH",
        "HOME",
        "USER",
        "SHELL",
    }
)

# Dangerous patterns in arguments
_DANGEROUS_ARG_PATTERNS = [
    r"\$\(",  # $(command) - command substitution
    r"`",  # `command` - backtick command substitution
    r"\$\{",  # ${var} - variable expansion
    r"[;&|]",  # command chaining
    r"[<>]",  # redirects
    r"\x00",  # null byte
]

# Maximum limits
MAX_ARG_LENGTH = 1000
MAX_ARGS_COUNT = 50
MAX_ENV_KEY_LENGTH = 64
MAX_ENV_VALUE_LENGTH = 4096
MAX_SERVER_NAME_LENGTH = 64

_PROTECTED_UVX_OPTIONS = frozenset({"--cache-dir", "--link-mode"})
# Keep known no-value options aligned with the supported uvx versions. Unknown
# split options are treated as value-taking so a new global option cannot hide a
# protected option before the actual command.
_UVX_OPTIONS_WITHOUT_VALUES = frozenset(
    {
        "--compile-bytecode",
        "--help",
        "--isolated",
        "--lfs",
        "--managed-python",
        "--native-tls",
        "--no-binary",
        "--no-build",
        "--no-build-isolation",
        "--no-cache",
        "--no-config",
        "--no-env-file",
        "--no-index",
        "--no-managed-python",
        "--no-progress",
        "--no-python-downloads",
        "--no-sources",
        "--offline",
        "--quiet",
        "--refresh",
        "--reinstall",
        "--system-certs",
        "--upgrade",
        "--verbose",
        "--version",
        "-U",
        "-V",
        "-h",
        "-n",
        "-q",
        "-v",
    }
)
_UVX_SHORT_VALUE_OPTION_CHARS = frozenset("CPbcfipw")
_UVX_SHORT_FLAG_CHARS = frozenset("UVhnqv")


class MCPValidationError(ValueError):
    """Raised when MCP configuration validation fails."""

    pass


class MCPConfigurationError(Exception):
    """Raised when an MCP integration cannot be resolved into a usable server config."""

    pass


class MCPSecretResolutionError(Exception):
    """Raised when configured MCP integration credentials cannot be resolved."""

    def __init__(
        self,
        message: str,
        *,
        mcp_integration_id: uuid.UUID,
        server_name: str,
        server_slug: str | None = None,
    ) -> None:
        super().__init__(message)
        self.mcp_integration_id = mcp_integration_id
        self.server_name = server_name
        self.server_slug = server_slug


class MCPConnectionVerificationError(Exception):
    """Raised when connectivity verification against an MCP server fails."""

    def __init__(self, message: str, error: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error = error


def validate_mcp_command(command: str) -> None:
    """Validate that a command is in the allowlist.

    Args:
        command: The command to validate (e.g., 'npx', 'python')

    Raises:
        MCPValidationError: If the command is not allowed
    """
    if not command:
        raise MCPValidationError("Command cannot be empty")

    # Must be a single token (no embedded arguments or paths)
    if " " in command or "\t" in command:
        raise MCPValidationError(
            "Command must be a single token without embedded arguments"
        )

    # No path separators allowed
    if "/" in command or "\\" in command:
        raise MCPValidationError("Command must not contain path separators")

    # Must match allowlist exactly
    if command not in ALLOWED_MCP_COMMANDS:
        allowed = ", ".join(sorted(ALLOWED_MCP_COMMANDS))
        raise MCPValidationError(
            f"Command '{command}' is not allowed. Allowed commands: {allowed}"
        )


def validate_mcp_arg(arg: str) -> None:
    """Validate a single command argument.

    Args:
        arg: The argument to validate

    Raises:
        MCPValidationError: If the argument contains dangerous patterns
    """
    if not isinstance(arg, str):
        raise MCPValidationError(f"Argument must be a string, got {type(arg).__name__}")

    if len(arg) > MAX_ARG_LENGTH:
        raise MCPValidationError(
            f"Argument too long: {len(arg)} > {MAX_ARG_LENGTH} characters"
        )

    for pattern in _DANGEROUS_ARG_PATTERNS:
        if re.search(pattern, arg):
            raise MCPValidationError(f"Argument contains dangerous pattern: {pattern}")


def validate_mcp_args(args: list[str] | None) -> None:
    """Validate command arguments list.

    Args:
        args: List of arguments to validate

    Raises:
        MCPValidationError: If any argument is invalid
    """
    if args is None:
        return

    if not isinstance(args, list):
        raise MCPValidationError(f"Args must be a list, got {type(args).__name__}")

    if len(args) > MAX_ARGS_COUNT:
        raise MCPValidationError(f"Too many arguments: {len(args)} > {MAX_ARGS_COUNT}")

    for i, arg in enumerate(args):
        try:
            validate_mcp_arg(arg)
        except MCPValidationError as e:
            raise MCPValidationError(f"Invalid argument at index {i}: {e}") from e


def _uvx_option_consumes_next_arg(arg: str) -> bool:
    """Return whether a pre-command uvx option consumes the next argument."""
    option, separator, _value = arg.partition("=")
    if separator or option in _UVX_OPTIONS_WITHOUT_VALUES:
        return False

    if option.startswith("-") and not option.startswith("--"):
        short_options = option[1:]
        for index, short_option in enumerate(short_options):
            if short_option in _UVX_SHORT_FLAG_CHARS:
                continue
            if short_option in _UVX_SHORT_VALUE_OPTION_CHARS:
                return index == len(short_options) - 1
            return True
        return False

    return True


def sanitize_mcp_command_args(
    *,
    command: str,
    args: list[str] | None,
) -> tuple[list[str], frozenset[str]]:
    """Remove command options reserved for Tracecat's runtime isolation.

    The returned option names are safe to log because option values are never
    included. Parsing stops at the uvx command or an explicit ``--`` separator,
    so options belonging to the MCP command remain untouched. New configurations
    reject protected options, while the runtime uses this helper to contain legacy
    persisted configs.

    Args:
        command: The executable used by the stdio MCP server.
        args: Optional command arguments.

    Returns:
        The sanitized arguments and protected option names that were removed.
    """
    if command != "uvx" or not args:
        return list(args or ()), frozenset()

    sanitized_args: list[str] = []
    protected_options: set[str] = set()
    arg_index = 0
    while arg_index < len(args):
        arg = args[arg_index]
        if arg == "--":
            sanitized_args.extend(args[arg_index:])
            break

        if not arg.startswith("-"):
            sanitized_args.extend(args[arg_index:])
            break

        option, separator, _value = arg.partition("=")
        if option not in _PROTECTED_UVX_OPTIONS:
            sanitized_args.append(arg)
            arg_index += 1
            if _uvx_option_consumes_next_arg(arg) and arg_index < len(args):
                sanitized_args.append(args[arg_index])
                arg_index += 1
            continue

        protected_options.add(option)
        arg_index += 1 if separator else 2

    return sanitized_args, frozenset(protected_options)


def validate_mcp_env_key(key: str) -> None:
    """Validate an environment variable key.

    Args:
        key: The environment variable name

    Raises:
        MCPValidationError: If the key is invalid
    """
    if not isinstance(key, str):
        raise MCPValidationError(f"Env key must be a string, got {type(key).__name__}")

    if len(key) > MAX_ENV_KEY_LENGTH:
        raise MCPValidationError(
            f"Env key too long: {len(key)} > {MAX_ENV_KEY_LENGTH} characters"
        )

    # Must be valid POSIX env var name
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
        raise MCPValidationError(
            f"Invalid env var name '{key}': must match [A-Za-z_][A-Za-z0-9_]*"
        )

    # Cannot override protected variables
    if key in PROTECTED_ENV_VARS:
        raise MCPValidationError(f"Cannot override protected env var: {key}")


def validate_mcp_env_value(value: str) -> None:
    """Validate an environment variable value.

    Args:
        value: The environment variable value

    Raises:
        MCPValidationError: If the value is invalid
    """
    if not isinstance(value, str):
        raise MCPValidationError(
            f"Env value must be a string, got {type(value).__name__}"
        )

    if len(value) > MAX_ENV_VALUE_LENGTH:
        raise MCPValidationError(
            f"Env value too long: {len(value)} > {MAX_ENV_VALUE_LENGTH} characters"
        )

    # No null bytes
    if "\x00" in value:
        raise MCPValidationError("Env value cannot contain null bytes")

    # No control characters except tab and newline
    for char in value:
        if ord(char) < 32 and char not in ("\t", "\n"):
            raise MCPValidationError(
                f"Env value contains invalid control character: {ord(char)}"
            )


def validate_mcp_env(env: dict[str, str] | None) -> None:
    """Validate environment variables dictionary.

    Args:
        env: Dictionary of environment variables

    Raises:
        MCPValidationError: If any key or value is invalid
    """
    if env is None:
        return

    if not isinstance(env, dict):
        raise MCPValidationError(f"Env must be a dict, got {type(env).__name__}")

    for key, value in env.items():
        try:
            validate_mcp_env_key(key)
        except MCPValidationError as e:
            raise MCPValidationError(f"Invalid env key '{key}': {e}") from e

        try:
            validate_mcp_env_value(value)
        except MCPValidationError as e:
            raise MCPValidationError(f"Invalid env value for '{key}': {e}") from e


def validate_mcp_server_name(name: str) -> None:
    """Validate an MCP server name.

    Args:
        name: The server name/identifier

    Raises:
        MCPValidationError: If the name is invalid
    """
    if not isinstance(name, str):
        raise MCPValidationError(f"Name must be a string, got {type(name).__name__}")

    if not name:
        raise MCPValidationError("Server name cannot be empty")

    if len(name) > MAX_SERVER_NAME_LENGTH:
        raise MCPValidationError(
            f"Server name too long: {len(name)} > {MAX_SERVER_NAME_LENGTH} characters"
        )

    # Alphanumeric, hyphens, underscores only
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise MCPValidationError(
            f"Invalid server name '{name}': must contain only alphanumeric, "
            "hyphens, and underscores"
        )


def validate_mcp_command_config(
    *,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    name: str | None = None,
) -> None:
    """Validate a complete MCP command configuration.

    Args:
        command: The command to run
        args: Optional list of arguments
        env: Optional environment variables
        name: Optional server name

    Raises:
        MCPValidationError: If any part of the configuration is invalid
    """
    validate_mcp_command(command)
    validate_mcp_args(args)
    _, protected_options = sanitize_mcp_command_args(command=command, args=args)
    if protected_options:
        options = ", ".join(sorted(protected_options))
        raise MCPValidationError(
            f"Cannot override protected {command} option(s): {options}"
        )
    validate_mcp_env(env)
    if name is not None:
        validate_mcp_server_name(name)
