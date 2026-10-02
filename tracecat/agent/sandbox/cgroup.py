"""Agent entrypoint for the shared sandbox cgroup bootstrap."""

from pathlib import Path

from tracecat.sandbox import cgroup

CGROUP_PATH_ENV = "TRACECAT__AGENT_SANDBOX_CGROUP_PATH"


def sandbox_cgroup() -> Path:
    """Require the agent worker's delegated sandbox subtree."""
    return cgroup.sandbox_cgroup(CGROUP_PATH_ENV)


def main() -> None:
    """Keep the deployed agent bootstrap entrypoint and environment contract."""
    cgroup.main(CGROUP_PATH_ENV)


if __name__ == "__main__":
    main()
