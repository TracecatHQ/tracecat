"""Environment variable rules shared by every process launcher.

Kept dependency-free so the agent sandbox can import it without widening its
cold-start import footprint.
"""

LOADER_ENV_VAR_PREFIX = "LD_"
"""Prefix of the variables the glibc dynamic loader reads at process start."""

LOADER_ENV_VARS = frozenset({"GLIBC_TUNABLES", "GCONV_PATH"})
"""Other glibc variables that change how libraries are loaded."""

LOADER_ENV_VAR_ERROR = (
    "Dynamic loader environment variables (LD_*, GLIBC_TUNABLES, GCONV_PATH) "
    "cannot be set"
)
"""Error text for a refused loader key. The key is not echoed: a prefix match
does not prove it is a known constant, and a resolved secret could start with
``LD_``."""


def is_loader_env_var(key: str) -> bool:
    """Return whether ``key`` controls which libraries a process loads.

    The sandbox env map is also nsjail's own process environment on the host,
    and the dynamic loader reads these keys before any jail exists, so they must
    never come from user input. Other keys are inert there only because nsjail
    is a native binary started by absolute path.
    """
    return key.startswith(LOADER_ENV_VAR_PREFIX) or key in LOADER_ENV_VARS
