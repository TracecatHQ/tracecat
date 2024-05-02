from functools import cached_property
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, validator

from tracecat.logger import standard_logger
from tracecat.runner.actions import (
    Action,
    ActionVariant,
)
from tracecat.types.api import (
    ActionResponse,
    WorkflowResponse,
)

logger = standard_logger(__name__)


class Workflow(BaseModel):
    """Configuration for a workflow.

    This is different from a workflow run, which is a specific instance of a workflow.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: str = Field(default_factory=lambda: uuid4().hex)
    title: str
    status: Literal["online", "offline"]
    adj_list: dict[str, list[str]]
    actions: dict[str, ActionVariant]
    owner_id: str

    @cached_property
    def action_dependencies(self) -> dict[str, set[str]]:
        """Return a mapping of action IDs to their dependencies."""
        deps: dict[str, set[str]] = {k: set() for k in self.adj_list.keys()}

        for dependency, actions in self.adj_list.items():
            for action in actions:
                deps[action].add(dependency)
        return deps

    @validator("actions", pre=True)
    def parse_actions(cls, v: dict[str, Any]) -> Any:
        return {k: Action.from_dict(v) for k, v in v.items()}

    @classmethod
    def from_response(cls, response: WorkflowResponse) -> Self:
        """Create a Workflow from a WorkflowResponse.

        Logic
        -----
        1. Create the adjacency list from the response.
            - Workflow response actions is a mapping of action IDs to the actual node data.
            - We only need to extract {<action_key>: [...<downstream_deps>]...} from the response.
        2. Filter out the actions from the response.
        """
        adj_list = _graph_obj_to_adj_list(response.object, response.actions)
        actions = {}
        for action in response.actions.values():
            inputs = action.inputs or {}
            # Handle hierarchical action types
            if action.type.startswith("llm."):
                # Special case for LLM actions
                # NOTE!!!!: Tech debt incurring...
                # This design needs to change
                data = {
                    "key": action.key,
                    "title": action.title,
                    "type": "llm",
                    "message": inputs.pop("message", ""),
                }
                if system_context := inputs.pop("system_context", None):
                    data.update(system_context=system_context)
                if model := inputs.pop("model", None):
                    data.update(model=model)
                if response_schema := inputs.pop("response_schema", None):
                    data.update(response_schema=response_schema)
                if llm_kwargs := inputs.pop("llm_kwargs", None):
                    data.update(llm_kwargs=llm_kwargs)
                data.update(
                    task_fields={"type": action.type, **inputs},
                )
            elif action.type.startswith("condition."):
                inputs.update(type=action.type)
                data = {
                    "key": action.key,
                    "title": action.title,
                    "type": "condition",
                    "condition_rules": inputs,
                }
            elif action.type.startswith("integrations."):
                data = {
                    "key": action.key,
                    "title": action.title,
                    "type": "integration",
                    "qualname": action.type,
                    "params": inputs,
                }
            else:
                # All other root level action types
                data = {
                    "key": action.key,
                    "title": action.title,
                    "type": action.type,
                    **inputs,
                }
            actions[action.key] = data

        return cls(
            id=response.id,
            title=response.title,
            adj_list=adj_list,
            actions=actions,
            owner_id=response.owner_id,
            status=response.status,
        )


def _graph_obj_to_adj_list(
    obj: dict[str, Any], actions: dict[str, ActionResponse]
) -> dict[str, set[str]]:
    """Convert a react flow object to simple adjacency list of runner Action keys.

    This is used to create the adj_list for a runner Workflow.
    """
    nodes: list[dict[str, Any]] = obj.get("nodes")
    edges: list[dict[str, Any]] = obj.get("edges")

    # Action keys to downstream action keys
    adj_list = {actions[node["id"]].key: set() for node in nodes}

    if not edges:
        return adj_list

    for edge in edges:
        source_id, target_id = edge["source"], edge["target"]
        adj_list[actions[source_id].key].add(actions[target_id].key)
    return adj_list
