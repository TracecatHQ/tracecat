from pydantic import BaseModel


class WorkflowScheduleParam(BaseModel):
    cron: str
    entrypoint_key: str
    entrypoint_payload: str  # JSON-serialized String of payload
