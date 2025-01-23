import inspect
from typing import Union, get_type_hints

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.editor.models import EditorActionRead, EditorFunctionRead, EditorParamRead
from tracecat.expressions.functions import FUNCTION_MAPPING
from tracecat.identifiers.workflow import AnyWorkflowIDQuery
from tracecat.types.auth import Role
from tracecat.workflow.management.management import WorkflowsManagementService

router = APIRouter(prefix="/editor", tags=["editor"])


@router.get("/functions", response_model=list[EditorFunctionRead])
async def list_functions(
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
):
    functions = []

    for name, func in FUNCTION_MAPPING.items():
        # Get function signature
        sig = inspect.signature(func)
        doc = inspect.getdoc(func) or ""
        type_hints = get_type_hints(func)

        # Extract parameter information
        parameters = []
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue

            param_type_hint = type_hints.get(param_name, "Any")

            def format_type(type_hint) -> str:
                if hasattr(type_hint, "__origin__") and type_hint.__origin__ is Union:
                    return " | ".join(format_type(t) for t in type_hint.__args__)
                # Handle generic types like list[str], dict[str, int], etc.
                elif hasattr(type_hint, "__origin__"):
                    args = ", ".join(format_type(arg) for arg in type_hint.__args__)
                    return f"{type_hint.__origin__.__name__}[{args}]"
                # Handle basic types
                return getattr(type_hint, "__name__", str(type_hint))

            param_type = format_type(param_type_hint)
            parameters.append(
                EditorParamRead(
                    name=param_name,
                    type=param_type,
                    optional=param.default != inspect.Parameter.empty,
                )
            )

        # Update return type handling
        return_type = type_hints.get("return", "Any")
        return_type_str = format_type(return_type)

        functions.append(
            EditorFunctionRead(
                name=name,
                description=doc,
                parameters=parameters,
                return_type=return_type_str,
            )
        )

    return functions


@router.get("/actions", response_model=list[EditorActionRead])
async def list_actions(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDQuery,
):
    # find actions that are in the workflow
    service = WorkflowsManagementService(session, role=role)
    workflow = await service.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    actions = workflow.actions
    if not actions:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No actions found in workflow",
        )

    return [
        EditorActionRead(
            type=action.type, ref=action.ref, description=action.description
        )
        for action in actions
    ]
