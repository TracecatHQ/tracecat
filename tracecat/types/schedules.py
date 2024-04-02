from pydantic import BaseModel


class WorkflowScheduleParams(BaseModel):
    cron: str
    entrypoint_key: str
    entrypoint_payload: str  # JSON-serialized String of payload
