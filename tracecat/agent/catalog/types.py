"""Domain types for the agent model catalog."""

from dataclasses import dataclass
from typing import NamedTuple
from uuid import UUID


class ModelKey(NamedTuple):
    """Portable identity of a catalog model across deployments.

    ``catalog_id`` is a random per-environment UUID, so this pair is the stable
    identifier used to correlate an imported model with a local catalog row.
    """

    model_provider: str
    model_name: str


@dataclass(frozen=True, slots=True)
class ModelReplacement:
    """Model a retired model's runs move to."""

    model_name: str
    catalog_id: UUID | None
