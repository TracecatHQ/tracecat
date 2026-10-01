"""Versioned, provider-neutral data contracts for future reference preparation.

These are trusted internal messages, not API request models. Deserializing an
origin or an authority envelope does not authorize it: admission services must
construct them from authenticated configuration writes and the original actor.
No credentials, native history, sandbox paths, or SDK objects belong here.
"""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

from tracecat.agent.references.immutable import FrozenJSONObject, FrozenMap
from tracecat.agent.references.types import ReferenceDiagnostic, SourceLocation
from tracecat.agent.references.uri import ReferenceKind, ReferenceTarget
from tracecat.registry.lock.types import RegistryLock

Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


def _relative_logical_path(path: str) -> str:
    if (
        path.startswith("/")
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError("Path must be a relative logical path")
    return path


LogicalPath = Annotated[
    str, StringConstraints(min_length=1), AfterValidator(_relative_logical_path)
]
ScopeKey = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]+$")]


def _strict_version(value: object) -> int:
    # Literal[1] alone also matches True and 1.0, even in Pydantic strict mode.
    if type(value) is not int:
        raise ValueError("Schema versions must be JSON integers")
    return value


SchemaVersion = Annotated[Literal[1], BeforeValidator(_strict_version)]


class ReferenceContract(BaseModel):
    """Strict wire contract; additive revisions require explicit version support."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: SchemaVersion = Field(default=1)


class AuthoredSource(ReferenceContract):
    """Trusted provenance of one authored Markdown source or admitted override."""

    origin: Literal["agent_instructions", "skill_file", "authorized_override"]
    owner: ReferenceTarget
    version_id: UUID
    path: LogicalPath
    content_hash: Digest
    markdown: str


class ExplicitDeclarations(ReferenceContract):
    """Manual contributions only; never reconstructed from flattened actions."""

    targets: tuple[ReferenceTarget, ...] = ()


class AuthoredReferenceInput(ReferenceContract):
    """Root configuration and its separately admitted configuration overrides."""

    root: AuthoredSource
    explicit: ExplicitDeclarations
    overrides: tuple[AuthoredSource, ...] = ()
    override_declarations: ExplicitDeclarations = Field(
        default_factory=ExplicitDeclarations
    )


class PromptContext(ReferenceContract):
    """Context for model assembly. Never a dependency-graph seed."""

    parts: tuple[str, ...] = ()


class ExecutionAuthority(ReferenceContract):
    """Admission restrictions, independent of the orchestration service role.

    None means no additional admission action ceiling, not universal permission.
    Existing policy still applies. Empty means no actions. Delegated modes keep
    their own authorization semantics; do not apply workspace-chat filtering to
    every preset execution. Only trusted admission code can construct this.
    """

    actor_id: UUID | None
    session_mode: Literal[
        "workspace_chat", "delegated_preset", "workflow", "builder", "tool_free"
    ]
    activate_references: StrictBool
    action_scope_ceiling: tuple[str, ...] | None
    namespace_ceiling: tuple[str, ...] | None
    excluded_targets: tuple[ReferenceTarget, ...] = ()
    admission_policy_hash: Digest

    @model_validator(mode="after")
    def validate_tool_free(self) -> Self:
        if self.session_mode == "tool_free" and (
            self.activate_references or self.action_scope_ceiling != ()
        ):
            raise ValueError(
                "Tool-free admission cannot activate references or grant actions"
            )
        return self


class ReferencePreparationInput(ReferenceContract):
    """Trusted declaration/authority envelope; prompt context travels separately."""

    organization_id: UUID
    workspace_id: UUID
    session_id: UUID
    logical_turn_id: UUID
    backend_id: str
    harness_type: str
    authored: AuthoredReferenceInput
    authority: ExecutionAuthority
    input_hash: Digest


class SelectedReference(ReferenceContract):
    """One selected identity; versioned entities require an immutable version."""

    target: ReferenceTarget
    version_id: UUID | None = Field(default=None)
    content_hash: Digest | None = Field(default=None)

    @model_validator(mode="after")
    def require_published_version(self) -> Self:
        if (
            self.target.kind
            in {ReferenceKind.SKILL, ReferenceKind.AGENT, ReferenceKind.WORKFLOW}
            and self.version_id is None
        ):
            raise ValueError(
                "Versioned references require a selected published version"
            )
        return self


class ReferenceEdge(ReferenceContract):
    """Dependency provenance in its owning agent scope."""

    scope: ScopeKey
    source: ReferenceTarget
    target: ReferenceTarget
    location: SourceLocation | None = Field(default=None)
    contribution: Literal["mention", "explicit", "skill_tool"]


class LogicalArtifact(ReferenceContract):
    """Immutable content reference; logical paths are mapped by the runtime."""

    key: str
    path: LogicalPath
    content_hash: Digest
    size_bytes: int = Field(ge=0, strict=True)
    media_type: str


class SkillManifest(ReferenceContract):
    """Exact skill files for a selected published identity."""

    skill: SelectedReference
    manifest_hash: Digest
    files: tuple[LogicalArtifact, ...]

    @model_validator(mode="after")
    def require_skill_target(self) -> Self:
        if self.skill.target.kind != ReferenceKind.SKILL:
            raise ValueError("Skill manifests require a skill reference")
        return self


class CallableBinding(ReferenceContract):
    """Canonical callable contract; native names and eager flags are runtime-owned."""

    key: str
    target: SelectedReference
    action: str | None = Field(default=None)
    description: str
    # JSON Schema is an open recursive vocabulary, not an untyped payload.
    input_schema: FrozenJSONObject
    output_schema: FrozenJSONObject | None = Field(default=None)
    requires_approval: StrictBool


class ReferenceRegistryLock(ReferenceContract):
    """Snapshot-owned registry lock; copied, immutable, and strict on the wire."""

    origins: FrozenMap[str]
    actions: FrozenMap[str]
    origin_fingerprints: FrozenMap[str] = Field(
        default_factory=dict, validate_default=True
    )

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        # Reuse the registry's semantic validation without retaining mutable state.
        RegistryLock(
            origins=dict(self.origins),
            actions=dict(self.actions),
            origin_fingerprints=dict(self.origin_fingerprints),
        )
        return self


class ReferenceScope(ReferenceContract):
    """Prepared main or direct-child capability set; no native session state."""

    key: ScopeKey
    parent: ScopeKey | None = Field(default=None)
    selected: tuple[SelectedReference, ...]
    edges: tuple[ReferenceEdge, ...]
    callables: tuple[CallableBinding, ...]
    required_tool_keys: tuple[str, ...]
    registry_lock: ReferenceRegistryLock
    skills: tuple[SkillManifest, ...] = ()
    artifacts: tuple[LogicalArtifact, ...] = ()
    policy_hash: Digest


class ReferenceSnapshotRef(ReferenceContract):
    """Compact durable handle. Only trusted workers dereference object_key."""

    snapshot_id: UUID
    workspace_id: UUID
    logical_turn_id: UUID
    object_key: str
    content_hash: Digest


class ResolvedReferenceSnapshot(ReferenceContract):
    """Immutable semantic preparation result; native readiness is separate."""

    snapshot_id: UUID
    workspace_id: UUID
    logical_turn_id: UUID
    backend_id: str
    harness_type: str
    input_hash: Digest
    scopes: tuple[ReferenceScope, ...]


class RuntimeReferenceBinding(ReferenceContract):
    """Installation identity and readiness evidence, never a source of grants."""

    snapshot: ReferenceSnapshotRef
    backend_id: str
    harness_type: str
    scope: ScopeKey
    integration_version: str
    artifact_hashes: tuple[Digest, ...]
    ready_tool_keys: tuple[str, ...]


class ReferenceCapabilities(ReferenceContract):
    """Explicit support for a backend/harness pair. Absence means unsupported."""

    harness_type: str
    kinds: frozenset[ReferenceKind]
    mcp_transports: frozenset[Literal["http", "stdio"]] = frozenset()
    direct_child_delegation: StrictBool = False
    eager_callable_readiness: StrictBool = False
    snapshot_versions: frozenset[SchemaVersion] = frozenset({1})
    binding_versions: frozenset[SchemaVersion] = frozenset({1})


class ReferencePreparationFailure(ReferenceContract):
    """Typed diagnostics for a rejected preparation; no partial executable result."""

    diagnostics: tuple[ReferenceDiagnostic, ...]
