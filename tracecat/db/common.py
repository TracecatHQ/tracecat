from enum import StrEnum


class DBConstraints(StrEnum):
    WORKFLOW_ALIAS_UNIQUE_IN_WORKSPACE = "uq_workflow_alias_owner_id"

    def msg(self) -> str:
        return CONSTRAINT_TO_MSG[self.value]


CONSTRAINT_TO_MSG = {
    "uq_workflow_alias_owner_id": "Workflow alias must be unique within the workspace.",
}
