from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from tracecat.exceptions import RegistryError
from tracecat.registry.actions.schemas import (
    BoundRegistryAction,
    RegistryActionSpec,
)


class RegistryIndexEntry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    action_id: str
    spec: RegistryActionSpec
    loader: BoundRegistryAction
    origin: str
    registry_version: str | None = None
    extension: str | None = None


class RegistryIndex:
    """In-memory index of registry actions and loaders."""

    def __init__(self, entries: Mapping[str, RegistryIndexEntry] | None = None):
        self._entries: dict[str, RegistryIndexEntry] = dict(entries or {})

    def __contains__(self, action_id: str) -> bool:  # pragma: no cover - trivial
        return action_id in self._entries

    def add(
        self,
        bound: BoundRegistryAction,
        *,
        registry_version: str | None = None,
        extension: str | None = None,
        repository_id: Any | None = None,
    ) -> None:
        if bound.action in self._entries:
            raise RegistryError(f"Duplicate registry action: {bound.action}")

        spec = RegistryActionSpec.from_bound(
            bound, repository_id=repository_id, registry_version=registry_version
        )
        self._entries[bound.action] = RegistryIndexEntry(
            action_id=bound.action,
            spec=spec,
            loader=bound,
            origin=bound.origin,
            registry_version=registry_version,
            extension=extension,
        )

    def get_loader(self, action_id: str) -> BoundRegistryAction:
        try:
            return self._entries[action_id].loader
        except KeyError as exc:  # pragma: no cover - defensive
            raise RegistryError(f"Loader not found for action: {action_id}") from exc

    def get_spec(self, action_id: str) -> RegistryActionSpec:
        try:
            return self._entries[action_id].spec
        except KeyError as exc:  # pragma: no cover - defensive
            raise RegistryError(f"Spec not found for action: {action_id}") from exc

    def iter_specs(self) -> Iterable[RegistryActionSpec]:
        return (entry.spec for entry in self._entries.values())

    def iter_loaders(self) -> Iterable[BoundRegistryAction]:
        return (entry.loader for entry in self._entries.values())

    def iter_entries(self) -> Iterable[RegistryIndexEntry]:
        return self._entries.values()

    @classmethod
    def from_store(
        cls,
        store: Mapping[str, BoundRegistryAction],
        *,
        registry_version: str | None = None,
        extension: str | None = None,
        repository_id: Any | None = None,
    ) -> RegistryIndex:
        index = cls()
        for bound in store.values():
            index.add(
                bound,
                registry_version=registry_version,
                extension=extension,
                repository_id=repository_id,
            )
        return index
