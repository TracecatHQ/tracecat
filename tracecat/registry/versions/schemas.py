"""Schemas for registry version management."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from tracecat_registry import RegistrySecretType

from tracecat.registry.actions.schemas import (
    RegistryActionCreate,
    RegistryActionInterface,
    RegistryActionOptions,
    RegistryActionType,
)


class RegistryVersionManifestAction(BaseModel):
    """A single action entry in the version manifest.

    This is a lightweight representation of an action that can be stored
    in the manifest JSONB field. It contains all metadata needed to
    populate RegistryIndex entries and locate the action implementation.
    """

    namespace: str
    name: str
    action_type: RegistryActionType
    description: str
    default_title: str | None = None
    display_group: str | None = None
    doc_url: str | None = None
    author: str | None = None
    deprecated: str | None = None
    secrets: list[RegistrySecretType] | None = None
    interface: RegistryActionInterface
    options: RegistryActionOptions = Field(default_factory=RegistryActionOptions)
    implementation: dict[str, Any]

    @staticmethod
    def from_action_create(
        action: RegistryActionCreate,
    ) -> RegistryVersionManifestAction:
        """Create a manifest action from a RegistryActionCreate."""
        return RegistryVersionManifestAction(
            namespace=action.namespace,
            name=action.name,
            action_type=action.type,
            description=action.description,
            default_title=action.default_title,
            display_group=action.display_group,
            doc_url=action.doc_url,
            author=action.author,
            deprecated=action.deprecated,
            secrets=action.secrets,
            interface=action.interface,
            options=action.options,
            implementation=action.implementation.model_dump(mode="json"),
        )

    @property
    def action(self) -> str:
        """Full action identifier."""
        return f"{self.namespace}.{self.name}"


class RegistryVersionManifest(BaseModel):
    """The frozen manifest stored in RegistryVersion.

    Contains all action definitions and metadata needed to execute
    actions from this version.
    """

    schema_version: str = Field(
        default="1.0",
        description="Manifest schema version for forward compatibility",
    )
    actions: dict[str, RegistryVersionManifestAction] = Field(
        default_factory=dict,
        description="Map of action name to action definition",
    )

    @staticmethod
    def from_actions(
        actions: list[RegistryActionCreate],
    ) -> RegistryVersionManifest:
        """Build a manifest from a list of action creates."""
        manifest_actions = {}
        for action in actions:
            action_name = f"{action.namespace}.{action.name}"
            manifest_actions[action_name] = (
                RegistryVersionManifestAction.from_action_create(action)
            )
        return RegistryVersionManifest(actions=manifest_actions)


class RegistryVersionCreate(BaseModel):
    """Parameters for creating a new registry version."""

    repository_id: UUID
    version: str = Field(..., description="Version string, e.g., '1.0.0'")
    commit_sha: str | None = Field(
        default=None,
        description="Git commit SHA if applicable",
    )
    manifest: RegistryVersionManifest
    tarball_uri: str = Field(
        ...,
        description="S3 URI to the compressed tarball venv for action execution",
    )


class RegistryVersionRead(BaseModel):
    """API response model for a registry version."""

    id: UUID
    repository_id: UUID
    version: str
    commit_sha: str | None
    manifest: RegistryVersionManifest
    tarball_uri: str
    created_at: datetime


class RegistryVersionReadMinimal(BaseModel):
    """Minimal API response model for a registry version (without manifest)."""

    id: UUID
    repository_id: UUID
    version: str
    commit_sha: str | None
    tarball_uri: str
    created_at: datetime
    action_count: int = Field(
        default=0,
        description="Number of actions in this version",
    )


class RegistryIndexCreate(BaseModel):
    """Parameters for creating a registry index entry."""

    registry_version_id: UUID
    namespace: str
    name: str
    action_type: RegistryActionType
    description: str
    default_title: str | None = None
    display_group: str | None = None
    doc_url: str | None = None
    author: str | None = None
    deprecated: str | None = None
    secrets: list[RegistrySecretType] | None = None
    interface: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)


class RegistryIndexRead(BaseModel):
    """API response model for a registry index entry."""

    id: UUID
    registry_version_id: UUID
    namespace: str
    name: str
    action_type: RegistryActionType
    description: str
    default_title: str | None
    display_group: str | None
    doc_url: str | None
    author: str | None
    deprecated: str | None
    secrets: list[RegistrySecretType] | None
    interface: dict[str, Any]
    options: dict[str, Any]

    @property
    def action(self) -> str:
        """Full action identifier."""
        return f"{self.namespace}.{self.name}"


# Version Diff Schemas


class ActionInterfaceChange(BaseModel):
    """Describes a change to an action's interface (expects or returns)."""

    field: Literal["expects", "returns"]
    change_type: Literal["added", "removed", "modified"]
    old_value: dict[str, Any] | None = None
    new_value: dict[str, Any] | None = None


class ActionChange(BaseModel):
    """Describes a change to an action between two versions."""

    action_name: str
    change_type: Literal["added", "removed", "modified"]
    interface_changes: list[ActionInterfaceChange] = Field(default_factory=list)
    description_changed: bool = False


class VersionDiff(BaseModel):
    """Result of comparing two registry versions."""

    base_version_id: UUID
    base_version: str
    compare_version_id: UUID
    compare_version: str
    actions_added: list[str] = Field(default_factory=list)
    actions_removed: list[str] = Field(default_factory=list)
    actions_modified: list[ActionChange] = Field(default_factory=list)
    total_changes: int = 0
