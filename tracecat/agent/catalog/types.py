"""Domain types for the agent model catalog."""

from dataclasses import dataclass
from typing import Literal, NamedTuple, TypedDict
from uuid import UUID


class ModelKey(NamedTuple):
    """Portable identity of a catalog model across deployments.

    ``catalog_id`` is a random per-environment UUID, so this pair is the stable
    identifier used to correlate an imported model with a local catalog row.
    """

    model_provider: str
    model_name: str


class ModelSelectionUpdate(TypedDict):
    """Fields a replacement rewrites on a run's model selection."""

    model_provider: str
    model_name: str
    catalog_id: UUID | None


@dataclass(frozen=True, slots=True)
class ModelReplacement:
    """Model a run moves to instead of the one it selected."""

    model_provider: str
    model_name: str
    catalog_id: UUID | None
    # Inline Literal: Temporal's dataclass decoder rejects ``type`` aliases.
    reason: Literal["retired"] = "retired"

    def selection_update(self) -> ModelSelectionUpdate:
        """Return the selection fields to overwrite, provider included."""
        return {
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "catalog_id": self.catalog_id,
        }
