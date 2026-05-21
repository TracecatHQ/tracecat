from __future__ import annotations

from typing import Annotated

from tracecat_registry import registry
from typing_extensions import Doc


@registry.register(
    default_title="Echo smoke marker",
    description="Return a deterministic marker for agent custom registry smoke tests.",
    display_group="Agent smoke",
    namespace="tools.agent_smoke",
)
def echo_marker(
    marker: Annotated[str, Doc("Marker to echo back to the agent.")],
) -> str:
    return marker
