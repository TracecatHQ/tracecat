"""Bounded dependency expansion from trusted authored declarations only."""

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from tracecat.agent.references.compiler_types import (
    CompileDiagnostic,
    CompiledReferences,
    CompiledScope,
    Declaration,
    GraphLimits,
    ReferenceLookup,
    ReferenceLookupError,
    ReferenceResource,
    ScopePolicy,
)
from tracecat.agent.references.contracts import (
    AuthoredReferenceInput,
    AuthoredSource,
    ExecutionAuthority,
    ReferenceEdge,
    SelectedReference,
)
from tracecat.agent.references.markdown import parse_markdown_references
from tracecat.agent.references.types import SourceLocation
from tracecat.agent.references.uri import ReferenceDiagnosticCode as Code
from tracecat.agent.references.uri import ReferenceKind as Kind
from tracecat.agent.references.uri import ReferenceTarget
from tracecat.agent.subagents import has_manual_tool_approvals, validate_subagent_alias


class _Rejected(Exception):
    def __init__(
        self,
        code: Code,
        path: tuple[ReferenceTarget, ...],
        location: SourceLocation | None = None,
    ):
        self.diagnostic = CompileDiagnostic(code, path, location)


@dataclass(slots=True)
class _Scope:
    key: str
    parent: str | None
    policy: ScopePolicy
    selected: dict[ReferenceTarget, SelectedReference] = field(default_factory=dict)
    edges: list[ReferenceEdge] = field(default_factory=list)
    heights: dict[ReferenceTarget, int] = field(default_factory=dict)
    provenance: dict[
        ReferenceTarget, tuple[tuple[ReferenceTarget, ...], SourceLocation | None]
    ] = field(default_factory=dict)


async def compile_references(
    authored: AuthoredReferenceInput,
    authority: ExecutionAuthority,
    lookup: ReferenceLookup,
    *,
    policy: ScopePolicy = ScopePolicy(),
    limits: GraphLimits = GraphLimits(),
    candidate_sources: tuple[AuthoredSource, ...] = (),
    aliases: tuple[tuple[ReferenceTarget, str], ...] = (),
    selected_versions: tuple[SelectedReference, ...] | None = None,
) -> CompiledReferences:
    """Compile a candidate or admitted run without persisting or activating it.

    Admission constructs the source/authority envelopes. This is deliberately not
    an API route and never accepts PromptContext. Overrides are already effective
    admitted additions, not raw user request text. Supplying selected_versions
    makes selection closed: every versioned dependency must be pinned.
    """
    if not authority.activate_references:
        return CompiledReferences()
    try:
        if selected_versions is not None and len(
            {item.target for item in selected_versions}
        ) != len(selected_versions):
            raise _Rejected(Code.SNAPSHOT_CONFLICT, (authored.root.owner,))
        state = _Compiler(lookup, authority, limits, aliases, selected_versions)
        async with asyncio.timeout(limits.seconds):
            return await state.compile(authored, policy, candidate_sources)
    except _Rejected as exc:
        return CompiledReferences(diagnostics=(exc.diagnostic,))
    except TimeoutError:
        return CompiledReferences(
            diagnostics=(
                CompileDiagnostic(Code.LIMIT_EXCEEDED, (authored.root.owner,)),
            )
        )


class _Compiler:
    def __init__(
        self,
        lookup: ReferenceLookup,
        authority: ExecutionAuthority,
        limits: GraphLimits,
        aliases: tuple[tuple[ReferenceTarget, str], ...],
        pins: tuple[SelectedReference, ...] | None,
    ):
        self.lookup = lookup
        self.authority = authority
        self.limits = limits
        self.alias_inputs = aliases
        self.aliases: dict[ReferenceTarget, str] = {}
        self.pins = None if pins is None else {item.target: item for item in pins}
        self.resources: dict[ReferenceTarget, ReferenceResource] = {}
        self.scopes: dict[str, _Scope] = {}
        self.edge_count = 0
        self.source_bytes = 0

    async def compile(
        self,
        authored: AuthoredReferenceInput,
        policy: ScopePolicy,
        candidate_sources: tuple[AuthoredSource, ...],
    ) -> CompiledReferences:
        root = authored.root
        if root.owner.kind not in {Kind.AGENT, Kind.SKILL}:
            raise _Rejected(Code.INVALID_SOURCE, (root.owner,))
        self._record_aliases(self.alias_inputs, (root.owner,))
        sources = (root, *candidate_sources)
        self._validate_sources(root.owner, root.version_id, (root, *candidate_sources))
        self._validate_sources(
            root.owner, root.version_id, authored.overrides, override=True
        )
        # The candidate envelope owns manual declarations, including skill
        # frontmatter tools. Reloading a published root would resurrect removed
        # tools or fail for an unpublished candidate.
        root_resource = ReferenceResource(
            selected=SelectedReference(
                target=root.owner,
                version_id=root.version_id,
                content_hash=root.content_hash,
            ),
            sources=sources,
            declarations=tuple(
                Declaration(t)
                for t in (
                    *authored.explicit.targets,
                    *authored.override_declarations.targets,
                )
            ),
            policy=policy,
        )
        self.resources[root.owner] = root_resource
        main = _Scope("root", None, policy)
        self.scopes[main.key] = main
        await self._expand(
            root_resource, main, (root.owner,), overrides=authored.overrides
        )
        compiled: list[CompiledScope] = []
        for scope in self.scopes.values():
            targets = tuple(scope.selected)
            try:
                grants = await self.lookup.policy(targets, scope.policy)
            except ReferenceLookupError as exc:
                path, location = (
                    scope.provenance.get(exc.target, (tuple(scope.selected)[:1], None))
                    if exc.target is not None
                    else (tuple(scope.selected)[:1], None)
                )
                raise _Rejected(exc.code, path, location) from exc
            if grants.tool_count > self.limits.tools:
                raise _Rejected(Code.LIMIT_EXCEEDED, tuple(scope.selected)[:1])
            if scope.parent is not None and has_manual_tool_approvals(
                dict(grants.approvals)
            ):
                raise _Rejected(Code.UNSUPPORTED_CAPABILITY, tuple(scope.selected)[:1])
            compiled.append(
                CompiledScope(
                    scope.key,
                    scope.parent,
                    tuple(scope.selected.values()),
                    tuple(scope.edges),
                    grants,
                )
            )
        return CompiledReferences(scopes=tuple(compiled))

    def _record_aliases(
        self,
        aliases: tuple[tuple[ReferenceTarget, str], ...],
        path: tuple[ReferenceTarget, ...],
    ) -> None:
        for target, alias in aliases:
            if alias != alias.strip():
                raise _Rejected(Code.INVALID_SOURCE, path)
            try:
                validate_subagent_alias(alias)
            except ValueError as exc:
                raise _Rejected(Code.INVALID_SOURCE, path) from exc
            if (
                target.kind != Kind.AGENT
                or (target in self.aliases and self.aliases[target] != alias)
                or any(t != target and a == alias for t, a in self.aliases.items())
            ):
                raise _Rejected(Code.INVALID_SOURCE, path)
            self.aliases[target] = alias

    def _validate_sources(
        self,
        owner: ReferenceTarget,
        version: UUID | None,
        sources: tuple[AuthoredSource, ...],
        *,
        override: bool = False,
    ) -> None:
        for source in sources:
            expected_origin = (
                "skill_file" if owner.kind == Kind.SKILL else "agent_instructions"
            )
            if override:
                expected_origin = "authorized_override"
            if (
                source.owner != owner
                or source.version_id != version
                or source.origin != expected_origin
                or hashlib.sha256(source.markdown.encode()).hexdigest()
                != source.content_hash
            ):
                raise _Rejected(Code.INVALID_SOURCE, (owner,))

    async def _expand(
        self,
        resource: ReferenceResource,
        scope: _Scope,
        path: tuple[ReferenceTarget, ...],
        *,
        overrides: tuple[AuthoredSource, ...] = (),
    ) -> int:
        owner = resource.selected.target
        scope.selected[owner] = resource.selected
        if owner in scope.heights:
            height = scope.heights[owner]
            if len(path) - 1 + height > self.limits.depth:
                raise _Rejected(Code.LIMIT_EXCEEDED, path)
            return height
        height = 0
        self._validate_sources(
            owner,
            resource.selected.version_id,
            resource.sources,
        )
        self._validate_sources(
            owner, resource.selected.version_id, overrides, override=True
        )
        self._record_aliases(
            tuple(
                (d.target, d.alias)
                for d in resource.declarations
                if d.alias is not None
            ),
            path,
        )
        for source in (*resource.sources, *overrides):
            self.source_bytes += len(source.markdown.encode())
            if self.source_bytes > self.limits.source_bytes:
                raise _Rejected(Code.LIMIT_EXCEEDED, path)
            parsed = parse_markdown_references(source.markdown, path=source.path)
            if parsed.diagnostics:
                error = parsed.diagnostics[0]
                raise _Rejected(error.code, path, error.location)
            for occurrence in parsed.references:
                height = max(
                    height,
                    await self._visit(
                        occurrence.target, scope, path, "mention", occurrence.location
                    ),
                )
        for declaration in resource.declarations:
            height = max(
                height,
                await self._visit(
                    declaration.target, scope, path, declaration.contribution, None
                ),
            )
        scope.heights[owner] = height
        return height

    async def _visit(
        self,
        target: ReferenceTarget,
        scope: _Scope,
        path: tuple[ReferenceTarget, ...],
        contribution: Literal["mention", "explicit", "skill_tool"],
        location: SourceLocation | None,
    ) -> int:
        next_path = (*path, target)
        self.edge_count += 1
        if (
            self.edge_count > self.limits.edges
            or len(next_path) - 1 > self.limits.depth
        ):
            raise _Rejected(Code.LIMIT_EXCEEDED, next_path, location)
        if target in path:
            raise _Rejected(Code.CYCLE, next_path, location)
        if target.kind == Kind.AGENT and scope.parent is not None:
            raise _Rejected(Code.NESTED_AGENT, next_path, location)
        self._authorize(target, next_path, location)
        if (
            target.kind == Kind.TOOL
            and scope.policy.namespaces
            and not any(
                target.identity.startswith(ns) for ns in scope.policy.namespaces
            )
        ):
            raise _Rejected(Code.FORBIDDEN, next_path, location)
        scope.provenance.setdefault(target, (next_path, location))
        scope.edges.append(
            ReferenceEdge(
                scope=scope.key,
                source=path[-1],
                target=target,
                location=location,
                contribution=contribution,
            )
        )
        if target not in self.resources:
            if len(self.resources) >= self.limits.nodes:
                raise _Rejected(Code.LIMIT_EXCEEDED, next_path, location)
            pin = self.pins.get(target) if self.pins is not None else None
            if (
                self.pins is not None
                and target.kind in {Kind.AGENT, Kind.SKILL, Kind.WORKFLOW}
                and pin is None
            ):
                raise _Rejected(Code.SNAPSHOT_CONFLICT, next_path, location)
            try:
                resource = await self.lookup.resolve(
                    target,
                    version_id=pin.version_id if pin else None,
                    limits=self.limits,
                )
            except ReferenceLookupError as exc:
                raise _Rejected(exc.code, next_path, location) from exc
            if resource.selected.target != target or (
                pin is not None
                and (
                    resource.selected.version_id != pin.version_id
                    or (
                        pin.content_hash is not None
                        and resource.selected.content_hash != pin.content_hash
                    )
                )
            ):
                raise _Rejected(Code.SNAPSHOT_CONFLICT, next_path, location)
            self.resources[target] = resource
        resource = self.resources[target]
        scope.selected[target] = resource.selected
        if target.kind == Kind.AGENT:
            alias = self.aliases.get(target)
            if alias is None:
                # The stable suffix avoids order-dependent slug collisions.
                alias = f"{resource.slug[:60] or 'specialist'}-{UUID(target.identity).hex[:8]}"
                self._record_aliases(((target, alias),), next_path)
            child = self.scopes.setdefault(
                alias, _Scope(alias, scope.key, resource.policy)
            )
            return 1 + await self._expand(resource, child, next_path)
        return 1 + await self._expand(resource, scope, next_path)

    def _authorize(
        self,
        target: ReferenceTarget,
        path: tuple[ReferenceTarget, ...],
        location: SourceLocation | None,
    ) -> None:
        authority = self.authority
        denied = target in authority.excluded_targets or (
            target.kind == Kind.MCP_TOOL
            and ReferenceTarget(Kind.MCP_SERVER, target.identity)
            in authority.excluded_targets
        )
        if target.kind == Kind.MCP_SERVER:
            denied |= any(
                t.kind == Kind.MCP_TOOL and t.identity == target.identity
                for t in authority.excluded_targets
            )
        if target.kind == Kind.TOOL:
            denied |= (
                authority.action_scope_ceiling is not None
                and target.identity not in authority.action_scope_ceiling
            )
            denied |= authority.namespace_ceiling is not None and not any(
                target.identity.startswith(ns) for ns in authority.namespace_ceiling
            )
        elif target.kind in {Kind.MCP_SERVER, Kind.MCP_TOOL, Kind.WORKFLOW, Kind.AGENT}:
            # A registry-only admission ceiling cannot silently grant other call routes.
            denied |= authority.action_scope_ceiling is not None
        if denied:
            raise _Rejected(Code.FORBIDDEN, path, location)
