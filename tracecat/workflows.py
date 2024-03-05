from functools import cached_property
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, validator

from tracecat.actions import (
    Action,
    ActionSubclass,
)


class Workflow(BaseModel):
    """Configuration for a workflow.

    This is different from a workflow run, which is a specific instance of a workflow.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: str = Field(default_factory=lambda: uuid4().hex)
    adj_list: dict[str, list[str]]
    action_map: dict[str, ActionSubclass]

    @cached_property
    def action_dependencies(self) -> dict[str, set[str]]:
        """Return a mapping of action IDs to their dependencies."""
        deps: dict[str, set[str]] = {k: set() for k in self.adj_list.keys()}

        for dependency, actions in self.adj_list.items():
            for action in actions:
                deps[action].add(dependency)
        return deps

    @validator("action_map", pre=True)
    def parse_actions(cls, v: dict[str, Any]) -> Any:
        return {k: Action.from_dict(v) for k, v in v.items()}
