"""Graph invariants, independently of persistence, providers, and prompt assembly."""

import asyncio
import hashlib
import inspect
from collections import Counter
from uuid import uuid4

import pytest

from tracecat.agent.references.compiler import compile_references
from tracecat.agent.references.compiler_types import (
    GraphLimits,
    ReferenceLookupError,
    ReferenceResource,
    ScopeGrants,
    ScopePolicy,
)
from tracecat.agent.references.contracts import (
    AuthoredReferenceInput,
    AuthoredSource,
    ExecutionAuthority,
    ExplicitDeclarations,
    SelectedReference,
)
from tracecat.agent.references.uri import ReferenceDiagnosticCode as Code
from tracecat.agent.references.uri import ReferenceKind as Kind
from tracecat.agent.references.uri import ReferenceTarget, serialize_reference_uri


@pytest.fixture
def anyio_backend():
    return "asyncio"


def target(kind=Kind.SKILL):
    return ReferenceTarget(kind, str(uuid4()))


TOOL = ReferenceTarget(Kind.TOOL, "core.http_request")
OTHER = ReferenceTarget(Kind.TOOL, "tools.example.read")


def source(owner, targets=(), text=None, version=None, path=None):
    markdown = (
        text
        if text is not None
        else "\n".join(f"[Use]({serialize_reference_uri(t)})" for t in targets)
    )
    return AuthoredSource(
        origin="skill_file" if owner.kind == Kind.SKILL else "agent_instructions",
        owner=owner,
        version_id=version or uuid4(),
        path=path or ("SKILL.md" if owner.kind == Kind.SKILL else "instructions.md"),
        content_hash=hashlib.sha256(markdown.encode()).hexdigest(),
        markdown=markdown,
    )


def resource(owner, targets=(), declarations=(), policy=ScopePolicy()):
    src = source(owner, targets)
    return ReferenceResource(
        SelectedReference(target=owner, version_id=src.version_id),
        (src,),
        tuple(declarations),
        policy,
        "helper",
    )


def authored(targets=(), explicit=(), root=None):
    return AuthoredReferenceInput(
        root=root or source(target(Kind.AGENT), targets),
        explicit=ExplicitDeclarations(targets=tuple(explicit)),
    )


def authority(**changes):
    return ExecutionAuthority(
        actor_id=uuid4(),
        session_mode="workspace_chat",
        activate_references=True,
        action_scope_ceiling=None,
        namespace_ceiling=None,
        admission_policy_hash="a" * 64,
        **changes,
    )


class Lookup:
    def __init__(self, *resources):
        self.resources = {r.selected.target: r for r in resources}
        self.calls = Counter()
        self.pins = []

    async def resolve(self, target, *, version_id, limits):
        self.calls[target] += 1
        self.pins.append(version_id)
        if target in self.resources:
            return self.resources[target]
        if target.kind == Kind.TOOL:
            return ReferenceResource(SelectedReference(target=target))
        raise ReferenceLookupError()

    async def policy(self, targets, policy):
        actions = tuple(t.identity for t in targets if t.kind == Kind.TOOL)
        return ScopeGrants(
            actions=actions, approvals=policy.approvals, tool_count=len(actions)
        )


@pytest.mark.anyio
async def test_nested_diamond_deduplicates_but_retains_contributions():
    a, b, shared = target(), target(), target()
    lookup = Lookup(
        resource(a, (shared,)), resource(b, (shared,)), resource(shared, (TOOL,))
    )
    result = await compile_references(
        authored((a, b, TOOL), (TOOL,)), authority(), lookup
    )
    assert not result.diagnostics
    (scope,) = result.scopes
    assert scope.grants.actions == (TOOL.identity,)
    assert lookup.calls[shared] == lookup.calls[TOOL] == 1
    assert len([e for e in scope.edges if e.target == shared]) == 2
    assert {e.contribution for e in scope.edges if e.target == TOOL} == {
        "mention",
        "explicit",
    }


@pytest.mark.anyio
async def test_removal_recompiles_only_remaining_sources():
    skill = target()
    lookup = Lookup(resource(skill, (TOOL,)))
    for refs, manual, expected in [
        ((skill, TOOL), (), (TOOL.identity,)),
        ((skill,), (), (TOOL.identity,)),
        ((), (TOOL,), (TOOL.identity,)),
        ((), (), ()),
    ]:
        result = await compile_references(authored(refs, manual), authority(), lookup)
        assert result.scopes[0].grants.actions == expected


@pytest.mark.anyio
async def test_scopes_are_separate_but_versions_shared():
    skill, child = target(), target(Kind.AGENT)
    lookup = Lookup(resource(skill, (TOOL,)), resource(child, (skill, OTHER)))
    result = await compile_references(authored((skill, child)), authority(), lookup)
    assert not result.diagnostics
    root, sub = result.scopes
    assert root.grants.actions == (TOOL.identity,)
    assert set(sub.grants.actions) == {TOOL.identity, OTHER.identity}
    assert sub.parent == "root"
    assert lookup.calls[skill] == 1
    assert next(s for s in root.selected if s.target == skill) == next(
        s for s in sub.selected if s.target == skill
    )


@pytest.mark.anyio
async def test_child_skill_cannot_delegate_and_error_keeps_path():
    child, skill, nested = target(Kind.AGENT), target(), target(Kind.AGENT)
    lookup = Lookup(resource(child, (skill,)), resource(skill, (nested,)))
    request = authored((child,))
    result = await compile_references(request, authority(), lookup)
    assert result.scopes == ()
    (error,) = result.diagnostics
    assert error.code == Code.NESTED_AGENT
    assert error.path == (request.root.owner, child, skill, nested)
    assert error.location is not None
    assert error.location.path == "SKILL.md"
    assert nested not in lookup.calls


@pytest.mark.anyio
async def test_candidate_root_replaces_old_published_graph_for_cycle_check():
    a, b = target(), target()
    lookup = Lookup(resource(a), resource(b, (a,)))
    result = await compile_references(
        authored(root=source(a, (b,))), authority(), lookup
    )
    assert result.diagnostics[0].code == Code.CYCLE
    assert lookup.calls[a] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("manual", [True, False])
async def test_manual_and_derived_tool_grants_obey_authority(manual):
    auth = authority().model_copy(update={"action_scope_ceiling": ()})
    request = authored((), (TOOL,)) if manual else authored((TOOL,))
    result = await compile_references(request, auth, Lookup())
    assert result.scopes == ()
    assert result.diagnostics[0].code == Code.FORBIDDEN
    assert result.diagnostics[0].path[-1] == TOOL


@pytest.mark.anyio
async def test_namespace_policy_denial_keeps_skill_source_location():
    skill = target()
    result = await compile_references(
        authored((skill,)),
        authority(),
        Lookup(resource(skill, (OTHER,))),
        policy=ScopePolicy(namespaces=("core.",)),
    )
    (error,) = result.diagnostics
    assert error.code == Code.FORBIDDEN
    assert error.path[-2:] == (skill, OTHER)
    assert error.location is not None
    assert error.location.path == "SKILL.md"


@pytest.mark.anyio
async def test_child_approval_policy_is_not_dropped():
    child = target(Kind.AGENT)
    result = await compile_references(
        authored((child,)),
        authority(),
        Lookup(
            resource(
                child, (TOOL,), policy=ScopePolicy(approvals=((TOOL.identity, True),))
            )
        ),
    )
    assert result.diagnostics[0].code == Code.UNSUPPORTED_CAPABILITY
    assert not result.scopes
    root = await compile_references(
        authored((TOOL,)),
        authority(),
        Lookup(),
        policy=ScopePolicy(approvals=((TOOL.identity, True),)),
    )
    assert root.scopes[0].grants.approvals == ((TOOL.identity, True),)


@pytest.mark.anyio
async def test_tool_free_ignores_inherited_prompt_without_lookup():
    lookup = Lookup()
    auth = authority().model_copy(
        update={
            "session_mode": "tool_free",
            "activate_references": False,
            "action_scope_ceiling": (),
        }
    )
    result = await compile_references(authored((TOOL,)), auth, lookup)
    assert result.scopes == result.diagnostics == ()
    assert not lookup.calls


@pytest.mark.anyio
async def test_code_and_quoted_builder_configuration_not_authored_sources():
    text = f"```\n[Use]({serialize_reference_uri(TOOL)})\n```"
    result = await compile_references(
        authored(root=source(target(Kind.AGENT), text=text)), authority(), Lookup()
    )
    assert result.scopes[0].grants.actions == ()
    # There is no PromptContext argument; admission must not supply quoted text as a source.
    assert "prompt_context" not in inspect.signature(compile_references).parameters


@pytest.mark.anyio
async def test_alias_reuse_and_conflicts():
    child, other = target(Kind.AGENT), target(Kind.AGENT)
    lookup = Lookup(resource(child), resource(other))
    result = await compile_references(
        authored((child,), (child,)),
        authority(),
        lookup,
        aliases=((child, "specialist"),),
    )
    assert [s.key for s in result.scopes] == ["root", "specialist"]
    for aliases in [
        ((child, "one"), (child, "two")),
        ((child, "one"), (other, "one")),
        ((child, "root"),),
    ]:
        result = await compile_references(
            authored((child, other)), authority(), lookup, aliases=aliases
        )
        assert result.diagnostics[0].code == Code.INVALID_SOURCE


@pytest.mark.anyio
@pytest.mark.parametrize(
    "limits",
    [
        GraphLimits(depth=1),
        GraphLimits(nodes=2),
        GraphLimits(edges=1),
        GraphLimits(source_bytes=1),
        GraphLimits(tools=1),
    ],
)
async def test_graph_budgets_return_no_partial_grants(limits):
    skill = target()
    result = await compile_references(
        authored((skill, TOOL)),
        authority(),
        Lookup(resource(skill, (TOOL, OTHER))),
        limits=limits,
    )
    assert not result.scopes
    assert result.diagnostics[0].code == Code.LIMIT_EXCEEDED


@pytest.mark.anyio
async def test_timeout_bounds_lookup():
    class Slow(Lookup):
        async def resolve(self, target, *, version_id, limits):
            await asyncio.sleep(1)
            return await super().resolve(target, version_id=version_id, limits=limits)

    result = await compile_references(
        authored((TOOL,)), authority(), Slow(), limits=GraphLimits(seconds=0.001)
    )
    assert result.diagnostics[0].code == Code.LIMIT_EXCEEDED
    assert not result.scopes


@pytest.mark.anyio
async def test_exact_selection_does_not_silently_follow_latest():
    skill = target()
    old = resource(skill, (TOOL,))
    lookup = Lookup(old)
    result = await compile_references(
        authored((skill,)), authority(), lookup, selected_versions=(old.selected,)
    )
    assert not result.diagnostics
    assert lookup.pins[0] == old.selected.version_id
    changed = Lookup(resource(skill, (OTHER,)))
    for pins in [(), (old.selected,)]:
        result = await compile_references(
            authored((skill,)), authority(), changed, selected_versions=pins
        )
        assert result.diagnostics[0].code == Code.SNAPSHOT_CONFLICT
        assert not result.scopes


@pytest.mark.anyio
async def test_missing_and_tampered_sources_fail_closed():
    missing = await compile_references(authored((target(),)), authority(), Lookup())
    assert missing.diagnostics[0].code == Code.UNRESOLVED
    src = source(target(Kind.AGENT), (TOOL,))
    altered = src.model_copy(update={"content_hash": "f" * 64})
    result = await compile_references(authored(root=altered), authority(), Lookup())
    assert result.diagnostics[0].code == Code.INVALID_SOURCE
    assert not result.scopes


@pytest.mark.anyio
async def test_longer_diamond_path_cannot_bypass_depth_budget():
    shared, deep = target(), target()
    lookup = Lookup(resource(shared, (TOOL,)), resource(deep, (shared,)))
    result = await compile_references(
        authored((shared, deep)), authority(), lookup, limits=GraphLimits(depth=2)
    )
    assert result.diagnostics[0].code == Code.LIMIT_EXCEEDED
    assert not result.scopes


@pytest.mark.anyio
async def test_whole_server_cannot_hide_an_explicit_tool_denial():
    server = target(Kind.MCP_SERVER)
    excluded = ReferenceTarget(Kind.MCP_TOOL, server.identity, "write")
    auth = authority().model_copy(update={"excluded_targets": (excluded,)})
    result = await compile_references(authored((server,)), auth, Lookup())
    assert result.diagnostics[0].code == Code.FORBIDDEN


@pytest.mark.anyio
async def test_mixed_skill_agent_cycle_reports_full_path():
    skill, child = target(), target(Kind.AGENT)
    root = source(target(Kind.AGENT), (skill,))
    lookup = Lookup(resource(skill, (child,)), resource(child, (root.owner,)))
    result = await compile_references(authored(root=root), authority(), lookup)
    assert result.diagnostics[0].code == Code.CYCLE
    assert result.diagnostics[0].path == (root.owner, skill, child, root.owner)


@pytest.mark.anyio
@pytest.mark.parametrize("override_count", [1, 64])
async def test_effective_authorized_override_is_a_separate_contribution(override_count):
    request = authored((TOOL,))
    override = source(
        request.root.owner,
        (OTHER,),
        version=request.root.version_id,
        path="override.md",
    ).model_copy(update={"origin": "authorized_override"})
    overrides = tuple(
        override.model_copy(update={"path": f"override-{index}.md"})
        for index in range(override_count)
    )
    request = request.model_copy(update={"overrides": overrides})
    result = await compile_references(request, authority(), Lookup())
    assert set(result.scopes[0].grants.actions) == {TOOL.identity, OTHER.identity}
    edges = [e for e in result.scopes[0].edges if e.target == OTHER]
    assert {e.location.path for e in edges if e.location is not None} == {
        f"override-{index}.md" for index in range(override_count)
    }


@pytest.mark.anyio
async def test_builder_and_workspace_ceiling_do_not_expand_on_nested_skills():
    skill = target()
    for mode in ("builder", "workspace_chat"):
        auth = authority().model_copy(
            update={"session_mode": mode, "action_scope_ceiling": (TOOL.identity,)}
        )
        result = await compile_references(
            authored((TOOL, skill)), auth, Lookup(resource(skill, (OTHER,)))
        )
        assert not result.scopes
        assert result.diagnostics[0].path[-1] == OTHER
        assert result.diagnostics[0].code == Code.FORBIDDEN


@pytest.mark.anyio
async def test_all_candidate_skill_files_are_scanned():
    root = source(target(), (TOOL,))
    secondary = source(
        root.owner, (OTHER,), version=root.version_id, path="references/details.md"
    )
    result = await compile_references(
        authored(root=root), authority(), Lookup(), candidate_sources=(secondary,)
    )
    assert set(result.scopes[0].grants.actions) == {TOOL.identity, OTHER.identity}
    wrong_owner = source(target(), (OTHER,), version=root.version_id)
    result = await compile_references(
        authored(root=root), authority(), Lookup(), candidate_sources=(wrong_owner,)
    )
    assert result.diagnostics[0].code == Code.INVALID_SOURCE
    assert not result.scopes


@pytest.mark.anyio
async def test_duplicate_snapshot_identities_return_diagnostic():
    skill = target()
    selected = resource(skill).selected
    result = await compile_references(
        authored((skill,)),
        authority(),
        Lookup(),
        selected_versions=(selected, selected),
    )
    assert not result.scopes
    assert result.diagnostics[0].code == Code.SNAPSHOT_CONFLICT


@pytest.mark.anyio
@pytest.mark.parametrize("alias", ["worker ", " worker", "\tworker"])
async def test_noncanonical_alias_is_rejected_before_expansion(alias):
    child = target(Kind.AGENT)
    result = await compile_references(
        authored((child,)),
        authority(),
        Lookup(resource(child, (TOOL,))),
        aliases=((child, alias),),
    )
    assert not result.scopes
    assert result.diagnostics[0].code == Code.INVALID_SOURCE


@pytest.mark.anyio
@pytest.mark.parametrize("slot", ["root", "candidate", "dependency", "override"])
async def test_source_origins_are_bound_to_their_admitted_slot(slot):
    root = source(target(Kind.AGENT))
    request = authored(root=root)
    lookup = Lookup()
    candidates = ()
    if slot == "root":
        request = authored(
            root=root.model_copy(update={"origin": "authorized_override"})
        )
    elif slot == "candidate":
        candidates = (
            source(root.owner, version=root.version_id, path="other.md").model_copy(
                update={"origin": "authorized_override"}
            ),
        )
    elif slot == "dependency":
        skill = target()
        dependency = resource(skill)
        bad = dependency.sources[0].model_copy(update={"origin": "authorized_override"})
        lookup = Lookup(ReferenceResource(dependency.selected, (bad,)))
        request = authored((skill,))
    else:
        request = request.model_copy(update={"overrides": (root,)})
    result = await compile_references(
        request, authority(), lookup, candidate_sources=candidates
    )
    assert not result.scopes
    assert result.diagnostics[0].code == Code.INVALID_SOURCE


@pytest.mark.anyio
async def test_root_skill_uses_candidate_manual_tools_without_loading_old_version():
    skill, server = target(), target(Kind.MCP_SERVER)
    # A previously published declaration must not reappear after the candidate removes it.
    lookup = Lookup(
        resource(skill, (OTHER,)), ReferenceResource(SelectedReference(target=server))
    )
    request = authored(root=source(skill), explicit=(TOOL, server))
    result = await compile_references(request, authority(), lookup)
    assert not result.diagnostics
    assert lookup.calls[skill] == 0
    assert result.scopes[0].grants.actions == (TOOL.identity,)
    assert {e.target for e in result.scopes[0].edges} == {TOOL, server}
    assert all(e.contribution == "explicit" for e in result.scopes[0].edges)
    removed = await compile_references(authored(root=request.root), authority(), lookup)
    assert not removed.scopes[0].grants.actions
    assert not removed.scopes[0].edges
