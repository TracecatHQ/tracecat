from pydantic import BaseModel


class WorkflowScheduleParams(BaseModel):
    cron: str | None = None
    entrypoint_key: str | None = None
    entrypoint_payload: str | None = None  # JSON-serialized String of payload
