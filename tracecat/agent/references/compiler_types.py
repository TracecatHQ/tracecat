"""Internal compiler inputs and results; no runtime or persistence objects."""

import math
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from tracecat.agent.references.contracts import (
    AuthoredSource,
    ReferenceEdge,
    SelectedReference,
)
from tracecat.agent.references.types import SourceLocation
from tracecat.agent.references.uri import ReferenceDiagnosticCode, ReferenceTarget


@dataclass(frozen=True, slots=True)
class GraphLimits:
    depth: int = 20
    nodes: int = 256
    edges: int = 2048
    source_bytes: int = 2_000_000
    tools: int = 1000
    seconds: float = 30

    def __post_init__(self) -> None:
        if (
            min(
                self.depth,
                self.nodes,
                self.edges,
                self.source_bytes,
                self.tools,
                self.seconds,
            )
            <= 0
        ):
            raise ValueError("Graph limits must be positive")
        if not math.isfinite(self.seconds):
            raise ValueError("Preparation deadline must be finite")
        if self.depth > 100:
            raise ValueError("Graph depth cannot exceed 100")


@dataclass(frozen=True, slots=True)
class ScopePolicy:
    namespaces: tuple[str, ...] = ()
    approvals: tuple[tuple[str, bool], ...] = ()
    internet_access: bool = False


@dataclass(frozen=True, slots=True)
class Declaration:
    target: ReferenceTarget
    contribution: Literal["explicit", "skill_tool"] = "explicit"
    alias: str | None = None


@dataclass(frozen=True, slots=True)
class ReferenceResource:
    """Authorized selected metadata, loaded without MCP discovery or credentials."""

    selected: SelectedReference
    sources: tuple[AuthoredSource, ...] = ()
    declarations: tuple[Declaration, ...] = ()
    policy: ScopePolicy = ScopePolicy()
    slug: str = ""


@dataclass(frozen=True, slots=True)
class CompileDiagnostic:
    code: ReferenceDiagnosticCode
    path: tuple[ReferenceTarget, ...]
    location: SourceLocation | None = None


class ReferenceLookupError(ValueError):
    """Safe classification only; never return resource names from failed lookups."""

    def __init__(
        self,
        code: ReferenceDiagnosticCode = ReferenceDiagnosticCode.UNRESOLVED,
        *,
        target: ReferenceTarget | None = None,
    ):
        self.code = code
        self.target = target
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class ScopeGrants:
    actions: tuple[str, ...] = ()
    mcp: tuple[tuple[UUID, frozenset[str] | None], ...] = ()
    approvals: tuple[tuple[str, bool], ...] = ()
    internet_access: bool = False
    tool_count: int = 0


@dataclass(frozen=True, slots=True)
class CompiledScope:
    key: str
    parent: str | None
    selected: tuple[SelectedReference, ...]
    edges: tuple[ReferenceEdge, ...]
    grants: ScopeGrants


@dataclass(frozen=True, slots=True)
class CompiledReferences:
    """A failed graph contains diagnostics only, never partial grants."""

    scopes: tuple[CompiledScope, ...] = ()
    diagnostics: tuple[CompileDiagnostic, ...] = ()


class ReferenceLookup(Protocol):
    async def resolve(
        self, target: ReferenceTarget, *, version_id: UUID | None, limits: GraphLimits
    ) -> ReferenceResource: ...

    async def policy(
        self, targets: tuple[ReferenceTarget, ...], policy: ScopePolicy
    ) -> ScopeGrants: ...
