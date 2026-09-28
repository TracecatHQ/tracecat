"""Domain types for the agent model catalog."""

from typing import NamedTuple


class ModelKey(NamedTuple):
    """Portable identity of a catalog model across deployments.

    ``catalog_id`` is a random per-environment UUID, so this pair is the stable
    identifier used to correlate an imported model with a local catalog row.
    """

    model_provider: str
    model_name: str
