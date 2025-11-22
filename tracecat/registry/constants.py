DEFAULT_REGISTRY_ORIGIN = "tracecat_registry"
DEFAULT_REMOTE_REGISTRY_ORIGIN = "remote"
DEFAULT_LOCAL_REGISTRY_ORIGIN = "local"
REGISTRY_GIT_SSH_KEY_SECRET_NAME = "github-ssh-key"
"""Name of the SSH key secret for the registry."""

STORE_GIT_SSH_KEY_SECRET_NAME = "store-ssh-key"
"""Name of the SSH key secret for the store."""


REGISTRY_REPOS_PATH: str = "/registry/repos"
"""Base path for repository-related endpoints"""

REGISTRY_ACTIONS_PATH: str = "/registry/actions"
"""Base path for action-related endpoints"""
