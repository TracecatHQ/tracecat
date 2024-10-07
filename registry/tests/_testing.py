import asyncio
import random
from typing import Any

from tracecat_registry import RegistrySecret, registry, secrets

my_secret = RegistrySecret(name="my_secret", keys=["KEY"])


@registry.register(
    default_title="Set environment variable",
    display_group="Testing",
    description="Set an environment variable and return it",
    namespace="core.testing",
    secrets=[my_secret],
)
async def set_environment(value: str) -> dict[str, Any]:
    """When we enter this function, the secret is already set in the SM.

    Options
    -------
    - We can access the secret with `secrets.get("KEY")`
        - This retrieves the secret from contextvars
    """
    # Get secret
    # os.environ["__FOO"] = value
    # This was injected into the sandbox

    await asyncio.sleep(random.random())

    secrets.set("DIFF_KEY", value)
    return {
        "injected": value,
        "set_in_env": secrets.get("DIFF_KEY"),
    }
