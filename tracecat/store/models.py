from pydantic import BaseModel

from tracecat.identifiers import WorkflowExecutionID, WorkflowID


class ActionResultObject(BaseModel):
    """Represents a structured storage path"""

    workflow_id: WorkflowID
    execution_id: WorkflowExecutionID
    object_name: str

    def to_path(self, ext: str = "json") -> str:
        """Convert to storage path string"""
        return f"{self.workflow_id}/{self.execution_id}/{self.object_name}.{ext}"
