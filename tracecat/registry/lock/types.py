"""Types for registry version locks."""

# Type alias for registry version locks.
# Maps repository origin to pinned version string.
# Example: {"tracecat_registry": "2024.12.10.123456", "git+ssh://...": "abc1234"}
RegistryLock = dict[str, str]
