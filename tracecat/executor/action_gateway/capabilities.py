"""Bound nested Agent SDK calls to the configured registry toolset.

``core.script.run_python`` runs user-authored code, so its executor token must
not expose every Action Gateway operation available to the caller. For each
nested request this module enforces the two independent authorization bounds::

    caller Role allows action
    AND
    Agent execution grant allows action

The SDK calls lower-level HTTP endpoints rather than dispatching another
registry action, so the server-matched FastAPI endpoint is mapped back to the
registry action(s) that legitimately use it. The lookup is O(1), happens after
routing, and denies unmapped endpoints by default for Agent ``run_python``.

Lower-level SDK surfaces without a registry action are intentionally unmapped.
In particular, granting ``run_python`` authorizes script execution; it is not a
wildcard for workspace variables or every internal API available to the caller.

Most endpoints represent one concrete operation. A few are shared by registry
actions that intentionally use the same primitive (for example an update action
that reads back its result). When request parameters select a stronger operation,
the endpoint's capability resolver narrows the candidate action before the
dual check. Add a resolver and an attack-shaped test whenever a new shared
endpoint has such a parameter-dependent privilege boundary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, Literal, NamedTuple

from fastapi import HTTPException, Request, status
from fastapi.routing import APIRoute

from tracecat.auth.executor_tokens import ExecutorTokenPayload, verify_executor_token
from tracecat.authz.controls import has_scope
from tracecat.dsl.enums import PlatformAction

GatewayMethod = Literal["DELETE", "GET", "PATCH", "POST"]


class GatewayRouteKey(NamedTuple):
    """A matched Action Gateway HTTP operation."""

    method: GatewayMethod
    path: str


@dataclass(frozen=True, slots=True)
class GatewayActionRequirement:
    """Alternative base actions plus any cumulative action requirements."""

    any_of: frozenset[str]
    all_of: frozenset[str] = frozenset()

    @property
    def actions(self) -> frozenset[str]:
        """Return every action referenced by this requirement."""
        return self.any_of | self.all_of


GatewayActionResolver = Callable[[Request], Awaitable[GatewayActionRequirement]]


def gateway_route_key(method: str, path: str) -> GatewayRouteKey | None:
    """Build a typed route key, denying HTTP methods outside the gateway policy."""
    match method:
        case "DELETE" | "GET" | "PATCH" | "POST":
            return GatewayRouteKey(method, path)
        case _:
            return None


@dataclass(frozen=True, slots=True)
class GatewayCapability:
    """Registry actions and optional request-aware selector for one route."""

    method: GatewayMethod
    path: str
    actions: frozenset[str]
    resolver: GatewayActionResolver | None = None

    @property
    def route_key(self) -> GatewayRouteKey:
        return GatewayRouteKey(self.method, self.path)


def _index_capabilities(
    declarations: tuple[GatewayCapability, ...],
) -> dict[GatewayRouteKey, GatewayCapability]:
    indexed: dict[GatewayRouteKey, GatewayCapability] = {}
    for capability in declarations:
        key = capability.route_key
        if key in indexed:
            raise ValueError(f"Duplicate Action Gateway capability declaration: {key}")
        indexed[key] = capability
    return indexed


async def _request_json_object(request: Request) -> dict[str, Any]:
    """Read a JSON object without turning malformed input into a gateway 500."""
    try:
        payload = await request.json()
    except (JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


async def _resolve_run_agent_action(request: Request) -> GatewayActionRequirement:
    """Distinguish an ad-hoc Agent configuration from a stored preset run."""
    payload = await _request_json_object(request)
    if payload.get("preset_slug"):
        return GatewayActionRequirement(any_of=frozenset({"ai.preset_agent"}))
    return GatewayActionRequirement(any_of=frozenset({"ai.agent"}))


async def _resolve_get_case_action(request: Request) -> GatewayActionRequirement:
    """Treat linked rows as a separate capability from base case details."""
    include_rows = request.query_params.get("include_rows")
    if include_rows is None or include_rows.lower() in {"false", "0", "off", "no"}:
        return GatewayActionRequirement(any_of=frozenset({"core.cases.get_case"}))
    return GatewayActionRequirement(
        any_of=frozenset({"core.cases.get_linked_case_rows"})
    )


def _case_action_requirement(
    request: Request, base_action: str
) -> GatewayActionRequirement:
    """Require linked-row access in addition to the base case operation."""
    include_rows = request.query_params.get("include_rows")
    all_of = (
        frozenset({"core.cases.get_linked_case_rows"})
        if include_rows is not None
        and include_rows.lower() not in {"false", "0", "off", "no"}
        else frozenset()
    )
    return GatewayActionRequirement(any_of=frozenset({base_action}), all_of=all_of)


async def _resolve_list_cases_action(request: Request) -> GatewayActionRequirement:
    """Bound optional list hydration by the linked-row action grant."""
    return _case_action_requirement(request, "core.cases.list_cases")


async def _resolve_search_cases_action(request: Request) -> GatewayActionRequirement:
    """Bound optional search hydration by the linked-row action grant."""
    return _case_action_requirement(request, "core.cases.search_cases")


async def _resolve_update_case_action(request: Request) -> GatewayActionRequirement:
    """Bound optional update-response hydration by the linked-row action grant."""
    return _case_action_requirement(request, "core.cases.update_case")


async def _resolve_create_comment_action(request: Request) -> GatewayActionRequirement:
    """Require the general comment capability for top-level comments."""
    payload = await _request_json_object(request)
    if payload.get("parent_id") is None:
        return GatewayActionRequirement(any_of=frozenset({"core.cases.create_comment"}))
    # The general create action also accepts a parent ID, so either configured
    # action legitimately represents a reply.
    return GatewayActionRequirement(
        any_of=frozenset({"core.cases.create_comment", "core.cases.reply_to_comment"})
    )


async def _resolve_table_lookup_action(request: Request) -> GatewayActionRequirement:
    """Prevent a single-row lookup grant from expanding to an arbitrary batch."""
    payload = await _request_json_object(request)
    if payload.get("limit") == 1:
        return GatewayActionRequirement(
            any_of=frozenset({"core.table.lookup", "core.table.lookup_many"})
        )
    return GatewayActionRequirement(any_of=frozenset({"core.table.lookup_many"}))


# Values identify registry actions whose implementation legitimately reaches the
# endpoint. Multiple values are allowed only for a shared underlying primitive;
# parameter-dependent privilege differences use the capability's resolver.
# Do not map non-registry SDK surfaces (for example workspace variables) to
# ``run_python``: doing so would let script execution bypass the Agent toolset
# upper bound. Their existing resource-scope checks constrain only the caller.
_GATEWAY_CAPABILITY_DECLARATIONS: tuple[GatewayCapability, ...] = (
    GatewayCapability(
        "POST",
        "/internal/agent/run",
        frozenset({"ai.agent", "ai.preset_agent"}),
        resolver=_resolve_run_agent_action,
    ),
    GatewayCapability(
        "POST",
        "/internal/agent/rank",
        frozenset({"ai.rank_documents", "ai.select_field", "ai.select_fields"}),
    ),
    GatewayCapability(
        "POST",
        "/internal/agent/rank-pairwise",
        frozenset({"ai.rank_documents", "ai.select_field", "ai.select_fields"}),
    ),
    GatewayCapability(
        "GET", "/internal/agent/presets", frozenset({"ai.agent.list_presets"})
    ),
    GatewayCapability(
        "POST", "/internal/agent/presets", frozenset({"ai.agent.create_preset"})
    ),
    GatewayCapability(
        "GET",
        "/internal/agent/presets/by-slug/{slug}",
        frozenset({"ai.agent.get_preset"}),
    ),
    GatewayCapability(
        "PATCH",
        "/internal/agent/presets/by-slug/{slug}",
        frozenset({"ai.agent.update_preset"}),
    ),
    GatewayCapability(
        "DELETE",
        "/internal/agent/presets/by-slug/{slug}",
        frozenset({"ai.agent.delete_preset"}),
    ),
    GatewayCapability(
        "GET", "/internal/agent/skills", frozenset({"ai.skill.list_skills"})
    ),
    GatewayCapability(
        "POST", "/internal/agent/skills", frozenset({"ai.skill.create_skill"})
    ),
    GatewayCapability(
        "GET", "/internal/agent/skills/{skill_id}", frozenset({"ai.skill.get_skill"})
    ),
    GatewayCapability(
        "POST",
        "/internal/agent/skills/{skill_id}/versions",
        frozenset({"ai.skill.publish_skill_version"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/agent/skills/{skill_id}/versions",
        frozenset({"ai.skill.list_skill_versions"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/agent/skills/{skill_id}/versions/{version_id}",
        frozenset({"ai.skill.get_skill_version"}),
    ),
    GatewayCapability(
        "POST",
        "/internal/agent/skills/{skill_id}/versions/{version_id}/restore",
        frozenset({"ai.skill.restore_skill_version"}),
    ),
    GatewayCapability(
        "DELETE",
        "/internal/agent/skills/{skill_id}",
        frozenset({"ai.skill.archive_skill"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/cases",
        frozenset({"core.cases.list_cases", "core.cases.get_linked_case_rows"}),
        resolver=_resolve_list_cases_action,
    ),
    GatewayCapability(
        "GET",
        "/internal/cases/search",
        frozenset({"core.cases.search_cases", "core.cases.get_linked_case_rows"}),
        resolver=_resolve_search_cases_action,
    ),
    GatewayCapability(
        "GET",
        "/internal/cases/{case_id}",
        frozenset({"core.cases.get_case", "core.cases.get_linked_case_rows"}),
        resolver=_resolve_get_case_action,
    ),
    GatewayCapability("POST", "/internal/cases", frozenset({"core.cases.create_case"})),
    GatewayCapability(
        "POST", "/internal/cases/simple", frozenset({"core.cases.create_case"})
    ),
    GatewayCapability(
        "PATCH",
        "/internal/cases/{case_id}",
        frozenset({"core.cases.update_case", "core.cases.get_linked_case_rows"}),
        resolver=_resolve_update_case_action,
    ),
    GatewayCapability(
        "PATCH",
        "/internal/cases/{case_id}/simple",
        frozenset({"core.cases.update_case"}),
    ),
    GatewayCapability(
        "DELETE", "/internal/cases/{case_id}", frozenset({"core.cases.delete_case"})
    ),
    GatewayCapability(
        "GET",
        "/internal/cases/{case_id}/events",
        frozenset({"core.cases.list_case_events"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/cases/{case_id}/comments",
        frozenset({"core.cases.list_comments"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/cases/{case_id}/comments/threads",
        frozenset({"core.cases.list_comment_threads"}),
    ),
    GatewayCapability(
        "POST",
        "/internal/cases/{case_id}/comments",
        frozenset({"core.cases.create_comment", "core.cases.reply_to_comment"}),
        resolver=_resolve_create_comment_action,
    ),
    GatewayCapability(
        "POST",
        "/internal/cases/{case_id}/comments/simple",
        frozenset({"core.cases.create_comment", "core.cases.reply_to_comment"}),
        resolver=_resolve_create_comment_action,
    ),
    GatewayCapability(
        "PATCH",
        "/internal/cases/{case_id}/comments/{comment_id}",
        frozenset({"core.cases.update_comment"}),
    ),
    GatewayCapability(
        "PATCH",
        "/internal/comments/{comment_id}",
        frozenset({"core.cases.update_comment"}),
    ),
    GatewayCapability(
        "PATCH",
        "/internal/comments/{comment_id}/simple",
        frozenset({"core.cases.update_comment"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/comments/{comment_id}/thread",
        frozenset({"core.cases.get_comment_thread"}),
    ),
    GatewayCapability(
        "POST",
        "/internal/cases/{case_id}/assign",
        frozenset({"core.cases.assign_user"}),
    ),
    GatewayCapability(
        "POST",
        "/internal/cases/{case_id}/assign-by-email",
        frozenset({"core.cases.assign_user_by_email"}),
    ),
    GatewayCapability(
        "POST", "/internal/cases/{case_id}/tags", frozenset({"core.cases.add_case_tag"})
    ),
    GatewayCapability(
        "DELETE",
        "/internal/cases/{case_id}/tags/{tag_identifier}",
        frozenset({"core.cases.remove_case_tag"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/cases/{case_id}/attachments",
        frozenset({"core.cases.list_attachments"}),
    ),
    GatewayCapability(
        "POST",
        "/internal/cases/{case_id}/attachments",
        frozenset(
            {"core.cases.upload_attachment", "core.cases.upload_attachment_from_url"}
        ),
    ),
    GatewayCapability(
        "GET",
        "/internal/cases/{case_id}/attachments/{attachment_id}",
        frozenset({"core.cases.get_attachment_download_url"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/cases/{case_id}/attachments/{attachment_id}/metadata",
        frozenset({"core.cases.get_attachment"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/cases/{case_id}/attachments/{attachment_id}/download",
        frozenset({"core.cases.download_attachment"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/cases/{case_id}/attachments/{attachment_id}/url",
        frozenset({"core.cases.get_attachment_download_url"}),
    ),
    GatewayCapability(
        "DELETE",
        "/internal/cases/{case_id}/attachments/{attachment_id}",
        frozenset({"core.cases.delete_attachment"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/cases/{case_id}/rows",
        frozenset({"core.cases.get_linked_case_rows"}),
    ),
    GatewayCapability(
        "POST", "/internal/cases/{case_id}/rows", frozenset({"core.cases.link_row"})
    ),
    GatewayCapability(
        "POST",
        "/internal/cases/{case_id}/rows/insert",
        frozenset({"core.cases.insert_row"}),
    ),
    GatewayCapability(
        "DELETE",
        "/internal/cases/{case_id}/rows/{table_id}/{row_id}",
        frozenset({"core.cases.unlink_row"}),
    ),
    GatewayCapability(
        "GET", "/internal/cases/{case_id}/tasks", frozenset({"core.cases.list_tasks"})
    ),
    GatewayCapability(
        "POST", "/internal/cases/{case_id}/tasks", frozenset({"core.cases.create_task"})
    ),
    GatewayCapability(
        "PATCH",
        "/internal/cases/{case_id}/tasks/{task_id}",
        frozenset({"core.cases.update_task"}),
    ),
    GatewayCapability(
        "DELETE",
        "/internal/cases/{case_id}/tasks/{task_id}",
        frozenset({"core.cases.delete_task"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/cases/tasks/{task_id}",
        frozenset({"core.cases.get_task", "core.cases.update_task"}),
    ),
    GatewayCapability(
        "PATCH",
        "/internal/cases/tasks/{task_id}",
        frozenset({"core.cases.update_task"}),
    ),
    GatewayCapability(
        "DELETE",
        "/internal/cases/tasks/{task_id}",
        frozenset({"core.cases.delete_task"}),
    ),
    GatewayCapability(
        "POST", "/internal/cases/metrics", frozenset({"core.cases.get_case_metrics"})
    ),
    GatewayCapability(
        "POST",
        "/internal/deduplicate/digests",
        frozenset({"core.transform.deduplicate", "core.transform.is_duplicate"}),
    ),
    GatewayCapability("GET", "/internal/tables", frozenset({"core.table.list_tables"})),
    GatewayCapability(
        "POST", "/internal/tables", frozenset({"core.table.create_table"})
    ),
    GatewayCapability(
        "PATCH", "/internal/tables/{table_name}", frozenset({"core.table.update_table"})
    ),
    GatewayCapability(
        "GET",
        "/internal/tables/{table_name}/metadata",
        frozenset({"core.table.get_table_metadata"}),
    ),
    GatewayCapability(
        "POST",
        "/internal/tables/{table_name}/columns",
        frozenset({"core.table.create_column"}),
    ),
    GatewayCapability(
        "PATCH",
        "/internal/tables/{table_name}/columns/{column_name}",
        frozenset({"core.table.update_column"}),
    ),
    GatewayCapability(
        "DELETE",
        "/internal/tables/{table_name}/columns/{column_name}",
        frozenset({"core.table.delete_column"}),
    ),
    GatewayCapability(
        "POST",
        "/internal/tables/{table_name}/lookup",
        frozenset({"core.table.lookup", "core.table.lookup_many"}),
        resolver=_resolve_table_lookup_action,
    ),
    GatewayCapability(
        "POST", "/internal/tables/{table_name}/exists", frozenset({"core.table.is_in"})
    ),
    GatewayCapability(
        "POST",
        "/internal/tables/{table_name}/search",
        frozenset({"core.table.search_rows"}),
    ),
    GatewayCapability(
        "POST",
        "/internal/tables/{table_name}/rows",
        frozenset({"core.table.insert_row"}),
    ),
    GatewayCapability(
        "POST",
        "/internal/tables/{table_name}/rows/batch",
        frozenset({"core.table.insert_rows"}),
    ),
    GatewayCapability(
        "PATCH",
        "/internal/tables/{table_name}/rows/{row_id}",
        frozenset({"core.table.update_row"}),
    ),
    GatewayCapability(
        "DELETE",
        "/internal/tables/{table_name}/rows/{row_id}",
        frozenset({"core.table.delete_row"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/tables/{table_name}/download",
        frozenset({"core.table.download"}),
    ),
    GatewayCapability(
        "POST", "/internal/workflows", frozenset({"core.workflow.create_workflow"})
    ),
    GatewayCapability(
        "GET",
        "/internal/workflows/{workflow_id}/edit-document",
        frozenset({"core.workflow.get_workflow"}),
    ),
    GatewayCapability(
        "PATCH",
        "/internal/workflows/{workflow_id}/edit-document",
        frozenset({"core.workflow.edit_workflow"}),
    ),
    GatewayCapability(
        "POST",
        "/internal/workflows/{workflow_id}/publish",
        frozenset({"core.workflow.publish"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/workflows/{workflow_id}/webhook",
        frozenset({"core.workflow.get_webhook", "core.workflow.update_webhook"}),
    ),
    GatewayCapability(
        "PATCH",
        "/internal/workflows/{workflow_id}/webhook",
        frozenset({"core.workflow.update_webhook"}),
    ),
    GatewayCapability(
        "GET",
        "/internal/workflows/{workflow_id}/case-trigger",
        frozenset(
            {"core.workflow.get_case_trigger", "core.workflow.update_case_trigger"}
        ),
    ),
    GatewayCapability(
        "PATCH",
        "/internal/workflows/{workflow_id}/case-trigger",
        frozenset({"core.workflow.update_case_trigger"}),
    ),
    GatewayCapability(
        "POST",
        "/internal/workflows/authoring-context",
        frozenset({"core.workflow.get_authoring_context"}),
    ),
    GatewayCapability(
        "POST", "/internal/workflows/executions", frozenset({"core.workflow.execute"})
    ),
    GatewayCapability(
        "POST", "/internal/workflows/run", frozenset({"core.workflow.run"})
    ),
    # ``core.workflow.execute`` is intentionally not an alternative grant here:
    # the endpoint reads any workspace execution by ID, and the gateway cannot
    # bound an execute-only script to the executions it started itself. Scripts
    # using ``wait_strategy="wait"`` need ``core.workflow.get_status`` granted.
    GatewayCapability(
        "GET",
        "/internal/workflows/executions/{execution_id:path}",
        frozenset({"core.workflow.get_status"}),
    ),
)

GATEWAY_CAPABILITIES = _index_capabilities(_GATEWAY_CAPABILITY_DECLARATIONS)

# Every route mounted by the Action Gateway must be classified as callable by a
# configured registry action, explicitly denied to Agent-authored Python, or
# exempt from capability enforcement. Runtime authorization remains fail-closed
# for unknown endpoints; the exhaustive declaration test prevents new routes
# from relying on that fallback silently.
GATEWAY_DENIED_ROUTES: frozenset[GatewayRouteKey] = frozenset(
    {
        GatewayRouteKey("POST", "/internal/agent/skills:upload"),
        GatewayRouteKey("PATCH", "/internal/agent/skills/{skill_id}/draft"),
        GatewayRouteKey("GET", "/internal/agent/skills/{skill_id}/draft"),
        GatewayRouteKey("GET", "/internal/agent/skills/{skill_id}/draft/file"),
        GatewayRouteKey("DELETE", "/internal/cases/{case_id}/comments/{comment_id}"),
        GatewayRouteKey("GET", "/internal/cases/{case_id}/tags"),
        GatewayRouteKey("GET", "/internal/case-tags"),
        GatewayRouteKey("POST", "/internal/case-tags"),
        GatewayRouteKey("GET", "/internal/variables/{variable_name}"),
        GatewayRouteKey("GET", "/internal/variables/{variable_name}/value"),
    }
)
GATEWAY_EXEMPT_ROUTES: frozenset[GatewayRouteKey] = frozenset(
    {GatewayRouteKey("GET", "/internal/health")}
)


def _request_route_key(request: Request) -> GatewayRouteKey | None:
    route = request.scope.get("route")
    if not isinstance(route, APIRoute):
        return None
    return gateway_route_key(request.method, route.path_format)


async def resolve_gateway_actions(
    request: Request,
    route_key: GatewayRouteKey,
) -> GatewayActionRequirement | None:
    """Resolve the concrete registry operation represented by this request."""
    capability = GATEWAY_CAPABILITIES.get(route_key)
    if capability is None:
        return None
    if capability.resolver is None:
        return GatewayActionRequirement(any_of=capability.actions)

    requirement = await capability.resolver(request)
    if not requirement.actions <= capability.actions:
        return None
    return requirement


def _agent_gateway_action_allowed(
    claims: ExecutorTokenPayload,
    requirement: GatewayActionRequirement | None,
) -> bool:
    """Require each action constraint to pass both authorization bounds.

    ``any_of`` contains equivalent candidates for the concrete endpoint operation;
    one must pass. Every action in ``all_of`` must also pass when request parameters
    cross an additional privilege boundary.
    """
    scopes = claims.scopes
    allowed_actions = claims.allowed_actions
    if scopes is None or allowed_actions is None or requirement is None:
        return False

    def action_allowed(action: str) -> bool:
        return action in allowed_actions and has_scope(
            scopes, f"action:{action}:execute"
        )

    return any(action_allowed(action) for action in requirement.any_of) and all(
        action_allowed(action) for action in requirement.all_of
    )


async def enforce_agent_action_capability(request: Request) -> None:
    """Enforce the Agent grant on the server-matched endpoint.

    This is a FastAPI dependency, rather than middleware, because FastAPI has
    already selected ``request.scope["route"]`` when dependencies run. That
    gives us a constant-time lookup without reimplementing route matching.
    """
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        # The normal route authentication dependency owns missing credentials.
        return

    try:
        claims = verify_executor_token(token)
    except ValueError:
        # The normal route authentication dependency owns invalid credentials.
        return

    route_key = _request_route_key(request)
    if (
        claims.action != PlatformAction.RUN_PYTHON
        # ``None`` marks an execution created before the Agent-grant patch. Keep
        # those in-flight Temporal histories on their recorded legacy behavior.
        or claims.allowed_actions is None
        or route_key in GATEWAY_EXEMPT_ROUTES
    ):
        return

    requirement = (
        await resolve_gateway_actions(request, route_key)
        if route_key is not None
        else None
    )
    if _agent_gateway_action_allowed(claims, requirement):
        return

    required_scopes = [
        f"action:{action}:execute"
        for action in sorted(requirement.actions if requirement is not None else ())
    ]
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": {
                "code": "action_not_allowed",
                "message": "Action Gateway operation is not in the Agent toolset",
                "required_scopes": required_scopes,
                "missing_scopes": required_scopes,
            }
        },
    )
